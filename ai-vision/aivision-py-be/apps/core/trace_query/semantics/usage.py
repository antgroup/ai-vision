"""Shared usage contract: inclusive input/output, reported totals, decimal cost.

Attributes are never mutated. Langfuse's named ``input`` bucket excludes its
cache buckets; OpenAI prompt_tokens and OTel input_tokens include them. Flat
Langfuse buckets are mutually exclusive, whereas nested provider reasoning
details are already included in their output/completion token count. Unknown remains
None. Prices are explicit configuration, not guesses based on model names.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from apps.core.services.official_model_pricing import OfficialModelPriceIndex, PUBLISHER_PRICE_SOURCES

FIELDS = ("input", "output", "total", "cacheReadInput", "cacheWriteInput", "reasoning")
STANDARD = {
    "input": ("gen_ai.usage.input_tokens",),
    "output": ("gen_ai.usage.output_tokens",),
    "total": ("gen_ai.usage.total_tokens",),
    "cacheReadInput": (
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.input_cached_tokens",
        "gen_ai.usage.cache_read_input_tokens",
    ),
    "cacheWriteInput": (
        "gen_ai.usage.cache_creation.input_tokens",
        "gen_ai.usage.cache_write.input_tokens",
        "gen_ai.usage.cache_creation_input_tokens",
    ),
    "reasoning": (
        "gen_ai.usage.reasoning.output_tokens",
        "gen_ai.usage.output_reasoning_tokens",
    ),
}
ALIASES = {
    "input": ("input", "prompt_tokens", "input_tokens"),
    "output": ("output", "completion_tokens", "output_tokens"),
    "total": ("total", "total_tokens"),
    "cacheReadInput": (
        "input_cache_read",
        "cache_read_input_tokens",
        "cached_tokens",
        "input_cached_tokens",
    ),
    "cacheWriteInput": (
        "input_cache_creation",
        "cache_creation_input_tokens",
        "cache_write_input_tokens",
    ),
    "reasoning": ("output_reasoning", "reasoning_tokens", "output_reasoning_tokens"),
}
OPENINFERENCE = {
    "input": "llm.token_count.prompt",
    "output": "llm.token_count.completion",
    "total": "llm.token_count.total",
    "cacheReadInput": "llm.token_count.prompt_details.cache_read",
    "cacheWriteInput": "llm.token_count.prompt_details.cache_write",
    "reasoning": "llm.token_count.completion_details.reasoning",
}


class _FrozenPrice(dict):
    """Keep the indexed snapshot JSON-compatible without exposing mutable rates."""
    def _immutable(self, *args, **kwargs):
        raise TypeError("Indexed model prices are immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable


def _freeze_price(value):
    if isinstance(value, dict):
        return _FrozenPrice({key: _freeze_price(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_price(item) for item in value)
    return value


def _thaw_price(value):
    if isinstance(value, dict):
        return {key: _thaw_price(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_price(item) for item in value]
    return value


def _price_fingerprint(prices):
    return hashlib.sha256(json.dumps(prices, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _metered_price(item):
    return isinstance(item, dict) and item.get("pricingSource") not in PUBLISHER_PRICE_SOURCES


class IndexedModelPrices(tuple):
    """Request-local JSON-compatible rate snapshot indexed by exact identity.

    Build once at a window or resolver boundary. The frozen rates and cached
    fingerprint are safe to share across detail workers for that projection.
    """
    def __new__(cls, prices):
        return super().__new__(cls, (_freeze_price(item) for item in prices))

    def __init__(self, prices):
        self.fingerprint = _price_fingerprint(self)
        self.valid = all(isinstance(item, dict) for item in self)
        self._candidates = {}
        for item in self:
            if not _metered_price(item):
                continue
            provider, model = item.get("provider"), item.get("model")
            if isinstance(provider, str) and isinstance(model, str):
                self._candidates.setdefault((provider, model), []).append(item)
        self.official_models = OfficialModelPriceIndex(self)

    def candidates(self, provider, model):
        if not isinstance(provider, str) or not isinstance(model, str):
            return ()
        return tuple(self._candidates.get((provider, model), ()))


def model_prices_fingerprint(prices):
    # Plain configuration lists remain mutable: fingerprint them on every use
    # so changing a rate cannot accidentally reuse an older canonical result.
    return prices.fingerprint if isinstance(prices, IndexedModelPrices) else _price_fingerprint(prices)


def indexed_model_prices(prices):
    if isinstance(prices, (list, tuple)) and not isinstance(prices, IndexedModelPrices):
        return IndexedModelPrices(prices)
    return prices


def attribute_values(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Accept raw attributes or a flat query row with attributes.* columns."""
    nested = attributes.get("attributes")
    result = dict(nested) if isinstance(nested, Mapping) else {}
    result.update(
        {
            key.removeprefix("attributes."): value
            for key, value in attributes.items()
            if key != "attributes"
        }
    )
    return result


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() and number >= 0 else None


def _count(value: Any) -> int | None:
    number = _decimal(value)
    return (
        int(number)
        if number is not None and number == number.to_integral_value()
        else None
    )


def _complete_flat_exclusive_usage(raw: Mapping[str, Any]) -> bool:
    """Prove absent buckets are zero using only one valid raw breakdown."""
    optional = ("cacheReadInput", "cacheWriteInput", "reasoning")
    allowed = {"input", "output", "total"} | {
        alias for field in optional for alias in ALIASES[field]
    }
    if (
        not {"input", "output", "total"}.issubset(raw)
        or not set(raw).issubset(allowed)
        or any(_count(value) is None for value in raw.values())
    ):
        return False
    counts = {field: _count(raw[field]) for field in ("input", "output", "total")}
    for field in optional:
        names = [name for name in ALIASES[field] if name in raw]
        # Multiple aliases cannot prove a mutually exclusive breakdown, even
        # when choosing one of them would make the reported total balance.
        if len(names) > 1:
            return False
        counts[field] = _count(raw[names[0]]) if names else 0
    return counts["total"] == sum(
        counts[field] for field in ("input", "output", *optional)
    )


def _text(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def _price_time(value):
    try:
        # Price effective dates are ISO timestamps; UTC is required if an
        # observation time is supplied so historical recomputation is stable.
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def _cost(attrs, usage, *, model, provider, prices, observed_at):
    """Resolve billing basis before interpreting SDK money as a call charge.

    Subscription SDKs can report a public token-price equivalent, including
    zero, rather than an attributable invoice charge. Preserve that evidence
    separately; never add it to known monetary subtotals. An explicit per-call
    mode permits a Space/model default to coexist with pay-as-you-go accounts.
    """
    mode = attrs.get("aivision.billing.mode")
    valid_prices = isinstance(prices, (list, tuple)) and (
        prices.valid if isinstance(prices, IndexedModelPrices)
        else all(isinstance(item, dict) for item in prices)
    )
    candidates = (list(prices.candidates(provider, model)) if isinstance(prices, IndexedModelPrices) else [
        item for item in prices or [] if _metered_price(item) and item.get("provider") == provider and item.get("model") == model
    ]) if isinstance(prices, (list, tuple)) else []
    markers = [item for item in candidates if item.get("pricingSource") == "subscription"]
    if mode is None and not markers:
        return _pay_as_you_go_cost(attrs, usage, model=model, provider=provider, prices=prices, observed_at=observed_at)
    paygo_prices = [item for item in candidates if item.get("pricingSource") != "subscription"] if valid_prices else prices
    if mode == "pay_as_you_go":
        result = _pay_as_you_go_cost(attrs, usage, model=model, provider=provider, prices=paygo_prices, observed_at=observed_at)
        result["billingMode"] = mode
        return result
    error = None
    marker = None
    if mode is not None and mode != "subscription":
        error = "invalid_billing_mode"
    else:
        if any(_price_time(item.get("effectiveFrom")) is None for item in markers):
            error = "invalid_price_configuration"
        elif markers and _price_time(observed_at) is None:
            error = "pricing_time_missing"
        else:
            effective = [item for item in markers if _price_time(item["effectiveFrom"]) <= _price_time(observed_at)]
            if not effective and mode is None:
                return _pay_as_you_go_cost(attrs, usage, model=model, provider=provider, prices=paygo_prices, observed_at=observed_at)
            effective.sort(key=lambda item: _price_time(item["effectiveFrom"]), reverse=True)
            if len(effective) > 1 and _price_time(effective[0]["effectiveFrom"]) == _price_time(effective[1]["effectiveFrom"]):
                error = "ambiguous_price_configuration"
            elif effective:
                marker = effective[0]
        if mode == "subscription":
            # Explicit call billing evidence is sufficient for the safe
            # subscription result even when a broad marker cannot be timed.
            error = None
    result = _pay_as_you_go_cost({}, usage, model=model, provider=provider, prices=[], observed_at=observed_at)
    result.update(reason=error or "subscription_billing", billingMode=None if error else "subscription",
                  pricingSource="subscription" if not error else None,
                  valuationBasis="subscription" if not error else None,
                  estimateListPrice=not error,
                  listPriceEquivalent=None,
                  pricingVersion=marker.get("version") if marker else None,
                  pricingEffectiveFrom=marker.get("effectiveFrom") if marker else None)
    reported = _pay_as_you_go_cost(attrs, usage, model=model, provider=provider, prices=[], observed_at=observed_at)
    if any(reported.get(key) is not None for key in ("input", "output", "total")):
        result["reportedCost"] = {key: reported.get(key) for key in ("input", "output", "total", "currency", "source")}
        if reported.get("details"):
            result["reportedCost"]["details"] = reported["details"]
    if not error:
        resolved_model = attrs.get("gen_ai.response.model")
        if not isinstance(resolved_model, str) or not resolved_model.strip():
            resolved_model = model
        result["listPriceEquivalent"] = _official_base_estimate(
            usage, model=resolved_model, provider=provider, prices=prices, observed_at=observed_at
        )
    return result


def _can_use_base_tariff(reason):
    """Allow independently priced adjustments to be omitted in a base estimate."""
    base_adjustments = {"context_over_200k", "tiers", "multipliers", "web_search"}
    for part in reason.split("; "):
        if part in {"unsupported_input_conditions: overrides", "unsupported_output_conditions: overrides",
                    "unsupported_mode_pricing"}:
            continue
        prefix, _, fields = part.partition(": ")
        if prefix != "unsupported_cost_fields" or not set(fields.split(", ")) <= base_adjustments:
            return False
    return True


def _official_base_estimate(usage, *, model, provider, prices, observed_at):
    if isinstance(prices, IndexedModelPrices):
        index = prices.official_models
    else:
        index = OfficialModelPriceIndex(prices if isinstance(prices, (list, tuple)) else ())
    selected = index.resolve(provider, model)
    supplement = index.supplement(provider, model, selected=selected)
    if supplement is not None:
        selected = supplement
    if selected is None:
        estimate = _pay_as_you_go_cost({}, usage, model=model, provider=provider, prices=[], observed_at=observed_at)
        estimate["reason"] = "official_model_price_unavailable"
        return estimate

    price = dict(selected)
    if supplement is not None:
        # Reuse the scalar calculator without registering this row as metered
        # pricing or representing a historical tariff as effective today.
        price.update(pricingSource="catalog", valuationBasis="current_catalog")
    reason = price.get("unsupportedReason")
    if isinstance(reason, str) and reason and _can_use_base_tariff(reason):
        del price["unsupportedReason"]
    # Apply the base input rate to tokens without a reported cache split.
    # Preserve both the reported buckets and missing measurements in usage.
    for bucket in ("cacheReadInput", "cacheWriteInput"):
        if usage[bucket] is None:
            price[bucket] = None
    if usage["reasoning"] is None and _decimal(price.get("reasoning")) == _decimal(price.get("output")):
        price["reasoning"] = None
    estimate = _pay_as_you_go_cost({}, usage, model=price["model"], provider=price["provider"],
                                  prices=[price], observed_at=observed_at)
    if supplement is not None:
        estimate.update(pricingSource=supplement["pricingSource"], valuationBasis="publisher_base_rate",
                        publisherTariff=_thaw_price(supplement["publisherTariff"]))
    else:
        estimate.update(pricingSource="official_catalog", valuationBasis="official_base_catalog")
    return estimate


def _pay_as_you_go_cost(attrs, usage, *, model, provider, prices, observed_at):
    result = {
        "total": None,
        "input": None,
        "output": None,
        "source": None,
        "currency": "USD",
        "reason": None,
        "pricingVersion": None,
        "pricingEffectiveFrom": None,
        "pricingSource": None,
        "valuationBasis": None,
        "catalogProvider": None,
        "catalogModel": None,
        "catalogSnapshot": None,
    }
    raw = _object(attrs.get("langfuse.observation.cost_details"))
    if raw:
        result["details"] = {
            key: _text(_decimal(value))
            for key, value in raw.items()
            if _decimal(value) is not None
        }
    for key, aliases in {
        "input": ("input", "input_cost"),
        "output": ("output", "output_cost"),
        "total": ("total", "total_cost"),
    }.items():
        value = next((_decimal(raw[name]) for name in aliases if name in raw), None)
        if value is None:
            value = _decimal(attrs.get(f"gen_ai.usage.{key}_cost"))
        result[key] = _text(value)
    if _decimal(raw.get("input")) is not None:
        result["input"] = _text(
            _decimal(raw["input"])
            + sum(
                (
                    _decimal(raw.get(key)) or Decimal(0)
                    for key in (
                        "input_cache_read",
                        "input_cache_creation",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                        "cache_write_input_tokens",
                    )
                ),
                Decimal(0),
            )
        )
    if _decimal(raw.get("output")) is not None:
        result["output"] = _text(
            _decimal(raw["output"])
            + (
                _decimal(raw.get("output_reasoning", raw.get("reasoning_tokens")))
                or Decimal(0)
            )
        )
    if result["total"] is not None:
        result["source"] = "reported"
        result["pricingSource"] = "reported"
        return result
    if raw:
        parts = [
            _decimal(value)
            for key, value in raw.items()
            if key not in {"total", "total_cost"}
        ]
        if parts and all(value is not None for value in parts):
            result["total"] = _text(sum(parts, Decimal(0)))
            result["source"] = "reported"
            result["pricingSource"] = "reported"
            return result
    if result["input"] is not None and result["output"] is not None:
        result["total"] = _text(Decimal(result["input"]) + Decimal(result["output"]))
        result["source"] = "reported"
        result["pricingSource"] = "reported"
        return result
    if not provider:
        result["reason"] = "provider_unknown"
        return result
    if prices is not None and (
        not isinstance(prices, (list, tuple))
        or (not prices.valid if isinstance(prices, IndexedModelPrices)
            else any(not isinstance(item, dict) for item in prices))
    ):
        result["reason"] = "invalid_price_configuration"
        return result
    candidates = (list(prices.candidates(provider, model)) if isinstance(prices, IndexedModelPrices) else [
        item for item in prices or [] if _metered_price(item) and item.get("model") == model and item.get("provider") == provider
    ])
    if not candidates:
        result["reason"] = "price_not_configured"
        return result
    # Only an explicitly marked live catalog valuation can price an older
    # observation without asserting an historical effective date. Frozen rules
    # (including alias/reference selections) always obey their explicit date.
    def current_catalog(item):
        return item.get("pricingSource") == "catalog" and item.get("valuationBasis") == "current_catalog"

    if any(not current_catalog(item) and _price_time(item.get("effectiveFrom")) is None
           or type(item.get("pricingPriority", 350)) is not int for item in candidates):
        result["reason"] = "invalid_price_configuration"
        return result
    when = _price_time(observed_at) if observed_at is not None else None
    if (observed_at is not None and when is None
            or observed_at is None and sum(not current_catalog(item) for item in candidates) > 1):
        result["reason"] = "pricing_time_missing"
        return result
    candidates = [item for item in candidates
                  if current_catalog(item) or when is None or _price_time(item["effectiveFrom"]) <= when]
    if not candidates:
        result["reason"] = "price_not_effective"
        return result
    # Only a confirmed, effective reference can replace an explicitly
    # unsupported automatic catalog tariff. Invalid explicit rules and missing
    # usage still follow the usual validation path and remain unknown.
    if any(item.get("pricingSource") == "reference" for item in candidates):
        candidates = [item for item in candidates
                      if not (current_catalog(item) and item.get("unsupportedReason"))]
    def rank(item):
        return (item.get("pricingPriority", 350),
                _price_time(item.get("effectiveFrom")) or datetime.min.replace(tzinfo=timezone.utc))
    candidates.sort(key=rank, reverse=True)
    price = candidates[0]
    if len(candidates) > 1 and rank(price) == rank(candidates[1]):
        result["reason"] = "ambiguous_price_configuration"
        return result
    result.update(
        currency=price.get("currency", "USD"),
        pricingVersion=price.get("version"),
        pricingEffectiveFrom=None if current_catalog(price) else price.get("effectiveFrom"),
        pricingSource=price.get("pricingSource", "manual"),
        valuationBasis=price.get("valuationBasis", "effective_price"),
        **{key: price.get(key) for key in ("catalogProvider", "catalogModel", "catalogSnapshot")},
    )
    if price.get("unsupportedReason"):
        result["reason"] = str(price["unsupportedReason"])
        return result
    unit = _decimal(price.get("unitTokens"))
    if not unit or result["currency"] != "USD" or not price.get("version"):
        result["reason"] = "invalid_price_configuration"
        return result
    if usage["input"] is None or usage["output"] is None:
        result["reason"] = "usage_missing"
        return result
    parts = {"input": Decimal(usage["input"]), "output": Decimal(usage["output"])}
    for key in ("cacheReadInput", "cacheWriteInput"):
        count = usage[key]
        if count is None and _decimal(price.get(key)) is not None:
            result["reason"] = "cache_usage_missing"
            return result
        if count:
            parts[key] = Decimal(count)
            parts["input"] -= Decimal(count)
    if _decimal(price.get("reasoning")) is not None:
        if usage["reasoning"] is None:
            result["reason"] = "reasoning_usage_missing"
            return result
        if usage["reasoning"]:
            parts["reasoning"] = Decimal(usage["reasoning"])
            parts["output"] -= parts["reasoning"]
    if parts["input"] < 0 or parts["output"] < 0:
        result["reason"] = "overlapping_usage_buckets"
        return result
    amounts = {}
    for key, value in parts.items():
        rate = _decimal(price.get(key))
        if rate is None:
            result["reason"] = "price_bucket_missing"
            return result
        amounts[key] = value * rate / unit
    result.update(
        total=_text(sum(amounts.values(), Decimal(0))),
        input=_text(
            sum(
                (value for key, value in amounts.items() if key not in {"output", "reasoning"}), Decimal(0)
            )
        ),
        output=_text(amounts["output"] + amounts.get("reasoning", Decimal(0))),
        source="estimated",
    )
    result["details"] = {key: _text(value) for key, value in amounts.items()}
    return result


def normalize_usage(
    attributes: Mapping[str, Any],
    *,
    model=None,
    provider=None,
    prices=None,
    observed_at=None,
) -> dict[str, Any]:
    attrs = attribute_values(attributes)
    raw_usage = _object(attrs.get("langfuse.observation.usage_details"))
    provenance = _object(attrs.get("aivision.normalization.sources"))
    result = dict.fromkeys(FIELDS)
    sources = {}
    for field in FIELDS:
        # Recompute our older derived fields from their preserved raw source.
        # Explicit OTel fields remain authoritative, including an explicit zero.
        # Native agents must emit required numeric placeholders when their
        # source did not report usage. Those placeholders are not measurements;
        # independently preserved SDK usage can still supply the missing field.
        missing = attrs.get(f"agentic.usage.{field}_tokens.missing")
        unavailable = missing is True or missing == "true"
        candidates = [
            (name, attrs.get(name))
            for name in STANDARD[field]
            if name not in provenance and not unavailable
        ]
        candidates += [
            (f"langfuse.observation.usage_details.{name}", raw_usage.get(name))
            for name in ALIASES[field]
        ]
        candidates.append((OPENINFERENCE[field], attrs.get(OPENINFERENCE[field])))
        for name, raw in candidates:
            value = _count(raw)
            if value is not None:
                result[field], sources[field] = value, name
                break
    for field, parent, child in (
        ("cacheReadInput", "prompt_tokens_details", "cached_tokens"),
        ("cacheReadInput", "input_tokens_details", "cached_tokens"),
        ("reasoning", "completion_tokens_details", "reasoning_tokens"),
        ("reasoning", "output_tokens_details", "reasoning_tokens"),
    ):
        value = _count(_object(raw_usage.get(parent)).get(child))
        if result[field] is None and value is not None:
            result[field], sources[field] = (
                value,
                f"langfuse.observation.usage_details.{parent}.{child}",
            )
    # Prove completeness from raw SDK buckets independently of the selected
    # per-field OTel values. Overrides can neither invalidate a complete raw
    # breakdown nor repair an incomplete one into evidence for absent zeros.
    if _complete_flat_exclusive_usage(raw_usage):
        for field in ("cacheReadInput", "cacheWriteInput", "reasoning"):
            if result[field] is None and not any(
                key in raw_usage for key in ALIASES[field]
            ):
                result[field] = 0
                sources[field] = "langfuse.complete_usage_absent_bucket"
    incomplete_exclusive = False
    if sources.get("input") == "langfuse.observation.usage_details.input":
        if result["cacheReadInput"] is None or result["cacheWriteInput"] is None:
            result["input"] = None
            incomplete_exclusive = True
        else:
            result["input"] += result["cacheReadInput"] + result["cacheWriteInput"]
        # Preserve the exclusive source even when coverage is incomplete so
        # native v1 reads can round-trip the raw SDK buckets without inventing
        # inclusive measurements.
        sources["input"] = "langfuse.exclusive_input_plus_cache"
    if sources.get("output") == "langfuse.observation.usage_details.output":
        if result["reasoning"] is None:
            result["output"] = None
            incomplete_exclusive = True
        else:
            result["output"] += result["reasoning"]
        sources["output"] = "langfuse.exclusive_output_plus_reasoning"
    if (
        result["total"] is None
        and result["input"] is not None
        and result["output"] is not None
    ):
        result["total"] = result["input"] + result["output"]
        sources["total"] = "derived_input_plus_output"
    result["inputIncludesCache"] = True if result["input"] is not None else None
    result["unknownReason"] = (
        "exclusive_usage_incomplete"
        if incomplete_exclusive
        else "usage_missing" if result["total"] is None else None
    )
    result["source"] = {
        field: provenance.get(source, source) for field, source in sources.items()
    }
    result["raw"] = {
        key: value
        for key, value in attrs.items()
        if key.startswith(("gen_ai.usage.", "llm.token_count."))
        or key
        in {
            "langfuse.observation.usage_details", "langfuse.observation.cost_details",
            "agentic.usage.input_tokens.missing", "agentic.usage.output_tokens.missing",
        }
    }
    result["cost"] = _cost(
        attrs,
        result,
        model=model
        or attrs.get("gen_ai.request.model")
        or attrs.get("langfuse.observation.model.name"),
        provider=provider or attrs.get("gen_ai.provider.name"),
        prices=prices,
        observed_at=observed_at,
    )
    return result


def usage_coverage(observations) -> dict[str, int]:
    """Reported measurements of unique calls, not proof of a complete total."""
    calls = {
        (item.get("traceId"), item.get("id")): item
        for item in observations
        if item.get("type") in {"GENERATION", "EMBEDDING"}
    }
    known = [item for item in calls.values() if item["usage"]["total"] is not None]
    return {
        "billableCalls": len(calls),
        "knownUsageCalls": len(known),
        "knownTokens": sum(item["usage"]["total"] for item in known),
        "knownCostCalls": sum(
            item["usage"]["cost"]["total"] is not None for item in calls.values()
        ),
    }


def reported_usage(observations) -> dict[str, dict[str, int | None]]:
    """Known per-field subtotals of normalized calls, not complete totals.

    Nested calls can report overlapping measurements. Their canonical usage
    retains the ambiguity guard independently of these reported subtotals.
    """
    calls = {
        (item.get("traceId"), item.get("id")): item
        for item in observations
        if item.get("type") in {"GENERATION", "EMBEDDING"}
    }
    result = {}
    for field in ("input", "output", "total"):
        values = [item["usage"][field] for item in calls.values() if item["usage"][field] is not None]
        result[field] = {"value": sum(values) if values else None, "reportedCalls": len(values)}
    return result


def _nested_billable_calls(observations):
    nodes = {(item.get("traceId"), item.get("id")): item for item in observations}
    ambiguous = set()
    for item in nodes.values():
        if item.get("type") not in {"GENERATION", "EMBEDDING"}:
            continue
        child = (item.get("traceId"), item.get("id"))
        parent = (item.get("traceId"), item.get("parentObservationId"))
        visited = set()
        while parent in nodes and parent not in visited:
            if nodes[parent].get("type") in {"GENERATION", "EMBEDDING"}:
                ambiguous.update((child, parent))
            visited.add(parent)
            parent = (parent[0], nodes[parent].get("parentObservationId"))
    return ambiguous


def ambiguous_call_ids(observations):
    """Share nested ownership semantics with the typed statistics projector."""
    return _nested_billable_calls(observations)


def cost_coverage(observations, *, context=None, _ambiguous_calls=None):
    """Cost evidence for unique calls; partial subtotals require disjoint ownership."""
    observations = list(observations)
    calls = {(item.get("traceId"), item.get("id")): item for item in observations
             if item.get("type") in {"GENERATION", "EMBEDDING"}}
    result = {key: 0 for key in ("billableCalls", "knownCostCalls", "reportedCalls", "manualCalls",
                                "aliasCalls", "catalogCalls", "referenceCalls", "subscriptionCalls", "listPriceEquivalentCalls", "unknownCalls")}
    ambiguous = (_nested_billable_calls(context if context is not None else observations)
                 if _ambiguous_calls is None else _ambiguous_calls)
    result.update(billableCalls=len(calls), knownSubtotal=None, referenceSubtotal=None,
                  listPriceEquivalentSubtotal=None, currency="USD", unknownReasons={},
                  additive=not bool(ambiguous.intersection(calls)))
    known, reference, equivalents = [], [], []
    for item in calls.values():
        cost = item.get("usage", {}).get("cost", {})
        if cost.get("billingMode") == "subscription":
            result["subscriptionCalls"] += 1
            equivalent = cost.get("listPriceEquivalent") or {}
            amount = _decimal(equivalent.get("total"))
            if amount is not None and equivalent.get("currency", "USD") == "USD":
                equivalents.append(amount)
                result["listPriceEquivalentCalls"] += 1
                source = equivalent.get("pricingSource")
                if source in ("manual", "alias", "catalog", "reference", "official_catalog", *PUBLISHER_PRICE_SOURCES):
                    sources = result.setdefault("listPriceEquivalentSources", {})
                    sources[source] = sources.get(source, 0) + 1
            elif amount is None and cost.get("estimateListPrice") is True:
                reason = equivalent.get("reason") or "cost_missing"
                reasons = result.setdefault("listPriceEquivalentUnknownReasons", {})
                reasons[reason] = reasons.get(reason, 0) + 1
        amount = _decimal(cost.get("total"))
        if amount is None or cost.get("currency", "USD") != "USD":
            reason = cost.get("reason") or "cost_missing"
            if cost.get("currency", "USD") != "USD":
                reason = "unsupported_currency"
            result["unknownCalls"] += 1
            result["unknownReasons"][reason] = result["unknownReasons"].get(reason, 0) + 1
            continue
        result["knownCostCalls"] += 1
        source = "reported" if cost.get("source") == "reported" else cost.get("pricingSource", "manual")
        if source in {"reported", "manual", "alias", "catalog", "reference"}:
            result[source + "Calls"] += 1
        known.append(amount)
        if source == "reference":
            reference.append(amount)
    if result["additive"]:
        result["knownSubtotal"] = _text(sum(known, Decimal(0))) if known else None
        result["referenceSubtotal"] = _text(sum(reference, Decimal(0))) if reference else None
        result["listPriceEquivalentSubtotal"] = _text(sum(equivalents, Decimal(0))) if equivalents else None
    return result


def observation_cost_coverages(observations):
    """Per-call evidence with one ownership scan over the complete trace."""
    observations = list(observations)
    ambiguous = _nested_billable_calls(observations)
    return {
        item["id"]: cost_coverage([item], _ambiguous_calls=ambiguous)
        for item in observations if item.get("type") in {"GENERATION", "EMBEDDING"}
    }


def combine_cost_coverage(coverages):
    """Combine already deduplicated, disjoint Trace coverage in a Session."""
    coverages = list(coverages)
    result = cost_coverage([])
    for item in coverages:
        for key in ("billableCalls", "knownCostCalls", "reportedCalls", "manualCalls", "aliasCalls",
                    "catalogCalls", "referenceCalls", "subscriptionCalls", "listPriceEquivalentCalls", "unknownCalls"):
            result[key] += item.get(key, 0)
        for reason, count in item.get("unknownReasons", {}).items():
            result["unknownReasons"][reason] = result["unknownReasons"].get(reason, 0) + count
        for reason, count in item.get("listPriceEquivalentUnknownReasons", {}).items():
            reasons = result.setdefault("listPriceEquivalentUnknownReasons", {})
            reasons[reason] = reasons.get(reason, 0) + count
        for source, count in item.get("listPriceEquivalentSources", {}).items():
            sources = result.setdefault("listPriceEquivalentSources", {})
            sources[source] = sources.get(source, 0) + count
    result["additive"] = all(item.get("additive") is True for item in coverages)
    if result["additive"]:
        for field in ("knownSubtotal", "referenceSubtotal", "listPriceEquivalentSubtotal"):
            values = [_decimal(item.get(field)) for item in coverages if item.get(field) is not None]
            result[field] = _text(sum(values, Decimal(0))) if values else None
    return result


def aggregate_usage(observations, *, prices=None, context=None) -> dict[str, Any]:
    """Aggregate unique billable calls; never count agent/chain rollups again."""
    observations = list(observations)
    context = list(context) if context is not None else observations
    unique = {}
    for observation in observations:
        kind = str(observation.get("type", "")).upper()
        if kind not in {"GENERATION", "EMBEDDING"}:
            continue
        key = (observation.get("traceId"), observation.get("id"))
        unique[key] = observation
    usages = [
        item.get("usage")
        or normalize_usage(
            item.get("attributes") or item,
            model=item.get("model"),
            provider=item.get("provider"),
            prices=prices,
            observed_at=item.get("startTime"),
        )
        for item in unique.values()
    ]
    result = {
        field: (
            sum(usage[field] for usage in usages)
            if usages and all(usage[field] is not None for usage in usages)
            else None
        )
        for field in FIELDS
    }
    result.update(
        inputIncludesCache=True if result["input"] is not None else None,
        source={
            field: "aggregate_unique_calls"
            for field in FIELDS
            if result[field] is not None
        },
        raw={},
        observationCount=len(usages),
        unknownReason=None,
    )

    ambiguous = _nested_billable_calls(context)

    if ambiguous.intersection(unique):
        result.update(dict.fromkeys(FIELDS))
        result["unknownReason"] = "ambiguous_nested_generations"
        result["inputIncludesCache"] = None
        result["source"] = {}
    elif result["total"] is None:
        result["unknownReason"] = "usage_missing"
    evidence_calls = [{**item, "usage": usage} for item, usage in zip(unique.values(), usages)]
    result["costCoverage"] = cost_coverage(evidence_calls, context=context)
    costs = [usage["cost"] for usage in usages]
    result["cost"] = {
        "total": None,
        "input": None,
        "output": None,
        "source": "aggregate_unique_calls",
        "currency": "USD",
        "reason": "cost_missing",
        "pricingVersion": None,
    }
    if (
        costs
        and all(
            cost["total"] is not None and cost["currency"] == "USD" for cost in costs
        )
        and result["unknownReason"] != "ambiguous_nested_generations"
    ):
        for field in ("total", "input", "output"):
            if all(cost[field] is not None for cost in costs):
                result["cost"][field] = _text(
                    sum((Decimal(cost[field]) for cost in costs), Decimal(0))
                )
        result["cost"]["reason"] = None
    return result
