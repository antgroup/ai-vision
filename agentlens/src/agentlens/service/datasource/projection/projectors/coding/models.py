"""Coding projection models: ToolAction, FileMutation, VerificationAction.

These extend Projection with coding-agent-specific facts extracted
from raw span protocols. Every fact links back to a source span.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from agentlens.service.datasource.projection.models import Projection, SourceReference


@dataclass(frozen=True)
class ToolAction:
    """One tool call extracted from a span, with standard GenAI identity.

    identity_source records whether the tool-call ID/name came from the
    standard gen_ai.tool.call.id field, the span name, or was inferred.
    Missing call_id/name remains None — never fabricated.

    New optional fields come from standard OTel/GenAI attributes when
    present; None when absent. Old snapshots read them as None.
    """

    action_id: str
    source_span_id: str
    call_id: str | None = None
    name: str | None = None
    raw_name: str | None = None
    tool_type: str | None = None
    operation_name: str | None = None
    identity_source: Literal["tool_call", "span", "inferred"] = "span"
    arguments: Any = None
    result: Any = None
    status: Literal["success", "error", "unknown"] = "unknown"
    error_type: str | None = None
    duration_ms: int | None = None
    input_truncated: bool | None = None
    output_truncated: bool | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    field_sources: dict[str, SourceReference] = field(default_factory=dict)


@dataclass(frozen=True)
class FileMutation:
    """A code or configuration file write derived from a ToolAction.

    evidence_level: exact = parsed path+content; file_only = path known,
    content not parseable; inferred = path guessed from tool args.
    """

    source_action_id: str
    source_span_id: str
    path: str
    operation: Literal["edit", "write", "patch", "delete", "create"] = "edit"
    before: Any = None
    after: Any = None
    changed_regions: list[str] = field(default_factory=list)
    evidence_level: Literal["exact", "file_only", "inferred"] = "file_only"


@dataclass(frozen=True)
class VerificationAction:
    """A test/build/lint run that verifies mutations, not a write itself."""

    source_action_id: str
    source_span_id: str
    command: str | None = None
    result: Literal["passed", "failed", "unknown"] = "unknown"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    field_sources: dict[str, SourceReference] = field(default_factory=dict)


@dataclass(frozen=True)
class SubagentExecution:
    """A sub-agent invocation within the parent coding session."""

    source_span_id: str
    subagent_id: str | None = None
    description: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


@dataclass(kw_only=True)
class CodingProjection(Projection):
    """Coding-specific Projection with verified tool and subagent facts."""

    user_query_span_id: str | None = None
    agent_output_span_id: str | None = None
    tool_actions: list[ToolAction] = field(default_factory=list)

    @property
    def file_mutations(self) -> list[FileMutation]:
        from agentlens.service.datasource.projection.projectors.coding.adapter import (
            derive_file_mutations,
        )

        return derive_file_mutations(self.tool_actions)

    @property
    def verification_actions(self) -> list[VerificationAction]:
        from agentlens.service.datasource.projection.projectors.coding.adapter import (
            derive_verification_actions,
        )

        return derive_verification_actions(self.tool_actions)

    subagents: list[SubagentExecution] = field(default_factory=list)
