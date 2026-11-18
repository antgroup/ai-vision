"""Transport-neutral OTLP/JSON request encoding for semantic observations."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Mapping

from .privacy import canonical_json
from .semantic import SemanticObservation


def attr_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [attr_value(item) for item in value]}}
    return {"stringValue": str(value)}


def attributes(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": key, "value": attr_value(value)}
        for key, value in sorted(values.items())
        if value is not None
    ]


def unix_nano(timestamp: str) -> str:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return str(int(parsed.timestamp() * 1_000_000_000))


def _hex_id(value: str, width: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:width]


class OtlpJsonEncoder:
    """Convert one semantic stream into OTLP/JSON export requests."""

    def __init__(
        self,
        resource: Mapping[str, Any],
        *,
        scope_name: str = "observ-hermes",
        scope_version: str = "0.2.2",
        aggregation_temporality: int = 2,
        metric_start_time_unix_nano: int | None = None,
    ) -> None:
        if aggregation_temporality not in {1, 2}:
            raise ValueError(
                "aggregation temporality must be 1 (delta) or 2 (cumulative)"
            )
        if metric_start_time_unix_nano is not None and (
            type(metric_start_time_unix_nano) is not int
            or metric_start_time_unix_nano < 0
        ):
            raise ValueError("metric creation time must be a non-negative integer")
        self.resource = dict(resource)
        self.scope = {"name": scope_name, "version": scope_version}
        self.aggregation_temporality = aggregation_temporality
        self._starts: dict[str, SemanticObservation] = {}
        self._roots: dict[str, str] = {}
        self._metric_epoch = (
            str(metric_start_time_unix_nano)
            if metric_start_time_unix_nano is not None
            else None
        )
        self._metric_ends: dict[tuple[str, str | None, str | None, str], str] = {}

    def encode(
        self, observation: SemanticObservation
    ) -> tuple[str, dict[str, Any]] | None:
        timestamp = unix_nano(observation.timestamp)
        if self._metric_epoch is None or int(timestamp) < int(self._metric_epoch):
            # Replay has no runtime creation timestamp. Historical source
            # facts may also precede an explicitly supplied runtime boundary;
            # retain their actual clock rather than invent a later interval.
            self._metric_epoch = timestamp
        if observation.type == "span.start" and observation.key:
            self._starts[observation.key] = observation
            self._roots[observation.key] = (
                self._roots.get(observation.parent_key or "")
                or observation.parent_key
                or observation.key
            )
            return None
        if observation.type == "span.end" and observation.key:
            return self._span(observation)
        if observation.type == "event":
            return "logs", self._event(observation)
        if observation.type == "metric":
            return "metrics", self._metric(observation)
        if observation.type == "diagnostic":
            return "diagnostic", {
                "timestamp": observation.timestamp,
                "name": observation.name,
                "attributes": observation.attributes,
            }
        return None

    def abandon_pending(self) -> None:
        self._starts.clear()
        self._roots.clear()
        self._metric_ends.clear()
        self._metric_epoch = None

    def _resource(self) -> dict[str, Any]:
        return {"attributes": attributes(self.resource)}

    def _ids(self, key: str | None) -> tuple[str, str]:
        if not key:
            return "", ""
        root = self._roots.get(key, key)
        return _hex_id(root, 32), _hex_id(key, 16)

    def _span(self, terminal: SemanticObservation) -> tuple[str, dict[str, Any]]:
        start = self._starts.pop(terminal.key or "", None)
        if start is None or not terminal.key:
            return "diagnostic", {
                "timestamp": terminal.timestamp,
                "name": "span_end_without_start",
                "attributes": {"key": terminal.key or ""},
            }
        trace_id, span_id = self._ids(terminal.key)
        combined = {**start.attributes, **terminal.attributes}
        span: dict[str, Any] = {
            "traceId": trace_id,
            "spanId": span_id,
            "name": start.name,
            "kind": {"CHAT": 2, "LLM": 3, "TOOL": 1, "AGENT": 1}.get(
                start.span_kind or "", 1
            ),
            "startTimeUnixNano": unix_nano(start.timestamp),
            "endTimeUnixNano": unix_nano(terminal.timestamp),
            "attributes": attributes(combined),
            "status": {"code": 2 if terminal.status == "ERROR" else 1},
        }
        if start.parent_key:
            span["parentSpanId"] = self._ids(start.parent_key)[1]
        return "traces", {
            "resourceSpans": [
                {
                    "resource": self._resource(),
                    "scopeSpans": [{"scope": self.scope, "spans": [span]}],
                }
            ],
        }

    def _event(self, event: SemanticObservation) -> dict[str, Any]:
        trace_id, span_id = self._ids(event.key)
        record: dict[str, Any] = {
            "timeUnixNano": unix_nano(event.timestamp),
            "observedTimeUnixNano": unix_nano(event.timestamp),
            "severityNumber": 9,
            "severityText": "INFO",
            "body": {
                "kvlistValue": {
                    "values": [
                        {
                            "key": "event.name",
                            "value": {"stringValue": event.name},
                        }
                    ]
                }
            },
            "attributes": attributes(event.attributes),
        }
        if trace_id and span_id:
            record.update({"traceId": trace_id, "spanId": span_id, "flags": 1})
        return {
            "resourceLogs": [
                {
                    "resource": self._resource(),
                    "scopeLogs": [{"scope": self.scope, "logRecords": [record]}],
                }
            ],
        }

    def _metric(self, metric: SemanticObservation) -> dict[str, Any]:
        timestamp = unix_nano(metric.timestamp)
        start = timestamp
        if self.aggregation_temporality == 1:
            series = (
                metric.name,
                metric.metric_kind,
                metric.unit,
                canonical_json(metric.attributes),
            )
            start = self._metric_ends.get(series, self._metric_epoch or timestamp)
            if int(timestamp) < int(start):
                raise ValueError("metric source time precedes its delta interval")
            self._metric_ends[series] = timestamp
        point = {
            "attributes": attributes(metric.attributes),
            "startTimeUnixNano": start,
            "timeUnixNano": timestamp,
        }
        if metric.metric_kind == "counter":
            point["asInt"] = str(int(metric.value or 0))
            data = {
                "sum": {
                    "dataPoints": [point],
                    "aggregationTemporality": self.aggregation_temporality,
                    "isMonotonic": True,
                }
            }
        else:
            value = float(metric.value or 0)
            point.update(
                {
                    "count": "1",
                    "sum": value,
                    "bucketCounts": ["1"],
                    "explicitBounds": [],
                    "min": value,
                    "max": value,
                }
            )
            data = {
                "histogram": {
                    "dataPoints": [point],
                    "aggregationTemporality": self.aggregation_temporality,
                }
            }
        return {
            "resourceMetrics": [
                {
                    "resource": self._resource(),
                    "scopeMetrics": [
                        {
                            "scope": self.scope,
                            "metrics": [
                                {
                                    "name": metric.name,
                                    "unit": metric.unit or "",
                                    **data,
                                }
                            ],
                        }
                    ],
                }
            ],
        }
