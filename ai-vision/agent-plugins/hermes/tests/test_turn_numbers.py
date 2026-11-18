"""Collector turn numbering survives fresh processes without reading history."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from observ_hermes.config import ObservHermesConfig
from observ_hermes.pipeline import CanonicalPipeline

CHILD = r'''
import json, sys
from observ_hermes.config import ObservHermesConfig
from observ_hermes.pipeline import CanonicalPipeline
config = ObservHermesConfig(enable_telemetry=True, config_path=sys.argv[1])
config.otel_exporter.endpoint = sys.argv[3]
pipeline = CanonicalPipeline(config)
for turn_id in json.loads(sys.argv[2]):
    base = {"session_id": "native-session", "turn_id": turn_id}
    pipeline.process("pre_llm_call", {**base, "user_message": "private user text",
        "conversation_history": ([{"role":"system", "content":"[Compressed summary]"}]
            if turn_id == "native-c" else [{"role":"user", "content":"internal continue"}] * 9),
        "is_first_turn": False})
    for request in ("initial", "continuation"):
        fields = {**base, "api_request_id": turn_id + request, "model":"model", "provider":"provider"}
        pipeline.process("pre_api_request", fields)
        pipeline.process("post_api_request", {**fields, "usage":{"input_tokens":7,"output_tokens":3}})
    pipeline.process("on_session_end", {**base, "completed":True})
pipeline.process("on_session_finalize", {"session_id":"native-session"})
print(json.dumps([{"name":o.name,"parent":o.parent_key,"key":o.key,"attributes":o.attributes}
    for o in pipeline.memory_sink.observations if o.type in ("span.start", "event")]))
pipeline.close()
'''

class TurnNumberTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.config_path = self.home / "observ-hermes.json"

    def child(self, turns, endpoint="http://collector.invalid"):
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
        run = subprocess.run([sys.executable, "-c", CHILD, str(self.config_path), json.dumps(turns), endpoint],
            env=env, capture_output=True, text=True, check=True)
        return json.loads(run.stdout)

    @staticmethod
    def roots(rows):
        return [row for row in rows if row["attributes"].get("gen_ai.span.kind") == "CHAT"]

    def test_three_turns_across_two_processes_ignore_history_and_continuations(self):
        first = self.child(["native-a", "native-b"])
        second = self.child(["native-c"])
        roots = self.roots(first + second)
        self.assertEqual([row["attributes"]["agentic.turn.number"] for row in roots], [1, 2, 3])
        self.assertEqual([row["name"] for row in roots], ["Turn 1", "Turn 2", "Turn 3"])
        self.assertEqual([row["attributes"]["gen_ai.chat.id"] for row in roots], ["native-a", "native-b", "native-c"])
        for row in first + second:
            attrs = row["attributes"]
            if attrs.get("gen_ai.span.kind") == "LLM":
                self.assertEqual(row["parent"], "turn:native-session:" + attrs["gen_ai.chat.id"])
        api = [row for row in first + second if row["name"] == "api_request"]
        self.assertEqual(len(api), 6)
        self.assertEqual(sum(row["attributes"]["gen_ai.usage.input_tokens"] for row in api), 42)
        self.assertEqual(second[-1]["attributes"]["agentic.turn.number"], 3)

    def test_same_native_turn_is_idempotent_across_processes(self):
        self.child(["native-a", "native-b"])
        rows = self.roots(self.child(["native-a", "native-c"]))
        self.assertEqual([row["attributes"]["agentic.turn.number"] for row in rows], [1, 3])

    def test_different_export_destination_has_an_independent_sequence(self):
        self.child(["native-a", "native-b"])
        roots = self.roots(self.child(["native-c"], "http://another-collector.invalid"))
        self.assertEqual(roots[0]["attributes"]["agentic.turn.number"], 1)

    def test_concurrent_processes_allocate_unique_ordinals(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(lambda index: self.child([f"native-{index}"]), range(12)))
        self.assertEqual(sorted(self.roots(row)[0]["attributes"]["agentic.turn.number"] for row in rows), list(range(1, 13)))

    def test_state_is_private_and_contains_no_raw_identity_or_content(self):
        mode = self.home.stat().st_mode & 0o777
        self.child(["native-a"])
        state = self.home / "observ-hermes-state" / "turn-numbers.sqlite3"
        self.assertTrue(state.is_file())
        if os.name == "posix":
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)
            self.assertEqual(state.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.home.stat().st_mode & 0o777, mode)
        raw = state.read_bytes()
        for text in (b"native-session", b"native-a", b"private user text", b"collector.invalid"):
            self.assertNotIn(text, raw)

    def test_deleted_live_ledger_is_not_recreated_with_default_permissions(self):
        pipeline = CanonicalPipeline(ObservHermesConfig(enable_telemetry=True, config_path=str(self.config_path)))
        pipeline.process("pre_llm_call", {"session_id":"s", "turn_id":"t1"})
        pipeline.process("on_session_end", {"session_id":"s", "turn_id":"t1"})
        state = self.home / "observ-hermes-state" / "turn-numbers.sqlite3"
        state.unlink()
        with self.assertRaises(sqlite3.OperationalError):
            pipeline.process("pre_llm_call", {"session_id":"s", "turn_id":"t2"})
        self.assertFalse(state.exists())
        pipeline.close()

    def test_disabled_pipeline_does_not_create_state(self):
        pipeline = CanonicalPipeline(ObservHermesConfig(config_path=str(self.config_path)))
        pipeline.process("pre_llm_call", {"session_id":"s", "turn_id":"t"})
        pipeline.close()
        self.assertFalse((self.home / "observ-hermes-state").exists())

    def test_corrupt_state_does_not_emit_a_restarted_turn_one(self):
        state = self.home / "observ-hermes-state" / "turn-numbers.sqlite3"
        state.parent.mkdir(mode=0o700)
        state.write_text("corrupt state")
        pipeline = CanonicalPipeline(ObservHermesConfig(enable_telemetry=True, config_path=str(self.config_path)))
        with self.assertRaises(sqlite3.DatabaseError):
            pipeline.process("pre_llm_call", {"session_id":"s", "turn_id":"t"})
        self.assertFalse(pipeline.memory_sink.of_type("span.start"))
        self.assertEqual(state.read_text(), "corrupt state")
        pipeline.close()

    def test_registered_hooks_fail_open_when_state_cannot_be_created(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import observ_hermes
        config = ObservHermesConfig(enable_telemetry=True, config_path=str(self.config_path))
        runtime = SimpleNamespace(config=config, force_flush=lambda: None, shutdown=lambda: None)
        hooks = {}
        context = SimpleNamespace(register_hook=lambda name, callback: hooks.__setitem__(name, callback))
        (self.home / "observ-hermes-state").write_text("user-owned file")
        with patch.object(observ_hermes.ObservHermesConfig, "from_env", return_value=config), patch.object(
            observ_hermes, "init_telemetry", return_value=runtime
        ):
            observ_hermes.register(context)
        try:
            with self.assertLogs("observ_hermes", level="WARNING"):
                hooks["on_session_start"](session_id="s")
                hooks["pre_llm_call"](session_id="s", turn_id="t")
                hooks["pre_api_request"](session_id="s", turn_id="t", api_request_id="r")
                hooks["post_api_request"](session_id="s", turn_id="t", api_request_id="r")
                hooks["on_session_end"](session_id="s", turn_id="t")
                hooks["on_session_finalize"](session_id="s")
            self.assertEqual(runtime._observ_hermes_hook_failures["pre_llm_call:FileExistsError"], 1)
            self.assertFalse(runtime._observ_hermes_pipeline.memory_sink.of_type("event"))
            self.assertFalse(runtime._observ_hermes_pipeline.memory_sink.of_type("span.start"))
        finally:
            observ_hermes._shutdown()

    def test_replay_never_reads_or_changes_production_state(self):
        sys.path.insert(0, str(ROOT / "hermes-replay"))
        from hermes_replay.replay import replay_envelopes
        from aivision_hermes_core.semantic import MemorySink
        self.child(["native-a", "native-b"])
        state = self.home / "observ-hermes-state" / "turn-numbers.sqlite3"
        before = state.read_bytes()
        sink = MemorySink()
        replay_envelopes([{"hook":"pre_llm_call", "capturedAt":"2026-10-15T00:00:00Z",
            "rawInput":{"session_id":"native-session", "turn_id":"native-c"}}], sink)
        self.assertEqual(sink.of_type("span.start")[0].attributes["agentic.turn.number"], 1)
        self.assertEqual(state.read_bytes(), before)

if __name__ == "__main__":
    unittest.main()
