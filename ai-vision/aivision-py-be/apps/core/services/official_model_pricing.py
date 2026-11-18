"""Resolve the model publisher's exact catalog row for subscription estimates."""
from __future__ import annotations

from collections import defaultdict

CATALOG_PRICE_PRIORITY = 200
PUBLISHER_PRICE_SOURCES = ("official_publisher", "archived_publisher")

# Some direct models have bare canonical IDs. Restrict bare identities to
# publisher catalogs with known first-party API provider IDs. Region-specific
# routes and third-party hosts resolve through the upstream canonical model.
_PUBLISHERS = frozenset({
    'alibaba', 'anthropic', 'cohere', 'deepseek', 'google', 'minimax', 'mistral',
    'moonshotai', 'openai', 'perplexity', 'xai', 'zhipuai',
})

# DeepSeek confirmed that its V4.1 Flash 1017 upgrade kept the same tariff.
# https://api-docs.deepseek.com/updates/#deepseek-v41-flash-1017-update
_DEEPSEEK_V41_FLASH_MODEL = 'deepseek-v4.1-flash'
_DEEPSEEK_V41_FLASH = f'deepseek/{_DEEPSEEK_V41_FLASH_MODEL}'
_DEEPSEEK_V41_FLASH_1017 = 'deepseek/deepseek-v4.1-flash-1017'
_BAIDU_PUBLISHER_MODELS = frozenset({"ernie-5.5-flash-preview", "ernie-light-dt-1.0"})


def _is_catalog_row(row):
    return (
        isinstance(row, dict)
        and row.get('pricingSource') == 'catalog'
        and row.get('valuationBasis') == 'current_catalog'
        and row.get('pricingPriority') == CATALOG_PRICE_PRIORITY
        and all(isinstance(row.get(key), str) and row[key] for key in (
            'provider', 'model', 'canonicalModelId', 'catalogSnapshot',
        ))
        and row['provider'] == row.get('catalogProvider')
        and row['model'] == row.get('catalogModel')
    )


def _publisher_model(row):
    provider = row['provider']
    canonical = row['canonicalModelId']
    if provider == "baidu" and row["model"] in _BAIDU_PUBLISHER_MODELS:
        if canonical in (row["model"], f'baidu/{row["model"]}'):
            return f'baidu/{row["model"]}'
    owner, separator, model = canonical.partition('/')
    if separator:
        # A namespaced canonical model identifies the owning publisher when
        # its tariff is under that publisher. The bare-model fallback can
        # itself contain slashes, so do not use it as ownership evidence.
        if owner == provider and model and (canonical != row['model'] or provider in _PUBLISHERS):
            return canonical
    elif provider in _PUBLISHERS:
        return f'{provider}/{canonical}'
    return None


def _rate_terms(row):
    return tuple(row.get(field) for field in (
        'input', 'output', 'cacheReadInput', 'cacheWriteInput', 'reasoning',
        'currency', 'unitTokens', 'unsupportedReason', 'catalogSnapshot',
    ))


def _base_rates_missing(row):
    return (row.get('unsupportedReason') in ('missing_prices', 'missing_input_or_output_price')
            and (row.get('input') is None or row.get('output') is None))


class OfficialModelPriceIndex:
    """Index exact snapshot identities independently of Space pricing rules."""

    def __init__(self, prices):
        self._by_host = defaultdict(list)
        self._by_model = defaultdict(set)
        self._by_publisher_model = defaultdict(list)
        self._supplements = {}
        for row in prices:
            if (isinstance(row, dict)
                    and row.get("pricingSource") in PUBLISHER_PRICE_SOURCES
                    and row.get("pricingPriority") == CATALOG_PRICE_PRIORITY
                    and isinstance(row.get("publisherTariff"), dict)
                    and isinstance(row.get("provider"), str)
                    and isinstance(row.get("model"), str)):
                self._supplements[row["provider"], row["model"]] = row
            if not _is_catalog_row(row):
                continue
            self._by_host[row['provider'], row['model']].append(row)
            canonical = _publisher_model(row)
            if canonical is not None:
                self._by_publisher_model[canonical].append(row)
                for identity in (canonical, canonical.partition('/')[2], row['model']):
                    self._by_model[identity].add(canonical)

    def supplement(self, provider, model, *, selected=None):
        if selected is not None:
            if not _base_rates_missing(selected):
                return None
            canonical = _publisher_model(selected)
            if canonical is None:
                return None
            publisher, _, name = canonical.partition('/')
            return self._supplements.get((publisher, name))
        if not isinstance(model, str):
            return None
        observed = self._by_host.get((provider, model), ()) if isinstance(provider, str) else ()
        if len(observed) > 1:
            return None
        if observed:
            canonical = observed[0]["canonicalModelId"]
            # A hosted route's explicit identity takes precedence over its
            # name, including provider-prefixed routes that have been reused.
            if canonical != model or "/" in canonical:
                if "/" not in canonical:
                    return None
                model = canonical
        if "/" in model:
            publisher, _, name = model.partition("/")
        else:
            publisher, name = provider, model
            if provider == "baidu" and model == "deepseek-v4-flash-0731":
                publisher = "deepseek"
        if (not isinstance(publisher, str) or f'{publisher}/{name}' in self._by_publisher_model
                or len(self._by_model.get(model, ())) > 1):
            return None
        return self._supplements.get((publisher, name))

    def resolve(self, provider, model):
        if not isinstance(model, str) or not model:
            return None
        price_equivalent = model == _DEEPSEEK_V41_FLASH or (
            model == _DEEPSEEK_V41_FLASH_MODEL and provider in ('baidu', 'deepseek')
        )
        observed = self._by_host.get((provider, model), ()) if isinstance(provider, str) else ()
        if len(observed) > 1:
            return None
        if observed:
            row = observed[0]
            publisher_model = _publisher_model(row)
            if publisher_model is not None:
                return self._price_equivalent_tariff(publisher_model, row)
            canonical = row['canonicalModelId']
            if canonical == _DEEPSEEK_V41_FLASH or (
                price_equivalent and canonical == _DEEPSEEK_V41_FLASH_MODEL
            ):
                canonical = _DEEPSEEK_V41_FLASH
                if canonical not in self._by_publisher_model:
                    canonical = _DEEPSEEK_V41_FLASH_1017
                return self._resolve_publisher_model(canonical, canonical)
            if '/' in canonical:
                return self._resolve_publisher_model(canonical, model)
            # A hosted alias can carry a bare canonical identity. Its route
            # name may also belong to a different publisher.
            model = canonical

        canonical_ids = self._by_model.get(model, ())
        if len(canonical_ids) == 1:
            return self._resolve_publisher_model(next(iter(canonical_ids)), model)
        if not canonical_ids and not observed and price_equivalent:
            return self._resolve_publisher_model(_DEEPSEEK_V41_FLASH_1017, _DEEPSEEK_V41_FLASH_1017)
        return None

    def _resolve_publisher_model(self, canonical, model):
        return self._price_equivalent_tariff(canonical, self._select_publisher_tariff(canonical, model))

    def _price_equivalent_tariff(self, canonical, row):
        if canonical == _DEEPSEEK_V41_FLASH and row is not None and _base_rates_missing(row):
            return self._select_publisher_tariff(_DEEPSEEK_V41_FLASH_1017, _DEEPSEEK_V41_FLASH_1017) or row
        return row

    def _select_publisher_tariff(self, canonical, model):
        tariffs = self._by_publisher_model.get(canonical, ())
        if len(tariffs) <= 1:
            return tariffs[0] if tariffs else None
        # Different publisher route names can share the same base model. An
        # exact route name can select one. Identically priced aliases can also
        # supply a stable source row for the base model.
        exact = [row for row in tariffs if row['model'] == model]
        if not exact and model == canonical:
            # The namespaced identity can name the publisher's bare base route.
            bare_model = canonical.partition('/')[2]
            exact = [row for row in tariffs if row['model'] == bare_model]
        if len(exact) == 1:
            return exact[0]
        terms = _rate_terms(tariffs[0])
        if not exact and all(_rate_terms(row) == terms for row in tariffs[1:]):
            return min(tariffs, key=lambda row: row['model'])
        return None
