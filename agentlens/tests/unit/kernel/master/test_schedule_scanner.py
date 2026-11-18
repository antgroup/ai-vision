from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from agentlens.domain.dataset import Dataset
from agentlens.domain.diagnoser import Diagnoser
from agentlens.domain.enums import (
    DatasetType,
    DiagnoserMode,
    DiagnoserType,
    JobStatus,
    JobTriggerType,
)
from agentlens.domain.job import Job
from agentlens.infra.ob.engine import make_session_factory
from agentlens.infra.ob.tables.dataset_table import DatasetTable
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.kernel.master.schedule_scanner import ScheduleScanner
from agentlens.utils.uuid_gen import new_job_uuid


@pytest.mark.parametrize("change", ["disabled", "deleted", "manual", "cron"])
def test_scanned_job_changed_before_lock_does_not_create_task(monkeypatch, change):
    from agentlens.kernel.master import schedule_scanner

    baseline = datetime(2026, 6, 4, 10, 0)
    candidate = SimpleNamespace(
        uuid="j", schedule_cron="* * * * *", gmt_modified=baseline, gmt_create=baseline
    )
    current = SimpleNamespace(
        **vars(candidate), status=JobStatus.ENABLED, trigger_type=JobTriggerType.SCHEDULE
    )
    if change == "disabled":
        current.status = JobStatus.DISABLED
    elif change == "manual":
        current.trigger_type = JobTriggerType.MANUAL
    elif change == "cron":
        current.schedule_cron = "0 * * * *"
    else:
        current = None
    session = SimpleNamespace(rollback=lambda: None)
    jobs = SimpleNamespace(
        s=session,
        list_enabled_schedule_jobs=lambda: [candidate],
        get_by_uuid_for_update=lambda _: current,
    )
    tasks = SimpleNamespace(s=session, latest_scheduled_by_jobs=lambda *a, **k: {})
    monkeypatch.setattr(
        schedule_scanner,
        "snapshot_from_tables",
        lambda *a: pytest.fail("ineligible candidate cannot freeze or create a Task"),
    )
    scanner = ScheduleScanner(jobs, tasks, now_fn=lambda: datetime(2026, 6, 4, 10, 1))
    scanner._tick_sync()


@pytest.mark.parametrize("change", ["disabled", "deleted", "manual", "cron"])
def test_schedule_candidate_changed_by_another_transaction_is_not_inserted(
    session, engine, monkeypatch, change
):
    job = _seed_job(session, baseline=datetime(2026, 6, 4, 10, 0))
    original = JobTable.list_enabled_schedule_jobs

    def scan_then_change(table):
        candidates = original(table)
        with make_session_factory(engine)() as writer:
            jobs = JobTable(writer)
            if change == "deleted":
                jobs.delete_by_uuid(job.uuid)
            else:
                fields = {
                    "disabled": {"status": JobStatus.DISABLED.value},
                    "manual": {"trigger_type": JobTriggerType.MANUAL.value},
                    "cron": {"schedule_cron": "0 * * * *"},
                }[change]
                jobs.update_by_uuid(job.uuid, fields)
        return candidates

    monkeypatch.setattr(JobTable, "list_enabled_schedule_jobs", scan_then_change)
    scanner = ScheduleScanner(
        JobTable(session), TaskTable(session), now_fn=lambda: datetime(2026, 6, 4, 10, 1)
    )
    scanner._tick_sync()
    assert TaskTable(session).list_pending(limit=100) == []


def _seed_job(
    session,
    cron: str = "* * * * *",
    status=JobStatus.ENABLED,
    baseline: datetime | None = None,
) -> Job:
    if DatasetTable(session).get_by_uuid("ds-aaaaaaaa") is None:
        common = dict(gmt_create=None, gmt_modified=None, creator=None, modifier=None)
        DatasetTable(session).insert(
            Dataset(
                uuid="ds-aaaaaaaa",
                space_uuid="s",
                agent_uuid="a",
                name="schedule-ds",
                description="",
                type=DatasetType.SIGNOZ,
                spec={"runtime_type": "custom-runtime", "query": {"table": "trace"}},
                **common,
            )
        )
        DiagnoserTable(session).insert(
            Diagnoser(
                uuid="dg-bbbbbbbb",
                space_uuid="s",
                agent_uuid="a",
                name="schedule-dg",
                description="",
                type=DiagnoserType.BUILTIN,
                mode=DiagnoserMode.RULE,
                spec={"executor": "test_fixed", "metrics": [{"name": "quality"}]},
                version="v1",
                **common,
            )
        )
    j = Job(
        uuid=new_job_uuid(),
        space_uuid="s",
        agent_uuid="a",
        name=f"j-{cron}",
        description="",
        trigger_type=JobTriggerType.SCHEDULE,
        schedule_cron=cron,
        status=status,
        dataset_uuid="ds-aaaaaaaa",
        diagnoser_uuids=["dg-bbbbbbbb"],
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    JobTable(session).insert(j)
    if baseline is not None:
        session.execute(
            text("UPDATE agentlens_job SET gmt_create=:ts, gmt_modified=:ts WHERE uuid=:uuid"),
            {"ts": baseline, "uuid": j.uuid},
        )
        session.commit()
    return j


@pytest.mark.asyncio
async def test_every_minute_cron_emits_after_tick(session):
    job = _seed_job(session, cron="* * * * *", baseline=datetime(2026, 6, 4, 10, 0, 30))
    scanner = ScheduleScanner(
        job_table=JobTable(session),
        task_table=TaskTable(session),
        now_fn=lambda: datetime(2026, 6, 4, 10, 0, 30),  # 半分钟
    )
    await scanner.tick()
    pending1 = TaskTable(session).list_pending(limit=100)
    assert pending1 == []

    # 第二次 tick：时间推进 60s+，期望发一条
    scanner.now_fn = lambda: datetime(2026, 6, 4, 10, 1, 30)
    await scanner.tick()
    pending2 = TaskTable(session).list_pending(limit=100)
    assert len(pending2) == 1
    assert pending2[0].job_uuid == job.uuid
    assert pending2[0].execution_snapshot["dataset"]["spec"]["query"]["table"] == "trace"
    assert pending2[0].execution_snapshot["diagnosers"][0]["version"] == "v1"


@pytest.mark.asyncio
async def test_disabled_job_does_not_emit(session):
    _seed_job(
        session,
        cron="* * * * *",
        status=JobStatus.DISABLED,
        baseline=datetime(2026, 6, 4, 10, 0, 0),
    )
    scanner = ScheduleScanner(
        job_table=JobTable(session),
        task_table=TaskTable(session),
        now_fn=lambda: datetime(2026, 6, 4, 10, 0, 0),
    )
    await scanner.tick()
    scanner.now_fn = lambda: datetime(2026, 6, 4, 10, 5, 0)
    await scanner.tick()
    assert TaskTable(session).list_pending(limit=100) == []


@pytest.mark.asyncio
async def test_multiple_minutes_emits_one_per_due(session):
    _seed_job(session, cron="* * * * *", baseline=datetime(2026, 6, 4, 10, 0, 0))
    scanner = ScheduleScanner(
        job_table=JobTable(session),
        task_table=TaskTable(session),
        now_fn=lambda: datetime(2026, 6, 4, 10, 0, 0),
    )
    await scanner.tick()
    scanner.now_fn = lambda: datetime(2026, 6, 4, 10, 3, 30)  # 跳到 +3.5min
    await scanner.tick()
    assert len(TaskTable(session).list_pending(limit=100)) == 3


@pytest.mark.asyncio
async def test_new_schedule_job_is_visible_after_scanner_started(session, engine):
    scanner = ScheduleScanner(
        job_table=JobTable(session),
        task_table=TaskTable(session),
        now_fn=lambda: datetime(2026, 6, 4, 10, 0, 0),
    )
    await scanner.tick()  # 建立首轮读事务时尚无 schedule job

    writer = make_session_factory(engine)()
    try:
        job = _seed_job(writer, cron="* * * * *", baseline=datetime(2026, 6, 4, 10, 1, 0))
    finally:
        writer.close()

    scanner.now_fn = lambda: datetime(2026, 6, 4, 10, 1, 0)
    await scanner.tick()
    scanner.now_fn = lambda: datetime(2026, 6, 4, 10, 2, 0)
    await scanner.tick()

    pending = TaskTable(session).list_pending(limit=100)
    assert len(pending) == 1
    assert pending[0].job_uuid == job.uuid
    assert pending[0].scheduled_at == datetime(2026, 6, 4, 10, 2, 0)


@pytest.mark.asyncio
async def test_reenabled_job_does_not_backfill_disabled_window(session):
    job = _seed_job(session, cron="0 * * * *", baseline=datetime(2026, 7, 3, 1, 0, 0))
    scanner = ScheduleScanner(
        job_table=JobTable(session),
        task_table=TaskTable(session),
        now_fn=lambda: datetime(2026, 7, 3, 1, 0, 0),
    )
    await scanner.tick()

    JobTable(session).update_by_uuid(job.uuid, {"status": JobStatus.DISABLED.value})
    scanner.now_fn = lambda: datetime(2026, 7, 3, 10, 0, 0)
    await scanner.tick()

    JobTable(session).update_by_uuid(job.uuid, {"status": JobStatus.ENABLED.value})
    session.execute(
        text("UPDATE agentlens_job SET gmt_modified=:ts WHERE uuid=:uuid"),
        {"ts": datetime(2026, 7, 3, 10, 17, 50), "uuid": job.uuid},
    )
    session.commit()

    scanner.now_fn = lambda: datetime(2026, 7, 3, 10, 18, 0)
    await scanner.tick()
    assert TaskTable(session).list_pending(limit=100) == []


@pytest.mark.asyncio
async def test_schedule_emit_is_idempotent_for_same_job_and_scheduled_at(session):
    job = _seed_job(session, cron="* * * * *", baseline=datetime(2026, 6, 4, 10, 0, 0))
    scanner = ScheduleScanner(
        job_table=JobTable(session),
        task_table=TaskTable(session),
        now_fn=lambda: datetime(2026, 6, 4, 10, 0, 0),
    )
    await scanner.tick()
    scanner.now_fn = lambda: datetime(2026, 6, 4, 10, 1, 0)
    await scanner.tick()
    assert len(TaskTable(session).list_pending(limit=100)) == 1

    assert scanner._emit_task(job.uuid, datetime(2026, 6, 4, 10, 1, 0)) is False
    tasks = TaskTable(session).list_pending(limit=100)
    assert len(tasks) == 1
    assert tasks[0].scheduled_at == datetime(2026, 6, 4, 10, 1, 0)


@pytest.mark.asyncio
async def test_master_restart_resumes_from_persisted_cursor(session):
    job = _seed_job(session, cron="* * * * *", baseline=datetime(2026, 6, 4, 10, 0, 0))
    scanner = ScheduleScanner(
        job_table=JobTable(session),
        task_table=TaskTable(session),
        now_fn=lambda: datetime(2026, 6, 4, 10, 1, 0),
    )
    await scanner.tick()

    restarted = ScheduleScanner(
        job_table=JobTable(session),
        task_table=TaskTable(session),
        now_fn=lambda: datetime(2026, 6, 4, 10, 3, 0),
    )
    await restarted.tick()

    tasks = TaskTable(session).list_pending(limit=100)
    assert [task.scheduled_at for task in tasks] == [
        datetime(2026, 6, 4, 10, 1, 0),
        datetime(2026, 6, 4, 10, 2, 0),
        datetime(2026, 6, 4, 10, 3, 0),
    ]
    assert TaskTable(session).latest_scheduled_by_jobs(
        [job.uuid], before=datetime(2026, 6, 4, 10, 3, 0)
    ) == {job.uuid: datetime(2026, 6, 4, 10, 3, 0)}
