import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agentlens.infra.antcollector import AntcollectorClient
from agentlens.infra.ob.tables.dataset_table import DatasetTable
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.infra.signoz.client import SignozClient
from agentlens.infra.signoz.dialect import ColumnDialect
from agentlens.kernel.worker.runner import Runner
from agentlens.logging import get_logger
from agentlens.service.datasource.signoz import TraceStore
from agentlens.service.diagnosis_record_store import DiagnosisRecordStore
from agentlens.service.task_stats_store import TaskStatsStore
from agentlens.web.deps import (
    _engine_factory,
    get_antcollector_client,
    get_column_dialect,
    get_runtime,
    get_settings,
    get_signoz_client,
)

router = APIRouter(prefix="/internal", tags=["internal"])
_RUNNING_EXECUTIONS: set[asyncio.Task] = set()
_EXECUTION_SEMAPHORE: asyncio.Semaphore | None = None
_EXECUTION_LIMIT: int | None = None
_log = get_logger("runtime")


class ExecutePayload(BaseModel):
    task_uuid: str
    fenced_token: int


class NotifyPayload(BaseModel):
    task_uuid: str


def _make_runner(
    signoz: SignozClient,
    antcollector: AntcollectorClient,
    dialect: ColumnDialect,
    session: Session,
) -> Runner:
    settings = get_settings()
    return Runner(
        task_table=TaskTable(session),
        job_table=JobTable(session),
        dataset_table=DatasetTable(session),
        diagnoser_table=DiagnoserTable(session),
        model_connection_table=ModelConnectionTable(session),
        trace_store=TraceStore(signoz, dialect=dialect),
        record_store=DiagnosisRecordStore(
            signoz,
            antcollector,
            table=settings.diagnosis_record_table,
            dialect=dialect,
        ),
        task_stats_store=TaskStatsStore(
            signoz,
            antcollector,
            table=settings.diagnosis_record_table,
            dialect=dialect,
        ),
        self_uuid=settings.instance_uuid,
        session_factory=_engine_factory(),
    )


async def _run_task(
    task_uuid: str,
    signoz: SignozClient,
    antcollector: AntcollectorClient,
    dialect: ColumnDialect,
) -> None:
    session = _engine_factory()()
    try:
        await _make_runner(signoz, antcollector, dialect, session).run(task_uuid)
    finally:
        session.close()


def _on_execution_done(task: asyncio.Task) -> None:
    _RUNNING_EXECUTIONS.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # noqa: BLE001
        _log.warning("runtime.execute_task_fail", error=str(exc))


async def drain_execute_tasks() -> None:
    """Wait for accepted /internal/execute tasks before closing shared clients."""
    while _RUNNING_EXECUTIONS:
        tasks = list(_RUNNING_EXECUTIONS)
        await asyncio.gather(*tasks, return_exceptions=True)
        _RUNNING_EXECUTIONS.difference_update(task for task in tasks if task.done())


@router.post("/execute", status_code=202)
async def execute(
    payload: ExecutePayload,
    signoz: Annotated[SignozClient, Depends(get_signoz_client)],
    antcollector: Annotated[AntcollectorClient, Depends(get_antcollector_client)],
    dialect: Annotated[ColumnDialect, Depends(get_column_dialect)],
) -> dict:
    global _EXECUTION_LIMIT, _EXECUTION_SEMAPHORE
    limit = get_settings().worker_execute_concurrency
    if _EXECUTION_SEMAPHORE is None or _EXECUTION_LIMIT != limit:
        _EXECUTION_SEMAPHORE = asyncio.Semaphore(max(1, limit))
        _EXECUTION_LIMIT = limit
    if _EXECUTION_SEMAPHORE.locked():
        raise HTTPException(status_code=429, detail="worker execution concurrency limit reached")
    await _EXECUTION_SEMAPHORE.acquire()
    rt = get_runtime()
    if rt is not None and payload.fenced_token < rt.elector.fenced_token:
        _EXECUTION_SEMAPHORE.release()
        raise HTTPException(status_code=409, detail="stale fenced_token")

    async def run_with_slot() -> None:
        try:
            await _run_task(payload.task_uuid, signoz, antcollector, dialect)
        finally:
            _EXECUTION_SEMAPHORE.release()

    task = asyncio.create_task(run_with_slot(), name=f"runner:{payload.task_uuid}")
    _RUNNING_EXECUTIONS.add(task)
    task.add_done_callback(_on_execution_done)
    return {"status": "ack", "task_uuid": payload.task_uuid}


@router.post("/notify", status_code=200)
async def notify(payload: NotifyPayload) -> dict:
    rt = get_runtime()
    if rt is not None and rt.master is not None:
        await rt.master.dispatch.tick()
    return {"status": "noted", "task_uuid": payload.task_uuid}
