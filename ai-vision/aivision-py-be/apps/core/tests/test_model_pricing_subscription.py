"""Subscription calls retain usage and reference values without invented charges."""
from django.test import SimpleTestCase, TestCase, override_settings

from apps.core.trace_query.semantics.usage import aggregate_usage, combine_cost_coverage, normalize_usage
from apps.core.tests.test_model_pricing_semantics import call, official_price, price, usage
from apps.core.tests import test_model_pricing as pricing_tests


def subscription(**extra):
    return {"provider": "vendor", "model": "m", "pricingSource": "subscription", "pricingPriority": 500,
            "effectiveFrom": "2026-01-01T00:00:00Z", "version": "subscription-version", "estimateListPrice": True, **extra}


class SubscriptionSemanticsTests(SimpleTestCase):
    def test_subscription_uses_official_base_price_by_resolved_model(self):
        official = official_price(model="resolved-model", cacheReadInput="0.1", reasoning="4",
                                  unsupportedReason="unsupported_cost_fields: multipliers, web_search; unsupported_output_conditions: overrides")
        rates = [price(input="99", output="99"), official]
        normalized = usage(rates, **{
            "aivision.billing.mode": "subscription",
            "aivision.billing.estimate_list_price": False,
            "gen_ai.response.model": "resolved-model",
        })
        self.assertIsNone(normalized["cacheReadInput"])
        self.assertIsNone(normalized["cost"]["total"])
        equivalent = normalized["cost"]["listPriceEquivalent"]
        self.assertEqual(equivalent["total"], "0.00028")
        self.assertEqual(equivalent["pricingSource"], "official_catalog")
        self.assertEqual(equivalent["valuationBasis"], "official_base_catalog")
        self.assertEqual(equivalent["catalogProvider"], "openai")
        self.assertEqual(equivalent["catalogModel"], "resolved-model")

    def test_subscriptions_keep_unpriceable_units_unknown(self):
        for reason in ("unsupported_input_unit", "unsupported_cache_write_ttl",
                       "unsupported_cost_fields: audio, multipliers"):
            with self.subTest(reason=reason):
                estimate = usage([official_price(unsupportedReason=reason), subscription()])["cost"]["listPriceEquivalent"]
                self.assertIsNone(estimate["total"])
                self.assertEqual(estimate["reason"], reason)

    def test_marker_prevents_paygo_charge_but_preserves_separate_equivalent(self):
        result = usage([price(input="99"), official_price(), subscription()])["cost"]
        self.assertIsNone(result["total"])
        self.assertEqual(result["reason"], "subscription_billing")
        self.assertEqual(result["billingMode"], "subscription")
        self.assertEqual(result["pricingVersion"], "subscription-version")
        self.assertEqual(result["listPriceEquivalent"]["total"], "0.00028")
        self.assertEqual(result["listPriceEquivalent"]["pricingSource"], "official_catalog")

    def test_reported_money_including_zero_is_preserved_without_assuming_invoice(self):
        for amount in (0, "3.5"):
            result = usage([official_price(), subscription()], **{"gen_ai.usage.total_cost": amount})["cost"]
            self.assertIsNone(result["total"])
            self.assertEqual(result["reportedCost"]["total"], str(amount))
            self.assertEqual(result["listPriceEquivalent"]["total"], "0.00028")
        result = usage([subscription()], **{"langfuse.observation.cost_details": {"input": "1", "output": "2"}})["cost"]
        self.assertEqual(result["reportedCost"]["total"], "3")
        self.assertIsNone(result["listPriceEquivalent"]["total"])

    def test_explicit_call_mode_overrides_space_marker_in_both_directions(self):
        self.assertEqual(usage([price(), subscription()], **{"aivision.billing.mode": "pay_as_you_go"})["cost"]["total"], "0.00028")
        result = usage([official_price()], **{"aivision.billing.mode": "subscription"})["cost"]
        self.assertIsNone(result["total"])
        self.assertEqual(result["listPriceEquivalent"]["total"], "0.00028")
        self.assertEqual(usage([price()], **{"aivision.billing.mode": "typo"})["cost"]["reason"], "invalid_billing_mode")

    def test_estimate_is_automatic_and_legacy_options_do_not_change_it(self):
        rates = [official_price(), subscription(estimateListPrice=False)]
        for legacy in (False, True, "true", 1, None):
            result = usage(rates, **{"aivision.billing.estimate_list_price": legacy})["cost"]
            self.assertIsNone(result["total"])
            self.assertTrue(result["estimateListPrice"])
            self.assertEqual(result["listPriceEquivalent"]["total"], "0.00028")

    def test_future_marker_does_not_apply_and_marker_requires_observation_time(self):
        self.assertEqual(usage([price(), subscription(effectiveFrom="2026-03-01T00:00:00Z")])["cost"]["total"], "0.00028")
        result = normalize_usage({"gen_ai.usage.total_cost": 0}, model="m", provider="vendor", prices=[subscription()])["cost"]
        self.assertIsNone(result["total"])
        self.assertEqual(result["reason"], "pricing_time_missing")
        for when in (None, "invalid"):
            result = usage([price(), subscription(effectiveFrom=when)])["cost"]
            self.assertIsNone(result["total"])
            self.assertEqual(result["reason"], "invalid_price_configuration")

    def test_provider_and_model_boundaries_and_missing_equivalent(self):
        for change in ({"provider": "other"}, {"model": "other"}):
            self.assertEqual(usage([price(), subscription(**change)])["cost"]["total"], "0.00028")
        cost = usage([subscription()])["cost"]
        self.assertIsNone(cost["total"])
        self.assertEqual(cost["listPriceEquivalent"]["reason"], "official_model_price_unavailable")

    def test_partial_equivalent_coverage_never_enters_known_or_reference_subtotals(self):
        priced = usage([official_price(), subscription()])
        rows = [call("s", priced), call("p", usage([price()])),
                call("u", usage([subscription()])), call("s", priced)]
        result = aggregate_usage(rows)
        self.assertIsNone(result["cost"]["total"])
        coverage = result["costCoverage"]
        self.assertEqual((coverage["billableCalls"], coverage["knownCostCalls"], coverage["subscriptionCalls"], coverage["unknownCalls"]), (3, 1, 2, 2))
        self.assertEqual(coverage["knownSubtotal"], "0.00028")
        self.assertIsNone(coverage["referenceSubtotal"])
        self.assertEqual(coverage["listPriceEquivalentSubtotal"], "0.00028")
        self.assertEqual(coverage["listPriceEquivalentCalls"], 1)
        self.assertEqual(coverage.get("listPriceEquivalentSources"), {"official_catalog": 1})
        self.assertEqual(coverage["unknownReasons"], {"subscription_billing": 2})
        combined = combine_cost_coverage([coverage, aggregate_usage([call("extra", priced)])["costCoverage"]])
        self.assertEqual(combined["subscriptionCalls"], 3)
        self.assertEqual(combined["listPriceEquivalentSubtotal"], "0.00056")
        self.assertEqual(combined["listPriceEquivalentCalls"], 2)
        self.assertEqual(combined.get("listPriceEquivalentSources"), {"official_catalog": 2})

    def test_account_rates_do_not_override_official_estimates_or_become_charges(self):
        rows = []
        for source in ("manual", "alias", "catalog", "reference"):
            normalized = usage([price(source, input="99"), official_price(), subscription()], **{"gen_ai.usage.total_cost": "3.5"})
            self.assertEqual(normalized["cost"]["reportedCost"]["total"], "3.5")
            rows.append(call(source, normalized))
        rows.append(call("paygo", usage([price("catalog")])))
        coverage = aggregate_usage([*rows, rows[0]])["costCoverage"]
        self.assertEqual(coverage.get("listPriceEquivalentSources"), {"official_catalog": 4})
        self.assertEqual(coverage["listPriceEquivalentCalls"], 4)
        self.assertEqual(coverage["listPriceEquivalentSubtotal"], "0.00112")
        self.assertEqual(coverage["knownCostCalls"], 1)
        self.assertEqual(coverage["knownSubtotal"], "0.00028")
        self.assertEqual(coverage["catalogCalls"], 1)
        for source in ("manual", "alias", "reference", "reported"):
            self.assertEqual(coverage[source + "Calls"], 0)
        self.assertIsNone(coverage["referenceSubtotal"])

    def test_unpriced_equivalents_omit_empty_source_maps(self):
        rows = [call("paygo", usage([price()])),
                call("unpriced", usage([subscription()]))]
        coverage = aggregate_usage(rows)["costCoverage"]
        self.assertNotIn("listPriceEquivalentSources", coverage)
        self.assertNotIn("listPriceEquivalentSources", combine_cost_coverage([coverage]))

    def test_session_combines_equivalent_sources_across_traces(self):
        from apps.core.trace_query.canonical_sessions import aggregate_trace_usage
        traces = []
        for index in range(3):
            normalized = aggregate_usage([call(str(index), usage([official_price(), subscription()]))])
            traces.append({"usage": normalized, "coverage": {"billableCalls": 1}})
        coverage = aggregate_trace_usage(traces)["costCoverage"]
        self.assertEqual(coverage.get("listPriceEquivalentSources"), {"official_catalog": 3})
        self.assertEqual(coverage["listPriceEquivalentSubtotal"], "0.00084")
        self.assertEqual(coverage["referenceCalls"], 0)
        self.assertIsNone(coverage["knownSubtotal"])

    def test_nested_generation_ownership_disables_equivalent_subtotals(self):
        parent = call("p", usage([official_price(), subscription()]))
        child = call("c", usage([official_price(), subscription()]), parentObservationId="p")
        coverage = aggregate_usage([child], context=[parent, child])["costCoverage"]
        self.assertFalse(coverage["additive"])
        self.assertIsNone(coverage["listPriceEquivalentSubtotal"])
        self.assertEqual(coverage["listPriceEquivalentCalls"], 1)

    def test_subscription_values_inclusive_input_with_unclassified_cache_usage(self):
        # Replay the shapes of two observed responses; tariff values remain a
        # controlled official catalog fixture, not evidence of an actual plan charge.
        rates = [official_price(provider="zhipuai", model="glm-5.1", input="1.4", output="4.4",
                                cacheReadInput="0.26", cacheWriteInput="0.2")]
        rows = []
        for identifier, raw, expected in (
            ("reasoning", {"prompt_tokens": 58, "completion_tokens": 256, "total_tokens": 314,
                           "completion_tokens_details": {"reasoning_tokens": 256}}, "0.0012076"),
            ("answer", {"prompt_tokens": 52, "completion_tokens": 31, "total_tokens": 83}, "0.0002092"),
        ):
            normalized = normalize_usage({"langfuse.observation.usage_details": raw,
                                          "aivision.billing.mode": "subscription",
                                          "aivision.billing.estimate_list_price": True},
                                         provider="baidu-tokenplan", model="glm-5.1", prices=rates,
                                         observed_at="2026-10-26T00:00:00Z")
            self.assertIsNone(normalized["cacheReadInput"])
            self.assertIsNone(normalized["cacheWriteInput"])
            self.assertIsNone(normalized["cost"]["total"])
            self.assertEqual(normalized["cost"]["listPriceEquivalent"]["total"], expected)
            rows.append(call(identifier, normalized))
        result = aggregate_usage([*rows, rows[0]])
        self.assertEqual(result["total"], 397)
        self.assertIsNone(result["cost"]["total"])
        coverage = result["costCoverage"]
        self.assertEqual(coverage["subscriptionCalls"], 2)
        self.assertEqual(coverage["listPriceEquivalentCalls"], 2)
        self.assertEqual(coverage["unknownReasons"], {"subscription_billing": 2})
        self.assertIsNone(coverage["knownSubtotal"])
        self.assertEqual(coverage["listPriceEquivalentSubtotal"], "0.0014168")
        self.assertNotIn("listPriceEquivalentUnknownReasons", coverage)
        first, second = (aggregate_usage([row])["costCoverage"] for row in rows)
        self.assertEqual(combine_cost_coverage([first, second])["listPriceEquivalentSubtotal"], "0.0014168")

    def test_subscription_uses_reported_cache_buckets_and_regular_rate_for_remaining_input(self):
        official = official_price(cacheReadInput="0.25", cacheWriteInput="0.5")
        attrs = {"aivision.billing.mode": "subscription", "gen_ai.usage.cache_read.input_tokens": 25}
        for writes, expected in ((None, "0.00023625"), (10, "0.00022125")):
            with self.subTest(writes=writes):
                normalized = usage([official], **{**attrs, "gen_ai.usage.cache_write.input_tokens": writes})
                self.assertEqual(normalized["cacheReadInput"], 25)
                self.assertEqual(normalized["cacheWriteInput"], writes)
                self.assertIsNone(normalized["cost"]["total"])
                self.assertEqual(normalized["cost"]["listPriceEquivalent"]["total"], expected)
        paygo = usage([price(cacheReadInput="0.25", cacheWriteInput="0.5")], **{
            **attrs, "aivision.billing.mode": "pay_as_you_go",
        })["cost"]
        self.assertIsNone(paygo["total"])
        self.assertEqual(paygo["reason"], "cache_usage_missing")

    def test_equivalent_failure_reasons_only_describe_subscription_estimates(self):
        rows = [call("paygo", usage([])),
                call("unpriced", usage([subscription(estimateListPrice=False)])),
                call("priced", usage([official_price(), subscription()]))]
        coverage = aggregate_usage(rows)["costCoverage"]
        expected = {"official_model_price_unavailable": 1}
        self.assertEqual(coverage["listPriceEquivalentUnknownReasons"], expected)
        self.assertEqual(combine_cost_coverage([coverage])["listPriceEquivalentUnknownReasons"], expected)


class SubscriptionApiTests(TestCase):
    setUp = pricing_tests.PricingApiTests.setUp

    @override_settings(AIVISION_MODEL_PRICES='invalid')
    def test_invalid_deployment_prices_cannot_erase_subscription_billing_safeguard(self):
        from apps.core.services import model_pricing as pricing
        body = {key: value for key, value in self.body.items() if key != 'rates'}
        body.update(kind='subscription', estimateListPrice=True)
        response = self.client.post(self.base + '/rules', body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        prices = pricing.get_space_model_prices(self.space)
        cost = normalize_usage({'gen_ai.usage.input_tokens': 100, 'gen_ai.usage.output_tokens': 20,
                                'gen_ai.usage.total_cost': '100'},
                               provider=body['provider'], model=body['model'], prices=prices,
                               observed_at='2026-10-26T00:00:00Z')['cost']
        self.assertIsNone(cost['total'])
        self.assertEqual(cost['reason'], 'subscription_billing')
        self.assertEqual(cost['reportedCost']['total'], '100')
        self.assertIsNone(cost['listPriceEquivalent']['total'])
        self.assertEqual(cost['listPriceEquivalent']['reason'], 'official_model_price_unavailable')

    def test_subscription_rule_is_space_scoped_immutable_marker_without_prices(self):
        from apps.core.services import model_pricing as pricing
        body = {key: value for key, value in self.body.items() if key != "rates"}
        body["kind"] = "subscription"
        response = self.client.post(self.base + "/rules", body, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["rates"], {})
        self.assertFalse(response.data["data"]["estimateListPrice"])
        rules = pricing.get_space_model_prices(self.space)
        marker = next(row for row in rules if row["pricingSource"] == "subscription")
        self.assertEqual(marker["pricingPriority"], 500)
        self.assertFalse(any(row.get("pricingSource") == "subscription" for row in pricing.get_space_model_prices(self.other)))
        self.assertEqual(self.client.post(self.base + "/rules", body, format="json").status_code, 400)
        body["effectiveFrom"] = "2026-10-27T00:00:00Z"
        for fields in ({"rates": {"input": "0"}}, {"rates": None}, {"catalogProvider": "vendor"}, {"catalogSnapshot": "hash"}, {"effectiveFrom": None}, {"estimateListPrice": "true"}, {"estimateListPrice": 1}):
            self.assertEqual(self.client.post(self.base + "/rules", {**body, **fields}, format="json").status_code, 400)
        response = self.client.post(self.base + "/rules", {**body, "estimateListPrice": True}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["data"]["estimateListPrice"])
        self.client.force_authenticate(self.users[1])
        self.assertEqual(self.client.post(self.base + "/rules", body, format="json").status_code, 403)
