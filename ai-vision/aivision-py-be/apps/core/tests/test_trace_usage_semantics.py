import json
import unittest

from apps.core.trace_query.semantics.usage import aggregate_usage, normalize_usage


class TraceUsageSemanticsTests(unittest.TestCase):
    def test_native_missing_markers_do_not_turn_placeholders_into_measurements(self):
        for marker in (True, "true"):
            with self.subTest(marker=marker):
                attrs = {
                    "gen_ai.usage.input_tokens": 0,
                    "gen_ai.usage.output_tokens": 0,
                    "agentic.usage.input_tokens.missing": marker,
                    "agentic.usage.output_tokens.missing": marker,
                }
                usage = normalize_usage(attrs)
                self.assertEqual(
                    tuple(usage[key] for key in ("input", "output", "total")),
                    (None, None, None),
                )
                self.assertEqual(usage["unknownReason"], "usage_missing")
                self.assertEqual(usage["raw"], attrs)

    def test_native_missing_is_field_specific_and_reported_zero_stays_known(self):
        usage = normalize_usage({
            "gen_ai.usage.input_tokens": 0,
            "gen_ai.usage.output_tokens": 7,
            "agentic.usage.input_tokens.missing": "true",
        })
        self.assertEqual(
            tuple(usage[key] for key in ("input", "output", "total")),
            (None, 7, None),
        )
        for marker in (False, "false", None):
            usage = normalize_usage({
                "gen_ai.usage.input_tokens": 0,
                "gen_ai.usage.output_tokens": 0,
                "agentic.usage.input_tokens.missing": marker,
                "agentic.usage.output_tokens.missing": marker,
            })
            self.assertEqual(usage["total"], 0)
            self.assertIsNone(usage["unknownReason"])

    def test_native_placeholder_can_fall_back_to_independently_reported_usage(self):
        attrs = {
            "gen_ai.usage.input_tokens": 0,
            "gen_ai.usage.output_tokens": 0,
            "agentic.usage.input_tokens.missing": "true",
            "agentic.usage.output_tokens.missing": "true",
            "langfuse.observation.usage_details": {
                "prompt_tokens": 9, "completion_tokens": 3,
            },
        }
        before = json.dumps(attrs, sort_keys=True)
        usage = normalize_usage(attrs)
        self.assertEqual(
            tuple(usage[key] for key in ("input", "output", "total")),
            (9, 3, 12),
        )
        self.assertEqual(json.dumps(attrs, sort_keys=True), before)
        self.assertEqual(
            usage["source"]["input"],
            "langfuse.observation.usage_details.prompt_tokens",
        )

    def test_complete_flat_langfuse_usage_defaults_absent_optional_buckets_to_zero(
        self,
    ):
        cached = normalize_usage(
            {
                "langfuse.observation.usage_details": {
                    "input": 474,
                    "output": 62,
                    "total": 792,
                    "input_cache_read": 256,
                }
            }
        )
        uncached = normalize_usage(
            {
                "langfuse.observation.usage_details": {
                    "input": 10,
                    "output": 3,
                    "total": 13,
                }
            }
        )
        self.assertEqual(
            (
                uncached["cacheReadInput"],
                uncached["cacheWriteInput"],
                uncached["reasoning"],
            ),
            (0, 0, 0),
        )
        aggregate = aggregate_usage(
            [
                {"traceId": "t", "id": "cached", "type": "GENERATION", "usage": cached},
                {
                    "traceId": "t",
                    "id": "uncached",
                    "type": "GENERATION",
                    "usage": uncached,
                },
            ]
        )
        self.assertEqual(
            (
                aggregate["input"],
                aggregate["output"],
                aggregate["total"],
                aggregate["cacheReadInput"],
            ),
            (740, 65, 805, 256),
        )
        self.assertEqual((aggregate["cacheWriteInput"], aggregate["reasoning"]), (0, 0))

    def test_complete_raw_usage_proof_survives_individual_otel_overrides(self):
        from apps.core.trace_query.langfuse.native import observation_wire
        from apps.core.trace_query.semantics.observations import project_observation

        raw = {"input": 474, "output": 62, "total": 792, "input_cache_read": 256}
        for field, attribute, value, expected, details in (
            (
                "input",
                "gen_ai.usage.input_tokens",
                0,
                (0, 62, 792),
                {key: count for key, count in raw.items() if key != "input"},
            ),
            (
                "output",
                "gen_ai.usage.output_tokens",
                0,
                (730, 0, 792),
                {**raw, "output": 0},
            ),
            ("input", "gen_ai.usage.input_tokens", 730, (730, 62, 792), raw),
            ("output", "gen_ai.usage.output_tokens", 62, (730, 62, 792), raw),
            (
                "cacheReadInput",
                "gen_ai.usage.cache_read.input_tokens",
                0,
                (474, 62, 792),
                {
                    "input": 474,
                    "output": 62,
                    "total": 792,
                    "cache_read_input_tokens": 0,
                },
            ),
            (
                "cacheReadInput",
                "gen_ai.usage.cache_read.input_tokens",
                256,
                (730, 62, 792),
                {
                    "input": 474,
                    "output": 62,
                    "total": 792,
                    "cache_read_input_tokens": 256,
                },
            ),
            (
                "total",
                "gen_ai.usage.total_tokens",
                0,
                (730, 62, 0),
                {**raw, "total": 0},
            ),
        ):
            with self.subTest(attribute=attribute, value=value):
                observation = project_observation(
                    {
                        "span_id": "s",
                        "trace_id": "t",
                        "start_time_unix_nano": "2026-10-03T00:00:00Z",
                        "attributes": {
                            "langfuse.observation.usage_details": json.dumps(raw),
                            attribute: value,
                        },
                    },
                    project_id="p",
                )
                usage = observation["usage"]
                self.assertEqual(
                    tuple(usage[key] for key in ("input", "output", "total")), expected
                )
                self.assertEqual((usage["cacheWriteInput"], usage["reasoning"]), (0, 0))
                self.assertEqual(usage["source"][field], attribute)
                self.assertEqual(
                    usage["source"]["cacheWriteInput"],
                    "langfuse.complete_usage_absent_bucket",
                )
                self.assertEqual(
                    usage["source"]["reasoning"],
                    "langfuse.complete_usage_absent_bucket",
                )
                self.assertIsNone(usage["unknownReason"])
                wire = observation_wire(observation)
                self.assertEqual(
                    tuple(wire["usage"][key] for key in ("input", "output", "total")),
                    expected,
                )
                self.assertEqual(wire["usageDetails"], details)

    def test_otel_overrides_cannot_make_incomplete_or_contradictory_raw_usage_complete(
        self,
    ):
        from apps.core.trace_query.langfuse.native import observation_wire
        from apps.core.trace_query.semantics.observations import project_observation

        prices = [
            {
                "model": "m",
                "provider": "p",
                "currency": "USD",
                "unitTokens": 1,
                "input": "1",
                "output": "1",
                "version": "test",
                "effectiveFrom": "2026-01-01T00:00:00Z",
            }
        ]
        for raw, overrides, expected_output, details in (
            (
                {"input": 10, "output": 3, "total": 20},
                {"gen_ai.usage.cache_read.input_tokens": 7},
                None,
                {"input": 10, "output": 3, "total": 20, "cache_read_input_tokens": 7},
            ),
            (
                {"input": 10, "output": 3, "total": 13, "input_cache_read": "invalid"},
                {"gen_ai.usage.cache_read.input_tokens": 0},
                None,
                {"total": 13, "cache_read_input_tokens": 0},
            ),
            (
                {
                    "input": 10,
                    "output": 3,
                    "total": 18,
                    "input_cache_read": 5,
                    "cache_read_input_tokens": 0,
                },
                {"gen_ai.usage.output_tokens": 0},
                0,
                {
                    "input": 10,
                    "output": 0,
                    "total": 18,
                    "input_cache_read": 5,
                    "cache_read_input_tokens": 0,
                },
            ),
            (
                {"input": 10, "output": 3, "total": 13, "other": 7},
                {},
                None,
                {"input": 10, "output": 3, "total": 13, "other": 7},
            ),
        ):
            with self.subTest(raw=raw, overrides=overrides):
                observation = project_observation(
                    {
                        "span_id": "s",
                        "trace_id": "t",
                        "start_time_unix_nano": "2026-10-03T00:00:00Z",
                        "attributes": {
                            "langfuse.observation.usage_details": json.dumps(raw),
                            "gen_ai.request.model": "m",
                            "gen_ai.provider.name": "p",
                            **overrides,
                        },
                    },
                    project_id="p",
                    prices=prices,
                )
                usage = observation["usage"]
                self.assertIsNone(usage["input"])
                self.assertEqual(usage["output"], expected_output)
                self.assertEqual(usage["total"], raw["total"])
                self.assertIsNone(usage["cacheWriteInput"])
                self.assertIsNone(usage["reasoning"])
                self.assertEqual(usage["unknownReason"], "exclusive_usage_incomplete")
                self.assertIsNone(usage["cost"]["total"])
                self.assertEqual(
                    usage["raw"]["langfuse.observation.usage_details"], json.dumps(raw)
                )
                for attribute in overrides:
                    self.assertIn(attribute, usage["source"].values())
                self.assertEqual(observation_wire(observation)["usageDetails"], details)

    def test_missing_or_incomplete_usage_does_not_invent_cache_zero(self):
        for attrs in (
            {},
            {
                "gen_ai.usage.input_tokens": 10,
                "gen_ai.usage.output_tokens": 3,
                "gen_ai.usage.total_tokens": 13,
            },
            {"langfuse.observation.usage_details": {"input": 10, "output": 3}},
            {
                "langfuse.observation.usage_details": {
                    "input": 10,
                    "output": 3,
                    "total": 20,
                }
            },
            {
                "langfuse.observation.usage_details": {
                    "input": 10,
                    "output": 3,
                    "total": 13,
                    "input_cache_read": "invalid",
                }
            },
        ):
            self.assertIsNone(normalize_usage(attrs)["cacheReadInput"])

    def test_incomplete_exclusive_usage_cannot_assert_inclusive_counts_or_cost(self):
        prices = [
            {
                "model": "m",
                "provider": "p",
                "currency": "USD",
                "unitTokens": 1,
                "input": "1",
                "output": "1",
                "version": "test",
                "effectiveFrom": "2026-01-01T00:00:00Z",
            }
        ]
        for raw, reported_total in (
            ({"input": 10, "output": 3, "total": 20}, 20),
            ({"input": 10, "output": 3}, None),
            (
                {"input": 10, "output": 3, "total": 13, "input_cache_read": "invalid"},
                13,
            ),
        ):
            with self.subTest(raw=raw):
                result = normalize_usage(
                    {"langfuse.observation.usage_details": raw},
                    model="m",
                    provider="p",
                    prices=prices,
                    observed_at="2026-10-03T00:00:00Z",
                )
                self.assertIsNone(result["input"])
                self.assertIsNone(result["output"])
                self.assertEqual(result["total"], reported_total)
                self.assertIsNone(result["inputIncludesCache"])
                self.assertIsNotNone(result["unknownReason"])
                self.assertIsNone(result["cost"]["total"])
                self.assertIsNotNone(result["cost"]["reason"])
                self.assertEqual(
                    result["raw"]["langfuse.observation.usage_details"], raw
                )

    def test_explicit_zero_buckets_make_exclusive_usage_complete_without_total(self):
        raw = {
            "input": 4,
            "input_cache_read": 0,
            "input_cache_creation": 5,
            "output": 6,
            "output_reasoning": 7,
        }
        result = normalize_usage({"langfuse.observation.usage_details": raw})
        self.assertEqual(
            (result["input"], result["output"], result["total"]), (9, 13, 22)
        )
        self.assertEqual(
            (result["cacheReadInput"], result["cacheWriteInput"], result["reasoning"]),
            (0, 5, 7),
        )
        self.assertIsNone(result["unknownReason"])

    def test_complete_exclusive_usage_keeps_cache_and_reasoning_pricing(self):
        prices = [
            {
                "model": "m",
                "provider": "p",
                "currency": "USD",
                "unitTokens": 1,
                "input": "1",
                "output": "3",
                "cacheReadInput": "0.25",
                "cacheWriteInput": "2",
                "reasoning": "4",
                "version": "test",
                "effectiveFrom": "2026-01-01T00:00:00Z",
            }
        ]
        for raw in (
            {
                "input": 4,
                "input_cache_read": 0,
                "input_cache_creation": 5,
                "output": 6,
                "output_reasoning": 7,
            },
            {
                "input": 4,
                "input_cache_read": 0,
                "input_cache_creation": 5,
                "output": 6,
                "output_reasoning": 7,
                "total": 22,
            },
        ):
            with self.subTest(raw=raw):
                result = normalize_usage(
                    {"langfuse.observation.usage_details": raw},
                    model="m",
                    provider="p",
                    prices=prices,
                    observed_at="2026-10-03T00:00:00Z",
                )
                self.assertEqual(
                    (result["input"], result["output"], result["total"]), (9, 13, 22)
                )
                self.assertEqual(result["cost"]["total"], "60")
                self.assertEqual(
                    result["cost"]["details"],
                    {"input": "4", "output": "18", "cacheWriteInput": "10", "reasoning": "28"},
                )
                self.assertEqual(result["cost"]["source"], "estimated")
                self.assertIsNone(result["cost"]["reason"])

    def test_explicit_inclusive_otel_counts_override_incomplete_exclusive_usage(self):
        result = normalize_usage(
            {
                "langfuse.observation.usage_details": {
                    "input": 10,
                    "output": 3,
                    "total": 20,
                },
                "gen_ai.usage.input_tokens": 0,
                "gen_ai.usage.output_tokens": 3,
            }
        )
        self.assertEqual(
            (result["input"], result["output"], result["total"]), (0, 3, 20)
        )
        self.assertTrue(result["inputIncludesCache"])
        self.assertIsNone(result["unknownReason"])
        self.assertIsNone(result["cacheReadInput"])
        self.assertIsNone(result["reasoning"])

    def test_incomplete_exclusive_usage_keeps_reported_cost(self):
        result = normalize_usage(
            {
                "langfuse.observation.usage_details": {
                    "input": 10,
                    "output": 3,
                    "total": 20,
                },
                "langfuse.observation.cost_details": {"total": "0.02"},
            }
        )
        self.assertIsNone(result["input"])
        self.assertEqual(result["cost"]["total"], "0.02")
        self.assertEqual(result["cost"]["source"], "reported")

    def test_ingest_and_read_do_not_restore_incomplete_counts_from_derived_attributes(
        self,
    ):
        from opentelemetry.proto.trace.v1.trace_pb2 import Span
        from apps.core.views.langfuse_ingest import (
            _attribute_values,
            _normalize_token_usage,
        )
        from apps.core.trace_query.semantics.observations import project_observation
        from apps.core.trace_query.langfuse.native import observation_wire

        for raw, total in (
            ({"input": 10, "output": 3, "total": 20}, 20),
            ({"input": 10, "output": 3}, None),
        ):
            with self.subTest(raw=raw):
                span = Span()
                span.attributes.add(
                    key="langfuse.observation.usage_details"
                ).value.string_value = json.dumps(raw)
                _normalize_token_usage(span)
                attrs = _attribute_values(span.attributes)
                self.assertNotIn("gen_ai.usage.input_tokens", attrs)
                self.assertNotIn("gen_ai.usage.output_tokens", attrs)
                self.assertEqual(attrs.get("gen_ai.usage.total_tokens"), total)
                # Older ingestion wrote plausible base totals before coverage
                # was checked. Re-reading raw telemetry must discard those.
                attrs.update(
                    {
                        "gen_ai.usage.input_tokens": 10,
                        "gen_ai.usage.output_tokens": 3,
                        "gen_ai.usage.total_tokens": total if total is not None else 13,
                        "gen_ai.usage.cache_read.input_tokens": 0,
                        "gen_ai.usage.cache_creation.input_tokens": 0,
                        "gen_ai.usage.reasoning.output_tokens": 0,
                        "aivision.normalization.sources": json.dumps(
                            {
                                "gen_ai.usage.input_tokens": "langfuse.exclusive_input_plus_cache",
                                "gen_ai.usage.output_tokens": "langfuse.exclusive_output_plus_reasoning",
                                "gen_ai.usage.total_tokens": (
                                    "langfuse.observation.usage_details.total"
                                    if total is not None
                                    else "derived_input_plus_output"
                                ),
                                "gen_ai.usage.cache_read.input_tokens": "langfuse.complete_usage_absent_bucket",
                                "gen_ai.usage.cache_creation.input_tokens": "langfuse.complete_usage_absent_bucket",
                                "gen_ai.usage.reasoning.output_tokens": "langfuse.complete_usage_absent_bucket",
                            }
                        ),
                    }
                )
                observation = project_observation(
                    {
                        "span_id": "s",
                        "trace_id": "t",
                        "start_time_unix_nano": "2026-10-03T00:00:00Z",
                        "attributes": attrs,
                    },
                    project_id="p",
                )
                self.assertIsNone(observation["usage"]["input"])
                self.assertIsNone(observation["usage"]["output"])
                self.assertEqual(observation["usage"]["total"], total)
                for field in ("cacheReadInput", "cacheWriteInput", "reasoning"):
                    self.assertIsNone(observation["usage"][field])
                wire = observation_wire(observation)
                self.assertEqual(wire["usageDetails"], raw)
                self.assertEqual(wire["usage"]["input"], 0)
                self.assertEqual(wire["usage"]["output"], 0)

    def test_native_v1_keeps_one_sided_exclusive_usage_buckets(self):
        from apps.core.trace_query.langfuse.native import observation_wire
        from apps.core.trace_query.semantics.observations import project_observation

        for raw in ({"input": 11}, {"output": 3}, {"input": 10, "total": 20}):
            with self.subTest(raw=raw):
                attrs = {"langfuse.observation.usage_details": json.dumps(raw)}
                observation = project_observation(
                    {
                        "span_id": "s",
                        "trace_id": "t",
                        "attributes": attrs,
                        "start_time_unix_nano": "2026-10-03T00:00:00Z",
                    },
                    project_id="p",
                )
                self.assertIsNone(observation["usage"]["input"])
                self.assertIsNone(observation["usage"]["output"])
                self.assertEqual(observation_wire(observation)["usageDetails"], raw)
                for field in ("input", "output", "total"):
                    overridden = project_observation(
                        {
                            "span_id": "s",
                            "trace_id": "t",
                            "start_time_unix_nano": "2026-10-03T00:00:00Z",
                            "attributes": {
                                **attrs,
                                f"gen_ai.usage.{field}_tokens": 0,
                            },
                        },
                        project_id="p",
                    )
                    self.assertEqual(overridden["usage"][field], 0)
                    self.assertEqual(
                        observation_wire(overridden)["usageDetails"], {**raw, field: 0}
                    )

    def test_native_v1_explicit_otel_details_override_only_matching_raw_bucket(self):
        from apps.core.trace_query.langfuse.native import observation_wire
        from apps.core.trace_query.semantics.observations import project_observation

        raw = {
            "input": 4,
            "input_cache_read": 2,
            "input_cache_creation": 5,
            "output": 6,
            "output_reasoning": 7,
            "total": 24,
        }
        for field, attribute, raw_key, wire_key in (
            (
                "cacheReadInput",
                "gen_ai.usage.cache_read.input_tokens",
                "input_cache_read",
                "cache_read_input_tokens",
            ),
            (
                "cacheWriteInput",
                "gen_ai.usage.cache_creation.input_tokens",
                "input_cache_creation",
                "cache_creation_input_tokens",
            ),
            (
                "reasoning",
                "gen_ai.usage.reasoning.output_tokens",
                "output_reasoning",
                "reasoning_tokens",
            ),
        ):
            with self.subTest(field=field):
                observation = project_observation(
                    {
                        "span_id": "s",
                        "trace_id": "t",
                        "start_time_unix_nano": "2026-10-03T00:00:00Z",
                        "attributes": {
                            "langfuse.observation.usage_details": json.dumps(raw),
                            attribute: 0,
                        },
                    },
                    project_id="p",
                )
                expected = {key: value for key, value in raw.items() if key != raw_key}
                expected[wire_key] = 0
                self.assertEqual(observation["usage"][field], 0)
                self.assertEqual(
                    observation_wire(observation)["usageDetails"], expected
                )

    def test_real_langchain_cache_buckets_repair_only_derived_standard_counts(self):
        attrs = {
            "langfuse.observation.usage_details": '{"input":474,"output":62,"total":792,"input_cache_read":256}',
            "gen_ai.usage.input_tokens": 474,
            "gen_ai.usage.output_tokens": 62,
            "aivision.normalization.sources": '{"gen_ai.usage.input_tokens":"langfuse.exclusive_input_plus_cache","gen_ai.usage.output_tokens":"langfuse.exclusive_output_plus_reasoning"}',
        }
        result = normalize_usage(attrs)
        self.assertEqual(
            (
                result["input"],
                result["output"],
                result["total"],
                result["cacheReadInput"],
            ),
            (730, 62, 792, 256),
        )
        attrs.pop("aivision.normalization.sources")
        self.assertEqual(normalize_usage(attrs)["input"], 474)

    def test_partial_exclusive_coverage_keeps_known_reasoning_but_not_inclusive_input(
        self,
    ):
        result = normalize_usage(
            {
                "langfuse.observation.usage_details": '{"input":4,"input_cache_creation":5,"output":6,"output_reasoning":7}'
            }
        )
        self.assertEqual(
            (result["input"], result["output"], result["total"]), (None, 13, None)
        )
        self.assertIsNone(result["cacheReadInput"])
        self.assertEqual((result["cacheWriteInput"], result["reasoning"]), (5, 7))

    def test_reported_cost_keeps_exclusive_details_and_inclusive_input(self):
        result = normalize_usage(
            {
                "langfuse.observation.cost_details": '{"input":1,"cache_read_input_tokens":0.5,"output":2}'
            }
        )
        self.assertEqual(result["cost"]["total"], "3.5")
        self.assertEqual(result["cost"]["input"], "1.5")
        self.assertEqual(result["cost"]["details"]["cache_read_input_tokens"], "0.5")

    def test_standard_reported_cost_parts_derive_total_without_price_lookup(self):
        result = normalize_usage(
            {"gen_ai.usage.input_cost": "0.01", "gen_ai.usage.output_cost": "0.03"}
        )
        self.assertEqual(result["cost"]["total"], "0.04")
        self.assertEqual(result["cost"]["source"], "reported")

    def test_price_version_is_selected_at_observation_time(self):
        common = {
            "model": "m",
            "provider": "p",
            "currency": "USD",
            "unitTokens": 1000,
            "output": "0",
        }
        prices = [
            {
                **common,
                "input": "2",
                "version": "new",
                "effectiveFrom": "2026-10-02T00:00:00Z",
            },
            {
                **common,
                "input": "1",
                "version": "old",
                "effectiveFrom": "2026-10-01T00:00:00Z",
            },
        ]
        attrs = {"gen_ai.usage.input_tokens": 1000, "gen_ai.usage.output_tokens": 0}
        old = normalize_usage(
            attrs,
            model="m",
            provider="p",
            prices=prices,
            observed_at="2026-10-01T12:00:00Z",
        )
        self.assertEqual(old["cost"]["total"], "1")
        self.assertEqual(old["cost"]["pricingVersion"], "old")
        new = normalize_usage(
            attrs,
            model="m",
            provider="p",
            prices=prices,
            observed_at="2026-10-02T00:00:00Z",
        )
        self.assertEqual(new["cost"]["total"], "2")
        before = normalize_usage(
            attrs,
            model="m",
            provider="p",
            prices=prices,
            observed_at="2026-09-30T23:59:59Z",
        )
        self.assertIsNone(before["cost"]["total"])
        self.assertEqual(before["cost"]["reason"], "price_not_effective")

    def test_standard_usage_preserves_zero_and_reported_total(self):
        result = normalize_usage(
            {
                "gen_ai.usage.input_tokens": 0,
                "gen_ai.usage.output_tokens": 3,
                "gen_ai.usage.total_tokens": 9,
                "langfuse.observation.usage_details": '{"input":100,"output":20}',
            }
        )
        self.assertEqual(
            (result["input"], result["output"], result["total"]), (0, 3, 9)
        )
        self.assertTrue(result["inputIncludesCache"])
        self.assertEqual(result["source"]["input"], "gen_ai.usage.input_tokens")

    def test_exclusive_langfuse_buckets_become_inclusive_without_double_counting(self):
        raw = {
            "input": 7,
            "cache_read_input_tokens": 5,
            "cache_creation_input_tokens": 2,
            "output": 11,
            "reasoning_tokens": 4,
            "total": 25,
        }
        result = normalize_usage(
            {"langfuse.observation.usage_details": json.dumps(raw)}
        )
        self.assertEqual(
            (result["input"], result["output"], result["total"]), (14, 15, 25)
        )
        self.assertEqual(
            (result["cacheReadInput"], result["cacheWriteInput"], result["reasoning"]),
            (5, 2, 4),
        )
        self.assertEqual(
            result["raw"]["langfuse.observation.usage_details"], json.dumps(raw)
        )

    def test_openai_inclusive_details_are_not_added_to_input_or_output(self):
        result = normalize_usage(
            {
                "langfuse.observation.usage_details": json.dumps(
                    {
                        "prompt_tokens": 10,
                        "completion_tokens": 8,
                        "total_tokens": 18,
                        "prompt_tokens_details": {"cached_tokens": 6},
                        "completion_tokens_details": {"reasoning_tokens": 3},
                    }
                )
            }
        )
        self.assertEqual(
            (result["input"], result["output"], result["total"]), (10, 8, 18)
        )
        self.assertEqual((result["cacheReadInput"], result["reasoning"]), (6, 3))

    def test_missing_and_invalid_usage_are_unknown_not_zero(self):
        for attrs in (
            {},
            {"gen_ai.usage.input_tokens": -1},
            {"gen_ai.usage.input_tokens": True},
            {"gen_ai.usage.input_tokens": float("nan")},
        ):
            result = normalize_usage(attrs)
            self.assertIsNone(result["input"])
            self.assertIsNone(result["total"])
            self.assertIsNone(result["cost"]["total"])
            self.assertIsNotNone(result["unknownReason"])

    def test_cost_is_decimal_and_uses_exclusive_charged_buckets(self):
        prices = [
            {
                "model": "model-a",
                "provider": "vendor",
                "currency": "USD",
                "unitTokens": 1000000,
                "input": "2.50",
                "output": "10",
                "cacheReadInput": "0.25",
                "cacheWriteInput": "3",
                "version": "2026-10-01",
                "effectiveFrom": "2026-10-01T00:00:00Z",
            }
        ]
        result = normalize_usage(
            {
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 10,
                "gen_ai.usage.cache_read.input_tokens": 20,
                "gen_ai.usage.cache_creation.input_tokens": 5,
            },
            model="model-a",
            provider="vendor",
            prices=prices,
        )
        self.assertEqual(result["cost"]["total"], "0.0003075")
        self.assertEqual(result["cost"]["source"], "estimated")
        self.assertEqual(result["cost"]["pricingVersion"], "2026-10-01")
        self.assertEqual(result["cost"]["currency"], "USD")

    def test_reported_cost_wins_and_unknown_price_is_not_free(self):
        reported = normalize_usage(
            {
                "langfuse.observation.cost_details": '{"input":0,"output":0.002,"total":0.002}'
            }
        )
        self.assertEqual(reported["cost"]["total"], "0.002")
        self.assertEqual(reported["cost"]["source"], "reported")
        self.assertEqual(reported["cost"]["input"], "0")
        unknown = normalize_usage(
            {"gen_ai.usage.input_tokens": 1, "gen_ai.usage.output_tokens": 2},
            model="private-model",
            provider="private",
        )
        self.assertIsNone(unknown["cost"]["total"])
        self.assertEqual(unknown["cost"]["reason"], "price_not_configured")

    def test_flat_attributes_have_same_meaning(self):
        result = normalize_usage(
            {
                "attributes.gen_ai.usage.input_tokens": 3,
                "attributes.gen_ai.usage.output_tokens": 4,
            }
        )
        self.assertEqual(result["total"], 7)

    def test_aggregate_excludes_parent_rollups_deduplicates_and_keeps_retry_calls(self):
        records = [
            {
                "id": "root",
                "traceId": "t",
                "type": "AGENT",
                "attributes": {"gen_ai.usage.total_tokens": 99},
            },
            {
                "id": "g1",
                "traceId": "t",
                "type": "GENERATION",
                "attributes": {
                    "gen_ai.usage.input_tokens": 3,
                    "gen_ai.usage.output_tokens": 2,
                },
            },
            {
                "id": "g1",
                "traceId": "t",
                "type": "GENERATION",
                "attributes": {
                    "gen_ai.usage.input_tokens": 3,
                    "gen_ai.usage.output_tokens": 2,
                },
            },
            {
                "id": "retry",
                "traceId": "t",
                "type": "GENERATION",
                "attributes": {
                    "gen_ai.usage.input_tokens": 3,
                    "gen_ai.usage.output_tokens": 4,
                },
            },
        ]
        result = aggregate_usage(records)
        self.assertEqual(
            (result["input"], result["output"], result["total"]), (6, 6, 12)
        )
        self.assertEqual(result["observationCount"], 2)

    def test_nested_generation_rollups_are_flagged_instead_of_guessed(self):
        result = aggregate_usage(
            [
                {
                    "id": "a",
                    "traceId": "t",
                    "type": "GENERATION",
                    "attributes": {"gen_ai.usage.total_tokens": 10},
                },
                {
                    "id": "b",
                    "traceId": "t",
                    "parentObservationId": "a",
                    "type": "GENERATION",
                    "attributes": {"gen_ai.usage.total_tokens": 10},
                },
            ]
        )
        self.assertIsNone(result["total"])
        self.assertEqual(result["unknownReason"], "ambiguous_nested_generations")

    def test_generation_rollup_through_intermediate_span_is_ambiguous(self):
        result = aggregate_usage(
            [
                {
                    "id": "a",
                    "traceId": "t",
                    "type": "GENERATION",
                    "attributes": {"gen_ai.usage.total_tokens": 10},
                },
                {
                    "id": "wrapper",
                    "traceId": "t",
                    "parentObservationId": "a",
                    "type": "SPAN",
                },
                {
                    "id": "b",
                    "traceId": "t",
                    "parentObservationId": "wrapper",
                    "type": "GENERATION",
                    "attributes": {"gen_ai.usage.total_tokens": 10},
                },
            ]
        )
        self.assertIsNone(result["total"])
        self.assertEqual(result["unknownReason"], "ambiguous_nested_generations")
