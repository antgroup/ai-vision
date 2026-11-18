from typing import Any

from pydantic import BaseModel, Field, field_validator

from agentlens.domain.enums import JobStatus, JobTriggerType
from agentlens.domain.job import SAVE_BADCASE_ONLY, effective_job_spec
from agentlens.web.schemas.common import LocalDateTime, ResourceScopeCreate


def _validate_job_spec(value: dict[str, Any]) -> dict[str, Any]:
    if SAVE_BADCASE_ONLY in value and not isinstance(value[SAVE_BADCASE_ONLY], bool):
        raise ValueError(f"{SAVE_BADCASE_ONLY} must be a bool")
    return value


class JobCreate(ResourceScopeCreate):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    trigger_type: JobTriggerType
    schedule_cron: str | None = None
    status: JobStatus = JobStatus.ENABLED
    dataset_uuid: str
    diagnoser_uuids: list[str]
    spec: dict[str, Any] = Field(default_factory=lambda: effective_job_spec(None))
    creator: str | None = None
    modifier: str | None = None

    @field_validator("spec")
    @classmethod
    def validate_spec(cls, value: dict[str, Any]) -> dict[str, Any]:
        return effective_job_spec(_validate_job_spec(value))


class JobUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    trigger_type: JobTriggerType | None = None
    schedule_cron: str | None = None
    status: JobStatus | None = None
    dataset_uuid: str | None = None
    diagnoser_uuids: list[str] | None = None
    spec: dict[str, Any] | None = None
    modifier: str | None = None

    @field_validator("spec")
    @classmethod
    def validate_spec(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        if value is None:
            raise ValueError("spec must be an object")
        return _validate_job_spec(value)


class JobOut(BaseModel):
    uuid: str
    space_uuid: str
    agent_uuid: str
    name: str
    description: str
    trigger_type: JobTriggerType
    schedule_cron: str | None
    status: JobStatus
    dataset_uuid: str
    diagnoser_uuids: list[str]
    spec: dict[str, Any]
    gmt_create: LocalDateTime
    gmt_modified: LocalDateTime
    creator: str | None
    modifier: str | None


class JobTriggerResponse(BaseModel):
    task_uuid: str
