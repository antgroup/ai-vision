"""Unified quality dashboard statistics endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from agentlens.domain.errors import NotFound
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.service.diagnosis_stats_service import DiagnosisStatsService
from agentlens.service.task_stats_store import TaskStatsStore
from agentlens.web.deps import (
    get_diagnoser_table,
    get_job_table,
    get_task_stats_store,
    get_task_table,
)
from agentlens.web.schemas.diagnosis_stats import DiagnosisStatsResponse

router = APIRouter(prefix="/api/v1", tags=["diagnosis-stats"])

DiagnoserDep = Annotated[DiagnoserTable, Depends(get_diagnoser_table)]
JobDep = Annotated[JobTable, Depends(get_job_table)]
TaskDep = Annotated[TaskTable, Depends(get_task_table)]
TaskStatsDep = Annotated[TaskStatsStore, Depends(get_task_stats_store)]


@router.get("/diagnosis-stats", response_model=DiagnosisStatsResponse)
def list_diagnosis_stats(
    diagnosers: DiagnoserDep,
    jobs: JobDep,
    task_stats: TaskStatsDep,
    tasks: TaskDep,
    job_uuid: str = Query(..., min_length=1),
    task_uuid: str | None = Query(None, min_length=1, max_length=128),
    start_time: str | None = None,
    end_time: str | None = None,
    owner_id: str | None = Query(None, min_length=1, max_length=128),
) -> DiagnosisStatsResponse:
    job = jobs.get_by_uuid(job_uuid)
    if job is None:
        raise NotFound(f"job {job_uuid!r} not found")
    if task_uuid is not None:
        task = tasks.get_by_uuid(task_uuid)
        if task is None or task.job_uuid != job.uuid:
            raise NotFound("Task is not in the requested Job")
    result = DiagnosisStatsService(diagnosers, task_stats).list_stats(
        job=job,
        task_uuid=task_uuid,
        owner_id=owner_id,
        start_time=start_time,
        end_time=end_time,
    )
    return DiagnosisStatsResponse.model_validate(result)
