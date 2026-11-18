"""Shared execution mechanics for code-owned builtin Rules."""

from __future__ import annotations

import math
from abc import abstractmethod
from dataclasses import replace
from typing import Any, ClassVar

from agentlens.diagnoser.base import ApplicabilitySkipped, Executor
from agentlens.diagnoser.executors.rule.builtin.registry import BuiltinRuleDefinition
from agentlens.domain.diagnosis_record import DiagnosisResult, Execution, Metric
from agentlens.domain.enums import Verdict
from agentlens.domain.metric_definition import MetricDefinition, metric_definitions
from agentlens.service.datasource.projection import Projection
from agentlens.service.datasource.projection.projectors.coding.models import CodingProjection
from agentlens.service.datasource.projection.projectors.coding.tool_actions import (
    ToolAction,
    classify_tool_action,
    extract_tool_actions,
)
from agentlens.service.datasource.trace import Trace


def binary_metric(
    metric: MetricDefinition,
    good: bool,
    reason: str = "",
    evidence: dict[str, Any] | None = None,
) -> Metric:
    return Metric(
        name=metric.name,
        verdict=Verdict.GOOD if good else Verdict.BAD,
        reason=reason,
        evidence=evidence or {},
    )


def finite_policy(spec: dict[str, Any], key: str, *, maximum: float | None = None) -> float | None:
    if key not in spec:
        return None
    value = spec[key]
    try:
        finite = isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if (
        isinstance(value, bool)
        or not finite
        or value < 0
        or (maximum is not None and value > maximum)
    ):
        raise ValueError(
            f"{key} must be a finite non-negative number"
            + (f" <= {maximum}" if maximum is not None else "")
        )
    return float(value)


def located_finding(span_id: str | None, finding: dict[str, Any]) -> dict[str, Any]:
    """Keep factual findings independent of optional source-span navigation."""
    return {
        **finding,
        "location_status": "available" if span_id else "unavailable",
        **({"source_span_id": span_id} if span_id else {}),
    }


def exempt(metric: MetricDefinition, reason: str, *, code: str = "not_applicable") -> Metric:
    # Existing Rules decide applicability alongside their factual scan so visible
    # abnormal evidence is not hidden by another partially redacted field.
    raise ApplicabilitySkipped(f"{code}: {reason}")


def append_bad_span_finding(
    bad_spans: list[dict[str, Any]],
    span_id: str | None,
    finding: dict[str, str],
) -> bool:
    if not span_id:
        return False
    for bad_span in bad_spans:
        if bad_span["span_id"] == span_id:
            if finding in bad_span["findings"]:
                return False
            bad_span["findings"].append(finding)
            return True
    bad_spans.append({"span_id": span_id, "findings": [finding]})
    return True


def chat_evidence_span_id(projection: Projection, field_name: str) -> str | None:
    if not isinstance(projection, CodingProjection):
        return None
    span_id = getattr(projection, field_name, None)
    return span_id if isinstance(span_id, str) else None


def get_tool_actions(projection: Projection) -> list[ToolAction]:
    if not isinstance(projection, CodingProjection):
        return extract_tool_actions(projection)
    return [
        replace(
            classify_tool_action(
                step_index=index,
                tool_name=action.name or "",
                arguments=action.arguments,
                result=action.result,
                status=action.status,
            ),
            source_span_id=action.source_span_id or None,
        )
        for index, action in enumerate(projection.tool_actions)
    ]


class BuiltinRuleExecutor(Executor):
    definition: ClassVar[BuiltinRuleDefinition]

    def __init__(self, spec: dict[str, Any]) -> None:
        super().__init__(spec)
        self._metrics = metric_definitions(spec)
        expected = [item["name"] for item in self.definition.metrics]
        actual = [metric.name for metric in self._metrics]
        if actual != expected:
            raise ValueError(
                f"builtin Rule {self.definition.key} requires metrics {expected}, got {actual}"
            )

    @abstractmethod
    def measure(
        self,
        trace: Trace,
        projection: Projection,
        metric: MetricDefinition,
    ) -> Metric: ...

    def evaluate(self, trace: Trace) -> DiagnosisResult:
        projection = trace.require_projection()
        metrics: list[Metric] = []
        for metric in self._metrics:
            result = self.measure(trace, projection, metric)
            if result.name != metric.name:
                raise ValueError(
                    f"builtin Rule {self.definition.key} returned metric {result.name!r}, "
                    f"expected {metric.name!r}"
                )
            metrics.append(result)
        return DiagnosisResult(metrics=metrics, execution=Execution.succeeded())

    def applicability_skip_reason(self, trace: Trace) -> str | None:
        projection = trace.require_projection()
        from agentlens.service.datasource.projection.registry import builtin_scenarios_for

        if self.definition.scenario not in builtin_scenarios_for(trace.runtime_type):
            return (
                f"not_applicable: runtime {trace.runtime_type} "
                f"does not support {self.definition.scenario}"
            )
        try:
            for metric in self._metrics:
                self.measure(trace, projection, metric)
        except ApplicabilitySkipped as skip:
            return skip.reason
        return None
