from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, select, update
from sqlalchemy.orm import Session

from agentlens.domain.enums import JobStatus, JobTriggerType
from agentlens.domain.job import Job
from agentlens.infra.ob.models import JobModel


def _job_status(value: str) -> JobStatus:
    return JobStatus(value)


def _to_domain(m: JobModel) -> Job:
    return Job(
        uuid=m.uuid,
        space_uuid=m.space_uuid,
        agent_uuid=m.agent_uuid,
        name=m.name,
        description=m.description or "",
        trigger_type=JobTriggerType(m.trigger_type),
        schedule_cron=m.schedule_cron,
        status=_job_status(m.status),
        dataset_uuid=m.dataset_uuid,
        diagnoser_uuids=list(m.diagnoser_uuids or []),
        gmt_create=m.gmt_create,
        gmt_modified=m.gmt_modified,
        creator=m.creator,
        modifier=m.modifier,
        spec=dict(m.spec or {}),
    )


class JobTable:
    def __init__(self, session: Session) -> None:
        self.s = session

    def insert(self, j: Job) -> None:
        now = datetime.now()
        m = JobModel(
            uuid=j.uuid,
            space_uuid=j.space_uuid,
            agent_uuid=j.agent_uuid,
            name=j.name,
            description=j.description,
            trigger_type=j.trigger_type.value,
            schedule_cron=j.schedule_cron,
            status=j.status.value,
            dataset_uuid=j.dataset_uuid,
            diagnoser_uuids=j.diagnoser_uuids,
            spec=j.spec or None,
            gmt_create=now,
            gmt_modified=now,
            creator=j.creator,
            modifier=j.modifier,
        )
        self.s.add(m)
        self.s.commit()

    def get_by_uuid_for_update(self, uuid: str) -> Job | None:
        m = self.s.scalar(
            select(JobModel)
            .where(JobModel.uuid == uuid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return _to_domain(m) if m else None

    def get_by_uuid(self, uuid: str) -> Job | None:
        m = self.s.scalar(select(JobModel).where(JobModel.uuid == uuid))
        return _to_domain(m) if m else None

    def list_enabled_schedule_jobs(self) -> list[Job]:
        stmt = select(JobModel).where(
            JobModel.status == JobStatus.ENABLED.value,
            JobModel.trigger_type == JobTriggerType.SCHEDULE.value,
        )
        return [_to_domain(m) for m in self.s.scalars(stmt)]

    def list_all(
        self,
        space_uuid: str | None = None,
        agent_uuid: str | None = None,
        *,
        space_uuids: Sequence[str] | None = None,
    ) -> list[Job]:
        stmt = select(JobModel)
        ordered_spaces: tuple[str, ...] = ()
        if space_uuids is not None:
            ordered_spaces = tuple(dict.fromkeys(space_uuids))
            stmt = stmt.where(JobModel.space_uuid.in_(ordered_spaces))
        elif space_uuid is not None:
            stmt = stmt.where(JobModel.space_uuid == space_uuid)
        if agent_uuid is not None:
            stmt = stmt.where(JobModel.agent_uuid == agent_uuid)
        if ordered_spaces:
            stmt = stmt.order_by(
                case(
                    {value: index for index, value in enumerate(ordered_spaces)},
                    value=JobModel.space_uuid,
                    else_=len(ordered_spaces),
                ),
                JobModel.gmt_create.desc(),
                JobModel.id.desc(),
            )
        else:
            stmt = stmt.order_by(JobModel.gmt_create.desc(), JobModel.id.desc())
        return [_to_domain(m) for m in self.s.scalars(stmt)]

    def list_referencing_dataset(
        self,
        space_uuid: str,
        agent_uuid: str,
        dataset_uuid: str,
    ) -> list[Job]:
        stmt = select(JobModel).where(
            JobModel.space_uuid == space_uuid,
            JobModel.agent_uuid == agent_uuid,
            JobModel.dataset_uuid == dataset_uuid,
        )
        return [_to_domain(m) for m in self.s.scalars(stmt)]

    def list_referencing_diagnosers(
        self,
        space_uuid: str,
        agent_uuid: str,
        diagnoser_uuids: set[str],
    ) -> list[Job]:
        if not diagnoser_uuids:
            return []
        jobs = self.list_all(space_uuid, agent_uuid)
        return [job for job in jobs if diagnoser_uuids.intersection(job.diagnoser_uuids)]

    def update_by_uuid(
        self,
        uuid: str,
        fields: dict[str, Any],
        modifier: str | None = None,
    ) -> bool:
        if modifier is not None:
            fields = {**fields, "modifier": modifier}
        fields = {**fields, "gmt_modified": datetime.now()}
        r = self.s.execute(update(JobModel).where(JobModel.uuid == uuid).values(**fields))
        self.s.commit()
        return r.rowcount > 0

    def delete_by_uuid(self, uuid: str) -> bool:
        r = self.s.execute(delete(JobModel).where(JobModel.uuid == uuid))
        self.s.commit()
        return r.rowcount > 0
