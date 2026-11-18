"""Langfuse v1 read facade over AI Vision telemetry (never a Langfuse proxy).

Wire baseline: langfuse-python v4.16.0, commit
0bc5897b0e2402e50baf8aa004a1adbffd333a61, generated v1 clients.
"""

from __future__ import annotations

import json
import math
import re
import time
from decimal import Decimal, InvalidOperation

from django.conf import settings

from apps.core.trace_query.semantics.observations import project_trace, timestamp_ns, iso_timestamp
from apps.core.trace_query.semantics.usage import ALIASES
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import BaseTraceQuery, TraceQueryServiceError

TRACE_FIELDS = {
    "id",
    "timestamp",
    "name",
    "input",
    "output",
    "sessionId",
    "release",
    "version",
    "userId",
    "metadata",
    "tags",
    "public",
    "environment",
}
OBS_FIELDS = {
    "id",
    "traceId",
    "type",
    "name",
    "startTime",
    "endTime",
    "completionStartTime",
    "model",
    "modelParameters",
    "input",
    "output",
    "version",
    "metadata",
    "level",
    "statusMessage",
    "parentObservationId",
    "environment",
    "promptName",
    "promptVersion",
    "latency",
    "timeToFirstToken",
}
METRIC_COLUMNS = {
    "latency",
    "inputTokens",
    "outputTokens",
    "totalTokens",
    "tokens",
    "inputCost",
    "outputCost",
    "totalCost",
}
TRACE_COLUMNS = (
    TRACE_FIELDS
    | METRIC_COLUMNS
    | {
        "bookmarked",
        "level",
        "warningCount",
        "errorCount",
        "defaultCount",
        "debugCount",
        "scores_avg",
        "scores",
        "score_categories",
        "score_booleans",
    }
)
OBS_COLUMNS = (
    OBS_FIELDS
    | METRIC_COLUMNS
    | {"tokensPerSecond", "userId", "traceName", "traceEnvironment", "traceTags"}
)
OPS = {
    "datetime": {">", "<", ">=", "<="},
    "string": {"=", "contains", "does not contain", "starts with", "ends with"},
    "number": {"=", ">", "<", ">=", "<="},
    "stringOptions": {"any of", "none of"},
    "categoryOptions": {"any of", "none of"},
    "arrayOptions": {"any of", "none of", "all of"},
    "stringObject": {"=", "contains", "does not contain", "starts with", "ends with"},
    "numberObject": {"=", ">", "<", ">=", "<="},
    "booleanObject": {"=", "<>"},
    "boolean": {"=", "<>"},
    "null": {"is null", "is not null"},
}


def _float(value):
    return float(value) if value is not None else None


def observation_wire(item):
    result = {key: item.get(key) for key in OBS_FIELDS}
    usage = item["usage"]
    cost = usage["cost"]
    # The frozen v1 legacy Usage model requires integers. Unknown stays absent in
    # usageDetails and stays null in the canonical native contract.
    result["usage"] = {
        field: usage[field] if usage[field] is not None else 0
        for field in ("input", "output", "total")
    }
    result["usage"].update(
        unit="TOKENS",
        inputCost=_float(cost["input"]),
        outputCost=_float(cost["output"]),
        totalCost=_float(cost["total"]),
    )
    result["usageDetails"] = {
        field: usage[field]
        for field in ("input", "output", "total")
        if usage[field] is not None
    }
    for field, key in (
        ("cacheReadInput", "cache_read_input_tokens"),
        ("cacheWriteInput", "cache_creation_input_tokens"),
        ("reasoning", "reasoning_tokens"),
    ):
        if usage[field] is not None:
            result["usageDetails"][key] = usage[field]
    for field, components in (
        ("input", ("cacheReadInput", "cacheWriteInput")),
        ("output", ("reasoning",)),
    ):
        if field in result["usageDetails"]:
            exclusive = usage[field] - sum(usage[key] or 0 for key in components)
            if exclusive >= 0:
                result["usageDetails"][field] = exclusive
            else:
                # Conflicting counts are not repaired into a plausible charge.
                result["usageDetails"].pop(field)
    raw_usage = usage.get("raw", {}).get("langfuse.observation.usage_details")
    if isinstance(raw_usage, str):
        try:
            raw_usage = json.loads(raw_usage)
        except ValueError:
            raw_usage = None
    if (
        isinstance(raw_usage, dict)
        and raw_usage
        and all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in raw_usage.values()
        )
        and any(field in raw_usage for field in ("input", "output"))
    ):
        # usageDetails is an open bucket map. Preserve SDK-provided names and
        # absent fields, including one-sided/incomplete exclusive measurements.
        # Explicit inclusive counters override only their own field so they
        # cannot drop an unrelated raw bucket or be overwritten by it.
        details = dict(raw_usage)
        wire_keys = {
            "input": "input",
            "output": "output",
            "total": "total",
            "cacheReadInput": "cache_read_input_tokens",
            "cacheWriteInput": "cache_creation_input_tokens",
            "reasoning": "reasoning_tokens",
        }
        for field, aliases in ALIASES.items():
            source = usage.get("source", {}).get(field)
            if (
                source is None
                or source.startswith("langfuse.")
                or source == "derived_input_plus_output"
            ):
                continue
            for alias in aliases:
                details.pop(alias, None)
            key = wire_keys[field]
            if key in result["usageDetails"]:
                details[key] = result["usageDetails"][key]
        result["usageDetails"] = details
    result["costDetails"] = {
        field: _float(value)
        for field, value in cost.get("details", {}).items()
        if value is not None
    }
    if not result["costDetails"]:
        result["costDetails"] = {
            field: _float(cost[field])
            for field in ("input", "output")
            if cost[field] is not None
        }
    if cost["total"] is not None:
        result["costDetails"]["total"] = _float(cost["total"])
    result.update(
        promptId=None,
        modelId=None,
        inputPrice=None,
        outputPrice=None,
        totalPrice=None,
        calculatedInputCost=_float(cost["input"]),
        calculatedOutputCost=_float(cost["output"]),
        calculatedTotalCost=_float(cost["total"]),
    )
    return result


def trace_wire(item, *, detail=False, fields=None):
    result = {key: item.get(key) for key in TRACE_FIELDS}
    result.update(
        htmlPath=f"/project/{item['projectId']}/traces/{item['id']}",
        latency=item["latency"],
        totalCost=_float(item["usage"]["cost"]["total"]),
        scores=[],
        observations=[
            observation_wire(value) if detail else value["id"]
            for value in item["observations"]
        ],
    )
    if fields is not None:
        if "io" not in fields:
            for key in ("input", "output", "metadata"):
                result.pop(key, None)
        if "observations" not in fields:
            result["observations"] = []
        if "metrics" not in fields:
            result.update(latency=-1, totalCost=-1)
    return result


def _values(params, key):
    if hasattr(params, "getlist"):
        values = params.getlist(key)
    else:
        value = params.get(key)
        values = (
            value if isinstance(value, list) else [value] if value is not None else []
        )
    return values


def _page(params):
    try:
        page, limit = int(params.get("page", 1)), int(params.get("limit", 50))
    except (ValueError, TypeError):
        raise ValueError("page and limit must be integers") from None
    if page < 1 or not 1 <= limit <= 100:
        raise ValueError("page must be positive and limit must be between 1 and 100")
    return page, limit


def _fields(params):
    if params.get("fields") is None:
        return None
    fields = set(str(params["fields"]).split(","))
    if not fields <= {"core", "io", "scores", "observations", "metrics"}:
        raise ValueError("Unsupported fields group")
    return fields | {"core"}


def _numeric(value):
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (ValueError, InvalidOperation, TypeError):
        return None


def _datetime(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+-]\d\d:\d\d)", value
    ):
        return None
    try:
        return timestamp_ns(value)
    except (ValueError, OverflowError):
        return None


def _column_types(column):
    if column in {"timestamp", "startTime", "endTime", "completionStartTime"}:
        return {"datetime", "null"}
    if column in METRIC_COLUMNS | {
        "timeToFirstToken",
        "tokensPerSecond",
        "promptVersion",
        "warningCount",
        "errorCount",
        "defaultCount",
        "debugCount",
        "scores_avg",
        "scores",
    }:
        return {"number", "null"}
    if column in {"metadata", "score_categories", "score_booleans"}:
        return {
            "stringObject",
            "numberObject",
            "booleanObject",
            "categoryOptions",
            "null",
        }
    if column in {"tags", "traceTags"}:
        return {"arrayOptions", "null"}
    if column in {"public", "bookmarked"}:
        return {"boolean", "null"}
    return {"string", "stringOptions", "null"}


def _column(item, column):
    if column in {"inputTokens", "outputTokens", "totalTokens", "tokens"}:
        return item["usage"][
            {
                "inputTokens": "input",
                "outputTokens": "output",
                "totalTokens": "total",
                "tokens": "total",
            }[column]
        ]
    if column in {"inputCost", "outputCost", "totalCost"}:
        return item["usage"]["cost"][column.removesuffix("Cost")]
    if column == "tokensPerSecond":
        output = item["usage"]["output"]
        return (
            output / item["latency"] if output is not None and item["latency"] else None
        )
    if column == "bookmarked":
        return False
    if column in {"score_categories", "score_booleans"}:
        return {}
    if column in {"scores_avg", "scores"}:
        return None
    if column in {"warningCount", "errorCount", "defaultCount", "debugCount"}:
        level = column.removesuffix("Count").upper()
        return sum(value["level"] == level for value in item.get("observations", []))
    if column == "level" and "observations" in item:
        return max(
            (value["level"] for value in item["observations"]),
            key=lambda value: ["DEBUG", "DEFAULT", "WARNING", "ERROR"].index(value),
            default="DEFAULT",
        )
    return item.get(column)


def parse_filters(params, kind):
    raw = params.get("filter")
    if raw is None:
        return None
    try:
        filters = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("filter must be a JSON array") from None
    if not isinstance(filters, list):
        raise ValueError("filter must be a JSON array")
    columns = TRACE_COLUMNS if kind == "trace" else OBS_COLUMNS
    for condition in filters:
        if not isinstance(condition, dict) or condition.get("column") not in columns:
            raise ValueError("Unsupported filter column")
        typ = condition.get("type")
        if typ not in _column_types(condition["column"]):
            raise ValueError("Filter type does not match column")
        if condition.get("operator") not in OPS.get(typ, set()):
            raise ValueError("Unsupported filter type or operator")
        if typ.endswith("Object") or typ == "categoryOptions":
            if not isinstance(condition.get("key"), str):
                raise ValueError("Nested filter key is required")
        if typ != "null" and "value" not in condition:
            raise ValueError("Filter value is required")
        value = condition.get("value")
        if typ in {"number", "numberObject"} and _numeric(value) is None:
            raise ValueError("Numeric filter requires a finite number")
        if typ == "datetime" and _datetime(value) is None:
            raise ValueError("Datetime filter requires an ISO timestamp")
        if typ in {
            "arrayOptions",
            "stringOptions",
            "categoryOptions",
        } and not isinstance(value, list):
            raise ValueError("Options filter requires an array")
        if typ in {"boolean", "booleanObject"} and not isinstance(value, bool):
            raise ValueError("Boolean filter requires a boolean")
        if typ in {"string", "stringObject"} and not isinstance(value, str):
            raise ValueError("String filter requires a string")
    return filters


def _matches(item, condition):
    typ, op = condition["type"], condition["operator"]
    actual, expected = _column(item, condition["column"]), condition.get("value")
    if typ.endswith("Object") or typ == "categoryOptions":
        actual = actual.get(condition["key"]) if isinstance(actual, dict) else None
    if typ == "null":
        return (actual is None) == (op == "is null")
    if typ == "datetime":
        actual, expected = timestamp_ns(actual), timestamp_ns(expected)
    elif typ in {"number", "numberObject"}:
        actual, expected = _numeric(actual), _numeric(expected)
    if typ in {"arrayOptions", "stringOptions", "categoryOptions"}:
        values = (
            actual
            if isinstance(actual, list)
            else [actual] if actual is not None else []
        )
        if op == "all of":
            return all(value in values for value in expected)
        found = any(value in values for value in expected)
        return found if op == "any of" else not found
    if op == "<>":
        return actual != expected
    if actual is None:
        return False
    if op in {"contains", "does not contain", "starts with", "ends with"}:
        actual = str(actual)
        return {
            "contains": lambda: expected in actual,
            "does not contain": lambda: expected not in actual,
            "starts with": lambda: actual.startswith(expected),
            "ends with": lambda: actual.endswith(expected),
        }[op]()
    return {
        "=": lambda: actual == expected,
        ">": lambda: actual > expected,
        "<": lambda: actual < expected,
        ">=": lambda: actual >= expected,
        "<=": lambda: actual <= expected,
    }[op]()


def _legacy_matches(item, params, kind):
    keys = (
        ("name", "userId", "sessionId", "version", "release")
        if kind == "trace"
        else (
            (
                "name",
                "userId",
                "type",
                "traceId",
                "level",
                "parentObservationId",
                "version",
            )
            if kind == "observation"
            else ()
        )
    )
    if any(
        params.get(key) is not None and item.get(key) != params[key] for key in keys
    ):
        return False
    environments = _values(params, "environment")
    if environments and item.get("environment") not in environments:
        return False
    if kind == "trace" and any(
        value not in item["tags"] for value in _values(params, "tags")
    ):
        return False
    start_key, end_key, field = (
        ("fromStartTime", "toStartTime", "startTime")
        if kind == "observation"
        else (
            "fromTimestamp",
            "toTimestamp",
            "createdAt" if kind == "session" else "timestamp",
        )
    )
    for key, lower in ((start_key, True), (end_key, False)):
        value = params.get(key)
        if value is not None:
            bound = _datetime(value)
            if bound is None:
                raise ValueError(f"{key} must be an ISO timestamp")
            actual = timestamp_ns(item[field])
            if (lower and actual < bound) or (not lower and actual >= bound):
                return False
    return True


def select(items, params, kind):
    page, limit = _page(params)
    filters = parse_filters(params, kind) if kind != "session" else None
    # Validate bounds even when no row is available.
    if filters is None:
        for key in ("fromTimestamp", "toTimestamp", "fromStartTime", "toStartTime"):
            if params.get(key) is not None and _datetime(params[key]) is None:
                raise ValueError(f"{key} must be an ISO timestamp")
    selected = (
        [
            item
            for item in items
            if all(_matches(item, condition) for condition in filters)
        ]
        if filters is not None
        else [item for item in items if _legacy_matches(item, params, kind)]
    )
    default_field = {
        "trace": "timestamp",
        "observation": "startTime",
        "session": "createdAt",
    }[kind]
    order = (
        params.get("orderBy", f"{default_field}.desc")
        if kind == "trace"
        else f"{default_field}.desc"
    )
    field, separator, direction = str(order).rpartition(".")
    allowed = (
        {
            "id",
            "timestamp",
            "name",
            "userId",
            "release",
            "version",
            "public",
            "bookmarked",
            "sessionId",
        }
        if kind == "trace"
        else {default_field}
    )
    if not separator or field not in allowed or direction not in {"asc", "desc"}:
        raise ValueError("Unsupported orderBy")
    selected.sort(
        key=lambda item: (str(_column(item, field) or ""), item["id"]),
        reverse=direction == "desc",
    )
    total = len(selected)
    return selected[(page - 1) * limit : page * limit], {
        "page": page,
        "limit": limit,
        "totalItems": total,
        "totalPages": math.ceil(total / limit),
    }


class NativeLangfuseService:
    def __init__(self, *, space, agent, user=None, provider=None, prices=None):
        self.space, self.agent, self.user = space, agent, user
        self.provider, self.prices = provider or SignozTraceProvider(), prices
        self.project_id = str(agent.uuid if agent is not None else space.uuid)

    def _windows(self):
        # A fixed history origin is an operator assertion about retained data,
        # never a sliding recent-days default. This image's inner query guard is
        # 31 days even when the outer max_query_range is configured higher.
        origin = getattr(settings, "AIVISION_NATIVE_TRACE_HISTORY_START", "")
        start_ns = _datetime(origin)
        if start_ns is None or start_ns < 0:
            raise TraceQueryServiceError(
                "Native tracing requires AIVISION_NATIVE_TRACE_HISTORY_START to cover all retained history",
                3038,
            )
        skew = getattr(settings, "AIVISION_NATIVE_TRACE_FUTURE_SKEW_SECONDS", 300)
        maximum = getattr(settings, "AIVISION_NATIVE_TRACE_MAX_WINDOWS", 120)
        if (
            not isinstance(skew, int)
            or not 0 <= skew <= 86400
            or not isinstance(maximum, int)
            or maximum < 1
        ):
            raise TraceQueryServiceError(
                "Invalid native tracing history configuration", 3038
            )
        start = start_ns // 1000000
        end = time.time_ns() // 1000000 + skew * 1000
        step = 31 * 86400000
        if start >= end or math.ceil((end - start) / step) > maximum:
            raise TraceQueryServiceError(
                "Native tracing history exceeds configured window capacity", 3038
            )
        while start < end:
            stop = min(start + step, end)
            yield start, stop
            start = stop

    def _params(self, window, trace_id=None, session_id=None):
        result = {"from": window[0], "to": window[1]}
        if self.agent is not None:
            result.update(
                agent_product=self.agent.agent_product, agent_uuid=str(self.agent.uuid)
            )
        if trace_id is not None:
            result["trace_id"] = trace_id
        if session_id is not None:
            result["session_id"] = session_id
        return result

    def _traces(self, trace_id=None, session_id=None):
        merged = {}
        windows = tuple(self._windows())

        def collect(window, trace_id=None, session_id=None):
            payload = self.provider.collect_traces(
                BaseTraceQuery(
                    self.space, self.user, self._params(window, trace_id, session_id)
                )
            )
            if not payload.get("meta", {}).get("complete"):
                raise TraceQueryServiceError("Incomplete trace scan", 3035)
            identifiers = set()
            for detail in payload["data"]:
                identifier = str(detail.get("traceId") or detail.get("trace_id") or "")
                if not identifier:
                    raise TraceQueryServiceError(
                        "Trace identity missing in storage response", 3035
                    )
                identifiers.add(identifier)
                accumulated = merged.setdefault(
                    identifier, {"traceId": identifier, "spans": {}}
                )
                for span in detail.get("spans", []):
                    span_id = (
                        span.get("span_id") or span.get("spanId") or span.get("id")
                    )
                    if not span_id:
                        raise TraceQueryServiceError(
                            "Observation identity missing in storage response", 3035
                        )
                    accumulated["spans"][span_id] = span
            if len(merged) > 10000:
                raise TraceQueryServiceError(
                    "Native tracing exceeds the 10000-trace scan capacity", 3038
                )
            return identifiers

        selected = {window: collect(window, trace_id, session_id) for window in windows}
        if session_id is not None:
            # A session attribute may exist on only one span/window of a trace.
            # Select its IDs by SID, then join their SID-free history fragments.
            candidates = tuple(merged)
            for window in windows:
                for identifier in candidates:
                    if identifier not in selected[window]:
                        collect(window, identifier)
        return [
            project_trace(
                {**detail, "spans": list(detail["spans"].values())},
                project_id=self.project_id,
                prices=self.prices,
            )
            for detail in merged.values()
        ]

    def traces(self, params, trace_id=None):
        fields = _fields(params)
        if trace_id is not None:
            matches = [
                item for item in self._traces(trace_id) if item["id"] == trace_id
            ]
            if len(matches) != 1:
                raise TraceQueryServiceError("Trace not found", 3019)
            return trace_wire(matches[0], detail=True, fields=fields)
        _page(params)
        parse_filters(params, "trace")
        selected = self._selected_trace_page(params, fields)
        if selected is not None:
            return selected
        rows, meta = select(self._traces(), params, "trace")
        return {
            "data": [trace_wire(item, fields=fields) for item in rows],
            "meta": meta,
        }

    def _selected_trace_page(self, params, fields):
        """Select simple v1 pages from compact facts, then read only their content.

        Other v1 filter/order shapes and multi-window history retain the bounded
        compatibility scan. Do not map a narrow timestamp predicate to a Span
        window: that would omit earlier ancestors or later child observations.
        """
        from apps.core.trace_query.signoz.query_control import query_mode
        from apps.core.trace_query.signoz.snapshot_window import query_snapshot_window
        from apps.core.trace_query.signoz.selected_sources import read_selected_sources
        if (self.agent is None or query_mode() == 'raw'
                or set(params) - {'page', 'limit', 'orderBy', 'fromTimestamp', 'toTimestamp', 'fields'}
                or params.get('orderBy', 'timestamp.desc') not in {'timestamp.asc', 'timestamp.desc', 'id.asc', 'id.desc'}):
            return None
        windows = tuple(self._windows())
        if len(windows) != 1:
            return None
        query = BaseTraceQuery(self.space, self.user, self._params(windows[0]))
        snapshot = query_snapshot_window(self.provider, query, include_metrics=True, _prices=self.prices)
        if snapshot is None:
            return None
        window, _ = snapshot
        candidates = [{'id': row['trace_id'], 'timestamp': iso_timestamp(int(row['start_time_unix_nano']))}
                      for row in window.rows]
        rows, meta = select(candidates, params, 'trace')
        identifiers = [row['id'] for row in rows]
        details = read_selected_sources(self.provider, query, window, identifiers)
        if details is None:
            return None
        traces = [project_trace(detail, project_id=self.project_id, prices=self.prices) for detail in details]
        if any(trace['id'] != row['id'] or timestamp_ns(trace['timestamp']) != timestamp_ns(row['timestamp'])
               for trace, row in zip(traces, rows)):
            raise TraceQueryServiceError('Selected Trace differs from compact snapshot', 3035)
        return {'data': [trace_wire(trace, fields=fields) for trace in traces], 'meta': meta}

    def observations(self, params, observation_id=None):
        _page(params)
        parse_filters(params, "observation")
        # Structured filters replace all legacy parameters. Detail reads are
        # selected by observation ID; only list reads may narrow by traceId.
        trace_id = (
            params.get("traceId")
            if observation_id is None and params.get("filter") is None
            else None
        )
        rows = []
        for trace in self._traces(trace_id):
            for item in trace["observations"]:
                item.update(
                    traceName=trace["name"],
                    traceEnvironment=trace["environment"],
                    traceTags=trace["tags"],
                    userId=trace["userId"],
                )
                rows.append(item)
        if observation_id is not None:
            matches = [item for item in rows if item["id"] == observation_id]
            if len(matches) != 1:
                raise TraceQueryServiceError("Observation not found", 3019)
            return observation_wire(matches[0])
        rows, meta = select(rows, params, "observation")
        return {"data": [observation_wire(item) for item in rows], "meta": meta}

    def sessions(self, params, session_id=None):
        _page(params)
        sessions = {}
        for trace in sorted(
            self._traces(session_id=session_id),
            key=lambda item: (item["timestamp"], item["id"]),
        ):
            sid = trace["sessionId"]
            if not sid:
                continue
            session = sessions.setdefault(
                sid,
                {
                    "id": sid,
                    "createdAt": trace["timestamp"],
                    "projectId": self.project_id,
                    "environment": trace["environment"],
                    "traces": [],
                },
            )
            session["traces"].append({key: trace[key] for key in TRACE_FIELDS})
        if session_id is not None:
            if session_id not in sessions:
                raise TraceQueryServiceError("Session not found", 3019)
            return sessions[session_id]
        rows, meta = select(list(sessions.values()), params, "session")
        return {
            "data": [
                {key: value for key, value in item.items() if key != "traces"}
                for item in rows
            ],
            "meta": meta,
        }
