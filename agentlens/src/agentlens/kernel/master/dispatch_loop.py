import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from sqlalchemy import update

from agentlens.domain.enums import TaskStatus
from agentlens.infra.ob.models import TaskModel
from agentlens.infra.ob.tables.heartbeat_table import HeartbeatTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.logging import get_logger


class Notifier(Protocol):
    async def push_execute(self, worker_uuid: str, task_uuid: str, fenced_token: int) -> bool: ...


class DispatchLoop:
    def __init__(
        self,
        *,
        task_table: TaskTable,
        heartbeat_table: HeartbeatTable,
        notify: Notifier,
        self_uuid: str,
        fenced_token_provider: Callable[[], int],
        master_execute_tasks: bool = False,
        local_execute: Callable[[str], Awaitable[None]] | None = None,
        interval_sec: float = 3.0,
        batch: int = 50,
        alive_threshold_sec: int = 30,
        local_execute_concurrency: int = 1,
        worker_execute_concurrency: int = 10,
    ) -> None:
        self.tasks = task_table
        self.heartbeats = heartbeat_table
        self.notify = notify
        self.self_uuid = self_uuid
        self.fenced_token_provider = fenced_token_provider
        self.master_execute_tasks = master_execute_tasks
        # 派给自己的任务进程内直跑（绕开 HTTP loopback 自推，自连不可靠/端口漂移时仍能执行）
        self.local_execute = local_execute
        self.local_execute_concurrency = max(1, local_execute_concurrency)
        self.worker_execute_concurrency = max(1, worker_execute_concurrency)
        self._local_running: set[asyncio.Task] = set()
        self.interval = interval_sec
        self.batch = batch
        self.alive_threshold = alive_threshold_sec
        self._log = get_logger("dispatch")

    async def tick(self) -> None:
        self._reap_local_tasks()
        local_slots = self._local_available_slots()
        dispatches = await asyncio.to_thread(self._prepare_dispatches, local_slots)
        if not dispatches:
            return
        fenced = self.fenced_token_provider()
        ok_count = 0
        for worker, task_uuid in dispatches:
            # 派给自己 → 进程内直跑（不走 HTTP 自推；runner 自行 CAS 接管，等同 /internal/execute）
            if self.local_execute is not None and worker == self.self_uuid:
                task = asyncio.create_task(self.local_execute(task_uuid))
                self._local_running.add(task)
                task.add_done_callback(self._on_local_done)
                self._log.info("dispatch.local_execute", task_uuid=task_uuid)
                ok_count += 1
                continue
            ok = await self.notify.push_execute(worker, task_uuid, fenced)
            if ok:
                self._log.info(
                    "dispatch.push_ok",
                    task_uuid=task_uuid,
                    target_worker=worker,
                    fenced_token=fenced,
                )
                ok_count += 1
            else:
                await asyncio.to_thread(self._rollback_dispatch, task_uuid)
                self._log.warning("dispatch.push_fail", task_uuid=task_uuid, target_worker=worker)
        self._log.info("dispatch.tick", dispatched_ok=ok_count, dispatched_total=len(dispatches))

    def _local_available_slots(self) -> int:
        if self.local_execute is None:
            return 0
        return max(0, self.local_execute_concurrency - len(self._local_running))

    def _reap_local_tasks(self) -> None:
        self._local_running = {t for t in self._local_running if not t.done()}

    def _on_local_done(self, task: asyncio.Task) -> None:
        self._local_running.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            self._log.warning("dispatch.local_execute_fail", err=str(e))

    async def wait_local_tasks(self) -> None:
        """Wait until every already-dispatched in-process Runner finishes."""
        while self._local_running:
            tasks = list(self._local_running)
            await asyncio.gather(*tasks, return_exceptions=True)
            self._reap_local_tasks()

    def _prepare_dispatches(self, local_slots: int = 0) -> list[tuple[str, str]]:
        """Sync: read workers + pending tasks, CAS dispatch, return (worker, task_uuid) pairs."""
        alive = self.heartbeats.list_alive(self.alive_threshold)
        if self.master_execute_tasks:
            workers = [h.uuid for h in alive]
        else:
            workers = [h.uuid for h in alive if h.uuid != self.self_uuid]
        if self.local_execute is not None and self.self_uuid in workers and local_slots <= 0:
            workers = [w for w in workers if w != self.self_uuid]
        if not workers:
            self._log.info(
                "dispatch.no_workers",
                alive_instances=len(alive),
                master_execute_tasks=self.master_execute_tasks,
            )
            return []
        pending = self.tasks.list_pending(limit=self.batch)
        assigned_counts = self.tasks.count_assigned_by_workers(workers)
        self._log.info(
            "dispatch.scan",
            available_workers=len(workers),
            pending_tasks=len(pending),
            assigned_tasks_by_worker=assigned_counts,
        )
        if not pending:
            return []
        local_count = 0
        result = []
        for t in pending:
            eligible_workers = [
                worker
                for worker in workers
                if (
                    local_count < local_slots
                    if self.local_execute is not None and worker == self.self_uuid
                    else assigned_counts.get(worker, 0) < self.worker_execute_concurrency
                )
            ]
            if not eligible_workers:
                break
            chosen = min(
                eligible_workers,
                key=lambda worker: (
                    assigned_counts.get(worker, 0),
                    worker == self.self_uuid,
                    worker,
                ),
            )
            if self.tasks.cas_dispatch(t.uuid, worker=chosen):
                result.append((chosen, t.uuid))
                assigned_counts[chosen] = assigned_counts.get(chosen, 0) + 1
                if self.local_execute is not None and chosen == self.self_uuid:
                    local_count += 1
        return result

    def _rollback_dispatch(self, task_uuid: str) -> None:
        self.tasks.s.execute(
            update(TaskModel)
            .where(TaskModel.uuid == task_uuid, TaskModel.status == TaskStatus.DISPATCHED.value)
            .values(status=TaskStatus.PENDING.value, worker=None, gmt_modified=datetime.now())
        )
        self.tasks.s.commit()

    async def run(self) -> None:
        self._log.info("dispatch.start")
        while True:
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001
                await asyncio.to_thread(self.tasks.s.rollback)
                self._log.warning("dispatch.tick_fail", err=str(e))
            await asyncio.sleep(self.interval)
