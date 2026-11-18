from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from apps.core.models import ModelExchangeRateSnapshot, ModelExchangeRateState, Space, User
from apps.core.services.model_pricing import get_space_model_prices, store_catalog
from apps.core.trace_query.semantics.usage import cost_coverage, normalize_usage
from apps.core.trace_query.signoz.read_model import projection_context
from apps.core.trace_query.signoz.read_model_projection import build_projection


@override_settings(AIVISION_MODEL_PRICES=[])
class PublisherSubscriptionPriceTests(TestCase):
    def setUp(self):
        owner = User.objects.create(email="publisher@example.test", normalized_email="publisher@example.test")
        self.space = Space.objects.create(name="Publisher pricing", type=2, creator=owner)

    def set_rate(self, usd_to_cny):
        snapshot = ModelExchangeRateSnapshot.objects.create(
            snapshot_id=f"fx-{usd_to_cny}", rate_date=date(2026, 11, 13),
            rates={"USD": "1", "CNY": usd_to_cny},
        )
        ModelExchangeRateState.objects.update_or_create(key="ecb", defaults={"snapshot": snapshot})

    def call(self, model, *, provider="baidu", prices=None, mode="subscription", cache=None):
        attrs = {
            "gen_ai.usage.input_tokens": 100,
            "gen_ai.usage.output_tokens": 10,
            "aivision.billing.mode": mode,
        }
        if cache is not None:
            attrs["gen_ai.usage.cache_read.input_tokens"] = cache
        usage = normalize_usage(attrs, model=model, provider=provider,
                                prices=prices if prices is not None else get_space_model_prices(self.space))
        return {"type": "GENERATION", "id": model, "usage": usage}

    def test_domestic_official_prices_convert_with_saved_fx_and_keep_native_evidence(self):
        self.set_rate("8")
        prices = get_space_model_prices(self.space)
        light = self.call("ernie-light-dt-1.0", prices=prices, cache=20)
        flash = self.call("ernie-5.5-flash-preview", prices=prices)
        lcost = light["usage"]["cost"]
        fcost = flash["usage"]["cost"]
        self.assertIsNone(lcost["total"])
        self.assertIsNone(fcost["total"])
        first, second = (cost["listPriceEquivalent"] for cost in (lcost, fcost))
        self.assertEqual(first["total"], "0.000117")
        self.assertEqual(second["total"], "0.0000175")
        self.assertEqual(first["currency"], "USD")
        self.assertEqual(first["pricingSource"], "official_publisher")
        tariff = first["publisherTariff"]
        self.assertEqual(tariff["rates"], {"input": "8", "cacheReadInput": "0.8", "output": "28"})
        self.assertEqual(tariff["currency"], "CNY")
        self.assertEqual(tariff["exchangeRate"]["snapshotId"], "fx-8")
        coverage = cost_coverage([light, flash])
        self.assertEqual(coverage["listPriceEquivalentSources"], {"official_publisher": 2})
        self.assertEqual(coverage["listPriceEquivalentSubtotal"], "0.0001345")
        self.assertIsNone(coverage["knownSubtotal"])

        self.set_rate("4")
        refreshed = get_space_model_prices(self.space)
        self.assertNotEqual(projection_context(prices), projection_context(refreshed))
        self.assertEqual(
            Decimal(self.call("ernie-light-dt-1.0", prices=refreshed, cache=20)["usage"]["cost"]["listPriceEquivalent"]["total"]),
            2 * Decimal(first["total"]),
        )

    def test_missing_fx_only_blocks_native_cny_and_archive_stays_distinct(self):
        domestic = self.call("ernie-light-dt-1.0")["usage"]["cost"]["listPriceEquivalent"]
        self.assertIsNone(domestic["total"])
        self.assertEqual(domestic["reason"], "publisher_exchange_rate_unavailable")
        archive = self.call("deepseek-v4-flash-0731")["usage"]["cost"]["listPriceEquivalent"]
        self.assertEqual(archive["total"], "0.0000286")
        self.assertEqual(archive["pricingSource"], "archived_publisher")
        self.assertEqual(archive["publisherTariff"]["effectiveUntil"], "2026-09-10T04:00:00Z")
        self.assertIsNone(self.call("deepseek-v4-flash-0731", provider="other")["usage"]["cost"]["listPriceEquivalent"]["total"])

    def test_supplement_never_prices_metered_calls_or_replaces_a_publisher_catalog_row(self):
        self.set_rate("8")
        for prices in (get_space_model_prices(self.space), list(get_space_model_prices(self.space))):
            cost = self.call("ernie-light-dt-1.0", prices=prices, mode="pay_as_you_go")["usage"]["cost"]
            self.assertEqual(cost["reason"], "price_not_configured")
            self.assertIsNone(cost["total"])
        store_catalog({"baidu": {"models": {"ernie-light-dt-1.0": {
            "canonical_model_id": "baidu/ernie-light-dt-1.0",
            "cost": {"input": "5", "output": "6"},
        }}}})
        estimate = self.call("ernie-light-dt-1.0")["usage"]["cost"]["listPriceEquivalent"]
        self.assertEqual(estimate["pricingSource"], "official_catalog")
        self.assertEqual(estimate["total"], "0.00056")

    def test_exact_host_identity_precedes_an_archived_route_name(self):
        retired = "deepseek/deepseek-v4-flash-0731"
        store_catalog({
            "iteracompute": {"models": {retired: {"canonical_model_id": "deepseek/deepseek-v4.1-flash"}}},
            "bothub": {"models": {"v4-flash": {"canonical_model_id": retired}}},
            "deepseek": {"models": {"deepseek-flash": {
                "canonical_model_id": "deepseek/deepseek-v4.1-flash-1017",
                "cost": {"input": "0.15", "output": "0.6"},
            }}},
        })
        current = self.call(retired, provider="iteracompute")["usage"]["cost"]["listPriceEquivalent"]
        self.assertEqual(current["pricingSource"], "official_catalog")
        self.assertEqual(current["catalogModel"], "deepseek-flash")
        exact_archive = self.call("v4-flash", provider="bothub")["usage"]["cost"]["listPriceEquivalent"]
        self.assertEqual(exact_archive["pricingSource"], "archived_publisher")
        self.assertEqual(exact_archive["total"], "0.0000286")

    def test_native_read_model_projects_publisher_tariff_evidence(self):
        self.set_rate("8")
        space_id = str(self.space.uuid)
        spans = []
        for span_id, model in enumerate(("ernie-light-dt-1.0", "deepseek-v4-flash-0731"), start=1):
            spans.append({
                "trace_id": "trace", "span_id": str(span_id), "name": "model",
                "start_time_unix_nano": 1000000000, "end_time_unix_nano": 2000000000,
                "resource_attributes": {"aivision.space.id": space_id, "aivision.agent.id": "agent",
                                        "ant.agent.product": "langfuse"},
                "attributes": {"langfuse.observation.type": "generation", "gen_ai.provider.name": "baidu",
                               "gen_ai.response.model": model, "aivision.billing.mode": "subscription",
                               "gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 10},
            })
        result = build_projection({
            "supported": True, "contract": "native-trace-source-v1", "complete": True, "trace_id": "trace",
            "source_revision": 1, "maintenance_generation": 1, "span_count": len(spans), "scope_valid": True,
            "source_user_id": "user", "source_agent_product": "langfuse", "spans": spans,
        }, space_id=space_id, prices=get_space_model_prices(self.space))
        costs = {item["model"]: item["usage"]["cost"]["listPriceEquivalent"]
                 for item in result["row"]["_metrics"]["observations"]}
        domestic = costs["ernie-light-dt-1.0"]
        archive = costs["deepseek-v4-flash-0731"]
        self.assertEqual(domestic["total"], "0.000135")
        self.assertEqual(domestic["publisherTariff"]["exchangeRate"]["snapshotId"], "fx-8")
        self.assertEqual(archive["total"], "0.0000286")
        self.assertEqual(archive["publisherTariff"]["rates"]["cacheReadInput"], "0.007")

    def test_catalog_identity_without_base_rates_can_use_a_proven_publisher_tariff(self):
        self.set_rate("8")
        v41 = "deepseek-v4.1-flash"
        light = "ernie-light-dt-1.0"
        flash = "ernie-5.5-flash-preview"
        catalog = {
            "baidu": {"models": {light: {}, flash: {"cost": {"input": "1", "audio": "2"}}}},
            "deepseek": {"models": {
                v41: {"cost": {"input": "0.15"}},
                "deepseek-flash": {"canonical_model_id": f"deepseek/{v41}-1017",
                                   "cost": {"input": "0.15", "output": "0.6"}},
            }},
        }
        store_catalog(catalog)
        expected = {light: ("official_publisher", "0.000135"), v41: ("official_catalog", "0.000021")}
        for model, (source, total) in expected.items():
            with self.subTest(model=model):
                estimate = self.call(model)["usage"]["cost"]["listPriceEquivalent"]
                self.assertEqual((estimate["pricingSource"], estimate["total"]), (source, total))

        unsupported = self.call(flash)["usage"]["cost"]["listPriceEquivalent"]
        self.assertEqual(unsupported["pricingSource"], "official_catalog")
        self.assertIsNone(unsupported["total"])
        self.assertIn("unsupported_cost_fields: audio", unsupported["reason"])

        catalog["baidu"]["models"][light]["canonical_model_id"] = "deepseek/unrelated-model"
        store_catalog(catalog)
        unrelated = self.call(light)["usage"]["cost"]["listPriceEquivalent"]
        self.assertIsNone(unrelated["total"])
