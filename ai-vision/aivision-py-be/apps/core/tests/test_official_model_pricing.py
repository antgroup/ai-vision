from django.test import SimpleTestCase, TestCase, override_settings

from apps.core.models import Space, User
from apps.core.services import model_pricing
from apps.core.services.official_model_pricing import OfficialModelPriceIndex


def catalog_price(provider, model, canonical=None, **overrides):
    return {
        'provider': provider,
        'model': model,
        'canonicalModelId': canonical or model,
        'catalogProvider': provider,
        'catalogModel': model,
        'catalogSnapshot': 'public-snapshot',
        'version': 'public-snapshot',
        'pricingPriority': 200,
        'pricingSource': 'catalog',
        'valuationBasis': 'current_catalog',
        'input': '1',
        'output': '2',
        **overrides,
    }


class OfficialModelPriceSelectionTests(SimpleTestCase):
    def test_exact_host_alias_points_to_the_publishers_original_tariff(self):
        official = catalog_price('deepseek', 'deepseek-v4-pro', 'deepseek/deepseek-v4-pro-0813')
        hosted = catalog_price('host', 'tenant-deepseek', 'deepseek/deepseek-v4-pro-0813', input='99')
        overrides = [
            {**hosted, 'pricingSource': kind, 'provider': 'deepseek'}
            for kind in ('manual', 'alias', 'reference')
        ]
        prices = OfficialModelPriceIndex([hosted, *overrides, official])
        self.assertIs(prices.resolve('host', 'tenant-deepseek'), official)
        self.assertIs(prices.resolve('baidu', 'deepseek-v4-pro-0813'), official)
        self.assertIs(prices.resolve(None, 'deepseek/deepseek-v4-pro-0813'), official)
        self.assertIsNone(prices.resolve('unknown-host', 'tenant-deepseek'))
        self.assertIsNone(prices.resolve('baidu', 'deepseek-v4-pro-0814'))

    def test_explicit_bare_host_alias_uses_canonical_identity(self):
        unrelated = catalog_price('openai', 'shared-route')
        official = catalog_price('anthropic', 'actual-model', input='4')
        hosted = catalog_price('host', 'shared-route', 'actual-model', input='99')
        index = OfficialModelPriceIndex([unrelated, official, hosted])
        self.assertIs(index.resolve('host', 'shared-route'), official)
        self.assertIsNone(OfficialModelPriceIndex([unrelated, hosted]).resolve('host', 'shared-route'))

    def test_ambiguous_unqualified_names_and_missing_direct_tariffs_stay_unknown(self):
        first = catalog_price('openai', 'same-model')
        second = catalog_price('deepseek', 'same-model')
        third = catalog_price('host', 'same-model', 'unavailable/same-model')
        paygo = catalog_price('openai', 'other-model', pricingPriority=350)
        reseller = catalog_price('reseller', 'reseller-only-model')
        index = OfficialModelPriceIndex([first, second, third, paygo, reseller])
        self.assertIsNone(index.resolve(None, 'same-model'))
        self.assertIsNone(index.resolve('host', 'same-model'))
        self.assertIsNone(index.resolve('openai', 'other-model'))
        self.assertIsNone(index.resolve('reseller', 'reseller-only-model'))
        self.assertIs(index.resolve('openai', 'same-model'), first)

    def test_unsupported_official_tariff_keeps_source_and_never_uses_reseller_scalar(self):
        official = catalog_price('openai', 'gpt-5.5', unsupportedReason='unsupported_cost_fields: tiers')
        reseller = catalog_price('host', 'gpt-5.5', 'openai/gpt-5.5', input='9')
        index = OfficialModelPriceIndex([reseller, official])
        self.assertIs(index.resolve('host', 'gpt-5.5'), official)

    def test_canonical_aliases_require_agreement_on_the_official_tariff(self):
        first = catalog_price('deepseek', 'deepseek-flash', 'deepseek/deepseek-v4.1-flash-1017')
        second = catalog_price('deepseek', 'deepseek-v4-flash', 'deepseek/deepseek-v4.1-flash-1017')
        model = 'deepseek-v4.1-flash-1017'
        index = OfficialModelPriceIndex([second, first])
        self.assertIs(index.resolve('baidu', model), first)

        different = {**second, 'output': '3'}
        index = OfficialModelPriceIndex([different, first])
        self.assertIsNone(index.resolve('baidu', model))
        self.assertIs(index.resolve('deepseek', 'deepseek-v4-flash'), different)

    def test_deepseek_v41_flash_uses_the_price_equivalent_1017_tariff(self):
        model = 'deepseek-v4.1-flash'
        canonical = f'deepseek/{model}'
        official = catalog_price('deepseek', 'deepseek-flash', f'{canonical}-1017', input='0.15', output='0.6')
        hosted = catalog_price('baidu', model, canonical, input='9')
        bare_hosted = catalog_price('baidu', model, input='9')
        for hosted_row in (None, hosted, bare_hosted):
            index = OfficialModelPriceIndex([official, hosted_row])
            for provider, observed in [('baidu', model), ('deepseek', model), (None, canonical)]:
                with self.subTest(provider=provider, model=observed, host_identity=(hosted_row or {}).get('canonicalModelId')):
                    self.assertIs(index.resolve(provider, observed), official)

        self.assertIsNone(index.resolve('unknown-host', model))
        self.assertIsNone(index.resolve('baidu', 'deepseek-v4-flash-0731'))
        self.assertIsNone(OfficialModelPriceIndex([hosted]).resolve('baidu', model))
        for other_canonical in ('other-model', 'other/other-model'):
            with self.subTest(other_canonical=other_canonical):
                conflicting_host = catalog_price('baidu', model, other_canonical)
                self.assertIsNone(OfficialModelPriceIndex([official, conflicting_host]).resolve('baidu', model))

    def test_deepseek_v41_flash_prefers_an_exact_official_tariff(self):
        model = 'deepseek-v4.1-flash'
        canonical = f'deepseek/{model}'
        updated = catalog_price('deepseek', 'deepseek-flash', f'{canonical}-1017', input='0.15', output='0.6')
        exact = catalog_price('deepseek', model, canonical, input='0.2', output='0.8')
        hosted = catalog_price('baidu', model, canonical, input='9')
        index = OfficialModelPriceIndex([updated, exact, hosted])
        self.assertIs(index.resolve('baidu', model), exact)
        self.assertIs(index.resolve('deepseek', model), exact)
        self.assertIs(index.resolve(None, canonical), exact)

    def test_namespaced_model_selects_exact_publisher_route_over_other_aliases(self):
        canonical = 'zhipuai/glm-5.3-flash'
        base = catalog_price('zhipuai', 'glm-5.3-flash', canonical, input='0.11952', output='0.41831')
        premium = catalog_price('zhipuai', 'glm-5.3-flashx', canonical, input='0.29879', output='1.04577')
        hosted = catalog_price('host', canonical, canonical, input='99')
        index = OfficialModelPriceIndex([premium, hosted, base])
        self.assertIs(index.resolve('zhipuai', canonical), base)
        self.assertIs(index.resolve('host', canonical), base)
        self.assertIs(index.resolve('zhipuai', 'glm-5.3-flashx'), premium)


class OfficialModelPriceCatalogTests(TestCase):
    @override_settings(AIVISION_MODEL_PRICES=[])
    def test_catalog_carries_model_identity_without_changing_exact_host_price(self):
        user = User.objects.create(name='Owner', email='owner@example.invalid', normalized_email='owner@example.invalid')
        space = Space.objects.create(name='Official price sample', type=2, creator=user)
        canonical = 'deepseek/deepseek-v4-pro-0813'
        snapshot = model_pricing.store_catalog({
            'deepseek': {'models': {'deepseek-v4-pro': {
                'canonical_model_id': canonical, 'cost': {'input': '0.66', 'output': '1.98'},
            }}},
            'baidu': {'models': {'deepseek-v4-pro-0813': {
                'canonical_model_id': canonical, 'cost': {'input': '1.32', 'output': '3.96'},
            }}},
        })
        prices = model_pricing.get_space_model_prices(space)
        direct, = prices.candidates('deepseek', 'deepseek-v4-pro')
        hosted, = prices.candidates('baidu', 'deepseek-v4-pro-0813')
        self.assertEqual(direct['canonicalModelId'], canonical)
        self.assertEqual(direct['catalogSnapshot'], snapshot.snapshot_id)
        self.assertEqual(hosted['input'], '1.32')
        self.assertIs(OfficialModelPriceIndex(prices).resolve('baidu', 'deepseek-v4-pro-0813'), direct)
