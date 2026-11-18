from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import hermes_replay
from hermes_replay.capture import read_raw_transcript
from hermes_replay.config import ReplayConfig, ReplayConfigError

from tests.replay_test_support import write_raw_transcript


class Context:
    def __init__(self) -> None:
        self.hooks = {}

    def register_hook(self, name, callback) -> None:
        self.hooks[name] = callback


class ReplayCaptureConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        hermes_replay._shutdown()

    def test_auxiliary_hooks_are_captured_only_when_the_host_exposes_them(self):
        auxiliary = {"pre_auxiliary_call", "post_auxiliary_call"}
        for supported in (False, True):
            with self.subTest(supported=supported), tempfile.TemporaryDirectory() as tmp:
                context = Context()
                config = ReplayConfig(capture_enabled=True, session_dir=Path(tmp))
                host = SimpleNamespace(VALID_HOOKS=set(hermes_replay.HOOKS) | (auxiliary if supported else set()))
                with patch.object(ReplayConfig, "load", return_value=config), patch.dict(sys.modules, {"hermes_cli.plugins": host}):
                    hermes_replay.register(context)
                self.assertEqual(set(context.hooks), set(hermes_replay.HOOKS) | (auxiliary if supported else set()))
                if supported:
                    fields = {"telemetry_schema_version": "hermes.observer.v1",
                              "session_id": "native", "turn_id": "turn", "api_request_id": "aux-native",
                              "aux_task": "title_generation", "retry_count": 0, "streaming": False}
                    context.hooks["pre_auxiliary_call"](**fields, model="aux", request_messages=[])
                    context.hooks["post_auxiliary_call"](**fields, usage={"input_tokens": 16, "output_tokens": 4},
                        response={"assistant_message": {"role": "assistant", "content": "Title"}}, error=None)
                    hermes_replay._shutdown()
                    records = read_raw_transcript(next(Path(tmp).glob("*.jsonl")))
                    self.assertEqual([item["hook"] for item in records], ["pre_auxiliary_call", "post_auxiliary_call"])
                    self.assertEqual(records[1]["rawOutput"]["assistant_message"]["content"], "Title")
                    self.assertEqual(records[0]["correlation"]["turn_id"], "turn")

    def test_disabled_default_registers_no_hooks_and_resolves_no_artifact_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_root = root / "artifacts"
            context = Context()
            with patch.dict(
                os.environ,
                {
                    "HERMES_HOME": str(root / "hermes"),
                    "AIVISION_HOME": str(artifact_root),
                },
                clear=True,
            ):
                hermes_replay.register(context)
            self.assertEqual(context.hooks, {})
            self.assertFalse(artifact_root.exists())

    def test_config_file_is_independent_strict_and_environment_has_priority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hermes_home = root / "hermes"
            hermes_home.mkdir()
            path = hermes_home / "hermes-replay.json"
            path.write_text(
                json.dumps(
                    {
                        "captureEnabled": True,
                        "collectionMode": "privacy",
                        "sessionDir": str(root / "file-transcripts"),
                        "sessionName": "file",
                        "debug": False,
                    }
                )
            )
            config = ReplayConfig.load(
                environ={
                    "HERMES_HOME": str(hermes_home),
                    "HERMES_REPLAY_COLLECTION_MODE": "full",
                    "HERMES_REPLAY_SESSION_DIR": str(root / "env-transcripts"),
                    "HERMES_REPLAY_SESSION_NAME": "env",
                    "HERMES_REPLAY_DEBUG": "true",
                }
            )
            self.assertTrue(config.capture_enabled)
            self.assertEqual(config.collection_mode, "full")
            self.assertEqual(config.session_dir, root / "env-transcripts")
            self.assertEqual(config.session_name, "env")
            self.assertTrue(config.debug)

            invalid_values = (
                {"captureEnabled": "false"},
                {"collectionMode": "metadata"},
                {"sessionDir": 42},
                {"sessionName": []},
                {"debug": 1},
                {"unknown": True},
            )
            for invalid in invalid_values:
                with self.subTest(invalid=invalid):
                    path.write_text(json.dumps(invalid))
                    with self.assertRaises(ReplayConfigError):
                        ReplayConfig.load(environ={"HERMES_HOME": str(hermes_home)})

    def test_explicit_empty_artifact_strings_match_schema_min_length(self) -> None:
        schema_path = (
            Path(__file__).parents[2] / "docs/config/hermes-replay.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        for field in ("sessionDir", "sessionName"):
            self.assertEqual(schema["properties"][field]["minLength"], 1)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hermes_home = root / "hermes"
            hermes_home.mkdir()
            config_path = hermes_home / "hermes-replay.json"
            for field in ("sessionDir", "sessionName"):
                with self.subTest(source="file", field=field):
                    config_path.write_text(json.dumps({field: ""}))
                    with self.assertRaisesRegex(ReplayConfigError, "non-empty"):
                        ReplayConfig.load(environ={"HERMES_HOME": str(hermes_home)})

            config_path.unlink()
            for variable in ("HERMES_REPLAY_SESSION_DIR", "HERMES_REPLAY_SESSION_NAME"):
                with self.subTest(source="environment", variable=variable):
                    with self.assertRaisesRegex(ReplayConfigError, "non-empty"):
                        ReplayConfig.load(
                            environ={
                                "HERMES_HOME": str(hermes_home),
                                variable: "",
                            }
                        )

            config = ReplayConfig.load(
                environ={"HERMES_HOME": str(hermes_home)},
                home=root,
            )
            self.assertEqual(config.session_name, "")
            self.assertIsNone(config.session_dir)

            enabled = ReplayConfig.load(
                environ={
                    "HERMES_HOME": str(hermes_home),
                    "HERMES_REPLAY_CAPTURE_ENABLED": "true",
                },
                home=root,
            )
            self.assertEqual(enabled.session_name, "")
            self.assertEqual(enabled.session_dir, root / ".aivision/hermes/transcripts")

    def test_explicit_enablement_registers_capture_hooks_and_writes_raw_v1(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = Context()
            config = ReplayConfig(
                capture_enabled=True,
                collection_mode="full",
                session_dir=root,
            )
            with patch.object(ReplayConfig, "load", return_value=config):
                hermes_replay.register(context)
            self.assertEqual(set(context.hooks), set(hermes_replay.HOOKS))
            context.hooks["on_session_start"](
                telemetry_schema_version="hermes.observer.v1",
                session_id="capture-session",
                model="model",
            )
            hermes_replay._shutdown()
            transcript = next(root.glob("session_capture-session_*.jsonl"))
            envelope = read_raw_transcript(transcript)[0]
            self.assertEqual(envelope["schemaVersion"], "hermes.replay.raw.v1")
            self.assertEqual(envelope["plugin"], "hermes-replay")
            self.assertEqual(envelope["hook"], "on_session_start")

    def test_full_and_privacy_capture_redact_credentials_before_write(self) -> None:
        canary = "prompt-canary"
        secret = "Bearer capture-secret"
        schema = {
            "telemetry_schema_version": "hermes.observer.v1",
            "session_id": "privacy-session",
            "turn_id": "turn-1",
            "user_message": canary,
            "request": {"headers": {"authorization": secret}, "prompt": canary},
            "approx_input_tokens": 7,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full = write_raw_transcript(
                root / "full",
                "privacy-session",
                [("pre_api_request", schema)],
                collection_mode="full",
            )
            privacy = write_raw_transcript(
                root / "privacy",
                "privacy-session",
                [("pre_api_request", schema)],
                collection_mode="privacy",
            )
            full_text = full.read_text()
            privacy_text = privacy.read_text()
            self.assertIn(canary, full_text)
            self.assertNotIn(secret, full_text)
            self.assertNotIn(canary, privacy_text)
            self.assertNotIn(secret, privacy_text)
            privacy_record = read_raw_transcript(privacy)[0]
            self.assertEqual(privacy_record["rawInput"]["approx_input_tokens"], 7)
            self.assertTrue(privacy_record["redaction"]["contentRedacted"])


if __name__ == "__main__":
    unittest.main()
