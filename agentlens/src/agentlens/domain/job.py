from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agentlens.domain.enums import JobStatus, JobTriggerType

SAVE_BADCASE_ONLY = "save_badcase_only"


def effective_job_spec(spec: Mapping[str, Any] | None) -> dict[str, Any]:
    effective = dict(spec or {})
    effective.setdefault(SAVE_BADCASE_ONLY, True)
    return effective


def job_saves_badcase_only(spec: Mapping[str, Any] | None) -> bool:
    value = effective_job_spec(spec)[SAVE_BADCASE_ONLY]
    if not isinstance(value, bool):
        raise ValueError(f"job spec {SAVE_BADCASE_ONLY!r} must be a bool")
    return value


@dataclass
class Job:
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
    gmt_create: datetime
    gmt_modified: datetime
    creator: str | None
    modifier: str | None
    spec: dict[str, Any] = field(default_factory=dict)
