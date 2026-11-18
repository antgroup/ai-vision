"""Test-only constructors for shared, Coding, Framework, and raw Trace fixtures."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from agentlens.service.datasource.projection.models import (
    ContextEvidence,
    ExecutionStep,
    MessageEvidence,
    Projection,
    SourceReference,
    StepGroup,
    TextEvidence,
    TraceOutcome,
)
from agentlens.service.datasource.projection.projectors.coding.models import (
    CodingProjection,
    SubagentExecution,
    ToolAction,
)
from agentlens.service.datasource.projection.projectors.framework.models import FrameworkProjection
from agentlens.service.datasource.projection.registry import get_dedicated_projector
from agentlens.service.datasource.trace import Trace


def _refs(raw):
    return [item if isinstance(item, SourceReference) else SourceReference(**item) for item in raw]


def _evidence(raw, cls):
    if raw is None or isinstance(raw, cls):
        return raw
    return cls(**{**raw, "sources": _refs(raw.get("sources", []))})


def projection_from_dict(data: dict[str, Any]) -> Projection:
    outcome = data.get("outcome") or {}
    steps = []
    for index, step in enumerate(data.get("steps") or []):
        if isinstance(step, (ExecutionStep, StepGroup)):
            steps.append(step)
        elif step.get("kind") == "group":
            steps.append(StepGroup(**step))
        else:
            normalized = {
                **step,
                "name": step.get("name", step.get("node", "")),
                "ref": step.get("ref") or f"step-{index}",
            }
            steps.append(
                ExecutionStep(
                    **{
                        key: value
                        for key, value in normalized.items()
                        if key in {f.name for f in fields(ExecutionStep)}
                    }
                )
            )
    projector = get_dedicated_projector(str(data.get("runtime_type") or ""))
    cls = (
        FrameworkProjection
        if projector and projector.canonical_runtime == "langchain"
        else CodingProjection
        if projector and projector.canonical_runtime == "cc"
        else Projection
    )
    kwargs = dict(
        timestamp=data.get("timestamp"),
        user_query=str(data.get("user_query") or ""),
        agent_output=str(data.get("agent_output") or ""),
        outcome=TraceOutcome(
            bool(outcome.get("has_error")),
            outcome.get("result_state"),
            outcome.get("total_latency_ms"),
            bool(outcome.get("interrupted")),
        ),
        labels=dict(data.get("labels") or {}),
        node_seq=list(data.get("node_seq") or data.get("node_sequence") or []),
        steps=steps,
        current_request=_evidence(data.get("current_request"), TextEvidence),
        final_output=_evidence(data.get("final_output"), TextEvidence),
        observed_user_interruption=data.get("observed_user_interruption"),
    )
    for name, item_type in [
        ("conversation_history", MessageEvidence),
        ("provided_context", ContextEvidence),
        ("model_instructions", ContextEvidence),
        ("retrieval_observations", ContextEvidence),
    ]:
        raw = data.get(name)
        kwargs[name] = [_evidence(item, item_type) for item in raw] if raw is not None else None
    if issubclass(cls, CodingProjection):
        raw_actions = data.get("tool_actions")
        actions = []
        if raw_actions is not None:
            actions = [
                item if isinstance(item, ToolAction) else ToolAction(**item) for item in raw_actions
            ]
        else:
            for index, step in enumerate(steps):
                if isinstance(step, ExecutionStep) and step.type == "TOOL":
                    actions.append(
                        ToolAction(
                            action_id=f"action-{index}",
                            source_span_id=step.ref,
                            name=step.name,
                            arguments=step.input,
                            result=step.output,
                            status="success"
                            if step.status in {"OK", "success"}
                            else "error"
                            if step.status in {"ERROR", "failed", "error"}
                            else "unknown",
                        )
                    )
        kwargs.update(
            tool_actions=actions,
            user_query_span_id=data.get("user_query_span_id"),
            agent_output_span_id=data.get("agent_output_span_id"),
            subagents=[
                item if isinstance(item, SubagentExecution) else SubagentExecution(**item)
                for item in data.get("subagents") or []
            ],
        )
    return cls(**kwargs)


def trace_from_dict(data: dict[str, Any]) -> Trace:
    runtime = str(data.get("runtime_type") or "cc")
    return Trace(
        trace_id=str(data.get("trace_id") or ""),
        runtime_type=runtime,
        spans=list(data.get("spans") or []),
        owner_id=data.get("owner_id"),
        projection=projection_from_dict({**data, "runtime_type": runtime})
        if get_dedicated_projector(runtime)
        else None,
    )
