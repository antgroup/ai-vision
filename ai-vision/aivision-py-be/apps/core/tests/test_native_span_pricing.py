"""Selected native calls expose the same conservative pricing as trace totals."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.core.tests.test_model_pricing_semantics import official_price, price
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.semantics import usage as usage_semantics
from apps.core.trace_query.types import ObservationDetailQuery, SpanBatchQuery, TraceDetailQuery


class NativeSpanPricingTests(SimpleTestCase):
    def setUp(self):
        self.space = SimpleNamespace(type=1, uuid="space-1")
        self.user = SimpleNamespace(observation_subject_id="subject")
        self.provider = SignozTraceProvider()
        self.call = {
            "trace_id": "trace", "span_id": "call", "name": "chat m",
            "start_time_unix_nano": "1790989323000000000",
            "end_time_unix_nano": "1790989324000000000",
            "resource": {"aivision.space.id": "space-1"},
            "attributes": {
                "langfuse.observation.type": "GENERATION", "gen_ai.provider.name": "vendor",
                "gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.cache_read.input_tokens": 80, "gen_ai.usage.output_tokens": 20,
            },
        }

    def read(self, spans, prices, *, mode="trace"):
        client = Mock()
        client.get.side_effect = [
            {"traceId": "trace", "spans": deepcopy(spans)},
            {"data": [{"trace_id": "trace", "span_count": len(spans)}]},
        ]
        with (
            patch.object(self.provider, "_context", return_value=(client, {})),
            patch("apps.core.trace_query.signoz.provider.get_space_model_prices", return_value=prices),
        ):
            if mode == "batch":
                return self.provider.get_span_batch(SpanBatchQuery(self.space, self.user, {}, "trace", ["call"]))
            if mode == "observation":
                return self.provider.get_observation_detail(ObservationDetailQuery(self.space, self.user, {}, "call", "trace"))
            return self.provider.get_trace_detail(TraceDetailQuery(self.space, self.user, {}, "trace"))

    def test_cached_subscription_call_keeps_equivalent_and_source_in_detail_and_batch(self):
        self.call["attributes"]["aivision.billing.mode"] = "subscription"
        prices = [official_price(cacheReadInput="0.1")]
        result = self.read([self.call], prices)
        span = result["spans"][0]
        self.assertIn("usage", span)
        cost = span["usage"]["cost"]
        self.assertIsNone(cost["total"])
        self.assertEqual(cost["listPriceEquivalent"]["total"], "0.000128")
        self.assertEqual(cost["listPriceEquivalent"]["pricingSource"], "official_catalog")
        self.assertEqual(span["costCoverage"], result["semantics"]["costCoverage"])
        self.assertEqual(span["costCoverage"]["listPriceEquivalentSources"], {"official_catalog": 1})
        self.assertEqual({key: value for key, value in span["attributes"].items()
                          if key != "langfuse.trace.tags"}, self.call["attributes"])
        for mode in ("batch", "observation"):
            with self.subTest(mode=mode):
                selected = self.read([self.call], prices, mode=mode)
                self.assertEqual(selected["spans"][0] if mode == "batch" else selected, span)

    def test_explicit_zero_is_visible_and_generic_rollup_has_no_call_price(self):
        self.call["attributes"]["gen_ai.usage.total_cost"] = "0"
        root = {**deepcopy(self.call), "span_id": "root", "attributes": {"langfuse.observation.type": "AGENT"}}
        self.call["parent_span_id"] = "root"
        result = self.read([root, self.call], [])
        selected = {span["span_id"]: span for span in result["spans"]}
        self.assertIn("usage", selected["call"])
        self.assertEqual(selected["call"]["usage"]["cost"]["total"], "0")
        self.assertEqual(selected["call"]["costCoverage"]["reportedCalls"], 1)
        self.assertNotIn("costCoverage", selected["root"])
        self.assertNotIn("usage", selected["root"])

    def test_selected_nested_call_retains_full_trace_ownership_warning(self):
        parent = {**deepcopy(self.call), "span_id": "parent"}
        self.call["parent_span_id"] = "parent"
        separate = {**deepcopy(self.call), "span_id": "separate", "parent_span_id": None}
        result = self.read([parent, self.call, separate], [price(cacheReadInput="0.1")])
        for span in result["spans"][:2]:
            self.assertIn("costCoverage", span)
            self.assertFalse(span["costCoverage"]["additive"])
            self.assertIsNone(span["costCoverage"]["knownSubtotal"])
        self.assertTrue(result["spans"][2]["costCoverage"]["additive"])
        self.assertEqual(result["spans"][2]["costCoverage"]["knownSubtotal"], "0.000128")
        self.assertFalse(result["semantics"]["costCoverage"]["additive"])

    def test_call_coverages_reuse_one_ownership_scan(self):
        calls = [{"traceId": "trace", "id": str(index), "type": "GENERATION",
                  "usage": {"cost": {"total": "0", "source": "reported"}}}
                 for index in range(100)]
        with patch.object(usage_semantics, "_nested_billable_calls",
                          wraps=usage_semantics._nested_billable_calls) as ownership:
            coverages = usage_semantics.observation_cost_coverages(calls)
        self.assertEqual(ownership.call_count, 1)
        self.assertEqual(len(coverages), 100)
        self.assertTrue(all(item["knownSubtotal"] == "0" for item in coverages.values()))
