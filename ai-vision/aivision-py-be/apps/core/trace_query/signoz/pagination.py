"""Versioned storage pagination, with canonical projection of this page only.

The storage endpoint selects whole traces (never a filtered subset of spans),
and computes the total and facets in the same database statement. Unsupported
queries retain the verified-window compatibility implementation.
"""

import math

from apps.core.services.model_pricing import get_space_model_prices

from ..native_query import ENVIRONMENT_SEMANTICS, USER_IDENTITY_SEMANTICS, FIELDS, NativeTraceWindow, _decode, validate_params
from ..types import TraceListQuery, TraceQueryServiceError

CONTRACT = "native-trace-page-v1"
ORDER_FIELDS = {
    "trace_id", "start_time_unix_nano", "end_time_unix_nano",
    "duration_ms", "status_code", "span_count",
}
FILTER_FIELDS = ORDER_FIELDS | {
    "resource.attributes.ant.agent.env", "resource.attributes.ant.agent.scene",
    "resource.attributes.aivision.agent.id", "attributes.ant.username",
}
OP_ALIASES = {
    "eq": "=", "neq": "!=", "IN": "in", "NOT_IN": "not_in", "like": "LIKE",
    "is_null": "IS_NULL", "is_not_null": "IS_NOT_NULL",
}


def _invalid(message):
    raise TraceQueryServiceError(f"Invalid storage trace page: {message}", 3035)


def page_params(query):
    """Return a normalized supported request, or None for compatibility reads."""
    params = dict(query.query_params)
    validate_params(params)
    # Native predicates ignore falsey optional scalars. Drop them before the
    # GET mapper turns False/0 into strings or lets them shadow active aliases.
    for key in ("keyword", "input_keyword", "trace_id", "traceId",
                "session_id", "sessionId", "env", "scene", "username"):
        if not params.get(key):
            params.pop(key, None)
    if params.get("purpose") or params.get("input_keyword"):
        return None
    if params.get("username"):
        # Existing storage username predicates read the collector subject,
        # whereas this shortcut now follows the canonical SDK trace userId.
        # Use the explicit complete-window scan (maximum 10000 traces) until
        # the storage endpoint implements this public identity contract.
        return None
    if params.get("session_id") or params.get("sessionId"):
        return None
    if params.get("trace_id") and params.get("traceId") and params["trace_id"] != params["traceId"]:
        # Native treats both aliases as independent AND predicates; the legacy
        # transport parameter mapper can only forward one of them.
        return None
    if params.get("searchType", params.get("search_type", "traceId")) != "traceId":
        return None
    order = params.get("orderBy", params.get("order_by", "start_time_unix_nano.desc"))
    if not isinstance(order, str):
        raise TraceQueryServiceError("orderBy must be a string", 3027)
    field = order.removeprefix("-").removesuffix(".desc").removesuffix(".asc")
    if field not in ORDER_FIELDS:
        return None
    fields = _decode(params.get("fields"))
    if isinstance(fields, str):
        fields = [field.strip() for field in fields.split(",")]
    # Dynamic fields are discovered from a full window by the legacy contract.
    # A field absent on this page cannot prove it is absent in the whole window.
    if isinstance(fields, list) and any(not isinstance(f, str) or f not in FIELDS for f in fields):
        return None
    filters = _decode(params.get("filters") or [])
    if isinstance(filters, dict):
        filters = [filters] if "field" in filters else [
            {"field": field, "op": "=", "value": value} for field, value in filters.items()
        ]
    if not isinstance(filters, list) or not all(isinstance(item, dict) for item in filters):
        raise TraceQueryServiceError("filters must be an array of objects", 3027)
    if any(not isinstance(item.get("field"), str) or item["field"] not in FILTER_FIELDS for item in filters):
        return None
    validator = NativeTraceWindow([], project_id=str(query.space.uuid))
    # Reuse public validation, including strict page sizes, operator types and
    # nanosecond integers, before making a storage request.
    validator.list(params)
    # This storage contract derives status from every span, while public Trace
    # status follows only the canonical root. A page cannot prove which matches
    # were omitted or establish global root-status order. Keep those requests
    # on the verified window until storage provides canonical status semantics.
    # Projecting status from complete page spans remains safe.
    if field == "status_code" or any(item["field"] == "status_code" for item in filters):
        return None
    normalized = []
    for item in filters:
        op = item.get("op", item.get("operator", "="))
        op = OP_ALIASES.get(op, op)
        value = item.get("value")
        if op in {"in", "not_in"}:
            value = [validator._value(item["field"], v) for v in value]
        elif op not in {"IS_NULL", "IS_NOT_NULL"}:
            value = validator._value(item["field"], value)
        if (FIELDS[item["field"]] in {"integer", "nanoseconds"}
                and op not in {"IS_NULL", "IS_NOT_NULL"}):
            operands = value if op in {"in", "not_in"} else [value]
            if any(v is not None and not -(2 ** 63) <= v < 2 ** 63 for v in operands):
                # The canonical query accepts wider integers than SigNoz's
                # int64 parser. Preserve that behavior via the complete window.
                return None
        normalized.append({"field": item["field"], "op": op, "value": value})
    params.update(page=int(params.get("page", 1)), limit=int(params.get("limit", 50)),
                  orderBy=order, filters=normalized)
    tags = _decode(params.get("tags", params.get("tag", [])))
    params["tags"] = [tags] if isinstance(tags, str) else tags or []
    return params


def query_trace_page(provider, query):
    params = page_params(query)
    if params is None:
        return None
    params = provider._freeze_window_params(TraceListQuery(query.space, query.user, params))
    frozen = TraceListQuery(query.space, query.user, params)
    client, config = provider._context(frozen)
    # A business-space username is a trace predicate, not an authorization
    # restriction on physical spans. Legacy _params maps it to storage user;
    # keep those two roles separate on this endpoint.
    scope_params = {key: value for key, value in params.items() if key != "username"}
    storage_params = provider._params(
        TraceListQuery(query.space, query.user, scope_params), config, agent_filter=False,
    )
    if params.get("username"):
        storage_params["username"] = params["username"]
    storage_params.pop("fields", None)
    if query.agent_uuid:
        storage_params["agent_uuid"] = query.agent_uuid
    try:
        payload = client.get("/api/v1/ai-vision/traces/page", params=storage_params)
    except TraceQueryServiceError as exc:
        if exc.code == 3019:
            # Older SigNoz versions do not expose this endpoint. Do not treat
            # transport failures or malformed successful responses as fallback.
            return None
        raise
    if payload.get("supported") is False:
        if not isinstance(payload.get("reason"), str) or not payload["reason"]:
            _invalid("unsupported response lacks a reason")
        return None

    meta, details, facets = payload.get("meta"), payload.get("data"), payload.get("facets")
    if not isinstance(meta, dict) or meta.get("contract") != CONTRACT or meta.get("complete") is not True:
        _invalid("missing version or completeness proof")
    for key in ("page", "limit", "totalItems", "totalPages"):
        if type(meta.get(key)) is not int:
            _invalid(f"{key} must be an integer")
    page, limit, total = meta["page"], meta["limit"], meta["totalItems"]
    if page != params["page"] or limit != params["limit"] or total < 0:
        _invalid("pagination does not match the request")
    if meta["totalPages"] != math.ceil(total / limit):
        _invalid("inconsistent totalPages")
    for key in ("from", "to", "space_id", "agent_product", "user", "agent_uuid"):
        expected = storage_params.get(key)
        actual = meta.get(key)
        if key in {"from", "to"}:
            if type(actual) is not int or actual != expected:
                _invalid(f"{key} does not match the frozen window")
        elif (actual or "") != (expected or ""):
            _invalid(f"{key} does not match the authorized scope")
    expected_rows = max(0, min(limit, total - (page - 1) * limit))
    if not isinstance(details, list) or len(details) != expected_rows:
        _invalid("incomplete page")
    ids = []
    for detail in details:
        if not isinstance(detail, dict):
            _invalid("trace must be an object")
        trace_id, spans, count = detail.get("traceId"), detail.get("spans"), detail.get("spanCount")
        if not isinstance(trace_id, str) or not trace_id or trace_id in ids:
            _invalid("duplicate or missing trace ID")
        if type(count) is not int or count < 1 or not isinstance(spans, list) or len(spans) != count:
            _invalid("incomplete trace spans")
        span_ids = [span.get("span_id") for span in spans if isinstance(span, dict)]
        if (len(span_ids) != count or any(not isinstance(sid, str) or not sid for sid in span_ids)
                or len(set(span_ids)) != count):
            _invalid("duplicate or missing span ID")
        provider._verify_scope(query, spans)
        ids.append(trace_id)
    if not isinstance(facets, dict) or set(facets) != {"env", "scene", "username", "tags"}:
        _invalid("missing complete-window facets")
    for dimension, values in facets.items():
        if not isinstance(values, list):
            _invalid(f"{dimension} facets must be an array")
        seen = set()
        for item in values:
            if (not isinstance(item, dict) or not isinstance(item.get("value"), str)
                    or type(item.get("count")) is not int or not 0 < item["count"] <= total
                    or item["value"] in seen):
                _invalid(f"invalid {dimension} facet")
            seen.add(item["value"])
        facets[dimension] = sorted(values, key=lambda item: item["value"])
    environment_facets_complete = meta.get("environmentSemantics") == ENVIRONMENT_SEMANTICS
    user_identity_facets_complete = meta.get("userIdentitySemantics") == USER_IDENTITY_SEMANTICS
    legacy_environment_filter = bool(params.get("env")) and not environment_facets_complete
    if not environment_facets_complete:
        # Preserve ordinary storage pagination, including windows above the
        # compatibility scan limit. The caller can request canonical facets
        # independently; raw any-span counts must not masquerade as root counts.
        facets = {key: values for key, values in facets.items() if key != "env"}
    if not user_identity_facets_complete:
        # Raw collector/employee subjects are not public SDK user candidates.
        # Keep ordinary storage pagination; callers can request canonical
        # identity facets independently through the bounded complete window.
        facets = {key: values for key, values in facets.items() if key != "username"}
    try:
        window = NativeTraceWindow(details, project_id=str(query.space.uuid),
                                   prices=get_space_model_prices(query.space),
                                   include_previews=True)
        # Project all records in the received page. Reapplying the public
        # predicate/order is an independent check, never a second global page.
        local = {**params, "page": 1, "limit": 200}
        check_params = {**local, "fields": ["trace_id"]}
        if legacy_environment_filter:
            # Validate the legacy page against its declared raw predicate before
            # falling back; malformed successful responses must still fail.
            check_params.update(env=None, filters=[*params["filters"], {
                "field": "resource.attributes.ant.agent.env", "op": "=", "value": params["env"],
            }])
        checked = window.list(check_params)
        if [row["trace_id"] for row in checked["data"]] != ids:
            _invalid("storage filtering or ordering differs from native semantics")
        if legacy_environment_filter:
            # A legacy resource predicate can omit canonical matches entirely.
            # Even an empty, valid page cannot establish the canonical answer.
            return None
        result = window.list(local)
    except ValueError as exc:
        _invalid(str(exc))
    result["meta"] = {
        "page": page, "limit": limit, "totalItems": total, "totalPages": meta["totalPages"],
        "complete": True, "from": meta["from"], "to": meta["to"],
        "timeBoundary": "fromInclusiveToExclusive", "filters": query.get_param("filters", []),
        "agent_uuid": query.agent_uuid, "agent_product": query.agent_product,
        "queryMode": "storage", "queryContract": CONTRACT,
        **({"ioPreviewComplete": True} if result["meta"].get("ioPreviewComplete") else {}),
        "environmentFacetsComplete": environment_facets_complete,
        **({"environmentSemantics": ENVIRONMENT_SEMANTICS} if environment_facets_complete else {}),
        "userIdentityFacetsComplete": user_identity_facets_complete,
        **({"userIdentitySemantics": USER_IDENTITY_SEMANTICS} if user_identity_facets_complete else {}),
    }
    result["facets"] = facets
    return result
