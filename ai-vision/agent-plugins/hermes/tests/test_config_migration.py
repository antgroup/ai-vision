from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERMES_ROOT = Path(__file__).resolve().parents[1]
if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))

from scripts import install_plugin  # noqa: E402

SCRIPT = HERMES_ROOT / "scripts/install_plugin.py"
SOURCE = HERMES_ROOT / "observ_hermes"
TEMPLATE = SOURCE / "configs/aivision.json"


def install_command(root: Path, *extra: str) -> list[str]:
    return [
        sys.executable,
        str(SCRIPT),
        "install",
        "--source",
        str(SOURCE),
        "--destination",
        str(root / "plugins/observ_hermes"),
        "--config-template",
        str(TEMPLATE),
        "--config-destination",
        str(root / "observ-hermes.json"),
        "--legacy-env",
        str(root / "observ-hermes.env"),
        *extra,
    ]


class ConfigMigrationTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "legacy backup chmod is POSIX-only")
    def test_backup_chmod_failure_restores_config_and_legacy_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "observ-hermes.json"
            legacy = root / "observ-hermes.env"
            original = b"# retained exactly\r\nAGENT_ENABLE_TELEMETRY=true\r\n"
            legacy.write_bytes(original)
            plan = install_plugin.prepare_config_plan(
                template=TEMPLATE,
                destination=config,
                legacy_env=legacy,
            )
            assert plan.legacy_backup is not None
            real_chmod = os.chmod

            def fail_backup_chmod(path: os.PathLike[str] | str, mode: int) -> None:
                if Path(path) == plan.legacy_backup:
                    raise PermissionError("simulated legacy backup chmod failure")
                real_chmod(path, mode)

            with mock.patch.object(
                install_plugin.os, "chmod", side_effect=fail_backup_chmod
            ):
                with self.assertRaisesRegex(
                    PermissionError, "simulated legacy backup chmod failure"
                ):
                    install_plugin.commit_config(plan)

            self.assertFalse(config.exists())
            self.assertEqual(legacy.read_bytes(), original)
            self.assertFalse(plan.legacy_backup.exists())
            self.assertFalse(any(root.glob(".observ-hermes-config-commit-*")))

    @unittest.skipUnless(os.name == "posix", "legacy backup chmod is POSIX-only")
    def test_config_commit_recovery_preserves_evidence_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "observ-hermes.json"
            legacy = root / "observ-hermes.env"
            original = b"AGENT_ENABLE_TELEMETRY=true\n"
            recovered = b"externally recovered state\n"
            legacy.write_bytes(original)
            plan = install_plugin.prepare_config_plan(
                template=TEMPLATE,
                destination=config,
                legacy_env=legacy,
            )
            assert plan.legacy_backup is not None and plan.content is not None
            real_chmod = os.chmod

            def fail_after_external_recovery(
                path: os.PathLike[str] | str, mode: int
            ) -> None:
                if Path(path) == plan.legacy_backup:
                    legacy.write_bytes(recovered)
                    raise PermissionError("simulated legacy backup chmod failure")
                real_chmod(path, mode)

            with mock.patch.object(
                install_plugin.os, "chmod", side_effect=fail_after_external_recovery
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "config commit recovery failed.*recovery state retained at",
                ):
                    install_plugin.commit_config(plan)

            evidence = tuple(root.glob(".observ-hermes-config-commit-*"))
            self.assertEqual(len(evidence), 1)
            self.assertFalse(config.exists())
            self.assertEqual(
                (evidence[0] / "created-config").read_bytes(), plan.content
            )
            self.assertEqual(legacy.read_bytes(), recovered)
            self.assertEqual(plan.legacy_backup.read_bytes(), original)

    def test_legacy_allowlist_migrates_atomically_with_private_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "observ-hermes.env"
            legacy.write_text(
                "\n".join(
                    [
                        "AGENT_ENABLE_TELEMETRY=true",
                        "OTEL_SERVICE_NAME=legacy-hermes",
                        "OBSERV_HERMES_COLLECTION_MODE=privacy",
                        "AIVISION_ANT_USERNAME=legacy-user",
                        "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf",
                        "OBSERV_HERMES_SESSION_DIR=/must/not/migrate",
                        "OBSERV_HERMES_REPLAY_DIR=/must/not/migrate",
                        "OBSERV_HERMES_DEBUG=true",
                        "AGENT_LOG_USER_PROMPTS=true",
                        "AIVISION_HOME=/must/not/migrate",
                    ]
                )
                + "\n"
            )

            result = subprocess.run(
                install_command(root), text=True, capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            config_path = root / "observ-hermes.json"
            config = json.loads(config_path.read_text())
            self.assertTrue(config["enableTelemetry"])
            self.assertEqual(config["serviceName"], "legacy-hermes")
            self.assertEqual(config["collectionMode"], "privacy")
            self.assertEqual(config["otelExporter"]["protocol"], "http/protobuf")
            self.assertEqual(
                config["otelExporter"]["attributes"]["ant.username"], "legacy-user"
            )
            serialized = json.dumps(config)
            for forbidden in (
                "sessionDir",
                "replayDir",
                "AGENT_LOG",
                "AIVISION_HOME",
                "/must/not/migrate",
            ):
                self.assertNotIn(forbidden, serialized)
            self.assertFalse(config["debug"])
            self.assertFalse(legacy.exists())
            backup = root / "config-backups/observ-hermes.env.previous"
            self.assertTrue(backup.is_file())
            self.assertIn("OBSERV_HERMES_SESSION_DIR", result.stdout)
            if os.name == "posix":
                self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_existing_valid_json_is_preserved_and_legacy_is_not_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "observ-hermes.json"
            original = b'{"enableTelemetry": false, "serviceName": "user-owned"}\n'
            config_path.write_bytes(original)
            legacy = root / "observ-hermes.env"
            legacy.write_text(
                "AGENT_ENABLE_TELEMETRY=true\nOBSERV_HERMES_REPLAY_DIR=/ignored\n"
            )

            result = subprocess.run(
                install_command(root), text=True, capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config_path.read_bytes(), original)
            self.assertTrue(legacy.is_file())
            self.assertFalse(
                (root / "config-backups/observ-hermes.env.previous").exists()
            )

    def test_backup_conflict_fails_before_any_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "plugins/observ_hermes"
            destination.mkdir(parents=True)
            marker = destination / "old.txt"
            marker.write_text("old")
            legacy = root / "observ-hermes.env"
            legacy.write_text("AGENT_ENABLE_TELEMETRY=true\n")
            retained = root / "config-backups/observ-hermes.env.previous"
            retained.parent.mkdir()
            retained.write_text("old-backup")

            result = subprocess.run(
                install_command(root), text=True, capture_output=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(marker.read_text(), "old")
            self.assertEqual(legacy.read_text(), "AGENT_ENABLE_TELEMETRY=true\n")
            self.assertEqual(retained.read_text(), "old-backup")
            self.assertFalse((root / "observ-hermes.json").exists())

    def test_late_swap_failure_restores_legacy_source_and_created_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "plugins/observ_hermes"
            destination.mkdir(parents=True)
            marker = destination / "old.txt"
            marker.write_text("old")
            legacy = root / "observ-hermes.env"
            original = "AGENT_ENABLE_TELEMETRY=true\n"
            legacy.write_text(original)

            result = subprocess.run(
                install_command(root, "--fail-phase", "swap"),
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("simulated swap failure", result.stderr)
            self.assertEqual(marker.read_text(), "old")
            self.assertEqual(legacy.read_text(), original)
            self.assertFalse((root / "observ-hermes.json").exists())
            self.assertFalse(
                (root / "config-backups/observ-hermes.env.previous").exists()
            )

    def test_rollback_restores_plugin_and_legacy_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "plugins/observ_hermes"
            destination.mkdir(parents=True)
            (destination / "old.txt").write_text("old")
            legacy = root / "observ-hermes.env"
            original = "AGENT_ENABLE_TELEMETRY=true\n"
            legacy.write_text(original)

            installed = subprocess.run(
                install_command(root), text=True, capture_output=True
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            rolled_back = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "rollback",
                    "--destination",
                    str(destination),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
            self.assertEqual((destination / "old.txt").read_text(), "old")
            self.assertEqual(legacy.read_text(), original)
            self.assertFalse((root / "observ-hermes.json").exists())
            self.assertFalse(
                (root / "config-backups/observ-hermes.env.previous").exists()
            )


if __name__ == "__main__":
    unittest.main()
