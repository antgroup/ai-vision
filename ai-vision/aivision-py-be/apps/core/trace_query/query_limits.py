"""Public list-filter limits; these never reinterpret recorded measurements."""
from decimal import Decimal, InvalidOperation
import json

from .types import TraceQueryServiceError

MIN_TOTAL_TOKENS = 100
MIN_DURATION_MS = 1
MIN_COUNT = 1
MAX_LIST_THRESHOLD = 2**53 - 1


def validate_trace_lower_bounds(filters):
    """Reject ineffective lower bounds before any read-model or raw query.

    Equality to zero, unknown checks and status UNSET remain useful predicates.
    We neither drop an invalid condition nor silently clamp it to a new value.
    """
    if not filters:
        return
    if isinstance(filters, str):
        try:
            filters = json.loads(filters)
        except ValueError:
            raise TraceQueryServiceError("filters must be valid JSON", 3027)
    if isinstance(filters, dict):
        filters = ([filters] if "field" in filters else
                   [{"field": key, "op": "=", "value": value} for key, value in filters.items()])
    if not isinstance(filters, list) or any(not isinstance(item, dict) for item in filters):
        raise TraceQueryServiceError("filters must be an array of objects", 3027)
    minima = {"total_tokens": MIN_TOTAL_TOKENS, "attributes.gen_ai.usage.total_tokens": MIN_TOTAL_TOKENS,
              "duration_ms": MIN_DURATION_MS, "span_count": MIN_COUNT}
    for item in filters:
        field = item.get("field")
        if not isinstance(field, str):
            raise TraceQueryServiceError("filter field must be a string", 3027)
        minimum = minima.get(field)
        if minimum is None or item.get("op", item.get("operator", "=")) not in (">", ">=", "gt", "gte"):
            continue
        value = item.get("value")
        try:
            number = Decimal(str(value))
            if isinstance(value, bool) or not number.is_finite() or not minimum <= number <= MAX_LIST_THRESHOLD:
                raise InvalidOperation()
        except (InvalidOperation, ValueError):
            raise TraceQueryServiceError(
                f"{item['field']} minimum must be from {minimum} to {MAX_LIST_THRESHOLD}; clear the filter to view all values", 3027)
