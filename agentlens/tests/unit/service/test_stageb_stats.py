"""Persisted Core points and exact Task selection, with external transport in memory."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentlens.domain.execution_policy import bounded_core_execution_policy
from agentlens.domain.task_stats import canonical_task_stats, new_task_stats
from agentlens.service.diagnosis_stats_service import DiagnosisStatsService
from agentlens.service.execution_snapshot import build_execution_snapshot, snapshot_definitions
from tests.unit.diagnoser.test_core_execution_policy import inputs
from tests.unit.service.test_task_stats_store import _snapshot, _store


def frozen_core_stats():
    d, job, dataset, connection = inputs()
    frozen = build_execution_snapshot(
        job,
        dataset,
        [d],
        model_connections={"connection": connection},
        execution_policy=bounded_core_execution_policy(["t"]),
    )
    stats = new_task_stats(definitions=snapshot_definitions(frozen))
    stats["source"] = {"total": 1, "invalid": 0, "projection_failed": 0}
    stats["diagnosers"] = {
        d.uuid: {"task_completion": {"total": 1, "good": 1, "bad": 0, "failed": 0}}
    }
    return stats


def test_frozen_core_terminal_writer_roundtrip_produces_real_quality_point():
    stats = frozen_core_stats()
    store, rows = _store()
    store.write_terminal_snapshots(
        task_uuid="core",
        job_uuid="j-1",
        attempt=1,
        status="succeeded",
        stats=stats,
        business_time=datetime.now() - timedelta(minutes=5),
        owner_stats={},
    )
    assert len(rows.get("agentlens_trace", [])) == 1
    loaded = store.latest_succeeded(job_uuid="j-1")
    assert loaded.stats == canonical_task_stats(stats) == stats
    result = DiagnosisStatsService(Mock(), store).list_stats(
        job=SimpleNamespace(uuid="j-1", diagnoser_uuids=[])
    )
    assert len(result["tasks"]) == 1
    assert result["tasks"][0]["global"]["score"] == 100
    assert result["tasks"][0]["task_uuid"] == "core"


@pytest.mark.parametrize(
    "key,value",
    [
        ("private_prompt", "leak"),
        ("builtin_key", 1),
        ("canonical_variant", True),
        ("prompt_version", {}),
        ("result_boundary", "x" * 513),
    ],
)
def test_core_stats_unknown_or_corrupt_metadata_stays_rejected(key, value):
    stats = frozen_core_stats()
    entry = next(iter(stats["diagnoser_definitions"].values()))
    entry[key] = value
    assert canonical_task_stats(stats) is None
    store, _ = _store()
    with pytest.raises(ValueError, match="not canonical"):
        store.write_snapshot(_snapshot("bad", stats=stats))


def test_core_optional_metadata_missing_keeps_historical_stats_readable():
    stats = frozen_core_stats()
    for entry in stats["diagnoser_definitions"].values():
        for key in ("builtin_key", "canonical_variant", "prompt_version", "result_boundary"):
            entry.pop(key, None)
    store, _ = _store()
    store.write_snapshot(_snapshot("legacy", stats=stats))
    assert store.latest_succeeded(job_uuid="j-1").stats == stats


def test_explicit_task_ignores_cutoff_and_filters_before_business_dedup():
    store, _ = _store()
    old = datetime(2000, 1, 1)
    store.write_snapshot(_snapshot("selected", period=old))
    store.write_snapshot(
        _snapshot("competitor", period=old, end_time=datetime.now() + timedelta(seconds=1))
    )
    assert store.query_succeeded(job_uuid="j-1") == []
    points = store.query_succeeded(
        job_uuid="j-1", task_uuid="selected", start_time="2099-01-01", end_time="2099-01-02"
    )
    assert [p.task_uuid for p in points] == ["selected"]
    result = DiagnosisStatsService(Mock(), store).list_stats(
        job=SimpleNamespace(uuid="j-1", diagnoser_uuids=[]), task_uuid="selected"
    )
    assert [p["task_uuid"] for p in result["tasks"]] == ["selected"]


def test_http_exact_task_checks_existence_and_job_before_stats_query():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentlens.web.deps import (
        get_diagnoser_table,
        get_job_table,
        get_task_stats_store,
        get_task_table,
    )
    from agentlens.web.errors import install_error_handlers
    from agentlens.web.routes.diagnosis_stats import router

    app = FastAPI()
    app.include_router(router)
    install_error_handlers(app)
    jobs, tasks, stats = Mock(), Mock(), Mock()
    jobs.get_by_uuid.return_value = SimpleNamespace(
        uuid="job", space_uuid="space", agent_uuid="agent", diagnoser_uuids=[]
    )
    stats.query_succeeded.return_value = []

    def override(value):
        return lambda: value

    for dependency, value in (
        (get_job_table, jobs),
        (get_task_table, tasks),
        (get_task_stats_store, stats),
        (get_diagnoser_table, Mock()),
    ):
        app.dependency_overrides[dependency] = override(value)
    with TestClient(app) as client:
        for task in (None, SimpleNamespace(job_uuid="other-job")):
            tasks.get_by_uuid.return_value = task
            response = client.get(
                "/api/v1/diagnosis-stats", params={"job_uuid": "job", "task_uuid": "task"}
            )
            assert response.status_code == 404
            stats.query_succeeded.assert_not_called()
        tasks.get_by_uuid.return_value = SimpleNamespace(job_uuid="job")
        response = client.get(
            "/api/v1/diagnosis-stats", params={"job_uuid": "job", "task_uuid": "task"}
        )
        assert response.status_code == 200 and response.json()["tasks"] == []
        assert stats.query_succeeded.call_args.kwargs["task_uuid"] == "task"


def test_persisted_task_identity_scopes_exact_http_stats(session):
    from uuid import uuid4

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentlens.infra.ob.tables.job_table import JobTable
    from agentlens.infra.ob.tables.task_table import TaskTable
    from agentlens.service.quick_runs import digest, preflight, run_quick
    from agentlens.web.deps import (
        get_diagnoser_table,
        get_job_table,
        get_task_stats_store,
        get_task_table,
    )
    from agentlens.web.errors import install_error_handlers
    from agentlens.web.routes.diagnosis_stats import router
    from tests.unit.service.test_quick_runs import scoped_payload

    traces = Mock()
    traces.read_explicit_trace_rows.return_value = [{"trace_id": "trace-a"}]
    prepared = preflight(session, traces, scoped_payload())
    owned = []
    for _ in range(2):
        owned.append(
            run_quick(
                session,
                traces,
                {
                    **prepared,
                    "receipt_uuid": uuid4().hex,
                    "request_identity": digest(uuid4().hex),
                    "input_digest": digest({"intent": 1}),
                    **{key: uuid4().hex for key in ("job_uuid", "task_uuid", "dataset_uuid")},
                },
            )
        )
    store = Mock()
    store.query_succeeded.return_value = []
    app = FastAPI()
    app.include_router(router)
    install_error_handlers(app)
    app.dependency_overrides[get_job_table] = lambda: JobTable(session)
    app.dependency_overrides[get_task_table] = lambda: TaskTable(session)
    app.dependency_overrides[get_task_stats_store] = lambda: store
    app.dependency_overrides[get_diagnoser_table] = lambda: Mock()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/diagnosis-stats",
            params={"job_uuid": owned[0]["job_uuid"], "task_uuid": owned[1]["task_uuid"]},
        )
        assert response.status_code == 404
        store.query_succeeded.assert_not_called()
        response = client.get(
            "/api/v1/diagnosis-stats",
            params={"job_uuid": owned[0]["job_uuid"], "task_uuid": owned[0]["task_uuid"]},
        )
        assert response.status_code == 200
        assert store.query_succeeded.call_args.kwargs["task_uuid"] == owned[0]["task_uuid"]
