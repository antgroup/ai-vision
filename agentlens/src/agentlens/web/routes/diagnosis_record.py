from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from agentlens.domain.enums import ReviewStatus, Verdict
from agentlens.domain.errors import Conflict, ExternalUnavailable, NotFound, Validation
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.signoz.client import SignozQueryError
from agentlens.service.diagnosis_record_store import DiagnosisRecordStore
from agentlens.service.execution_snapshot import record_snapshot_definition, snapshot_definitions
from agentlens.web.deps import get_diagnoser_table, get_record_store
from agentlens.web.schemas.common import ListResponse
from agentlens.web.schemas.diagnosis_record import (
    DiagnosisRecordListItemOut,
    DiagnosisRecordOut,
    ExecutionOut,
    MetricOut,
)
from agentlens.web.schemas.feedback import FeedbackSubmit

router = APIRouter(prefix="/api/v1", tags=["diagnosis-record"])
StoreDep = Annotated[DiagnosisRecordStore, Depends(get_record_store)]
DiagnoserDep = Annotated[DiagnoserTable, Depends(get_diagnoser_table)]


def _parse_verdicts(value: str | None) -> list[str] | None:
    if not value:
        return None
    verdicts = [item.strip() for item in value.split(",") if item.strip()]
    allowed = {item.value for item in Verdict}
    if not verdicts or any(item not in allowed for item in verdicts):
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="invalid verdict")
    return verdicts


def _list_out(r) -> DiagnosisRecordListItemOut:
    return DiagnosisRecordListItemOut(
        space_uuid=r.space_uuid,
        diagnoser_definition=r.diagnoser_definition,
        uuid=r.uuid,
        task_uuid=r.task_uuid,
        job_uuid=r.job_uuid,
        diagnoser_uuid=r.diagnoser_uuid,
        diagnoser_version=r.diagnoser_version,
        target_type=r.target_type,
        target_id=r.target_id,
        owner_id=r.owner_id,
        exp_ver_id=r.exp_ver_id,
        model=r.model,
        verdict=r.verdict,
        review_status=r.review_status,
        summary=r.summary,
        reviewed_at=r.reviewed_at,
        diagnosed_at=r.diagnosed_at,
        target_created_at=r.target_created_at,
    )


def _metric_out(metric) -> MetricOut:
    return MetricOut(
        name=metric.name,
        verdict=metric.verdict,
        reason=metric.reason,
        evidence=metric.evidence,
    )


def _out(r) -> DiagnosisRecordOut:
    metrics_out = [_metric_out(metric) for metric in r.metrics]
    return DiagnosisRecordOut(
        space_uuid=r.space_uuid,
        diagnoser_definition=record_snapshot_definition(
            r.trace_snapshot, r.diagnoser_uuid, r.diagnoser_version
        ),
        uuid=r.uuid,
        task_uuid=r.task_uuid,
        job_uuid=r.job_uuid,
        diagnoser_uuid=r.diagnoser_uuid,
        diagnoser_version=r.diagnoser_version,
        target_type=r.target_type,
        target_id=r.target_id,
        owner_id=r.owner_id,
        exp_ver_id=r.exp_ver_id,
        model=r.model,
        verdict=r.verdict,
        summary=r.summary,
        execution=ExecutionOut(
            status=r.execution.status,
            code=r.execution.code,
            msg=r.execution.msg,
            evidence=r.execution.evidence,
        ),
        trace_snapshot=r.trace_snapshot,
        review_status=r.review_status,
        feedback=r.feedback,
        reviewed_by=r.reviewed_by,
        reviewed_at=r.reviewed_at,
        diagnosed_at=r.diagnosed_at,
        target_created_at=r.target_created_at,
        metrics=metrics_out,
    )


@router.get("/diagnosis-records", response_model=ListResponse[DiagnosisRecordListItemOut])
def list_records(
    store: StoreDep,
    diagnosers: DiagnoserDep,
    job_uuid: Annotated[list[str], Query(min_length=1)],
    start_time: str | None = None,
    end_time: str | None = None,
    task_uuid: str | None = None,
    owner_id: str | None = None,
    verdict: str | None = None,
    review_status: ReviewStatus | None = None,
    diagnoser_uuid: str | None = None,
    exp_ver_id: Annotated[list[str] | None, Query()] = None,
    model: Annotated[list[str] | None, Query()] = None,
    category: Annotated[str | None, Query(min_length=1)] = None,
    keyword: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort_by: Literal[
        "uuid",
        "target_id",
        "verdict",
        "review_status",
        "reviewed_at",
        "diagnosed_at",
        "target_created_at",
    ] = "diagnosed_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> ListResponse[DiagnosisRecordListItemOut]:
    job_uuids = list(dict.fromkeys(job_uuid))
    normalized_exp_ver_ids: list[str] = []
    for value in exp_ver_id or []:
        normalized = value.strip()
        if not normalized.isascii() or not normalized.isdigit():
            raise HTTPException(
                status_code=422,
                detail="exp_ver_id must contain digits only",
            )
        if normalized not in normalized_exp_ver_ids:
            normalized_exp_ver_ids.append(normalized)
    diagnoser_filter: str | list[str] | None = diagnoser_uuid
    frozen_filter = None
    if category is not None:
        if hasattr(diagnosers, "s"):
            from sqlalchemy import select

            from agentlens.infra.ob.models import TaskModel

            frozen_filter = {}
            legacy_filter = [
                d.uuid
                for d in diagnosers.list()
                if d.spec.get("category") == category
                and (not diagnoser_uuid or d.uuid == diagnoser_uuid)
            ]
            for task in diagnosers.s.scalars(
                select(TaskModel).where(TaskModel.job_uuid.in_(job_uuids))
            ):
                if task.execution_snapshot is None:
                    # The store applies current-category compatibility only to
                    # legacy Records; a missing Task must not erase saved metadata.
                    continue
                try:
                    definitions = snapshot_definitions(task.execution_snapshot)
                except (ValueError, TypeError, KeyError, Validation):
                    definitions = {}
                frozen_filter[task.uuid] = [
                    key
                    for key, entry in definitions.items()
                    if entry.get("category") == category
                    and (not diagnoser_uuid or key == diagnoser_uuid)
                ]
        else:
            # Protocol-only route fixtures have no task repository.
            diagnoser_filter = [
                d.uuid
                for d in diagnosers.list()
                if d.spec.get("category") == category
                and (not diagnoser_uuid or d.uuid == diagnoser_uuid)
            ]
    try:
        rows, total = store.query_record_list(
            job_uuid=job_uuids,
            verdict=_parse_verdicts(verdict),
            task_uuid=task_uuid,
            owner_id=owner_id,
            start_time=start_time,
            end_time=end_time,
            review_status=review_status.value if review_status is not None else None,
            diagnoser_uuid=diagnoser_filter,
            exp_ver_id=normalized_exp_ver_ids,
            model=model,
            keyword=keyword,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
            **(
                {
                    "frozen_diagnoser_by_task": frozen_filter,
                    "category": category,
                    "legacy_diagnoser_uuids": legacy_filter,
                }
                if frozen_filter is not None
                else {}
            ),
        )
    except SignozQueryError as exc:
        raise ExternalUnavailable(f"SigNoz query failed: {exc}") from exc
    return ListResponse(items=[_list_out(r) for r in rows], total=total)


@router.get("/diagnosis-record/{uuid}", response_model=DiagnosisRecordOut)
def get_record(
    uuid: str,
    store: StoreDep,
) -> DiagnosisRecordOut:
    rec = store.get_record(uuid)
    if not rec:
        raise NotFound(f"diagnosis record {uuid!r} not found")
    return _out(rec)


@router.post("/diagnosis-record/{uuid}/feedback", response_model=DiagnosisRecordOut)
def submit_feedback(
    uuid: str,
    payload: FeedbackSubmit,
    response: Response,
    store: StoreDep,
) -> DiagnosisRecordOut:
    rec = store.get_record(uuid)
    if not rec:
        raise NotFound(f"diagnosis record {uuid!r} not found")
    if rec.review_status in (ReviewStatus.CONFIRMED, ReviewStatus.FALSE_POSITIVE):
        raise Conflict(f"record {uuid!r} already reviewed as {rec.review_status.value}")
    review = payload.model_dump(exclude_none=True)
    if "review_status" in review:
        rs = review["review_status"]
        rec.review_status = ReviewStatus(rs.value if hasattr(rs, "value") else rs)
    if "feedback" in review:
        rec.feedback = review["feedback"]
    if "reviewed_by" in review:
        rec.reviewed_by = review["reviewed_by"]
    rec.reviewed_at = datetime.now()
    visible = store.write_record_and_wait_visible(
        rec,
        attempt=getattr(rec, "_attempt", 0) or 0,
    )
    if visible is None:
        response.status_code = 202
        return _out(rec)
    return _out(visible)
