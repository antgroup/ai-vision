"""Text pricing and cost evidence over exact canonical call identities."""
from django.test import SimpleTestCase, TestCase

from apps.core.trace_query.semantics.usage import aggregate_usage, normalize_usage
from apps.core.trace_query.canonical_sessions import aggregate_trace_usage
from apps.core.trace_query.signoz.read_model import projection_context


def price(source="manual", **extra):
    return {"model": "m", "provider": "vendor", "input": "2", "output": "4",
            "currency": "USD", "unitTokens": 1000000, "version": source,
            "effectiveFrom": "2026-01-01T00:00:00Z", "pricingSource": source,
            "pricingPriority": {"manual": 400, "alias": 300, "catalog": 200, "reference": 100}[source],
            "valuationBasis": "effective_price", **extra}


def official_price(*, provider="openai", model="m", **extra):
    return price("catalog", **{
        "provider": provider, "model": model, "canonicalModelId": model,
        "catalogProvider": provider, "catalogModel": model, "catalogSnapshot": "official-snapshot",
        "version": "official-snapshot", "valuationBasis": "current_catalog", "effectiveFrom": None,
        **extra,
    })


def usage(prices, **attrs):
    return normalize_usage({"gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 20, **attrs},
                           model="m", provider="vendor", prices=prices,
                           observed_at="2026-02-01T00:00:00Z")


def call(identifier, normalized, **extra):
    return {"traceId": "t", "id": identifier, "type": "GENERATION", "usage": normalized, **extra}


class ModelPricingSemanticsTests(SimpleTestCase):
    def test_rule_priority_precedes_newer_catalog_and_retains_provenance(self):
        rates = [price("catalog", input="8", effectiveFrom="2026-01-20T00:00:00Z"), price("manual")]
        result = usage(rates)["cost"]
        self.assertEqual(result["total"], "0.00028")
        self.assertEqual(result["pricingSource"], "manual")
        self.assertEqual(result["valuationBasis"], "effective_price")

    def test_priority_tie_uses_latest_effective_rule_but_exact_tie_is_unknown(self):
        rates = [price(input="9"), price(input="3", effectiveFrom="2026-01-20T00:00:00Z")]
        self.assertEqual(usage(rates)["cost"]["total"], "0.00038")
        self.assertEqual(usage([price(), price()])["cost"]["reason"], "ambiguous_price_configuration")

    def test_exact_provider_identity_prevents_same_model_custom_host_pricing(self):
        self.assertEqual(usage([price(provider="https://proxy.example")])["cost"]["reason"], "price_not_configured")

    def test_routed_kimi_call_can_be_priced_but_legacy_placeholders_cannot(self):
        attrs = {"gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 20,
                 "gen_ai.usage.cache_read.input_tokens": 30}
        observed_at = "2026-02-01T00:00:00Z"
        model = "gateway/route/model-a"
        rate = price("reference", provider="gateway", model=model, cacheReadInput="0.1",
                     cacheWriteInput="2.5", catalogProvider="another-host", catalogModel="model-a")
        lookup = {"model": model, "provider": "gateway", "observed_at": observed_at}
        cost = normalize_usage(attrs, prices=[rate], **lookup)["cost"]
        self.assertEqual((cost["total"], cost["pricingSource"]), ("0.000223", "reference"))
        manual = price(provider="gateway", model=model, cacheReadInput="0.1")
        cost = normalize_usage(attrs, prices=[manual, rate], **lookup)["cost"]
        self.assertEqual((cost["total"], cost["pricingSource"]), ("0.000223", "manual"))
        for provider, model, reason in (
            ("unknown", "unknown", "provider_unknown"),
            ("gateway", "unknown", "model_unknown"),
        ):
            with self.subTest(reason=reason):
                old_rule = price("reference", provider=provider, model=model)
                cost = normalize_usage(attrs, model=model, provider=provider, prices=[old_rule], observed_at=observed_at)["cost"]
                self.assertEqual((cost["total"], cost["reason"]), (None, reason))
                reported = normalize_usage({**attrs, "gen_ai.usage.total_cost": 0}, model=model,
                                           provider=provider, prices=[old_rule], observed_at=observed_at)["cost"]
                self.assertEqual((reported["total"], reported["source"]), ("0", "reported"))

    def test_current_catalog_values_old_observation_and_marks_basis(self):
        rate = price("catalog", effectiveFrom=None, valuationBasis="current_catalog",
                     catalogProvider="vendor", catalogModel="m", catalogSnapshot="snapshot-sha")
        cost = usage([rate])["cost"]
        self.assertEqual(cost["total"], "0.00028")
        self.assertEqual(cost["valuationBasis"], "current_catalog")
        self.assertIsNone(cost["pricingEffectiveFrom"])
        self.assertEqual(cost["catalogSnapshot"], "snapshot-sha")

    def test_future_rule_does_not_override_effective_catalog_fallback(self):
        rate = price("catalog", effectiveFrom=None, valuationBasis="current_catalog")
        self.assertEqual(usage([price(effectiveFrom="2026-03-01T00:00:00Z"), rate])["cost"]["pricingSource"], "catalog")
        self.assertEqual(usage([price(effectiveFrom="2026-03-01T00:00:00Z")])["cost"]["reason"], "price_not_effective")

    def test_reasoning_rate_replaces_inclusive_output_partition(self):
        result = usage([price(reasoning="10")], **{"gen_ai.usage.reasoning.output_tokens": 5})["cost"]
        self.assertEqual(result["total"], "0.00031")
        self.assertEqual(result["output"], "0.00011")
        self.assertEqual(result["details"]["reasoning"], "0.00005")

    def test_missing_optional_usage_keeps_base_estimate_and_unknown_measurements(self):
        result = usage([price(cacheReadInput="0", cacheWriteInput="1", reasoning="0")])
        self.assertEqual([result[k] for k in ("cacheReadInput", "cacheWriteInput", "reasoning")], [None, None, None])
        self.assertEqual(result["cost"]["details"], {"input": "0.0002", "output": "0.00008"})
        self.assertEqual((result["cost"]["source"], result["cost"]["total"]), ("estimated", "0.00028"))

    def test_unpriced_optional_tokens_are_excluded_and_explicit_free_usage_is_retained(self):
        attrs = {
            "gen_ai.usage.cache_read.input_tokens": 30,
            "gen_ai.usage.cache_write.input_tokens": 10,
            "gen_ai.usage.reasoning.output_tokens": 5,
        }
        base_only = usage([price()], **attrs)["cost"]
        self.assertEqual(base_only["details"], {"input": "0.00012", "output": "0.00006"})
        self.assertEqual((base_only["total"], base_only["knownSubtotal"], base_only["reason"]),
                         (None, "0.00018", "price_bucket_missing"))
        free_reads = usage([price(cacheReadInput="0")], **attrs)["cost"]
        self.assertEqual(free_reads["details"], {**base_only["details"], "cacheReadInput": "0"})
        self.assertEqual((free_reads["total"], free_reads["knownSubtotal"]), (None, "0.00018"))

    def test_unpriced_usage_does_not_masquerade_as_zero_cost(self):
        attrs = {"gen_ai.usage.cache_read.input_tokens": 100, "gen_ai.usage.reasoning.output_tokens": 20}
        unknown = usage([price()], **attrs)["cost"]
        self.assertEqual((unknown["total"], unknown.get("knownSubtotal"), unknown["reason"]),
                         (None, None, "price_bucket_missing"))
        partial = usage([price(cacheReadInput="0")], **attrs)["cost"]
        self.assertEqual((partial["total"], partial["knownSubtotal"]), (None, "0"))
        free = usage([price(cacheReadInput="0", reasoning="0")], **attrs)["cost"]
        self.assertEqual((free["total"], free["reason"]), ("0", None))
        no_tokens = usage([price()], **{"gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0})["cost"]
        self.assertEqual((no_tokens["total"], no_tokens["reason"]), ("0", None))

    def test_incomplete_call_contributes_only_to_trace_and_session_subtotals(self):
        partial = call("partial", usage([price("reference")], **{"gen_ai.usage.cache_read.input_tokens": 40}))
        complete = call("complete", usage([price()]))
        result = aggregate_usage([partial])
        self.assertIsNone(result["cost"]["total"])
        coverage = result["costCoverage"]
        self.assertEqual((coverage["knownCostCalls"], coverage["unknownCalls"]), (0, 1))
        self.assertEqual((coverage["knownSubtotal"], coverage["referenceSubtotal"]), ("0.0002", "0.0002"))
        self.assertEqual(coverage["unknownReasons"], {"price_bucket_missing": 1})
        self.assertEqual(coverage.get("partialCostSources"), {"reference": 1})
        self.assertEqual(coverage["referenceCalls"], 0)
        combined = aggregate_trace_usage([{"usage": result, "coverage": {"billableCalls": 1}},
                                          {"usage": aggregate_usage([complete]), "coverage": {"billableCalls": 1}}])
        self.assertIsNone(combined["cost"]["total"])
        self.assertEqual(combined["costCoverage"]["knownSubtotal"], "0.00048")
        self.assertEqual(combined["costCoverage"].get("partialCostSources"), {"reference": 1})
        child = {**partial, "parentObservationId": "complete"}
        self.assertIsNone(aggregate_usage([complete, child])["costCoverage"]["knownSubtotal"])

    def test_partial_catalog_and_reported_charges_retain_independent_sources(self):
        cached = {"gen_ai.usage.cache_read.input_tokens": 40}
        partial = call("partial", usage([price("catalog")], **cached))
        reported = call("reported", usage([], **{"gen_ai.usage.total_cost": "1"}))
        missing = call("missing", usage([price("catalog")], **{
            "gen_ai.usage.cache_read.input_tokens": 100,
            "gen_ai.usage.reasoning.output_tokens": 20,
        }))
        coverage = aggregate_usage([partial, reported, missing])["costCoverage"]
        self.assertEqual(coverage["knownSubtotal"], "1.0002")
        self.assertEqual((coverage["reportedCalls"], coverage["catalogCalls"], coverage["unknownCalls"]), (1, 0, 2))
        self.assertEqual(coverage.get("partialCostSources"), {"catalog": 1})

    def test_impossible_breakdowns_are_rejected_even_without_optional_rates(self):
        for attrs in (
            {"gen_ai.usage.reasoning.output_tokens": 21},
            {"gen_ai.usage.cache_read.input_tokens": 91, "gen_ai.usage.cache_write.input_tokens": 10},
        ):
            with self.subTest(attrs=attrs):
                self.assertEqual(usage([price()], **attrs)["cost"]["reason"], "overlapping_usage_buckets")

    def test_unsupported_tariff_remains_unknown_and_reported_zero_wins(self):
        rates = [price(unsupportedReason="unsupported_conditional_tariff")]
        self.assertEqual(usage(rates)["cost"]["reason"], "unsupported_conditional_tariff")
        cost = usage(rates, **{"gen_ai.usage.total_cost": 0})["cost"]
        self.assertEqual((cost["total"], cost["source"]), ("0", "reported"))

    def test_confirmed_reference_bypasses_only_unsupported_automatic_catalog(self):
        catalog = price("catalog", effectiveFrom=None, valuationBasis="current_catalog",
                        output=None, unsupportedReason="unsupported_conditional_tariff")
        reference = price("reference")
        cost = usage([catalog, reference])["cost"]
        self.assertEqual((cost["total"], cost["pricingSource"]), ("0.00028", "reference"))
        valid_catalog = price("catalog", effectiveFrom=None, valuationBasis="current_catalog", input="3")
        cost = usage([valid_catalog, reference])["cost"]
        self.assertEqual((cost["total"], cost["pricingSource"]), ("0.00038", "catalog"))
        for fallback in ([], [price("reference", effectiveFrom="2026-03-01T00:00:00Z")]):
            self.assertEqual(usage([catalog, *fallback])["cost"]["reason"], "unsupported_conditional_tariff")
        for source in ("manual", "alias"):
            self.assertEqual(usage([price(source, unsupportedReason="invalid_explicit_tariff"), reference])["cost"]["reason"],
                             "invalid_explicit_tariff")
        self.assertEqual(usage([price("catalog", output=None), reference])["cost"]["reason"], "price_bucket_missing")
        self.assertEqual(usage([catalog, reference], **{"gen_ai.usage.input_tokens": None})["cost"]["reason"], "usage_missing")
        cost = usage([catalog, reference], **{"gen_ai.usage.total_cost": 0})["cost"]
        self.assertEqual((cost["total"], cost["source"]), ("0", "reported"))

    def test_partial_coverage_deduplicates_and_keeps_reference_subtotal(self):
        rows = [call("manual", usage([price()])), call("reference", usage([price("reference")])),
                call("unknown", usage([])), call("manual", usage([price()]))]
        result = aggregate_usage(rows)
        self.assertIsNone(result["cost"]["total"])
        self.assertEqual(result["costCoverage"], {
            "billableCalls": 3, "knownCostCalls": 2, "reportedCalls": 0, "manualCalls": 1,
            "aliasCalls": 0, "catalogCalls": 0, "referenceCalls": 1, "subscriptionCalls": 0, "listPriceEquivalentCalls": 0, "unknownCalls": 1,
            "knownSubtotal": "0.00056", "referenceSubtotal": "0.00028", "currency": "USD",
            "listPriceEquivalentSubtotal": None,
            "unknownReasons": {"price_not_configured": 1}, "additive": True,
        })

    def test_nested_calls_disable_additive_subtotals_even_across_model_groups(self):
        parent = call("parent", usage([price()]))
        child = call("child", usage([price("reference")]), parentObservationId="parent")
        result = aggregate_usage([child], context=[parent, child])
        self.assertIsNone(result["cost"]["total"])
        self.assertFalse(result["costCoverage"]["additive"])
        self.assertIsNone(result["costCoverage"]["knownSubtotal"])
        self.assertIsNone(result["costCoverage"]["referenceSubtotal"])

    def test_session_coverage_combines_disjoint_trace_subtotals(self):
        first, second = aggregate_usage([call("m", usage([price()]))]), aggregate_usage([call("u", usage([]))])
        result = aggregate_trace_usage([{"usage": first, "coverage": {"billableCalls": 1}},
                                        {"usage": second, "coverage": {"billableCalls": 1}}])
        self.assertIsNone(result["cost"]["total"])
        self.assertEqual(result["costCoverage"]["knownSubtotal"], "0.00028")
        self.assertEqual(result["costCoverage"]["unknownCalls"], 1)

    def test_indexed_price_snapshot_looks_up_only_exact_identity(self):
        from apps.core.trace_query.semantics.usage import IndexedModelPrices
        class CountedPrice(dict):
            reads = 0
            def get(self, *args):
                type(self).reads += 1
                return super().get(*args)
        rates = [CountedPrice(price(model=f"other-{number}")) for number in range(2000)]
        rates.append(CountedPrice(price()))
        indexed = IndexedModelPrices(rates)
        CountedPrice.reads = 0
        self.assertEqual(usage(indexed)["cost"], usage([price()])["cost"])
        self.assertLess(CountedPrice.reads, 60)
        import json
        self.assertEqual(json.loads(json.dumps(indexed)), rates)

    def test_pricing_metadata_and_catalog_snapshot_invalidate_projection(self):
        self.assertNotEqual(projection_context([price(catalogSnapshot="a")]), projection_context([price(catalogSnapshot="b")]))

    def test_resolver_preserves_invalid_legacy_configuration(self):
        from types import SimpleNamespace
        from apps.core.services.model_pricing import get_space_model_prices
        for invalid in ([price(), None], {"model": "m"}, "invalid"):
            with self.subTest(invalid=invalid), self.settings(AIVISION_MODEL_PRICES=invalid):
                prices = get_space_model_prices(SimpleNamespace(uuid="space"))
                self.assertEqual(usage(prices)["cost"]["reason"], "invalid_price_configuration")
                self.assertEqual(usage(prices, **{"gen_ai.usage.total_cost": 0})["cost"]["total"], "0")


class ModelPricingNativeIntegrationTests(TestCase):
    def setUp(self):
        from apps.core.models import Space
        from apps.core.tests.helpers import create_local_user, create_test_space_member
        from apps.core.services.model_pricing import create_rule
        self.user = create_local_user('pricing-native')
        self.space = Space.objects.create(name='pricing', type=2, creator=self.user)
        self.other = Space.objects.create(name='other-pricing', type=2, creator=self.user)
        create_test_space_member(space=self.space, user=self.user, role=1)
        create_rule(space=self.space, user=self.user, data={"provider": "vendor", "model": "m", "kind": "manual",
                                          "effectiveFrom": "2026-01-01T00:00:00Z",
                                          "rates": {"input": "2", "output": "4"}})

    def test_native_provider_applies_only_own_space_rule(self):
        from unittest.mock import patch
        from apps.core.tests.test_native_query import trace
        from apps.core.trace_query.signoz.provider import SignozTraceProvider
        from apps.core.trace_query.types import BaseTraceQuery
        detail = trace(1)
        detail['spans'][0]['attributes'].update({"gen_ai.provider.name": "vendor", "gen_ai.request.model": "m",
            "gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 20})
        provider = SignozTraceProvider()
        with patch.object(provider, 'collect_traces', return_value={'data': [detail], 'meta': {'complete': True}}):
            own, _ = provider._window(BaseTraceQuery(self.space, self.user, {}), _snapshot=False)
            other, _ = provider._window(BaseTraceQuery(self.other, self.user, {}), _snapshot=False)
        self.assertEqual(own.rows[0]['usage']['cost']['total'], '0.00028')
        self.assertIsNone(other.rows[0]['usage']['cost']['total'])

    def test_space_rule_update_changes_read_model_request_fingerprint(self):
        from unittest.mock import Mock
        from apps.core.trace_query.signoz.read_model import query_read_model
        from apps.core.trace_query.types import TraceListQuery
        query = TraceListQuery(self.space, self.user, {'agent_uuid': 'a'})
        provider, client = Mock(), Mock()
        provider._freeze_window_params.return_value = {}
        provider._context.return_value = (client, {})
        provider._params.return_value = {}
        client.get.return_value = {'supported': False, 'reason': 'fixture_fallback'}
        query_read_model(provider, query)
        first = client.get.call_args.kwargs['params']['projection_context']
        from apps.core.models.model_pricing import ModelPricingRule
        ModelPricingRule.objects.filter(space=self.space).update(rates={'input': '3', 'output': '4'})
        query_read_model(provider, query)
        self.assertNotEqual(first, client.get.call_args.kwargs['params']['projection_context'])
