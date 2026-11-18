"""CLI guard behavior uses an injected domain fixture, never a local database."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentlens.service.diagnoser_retirement import Ownership, digest
from agentlens.service.diagnoser_retirement_journal import Journal

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "migrate_retired_diagnosis.py"
spec = importlib.util.spec_from_file_location("retirement_script", SCRIPT)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def args(**changes):
    values = dict(
        phase="inventory",
        apply=False,
        ownership_manifest=Path("/private/grant.json"),
        plan=None,
        manifest=None,
        preview=None,
        expect_preview_sha256=None,
        configurations=None,
        target_build_evidence=None,
        expect_target_evidence_sha256=None,
        source_build_evidence=None,
        expect_source_evidence_sha256=None,
        drain_timeout_sec=0,
        output=None,
    )
    return SimpleNamespace(**(values | changes))


def grant(tmp_path):
    value = dict(
        schema_version=1,
        environment_sha256="1" * 64,
        authorization_ref="sealed/owner",
        space_uuid="space",
        agent_uuid="agent",
        job_uuids=["j-existing"],
        dataset_uuids=["ds-existing"],
        diagnoser_uuids=["dg-existing"],
    )
    path = tmp_path / "grant.json"
    with Journal(path).locked() as journal:
        journal.write(value)
    return path


def test_default_cli_is_inventory_without_apply():
    parsed = cli.parser().parse_args(["--ownership-manifest", "/private/grant.json"])
    assert parsed.phase == "inventory" and parsed.apply is False
    cli.validate_arguments(parsed)


@pytest.mark.parametrize(
    "entrypoint",
    [
        ["scripts/migrate_retired_diagnosis.py"],
        ["-m", "scripts.migrate_retired_diagnosis"],
    ],
)
def test_cli_entrypoints_preserve_read_only_failure_contract(entrypoint, tmp_path):
    result = subprocess.run(
        [sys.executable, *entrypoint, "--ownership-manifest", str(tmp_path / "missing.json")],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "status": "blocked",
        "code": "RETIRED_DIAGNOSIS_VALIDATION_FAILED",
    }
    assert result.stderr == ""


@pytest.mark.parametrize(
    "phase", ["pause", "ready", "remap", "resume", "rollback_pause", "rollback_restore"]
)
def test_every_write_phase_requires_all_explicit_artifacts(phase):
    with pytest.raises(cli.ArgumentError):
        cli.validate_arguments(args(phase=phase, apply=True))
    with pytest.raises(cli.ArgumentError):
        cli.validate_arguments(
            args(phase=phase, apply=True, plan=Path("plan"), manifest=Path("journal"))
        )


def test_apply_inventory_or_drain_never_connects(monkeypatch):
    create = Mock()
    monkeypatch.setattr(cli, "create_store", create)
    with pytest.raises(cli.ArgumentError):
        cli.run(args(apply=True, preview=Path("preview"), expect_preview_sha256="1" * 64))
    create.assert_not_called()


def test_invalid_grant_is_rejected_before_connection(tmp_path, monkeypatch):
    path = tmp_path / "invalid.json"
    path.write_text('{"api_key": "NEVER-ECHO-THIS"}')
    path.chmod(0o600)
    create = Mock()
    monkeypatch.setattr(cli, "create_store", create)
    with pytest.raises(cli.ValidationError):
        cli.run(args(ownership_manifest=path))
    create.assert_not_called()


def test_inventory_backend_mock_is_read_only_and_prefixed_uuids_supported(tmp_path):
    path = grant(tmp_path)
    store = Mock()
    store.read.return_value = dict(jobs={}, datasets={}, diagnosers={}, tasks={}, models={})
    result = cli.run(args(ownership_manifest=path), store=store)
    assert result["applied"] is False
    ownership = store.read.call_args.args[0]
    assert ownership.job_uuids == ["j-existing"]
    store.transaction.assert_not_called()
    store.set_job.assert_not_called()


def test_main_does_not_echo_pydantic_inputs_or_private_material(tmp_path, monkeypatch, capsys):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"secret": "PRIVATE-SECRET-CREDENTIAL"}))
    path.chmod(0o600)
    assert cli.main(["--ownership-manifest", str(path)]) == 2
    assert "PRIVATE-SECRET-CREDENTIAL" not in capsys.readouterr().out


def test_readiness_evidence_requires_independent_expected_hash():
    with pytest.raises(cli.ArgumentError):
        cli.validate_arguments(args(target_build_evidence=Path("proof.json")))
    with pytest.raises(cli.ArgumentError):
        cli.validate_arguments(args(expect_source_evidence_sha256="1" * 64))


def test_private_grant_digest_is_stable_not_a_creator_inference(tmp_path):
    value = cli.read_private_json(grant(tmp_path))
    assert digest(value) == digest(Ownership.model_validate(value).model_dump())
    assert "creator" not in value


def test_output_never_overwrites_grant_or_recovery_journal_before_connect():
    with pytest.raises(cli.ArgumentError, match="overwrite"):
        cli.validate_arguments(args(output=Path("/private/grant.json")))
    with pytest.raises(cli.ArgumentError, match="overwrite"):
        cli.validate_arguments(
            args(
                phase="pause",
                plan=Path("/private/plan.json"),
                manifest=Path("/private/manifest.json"),
                output=Path("/private/manifest.json"),
            )
        )
