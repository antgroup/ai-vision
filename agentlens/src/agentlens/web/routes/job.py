from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from agentlens.diagnoser.registry import is_copied_rule_spec, is_specialist_builtin
from agentlens.domain.enums import DiagnoserType, TaskStatus
from agentlens.domain.errors import Conflict, NotFound, Validation
from agentlens.domain.job import Job, effective_job_spec
from agentlens.domain.scope import visible_space_uuids
from agentlens.domain.task import Task
from agentlens.infra.ob.tables.dataset_table import DatasetTable
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.kernel.notify import NotifyClient
from agentlens.service.datasource.projection.registry import (
    builtin_scenarios_for,
)
from agentlens.service.datasource.runtime import supports_specialist_runtime, validate_runtime_type
from agentlens.service.execution_snapshot import (
    snapshot_from_tables,
    validate_current_managed_definitions,
)
from agentlens.service.job_schedule import validate_job_schedule
from agentlens.utils.uuid_gen import new_job_uuid, new_task_uuid
from agentlens.web.deps import (
    SettingsDep,
    get_dataset_table,
    get_diagnoser_table,
    get_job_table,
    get_task_table,
)
from agentlens.web.schemas.common import ListResponse
from agentlens.web.schemas.job import JobCreate, JobOut, JobTriggerResponse, JobUpdate

router = APIRouter(prefix="/api/v1", tags=["job"])
JobsDep = Annotated[JobTable, Depends(get_job_table)]
TasksDep = Annotated[TaskTable, Depends(get_task_table)]
DatasetsDep = Annotated[DatasetTable, Depends(get_dataset_table)]
DiagnosersDep = Annotated[DiagnoserTable, Depends(get_diagnoser_table)]


def _validate_job_bindings(
    space_uuid: str,
    agent_uuid: str,
    dataset_uuid: str,
    diagnoser_uuids: list[str],
    datasets: DatasetTable,
    diagnosers: DiagnoserTable,
    *,
    lock_resources: bool = False,
) -> None:
    """Validate exact-scope Dataset and Diagnoser bindings.

    Raises Validation (422) — this is caller-provided-binding rejection, not
    cross-scope existence probing, so 422 is correct (not the 404 used for
    detail/mutation operations against a specific resource UUID).
    """
    dataset = (
        datasets.get_by_uuid_for_update(dataset_uuid)
        if lock_resources
        else datasets.get_by_uuid(dataset_uuid)
    )
    if dataset is None:
        raise Validation(f"dataset {dataset_uuid!r} not found")
    runtime_type = validate_runtime_type(dataset.spec.get("runtime_type"))
    if (dataset.space_uuid, dataset.agent_uuid) != (space_uuid, agent_uuid):
        raise Validation(f"dataset {dataset_uuid!r} does not belong to this job's scope")
    bound_by_uuid = {}
    for diagnoser_uuid in sorted(set(diagnoser_uuids)):
        d = (
            diagnosers.get_by_uuid_for_update(diagnoser_uuid)
            if lock_resources
            else diagnosers.get_by_uuid(diagnoser_uuid)
        )
        if d is None:
            raise Validation(f"diagnoser {diagnoser_uuid!r} not found")
        if (d.space_uuid, d.agent_uuid) != (space_uuid, agent_uuid):
            raise Validation(f"diagnoser {diagnoser_uuid!r} does not belong to this job's scope")
        bound_by_uuid[diagnoser_uuid] = d
    bound_diagnosers = [bound_by_uuid[uuid] for uuid in diagnoser_uuids]
    validate_current_managed_definitions(bound_diagnosers)
    if any(
        is_specialist_builtin(d.type, d.spec)
        and not supports_specialist_runtime(runtime_type, key=d.spec.get("builtin_key"))
        for d in bound_diagnosers
    ):
        raise Validation("Specialist runtime mapping is unavailable")

    builtin_scenarios = builtin_scenarios_for(runtime_type)
    unsupported_builtins = [
        diagnoser.name
        for diagnoser in bound_diagnosers
        if (diagnoser.type == DiagnoserType.BUILTIN or is_copied_rule_spec(diagnoser.spec))
        and not is_specialist_builtin(diagnoser.type, diagnoser.spec)
        and diagnoser.spec.get("scenario")
        and diagnoser.spec.get("scenario") not in builtin_scenarios
    ]
    if unsupported_builtins:
        raise Validation(
            f"runtime_type {runtime_type!r} 不适用内置诊断器：" + ", ".join(unsupported_builtins)
        )


def _out(j: Job) -> JobOut:
    return JobOut(
        uuid=j.uuid,
        space_uuid=j.space_uuid,
        agent_uuid=j.agent_uuid,
        name=j.name,
        description=j.description,
        trigger_type=j.trigger_type,
        schedule_cron=j.schedule_cron,
        status=j.status,
        dataset_uuid=j.dataset_uuid,
        diagnoser_uuids=j.diagnoser_uuids,
        spec=effective_job_spec(j.spec),
        gmt_create=j.gmt_create,
        gmt_modified=j.gmt_modified,
        creator=j.creator,
        modifier=j.modifier,
    )


@router.post("/job", status_code=201, response_model=JobOut)
def create_job(
    payload: JobCreate, table: JobsDep, datasets: DatasetsDep, diagnosers: DiagnosersDep
) -> JobOut:
    validate_job_schedule(payload.trigger_type, payload.schedule_cron)
    _validate_job_bindings(
        payload.space_uuid,
        payload.agent_uuid,
        payload.dataset_uuid,
        payload.diagnoser_uuids,
        datasets,
        diagnosers,
        lock_resources=True,
    )
    creator = payload.creator or payload.modifier
    modifier = payload.modifier or payload.creator
    j = Job(
        uuid=new_job_uuid(),
        space_uuid=payload.space_uuid,
        agent_uuid=payload.agent_uuid,
        name=payload.name,
        description=payload.description,
        trigger_type=payload.trigger_type,
        schedule_cron=payload.schedule_cron,
        status=payload.status,
        dataset_uuid=payload.dataset_uuid,
        diagnoser_uuids=payload.diagnoser_uuids,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        creator=creator,
        modifier=modifier,
        spec=payload.spec,
    )
    try:
        table.insert(j)
    except IntegrityError as e:
        raise Conflict(f"job {payload.name!r} already exists") from e
    return _out(table.get_by_uuid(j.uuid))


@router.get("/job/{uuid}", response_model=JobOut)
def get_job(uuid: str, table: JobsDep) -> JobOut:
    j = table.get_by_uuid(uuid)
    if j is None:
        raise NotFound(f"job {uuid!r} not found")
    return _out(j)


@router.get("/jobs", response_model=ListResponse[JobOut])
def list_jobs(
    table: JobsDep,
    space_uuid: str = Query(..., min_length=1),
    agent_uuid: str = Query(..., min_length=1),
    include_user_shared: bool = Query(False),
) -> ListResponse[JobOut]:
    rows = table.list_all(
        agent_uuid=agent_uuid,
        space_uuids=visible_space_uuids(
            space_uuid,
            include_user_shared=include_user_shared,
        ),
    )
    return ListResponse(items=[_out(j) for j in rows], total=len(rows))


@router.patch("/job/{uuid}", response_model=JobOut)
def update_job(
    uuid: str,
    payload: JobUpdate,
    table: JobsDep,
    datasets: DatasetsDep,
    diagnosers: DiagnosersDep,
) -> JobOut:
    # Share the Job lock with trigger/schedule and retirement phases, then use
    # the fresh bindings while Dataset/Diagnoser locks remain in this transaction.
    current = table.get_by_uuid_for_update(uuid)
    if current is None:
        raise NotFound(f"job {uuid!r} not found")
    if (current.spec or {}).get("managed_by") == "aivision.quick_diagnosis":
        raise Conflict("managed quick Job cannot be edited")
    fields: dict = {}
    for k, v in payload.model_dump(exclude_unset=True).items():
        if k == "spec":
            if v:
                fields[k] = {**current.spec, **v}
            continue
        if k == "status" and v is not None:
            fields[k] = v.value if hasattr(v, "value") else v
        else:
            fields[k] = v
    validate_job_schedule(
        fields.get("trigger_type", current.trigger_type),
        fields.get("schedule_cron", current.schedule_cron),
    )
    # A status-only enable is also execution authorization, including old saved Jobs.
    if "dataset_uuid" in fields or "diagnoser_uuids" in fields or fields.get("status") == "enabled":
        next_dataset_uuid = fields.get("dataset_uuid", current.dataset_uuid)
        next_diagnoser_uuids = fields.get("diagnoser_uuids", current.diagnoser_uuids)
        _validate_job_bindings(
            current.space_uuid,
            current.agent_uuid,
            next_dataset_uuid,
            next_diagnoser_uuids,
            datasets,
            diagnosers,
            lock_resources=True,
        )
    if fields.get("modifier") and not current.creator:
        fields["creator"] = fields["modifier"]
    if fields:
        table.update_by_uuid(uuid, fields)
    return _out(table.get_by_uuid(uuid))


@router.delete("/job/{uuid}", status_code=204)
def delete_job(
    uuid: str,
    table: JobsDep,
    tasks: TasksDep,
) -> None:
    current = table.get_by_uuid(uuid)
    if current is None:
        raise NotFound(f"job {uuid!r} not found")
    if (current.spec or {}).get("managed_by") == "aivision.quick_diagnosis":
        raise Conflict("managed quick Job retains its request receipt")
    if tasks.find_active_by_job(uuid) is not None:
        raise Conflict("job has an active task; deletion is blocked")
    tasks.delete_by_job_uuid(uuid)
    if not table.delete_by_uuid(uuid):
        raise NotFound(f"job {uuid!r} not found")


@router.post("/job/{uuid}/trigger", status_code=202, response_model=JobTriggerResponse)
async def trigger_job(
    uuid: str,
    jobs: JobsDep,
    tasks: TasksDep,
    datasets: DatasetsDep,
    diagnosers: DiagnosersDep,
    settings: SettingsDep,
) -> JobTriggerResponse:
    j = jobs.get_by_uuid(uuid)
    if j is None:
        raise NotFound(f"job {uuid!r} not found")
    if (j.spec or {}).get("managed_by") == "aivision.quick_diagnosis":
        raise Conflict("managed quick Job cannot be triggered through generic API")
    if j.status.value == "disabled":
        raise Validation("job is disabled")
    # Validate bindings before creating the Task so invalid configuration
    # returns 422 at the Web boundary.
    _validate_job_bindings(
        j.space_uuid,
        j.agent_uuid,
        j.dataset_uuid,
        j.diagnoser_uuids,
        datasets,
        diagnosers,
    )
    # Idempotent + race-safe: lock the Job row, then find-or-create.
    # This prevents two concurrent triggers from both creating a Task.
    try:
        tasks.s.execute(
            text("SELECT uuid FROM agentlens_job WHERE uuid=:u FOR UPDATE"), {"u": uuid}
        )
        # Reload and recheck eligibility after the lock; the initial read may be stale.
        j = jobs.get_by_uuid_for_update(uuid)
        if j is None:
            raise NotFound(f"job {uuid!r} not found")
        if j.status.value == "disabled":
            raise Validation("job is disabled")
        active = tasks.find_active_by_job(j.uuid)
        if active is not None:
            tasks.s.commit()
            return JobTriggerResponse(task_uuid=active.uuid)
        snapshot = snapshot_from_tables(j, datasets, diagnosers, ModelConnectionTable(tasks.s))
        t = Task(
            uuid=new_task_uuid(),
            job_uuid=j.uuid,
            status=TaskStatus.PENDING,
            worker=None,
            scheduled_at=datetime.now(),
            started_at=None,
            finished_at=None,
            attempt=0,
            msg="",
            gmt_create=datetime.now(),
            gmt_modified=datetime.now(),
            creator=None,
            modifier=None,
            execution_snapshot=snapshot,
        )
        tasks.insert(t)
    except OperationalError as exc:
        tasks.s.rollback()
        if getattr(exc.orig, "args", ()) and exc.orig.args[0] == 6235:
            raise Conflict("configuration changed while creating task; retry") from None
        raise
    # best-effort notify master; RescueLoop covers if this fails
    try:
        master_row = (
            tasks.s.execute(
                text("""
            SELECT holder_uuid FROM agentlens_leader_lease WHERE lock_key='master'
        """)
            )
            .mappings()
            .first()
        )
        if master_row and master_row["holder_uuid"] != "__bootstrap__":
            async with NotifyClient(token=settings.token, port=settings.port) as nc:
                await nc.notify_master(master_row["holder_uuid"], t.uuid)
    except Exception:  # noqa: BLE001
        pass  # RescueLoop will pick it up
    return JobTriggerResponse(task_uuid=t.uuid)
