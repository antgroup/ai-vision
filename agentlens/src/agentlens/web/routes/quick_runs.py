"""Platform-token-only trusted quick orchestration; end-user ACL lives in Django."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from agentlens.service.datasource.signoz import TraceStore
from agentlens.service.quick_runs import (
    preflight,
    public_catalog,
    run_quick,
    saved_options,
    task_context,
    validate_plan_current,
)
from agentlens.web.deps import SessionDep, SettingsDep, get_trace_store
from agentlens.web.routes.diagnoser import TableDep, ensure_diagnoser_presets

router = APIRouter(prefix="/api/v1/quick-runs", tags=["quick-runs"])
diagnosis_runs_router = APIRouter(prefix="/api/v1/runs", tags=["diagnosis-runs"])
TraceDep = Annotated[TraceStore, Depends(get_trace_store)]


class PreflightInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space_uuid: str
    agent_uuid: str
    dataset_spec: dict
    trace_ids: list[str] = Field(min_length=1, max_length=10)
    builtin_keys: list[str] = Field(default_factory=list)
    scene_key: str = "quick_runtime"
    model_connection_uuid: str | None = None
    before_task_uuid: str | None = None


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt_uuid: str = Field(pattern=r"^[a-f0-9]{32}$")
    request_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_uuid: str = Field(pattern=r"^[a-f0-9]{32}$")
    job_uuid: str = Field(pattern=r"^[a-f0-9]{32}$")
    task_uuid: str = Field(pattern=r"^[a-f0-9]{32}$")
    plan: dict


class SavedScopeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    space_uuid: str = Field(min_length=1, max_length=64)
    agent_uuid: str = Field(min_length=1, max_length=64)
    dataset_spec: dict


class SavedOptionsInput(SavedScopeInput):
    ensure_core_presets: bool = False


class SavedPreflightInput(SavedScopeInput):
    trace_ids: list[str] = Field(min_length=1, max_length=10)
    diagnoser_uuid: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")


@diagnosis_runs_router.post("/options")
def saved_choices(
    payload: SavedOptionsInput, session: SessionDep, table: TableDep, settings: SettingsDep
):
    if payload.ensure_core_presets:
        ensure_diagnoser_presets(
            table,
            payload.space_uuid,
            payload.agent_uuid,
            settings.default_builtin_scenario_list,
            include_core=True,
        )
    return saved_options(session, payload.model_dump(exclude={"ensure_core_presets"}))


@diagnosis_runs_router.post("/preflight")
def saved_check(payload: SavedPreflightInput, session: SessionDep, traces: TraceDep):
    return preflight(session, traces, payload.model_dump())


@router.get("/catalog")
def catalog():
    return public_catalog()


@router.post("/preflight")
def check(payload: PreflightInput, session: SessionDep, traces: TraceDep):
    return preflight(session, traces, payload.model_dump(exclude_none=True))


@router.post("")
@diagnosis_runs_router.post("")
def create(payload: RunInput, session: SessionDep, traces: TraceDep):
    return run_quick(session, traces, payload.model_dump())


@router.get("/tasks/{uuid}/context")
def context(uuid: str, session: SessionDep):
    return task_context(session, uuid)


class ValidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: dict


@router.post("/validate")
@diagnosis_runs_router.post("/validate")
def validate(payload: ValidateInput, session: SessionDep, traces: TraceDep):
    validate_plan_current(session, traces, payload.plan)
    return {"valid": True}
