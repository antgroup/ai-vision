from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo
from apps.core.openapi_time import (
    OpenAPITimeParseError,
    get_present_param,
    parse_openapi_time,
)
from apps.core.trace_query.types import TraceQueryServiceError
from .types import EventListQuery, EventFacetQuery, EventRawSqlQuery
from .filters import FilterOperator, FilterSpec, OrderSpec

QUERY_TIMEZONE = ZoneInfo("Asia/Shanghai")

DEFAULT_PAGE_SIZE = 50

DEFAULT_MAX_PAGE_SIZE = 200

DEFAULT_MAX_TIME_RANGE_DAYS = 30

ABSOLUTE_MAX_TIME_RANGE_DAYS = 90

SORTABLE_FIELDS = {
    "time",
    "event_name",
    "trace_id",
    "session_id",
    "username",
    "env",
    "model",
    "duration_ms",
}

DEFAULT_FIELDS = ["time", "event_name", "trace_id", "session_id", "username"]

DEFAULT_FACET_FIELDS = ["event_name", "env", "username", "scene", "model"]

ALLOWED_OPERATORS = {
    "=": FilterOperator.EQ,
    "eq": FilterOperator.EQ,
    "in": FilterOperator.IN,
    "IN": FilterOperator.IN,
    ">=": FilterOperator.GTE,
    "gte": FilterOperator.GTE,
    "<=": FilterOperator.LTE,
    "lte": FilterOperator.LTE,
    "like": FilterOperator.LIKE,
    "LIKE": FilterOperator.LIKE,
    "is_null": FilterOperator.IS_NULL,
    "IS_NULL": FilterOperator.IS_NULL,
    "is_not_null": FilterOperator.IS_NOT_NULL,
    "IS_NOT_NULL": FilterOperator.IS_NOT_NULL,
}


def _nested(config: dict[str, Any], *keys: str) -> dict[str, Any]:
    node: Any = config
    for key in keys:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
    return node if isinstance(node, dict) else {}


def _event_config(config: dict[str, Any]) -> dict[str, Any]:
    return _nested(config, "openapi", "events")


def _limits(config: dict[str, Any]) -> dict[str, Any]:
    limits = _nested(config, "openapi", "events", "limits")
    return limits if isinstance(limits, dict) else {}


def _safe_int(
    value: Any, default: int, *, minimum: int = 1, maximum: int | None = None
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _public_error(message: str, code: int = 3027) -> TraceQueryServiceError:
    return TraceQueryServiceError(message, code)


def _public_limits(config: dict[str, Any]) -> dict[str, int]:
    limits = _limits(config)
    max_page = _safe_int(
        limits.get("max_page_size"),
        DEFAULT_MAX_PAGE_SIZE,
        minimum=1,
        maximum=DEFAULT_MAX_PAGE_SIZE,
    )
    default_page = _safe_int(
        limits.get("default_page_size"), DEFAULT_PAGE_SIZE, minimum=1, maximum=max_page
    )
    max_days = _safe_int(
        limits.get("max_time_range_days"),
        DEFAULT_MAX_TIME_RANGE_DAYS,
        minimum=1,
        maximum=ABSOLUTE_MAX_TIME_RANGE_DAYS,
    )
    return {
        "defaultPageSize": default_page,
        "maxPageSize": max_page,
        "maxTimeRangeDays": max_days,
    }


def _parse_pagination(
    query: EventListQuery, config: dict[str, Any]
) -> tuple[int, int, int]:
    limits = _public_limits(config)
    page = _safe_int(query.get_param("page"), 1)
    limit = _safe_int(
        query.get_param("limit"),
        limits["defaultPageSize"],
        minimum=1,
        maximum=limits["maxPageSize"],
    )
    return limit, (page - 1) * limit, page


def _parse_required_time_range(
    query: EventListQuery | EventFacetQuery | EventRawSqlQuery, config: dict[str, Any]
) -> tuple[datetime, datetime]:
    from_key, from_ts = get_present_param(query.query_params, "from", "fromTimestamp")
    to_key, to_ts = get_present_param(query.query_params, "to", "toTimestamp")
    if from_key is None or to_key is None:
        raise _public_error("Event query requires valid from and to timestamps")
    try:
        from_dt = parse_openapi_time(from_ts, field_name=from_key, tz=QUERY_TIMEZONE)
        to_dt = parse_openapi_time(to_ts, field_name=to_key, tz=QUERY_TIMEZONE)
    except OpenAPITimeParseError as exc:
        raise _public_error(str(exc)) from exc
    if from_dt > to_dt:
        raise _public_error("Event query from cannot be later than to")
    max_days = _public_limits(config)["maxTimeRangeDays"]
    if to_dt - from_dt > timedelta(days=max_days):
        raise _public_error(f"Event query time range cannot exceed {max_days} days")
    return from_dt, to_dt


def _select_public_fields(
    requested: Any, config: dict[str, Any], field_map: dict[str, str]
) -> list[str]:
    default_fields = _event_config(config).get("default_fields")
    if requested in (None, ""):
        requested = (
            default_fields if isinstance(default_fields, list) else DEFAULT_FIELDS
        )
    if isinstance(requested, str):
        requested = [item.strip() for item in requested.split(",") if item.strip()]
    if not isinstance(requested, list) or not requested:
        raise _public_error("fields must be a non-empty list")
    result: list[str] = []
    for item in requested:
        if not isinstance(item, str) or item not in field_map:
            raise _public_error(f"Unknown event field: {item}")
        if item not in result:
            result.append(item)
    return result


def _normalize_filters(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        if isinstance(value.get("field"), str):
            return [value]
        return [
            {"field": key, "op": "=", "value": item}
            for key, item in value.items()
            if isinstance(key, str)
        ]
    if isinstance(value, list):
        if not all(isinstance(item, dict) for item in value):
            raise _public_error("Each filter must be an object")
        return value
    raise _public_error("filters must be an object or array")


def _parse_public_filters(value: Any, field_map: dict[str, str]) -> list[FilterSpec]:
    filters: list[FilterSpec] = []
    for item in _normalize_filters(value):
        public_field = item.get("field")
        if not isinstance(public_field, str) or public_field not in field_map:
            raise _public_error(f"Unknown event filter field: {public_field}")
        raw_op = item.get("op") or item.get("operator") or "="
        operator = ALLOWED_OPERATORS.get(str(raw_op))
        if operator is None:
            raise _public_error(f"Unsupported event filter operator: {raw_op}")
        value = item.get("value")
        if operator == FilterOperator.IN and not isinstance(value, list):
            raise _public_error("IN filter value must be a list")
        if operator in (FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL):
            value = None
        filters.append(FilterSpec(field_map[public_field], operator, value))
    return filters


def _parse_order_by(value: Any, field_map: dict[str, str]) -> list[OrderSpec]:
    if value in (None, ""):
        return (
            [OrderSpec(field_map["time"], ascending=False)]
            if "time" in field_map
            else []
        )
    if not isinstance(value, str):
        raise _public_error("orderBy must be a string")
    ascending = not value.startswith("-")
    public_key = value[1:] if value.startswith("-") else value
    if public_key not in field_map or public_key not in SORTABLE_FIELDS:
        raise _public_error(f"Unknown or unsortable orderBy field: {public_key}")
    return [OrderSpec(field_map[public_key], ascending=ascending)]


def metadata_without_events(raw_sql_enabled: bool = False) -> dict[str, Any]:
    del raw_sql_enabled
    return {
        "capabilities": {
            "traces": {"list": True, "detail": True, "spans": True, "sessions": True},
            "events": {
                "list": False,
                "facets": False,
                "query": False,
                "detail": False,
                "modes": {"fieldsJson": False, "rawSql": False},
            },
        },
        "sources": {"events": []},
        "eventSchema": {
            "fields": [],
            "allowedOperators": [],
            "limits": _public_limits({}),
        },
    }
