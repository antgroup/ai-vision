from datetime import UTC, datetime

import pytest

from agentlens.domain.enums import TaskStatus
from agentlens.domain.task import Task
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.utils.uuid_gen import new_task_uuid
from agentlens.web.routes.task import list_tasks


@pytest.mark.parametrize(
    "snapshot",
    [
        {"schema_version": 2, "spec": {"api_key": "private-test-value"}},
        {"schema_version": 1, "executor_revision": "2.0.0", "diagnosers": {"future": "shape"}},
        {
            "schema_version": 1,
            "executor_revision": "1.0.0",
            "score_policy_version": "future-v2",
        },
        {
            "schema_version": 1,
            "executor_revision": "1.0.0",
            "score_policy_version": "lightweight-v1",
            "diagnosers": {"future": "shape"},
        },
    ],
)
def test_task_out_unknown_snapshot_is_safe_and_identifiable(snapshot):
    from agentlens.web.routes.task import _out

    task = Task(
        uuid="t-future",
        job_uuid="j-future",
        status=TaskStatus.PENDING,
        worker=None,
        scheduled_at=datetime.now(UTC),
        started_at=None,
        finished_at=None,
        attempt=0,
        msg="",
        gmt_create=datetime.now(UTC),
        gmt_modified=datetime.now(UTC),
        creator=None,
        modifier=None,
        execution_snapshot=snapshot,
    )
    result = _out(task)
    assert result.definition_source == "unsupported_snapshot"
    assert result.diagnoser_definitions == {}
    assert "private-test-value" not in result.model_dump_json()


def test_known_schema_historical_revision_exposes_only_safe_definition_metadata():
    from agentlens.service.execution_snapshot import restore_execution_snapshot
    from agentlens.web.routes.task import _out

    snapshot = {
        "schema_version": 1,
        "executor_revision": "0.9.0",
        "score_policy_version": "lightweight-v1",
        "diagnosers": [
            {
                "uuid": "dg-old",
                "key": "old_rule",
                "name": "Historical label",
                "version": "v-old",
                "revision": "0.9.0",
                "digest": "old-digest",
                "spec": {
                    "prompt": "private prompt",
                    "model": {"api_key": "private-key"},
                    "category": "outcome",
                    "weight": 1,
                    "score_role": "quality",
                    "metrics": [{"name": "old_metric", "desc": "Old metric"}],
                },
            }
        ],
    }
    now = datetime.now(UTC)
    task = Task(
        uuid="t-old",
        job_uuid="j-old",
        status=TaskStatus.SUCCEEDED,
        worker=None,
        scheduled_at=now,
        started_at=None,
        finished_at=now,
        attempt=1,
        msg="",
        gmt_create=now,
        gmt_modified=now,
        creator=None,
        modifier=None,
        execution_snapshot=snapshot,
    )
    result = _out(task)
    assert result.definition_source == "task_snapshot"
    assert result.diagnoser_definitions["dg-old"]["name"] == "Historical label"
    assert result.diagnoser_definitions["dg-old"]["revision"] == "0.9.0"
    assert "private" not in result.model_dump_json()
    with pytest.raises(Exception, match="unsupported executor revision"):
        restore_execution_snapshot(snapshot)
    snapshot["diagnosers"][0]["spec"]["category"] = {"api_key": "hidden-key"}
    invalid = _out(task)
    assert invalid.definition_source == "unsupported_snapshot"
    assert "hidden-key" not in invalid.model_dump_json()


def _seed(session, status=TaskStatus.PENDING, name_suffix=None, stats=None) -> str:
    import random

    from agentlens.domain.enums import JobStatus, JobTriggerType
    from agentlens.domain.job import Job
    from agentlens.infra.ob.tables.job_table import JobTable
    from agentlens.utils.uuid_gen import new_job_uuid

    suffix = name_suffix or f"{random.randint(10000, 99999)}"
    job = Job(
        uuid=new_job_uuid(),
        space_uuid="s",
        agent_uuid="a",
        name=f"test-job-{suffix}",
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        dataset_uuid="ds-aaa",
        diagnoser_uuids=[],
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    JobTable(session).insert(job)
    t = Task(
        uuid=new_task_uuid(),
        job_uuid=job.uuid,
        status=status,
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
        stats=stats,
    )
    TaskTable(session).insert(t)
    return t.uuid


def test_get_task(client, session):
    uid = _seed(session)
    r = client.get(f"/api/v1/task/{uid}")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_get_task_returns_stats_without_validation(client, session):
    stats = {"metric_a": {"total": 2, "good": 1, "bad": 1}}
    uid = _seed(
        session,
        stats=stats,
    )

    response = client.get(f"/api/v1/task/{uid}")

    assert response.status_code == 200
    assert response.json()["stats"] == stats


def test_list_tasks_returns_stats_without_validation(client, session):
    stats = {"legacy": {"processed": 3}}
    uid = _seed(session, stats=stats)
    job_uuid = TaskTable(session).get_by_uuid(uid).job_uuid

    response = client.get("/api/v1/tasks", params={"job_uuid": job_uuid})

    assert response.status_code == 200
    assert response.json()["items"][0]["stats"] == stats


def test_get_task_returns_canonical_stats(client, session):
    uid = _seed(
        session,
        stats={
            "source": {},
            "diagnosers": {
                "dg-1": {
                    "quality": {
                        "total": 1,
                        "good": 1,
                        "bad": 0,
                        "failed": 0,
                    }
                }
            },
        },
    )

    response = client.get(f"/api/v1/task/{uid}", params={"space_uuid": "s", "agent_uuid": "a"})

    assert response.status_code == 200
    assert response.json()["stats"] == {
        "source": {},
        "diagnosers": {
            "dg-1": {
                "quality": {
                    "total": 1,
                    "good": 1,
                    "bad": 0,
                    "failed": 0,
                }
            }
        },
    }


def test_get_task_404(client):
    assert client.get("/api/v1/task/t-deadbeef").status_code == 404


def test_cancel_pending_task(client, session):
    stats = {"partial": True}
    uid = _seed(session, stats=stats)
    r = client.post(f"/api/v1/task/{uid}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "canceled"
    assert r.json()["stats"] == stats


def test_cancel_running_returns_409(client, session):
    uid = _seed(session, status=TaskStatus.RUNNING)
    r = client.post(f"/api/v1/task/{uid}/cancel")
    assert r.status_code == 409
    assert TaskTable(session).get_by_uuid(uid).status == TaskStatus.RUNNING


def test_cancel_terminal_status_returns_409(client, session):
    uid = _seed(session, status=TaskStatus.SUCCEEDED)
    r = client.post(f"/api/v1/task/{uid}/cancel")
    assert r.status_code == 409


def test_list_tasks_requires_job_uuid(client):
    response = client.get("/api/v1/tasks")

    assert response.status_code == 422


def test_list_tasks_filters_by_job(client, session):
    a = _seed(session)
    b = _seed(session, status=TaskStatus.RUNNING)
    job_uuid = TaskTable(session).get_by_uuid(a).job_uuid

    body = client.get("/api/v1/tasks", params={"job_uuid": job_uuid}).json()
    uuids = {it["uuid"] for it in body["items"]}

    assert a in uuids
    assert b not in uuids


def test_list_tasks_filter_by_status(client, session):
    a = _seed(session)  # PENDING
    job_uuid = TaskTable(session).get_by_uuid(a).job_uuid

    body = client.get(
        "/api/v1/tasks",
        params={"job_uuid": job_uuid, "status": "pending"},
    ).json()
    uuids = {it["uuid"] for it in body["items"]}

    assert a in uuids

    running = client.get(
        "/api/v1/tasks",
        params={"job_uuid": job_uuid, "status": "running"},
    ).json()
    assert running["items"] == []


def test_list_tasks_filters_by_job_before_applying_limit():
    target_job_uuid = "j-target"
    target_task = Task(
        uuid="t-target",
        job_uuid=target_job_uuid,
        status=TaskStatus.SUCCEEDED,
        worker=None,
        scheduled_at=datetime.now(UTC),
        started_at=None,
        finished_at=None,
        attempt=0,
        msg="",
        gmt_create=datetime.now(UTC),
        gmt_modified=datetime.now(UTC),
        creator=None,
        modifier=None,
    )

    class FakeTasks:
        def list_tasks(self, **kwargs):
            return [target_task] if kwargs.get("job_uuid") == target_job_uuid else []

        def count_tasks(self, **kwargs):
            return 1 if kwargs.get("job_uuid") == target_job_uuid else 0

    response = list_tasks(FakeTasks(), job_uuid=target_job_uuid, limit=10, offset=0)

    assert [item.uuid for item in response.items] == [target_task.uuid]
    assert response.total == 1


def test_list_tasks_paginates_and_returns_full_total(client, session):
    first_uuid = _seed(session)
    table = TaskTable(session)
    job_uuid = table.get_by_uuid(first_uuid).job_uuid
    for index in range(24):
        table.insert(
            Task(
                uuid=new_task_uuid(),
                job_uuid=job_uuid,
                status=TaskStatus.SUCCEEDED,
                worker=None,
                scheduled_at=datetime.now(UTC),
                started_at=None,
                finished_at=None,
                attempt=0,
                msg=f"task-{index}",
                gmt_create=None,
                gmt_modified=None,
                creator=None,
                modifier=None,
            )
        )

    first_page = client.get("/api/v1/tasks", params={"job_uuid": job_uuid}).json()
    second_page = client.get(
        "/api/v1/tasks",
        params={"job_uuid": job_uuid, "limit": 20, "offset": 20},
    ).json()

    assert first_page["total"] == 25
    assert len(first_page["items"]) == 20
    assert second_page["total"] == 25
    assert len(second_page["items"]) == 5
