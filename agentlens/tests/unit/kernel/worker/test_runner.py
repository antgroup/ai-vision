import threading
from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace

import pytest

from agentlens.diagnoser.base import (
    Executor,
    PreEvaluateDecision,
    PreEvaluateSpanFields,
)
from agentlens.diagnoser.registry import ExecutorRegistry
from agentlens.domain.dataset import Dataset, TraceQuerySpec
from agentlens.domain.diagnoser import Diagnoser
from agentlens.domain.diagnosis_record import DiagnosisResult, Execution, Metric
from agentlens.domain.enums import (
    DatasetType,
    DiagnoserMode,
    DiagnoserType,
    JobStatus,
    JobTriggerType,
    TaskStatus,
    Verdict,
)
from agentlens.domain.job import Job
from agentlens.domain.task import Task
from agentlens.infra.antcollector import InMemoryAntcollectorClient
from agentlens.infra.ob.tables.dataset_table import DatasetTable
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.infra.signoz.client import InMemorySignozClient
from agentlens.kernel.worker.runner import (
    Runner,
    _EvaluationTarget,
    _failed_result,
    _parse_trace_timestamp,
    _pre_evaluate_timestamp,
    _resolve_time_range,
    _RunAcc,
    _window_midpoint,
)
from agentlens.service.datasource.signoz import TraceStore
from agentlens.service.datasource.trace import Trace
from agentlens.service.diagnosis_record_store import DiagnosisRecordStore
from agentlens.utils.uuid_gen import (
    new_dataset_uuid,
    new_diagnoser_uuid,
    new_job_uuid,
    new_task_uuid,
)
from tests.unit.diagnoser.projection_factory import trace_from_dict


def _projection(timestamp: str):
    return trace_from_dict(
        {
            "trace_id": "projection",
            "runtime_type": "codex",
            "timestamp": timestamp,
            "user_query": "q",
            "agent_output": "a",
            "outcome": {"total_latency_ms": 0},
        }
    ).projection


def _seed_world(session, signoz):
    diag = Diagnoser(
        uuid=new_diagnoser_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="test-fixed",
        description="",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        spec={"executor": "test_fixed", "metrics": [{"name": "quality"}]},
        version="v1",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    DiagnoserTable(session).insert(diag)

    ds = Dataset(
        uuid=new_dataset_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="ds1",
        description="",
        type=DatasetType.SIGNOZ,
        spec={
            "runtime_type": "custom-runtime",
            "query": {"table": "trace", "where": "`agent_uuid` = 'A'"},
        },
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    DatasetTable(session).insert(ds)

    job = Job(
        uuid=new_job_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="job1",
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        dataset_uuid=ds.uuid,
        diagnoser_uuids=[diag.uuid],
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
        spec={"save_badcase_only": False},
    )
    JobTable(session).insert(job)

    signoz.seed(
        "trace",
        [
            {"trace_id": "t1", "agent_uuid": "A", "steps": ["plan", "answer"]},
            {"trace_id": "t2", "agent_uuid": "A", "steps": ["plan", "tool_call", "answer"]},
        ],
    )

    task = Task(
        uuid=new_task_uuid(),
        job_uuid=job.uuid,
        status=TaskStatus.DISPATCHED,
        worker="w/1.1.1.1",
        scheduled_at=datetime.now(),
        started_at=None,
        finished_at=None,
        attempt=0,
        msg="",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    TaskTable(session).insert(task)
    return task


def test_prepare_executors_accepts_unified_trace_executor(monkeypatch):
    class _Executor(Executor):
        def evaluate(self, trace):
            return None

    created = []

    def create(mode, name, spec):
        created.append((mode, name, spec))
        return _Executor({})

    monkeypatch.setattr(ExecutorRegistry, "create", staticmethod(create))
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=None,
        self_uuid="worker-1",
    )
    diagnoser = SimpleNamespace(
        uuid="dg-input",
        name="input-test",
        type=DiagnoserType.CUSTOM,
        mode=DiagnoserMode.RULE,
        spec={"code": "unused in this routing test"},
    )

    prepared = runner._prepare_executors([diagnoser])

    assert len(prepared) == 1
    assert isinstance(prepared[0]["executor"], _Executor)
    assert created == [("rule", "rule_custom", diagnoser.spec)]


def test_relative_time_windows_are_anchored_to_scheduled_at():
    spec = TraceQuerySpec(table="trace", time_range="5m")

    first = _resolve_time_range(spec, "1h", anchor=datetime(2026, 7, 23, 10, 10, 0))
    second = _resolve_time_range(spec, "1h", anchor=datetime(2026, 7, 23, 10, 15, 0))

    assert first == ("2026-07-23 09:05:00", "2026-07-23 09:10:00")
    assert second == ("2026-07-23 09:10:00", "2026-07-23 09:15:00")
    assert first[1] == second[0]


def test_absolute_time_window_does_not_depend_on_scheduled_at():
    spec = TraceQuerySpec(
        table="trace",
        start_time="2026-07-20 01:00:00",
        end_time="2026-07-20 01:05:00",
    )
    assert _resolve_time_range(spec, anchor=datetime(2026, 7, 23, 10, 15, 0)) == (
        "2026-07-20 01:00:00",
        "2026-07-20 01:05:00",
    )


def test_window_midpoint_uses_resolved_query_range():
    assert _window_midpoint(
        "2026-07-20 01:00:00",
        "2026-07-20 01:05:00",
    ) == datetime(2026, 7, 20, 1, 2, 30)


@pytest.mark.asyncio
async def test_runner_writes_one_record_per_trace_and_marks_success(session):
    store = defaultdict(list)
    signoz = InMemorySignozClient(store)
    antcollector = InMemoryAntcollectorClient(store)
    task = _seed_world(session, signoz)
    runner = Runner(
        task_table=TaskTable(session),
        job_table=JobTable(session),
        dataset_table=DatasetTable(session),
        diagnoser_table=DiagnoserTable(session),
        trace_store=TraceStore(signoz),
        record_store=DiagnosisRecordStore(signoz, antcollector, table="agentlens_trace"),
        self_uuid="w/1.1.1.1",
    )
    await runner.run(task.uuid)
    t = TaskTable(session).get_by_uuid(task.uuid)
    assert t.status == TaskStatus.SUCCEEDED

    written = antcollector.records.get("agentlens_trace", [])
    assert len(written) == 2
    import json

    bodies = [json.loads(r["attributes.record.body"]) for r in written]
    assert all(b["task_uuid"] == task.uuid for b in bodies)
    assert all(b["diagnoser_uuid"].startswith("dg-") for b in bodies)


@pytest.mark.asyncio
async def test_runner_marks_failed_on_exception(session, monkeypatch):
    store = defaultdict(list)
    signoz = InMemorySignozClient(store)
    antcollector = InMemoryAntcollectorClient(store)
    task = _seed_world(session, signoz)

    class _Boom(Exception): ...

    def boom(*_a, **_k):
        raise _Boom("explode")

    monkeypatch.setattr(TraceStore, "read_trace_targets", boom)

    runner = Runner(
        task_table=TaskTable(session),
        job_table=JobTable(session),
        dataset_table=DatasetTable(session),
        diagnoser_table=DiagnoserTable(session),
        trace_store=TraceStore(signoz),
        record_store=DiagnosisRecordStore(signoz, antcollector, table="agentlens_trace"),
        self_uuid="w/1.1.1.1",
    )
    await runner.run(task.uuid)
    t = TaskTable(session).get_by_uuid(task.uuid)
    assert t.status == TaskStatus.FAILED
    assert "explode" in t.msg


@pytest.mark.asyncio
async def test_runner_marks_task_failed_when_phase1_pre_evaluate_query_fails(session, monkeypatch):
    store = defaultdict(list)
    signoz = InMemorySignozClient(store)
    antcollector = InMemoryAntcollectorClient(store)
    task = _seed_world(session, signoz)

    jobs = JobTable(session)
    job = jobs.get_by_uuid(task.job_uuid)
    datasets = DatasetTable(session)
    dataset = datasets.get_by_uuid(job.dataset_uuid)
    datasets.update_by_uuid(
        dataset.uuid,
        {
            "spec": {
                **dataset.spec,
                "query": {
                    **dataset.spec["query"],
                    "time_offset": "",
                },
            }
        },
    )

    class _PreEvaluateExecutor(Executor):
        def pre_evaluate_span_fields(self):
            return (PreEvaluateSpanFields(name="terminal", fields=("signal",)),)

        def evaluate(self, trace):
            return _test_record(trace.trace_id)

    monkeypatch.setattr(
        ExecutorRegistry,
        "create",
        staticmethod(lambda _mode, _name, spec: _PreEvaluateExecutor(spec)),
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError("phase1 query unavailable")

    monkeypatch.setattr(TraceStore, "read_trace_targets", boom)

    runner = Runner(
        task_table=TaskTable(session),
        job_table=jobs,
        dataset_table=datasets,
        diagnoser_table=DiagnoserTable(session),
        trace_store=TraceStore(signoz),
        record_store=DiagnosisRecordStore(signoz, antcollector, table="agentlens_trace"),
        self_uuid="w/1.1.1.1",
    )

    await runner.run(task.uuid)

    failed = TaskTable(session).get_by_uuid(task.uuid)
    assert failed.status == TaskStatus.FAILED
    assert "phase1 query unavailable" in failed.msg
    assert antcollector.records.get("agentlens_trace", []) == []


@pytest.mark.asyncio
async def test_runner_does_not_repeat_scope_validation(session):
    store = defaultdict(list)
    signoz = InMemorySignozClient(store)
    antcollector = InMemoryAntcollectorClient(store)
    task = _seed_world(session, signoz)
    jobs = JobTable(session)
    job = jobs.get_by_uuid(task.job_uuid)

    shared_diag = Diagnoser(
        uuid=new_diagnoser_uuid(),
        space_uuid="s-shard",
        agent_uuid="A",
        name="shared-test-fixed",
        description="",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        spec={"executor": "test_fixed", "metrics": [{"name": "quality"}]},
        version="v1",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    DiagnoserTable(session).insert(shared_diag)
    jobs.update_by_uuid(job.uuid, {"diagnoser_uuids": [shared_diag.uuid]})

    runner = Runner(
        task_table=TaskTable(session),
        job_table=JobTable(session),
        dataset_table=DatasetTable(session),
        diagnoser_table=DiagnoserTable(session),
        trace_store=TraceStore(signoz),
        record_store=DiagnosisRecordStore(signoz, antcollector, table="agentlens_trace"),
        self_uuid="w/1.1.1.1",
    )
    await runner.run(task.uuid)
    t = TaskTable(session).get_by_uuid(task.uuid)
    assert t.status == TaskStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_runner_emits_failed_results_for_unprojectable_trace(session):
    """An unprojectable selected Trace must not be
    silently dropped -- it gets one failed result set per bound Diagnoser
    and total_n/metric totals stay aligned; the Task still succeeds."""
    store = defaultdict(list)
    signoz = InMemorySignozClient(store)
    antcollector = InMemoryAntcollectorClient(store)

    diag = Diagnoser(
        uuid=new_diagnoser_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="latency-projection",
        description="",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        spec={
            "executor": "total_latency",
            "metrics": [{"name": "latency_decomposition"}],
            "score_role": "signal",
        },
        version="3.0.0",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    DiagnoserTable(session).insert(diag)

    ds = Dataset(
        uuid=new_dataset_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="ds-unprojectable",
        description="",
        type=DatasetType.SIGNOZ,
        spec={
            "runtime_type": "custom-runtime",
            "query": {"table": "trace", "where": "`agent_uuid` = 'A'"},
        },
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    DatasetTable(session).insert(ds)

    job = Job(
        uuid=new_job_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="job-unprojectable",
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        dataset_uuid=ds.uuid,
        diagnoser_uuids=[diag.uuid],
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
        spec={"save_badcase_only": False},
    )
    JobTable(session).insert(job)

    # No source span payload that can be projected.
    signoz.seed(
        "trace",
        [
            {"trace_id": "unprojectable-1", "agent_uuid": "A"},
        ],
    )

    task = Task(
        uuid=new_task_uuid(),
        job_uuid=job.uuid,
        status=TaskStatus.DISPATCHED,
        worker="w/1.1.1.1",
        scheduled_at=datetime.now(),
        started_at=None,
        finished_at=None,
        attempt=0,
        msg="",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    TaskTable(session).insert(task)

    runner = Runner(
        task_table=TaskTable(session),
        job_table=JobTable(session),
        dataset_table=DatasetTable(session),
        diagnoser_table=DiagnoserTable(session),
        trace_store=TraceStore(signoz),
        record_store=DiagnosisRecordStore(signoz, antcollector, table="agentlens_trace"),
        self_uuid="w/1.1.1.1",
    )
    await runner.run(task.uuid)

    t = TaskTable(session).get_by_uuid(task.uuid)
    assert t.status == TaskStatus.SUCCEEDED
    assert t.stats["diagnosers"][diag.uuid]["latency_decomposition"]["total"] == 1
    assert t.stats["diagnosers"][diag.uuid]["latency_decomposition"]["failed"] == 1

    written = antcollector.records.get("agentlens_trace", [])
    assert len(written) == 1
    import json

    row = written[0]
    body = json.loads(row["attributes.record.body"])
    assert row["attributes.verdict"] == "failed"
    assert body["target_id"] == "unprojectable-1"
    assert body["metrics"][0]["verdict"] == "failed"
    assert body["execution"]["status"] == "failed"


@pytest.mark.asyncio
async def test_runner_empty_valid_query_succeeds_with_zero_total(session):
    """A valid query returning no targets is a
    successful execution with total_n=0, not a failure."""
    store = defaultdict(list)
    signoz = InMemorySignozClient(store)
    antcollector = InMemoryAntcollectorClient(store)
    task = _seed_world(session, signoz)
    # Narrow the Dataset where-clause so the valid query matches zero rows.
    jobs = JobTable(session)
    job = jobs.get_by_uuid(task.job_uuid)
    datasets = DatasetTable(session)
    dataset = datasets.get_by_uuid(job.dataset_uuid)
    datasets.update_by_uuid(
        dataset.uuid,
        {
            "spec": {
                **dataset.spec,
                "query": {
                    **dataset.query_spec(),
                    "where": "`agent_uuid` = 'nonexistent-agent'",
                },
            },
        },
    )

    runner = Runner(
        task_table=TaskTable(session),
        job_table=JobTable(session),
        dataset_table=DatasetTable(session),
        diagnoser_table=DiagnoserTable(session),
        trace_store=TraceStore(signoz),
        record_store=DiagnosisRecordStore(signoz, antcollector, table="agentlens_trace"),
        self_uuid="w/1.1.1.1",
    )
    await runner.run(task.uuid)

    t = TaskTable(session).get_by_uuid(task.uuid)
    assert t.status == TaskStatus.SUCCEEDED
    assert "saved_records=0" in t.msg
    written = antcollector.records.get("agentlens_trace", [])
    assert len(written) == 0


def _seed_grouped_world(session, signoz):
    """Seed a SigNoz world whose rows are grouped by fixed trace_id."""
    diag = Diagnoser(
        uuid=new_diagnoser_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="test-fixed",
        description="",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        spec={"executor": "test_fixed", "metrics": [{"name": "quality"}]},
        version="v1",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    DiagnoserTable(session).insert(diag)

    ds = Dataset(
        uuid=new_dataset_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="ds-grp",
        description="",
        type=DatasetType.SIGNOZ,
        spec={
            "runtime_type": "custom-runtime",
            "query": {
                "table": "trace",
                "where": "`agent_uuid` = 'A'",
                "group_time_window_min": 10,
            },
        },
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    DatasetTable(session).insert(ds)

    job = Job(
        uuid=new_job_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="job-grp",
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        dataset_uuid=ds.uuid,
        diagnoser_uuids=[diag.uuid],
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
        spec={"save_badcase_only": False},
    )
    JobTable(session).insert(job)

    signoz.seed(
        "trace",
        [
            {"trace_id": "t1", "agent_uuid": "A", "name": "spanA", "period": "2026-06-10 13:00:00"},
            {"trace_id": "t1", "agent_uuid": "A", "name": "spanB", "period": "2026-06-10 13:00:01"},
            {"trace_id": "t2", "agent_uuid": "A", "name": "spanC", "period": "2026-06-10 13:00:00"},
        ],
    )

    task = Task(
        uuid=new_task_uuid(),
        job_uuid=job.uuid,
        status=TaskStatus.DISPATCHED,
        worker="w/1.1.1.1",
        scheduled_at=datetime.now(),
        started_at=None,
        finished_at=None,
        attempt=0,
        msg="",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    TaskTable(session).insert(task)
    return task


@pytest.mark.asyncio
async def test_runner_writes_stats_to_task(session):
    """A successful Task persists per-Diagnoser metric counters."""
    store = defaultdict(list)
    signoz = InMemorySignozClient(store)
    antcollector = InMemoryAntcollectorClient(store)
    task = _seed_world(session, signoz)
    runner = Runner(
        task_table=TaskTable(session),
        job_table=JobTable(session),
        dataset_table=DatasetTable(session),
        diagnoser_table=DiagnoserTable(session),
        trace_store=TraceStore(signoz),
        record_store=DiagnosisRecordStore(signoz, antcollector, table="agentlens_trace"),
        self_uuid="w/1.1.1.1",
    )
    await runner.run(task.uuid)
    t = TaskTable(session).get_by_uuid(task.uuid)
    assert t.status == TaskStatus.SUCCEEDED
    dg = JobTable(session).get_by_uuid(task.job_uuid).diagnoser_uuids[0]
    s = t.stats["diagnosers"][dg]["quality"]
    assert s["total"] == 2
    assert s["good"] + s["bad"] == 2


def _seed_grouped_small_batch(session, signoz):
    """group_batch_size=1 + 2 个 trace_id → 2 个批次，用于验证 stats 跨批累计不被重置。"""
    diag = Diagnoser(
        uuid=new_diagnoser_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="test-fixed",
        description="",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        spec={"executor": "test_fixed", "metrics": [{"name": "quality"}]},
        version="v1",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    DiagnoserTable(session).insert(diag)
    ds = Dataset(
        uuid=new_dataset_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="ds-grp-1",
        description="",
        type=DatasetType.SIGNOZ,
        spec={
            "runtime_type": "custom-runtime",
            "query": {
                "table": "trace",
                "where": "`agent_uuid` = 'A'",
                "group_time_window_min": 10,
                "group_batch_size": 1,
            },
        },
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    DatasetTable(session).insert(ds)
    job = Job(
        uuid=new_job_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="job-grp-1",
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        dataset_uuid=ds.uuid,
        diagnoser_uuids=[diag.uuid],
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
        spec={"save_badcase_only": False},
    )
    JobTable(session).insert(job)
    signoz.seed(
        "trace",
        [
            {"trace_id": "t1", "agent_uuid": "A", "name": "spanA", "period": "2026-06-10 13:00:00"},
            {"trace_id": "t2", "agent_uuid": "A", "name": "spanC", "period": "2026-06-10 13:00:00"},
        ],
    )
    task = Task(
        uuid=new_task_uuid(),
        job_uuid=job.uuid,
        status=TaskStatus.DISPATCHED,
        worker="w/1.1.1.1",
        scheduled_at=datetime.now(),
        started_at=None,
        finished_at=None,
        attempt=0,
        msg="",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    TaskTable(session).insert(task)
    return task, diag.uuid


@pytest.mark.asyncio
async def test_runner_stats_accumulate_across_batches(session):
    store = defaultdict(list)
    signoz = InMemorySignozClient(store)
    antcollector = InMemoryAntcollectorClient(store)
    task, dg = _seed_grouped_small_batch(session, signoz)
    runner = Runner(
        task_table=TaskTable(session),
        job_table=JobTable(session),
        dataset_table=DatasetTable(session),
        diagnoser_table=DiagnoserTable(session),
        trace_store=TraceStore(signoz),
        record_store=DiagnosisRecordStore(signoz, antcollector, table="agentlens_trace"),
        self_uuid="w/1.1.1.1",
    )
    await runner.run(task.uuid)
    t = TaskTable(session).get_by_uuid(task.uuid)
    assert t.status == TaskStatus.SUCCEEDED
    # 2 个批次各 1 条 → stats 累计 total=2（不被每批重置）
    assert t.stats["diagnosers"][dg]["quality"]["total"] == 2


@pytest.mark.asyncio
async def test_runner_signoz_groups_spans_by_trace_id(session):
    store = defaultdict(list)
    signoz = InMemorySignozClient(store)
    antcollector = InMemoryAntcollectorClient(store)
    task = _seed_grouped_world(session, signoz)
    runner = Runner(
        task_table=TaskTable(session),
        job_table=JobTable(session),
        dataset_table=DatasetTable(session),
        diagnoser_table=DiagnoserTable(session),
        trace_store=TraceStore(signoz),
        record_store=DiagnosisRecordStore(signoz, antcollector, table="agentlens_trace"),
        self_uuid="w/1.1.1.1",
    )
    await runner.run(task.uuid)
    t = TaskTable(session).get_by_uuid(task.uuid)
    assert t.status == TaskStatus.SUCCEEDED

    written = antcollector.records.get("agentlens_trace", [])
    import json

    bodies = [json.loads(r["attributes.record.body"]) for r in written]
    assert len(bodies) == 2
    target_ids = sorted(b["target_id"] for b in bodies)
    assert target_ids == ["t1", "t2"]


def _test_record(_target_id: str, verdict: Verdict = Verdict.GOOD) -> DiagnosisResult:
    if verdict is Verdict.FAILED:
        return DiagnosisResult(
            metrics=[Metric(name="quality", verdict=Verdict.FAILED, reason="executor error")],
            execution=Execution.failed("test_failure", "executor error"),
        )
    return DiagnosisResult(
        metrics=[
            Metric(
                name="quality",
                verdict=verdict,
                reason="bad" if verdict is Verdict.BAD else "",
            )
        ],
        execution=Execution.succeeded(),
    )


def test_trace_timestamp_normalizes_iso_and_epoch_nanoseconds():
    expected = (
        datetime.fromisoformat("2026-07-23T15:46:54.098+08:00").astimezone().replace(tzinfo=None)
    )
    assert _parse_trace_timestamp("2026-07-23T15:46:54.098+08:00") == expected
    assert _parse_trace_timestamp("2026-07-23T07:46:54.098Z") == expected
    epoch_expected = (
        datetime.fromisoformat("2025-07-22T04:00:00+00:00").astimezone().replace(tzinfo=None)
    )
    assert _parse_trace_timestamp(1_753_156_800_000_000_000) == epoch_expected
    assert _parse_trace_timestamp("1753156800000000000") == epoch_expected
    assert _parse_trace_timestamp("not-a-time") is None


def test_pre_evaluate_timestamp_uses_earliest_iso_value():
    rows = [
        {"start_time_unix_nano": "2026-07-23T15:46:55.098+08:00"},
        {"start_time_unix_nano": "2026-07-23T15:46:54.098+08:00"},
        {"start_time_unix_nano": None},
    ]

    expected = (
        datetime.fromisoformat("2026-07-23T15:46:54.098+08:00").astimezone().replace(tzinfo=None)
    )
    assert _pre_evaluate_timestamp(rows) == expected


def test_failed_result_marks_each_declared_metric_failed():
    diagnoser = SimpleNamespace(spec={"metrics": [{"name": "quality"}, {"name": "safety"}]})

    record = _failed_result(
        diagnoser,
        RuntimeError("boom"),
    )

    assert record.verdict is Verdict.FAILED
    results = {result.name: result for result in record.metrics}
    assert set(results) == {"quality", "safety"}
    assert all(result.verdict is Verdict.FAILED for result in results.values())
    assert all(result.reason == "诊断器执行失败" for result in results.values())
    assert record.execution.code == "executor_exception"
    assert record.execution.evidence == {"error_type": "RuntimeError"}


def test_run_acc_tracks_good_and_failed_separately():
    acc = _RunAcc()
    good = _test_record("good")
    good.metrics = [
        Metric(
            name="quality",
            verdict=Verdict.GOOD,
            reason="not applicable",
        )
    ]
    failed = _test_record("fail", Verdict.FAILED)
    failed.metrics = [
        Metric(
            name="quality",
            verdict=Verdict.FAILED,
            reason="executor error",
        )
    ]

    acc.record(good, "dg-1")
    acc.record(failed, "dg-1")

    assert acc.stats["diagnosers"]["dg-1"]["quality"] == {
        "total": 2,
        "good": 1,
        "bad": 0,
        "failed": 1,
    }


def test_run_acc_records_only_declared_multi_metric_results():
    acc = _RunAcc()
    record = _test_record("multi")
    record.metrics = [
        Metric(
            name="medical_safety",
            verdict=Verdict.GOOD,
        ),
        Metric(
            name="urgent_triage",
            verdict=Verdict.BAD,
            reason="urgent triage missing",
        ),
    ]

    acc.record(record, "dg-1", evaluate=True)

    assert acc.stats["diagnosers"]["dg-1"] == {
        "medical_safety": {
            "total": 1,
            "good": 1,
            "bad": 0,
            "failed": 0,
            "evaluate": 1,
        },
        "urgent_triage": {
            "total": 1,
            "good": 0,
            "bad": 1,
            "failed": 0,
            "evaluate": 1,
        },
    }


def test_pre_evaluate_final_results_are_counted_but_never_persisted():
    class _NoEvaluateExecutor(Executor):
        def evaluate(self, trace):
            raise AssertionError("pre_evaluate-final target must not be evaluated")

    diagnosers = [
        Diagnoser(
            uuid=f"dg-{name}",
            space_uuid="s",
            agent_uuid="",
            name=name,
            description="",
            type=DiagnoserType.BUILTIN,
            mode=DiagnoserMode.RULE,
            spec={"name": name, "metrics": [{"name": "quality"}]},
            version="v1",
            gmt_create=None,
            gmt_modified=None,
            creator=None,
            modifier=None,
        )
        for name in ("good", "bad", "failed")
    ]
    prepared = [
        {
            "diagnoser": diagnoser,
            "executor": _NoEvaluateExecutor({}),
            "pre_evaluate_span_fields": (),
        }
        for diagnoser in diagnosers
    ]
    good = _test_record("trace-1")
    bad = _test_record("trace-1", Verdict.BAD)
    failed = _test_record("trace-1", Verdict.FAILED)
    failed.metrics = [
        Metric(
            name="quality",
            verdict=Verdict.FAILED,
            reason="pre_evaluate failed",
        )
    ]
    failed.execution = Execution.failed(
        "pre_evaluate_user_query_unavailable",
        "phase1 目标行未提取到用户问题",
        {"llm": {"latency_ms": 12.5}},
    )
    log_calls = []
    written = []
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda record, **_kwargs: written.append(record)),
        self_uuid="worker-1",
    )
    runner._log = SimpleNamespace(
        warning=lambda event, **fields: log_calls.append((event, fields)),
        debug=lambda *_args, **_kwargs: None,
    )
    acc = _RunAcc()

    count = runner._evaluate_all(
        SimpleNamespace(uuid="task-pre_evaluate-final", attempt=1),
        SimpleNamespace(uuid="job-1"),
        diagnosers,
        [
            _EvaluationTarget(
                trace=Trace(trace_id="trace-1", runtime_type="custom-runtime", spans=[]),
                pre_evaluate_results={
                    "dg-good": good,
                    "dg-bad": bad,
                    "dg-failed": failed,
                },
                evaluate_diagnoser_uuids=set(),
            )
        ],
        acc=acc,
        prepared=prepared,
    )

    assert count == 0
    assert written == []
    assert acc.stats["diagnosers"]["dg-good"]["quality"] == {
        "total": 1,
        "good": 1,
        "bad": 0,
        "failed": 0,
        "evaluate": 0,
    }
    assert acc.stats["diagnosers"]["dg-bad"]["quality"]["bad"] == 1
    assert acc.stats["diagnosers"]["dg-failed"]["quality"]["failed"] == 1
    assert log_calls == [
        (
            "runner.diagnosis_failed",
            {
                "task_uuid": "task-pre_evaluate-final",
                "target_id": "trace-1",
                "diagnoser_uuid": "dg-failed",
                "diagnoser_name": "failed",
                "code": "pre_evaluate_user_query_unavailable",
                "msg": "phase1 目标行未提取到用户问题",
                "llm_latency_ms": 12.5,
            },
        )
    ]


def test_pre_evaluate_skip_is_local_counted_and_never_persisted():
    class _Executor(Executor):
        def evaluate(self, trace):
            return _test_record(trace.trace_id)

    diagnosers = [
        Diagnoser(
            uuid=f"dg-{name}",
            space_uuid="s",
            agent_uuid="",
            name=name,
            description="",
            type=DiagnoserType.BUILTIN,
            mode=DiagnoserMode.RULE,
            spec={"name": name, "metrics": [{"name": "quality"}]},
            version="v1",
            gmt_create=None,
            gmt_modified=None,
            creator=None,
            modifier=None,
        )
        for name in ("skipped", "evaluated")
    ]
    prepared = [
        {
            "diagnoser": diagnoser,
            "executor": _Executor({}),
            "pre_evaluate_span_fields": (),
        }
        for diagnoser in diagnosers
    ]
    written = []
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda record, **_kwargs: written.append(record)),
        self_uuid="worker-1",
    )
    runner._log = SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
        debug=lambda *_args, **_kwargs: None,
        exception=lambda *_args, **_kwargs: None,
    )
    acc = _RunAcc()

    count = runner._evaluate_all(
        SimpleNamespace(uuid="task-skip", attempt=1),
        SimpleNamespace(uuid="job-1", spec={"save_badcase_only": False}),
        diagnosers,
        [
            _EvaluationTarget(
                trace=Trace(trace_id="trace-1", runtime_type="custom-runtime", spans=[]),
                skipped_diagnosers={"dg-skipped": "query_missing_or_blank"},
                evaluate_diagnoser_uuids={"dg-evaluated"},
            )
        ],
        acc=acc,
        prepared=prepared,
    )

    assert count == 1
    assert [record.diagnoser_uuid for record in written] == ["dg-evaluated"]
    assert acc.stats["diagnosers"]["dg-skipped"]["quality"] == {
        "total": 0,
        "good": 0,
        "bad": 0,
        "failed": 0,
        "skipped": 1,
    }
    assert acc.stats["diagnosers"]["dg-evaluated"]["quality"] == {
        "total": 1,
        "good": 1,
        "bad": 0,
        "failed": 0,
        "evaluate": 1,
    }


@pytest.mark.parametrize(
    ("spec", "verdict", "expected_saved"),
    [
        ({}, Verdict.GOOD, 0),
        ({}, Verdict.BAD, 1),
        ({}, Verdict.FAILED, 0),
        ({"save_badcase_only": False}, Verdict.GOOD, 1),
        ({"save_badcase_only": False}, Verdict.BAD, 1),
        ({"save_badcase_only": False}, Verdict.FAILED, 1),
    ],
)
def test_evaluate_result_persistence_matrix_keeps_stats(monkeypatch, spec, verdict, expected_saved):
    class _Executor(Executor):
        def evaluate(self, trace):
            return _test_record(trace.trace_id, verdict)

    monkeypatch.setattr(
        ExecutorRegistry,
        "create",
        staticmethod(lambda *_args: _Executor({})),
    )

    written = []
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda record, **_kwargs: written.append(record)),
        self_uuid="worker-1",
    )
    diagnoser = Diagnoser(
        uuid="dg-policy",
        space_uuid="s",
        agent_uuid="a",
        name="policy",
        description="",
        type=DiagnoserType.CUSTOM,
        mode=DiagnoserMode.RULE,
        spec={"executor": "policy", "metrics": [{"name": "quality"}]},
        version="v1",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    acc = _RunAcc(owner_enabled=True)

    saved = runner._evaluate_all(
        SimpleNamespace(uuid="task-policy", attempt=3),
        SimpleNamespace(uuid="job-policy", spec=spec),
        [diagnoser],
        [
            Trace(
                trace_id="trace-policy",
                runtime_type="custom-runtime",
                spans=[],
                owner_id="owner-policy",
            )
        ],
        acc=acc,
    )

    assert saved == expected_saved
    assert len(written) == expected_saved
    stats = acc.stats["diagnosers"][diagnoser.uuid]["quality"]
    assert stats[verdict.value] == 1
    assert stats["total"] == 1
    owner_stats = acc.owner_stats["owner-policy"]["diagnosers"][diagnoser.uuid]["quality"]
    assert owner_stats == stats


def test_default_policy_saves_complete_multi_metric_bad_record(monkeypatch):
    class _Executor(Executor):
        def evaluate(self, trace):
            return DiagnosisResult(
                metrics=[
                    Metric(
                        name="passed_metric",
                        verdict=Verdict.GOOD,
                    ),
                    Metric(
                        name="failed_metric",
                        verdict=Verdict.BAD,
                        reason="failed metric",
                    ),
                ],
                execution=Execution.succeeded(),
            )

    monkeypatch.setattr(
        ExecutorRegistry,
        "create",
        staticmethod(lambda *_args: _Executor({})),
    )
    written = []
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda record, **_kwargs: written.append(record)),
        self_uuid="worker-1",
    )
    diagnoser = Diagnoser(
        uuid="dg-multi-policy",
        space_uuid="s",
        agent_uuid="a",
        name="multi-policy",
        description="",
        type=DiagnoserType.CUSTOM,
        mode=DiagnoserMode.RULE,
        spec={
            "executor": "multi-policy",
            "metrics": [{"name": "passed_metric"}, {"name": "failed_metric"}],
        },
        version="v1",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )

    saved = runner._evaluate_all(
        SimpleNamespace(uuid="task-multi-policy", attempt=1),
        SimpleNamespace(uuid="job-multi-policy"),
        [diagnoser],
        [Trace(trace_id="trace-multi-policy", runtime_type="custom-runtime", spans=[])],
        acc=_RunAcc(),
    )

    assert saved == 1
    assert len(written) == 1
    assert [result.name for result in written[0].metrics] == [
        "passed_metric",
        "failed_metric",
    ]


@pytest.mark.asyncio
async def test_runner_grouped_missing_source_writes_failed_result(session, monkeypatch):
    store = defaultdict(list)
    signoz = InMemorySignozClient(store)
    antcollector = InMemoryAntcollectorClient(store)
    task = _seed_grouped_world(session, signoz)
    runner = Runner(
        task_table=TaskTable(session),
        job_table=JobTable(session),
        dataset_table=DatasetTable(session),
        diagnoser_table=DiagnoserTable(session),
        trace_store=TraceStore(signoz),
        record_store=DiagnosisRecordStore(signoz, antcollector, table="agentlens_trace"),
        self_uuid="w/1.1.1.1",
    )
    monkeypatch.setattr(
        "agentlens.kernel.worker.runner.project_trace",
        lambda trace: trace.with_projection(_projection("2026-07-22T10:00:00")),
    )
    original_targets = runner.traces.read_trace_targets

    def targets_with_missing(*args, **kwargs):
        _keys, rows = original_targets(*args, **kwargs)
        t1_rows = [row for row in rows if row.get("trace_id") == "t1"]
        return ["t1", "missing"], [
            *t1_rows,
            {"trace_id": "missing", "start_time_unix_nano": None},
        ]

    monkeypatch.setattr(
        runner.traces,
        "read_trace_targets",
        targets_with_missing,
    )
    original_read = runner.traces.read_trace_spans_batch_range

    def read_without_missing(*args, **kwargs):
        return [
            span for span in original_read(*args, **kwargs) if span.get("trace_id") != "missing"
        ]

    monkeypatch.setattr(runner.traces, "read_trace_spans_batch_range", read_without_missing)

    await runner.run(task.uuid)

    saved = TaskTable(session).get_by_uuid(task.uuid)
    diagnoser_uuid = JobTable(session).get_by_uuid(task.job_uuid).diagnoser_uuids[0]
    assert saved.stats["source"] == {
        "total": 2,
        "invalid": 1,
        "projection_failed": 0,
    }
    assert saved.stats["diagnosers"][diagnoser_uuid]["quality"]["total"] == 2
    assert saved.stats["diagnosers"][diagnoser_uuid]["quality"]["failed"] == 1
    assert saved.stats["diagnosers"][diagnoser_uuid]["quality"]["evaluate"] == 2

    import json

    bodies = [
        json.loads(row["attributes.record.body"]) for row in antcollector.records["agentlens_trace"]
    ]
    missing = next(body for body in bodies if body["target_id"] == "missing")
    assert missing["metrics"][0]["verdict"] == "failed"
    assert missing["execution"]["status"] == "failed"


@pytest.mark.asyncio
async def test_runner_reuses_and_closes_executor_across_grouped_batches(session, monkeypatch):
    store = defaultdict(list)
    signoz = InMemorySignozClient(store)
    antcollector = InMemoryAntcollectorClient(store)
    task, _diagnoser_uuid = _seed_grouped_small_batch(session, signoz)
    calls = {"create": 0, "pre_evaluate": 0, "close": 0}

    class _TrackingExecutor(Executor):
        def pre_evaluate(self, value):
            calls["pre_evaluate"] += 1
            return super().pre_evaluate(value)

        def evaluate(self, trace):
            return _test_record(trace.trace_id)

        def close(self):
            calls["close"] += 1

    def create_executor(*_args):
        calls["create"] += 1
        return _TrackingExecutor({})

    monkeypatch.setattr(ExecutorRegistry, "create", staticmethod(create_executor))
    runner = Runner(
        task_table=TaskTable(session),
        job_table=JobTable(session),
        dataset_table=DatasetTable(session),
        diagnoser_table=DiagnoserTable(session),
        trace_store=TraceStore(signoz),
        record_store=DiagnosisRecordStore(signoz, antcollector, table="agentlens_trace"),
        self_uuid="w/1.1.1.1",
    )

    await runner.run(task.uuid)

    assert calls == {"create": 1, "pre_evaluate": 2, "close": 1}


def test_evaluate_all_runs_same_trace_diagnosers_concurrently(monkeypatch):
    barrier = threading.Barrier(2, timeout=2)
    evaluated = {}
    evaluation_threads = []

    class _ConcurrentExecutor(Executor):
        def evaluate(self, trace):
            evaluated[self.spec["name"]] = trace
            evaluation_threads.append(threading.get_ident())
            barrier.wait()
            return _test_record(trace.trace_id)

    executors = {name: _ConcurrentExecutor({"name": name}) for name in ("first", "second")}
    monkeypatch.setattr(
        ExecutorRegistry,
        "create",
        staticmethod(lambda _mode, _name, spec: executors[spec["name"]]),
    )

    source_trace = Trace(
        trace_id="trace-1",
        runtime_type="custom-runtime",
        spans=[{"trace_id": "trace-1"}],
    )
    projected = source_trace.with_projection(_projection("2026-07-14T10:00:00"))
    projection_calls = []

    def project_once(trace):
        projection_calls.append(trace)
        return projected

    monkeypatch.setattr("agentlens.kernel.worker.runner.project_trace", project_once)

    written = []
    write_threads = []

    def write_record(record, **_kw):
        written.append(record)
        write_threads.append(threading.get_ident())

    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=write_record),
        self_uuid="worker-1",
    )
    diagnosers = [
        Diagnoser(
            uuid=f"dg-{name}",
            space_uuid="s",
            agent_uuid="",
            name=name,
            description="",
            type=DiagnoserType.CUSTOM,
            mode=DiagnoserMode.LLM,
            spec={"name": name, "metrics": [{"name": "quality"}]},
            version="v1",
            gmt_create=None,
            gmt_modified=None,
            creator=None,
            modifier=None,
        )
        for name in ("first", "second")
    ]
    caller_thread = threading.get_ident()

    count = runner._evaluate_all(
        SimpleNamespace(uuid="task-1", attempt=1),
        SimpleNamespace(uuid="job-1", spec={"save_badcase_only": False}),
        diagnosers,
        [source_trace],
        acc=_RunAcc(),
    )

    assert count == 2
    assert projection_calls == [source_trace]
    assert evaluated == {"first": projected, "second": projected}
    assert len(set(evaluation_threads)) == 2
    assert [record.diagnoser_uuid for record in written] == ["dg-first", "dg-second"]
    assert write_threads == [caller_thread, caller_thread]


def test_evaluate_all_does_not_read_task_ownership(monkeypatch):
    class _Executor(Executor):
        def evaluate(self, trace):
            return _test_record(trace.trace_id)

    monkeypatch.setattr(
        ExecutorRegistry,
        "create",
        staticmethod(lambda *_args: _Executor({})),
    )

    class _Tasks:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected Task access during evaluation: {name}")

    written = []
    runner = Runner(
        task_table=_Tasks(),
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda record, **_kw: written.append(record)),
        self_uuid="worker-1",
    )
    diagnosers = [
        Diagnoser(
            uuid=f"dg-{name}",
            space_uuid="s",
            agent_uuid="",
            name=name,
            description="",
            type=DiagnoserType.CUSTOM,
            mode=DiagnoserMode.RULE,
            spec={"executor": name, "metrics": [{"name": "quality"}]},
            version="v1",
            gmt_create=None,
            gmt_modified=None,
            creator=None,
            modifier=None,
        )
        for name in ("first", "second")
    ]

    acc = _RunAcc()
    count = runner._evaluate_all(
        SimpleNamespace(uuid="task-1", attempt=2),
        SimpleNamespace(uuid="job-1", spec={"save_badcase_only": False}),
        diagnosers,
        [Trace(trace_id="trace-1", runtime_type="custom-runtime", spans=[])],
        acc=acc,
    )

    assert count == 2
    assert [record.diagnoser_uuid for record in written] == ["dg-first", "dg-second"]
    assert set(acc.stats["diagnosers"]) == {"dg-first", "dg-second"}


def test_evaluate_all_runs_every_target(monkeypatch):
    seen = []

    class _FullExecutor(Executor):
        def evaluate(self, trace):
            seen.append(trace.trace_id)
            return _test_record(trace.trace_id)

    executor = _FullExecutor({})
    monkeypatch.setattr(
        ExecutorRegistry,
        "create",
        staticmethod(lambda _mode, _name, _spec: executor),
    )
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda _record, **_kw: None),
        self_uuid="worker-1",
    )
    diagnoser = Diagnoser(
        uuid="dg-full",
        space_uuid="s",
        agent_uuid="",
        name="full",
        description="",
        type=DiagnoserType.CUSTOM,
        mode=DiagnoserMode.RULE,
        spec={"executor": "full", "metrics": [{"name": "quality"}]},
        version="v1",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    traces = [
        Trace(trace_id=f"trace-{index}", runtime_type="custom-runtime", spans=[])
        for index in range(4)
    ]

    count = runner._evaluate_all(
        SimpleNamespace(uuid="task-full", attempt=1),
        SimpleNamespace(uuid="job-1", spec={"save_badcase_only": False}),
        [diagnoser],
        traces,
        acc=_RunAcc(),
    )

    assert seen == [trace.trace_id for trace in traces]
    assert count == len(traces)


def test_pre_evaluate_good_updates_stats_without_projecting_or_writing_a_record(monkeypatch):
    evaluated = []

    def unexpected_projection(_trace):
        raise AssertionError("pre_evaluate-final target must not be projected")

    monkeypatch.setattr("agentlens.kernel.worker.runner.project_trace", unexpected_projection)

    class _PreEvaluateExecutor(Executor):
        def evaluate(self, trace):
            evaluated.append(trace)
            return _test_record(trace.trace_id)

    diagnoser = Diagnoser(
        uuid="dg-pre_evaluate",
        space_uuid="s",
        agent_uuid="",
        name="pre_evaluate",
        description="",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        spec={"executor": "pre_evaluate", "metrics": [{"name": "quality"}]},
        version="v1",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    pre_evaluate_record = _test_record("trace-pre_evaluate")
    written = []
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda record, **_kwargs: written.append(record)),
        self_uuid="worker-1",
    )
    acc = _RunAcc()

    count = runner._evaluate_all(
        SimpleNamespace(uuid="task-pre_evaluate", attempt=1),
        SimpleNamespace(
            uuid="job-pre_evaluate",
            spec={"save_badcase_only": False},
        ),
        [diagnoser],
        [
            _EvaluationTarget(
                trace=Trace(
                    trace_id="trace-pre_evaluate",
                    runtime_type="custom-runtime",
                    spans=[],
                ),
                pre_evaluate_results={diagnoser.uuid: pre_evaluate_record},
                evaluate_diagnoser_uuids=set(),
            )
        ],
        acc=acc,
        prepared=[{"diagnoser": diagnoser, "executor": _PreEvaluateExecutor({})}],
    )

    assert evaluated == []
    assert count == 0
    assert written == []
    assert acc.stats["diagnosers"][diagnoser.uuid]["quality"] == {
        "total": 1,
        "good": 1,
        "bad": 0,
        "failed": 0,
        "evaluate": 0,
    }


def test_pre_evaluate_good_skips_per_target_task_queries():
    class _PreEvaluateExecutor(Executor):
        def evaluate(self, trace):
            raise AssertionError("pre_evaluate-final target must not be evaluated")

    class _Tasks:
        def __init__(self):
            self.get_calls = 0

        def get_by_uuid(self, _uuid):
            self.get_calls += 1
            return SimpleNamespace(status=TaskStatus.RUNNING)

    diagnoser = Diagnoser(
        uuid="dg-pre_evaluate-liveness",
        space_uuid="s",
        agent_uuid="",
        name="pre_evaluate",
        description="",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        spec={"executor": "pre_evaluate", "metrics": [{"name": "quality"}]},
        version="v1",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    tasks = _Tasks()
    runner = Runner(
        task_table=tasks,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda *_args, **_kwargs: None),
        self_uuid="worker-1",
    )
    task = SimpleNamespace(uuid="task-pre_evaluate-liveness", attempt=1)
    runner._evaluate_all(
        task,
        SimpleNamespace(uuid="job-pre_evaluate-liveness"),
        [diagnoser],
        [
            _EvaluationTarget(
                trace=Trace(
                    trace_id=f"trace-{index}",
                    runtime_type="custom-runtime",
                    spans=[],
                ),
                pre_evaluate_results={diagnoser.uuid: _test_record(f"trace-{index}")},
                evaluate_diagnoser_uuids=set(),
            )
            for index in range(3)
        ],
        acc=_RunAcc(),
        prepared=[{"diagnoser": diagnoser, "executor": _PreEvaluateExecutor({})}],
    )

    assert tasks.get_calls == 0


def test_grouped_pre_evaluate_routes_each_executor_and_hydrates_trace_union_once(monkeypatch):
    evaluated = defaultdict(list)
    pre_evaluate_rows_seen = defaultdict(list)
    pre_evaluate_row_keys = defaultdict(list)

    class _SelectiveExecutor(Executor):
        def pre_evaluate_span_fields(self):
            return (
                PreEvaluateSpanFields(
                    name=self.spec["span_name"],
                    fields=(self.spec["field"],),
                ),
            )

        def pre_evaluate(self, value):
            pre_evaluate_rows_seen[self.spec["name"]].extend(value.rows)
            pre_evaluate_row_keys[self.spec["name"]].extend(set(row) for row in value.rows)
            if any(row.get(self.spec["field"]) == "candidate" for row in value.rows):
                return PreEvaluateDecision.continue_evaluation()
            if any(row.get(self.spec["field"]) == "skip" for row in value.rows):
                return PreEvaluateDecision.skip("test_input_invalid")
            return PreEvaluateDecision.final(_test_record(value.target_id))

        def evaluate(self, trace):
            evaluated[self.spec["name"]].append(trace.trace_id)
            return _test_record(trace.trace_id)

    monkeypatch.setattr(
        ExecutorRegistry,
        "create",
        staticmethod(lambda _mode, _name, spec: _SelectiveExecutor(spec)),
    )
    monkeypatch.setattr(
        "agentlens.kernel.worker.runner.resolve_executor_name",
        lambda _type, _mode, spec: spec["executor"],
    )

    def project(trace):
        if trace.trace_id == "trace-3":
            return trace.with_projection(None, error="projection failed")
        return trace.with_projection(_projection("2026-07-22T10:00:00"))

    monkeypatch.setattr("agentlens.kernel.worker.runner.project_trace", project)

    diagnosers = [
        Diagnoser(
            uuid=f"dg-{name}",
            space_uuid="s",
            agent_uuid="",
            name=name,
            description="",
            type=DiagnoserType.BUILTIN,
            mode=DiagnoserMode.RULE,
            spec={
                "executor": name,
                "name": name,
                "field": field,
                "span_name": f"span-{name}",
                "metrics": [{"name": "quality"}],
            },
            version="v1",
            gmt_create=None,
            gmt_modified=None,
            creator=None,
            modifier=None,
        )
        for name, field in (("first", "signal_a"), ("second", "signal_b"))
    ]

    class _Tasks:
        def __init__(self, task):
            self.task = task

        def get_by_uuid(self, _uuid):
            return self.task

        def mark_succeeded(self, _uuid, *, stats, **_kwargs):
            self.task.stats = stats
            self.task.status = TaskStatus.SUCCEEDED
            return True

    class _Traces:
        def __init__(self):
            self.target_calls = []
            self.full_calls = []

        def read_trace_targets(self, _spec, fields, span_names):
            self.target_calls.append((set(fields), set(span_names)))
            rows = [
                {
                    "trace_id": "trace-1",
                    "name": "span-first",
                    "signal_a": "candidate",
                    "signal_b": "hidden",
                },
                {
                    "trace_id": "trace-1",
                    "name": "span-second",
                    "signal_a": "hidden",
                    "signal_b": "no",
                },
                {
                    "trace_id": "trace-2",
                    "name": "span-first",
                    "signal_a": "no",
                    "signal_b": "hidden",
                },
                {
                    "trace_id": "trace-2",
                    "name": "span-second",
                    "signal_a": "hidden",
                    "signal_b": "candidate",
                },
                {
                    "trace_id": "trace-3",
                    "name": "span-first",
                    "signal_a": "candidate",
                    "signal_b": "hidden",
                },
                {
                    "trace_id": "trace-3",
                    "name": "span-second",
                    "signal_a": "hidden",
                    "signal_b": "candidate",
                },
                {
                    "trace_id": "trace-4",
                    "name": "span-second",
                    "signal_a": "hidden",
                    "signal_b": "no",
                },
                {
                    "trace_id": "trace-5",
                    "name": "span-first",
                    "signal_a": "skip",
                    "signal_b": "hidden",
                },
                {
                    "trace_id": "trace-5",
                    "name": "span-second",
                    "signal_a": "hidden",
                    "signal_b": "skip",
                },
            ]
            return ["trace-1", "trace-2", "trace-3", "trace-4", "trace-5"], rows

        def read_trace_spans_batch_range(self, actual_spec, values, _start, _end):
            self.full_calls.append((actual_spec.where, list(values)))
            return [
                {
                    "trace_id": value,
                    "period": "2026-07-22 10:00:00",
                    "start_time_unix_nano": "2026-07-22T10:00:00+08:00",
                }
                for value in values
            ]

    task = SimpleNamespace(
        uuid="task-pre_evaluate",
        attempt=1,
        stats={},
        status=TaskStatus.RUNNING,
        scheduled_at=datetime(2026, 7, 22, 11, 0, 0),
    )
    tasks = _Tasks(task)
    traces = _Traces()
    written = []
    runner = Runner(
        task_table=tasks,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=traces,
        record_store=SimpleNamespace(write_record=lambda record, **_kwargs: written.append(record)),
        self_uuid="worker-1",
    )
    dataset = SimpleNamespace(
        spec={
            "username_column": "attributes.user",
            "username": "u1",
        },
        query_spec=lambda: {
            "group_batch_size": 10,
            "group_time_window_min": 10,
            "time_offset": "",
        },
    )
    spec = SimpleNamespace(
        table="trace",
        where="",
        time_range="1h",
        start_time="2026-07-22 10:00:00",
        end_time="2026-07-22 11:00:00",
        limit=100,
    )

    runner._run_signoz(
        task,
        SimpleNamespace(
            uuid="job-pre_evaluate",
            spec={"save_badcase_only": False},
        ),
        dataset,
        diagnosers,
        spec,
        "custom-runtime",
    )

    assert traces.target_calls == [({"signal_a", "signal_b"}, {"span-first", "span-second"})]
    assert traces.full_calls == [("`attributes.user` = 'u1'", ["trace-1", "trace-2", "trace-3"])]
    assert evaluated == {
        "first": ["trace-1", "trace-3"],
        "second": ["trace-2", "trace-3"],
    }
    assert len(written) == 4
    assert sorted(record.target_id for record in written) == [
        "trace-1",
        "trace-2",
        "trace-3",
        "trace-3",
    ]
    assert {(record.diagnoser_uuid, record.target_id) for record in written} == {
        ("dg-first", "trace-1"),
        ("dg-second", "trace-2"),
        ("dg-first", "trace-3"),
        ("dg-second", "trace-3"),
    }
    assert task.status is TaskStatus.SUCCEEDED
    assert task.stats["source"] == {
        "total": 5,
        "invalid": 0,
        "projection_failed": 1,
    }
    assert task.stats["diagnosers"]["dg-first"]["quality"]["total"] == 4
    assert task.stats["diagnosers"]["dg-second"]["quality"]["total"] == 4
    assert task.stats["diagnosers"]["dg-first"]["quality"]["evaluate"] == 2
    assert task.stats["diagnosers"]["dg-second"]["quality"]["evaluate"] == 2
    assert task.stats["diagnosers"]["dg-first"]["quality"]["skipped"] == 1
    assert task.stats["diagnosers"]["dg-second"]["quality"]["skipped"] == 1
    assert all("signal_b" not in keys for keys in pre_evaluate_row_keys["first"])
    assert all("signal_a" not in keys for keys in pre_evaluate_row_keys["second"])
    assert all(row["trace_id"] != "trace-4" for row in pre_evaluate_rows_seen["first"])


def test_prepare_executors_normalizes_and_validates_span_fields(monkeypatch):
    diagnoser = SimpleNamespace(
        uuid="dg-span-fields",
        name="span-fields",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        spec={"executor": "span-fields", "metrics": [{"name": "quality"}]},
    )
    monkeypatch.setattr(
        "agentlens.kernel.worker.runner.resolve_executor_name",
        lambda *_args: "span-fields",
    )

    class _Executor(Executor):
        def __init__(self, declarations):
            super().__init__({})
            self.declarations = declarations

        def pre_evaluate_span_fields(self):
            return self.declarations

        def evaluate(self, trace):
            return _test_record(trace.trace_id)

    def prepare(declarations):
        runner = Runner(
            task_table=None,
            job_table=None,
            dataset_table=None,
            diagnoser_table=None,
            trace_store=None,
            record_store=None,
            self_uuid="worker-1",
        )
        monkeypatch.setattr(
            ExecutorRegistry,
            "create",
            staticmethod(lambda *_args: _Executor(declarations)),
        )
        return runner._prepare_executors([diagnoser])

    prepared = prepare(
        (
            PreEvaluateSpanFields(name="terminal", fields=("a", "b")),
            PreEvaluateSpanFields(name="terminal", fields=("b", "c")),
        )
    )
    assert prepared[0]["pre_evaluate_span_fields"] == (
        PreEvaluateSpanFields(name="terminal", fields=("a", "b", "c")),
    )

    invalid = (
        [],
        ("terminal",),
        (PreEvaluateSpanFields(name="", fields=("a",)),),
        (PreEvaluateSpanFields(name="terminal", match="contains", fields=("a",)),),
        (PreEvaluateSpanFields(name="terminal", fields=["a"]),),
        (PreEvaluateSpanFields(name="terminal", fields=("",)),),
    )
    for declarations in invalid:
        with pytest.raises(ValueError):
            prepare(declarations)


def test_grouped_pre_evaluate_query_failure_propagates_without_full_evaluation(monkeypatch):
    pre_evaluated = []
    evaluated = []

    class _Executor(Executor):
        def pre_evaluate_span_fields(self):
            return (PreEvaluateSpanFields(name="terminal", fields=("signal",)),)

        def pre_evaluate(self, value):
            pre_evaluated.append(value.target_id)
            return PreEvaluateDecision.final(_test_record(value.target_id))

        def evaluate(self, trace):
            evaluated.append(trace.trace_id)
            return _test_record(trace.trace_id)

    monkeypatch.setattr(
        ExecutorRegistry,
        "create",
        staticmethod(lambda _mode, _name, spec: _Executor(spec)),
    )
    monkeypatch.setattr(
        "agentlens.kernel.worker.runner.resolve_executor_name",
        lambda _type, _mode, spec: spec["executor"],
    )

    diagnoser = Diagnoser(
        uuid="dg-fallback",
        space_uuid="s",
        agent_uuid="",
        name="fallback",
        description="",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        spec={"executor": "fallback", "metrics": [{"name": "quality"}]},
        version="v1",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )

    class _Tasks:
        def __init__(self, task):
            self.task = task

        def get_by_uuid(self, _uuid):
            return self.task

        def mark_succeeded(self, _uuid, *, stats, **_kwargs):
            self.task.stats = stats
            self.task.status = TaskStatus.SUCCEEDED
            return True

    class _Traces:
        def __init__(self):
            self.target_calls = 0
            self.full_calls = []

        def read_trace_targets(self, *_args):
            self.target_calls += 1
            raise RuntimeError("minimal field is unavailable")

        def read_trace_spans_batch_range(self, _spec, values, _start, _end):
            self.full_calls.append(list(values))
            return [
                {
                    "trace_id": value,
                    "period": "2026-07-22 10:00:00",
                    "start_time_unix_nano": "2026-07-22T10:00:00+08:00",
                }
                for value in values
            ]

    task = SimpleNamespace(
        uuid="task-pre_evaluate-fallback",
        attempt=1,
        stats={},
        status=TaskStatus.RUNNING,
        scheduled_at=datetime(2026, 7, 22, 11, 0, 0),
    )
    traces = _Traces()
    written = []
    runner = Runner(
        task_table=_Tasks(task),
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=traces,
        record_store=SimpleNamespace(write_record=lambda record, **_kwargs: written.append(record)),
        self_uuid="worker-1",
    )
    dataset = SimpleNamespace(
        spec={},
        query_spec=lambda: {
            "group_batch_size": 10,
            "group_time_window_min": 10,
            "time_offset": "",
        },
    )
    spec = SimpleNamespace(
        table="trace",
        where="",
        time_range="1h",
        start_time="2026-07-22 10:00:00",
        end_time="2026-07-22 11:00:00",
        limit=100,
    )

    with pytest.raises(RuntimeError, match="minimal field is unavailable"):
        runner._run_signoz(
            task,
            SimpleNamespace(uuid="job-pre_evaluate-fallback"),
            dataset,
            [diagnoser],
            spec,
            "custom-runtime",
        )

    assert traces.target_calls == 1
    assert traces.full_calls == []
    assert pre_evaluated == []
    assert evaluated == []
    assert written == []
    assert task.status is TaskStatus.RUNNING


def test_runner_injects_available_model_connection_only_at_runtime():
    from types import SimpleNamespace

    from agentlens.domain.enums import ModelConnectionStatus

    connection = SimpleNamespace(
        endpoint="http://judge/v1",
        model_name="judge-model",
        api_key="secret",
        status=ModelConnectionStatus.AVAILABLE,
    )

    class Connections:
        def get_in_space(self, uuid, space_uuid):
            assert uuid == "m-1"
            assert space_uuid == "s"
            return connection

    diagnoser = SimpleNamespace(
        uuid="dg-1",
        space_uuid="s",
        name="custom-judge",
        type=DiagnoserType.CUSTOM,
        mode=DiagnoserMode.LLM,
        spec={
            "model_connection_uuid": "m-1",
            "metrics": [{"name": "quality", "desc": "质量"}],
        },
    )
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=None,
        model_connection_table=Connections(),
        self_uuid="worker-1",
    )

    runtime_spec = runner._runtime_spec(diagnoser)

    assert runtime_spec["model"] == {
        "endpoint": "http://judge/v1",
        "model_id": "judge-model",
        "api_key": "secret",
    }
    assert runtime_spec["metrics"] == [{"name": "quality", "desc": "质量"}]
    assert "model" not in diagnoser.spec

    connection.status = ModelConnectionStatus.UNAVAILABLE
    assert "model" not in runner._runtime_spec(diagnoser)


def test_task_snapshot_survives_definition_update(monkeypatch):
    from agentlens.service.execution_snapshot import build_execution_snapshot

    now = datetime.now()
    common = dict(gmt_create=now, gmt_modified=now, creator=None, modifier=None)
    diag = Diagnoser(
        uuid="dg-frozen",
        space_uuid="s",
        agent_uuid="a",
        name="original",
        description="",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        version="v1",
        spec={"executor": "test_fixed", "weight": 2, "metrics": [{"name": "quality"}]},
        **common,
    )
    dataset = Dataset(
        uuid="ds-frozen",
        space_uuid="s",
        agent_uuid="a",
        name="original",
        description="",
        type=DatasetType.SIGNOZ,
        spec={"runtime_type": "custom-runtime", "query": {"table": "original", "limit": 2}},
        **common,
    )
    job = Job(
        uuid="j-frozen",
        space_uuid="s",
        agent_uuid="a",
        name="original",
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        dataset_uuid=dataset.uuid,
        diagnoser_uuids=[diag.uuid],
        spec={"save_badcase_only": False},
        **common,
    )
    snapshot = build_execution_snapshot(job, dataset, [diag])
    task = Task(
        uuid="t-frozen",
        job_uuid=job.uuid,
        status=TaskStatus.RUNNING,
        worker="w",
        scheduled_at=now,
        started_at=now,
        finished_at=None,
        attempt=1,
        msg="",
        execution_snapshot=snapshot,
        **common,
    )
    captured = []
    tasks = SimpleNamespace(
        cas_start_running=lambda *a, **k: True,
        get_by_uuid=lambda uuid: task,
        mark_failed=lambda *a, **k: pytest.fail(str(k)),
    )
    # Change every live resource after Task creation. Runner must not load them.
    job.diagnoser_uuids = []
    dataset.spec["query"]["table"] = "changed"
    diag.spec["weight"] = 99
    diag.name = "changed"
    diag.version = "v2"
    runner = Runner(
        task_table=tasks,
        job_table=SimpleNamespace(get_by_uuid=lambda _: job),
        dataset_table=SimpleNamespace(get_by_uuid=lambda _: dataset),
        diagnoser_table=SimpleNamespace(get_by_uuid=lambda _: diag),
        trace_store=None,
        record_store=None,
        self_uuid="w",
    )
    monkeypatch.setattr(
        runner,
        "_run_signoz",
        lambda task, job, ds, diags, spec, runtime: captured.append((job, ds, diags, spec)),
    )
    runner._run_sync(task.uuid)
    runner._run_sync(task.uuid)  # recovery uses the same frozen definitions
    assert len(captured) == 2
    for frozen_job, _frozen_dataset, frozen_diags, query in captured:
        assert frozen_job.diagnoser_uuids == [diag.uuid]
        assert frozen_job.spec["save_badcase_only"] is False
        assert query.table == "original"
        assert frozen_diags[0].name == "original"
        assert frozen_diags[0].spec["weight"] == 2
    new = build_execution_snapshot(job, dataset, [])
    assert new["dataset"]["spec"]["query"]["table"] == "changed"
    assert new["diagnosers"] == []
    job.diagnoser_uuids = [diag.uuid]
    latest = build_execution_snapshot(job, dataset, [diag])
    assert latest["diagnosers"][0]["version"] == "v2"
    assert latest["diagnosers"][0]["name"] == "changed"
    assert latest["diagnosers"][0]["spec"]["weight"] == 99


def test_snapshot_model_connection_rotates_only_credentials():
    import json

    from agentlens.domain.enums import ModelConnectionStatus
    from agentlens.service.execution_snapshot import (
        build_execution_snapshot,
        restore_execution_snapshot,
    )

    now = datetime.now()
    common = dict(gmt_create=now, gmt_modified=now, creator=None, modifier=None)
    diag = Diagnoser(
        uuid="dg-llm",
        space_uuid="s",
        agent_uuid="a",
        name="llm",
        description="",
        type=DiagnoserType.CUSTOM,
        mode=DiagnoserMode.LLM,
        version="v1",
        spec={
            "prompt": "judge",
            "model_connection_uuid": "mc",
            "max_tokens": 55,
            "temperature": 0.3,
            "metrics": [{"name": "quality", "desc": "Quality"}],
            "model": {"api_key": "inline-secret", "headers": {"Authorization": "secret"}},
            "api_key": "top-secret",
            "datasource": {"dsn": "mysql://user:password@host/db"},
        },
        **common,
    )
    dataset = Dataset(
        uuid="ds",
        space_uuid="s",
        agent_uuid="a",
        name="ds",
        description="",
        type=DatasetType.SIGNOZ,
        spec={
            "runtime_type": "custom-runtime",
            "query": {"table": "trace", "headers": {"Authorization": "source-secret"}},
            "api_key": "source-secret",
        },
        **common,
    )
    job = Job(
        uuid="j",
        space_uuid="s",
        agent_uuid="a",
        name="j",
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        dataset_uuid="ds",
        diagnoser_uuids=["dg-llm"],
        **common,
    )
    connection = SimpleNamespace(
        uuid="mc",
        endpoint="https://original.example/v1",
        model_name="old-model",
        api_key="old-key",
        status=ModelConnectionStatus.AVAILABLE,
    )
    snapshot = build_execution_snapshot(job, dataset, [diag], model_connections={"mc": connection})
    serialized = json.dumps(snapshot)
    assert all(
        secret not in serialized
        for secret in [
            "inline-secret",
            "top-secret",
            "source-secret",
            "old-key",
            "password",
            "Authorization",
        ]
    )
    connection.api_key = "rotated-key"
    _, _, frozen = restore_execution_snapshot(snapshot)
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=None,
        self_uuid="w",
        model_connection_table=SimpleNamespace(get_in_space=lambda *a: connection),
    )
    runner._execution_snapshot = snapshot
    spec = runner._runtime_spec(frozen[0])
    assert spec["model"] == {
        "endpoint": "https://original.example/v1",
        "model_id": "old-model",
        "api_key": "rotated-key",
    }
    assert spec["temperature"] == 0.3
    assert spec["max_tokens"] == 55
    snapshot["executor_revision"] = "0.9.0"
    with pytest.raises(Exception, match="unsupported executor revision"):
        restore_execution_snapshot(snapshot)


def test_snapshot_rejects_model_endpoint_credentials():
    from agentlens.service.execution_snapshot import _endpoint

    for endpoint in [
        "https://user:secret@example.test/v1",
        "https://example.test/v1?api_key=secret",
        "mysql://u:p@host/db",
    ]:
        with pytest.raises(Exception, match="without credentials"):
            _endpoint(endpoint)


@pytest.mark.asyncio
async def test_runner_persisted_snapshot_keeps_old_definition_and_record_labels(
    session, signoz_inmem
):
    from sqlalchemy import update

    from agentlens.infra.ob.models import TaskModel
    from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
    from agentlens.service.execution_snapshot import snapshot_from_tables

    task = _seed_world(session, signoz_inmem)
    jobs = JobTable(session)
    datasets = DatasetTable(session)
    diagnosers = DiagnoserTable(session)
    job = jobs.get_by_uuid(task.job_uuid)
    original_uuid = job.diagnoser_uuids[0]
    snapshot = snapshot_from_tables(job, datasets, diagnosers, ModelConnectionTable(session))
    session.execute(
        update(TaskModel).where(TaskModel.uuid == task.uuid).values(execution_snapshot=snapshot)
    )
    session.commit()
    jobs.update_by_uuid(job.uuid, {"diagnoser_uuids": [], "spec": {"save_badcase_only": True}})
    datasets.update_by_uuid(
        job.dataset_uuid, {"spec": {"runtime_type": "changed", "query": {"table": "changed"}}}
    )
    diagnosers.update_by_uuid(
        original_uuid,
        {"name": "changed", "version": "v2", "spec": {"metrics": [{"name": "changed"}]}},
    )
    writer = InMemoryAntcollectorClient(signoz_inmem._tables)
    records = DiagnosisRecordStore(signoz_inmem, writer, table="records")
    runner = Runner(
        task_table=TaskTable(session),
        job_table=jobs,
        dataset_table=datasets,
        diagnoser_table=diagnosers,
        trace_store=TraceStore(signoz_inmem),
        record_store=records,
        self_uuid=task.worker,
    )
    await runner.run(task.uuid)
    result = TaskTable(session).get_by_uuid(task.uuid)
    assert result.status == TaskStatus.SUCCEEDED
    assert result.stats["schema_version"] == 3
    definition = result.stats["diagnoser_definitions"][original_uuid]
    assert definition["name"] == "test-fixed"
    assert definition["version"] == "v1"
    assert result.stats["diagnosers"][original_uuid]["quality"]["good"] == 2
    assert len(signoz_inmem._tables["records"]) == 2
    import json

    body = json.loads(signoz_inmem._tables["records"][0]["attributes.record.body"])
    assert body["trace_snapshot"]["diagnoser_definition"] == definition


def test_runner_fails_unsupported_revision_before_loading_live_definition():
    task = SimpleNamespace(
        uuid="t-old-revision",
        job_uuid="j",
        attempt=1,
        execution_snapshot={"schema_version": 1, "executor_revision": "0.9.0", "diagnosers": []},
    )
    failed = []
    tasks = SimpleNamespace(
        cas_start_running=lambda *a, **k: True,
        get_by_uuid=lambda _: task,
        mark_failed=lambda *a, **k: failed.append(k) or True,
    )
    runner = Runner(
        task_table=tasks,
        job_table=SimpleNamespace(
            get_by_uuid=lambda _: pytest.fail("must not load current definitions")
        ),
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=None,
        self_uuid="w",
    )
    runner._run_sync(task.uuid)
    assert len(failed) == 1
    assert "unsupported executor revision" in failed[0]["msg_append"]
    assert "schema_version" not in failed[0]["stats"]


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        ({"schema_version": 2}, "unsupported Task execution snapshot version"),
        (
            {"schema_version": 1, "executor_revision": "2.0.0", "diagnosers": {"new": "shape"}},
            "unsupported executor revision",
        ),
        (
            {
                "schema_version": 1,
                "executor_revision": "1.4.0",
                "score_policy_version": "future-v2",
            },
            "unsupported score policy version",
        ),
    ],
)
def test_runner_rejected_snapshot_marks_failed_without_parsing_metadata(snapshot, reason):
    def forbidden(*args, **kwargs):
        pytest.fail("rejected snapshot must not read live definitions, Trace, or Judge")

    task = SimpleNamespace(uuid="t-future", job_uuid="j", attempt=1, execution_snapshot=snapshot)
    failed = []
    runner = Runner(
        task_table=SimpleNamespace(
            cas_start_running=lambda *a, **k: True,
            get_by_uuid=lambda _: task,
            mark_failed=lambda *a, **k: failed.append(k) or True,
        ),
        job_table=SimpleNamespace(get_by_uuid=forbidden),
        dataset_table=SimpleNamespace(get_by_uuid=forbidden),
        diagnoser_table=SimpleNamespace(get_by_uuid=forbidden),
        trace_store=SimpleNamespace(query=forbidden),
        record_store=SimpleNamespace(write=forbidden),
        self_uuid="worker",
    )
    runner._run_sync(task.uuid)
    assert len(failed) == 1
    assert reason in failed[0]["msg_append"]
    assert failed[0]["stats"]["diagnosers"] == {}
    assert "schema_version" not in failed[0]["stats"]
    assert "score_policy_version" not in failed[0]["stats"]
    assert runner._execution_snapshot is None


def test_snapshot_connection_uses_current_scoped_definition_read(monkeypatch):
    from agentlens.service import execution_snapshot

    current = SimpleNamespace(model_name="current-model")
    stale = SimpleNamespace(model_name="stale-model")
    definition = SimpleNamespace(
        type=DiagnoserType.CUSTOM, mode=DiagnoserMode.LLM, spec={"model_connection_uuid": "mc"}
    )
    job = SimpleNamespace(space_uuid="s", dataset_uuid="ds", diagnoser_uuids=["dg"])
    connections = SimpleNamespace(
        get_in_space=lambda *args: stale,
        get_in_space_for_update=lambda uuid, space: (
            current if (uuid, space) == ("mc", "s") else None
        ),
    )
    monkeypatch.setattr(
        execution_snapshot,
        "build_execution_snapshot",
        lambda *args, model_connections, sandbox_profiles: {
            "model": model_connections["mc"].model_name
        },
    )
    snapshot = execution_snapshot.snapshot_from_tables(
        job,
        SimpleNamespace(get_by_uuid_for_update=lambda _: object()),
        SimpleNamespace(get_by_uuid_for_update=lambda _: definition),
        connections,
    )
    assert snapshot["model"] == "current-model"


def test_snapshot_metadata_uses_effective_category_and_weight():
    from agentlens.domain.task_stats import canonical_task_stats, new_task_stats
    from agentlens.service.execution_snapshot import build_execution_snapshot, snapshot_definitions

    common = dict(gmt_create=None, gmt_modified=None, creator=None, modifier=None)
    dataset = Dataset(
        uuid="ds",
        space_uuid="s",
        agent_uuid="a",
        name="ds",
        description="",
        type=DatasetType.SIGNOZ,
        spec={"runtime_type": "custom-runtime", "query": {"table": "trace"}},
        **common,
    )
    diag = Diagnoser(
        uuid="dg",
        space_uuid="s",
        agent_uuid="a",
        name="d",
        description="",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        version="v1",
        spec={
            "executor": "test_fixed",
            "metrics": [{"name": "quality"}],
            "category": None,
            "weight": "invalid",
        },
        **common,
    )
    job = Job(
        uuid="j",
        space_uuid="s",
        agent_uuid="a",
        name="j",
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        dataset_uuid="ds",
        diagnoser_uuids=["dg"],
        **common,
    )
    snapshot = build_execution_snapshot(job, dataset, [diag])
    defs = snapshot_definitions(snapshot)
    assert defs["dg"]["category"] == "outcome"
    assert defs["dg"]["weight"] == 0
    stats = new_task_stats(definitions=defs)
    assert canonical_task_stats(stats) == stats


@pytest.mark.parametrize(
    "reason",
    [
        "not_applicable: no tools",
        "missing_required_evidence: output",
        "privacy_redacted: input",
        "budget_exhausted: request budget",
    ],
)
@pytest.mark.parametrize("all_skipped", [False, True])
@pytest.mark.parametrize("frozen", [False, True])
def test_full_trace_applicability_skip_is_local_and_has_no_record(reason, all_skipped, frozen):
    class GatedExecutor(Executor):
        def applicability_skip_reason(self, trace):
            assert trace.projection is not None
            return reason if self.spec.get("skip") else None

        def evaluate(self, trace):
            assert not self.spec.get("skip"), "skip must never evaluate"
            return _test_record(trace.trace_id)

    common = dict(
        space_uuid="s",
        agent_uuid="",
        description="",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        version="v1",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    diagnosers = [
        Diagnoser(
            uuid=f"dg-{name}",
            name=name,
            spec={"skip": skip, "metrics": [{"name": "quality"}]},
            **common,
        )
        for name, skip in [("skip", True), ("run", all_skipped)]
    ]
    written = []
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda record, **kw: written.append(record)),
        self_uuid="w",
    )
    snapshot = {
        "schema_version": 1,
        "executor_revision": "1.1.0",
        "score_policy_version": "lightweight-v1",
        "diagnosers": [
            {
                "uuid": d.uuid,
                "key": d.name,
                "name": d.name,
                "version": d.version,
                "revision": "1.1.0",
                "digest": "a" * 64,
                "spec": {**d.spec, "category": "outcome", "weight": 1, "score_role": "quality"},
            }
            for d in diagnosers
        ],
    }
    acc = _RunAcc(execution_snapshot=snapshot if frozen else None)
    count = runner._evaluate_all(
        SimpleNamespace(uuid="t", attempt=1),
        SimpleNamespace(uuid="j", spec={"save_badcase_only": False}),
        diagnosers,
        [
            Trace(
                trace_id="target",
                runtime_type="codex",
                spans=[],
                projection=_projection("2026-10-09T10:00:00"),
            )
        ],
        acc=acc,
        prepared=[{"diagnoser": d, "executor": GatedExecutor(d.spec)} for d in diagnosers],
    )
    assert count == (0 if all_skipped else 1)
    assert [record.diagnoser_uuid for record in written] == ([] if all_skipped else ["dg-run"])
    assert acc.stats["diagnosers"]["dg-skip"]["quality"]["total"] == 0
    assert acc.stats["diagnosers"]["dg-skip"]["quality"]["skipped"] == 1
    assert acc.stats["diagnosers"]["dg-run"]["quality"]["good"] == (0 if all_skipped else 1)
    if frozen:
        assert acc.stats["skip_reasons"]["dg-skip"] == {reason.partition(":")[0]: 1}
    for counters in acc.stats["diagnosers"].values():
        assert counters["quality"]["total"] + counters["quality"].get("skipped", 0) == 1


@pytest.mark.parametrize(
    "failure", [RuntimeError("bad projection/input"), ValueError("invalid gate")]
)
def test_full_trace_gate_fault_is_failed_and_never_skipped(failure):
    class BrokenGate(Executor):
        def applicability_skip_reason(self, trace):
            raise failure

        def evaluate(self, trace):
            pytest.fail("a failed gate cannot evaluate")

    diagnoser = SimpleNamespace(
        uuid="dg",
        name="broken",
        version="v1",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        spec={"metrics": [{"name": "quality"}]},
    )
    written = []
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda record, **kw: written.append(record)),
        self_uuid="w",
    )
    acc = _RunAcc()
    count = runner._evaluate_all(
        SimpleNamespace(uuid="t", attempt=1),
        SimpleNamespace(uuid="j", spec={"save_badcase_only": False}),
        [diagnoser],
        [Trace("target", "codex", [], projection=_projection("2026-10-09T10:00:00"))],
        acc=acc,
        prepared=[{"diagnoser": diagnoser, "executor": BrokenGate(diagnoser.spec)}],
    )
    assert count == 1
    assert len(written) == 1
    assert written[0].execution.status == "failed"
    assert written[0].verdict is Verdict.FAILED
    counters = acc.stats["diagnosers"]["dg"]["quality"]
    assert counters["total"] == counters["failed"] == 1
    assert counters.get("skipped", 0) == 0
