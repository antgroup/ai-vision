from datetime import UTC, datetime

from agentlens.domain.diagnoser import Diagnoser
from agentlens.domain.diagnosis_record import DiagnosisRecord, Execution, Metric
from agentlens.domain.enums import (
    DiagnoserMode,
    DiagnoserType,
    JobStatus,
    JobTriggerType,
    ReviewStatus,
    TargetType,
    TaskStatus,
    Verdict,
)
from agentlens.domain.heartbeat import Heartbeat
from agentlens.domain.job import Job
from agentlens.domain.task import Task


def test_diagnoser_fields():
    d = Diagnoser(
        uuid="dg-12345678",
        space_uuid="s1",
        agent_uuid="a1",
        name="n",
        description="",
        type=DiagnoserType.BUILTIN,
        mode=DiagnoserMode.RULE,
        spec={},
        version="v1",
        gmt_create=datetime.now(UTC),
        gmt_modified=datetime.now(UTC),
        creator="u1",
        modifier="u1",
    )
    assert d.uuid == "dg-12345678"
    assert d.type == DiagnoserType.BUILTIN


def test_job_holds_diagnoser_uuids_list():
    j = Job(
        uuid="j-12345678",
        space_uuid="s",
        agent_uuid="a",
        name="n",
        description="",
        trigger_type=JobTriggerType.SCHEDULE,
        schedule_cron="0 * * * *",
        status=JobStatus.ENABLED,
        dataset_uuid="ds-aaaa1111",
        diagnoser_uuids=["dg-bbbb2222", "dg-cccc3333"],
        gmt_create=datetime.now(UTC),
        gmt_modified=datetime.now(UTC),
        creator="u",
        modifier="u",
    )
    assert len(j.diagnoser_uuids) == 2


def test_task_pending_initial_state():
    t = Task(
        uuid="t-12345678",
        job_uuid="j-12345678",
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
    )
    assert t.worker is None
    assert t.attempt == 0


def test_heartbeat_has_no_audit_user_fields():
    hb = Heartbeat(
        uuid="h/i",
        hostname="h",
        ip="i",
        gmt_create=datetime.now(UTC),
        gmt_modified=datetime.now(UTC),
    )
    assert hb.uuid == "h/i"


def test_diagnosis_record_target_split():
    r = DiagnosisRecord(
        uuid="r-12345678",
        task_uuid="t-1",
        job_uuid="j-1",
        diagnoser_uuid="dg-1",
        diagnoser_version="v1",
        target_type=TargetType.TRACE,
        target_id="trace-abc",
        metrics=[Metric(name="quality", verdict=Verdict.BAD, reason="bad")],
        execution=Execution.succeeded(),
        trace_snapshot={},
        review_status=ReviewStatus.PENDING,
        feedback=None,
        reviewed_by=None,
        reviewed_at=None,
        diagnosed_at=datetime.now(UTC),
        target_created_at="",
    )
    assert r.target_type == TargetType.TRACE
    assert r.target_id == "trace-abc"
