from dataclasses import dataclass
from datetime import datetime

from agentlens.domain.enums import TaskStatus
from agentlens.domain.task_stats import TaskStats


@dataclass
class Task:
    uuid: str
    job_uuid: str
    status: TaskStatus
    worker: str | None
    scheduled_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    attempt: int
    msg: str
    gmt_create: datetime
    gmt_modified: datetime
    creator: str | None
    modifier: str | None
    # Canonical source + diagnosers execution counters.
    stats: TaskStats | None = None
    execution_snapshot: dict | None = None
    judge_request_ledger: dict | None = None
