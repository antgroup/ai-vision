"""Framework span views over the same complete native window as trace metrics."""
from collections import Counter
from heapq import nlargest
import json

from apps.core.services.model_pricing import get_space_model_prices

from apps.core.query.errors import AgentQueryInvalidPayloadError
from apps.core.services.agent_routing import resolve_space_agent_for_query
from apps.core.services.observation_subject_service import require_agent_subject
from apps.core.trace_query.routing import TRACE_QUERY_ACTIVE_AGENT_STATUSES
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.semantics.observations import iso_timestamp, timestamp_ns
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.signoz.query_control import query_mode
from apps.core.trace_query.signoz.read_model import PATH
from apps.core.trace_query.signoz.read_model_projection import build_projection, ProjectionDeferred
from apps.core.trace_query.signoz.snapshot_window import query_snapshot_window
from apps.core.trace_query.types import BaseTraceQuery, TraceProviderNotConfiguredError, TraceQueryServiceError

FRAMEWORK_QUERY_KEYS = frozenset({
    "framework.overview.event_stream", "framework.events.timeseries",
})
STEPS = {"PT5M": 300, "PT10M": 600, "PT1H": 3600, "P1D": 86400}
OPERATION_KINDS = {"GENERATION": "llm", "EMBEDDING": "llm", "TOOL": "tool"}


def _complete_window(provider, query, *, physical_user, raw, prices):
    snapshot = query_snapshot_window(provider, query, raw=raw, physical_user=physical_user, include_metrics=True, _prices=prices)
    if snapshot is not None:
        return snapshot[0]
    result = provider.collect_traces(query, _prices=prices)
    if result.get("meta", {}).get("complete") is not True:
        raise TraceQueryServiceError("Incomplete framework trace window", 3035)
    try:
        return NativeTraceWindow(result["data"], project_id=str(query.space.uuid),
                                 prices=prices)
    except ValueError as exc:
        raise TraceQueryServiceError(f"Invalid native trace: {exc}", 3035) from exc


def _operations(window, start, end):
    for trace in window.traces:
        for observation in trace["observations"]:
            kind = OPERATION_KINDS.get(observation["type"])
            at = timestamp_ns(observation["startTime"])
            if kind and start * 1000000 <= at < end * 1000000:
                yield trace, observation, kind, at


def _latest_operations(window, start, end, limit):
    return nlargest(limit, _operations(window, start, end),
                    key=lambda event: (event[3], event[0]["id"], event[1]["id"]))


def _hydrate_operations(provider, query, window, selected, *, physical_user, prices):
    """Read only selected clean Traces, bound to their original source revision.

    Dirty Traces already carry full observations in the complete snapshot. If a
    clean source changed, discard every hydrated row and restart one complete
    raw snapshot; never combine contents from different source revisions.
    """
    if selected and all(observation.get('_eventPreview') is True for _, observation, *_ in selected):
        return selected
    # Older engines can lack typed SQL while still serving complete compact
    # snapshots. Their revision-bound previews avoid whole-Trace hydration too.
    preview_maps = getattr(window, 'operation_previews', {})
    if preview_maps:
        from apps.core.trace_query.signoz.operation_page import _observation
        from apps.core.trace_query.native_query import _status
        projected = []
        for trace, observation, kind, at in selected:
            previews = preview_maps.get(trace['id'], {})
            if not isinstance(previews, dict) or len(previews) > 200:
                raise TraceQueryServiceError('Invalid snapshot operation previews', 3035)
            preview = previews.get(observation['id'])
            if preview is not None and all(observation.get(key) is None or isinstance(observation[key], str)
                                           for key in ('model', 'provider')):
                facts = {key: observation['usage'][key] for key in
                         ('input', 'output', 'total', 'cacheReadInput', 'cacheWriteInput', 'reasoning')}
                facts.update(model=observation.get('model'), provider=observation.get('provider'), ambiguous=False)
                item = _observation({'facts': facts, 'preview': preview}, observation['id'], observation['type'], at)
                if _status(item) != _status(observation):
                    raise TraceQueryServiceError('Operation preview status differs from snapshot', 3035)
                observation = item
            projected.append((trace, observation, kind, at))
        selected = projected
        if all(item[1].get('_eventPreview') is True for item in selected):
            return selected
    summaries = {row["trace_id"]: row for row in window.rows if "sourceRevision" in row}
    identifiers = list(dict.fromkeys(trace["id"] for trace, *_ in selected if trace["id"] in summaries))
    if not identifiers:
        return selected
    generation = getattr(window, 'maintenance_generation', None)
    if type(generation) is not int or not 1 <= generation < 2**63:
        # Old complete-window contracts can safely serve their own snapshot,
        # but cannot bind independently hydrated contents across a reset.
        return None
    client, config = provider._context(query)
    scope = provider._params(query, config, agent_filter=False)
    if physical_user is not None:
        scope["user"] = physical_user
    restored = {}
    for offset in range(0, len(identifiers), 100):
        chunk = identifiers[offset:offset+100]
        try:
            response = client.get(PATH + "/batch-snapshot", params={
                "space_id": str(query.space.uuid), "trace_ids": json.dumps(chunk, separators=(",", ":")),
            })
        except TraceQueryServiceError as exc:
            if exc.code == 3019:  # Older storage does not expose this capability.
                return None
            raise
        if not isinstance(response, dict):
            raise TraceQueryServiceError("Invalid event source snapshot batch", 3035)
        if response.get("supported") is False and isinstance(response.get("reason"), str) and response["reason"]:
            return None
        data = response.get("data")
        if (response.get("supported") is not True or response.get("contract") != "native-trace-source-batch-v1"
                or not isinstance(data, list) or len(data) != len(chunk)):
            raise TraceQueryServiceError("Invalid event source snapshot batch", 3035)
        for identifier, source in zip(chunk, data):
            if not isinstance(source, dict) or source.get("trace_id") != identifier:
                raise TraceQueryServiceError("Event source snapshot belongs to another Trace", 3035)
            if source.get("supported") is False and isinstance(source.get("reason"), str) and source["reason"]:
                return None
            if (source.get("supported") is not True or source.get("complete") is not True
                    or source.get("contract") != "native-trace-source-v1"):
                raise TraceQueryServiceError("Incomplete event source snapshot contract", 3035)
            revision = source.get("source_revision")
            if type(revision) is not int or revision < 1:
                raise TraceQueryServiceError("Invalid event source revision", 3035)
            source_generation = source.get('maintenance_generation')
            if type(source_generation) is not int or not 1 <= source_generation < 2**63:
                raise TraceQueryServiceError('Invalid event source maintenance generation', 3035)
            if source_generation != generation:
                return None
            if revision != summaries[identifier]["sourceRevision"]:
                return None
            if (source.get("scope_valid") is not True
                    or any(not isinstance(source.get(field), str) or not source[field]
                           for field in ("source_user_id", "source_agent_product"))
                    or (scope.get("user") and source["source_user_id"] != scope["user"])
                    or (scope.get("agent_product") and source["source_agent_product"] != scope["agent_product"])):
                raise TraceQueryServiceError("Event source differs from authorized scope", 3035)
            spans = source.get("spans")
            if (not isinstance(spans, list) or not spans
                    or any(not isinstance(span, dict) or span.get("trace_id", identifier) != identifier for span in spans)):
                raise TraceQueryServiceError("Invalid event source spans", 3035)
            starts = [timestamp_ns(span.get("start_time_unix_nano")) for span in spans]
            if (source.get("span_count") != summaries[identifier]["span_count"]
                    or any(at is None or not query.get_param("from") * 1000000 <= at < query.get_param("to") * 1000000
                           for at in starts)):
                raise TraceQueryServiceError("Event source differs from its complete snapshot", 3035)
            provider._verify_scope(query, spans)
            try:
                # Reuse the projector's complete source validation without
                # publishing anything or altering maintenance state.
                build_projection(source, space_id=str(query.space.uuid), prices=prices)
            except ProjectionDeferred:
                return None
            full = NativeTraceWindow([{"traceId": identifier, "spans": spans}], project_id=str(query.space.uuid),
                                     prices=prices)
            restored[identifier] = {item["id"]: item for item in full.traces[0]["observations"]}
    hydrated = []
    for trace, observation, kind, at in selected:
        full = restored.get(trace["id"], {}).get(observation["id"], observation)
        if ("input" not in full or full["type"] != observation["type"]
                or timestamp_ns(full["startTime"]) != at
                or ('parentObservationId' in observation and full["parentObservationId"] != observation["parentObservationId"])):
            raise TraceQueryServiceError("Event changed without a source revision", 3035)
        hydrated.append((trace, full, kind, at))
    return hydrated


def query_framework(context, prepared, agent_product):
    params = prepared["params"]
    if set(params) - {"agent_uuid", "limit", "time_step"}:
        raise AgentQueryInvalidPayloadError(message="Unknown framework query parameter")
    limit = params.get("limit", 200)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise AgentQueryInvalidPayloadError(message="Framework limit must be between 1 and 200")
    step_name = params.get("time_step", "PT1H")
    step = STEPS.get(step_name) if isinstance(step_name, str) else None
    if step is None:
        raise AgentQueryInvalidPayloadError(message="Unsupported framework time_step")
    agent_uuid = params.get("agent_uuid") or str(context["agent"].uuid)
    if not isinstance(agent_uuid, str):
        raise AgentQueryInvalidPayloadError(message="agent_uuid must be a string")
    start, end = prepared["from_ms"], prepared["to_ms"]
    # The provider resolves and rechecks the selected Agent in this Space,
    # enforces every span's trusted scope and refuses incomplete/over-limit scans.
    query_params = {"from": start, "to": end, "agent_product": agent_product, "agent_uuid": agent_uuid}
    physical_user = None
    if prepared.get("scope") == "personal":
        # Use the same authorized Space/product/instance selection as native
        # provider routing. The product-default context can belong to another
        # instance and must not supply the selected Agent's personal subject.
        selected = resolve_space_agent_for_query(
            context["space"], agent_product, agent_uuid=agent_uuid,
            active_statuses=TRACE_QUERY_ACTIVE_AGENT_STATUSES,
        )
        if selected is None:
            raise TraceProviderNotConfiguredError("Agent query route not found", 3011)
        physical_user = require_agent_subject(selected)
        query_params["username"] = physical_user
    prices = get_space_model_prices(context["space"])
    provider = SignozTraceProvider()
    query = BaseTraceQuery(context["space"], context.get("user"), query_params)
    timeseries = prepared["query_key"] == "framework.events.timeseries"
    if timeseries:
        from apps.core.trace_query.signoz.statistics_aggregate import query_metrics_aggregate
        aggregate_query = BaseTraceQuery(query.space, query.user, {**query.query_params, 'bucket': 'minute'})
        aggregate = query_metrics_aggregate(provider, aggregate_query, physical_user=physical_user,
                                            operations_only=True, _prices=prices)
        if aggregate is not None:
            width = step * 1000000000
            counts = Counter()
            for item in aggregate['operations']:
                counts[(item['start_ns'] // width * width, OPERATION_KINDS[item['type']])] += item['count']
            rows = [{'time': iso_timestamp(at), 'kind': kind, 'count': count}
                    for (at, kind), count in sorted(counts.items())]
            return {'columns': [{'key': key, 'type': 'number' if key == 'count' else 'string'}
                                for key in rows[0].keys()] if rows else [], 'rows': rows}
    page = None
    if not timeseries:
        from apps.core.trace_query.signoz.operation_page import query_operation_page
        page = query_operation_page(provider, query, limit=limit, physical_user=physical_user, _prices=prices)
    window = (page[0] if page is not None else
              _complete_window(provider, query, physical_user=physical_user, raw=query_mode() == "raw", prices=prices))
    rows = []
    counts = Counter()
    width = step * 1000000000
    if timeseries:
        for _, _, kind, at in _operations(window, start, end):
            counts[(at // width * width, kind)] += 1
    else:
        selected = page[1] if page is not None else _latest_operations(window, start, end, limit)
        hydrated = _hydrate_operations(provider, query, window, selected, physical_user=physical_user, prices=prices)
        if hydrated is None:
            window = _complete_window(provider, query, physical_user=physical_user, raw=True, prices=prices)
            hydrated = _latest_operations(window, start, end, limit)
        for trace, observation, kind, _ in hydrated:
            usage = observation["usage"]
            rows.append({
                "trace_id": trace["id"], "span_id": observation["id"],
                "time": observation["startTime"], "name": observation["name"], "kind": kind,
                "cost_ms": observation["latency"] * 1000 if observation["latency"] is not None else None,
                "model": observation["model"], "input_tokens": usage["input"],
                "output_tokens": usage["output"], "cache_read_tokens": usage["cacheReadInput"],
                "total_tokens": usage["total"], "input": observation["input"], "output": observation["output"],
                "status_code": observation["otelStatus"].get("code"),
                **({'content_mode': 'preview', 'input_truncated': observation['input_truncated'],
                    'output_truncated': observation['output_truncated']} if observation.get('_eventPreview') else {}),
            })
    if timeseries:
        rows = [{"time": iso_timestamp(at), "kind": kind, "count": count}
                for (at, kind), count in sorted(counts.items())]
    fields = rows[0].keys() if rows else []
    return {"columns": [{"key": key, "type": "number" if key in {
        "cost_ms", "input_tokens", "output_tokens", "cache_read_tokens", "total_tokens", "count",
    } else "string"} for key in fields], "rows": rows}
