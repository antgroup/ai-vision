"""Session lists over an already authorized, verified complete span window.

Selection and metadata follow the storage session contract at span grain. Usage
describes all traces belonging to a selected session in the complete window.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from .native_query import NativeTraceWindow, error
from .semantics.observations import iso_timestamp, timestamp_ns
from .semantics.usage import aggregate_usage, usage_coverage


FILTER_FIELDS = {
    "trace_id": "trace_id",
    "span_id": "span_id",
    "session_id": "session_id",
    "attributes.gen_ai.conversation.id": "session_id",
    "name": "name",
    "status_code": "status_code",
    "agent_product": "agent_product",
    "attributes.ant.username": "username",
    "username": "username",
    "resource.attributes.ant.agent.env": "env",
    "env": "env",
    "resource.attributes.ant.agent.scene": "scene",
    "scene": "scene",
    "attributes.langfuse.trace.tags": "tags",
    "tags": "tags",
    "attributes.gen_ai.request.model": "model",
    "model": "model",
    "start_time_unix_nano": "start_time_unix_nano",
    "end_time_unix_nano": "end_time_unix_nano",
    "resource.attributes.aivision.agent.id": "agent_uuid",
}
NUMERIC_FIELDS = {"status_code", "start_time_unix_nano", "end_time_unix_nano"}
DIMENSIONS = {"env", "scene", "username", "tags"}
ROW_FIELDS = {
    "session_id",
    "created_at",
    "last_activity",
    "start_time_unix_nano",
    "end_time_unix_nano",
    "trace_count",
    "span_count",
    "user_id",
    "space_id",
    "agent_product",
    "environment",
    "scene",
    "scenes",
    "username",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "usage",
    "coverage",
    "costCoverage",
}
PARAMS = {
    "page",
    "limit",
    "fields",
    "filters",
    "orderBy",
    "order_by",
    "from",
    "to",
    "fromTimestamp",
    "toTimestamp",
    "agent_product",
    "agent_uuid",
    "session_id",
    "sessionId",
    "trace_id",
    "traceId",
    "env",
    "scene",
    "username",
    "tags",
    "tag",
    "keyword",
    "searchType",
    "search_type",
    "input_keyword",
    "detail",
    "source_system",
    "sourceSystem",
}


def _decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def _text(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _first(*values):
    return next((value for value in values if value is not None and value != ""), None)


def _rfc3339(nano):
    value = iso_timestamp(nano)
    if value is None:
        return None
    return (
        value[:19]
        + ("." + value[20:-1].rstrip("0") if value[20:-1].rstrip("0") else "")
        + "Z"
    )


def _value(field, value):
    if value is None:
        return None
    if isinstance(value, (dict, list, bool)):
        error(f"{field} requires a scalar value")
    if field not in NUMERIC_FIELDS:
        return _text(value)
    if field == "status_code" and isinstance(value, str):
        value = {"OK": 1, "ERROR": 2, "UNSET": 0}.get(value.upper(), value)
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError()
        return int(number)
    except (ValueError, InvalidOperation):
        error(f"{field} requires an integer value")


def _tags(value, *, allow_empty=False):
    value = _decode(value)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        error("tags must be a string or an array of strings")
    values = list(dict.fromkeys(item.strip() for item in value if item.strip()))
    if (
        (not values and not allow_empty)
        or len(values) > 100
        or any(len(item) > 255 for item in values)
    ):
        error("tags requires 1 to 100 values of at most 255 characters")
    return values


def _filters(params):
    filters = _decode(params.get("filters") or [])
    if isinstance(filters, dict):
        filters = (
            [filters]
            if "field" in filters
            else [{"field": key, "value": value} for key, value in filters.items()]
        )
    if (
        not isinstance(filters, list)
        or len(filters) > 100
        or not all(isinstance(item, dict) for item in filters)
    ):
        error("filters must contain at most 100 objects")
    filters = list(filters)
    for param, field in (
        ("session_id", "session_id"),
        ("sessionId", "session_id"),
        ("trace_id", "trace_id"),
        ("traceId", "trace_id"),
        ("agent_uuid", "resource.attributes.aivision.agent.id"),
        ("env", "env"),
        ("scene", "scene"),
        ("username", "username"),
    ):
        value = params.get(param)
        if value not in (None, ""):
            if not isinstance(value, str) or len(value) > 255:
                error(f"{param} requires a string of at most 255 characters")
            filters.append({"field": field, "value": value.strip()})
    direct_tags = params.get("tags", params.get("tag"))
    if direct_tags not in (None, ""):
        tags = _tags(direct_tags, allow_empty=True)
        if tags:
            filters.append({"field": "tags", "op": "IN", "value": tags})
    result = []
    for item in filters:
        raw_field = item.get("field")
        field = FILTER_FIELDS.get(raw_field) if isinstance(raw_field, str) else None
        if field is None:
            error(f"Unsupported session filter field: {raw_field}")
        op = item.get("op", item.get("operator", "="))
        if not isinstance(op, str):
            error("filter operator must be a string")
        op = op.strip().replace("_", " ").upper()
        if op in {"", "EQ"}:
            op = "="
        value = item.get("value")
        if field == "tags":
            if op not in {"=", "IN"}:
                error(f"Unsupported tags operator: {op}")
            value = _tags(value)
        elif op in {"IN", "NOT IN"}:
            if not isinstance(value, list) or not 1 <= len(value) <= 200:
                error(f"{op} requires 1 to 200 values")
            value = [_value(field, item) for item in value]
        elif op in {"=", "!=", ">", ">=", "<", "<="}:
            value = _value(field, value)
        elif op in {"LIKE", "CONTAINS"}:
            if not isinstance(value, str) or not value.strip():
                error(f"{op} requires a non-empty string")
        elif op not in {"IS NULL", "IS NOT NULL"}:
            error(f"Unsupported session filter operator: {op}")
        result.append((field, op, value))
    return result


def _contains(value, pattern):
    # Storage wraps patterns in %, and only % and _ are wildcards. A row of
    # dynamic-programming states bounds work to O(len(value) * len(pattern))
    # and memory to O(len(pattern)); near misses cannot trigger backtracking.
    pattern = "%" + pattern + "%"
    states = [True] + [False] * len(pattern)
    for index, char in enumerate(pattern, 1):
        states[index] = states[index - 1] and char == "%"
    for char in str(value):
        previous = states[0]
        states[0] = False
        for index, token in enumerate(pattern, 1):
            old = states[index]
            states[index] = (
                states[index - 1] or old
                if token == "%"
                else previous and (token == "_" or token == char)
            )
            previous = old
    return states[-1]


def _matches(row, predicate):
    field, op, rhs = predicate
    if field == "tags":
        return bool(set(row["tags"]).intersection(rhs))
    lhs = row.get(field)
    if op == "IS NULL":
        return lhs is None
    if op == "IS NOT NULL":
        return lhs is not None
    if lhs is None or rhs is None:
        return False
    lhs = _value(field, lhs)
    if op == "IN":
        return lhs in rhs
    if op == "NOT IN":
        return None not in rhs and lhs not in rhs
    if op in {"LIKE", "CONTAINS"}:
        return _contains(lhs, rhs)
    return {
        "=": lambda: lhs == rhs,
        "!=": lambda: lhs != rhs,
        ">": lambda: lhs > rhs,
        ">=": lambda: lhs >= rhs,
        "<": lambda: lhs < rhs,
        "<=": lambda: lhs <= rhs,
    }[op]()


class NativeSessionWindow:
    def __init__(self, details, *, project_id, prices=None):
        details = list(details)
        window = NativeTraceWindow(details, project_id=project_id, prices=prices)
        self.prices = prices
        self.traces = {trace["id"]: trace for trace in window.traces}
        rows = {}
        self.session_traces = defaultdict(set)
        for detail, trace in zip(details, window.traces):
            observations = {item["id"]: item for item in trace["observations"]}
            for raw in detail["spans"]:
                payload = _decode(raw.get("payload"))
                payload = payload if isinstance(payload, dict) else {}
                merged = {**payload, **raw}
                identifier = _first(
                    merged.get("span_id"), merged.get("spanId"), merged.get("id")
                )
                observation = observations[str(identifier)]
                attrs, resource = (
                    observation["attributes"],
                    observation["resourceAttributes"],
                )
                sid = _first(observation["sessionId"], merged.get("session_id"))
                if sid is None:
                    continue
                sid = str(sid)
                collector = _first(merged.get("user_id"), resource.get("ant.username"))
                tags = _decode(attrs.get("langfuse.trace.tags"))
                tags = tags if isinstance(tags, list) else []
                row = {
                    "trace_id": trace["id"],
                    "span_id": observation["id"],
                    "session_id": sid,
                    "name": _text(observation["name"]),
                    "status_code": _value(
                        "status_code", observation["otelStatus"].get("code", 0)
                    ),
                    "start_time_unix_nano": timestamp_ns(observation["startTime"]),
                    "end_time_unix_nano": timestamp_ns(observation["endTime"]),
                    "user_id": _text(collector),
                    "space_id": _text(
                        _first(
                            merged.get("space_id"), resource.get("aivision.space.id")
                        )
                    ),
                    "agent_product": _text(
                        _first(
                            merged.get("agent_product"),
                            resource.get("ant.agent.product"),
                        )
                    ),
                    "agent_uuid": _text(resource.get("aivision.agent.id")),
                    "env": _text(
                        _first(
                            resource.get("ant.agent.env"),
                            resource.get("deployment.environment"),
                            attrs.get("ant.agent.env"),
                        )
                    ),
                    "scene": _text(
                        _first(
                            resource.get("ant.agent.scene"),
                            attrs.get("ant.scene"),
                            attrs.get("scene"),
                        )
                    ),
                    "username": _text(
                        _first(
                            collector, attrs.get("ant.username"), attrs.get("user.id")
                        )
                    ),
                    "model": _text(
                        _first(
                            attrs.get("gen_ai.request.model"),
                            attrs.get("gen_ai.response.model"),
                        )
                    ),
                    "tags": [item for item in tags if isinstance(item, str)],
                    "input": _text(
                        _first(
                            attrs.get("gen_ai.input.messages"),
                            attrs.get("langfuse.observation.input"),
                            attrs.get("input"),
                        )
                    )
                    or "",
                    "output": _text(
                        _first(
                            attrs.get("gen_ai.output.messages"),
                            attrs.get("langfuse.observation.output"),
                            attrs.get("output"),
                        )
                    )
                    or "",
                }
                rows[(trace["id"], observation["id"])] = row
                self.session_traces[sid].add(trace["id"])
        self.rows = list(rows.values())

    def list(self, params):
        unknown = set(params) - PARAMS
        if unknown:
            error("Unknown session query parameter: " + ", ".join(sorted(unknown)))
        try:
            if any(
                isinstance(params.get(key), (bool, float)) for key in ("page", "limit")
            ):
                raise ValueError()
            page, limit = int(params.get("page", 1)), int(params.get("limit", 50))
            if page < 1 or not 1 <= limit <= 200:
                raise ValueError()
        except (ValueError, TypeError):
            error("page must be positive and limit between 1 and 200")
        fields = _decode(params.get("fields"))
        if isinstance(fields, str):
            fields = [item.strip() for item in fields.split(",")]
        if fields is not None and (
            not isinstance(fields, list)
            or not fields
            or any(
                not isinstance(item, str) or item not in ROW_FIELDS for item in fields
            )
        ):
            error("fields must be a non-empty array of supported session fields")
        predicates = _filters(params)
        keyword = params.get("keyword") or ""
        fuzzy = params.get("input_keyword") or ""
        if any(
            not isinstance(value, str) or len(value) > 4096
            for value in (keyword, fuzzy)
        ):
            error(
                "keyword and input_keyword must be strings of at most 4096 characters"
            )
        search = params.get("searchType", params.get("search_type", "traceId"))
        if keyword and search not in {"", "traceId", "trace_id", "input", "output"}:
            error(f"Unsupported session searchType: {search}")

        def selected(row, excluded=None):
            if not all(
                _matches(row, predicate)
                for predicate in predicates
                if predicate[0] != excluded
            ):
                return False
            if keyword.strip():
                field = "trace_id" if search in {"", "traceId", "trace_id"} else search
                if field == "trace_id" and row[field] != keyword.strip():
                    return False
                if field != "trace_id" and not _contains(row[field], keyword.strip()):
                    return False
            if fuzzy.strip() and not any(
                _contains(row[field], fuzzy.strip()) for field in ("input", "output")
            ):
                return False
            return True

        groups = defaultdict(list)
        for row in self.rows:
            if selected(row):
                groups[row["session_id"]].append(row)
        rows = []
        for sid, spans in groups.items():
            starts = [row["start_time_unix_nano"] for row in spans]
            ends = [
                row["end_time_unix_nano"]
                for row in spans
                if row["end_time_unix_nano"] is not None
            ]
            start, end = min(starts), max(ends) if ends else None

            def maximum(field):
                values = [row[field] for row in spans if row[field] is not None]
                return max(values) if values else None

            observations = [
                item
                for trace_id in sorted(self.session_traces[sid])
                for item in self.traces[trace_id]["observations"]
            ]
            usage = aggregate_usage(observations, prices=self.prices)
            scene = maximum("scene")
            rows.append(
                {
                    "session_id": sid,
                    "created_at": _rfc3339(start),
                    "last_activity": _rfc3339(end),
                    # These historical field names carry RFC3339 text on the wire.
                    "start_time_unix_nano": _rfc3339(start),
                    "end_time_unix_nano": _rfc3339(end),
                    "trace_count": len({row["trace_id"] for row in spans}),
                    "span_count": len(spans),
                    "user_id": maximum("user_id"),
                    "space_id": maximum("space_id"),
                    "agent_product": maximum("agent_product"),
                    "environment": maximum("env"),
                    "scene": scene,
                    "scenes": [scene] if scene and scene.strip() else [],
                    "username": maximum("username"),
                    "input_tokens": usage["input"],
                    "output_tokens": usage["output"],
                    "total_tokens": usage["total"],
                    "usage": usage,
                    "coverage": usage_coverage(observations),
                    "costCoverage": usage["costCoverage"],
                }
            )
        order = params.get("orderBy", params.get("order_by", "end_time_unix_nano.desc"))
        if not isinstance(order, str):
            error("orderBy must be a string")
        parts = order.strip().split(".")
        descending = True
        if parts[-1].upper() in {"ASC", "DESC"}:
            descending = parts.pop().upper() == "DESC"
        field = {
            "last_activity": "end_time_unix_nano",
            "end_time_unix_nano": "end_time_unix_nano",
            "created_at": "start_time_unix_nano",
            "start_time_unix_nano": "start_time_unix_nano",
            "session_id": "session_id",
            "trace_count": "trace_count",
        }.get(".".join(parts))
        if field is None:
            field, descending = "end_time_unix_nano", True
        rows.sort(key=lambda row: row["session_id"])

        def order_value(row):
            value = row[field]
            if field.endswith("_unix_nano") and value is not None:
                value = timestamp_ns(value)
            return value is not None, value

        rows.sort(key=order_value, reverse=descending)
        facets = {}
        for dimension in ("env", "username", "scene"):
            values = defaultdict(set)
            for row in self.rows:
                if row[dimension] not in (None, "") and selected(
                    row, excluded=dimension
                ):
                    values[row[dimension]].add(row["session_id"])
            facets[dimension] = [
                {"value": value, "count": len(sessions)}
                for value, sessions in sorted(
                    values.items(), key=lambda item: (-len(item[1]), item[0])
                )[:100]
            ]
        result = rows[(page - 1) * limit : page * limit]
        # Storage session lists accept fields but return their fixed row shape.
        # In particular, usage and coverage must stay visible together.
        return {
            "data": result,
            "meta": {
                "page": page,
                "limit": limit,
                "totalItems": len(rows),
                "totalPages": math.ceil(len(rows) / limit),
                "complete": True,
            },
            "facets": facets,
        }
