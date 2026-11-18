import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import agentlens.diagnoser  # noqa: F401 — auto-register executors


def test_execute_requires_task_uuid(client):
    r = client.post("/internal/execute", json={"fenced_token": 1})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_run_task_closes_its_session(monkeypatch):
    from agentlens.web.routes import internal

    session = MagicMock()
    runner = SimpleNamespace(run=AsyncMock())
    monkeypatch.setattr(internal, "_engine_factory", lambda: lambda: session)
    monkeypatch.setattr(internal, "_make_runner", lambda *_args: runner)

    await internal._run_task("t-1", MagicMock(), MagicMock(), MagicMock())

    runner.run.assert_awaited_once_with("t-1")
    session.close.assert_called_once_with()


def test_execute_accepts_valid_payload(client, session, signoz_inmem):
    from datetime import datetime

    from agentlens.domain.dataset import Dataset
    from agentlens.domain.diagnoser import Diagnoser
    from agentlens.domain.enums import (
        DatasetType,
        DiagnoserMode,
        DiagnoserType,
        JobStatus,
        JobTriggerType,
        TaskStatus,
    )
    from agentlens.domain.job import Job
    from agentlens.domain.task import Task
    from agentlens.infra.ob.tables.dataset_table import DatasetTable
    from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
    from agentlens.infra.ob.tables.job_table import JobTable
    from agentlens.infra.ob.tables.task_table import TaskTable
    from agentlens.utils.uuid_gen import (
        new_dataset_uuid,
        new_diagnoser_uuid,
        new_job_uuid,
        new_task_uuid,
    )

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
        name="ds",
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
        name="j",
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
    )
    JobTable(session).insert(job)
    task = Task(
        uuid=new_task_uuid(),
        job_uuid=job.uuid,
        status=TaskStatus.DISPATCHED,
        worker="test-host/127.0.0.1",
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

    r = client.post("/internal/execute", json={"task_uuid": task.uuid, "fenced_token": 1})
    assert r.status_code == 202

    time.sleep(0.5)
    f = TaskTable(session).get_by_uuid(task.uuid)
    assert f.status == TaskStatus.SUCCEEDED


def test_notify_returns_200(client):
    r = client.post("/internal/notify", json={"task_uuid": "t-x"})
    assert r.status_code == 200
