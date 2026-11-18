"""Conservative USD/text-token estimates from a public catalog and Space rules.

A models.dev v1 projection is not a provider invoice: some upstream conditions
are absent from that endpoint. Explicit unsupported conditions are never priced.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
import zlib
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path

import requests as http_requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core.models import Space, Agent
from apps.core.trace_query.routing import resolve_signoz_runtime_config
from apps.core.trace_query.types import BaseTraceQuery, TraceQueryServiceError
from apps.core.models.model_pricing import ModelPricingCatalogSnapshot, ModelPricingCatalogState, ModelPricingRule
from apps.core.services.official_model_pricing import CATALOG_PRICE_PRIORITY
from apps.core.services.publisher_subscription_prices import subscription_price_supplements

CATALOG_URL = 'https://models.dev/api.json'
REPOSITORY_URL = 'https://github.com/anomalyco/models.dev'
REPOSITORY_ARCHIVE_URL = 'https://codeload.github.com/anomalyco/models.dev/tar.gz/'
MAX_CATALOG_BYTES = 20 * 1024 * 1024
BUNDLED_CATALOG_PATH = Path(__file__).resolve().parents[1] / 'data' / 'models-dev-api.zip'
# Repository prices can contain 19-place cache rates. Preserve their decimal
# text exactly rather than rounding a whole import to a binary float.
MAX_RATE_DECIMAL_PLACES = 24
RATE_KEYS = ('input', 'output', 'cacheReadInput', 'cacheWriteInput', 'reasoning')
V1_RATES = {'input': 'input', 'output': 'output', 'cache_read': 'cacheReadInput', 'cache_write': 'cacheWriteInput', 'reasoning': 'reasoning'}
PRIORITY = {'subscription': 500, 'manual': 400, 'alias': 300, 'catalog': CATALOG_PRICE_PRIORITY, 'reference': 100}
WARNINGS = [
    'Canonical text-token estimates use USD; subscription equivalents and display conversions are separate from provider invoices.',
    'models.dev v1 may omit context tiers, service/region multipliers and cache-write TTL conditions; verify provider tariffs.',
    'Catalog retrieval time is not a historical effective price. Automatic matches use current-catalog valuation.',
    'Repository imports retain rich conditions; unsupported token units, cache TTLs and conditional tariffs remain unknown.',
]


class PricingError(ValueError):
    pass


def _identity(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum or any(ord(c) < 32 for c in value):
        raise PricingError(f'Invalid {name}')
    return value.strip()


def _rate(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise PricingError('Prices must be finite non-negative decimal numbers')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise PricingError('Prices must be finite non-negative decimal numbers') from exc
    if not result.is_finite() or result < 0 or result > Decimal('1000000000') or result.as_tuple().exponent < -MAX_RATE_DECIMAL_PLACES:
        raise PricingError(f'Prices must be finite non-negative decimal numbers with at most {MAX_RATE_DECIMAL_PLACES} decimal places')
    return format(result, 'f')


def _normalize_cost(cost):
    rates = {key: None for key in RATE_KEYS}
    reasons = []
    if cost is None:
        return rates, ['missing_prices']
    if not isinstance(cost, dict):
        raise PricingError('Invalid catalog cost object')
    if 'text' in cost:
        # Accept only the unconditional scalar subset of rich schema v2.
        unknown = sorted(set(cost) - {'text'})
        if unknown:
            reasons.append('unsupported_cost_fields: ' + ', '.join(unknown))
        text = cost['text']
        if not isinstance(text, dict):
            raise PricingError('Invalid catalog text cost')
        if set(text) - {'input', 'output'}:
            reasons.append('unsupported_text_cost_fields')
        for direction in ('input', 'output'):
            part = text.get(direction)
            if part is None:
                continue
            if not isinstance(part, dict):
                raise PricingError('Invalid rich catalog rate')
            rates[direction] = _rate(part.get('price'))
            if part.get('unit', 'token') != 'token':
                reasons.append('unsupported_' + direction + '_unit')
            allowed = {'price', 'unit', 'cache'} if direction == 'input' else {'price', 'unit', 'reasoning'}
            if set(part) - allowed:
                reasons.append('unsupported_' + direction + '_conditions: ' + ', '.join(sorted(set(part) - allowed)))
            if direction == 'output':
                rates['reasoning'] = _rate(part.get('reasoning'))
            elif part.get('cache') is not None:
                cache = part['cache']
                if not isinstance(cache, dict):
                    raise PricingError('Invalid catalog cache price')
                if set(cache) - {'read', 'write'}:
                    reasons.append('unsupported_cache_conditions')
                rates['cacheReadInput'] = _rate(cache.get('read'))
                write = cache.get('write')
                if isinstance(write, dict):
                    reasons.append('unsupported_cache_write_ttl')
                    for value in write.values():
                        _rate(value)
                else:
                    rates['cacheWriteInput'] = _rate(write)
    else:
        for source, target in V1_RATES.items():
            rates[target] = _rate(cost.get(source))
        unknown = sorted(set(cost) - set(V1_RATES))
        if unknown:
            reasons.append('unsupported_cost_fields: ' + ', '.join(unknown))
    if rates['input'] is None or rates['output'] is None:
        reasons.append('missing_input_or_output_price')
    return rates, reasons


def normalize_catalog(payload):
    if not isinstance(payload, dict) or not payload:
        raise PricingError('Catalog must be a non-empty provider map')
    rows = []
    for provider_key, provider in payload.items():
        provider_id = _identity(provider_key, 'catalog provider', 128)
        if not isinstance(provider, dict) or not isinstance(provider.get('models'), dict):
            raise PricingError('Invalid catalog provider models')
        for model_key, model in provider['models'].items():
            model_id = _identity(model_key, 'catalog model', 256)
            if not isinstance(model, dict):
                raise PricingError('Invalid catalog model')
            rates, reasons = _normalize_cost(model.get('cost'))
            experimental = model.get('experimental', {})
            if not isinstance(experimental, dict) or not isinstance(experimental.get('modes', {}), dict):
                raise PricingError('Invalid catalog mode definitions')
            modes = experimental.get('modes', {})
            if any(not isinstance(mode, dict) for mode in modes.values()):
                raise PricingError('Invalid catalog mode definition')
            if any('cost' in mode for mode in modes.values()):
                reasons.append('unsupported_mode_pricing')
            rows.append({'provider': provider_id, 'model': model_id,
                         'name': str(model.get('name') or model_id)[:256],
                         'canonicalModelId': str(model.get('canonical_model_id') or model.get('canonicalModelId') or model_id)[:512],
                         'rates': rates, 'supported': not reasons, 'unsupportedReasons': reasons})
            if len(rows) > 100000:
                raise PricingError('Catalog model limit exceeded')
    if not rows:
        raise PricingError('Catalog contains no models')
    return sorted(rows, key=lambda row: (row['provider'], row['model']))


@transaction.atomic
def store_catalog(payload, *, provenance=None, if_missing=False):
    rows = normalize_catalog(payload)
    metadata = {'sourceType': 'api', 'sourceUrl': CATALOG_URL, 'sourceRevision': ''}
    if provenance is not None:
        if not isinstance(provenance, dict) or set(provenance) - set(metadata):
            raise PricingError('Invalid catalog provenance')
        metadata.update(provenance)
    kind, revision, url = metadata['sourceType'], metadata['sourceRevision'], metadata['sourceUrl']
    if kind not in {'api', 'repository', 'local_repository', 'file', 'bundled'}:
        raise PricingError('Invalid catalog source')
    if not isinstance(revision, str) or len(revision) > 80:
        raise PricingError('Invalid catalog source revision')
    if kind == 'repository' and (not re.fullmatch(r'[0-9a-f]{40}', revision) or url != f'{REPOSITORY_URL}/tree/{revision}'):
        raise PricingError('Invalid repository provenance')
    if kind in {'local_repository', 'file', 'bundled'}:
        if (kind == 'bundled' or revision) and not re.fullmatch(r'sha256:[0-9a-f]{64}', revision):
            raise PricingError('Invalid local content revision')
        metadata['sourceUrl'] = None
    elif kind == 'api' and (url != CATALOG_URL or revision):
        raise PricingError('Invalid API provenance')
    # Derived eligibility can change when the parser learns a new tariff
    # condition. Include normalized rows so an unchanged upstream commit never
    # revives a snapshot built with older, less conservative interpretation.
    digest_builder = hashlib.sha256(b'pricing-catalog-v2\0')
    for material in (payload, metadata, rows):
        digest_builder.update(json.dumps(material, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
        digest_builder.update(b'\0')
    digest = digest_builder.hexdigest()
    state, _ = ModelPricingCatalogState.objects.select_for_update().get_or_create(key='models.dev')
    # Startup may have overlapped a successful manual or offline import.
    if if_missing and state.snapshot_id:
        return None
    snapshot, _ = ModelPricingCatalogSnapshot.objects.get_or_create(snapshot_id=digest, defaults={
        'models': rows, 'source_type': kind, 'source_url': metadata['sourceUrl'] or '', 'source_revision': revision,
    })
    state.snapshot = snapshot
    state.fetched_at = state.last_attempt_at = timezone.now()
    state.last_error = ''
    state.save()
    return snapshot


def parse_catalog_json(body):
    """Decode both network and offline catalog numbers without float rounding."""
    def reject_constant(_):
        raise ValueError

    try:
        payload = json.loads(body.decode('utf-8'), parse_float=Decimal, parse_constant=reject_constant)
        # JSONField and snapshot hashes receive decimal strings, never floats.
        return json.loads(json.dumps(payload, default=str))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PricingError('Catalog response is not valid JSON') from exc


def _download_catalog(url):
    response = None
    try:
        response = http_requests.get(url, timeout=(5, 20), allow_redirects=False, stream=True, headers={
            'Accept': 'application/json' if url == CATALOG_URL else 'application/gzip',
            'User-Agent': 'AI-Vision-model-pricing',
        })
        if response.status_code != 200:
            raise PricingError(f'Catalog fetch failed (HTTP {response.status_code})')
        declared = response.headers.get('Content-Length')
        if declared and (not str(declared).isdigit() or int(declared) > MAX_CATALOG_BYTES):
            raise PricingError('Catalog exceeds the size limit')
        body = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            body.extend(chunk)
            if len(body) > MAX_CATALOG_BYTES:
                raise PricingError('Catalog exceeds the size limit')
        return bytes(body)
    except http_requests.RequestException as exc:
        raise PricingError('Catalog request failed; retry later') from exc
    finally:
        if response is not None:
            response.close()


def validate_sync_options(source='auto', revision=None):
    if not isinstance(source, str) or source not in {'auto', 'api', 'repository'}:
        raise PricingError('Catalog source must be auto, api or repository')
    if revision is not None and (source == 'api' or not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision)):
        raise PricingError('A repository revision must be a full lowercase commit SHA')


@transaction.atomic
def _record_sync_failure(message, *, if_missing):
    state, _ = ModelPricingCatalogState.objects.select_for_update().get_or_create(key='models.dev')
    if if_missing and state.snapshot_id:
        return False
    state.last_attempt_at = timezone.now()
    state.last_error = message[:256]
    state.save(update_fields=['last_attempt_at', 'last_error'])
    return True


def sync_catalog(*, source='auto', revision=None, if_missing=False):
    validate_sync_options(source, revision)
    if if_missing and _catalog_exists():
        return None
    failures = []
    if source in {'auto', 'api'} and revision is None:
        try:
            return store_catalog(parse_catalog_json(_download_catalog(CATALOG_URL)), if_missing=if_missing)
        except PricingError as exc:
            failures.append(f'API: {exc}')
    if source in {'auto', 'repository'}:
        from .model_pricing_repository import catalog_from_archive, RepositoryCatalogError
        try:
            archive = _download_catalog(REPOSITORY_ARCHIVE_URL + (revision or 'refs/heads/dev'))
            payload, provenance = catalog_from_archive(archive, expected_revision=revision)
            return store_catalog(payload, provenance=provenance, if_missing=if_missing)
        except (PricingError, RepositoryCatalogError) as exc:
            failures.append(f'Repository: {exc}')
    message = '; '.join(failures) or 'Catalog synchronization failed'
    if _record_sync_failure(message, if_missing=if_missing):
        raise PricingError(message)
    return None


def _catalog_exists():
    return ModelPricingCatalogState.objects.filter(key='models.dev', snapshot__isnull=False).exists()


def import_bundled_catalog(*, if_missing=False):
    if if_missing and _catalog_exists():
        return None
    try:
        try:
            with BUNDLED_CATALOG_PATH.open('rb') as bundle:
                compressed = bundle.read(MAX_CATALOG_BYTES + 1)
            if len(compressed) > MAX_CATALOG_BYTES:
                raise PricingError('Bundled catalog exceeds the compressed size limit')
            with zipfile.ZipFile(BytesIO(compressed)) as archive:
                members = archive.infolist()
                if len(members) != 1:
                    raise PricingError('Bundled catalog must contain one api.json file')
                member = members[0]
                file_type = stat.S_IFMT(member.external_attr >> 16)
                if (member.orig_filename != 'api.json' or member.is_dir()
                        or file_type not in (0, stat.S_IFREG)):
                    raise PricingError('Bundled catalog must contain one api.json file')
                if member.file_size > MAX_CATALOG_BYTES:
                    raise PricingError('Bundled catalog exceeds the decoded size limit')
                with archive.open(member) as payload:
                    body = payload.read(MAX_CATALOG_BYTES + 1)
            if len(body) > MAX_CATALOG_BYTES:
                raise PricingError('Bundled catalog exceeds the decoded size limit')
        except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zlib.error) as exc:
            raise PricingError('Bundled models.dev catalog is unavailable or invalid') from exc
        return store_catalog(parse_catalog_json(body), provenance={
            'sourceType': 'bundled', 'sourceUrl': None,
            'sourceRevision': 'sha256:' + hashlib.sha256(body).hexdigest(),
        }, if_missing=if_missing)
    except PricingError as exc:
        if _record_sync_failure(str(exc), if_missing=if_missing):
            raise
        return None


def import_repository(path):
    from .model_pricing_repository import catalog_from_directory, RepositoryCatalogError
    try:
        payload, provenance = catalog_from_directory(path)
        return store_catalog(payload, provenance=provenance)
    except RepositoryCatalogError as exc:
        raise PricingError(str(exc)) from exc


def catalog_status():
    state = ModelPricingCatalogState.objects.select_related('snapshot').filter(key='models.dev').first()
    snapshot = state.snapshot if state else None
    return {'snapshotId': snapshot.snapshot_id if snapshot else None,
            'fetchedAt': state.fetched_at.isoformat() if state and state.fetched_at else None,
            'modelCount': len(snapshot.models) if snapshot else 0,
            'status': 'error' if state and state.last_error else ('ready' if snapshot else 'empty'),
            'lastError': state.last_error if state else None, 'scope': 'global', 'warnings': list(WARNINGS),
            'sourceType': snapshot.source_type if snapshot else None,
            'sourceUrl': snapshot.source_url or None if snapshot else None,
            'sourceRevision': snapshot.source_revision or None if snapshot else None}


def _current_snapshot():
    state = ModelPricingCatalogState.objects.select_related('snapshot').filter(key='models.dev').first()
    return state.snapshot if state else None


def search_catalog(*, provider='', model='', q='', reference=False):
    snapshot = _current_snapshot()
    if not snapshot:
        return []
    provider, model, q = provider.strip(), model.strip(), q.strip().casefold()
    matches = []
    for row in snapshot.models:
        same_provider = row['provider'].casefold() == provider.casefold()
        if provider and not same_provider and not reference:
            continue
        if q and q not in (row['provider'] + ' ' + row['model'] + ' ' + row['name']).casefold():
            continue
        exact = row['model'] == model
        score = (100 if exact else round(SequenceMatcher(None, model.casefold(), row['model'].casefold()).ratio() * 80)) if model else 0
        reasons = (['provider_match'] if same_provider else ['reference_only']) + (['exact_model'] if exact else ['candidate_requires_confirmation'])
        matches.append({**row, 'score': score, 'reasons': reasons, 'catalogSnapshot': snapshot.snapshot_id})
    return sorted(matches, key=lambda row: (-row['score'], row['provider'], row['model']))[:100]


def serialize_rule(rule):
    return {'uuid': rule.uuid, 'provider': rule.provider, 'model': rule.model, 'kind': rule.kind,
            'rates': rule.rates, 'estimateListPrice': rule.estimate_list_price,
            'effectiveFrom': rule.effective_from.isoformat(), 'version': rule.uuid,
            'catalogProvider': rule.catalog_provider or None, 'catalogModel': rule.catalog_model or None,
            'catalogSnapshot': rule.catalog_snapshot or None, 'active': rule.gmt_deleted is None,
            'createdAt': rule.gmt_create.isoformat()}


@transaction.atomic
def create_rule(*, space, user, data):
    from apps.core.permissions.constants import MODEL_PRICING_MANAGE
    from apps.core.permissions.space import require_space_permission
    space = Space.objects.select_for_update().get(pk=space.pk)
    require_space_permission(user=user, space_id=space.uuid, permission=MODEL_PRICING_MANAGE, lock=True)
    if not isinstance(data, dict):
        raise PricingError('Rule must be an object')
    allowed = {'provider', 'model', 'kind', 'rates', 'effectiveFrom', 'catalogProvider', 'catalogModel', 'catalogSnapshot', 'estimateListPrice'}
    if set(data) - allowed:
        raise PricingError('Unknown pricing rule fields')
    provider = _identity(data.get('provider'), 'provider', 128)
    model = _identity(data.get('model'), 'model', 256)
    kind = data.get('kind')
    if kind not in ('manual', 'alias', 'reference', 'subscription'):
        raise PricingError('Invalid pricing rule kind')
    estimate_list_price = data.get('estimateListPrice', False)
    if type(estimate_list_price) is not bool or (estimate_list_price and kind != 'subscription'):
        raise PricingError('estimateListPrice must be boolean and enabled only for subscription markers')
    try:
        effective = parse_datetime(data.get('effectiveFrom') or '')
    except (TypeError, ValueError):
        effective = None
    if effective is None or timezone.is_naive(effective):
        raise PricingError('effectiveFrom must be an explicit ISO timestamp with timezone')
    if ModelPricingRule.objects.filter(space=space, provider=provider, model=model, kind=kind,
            effective_from=effective, gmt_deleted__isnull=True).exists():
        raise PricingError('Deactivate the existing rule for this model, kind and effective time before replacing it')
    catalog_provider = catalog_model = catalog_snapshot = ''
    if kind == 'subscription':
        if 'rates' in data and data['rates'] != {}:
            raise PricingError('Subscription markers cannot define token rates')
        if any(data.get(key) for key in ('catalogProvider', 'catalogModel', 'catalogSnapshot')):
            raise PricingError('Subscription markers cannot select a catalog model')
        rates = {}
    elif kind == 'manual':
        material = data.get('rates')
        if not isinstance(material, dict) or set(material) - set(RATE_KEYS):
            raise PricingError('Unsupported rate fields')
        rates = {key: _rate(material.get(key)) for key in RATE_KEYS}
        if rates['input'] is None or rates['output'] is None:
            raise PricingError('Manual input and output rates are required; use explicit zero for free tokens')
        if any(data.get(key) for key in ('catalogProvider', 'catalogModel', 'catalogSnapshot')):
            raise PricingError('Manual rules cannot select a catalog model')
    else:
        catalog_provider = _identity(data.get('catalogProvider'), 'catalog provider', 128)
        catalog_model = _identity(data.get('catalogModel'), 'catalog model', 256)
        if kind == 'alias' and provider.casefold() != catalog_provider.casefold():
            raise PricingError('Aliases require the same billing provider; explicitly use reference for another host')
        snapshot = _current_snapshot()
        if not snapshot or (data.get('catalogSnapshot') and data['catalogSnapshot'] != snapshot.snapshot_id):
            raise PricingError('Catalog changed or is unavailable; refresh candidates before saving')
        candidate = next((row for row in snapshot.models if row['provider'] == catalog_provider and row['model'] == catalog_model), None)
        if candidate is None or not candidate['supported']:
            raise PricingError('Catalog model is missing or uses unsupported pricing conditions')
        rates = candidate['rates']
        catalog_snapshot = snapshot.snapshot_id
    return ModelPricingRule.objects.create(space=space, creator=user, provider=provider, model=model, kind=kind,
        effective_from=effective, rates=rates, estimate_list_price=estimate_list_price,
        catalog_provider=catalog_provider, catalog_model=catalog_model,
        catalog_snapshot=catalog_snapshot)


def get_space_model_prices(space):
    from apps.core.trace_query.semantics.usage import IndexedModelPrices
    legacy = getattr(settings, 'AIVISION_MODEL_PRICES', [])
    if legacy is None:
        legacy = []
    if not isinstance(legacy, (list, tuple)) or any(not isinstance(row, dict) for row in legacy):
        # Keep explicit malformed configuration unknown; never silently drop it
        # and let an automatic catalog price replace the user's configuration.
        # Keep loading Space billing markers: invalid token prices must not
        # turn subscription SDK list-price telemetry into a billed amount.
        prices = [None]
    else:
        prices = [{**row, 'pricingPriority': 350} for row in legacy]
    if not isinstance(space, Space) or not space.pk or space._state.adding:
        return IndexedModelPrices(prices)
    snapshot = _current_snapshot()
    if snapshot:
        for row in snapshot.models:
            entry = {**row['rates'], 'provider': row['provider'], 'model': row['model'], 'currency': 'USD',
                     'unitTokens': 1000000, 'version': snapshot.snapshot_id, 'pricingSource': 'catalog',
                     'pricingPriority': PRIORITY['catalog'], 'valuationBasis': 'current_catalog',
                     'canonicalModelId': row.get('canonicalModelId') or row['model'],
                     'catalogProvider': row['provider'], 'catalogModel': row['model'], 'catalogSnapshot': snapshot.snapshot_id}
            if row['unsupportedReasons']:
                entry['unsupportedReason'] = '; '.join(row['unsupportedReasons'])
            prices.append(entry)
    for rule in ModelPricingRule.objects.filter(space=space, gmt_deleted__isnull=True).order_by('effective_from', 'gmt_create'):
        prices.append({**rule.rates, 'provider': rule.provider, 'model': rule.model, 'currency': 'USD',
                       'unitTokens': 1000000, 'version': rule.uuid, 'effectiveFrom': rule.effective_from.isoformat(),
                       'pricingSource': rule.kind, 'pricingPriority': PRIORITY[rule.kind], 'valuationBasis': 'effective_price',
                       'estimateListPrice': rule.estimate_list_price,
                       'catalogProvider': rule.catalog_provider or None, 'catalogModel': rule.catalog_model or None,
                       'catalogSnapshot': rule.catalog_snapshot or None})
    prices.extend(subscription_price_supplements())
    return IndexedModelPrices(prices)


def native_inventory(*, space, user, params):
    """Only complete native windows may establish observed model coverage."""
    from collections import defaultdict
    from apps.core.services.onboarding_demo import reserved_demo_agent_uuid
    from apps.core.trace_query.signoz.provider import SignozTraceProvider
    from apps.core.trace_query.semantics.usage import aggregate_usage
    from apps.core.trace_query.semantics.observations import timestamp_ns
    allowed = {'from', 'to', 'agent_product', 'agent_uuid'}
    if set(params) - allowed:
        raise PricingError('Unknown inventory query parameter; only native SigNoz is supported')
    frozen = {}
    for key in ('from', 'to'):
        value = params.get(key)
        try:
            if isinstance(value, str) and value.isdigit():
                boundary = int(value)
            else:
                timestamp = parse_datetime(value or '')
                if timestamp is None or timezone.is_naive(timestamp):
                    raise ValueError
                boundary = int(timestamp.timestamp() * 1000)
        except (ValueError, TypeError, OverflowError):
            raise PricingError('Inventory requires explicit from/to timestamps with timezone') from None
        frozen[key] = boundary
    if frozen['from'] >= frozen['to'] or frozen['to'] - frozen['from'] > 31 * 86400000:
        raise PricingError('Inventory window must be positive and at most 31 days')
    for key in ('agent_product', 'agent_uuid'):
        if params.get(key):
            frozen[key] = _identity(params[key], key, 128)
    if frozen.get('agent_uuid'):
        agent = Agent.objects.filter(space=space, uuid=frozen['agent_uuid'], gmt_deleted__isnull=True).first()
        if agent is None:
            raise InventoryScopeNotFound('Agent not found in this Space')
        if frozen.get('agent_product') and frozen['agent_product'] != agent.agent_product:
            raise PricingError('Agent and product scope do not match')
        frozen['agent_product'] = agent.agent_product
    query = BaseTraceQuery(space, user, frozen)
    try:
        resolve_signoz_runtime_config(query)
    except TraceQueryServiceError as exc:
        raise PricingError('Native SigNoz inventory is not configured; external Langfuse inventory is unsupported') from exc
    # Reuse the bounded metrics snapshot, including canonical per-observation
    # pricing and ancestry, without hydrating every Trace's messages. The
    # provider retains its verified raw fallback for older data planes.
    window, meta = SignozTraceProvider()._window(query, _metrics=True)
    if (meta.get('complete') is not True or meta.get('from') != frozen['from'] or meta.get('to') != frozen['to']
            or meta.get('totalItems') != len(window.traces)):
        raise TraceQueryServiceError('Inventory requires a verified complete native window; narrow the range and retry', 3035)
    # Space/product pricing inventory describes production usage. Agent-specific
    # inventory may inspect the example, including its illustrative costs.
    demo_uuid = reserved_demo_agent_uuid(space) if window.traces and not frozen.get('agent_uuid') else None
    observations = []
    for row, trace in zip(window.rows, window.traces, strict=True):
        # Compact facts omit resource attributes. Their verified summary scope
        # applies to every Span, since mixed-Agent traces cannot be projected.
        projected_agent = row.get('resource.attributes.aivision.agent.id')
        for observation in trace['observations']:
            if demo_uuid is not None:
                resource = observation.get('resourceAttributes')
                owner = resource.get('aivision.agent.id') if resource is not None else projected_agent
                if owner == demo_uuid:
                    continue
            observations.append(observation)
    selected = [observation for observation in observations
                if (stamp := timestamp_ns(observation.get('startTime'))) is not None
                and frozen['from'] * 1000000 <= stamp < frozen['to'] * 1000000]
    groups = defaultdict(list)
    for observation in selected:
        if observation.get('type') in {'GENERATION', 'EMBEDDING'}:
            groups[(observation.get('provider'), observation.get('model'))].append(observation)
    models = []
    for (provider, model), calls in sorted(groups.items(), key=lambda row: (row[0][0] or '', row[0][1] or '')):
        usage = aggregate_usage(calls, context=observations)
        coverage = usage['costCoverage']
        models.append({'provider': provider, 'model': model, 'calls': coverage['billableCalls'], 'costCoverage': coverage})
    return {'from': frozen['from'], 'to': frozen['to'], 'complete': True, 'provider': 'signoz',
            'models': models, 'costCoverage': aggregate_usage(selected, context=observations)['costCoverage'],
            'scope': {'agentProduct': frozen.get('agent_product'), 'agentUuid': frozen.get('agent_uuid')},
            'warnings': list(WARNINGS)}


class InventoryScopeNotFound(PricingError):
    pass
