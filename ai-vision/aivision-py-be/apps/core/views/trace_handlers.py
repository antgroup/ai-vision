"""Composable trace query primitives shared by internal and external (OpenAPI) views."""

import json
import logging

import requests as http_requests
from rest_framework.response import Response

from ..services.trace_query_service import (
    TraceQueryServiceError,
    get_default_trace_query_service,
)
from .common import SpaceObservationCredentialDecryptionError, error_response

logger = logging.getLogger(__name__)

_NATIVE_FILTER_DIMENSION_FIELDS = {
    "env": "resource.attributes.ant.agent.env",
    "scene": "resource.attributes.ant.agent.scene",
    "username": "attributes.ant.username",
}


def _normalize_filters(value):
    """Keep WebAPI filter arrays while accepting OpenAPI-friendly filter maps."""
    if value in (None, ""):
        return value
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
        if isinstance(value, list):
            return value
    if isinstance(value, dict):
        if isinstance(value.get("field"), str):
            return [value]
        return [
            {"field": field, "op": "=", "value": filter_value}
            for field, filter_value in value.items()
            if isinstance(field, str) and field
        ]
    return value


def _merge_payload_keys(payload, query_params, keys):
    merged = dict(query_params)
    for key in keys:
        value = payload.get(key)
        if value is not None:
            merged[key] = _normalize_filters(value) if key == "filters" else value
    if "filters" in merged:
        merged["filters"] = _normalize_filters(merged["filters"])
    return merged


def _append_native_dimension_filters(params, *, include_environment=True, include_username=True):
    """Mirror WebAPI native trace filtering for OpenAPI scalar dimensions."""
    filters = params.get("filters")
    if filters in (None, ""):
        filters = []
    filters = _normalize_filters(filters)
    if not isinstance(filters, list):
        return params

    existing_fields = {
        item.get("field") for item in filters if isinstance(item, dict)
    }
    for param_key, field in _NATIVE_FILTER_DIMENSION_FIELDS.items():
        if param_key == "env" and not include_environment:
            continue
        if param_key == "username" and not include_username:
            continue
        value = params.get(param_key)
        if value in (None, "") or field in existing_fields:
            continue
        filters.append({"field": field, "op": "=", "value": value})
        existing_fields.add(field)

    if filters:
        params["filters"] = filters
    return params


def handle_query_error(exc, method_label: str):
    if isinstance(exc, SpaceObservationCredentialDecryptionError):
        return error_response(str(exc), 3017)
    if isinstance(exc, TraceQueryServiceError):
        if exc.code in (3036, 3037):
            return Response({"code": exc.code, "message": exc.message, "data": None}, status=413 if exc.code == 3036 else 429,
                            headers={'Retry-After': '1'} if exc.code == 3037 else None)
        if exc.code == 3019:
            return Response(
                {"code": exc.code, "message": exc.message, "data": None},
                status=404,
            )
        return error_response(exc.message, exc.code)
    if isinstance(exc, http_requests.RequestException):
        logger.error("%s error: %s", method_label, exc)
        return error_response(f"Failed to {method_label}: {exc}", 3012)
    raise exc


def merge_trace_list_params(payload, query_params):
    params = _merge_payload_keys(
        payload,
        query_params,
        (
            "fields",
            "filters",
            "orderBy",
            "page",
            "limit",
            "from",
            "to",
            "fromTimestamp",
            "toTimestamp",
            "agent_product",
            "agent_uuid",
            "searchType",
            "keyword",
            "input_keyword",
            "trace_id",
            "traceId",
            "tag",
            "tags",
            "entry_only",
            "env",
            "scene",
            "username",
        ),
    )
    # Preserve unrecognized body keys for provider validation, rather than
    # silently turning a misspelled filter or limit into a broader query.
    params.update({key: value for key, value in payload.items() if key not in params})
    for control in ("io", "usage"):
        params.pop(control, None)
    # Trace environment and public user identity have canonical trace scope;
    # let the provider handle their shortcuts. Sessions retain raw predicates.
    return _append_native_dimension_filters(params, include_environment=False, include_username=False)


def merge_facets_params(payload, query_params):
    return _merge_payload_keys(
        payload,
        query_params,
        (
            "from",
            "to",
            "fromTimestamp",
            "toTimestamp",
            "agent_product",
            "agent_uuid",
            "env",
            "scene",
            "username",
            "tags",
        ),
    )


def merge_session_list_params(payload, query_params):
    params = _merge_payload_keys(
        payload,
        query_params,
        (
            "fields",
            "filters",
            "orderBy",
            "page",
            "limit",
            "from",
            "to",
            "fromTimestamp",
            "toTimestamp",
            "agent_product",
            "agent_uuid",
            "keyword",
            "sessionId",
            "session_id",
            "detail",
            "env",
            "scene",
            "username",
            "tags",
            "session_semantics",
            "session_keyword",
            "session_status",
            "min_trace_count",
            "min_total_tokens",
            "min_duration_ms",
            "searchType",
            "search_type",
        ),
    )
    if params.get("session_semantics") == "canonical-trace-v1":
        params.update({key: value for key, value in payload.items() if key not in params})
        params.pop("io", None)
        params.pop("usage", None)
        return params
    return _append_native_dimension_filters(params)


def merge_batch_params(payload, query_params, extra_keys=()):
    merged = dict(query_params)
    for key in (
        "from",
        "to",
        "fromTimestamp",
        "toTimestamp",
        "agent_product",
        "agent_uuid",
        "session_semantics",
        *extra_keys,
    ):
        value = payload.get(key)
        if value not in (None, ""):
            merged[key] = value
    return merged


def parse_batch_ids(payload, key, snake_key, max_items=100):
    ids_raw = payload.get(key, payload.get(snake_key))
    if not isinstance(ids_raw, list):
        return None, f"{key} must be a list"
    ids = [i.strip() for i in ids_raw if isinstance(i, str) and i.strip()]
    if not ids:
        return None, f"{key} must not be empty"
    if len(ids) > max_items:
        return None, f"{key} supports at most {max_items} items"
    return ids, None


def query_trace_list(space, user, query_params, source_system):
    return get_default_trace_query_service().list_traces(
        space=space, user=user,
        query_params=query_params,
        source_system_override=source_system,
    )


def query_trace_facets(space, user, query_params, source_system):
    return get_default_trace_query_service().list_facets(
        space=space, user=user,
        query_params=query_params,
        source_system_override=source_system,
    )


def query_trace_io_batch(space, user, trace_ids, query_params, source_system):
    return get_default_trace_query_service().get_trace_io_batch(
        space=space, user=user,
        trace_ids=trace_ids, query_params=query_params,
        source_system_override=source_system,
    )


def query_trace_usage_batch(space, user, trace_ids, query_params, source_system):
    return get_default_trace_query_service().get_trace_usage_batch(
        space=space, user=user,
        trace_ids=trace_ids, query_params=query_params,
        source_system_override=source_system,
    )


def query_trace_detail(space, user, trace_id, query_params, source_system):
    return get_default_trace_query_service().get_trace_detail(
        space=space, user=user,
        trace_id=trace_id, query_params=query_params,
        source_system_override=source_system,
    )


def query_span_batch(space, user, trace_id, span_ids, query_params, source_system):
    return get_default_trace_query_service().get_span_batch(
        space=space, user=user,
        trace_id=trace_id, span_ids=span_ids,
        query_params=query_params,
        source_system_override=source_system,
    )


def query_span_detail(space, user, trace_id, span_id, query_params, source_system):
    return get_default_trace_query_service().get_observation_detail(
        space=space, user=user,
        observation_id=span_id, trace_id=trace_id,
        query_params=query_params,
        source_system_override=source_system,
    )


def query_session_list(space, user, query_params, source_system):
    return get_default_trace_query_service().list_sessions(
        space=space, user=user,
        query_params=query_params,
        source_system_override=source_system,
    )


def query_session_io_batch(space, user, session_ids, query_params, source_system):
    return get_default_trace_query_service().get_session_io_batch(
        space=space, user=user,
        session_ids=session_ids, query_params=query_params,
        source_system_override=source_system,
    )


def query_session_usage_batch(space, user, session_ids, query_params, source_system):
    return get_default_trace_query_service().get_session_usage_batch(
        space=space, user=user,
        session_ids=session_ids, query_params=query_params,
        source_system_override=source_system,
    )


def query_session_detail(space, user, session_id, query_params, source_system):
    return get_default_trace_query_service().get_session_detail(
        space=space, user=user,
        session_id=session_id, query_params=query_params,
        source_system_override=source_system,
    )


def query_session_io(space, user, session_id, query_params, source_system):
    return get_default_trace_query_service().get_session_io(
        space=space, user=user,
        session_id=session_id, query_params=query_params,
        source_system_override=source_system,
    )


def _result_data_list(result):
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and isinstance(result.get("data"), list):
        return result["data"]
    return []


def _get_trace_item_id(item):
    if not isinstance(item, dict):
        return None
    for key in ("trace_id", "traceId", "id"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _get_detail_io_trace_id(data, fallback_trace_id):
    if isinstance(data, dict):
        for key in ("traceId", "trace_id", "id"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return fallback_trace_id


def _get_session_item_id(item):
    if not isinstance(item, dict):
        return None
    for key in ("session_id", "sessionId", "id"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _get_detail_io_session_id(data, fallback_session_id):
    if isinstance(data, dict):
        for key in ("sessionId", "session_id", "id"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return fallback_session_id


def enrich_list_with_io(data, space, user, query_params, source_system, max_traces=50):
    items = data.get("data") if isinstance(data, dict) else data
    if not items or not isinstance(items, list):
        return data
    trace_ids = [_get_trace_item_id(item) for item in items]
    trace_ids = [trace_id for trace_id in trace_ids if trace_id][:max_traces]
    if not trace_ids:
        return data
    io_result = query_trace_io_batch(space, user, trace_ids, query_params, source_system)
    io_list = _result_data_list(io_result)
    if io_list:
        io_map = {item.get("id"): item for item in io_list}
        for item in items:
            io_item = io_map.get(_get_trace_item_id(item))
            if io_item:
                item["input"] = io_item.get("input")
                item["output"] = io_item.get("output")
    return data


def enrich_list_with_usage(data, space, user, query_params, source_system, max_traces=50):
    items = data.get("data") if isinstance(data, dict) else data
    if not items or not isinstance(items, list):
        return data
    trace_ids = [_get_trace_item_id(item) for item in items]
    trace_ids = [trace_id for trace_id in trace_ids if trace_id][:max_traces]
    if not trace_ids:
        return data
    usage_result = query_trace_usage_batch(space, user, trace_ids, query_params, source_system)
    usage_list = _result_data_list(usage_result)
    if usage_list:
        usage_map = {item.get("id"): item for item in usage_list}
        for item in items:
            usage_item = usage_map.get(_get_trace_item_id(item))
            if usage_item:
                item["usage"] = usage_item.get("usage")
    return data


def enrich_detail_with_io(data, space, user, trace_id, query_params, source_system):
    io_trace_id = _get_detail_io_trace_id(data, trace_id)
    io_result = query_trace_io_batch(space, user, [io_trace_id], query_params, source_system)
    io_list = _result_data_list(io_result)
    if io_list:
        data["input"] = io_list[0].get("input")
        data["output"] = io_list[0].get("output")
    return data


def enrich_list_with_session_io(
    data, space, user, query_params, source_system, max_sessions=50
):
    items = data.get("data") if isinstance(data, dict) else data
    if not items or not isinstance(items, list):
        return data
    session_ids = [_get_session_item_id(item) for item in items]
    session_ids = [session_id for session_id in session_ids if session_id][:max_sessions]
    if not session_ids:
        return data
    io_result = query_session_io_batch(
        space, user, session_ids, query_params, source_system
    )
    io_list = _result_data_list(io_result)
    if io_list:
        io_map = {item.get("id"): item for item in io_list}
        for item in items:
            io_item = io_map.get(_get_session_item_id(item))
            if io_item:
                item["input"] = io_item.get("input")
                item["output"] = io_item.get("output")
    return data


def enrich_list_with_session_usage(
    data, space, user, query_params, source_system, max_sessions=50
):
    items = data.get("data") if isinstance(data, dict) else data
    if not items or not isinstance(items, list):
        return data
    pending = [
        item for item in items
        if not (isinstance(item.get("usage"), dict) and isinstance(item.get("coverage"), dict))
    ]
    session_ids = [_get_session_item_id(item) for item in pending]
    session_ids = [session_id for session_id in session_ids if session_id][:max_sessions]
    if not session_ids:
        return data
    usage_result = query_session_usage_batch(
        space, user, session_ids, query_params, source_system
    )
    usage_list = _result_data_list(usage_result)
    if usage_list:
        usage_map = {item.get("id"): item for item in usage_list}
        for item in pending:
            usage_item = usage_map.get(_get_session_item_id(item))
            if usage_item:
                item["usage"] = usage_item.get("usage")
    return data


def enrich_detail_with_session_io(
    data, space, user, session_id, query_params, source_system
):
    io_session_id = _get_detail_io_session_id(data, session_id)
    io_item = query_session_io(space, user, io_session_id, query_params, source_system)
    if isinstance(io_item, dict):
        data["input"] = io_item.get("input")
        data["output"] = io_item.get("output")
    return data
