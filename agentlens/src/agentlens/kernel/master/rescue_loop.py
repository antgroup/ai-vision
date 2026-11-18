import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select, text, update

from agentlens.domain.enums import TaskStatus
from agentlens.infra.ob.models import TaskModel
from agentlens.infra.ob.tables.heartbeat_table import HeartbeatTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.logging import get_logger


class RescueLoop:
    def __init__(
        self,
        *,
        task_table: TaskTable,
        heartbeat_table: HeartbeatTable,
        dispatch_timeout_sec: int,
        alive_threshold_sec: int,
        max_task_attempts: int = 2,
        interval_sec: float = 10.0,
    ) -> None:
        self.tasks = task_table
        self.heartbeats = heartbeat_table
        self.timeout = dispatch_timeout_sec
        self.alive_threshold = alive_threshold_sec
        self.max_task_attempts = max_task_attempts
        self.interval = interval_sec
        self._log = get_logger("rescue")

    async def tick(self) -> None:
        await asyncio.to_thread(self._tick_sync)

    def _tick_sync(self) -> None:
        self._log.info("rescue.tick")
        self._rescue_dispatched_timeout()
        self._rescue_running_dead_worker()

    def _rescue_dispatched_timeout(self) -> None:
        now = datetime.now()
        before = now - timedelta(seconds=self.timeout)
        failed = self.tasks.s.execute(
            text("""
            UPDATE agentlens_task
            SET status='failed', finished_at=:now, stats=NULL, gmt_modified=:now,
                msg=CONCAT(msg, '\n[', :now, '] failed attempt limit reached: dispatch timeout')
            WHERE status='dispatched' AND gmt_modified < :before
              AND attempt >= :max_task_attempts
        """),
            {
                "now": now,
                "before": before,
                "max_task_attempts": self.max_task_attempts,
            },
        )
        rescued = self.tasks.s.execute(
            text("""
            UPDATE agentlens_task
            SET status='pending', worker=NULL, stats=NULL, gmt_modified=:now,
                msg=CONCAT(msg, '\n[', :now, '] rescue: dispatch timeout')
            WHERE status='dispatched' AND gmt_modified < :before
              AND attempt < :max_task_attempts
        """),
            {
                "now": now,
                "before": before,
                "max_task_attempts": self.max_task_attempts,
            },
        )
        self.tasks.s.commit()
        self._log.info(
            "rescue.check_dispatch_timeout",
            dispatch_timeout_sec=self.timeout,
            max_task_attempts=self.max_task_attempts,
            rescued_tasks=rescued.rowcount,
            failed_tasks=failed.rowcount,
        )

    def _rescue_running_dead_worker(self) -> None:
        alive = {h.uuid for h in self.heartbeats.list_alive(self.alive_threshold)}
        rows = self.tasks.s.scalars(
            select(TaskModel).where(TaskModel.status == TaskStatus.RUNNING.value)
        ).all()
        self._log.info(
            "rescue.check_dead_worker", alive_worker_count=len(alive), running_task_count=len(rows)
        )
        for m in rows:
            if m.worker is None or m.worker in alive:
                continue
            dead_worker_uuid = m.worker
            attempt = m.attempt
            limit_reached = attempt >= self.max_task_attempts
            now = datetime.now()
            values = {
                "status": (TaskStatus.FAILED.value if limit_reached else TaskStatus.PENDING.value),
                "stats": None,
                "gmt_modified": now,
                "msg": (m.msg or "")
                + f"\n[{now.isoformat()}] "
                + (
                    "failed attempt limit reached: worker dead"
                    if limit_reached
                    else "rescue: worker dead"
                ),
            }
            if limit_reached:
                values["finished_at"] = now
            else:
                values["worker"] = None
            result = self.tasks.s.execute(
                update(TaskModel)
                .where(
                    TaskModel.uuid == m.uuid,
                    TaskModel.worker == dead_worker_uuid,
                    TaskModel.status == TaskStatus.RUNNING.value,
                )
                .values(**values)
            )
            if result.rowcount == 0:
                continue
            if limit_reached:
                self._log.info(
                    "rescue.limit_exceeded",
                    task_uuid=m.uuid,
                    reason="worker_dead",
                    attempt=attempt,
                    max_task_attempts=self.max_task_attempts,
                )
            else:
                self._log.info(
                    "rescue.dead_worker",
                    task_uuid=m.uuid,
                    dead_worker_uuid=dead_worker_uuid,
                    attempt=attempt,
                    max_task_attempts=self.max_task_attempts,
                )
        self.tasks.s.commit()

    async def run(self) -> None:
        self._log.info("rescue.start")
        while True:
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001
                await asyncio.to_thread(self.tasks.s.rollback)
                self._log.warning("rescue.tick_fail", err=str(e))
            await asyncio.sleep(self.interval)
