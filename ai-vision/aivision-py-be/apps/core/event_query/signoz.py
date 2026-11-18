from __future__ import annotations

from typing import Any
import json
import math
from fnmatch import fnmatchcase
from collections import Counter
from decimal import Decimal, InvalidOperation

import requests

from apps.core.metrics import external_call_timer
from apps.core.services.observation_subject_service import require_default_subject, require_agent_subject
from apps.core.services.agent_routing import resolve_space_agent_for_query
from apps.core.trace_query.semantics.observations import iso_timestamp, timestamp_ns
from apps.core.trace_query.config import resolve_configured_agent_product
from apps.core.trace_query.types import TraceQueryServiceError

from .common import (
    DEFAULT_FACET_FIELDS,
    _parse_order_by,
    _parse_pagination,
    _parse_public_filters,
    _parse_required_time_range,
    _public_limits,
    _select_public_fields,
    _to_ms,
    SORTABLE_FIELDS,
)
from .types import EventFacetQuery, EventListQuery, EventMetadataQuery, EventRawSqlQuery


SIGNOZ_EVENT_FIELDS = {
    "time": "time",
    "event_name": "event_name",
    "event_kind": "event_kind",
    "trace_id": "trace_id",
    "span_id": "span_id",
    "session_id": "session_id",
    "username": "username",
    "env": "env",
    "scene": "scene",
    "model": "model",
    "duration_ms": "duration_ms",
    "status": "status",
    "body": "body",
    "severity_text": "severity_text",
}
SIGNOZ_DEFAULT_MAX_TIME_RANGE_DAYS = 7
BUILDER_FIELDS = {"body", "severity_text"}
MAX_LOG_SCAN = 10000
LOG_COLUMNS = ("body", "severity_text", "log_id", "space_id", "agent_product", "user_id",
               "event_name", "session_id", "trace_id", "span_id", "timestamp", "attributes", "resource_attributes")


def _literal(value):
    # Only server-resolved identities enter the builder expression. Public
    # filters stay structured and are evaluated after the complete scan.
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _canonical_log(row):
    attrs, resource = row.get("attributes") or {}, row.get("resource_attributes") or {}
    if not isinstance(attrs, dict) or not isinstance(resource, dict):
        raise _service_error("Invalid log attributes", 3035)
    def first(*values):
        value = next((value for value in values if value is not None and value != ""), None)
        # Match native JSON_UNQUOTE projections, including numeric status_code.
        return value if value is None or isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    duration = attrs.get("duration_ms")
    try:
        duration = float(duration) if duration is not None and duration != "" else None
        if duration is not None and not math.isfinite(duration):
            duration = None
    except (ValueError, TypeError):
        duration = None
    return {"time": iso_timestamp(row["timestamp"]), "event_name": first(row.get("event_name"), attrs.get("event.name")),
            "event_kind": first(attrs.get("event.kind"), attrs.get("gen_ai.span.kind")),
            "trace_id": row.get("trace_id"), "span_id": row.get("span_id"), "session_id": row.get("session_id"),
            "username": row.get("user_id"),
            "env": first(resource.get("ant.agent.env"), resource.get("deployment.environment"), attrs.get("environment"), attrs.get("env")),
            "scene": first(resource.get("ant.agent.scene"), attrs.get("ant.scene"), attrs.get("scene")),
            "model": first(attrs.get("gen_ai.request.model"), attrs.get("gen_ai.response.model"), attrs.get("model")),
            "duration_ms": duration, "status": first(attrs.get("status_code"), attrs.get("status"), row.get("severity_text")),
            "body": row.get("body"), "severity_text": row.get("severity_text")}


def _filter_log(row, spec):
    actual, expected, op = row.get(spec.field), spec.value, spec.operator.value
    if op == "IS NULL":
        return actual is None
    if op == "IS NOT NULL":
        return actual is not None
    def value(raw):
        if raw is None:
            return None
        if isinstance(raw, (dict, list, bool)):
            raise _service_error("Event filter value must be a scalar")
        if spec.field == "time":
            if isinstance(raw, str) and "T" in raw:
                try:
                    parsed = timestamp_ns(raw)
                except (ValueError, OverflowError):
                    parsed = None
                if parsed is not None and 0 <= parsed <= 2**63 - 1:
                    return parsed
            try:
                number = Decimal(str(raw))
                if not number.is_finite() or not 0 <= number <= (2**63 - 1) // 1000000 or number != number.to_integral_value():
                    raise ValueError()
                return int(number) * 1000000
            except (ValueError, InvalidOperation):
                raise _service_error("Invalid event time filter") from None
        if spec.field == "duration_ms":
            try:
                number = Decimal(str(raw))
                if number.is_finite():
                    return number
            except InvalidOperation:
                pass
            raise _service_error("Invalid event duration filter")
        return str(raw)
    if op == "LIKE":
        if not isinstance(expected, str) or len(expected) > 512:
            raise _service_error("LIKE requires a string of at most 512 characters")
        parts, escaped = [], False
        def literal(char):
            return {"*": "[*]", "?": "[?]", "[": "[[]"}.get(char, char)
        for char in expected:
            if escaped:
                parts.append(literal(char)); escaped = False
            elif char == "\\":
                escaped = True
            else:
                parts.append("*" if char == "%" else "?" if char == "_" else literal(char))
        if escaped:
            parts.append("\\")
        # Python's glob translator uses atomic wildcard groups, avoiding the
        # exponential backtracking of a naive '%' -> '.*' regex.
        return actual is not None and fnmatchcase(str(actual), "".join(parts))
    if op == "IN":
        if not expected or len(expected) > 100:
            raise _service_error("IN requires between 1 and 100 values")
        expected = [value(item) for item in expected]
        return actual is not None and value(actual) in expected
    expected = value(expected)
    if actual is None or expected is None:
        return False
    actual = value(actual)
    return {"=": lambda: actual == expected, ">=": lambda: actual >= expected, "<=": lambda: actual <= expected}[op]()


def _service_error(message: str, code: int = 3027) -> TraceQueryServiceError:
    return TraceQueryServiceError(message, code)


def _effective_event_config(config: dict[str, Any]) -> dict[str, Any]:
    """Apply AI Vision's default event-query window to the SigNoz datasource."""
    openapi = dict(config.get("openapi") or {})
    events = dict(openapi.get("events") or {})
    limits = dict(events.get("limits") or {})
    limits.setdefault("max_time_range_days", SIGNOZ_DEFAULT_MAX_TIME_RANGE_DAYS)
    events["limits"] = limits
    openapi["events"] = events
    return {**config, "openapi": openapi}


class _SigNozEventClient:
    def __init__(self, config: dict[str, Any]) -> None:
        endpoint = config.get("endpoint") or config.get("signoz_endpoint")
        if not endpoint:
            raise _service_error("SigNoz endpoint is not configured", 3011)
        self.endpoint = str(endpoint).rstrip("/")
        self.api_key = str(
            config.get("api_key")
            or config.get("access_token")
            or config.get("signoz_api_key")
            or ""
        )

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["SIGNOZ-API-KEY"] = self.api_key
        return headers

    @staticmethod
    def _decode_response(response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise _service_error("Invalid SigNoz response", 3035) from exc

        if response.status_code >= 400:
            message = None
            if isinstance(payload, dict):
                raw_message = payload.get("error") or payload.get("message")
                if isinstance(raw_message, str) and raw_message.strip():
                    message = raw_message.strip()
            if response.status_code == 404:
                raise _service_error(message or "Telemetry record not found", 3019)
            if response.status_code in (401, 403):
                raise _service_error("SigNoz query backend authentication failed", 3011)
            if response.status_code == 400:
                raise _service_error(message or "Invalid event query")
            raise _service_error("SigNoz event query failed", 3035)

        if isinstance(payload, dict) and payload.get("status") not in (None, "success"):
            raise _service_error("SigNoz event query failed", 3035)

        if (
            isinstance(payload, dict)
            and payload.get("status") == "success"
            and "data" in payload
        ):
            payload = payload["data"]
        if not isinstance(payload, dict):
            raise _service_error("Invalid SigNoz response", 3035)
        return payload

    def get(self, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        with external_call_timer("signoz", path):
            response = requests.get(
                f"{self.endpoint}{path}",
                params=params,
                headers=self.headers,
                timeout=30,
            )
        return self._decode_response(response)

    def post(self, path: str, *, payload: dict[str, Any]) -> dict[str, Any]:
        with external_call_timer("signoz", path):
            response = requests.post(
                f"{self.endpoint}{path}",
                json=payload,
                headers={**self.headers, "Content-Type": "application/json"},
                timeout=30,
            )
        return self._decode_response(response)


class SignozEventProvider:
    """Typed Logs/Events adapter for the OceanBase-backed SigNoz profile."""

    def __init__(self, datasource) -> None:
        self.datasource = datasource

    def _context(
        self, query
    ) -> tuple[_SigNozEventClient, dict[str, Any], dict[str, str]]:
        raw_config = self.datasource.config or {}
        config = _effective_event_config(raw_config)
        agent_product = resolve_configured_agent_product(config)
        if not agent_product:
            raise _service_error(
                "SigNoz event datasource agent_product_filter is not configured", 3011
            )

        space_id = str(getattr(query.space, "uuid", "") or "").strip()
        if not space_id:
            raise _service_error(
                "Cannot determine space identity for event access", 3018
            )

        identity = {"space_id": space_id, "agent_product": agent_product}
        agent = None
        agent_uuid = query.get_param("agent_uuid")
        if agent_uuid:
            agent = resolve_space_agent_for_query(query.space, query.agent_product, agent_uuid=agent_uuid, active_statuses=(1, 2))
            if agent is None:
                raise _service_error("Agent does not belong to the requested Space and product", 3019)
            identity["agent_uuid"] = str(agent.uuid)
        if getattr(query.space, "type", None) != 2:
            identity["user"] = require_agent_subject(agent) if agent is not None else require_default_subject(
                space=query.space,
                user=query.user,
                source_system=self.datasource.source_system,
            )
        return _SigNozEventClient(raw_config), config, identity

    @staticmethod
    def _source(datasource, config: dict[str, Any]) -> dict[str, Any]:
        return {
            "sourceSystem": "signoz",
            "default": bool(getattr(datasource, "is_default", 0)),
            "protocol": str(config.get("protocol") or "otlp"),
            "responseFormat": str(config.get("response_format") or "table"),
        }

    def get_metadata(self, query: EventMetadataQuery) -> dict[str, Any]:
        client, config, identity = self._context(query)
        payload = client.get("/api/v1/ai-vision/events/metadata", params=identity)
        raw_fields = payload.get("canonicalFields")
        if not isinstance(raw_fields, list):
            raise _service_error("Invalid SigNoz event metadata response", 3035)
        fields = [
            {**field, "projectable": True, "filterable": True,
             "sortable": field.get("key") in SORTABLE_FIELDS}
            for field in raw_fields
            if isinstance(field, dict) and field.get("key") in SIGNOZ_EVENT_FIELDS
        ]
        # The pinned OceanBase native endpoint exposes these physical columns,
        # but omits them from its canonical allowlist. Our bounded standard logs
        # builder path supplies their projection/filtering (never raw SQL).
        physical = {field.get("name") for field in payload.get("fields", []) if isinstance(field, dict)}
        for key in sorted(BUILDER_FIELDS & physical):
            if not any(field.get("key") == key for field in fields):
                fields.append({"key": key, "type": "string", "projectable": True, "filterable": True, "sortable": False})
        return {
            "agentProduct": query.agent_product,
            "capabilities": {
                "traces": {
                    "list": True,
                    "detail": True,
                    "spans": True,
                    "sessions": True,
                },
                "events": {
                    "list": True,
                    "facets": True,
                    "query": True,
                    "detail": False,
                    "modes": {"fieldsJson": True, "rawSql": False},
                },
            },
            "sources": {"events": [self._source(self.datasource, config)]},
            "eventSchema": {
                "fields": fields,
                "allowedOperators": [
                    "=",
                    "IN",
                    ">=",
                    "<=",
                    "LIKE",
                    "IS_NULL",
                    "IS_NOT_NULL",
                ],
                "limits": {**_public_limits(config), "maxBuilderScanEvents": MAX_LOG_SCAN},
            },
        }

    def get_schema_metadata(self, query: EventMetadataQuery) -> dict[str, Any]:
        client, config, identity = self._context(query)
        payload = client.get("/api/v1/ai-vision/events/metadata", params=identity)
        raw_fields = payload.get("fields")
        if not isinstance(raw_fields, list):
            raise _service_error("Invalid SigNoz event metadata response", 3035)
        fields = [
            {**field, "queryable": field.get("name") in SIGNOZ_EVENT_FIELDS}
            for field in raw_fields if isinstance(field, dict)
        ]
        return {
            "agentProduct": query.agent_product,
            "kind": "events",
            "schemaKind": "storage",
            "source": self._source(self.datasource, config),
            "fields": fields,
            "totalFields": len(fields),
        }

    @staticmethod
    def _builder_result(client, payload):
        response = client.post("/api/v5/query_range", payload=payload)
        warning = response.get("warning")
        # The supported image can resolve a trusted resource key directly even
        # before its discovery metadata is populated. This is not truncation.
        benign = "key `aivision.agent.id` not found in metadata; querying the underlying data directly. If this is unexpected, check the key name for typos."
        if warning:
            messages = warning.get("warnings") if isinstance(warning, dict) else None
            if not isinstance(messages, list) or not messages or any(not isinstance(item, dict) or item.get("message") != benign for item in messages):
                raise _service_error("Incomplete log query: storage warning", 3035)
        data = response.get("data")
        results = data.get("results") if isinstance(data, dict) else None
        if (response.get("type") != payload["requestType"] or not isinstance(results, list) or len(results) != 1
                or not isinstance(results[0], dict) or results[0].get("queryName") != "events"
                or response.get("error") or response.get("partial") or results[0].get("error") or results[0].get("partial")):
            raise _service_error("Incomplete log query response", 3035)
        return results[0]

    def _builder_rows(self, client, identity, from_dt, to_dt):
        start, end = _to_ms(from_dt), _to_ms(to_dt)
        if start >= end:
            raise _service_error("Event query from must be earlier than to")
        expression = " AND ".join(f"{key} = {_literal(identity[name])}" for key, name in (
            ("space_id", "space_id"), ("agent_product", "agent_product"), ("user_id", "user"),
            ("resource.aivision.agent.id", "agent_uuid")) if name in identity)
        # Native events use a closed [from,to] range, whereas the supported
        # builder uses [start,end). Read a 1 ms envelope completely, then trim
        # exact integer nanoseconds locally; never send 19-digit numeric BQL.
        if end >= (2**63 - 1) // 1000000:
            raise _service_error("Event time range exceeds the log builder capacity")
        base = {"schemaVersion": "v1", "start": start, "end": end + 1, "noCache": True}
        def request(kind, **extra):
            spec = {"name": "events", "signal": "logs", "filter": {"expression": expression}, **extra}
            return self._builder_result(client, {**base, "requestType": kind,
                "compositeQuery": {"queries": [{"type": "builder_query", "spec": spec}]}})
        def count():
            result = request("scalar", aggregations=[{"expression": "count()"}]).get("data")
            if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], list) or len(result[0]) != 1:
                raise _service_error("Incomplete log count response", 3035)
            value = result[0][0]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value != int(value):
                raise _service_error("Invalid log count", 3035)
            if value > MAX_LOG_SCAN:
                raise _service_error("Log scan limit is 10000; narrow the time range", 3027)
            return int(value)
        expected = count()
        rows, seen, cursors, cursor = [], set(), set(), ""
        while len(rows) < expected:
            args = {"selectFields": [{"name": key} for key in LOG_COLUMNS], "limit": 200,
                    "order": [{"key": {"name": "timestamp"}, "direction": "asc"},
                              {"key": {"name": "log_id"}, "direction": "asc"}]}
            # The pinned OceanBase builder consumes offset; retain it even if
            # an outer querier also supplies a cursor for another dialect.
            args["offset"] = len(rows)
            if cursor:
                args["cursor"] = cursor
            result = request("raw", **args)
            batch = result.get("rows")
            if not isinstance(batch, list) or not batch:
                raise _service_error("Incomplete log page", 3035)
            for wrapper in batch:
                row = wrapper.get("data") if isinstance(wrapper, dict) else None
                if not isinstance(row, dict) or not isinstance(row.get("log_id"), str) or not row["log_id"] or row["log_id"] in seen:
                    raise _service_error("Duplicate or missing log identity", 3035)
                if any(str(row.get(field, "")) != str(identity[name]) for field, name in (
                        ("space_id", "space_id"), ("agent_product", "agent_product"), ("user_id", "user")) if name in identity):
                    raise _service_error("Log record outside the authorized scope", 3019)
                resource = row.get("resource_attributes") or {}
                if not isinstance(resource, dict) or (resource.get("aivision.space.id") is not None and str(resource["aivision.space.id"]) != identity["space_id"]):
                    raise _service_error("Log record outside the authorized scope", 3019)
                if identity.get("agent_uuid") and resource.get("aivision.agent.id") != identity["agent_uuid"]:
                    raise _service_error("Log record outside the authorized Agent", 3019)
                timestamp = row.get("timestamp")
                if isinstance(timestamp, bool) or not isinstance(timestamp, int) or not start * 1000000 <= timestamp < (end + 1) * 1000000:
                    raise _service_error("Log record outside the requested time range", 3035)
                if any(field not in row for field in BUILDER_FIELDS):
                    raise _service_error("Incomplete log projection", 3035)
                seen.add(row["log_id"])
                rows.append((row["log_id"], _canonical_log(row), timestamp))
            if len(rows) > expected:
                raise _service_error("Changed log window during pagination; retry", 3035)
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or cursor and cursor in cursors:
                raise _service_error("Invalid or repeated log cursor", 3035)
            if cursor:
                cursors.add(cursor)
        if count() != expected:
            raise _service_error("Changed log window during pagination; retry", 3035)
        return [(identifier, row) for identifier, row, timestamp in rows if timestamp <= end * 1000000]

    def _filtered_builder_rows(self, client, identity, from_dt, to_dt, filters, keyword):
        if len(filters) > 20:
            raise _service_error("filters exceeds maximum")
        if len(keyword) > 512:
            raise _service_error("keyword exceeds maximum")
        # Validate even on an empty window; no invalid filter becomes success.
        for spec in filters:
            _filter_log({}, spec)
        return [(identifier, row) for identifier, row in self._builder_rows(client, identity, from_dt, to_dt)
                if all(_filter_log(row, spec) for spec in filters)
                and (not keyword or keyword in str(row.get("event_name") or ""))]

    def list_events(self, query: EventListQuery) -> dict[str, Any]:
        client, config, identity = self._context(query)
        from_dt, to_dt = _parse_required_time_range(query, config)
        limit, _offset, page = _parse_pagination(query, config)
        fields = _select_public_fields(
            query.get_param("fields"), config, SIGNOZ_EVENT_FIELDS
        )
        filters = _parse_public_filters(query.get_param("filters"), SIGNOZ_EVENT_FIELDS)
        order = _parse_order_by(query.get_param("orderBy"), SIGNOZ_EVENT_FIELDS)
        order_by = ""
        if order:
            public_field = next(
                (
                    key
                    for key, value in SIGNOZ_EVENT_FIELDS.items()
                    if value == order[0].field
                ),
                order[0].field,
            )
            order_by = ("" if order[0].ascending else "-") + public_field

        keyword = query.get_param("keyword")
        if not isinstance(keyword, str):
            keyword = ""
        if BUILDER_FIELDS.intersection(fields) or any(spec.field in BUILDER_FIELDS for spec in filters) or identity.get("agent_uuid"):
            rows = self._filtered_builder_rows(client, identity, from_dt, to_dt, filters, keyword.strip())
            if order:
                field = order[0].field
                rows.sort(key=lambda item: (item[1].get(field) is not None, item[1].get(field), item[0]), reverse=not order[0].ascending)
            total = len(rows)
            return {"data": [{field: row.get(field) for field in fields} for _, row in rows[(page - 1) * limit:page * limit]],
                    "meta": {"page": page, "limit": limit, "totalItems": total, "totalPages": max(1, math.ceil(total / limit)),
                             "complete": True, "maxScanEvents": MAX_LOG_SCAN}}
        payload = {
            **identity,
            "from": _to_ms(from_dt),
            "to": _to_ms(to_dt),
            "page": page,
            "limit": limit,
            "fields": fields,
            "filters": [
                {"field": spec.field, "op": spec.operator.value, "value": spec.value}
                for spec in filters
            ],
            "orderBy": order_by,
            "keyword": keyword.strip(),
        }
        return client.post("/api/v1/ai-vision/events/list", payload=payload)

    def list_facets(self, query: EventFacetQuery) -> dict[str, Any]:
        client, config, identity = self._context(query)
        from_dt, to_dt = _parse_required_time_range(query, config)
        filters = _parse_public_filters(query.get_param("filters"), SIGNOZ_EVENT_FIELDS)
        dimensions = (
            query.get_param("dimensions")
            or query.get_param("fields")
            or DEFAULT_FACET_FIELDS
        )
        if isinstance(dimensions, str):
            dimensions = [
                item.strip() for item in dimensions.split(",") if item.strip()
            ]
        if not isinstance(dimensions, list) or not dimensions:
            raise _service_error("dimensions must be a non-empty list")
        normalized_dimensions: list[str] = []
        for item in dimensions:
            if not isinstance(item, str) or item not in SIGNOZ_EVENT_FIELDS:
                raise _service_error(f"Unknown event facet field: {item}")
            if item not in normalized_dimensions:
                normalized_dimensions.append(item)

        raw_limit = query.get_param("limit")
        try:
            limit = min(max(1, int(raw_limit or 20)), 100)
        except (TypeError, ValueError):
            limit = 20
        if len(normalized_dimensions) > 10:
            raise _service_error("dimensions exceeds maximum")
        if BUILDER_FIELDS.intersection(normalized_dimensions) or any(spec.field in BUILDER_FIELDS for spec in filters) or identity.get("agent_uuid"):
            rows = self._filtered_builder_rows(client, identity, from_dt, to_dt, filters, "")
            data = {}
            for dimension in normalized_dimensions:
                counts = Counter(row.get(dimension) for _, row in rows)
                data[dimension] = [{"value": value, "count": count} for value, count in
                    sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))[:limit]]
            return {"data": data, "meta": {"limit": limit, "complete": True, "maxScanEvents": MAX_LOG_SCAN}}
        payload = {
            **identity,
            "from": _to_ms(from_dt),
            "to": _to_ms(to_dt),
            "dimensions": normalized_dimensions,
            "filters": [
                {"field": spec.field, "op": spec.operator.value, "value": spec.value}
                for spec in filters
            ],
            "limit": limit,
        }
        return client.post("/api/v1/ai-vision/events/facets", payload=payload)

    def query_raw_sql(self, query: EventRawSqlQuery) -> dict[str, Any]:
        del query
        raise _service_error(
            "rawSql mode is not supported by the SigNoz OceanBase event backend"
        )
