"""Sessions over canonical Trace summaries; selection never trims the turns.

Only callers requesting this version use it. The older Span-grain session API
remains independent. The same aggregator consumes verified raw Trace summaries
and versioned storage summaries, so switching off acceleration keeps semantics.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
import json
import math

from .native_query import ENVIRONMENT_SEMANTICS, USER_IDENTITY_SEMANTICS, NativeTraceWindow
from .semantics.observations import iso_timestamp
from .semantics.usage import FIELDS as USAGE_FIELDS, combine_cost_coverage
from .types import TraceQueryServiceError
from .query_limits import MIN_TOTAL_TOKENS, MIN_DURATION_MS, MAX_LIST_THRESHOLD

SEMANTICS = "canonical-trace-v1"
SESSION_PREVIEW_VERSION = "native-session-preview-v1"
PARAMS = {"session_semantics", "session_keyword", "session_status", "min_trace_count",
          "min_total_tokens", "min_duration_ms", "session_id", "sessionId", "env", "username", "tags",
          "keyword", "searchType", "search_type", "page", "limit", "from", "to", "fromTimestamp",
          "toTimestamp", "agent_product", "agent_uuid", "source_system", "sourceSystem", "orderBy", "order_by"}


def invalid(message):
    raise TraceQueryServiceError("Invalid canonical session query: " + message, 3027)


def prepare_params(params):
    params = dict(params)
    unknown = set(params) - PARAMS
    if unknown:
        invalid("unsupported parameters: " + ", ".join(sorted(unknown)))
    if params.get("session_semantics") != SEMANTICS:
        invalid("unsupported session_semantics")
    for field, default, minimum, maximum in (("page", 1, 1, 2**31-1), ("limit", 20, 1, 50),
                                            ("min_trace_count", None, 1, MAX_LIST_THRESHOLD),
                                            ("min_total_tokens", None, MIN_TOTAL_TOKENS, MAX_LIST_THRESHOLD)):
        value = params.get(field, default)
        if value in (None, ""):
            if default is None:
                params.pop(field, None)
                continue
            value = default
        if isinstance(value, bool):
            invalid(field + " requires an integer")
        try:
            number = Decimal(str(value))
            if not number.is_finite() or number != number.to_integral_value() or not minimum <= number <= maximum:
                raise ValueError()
            params[field] = int(number)
        except (ValueError, ArithmeticError):
            invalid(f"{field} requires an integer from {minimum} to {maximum}; clear the filter to view all values")
    value = params.get("min_duration_ms")
    if value not in (None, ""):
        try:
            if isinstance(value, bool) or not math.isfinite(float(value)) or not MIN_DURATION_MS <= float(value) <= MAX_LIST_THRESHOLD:
                raise ValueError()
            params["min_duration_ms"] = float(value)
        except (TypeError, ValueError, OverflowError):
            invalid(f"min_duration_ms must be from {MIN_DURATION_MS} to {MAX_LIST_THRESHOLD} ms; clear the filter to view all values")
    else:
        params.pop("min_duration_ms", None)
    for field in ("session_keyword", "session_id", "sessionId", "env", "username", "keyword"):
        value = params.get(field)
        if value in (None, ""):
            params.pop(field, None)
        elif not isinstance(value, str) or len(value) > (4096 if field == "keyword" else 255):
            invalid(field + " requires a bounded string")
        # Recorded SDK dimensions and literal substrings can contain boundary
        # whitespace. Preserve facet values and exact Session IDs verbatim.
    if params.get("session_status") not in (None, "", "error", "ok", "unset"):
        invalid("session_status must be error, ok or unset")
    search = params.get("searchType", params.get("search_type", "input"))
    if search not in ("input", "output"):
        invalid("searchType must be input or output")
    params["searchType"] = search
    tags = params.get("tags", [])
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except ValueError:
            invalid("tags must be a JSON array")
    if not isinstance(tags, list) or len(tags) > 100 or any(not isinstance(tag, str) or not tag or len(tag) > 255 for tag in tags):
        invalid("tags must contain at most 100 nonempty strings")
    params["tags"] = list(dict.fromkeys(tags))
    order = params.get("orderBy", params.get("order_by", "end_time_unix_nano.desc"))
    if order not in ("end_time_unix_nano.desc", "last_activity.desc"):
        invalid("only last_activity.desc ordering is supported")
    params.pop("order_by", None)
    params["orderBy"] = "end_time_unix_nano.desc"
    return params


def _nano(row, field):
    value = row.get(field)
    return int(value) if value is not None else None


def _sum_complete(values):
    return sum(values) if values and all(value is not None for value in values) else None


def aggregate_trace_usage(traces):
    """Disjoint Trace totals are already deduplicated; preserve their unknowns."""
    usages = [row["usage"] for row in traces if row.get("coverage", {}).get("billableCalls", 0) > 0]
    result = {field: _sum_complete([usage.get(field) for usage in usages]) for field in USAGE_FIELDS}
    result.update(inputIncludesCache=True if result["input"] is not None else None,
                  source={field: "aggregate_canonical_traces" for field in USAGE_FIELDS if result[field] is not None},
                  raw={}, observationCount=sum(usage.get("observationCount", 0) for usage in usages),
                  unknownReason=None if result["total"] is not None else "usage_missing")
    if any(usage.get("unknownReason") == "ambiguous_nested_generations" for usage in usages):
        result["unknownReason"] = "ambiguous_nested_generations"
    costs = [usage.get("cost", {}) for usage in usages]
    cost = {"total": None, "input": None, "output": None, "source": "aggregate_canonical_traces",
            "currency": "USD", "reason": "cost_missing", "pricingVersion": None}
    if costs and all(item.get("total") is not None and item.get("currency") == "USD" for item in costs):
        for field in ("total", "input", "output"):
            if all(item.get(field) is not None for item in costs):
                cost[field] = format(sum((Decimal(item[field]) for item in costs), Decimal(0)).normalize(), "f")
        cost["reason"] = None
    result["cost"] = cost
    result["costCoverage"] = combine_cost_coverage(usage.get("costCoverage", {}) for usage in usages)
    return result


def trace_matches(row, params, excluded=None):
    for dim, field in (("env", "environment"), ("username", "userId")):
        if dim != excluded and params.get(dim) and row.get(field) != params[dim]:
            return False
    if excluded != "tags" and params.get("tags") and not set(params["tags"]).intersection(row.get("_traceTags", [])):
        return False
    if params.get("keyword"):
        field = "attributes.gen_ai." + params["searchType"] + ".messages"
        if params["keyword"].lower() not in str(row.get(field) or "").lower():
            return False
    return True


def session_summary(sid, traces, matched_ids):
    traces = sorted(traces, key=lambda row: (_nano(row, "start_time_unix_nano"), row["trace_id"]))
    starts = [_nano(row, "start_time_unix_nano") for row in traces]
    ends = [_nano(row, "end_time_unix_nano") for row in traces]
    start = min(starts)
    end = max(ends) if all(value is not None for value in ends) else None
    activity = max(ended if ended is not None else began for began, ended in zip(starts, ends))
    codes = [row["status_code"] for row in traces]
    code = 2 if 2 in codes else 1 if all(value == 1 for value in codes) else 0
    trace_status_counts = {"unset": codes.count(0), "ok": codes.count(1), "error": codes.count(2)}
    usage = aggregate_trace_usage(traces)
    coverage = {field: sum(row.get("coverage", {}).get(field, 0) for row in traces)
                for field in ("billableCalls", "knownUsageCalls", "knownTokens", "knownCostCalls")}
    users = sorted({row["userId"] for row in traces if row.get("userId")})
    tags = sorted({tag for row in traces for tag in row.get("_traceTags", [])})
    first, last = traces[0], traces[-1]
    input_preview, output_preview = first["ioPreview"], last["ioPreview"]
    preview = {"version": SESSION_PREVIEW_VERSION, "format": "message-content-v1", "complete": True,
               "input": input_preview["input"], "output": output_preview["output"],
               "inputTruncated": input_preview["inputTruncated"], "outputTruncated": output_preview["outputTruncated"],
               "inputTraceId": first["trace_id"], "outputTraceId": last["trace_id"]}
    return {"session_id": sid, "created_at": iso_timestamp(start), "last_activity": iso_timestamp(activity),
            # Retain established session wire fields carrying RFC3339 text.
            "start_time_unix_nano": iso_timestamp(start), "end_time_unix_nano": iso_timestamp(end),
            "duration_ms": (end-start)/1000000 if end is not None else None,
            "trace_count": len(traces), "span_count": sum(row["span_count"] for row in traces),
            "session_status": {0: "unset", 1: "ok", 2: "error"}[code], "status_code": code,
            "trace_status_counts": trace_status_counts,
            "user_id": users[0] if len(users) == 1 else None, "username": users[0] if len(users) == 1 else None,
            "user_ids": users, "tags": tags, "environment": first["environment"],
            "space_id": first.get("resource.attributes.aivision.space.id"),
            "agent_product": first.get("resource.attributes.ant.agent.product"),
            "input_tokens": usage["input"], "output_tokens": usage["output"], "total_tokens": usage["total"],
            "usage": usage, "coverage": coverage, "costCoverage": usage["costCoverage"], "matched_trace_ids": list(matched_ids),
            "matched_trace_count": len(matched_ids), "ioPreview": preview,
            "attributes.gen_ai.input.messages": preview["input"], "attributes.gen_ai.output.messages": preview["output"]}


def aggregate_matches(row, params):
    if params.get("session_id", params.get("sessionId")) and row["session_id"] != params.get("session_id", params.get("sessionId")):
        return False
    if params.get("session_keyword") and params["session_keyword"] not in row["session_id"]:
        return False
    if params.get("session_status") and row["session_status"] != params["session_status"]:
        return False
    for param, field in (("min_trace_count", "trace_count"), ("min_total_tokens", "total_tokens"), ("min_duration_ms", "duration_ms")):
        if params.get(param) is not None and (row[field] is None or row[field] < params[param]):
            return False
    return True


class CanonicalSessionWindow:
    def __init__(self, rows):
        self.groups = defaultdict(list)
        for row in rows:
            if row.get("session_id"):
                self.groups[row["session_id"]].append(row)

    @classmethod
    def from_details(cls, details, *, project_id, prices=None):
        window = NativeTraceWindow(details, project_id=project_id, prices=prices, include_previews=True)
        return cls([{**row, "_traceTags": trace["tags"]} for row, trace in zip(window.rows, window.traces)])

    def list(self, raw_params):
        params = prepare_params(raw_params)
        rows, eligible = [], {}
        for sid, traces in self.groups.items():
            row = session_summary(sid, traces, [])
            if not aggregate_matches(row, params):
                continue
            eligible[sid] = traces
            matched = [trace["trace_id"] for trace in sorted(traces, key=lambda item: (_nano(item, "start_time_unix_nano"), item["trace_id"]))
                       if trace_matches(trace, params)]
            if matched:
                rows.append(session_summary(sid, traces, matched))
        rows.sort(key=lambda row: row["session_id"])
        rows.sort(key=lambda row: row["last_activity"], reverse=True)
        facets = {}
        for dim, field in (("env", "environment"), ("username", "userId"), ("tags", "_traceTags")):
            values = defaultdict(set)
            for sid, traces in eligible.items():
                for trace in traces:
                    if trace_matches(trace, params, excluded=dim):
                        candidates = trace.get(field, []) if dim == "tags" else [trace.get(field)]
                        for value in candidates:
                            if value:
                                values[value].add(sid)
            facets[dim] = [{"value": value, "count": len(sessions)} for value, sessions in sorted(values.items())][:500]
        page, limit, total = params["page"], params["limit"], len(rows)
        return {"data": rows[(page-1)*limit:page*limit], "facets": facets,
                "meta": {"page": page, "limit": limit, "totalItems": total, "totalPages": math.ceil(total/limit),
                         "windowTotalItems": len(self.groups), "complete": True, "sessionSemantics": SEMANTICS,
                         "environmentSemantics": ENVIRONMENT_SEMANTICS, "userIdentitySemantics": USER_IDENTITY_SEMANTICS,
                         "ioPreviewComplete": True, "timeBoundary": "fromInclusiveToExclusive"}}


def comparable_result(result):
    value = deepcopy(result)
    value["meta"] = {key: result.get("meta", {}).get(key) for key in (
        "page", "limit", "totalItems", "totalPages", "windowTotalItems", "complete", "sessionSemantics")}
    return value
