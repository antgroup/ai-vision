"""Four exact trusted routes; final end-user authorization lives in Django."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from agentlens.service.datasource.signoz import TraceStore
from agentlens.service.specialists import (
    SpecialistError,
    configure_specialist_public,
    specialist_catalog,
    sync_specialist_public,
    trial_specialist,
)
from agentlens.web.deps import SessionDep, get_trace_store
from agentlens.web.schemas.specialist import (
    SpecialistConfigurationInput,
    SpecialistSyncInput,
    SpecialistTrialInput,
)

router = APIRouter(prefix="/api/v1", tags=["specialist"])
TraceDep = Annotated[TraceStore, Depends(get_trace_store)]


def _safe_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except SpecialistError as exc:
        return JSONResponse({"code": exc.code, "msg": exc.code}, status_code=exc.status)


def _input(schema, payload):
    try:
        return schema.model_validate(payload)
    except ValidationError:
        # Pydantic raw errors/field names may contain write-only policy material.
        raise SpecialistError("SPECIALIST_INPUT_INVALID") from None


@router.get("/diagnosers/specialist-catalog")
def catalog(
    space_uuid: Annotated[str, Query(min_length=1, max_length=64)],
    agent_uuid: Annotated[str, Query(min_length=1, max_length=64)],
    runtime_type: Annotated[str, Query(min_length=1, max_length=64)],
):
    return _safe_call(
        specialist_catalog, space_uuid=space_uuid, agent_uuid=agent_uuid, runtime_type=runtime_type
    )


@router.post("/diagnosers/sync-specialist-builtins")
def sync(payload: dict, session: SessionDep):
    return _safe_call(lambda: sync_specialist_public(session, _input(SpecialistSyncInput, payload)))


@router.patch("/diagnosers/{uuid}/specialist-configuration")
def configuration(uuid: str, payload: dict, session: SessionDep):
    return _safe_call(
        lambda: configure_specialist_public(
            session, uuid, _input(SpecialistConfigurationInput, payload)
        )
    )


@router.post("/diagnoser/specialist-trial")
def trial(payload: dict, session: SessionDep, traces: TraceDep):
    return _safe_call(
        lambda: trial_specialist(session, traces, _input(SpecialistTrialInput, payload))
    )
