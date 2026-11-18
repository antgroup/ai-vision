"""Official middleware observation never changes provider control flow."""
from __future__ import annotations

import sys
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observ_hermes.config import ObservHermesConfig
from observ_hermes.hooks.session import on_session_end, on_session_finalize, on_session_reset
from observ_hermes.hooks.usage import apply_reported_usage, observe_llm_execution
from observ_hermes.hooks.llm import api_request_error, pre_api_request, post_api_request
from observ_hermes.pipeline import get_pipeline


class ExecutionUsageTests(unittest.TestCase):
    def setUp(self):
        self.runtime = SimpleNamespace(config=ObservHermesConfig(enable_telemetry=True))
        self.identity = {"session_id": "session", "api_request_id": "call"}

    def capture(self, usage, **identity):
        response = {"usage": usage, "headers": {"Authorization": "secret"}}
        result = observe_llm_execution(self.runtime, {}, lambda request: response, **(identity or self.identity))
        self.assertIs(result, response)
        return response

    def test_only_valid_reported_counters_are_retained(self):
        self.capture({"prompt_tokens": 0, "completion_tokens": 7, "total_tokens": 7,
                      "input_tokens": True, "output_tokens": -1, "other": "secret",
                      "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 2, "credentials": "secret"},
                      "completion_tokens_details": {"reasoning_tokens": 3, "arbitrary": "secret"}})
        actual = apply_reported_usage(self.runtime, self.identity)["reported_usage"]
        self.assertEqual(actual, {"prompt_tokens": 0, "completion_tokens": 7, "total_tokens": 7,
                                  "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 2},
                                  "completion_tokens_details": {"reasoning_tokens": 3}})
        self.assertNotIn("reported_usage", apply_reported_usage(self.runtime, self.identity))

    def test_provider_error_and_cancel_propagate_and_discard_same_call(self):
        for error in (RuntimeError("provider failed"), KeyboardInterrupt("cancelled")):
            with self.subTest(error=type(error).__name__):
                self.capture({"prompt_tokens": 17})
                self.capture({"prompt_tokens": 19}, session_id="other", api_request_id="call")
                def fail(request):
                    raise error
                with self.assertRaises(type(error)) as raised:
                    observe_llm_execution(self.runtime, {}, fail, **self.identity)
                self.assertIs(raised.exception, error)
                self.assertNotIn("reported_usage", apply_reported_usage(self.runtime, self.identity))
                self.assertEqual(apply_reported_usage(self.runtime, {"session_id": "other", "api_request_id": "call"})["reported_usage"], {"prompt_tokens": 19})

    def test_session_terminal_hooks_discard_unconsumed_facts(self):
        for callback in (on_session_end, on_session_finalize, on_session_reset):
            with self.subTest(callback=callback.__name__):
                self.capture({"prompt_tokens": 17})
                callback(self.runtime, session_id="session")
                self.assertNotIn("reported_usage", apply_reported_usage(self.runtime, self.identity))

    def test_observer_storage_failure_never_replaces_provider_return(self):
        class BrokenFacts:
            def __iter__(self):
                raise RuntimeError("secret-bearing diagnostic must not escape")
        self.runtime._observ_hermes_reported_usage = BrokenFacts()
        with self.assertLogs("observ_hermes", level="WARNING") as logs:
            response = self.capture({"prompt_tokens": 7})
        self.assertEqual(response["usage"], {"prompt_tokens": 7})
        self.assertNotIn("secret-bearing", "\n".join(logs.output))

    def length_response(self, usage, **changes):
        return SimpleNamespace(
            **{"id": "stream-real-response", "model": "nvidia-model", "usage": usage,
               "choices": [SimpleNamespace(finish_reason="length", message=SimpleNamespace(
                   role="assistant", content="receipt: partial", tool_calls=None))],
               "headers": {"Authorization": "secret-header"}, **changes})

    def length_call(self, usage, **changes):
        fields = {**self.identity, "turn_id": "turn", "task_id": "task",
                  "model": "nvidia-model", "provider": "nvidia", "api_mode": "chat_completions"}
        pre_api_request(self.runtime, **fields, started_at=1750000000,
                        request_messages=[{"role": "user", "content": "read receipt"}])
        response = self.length_response(usage, **changes)
        with patch("time.time", return_value=1750000002):
            returned = observe_llm_execution(self.runtime, {"headers": {"Authorization": "secret-request"}},
                                             lambda request: response, **fields)
        self.assertIs(returned, response)
        return fields, response, get_pipeline(self.runtime).memory_sink

    def test_length_continuation_without_post_hook_completes_the_physical_call(self):
        fields, response, sink = self.length_call({"prompt_tokens": 3389, "completion_tokens": 512, "total_tokens": 3901})
        # Hermes 0.18.2 continues before post_api_request for the length response.
        # A subsequent normal call and session shutdown must not cancel it.
        next_fields = {**fields, "api_request_id": "continued"}
        pre_api_request(self.runtime, **next_fields, started_at=1750000003)
        observe_llm_execution(self.runtime, {}, lambda request: {"usage": {"prompt_tokens": 3455, "completion_tokens": 241}}, **next_fields)
        post_api_request(self.runtime, **next_fields, ended_at=1750000005, finish_reason="stop",
                         assistant_message={"role": "assistant", "content": "receipt: complete"})
        on_session_end(self.runtime, session_id="session", completed=True, ended_at=1750000006)
        end = next(item for item in sink.of_type("span.end") if item.key == "api:session:call")
        self.assertEqual(end.status, "OK")
        self.assertEqual(end.attributes["gen_ai.response.finish_reasons"], '["length"]')
        self.assertEqual(end.attributes["gen_ai.usage.input_tokens"], 3389)
        self.assertEqual(end.attributes["gen_ai.usage.output_tokens"], 512)
        self.assertNotIn("error.type", end.attributes)
        self.assertEqual(end.timestamp, "2025-06-15T15:06:42Z")
        self.assertEqual(json.loads(end.attributes["gen_ai.output.messages"])[0]["content"], "receipt: partial")
        continuation = next(item for item in sink.of_type("span.end") if item.key == "api:session:continued")
        self.assertEqual(continuation.attributes["gen_ai.response.finish_reasons"], '["stop"]')
        self.assertEqual(continuation.attributes["gen_ai.usage.input_tokens"], 3455)
        self.assertEqual(continuation.attributes["gen_ai.usage.output_tokens"], 241)
        self.assertEqual(json.loads(continuation.attributes["gen_ai.output.messages"])[0]["content"], "receipt: complete")
        events = [item for item in sink.of_type("event") if item.name == "api_request"]
        self.assertEqual(len(events), 2)
        self.assertEqual([item.value for item in sink.of_type("metric") if item.name == "agent.api_request.count"], [1, 1])
        self.assertEqual(sum(item.value for item in sink.of_type("metric") if item.name == "agent.token.usage" and item.attributes["type"] == "input"), 6844)
        self.assertEqual(sum(item.value for item in sink.of_type("metric") if item.name == "agent.token.usage" and item.attributes["type"] == "output"), 753)
        self.assertNotIn("secret-header", repr(sink.observations))
        self.assertNotIn("secret-request", repr(sink.observations))

    def test_later_post_hook_cannot_recount_or_replace_length_completion(self):
        fields, response, sink = self.length_call({"prompt_tokens": 20, "completion_tokens": 9,
            "prompt_tokens_details": {"cached_tokens": 5, "cache_write_tokens": 2},
            "completion_tokens_details": {"reasoning_tokens": 4}})
        post_api_request(self.runtime, **fields, ended_at=1750000099, finish_reason="stop",
                         usage={"input_tokens": 0, "output_tokens": 0},
                         assistant_message={"role": "assistant", "content": "later hook"})
        ends = [item for item in sink.of_type("span.end") if item.key == "api:session:call"]
        self.assertEqual(len(ends), 1)
        end = ends[0]
        self.assertEqual(end.attributes["gen_ai.response.finish_reasons"], '["length"]')
        self.assertEqual(end.timestamp, "2025-06-15T15:06:42Z")
        self.assertEqual(end.attributes["gen_ai.usage.cache_read.input_tokens"], 5)
        self.assertEqual(end.attributes["gen_ai.usage.cache_creation.input_tokens"], 2)
        self.assertEqual(end.attributes["gen_ai.usage.reasoning.output_tokens"], 4)
        self.assertEqual([item.value for item in sink.of_type("metric") if item.name == "agent.token.usage"], [13, 9])
        self.assertEqual(len([item for item in sink.of_type("event") if item.name == "api_request"]), 1)

    def test_length_completion_preserves_unknown_partial_and_explicit_zero_usage(self):
        for usage, input_value, output_value, missing in ((None, None, None, ("input", "output")),
                ({"prompt_tokens": 7}, 7, None, ("output",)),
                ({"prompt_tokens": 0, "completion_tokens": 0}, 0, 0, ())):
            with self.subTest(usage=usage):
                self.setUp()
                _, _, sink = self.length_call(usage)
                ends = [item for item in sink.of_type("span.end") if item.key == "api:session:call"]
                self.assertEqual(len(ends), 1, "returned length response requires a physical terminal before any host post hook")
                end = ends[0]
                self.assertEqual(end.status, "OK")
                self.assertEqual(end.attributes.get("gen_ai.usage.input_tokens"), input_value)
                self.assertEqual(end.attributes.get("gen_ai.usage.output_tokens"), output_value)
                for direction in ("input", "output"):
                    self.assertEqual(end.attributes.get(f"agentic.usage.{direction}_tokens.missing"),
                                     "true" if direction in missing else None)

    def test_partial_stream_stub_and_content_filter_do_not_become_completed_calls(self):
        for changes in ({"id": "partial-stream-stub"}, {"_content_filter_terminated": True}):
            with self.subTest(changes=changes):
                self.setUp()
                _, _, sink = self.length_call(None, **changes)
                self.assertFalse(sink.of_type("span.end"))
                on_session_end(self.runtime, session_id="session", interrupted=True, ended_at=1750000006)
                end = next(item for item in sink.of_type("span.end") if item.key == "api:session:call")
                self.assertEqual(end.status, "ERROR")
                self.assertEqual(end.attributes["error.type"], "cancelled")

    def test_execution_exception_and_real_cancel_never_emit_success_or_tokens(self):
        for error in (RuntimeError("provider failed"), KeyboardInterrupt("user cancelled")):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                fields = {**self.identity, "turn_id": "turn", "api_mode": "chat_completions", "model": "nvidia-model"}
                pre_api_request(self.runtime, **fields, started_at=1750000000)
                def fail(request):
                    raise error
                with self.assertRaises(type(error)) as raised:
                    observe_llm_execution(self.runtime, {}, fail, **fields)
                self.assertIs(raised.exception, error)
                sink = get_pipeline(self.runtime).memory_sink
                self.assertFalse(sink.of_type("span.end"))
                if isinstance(error, Exception):
                    api_request_error(self.runtime, **fields, ended_at=1750000002,
                                      error={"type": "provider_error", "message": "failed"})
                else:
                    on_session_end(self.runtime, session_id="session", interrupted=True, ended_at=1750000002)
                end = next(item for item in sink.of_type("span.end") if item.key == "api:session:call")
                self.assertEqual(end.status, "ERROR")
                self.assertEqual(end.attributes["agentic.usage.input_tokens.missing"], "true")
                self.assertEqual(end.attributes["agentic.usage.output_tokens.missing"], "true")
                self.assertNotIn("gen_ai.usage.input_tokens", end.attributes)
                self.assertNotIn("gen_ai.usage.output_tokens", end.attributes)
                self.assertFalse(any(item.name == "agent.token.usage" for item in sink.of_type("metric")))

    def test_length_completion_uses_the_normal_privacy_projection(self):
        self.runtime.config.collection_mode = "privacy"
        _, _, sink = self.length_call({"prompt_tokens": 7, "completion_tokens": 2})
        end = next(item for item in sink.of_type("span.end") if item.key == "api:session:call")
        self.assertEqual(end.attributes["gen_ai.usage.input_tokens"], 7)
        self.assertEqual(end.attributes["gen_ai.usage.output_tokens"], 2)
        self.assertNotIn("receipt: partial", repr(sink.observations))
        self.assertNotIn("secret-header", repr(sink.observations))
        self.assertEqual(self.runtime._observ_hermes_reported_usage, {})

    def test_length_completion_preserves_reported_reasoning_with_existing_privacy_rules(self):
        for mode in ("full", "privacy"):
            with self.subTest(mode=mode):
                self.setUp()
                self.runtime.config.collection_mode = mode
                message = SimpleNamespace(role="assistant", content=None, tool_calls=None,
                                          reasoning_content="actual truncated reasoning")
                _, _, sink = self.length_call({"prompt_tokens": 7, "completion_tokens": 2},
                    choices=[SimpleNamespace(finish_reason="length", message=message)])
                end = next(item for item in sink.of_type("span.end") if item.key == "api:session:call")
                if mode == "full":
                    output = json.loads(end.attributes["gen_ai.output.messages"])[0]
                    self.assertIsNone(output["content"])
                    self.assertEqual(output.get("reasoning_content"), "actual truncated reasoning")
                else:
                    self.assertNotIn("actual truncated reasoning", repr(sink.observations))
                self.assertEqual(end.attributes["gen_ai.usage.output_tokens"], 2)

    def test_length_completion_does_not_invent_absent_or_nonstring_reasoning(self):
        for reasoning in (None, {"private_header": "secret-header"}):
            with self.subTest(reasoning=reasoning):
                self.setUp()
                message = SimpleNamespace(role="assistant", content="text", tool_calls=None)
                if reasoning is not None:
                    message.reasoning_content = reasoning
                _, _, sink = self.length_call({"prompt_tokens": 7, "completion_tokens": 2},
                    choices=[SimpleNamespace(finish_reason="length", message=message)])
                end = next(item for item in sink.of_type("span.end") if item.key == "api:session:call")
                self.assertNotIn("reasoning_content", json.loads(end.attributes["gen_ai.output.messages"])[0])
                self.assertNotIn("secret-header", repr(sink.observations))

    def test_nonlength_and_tool_call_responses_keep_the_ordinary_post_hook_boundary(self):
        for changes in (
            {"choices": [SimpleNamespace(finish_reason="stop", message=SimpleNamespace(role="assistant", content="done", tool_calls=None))]},
            {"choices": [SimpleNamespace(finish_reason="length", message=SimpleNamespace(role="assistant", content=None, tool_calls=[{"id": "tool-call"}]))]},
        ):
            with self.subTest(changes=changes):
                self.setUp()
                fields, _, sink = self.length_call({"prompt_tokens": 7, "completion_tokens": 2}, **changes)
                self.assertFalse(sink.of_type("span.end"))
                post_api_request(self.runtime, **fields, ended_at=1750000003, finish_reason="stop")
                ends = [item for item in sink.of_type("span.end") if item.key == "api:session:call"]
                self.assertEqual(len(ends), 1)
                self.assertEqual(ends[0].attributes["gen_ai.usage.input_tokens"], 7)


if __name__ == "__main__":
    unittest.main()
