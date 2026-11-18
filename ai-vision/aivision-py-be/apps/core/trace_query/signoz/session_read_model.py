"""Scope-verified session groups over the versioned canonical Trace read model."""
from __future__ import annotations

from copy import deepcopy
import math
import time

from apps.core.services.model_pricing import get_space_model_prices

from ..canonical_sessions import (SEMANTICS, aggregate_matches, prepare_params,
                                  session_summary, trace_matches)
from ..native_query import ENVIRONMENT_SEMANTICS, USER_IDENTITY_SEMANTICS
from ..types import SessionListQuery, TraceQueryServiceError
from .read_model import PATH, _validate_summary, projection_context, valid_preview

CONTRACT = "native-session-read-model-v1"
MAX_TRACES = 5000


def invalid(message):
    raise TraceQueryServiceError("Invalid Session read model: " + message, 3035)


def query_read_model(provider, query):
    started = time.perf_counter()
    params = prepare_params(query.query_params)
    if not query.agent_uuid:
        return None, {"state": "fallback", "reason": "unsupported_query"}
    client, config = provider._context(query)
    # SDK task user is never the physical authorization identity.
    scope = {key: value for key, value in params.items() if key != "username"}
    wire = provider._params(SessionListQuery(query.space, query.user, scope), config, agent_filter=False)
    for key in ("session_semantics", "session_keyword", "session_status", "min_trace_count", "min_total_tokens", "min_duration_ms", "username"):
        if params.get(key) not in (None, ""):
            wire[key] = params[key]
    wire["agent_uuid"] = query.agent_uuid
    wire["projection_context"] = projection_context(get_space_model_prices(query.space))
    if params.get("keyword"):
        wire["keyword"] = params["keyword"].lower()
    # Public bounds count Unicode characters. The optional v1 endpoint counts
    # UTF-8 bytes (after lower normalization) and supports fewer deep pages.
    # Keep valid canonical requests on the scan path before its parser rejects
    # them, including values deliberately deferred by the bounded projector.
    bounds = {key: 255 for key in ("agent_uuid", "user", "agent_product", "session_id", "env", "username", "session_keyword")}
    bounds["keyword"] = 4096
    if params["page"] > 1000000 or any(
        isinstance(wire.get(key), str) and len(wire[key].encode("utf-8")) > maximum
        for key, maximum in bounds.items()
    ):
        return None, {"state": "fallback", "reason": "unsupported_wire_bounds"}
    if params.get('keyword') and params.get('searchType') in {'input', 'output'}:
        from .performance_capabilities import performance_capabilities
        if performance_capabilities(client).get('content_projection') != 'native-trace-content-v1':
            return None, {'state': 'fallback', 'reason': 'content_capability_unavailable'}
    try:
        payload = client.get(PATH + "/sessions/page", params=wire)
    except TraceQueryServiceError as exc:
        if exc.code == 3019:
            return None, {"state": "fallback", "reason": "capability_unavailable"}
        raise
    if payload.get("supported") is False:
        if not isinstance(payload.get("reason"), str) or not payload["reason"]:
            invalid("unsupported response needs reason")
        return None, {"state": "fallback", "reason": payload["reason"]}
    result = validate_page(payload, query, params, wire)
    return result, {"state": "ready", "elapsedMs": round((time.perf_counter()-started)*1000, 3)}


def validate_page(payload, query, params, wire):
    meta, groups, facets = payload.get("meta"), payload.get("data"), payload.get("facets")
    if (payload.get("supported") is not True or not isinstance(meta, dict)
            or meta.get("contract") != CONTRACT or meta.get("complete") is not True
            or meta.get("sessionSemantics") != SEMANTICS
            or meta.get("environmentSemantics") != ENVIRONMENT_SEMANTICS
            or meta.get("userIdentitySemantics") != USER_IDENTITY_SEMANTICS):
        invalid("missing complete canonical contract")
    for key in ("from", "to", "space_id", "agent_product", "user", "agent_uuid", "projection_context"):
        if key in {"from", "to"}:
            if type(meta.get(key)) is not int or meta[key] != wire.get(key):
                invalid(key + " differs from frozen window")
        elif (meta.get(key) or "") != (wire.get(key) or ""):
            invalid(key + " differs from authorized scope")
    for key in ("page", "limit", "totalItems", "totalPages", "windowTotalItems"):
        if type(meta.get(key)) is not int:
            invalid(key + " is not an integer")
    page, limit, total = meta["page"], meta["limit"], meta["totalItems"]
    if (page != params["page"] or limit != params["limit"] or total < 0
            or meta["totalPages"] != math.ceil(total/limit) or meta["windowTotalItems"] < total):
        invalid("inconsistent pagination")
    expected = max(0, min(limit, total-(page-1)*limit))
    if not isinstance(groups, list) or len(groups) != expected:
        invalid("incomplete session page")
    rows, seen, seen_traces, trace_count = [], set(), set(), 0
    for group in groups:
        if not isinstance(group, dict):
            invalid("group is not an object")
        sid, traces, matched = group.get("session_id"), group.get("traces"), group.get("matchedTraceIds")
        if not isinstance(sid, str) or not sid or sid in seen:
            invalid("duplicate or missing Session ID")
        seen.add(sid)
        if (not isinstance(traces, list) or not traces or type(group.get("traceCount")) is not int
                or group["traceCount"] != len(traces)):
            invalid("incomplete session Trace group")
        trace_count += len(traces)
        if trace_count > MAX_TRACES:
            invalid("session page exceeds Trace bound")
        if (not isinstance(matched, list) or not matched or any(not isinstance(value, str) for value in matched)
                or len(set(matched)) != len(matched)):
            invalid("invalid matched Trace IDs")
        ids, ordered = [], []
        for trace in traces:
            if not isinstance(trace, dict):
                invalid("Trace row is not an object")
            _validate_summary(trace)
            start = int(trace["start_time_unix_nano"])
            end = trace["end_time_unix_nano"]
            if not meta["from"] * 1000000 <= start < meta["to"] * 1000000 or end is not None and int(end) < start:
                invalid("Trace timestamp is outside canonical window")
            identifier = trace.get("trace_id")
            if not isinstance(identifier, str) or not identifier or identifier in seen_traces or trace.get("session_id") != sid:
                invalid("duplicate Trace or incorrect group membership")
            seen_traces.add(identifier)
            ids.append(identifier)
            ordered.append((int(trace["start_time_unix_nano"]), identifier))
            if (trace.get("resource.attributes.aivision.agent.id") != query.agent_uuid
                    or trace.get("resource.attributes.aivision.space.id") != str(query.space.uuid)):
                invalid("Trace scope mismatch")
            if (type(trace.get("sourceRevision")) is not int or trace["sourceRevision"] < 1
                    or type(trace.get("span_count")) is not int or trace["span_count"] < 1
                    or type(trace.get("status_code")) is not int or trace["status_code"] not in (0, 1, 2)):
                invalid("invalid Trace evidence")
            for field in ("_traceTags", "_sceneValues"):
                if not isinstance(trace.get(field), list) or any(not isinstance(value, str) for value in trace[field]):
                    invalid("missing canonical dimensions")
            if not valid_preview(trace.get("ioPreview"), trace.get("rootObservationId")):
                invalid("invalid bounded IO preview")
            for field in ("billableCalls", "knownUsageCalls", "knownTokens", "knownCostCalls"):
                value = trace["coverage"].get(field)
                if type(value) is not int or value < 0:
                    invalid("invalid usage coverage")
            for field in ("input", "output", "total", "cacheReadInput", "cacheWriteInput", "reasoning"):
                value = trace["usage"].get(field)
                if value is not None and (type(value) is not int or value < 0):
                    invalid("invalid canonical token count")
            if type(trace["usage"].get("observationCount")) is not int or trace["usage"]["observationCount"] < 0:
                invalid("invalid usage call count")
            if trace.get("total_tokens") != trace["usage"].get("total"):
                invalid("inconsistent canonical total")
            if not isinstance(trace["usage"].get("cost"), dict):
                invalid("invalid canonical cost")
            cost = trace["usage"]["cost"]
            for field in ("total", "input", "output"):
                value = cost.get(field)
                if value is not None:
                    try:
                        from decimal import Decimal
                        if not isinstance(value, str) or not Decimal(value).is_finite() or Decimal(value) < 0:
                            raise ValueError()
                    except (ValueError, ArithmeticError):
                        invalid("invalid canonical cost amount")
        if ordered != sorted(ordered) or matched != [identifier for identifier in ids if identifier in set(matched)]:
            invalid("unordered Traces or matched IDs outside group")
        metadata = {key: value for key, value in params.items() if key != "keyword"}
        metadata_ids = [trace["trace_id"] for trace in traces if trace_matches(trace, metadata)]
        if not set(matched).issubset(metadata_ids) or not params.get("keyword") and matched != metadata_ids:
            invalid("matched Trace metadata differs from canonical predicates")
        row = session_summary(sid, traces, matched)
        if not aggregate_matches(row, params):
            invalid("aggregate predicates do not match session")
        rows.append(row)
    sorted_rows = sorted(rows, key=lambda row: row["session_id"])
    sorted_rows.sort(key=lambda row: row["last_activity"], reverse=True)
    if [row["session_id"] for row in rows] != [row["session_id"] for row in sorted_rows]:
        invalid("unordered sessions")
    if not isinstance(facets, dict) or set(facets) != {"env", "username", "tags"}:
        invalid("missing canonical facets")
    for values in facets.values():
        if not isinstance(values, list) or len(values) > 500:
            invalid("invalid facets")
        found = set()
        for item in values:
            if (not isinstance(item, dict) or not isinstance(item.get("value"), str) or not item["value"]
                    or item["value"] in found or type(item.get("count")) is not int or item["count"] < 1
                    or item["count"] > meta["windowTotalItems"]):
                invalid("invalid facet count or value")
            found.add(item["value"])
    result_meta = deepcopy(meta)
    result_meta.update(queryMode="read_model", queryContract=CONTRACT, ioPreviewComplete=True,
                       agent_uuid=query.agent_uuid, agent_product=query.agent_product,
                       timeBoundary="fromInclusiveToExclusive")
    return {"data": rows, "facets": {key: sorted(values, key=lambda item: item["value"]) for key, values in facets.items()},
            "meta": result_meta}
