from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from agentlens.diagnoser.executors.rule.custom.enhanced_contract import CAPABILITY
from agentlens.domain.errors import Conflict, NotFound
from agentlens.domain.sandbox import (
    SandboxBinding,
    SandboxConnection,
    SandboxError,
    SandboxStatus,
    sandbox_error_message,
)
from agentlens.infra.ob.tables.sandbox_table import SandboxTable
from agentlens.infra.sandbox import SandboxClient, effective_input_limit
from agentlens.service import sandbox_enrollment
from agentlens.service.rule_runtime import qualified_runtime_manifests
from agentlens.web.deps import SessionDep, SettingsDep
from agentlens.web.schemas.common import ListResponse
from agentlens.web.schemas.sandbox import (
    SandboxBindingOut,
    SandboxBindingUpdate,
    SandboxConnectionCreate,
    SandboxConnectionOut,
    SandboxConnectionRuntimeOut,
    SandboxConnectionUpdate,
    SandboxEnrollmentEnable,
    SandboxProviderOut,
    SandboxTestOut,
    SandboxTestRequest,
)

router = APIRouter(prefix="/api/v1", tags=["sandbox"])


def get_sandbox_table(session: SessionDep) -> SandboxTable:
    return SandboxTable(session)


TableDep = Annotated[SandboxTable, Depends(get_sandbox_table)]


def _out(connection: SandboxConnection) -> SandboxConnectionOut:
    fields = {
        key: getattr(connection, key)
        for key in SandboxConnectionOut.model_fields
        if key != "api_key_configured"
    }
    return SandboxConnectionOut(**fields, api_key_configured=bool(connection.api_key))


@router.get("/sandbox-providers", response_model=ListResponse[SandboxProviderOut])
def providers():
    return ListResponse(
        items=[
            SandboxProviderOut(
                id="openshell", name="OpenShell", protocol_version=1, capabilities=["custom_rule"]
            )
        ],
        total=1,
    )


@router.get("/sandbox-connections", response_model=ListResponse[SandboxConnectionOut])
def list_connections(table: TableDep, space_uuid: str = Query(..., min_length=1, max_length=64)):
    connections = table.list(space_uuid)
    return ListResponse(items=[_out(row) for row in connections], total=len(connections))


@router.post("/sandbox-connection", response_model=SandboxConnectionOut, status_code=201)
def create_connection(payload: SandboxConnectionCreate, table: TableDep):
    now = datetime.now()
    connection = SandboxConnection(
        uuid="sb-" + uuid4().hex[:29],
        **payload.model_dump(),
        config_revision=1,
        status=SandboxStatus.UNTESTED,
        gmt_create=now,
        gmt_modified=now,
    )
    try:
        table.insert(connection)
    except IntegrityError as error:
        table.s.rollback()
        raise Conflict("Sandbox connection name already exists in this Space.") from error
    return _out(connection)


@router.put("/sandbox-enrollment/{uuid}", response_model=SandboxConnectionOut)
def enroll_connection(uuid: str, payload: SandboxConnectionCreate, table: TableDep):
    try:
        return _out(sandbox_enrollment.enroll(table, uuid, payload.model_dump()))
    except SandboxError as error:
        return JSONResponse(
            {"code": error.code, "msg": "Sandbox provider is not ready."}, status_code=422
        )


@router.post("/sandbox-enrollment/{uuid}/enable", response_model=SandboxBindingOut)
def enable_enrollment(uuid: str, payload: SandboxEnrollmentEnable, table: TableDep):
    try:
        binding = sandbox_enrollment.enable(
            table, uuid, payload.space_uuid, payload.modifier, payload.expected_config_revision
        )
    except SandboxError as error:
        return JSONResponse(
            {"code": error.code, "msg": "Sandbox provider is not ready."}, status_code=422
        )
    return SandboxBindingOut(
        space_uuid=binding.space_uuid,
        connection_uuid=binding.connection_uuid,
        require_sandbox=binding.require_sandbox,
    )


@router.get("/sandbox-connection/{uuid}", response_model=SandboxConnectionOut)
def get_connection(
    uuid: str, table: TableDep, space_uuid: str = Query(..., min_length=1, max_length=64)
):
    connection = table.get_in_space(uuid, space_uuid)
    if connection is None:
        raise NotFound("Sandbox connection not found in this Space.")
    return _out(connection)


@router.get("/sandbox-connection/{uuid}/runtime", response_model=SandboxConnectionRuntimeOut)
def connection_runtime(
    uuid: str,
    table: TableDep,
    settings: SettingsDep,
    space_uuid: str = Query(..., min_length=1, max_length=64),
):
    # probe_snapshot ends the read transaction before any external I/O.
    connection = table.probe_snapshot(uuid, space_uuid)
    if connection is None:
        raise NotFound("Sandbox connection not found in this Space.")
    result = {
        "connection_uuid": connection.uuid,
        "config_revision": connection.config_revision,
        "image": connection.image,
        "ready": False,
        "capabilities": [],
        "runtime_manifests": [],
        "effective_timeout_seconds": min(
            settings.enhanced_exec_timeout, connection.timeout_seconds
        ),
    }
    if connection.status == SandboxStatus.DISABLED:
        return result
    client = None
    try:
        client = SandboxClient(connection)
        health = client.health()
        manifests = qualified_runtime_manifests(health, connection.image)
        result.update(
            ready=True,
            capabilities=["custom_rule"] + ([CAPABILITY] if manifests else []),
            runtime_manifests=manifests,
            effective_input_bytes=effective_input_limit(
                health, connection.image, settings.sandbox_max_input_bytes
            ),
        )
    except SandboxError:
        pass
    finally:
        if client is not None:
            client.close()
    current = table.get_in_space(uuid, space_uuid)
    if (
        current is None
        or current.config_revision != connection.config_revision
        or current.status == SandboxStatus.DISABLED
    ):
        raise Conflict("Sandbox connection changed during verification; retry the current profile.")
    return result


@router.patch("/sandbox-connection/{uuid}", response_model=SandboxConnectionOut)
def update_connection(uuid: str, payload: SandboxConnectionUpdate, table: TableDep):
    fields = payload.model_dump(exclude_unset=True, exclude={"space_uuid"})
    for key in tuple(fields):
        if fields[key] is None:
            fields.pop(key)
    try:
        table.update(uuid, payload.space_uuid, fields)
    except SandboxError as error:
        return JSONResponse(
            {"code": error.code, "msg": sandbox_error_message(error.code)}, status_code=422
        )
    except IntegrityError as error:
        table.s.rollback()
        raise Conflict("Sandbox connection name already exists in this Space.") from error
    return _out(table.get_in_space(uuid, payload.space_uuid))


@router.delete("/sandbox-connection/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(
    uuid: str, table: TableDep, space_uuid: str = Query(..., min_length=1, max_length=64)
):
    table.delete(uuid, space_uuid)


@router.post("/sandbox-connection/{uuid}/test", response_model=SandboxTestOut)
def test_connection(uuid: str, payload: SandboxTestRequest, table: TableDep):
    connection = table.probe_snapshot(uuid, payload.space_uuid)
    if connection is None:
        raise NotFound("Sandbox connection not found in this Space.")
    if connection.status == SandboxStatus.DISABLED:
        raise Conflict("Enable the Sandbox connection before testing.")
    next_status = SandboxStatus.UNAVAILABLE
    test_error = None
    client = None
    try:
        client = SandboxClient(connection)
        client.health()
        next_status = SandboxStatus.READY
    except SandboxError as error:
        test_error = error.code
    finally:
        if client is not None:
            client.close()
    if not table.save_test_status(connection, next_status):
        raise Conflict("Sandbox connection changed during verification; retry the current profile.")
    current = table.get_in_space(uuid, payload.space_uuid)
    return SandboxTestOut(**_out(current).model_dump(), test_error=test_error)


@router.get("/sandbox-binding", response_model=SandboxBindingOut)
def get_binding(table: TableDep, space_uuid: str = Query(..., min_length=1, max_length=64)):
    binding = table.get_binding(space_uuid)
    return SandboxBindingOut(
        space_uuid=space_uuid,
        connection_uuid=binding.connection_uuid,
        require_sandbox=binding.require_sandbox,
    )


@router.patch("/sandbox-binding", response_model=SandboxBindingOut)
def update_binding(payload: SandboxBindingUpdate, table: TableDep):
    current = table.get_binding(payload.space_uuid, for_update=True)
    fields = payload.model_dump(exclude_unset=True)
    binding = SandboxBinding(
        space_uuid=payload.space_uuid,
        connection_uuid=fields.get("connection_uuid", current.connection_uuid),
        require_sandbox=fields.get("require_sandbox", current.require_sandbox),
        modifier=payload.modifier,
    )
    try:
        table.save_binding(binding)
    except IntegrityError as error:
        table.s.rollback()
        raise Conflict("Sandbox binding changed concurrently; retry.") from error
    return SandboxBindingOut(
        space_uuid=binding.space_uuid,
        connection_uuid=binding.connection_uuid,
        require_sandbox=binding.require_sandbox,
    )
