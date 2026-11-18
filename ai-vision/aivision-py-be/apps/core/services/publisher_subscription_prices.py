"""Publisher rate cards for subscription comparisons missing from models.dev."""
from decimal import Decimal, InvalidOperation, localcontext

from django.db import DatabaseError

from apps.core.models.model_exchange_rates import ModelExchangeRateState
from apps.core.services.official_model_pricing import CATALOG_PRICE_PRIORITY

_BAIDU_PRICE_URL = "https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya"
_DEEPSEEK_ARCHIVE_URL = "https://api-docs.deepseek.com/img/v4_260813_price_en.png"

# Rates are per million text tokens. Use published base rates before promotions
# and peak adjustments, matching the models.dev subscription comparison.
_RATE_CARDS = (
    {
        "provider": "baidu",
        "model": "ernie-light-dt-1.0",
        "currency": "CNY",
        "rates": {"input": "8", "cacheReadInput": "0.8", "output": "28"},
        "rateBasis": "flat",
        "sourceUrl": _BAIDU_PRICE_URL,
        "verifiedOn": "2026-11-13",
    },
    {
        "provider": "baidu",
        "model": "ernie-5.5-flash-preview",
        "currency": "CNY",
        "rates": {"input": "1", "cacheReadInput": "0.1", "output": "4"},
        "rateBasis": "undiscounted_off_peak",
        "sourceUrl": _BAIDU_PRICE_URL,
        "verifiedOn": "2026-11-13",
    },
    {
        "provider": "deepseek",
        "model": "deepseek-v4-flash-0731",
        "currency": "USD",
        "rates": {"input": "0.22", "cacheReadInput": "0.007", "output": "0.66"},
        "rateBasis": "off_peak",
        "sourceUrl": _DEEPSEEK_ARCHIVE_URL,
        "effectiveFrom": "2026-08-16T16:00:00Z",
        "effectiveUntil": "2026-09-10T04:00:00Z",
        "verifiedOn": "2026-11-13",
    },
)


def subscription_price_supplements():
    """Freeze official prices and the saved FX snapshot into the pricing book."""
    conversion = None
    try:
        saved = ModelExchangeRateState.objects.filter(key="ecb").values_list(
            "snapshot_id", "snapshot__rate_date", "snapshot__rates"
        ).first()
        if saved:
            snapshot_id, rate_date, rates = saved
            usd_to_cny = Decimal((rates or {}).get("CNY") or "0")
            if snapshot_id and rate_date and usd_to_cny.is_finite() and usd_to_cny > 0:
                conversion = {"snapshotId": snapshot_id, "rateDate": rate_date.isoformat(),
                              "usdToCny": str(usd_to_cny)}
    except (DatabaseError, InvalidOperation):
        pass

    for card in _RATE_CARDS:
        native_rates = card["rates"]
        proof = {**card, "unitTokens": 1000000}
        entry = {
            "provider": card["provider"],
            "model": card["model"],
            "currency": "USD",
            "unitTokens": 1000000,
            "pricingPriority": CATALOG_PRICE_PRIORITY,
            "version": f'{card["provider"]}/{card["model"]}@{card["verifiedOn"]}',
            "pricingSource": "archived_publisher" if "effectiveUntil" in card else "official_publisher",
            "publisherTariff": proof,
        }
        if card["currency"] == "USD":
            entry.update(native_rates)
        elif conversion:
            proof["exchangeRate"] = conversion
            with localcontext() as ctx:
                ctx.prec = 60
                entry.update({key: format((Decimal(rate) / usd_to_cny).quantize(Decimal("1e-24")), "f")
                              for key, rate in native_rates.items()})
        else:
            entry["unsupportedReason"] = "publisher_exchange_rate_unavailable"
        yield entry
