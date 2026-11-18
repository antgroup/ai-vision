"""Real source-clock intervals for independently exported delta series."""

from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from observ_hermes.exporters.otlp_json import OtlpJsonEncoder, unix_nano
from observ_hermes.semantic import SemanticObservation


def observation(second, *, name="agent.api_request.count", kind="counter", attributes=None, value=1):
    return SemanticObservation(type="metric", name=name,
        timestamp=f"2026-10-04T12:00:{second:02d}+00:00", metric_kind=kind,
        unit="{count}" if kind == "counter" else "ms", value=value,
        attributes=attributes or {"gen_ai.request.model": "model-a"})


def begin(encoder, second):
    encoder.encode(SemanticObservation(type="span.start", name="turn", key="turn",
        timestamp=f"2026-10-04T12:00:{second:02d}+00:00", span_kind="CHAT"))


def point(encoder, metric):
    signal, payload = encoder.encode(metric)
    assert signal == "metrics"
    exported = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]
    data = exported["sum" if metric.metric_kind == "counter" else "histogram"]
    assert data["aggregationTemporality"] == 1
    return data["dataPoints"][0]


class DeltaMetricIntervalTests(unittest.TestCase):
    def test_known_runtime_creation_time_covers_immediate_session_count(self):
        created = unix_nano("2026-10-04T12:00:00+00:00")
        encoder = OtlpJsonEncoder({}, aggregation_temporality=1,
                                  metric_start_time_unix_nano=int(created))
        exported = point(encoder, observation(2, name="agent.session.count"))
        self.assertEqual(exported["startTimeUnixNano"], created)
        self.assertLess(int(exported["startTimeUnixNano"]), int(exported["timeUnixNano"]))

    def test_known_creation_time_rejects_non_integer_or_negative_values(self):
        for invalid in (True, -1, 0.5, "1"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                OtlpJsonEncoder({}, aggregation_temporality=1,
                                metric_start_time_unix_nano=invalid)

    def test_first_interval_starts_at_real_stream_start_then_previous_export(self):
        encoder = OtlpJsonEncoder({}, aggregation_temporality=1)
        begin(encoder, 0)
        first = point(encoder, observation(2))
        second = point(encoder, observation(5))
        self.assertEqual(first["startTimeUnixNano"], unix_nano("2026-10-04T12:00:00+00:00"))
        self.assertEqual(second["startTimeUnixNano"], first["timeUnixNano"])
        for exported in (first, second):
            self.assertLess(int(exported["startTimeUnixNano"]), int(exported["timeUnixNano"]))
            self.assertEqual(exported["asInt"], "1")

    def test_full_series_identity_preserves_independent_model_and_token_intervals(self):
        encoder = OtlpJsonEncoder({}, aggregation_temporality=1)
        begin(encoder, 0)
        first = point(encoder, observation(2))
        another_model = point(encoder, observation(3, attributes={"gen_ai.request.model":"model-b"}))
        repeated = point(encoder, observation(4))
        input_metric = replace(observation(5, name="agent.token.usage", attributes={"type":"input"}, value=11), unit="{token}")
        input_first = point(encoder, input_metric)
        output_first = point(encoder, replace(input_metric, timestamp=observation(6).timestamp, attributes={"type":"output"}, value=4))
        input_second = point(encoder, replace(input_metric, timestamp=observation(7).timestamp, value=13))
        epoch = unix_nano("2026-10-04T12:00:00+00:00")
        self.assertEqual(another_model["startTimeUnixNano"], epoch)
        self.assertEqual(repeated["startTimeUnixNano"], first["timeUnixNano"])
        self.assertEqual(input_first["startTimeUnixNano"], epoch)
        self.assertEqual(output_first["startTimeUnixNano"], epoch)
        self.assertEqual(input_second["startTimeUnixNano"], input_first["timeUnixNano"])
        self.assertEqual([input_first["asInt"], output_first["asInt"], input_second["asInt"]], ["11","4","13"])

    def test_histogram_and_cache_series_use_real_intervals_without_changing_values(self):
        encoder = OtlpJsonEncoder({}, aggregation_temporality=1)
        begin(encoder, 0)
        duration = point(encoder, observation(2, name="agent.api_request.duration", kind="histogram", value=125.5))
        duration_next = point(encoder, observation(4, name="agent.api_request.duration", kind="histogram", value=87.5))
        cache = point(encoder, replace(observation(5, name="agent.cache.read_tokens", value=5), unit="{token}"))
        cache_next = point(encoder, replace(observation(7, name="agent.cache.read_tokens", value=8), unit="{token}"))
        self.assertLess(int(duration["startTimeUnixNano"]), int(duration["timeUnixNano"]))
        self.assertEqual(duration_next["startTimeUnixNano"], duration["timeUnixNano"])
        self.assertEqual([duration["sum"],duration_next["sum"]], [125.5,87.5])
        self.assertEqual(cache_next["startTimeUnixNano"], cache["timeUnixNano"])
        self.assertEqual([cache["asInt"],cache_next["asInt"]], ["5","8"])

    def test_new_process_starts_new_real_interval_and_never_reuses_previous_process_time(self):
        first = OtlpJsonEncoder({}, aggregation_temporality=1)
        begin(first, 0)
        point(first, observation(2))
        resumed = OtlpJsonEncoder({}, aggregation_temporality=1)
        begin(resumed, 10)
        exported = point(resumed, observation(12))
        self.assertEqual(exported["startTimeUnixNano"], unix_nano("2026-10-04T12:00:10+00:00"))
        self.assertEqual(exported["asInt"], "1")

    def test_missing_earlier_source_time_is_not_replaced_with_an_invented_interval(self):
        encoder = OtlpJsonEncoder({}, aggregation_temporality=1)
        exported = point(encoder, observation(2))
        self.assertEqual(exported["startTimeUnixNano"], exported["timeUnixNano"])

    def test_earlier_start_fact_before_first_metric_uses_source_clock(self):
        encoder = OtlpJsonEncoder({}, aggregation_temporality=1)
        begin(encoder, 2)
        encoder.encode(SemanticObservation(type="span.start", name="api", key="api",
            timestamp=observation(0).timestamp, span_kind="LLM"))
        exported = point(encoder, observation(3))
        self.assertEqual(exported["startTimeUnixNano"], unix_nano(observation(0).timestamp))

    def test_backwards_source_time_is_rejected_without_advancing_series_boundary(self):
        encoder = OtlpJsonEncoder({}, aggregation_temporality=1)
        begin(encoder, 0)
        previous = point(encoder, observation(5))
        with self.assertRaisesRegex(ValueError, "source time"):
            point(encoder, observation(4))
        following = point(encoder, observation(6))
        self.assertEqual(following["startTimeUnixNano"], previous["timeUnixNano"])


if __name__ == "__main__":
    unittest.main()
