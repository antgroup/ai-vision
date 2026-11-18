import asyncio
from collections.abc import Callable
from datetime import datetime

from croniter import croniter

from agentlens.domain.enums import JobStatus, JobTriggerType, TaskStatus
from agentlens.domain.errors import Validation
from agentlens.domain.task import Task
from agentlens.infra.ob.tables.dataset_table import DatasetTable
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.logging import get_logger
from agentlens.service.execution_snapshot import snapshot_from_tables
from agentlens.utils.uuid_gen import new_task_uuid


class ScheduleScanner:
    """每 tick 扫 enabled schedule job，为每个 job 按 cron 生成 pending task。"""

    def __init__(
        self,
        job_table: JobTable,
        task_table: TaskTable,
        now_fn: Callable[[], datetime] | None = None,
        interval_sec: float = 5.0,
    ) -> None:
        self.jobs = job_table
        self.tasks = task_table
        self.now_fn = now_fn or (lambda: datetime.now())
        self.interval = interval_sec
        self._log = get_logger("schedule-scanner")

    async def tick(self) -> None:
        await asyncio.to_thread(self._tick_sync)

    def _tick_sync(self) -> None:
        # ScheduleScanner 持有长生命周期 Session。MySQL 的 REPEATABLE READ 会让上一轮
        # SELECT 建立的快照持续存在；结束读事务后本轮才能看到运行期间新建/启用的 job。
        self.jobs.s.rollback()
        if self.tasks.s is not self.jobs.s:
            self.tasks.s.rollback()
        now = self.now_fn()
        jobs = self.jobs.list_enabled_schedule_jobs()
        latest_by_job = self.tasks.latest_scheduled_by_jobs(
            [job.uuid for job in jobs],
            before=now,
        )
        self._log.info("schedule-scanner.tick", enabled_schedule_jobs=len(jobs))
        for job in jobs:
            cron = job.schedule_cron
            if not cron:
                continue
            candidates = [latest_by_job.get(job.uuid), job.gmt_modified, job.gmt_create]
            last = max(value for value in candidates if value is not None)
            it = croniter(cron, last)
            n_emitted = 0
            n_skipped = 0
            while True:
                nxt = it.get_next(datetime)
                if nxt > now:
                    break
                try:
                    emitted = self._emit_task(
                        job.uuid,
                        scheduled_at=nxt,
                        expected_cron=cron,
                        expected_modified=job.gmt_modified,
                    )
                except Validation as exc:
                    # A rejected definition/policy belongs to this Job. Release
                    # its freeze locks and allow unrelated Spaces to schedule.
                    self.jobs.s.rollback()
                    if self.tasks.s is not self.jobs.s:
                        self.tasks.s.rollback()
                    self._log.warning(
                        "schedule.job_unavailable",
                        job_uuid=job.uuid,
                        error_type=type(exc).__name__,
                    )
                    break
                if emitted:
                    n_emitted += 1
                else:
                    n_skipped += 1
            if n_emitted or n_skipped:
                self._log.info(
                    "schedule.emit",
                    job_uuid=job.uuid,
                    emitted_tasks=n_emitted,
                    skipped_existing=n_skipped,
                )

    def _emit_task(
        self,
        job_uuid: str,
        scheduled_at: datetime,
        *,
        expected_cron: str | None = None,
        expected_modified: datetime | None = None,
    ) -> bool:
        job = self.jobs.get_by_uuid_for_update(job_uuid)
        if (
            job is None
            or job.status != JobStatus.ENABLED
            or job.trigger_type != JobTriggerType.SCHEDULE
            or not job.schedule_cron
            or (expected_cron is not None and job.schedule_cron != expected_cron)
            or (expected_modified is not None and job.gmt_modified != expected_modified)
        ):
            self.jobs.s.rollback()
            return False
        snapshot = snapshot_from_tables(
            job,
            DatasetTable(self.jobs.s),
            DiagnoserTable(self.jobs.s),
            ModelConnectionTable(self.jobs.s),
        )
        return self.tasks.insert_for_schedule(
            Task(
                uuid=new_task_uuid(),
                job_uuid=job_uuid,
                status=TaskStatus.PENDING,
                worker=None,
                scheduled_at=scheduled_at,
                started_at=None,
                finished_at=None,
                attempt=0,
                msg="",
                gmt_create=None,
                gmt_modified=None,
                creator=None,
                modifier=None,
                execution_snapshot=snapshot,
            )
        )

    async def run(self) -> None:
        self._log.info("schedule-scanner.start")
        while True:
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001
                await asyncio.to_thread(self.tasks.s.rollback)
                self._log.warning("schedule-scanner.tick_fail", err=str(e))
            await asyncio.sleep(self.interval)
