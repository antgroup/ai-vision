"""Count-only PowerContext adaptation; never reconstruct MCP calls or memory bodies."""

import math

from .labs_mcp import _mcp_span_info, _span_time
from .labs_scan import tool_span_outcome

SCOPES = {"powercontext.client", "powercontext.server"}
SERVICE_OPERATIONS = {"prepare_context", "capture_content_source"}
LAYER_RANK = {"dependency": 0, "transport": 1, "application": 2}


def identity(span):
    return str(span.get("trace_id") or ""), str(span.get("span_id") or "")


def native_operation(span):
    a = span.get("attributes") or {}
    if not isinstance(a, dict):
        return None
    scope = a.get("aivision.otlp.scope.name")
    if not isinstance(scope, str) or scope not in SCOPES:
        return None
    op, unit = (
        a.get("powercontext.operation.name"),
        a.get("powercontext.operation.unit"),
    )
    if (
        isinstance(op, str)
        and op in SERVICE_OPERATIONS
        and isinstance(unit, str)
        and unit in LAYER_RANK
        or op == "memory.search"
        and unit == "stage"
    ):
        return op, unit
    return None


def service_layer(span):
    a = span.get("attributes") or {}
    if (
        isinstance(a, dict)
        and isinstance(a.get("aivision.otlp.scope.name"), str)
        and a.get("aivision.otlp.scope.name") in SCOPES
        and isinstance(a.get("powercontext.operation.name"), str)
        and isinstance(a.get("powercontext.operation.unit"), str)
        and a.get("powercontext.operation.unit") in LAYER_RANK
    ):
        return a["powercontext.operation.name"], a["powercontext.operation.unit"]
    return None


def mcp_connector(span):
    a = span.get("attributes") or {}
    return (
        isinstance(a, dict)
        and a.get("aivision.otlp.scope.name") == "powercontext.server"
        and a.get("powercontext.operation.unit") == "transport"
        and a.get("powercontext.transport") == "mcp"
    )


def native_outcome(span):
    outcome = (span.get("attributes") or {}).get("powercontext.operation.outcome")
    if tool_span_outcome(span) == "error" or outcome == "failure":
        return "error"
    return "ok" if outcome == "success" else "unknown"


def native_memory_calls(spans, agent_product=""):
    by_id = {identity(s): s for s in spans}

    def ancestors(span):
        seen = {identity(span)}
        while span.get("parent_span_id"):
            key = str(span.get("trace_id") or ""), str(span["parent_span_id"])
            if key in seen or key not in by_id:
                return
            seen.add(key)
            span = by_id[key]
            yield span

    def anchor(span):
        """Only climb one SDK -> transport -> application invocation chain."""
        op, unit = service_layer(span)
        selected, rank = span, LAYER_RANK[unit]
        for parent in ancestors(span):
            if mcp_connector(parent):
                if LAYER_RANK["transport"] >= rank:
                    break
                rank = LAYER_RANK["transport"]
                continue
            layer = service_layer(parent)
            if layer is None:
                continue
            parent_op, parent_unit = layer
            if parent_op != op or LAYER_RANK[parent_unit] >= rank:
                break
            selected, rank = parent, LAYER_RANK[parent_unit]
        return selected

    groups, stages, mcp_owned = {}, {}, set()
    for span in by_id.values():
        layer = service_layer(span)
        if layer and layer[0] in SERVICE_OPERATIONS and not mcp_connector(span):
            canonical = anchor(span)
            groups.setdefault(identity(canonical), (canonical, []))[1].append(span)
        if mcp_connector(span):
            # A direct SDK -> MCP bridge can be the only observed lower layer.
            for parent in ancestors(span):
                owner = service_layer(parent)
                if owner:
                    if owner[0] in SERVICE_OPERATIONS and owner[1] == "dependency":
                        mcp_owned.add(identity(parent))
                    break
        if native_operation(span) == ("memory.search", "stage"):
            for parent in ancestors(span):
                owner = service_layer(parent)
                if owner:
                    if owner[0] == "prepare_context":
                        stages.setdefault(identity(anchor(parent)), []).append(span)
                    break

    calls = []
    for key, (span, members) in groups.items():
        owner_path = [
            node for member in members for node in [member, *ancestors(member)]
        ]
        if (
            key in mcp_owned
            or any(_mcp_span_info(p) for p in owner_path)
            or any(
                (p.get("attributes") or {}).get("powercontext.transport") == "mcp"
                for p in owner_path
            )
        ):
            continue
        a = span.get("attributes") or {}
        status = native_outcome(span)
        searches = stages.get(key, [])
        evidence, category, execution = None, None, "unknown"
        search_duration = None
        if len(searches) == 1:
            search = searches[0]
            sa = search.get("attributes") or {}
            limit, mode = (
                sa.get("powercontext.memory.search.limit"),
                sa.get("powercontext.memory.search.mode"),
            )
            skipped = (
                sa.get("powercontext.memory.search.memory_present") is False
                or limit == 0
            )
            ran = not skipped and isinstance(mode, str) and bool(mode.strip())
            execution = "not_run" if skipped else "completed" if ran else "unknown"
            if ran:
                stage_start = _span_time(search.get("start_time_unix_nano"))
                stage_end = _span_time(search.get("end_time_unix_nano"))
                if stage_start and stage_end and stage_end >= stage_start:
                    search_duration = round(
                        (stage_end - stage_start).total_seconds() * 1000
                    )
                category = "search"
                count = sa.get("powercontext.memory.search.result_count")
                member_ids = {identity(member) for member in members}
                path_error = any(
                    native_outcome(node) == "error"
                    for node in [search, *ancestors(search)]
                    if node is search or identity(node) in member_ids
                )
                if (
                    status == "ok"
                    and native_outcome(search) == "ok"
                    and not path_error
                    and type(count) is int
                    and count >= 0
                ):
                    evidence = {"type": "search", "hits": count, "entries": []}
        start, end = (
            _span_time(span.get("start_time_unix_nano")),
            _span_time(span.get("end_time_unix_nano")),
        )
        duration = (
            (end - start).total_seconds() * 1000
            if start and end and end >= start
            else None
        )
        if duration is not None and not math.isfinite(duration):
            duration = None
        calls.append(
            {
                "serverId": "powercontext",
                "toolName": a["powercontext.operation.name"],
                "traceId": str(span.get("trace_id") or ""),
                "spanId": str(span.get("span_id") or ""),
                "agentProduct": str(span.get("agent_product") or agent_product or ""),
                "status": status,
                "durationMs": duration,
                "startMs": start.timestamp() * 1000 if start else 0,
                "timestamp": start.isoformat().replace("+00:00", "Z") if start else "",
                "lastError": str(a.get("powercontext.error.category") or "")
                if status == "error"
                else "",
                "argsSummary": "",
                "args": {},
                "evidence": evidence,
                "isMemory": True,
                "nativeCategory": category,
                "searchDurationMs": search_duration,
                "transport": "http",
                "executionState": execution,
            }
        )
    return calls
