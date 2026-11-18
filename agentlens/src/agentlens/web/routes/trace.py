"""从 Job 绑定的 SigNoz 数据集读取完整 Trace 并返回语义投影。"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from agentlens.domain.dataset import TraceQuerySpec
from agentlens.domain.enums import DatasetType
from agentlens.domain.errors import NotFound, Validation
from agentlens.infra.ob.tables.dataset_table import DatasetTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.service.datasource.projection.serialization import serialize_trace_projection
from agentlens.service.datasource.projection.service import project_trace
from agentlens.service.datasource.runtime import validate_runtime_type
from agentlens.service.datasource.signoz import TraceStore, dataset_scope_predicates
from agentlens.service.datasource.trace import Trace
from agentlens.web.deps import (
    get_dataset_table,
    get_job_table,
    get_trace_store,
)

router = APIRouter(prefix="/api/v1", tags=["trace"])

# Keep the replay query bounded while covering traces that outlive the task
# context window. ``window_min`` is applied on both sides of center_time.
_TRACE_REPLAY_WINDOW_MIN = 24 * 60

TraceDep = Annotated[TraceStore, Depends(get_trace_store)]
JobsDep = Annotated[JobTable, Depends(get_job_table)]
DatasetsDep = Annotated[DatasetTable, Depends(get_dataset_table)]


def _to_projection(trace: Trace) -> dict[str, Any]:
    """Serialize one canonical, bounded Projection response."""
    return serialize_trace_projection(trace)


def _raw_projection(trace: Trace) -> dict[str, Any]:
    """Return unchanged source spans when this runtime has no semantic projection."""
    return {
        "projection_kind": "raw",
        "trace_id": trace.trace_id,
        "runtime_type": trace.runtime_type,
        "raw": {"spans": trace.spans},
    }


def _detail_response(trace: Trace) -> dict[str, Any]:
    projected = project_trace(trace, require_text_evidence=False)
    if projected.projection is None:
        return _raw_projection(projected)
    return _to_projection(projected)


# ── SigNoz 投影 ──────────────────────────────────────────────────────


@lru_cache(maxsize=512)
def _project_signoz(
    trace_store: TraceStore,
    trace_id: str,
    table: str,
    where: str,
    center_time: str,
    runtime_type: str,
) -> dict[str, Any] | None:
    """SigNoz 路径：拉原始 spans → 与 Runner 相同的投影入口。

    trace_store 是单例可作缓存键；trace 不可变,同 (trace, 数据源, runtime_type) 复用。
    """
    spec = TraceQuerySpec(table=table, where=where)
    rows = trace_store.read_trace_spans(
        spec,
        trace_id,
        center_time,
        window_min=_TRACE_REPLAY_WINDOW_MIN,
    )
    if not rows:
        return None
    return _detail_response(
        Trace(
            trace_id=trace_id,
            runtime_type=runtime_type,
            spans=rows,
            source_type=DatasetType.SIGNOZ,
        )
    )


# ── Route ───────────────────────────────────────────────────────────────


@router.get("/trace/{trace_id}/projection")
def trace_projection(
    trace_id: str,
    traces: TraceDep,
    jobs: JobsDep,
    datasets: DatasetsDep,
    job_uuid: str = Query(..., description="trace 所属 job,用于解析 dataset spec"),
    center_time: str = Query(..., description="trace 时间锚点，用于定位来源分区或 spans"),
) -> dict[str, Any]:
    """按 Job 数据集的空间约束读取完整 spans；不套用批量选样条件。"""
    job = jobs.get_by_uuid(job_uuid)
    if job is None:
        raise NotFound(f"job {job_uuid!r} not found")
    dataset = datasets.get_by_uuid(job.dataset_uuid)
    if dataset is None:
        raise NotFound(f"dataset {job.dataset_uuid!r} not found")

    runtime_type = validate_runtime_type(dataset.spec.get("runtime_type"))
    selection = dataset.spec.get("trace_selection")
    if selection is not None:
        if trace_id not in selection.get("trace_ids", []):
            raise NotFound("trace is outside the fixed selection")
        rows = traces.read_explicit_trace_rows(dataset.spec, [trace_id])
        if not rows:
            raise NotFound("trace not found")
        return _detail_response(
            Trace(
                trace_id=trace_id,
                runtime_type=runtime_type,
                spans=rows,
                source_type=DatasetType.SIGNOZ,
            )
        )

    try:
        if dataset.type == DatasetType.SIGNOZ:
            sspec = dataset.trace_query_spec()
            result = _project_signoz(
                traces,
                trace_id,
                sspec.table,
                " AND ".join(dataset_scope_predicates(dataset.spec)),
                center_time,
                runtime_type,
            )
        else:
            raise Validation(f"unsupported dataset type: {dataset.type!r}")
    except ValueError as exc:
        raise Validation(str(exc)) from exc

    if result is None:
        raise NotFound(f"trace {trace_id!r} not found")
    return result
