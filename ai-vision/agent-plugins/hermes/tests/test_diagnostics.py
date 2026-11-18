from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from observ_hermes.config import ObservHermesConfig
from observ_hermes.diagnostics import (
    BACKUP_COUNT,
    LOG_FILE_NAME,
    MAX_LOG_BYTES,
    close_diagnostics,
    configure_diagnostics,
)


class DiagnosticLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger("observ_hermes.test_diagnostics")
        self.logger.propagate = False
        close_diagnostics(target_logger=self.logger)

    def tearDown(self) -> None:
        close_diagnostics(target_logger=self.logger)

    def test_debug_controls_verbosity_and_redacts_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "logs"
            config = ObservHermesConfig(log_dir=str(root), debug=False)
            path = configure_diagnostics(config, target_logger=self.logger)
            self.logger.debug("debug authorization=%s", "Bearer debug-secret")
            self.logger.info(
                "token=%s password=%s", "sk-private-secret-value", "hunter2"
            )
            close_diagnostics(target_logger=self.logger)

            assert path is not None
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("debug authorization", content)
            self.assertNotIn("private-secret", content)
            self.assertNotIn("hunter2", content)
            self.assertIn("[REDACTED]", content)

            config.debug = True
            configure_diagnostics(config, target_logger=self.logger)
            self.logger.debug("diagnostic detail count=%d", 3)
            close_diagnostics(target_logger=self.logger)
            self.assertIn("diagnostic detail count=3", path.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "posix", "POSIX permission assertions")
    def test_file_is_private_and_existing_directory_mode_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "logs"
            root.mkdir(mode=0o755)
            path = configure_diagnostics(
                ObservHermesConfig(log_dir=str(root)),
                target_logger=self.logger,
            )
            self.logger.info("started")
            close_diagnostics(target_logger=self.logger)

            assert path is not None
            self.assertEqual(root.stat().st_mode & 0o777, 0o755)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rotation_bounds_files_and_creates_no_replay_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "diagnostics"
            path = configure_diagnostics(
                ObservHermesConfig(log_dir=str(log_dir), debug=True),
                target_logger=self.logger,
            )
            for index in range(80):
                self.logger.debug("record=%d body=%s", index, "x" * 4096)
            close_diagnostics(target_logger=self.logger)

            assert path is not None
            files = sorted(log_dir.glob(f"{LOG_FILE_NAME}*"))
            self.assertLessEqual(len(files), BACKUP_COUNT + 1)
            self.assertTrue(all(item.stat().st_size <= MAX_LOG_BYTES for item in files))
            self.assertFalse((root / "transcripts").exists())
            self.assertFalse((root / "replays").exists())
            self.assertFalse(list(root.rglob("*.jsonl")))

    def test_production_registration_activates_only_owned_diagnostics(self) -> None:
        import observ_hermes

        class Runtime:
            _observ_hermes_pipeline = None

            def force_flush(self) -> None:
                pass

            def shutdown(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ObservHermesConfig(
                enable_telemetry=True,
                debug=True,
                log_dir=str(root / "logs"),
            )
            context = SimpleNamespace(hooks={})
            context.register_hook = lambda name, callback: context.hooks.__setitem__(
                name, callback
            )
            with (
                patch.object(
                    observ_hermes.ObservHermesConfig,
                    "from_env",
                    return_value=config,
                ),
                patch.object(observ_hermes, "init_telemetry", return_value=Runtime()),
            ):
                observ_hermes.register(context)
                observ_hermes._shutdown()

            content = (root / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")
            self.assertIn("production diagnostics configured", content)
            self.assertIn("registered", content)
            self.assertFalse((root / "transcripts").exists())
            self.assertFalse((root / "replays").exists())
            self.assertFalse(list(root.rglob("*.jsonl")))


if __name__ == "__main__":
    unittest.main()
