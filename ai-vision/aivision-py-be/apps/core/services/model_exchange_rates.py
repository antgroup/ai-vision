"""ECB reference FX for optional display; never provider billing or settlement."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from xml.etree import ElementTree

import requests as http_requests
from django.db import transaction
from django.utils import timezone

from apps.core.models.model_exchange_rates import ModelExchangeRateSnapshot, ModelExchangeRateState

ECB_XML_URL = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
ECB_INFO_URL = 'https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html'
MAX_RATE_BYTES = 64 * 1024
CURRENCIES = ('USD', 'CNY', 'JPY')
_CUBE = '{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}Cube'


class ExchangeRateError(ValueError):
    pass


def parse_ecb_rates(body):
    """Validate one dated EUR rate set, then derive USD cross rates in Decimal.

    Cross rates round to 24 fractional digits; the original published EUR rates
    stay in each immutable snapshot so conversion evidence is reproducible.
    """
    if not isinstance(body, bytes) or len(body) > MAX_RATE_BYTES:
        raise ExchangeRateError('Invalid ECB reference-rate document')
    try:
        document = body.decode('utf-8-sig')
        if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', document, re.I):
            raise ValueError
        root = ElementTree.fromstring(document)
        dated = [node for node in root.iter(_CUBE) if 'time' in node.attrib]
        if len(dated) != 1:
            raise ValueError
        rate_date = date.fromisoformat(dated[0].attrib['time'])
        if rate_date < date(1999, 1, 1) or rate_date > timezone.now().date() + timedelta(days=1):
            raise ValueError
        source_rates = {}
        for node in dated[0]:
            currency = node.attrib.get('currency')
            if currency not in CURRENCIES:
                continue
            raw = node.attrib.get('rate', '')
            if currency in source_rates or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,12})?', raw):
                raise ValueError
            value = Decimal(raw)
            if not value.is_finite() or not Decimal('0') < value <= Decimal('1000000'):
                raise ValueError
            source_rates[currency] = format(value, 'f')
        if set(source_rates) != set(CURRENCIES):
            raise ValueError
        with localcontext() as context:
            context.prec = 50
            usd = Decimal(source_rates['USD'])
            rates = {currency: format((Decimal(source_rates[currency]) / usd).quantize(Decimal('1e-24')).normalize(), 'f')
                     for currency in CURRENCIES}
        return {'rateDate': rate_date, 'sourceRates': source_rates, 'rates': rates}
    except (ElementTree.ParseError, UnicodeError, ValueError, KeyError, InvalidOperation) as exc:
        raise ExchangeRateError('Invalid ECB reference-rate document') from exc


@transaction.atomic
def store_exchange_rates(body):
    material = parse_ecb_rates(body)
    state, _ = ModelExchangeRateState.objects.get_or_create(key='ecb')
    state = ModelExchangeRateState.objects.select_for_update().select_related('snapshot').get(pk=state.pk)
    if state.snapshot and state.snapshot.rate_date > material['rateDate']:
        raise ExchangeRateError('ECB reference rates are older than the retained snapshot')
    identity = json.dumps({'date': material['rateDate'].isoformat(), 'sourceRates': material['sourceRates'],
                           'source': ECB_XML_URL, 'crossRateVersion': 1}, sort_keys=True, separators=(',', ':'))
    snapshot, _ = ModelExchangeRateSnapshot.objects.get_or_create(
        snapshot_id=hashlib.sha256(identity.encode()).hexdigest(),
        defaults={'rate_date': material['rateDate'], 'source_rates': material['sourceRates'],
                  'rates': material['rates'], 'source_url': ECB_XML_URL})
    now = timezone.now()
    state.snapshot = snapshot
    state.fetched_at = state.last_attempt_at = now
    state.last_error = ''
    state.save(update_fields=['snapshot', 'fetched_at', 'last_attempt_at', 'last_error'])
    return snapshot


def sync_exchange_rates():
    """One fixed HTTPS fetch, bounded body, no redirects or user data."""
    try:
        with http_requests.get(ECB_XML_URL, timeout=(5, 15), stream=True, allow_redirects=False,
                               headers={'User-Agent': 'AI-Vision-model-pricing-fx'}) as response:
            if response.status_code != 200:
                raise ExchangeRateError('ECB reference rates are temporarily unavailable')
            length = response.headers.get('Content-Length')
            if length and (not str(length).isdigit() or int(length) > MAX_RATE_BYTES):
                raise ExchangeRateError('Invalid ECB reference-rate response size')
            chunks, size = [], 0
            for chunk in response.iter_content(8192):
                size += len(chunk)
                if size > MAX_RATE_BYTES:
                    raise ExchangeRateError('Invalid ECB reference-rate response size')
                chunks.append(chunk)
        return store_exchange_rates(b''.join(chunks))
    except (http_requests.RequestException, ExchangeRateError) as exc:
        safe = str(exc) if isinstance(exc, ExchangeRateError) else 'ECB reference rates are temporarily unavailable'
        ModelExchangeRateState.objects.update_or_create(key='ecb', defaults={
            'last_attempt_at': timezone.now(), 'last_error': safe})
        raise ExchangeRateError(safe) from exc


def exchange_rate_status():
    state = ModelExchangeRateState.objects.select_related('snapshot').filter(key='ecb').first()
    snapshot = state.snapshot if state else None
    status = 'empty' if snapshot is None else (
        'stale' if snapshot.rate_date < timezone.now().date() - timedelta(days=7) else 'ready')
    if state and state.last_error:
        status = 'error'
    return {'baseCurrency': 'USD', 'supportedCurrencies': list(CURRENCIES),
            'snapshotId': snapshot.snapshot_id if snapshot else None,
            'rateDate': snapshot.rate_date.isoformat() if snapshot else None,
            'rates': snapshot.rates if snapshot else {'USD': '1'},
            'fetchedAt': state.fetched_at.isoformat() if state and state.fetched_at else None,
            'sourceName': 'ECB', 'sourceUrl': ECB_INFO_URL, 'status': status,
            'lastError': state.last_error if state else None, 'scope': 'global'}
