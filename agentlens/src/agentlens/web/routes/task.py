from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from agentlens.domain.enums import TaskStatus
from agentlens.domain.errors import Conflict, NotFound, Validation
from agentlens.domain.judge_usage import judge_request_summary
from agentlens.domain.task import Task
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.service.execution_snapshot import snapshot_definitions
from agentlens.web.deps import get_task_table
from agentlens.web.schemas.common import ListResponse
from agentlens.web.schemas.task import TaskOut

router = APIRouter(prefix="/api/v1", tags=["task"])
TasksDep = Annotated[TaskTable, Depends(get_task_table)]


def _out(t: Task) -> TaskOut:
    definition_source = "legacy_current_definition"
    definitions = {}
    if t.execution_snapshot is not None:
        try:
            definitions = snapshot_definitions(t.execution_snapshot)
            definition_source = "task_snapshot"
        except (Validation, KeyError, TypeError, ValueError):
            definition_source = "unsupported_snapshot"
    request_summary = None
    summary_state = "not_enabled"
    ledger = getattr(t, "judge_request_ledger", None)
    snapshot = t.execution_snapshot if isinstance(t.execution_snapshot, dict) else {}
    if ledger is not None or snapshot.get("execution_policy") is not None:
        try:
            request_summary = judge_request_summary(ledger)
            summary_state = "available"
        except (TypeError, ValueError, KeyError):
            summary_state = "unavailable"
    return TaskOut(
        judge_request_summary=request_summary,
        judge_request_summary_state=summary_state,
        uuid=t.uuid,
        job_uuid=t.job_uuid,
        status=t.status,
        worker=t.worker,
        scheduled_at=t.scheduled_at,
        started_at=t.started_at,
        finished_at=t.finished_at,
        attempt=t.attempt,
        msg=t.msg,
        stats=t.stats,
        definition_source=definition_source,
        diagnoser_definitions=definitions,
        gmt_create=t.gmt_create,
        gmt_modified=t.gmt_modified,
    )


@router.get("/tasks", response_model=ListResponse[TaskOut])
def list_tasks(
    table: TasksDep,
    job_uuid: str = Query(..., min_length=1),
    status: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ListResponse[TaskOut]:
    rows = table.list_tasks(
        status=status,
        job_uuid=job_uuid,
        scheduled_start_time=start_time,
        scheduled_end_time=end_time,
        limit=limit,
        offset=offset,
    )
    total = table.count_tasks(
        status=status,
        job_uuid=job_uuid,
        scheduled_start_time=start_time,
        scheduled_end_time=end_time,
    )
    return ListResponse(items=[_out(t) for t in rows], total=total)


@router.get("/task/{uuid}", response_model=TaskOut)
def get_task(
    uuid: str,
    table: TasksDep,
) -> TaskOut:
    t = table.get_by_uuid(uuid)
    if t is None:
        raise NotFound(f"task {uuid!r} not found")
    return _out(t)


@router.delete("/task/{uuid}", status_code=204)
def delete_task(
    uuid: str,
    table: TasksDep,
) -> None:
    t = table.get_by_uuid(uuid)
    if t is None:
        raise NotFound(f"task {uuid!r} not found")
    if not table.delete_by_uuid(uuid):
        raise NotFound(f"task {uuid!r} not found")


@router.post("/task/{uuid}/cancel", response_model=TaskOut)
def cancel_task(
    uuid: str,
    table: TasksDep,
) -> TaskOut:
    t = table.get_by_uuid(uuid)
    if t is None:
        raise NotFound(f"task {uuid!r} not found")
    if t.status not in (TaskStatus.PENDING, TaskStatus.DISPATCHED):
        raise Conflict(f"task in {t.status.value} cannot be canceled")
    if not table.cas_cancel(uuid):
        raise Conflict("task status changed before cancellation")
    return _out(table.get_by_uuid(uuid))
