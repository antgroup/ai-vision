import asyncio
from datetime import datetime, timedelta
from time import perf_counter

from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.logging import get_logger


class TaskCleanupLoop:
    def __init__(
        self,
        *,
        task_table: TaskTable,
        retention_days: int,
        interval_sec: float,
        batch_size: int,
    ) -> None:
        self.tasks = task_table
        self.retention_days = retention_days
        self.interval = interval_sec
        self.batch_size = batch_size
        self._log = get_logger("task-cleanup")

    async def tick(self) -> tuple[int, int]:
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        started = perf_counter()
        try:
            selected, deleted = await asyncio.to_thread(
                self.tasks.delete_expired_terminal,
                cutoff=cutoff,
                batch_size=self.batch_size,
            )
        except Exception as exc:
            error = str(exc)
            try:
                await asyncio.to_thread(self.tasks.s.rollback)
            except Exception as rollback_exc:
                error = f"{error}; rollback_error={rollback_exc}"
            self._log.warning("task-cleanup.fail", cutoff=cutoff, error=error)
            return 0, 0

        log = self._log.info if deleted > 0 else self._log.debug
        log(
            "task-cleanup.batch",
            cutoff=cutoff,
            selected=selected,
            deleted=deleted,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
        )
        return selected, deleted

    async def run(self) -> None:
        self._log.info(
            "task-cleanup.start",
            retention_days=self.retention_days,
            interval_sec=self.interval,
            batch_size=self.batch_size,
        )
        while True:
            await self.tick()
            await asyncio.sleep(self.interval)
