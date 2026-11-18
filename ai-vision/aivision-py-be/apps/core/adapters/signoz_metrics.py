"""OceanBase-backed SigNoz metrics adapter.

Queries go through the original SigNoz v5 querier with explicit time, space and
scalar-reduction semantics. PromQL and arbitrary SQL are not forwarded.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import requests

from apps.core.metrics import external_call_timer

from .base import BaseQueryAdapter, QueryContext, QueryResult, ResponseFormat, ValueType


_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:/-]*$")
_DURATION_RE = re.compile(r"^[1-9][0-9]*(?:ms|s|m|h|d)$")
_LEGACY_RANGE_QUERY_RE = re.compile(
    r"^sum\(sum_over_time\((?P<metric>[A-Za-z_:][A-Za-z0-9_.:/-]*)"
    r"\{(?P<labels>.*)\}\[\{\{\s*__range\s*\}\}:?\]\)\)$"
)
_LEGACY_SUM_QUERY_RE = re.compile(
    r"^sum\((?P<metric>[A-Za-z_:][A-Za-z0-9_.:/-]*)"
    r"\{(?P<labels>.*)\}\)(?:\s+by\((?P<groups>[^)]+)\))?$"
)
_LEGACY_LABEL_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_.:/-]*)\s*"
    r"(?P<op>=~|=)\s*(?P<quote>['\"])(?P<value>.*)(?P=quote)\s*$"
)
_PROTECTED_FILTER_FIELDS = {
    "space_id",
    "space.id",
    "aivision.space.id",
    "ai.vision.space.id",
    "agent_product",
}
_FILTER_FIELD_ALIASES = {"username": "user_id"}


def _split_legacy_labels(raw: str) -> list[str]:
    """Split the limited legacy selector without accepting arbitrary PromQL."""
    labels: list[str] = []
    start = 0
    quote = ""
    for index, char in enumerate(raw):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == ",":
            labels.append(raw[start:index].strip())
            start = index + 1
    if quote:
        raise ValueError("Unsupported unterminated legacy PromQL selector")
    labels.append(raw[start:].strip())
    return [label for label in labels if label]


def migrate_legacy_metrics_query(query_def: dict[str, Any]) -> dict[str, Any]:
    """Convert the six legacy AI Vision metric templates to a controlled schema.

    Unknown templates are returned unchanged and consequently fail closed in the
    adapter.  This avoids pretending that arbitrary PromQL is supported by the
    OceanBase query profile.
    """
    migrated = deepcopy(query_def)
    if migrated.get("mode") != "template":
        return migrated
    template = " ".join(str(migrated.get("template") or "").split())
    match = _LEGACY_RANGE_QUERY_RE.fullmatch(template)
    range_sum = match is not None
    if match is None:
        match = _LEGACY_SUM_QUERY_RE.fullmatch(template)
    if match is None:
        return migrated

    filters: dict[str, Any] = {}
    for raw_label in _split_legacy_labels(match.group("labels")):
        label = _LEGACY_LABEL_RE.fullmatch(raw_label)
        if label is None:
            return migrated
        value: Any = label.group("value")
        if value.strip() == "{{ username }}":
            value = "$username"
        elif label.group("op") == "=~":
            alternatives = value.split("|")
            if not alternatives or any(
                not re.fullmatch(r"[A-Za-z0-9_.:/-]+", item)
                for item in alternatives
            ):
                return migrated
            value = alternatives
        filters[_FILTER_FIELD_ALIASES.get(label.group("name"), label.group("name"))] = value

    migrated.pop("template", None)
    migrated.update(
        {
            "mode": "simple",
            "metric": match.group("metric"),
            "filters": filters,
            "aggregation": "sum",
            "time_aggregation": "sum" if range_sum else "latest",
            "reduce_to": "sum" if range_sum else "last",
        }
    )
    groups = match.groupdict().get("groups")
    if groups:
        migrated["group_by"] = [item.strip() for item in groups.split(",")]
    return migrated


def migrate_legacy_metrics_queries(queries: Any) -> Any:
    if not isinstance(queries, dict):
        return deepcopy(queries)
    return {
        key: migrate_legacy_metrics_query(value)
        if isinstance(value, dict)
        else deepcopy(value)
        for key, value in queries.items()
    }


class SignozMetricsQueryAdapter(BaseQueryAdapter):
    """Query OTLP metric samples through the OceanBase Query Service only."""

    def render_simple(self, query_def: dict, context: QueryContext) -> str:
        payload = self._build_query_range_payload(query_def, context)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def render_template(self, query_def: dict, context: QueryContext) -> str:
        raise ValueError(
            "SigNoz metrics requires a controlled simple query definition; "
            "PromQL and raw SQL are not supported"
        )

    @staticmethod
    def _epoch_millis(value: str) -> int:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1000)

    @staticmethod
    def _validate_field_name(value: Any) -> str:
        name = str(value or "").strip()
        if not _FIELD_NAME_RE.fullmatch(name):
            raise ValueError(f"Unsupported metrics field: {name!r}")
        return name

    @staticmethod
    def _quote_filter_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        # SigNoz's filter grammar uses backslash escapes, not SQL doubled quotes.
        return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"

    def _resolve_aggregation(self, query_def: dict) -> str:
        direct = str(
            query_def.get("aggregation")
            or query_def.get("space_aggregation")
            or ""
        ).strip().lower()
        if direct:
            if direct not in {"sum", "avg", "count"}:
                raise ValueError(f"Unsupported metrics aggregation: {direct}")
            return direct

        mapped: set[str] = set()
        aliases = {
            "sum": "sum",
            "sum_over_time": "sum",
            "avg": "avg",
            "avg_over_time": "avg",
            "count": "count",
            "count_over_time": "count",
        }
        for item in query_def.get("aggregations") or []:
            name = str(item.get("func") if isinstance(item, dict) else "").lower()
            if name not in aliases:
                raise ValueError(f"Unsupported metrics aggregation: {name or item!r}")
            mapped.add(aliases[name])
        if len(mapped) != 1:
            raise ValueError("Metrics query must define exactly one aggregation semantic")
        return mapped.pop()

    def _expand_filters(
        self, query_def: dict, context: QueryContext
    ) -> list[list[tuple[str, str, Any]]]:
        raw_filters = query_def.get("filters") or {}
        if not isinstance(raw_filters, dict):
            raise ValueError("Metrics filters must be an object")
        template_vars = self._build_template_vars(context)
        filters: list[tuple[str, str, Any]] = []

        for raw_name, raw_value in raw_filters.items():
            name = self._validate_field_name(raw_name)
            name = _FILTER_FIELD_ALIASES.get(name, name)
            if name in _PROTECTED_FILTER_FIELDS:
                raise ValueError(f"Query definition cannot override protected field: {name}")
            operator = "="
            value = raw_value
            if isinstance(value, dict):
                if set(value) == {"in"}:
                    value = value["in"]
                elif set(value) == {"op", "value"}:
                    operator = str(value["op"]).strip().upper()
                    value = value["value"]
                else:
                    raise ValueError(f"Unsupported filter definition for {name}")
            if operator not in {"=", "!=", ">", ">=", "<", "<=", "CONTAINS"}:
                raise ValueError(f"Unsupported filter operator: {operator}")

            if isinstance(value, list):
                if not value or len(value) > 100:
                    raise ValueError(f"Filter alternatives require 1 to 100 items: {name}")
                if operator not in {"=", "!="}:
                    raise ValueError("List filters only support equality or inequality")
                operator = "IN" if operator == "=" else "NOT IN"
                value = [self._resolve_variable(item, template_vars) for item in value]
            else:
                value = self._resolve_variable(value, template_vars)
            filters.append((name, operator, value))

        space_id = str(context.space_id or "").strip()
        if not space_id or len(space_id) > 255:
            raise ValueError("space_id is required for SigNoz metrics queries")
        product = str(
            self.datasource_config.get("agent_product_filter")
            or ""
        ).strip()
        if not product:
            raise ValueError("agent_product_filter is required for SigNoz metrics queries")
        return [
            [
                ("space_id", "=", space_id),
                ("agent_product", "=", product),
                *filters,
            ]
        ]

    def _build_query_range_payload(
        self, query_def: dict, context: QueryContext
    ) -> dict[str, Any]:
        metric = self._validate_field_name(query_def.get("metric"))
        value_type = query_def.get("value_type", "number")
        if value_type == "number":
            request_type = "scalar"
        elif value_type == "timeseries":
            request_type = "time_series"
        else:
            raise ValueError(
                "SigNoz metrics supports only number and timeseries value types"
            )

        aggregation = self._resolve_aggregation(query_def)
        branches = self._expand_filters(query_def, context)
        time_aggregation = str(query_def.get("time_aggregation") or aggregation).lower()
        if time_aggregation not in {"sum", "avg", "count", "min", "max", "latest", "rate", "increase"}:
            raise ValueError(f"Unsupported time aggregation: {time_aggregation}")
        # count means count samples, not count already-aggregated series.
        space_aggregation = "sum" if aggregation == "count" else aggregation
        reduce_to = str(query_def.get("reduce_to") or (
            "sum" if aggregation == "count" else aggregation
        )).lower()
        if reduce_to not in {"sum", "avg", "min", "max", "last", "count", "median"}:
            raise ValueError(f"Unsupported scalar reduction: {reduce_to}")

        raw_group_by = query_def.get("group_by") or []
        if not isinstance(raw_group_by, list):
            raise ValueError("group_by must be an array")
        group_by = []
        for item in raw_group_by:
            if isinstance(item, dict):
                field = deepcopy(item)
                field["name"] = self._validate_field_name(field.get("name"))
            else:
                field = {"name": self._validate_field_name(item)}
            group_by.append(field)

        step = query_def.get("step", self.datasource_config.get("step", 60))
        if not (
            isinstance(step, (int, float))
            and not isinstance(step, bool)
            and step > 0
        ) and not (isinstance(step, str) and _DURATION_RE.fullmatch(step)):
            raise ValueError("step must be positive seconds or a duration")

        queries = []
        for index, filters in enumerate(branches):
            expression = " AND ".join(
                f"{name} {operator} " + (
                    "(" + ", ".join(self._quote_filter_value(item) for item in value) + ")"
                    if isinstance(value, list) else self._quote_filter_value(value)
                )
                for name, operator, value in filters
            )
            queries.append(
                {
                    "type": "builder_query",
                    "spec": {
                        "name": f"A{index + 1}",
                        "signal": "metrics",
                        "stepInterval": step,
                        "aggregations": [
                            {
                                "metricName": metric,
                                "timeAggregation": time_aggregation,
                                "spaceAggregation": space_aggregation,
                                **({"reduceTo": reduce_to} if request_type == "scalar" else {}),
                            }
                        ],
                        "filter": {"expression": expression},
                        "groupBy": group_by,
                        "limit": context.limit,
                    },
                }
            )

        return {
            "schemaVersion": "v1",
            "start": self._epoch_millis(context.time_start),
            "end": self._epoch_millis(context.time_end),
            "requestType": request_type,
            "compositeQuery": {"queries": queries},
        }

    def execute(self, rendered_query: str, context: QueryContext) -> Any:
        endpoint = str(self.datasource_config.get("endpoint") or "").rstrip("/")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise ValueError("SigNoz metrics endpoint is not configured correctly")
        try:
            body = json.loads(rendered_query)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid controlled SigNoz metrics query") from exc

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        api_key = str(self.datasource_config.get("api_key") or "").strip()
        if api_key:
            headers["SIGNOZ-API-KEY"] = api_key
        url = f"{endpoint}/api/v5/query_range"
        with external_call_timer("signoz", "metrics_query"):
            response = requests.post(url, json=body, headers=headers, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "success"
            or not isinstance(payload.get("data"), dict)
        ):
            raise ValueError("Invalid SigNoz metrics response")
        return payload["data"]

    @staticmethod
    def _labels_as_dict(raw_labels: Any) -> dict[str, Any]:
        labels: dict[str, Any] = {}
        for item in raw_labels if isinstance(raw_labels, list) else []:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            name = key.get("name") if isinstance(key, dict) else key
            if name:
                labels[str(name)] = item.get("value")
        return labels

    def parse_response(
        self, raw_result: Any, query_def: dict, context: QueryContext
    ) -> QueryResult:
        expected_type = (
            "scalar" if query_def.get("value_type", "number") == "number"
            else "time_series"
        )
        if raw_result.get("type") != expected_type:
            raise ValueError("SigNoz metrics response type does not match the query")
        data = raw_result.get("data")
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise ValueError("Invalid SigNoz metrics result set")

        if expected_type == "scalar":
            value = 0.0
            for result in results:
                for row in result.get("data", []) if isinstance(result, dict) else []:
                    if isinstance(row, list) and row and row[-1] is not None:
                        value += float(row[-1])
            return QueryResult(
                success=True,
                response_format=ResponseFormat.PROMETHEUS,
                value_type=ValueType.NUMBER,
                data={"value": value},
            )

        combined: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
        for result in results:
            aggregations = result.get("aggregations", []) if isinstance(result, dict) else []
            for bucket in aggregations:
                for series in bucket.get("series", []) if isinstance(bucket, dict) else []:
                    labels = self._labels_as_dict(series.get("labels"))
                    key = tuple(sorted((name, str(value)) for name, value in labels.items()))
                    target = combined.setdefault(key, {"labels": labels, "values": {}})
                    for point in series.get("values", []):
                        timestamp = point.get("timestamp")
                        point_value = point.get("value")
                        if timestamp is None or point_value is None:
                            continue
                        target["values"][timestamp] = (
                            target["values"].get(timestamp, 0.0) + float(point_value)
                        )
        output_series = [
            {
                "labels": item["labels"],
                "values": [
                    {"timestamp": timestamp, "value": value}
                    for timestamp, value in sorted(item["values"].items())
                ],
            }
            for _, item in sorted(combined.items())
        ]
        return QueryResult(
            success=True,
            response_format=ResponseFormat.PROMETHEUS,
            value_type=ValueType.TIMESERIES,
            data={"series": output_series},
        )
