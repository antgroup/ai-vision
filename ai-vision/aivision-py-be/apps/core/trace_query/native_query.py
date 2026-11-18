"""One in-process query contract over a verified complete native trace window.

The bounded scan is a correctness-first fallback for stores without aggregation.
It never interprets a page as a full window or an unknown measurement as zero.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

from .semantics.observations import io_attribute, timestamp_ns
from .native_projection import canonical_trace, io_preview
from .semantics.usage import aggregate_usage, indexed_model_prices, reported_usage, usage_coverage
from .types import TraceQueryServiceError

ENVIRONMENT_SEMANTICS = "root-span-resource-v1"
USER_IDENTITY_SEMANTICS = "trace-sdk-user-id-v1"
TOTAL_TOKEN_FIELDS = {"total_tokens", "attributes.gen_ai.usage.total_tokens"}

FIELDS = {
    "trace_id": "string", "name": "string", "session_id": "string", "userId": "string",
    "environment": "string", "environmentSource": "string",
    "usage": "json", "reportedUsage": "json", "coverage": "json", "costCoverage": "json", "ioPreview": "json", "inputEvidence": "json",
    "start_time_unix_nano": "nanoseconds", "end_time_unix_nano": "nanoseconds",
    "status_code": "integer", "span_count": "integer", "input_tokens": "integer",
    "output_tokens": "integer", "total_tokens": "integer", "duration_ms": "number",
    "attributes.gen_ai.span.kind": "string", "attributes.gen_ai.request.model": "string",
    "attributes.gen_ai.usage.input_tokens": "integer", "attributes.gen_ai.usage.output_tokens": "integer",
    "attributes.gen_ai.usage.total_tokens": "integer", "attributes.gen_ai.input.messages": "json",
    "attributes.gen_ai.output.messages": "json", "attributes.langfuse.trace.tags": "json",
    "resource.attributes.ant.agent.env": "string", "resource.attributes.ant.agent.scene": "string",
    "resource.attributes.aivision.agent.id": "string", "attributes.ant.username": "string",
    "attributes.gen_ai.tool.call.name": "string", "attributes.gen_ai.tool.call.arguments": "json",
    "attributes.gen_ai.tool.call.result": "json", "attributes.gen_ai.operation.name": "string",
    "attributes.gen_ai.provider.name": "string",
}
OPERATORS = ("=", "!=", "in", "not_in", ">", ">=", "<", "<=", "LIKE", "IS_NULL", "IS_NOT_NULL")
TRACE_FIELDS = {key for key in FIELDS if not key.startswith(("attributes.", "resource.attributes."))} | {
    "attributes.gen_ai.input.messages", "attributes.gen_ai.output.messages", "attributes.langfuse.trace.tags",
    "attributes.gen_ai.usage.input_tokens", "attributes.gen_ai.usage.output_tokens", "attributes.gen_ai.usage.total_tokens",
}
PARAMS = {"fields", "filters", "orderBy", "order_by", "page", "limit", "from", "to", "fromTimestamp", "toTimestamp",
          "agent_product", "agent_uuid", "searchType", "search_type", "keyword", "input_keyword", "trace_id", "traceId",
          "session_id", "sessionId", "tag", "tags", "env", "scene", "username", "entry_only", "source_system", "sourceSystem",
          "bucket", "groupBy", "purpose", "include_cost"}


def error(message):
    raise TraceQueryServiceError(message, 3027)


def validate_params(params):
    if "purpose" in params and params["purpose"] != "diagnosis_input":
        error("Unknown trace query purpose")
    unknown = set(params) - PARAMS
    if unknown:
        error("Unknown trace query parameter: " + ", ".join(sorted(unknown)))
    from .query_limits import validate_trace_lower_bounds
    validate_trace_lower_bounds(params.get("filters"))


def _decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def requests_input_evidence(params):
    fields = _decode(params.get("fields"))
    if isinstance(fields, str):
        fields = [field.strip() for field in fields.split(",")]
    return isinstance(fields, list) and "inputEvidence" in fields


def _input_evidence(trace):
    """Lossless input sources with the context needed to reject derived/tool IO.

    Materialized only on explicit projection, never stored in summary rows.
    Keep original values/masking; do not turn presentation messages into evidence.
    """
    if "observations" not in trace:
        error("Input evidence requires complete original Trace observations")
    keys = ("gen_ai.input.messages", "langfuse.observation.input", "input.value",
            "aivision.request.input", "gen_ai.tool.call.arguments", "langfuse.trace.input",
            "aivision.normalization.sources", "gen_ai.operation.name",
            "langfuse.observation.type", "gen_ai.span.kind", "openinference.span.kind")
    result = []
    for observation in trace["observations"]:
        attributes = {key: observation["attributes"][key] for key in keys if key in observation["attributes"]}
        source_key = io_attribute(attributes, "input")
        provenance = _decode(attributes.get("aivision.normalization.sources"))
        if isinstance(provenance, dict) and "gen_ai.input.messages" in provenance:
            source_key = next((key for key in ("langfuse.observation.input", "input.value", "aivision.request.input")
                               if attributes.get(key) is not None), source_key)
        source = {"id": observation["id"], "name": observation["name"],
                  "input": _decode(attributes.get(source_key)), "attributes": attributes,
                  "inputSource": source_key,
                  "startTime": observation["startTime"],
                  "isRoot": observation["id"] == trace["rootObservationId"]}
        result.append(source)
        if "langfuse.trace.input" in attributes:
            # This is an independent original source, even when its value
            # equals the observation's derived message projection. The full
            # observation aliases and provenance remain on the source above.
            trace_attributes = {key: value for key, value in attributes.items()
                                if key != "gen_ai.input.messages"}
            result.append({**source, "id": observation["id"] + ":trace",
                           "input": _decode(attributes["langfuse.trace.input"]),
                           "attributes": trace_attributes,
                           "inputSource": "langfuse.trace.input"})
    return result


def costs_requested(params):
    value = params.get("include_cost", True)
    if type(value) is bool:
        return value
    if isinstance(value, str) and value in {"true", "false"}:
        return value == "true"
    error("include_cost must be true or false")


def without_costs(value):
    """Explicit numeric-only contract; omitted prices are never unknown/zero."""
    if isinstance(value, dict):
        return {key: without_costs(item) for key, item in value.items()
                if key not in {"cost", "costCoverage", "knownCostCalls"}}
    if isinstance(value, list):
        return [without_costs(item) for item in value]
    return value


def normalized_agent_params(params):
    """Remove only a predicate proven redundant by the explicit Agent scope."""
    params = dict(params)
    agent = params.get("agent_uuid")
    filters = _decode(params.get("filters"))
    if isinstance(agent, str) and agent and isinstance(filters, list):
        params["filters"] = [item for item in filters if not (
            isinstance(item, dict) and item.get("field") == "resource.attributes.aivision.agent.id"
            and item.get("op", item.get("operator", "=")) in ("=", "eq")
            and item.get("value") == agent)]
    return params


def _matches_like(value, pattern):
    """Match literal fragments separated by %, without regex backtracking."""
    parts = pattern.split("%")
    if len(parts) == 1:
        return value == pattern
    if not value.startswith(parts[0]) or not value.endswith(parts[-1]):
        return False
    start, end = len(parts[0]), len(value) - len(parts[-1])
    if start > end:
        return False
    for part in parts[1:-1]:
        if not part:
            continue
        position = value.find(part, start, end)
        if position < 0:
            return False
        start = position + len(part)
    return True


def _status(observation):
    # Trace status follows the root observation only: a failed child span
    # (e.g. a retried tool call) must not fail a trace that finished OK.
    code = str(observation["otelStatus"].get("code", "0")).upper()
    if code in {"2", "ERROR", "STATUS_CODE_ERROR"}:
        return 2
    return 1 if code in {"1", "OK", "STATUS_CODE_OK"} else 0


class NativeTraceWindow:
    @classmethod
    def from_summary_rows(cls, rows, *, include_previews=False, include_metrics=False):
        """Validate/filter an already canonical page without rebuilding spans.

        Only the versioned SigNoz read-model adapter uses this path. Its private
        supplements retain trace tags and any-observation scene facets; they
        never become public row fields. Other per-observation predicates must
        use the legacy complete-window path.
        """
        window = cls([], project_id="", include_previews=include_previews)
        for item in rows:
            row = {key: value for key, value in item.items() if not key.startswith("_")}
            if not include_previews:
                row.pop("ioPreview", None)
            window.rows.append(row)
            if include_metrics:
                from .metrics_projection import restore_metrics
                window.traces.append(restore_metrics(item))
            else:
                window.traces.append({"tags": item["_traceTags"]})
            window.observation_values.append({
                "resource.attributes.ant.agent.scene": item["_sceneValues"],
                "resource.attributes.aivision.agent.id": [row.get("resource.attributes.aivision.agent.id")],
            })
        return window

    def __init__(self, details, *, project_id, prices=None, include_previews=False):
        prices = indexed_model_prices(prices)
        self.traces = [canonical_trace(detail, project_id=project_id, prices=prices) for detail in details]
        self.include_previews = include_previews
        self.prices = prices
        self.fields = dict(FIELDS)
        self.rows = []
        self.observation_values = []
        for trace in self.traces:
            root = next(o for o in trace["observations"] if o["id"] == trace["rootObservationId"])
            attrs = {"attributes." + k: v for k, v in root["attributes"].items()}
            resource = {"resource.attributes." + k: v for k, v in root["resourceAttributes"].items()}
            last_end = max((timestamp_ns(o["endTime"]) for o in trace["observations"] if o["endTime"]), default=None)
            row = {**attrs, **resource, "trace_id": trace["id"], "name": trace["name"], "session_id": trace["sessionId"],
                   "start_time_unix_nano": str(timestamp_ns(trace["timestamp"])),
                   "end_time_unix_nano": str(last_end) if last_end is not None else None,
                   "status_code": _status(root), "span_count": len(trace["observations"]),
                   "duration_ms": trace["latency"] * 1000 if trace["latency"] is not None else None,
                   "environment": trace["environment"], "environmentSource": trace["environmentSource"],
                   "reportedUsage": trace["reportedUsage"], "coverage": trace["coverage"],
                   "costCoverage": trace["usage"]["costCoverage"],
                   "input_tokens": trace["usage"]["input"], "output_tokens": trace["usage"]["output"], "total_tokens": trace["usage"]["total"],
                   "attributes.gen_ai.input.messages": trace["input"], "attributes.gen_ai.output.messages": trace["output"],
                   "attributes.gen_ai.usage.input_tokens": trace["usage"]["input"],
                   "attributes.gen_ai.usage.output_tokens": trace["usage"]["output"],
                   "attributes.gen_ai.usage.total_tokens": trace["usage"]["total"], "usage": trace["usage"],
                   "rootObservationId": trace["rootObservationId"], "rootSelectionReason": trace["rootSelectionReason"],
                   "missingParentIds": trace["missingParentIds"]}
            # Keep native list's established JSON-text projection for UI/CLI;
            # canonical traces above retain structured JSON for the v1 API.
            for key in ("attributes.gen_ai.input.messages", "attributes.gen_ai.output.messages", "attributes.langfuse.trace.tags"):
                if isinstance(row.get(key), (dict, list)):
                    row[key] = json.dumps(row[key], ensure_ascii=False)
            values_by_field = defaultdict(list)
            ordered = [root, *[o for o in trace["observations"] if o["id"] != root["id"]]]
            for observation in ordered:
                for prefix, values in (("attributes.", observation["attributes"]), ("resource.attributes.", observation["resourceAttributes"])):
                    for key, value in values.items():
                        field = prefix + key
                        kind = "json" if isinstance(value, (dict, list)) else "integer" if isinstance(value, int) and not isinstance(value, bool) else "number" if isinstance(value, float) else "string"
                        self.fields.setdefault(field, kind)
                        values_by_field[field].append(value)
                        if field not in TRACE_FIELDS and row.get(field) is None:
                            row[field] = value
            # The public username facet predates native resources. Collectors
            # stamp the authoritative subject on the resource; keep that alias
            # queryable through the established field as well.
            usernames = values_by_field.get("resource.attributes.ant.username", [])
            if usernames:
                values_by_field["attributes.ant.username"] = usernames
                row["attributes.ant.username"] = next((value for value in usernames if value is not None), None)
            # Public identity follows the canonical SDK trace update, which can
            # live on a child observation. An explicit null/empty SDK update
            # clears it; only traces with no SDK identity attribute at all use
            # the legacy collector identity. Keep that raw alias independent.
            has_sdk_identity = any(
                key in observation["attributes"]
                for observation in trace["observations"]
                for key in ("gen_ai.user.id", "langfuse.user.id", "user.id")
            )
            # Legacy public projection is root-first across observations. A
            # child's collector resource must not replace an identity reported
            # by the root; the established raw alias above retains its own
            # resource-precedence query behavior for compatibility.
            legacy_user_id = next((
                value for observation in ordered
                for value in (observation["resourceAttributes"].get("ant.username"),
                              observation["attributes"].get("ant.username"))
                if value not in (None, "")
            ), None)
            row["userId"] = trace["userId"] if has_sdk_identity else legacy_user_id
            if include_previews:
                row["ioPreview"] = io_preview(trace)
            self.observation_values.append(values_by_field)
            self.rows.append(row)

    def schema(self):
        return {"fields": [{"key": key, "type": kind, "nullable": True,
                            "unit": "ns" if kind == "nanoseconds" else "ms" if key == "duration_ms" else None,
                            "filterable": key != "inputEvidence", "projectable": True, "sortable": kind != "json",
                            "operators": [] if key == "inputEvidence" else list(OPERATORS),
                            "projection": "trace" if key in TRACE_FIELDS else "firstNonNullObservationRootFirst",
                            "filterScope": "trace" if key in TRACE_FIELDS else "anyObservationPerPredicate",
                            "negativeFilterScope": "trace" if key in TRACE_FIELDS else "allObservations"} for key, kind in self.fields.items()],
                "timeBoundary": "fromInclusiveToExclusive", "maxPageSize": 200, "maxScanTraces": 10000}

    def _field(self, field):
        if not isinstance(field, str) or field not in self.fields:
            error(f"Unknown trace field: {field}")
        return self.fields[field]

    def _value(self, field, value):
        kind = self._field(field)
        if value is None:
            return None
        if field == "status_code" and isinstance(value, str):
            value = {"OK": 1, "ERROR": 2, "UNSET": 0}.get(value.upper(), value)
        if kind in {"integer", "number", "nanoseconds"}:
            try:
                if isinstance(value, bool):
                    raise ValueError()
                result = float(value) if kind == "number" else int(value)
                if not math.isfinite(result) or (kind != "number" and isinstance(value, float) and value != result):
                    raise ValueError()
                return result
            except (ValueError, TypeError, OverflowError):
                error(f"Invalid {kind} value for {field}")
        if kind == "json":
            value = _decode(value)
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True, ensure_ascii=False)
        return str(value)

    def _filtered(self, params):
        validate_params(params)
        search = params.get("searchType", params.get("search_type", "traceId"))
        search_column = {"traceId": "trace_id", "input": "attributes.gen_ai.input.messages", "output": "attributes.gen_ai.output.messages", "name": "name", "sessionId": "session_id"}.get(search)
        if search_column is None:
            error(f"Unsupported trace search type: {search}")
        tags = _decode(params.get("tags", params.get("tag", [])))
        tags = [tags] if isinstance(tags, str) else tags
        if tags is not None and (not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags)):
            error("tags must be a string or array of strings")
        filters = _decode(params.get("filters") or [])
        if isinstance(filters, dict):
            filters = [filters] if "field" in filters else [{"field": k, "op": "=", "value": v} for k, v in filters.items()]
        if not isinstance(filters, list) or not all(isinstance(f, dict) for f in filters):
            error("filters must be an array of objects")
        filters = list(filters)
        for param, field in (("trace_id", "trace_id"), ("traceId", "trace_id"), ("session_id", "session_id"), ("sessionId", "session_id"),
                             ("agent_uuid", "resource.attributes.aivision.agent.id"), ("env", "environment"),
                             ("scene", "resource.attributes.ant.agent.scene"), ("username", "userId")):
            if params.get(param):
                filters.append({"field": field, "op": "=", "value": params[param]})
        normalized = []
        for f in filters:
            field, op = f.get("field"), f.get("op", f.get("operator", "="))
            self._field(field)
            if field == "inputEvidence":
                error("Input evidence supports projection only")
            op = {"eq": "=", "neq": "!=", "IN": "in", "NOT_IN": "not_in", "like": "LIKE", "is_null": "IS_NULL", "is_not_null": "IS_NOT_NULL"}.get(op, op)
            if op not in OPERATORS:
                error(f"Unsupported trace filter operator: {op}")
            value = f.get("value")
            if op in {"in", "not_in"}:
                if not isinstance(value, list):
                    error("IN filter value must be an array")
                value = [self._value(field, v) for v in value]
            elif op not in {"IS_NULL", "IS_NOT_NULL"}:
                value = self._value(field, value)
            normalized.append((field, op, value))
        matches = []
        for index, row in enumerate(self.rows):
            def compare(field, op, rhs, value):
                lhs = self._value(field, value)
                if op == "IS_NULL": return lhs is None
                if op == "IS_NOT_NULL": return lhs is not None
                if op == "=": return lhs == rhs
                if op == "!=": return lhs != rhs
                if op == "in": return lhs in rhs
                if op == "not_in": return lhs not in rhs
                if lhs is None or rhs is None: return False
                if op == "LIKE": return _matches_like(str(lhs), str(rhs))
                return {">": lambda: lhs > rhs, ">=": lambda: lhs >= rhs, "<": lambda: lhs < rhs, "<=": lambda: lhs <= rhs}[op]()
            def accepts(field, op, rhs):
                values = ([row.get(field)] if field in TRACE_FIELDS else self.observation_values[index].get(field, [None]))
                predicate = all if op in {"!=", "not_in", "IS_NULL"} else any
                return predicate(compare(field, op, rhs, value) for value in values)
            if not all(accepts(*f) for f in normalized):
                continue
            keyword = params.get("keyword")
            if keyword:
                if str(keyword).lower() not in str(row.get(search_column) or "").lower(): continue
            input_keyword = params.get("input_keyword")
            if input_keyword and str(input_keyword).lower() not in str(row.get("attributes.gen_ai.input.messages") or "").lower():
                continue
            if tags and not set(tags).intersection(self.traces[index]["tags"]): continue
            matches.append(index)
        return matches

    def facets(self, params):
        counts = {key: defaultdict(int) for key in ("env", "scene", "username", "tags")}
        for index in self._filtered(params):
            for name, field in (("env", "environment"), ("scene", "resource.attributes.ant.agent.scene"), ("username", "userId")):
                values = [self.rows[index][field]] if field in TRACE_FIELDS else self.observation_values[index].get(field, [])
                for value in {str(v) for v in values if v not in (None, "")}:
                    counts[name][value] += 1
            for value in set(self.traces[index]["tags"]):
                counts["tags"][value] += 1
        return {name: [{"value": value, "count": count} for value, count in sorted(values.items())] for name, values in counts.items()}

    def list(self, params):
        indices = self._filtered(params)
        order = params.get("orderBy", params.get("order_by", "start_time_unix_nano.desc"))
        if not isinstance(order, str): error("orderBy must be a string")
        descending = order.startswith("-") or order.endswith(".desc")
        field = order.removeprefix("-").removesuffix(".desc").removesuffix(".asc")
        if self._field(field) == "json": error("Cannot sort a JSON field")
        # A missing Token measurement ranks below every known count, even
        # when the requested order reverses the measured values and tie-break.
        reverse_missing_rank = descending and field in TOTAL_TOKEN_FIELDS

        def sort_key(index):
            row = self.rows[index]
            value = row.get(field)
            missing = value is None
            return missing != reverse_missing_rank, self._value(field, value), row["trace_id"]

        indices.sort(key=sort_key, reverse=descending)
        try:
            if any(isinstance(params.get(key), (bool, float)) for key in ("page", "limit")):
                raise ValueError()
            page, limit = int(params.get("page", 1)), int(params.get("limit", 50))
            if page < 1 or not 1 <= limit <= 200: raise ValueError()
        except (ValueError, TypeError): error("page must be positive and limit between 1 and 200")
        fields = _decode(params.get("fields"))
        if isinstance(fields, str): fields = [f.strip() for f in fields.split(",")]
        if fields is not None:
            if not isinstance(fields, list) or not fields: error("fields must be a non-empty array")
            for field in fields: self._field(field)
        selected = indices[(page - 1) * limit:page * limit]
        rows = [{**self.rows[i], "inputEvidence": _input_evidence(self.traces[i])}
                if requests_input_evidence(params) else self.rows[i] for i in selected]
        if fields:
            rows = [{**{field: row.get(field) for field in fields},
                     **({"ioPreview": row["ioPreview"]} if self.include_previews else {})} for row in rows]
        return {"data": rows, "meta": {"page": page, "limit": limit, "totalItems": len(indices), "totalPages": math.ceil(len(indices) / limit), "complete": True,
                                       "environmentSemantics": ENVIRONMENT_SEMANTICS, "environmentFacetsComplete": True,
                                       **({"ioPreviewComplete": True} if self.include_previews else {})}}

    def metrics(self, params):
        include_cost = costs_requested(params)
        if any(key in params for key in ("page", "limit", "fields", "orderBy", "order_by")):
            error("Metrics describe the complete window; pagination/projection is not supported")
        bucket = params.get("bucket", "hour")
        seconds = {"minute": 60, "hour": 3600, "day": 86400}.get(bucket)
        if seconds is None: error("bucket must be minute, hour or day")
        group_by = params.get("groupBy", "model")
        if group_by not in {"model", "provider"}: error("groupBy must be model or provider")
        traces = [self.traces[i] for i in self._filtered(params)]
        observations = [o for trace in traces for o in trace["observations"]]
        calls = [o for o in observations if o["type"] in {"GENERATION", "EMBEDDING"}]
        unique_calls = {(o["traceId"], o["id"]): o for o in calls}.values()
        usage_by_type = []
        for kind in ("GENERATION", "EMBEDDING"):
            own = [o for o in unique_calls if o["type"] == kind]
            if own:
                usage_by_type.append({
                    "type": kind, "callCount": len(own),
                    "usage": aggregate_usage(own, prices=self.prices, context=observations),
                    "reportedUsage": reported_usage(own),
                })
        errors = sum(
            _status(next(o for o in t["observations"] if o["id"] == t["rootObservationId"])) == 2
            for t in traces
        )
        latencies = sorted(t["latency"] * 1000 for t in traces if t["latency"] is not None)
        grouped, buckets = defaultdict(list), defaultdict(list)
        for call in calls:
            grouped[call.get(group_by)].append(call)
            start = timestamp_ns(call["startTime"])
            buckets[start // (seconds * 1000000000) * seconds * 1000000000].append(call)
        from .semantics.observations import iso_timestamp
        usage = aggregate_usage(observations, prices=self.prices)
        result = {"traceCount": len(traces), "spanCount": len(observations),
                "generationCount": sum(o["type"] == "GENERATION" for o in observations),
                "toolCount": sum(o["type"] == "TOOL" for o in observations), "errorCount": errors,
                "errorRate": errors / len(traces) if traces else None,
                "usage": usage, "costCoverage": usage["costCoverage"],
                "reportedUsage": reported_usage(observations), "usageByType": usage_by_type,
                "timeseries": [{"timestamp": iso_timestamp(start), "usage": aggregate_usage(items, prices=self.prices, context=observations), "callCount": len(items)} for start, items in sorted(buckets.items())],
                "groups": [{"dimension": group_by, "value": key, "usage": aggregate_usage(items, prices=self.prices, context=observations), "callCount": len(items)} for key, items in sorted(grouped.items(), key=lambda pair: str(pair[0] or ""))],
                "coverage": usage_coverage(observations),
                "latencyMs": {"mean": sum(latencies) / len(latencies) if latencies else None,
                              "p95": latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else None}}
        return result if include_cost else {**without_costs(result), "costsIncluded": False}
