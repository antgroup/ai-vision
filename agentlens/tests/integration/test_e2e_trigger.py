import time

import agentlens.diagnoser  # noqa: F401 — auto-register executors
from agentlens.domain.dataset import Dataset
from agentlens.domain.diagnoser import Diagnoser
from agentlens.domain.enums import (
    DatasetType,
    DiagnoserMode,
    DiagnoserType,
    JobStatus,
    JobTriggerType,
)
from agentlens.domain.job import Job
from agentlens.infra.ob.tables.dataset_table import DatasetTable
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.utils.uuid_gen import new_dataset_uuid, new_diagnoser_uuid, new_job_uuid


def _seed_world(session, signoz_inmem):
    diag = Diagnoser(
        uuid=new_diagnoser_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="test-fixed",
        description="",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        spec={"executor": "test_fixed", "metrics": [{"name": "quality"}]},
        version="v1",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    DiagnoserTable(session).insert(diag)
    ds = Dataset(
        uuid=new_dataset_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="ds",
        description="",
        type=DatasetType.SIGNOZ,
        spec={
            "runtime_type": "custom-runtime",
            "query": {"table": "trace", "where": "`agent_uuid` = 'A'"},
        },
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    DatasetTable(session).insert(ds)
    job = Job(
        uuid=new_job_uuid(),
        space_uuid="s",
        agent_uuid="A",
        name="j",
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        dataset_uuid=ds.uuid,
        diagnoser_uuids=[diag.uuid],
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
        spec={"save_badcase_only": False},
    )
    JobTable(session).insert(job)
    signoz_inmem.seed(
        "trace",
        [{"trace_id": "T1", "agent_uuid": "A", "steps": ["plan", "answer"]}],
    )
    return job


def test_trigger_then_execute_writes_record(client, session, signoz_inmem):
    job = _seed_world(session, signoz_inmem)
    scope = {"space_uuid": "s", "agent_uuid": "A"}
    r = client.post(f"/api/v1/job/{job.uuid}/trigger")
    assert r.status_code == 202
    task_uuid = r.json()["task_uuid"]

    worker_id = "test-host/127.0.0.1"
    assert TaskTable(session).cas_dispatch(task_uuid, worker=worker_id)

    r = client.post("/internal/execute", json={"task_uuid": task_uuid, "fenced_token": 1})
    assert r.status_code == 202

    time.sleep(0.5)

    body = client.get(f"/api/v1/task/{task_uuid}", params=scope).json()
    assert body["status"] == "succeeded"

    rec_list = client.get(
        "/api/v1/diagnosis-records", params={**scope, "job_uuid": job.uuid}
    ).json()
    assert rec_list["total"] == 1
    assert rec_list["items"][0]["target_id"] == "T1"
