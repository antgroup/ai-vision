from typing import Any

from pydantic import BaseModel

from agentlens.domain.enums import ExecutionStatus, ReviewStatus, TargetType, Verdict
from agentlens.web.schemas.common import LocalDateTime
from agentlens.web.schemas.feedback import FeedbackContent


class MetricOut(BaseModel):
    name: str
    verdict: Verdict
    reason: str
    evidence: dict[str, Any]


class ExecutionOut(BaseModel):
    status: ExecutionStatus
    code: str
    msg: str
    evidence: dict[str, Any]


class DiagnosisRecordListItemOut(BaseModel):
    space_uuid: str = ""
    diagnoser_definition: dict[str, Any] | None = None
    uuid: str
    task_uuid: str
    job_uuid: str
    diagnoser_uuid: str
    diagnoser_version: str
    target_type: TargetType
    target_id: str
    owner_id: str | None
    exp_ver_id: str
    model: str
    verdict: Verdict
    review_status: ReviewStatus
    summary: str
    reviewed_at: LocalDateTime | None
    diagnosed_at: LocalDateTime | None
    target_created_at: LocalDateTime | None


class DiagnosisRecordOut(BaseModel):
    space_uuid: str = ""
    diagnoser_definition: dict[str, Any] | None = None
    uuid: str
    task_uuid: str
    job_uuid: str
    diagnoser_uuid: str
    diagnoser_version: str
    target_type: TargetType
    target_id: str
    owner_id: str | None
    exp_ver_id: str
    model: str
    verdict: Verdict
    summary: str
    execution: ExecutionOut
    trace_snapshot: dict[str, Any]
    review_status: ReviewStatus
    feedback: FeedbackContent | None
    reviewed_by: str | None
    reviewed_at: LocalDateTime | None
    diagnosed_at: LocalDateTime | None
    target_created_at: LocalDateTime | None
    metrics: list[MetricOut]
