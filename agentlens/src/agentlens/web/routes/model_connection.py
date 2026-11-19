from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from agentlens.domain.enums import ModelConnectionStatus, ModelConnectionTestError
from agentlens.domain.errors import Conflict, NotFound
from agentlens.domain.model_connection import ModelConnection
from agentlens.infra.llm_judge import JudgeUnavailable, LLMJudge
from agentlens.infra.llm_judge.model_discovery import (
    ModelDiscoveryError,
    discover_models,
    model_discovery_message,
    normalize_endpoint,
)
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
from agentlens.service.model_validation import readable_validation, validate_connection
from agentlens.utils.uuid_gen import new_model_connection_uuid
from agentlens.web.deps import get_diagnoser_table, get_model_connection_table
from agentlens.web.schemas.common import ListResponse
from agentlens.web.schemas.model_connection import (
    DiscoveredModelOut,
    ModelConnectionCreate,
    ModelConnectionOut,
    ModelConnectionReferenceOut,
    ModelConnectionTestResponse,
    ModelConnectionUpdate,
    ModelConnectionValidateRequest,
    ModelDiscoveryRequest,
)

router = APIRouter(prefix="/api/v1", tags=["model-connection"])
ConnectionsDep = Annotated[ModelConnectionTable, Depends(get_model_connection_table)]
DiagnosersDep = Annotated[DiagnoserTable, Depends(get_diagnoser_table)]


def _discovery_error(error: ModelDiscoveryError) -> JSONResponse:
    return JSONResponse(
        {
            "code": "validation" if error.status == 422 else "external_unavailable",
            "msg": model_discovery_message(error.code),
            "discovery_error": error.code,
        },
        status_code=error.status,
    )


@router.post("/model-connections/discover", response_model=ListResponse[DiscoveredModelOut])
def discover_model_connections(payload: ModelDiscoveryRequest, table: ConnectionsDep):
    api_key = payload.api_key
    if payload.connection_uuid:
        connection = table.get_in_space(payload.connection_uuid, payload.space_uuid)
        if connection is None:
            raise NotFound("Model connection not found in this space.")
        if not api_key:
            if normalize_endpoint(payload.endpoint) != normalize_endpoint(connection.endpoint):
                return _discovery_error(ModelDiscoveryError("missing_api_key"))
            api_key = connection.api_key
    try:
        models = discover_models(payload.endpoint, api_key or "")
    except ModelDiscoveryError as error:
        return _discovery_error(error)
    return ListResponse(items=[DiscoveredModelOut(id=model) for model in models], total=len(models))


def _out(connection: ModelConnection) -> ModelConnectionOut:
    return ModelConnectionOut(
        uuid=connection.uuid,
        space_uuid=connection.space_uuid,
        name=connection.name,
        endpoint=connection.endpoint,
        model_name=connection.model_name,
        api_key_configured=bool(connection.api_key),
        diagnostic_validation=readable_validation(connection.diagnostic_validation),
        status=connection.status,
        gmt_create=connection.gmt_create,
        gmt_modified=connection.gmt_modified,
        creator=connection.creator,
        modifier=connection.modifier,
    )


@router.post(
    "/model-connection",
    status_code=status.HTTP_201_CREATED,
    response_model=ModelConnectionOut,
)
def create_model_connection(
    payload: ModelConnectionCreate, table: ConnectionsDep
) -> ModelConnectionOut:
    connection = ModelConnection(
        uuid=new_model_connection_uuid(),
        space_uuid=payload.space_uuid,
        name=payload.name,
        endpoint=payload.endpoint,
        model_name=payload.model_name,
        api_key=payload.api_key,
        status=ModelConnectionStatus.UNTESTED,
        gmt_create=None,
        gmt_modified=None,
        creator=payload.creator,
        modifier=payload.modifier,
    )
    try:
        table.insert(connection)
    except IntegrityError as exc:
        raise Conflict(f"model connection {payload.name!r} already exists in this space") from exc
    created = table.get_by_uuid(connection.uuid)
    if created is None:
        raise NotFound(f"model connection {connection.uuid!r} not found after creation")
    return _out(created)


def _references(connection: ModelConnection, diagnosers: DiagnoserTable):
    return [
        diagnoser
        for diagnoser in diagnosers.list(space_uuid=connection.space_uuid)
        if diagnoser.spec.get("model_connection_uuid") == connection.uuid
    ]


@router.get("/model-connections", response_model=ListResponse[ModelConnectionOut])
def list_model_connections(
    table: ConnectionsDep,
    space_uuid: str = Query(..., min_length=1),
) -> ListResponse[ModelConnectionOut]:
    rows = table.list(space_uuid)
    return ListResponse(items=[_out(row) for row in rows], total=len(rows))


@router.get("/model-connection/{uuid}", response_model=ModelConnectionOut)
def get_model_connection(uuid: str, table: ConnectionsDep) -> ModelConnectionOut:
    connection = table.get_by_uuid(uuid)
    if connection is None:
        raise NotFound(f"model connection {uuid!r} not found")
    return _out(connection)


@router.get(
    "/model-connection/{uuid}/references",
    response_model=ListResponse[ModelConnectionReferenceOut],
)
def list_model_connection_references(
    uuid: str,
    table: ConnectionsDep,
    diagnosers: DiagnosersDep,
) -> ListResponse[ModelConnectionReferenceOut]:
    connection = table.get_by_uuid(uuid)
    if connection is None:
        raise NotFound(f"model connection {uuid!r} not found")
    references = _references(connection, diagnosers)
    return ListResponse(
        items=[
            ModelConnectionReferenceOut(
                uuid=diagnoser.uuid,
                name=diagnoser.name,
                agent_uuid=diagnoser.agent_uuid,
                type=diagnoser.type.value,
                mode=diagnoser.mode.value,
            )
            for diagnoser in references
        ],
        total=len(references),
    )


@router.patch("/model-connection/{uuid}", response_model=ModelConnectionOut)
def update_model_connection(
    uuid: str,
    payload: ModelConnectionUpdate,
    table: ConnectionsDep,
) -> ModelConnectionOut:
    current = table.get_by_uuid_for_update(uuid)
    if current is None:
        raise NotFound(f"model connection {uuid!r} not found")
    fields = payload.model_dump(exclude_unset=True)
    if (
        fields.get("endpoint") is not None
        and normalize_endpoint(fields["endpoint"]) != normalize_endpoint(current.endpoint)
        and not (fields.get("api_key") or "").strip()
    ):
        return _discovery_error(ModelDiscoveryError("missing_api_key"))
    if fields.get("api_key") is None:
        fields.pop("api_key", None)
    changed_connection_fields = {"endpoint", "api_key", "model_name"}
    if changed_connection_fields.intersection(fields):
        fields["status"] = ModelConnectionStatus.UNTESTED.value
    if fields:
        try:
            table.update_by_uuid(uuid, fields)
        except IntegrityError as exc:
            raise Conflict("model connection name already exists in this space") from exc
    updated = table.get_by_uuid(uuid)
    if updated is None:
        raise NotFound(f"model connection {uuid!r} not found after update")
    return _out(updated)


@router.post("/model-connection/{uuid}/validate", response_model=ModelConnectionOut)
def validate_model_connection(
    uuid: str, payload: ModelConnectionValidateRequest, table: ConnectionsDep
):
    connection = table.validation_snapshot(uuid, payload.space_uuid)
    if connection is None:
        raise NotFound("Model connection not found in this space.")
    report, connection_status = validate_connection(connection, payload.generation_options)
    if not table.save_validation(connection, report.model_dump(mode="json"), connection_status):
        raise Conflict(
            "Model connection changed during validation; retry with its current configuration."
        )
    updated = table.get_in_space(uuid, payload.space_uuid)
    if updated is None:
        raise Conflict("Model connection changed during validation.")
    return _out(updated)


@router.post(
    "/model-connection/{uuid}/test",
    response_model=ModelConnectionTestResponse,
)
def test_model_connection(uuid: str, table: ConnectionsDep) -> ModelConnectionTestResponse:
    connection = table.connectivity_snapshot(uuid)
    if connection is None:
        raise NotFound(f"model connection {uuid!r} not found")

    next_status = ModelConnectionStatus.UNAVAILABLE
    test_error = ModelConnectionTestError.INVALID_REQUEST
    valid_key = (
        bool(connection.api_key)
        and len(connection.api_key) <= 8192
        and all(33 <= ord(char) <= 126 for char in connection.api_key)
    )
    if connection.endpoint and connection.model_name and valid_key:
        judge = LLMJudge(
            connection.endpoint,
            connection.api_key,
            connection.model_name,
            timeout=180,
            max_retries=0,
            enable_thinking=None,
        )
        try:
            judge.probe()
        except JudgeUnavailable as error:
            test_error = error.code
        else:
            next_status = ModelConnectionStatus.AVAILABLE
            test_error = None
        finally:
            judge.close()
    if not table.save_test_status(connection, next_status):
        raise Conflict("Model connection changed during the test; retry its current configuration.")
    updated = table.get_by_uuid(uuid)
    if updated is None:
        raise NotFound(f"model connection {uuid!r} not found after test")
    return ModelConnectionTestResponse(**_out(updated).model_dump(), test_error=test_error)


@router.delete("/model-connection/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_connection(
    uuid: str,
    table: ConnectionsDep,
    diagnosers: DiagnosersDep,
) -> None:
    connection = table.get_by_uuid_for_update(uuid)
    if connection is None:
        raise NotFound(f"model connection {uuid!r} not found")
    references = _references(connection, diagnosers)
    if references:
        names = ", ".join(diagnoser.name for diagnoser in references)
        raise Conflict(f"model connection {uuid!r} is referenced by diagnosers: {names}")
    if not table.delete_by_uuid(uuid):
        raise NotFound(f"model connection {uuid!r} not found")
