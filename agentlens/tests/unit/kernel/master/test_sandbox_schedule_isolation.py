"""Offline scheduling behavior across a sandbox policy boundary."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import Integer, MetaData, create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from structlog.testing import capture_logs

from agentlens.infra.ob.models import (
    DatasetModel,
    DiagnoserModel,
    JobModel,
    SandboxBindingModel,
    TaskModel,
)
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.kernel.master.schedule_scanner import ScheduleScanner


@pytest.fixture
def schedule_session():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    for model in (DatasetModel, DiagnoserModel, JobModel, SandboxBindingModel, TaskModel):
        table = model.__table__.to_metadata(metadata)
        table.c.id.type = Integer()
    metadata.create_all(engine)
    with sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


def _seed_custom_schedule(session, label, baseline):
    scope = {"space_uuid": "space-" + label, "agent_uuid": "agent"}
    audit = {"gmt_create": baseline, "gmt_modified": baseline}
    session.add(
        DatasetModel(
            uuid="dataset-" + label,
            name="dataset",
            type="signoz",
            spec={"runtime_type": "other", "query": {}},
            **scope,
            **audit,
        )
    )
    session.add(
        DiagnoserModel(
            uuid="diagnoser-" + label,
            name="rule",
            type="custom",
            mode="rule",
            version="1",
            spec={
                "metrics": [{"name": "quality"}],
                "code": 'def evaluate(trace):\n    return {"metrics": []}',
            },
            **scope,
            **audit,
        )
    )
    session.add(
        JobModel(
            uuid="job-" + label,
            name=label,
            trigger_type="schedule",
            schedule_cron="* * * * *",
            status="enabled",
            dataset_uuid="dataset-" + label,
            diagnoser_uuids=["diagnoser-" + label],
            spec={},
            **scope,
            **audit,
        )
    )
    session.commit()


def test_required_sandbox_without_default_does_not_starve_other_spaces(schedule_session):
    session = schedule_session
    baseline = datetime(2026, 11, 2, 12, 0)
    _seed_custom_schedule(session, "requires-sandbox", baseline)
    _seed_custom_schedule(session, "healthy", baseline)
    session.add(SandboxBindingModel(space_uuid="space-requires-sandbox", require_sandbox=1))
    session.commit()
    scanner = ScheduleScanner(
        JobTable(session), TaskTable(session), now_fn=lambda: baseline + timedelta(minutes=2)
    )

    with capture_logs() as logs:
        scanner._tick_sync()
        scanner._tick_sync()

    tasks = session.scalars(select(TaskModel).order_by(TaskModel.scheduled_at)).all()
    assert [(task.job_uuid, task.scheduled_at) for task in tasks] == [
        ("job-healthy", datetime(2026, 11, 2, 12, 1)),
        ("job-healthy", datetime(2026, 11, 2, 12, 2)),
    ]
    assert all(
        task.execution_snapshot["diagnosers"][0]["spec"]["sandbox"] == {"mode": "local"}
        for task in tasks
    )
    rejected = [entry for entry in logs if entry.get("event") == "schedule.job_unavailable"]
    assert len(rejected) == 2
    assert all(
        entry["job_uuid"] == "job-requires-sandbox"
        and entry["error_type"] == "Validation"
        and "err" not in entry
        for entry in rejected
    )


def test_unexpected_database_failure_still_aborts_scan(schedule_session, monkeypatch):
    baseline = datetime(2026, 11, 2, 12, 0)
    _seed_custom_schedule(schedule_session, "healthy", baseline)
    scanner = ScheduleScanner(
        JobTable(schedule_session),
        TaskTable(schedule_session),
        now_fn=lambda: baseline + timedelta(minutes=1),
    )
    failure = OperationalError("SELECT ...", {}, RuntimeError("database unavailable"))

    def unavailable(*args, **kwargs):
        raise failure

    monkeypatch.setattr(scanner, "_emit_task", unavailable)
    with pytest.raises(OperationalError) as raised:
        scanner._tick_sync()
    assert raised.value is failure
