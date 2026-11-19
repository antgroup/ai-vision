import copy
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.exc import IntegrityError

from agentlens.domain.dataset import Dataset
from agentlens.domain.enums import DatasetType
from agentlens.domain.errors import Conflict, NotFound, Validation
from agentlens.domain.scope import visible_space_uuids
from agentlens.domain.secrets import merge_secret_fields, redact_spec
from agentlens.infra.ob.tables.dataset_table import DatasetTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.service.datasource.dataset_filter import (
    normalize_dataset_table,
    validate_dataset_filter,
)
from agentlens.service.datasource.runtime import validate_runtime_type
from agentlens.service.datasource.selector import TargetSelectorRegistry
from agentlens.service.datasource.signoz import DATASET_SCOPE_FIELDS
from agentlens.utils.uuid_gen import new_dataset_uuid
from agentlens.web.deps import get_dataset_table, get_job_table
from agentlens.web.schemas.common import ListResponse
from agentlens.web.schemas.dataset import DatasetCreate, DatasetOut, DatasetUpdate


def _validate_dataset_spec(
    spec: dict[str, Any] | None, dataset_type: DatasetType
) -> dict[str, Any]:
    """Validate the canonical nested dataset spec."""
    normalized = copy.deepcopy(spec or {})
    for key in ("stargazer", "odps", "db", "biz_domain"):
        if key in normalized:
            raise Validation(
                f"dataset spec field '{key}' is retired; use the SigNoz query contract"
            )
    if not isinstance(normalized.get("query"), dict):
        raise Validation("dataset spec field 'query' must be an object")
    limit = normalized["query"].get("limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise Validation("dataset spec field 'query.limit' must be a positive integer")
    try:
        normalize_dataset_table(normalized["query"].get("table"))
    except ValueError:
        raise Validation("dataset spec field 'query.table' must be a valid table name") from None
    if dataset_type == DatasetType.SIGNOZ:
        try:
            validate_dataset_filter(normalized["query"].get("where", ""))
        except ValueError:
            raise Validation(
                "dataset spec field 'query.where' is not a supported trace predicate"
            ) from None
        for column_key, value_key in DATASET_SCOPE_FIELDS:
            column = normalized.get(column_key)
            value = normalized.get(value_key)
            if (column is None) != (value is None):
                raise Validation(
                    f"dataset spec fields '{column_key}' and '{value_key}' must be set together"
                )
            for key, item in ((column_key, column), (value_key, value)):
                if item is not None and (not isinstance(item, str) or not item.strip()):
                    raise Validation(f"dataset spec field '{key}' must be a non-empty string")
    else:
        raise Validation(f"unsupported dataset type: {dataset_type.value!r}")
    normalized["runtime_type"] = validate_runtime_type(normalized.get("runtime_type"))
    try:
        selector = TargetSelectorRegistry.normalize_config(
            normalized.get("selector"),
            dataset_type,
            runtime_type=normalized["runtime_type"],
        )
    except ValueError as exc:
        raise Validation(str(exc)) from exc
    if selector is None:
        normalized.pop("selector", None)
    else:
        normalized["selector"] = selector
    return normalized


router = APIRouter(prefix="/api/v1", tags=["dataset"])
TableDep = Annotated[DatasetTable, Depends(get_dataset_table)]
JobsDep = Annotated[JobTable, Depends(get_job_table)]


def _out(d: Dataset) -> DatasetOut:
    return DatasetOut(
        uuid=d.uuid,
        space_uuid=d.space_uuid,
        agent_uuid=d.agent_uuid,
        name=d.name,
        description=d.description,
        type=d.type,
        spec=redact_spec(d.spec),
        gmt_create=d.gmt_create,
        gmt_modified=d.gmt_modified,
        creator=d.creator,
        modifier=d.modifier,
    )


@router.post("/dataset", status_code=status.HTTP_201_CREATED, response_model=DatasetOut)
def create_dataset(payload: DatasetCreate, table: TableDep) -> DatasetOut:
    spec = _validate_dataset_spec(payload.spec, payload.type)
    creator = payload.creator or payload.modifier
    modifier = payload.modifier or payload.creator
    d = Dataset(
        uuid=new_dataset_uuid(),
        space_uuid=payload.space_uuid,
        agent_uuid=payload.agent_uuid,
        name=payload.name,
        description=payload.description,
        type=payload.type,
        spec=spec,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        creator=creator,
        modifier=modifier,
    )
    try:
        table.insert(d)
    except IntegrityError as e:
        raise Conflict(f"dataset {payload.name!r} already exists") from e
    return _out(table.get_by_uuid(d.uuid))


@router.get("/dataset/{uuid}", response_model=DatasetOut)
def get_dataset(uuid: str, table: TableDep) -> DatasetOut:
    d = table.get_by_uuid(uuid)
    if d is None:
        raise NotFound(f"dataset {uuid!r} not found")
    return _out(d)


@router.get("/datasets", response_model=ListResponse[DatasetOut])
def list_datasets(
    table: TableDep,
    space_uuid: str = Query(..., min_length=1),
    agent_uuid: str = Query(..., min_length=1),
    include_user_shared: bool = Query(False),
    include_biz_shared: bool = Query(False),
    include_shared: bool = Query(False),
) -> ListResponse[DatasetOut]:
    rows = table.list(
        agent_uuid=agent_uuid,
        space_uuids=visible_space_uuids(
            space_uuid,
            include_user_shared=include_user_shared,
            include_biz_shared=include_biz_shared,
            include_shared=include_shared,
        ),
    )
    return ListResponse(items=[_out(d) for d in rows], total=len(rows))


@router.patch("/dataset/{uuid}", response_model=DatasetOut)
def update_dataset(uuid: str, payload: DatasetUpdate, table: TableDep) -> DatasetOut:
    current = table.get_by_uuid(uuid)
    if current is None:
        raise NotFound(f"dataset {uuid!r} not found")
    if current.spec.get("managed_by") == "aivision.quick_diagnosis":
        raise Conflict("managed quick Dataset cannot be edited")
    fields = payload.model_dump(exclude_unset=True)
    if isinstance(fields.get("spec"), dict):
        # Secrets are write-only: omitting model.api_key preserves the stored
        # value; submitting "" clears it; a non-empty value rotates it.
        fields["spec"] = merge_secret_fields(current.spec, fields["spec"])
        fields["spec"] = _validate_dataset_spec(fields["spec"], current.type)
    if fields.get("modifier") and not current.creator:
        fields["creator"] = fields["modifier"]
    if fields:
        table.update_by_uuid(uuid, fields)
    return _out(table.get_by_uuid(uuid))


@router.delete("/dataset/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(uuid: str, table: TableDep, jobs: JobsDep) -> None:
    current = table.get_by_uuid_for_update(uuid)
    if current is None:
        raise NotFound(f"dataset {uuid!r} not found")
    if current.spec.get("managed_by") == "aivision.quick_diagnosis":
        raise Conflict("managed quick Dataset retains its request identity")
    references = jobs.list_referencing_dataset(
        current.space_uuid,
        current.agent_uuid,
        current.uuid,
    )
    if references:
        raise Conflict(f"dataset {uuid!r} is referenced by job {references[0].uuid!r}")
    if not table.delete_by_uuid(uuid):
        raise NotFound(f"dataset {uuid!r} not found")
