"""Typed observation parsed from one source span."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    # Trace timestamps end with 'Z'; fromisoformat in <3.11 doesn't accept it.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@dataclass
class Observation:
    """A single span within a trace (CHAIN, AGENT, TOOL, GENERATION, ...)."""

    id: str
    trace_id: str
    parent_id: str | None
    meta_span_id: str | None
    sofa_span_id: str | None
    name: str
    type: str
    level: str
    status_message: str
    start_time: datetime | None
    end_time: datetime | None
    latency: float | None
    input: Any
    output: Any
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost: float | None
    metadata: dict[str, Any]
    raw: dict[str, Any] = field(repr=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Observation:
        metadata = d.get("metadata") or {}
        attrs = metadata.get("attributes") or {}
        sofa_id = attrs.get("_internal.sofa.span_id") if isinstance(attrs, dict) else None
        # Normalize "None" string to actual None
        if sofa_id in (None, "None", ""):
            sofa_id = None

        return cls(
            id=d["id"],
            trace_id=d["traceId"],
            parent_id=d.get("parentObservationId"),
            meta_span_id=d.get("metaSpanId"),
            sofa_span_id=sofa_id,
            name=d.get("name") or "",
            type=d.get("type") or "",
            level=d.get("level") or "DEFAULT",
            status_message=d.get("statusMessage") or "",
            start_time=_parse_ts(d.get("startTime")),
            end_time=_parse_ts(d.get("endTime")),
            latency=d.get("latency"),
            input=d.get("input"),
            output=d.get("output"),
            model=d.get("model"),
            prompt_tokens=d.get("promptTokens") or 0,
            completion_tokens=d.get("completionTokens") or 0,
            total_tokens=d.get("totalTokens") or 0,
            total_cost=d.get("totalCost"),
            metadata=metadata,
            raw=d,
        )

    @property
    def duration(self) -> float | None:
        """Wall-clock duration in seconds, derived from start/end if needed."""
        if self.latency is not None:
            return self.latency
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None

    @property
    def is_error(self) -> bool:
        return self.level.upper() in {"ERROR", "WARNING"}
