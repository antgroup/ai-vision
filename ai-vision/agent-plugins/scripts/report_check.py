#!/usr/bin/env python3
"""Post replay JSONL files to a local AiVision/weaver OTLP HTTP endpoint.

The root Makefile uses this for:
  make report-check cc
  make report-check-golden cc

Each non-empty JSONL line is expected to be one OTLP Export*ServiceRequest JSON
payload and is posted to the endpoint inferred from the file name.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable


@dataclass
class PostResult:
    files: int = 0
    records: int = 0
    ok: int = 0
    failed: int = 0


def endpoint_for(path: str, first_payload: dict | None = None) -> str:
    name = os.path.basename(path)
    if name.startswith(("spans_", "traces_")):
        return "/v1/traces"
    if name.startswith(("logs_", "events_")):
        return "/v1/logs"
    if name.startswith("metrics_"):
        return "/v1/metrics"

    if first_payload is not None:
        if "resourceSpans" in first_payload:
            return "/v1/traces"
        if "resourceLogs" in first_payload:
            return "/v1/logs"
        if "resourceMetrics" in first_payload:
            return "/v1/metrics"

    raise ValueError(
        f"cannot infer OTLP endpoint for {path!r}; expected file prefix "
        "spans_/traces_, events_/logs_, or metrics_"
    )


def normalize_base_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if not base_url:
        raise ValueError("base URL must not be empty")
    return base_url


def post_json(base_url: str, endpoint: str, payload: bytes, timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{base_url}{endpoint}",
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body


def iter_jsonl(path: str) -> Iterable[tuple[int, bytes, dict]]:
    with open(path, "rb") as fh:
        for line_no, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL record: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(f"{path}:{line_no}: OTLP JSON record must be an object")
            yield line_no, stripped, parsed


def post_files(
    base_url: str,
    files: list[str],
    timeout: float,
    dry_run: bool,
    verbose: bool,
) -> PostResult:
    result = PostResult()
    for path in files:
        if not os.path.isfile(path):
            print(f"[report-check] missing file: {path}", file=sys.stderr)
            result.failed += 1
            continue

        records = list(iter_jsonl(path))
        if not records:
            print(f"[report-check] skip empty file: {path}")
            continue

        endpoint = endpoint_for(path, records[0][2])
        result.files += 1
        print(f"[report-check] {path} -> {endpoint} ({len(records)} records)")
        file_ok = 0
        file_failed = 0

        for line_no, payload, _parsed in records:
            result.records += 1
            if dry_run:
                result.ok += 1
                file_ok += 1
                if verbose:
                    print(f"  dry-run ok line={line_no}")
                continue

            status, body = post_json(base_url, endpoint, payload, timeout)
            if 200 <= status < 300:
                result.ok += 1
                file_ok += 1
                if verbose:
                    print(f"  ok line={line_no} status={status}")
            else:
                result.failed += 1
                file_failed += 1
                compact_body = " ".join(body.split())[:500]
                print(
                    f"  failed line={line_no} status={status} body={compact_body}",
                    file=sys.stderr,
                )
        print(f"  file-summary records={len(records)} ok={file_ok} failed={file_failed}")
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", help="Replay JSONL files to post")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:4319",
        help="AiVision/weaver OTLP HTTP base URL (default: %(default)s)",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds")
    parser.add_argument("--dry-run", action="store_true", help="Validate routing without posting")
    parser.add_argument("--verbose", action="store_true", help="Print every successful record")
    args = parser.parse_args(argv)

    base_url = normalize_base_url(args.base_url)
    result = post_files(base_url, args.files, args.timeout, args.dry_run, args.verbose)
    print(
        f"[report-check] summary files={result.files} records={result.records} "
        f"ok={result.ok} failed={result.failed} base_url={base_url}"
    )
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
