from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from agentlens.config import Settings
from agentlens.infra.antcollector import AntcollectorClient, HttpAntcollectorClient
from agentlens.infra.ob.engine import make_engine, make_session_factory
from agentlens.infra.ob.tables.dataset_table import DatasetTable
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.ob.tables.heartbeat_table import HeartbeatTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.infra.ob.tables.leader_lease_table import LeaderLeaseTable
from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.infra.signoz.client import SignozClient
from agentlens.infra.signoz.dialect import ColumnDialect, SignozObDialect
from agentlens.infra.signoz.ob_client import SignozObClient
from agentlens.kernel.worker.runner import Runner
from agentlens.service.datasource.signoz import TraceStore
from agentlens.service.diagnosis_record_store import DiagnosisRecordStore
from agentlens.service.inference.inference_proxy import InferenceProxyService
from agentlens.service.ingest_exporter import make_ingest_exporter
from agentlens.service.task_stats_store import TaskStatsStore


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def _engine_factory():
    s = get_settings()
    eng = make_engine(s.ob_url, sql_log_enabled=s.ob_sql_log_enabled)
    return make_session_factory(eng)


def get_session() -> Iterator[Session]:
    sf = _engine_factory()
    s = sf()
    try:
        yield s
    finally:
        s.close()


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_inference_proxy(request: Request) -> InferenceProxyService:
    return request.app.state.inference_proxy


def get_diagnoser_table(s: SessionDep) -> DiagnoserTable:
    return DiagnoserTable(s)


def get_dataset_table(s: SessionDep) -> DatasetTable:
    return DatasetTable(s)


def get_job_table(s: SessionDep) -> JobTable:
    return JobTable(s)


def get_model_connection_table(s: SessionDep) -> ModelConnectionTable:
    return ModelConnectionTable(s)


def get_task_table(s: SessionDep) -> TaskTable:
    return TaskTable(s)


def get_heartbeat_table(s: SessionDep) -> HeartbeatTable:
    return HeartbeatTable(s)


def get_lease_table(s: SessionDep) -> LeaderLeaseTable:
    return LeaderLeaseTable(s)


@lru_cache(maxsize=1)
def get_signoz_client() -> SignozClient:
    s = get_settings()
    return SignozObClient(
        s.signoz_ob_url,
        timeout_sec=s.signoz_query_timeout_sec,
        sql_log_enabled=s.ob_sql_log_enabled,
    )


@lru_cache(maxsize=1)
def get_column_dialect() -> ColumnDialect:
    return SignozObDialect(org_id=get_settings().signoz_org_id)


@lru_cache(maxsize=1)
def get_antcollector_client() -> AntcollectorClient:
    s = get_settings()
    return HttpAntcollectorClient(
        otlp_endpoint=s.antcollector_url,
        access_id=s.antcollector_access_id,
        access_token=s.antcollector_access_token,
        span_exporter=make_ingest_exporter(
            s, _engine_factory() if s.aivision_ingest_token_url else None
        ),
    )


def close_antcollector_client() -> None:
    """Close the cached OTLP client only when it was actually constructed."""
    if get_antcollector_client.cache_info().currsize == 0:
        return
    client = get_antcollector_client()
    try:
        client.close()
    finally:
        get_antcollector_client.cache_clear()


SignozClientDep = Annotated[SignozClient, Depends(get_signoz_client)]
ColumnDialectDep = Annotated[ColumnDialect, Depends(get_column_dialect)]
AntcollectorClientDep = Annotated[AntcollectorClient, Depends(get_antcollector_client)]


@lru_cache(maxsize=1)
def get_trace_store(c: SignozClientDep, dialect: ColumnDialectDep) -> TraceStore:
    return TraceStore(c, dialect=dialect)


def get_record_store(
    reader: SignozClientDep,
    writer: AntcollectorClientDep,
    dialect: ColumnDialectDep,
) -> DiagnosisRecordStore:
    s = get_settings()
    return DiagnosisRecordStore(reader, writer, table=s.diagnosis_record_table, dialect=dialect)


def get_task_stats_store(
    reader: SignozClientDep,
    writer: AntcollectorClientDep,
    dialect: ColumnDialectDep,
) -> TaskStatsStore:
    s = get_settings()
    return TaskStatsStore(reader, writer, table=s.diagnosis_record_table, dialect=dialect)


_RUNTIME_REF: dict = {}  # 运行期由 main.py 设置 {'runtime': Runtime 实例}


def set_runtime(rt) -> None:
    if rt is None:
        _RUNTIME_REF.pop("runtime", None)
    else:
        _RUNTIME_REF["runtime"] = rt


def get_runtime():
    return _RUNTIME_REF.get("runtime")


def get_runner(
    s: SessionDep,
    trace: Annotated["TraceStore", Depends(get_trace_store)],
    record: Annotated["DiagnosisRecordStore", Depends(get_record_store)],
    task_stats: Annotated["TaskStatsStore", Depends(get_task_stats_store)],
    settings: SettingsDep,
) -> Runner:
    from agentlens.infra.ob.tables.dataset_table import DatasetTable
    from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
    from agentlens.infra.ob.tables.job_table import JobTable
    from agentlens.infra.ob.tables.task_table import TaskTable

    return Runner(
        task_table=TaskTable(s),
        job_table=JobTable(s),
        dataset_table=DatasetTable(s),
        diagnoser_table=DiagnoserTable(s),
        model_connection_table=ModelConnectionTable(s),
        trace_store=trace,
        record_store=record,
        task_stats_store=task_stats,
        self_uuid=settings.instance_uuid,
        session_factory=_engine_factory(),
    )
