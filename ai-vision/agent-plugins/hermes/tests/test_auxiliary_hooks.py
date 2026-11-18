"""Forward-compatible auxiliary attempts retain native identity and honest usage."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hermes-core"))
from aivision_hermes_core.normalize import CanonicalNormalizer
from aivision_hermes_core.semantic import MemorySink, SemanticEngine


class AuxiliaryHookTests(unittest.TestCase):
    def setUp(self):
        self.normalizer = CanonicalNormalizer()
        self.sink = MemorySink()
        self.engine = SemanticEngine(sink=self.sink)
        self.index = 0
        self.common = {"session_id": "native", "turn_id": "turn", "api_request_id": "call"}
        self.feed("pre_llm_call", user_message="read receipt")

    def feed(self, hook, **fields):
        self.index += 1
        try:
            event = self.normalizer.normalize(hook, {**self.common, **fields}, timestamp=1750000000 + self.index)
        except ValueError as error:
            self.fail(str(error))
        self.engine.apply(event)

    def test_retry_attempts_do_not_collide_with_main_call(self):
        self.feed("pre_api_request", model="main", provider="nvidia")
        for retry in (0, 1):
            self.feed("pre_auxiliary_call", model="aux", provider="nvidia", aux_task="compression", retry_count=retry, request_messages=[{"role": "user", "content": "compress"}])
            self.feed("post_auxiliary_call", aux_task="compression", retry_count=retry,
                      error="RuntimeError: failed" if retry == 0 else None,
                      usage=None if retry == 0 else {"input_tokens": 11, "output_tokens": 3},
                      response={"assistant_message": {"role": "assistant", "content": "compressed"}})
        self.feed("post_api_request", reported_usage={"prompt_tokens": 20, "completion_tokens": 7})
        starts = [item for item in self.sink.of_type("span.start") if item.span_kind == "LLM"]
        ends = [item for item in self.sink.of_type("span.end") if item.key.startswith("api:")]
        self.assertEqual(len(starts), 3)
        self.assertEqual(len({item.key for item in starts}), 3)
        self.assertTrue(all(item.parent_key == "turn:native:turn" for item in starts))
        self.assertTrue(all(item.attributes.get("agentic.model_call.id") == "call" for item in starts))
        self.assertEqual(sorted(item.status for item in ends), ["ERROR", "OK", "OK"])
        self.assertEqual(self.engine.state.pending_counts()["api_requests"], 0)
        self.assertTrue(any("compressed" in item.attributes.get("gen_ai.output.messages", "") for item in ends))

    def test_normalized_auxiliary_zero_and_streamed_usage_remain_unknown(self):
        for attempt, usage in enumerate(({"input_tokens": 0, "output_tokens": 0}, None)):
            self.feed("pre_auxiliary_call", model="aux", provider="nvidia", aux_task="title", retry_count=attempt)
            self.feed("post_auxiliary_call", aux_task="title", retry_count=attempt, usage=usage, streaming=usage is None)
        ends = self.sink.of_type("span.end")
        self.assertEqual(len(ends), 2)
        for item in ends:
            self.assertEqual(item.attributes.get("agentic.usage.input_tokens.missing"), "true")
            self.assertEqual(item.attributes.get("agentic.usage.output_tokens.missing"), "true")
        for item in ends + self.sink.of_type("event"):
            self.assertNotIn("gen_ai.usage.input_tokens", item.attributes)
            self.assertNotIn("gen_ai.usage.output_tokens", item.attributes)
        self.assertFalse(any(item.name == "agent.token.usage" for item in self.sink.of_type("metric")))

    def test_turnless_auxiliary_does_not_invent_chat_turn(self):
        self.feed("pre_auxiliary_call", turn_id="", model="aux", aux_task="cron", retry_count=0)
        self.assertEqual(len(self.sink.of_type("span.start")), 1)
        self.assertTrue(any(item.name == "auxiliary_missing_turn_identity" for item in self.sink.of_type("diagnostic")))

    def test_auxiliary_requires_an_existing_active_parent_turn(self):
        for hook in ("pre_auxiliary_call", "post_auxiliary_call"):
            with self.subTest(hook=hook):
                sink = MemorySink()
                engine = SemanticEngine(sink=sink)
                engine.apply(self.normalizer.normalize(hook, {**self.common,
                    "model": "aux", "aux_task": "title", "retry_count": 0}))
                self.assertEqual(sink.of_type("span.start"), [])
                self.assertEqual(sink.of_type("span.end"), [])
                self.assertTrue(any(item.name == "auxiliary_missing_active_turn" for item in sink.of_type("diagnostic")))

    def test_late_auxiliary_completes_on_original_parent_without_superseding_current_turn(self):
        self.feed("pre_auxiliary_call", model="aux", aux_task="title", retry_count=0)
        self.feed("pre_llm_call", turn_id="next-turn", user_message="next prompt")
        active = self.engine.state.sessions["native"].active_turn
        ended = len(self.sink.of_type("span.end"))
        self.feed("post_auxiliary_call", model="aux", aux_task="title", retry_count=0,
                  usage={"input_tokens": 1, "output_tokens": 1})
        self.assertIs(self.engine.state.sessions["native"].active_turn, active)
        self.assertEqual(len(self.sink.of_type("span.end")), ended + 1)
        terminal = self.sink.of_type("span.end")[-1]
        self.assertEqual(terminal.key, "api:native:aux:call:title:0")
        self.assertEqual(terminal.status, "OK")

    def test_deferred_title_after_session_finalize_keeps_observed_parent(self):
        # Native 0.21.5 custom-route order: turn end, finalize, title pre/post.
        self.feed("on_session_end")
        self.feed("on_session_finalize", reason="cli_exit")
        self.feed("pre_auxiliary_call", model="aux", provider="custom", aux_task="title_generation",
                  retry_count=0, request_messages=[{"role": "user", "content": "title input"}])
        self.feed("post_auxiliary_call", aux_task="title_generation", retry_count=0,
                  usage={"input_tokens": 16, "output_tokens": 4},
                  response={"assistant_message": {"role": "assistant", "content": "Receipt reading"}})
        starts = self.sink.of_type("span.start")
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[-1].parent_key, "turn:native:turn")
        self.assertEqual(starts[-1].attributes["gen_ai.agent.name"], "title_generation")
        self.assertEqual(self.sink.of_type("span.end")[-1].status, "OK")
        self.assertEqual(self.engine.state.sessions, {})
        self.assertEqual(sum(item.value for item in self.sink.of_type("metric")
                             if item.name == "agent.api_request.count"), 1)

    def test_unknown_session_cannot_reuse_a_known_turn_id(self):
        self.feed("pre_auxiliary_call", session_id="unrelated", model="aux", aux_task="title", retry_count=0)
        self.assertEqual(len(self.sink.of_type("span.start")), 1)
        self.assertNotIn("unrelated", self.engine.state.sessions)

    def test_retained_parent_identity_is_bounded_and_does_not_keep_prompt_content(self):
        self.engine._auxiliary_turn_limit = 2
        for number in range(3):
            self.feed("pre_llm_call", turn_id=f"turn-{number}", user_message="private prompt")
            self.feed("post_llm_call", turn_id=f"turn-{number}", assistant_response="private answer")
        self.assertEqual(len(self.engine._auxiliary_turns), 2)
        for turn in self.engine._auxiliary_turns.values():
            self.assertIsNone(turn.user_message)
            self.assertIsNone(turn.assistant_response)
        starts = len(self.sink.of_type("span.start"))
        self.feed("pre_auxiliary_call", turn_id="turn-0", model="aux", aux_task="title", retry_count=0)
        self.assertEqual(len(self.sink.of_type("span.start")), starts)
        self.engine.shutdown()
        self.assertEqual(self.engine._auxiliary_turns, {})


if __name__ == "__main__":
    unittest.main()
