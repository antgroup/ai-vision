"""Validated corpus -> owned collector -> scoped platform source and projection."""

import argparse
import base64
import json
import re
import sys
import time
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from calibrate import _targets
from capture import decode_export, response_admitted
from core_trial import compare_authenticated_rows, compare_rows, require, source_rows
from corpus import _outside_git, _write, load_package, stable_hash
from source import validate_projection, validate_targets


def readback(platform, expected, *, timeout=90, authenticated_scope=None):
    require(
        type(timeout) in (int, float) and 0 <= timeout <= 90,
        "Bounded replay readback timeout required",
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            sample = platform.scoped(
                "/diagnoser/trace-sample", params={"trace_id": expected[0]["trace_id"]}
            )
        except RuntimeError as error:
            if (
                getattr(error, "http_status", None) != 404
                or time.monotonic() >= deadline
            ):
                raise
            time.sleep(min(1, max(0, deadline - time.monotonic())))
            continue
        actual = sample.get("test_input", {})
        require(
            actual.get("trace_id") == expected[0]["trace_id"],
            "Platform trace identity differs",
        )
        proof = (
            compare_rows(expected, actual.get("spans", []))
            if authenticated_scope is None
            else compare_authenticated_rows(
                expected, actual.get("spans", []), scope=authenticated_scope
            )
        )
        if proof["verified"] or time.monotonic() >= deadline:
            return actual
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def replay_payload(export):
    """Convert protobuf JSON byte IDs to OTLP JSON hex; never change identity/time."""
    require(
        export.get("admitted") is True
        and export.get("signal") in {"traces", "logs", "metrics"},
        "Replay requires admitted original signal exports",
    )
    payload = deepcopy(export.get("otlp"))
    require(isinstance(payload, dict), "Original OTLP payload is missing")
    if export["signal"] == "traces":
        expected = export.get("spans")
        require(
            isinstance(expected, list) and bool(expected),
            "Original trace batch spans are missing",
        )
        parents = {}
        for span in expected:
            require(isinstance(span, dict), "Invalid trace batch span")
            trace, identity, parent = (
                span.get("trace_id"),
                span.get("span_id"),
                span.get("parent_span_id"),
            )
            require(
                isinstance(trace, str)
                and re.fullmatch("[0-9a-f]{32}", trace)
                and isinstance(identity, str)
                and re.fullmatch("[0-9a-f]{16}", identity)
                and (
                    parent is None
                    or parent == ""
                    or (
                        isinstance(parent, str) and re.fullmatch("[0-9a-f]{16}", parent)
                    )
                ),
                "Invalid trace batch identity or parent",
            )
            require((trace, identity) not in parents, "Duplicate trace batch span")
            parents[(trace, identity)] = parent
        require(
            len({trace for trace, _ in parents}) == 1,
            "One trace per replay batch is required",
        )
        for identity in parents:
            seen = set()
            current = identity
            # Export batches may refer to a parent in another batch. The complete
            # source_rows(report) gate still checks the entire original tree.
            while current in parents:
                require(current not in seen, "Trace batch parent cycle")
                seen.add(current)
                current = (current[0], parents[current])
    known = {
        span.get(field)
        for span in export.get("spans", []) + export.get("logs", [])
        for field in ("trace_id", "span_id", "parent_span_id")
        if span.get(field)
    }

    def convert(value):
        if isinstance(value, list):
            return [convert(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if (
                key
                in {
                    "traceId",
                    "trace_id",
                    "spanId",
                    "span_id",
                    "parentSpanId",
                    "parent_span_id",
                }
                and item
            ):
                if item in known:
                    result[key] = item
                else:
                    try:
                        identifier = base64.b64decode(item, validate=True).hex()
                    except (ValueError, TypeError):
                        raise ValueError(
                            "OTLP identifier encoding differs from source"
                        ) from None
                    if export["signal"] == "traces":
                        require(
                            identifier in known, "OTLP identifier differs from source"
                        )
                    result[key] = identifier
            else:
                result[key] = convert(item)
        return result

    payload = convert(payload)
    decoded = decode_export(
        export["signal"], json.dumps(payload).encode(), "application/json"
    )
    if export["signal"] == "traces":
        # Compare the complete nested captured span material, not a flattened
        # single-export tree that incorrectly requires cross-batch parents.
        actual = decoded["spans"]
        require(
            compare_rows(expected, actual)["verified"],
            "OTLP payload disagrees with captured source",
        )
    return payload


def projection_diff(actual, evidence):
    """Independent raw-material checks plus original-source fact reproducibility.

    The existing pure Coding projector supplies derived mutation/verification
    facts from original rows, not readback or a scenario tool plan. Comparing
    those facts proves replay consistency, not independent classifier accuracy.
    """
    local_src = str(Path(__file__).resolve().parents[4] / "agentlens/src")
    if local_src not in sys.path:
        sys.path.insert(0, local_src)
    from agentlens.service.datasource.projection.projectors.coding.adapter import (
        project_coding_raw,
    )
    from agentlens.service.datasource.projection.serialization import (
        serialize_projection,
    )
    from scenarios import scenarios

    rows = source_rows(evidence)
    originals = project_coding_raw(rows, rows[0]["trace_id"])
    require(len(originals) == 1, "Original source has no unique Coding projection")
    facts = serialize_projection(originals[0])
    matches = [
        item
        for item in scenarios(True, include_extended=True, include_rag=True)
        if item.name == evidence.get("scenario")
    ]
    require(len(matches) == 1, "Unknown captured source scenario")
    report = {
        **evidence,
        "mode": "replay",
        "source": {
            "source_trace_ids": [rows[0]["trace_id"]],
            "source_span_ids": [row["span_id"] for row in rows],
        },
    }
    return validate_projection(actual, report, matches[0], original_facts=facts)


def replay_package(
    manifest_path,
    *,
    target,
    platform,
    out,
    transport=None,
    headers_factory=None,
    readback_timeout=90,
    authenticated_scope=None,
):
    package = load_package(manifest_path)
    origin = target.get("collector_endpoint", "")
    parsed = urlsplit(origin)
    require(
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment,
        "Only an owned credential-free loopback collector is allowed",
    )
    out = _outside_git(out)
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    owned = transport is None
    client = transport or httpx.Client(timeout=60, trust_env=False)
    cases = []
    try:
        for case in package["cases"]:
            evidence = case["evidence"]
            row = {
                "case_id": case["case_id"],
                "origin": case["provenance"]["origin"],
                "source_sha256": stable_hash(evidence),
                "admissions": [],
                "verified": False,
                "failures": [],
            }
            try:
                expected = source_rows(evidence)
                for export in evidence["exports"]:
                    payload = replay_payload(export)
                    headers = dict(
                        headers_factory()
                        if headers_factory
                        else target.get("collector_headers", {})
                    )
                    headers["Content-Type"] = "application/json"
                    response = client.post(
                        origin.rstrip("/") + "/v1/" + export["signal"],
                        content=json.dumps(payload).encode(),
                        headers=headers,
                    )
                    admitted = response_admitted(
                        export["signal"],
                        response.status_code,
                        response.content,
                        response.headers.get("Content-Type", ""),
                    )
                    row["admissions"].append(
                        {
                            "signal": export["signal"],
                            "admitted": admitted,
                            "status": response.status_code,
                            "original_otlp_sha256": stable_hash(export["otlp"]),
                            "wire_otlp_sha256": stable_hash(payload),
                        }
                    )
                    require(admitted, "Collector rejected replay signal")
                actual = readback(
                    platform,
                    expected,
                    timeout=readback_timeout,
                    authenticated_scope=authenticated_scope,
                )
                row["source_diff"] = compare_rows(expected, actual.get("spans", []))
                source_gate = row["source_diff"]
                if authenticated_scope is not None:
                    source_gate = compare_authenticated_rows(
                        expected, actual.get("spans", []), scope=authenticated_scope
                    )
                    row["authenticated_source_diff"] = source_gate
                row["projection"] = actual
                row["projection_diff"] = projection_diff(actual, evidence)
                row["verified"] = (
                    source_gate["verified"] and row["projection_diff"]["verified"]
                )
                row["failures"] = (
                    source_gate["failures"] + row["projection_diff"]["failures"]
                )
            except (
                ValueError,
                KeyError,
                TypeError,
                RuntimeError,
                httpx.HTTPError,
            ) as error:
                row["failures"].append(type(error).__name__)
            cases.append(row)
        report = {
            "stage": "platform_replay",
            "verified": bool(cases) and all(row["verified"] for row in cases),
            "labels_sha256": package["labels_sha256"],
            "cases": cases,
            "diagnosis": "not_requested",
            "boundary": "Admission/source/projection only; no semantic accuracy or wire-byte fidelity claim",
        }
        _write(out / "report.json", report)
        return report
    finally:
        if owned:
            client.close()


def authenticated_replay_scope(platform, target):
    """Bind an explicit platform entrypoint to its current authenticated user."""
    me = platform.request("GET", "/api/v1/auth/me", allowed=(200,))
    user = me.get("user") if isinstance(me, dict) else None
    identity = user.get("user_uuid") if isinstance(user, dict) else None
    require(
        type(identity) is str
        and identity
        and identity.strip() == identity
        and len(identity) <= 36,
        "Authenticated replay user identity is invalid",
    )
    current = platform.target
    fields = ("space_id", "agent_uuid", "agent_product")
    require(
        isinstance(current, dict)
        and isinstance(target, dict)
        and all(
            type(current.get(field)) is str
            and current[field]
            and current[field].strip() == current[field]
            and type(target.get(field)) is str
            and current[field] == target[field]
            for field in fields
        ),
        "Current authenticated replay target differs from the owned target",
    )
    return {
        **{field: current[field] for field in fields},
        "authenticated_user_id": identity,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("corpus", "targets", "out"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--app", choices=("codex", "dsh02"), required=True)
    args = parser.parse_args(argv)
    try:
        from credentials import RenewingIngestHeaders
        from platform_client import PlatformClient
        from run_suite import native_sessions

        targets = validate_targets(_targets(args.targets), [args.app])
        target = targets[args.app]
        sessions = native_sessions.DashboardSessions()
        with PlatformClient(
            target["endpoint"], target=target, dashboard_sessions=sessions
        ) as platform:
            result = replay_package(
                args.corpus,
                target=target,
                platform=platform,
                out=args.out,
                headers_factory=RenewingIngestHeaders(
                    target, native_sessions.refresh_ingest
                ),
                authenticated_scope=authenticated_replay_scope(platform, target),
            )
        return 0 if result["verified"] else 1
    except (ValueError, OSError, KeyError, TypeError):
        parser.exit(
            1, "Replay rejected; inspect private source/ownership/target contracts.\n"
        )


if __name__ == "__main__":
    raise SystemExit(main())
