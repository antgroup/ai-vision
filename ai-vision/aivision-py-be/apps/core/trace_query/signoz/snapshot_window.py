"""Complete bounded storage snapshots, merging clean summaries and dirty raw spans.

The storage service reads both halves in one repeatable-read transaction. An
old backend can decline the capability; a malformed success, authentication or
transport failure must never silently select another path.
"""
from __future__ import annotations

from apps.core.services.model_pricing import get_space_model_prices

from ..native_query import NativeTraceWindow, FIELDS, TRACE_FIELDS, _decode, requests_input_evidence
from ..memory_budget import admit_query
from ..semantics.observations import timestamp_ns
from ..types import BaseTraceQuery, TraceQueryServiceError
from .read_model import PATH, _validate_summary, projection_context, valid_preview

CONTRACT = "native-trace-window-v1"
MAX_TRACES = 50000
METRICS_CONTRACT = 'native-trace-metrics-window-v1'


def metrics_need_content(params):
    if params.get('input_keyword') or (params.get('keyword') and params.get('searchType', params.get('search_type')) in {'input', 'output'}):
        return True
    filters = _decode(params.get('filters') or [])
    if isinstance(filters, dict):
        filters = [filters] if 'field' in filters else [{'field': key} for key in filters]
    return isinstance(filters, list) and any(isinstance(item, dict) and item.get('field') in {
        'attributes.gen_ai.input.messages', 'attributes.gen_ai.output.messages', 'ioPreview',
    } for item in filters)


def invalid(message):
    raise TraceQueryServiceError("Invalid complete Trace snapshot: " + message, 3035)


def needs_raw_observations(params):
    if params.get("purpose") or params.get("entry_only") or requests_input_evidence(params):
        return True
    order = params.get("orderBy", params.get("order_by", "start_time_unix_nano.desc"))
    if not isinstance(order, str) or order.removeprefix("-").removesuffix(".desc").removesuffix(".asc") not in FIELDS:
        return True
    fields = _decode(params.get("fields"))
    if isinstance(fields, str):
        fields = [item.strip() for item in fields.split(",")]
    if fields is not None and (not isinstance(fields, list) or any(not isinstance(field, str) or field not in FIELDS for field in fields)):
        return True
    filters = _decode(params.get("filters") or [])
    if isinstance(filters, dict):
        filters = [filters] if "field" in filters else [{"field": field} for field in filters]
    return not isinstance(filters, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("field"), str) or item["field"] not in TRACE_FIELDS
        for item in filters)


def query_snapshot_window(provider, query, *, raw=False, physical_user=None, include_metrics=False, _prices=None):
    raw = raw or include_metrics and metrics_need_content(query.query_params)
    compact = include_metrics and not raw
    client, config = provider._context(query)
    prices = get_space_model_prices(query.space) if _prices is None else _prices
    # SDK task user is a canonical predicate, not the physical authorization
    # subject of a business Space. Match the indexed adapters.
    scope_params = {key: value for key, value in provider._freeze_window_params(query).items() if key != "username"}
    params = provider._params(BaseTraceQuery(query.space, query.user, scope_params), config, agent_filter=False)
    wire = {key: value for key, value in params.items()
            if key in {"from", "to", "space_id", "user", "agent_product"}}
    # Only internal callers with an already verified subject may narrow the
    # physical scope. Public username remains a canonical SDK dimension.
    if physical_user is not None:
        wire["user"] = physical_user
    wire.update(agent_uuid=query.agent_uuid or "", projection_context=projection_context(prices), raw="true" if raw else "false")
    read = lambda: _read_snapshot(provider, query, client, wire, raw=raw, compact=compact,
                                  physical_user=physical_user, include_metrics=include_metrics, prices=prices)
    if compact:
        from .snapshot_inflight import read_once, scope_key
        # Each HTTP consumer retains and processes the shared snapshot. Admit
        # followers too, even when only the owner performs a storage read.
        admit_query()
        return read_once(scope_key(client, wire), read)
    return read()


def _read_snapshot(provider, query, client, wire, *, raw, compact, physical_user, include_metrics, prices):
    try:
        payload = client.get(PATH + ('/metrics-window' if compact else '/window'), params=wire)
    except TraceQueryServiceError as exc:
        if exc.code == 3019:
            if compact:
                return query_snapshot_window(provider, query, raw=True, physical_user=physical_user, include_metrics=True, _prices=prices)
            return None
        raise
    if payload.get("supported") is False:
        if not isinstance(payload.get("reason"), str) or not payload["reason"]:
            invalid("unsupported response needs a reason")
        if compact:
            return query_snapshot_window(provider, query, raw=True, physical_user=physical_user, include_metrics=True, _prices=prices)
        return None
    meta, rows, details = payload.get("meta"), payload.get("rows"), payload.get("raw")
    if (payload.get("supported") is not True or payload.get("complete") is not True
            or payload.get("contract") != (METRICS_CONTRACT if compact else CONTRACT) or not isinstance(meta, dict)
            or not isinstance(rows, list) or not isinstance(details, list)):
        invalid("missing complete snapshot contract")
    if compact and meta.get('representation') != 'metrics-v1':
        invalid('missing metrics representation')
    for key in ("from", "to", "space_id", "user", "agent_product", "agent_uuid", "projection_context"):
        if key in {"from", "to"}:
            if type(meta.get(key)) is not int or meta[key] != wire.get(key):
                invalid("time window differs from request")
        elif (meta.get(key) or "") != (wire.get(key) or ""):
            invalid("authorized scope/context differs from request")
    if type(meta.get("raw")) is not bool or raw and (not meta["raw"] or rows):
        invalid("raw recovery used derived summaries")
    omitted = meta.get("omittedAgentTraceCount")
    if type(omitted) is not int or omitted < 0 or not query.agent_uuid and omitted:
        invalid("invalid omitted Agent count")
    counts = {"traceCount": len(rows) + len(details) + omitted,
              "projectedTraceCount": len(rows) + omitted, "rawTraceCount": len(details)}
    for key, value in counts.items():
        if type(meta.get(key)) is not int or meta[key] != value:
            invalid("inconsistent " + key)
    if counts["traceCount"] > MAX_TRACES:
        invalid("snapshot exceeds Trace limit")
    if (type(meta.get("spanCount")) is not int or meta["spanCount"] < 0
            or type(meta.get("rawSpanCount")) is not int or meta["rawSpanCount"] < 0):
        invalid("invalid Span count")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            invalid("summary is not an object")
        _validate_summary(row, include_content=not compact)
        if compact:
            from ..metrics_projection import ROW_FIELDS
            if set(row) - ROW_FIELDS - {'sourceRevision'}:
                invalid('unexpected full content in metrics representation')
        identifier = row.get("trace_id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            invalid("duplicate or missing Trace identity")
        seen.add(identifier)
        if (row.get("resource.attributes.aivision.space.id") != str(query.space.uuid)
                or query.agent_uuid and row.get("resource.attributes.aivision.agent.id") != query.agent_uuid
                or type(row.get("sourceRevision")) is not int or row["sourceRevision"] < 1):
            invalid("summary scope/revision mismatch")
        if (type(row.get("span_count")) is not int or row["span_count"] < 1
                or type(row.get("status_code")) is not int or row["status_code"] not in (0, 1, 2)):
            invalid("invalid summary counts or status")
        for field in ("input", "output", "total", "cacheReadInput", "cacheWriteInput", "reasoning"):
            value = row["usage"].get(field)
            if value is not None and (type(value) is not int or value < 0):
                invalid("invalid canonical Token usage")
        if row.get("total_tokens") != row["usage"].get("total"):
            invalid("inconsistent canonical Token total")
        if not compact and not valid_preview(row.get("ioPreview"), row.get("rootObservationId")):
            invalid("invalid summary preview")
        if any(not isinstance(row.get(key), list) or any(not isinstance(item, str) for item in row[key])
               for key in ("_traceTags", "_sceneValues")):
            invalid("missing canonical supplements")
        if not wire["from"] * 1000000 <= int(row["start_time_unix_nano"]) < wire["to"] * 1000000:
            invalid("summary outside selected window")
    selected, span_count = [], 0
    for detail in details:
        if not isinstance(detail, dict) or not isinstance(detail.get("spans"), list) or not detail["spans"]:
            invalid("incomplete raw Trace group")
        identifier, spans = detail.get("traceId"), detail["spans"]
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            invalid("duplicate raw Trace identity")
        seen.add(identifier)
        span_ids = [span.get("span_id") for span in spans if isinstance(span, dict)]
        if (len(span_ids) != len(spans) or any(not isinstance(value, str) or not value for value in span_ids)
                or len(set(span_ids)) != len(spans)):
            invalid("duplicate or missing raw Span identity")
        if any(span.get("trace_id", identifier) != identifier for span in spans):
            invalid("Span belongs to another Trace")
        for span in spans:
            start = timestamp_ns(span.get("start_time_unix_nano"))
            if start is None or not wire["from"] * 1000000 <= start < wire["to"] * 1000000:
                invalid("raw Span outside selected window")
        span_count += len(spans)
        provider._verify_scope(query, spans, verify_agent=False)
        identities = {str(provider._span_resource(span).get("aivision.agent.id") or "") for span in spans}
        if query.agent_uuid:
            if len(identities) != 1 or "" in identities:
                invalid("missing or mixed Agent identity")
            if identities != {query.agent_uuid}:
                continue
        provider._verify_scope(query, spans)
        selected.append(detail)
    if span_count != meta["rawSpanCount"]:
        invalid("incomplete raw Span collection")
    known_span_count = span_count + sum(row["span_count"] for row in rows)
    if (meta["spanCount"] < known_span_count or not omitted and meta["spanCount"] != known_span_count):
        invalid("incomplete total Span collection")
    fields = _decode(query.query_params.get('fields'))
    if isinstance(fields, str):
        fields = [key.strip() for key in fields.split(',')]
    content_fields = {'attributes.gen_ai.input.messages', 'attributes.gen_ai.output.messages',
                      'attributes.gen_ai.tool.call.arguments', 'attributes.gen_ai.tool.call.result'}
    if (not compact and not raw and any(row.get('_contentComplete') is False for row in rows)
            and (not fields or content_fields.intersection(fields) or metrics_need_content(query.query_params))):
        return query_snapshot_window(provider, query, raw=True, physical_user=physical_user,
                                     include_metrics=include_metrics, _prices=prices)
    if include_metrics and any(not isinstance(row.get('_metrics'), dict)
                               or row['_metrics'].get('version') != 'native-trace-metrics-v1' for row in rows):
        # Older summaries can still serve lists. Metrics require complete facts;
        # use one bounded raw snapshot while the managed projector upgrades them.
        return query_snapshot_window(provider, query, raw=True, physical_user=physical_user, include_metrics=True, _prices=prices)
    try:
        window = NativeTraceWindow.from_summary_rows(rows, include_previews=not compact, include_metrics=include_metrics)
        window.operation_previews = {row['trace_id']: row['_operation_previews'] for row in rows
                                     if '_operation_previews' in row}
        if include_metrics and any(not wire['from'] * 1000000 <= timestamp_ns(item['startTime']) < wire['to'] * 1000000
                                   for trace in window.traces for item in trace['observations']):
            invalid('metrics observation outside selected window')
        raw_window = NativeTraceWindow(selected, project_id=str(query.space.uuid),
                                       prices=prices, include_previews=True)
    except (ValueError, TypeError, KeyError) as exc:
        invalid(str(exc))
    window.rows.extend(raw_window.rows)
    window.traces.extend(raw_window.traces)
    window.observation_values.extend(raw_window.observation_values)
    window.fields.update(raw_window.fields)
    generation = meta.get('maintenance_generation')
    if rows and type(generation) is int and 1 <= generation < 2**63:
        # One-RR metrics/list reads need no split-step fence. Later hydration
        # must bind this private proof to every independently read source.
        window.maintenance_generation = generation
    result_meta = {"complete": True, "totalItems": len(window.rows), "from": wire["from"], "to": wire["to"],
                   "queryMode": "raw_snapshot" if meta["raw"] else "hybrid_snapshot", "queryContract": METRICS_CONTRACT if compact else CONTRACT,
                   "projectedTraceCount": len(rows), "rawTraceCount": len(details),
                   "timeBoundary": "fromInclusiveToExclusive"}
    return window, result_meta
