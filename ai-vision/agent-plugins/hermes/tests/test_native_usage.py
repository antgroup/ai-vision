"""Reported transport usage must survive host normalization and privacy projection."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hermes-core"))
from aivision_hermes_core.normalize import CanonicalNormalizer
from aivision_hermes_core.semantic import MemorySink, SemanticEngine


class NativeUsageTests(unittest.TestCase):
    def observations(self, raw_usage, *, normalized=None, mode="full", reported=True):
        normalizer = CanonicalNormalizer(mode)
        sink = MemorySink()
        engine = SemanticEngine(sink=sink)
        common = {"session_id": "native", "turn_id": "turn", "api_request_id": "request"}
        for index, (hook, fields) in enumerate([
            ("pre_api_request", {"model": "nvidia-model", "provider": "nvidia"}),
            ("post_api_request", {
                "response": {"model": "nvidia-model", "usage": raw_usage},
                **({"reported_usage": raw_usage} if reported else {}),
                "usage": normalized or {"input_tokens": 0, "output_tokens": 0},
                "assistant_message": {"role": "assistant", "content": "receipt"},
            }),
        ]):
            engine.apply(normalizer.normalize(hook, {**common, **fields}, timestamp=1750000000 + index))
        end = next(item for item in sink.of_type("span.end") if item.key == "api:native:request")
        event = next(item for item in sink.of_type("event") if item.name == "api_request")
        return end, event, sink

    def test_transport_absence_overrides_host_prefilled_zero(self):
        for mode in ("full", "privacy"):
            with self.subTest(mode=mode):
                end, event, sink = self.observations(None, mode=mode)
                for field in ("input_tokens", "output_tokens"):
                    self.assertEqual(end.attributes.get(f"agentic.usage.{field}.missing"), "true")
                    self.assertEqual(event.attributes.get(f"agentic.usage.{field}.missing"), "true")
                    self.assertNotIn(f"gen_ai.usage.{field}", end.attributes)
                    self.assertNotIn(f"gen_ai.usage.{field}", event.attributes)
                self.assertNotIn("gen_ai.usage.total_tokens", event.attributes)
                self.assertFalse(any(item.name == "agent.token.usage" for item in sink.of_type("metric")))

    def test_partial_transport_usage_does_not_become_complete(self):
        end, event, _ = self.observations({"prompt_tokens": 7})
        self.assertEqual(end.attributes["gen_ai.usage.input_tokens"], 7)
        self.assertNotIn("agentic.usage.input_tokens.missing", end.attributes)
        self.assertEqual(end.attributes["agentic.usage.output_tokens.missing"], "true")
        self.assertNotIn("gen_ai.usage.output_tokens", end.attributes)
        self.assertNotIn("gen_ai.usage.output_tokens", event.attributes)
        self.assertNotIn("gen_ai.usage.total_tokens", event.attributes)

    def test_native_call_id_survives_terminal_span_and_event_projection(self):
        end, event, sink = self.observations({"prompt_tokens": 7, "completion_tokens": 2})
        start = next(item for item in sink.of_type("span.start") if item.span_kind == "LLM")
        for item in (start, end, event):
            self.assertEqual(item.attributes.get("agentic.model_call.id"), "request")

    def test_explicit_transport_zero_is_reported(self):
        end, event, _ = self.observations({"prompt_tokens": 0, "completion_tokens": 0})
        self.assertEqual(end.attributes["gen_ai.usage.input_tokens"], 0)
        self.assertEqual(end.attributes["gen_ai.usage.output_tokens"], 0)
        self.assertNotIn("agentic.usage.input_tokens.missing", end.attributes)
        self.assertNotIn("agentic.usage.output_tokens.missing", end.attributes)
        self.assertEqual(event.attributes["gen_ai.usage.total_tokens"], 0)
        self.assertEqual(event.attributes["gen_ai.usage.input_tokens"], 0)
        self.assertEqual(event.attributes["gen_ai.usage.output_tokens"], 0)

    def test_normalized_zero_without_reported_fact_remains_unknown(self):
        normalizer = CanonicalNormalizer()
        sink = MemorySink()
        engine = SemanticEngine(sink=sink)
        common = {"session_id": "native", "turn_id": "turn", "api_request_id": "request"}
        for hook, fields in (("pre_api_request", {"model": "model"}),
                             ("post_api_request", {"usage": {"input_tokens": 0, "output_tokens": 0}})):
            engine.apply(normalizer.normalize(hook, {**common, **fields}))
        end = sink.of_type("span.end")[0]
        self.assertEqual(end.attributes.get("agentic.usage.input_tokens.missing"), "true")
        self.assertEqual(end.attributes.get("agentic.usage.output_tokens.missing"), "true")

    def test_openai_cache_is_a_subset_and_reasoning_is_not_added_twice(self):
        end, event, sink = self.observations({
            "prompt_tokens": 20, "completion_tokens": 9, "total_tokens": 29,
            "prompt_tokens_details": {"cached_tokens": 5, "cache_write_tokens": 2},
            "completion_tokens_details": {"reasoning_tokens": 4},
        })
        self.assertEqual(end.attributes["gen_ai.usage.input_tokens"], 20)
        self.assertEqual(end.attributes["gen_ai.usage.output_tokens"], 9)
        self.assertEqual(event.attributes["gen_ai.usage.total_tokens"], 29)
        self.assertEqual(event.attributes["agentic.usage.cache_read_tokens"], 5)
        self.assertEqual(event.attributes.get("agentic.usage.cache_write_tokens"), 2)
        for item in (end, event):
            self.assertEqual(item.attributes.get("gen_ai.usage.cache_read.input_tokens"), 5)
            self.assertEqual(item.attributes.get("gen_ai.usage.cache_creation.input_tokens"), 2)
            self.assertEqual(item.attributes.get("gen_ai.usage.reasoning.output_tokens"), 4)
        input_metrics = [item.value for item in sink.of_type("metric")
                         if item.name == "agent.token.usage" and item.attributes.get("type") == "input"]
        self.assertEqual(input_metrics, [13])
        self.assertEqual(event.attributes.get("agentic.usage.fresh_input_tokens"), 13)

    def test_invalid_cache_subset_does_not_create_negative_fresh_metric(self):
        end, event, sink = self.observations({"prompt_tokens": 2, "completion_tokens": 1,
            "prompt_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 2}})
        self.assertEqual(end.attributes["gen_ai.usage.input_tokens"], 2)
        self.assertEqual(event.attributes["gen_ai.usage.total_tokens"], 3)
        self.assertNotIn("agentic.usage.fresh_input_tokens", event.attributes)
        self.assertFalse(any(item.name == "agent.token.usage" and item.attributes.get("type") == "input"
                             for item in sink.of_type("metric")))

    def test_reasoning_zero_is_reported_and_missing_reasoning_stays_absent(self):
        end, event, _ = self.observations({"prompt_tokens": 2, "completion_tokens": 0,
            "completion_tokens_details": {"reasoning_tokens": 0}})
        for item in (end, event):
            self.assertEqual(item.attributes.get("gen_ai.usage.reasoning.output_tokens"), 0)
        end, event, _ = self.observations({"prompt_tokens": 2, "completion_tokens": 1})
        for item in (end, event):
            self.assertNotIn("gen_ai.usage.reasoning.output_tokens", item.attributes)

    def test_reported_cache_zero_is_known_and_normalized_cache_zero_is_unknown(self):
        end, event, _ = self.observations({"prompt_tokens": 5, "completion_tokens": 2,
            "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}})
        self.assertEqual(end.attributes["gen_ai.usage.details.cache_read_input_tokens"], 0)
        self.assertEqual(end.attributes.get("gen_ai.usage.cache_read.input_tokens"), 0)
        self.assertEqual(end.attributes.get("gen_ai.usage.cache_creation.input_tokens"), 0)
        self.assertEqual(event.attributes["agentic.usage.cache_write_tokens"], 0)
        end, event, _ = self.observations(None, reported=False, normalized={
            "input_tokens": 5, "output_tokens": 2, "cache_read_tokens": 0, "cache_write_tokens": 0})
        self.assertNotIn("gen_ai.usage.details.cache_read_input_tokens", end.attributes)
        self.assertNotIn("agentic.usage.cache_write_tokens", event.attributes)
        self.assertEqual(event.attributes["gen_ai.usage.total_tokens"], 7)


if __name__ == "__main__":
    unittest.main()
