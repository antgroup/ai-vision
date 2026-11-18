"""A bounded trace window shares one price snapshot without retaining per-trace catalogs."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.core.tests.test_model_pricing_semantics import official_price, price
from apps.core.tests.test_native_query import trace
from apps.core.trace_query.native_projection import ProjectedNativeDetail, canonical_trace
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.semantics.usage import IndexedModelPrices
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import BaseTraceQuery, SessionUsageBatchQuery, TraceDetailQuery


class NativePricingSnapshotTests(SimpleTestCase):
    def test_multi_model_chain_keeps_subscription_equivalent_out_of_paygo_subtotal(self):
        # Controlled tariff fixtures exercise raw-span projection, not live
        # vendor prices or invoice reconciliation.
        detail = trace(1)
        root = detail["spans"][0]
        root.update(name="Mixed model workflow", end_time_unix_nano="1790989328000000000")
        root["attributes"] = {"langfuse.observation.type": "AGENT"}
        identities = [
            ("paid", "paygo-vendor", "paid-model", 100, 20, {}),
            ("free", "paygo-vendor", "free-model", 10, 2, {}),
            ("plan", "coding-plan-vendor", "plan-model", 200, 40,
             {"gen_ai.usage.total_cost": "3.5"}),
            ("missing", "unconfigured-vendor", "unconfigured-model", 7, 3, {}),
        ]
        for index, (identifier, provider, model, input_tokens, output_tokens, extra) in enumerate(identities):
            start = 1790989323100000000 + index * 1000000000
            detail["spans"].append({
                **root, "span_id": identifier, "parent_span_id": root["span_id"], "name": model,
                "start_time_unix_nano": str(start), "end_time_unix_nano": str(start + 500000000),
                "attributes": {"langfuse.observation.type": "GENERATION", "gen_ai.provider.name": provider,
                               "gen_ai.request.model": model, "gen_ai.usage.input_tokens": input_tokens,
                               "gen_ai.usage.output_tokens": output_tokens, **extra},
            })
        prices = IndexedModelPrices([
            price(provider="paygo-vendor", model="paid-model"),
            price("catalog", provider="paygo-vendor", model="free-model", input="0", output="0",
                  effectiveFrom=None, valuationBasis="current_catalog"),
            official_price(model="plan-model", input="3", output="6"),
            {"provider": "coding-plan-vendor", "model": "plan-model", "pricingSource": "subscription",
             "pricingPriority": 500, "effectiveFrom": "2026-01-01T00:00:00Z",
             "version": "plan-marker", "estimateListPrice": True},
        ])
        canonical = canonical_trace(detail, project_id="space", prices=prices)
        observations = {item["id"]: item for item in canonical["observations"]}
        plan = observations["plan"]["usage"]["cost"]
        self.assertIsNone(plan["total"])
        self.assertEqual(plan["reportedCost"]["total"], "3.5")
        self.assertEqual(plan["listPriceEquivalent"]["total"], "0.00084")
        self.assertEqual(plan["listPriceEquivalent"]["pricingSource"], "official_catalog")
        self.assertEqual(observations["free"]["usage"]["cost"]["total"], "0")
        self.assertEqual(observations["missing"]["usage"]["cost"]["reason"], "price_not_configured")

        expected_coverage = {
            "billableCalls": 4, "knownCostCalls": 2, "reportedCalls": 0, "manualCalls": 1,
            "aliasCalls": 0, "catalogCalls": 1, "referenceCalls": 0, "subscriptionCalls": 1,
            "listPriceEquivalentCalls": 1, "unknownCalls": 2, "knownSubtotal": "0.00028",
            "referenceSubtotal": None, "listPriceEquivalentSubtotal": "0.00084", "currency": "USD",
            "listPriceEquivalentSources": {"official_catalog": 1},
            "unknownReasons": {"subscription_billing": 1, "price_not_configured": 1}, "additive": True,
        }
        window = NativeTraceWindow([detail], project_id="space", prices=prices)
        listing = window.list({})["data"][0]
        metrics = window.metrics({})
        # Detail, outer list and framework totals must agree despite four
        # distinct models and a separate SDK-reported subscription value.
        for result in (canonical, listing, metrics):
            with self.subTest(surface="detail" if result is canonical else "list" if result is listing else "metrics"):
                self.assertIsNone(result["usage"]["cost"]["total"])
                self.assertEqual(result["usage"]["total"], 382)
                self.assertEqual(result["costCoverage"], expected_coverage)
        grouped = {row["value"]: row["usage"]["costCoverage"] for row in metrics["groups"]}
        self.assertEqual(grouped["plan-model"]["listPriceEquivalentSubtotal"], "0.00084")
        self.assertEqual(grouped["plan-model"].get("listPriceEquivalentSources"), {"official_catalog": 1})
        self.assertIsNone(grouped["plan-model"]["knownSubtotal"])
        self.assertEqual(grouped["free-model"]["knownSubtotal"], "0")

    def test_details_retain_only_compact_price_identity_without_catalog_copy(self):
        class CopyCountedPrice(dict):
            copies = 0

            def __deepcopy__(self, memo):
                type(self).copies += 1
                return dict(self)

        prices = IndexedModelPrices([CopyCountedPrice(price(model=f"model-{i}")) for i in range(2000)])
        CopyCountedPrice.copies = 0
        details = [ProjectedNativeDetail({}, {"sentinel": i}, project_id="space", prices=prices) for i in range(25)]
        self.assertEqual(CopyCountedPrice.copies, 0)
        for item in details:
            self.assertFalse(any(isinstance(value, (list, tuple)) for value in vars(item).values()))
            self.assertIs(canonical_trace(item, project_id="space", prices=prices), item.canonical)

    def test_plain_list_mutation_and_catalog_snapshot_change_invalidate_cached_projection(self):
        prices = [price(catalogSnapshot="before")]
        detail = ProjectedNativeDetail(trace(1), {"sentinel": True}, project_id="space", prices=prices)
        self.assertIs(canonical_trace(detail, project_id="space", prices=prices), detail.canonical)
        prices[0]["input"] = "3"
        self.assertNotIn("sentinel", canonical_trace(detail, project_id="space", prices=prices))
        indexed = IndexedModelPrices([price(catalogSnapshot="before")])
        detail = ProjectedNativeDetail(trace(1), {"sentinel": True}, project_id="space", prices=indexed)
        changed = IndexedModelPrices([price(catalogSnapshot="after")])
        self.assertNotIn("sentinel", canonical_trace(detail, project_id="space", prices=changed))
        self.assertEqual(canonical_trace(detail, project_id="other", prices=indexed)["projectId"], "other")

    def test_indexed_fingerprint_is_cached_for_repeated_projection_reuse(self):
        prices = IndexedModelPrices([price()])
        detail = ProjectedNativeDetail({}, {"sentinel": True}, project_id="space", prices=prices)
        with patch("apps.core.trace_query.semantics.usage.json.dumps", side_effect=AssertionError("catalog reserialized")):
            for _ in range(25):
                self.assertIs(canonical_trace(detail, project_id="space", prices=prices), detail.canonical)

    def test_indexed_snapshot_cannot_be_changed_through_source_or_rate_dictionary(self):
        source = [price(input="2")]
        prices = IndexedModelPrices(source)
        source[0]["input"] = "3"
        self.assertEqual(prices[0]["input"], "2")
        with self.assertRaises(TypeError):
            prices[0]["input"] = "4"
        with self.assertRaises(TypeError):
            prices[0] = price()

    def _collect(self, *, window):
        provider = SignozTraceProvider()
        query = BaseTraceQuery(SimpleNamespace(uuid="space", type=2), None, {"from": 1000, "to": 2000})
        prices = IndexedModelPrices([price()])
        details = {trace(i)["traceId"]: trace(i) for i in (1, 2)}
        listing = {"data": [{"trace_id": key, "span_count": 1} for key in details],
                   "meta": {"page": 1, "limit": 200, "totalItems": 2, "totalPages": 1}}
        with patch("apps.core.trace_query.signoz.provider.get_space_model_prices", return_value=prices) as load, \
             patch.object(provider, "list_traces", return_value=listing), \
             patch.object(provider, "get_trace_detail", side_effect=lambda q, **kw: details[q.trace_id]) as detail:
            if window:
                provider._window(query, _snapshot=False)
            else:
                provider.collect_traces(query)
        self.assertEqual(load.call_count, 1)
        self.assertEqual(detail.call_count, 2)
        self.assertTrue(all(call.kwargs.get("_prices") is prices for call in detail.call_args_list))

    def test_collection_shares_one_pricebook(self):
        self._collect(window=False)

    def test_window_keeps_collection_pricebook(self):
        self._collect(window=True)

    def test_serial_and_parallel_batches_share_one_pricebook(self):
        provider = SignozTraceProvider()
        query = BaseTraceQuery(SimpleNamespace(uuid="space", type=2), None, {})
        prices = IndexedModelPrices([price()])
        for ids in (["trace-0001"], ["trace-0001", "trace-0002"]):
            with self.subTest(ids=ids), \
                 patch("apps.core.trace_query.signoz.provider.get_space_model_prices", return_value=prices) as load, \
                 patch.object(provider, "get_trace_detail", return_value=trace(1)) as detail:
                provider._projected_traces(query, ids)
            self.assertEqual(load.call_count, 1)
            self.assertTrue(all(call.kwargs.get("_prices") is prices for call in detail.call_args_list))

    def test_session_batch_aggregates_priced_window_without_reloading_catalog(self):
        provider = SignozTraceProvider()
        query = SessionUsageBatchQuery(SimpleNamespace(uuid="space", type=2), None, {}, ["first", "second"])
        prices = IndexedModelPrices([price()])
        groups = {}
        for index, session_id in enumerate(query.session_ids, 1):
            detail = trace(index)
            detail["spans"][0]["attributes"].update({"gen_ai.provider.name": "vendor", "gen_ai.request.model": "m"})
            groups[session_id] = [canonical_trace(detail, project_id="space", prices=prices)]
        with patch.object(provider, "_session_traces", return_value=(groups, {"complete": True})), \
             patch("apps.core.trace_query.signoz.provider.get_space_model_prices", return_value=prices) as load:
            result = provider.get_session_usage_batch(query)
        self.assertEqual([item["usage"]["cost"]["total"] for item in result["data"]], ["0.000028", "0.000028"])
        self.assertEqual([item["costCoverage"]["knownCostCalls"] for item in result["data"]], [1, 1])
        load.assert_not_called()

    def test_verified_detail_uses_supplied_book_without_loading_catalog(self):
        provider = SignozTraceProvider()
        query = TraceDetailQuery(SimpleNamespace(uuid="space", type=2), None, {}, "trace-0001")
        raw = trace(1)
        raw["spans"][0]["resource_attributes"] = {"aivision.space.id": "space"}
        client = Mock()
        client.get.side_effect = [raw, {"data": [{"trace_id": query.trace_id, "span_count": 1}]}]
        prices = IndexedModelPrices([])
        with patch.object(provider, "_context", return_value=(client, {})), \
             patch("apps.core.trace_query.signoz.provider.get_space_model_prices", side_effect=AssertionError("reloaded catalog")):
            detail = provider.get_trace_detail(query, _prices=prices)
        self.assertIs(canonical_trace(detail, project_id="space", prices=prices), detail.canonical)
