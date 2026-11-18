"""Shared diagnosis-ready Projection models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class TraceOutcome:
    """Execution outcome shared by every runtime projection."""

    has_error: bool
    result_state: str | None
    total_latency_ms: int | None
    interrupted: bool


@dataclass
class ExecutionStep:
    """One runtime execution step with optional I/O."""

    kind: Literal["execution"] = "execution"
    name: str = ""
    type: str = ""
    status: str = ""
    latency_ms: int | None = None
    input: Any = None
    output: Any = None
    index: int | None = None
    depth: int | None = None
    model: str | None = None
    usage: dict[str, Any] | None = None
    ref: str = ""


@dataclass
class StepGroup:
    """A compressed step group referencing sibling projection data."""

    kind: Literal["group"] = "group"
    target: Literal["history", "retrieval", "graph_trajectory"] = "retrieval"
    name: str = ""
    type: str = ""
    child_count: int = 0
    ref: str = ""


TraceStep = ExecutionStep | StepGroup


@dataclass(frozen=True)
class SourceReference:
    trace_id: str
    span_id: str | None
    field_path: str
    call_id: str | None = None


@dataclass(frozen=True)
class TextEvidence:
    content: str
    sources: list[SourceReference]


@dataclass(frozen=True)
class MessageEvidence(TextEvidence):
    content: str | None
    role: str


@dataclass(frozen=True)
class ContextEvidence(TextEvidence):
    kind: str
    producer_source: SourceReference | None = None


@dataclass(kw_only=True)
class Projection:
    """Runtime-owned semantic facts shared by all Projector families."""

    timestamp: str | None
    user_query: str
    agent_output: str
    outcome: TraceOutcome
    labels: dict[str, Any]
    node_seq: list[str] = field(default_factory=list)
    steps: list[TraceStep] = field(default_factory=list)
    current_request: TextEvidence | None = None
    final_output: TextEvidence | None = None
    conversation_history: list[MessageEvidence] | None = None
    provided_context: list[ContextEvidence] | None = None
    model_instructions: list[ContextEvidence] | None = None
    retrieval_observations: list[ContextEvidence] | None = None
    observed_user_interruption: bool | None = None

    @property
    def intent(self) -> str | None:
        return self.labels.get("intent") or None
