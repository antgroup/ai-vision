from datetime import datetime
from types import SimpleNamespace

import pytest

from agentlens.domain.dataset import Dataset
from agentlens.domain.enums import DatasetType, TaskStatus
from agentlens.kernel.worker.runner import Runner


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("owns_attempt", [False, True])
def test_signoz_terminal_snapshot_requires_attempt_ownership(fails, owns_attempt):
    task = SimpleNamespace(
        uuid="t-1",
        job_uuid="j-1",
        attempt=1,
        scheduled_at=datetime(2026, 8, 5, 10),
        status=TaskStatus.RUNNING,
    )
    dataset = Dataset(
        uuid="ds-1",
        space_uuid="s",
        agent_uuid="a",
        name="signoz",
        description="",
        type=DatasetType.SIGNOZ,
        spec={
            "runtime_type": "cc",
            "query": {"table": "signoz_traces", "time_range": "1h", "time_offset": "3h"},
        },
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    job = SimpleNamespace(uuid="j-1", dataset_uuid="ds-1", diagnoser_uuids=[], space_uuid="s")
    snapshots, terminal = [], []

    def finish(*args, **kwargs):
        terminal.append(kwargs)
        return owns_attempt

    def read(*args, **kwargs):
        if fails:
            raise RuntimeError("query unavailable")
        return [], []

    runner = Runner(
        task_table=SimpleNamespace(
            cas_start_running=lambda *a, **kw: True,
            get_by_uuid=lambda _: task,
            mark_succeeded=finish,
            mark_failed=finish,
        ),
        job_table=SimpleNamespace(get_by_uuid=lambda _: job),
        dataset_table=SimpleNamespace(get_by_uuid=lambda _: dataset),
        diagnoser_table=SimpleNamespace(get_by_uuid=lambda _: None),
        trace_store=SimpleNamespace(read_trace_targets=read),
        record_store=SimpleNamespace(write_record=lambda *a, **kw: None),
        task_stats_store=SimpleNamespace(
            write_terminal_snapshots=lambda **kw: snapshots.append(kw)
        ),
        self_uuid="worker",
    )
    runner._run_sync(task.uuid)
    assert terminal
    assert len(snapshots) == int(owns_attempt)
    if owns_attempt:
        assert snapshots[0]["status"] == ("failed" if fails else "succeeded")
        assert snapshots[0]["business_time"] == datetime(2026, 8, 5, 6, 30)
        assert snapshots[0]["stats"]["source"] == {"total": 0, "invalid": 0, "projection_failed": 0}


@pytest.mark.parametrize("source_key", ["invalid", "projection_failed"])
def test_v3_owner_snapshots_keep_global_source_incompleteness(source_key):
    from agentlens.domain.diagnosis_record import DiagnosisResult, Execution, Metric
    from agentlens.domain.enums import Verdict
    from agentlens.kernel.worker.runner import _RunAcc

    snapshot = {
        "schema_version": 1,
        "executor_revision": "1.0.0",
        "score_policy_version": "lightweight-v1",
        "diagnosers": [
            {
                "uuid": "dg",
                "key": "test",
                "name": "Test",
                "version": "v1",
                "revision": "1.0.0",
                "digest": "a" * 64,
                "spec": {
                    "category": "outcome",
                    "weight": 1,
                    "score_role": "quality",
                    "metrics": [{"name": "quality"}],
                },
            }
        ],
    }
    acc = _RunAcc(owner_enabled=True, execution_snapshot=snapshot)
    result = DiagnosisResult(
        metrics=[Metric(name="quality", verdict=Verdict.GOOD)], execution=Execution.succeeded()
    )
    acc.record(result, "dg", owner_id="owner-before")
    acc.record_source(source_key)
    acc.record(result, "dg", owner_id="owner-after")
    assert acc.stats["source_complete"] is False
    assert all(owner["source_complete"] is False for owner in acc.owner_stats.values())
