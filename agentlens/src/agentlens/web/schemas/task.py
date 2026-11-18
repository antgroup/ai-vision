from typing import Literal

from pydantic import BaseModel

from agentlens.domain.enums import TaskStatus
from agentlens.domain.judge_usage import JudgeRequestSummary
from agentlens.domain.task_stats import TaskStats
from agentlens.web.schemas.common import LocalDateTime


class TaskOut(BaseModel):
    uuid: str
    job_uuid: str
    status: TaskStatus
    worker: str | None
    scheduled_at: LocalDateTime
    started_at: LocalDateTime | None
    finished_at: LocalDateTime | None
    attempt: int
    msg: str
    stats: TaskStats | None = None
    judge_request_summary: JudgeRequestSummary | None = None
    judge_request_summary_state: Literal["not_enabled", "available", "unavailable"] = "not_enabled"
    definition_source: str = "legacy_current_definition"
    diagnoser_definitions: dict = {}
    gmt_create: LocalDateTime
    gmt_modified: LocalDateTime
