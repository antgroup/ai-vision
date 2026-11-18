from datetime import datetime

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentlens.domain.enums import TaskStatus
from agentlens.domain.task import Task
from agentlens.domain.task_stats import TaskStats
from agentlens.infra.ob.models import TaskModel


def _to_domain(m: TaskModel) -> Task:
    return Task(
        uuid=m.uuid,
        job_uuid=m.job_uuid,
        status=TaskStatus(m.status),
        worker=m.worker,
        scheduled_at=m.scheduled_at,
        started_at=m.started_at,
        finished_at=m.finished_at,
        attempt=m.attempt,
        msg=m.msg or "",
        gmt_create=m.gmt_create,
        gmt_modified=m.gmt_modified,
        creator=m.creator,
        modifier=m.modifier,
        stats=m.stats,
        execution_snapshot=m.execution_snapshot,
        judge_request_ledger=m.judge_request_ledger,
    )


def _append_expr(line: str):
    ts = datetime.now().isoformat(timespec="seconds")
    entry = f"[{ts}] {line}"
    current = func.coalesce(TaskModel.msg, "")
    return case(
        (current == "", entry),
        else_=func.concat(current, "\n", entry),
    )


class TaskTable:
    def __init__(self, session: Session) -> None:
        self.s = session

    def insert(self, t: Task) -> None:
        now = datetime.now()
        m = TaskModel(
            uuid=t.uuid,
            job_uuid=t.job_uuid,
            status=t.status.value,
            worker=t.worker,
            scheduled_at=t.scheduled_at,
            started_at=t.started_at,
            finished_at=t.finished_at,
            attempt=t.attempt,
            msg=t.msg,
            stats=t.stats,
            execution_snapshot=t.execution_snapshot,
            judge_request_ledger=t.judge_request_ledger,
            gmt_create=now,
            gmt_modified=now,
            creator=t.creator,
            modifier=t.modifier,
        )
        self.s.add(m)
        self.s.commit()

    def insert_for_schedule(self, t: Task) -> bool:
        """Insert one cron Task after checking the logical schedule identity."""
        if self.exists_for_schedule(t.job_uuid, t.scheduled_at):
            return False
        try:
            self.insert(t)
            return True
        except IntegrityError:
            self.s.rollback()
            if self.exists_for_schedule(t.job_uuid, t.scheduled_at):
                return False
            raise

    def get_by_uuid(self, uuid: str) -> Task | None:
        m = self.s.scalar(select(TaskModel).where(TaskModel.uuid == uuid))
        return _to_domain(m) if m else None

    def list_tasks(
        self,
        status: str | None = None,
        job_uuid: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        scheduled_start_time: datetime | None = None,
        scheduled_end_time: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
        job_uuids: list[str] | None = None,
    ) -> list[Task]:
        stmt = select(TaskModel)
        if status:
            stmt = stmt.where(TaskModel.status == status)
        if job_uuid:
            stmt = stmt.where(TaskModel.job_uuid == job_uuid)
        elif job_uuids:
            stmt = stmt.where(TaskModel.job_uuid.in_(job_uuids))
        if start_time:
            stmt = stmt.where(TaskModel.finished_at >= start_time)
        if end_time:
            stmt = stmt.where(TaskModel.finished_at <= end_time)
        if scheduled_start_time:
            stmt = stmt.where(TaskModel.scheduled_at >= scheduled_start_time)
        if scheduled_end_time:
            stmt = stmt.where(TaskModel.scheduled_at <= scheduled_end_time)
        stmt = stmt.order_by(TaskModel.gmt_modified.desc(), TaskModel.id.desc())
        stmt = stmt.offset(offset).limit(limit)
        self.s.commit()
        return [_to_domain(m) for m in self.s.scalars(stmt)]

    def count_tasks(
        self,
        status: str | None = None,
        job_uuid: str | None = None,
        scheduled_start_time: datetime | None = None,
        scheduled_end_time: datetime | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(TaskModel)
        if status:
            stmt = stmt.where(TaskModel.status == status)
        if job_uuid:
            stmt = stmt.where(TaskModel.job_uuid == job_uuid)
        if scheduled_start_time:
            stmt = stmt.where(TaskModel.scheduled_at >= scheduled_start_time)
        if scheduled_end_time:
            stmt = stmt.where(TaskModel.scheduled_at <= scheduled_end_time)
        return int(self.s.scalar(stmt) or 0)

    def list_pending(self, limit: int = 50) -> list[Task]:
        stmt = (
            select(TaskModel)
            .where(TaskModel.status == TaskStatus.PENDING.value)
            .order_by(TaskModel.scheduled_at.asc())
            .limit(limit)
        )
        return [_to_domain(m) for m in self.s.scalars(stmt)]

    def count_assigned_by_workers(self, worker_uuids: list[str]) -> dict[str, int]:
        """Count RUNNING and DISPATCHED Tasks occupying worker capacity."""
        if not worker_uuids:
            return {}
        counts = dict.fromkeys(worker_uuids, 0)
        rows = self.s.execute(
            select(TaskModel.worker, func.count(TaskModel.id))
            .where(
                TaskModel.worker.in_(worker_uuids),
                TaskModel.status.in_([TaskStatus.RUNNING.value, TaskStatus.DISPATCHED.value]),
            )
            .group_by(TaskModel.worker)
        ).all()
        counts.update({worker: int(count) for worker, count in rows if worker is not None})
        return counts

    def exists_for_schedule(self, job_uuid: str, scheduled_at: datetime) -> bool:
        stmt = (
            select(TaskModel.uuid)
            .where(
                TaskModel.job_uuid == job_uuid,
                TaskModel.scheduled_at == scheduled_at,
            )
            .limit(1)
        )
        return self.s.scalar(stmt) is not None

    def latest_scheduled_by_jobs(
        self,
        job_uuids: list[str],
        before: datetime,
    ) -> dict[str, datetime]:
        """Return each Job's latest Task logical time at or before the scan time."""
        if not job_uuids:
            return {}
        rows = self.s.execute(
            select(TaskModel.job_uuid, func.max(TaskModel.scheduled_at))
            .where(
                TaskModel.job_uuid.in_(job_uuids),
                TaskModel.scheduled_at <= before,
            )
            .group_by(TaskModel.job_uuid)
        ).all()
        return {job_uuid: scheduled_at for job_uuid, scheduled_at in rows}

    _ACTIVE_STATUSES = (
        TaskStatus.PENDING.value,
        TaskStatus.DISPATCHED.value,
        TaskStatus.RUNNING.value,
    )

    def find_active_by_job(self, job_uuid: str) -> Task | None:
        """Return the one active Task (pending/dispatched/running) for a Job, or None."""
        stmt = (
            select(TaskModel)
            .where(
                TaskModel.job_uuid == job_uuid,
                TaskModel.status.in_(self._ACTIVE_STATUSES),
            )
            .order_by(TaskModel.gmt_modified.desc())
            .limit(1)
        )
        m = self.s.scalar(stmt)
        return _to_domain(m) if m else None

    def cas_dispatch(self, uuid: str, worker: str) -> bool:
        r = self.s.execute(
            update(TaskModel)
            .where(TaskModel.uuid == uuid, TaskModel.status == TaskStatus.PENDING.value)
            .values(status=TaskStatus.DISPATCHED.value, worker=worker, gmt_modified=datetime.now())
        )
        self.s.commit()
        return r.rowcount > 0

    def cas_start_running(self, uuid: str, worker: str) -> bool:
        """status=DISPATCHED → RUNNING; attempt+=1; 不动 msg (msg 由 worker 异步追加)."""
        now = datetime.now()
        r = self.s.execute(
            update(TaskModel)
            .where(
                TaskModel.uuid == uuid,
                TaskModel.worker == worker,
                TaskModel.status == TaskStatus.DISPATCHED.value,
            )
            .values(
                status=TaskStatus.RUNNING.value,
                started_at=now,
                attempt=TaskModel.attempt + 1,
                stats=None,
                gmt_modified=now,
            )
        )
        self.s.commit()
        return r.rowcount > 0

    def mark_succeeded(
        self,
        uuid: str,
        worker: str,
        attempt: int,
        msg_append: str = "",
        stats: TaskStats | None = None,
    ) -> bool:
        """Fence: uuid + worker + attempt + RUNNING → SUCCEEDED."""
        now = datetime.now()
        values = {
            "status": TaskStatus.SUCCEEDED.value,
            "finished_at": now,
            "msg": _append_expr(f"succeeded {msg_append}".strip()),
            "gmt_modified": now,
        }
        if stats is not None:
            values["stats"] = stats
        r = self.s.execute(
            update(TaskModel)
            .where(
                TaskModel.uuid == uuid,
                TaskModel.worker == worker,
                TaskModel.attempt == attempt,
                TaskModel.status == TaskStatus.RUNNING.value,
            )
            .values(**values)
        )
        self.s.commit()
        return r.rowcount > 0

    def mark_failed(
        self,
        uuid: str,
        worker: str,
        attempt: int,
        msg_append: str = "",
        stats: TaskStats | None = None,
    ) -> bool:
        """Fence: uuid + worker + attempt + RUNNING → FAILED."""
        now = datetime.now()
        values = {
            "status": TaskStatus.FAILED.value,
            "finished_at": now,
            "msg": _append_expr(f"failed {msg_append}".strip()),
            "gmt_modified": now,
        }
        if stats is not None:
            values["stats"] = stats
        r = self.s.execute(
            update(TaskModel)
            .where(
                TaskModel.uuid == uuid,
                TaskModel.worker == worker,
                TaskModel.attempt == attempt,
                TaskModel.status == TaskStatus.RUNNING.value,
            )
            .values(**values)
        )
        self.s.commit()
        return r.rowcount > 0

    def delete_by_uuid(self, uuid: str) -> bool:
        r = self.s.execute(delete(TaskModel).where(TaskModel.uuid == uuid))
        self.s.commit()
        return r.rowcount > 0

    def delete_by_job_uuid(self, job_uuid: str) -> int:
        r = self.s.execute(delete(TaskModel).where(TaskModel.job_uuid == job_uuid))
        self.s.commit()
        return r.rowcount

    def delete_expired_terminal(self, *, cutoff: datetime, batch_size: int) -> tuple[int, int]:
        """Delete one oldest-first batch of terminal Tasks in a short transaction."""
        terminal = (
            TaskStatus.SUCCEEDED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELED.value,
        )
        candidate_ids = list(
            self.s.scalars(
                select(TaskModel.id)
                .where(
                    TaskModel.status.in_(terminal),
                    TaskModel.finished_at < cutoff,
                )
                .order_by(TaskModel.finished_at.asc(), TaskModel.id.asc())
                .limit(batch_size)
            )
        )
        if not candidate_ids:
            self.s.commit()
            return 0, 0
        result = self.s.execute(delete(TaskModel).where(TaskModel.id.in_(candidate_ids)))
        self.s.commit()
        return len(candidate_ids), result.rowcount

    def reset_orphaned_by_worker(
        self,
        worker: str,
        *,
        max_task_attempts: int = 2,
    ) -> tuple[int, int]:
        """Recover this worker's orphaned tasks within the attempt limit."""
        now = datetime.now()
        failed = self.s.execute(
            update(TaskModel)
            .where(
                TaskModel.worker == worker,
                TaskModel.status.in_([TaskStatus.RUNNING.value, TaskStatus.DISPATCHED.value]),
                TaskModel.attempt >= max_task_attempts,
            )
            .values(
                status=TaskStatus.FAILED.value,
                finished_at=now,
                stats=None,
                msg=_append_expr("failed attempt limit reached: worker restarted"),
                gmt_modified=now,
            )
        )
        rescued = self.s.execute(
            update(TaskModel)
            .where(
                TaskModel.worker == worker,
                TaskModel.status.in_([TaskStatus.RUNNING.value, TaskStatus.DISPATCHED.value]),
                TaskModel.attempt < max_task_attempts,
            )
            .values(
                status=TaskStatus.PENDING.value,
                worker=None,
                stats=None,
                msg=_append_expr("rescue: worker restarted"),
                gmt_modified=now,
            )
        )
        self.s.commit()
        return rescued.rowcount, failed.rowcount

    def cas_cancel(self, uuid: str) -> bool:
        """status ∈ {PENDING, DISPATCHED} → CANCELED."""
        now = datetime.now()
        allowed = (TaskStatus.PENDING, TaskStatus.DISPATCHED)
        r = self.s.execute(
            update(TaskModel)
            .where(TaskModel.uuid == uuid, TaskModel.status.in_([s.value for s in allowed]))
            .values(
                status=TaskStatus.CANCELED.value,
                finished_at=now,
                msg=_append_expr("canceled"),
                gmt_modified=now,
            )
        )
        self.s.commit()
        return r.rowcount > 0
