#!/usr/bin/env python3
"""Read-only by default. Operates one explicitly granted OceanBase/seekdb scope."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url

from agentlens.infra.ob.engine import make_engine, make_session_factory
from agentlens.service.diagnoser_retirement import (
    FrozenSpecialistAdapter,
    Ownership,
    Plan,
    RetirementError,
    RetirementMigration,
    RetirementStore,
    digest,
    inventory,
)
from agentlens.service.diagnoser_retirement_evidence import LocalEvidenceVerifier
from agentlens.service.diagnoser_retirement_journal import Journal, JournalError, read_private_json


class ArgumentError(ValueError):
    pass


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--phase",
        choices=[
            "inventory",
            "pause",
            "drain",
            "ready",
            "remap",
            "resume",
            "rollback_pause",
            "rollback_restore",
        ],
        default="inventory",
    )
    result.add_argument("--apply", action="store_true")
    result.add_argument("--ownership-manifest", type=Path, required=True)
    result.add_argument("--plan", type=Path)
    result.add_argument("--manifest", type=Path)
    result.add_argument("--preview", type=Path)
    result.add_argument("--expect-preview-sha256")
    result.add_argument(
        "--configurations",
        type=Path,
        help="owned 0600 reference-to-configuration object; never printed",
    )
    result.add_argument("--target-build-evidence", type=Path)
    result.add_argument("--expect-target-evidence-sha256")
    result.add_argument("--source-build-evidence", type=Path)
    result.add_argument("--expect-source-evidence-sha256")
    result.add_argument("--drain-timeout-sec", type=float, default=0)
    result.add_argument("--output", type=Path, help="atomic 0600 safe preview output")
    return result


def validate_arguments(args):
    if args.output is not None and any(
        path is not None and args.output.resolve() == path.resolve()
        for path in (
            args.ownership_manifest,
            args.plan,
            args.manifest,
            args.preview,
            args.configurations,
            args.target_build_evidence,
            args.source_build_evidence,
        )
    ):
        raise ArgumentError("output must not overwrite any input or recovery manifest")
    if args.phase != "inventory" and (args.plan is None or args.manifest is None):
        raise ArgumentError("phase requires --plan and --manifest")
    if args.apply and (
        args.phase in {"inventory", "drain"}
        or args.preview is None
        or args.expect_preview_sha256 is None
    ):
        raise ArgumentError(
            "write phase requires --apply --phase --preview --expect-preview-sha256"
        )
    if not args.apply and args.expect_preview_sha256 is not None:
        raise ArgumentError("--expect-preview-sha256 requires --apply")
    for file, expected in (
        (args.target_build_evidence, args.expect_target_evidence_sha256),
        (args.source_build_evidence, args.expect_source_evidence_sha256),
    ):
        if (file is None) != (expected is None):
            raise ArgumentError("build evidence and its independent expected SHA256 must be paired")
    if args.phase == "inventory" and args.preview is not None:
        raise ArgumentError("inventory does not consume an execution preview")


def environment_fingerprint(url, session_factory):
    """Credentials are neither printed nor included in the identity digest."""
    parsed = make_url(url)
    if parsed.get_backend_name() not in {"mysql", "mariadb"}:
        raise RetirementError("only the configured OceanBase/seekdb MySQL protocol is supported")
    with session_factory() as session:
        revisions = sorted(session.scalars(text("SELECT version_num FROM alembic_version")))
        database = session.scalar(text("SELECT DATABASE()"))
        version = session.scalar(text("SELECT VERSION()"))
    return digest(
        {
            "driver": parsed.drivername,
            "account": parsed.username,
            "host": parsed.host,
            "port": parsed.port,
            "database": database,
            "server_version": version,
            "schema_revisions": revisions,
        }
    )


def create_store(args):
    # This utility needs only the control-plane connection, not Source/provider config.
    # Existing deployment Settings use the same AGENTLENS_OB_URL override.
    url = os.environ.get("AGENTLENS_OB_URL")
    if not url:
        raise ArgumentError("provide the existing private AGENTLENS_OB_URL environment setting")
    if make_url(url).get_backend_name() not in {"mysql", "mariadb"}:
        raise ArgumentError("unsupported data backend")
    engine = make_engine(url, sql_log_enabled=False)
    factory = make_session_factory(engine)
    return RetirementStore(
        factory, environment_sha256=environment_fingerprint(url, factory)
    ), engine


def run(args, *, store=None):
    validate_arguments(args)
    ownership = Ownership.model_validate(read_private_json(args.ownership_manifest))
    owned_engine = None
    if store is None:
        store, owned_engine = create_store(args)
    try:
        if args.phase == "inventory":
            return {"schema_version": 1, "applied": False, "inventory": inventory(store, ownership)}
        plan = Plan.model_validate(read_private_json(args.plan))
        configurations = read_private_json(args.configurations) if args.configurations else {}

        if hasattr(store, "target_model_uuids"):
            from agentlens.domain.specialist_policy import normalize_specialist_configuration

            for planned in plan.targets:
                raw = configurations.get(planned.configuration_ref)
                if raw is not None:
                    normalized = normalize_specialist_configuration(
                        planned.builtin_key, planned.canonical_variant, raw
                    )
                    if normalized.model_connection_uuid:
                        store.target_model_uuids.add(normalized.model_connection_uuid)

        def configuration_loader(reference):
            if reference not in configurations:
                raise RetirementError("private configuration reference is unavailable")
            return configurations[reference]

        target = (
            LocalEvidenceVerifier(
                args.target_build_evidence, expected_sha256=args.expect_target_evidence_sha256
            )
            if args.target_build_evidence
            else None
        )
        source = (
            LocalEvidenceVerifier(
                args.source_build_evidence,
                expected_sha256=args.expect_source_evidence_sha256,
                source=True,
            )
            if args.source_build_evidence
            else None
        )
        service = RetirementMigration(
            store,
            ownership,
            plan,
            Journal(args.manifest),
            configuration_loader=configuration_loader,
            specialist_adapter=FrozenSpecialistAdapter(store.session_factory),
            readiness_verifier=target,
            source_build_verifier=source,
        )
        if args.phase == "drain":
            return service.wait_for_drain(timeout_sec=args.drain_timeout_sec)
        if args.apply:
            preview = read_private_json(args.preview)
            if preview.get("phase") != args.phase:
                raise RetirementError("explicit phase differs from supplied preview")
            return service.apply(
                preview, expected_preview_sha256=args.expect_preview_sha256, apply=True
            )
        return service.preview(args.phase)
    finally:
        if owned_engine is not None:
            owned_engine.dispose()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = run(args)
        if args.output:
            with Journal(args.output).locked() as output:
                output.write(result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except (ArgumentError, RetirementError, JournalError, ValidationError):
        # Pydantic exceptions may contain raw policy/connection input. Only a bounded
        # safe code escapes; the operator can inspect their private file locally.
        print(json.dumps({"status": "blocked", "code": "RETIRED_DIAGNOSIS_VALIDATION_FAILED"}))
        return 2
    except Exception:
        print(json.dumps({"status": "blocked", "code": "RETIRED_DIAGNOSIS_OPERATION_FAILED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
