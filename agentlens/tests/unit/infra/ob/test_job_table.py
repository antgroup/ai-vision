from datetime import datetime

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from agentlens.domain.enums import JobStatus, JobTriggerType
from agentlens.domain.job import Job
from agentlens.infra.ob.models import JobModel
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.utils.uuid_gen import new_job_uuid


def _make(
    name="j",
    space="s",
    agent="A",
    trigger=JobTriggerType.SCHEDULE,
    status=JobStatus.ENABLED,
) -> Job:
    return Job(
        uuid=new_job_uuid(),
        space_uuid=space,
        agent_uuid=agent,
        name=name,
        description="",
        trigger_type=trigger,
        schedule_cron="0 * * * *" if trigger == JobTriggerType.SCHEDULE else None,
        status=status,
        dataset_uuid="ds-aaaaaaaa",
        diagnoser_uuids=["dg-bbbbbbbb"],
        gmt_create=None,
        gmt_modified=None,
        creator="u",
        modifier="u",
    )


def test_insert_get(session):
    t = JobTable(session)
    j = _make()
    t.insert(j)
    f = t.get_by_uuid(j.uuid)
    assert f.diagnoser_uuids == ["dg-bbbbbbbb"]
    assert f.trigger_type == JobTriggerType.SCHEDULE


def test_insert_get_preserves_job_spec(session):
    table = JobTable(session)
    job = _make()
    job.spec = {"save_badcase_only": False, "future_option": {"mode": "audit"}}

    table.insert(job)

    found = table.get_by_uuid(job.uuid)
    assert found.spec == job.spec


def test_get_null_job_spec_as_empty_object(session):
    table = JobTable(session)
    job = _make()
    table.insert(job)

    found = table.get_by_uuid(job.uuid)

    assert found.spec == {}


def test_list_enabled_schedule_jobs(session):
    t = JobTable(session)
    t.insert(_make(name="a", trigger=JobTriggerType.SCHEDULE, status=JobStatus.ENABLED))
    t.insert(_make(name="b", trigger=JobTriggerType.SCHEDULE, status=JobStatus.DISABLED))
    t.insert(_make(name="c", trigger=JobTriggerType.MANUAL, status=JobStatus.ENABLED))
    rows = t.list_enabled_schedule_jobs()
    assert {r.name for r in rows} == {"a"}


def test_list_all_returns_exact_scope_only(session):
    table = JobTable(session)
    table.insert(_make(name="exact", space="s", agent="a"))
    table.insert(_make(name="shared-space", space="s-shard", agent="a"))
    table.insert(_make(name="other-space", space="other", agent="a"))
    table.insert(_make(name="other-agent", space="s", agent="other"))

    rows = table.list_all(space_uuid="s", agent_uuid="a")

    assert [row.name for row in rows] == ["exact"]


def test_list_all_includes_selected_spaces_in_requested_order(session):
    table = JobTable(session)
    table.insert(_make(name="local", space="s", agent="a"))
    table.insert(_make(name="user-shared", space="s-user_shared", agent="a"))
    table.insert(_make(name="global-shared", space="s-shard", agent="a"))
    table.insert(_make(name="other-agent", space="s-user_shared", agent="other"))

    rows = table.list_all(
        agent_uuid="a",
        space_uuids=("s", "s-user_shared"),
    )

    assert [(row.space_uuid, row.name) for row in rows] == [
        ("s", "local"),
        ("s-user_shared", "user-shared"),
    ]


def test_list_all_orders_by_creation_time_descending(session):
    table = JobTable(session)
    older = _make(name="older")
    newer = _make(name="newer")
    table.insert(older)
    table.insert(newer)
    session.execute(
        update(JobModel)
        .where(JobModel.uuid == older.uuid)
        .values(gmt_create=datetime(2026, 8, 3, 10, 0, 0))
    )
    session.execute(
        update(JobModel)
        .where(JobModel.uuid == newer.uuid)
        .values(gmt_create=datetime(2026, 8, 4, 10, 0, 0))
    )
    session.commit()

    rows = table.list_all(space_uuid="s", agent_uuid="A")

    assert [row.name for row in rows] == ["newer", "older"]


def test_duplicate_name_collides(session):
    t = JobTable(session)
    t.insert(_make(name="dup"))
    with pytest.raises(IntegrityError):
        t.insert(_make(name="dup"))


def test_update_status_and_cron(session):
    t = JobTable(session)
    j = _make()
    t.insert(j)
    t.update_by_uuid(j.uuid, {"status": JobStatus.DISABLED.value, "schedule_cron": "*/5 * * * *"})
    f = t.get_by_uuid(j.uuid)
    assert f.status == JobStatus.DISABLED
    assert f.schedule_cron == "*/5 * * * *"
