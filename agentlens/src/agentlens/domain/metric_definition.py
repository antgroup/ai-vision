"""MetricDefinition: typed per-metric declaration inside a Diagnoser spec.

Every Diagnoser spec declares ``metrics: MetricDefinition[]``.
One-shot LLM aliases may declare one or more atomic metrics; other executors
may impose narrower contracts.

MetricDefinition owns only stable identity and display metadata. A diagnoser-level
summary is a TaskStats-only display aggregate, never a parent metric declaration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MetricDefinition:
    """One metric declared by a Diagnoser."""

    name: str
    desc: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MetricDefinition:
        name = str(d.get("name") or "")
        if not name:
            raise ValueError("MetricDefinition requires a non-empty 'name'")
        return cls(name=name, desc=str(d.get("desc") or ""))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "desc": self.desc}

    @property
    def display_name(self) -> str:
        return self.desc if self.desc else self.name


def metric_definitions(spec: dict[str, Any]) -> list[MetricDefinition]:
    """Return the MetricDefinitions declared in ``spec["metrics"]``."""
    raw = spec.get("metrics")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("metrics must be a list")
    if any(not isinstance(item, dict) for item in raw):
        raise ValueError("metrics items must be objects")
    return [MetricDefinition.from_dict(item) for item in raw]
