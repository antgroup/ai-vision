from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from agentlens.domain.enums import TaskStatus
from agentlens.domain.task import Task
from agentlens.infra.ob.tables.heartbeat_table import HeartbeatTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.kernel.master.rescue_loop import RescueLoop
from agentlens.utils.uuid_gen import new_task_uuid


def _insert_task(session, status: TaskStatus, worker: str | None = None, attempt: int = 0) -> str:
    t = TaskTable(session)
    u = new_task_uuid()
    t.insert(
        Task(
            uuid=u,
            job_uuid="j-aaaaaaaa",
            status=status,
            worker=worker,
            scheduled_at=datetime.now(UTC) - timedelta(minutes=2),
            started_at=None,
            finished_at=None,
            attempt=attempt,
            msg="",
            gmt_create=None,
            gmt_modified=None,
            creator=None,
            modifier=None,
            execution_snapshot={
                "schema_version": 1,
                "executor_revision": "1.0.0",
                "diagnosers": [],
            },
        )
    )
    return u


def _age_task(session, uuid: str, minutes: int) -> None:
    session.execute(
        text("UPDATE agentlens_task SET gmt_modified=:modified WHERE uuid=:u"),
        # RescueLoop compares against the application's local clock. The owned
        # database container may use a different timezone from the test process.
        {"modified": datetime.now() - timedelta(minutes=minutes), "u": uuid},
    )
    session.commit()


@pytest.mark.asyncio
async def test_rescue_dispatched_timeout_back_to_pending(session):
    HeartbeatTable(session).upsert("w1", "1.1.1.1")
    uid = _insert_task(session, TaskStatus.DISPATCHED, worker="w1/1.1.1.1")
    session.execute(
        text("UPDATE agentlens_task SET stats=JSON_OBJECT('partial', 1) WHERE uuid=:u"),
        {"u": uid},
    )
    session.commit()
    _age_task(session, uid, minutes=2)
    loop = RescueLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        dispatch_timeout_sec=60,
        alive_threshold_sec=30,
    )
    scheduled_at = TaskTable(session).get_by_uuid(uid).scheduled_at
    await loop.tick()
    t = TaskTable(session).get_by_uuid(uid)
    assert t.status == TaskStatus.PENDING
    assert t.worker is None
    assert t.attempt == 0
    assert t.stats is None
    assert t.scheduled_at == scheduled_at
    assert t.execution_snapshot == {
        "schema_version": 1,
        "executor_revision": "1.0.0",
        "diagnosers": [],
    }

    session.execute(
        text("UPDATE agentlens_task SET status='dispatched', worker='w1/1.1.1.1' WHERE uuid=:u"),
        {"u": uid},
    )
    session.commit()
    _age_task(session, uid, minutes=2)

    await loop.tick()

    retried = TaskTable(session).get_by_uuid(uid)
    assert retried.status == TaskStatus.PENDING
    assert retried.attempt == 0


@pytest.mark.asyncio
async def test_rescue_running_with_dead_worker(session):
    # 不 upsert worker → list_alive 不含它 → 视为死
    uid = _insert_task(session, TaskStatus.RUNNING, worker="dead/9.9.9.9", attempt=1)
    session.execute(
        text("UPDATE agentlens_task SET stats=JSON_OBJECT('partial', 1) WHERE uuid=:u"),
        {"u": uid},
    )
    session.commit()
    loop = RescueLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        dispatch_timeout_sec=60,
        alive_threshold_sec=30,
    )
    scheduled_at = TaskTable(session).get_by_uuid(uid).scheduled_at
    await loop.tick()
    t = TaskTable(session).get_by_uuid(uid)
    assert t.status == TaskStatus.PENDING
    assert t.worker is None
    assert t.attempt == 1
    assert t.stats is None
    assert t.scheduled_at == scheduled_at
    assert t.execution_snapshot == {
        "schema_version": 1,
        "executor_revision": "1.0.0",
        "diagnosers": [],
    }


@pytest.mark.asyncio
async def test_dispatched_timeout_fails_at_attempt_limit(session):
    uid = _insert_task(
        session,
        TaskStatus.DISPATCHED,
        worker="dead/9.9.9.9",
        attempt=2,
    )
    _age_task(session, uid, minutes=2)
    loop = RescueLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        dispatch_timeout_sec=60,
        alive_threshold_sec=30,
        max_task_attempts=2,
    )

    await loop.tick()

    task = TaskTable(session).get_by_uuid(uid)
    assert task.status == TaskStatus.FAILED
    assert task.worker == "dead/9.9.9.9"
    assert task.attempt == 2
    assert task.stats is None
    assert task.finished_at is not None
    assert "attempt limit reached: dispatch timeout" in task.msg


@pytest.mark.asyncio
async def test_dead_worker_fails_at_attempt_limit(session):
    uid = _insert_task(session, TaskStatus.RUNNING, worker="dead/9.9.9.9", attempt=2)
    loop = RescueLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        dispatch_timeout_sec=60,
        alive_threshold_sec=30,
        max_task_attempts=2,
    )

    await loop.tick()

    task = TaskTable(session).get_by_uuid(uid)
    assert task.status == TaskStatus.FAILED
    assert task.worker == "dead/9.9.9.9"
    assert task.attempt == 2
    assert task.stats is None
    assert task.finished_at is not None
    assert "attempt limit reached: worker dead" in task.msg


@pytest.mark.asyncio
async def test_attempt_limit_one_fails_first_dead_worker_check(session):
    uid = _insert_task(session, TaskStatus.RUNNING, worker="dead/9.9.9.9", attempt=1)
    loop = RescueLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        dispatch_timeout_sec=60,
        alive_threshold_sec=30,
        max_task_attempts=1,
    )

    await loop.tick()

    task = TaskTable(session).get_by_uuid(uid)
    assert task.status == TaskStatus.FAILED
    assert task.attempt == 1


@pytest.mark.asyncio
async def test_failed_task_stays_failed(session):
    uid = _insert_task(session, TaskStatus.FAILED, worker=None, attempt=1)
    loop = RescueLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        dispatch_timeout_sec=60,
        alive_threshold_sec=30,
    )
    await loop.tick()
    t = TaskTable(session).get_by_uuid(uid)
    assert t.status == TaskStatus.FAILED
    assert t.attempt == 1
