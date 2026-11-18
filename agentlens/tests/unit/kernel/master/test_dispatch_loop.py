from datetime import UTC, datetime

import pytest

from agentlens.domain.enums import TaskStatus
from agentlens.domain.task import Task
from agentlens.infra.ob.tables.heartbeat_table import HeartbeatTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.kernel.master.dispatch_loop import DispatchLoop
from agentlens.utils.uuid_gen import new_task_uuid


def _seed_pending(session, n: int) -> list[str]:
    ids: list[str] = []
    t = TaskTable(session)
    for _ in range(n):
        u = new_task_uuid()
        t.insert(
            Task(
                uuid=u,
                job_uuid="j-aaaaaaaa",
                status=TaskStatus.PENDING,
                worker=None,
                scheduled_at=datetime.now(UTC),
                started_at=None,
                finished_at=None,
                attempt=0,
                msg="",
                gmt_create=None,
                gmt_modified=None,
                creator=None,
                modifier=None,
            )
        )
        ids.append(u)
    return ids


def _seed_running(session, worker: str, n: int) -> list[str]:
    ids = _seed_pending(session, n)
    table = TaskTable(session)
    for task_uuid in ids:
        assert table.cas_dispatch(task_uuid, worker)
        assert table.cas_start_running(task_uuid, worker)
    return ids


class _StubNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []  # (worker, task, fenced)
        self.ok = True

    async def push_execute(self, worker_uuid: str, task_uuid: str, fenced_token: int) -> bool:
        self.calls.append((worker_uuid, task_uuid, fenced_token))
        return self.ok


@pytest.mark.asyncio
async def test_dispatch_pushes_each_pending_to_alive_workers(session):
    HeartbeatTable(session).upsert("w1", "1.1.1.1")
    HeartbeatTable(session).upsert("w2", "2.2.2.2")
    ids = _seed_pending(session, 3)
    notifier = _StubNotifier()
    loop = DispatchLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        notify=notifier,
        self_uuid="w-master/0.0.0.0",
        fenced_token_provider=lambda: 7,
    )
    await loop.tick()
    assert {c[1] for c in notifier.calls} == set(ids)
    assert [c[0] for c in notifier.calls] == [
        "w1/1.1.1.1",
        "w2/2.2.2.2",
        "w1/1.1.1.1",
    ]
    # 排除自己
    for w, _, _ in notifier.calls:
        assert w != "w-master/0.0.0.0"


@pytest.mark.asyncio
async def test_dispatch_prefers_worker_with_fewer_running_tasks(session):
    HeartbeatTable(session).upsert("w1", "1.1.1.1")
    HeartbeatTable(session).upsert("w2", "2.2.2.2")
    _seed_running(session, "w1/1.1.1.1", 2)
    task_uuid = _seed_pending(session, 1)[0]
    notifier = _StubNotifier()
    loop = DispatchLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        notify=notifier,
        self_uuid="w-master/0.0.0.0",
        fenced_token_provider=lambda: 7,
    )

    await loop.tick()

    assert notifier.calls == [("w2/2.2.2.2", task_uuid, 7)]


@pytest.mark.asyncio
async def test_dispatch_prefers_non_master_when_running_counts_are_equal(session):
    import asyncio

    HeartbeatTable(session).upsert("master", "0.0.0.0")
    HeartbeatTable(session).upsert("worker", "1.1.1.1")
    task_uuid = _seed_pending(session, 1)[0]
    notifier = _StubNotifier()
    local_calls: list[str] = []

    async def local_exec(uuid: str) -> None:
        local_calls.append(uuid)

    loop = DispatchLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        notify=notifier,
        self_uuid="master/0.0.0.0",
        fenced_token_provider=lambda: 9,
        master_execute_tasks=True,
        local_execute=local_exec,
    )

    await loop.tick()
    await asyncio.sleep(0.05)

    assert notifier.calls == [("worker/1.1.1.1", task_uuid, 9)]
    assert local_calls == []


@pytest.mark.asyncio
async def test_dispatch_skipped_when_no_workers(session):
    _seed_pending(session, 1)
    notifier = _StubNotifier()
    loop = DispatchLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        notify=notifier,
        self_uuid="me/x",
        fenced_token_provider=lambda: 1,
    )
    await loop.tick()
    assert notifier.calls == []


@pytest.mark.asyncio
async def test_self_dispatch_runs_in_process_not_http(session):
    """master 给自己派单时 → 进程内直跑 local_execute，不走 HTTP 自推（解自连 loopback 失败）。"""
    import asyncio

    HeartbeatTable(session).upsert("me", "0.0.0.0")  # uuid = "me/0.0.0.0"
    uid = _seed_pending(session, 1)[0]
    notifier = _StubNotifier()
    local_calls: list[str] = []

    async def local_exec(task_uuid: str) -> None:
        local_calls.append(task_uuid)

    loop = DispatchLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        notify=notifier,
        self_uuid="me/0.0.0.0",
        fenced_token_provider=lambda: 1,
        master_execute_tasks=True,
        local_execute=local_exec,
    )
    await loop.tick()
    await asyncio.sleep(0.05)  # 让 fire-and-forget 的本地执行协程跑起来
    assert local_calls == [uid]  # 进程内执行
    assert notifier.calls == []  # 未走 HTTP 自推
    # 任务保持 dispatched（runner 接管），不回滚
    assert TaskTable(session).get_by_uuid(uid).status == TaskStatus.DISPATCHED


@pytest.mark.asyncio
async def test_self_dispatch_respects_local_concurrency(session):
    import asyncio

    HeartbeatTable(session).upsert("me", "0.0.0.0")
    ids = _seed_pending(session, 3)
    notifier = _StubNotifier()
    started: list[str] = []
    release = asyncio.Event()

    async def local_exec(task_uuid: str) -> None:
        started.append(task_uuid)
        await release.wait()

    loop = DispatchLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        notify=notifier,
        self_uuid="me/0.0.0.0",
        fenced_token_provider=lambda: 1,
        master_execute_tasks=True,
        local_execute=local_exec,
        local_execute_concurrency=1,
    )
    await loop.tick()
    await asyncio.sleep(0.05)
    assert started == [ids[0]]
    assert notifier.calls == []
    assert TaskTable(session).get_by_uuid(ids[0]).status == TaskStatus.DISPATCHED
    assert TaskTable(session).get_by_uuid(ids[1]).status == TaskStatus.PENDING

    await loop.tick()
    await asyncio.sleep(0.05)
    assert started == [ids[0]]
    assert TaskTable(session).get_by_uuid(ids[1]).status == TaskStatus.PENDING

    release.set()
    await asyncio.sleep(0.05)
    await loop.tick()
    await asyncio.sleep(0.05)
    assert started == [ids[0], ids[1]]


@pytest.mark.asyncio
async def test_wait_local_tasks_blocks_until_running_execution_finishes(session):
    import asyncio

    release = asyncio.Event()

    async def local_exec(_task_uuid: str) -> None:
        await release.wait()

    loop = DispatchLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        notify=_StubNotifier(),
        self_uuid="me/0.0.0.0",
        fenced_token_provider=lambda: 1,
        local_execute=local_exec,
    )
    running = asyncio.create_task(local_exec("t-running"))
    loop._local_running.add(running)
    running.add_done_callback(loop._on_local_done)

    waiter = asyncio.create_task(loop.wait_local_tasks())
    await asyncio.sleep(0)
    assert waiter.done() is False

    release.set()
    await waiter
    assert loop._local_running == set()


@pytest.mark.asyncio
async def test_remote_worker_still_uses_http(session):
    """非自己的 worker 仍走 HTTP 推送（local_execute 只对 self 生效）。"""
    HeartbeatTable(session).upsert("w1", "1.1.1.1")  # uuid = "w1/1.1.1.1"
    uid = _seed_pending(session, 1)[0]
    notifier = _StubNotifier()
    local_calls: list[str] = []

    async def local_exec(task_uuid: str) -> None:
        local_calls.append(task_uuid)

    loop = DispatchLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        notify=notifier,
        self_uuid="me/0.0.0.0",
        fenced_token_provider=lambda: 1,
        master_execute_tasks=True,
        local_execute=local_exec,
    )
    await loop.tick()
    assert local_calls == []  # 远端 worker 不走本地执行
    assert [c[1] for c in notifier.calls] == [uid]  # 走 HTTP 推送


@pytest.mark.asyncio
async def test_push_fail_rolls_back_to_pending(session):
    HeartbeatTable(session).upsert("w1", "1.1.1.1")
    uid = _seed_pending(session, 1)[0]
    notifier = _StubNotifier()
    notifier.ok = False
    loop = DispatchLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        notify=notifier,
        self_uuid="me/x",
        fenced_token_provider=lambda: 1,
    )
    await loop.tick()
    t = TaskTable(session).get_by_uuid(uid)
    assert t.status == TaskStatus.PENDING
    assert t.worker is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("running", "dispatched", "expected_new"),
    [(0, 0, 2), (1, 0, 1), (0, 1, 1), (1, 1, 0), (2, 1, 0)],
)
async def test_dispatch_checks_remaining_capacity_for_every_task(
    session, running, dispatched, expected_new
):
    worker = "w1/1.1.1.1"
    HeartbeatTable(session).upsert("w1", "1.1.1.1")
    table = TaskTable(session)
    _seed_running(session, worker, running)
    for task_uuid in _seed_pending(session, dispatched):
        assert table.cas_dispatch(task_uuid, worker)
    pending = _seed_pending(session, 5)
    notifier = _StubNotifier()
    loop = DispatchLoop(
        task_table=table,
        heartbeat_table=HeartbeatTable(session),
        notify=notifier,
        self_uuid="master/0.0.0.0",
        fenced_token_provider=lambda: 1,
        worker_execute_concurrency=2,
    )

    await loop.tick()

    assert [call[1] for call in notifier.calls] == pending[:expected_new]
    for task_uuid in pending[expected_new:]:
        task = table.get_by_uuid(task_uuid)
        assert task.status == TaskStatus.PENDING
        assert task.attempt == 0

    # Accepted requests have not entered RUNNING yet, but retain capacity on
    # the next scan instead of being treated as zero load again.
    await loop.tick()
    assert len(notifier.calls) == expected_new


@pytest.mark.asyncio
async def test_dispatch_prefers_worker_with_fewer_assigned_tasks(session):
    HeartbeatTable(session).upsert("w1", "1.1.1.1")
    HeartbeatTable(session).upsert("w2", "2.2.2.2")
    table = TaskTable(session)
    for task_uuid in _seed_pending(session, 2):
        table.cas_dispatch(task_uuid, "w1/1.1.1.1")
    _seed_running(session, "w2/2.2.2.2", 1)
    pending = _seed_pending(session, 1)
    notifier = _StubNotifier()
    loop = DispatchLoop(
        task_table=table,
        heartbeat_table=HeartbeatTable(session),
        notify=notifier,
        self_uuid="master/0.0.0.0",
        fenced_token_provider=lambda: 1,
    )

    await loop.tick()

    assert notifier.calls == [("w2/2.2.2.2", pending[0], 1)]


@pytest.mark.asyncio
async def test_dispatch_preserves_uuid_tie_break_and_caps_each_worker(session):
    HeartbeatTable(session).upsert("w2", "2.2.2.2")
    HeartbeatTable(session).upsert("w1", "1.1.1.1")
    pending = _seed_pending(session, 6)
    table = TaskTable(session)
    notifier = _StubNotifier()
    loop = DispatchLoop(
        task_table=table,
        heartbeat_table=HeartbeatTable(session),
        notify=notifier,
        self_uuid="master/0.0.0.0",
        fenced_token_provider=lambda: 1,
        worker_execute_concurrency=2,
    )

    await loop.tick()

    assert [call[0] for call in notifier.calls] == [
        "w1/1.1.1.1",
        "w2/2.2.2.2",
        "w1/1.1.1.1",
        "w2/2.2.2.2",
    ]
    assert [call[1] for call in notifier.calls] == pending[:4]
    for task_uuid in pending[4:]:
        assert table.get_by_uuid(task_uuid).status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_failed_dispatch_cas_does_not_consume_capacity(session, monkeypatch):
    HeartbeatTable(session).upsert("w1", "1.1.1.1")
    pending = _seed_pending(session, 3)
    table = TaskTable(session)
    original_cas = table.cas_dispatch
    monkeypatch.setattr(
        table,
        "cas_dispatch",
        lambda uuid, worker: False if uuid == pending[0] else original_cas(uuid, worker),
    )
    notifier = _StubNotifier()
    loop = DispatchLoop(
        task_table=table,
        heartbeat_table=HeartbeatTable(session),
        notify=notifier,
        self_uuid="master/0.0.0.0",
        fenced_token_provider=lambda: 1,
        worker_execute_concurrency=1,
    )

    await loop.tick()

    assert notifier.calls == [("w1/1.1.1.1", pending[1], 1)]
    assert table.get_by_uuid(pending[2]).status == TaskStatus.PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize("release", ["succeeded", "canceled", "push_failed"])
async def test_released_capacity_is_available_on_next_scan(session, release):
    worker = "w1/1.1.1.1"
    HeartbeatTable(session).upsert("w1", "1.1.1.1")
    table = TaskTable(session)
    pending = _seed_pending(session, 2)
    notifier = _StubNotifier()
    notifier.ok = release != "push_failed"
    loop = DispatchLoop(
        task_table=table,
        heartbeat_table=HeartbeatTable(session),
        notify=notifier,
        self_uuid="master/0.0.0.0",
        fenced_token_provider=lambda: 1,
        worker_execute_concurrency=1,
    )
    await loop.tick()
    if release == "succeeded":
        assert table.cas_start_running(pending[0], worker)
        assert table.mark_succeeded(pending[0], worker, 1)
    elif release == "canceled":
        assert table.cas_cancel(pending[0])
    notifier.ok = True
    notifier.calls.clear()

    await loop.tick()

    expected = pending[0] if release == "push_failed" else pending[1]
    assert notifier.calls == [(worker, expected, 1)]
