from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERMES_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERMES_ROOT / "hermes-replay"))
sys.path.insert(0, str(HERMES_ROOT / "hermes-core"))

from hermes_replay.config import default_session_dir  # noqa: E402

from observ_hermes import config  # noqa: E402
from tests.replay_test_support import write_raw_transcript  # noqa: E402


class ArtifactPathTests(unittest.TestCase):
    def test_replay_default_and_empty_aivision_home_use_home_directory(self) -> None:
        home = Path("/test/home")
        self.assertEqual(
            default_session_dir({}, home),
            home / ".aivision/hermes/transcripts",
        )
        self.assertEqual(
            default_session_dir({"AIVISION_HOME": "  "}, home),
            home / ".aivision/hermes/transcripts",
        )

    def test_replay_explicit_aivision_root_wins(self) -> None:
        self.assertEqual(
            default_session_dir({"AIVISION_HOME": "/shared"}),
            Path("/shared/hermes/transcripts"),
        )

    def test_production_runtime_has_no_transcript_or_replay_path_contract(self) -> None:
        with patch.dict(os.environ, {"AIVISION_HOME": "/shared"}, clear=True):
            runtime = config._build_config({})
        self.assertFalse(hasattr(runtime, "session_dir"))
        self.assertFalse(hasattr(runtime, "replay_dir"))

    def test_production_debug_and_legacy_path_overrides_cannot_enable_artifacts(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {"AIVISION_HOME": "/shared", "OBSERV_HERMES_DEBUG": "true"},
            clear=True,
        ):
            runtime = config._build_config({})
        self.assertTrue(runtime.debug)
        self.assertFalse(hasattr(runtime, "session_dir"))
        self.assertFalse(hasattr(runtime, "replay_dir"))

        with patch.dict(
            os.environ,
            {
                "AIVISION_HOME": "/shared",
                "OBSERV_HERMES_DEBUG": "true",
                "OBSERV_HERMES_SESSION_DIR": "/custom/transcripts",
                "OBSERV_HERMES_REPLAY_DIR": "/custom/replays",
            },
            clear=True,
        ):
            runtime = config._build_config({})
        self.assertTrue(runtime.debug)
        self.assertFalse(hasattr(runtime, "session_dir"))
        self.assertFalse(hasattr(runtime, "replay_dir"))


@unittest.skipUnless(os.name == "posix", "POSIX permission assertions")
class TranscriptPermissionTests(unittest.TestCase):
    def test_replay_transcript_file_is_private_and_existing_directory_mode_is_preserved(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "transcripts"
            session_dir.mkdir(mode=0o755)
            output = write_raw_transcript(
                session_dir,
                "session-123",
                [
                    (
                        "on_session_start",
                        {
                            "telemetry_schema_version": "hermes.observer.v1",
                            "session_id": "session-123",
                            "model": "model",
                        },
                    )
                ],
            )
            self.assertTrue(output.is_file())
            self.assertEqual(session_dir.stat().st_mode & 0o777, 0o755)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
