"""Unified diagnosis statistics response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agentlens.web.schemas.common import LocalDateTime


class DiagnosisMetricStats(BaseModel):
    id: str
    name: str
    score: float | None
    total: int
    good: int
    bad: int
    failed: int
    skipped: int


class DiagnosisDiagnoserStats(BaseModel):
    uuid: str
    name: str
    weight: float
    score: float | None
    score_role: Literal["quality", "signal"] | None = None
    version: str | None = None
    revision: str | None = None
    digest: str | None = None
    key: str | None = None
    metrics: list[DiagnosisMetricStats] = Field(default_factory=list)


class DiagnosisCategoryStats(BaseModel):
    id: str
    name: str
    score: float | None
    diagnosers: list[DiagnosisDiagnoserStats] = Field(default_factory=list)


class DiagnosisCategoryDefinition(BaseModel):
    id: str
    name: str


class DiagnosisGlobalStats(BaseModel):
    score: float | None
    categories: list[DiagnosisCategoryStats] = Field(default_factory=list)


class DiagnosisSelectorStats(BaseModel):
    type: str
    status: Literal["complete", "partial", "failed"]
    scanned_targets: int
    scanned_users: int
    selected_targets: int
    selected_users: int
    rejected_targets: int
    rejected_users: int
    invalid_targets: int
    unresolved_targets: int
    unresolved_users: int
    total_batches: int
    failed_batches: int


class DiagnosisTaskStats(BaseModel):
    task_uuid: str
    time: LocalDateTime
    target_type: Literal["trace"] = "trace"
    target_count: int
    schema_version: Literal[3] | None = None
    source_complete: bool | None = None
    score_policy_version: str | None = None
    definition_source: Literal["task_snapshot", "legacy_current_definition"] = (
        "legacy_current_definition"
    )
    skip_reasons: dict[str, dict[str, int]] = Field(default_factory=dict)
    selector: DiagnosisSelectorStats | None = None
    global_: DiagnosisGlobalStats = Field(alias="global", serialization_alias="global")


class DiagnosisStatsResponse(BaseModel):
    categories: list[DiagnosisCategoryDefinition]
    tasks: list[DiagnosisTaskStats] = Field(default_factory=list)
