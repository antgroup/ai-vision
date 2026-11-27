"""Versioned, scope-verified Trace summaries with explicit functional fallback."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import time

from django.conf import settings
from apps.core.services.model_pricing import get_space_model_prices

from ..native_projection import PREVIEW_CHARS, PREVIEW_VERSION
from ..native_query import (
    ENVIRONMENT_SEMANTICS, USER_IDENTITY_SEMANTICS, FIELDS, TOTAL_TOKEN_FIELDS, TRACE_FIELDS,
    NativeTraceWindow, _decode, requests_input_evidence, validate_params, normalized_agent_params,
)
from ..types import TraceListQuery, TraceQueryServiceError

PATH = "/api/v1/ai-vision/traces/read-model"
CONTRACT = "native-trace-read-model-v1"
SEMANTICS_VERSION = 1


def projection_context(prices=None):
    if prices is None:
        prices = getattr(settings, "AIVISION_MODEL_PRICES", [])
    data = {"semantics": "native-trace-summary-v17-failed-skill-read-tags", "prices": prices}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def invalid(message):
    raise TraceQueryServiceError("Invalid Trace read model: " + message, 3035)


def valid_preview(value, root_id):
    return (isinstance(value, dict) and value.get("version") == PREVIEW_VERSION
            and value.get("complete") is True and {"input", "output"} <= set(value) and value.get("rootObservationId") == root_id
            and isinstance(root_id, str) and bool(root_id)
            and all(value.get(k) is None or isinstance(value.get(k), str) and len(value[k]) <= PREVIEW_CHARS for k in ("input", "output"))
            and all(type(value.get(k)) is bool for k in ("inputTruncated", "outputTruncated")))


def _validate_summary(row, *, include_content=True):
    required = {"trace_id", "name", "userId", "session_id", "environment", "environmentSource",
                "start_time_unix_nano", "end_time_unix_nano", "duration_ms", "status_code", "span_count",
                "usage", "coverage", "reportedUsage", "rootObservationId", "rootSelectionReason", "missingParentIds",
                "attributes.gen_ai.input.messages", "attributes.gen_ai.output.messages"}
    if not include_content:
        required -= {'attributes.gen_ai.input.messages', 'attributes.gen_ai.output.messages'}
    if not required <= set(row):
        invalid("incomplete canonical summary")
    for field in ("start_time_unix_nano", "end_time_unix_nano"):
        value = row[field]
        if value is None and field == "end_time_unix_nano":
            continue
        if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 0 <= int(value) < 2**64:
            invalid("invalid canonical timestamp")
    for field in ("usage", "coverage", "reportedUsage"):
        if not isinstance(row[field], dict):
            invalid("invalid canonical usage/coverage")
    for field in ("name", "userId", "session_id", "environmentSource"):
        if row[field] is not None and not isinstance(row[field], str):
            invalid("invalid canonical dimension")
    if not isinstance(row["environment"], str) or not row["environment"]:
        invalid("invalid canonical environment")
    duration = row["duration_ms"]
    if duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
        invalid("invalid canonical duration")
    if (not isinstance(row["rootObservationId"], str) or not row["rootObservationId"]
            or not isinstance(row["rootSelectionReason"], str) or not row["rootSelectionReason"]
            or not isinstance(row["missingParentIds"], list)
            or not all(isinstance(value, str) for value in row["missingParentIds"])):
        invalid("invalid canonical root evidence")


def _prepared_params(query):
    params = normalized_agent_params(query.query_params)
    validate_params(params)
    if params.get("purpose") or params.get("entry_only") or requests_input_evidence(params):
        return None
    if params.get("trace_id") and params.get("traceId") and params["trace_id"] != params["traceId"]:
        # Canonical aliases are independent AND predicates; the wire can carry
        # only one trace_id, so retain the complete-window conjunction.
        return None
    fields = _decode(params.get("fields"))
    if isinstance(fields, str):
        fields = [item.strip() for item in fields.split(",")]
    if fields is not None and (not isinstance(fields, list) or any(not isinstance(field, str) or field not in FIELDS for field in fields)):
        return None
    order = params.get("orderBy", params.get("order_by", "start_time_unix_nano.desc"))
    if not isinstance(order, str):
        raise TraceQueryServiceError("orderBy must be a string", 3027)
    if order.removeprefix("-").removesuffix(".desc").removesuffix(".asc") not in FIELDS:
        return None
    filters = _decode(params.get("filters") or [])
    if isinstance(filters, dict):
        filters = [filters] if "field" in filters else [{"field": key, "op": "=", "value": value} for key, value in filters.items()]
    if not isinstance(filters, list) or any(not isinstance(item, dict) for item in filters):
        raise TraceQueryServiceError("filters must be an array of objects", 3027)
    # The storage contract deliberately does not claim any-observation semantics.
    if any(not isinstance(item.get("field"), str) or item["field"] not in TRACE_FIELDS for item in filters):
        return None
    validator = NativeTraceWindow([], project_id=str(query.space.uuid))
    validator.list(params)
    normalized = []
    aliases = {"eq": "=", "neq": "!=", "IN": "in", "NOT_IN": "not_in", "like": "LIKE",
               "is_null": "IS_NULL", "is_not_null": "IS_NOT_NULL"}
    for item in filters:
        field = item["field"]
        op = item.get("op", item.get("operator", "="))
        op = aliases.get(op, op)
        value = item.get("value")
        if op in {"in", "not_in"}:
            value = [validator._value(field, item) for item in value]
        elif op not in {"IS_NULL", "IS_NOT_NULL"}:
            value = validator._value(field, value)
        if FIELDS.get(field) in {"integer", "nanoseconds"} and op not in {"IS_NULL", "IS_NOT_NULL"}:
            values = value if isinstance(value, list) else [value]
            if any(v is not None and not -(2**63) <= v < 2**63 for v in values):
                return None
        normalized.append({"field": field, "op": op, "value": value})
    for key in ("keyword", "input_keyword", "trace_id", "traceId", "session_id", "sessionId", "env", "scene", "username"):
        if not params.get(key):
            params.pop(key, None)
    for key in ("keyword", "input_keyword"):
        if params.get(key):
            params[key] = str(params[key]).lower()
    params.update(page=int(params.get("page", 1)), limit=int(params.get("limit", 50)), filters=normalized)
    return params


def query_read_model(provider, query):
    started = time.perf_counter()
    params = _prepared_params(query)
    if params is None or not query.agent_uuid:
        return None, {"state": "fallback", "reason": "unsupported_query"}
    params = provider._freeze_window_params(TraceListQuery(query.space, query.user, params))
    frozen = TraceListQuery(query.space, query.user, params)
    client, config = provider._context(frozen)
    # A public SDK username is a business predicate, never a physical auth subject.
    scope_params = {key: value for key, value in params.items() if key != "username"}
    wire = provider._params(TraceListQuery(query.space, query.user, scope_params), config, agent_filter=False)
    if params.get("username"):
        wire["username"] = params["username"]
    wire.pop("fields", None)
    order = wire.get("orderBy", "")
    order_field = order.removeprefix("-").removesuffix(".desc").removesuffix(".asc")
    if order_field in TOTAL_TOKEN_FIELDS:
        # Match NativeTraceWindow's leading-minus precedence over a suffix.
        direction = "desc" if order.startswith("-") or order.endswith(".desc") else "asc"
        wire["orderBy"] = f"{order_field}.{direction}"
        wire["token_nulls_last"] = "true"
    fields = _decode(params.get('fields'))
    if isinstance(fields, str):
        fields = [field.strip() for field in fields.split(',')]
    content_fields = {'attributes.gen_ai.input.messages', 'attributes.gen_ai.output.messages',
                      'attributes.gen_ai.tool.call.arguments', 'attributes.gen_ai.tool.call.result'}
    from .snapshot_window import metrics_need_content
    if (isinstance(fields, list) and fields and not content_fields.intersection(fields)
            and not metrics_need_content(params)):
        wire['metadata_only'] = 'true'
    wire["agent_uuid"] = query.agent_uuid
    wire["projection_context"] = projection_context(get_space_model_prices(query.space))
    try:
        payload = client.get(PATH + "/page", params=wire)
    except TraceQueryServiceError as exc:
        if exc.code == 3019:
            return None, {"state": "fallback", "reason": "capability_unavailable"}
        raise
    if payload.get("supported") is False:
        if not isinstance(payload.get("reason"), str) or not payload["reason"]:
            invalid("unsupported response needs reason")
        return None, {"state": "fallback", "reason": payload["reason"]}
    result = validate_page(payload, query, params, wire)
    return result, {"state": "ready", "elapsedMs": round((time.perf_counter() - started) * 1000, 3)}


def validate_page(payload, query, params, wire):
    meta, rows, facets = payload.get("meta"), payload.get("data"), payload.get("facets")
    if (payload.get("supported") is not True or not isinstance(meta, dict)
            or meta.get("contract") != CONTRACT or meta.get("complete") is not True
            or meta.get("environmentSemantics") != ENVIRONMENT_SEMANTICS
            or meta.get("userIdentitySemantics") != USER_IDENTITY_SEMANTICS):
        invalid("missing complete canonical contract")
    if wire.get("token_nulls_last") == "true" and meta.get("tokenNullsLast") is not True:
        invalid("missing canonical Token order")
    for key in ("from", "to", "space_id", "agent_product", "user", "agent_uuid", "projection_context"):
        expected = wire.get(key)
        if key in {"from", "to"}:
            if type(meta.get(key)) is not int or meta[key] != expected:
                invalid(key + " differs from frozen window")
        elif (meta.get(key) or "") != (expected or ""):
            invalid(key + " differs from authorized scope")
    for key in ("page", "limit", "totalItems", "totalPages"):
        if type(meta.get(key)) is not int:
            invalid(key + " is not an integer")
    page, limit, total = meta["page"], meta["limit"], meta["totalItems"]
    if (page != params["page"] or limit != params["limit"] or total < 0
            or meta["totalPages"] != math.ceil(total / limit)):
        invalid("inconsistent pagination")
    if "windowTotalItems" in meta and (type(meta["windowTotalItems"]) is not int or meta["windowTotalItems"] < total):
        invalid("invalid unfiltered window count")
    expected_rows = max(0, min(limit, total - (page - 1) * limit))
    if not isinstance(rows, list) or len(rows) != expected_rows:
        invalid("incomplete page")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            invalid("row is not an object")
        _validate_summary(row, include_content=wire.get('metadata_only') != 'true')
        identifier = row.get("trace_id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            invalid("duplicate/missing Trace ID")
        seen.add(identifier)
        if (row.get("resource.attributes.aivision.agent.id") != query.agent_uuid
                or row.get("resource.attributes.aivision.space.id") != str(query.space.uuid)):
            invalid("row scope mismatch")
        if type(row.get("sourceRevision")) is not int or row["sourceRevision"] < 1:
            invalid("source revision missing")
        if type(row.get("span_count")) is not int or row["span_count"] < 1 or type(row.get("status_code")) is not int or row.get("status_code") not in (0, 1, 2):
            invalid("invalid summary count or status")
        for name in ("_traceTags", "_sceneValues"):
            if not isinstance(row.get(name), list) or not all(isinstance(v, str) for v in row[name]):
                invalid("canonical dimension evidence missing")
        if not valid_preview(row.get("ioPreview"), row.get("rootObservationId")):
            invalid("invalid IO preview")
    if not isinstance(facets, dict) or set(facets) != {"env", "scene", "username", "tags"}:
        invalid("missing canonical facets")
    for values in facets.values():
        if not isinstance(values, list):
            invalid("facets must be arrays")
        found = set()
        for item in values:
            if (not isinstance(item, dict) or not isinstance(item.get("value"), str) or item["value"] in found
                    or type(item.get("count")) is not int or not 0 < item["count"] <= total):
                invalid("invalid facet count")
            found.add(item["value"])
    include_previews = True
    window = NativeTraceWindow.from_summary_rows(rows, include_previews=include_previews)
    local = {**params, "page": 1, "limit": 200}
    try:
        checked = window.list({**local, "fields": ["trace_id"]})
        if [item["trace_id"] for item in checked["data"]] != [item["trace_id"] for item in rows]:
            invalid("row predicate/order differs from canonical semantics")
        result = window.list(local)
    except (TypeError, ValueError, KeyError, TraceQueryServiceError) as exc:
        invalid(str(exc))
    result["meta"].update(meta)
    result["meta"].update(queryMode="read_model", queryContract=CONTRACT,
                          environmentFacetsComplete=True, userIdentityFacetsComplete=True,
                          agent_product=query.agent_product, agent_uuid=query.agent_uuid,
                          filters=query.get_param("filters", []), timeBoundary="fromInclusiveToExclusive")
    if not include_previews:
        result["meta"].pop("ioPreviewComplete", None)
    result["facets"] = {key: sorted(value, key=lambda item: item["value"]) for key, value in facets.items()}
    return result


def facet_result(page):
    """Keep the independent facet endpoint's existing window-count semantics."""
    if "windowTotalItems" not in page["meta"]:
        return None
    meta = {**page["meta"], "totalItems": page["meta"]["windowTotalItems"], "supported": True}
    for key in ("page", "limit", "totalPages", "ioPreviewComplete"):
        meta.pop(key, None)
    return {"data": page["facets"], "meta": meta}


def comparable_result(value):
    """Only strip documented transport additions; keep all canonical row values."""
    result = deepcopy(value)
    for row in result.get("data", []):
        for key in ("sourceRevision",):
            row.pop(key, None)
    result["meta"] = {key: result.get("meta", {}).get(key) for key in ("page", "limit", "totalItems", "totalPages", "complete", "from", "to", "agent_uuid", "agent_product")}
    return result
