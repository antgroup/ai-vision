"""Native SDK objects keep structured content across live and raw replay paths."""
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hermes-core"), str(ROOT / "hermes-replay")]
from aivision_hermes_core.semantic import MemorySink
from hermes_replay.capture import read_raw_transcript
from hermes_replay.replay import replay_envelopes
from observ_hermes.config import ObservHermesConfig
from observ_hermes.pipeline import CanonicalPipeline
from tests.replay_test_support import write_raw_transcript


@dataclass
class NativeTool:
    id: str
    name: str
    arguments: str
    function = property(lambda self: self)
    type = property(lambda self: "function")


@dataclass
class NativeResponse:
    content: str | None
    tool_calls: list | None
    finish_reason: str
    reasoning: str | None = None


class SdkMessageTests(unittest.TestCase):
    def check_message(self, message, expected):
        base = {"telemetry_schema_version": "hermes.observer.v1", "session_id": "native",
                "turn_id": "turn", "api_request_id": "api", "model": "model"}
        hooks = [("pre_llm_call", {**base, "user_message": "read"}),
                 ("pre_api_request", {**base, "started_at": 1750000000}),
                 ("post_api_request", {**base, "ended_at": 1750000001,
                    "assistant_message": message, "response": {"model": "model"}, "api_duration": 1,
                    "usage": {"input_tokens": 7, "output_tokens": 2}})]
        for mode in ("full", "privacy"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                live_sink = MemorySink()
                live = CanonicalPipeline(ObservHermesConfig(enable_telemetry=True, collection_mode=mode), live_sink)
                for hook, fields in hooks:
                    live.process(hook, fields)
                live.close()
                path = write_raw_transcript(Path(tmp), "native", hooks, collection_mode=mode)
                envelopes = read_raw_transcript(path)
                replay_sink = MemorySink()
                replay_envelopes(envelopes, replay_sink)
                shape = lambda sink: [{k: v for k, v in asdict(item).items() if k != "timestamp"} for item in sink.observations]
                self.assertEqual(shape(live_sink), shape(replay_sink))
                if mode == "full":
                    raw_message = envelopes[-1]["rawInput"]["assistant_message"]
                    if isinstance(message, dict):
                        self.assertEqual(raw_message, message)
                    elif isinstance(message, SimpleNamespace):
                        self.assertEqual(raw_message["content"], message.content)
                    else:
                        self.assertNotIn("role", raw_message)
                        self.assertEqual(raw_message["finish_reason"], message.finish_reason)
                        if message.tool_calls:
                            self.assertEqual(raw_message["tool_calls"][0]["name"], "read_file")
                            self.assertNotIn("function", raw_message["tool_calls"][0])
                        if getattr(message, "usage", None) is not None:
                            self.assertEqual(raw_message["usage"], {"prompt_tokens": 7, "completion_tokens": 2,
                                                                   "total_tokens": 9, "cached_tokens": 3})
                            self.assertEqual(raw_message["provider_data"]["token"], "[REDACTED]")
                    for item in live_sink.of_type("span.end") + live_sink.of_type("event"):
                        if item.key == "api:native:api" or item.name == "api_request":
                            self.assertEqual(json.loads(item.attributes["gen_ai.output.messages"]), [expected])

    def test_normalized_tools_and_text_and_old_dicts(self):
        tool = {"id": "tool-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"receipt.txt"}'}}
        expected = {"role": "assistant", "content": None, "tool_calls": [tool]}
        self.check_message(NativeResponse(None, [NativeTool("tool-1", "read_file", '{"path":"receipt.txt"}')], "tool_calls"), expected)
        self.check_message(NativeResponse("receipt: abc", None, "stop", "read complete"),
                           {"role": "assistant", "content": "receipt: abc", "tool_calls": [], "reasoning_content": "read complete"})
        self.check_message(expected, expected)
        self.check_message(SimpleNamespace(role="assistant", content="old SDK answer", tool_calls=None),
                           {"role": "assistant", "content": "old SDK answer", "tool_calls": None})

    def test_installed_official_transport_objects(self):
        try:
            from agent.transports.types import NormalizedResponse, ToolCall, Usage
        except ImportError:
            self.skipTest("official Hermes runtime is not installed")
        message = NormalizedResponse(content=None, tool_calls=[ToolCall(id="native-id", name="read_file", arguments='{"path":"receipt.txt"}')],
                                     finish_reason="tool_calls", usage=Usage(prompt_tokens=7, completion_tokens=2, total_tokens=9, cached_tokens=3),
                                     provider_data={"token": "private-credential-value"})
        self.check_message(message, {"role": "assistant", "content": None, "tool_calls": [
            {"id": "native-id", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"receipt.txt"}'}}]})


if __name__ == "__main__":
    unittest.main()
