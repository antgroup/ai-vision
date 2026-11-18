from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

HERMES_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERMES_ROOT / "hermes-replay"))
sys.path.insert(0, str(HERMES_ROOT / "hermes-core"))

from aivision_hermes_core.privacy import privacy_marker  # noqa: E402
from hermes_replay.capture import read_raw_transcript  # noqa: E402
from hermes_replay.replay import replay_envelopes  # noqa: E402

from observ_hermes.config import AntIdentity, ObservHermesConfig  # noqa: E402
from observ_hermes.pipeline import CanonicalPipeline, semantic_config  # noqa: E402
from observ_hermes.semantic import MemorySink  # noqa: E402
from tests.replay_test_support import write_raw_transcript  # noqa: E402


def captured_hooks() -> dict[str, dict]:
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/hooks/v0.18.2/observer_envelopes.json"
        ).read_text()
    )
    return {entry["hook"]: entry["envelope"] for entry in fixture["hooks"]}


class LiveReplayParityTests(unittest.TestCase):
    @staticmethod
    def semantic_shape(observations):
        return [
            {key: value for key, value in asdict(item).items() if key != "timestamp"}
            for item in observations
        ]

    def test_auxiliary_responses_system_prompt_preserves_live_replay_privacy(self):
        instructions = "Classify the opening message without answering it."
        base = {"telemetry_schema_version": "hermes.observer.v1", "session_id": "native", "turn_id": "turn"}
        aux = {**base, "api_request_id": "aux", "model": "model", "provider": "custom",
               "aux_task": "title_generation", "retry_count": 0, "api_mode": "responses"}
        for mode, direct in (("full", True), ("privacy", True), ("full", False)):
            hooks = [("pre_llm_call", {**base, "user_message": "read"}),
                ("pre_auxiliary_call", {**aux, "request_messages": [{"role": "user", "content": "read"}],
                    "request": {"body": {"instructions": instructions}},
                    **({"system_prompt": instructions} if direct else {})}),
                ("post_auxiliary_call", {**aux, "api_duration": 0.01, "usage": None, "response": None})]
            with self.subTest(mode=mode, direct=direct), tempfile.TemporaryDirectory() as tmp:
                live_sink = MemorySink()
                live = CanonicalPipeline(ObservHermesConfig(enable_telemetry=True, collection_mode=mode), live_sink)
                for hook, raw in hooks: live.process(hook, raw)
                live.close()
                path = write_raw_transcript(Path(tmp), "native", hooks, collection_mode=mode)
                replay_sink = MemorySink()
                replay_envelopes(read_raw_transcript(path), replay_sink)
                self.assertEqual(self.semantic_shape(live_sink.observations), self.semantic_shape(replay_sink.observations))
                expected = instructions if mode == "full" else privacy_marker(instructions)
                start = next(item for item in live_sink.of_type("span.start") if item.span_kind == "LLM")
                event = next(item for item in live_sink.of_type("event") if item.name == "api_request")
                for item in (start, event):
                    self.assertEqual(json.loads(item.attributes["gen_ai.system_instructions"]), expected)

    def test_deferred_auxiliary_after_finalize_has_live_replay_parity(self):
        base = {"telemetry_schema_version": "hermes.observer.v1", "session_id": "native"}
        turn = {**base, "turn_id": "turn"}
        auxiliary = {**turn, "api_request_id": "aux-native", "model": "aux",
                     "provider": "custom", "aux_task": "title_generation", "retry_count": 0,
                     "streaming": False}
        hooks = [
            ("pre_llm_call", {**turn, "user_message": "read receipt"}),
            ("on_session_end", turn),
            ("on_session_finalize", {**base, "reason": "cli_exit"}),
            ("pre_auxiliary_call", {**auxiliary, "request_messages": [{"role": "user", "content": "title input"}]}),
            ("post_auxiliary_call", {**auxiliary, "api_duration": 0.01, "usage": {"input_tokens": 16, "output_tokens": 4},
                "response": {"assistant_message": {"role": "assistant", "content": "Receipt reading"}}, "error": None}),
        ]
        for mode, failed in (("full", False), ("privacy", False), ("full", True), ("privacy", True)):
            inputs = [(hook, dict(fields)) for hook, fields in hooks]
            if failed:
                inputs[-1][1].update(response=None, usage=None, error="APIConnectionError: Connection error.", error_type="APIConnectionError")
            with self.subTest(mode=mode, failed=failed), tempfile.TemporaryDirectory() as tmp:
                live_sink = MemorySink()
                live = CanonicalPipeline(ObservHermesConfig(enable_telemetry=True, collection_mode=mode), live_sink)
                for hook, fields in inputs:
                    live.process(hook, fields)
                live.close()
                transcript = write_raw_transcript(Path(tmp), "native", inputs, collection_mode=mode)
                replay_sink = MemorySink()
                replay_envelopes(read_raw_transcript(transcript), replay_sink)
                self.assertEqual(self.semantic_shape(live_sink.observations), self.semantic_shape(replay_sink.observations))
                starts = replay_sink.of_type("span.start")
                self.assertEqual(len(starts), 2)
                self.assertEqual(starts[-1].parent_key, starts[0].key)
                self.assertEqual(replay_sink.of_type("span.end")[-1].status, "ERROR" if failed else "OK")

    def test_live_and_replay_emit_identical_semantic_observations(self) -> None:
        order = [
            "on_session_start",
            "pre_llm_call",
            "pre_api_request",
            "post_api_request",
            "pre_tool_call",
            "post_tool_call",
            "subagent_start",
            "subagent_stop",
            "post_llm_call",
            "on_session_end",
            "on_session_finalize",
        ]
        captured = captured_hooks()
        with tempfile.TemporaryDirectory() as tmp:
            config = ObservHermesConfig(enable_telemetry=True)
            live_sink = MemorySink()
            live = CanonicalPipeline(config, live_sink)
            for hook in order:
                live.process(hook, captured[hook])
            live.close()

            session_id = captured["on_session_start"]["session_id"]
            transcript = write_raw_transcript(
                Path(tmp),
                session_id,
                [(hook, captured[hook]) for hook in order],
            )
            replay_sink = MemorySink()
            replay_envelopes(read_raw_transcript(transcript), replay_sink)

            self.assertEqual(
                self.semantic_shape(live_sink.observations),
                self.semantic_shape(replay_sink.observations),
            )
            self.assertTrue(
                any(item.span_kind == "AGENT" for item in replay_sink.observations)
            )

    def test_non_default_production_identity_does_not_change_core_parity(self) -> None:
        captured = captured_hooks()
        hooks = [
            ("on_session_start", captured["on_session_start"]),
            ("pre_llm_call", captured["pre_llm_call"]),
            ("on_session_end", captured["on_session_end"]),
        ]
        config = ObservHermesConfig(
            enable_telemetry=True,
            ant=AntIdentity(
                username="configured-user",
                uid="configured-uid",
                agent_type="configured-type",
                agent_product="Configured Product",
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            live_sink = MemorySink()
            live = CanonicalPipeline(config, live_sink)
            for hook, payload in hooks:
                live.process(hook, payload)
            live.close()

            transcript = write_raw_transcript(
                Path(tmp), captured["on_session_start"]["session_id"], hooks
            )
            replay_sink = MemorySink()
            replay_envelopes(read_raw_transcript(transcript), replay_sink)

        self.assertEqual(
            self.semantic_shape(live_sink.observations),
            self.semantic_shape(replay_sink.observations),
        )
        core_with_identity = next(
            item
            for item in live_sink.observations
            if "ant.username" in item.attributes and "user.id" in item.attributes
        )
        self.assertEqual(core_with_identity.attributes["ant.username"], "unknown")
        self.assertEqual(core_with_identity.attributes.get("user.id"), "unknown")
        self.assertEqual(semantic_config(config).agent_type, "hermes")
        self.assertEqual(semantic_config(config).agent_product, "Hermes")

    def test_privacy_capture_markers_replay_without_double_hashing(self) -> None:
        session_id = "privacy-parity"
        secret_prompt = "sensitive prompt for parity"
        schema = {
            "telemetry_schema_version": "hermes.observer.v1",
            "session_id": session_id,
        }
        hooks = [
            ("on_session_start", {**schema, "model": "model", "platform": "cli"}),
            (
                "pre_llm_call",
                {
                    **schema,
                    "turn_id": "turn-1",
                    "model": "model",
                    "user_message": secret_prompt,
                },
            ),
            ("on_session_end", {**schema, "turn_id": "turn-1", "completed": True}),
        ]
        config = ObservHermesConfig(
            enable_telemetry=True,
            collection_mode="privacy",
            ant=AntIdentity(username="private-user", uid="private-uid"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            live_sink = MemorySink()
            live = CanonicalPipeline(config, live_sink)
            for hook, payload in hooks:
                live.process(hook, payload)
            live.close()

            transcript = write_raw_transcript(
                Path(tmp), session_id, hooks, collection_mode="privacy"
            )
            envelopes = read_raw_transcript(transcript)
            captured_marker = envelopes[1]["rawInput"]["user_message"]
            replay_sink = MemorySink()
            replay_envelopes(envelopes, replay_sink)

        expected_marker = privacy_marker(secret_prompt)
        self.assertEqual(captured_marker, expected_marker)
        self.assertEqual(
            self.semantic_shape(live_sink.observations),
            self.semantic_shape(replay_sink.observations),
        )
        rendered = json.dumps(
            self.semantic_shape(replay_sink.observations), sort_keys=True
        )
        self.assertIn(expected_marker, rendered)
        self.assertNotIn(privacy_marker(expected_marker), rendered)

    def test_idless_tool_persists_one_synthetic_identity_into_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ObservHermesConfig(enable_telemetry=True)
            schema = {
                "telemetry_schema_version": "hermes.observer.v1",
                "session_id": "idless",
            }
            live_sink = MemorySink()
            live = CanonicalPipeline(config, live_sink)
            hooks = (
                ("on_session_start", {**schema, "model": "model"}),
                (
                    "pre_llm_call",
                    {**schema, "turn_id": "turn-1", "user_message": "read"},
                ),
                (
                    "pre_tool_call",
                    {**schema, "turn_id": "turn-1", "tool_name": "Read", "args": {}},
                ),
                (
                    "post_tool_call",
                    {
                        **schema,
                        "turn_id": "turn-1",
                        "tool_name": "Read",
                        "result": "ok",
                        "duration_ms": 1,
                    },
                ),
                ("on_session_end", {**schema, "turn_id": "turn-1", "completed": True}),
            )
            for hook, payload in hooks:
                live.process(hook, payload)
            live.close()
            transcript = write_raw_transcript(Path(tmp), "idless", hooks)
            replay_sink = MemorySink()
            replay_envelopes(read_raw_transcript(transcript), replay_sink)
            self.assertEqual(
                self.semantic_shape(live_sink.observations),
                self.semantic_shape(replay_sink.observations),
            )
            synthetic = {
                item.attributes.get("gen_ai.tool.call.id")
                for item in replay_sink.observations
                if item.attributes.get("agentic.tool.call_id.synthetic") == "true"
            }
            self.assertEqual(len(synthetic), 1)

    def test_replay_module_has_no_hook_adapter_dependency(self) -> None:
        source = (HERMES_ROOT / "hermes-replay/hermes_replay/replay.py").read_text()
        tree = ast.parse(source)
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertFalse(any(module.startswith("observ_hermes") for module in modules))
        self.assertNotIn("submit_hook", source)


if __name__ == "__main__":
    unittest.main()
