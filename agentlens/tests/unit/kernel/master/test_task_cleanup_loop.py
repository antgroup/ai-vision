import asyncio

import pytest

from agentlens.kernel.master.task_cleanup_loop import TaskCleanupLoop


class _Session:
    def __init__(self, rollback_error: Exception | None = None) -> None:
        self.rollbacks = 0
        self.rollback_error = rollback_error

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error


class _TaskTable:
    def __init__(
        self,
        result=(0, 0),
        error: Exception | None = None,
        rollback_error: Exception | None = None,
    ) -> None:
        self.s = _Session(rollback_error)
        self.result = result
        self.error = error
        self.calls = []
        self.called = asyncio.Event()

    def delete_expired_terminal(self, *, cutoff, batch_size):
        self.calls.append((cutoff, batch_size))
        if self.error is not None:
            raise self.error
        self.called.set()
        return self.result


@pytest.mark.asyncio
async def test_task_cleanup_tick_runs_one_batch():
    table = _TaskTable(result=(1000, 998))
    loop = TaskCleanupLoop(
        task_table=table,
        retention_days=30,
        interval_sec=600,
        batch_size=1000,
    )

    assert await loop.tick() == (1000, 998)
    assert len(table.calls) == 1
    assert table.calls[0][1] == 1000


@pytest.mark.asyncio
async def test_task_cleanup_tick_rolls_back_and_keeps_running_after_failure():
    table = _TaskTable(error=RuntimeError("database unavailable"))
    loop = TaskCleanupLoop(
        task_table=table,
        retention_days=30,
        interval_sec=600,
        batch_size=1000,
    )

    assert await loop.tick() == (0, 0)
    assert table.s.rollbacks == 1


@pytest.mark.asyncio
async def test_task_cleanup_tick_survives_rollback_failure():
    table = _TaskTable(
        error=RuntimeError("database unavailable"),
        rollback_error=RuntimeError("connection lost"),
    )
    loop = TaskCleanupLoop(
        task_table=table,
        retention_days=30,
        interval_sec=600,
        batch_size=1000,
    )

    assert await loop.tick() == (0, 0)
    assert table.s.rollbacks == 1


@pytest.mark.asyncio
async def test_task_cleanup_run_executes_immediately():
    table = _TaskTable()
    loop = TaskCleanupLoop(
        task_table=table,
        retention_days=30,
        interval_sec=3600,
        batch_size=1000,
    )

    task = asyncio.create_task(loop.run())
    await asyncio.wait_for(table.called.wait(), timeout=1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert len(table.calls) == 1
