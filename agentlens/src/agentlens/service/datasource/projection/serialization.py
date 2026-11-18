"""One-way, bounded serialization for typed trace projections."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import TYPE_CHECKING, Any

from agentlens.service.datasource.projection.models import ExecutionStep, StepGroup
from agentlens.service.datasource.projection.projectors.coding.models import CodingProjection

if TYPE_CHECKING:
    from agentlens.service.datasource.trace import Trace

_MAX_DEPTH = 8
_MAX_LIST_ITEMS = 100
_MAX_TEXT_CHARS = 20_000


@dataclass(frozen=True)
class TraceSnapshot:
    user_query: str | None
    agent_output: str | None
    outcome: dict[str, Any]
    labels: dict[str, Any]
    node_sequence: list[str]


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        return "…(truncated)"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT_CHARS else value[:_MAX_TEXT_CHARS] + "…(truncated)"
    if is_dataclass(value) and not isinstance(value, type):
        return _bounded(asdict(value), depth=depth)
    if isinstance(value, dict):
        return {str(key): _bounded(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_bounded(item, depth=depth + 1) for item in value[:_MAX_LIST_ITEMS]]
        if len(value) > _MAX_LIST_ITEMS:
            items.append({"truncated_items": len(value) - _MAX_LIST_ITEMS})
        return items
    return _bounded(str(value), depth=depth + 1)


def _serialize_coding(trace: CodingProjection) -> dict[str, Any]:
    """Serialize Coding facts derived from the canonical tool actions."""
    from agentlens.service.datasource.projection.projectors.coding.adapter import (
        derive_file_mutations,
        derive_verification_actions,
    )

    return {
        "user_query_span_id": trace.user_query_span_id,
        "agent_output_span_id": trace.agent_output_span_id,
        "tool_actions": _bounded(trace.tool_actions),
        "file_mutations": _bounded(derive_file_mutations(trace.tool_actions)),
        "verification_actions": _bounded(derive_verification_actions(trace.tool_actions)),
        "subagents": _bounded(trace.subagents),
    }


def serialize_projection(projection: Any) -> dict[str, Any]:
    """Serialize one typed projection; output is never parsed back."""
    steps: list[dict[str, Any]] = []
    for step in projection.steps:
        if isinstance(step, ExecutionStep):
            data = {
                "kind": step.kind,
                "index": step.index,
                "node": step.name,
                "type": step.type,
                "status": step.status,
                "latency_ms": step.latency_ms,
                "usage": step.usage,
                "input": step.input,
                "output": step.output,
                "span_id": step.ref or None,
            }
            if step.model:
                data["model"] = step.model
            steps.append(_bounded(data))
        elif isinstance(step, StepGroup):
            steps.append(
                _bounded(
                    {
                        "kind": step.kind,
                        "node": step.name,
                        "type": step.type,
                        "target": step.target,
                        "child_count": step.child_count,
                    }
                )
            )
        else:
            raise TypeError(f"unexpected TraceStep type: {type(step).__name__}")
    payload = {
        "projection_kind": "semantic",
        "timestamp": projection.timestamp,
        "user_query": _bounded(projection.user_query),
        "agent_output": _bounded(projection.agent_output),
        "outcome": _bounded(asdict(projection.outcome)),
        "labels": _bounded(projection.labels),
        "node_sequence": list(projection.node_seq),
        "steps": steps,
        **{
            name: _bounded(getattr(projection, name))
            for name in (
                "current_request",
                "final_output",
                "conversation_history",
                "provided_context",
                "model_instructions",
                "retrieval_observations",
                "observed_user_interruption",
            )
        },
        **(_serialize_coding(projection) if isinstance(projection, CodingProjection) else {}),
    }
    return payload


def serialize_trace_projection(trace: Trace) -> dict[str, Any]:
    """Serialize the public projection response with outer Trace identity."""
    projection = trace.require_projection()
    return {
        "trace_id": trace.trace_id,
        "runtime_type": trace.runtime_type,
        **serialize_projection(projection),
    }


def projection_expression_data(projection: dict[str, Any]) -> dict[str, Any]:
    """Add public variable aliases to a serialized semantic projection."""
    data = dict(projection)
    if "history" not in data and "conversation_history" in data:
        data["history"] = data["conversation_history"]
    return data


def resolve_projection_expression(projection: Any, expression: str) -> Any:
    """Resolve a path against the bounded, public semantic projection.

    Custom diagnosers may drill into supported projection facts such as
    ``history[0].content`` or ``steps[2].output``.  The path
    never receives source rows or raw OTel attributes for a typed runtime.
    """
    from agentlens.service.datasource.projection.expressions import query

    return query(expression, projection_expression_data(serialize_projection(projection)))


def build_trace_snapshot(projection: Any) -> TraceSnapshot:
    return TraceSnapshot(
        user_query=_bounded(projection.user_query),
        agent_output=_bounded(projection.agent_output),
        outcome=_bounded(asdict(projection.outcome)),
        labels=_bounded(projection.labels),
        node_sequence=list(projection.node_seq),
    )


def serialize_snapshot(projection: Any) -> dict[str, Any]:
    return asdict(build_trace_snapshot(projection))
