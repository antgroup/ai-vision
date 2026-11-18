"""Unified diagnosis statistics HTTP contract over TaskStats snapshots."""

from datetime import datetime, timedelta

import pytest

from agentlens.infra.antcollector import InMemoryAntcollectorClient
from agentlens.service.task_stats_store import TaskStatsSnapshot, TaskStatsStore


def _job_params(job_uuid, **extra):
    return {"job_uuid": job_uuid, **extra}


def _task_stats(diagnoser_uuid, metric_key, *, total=10, good=8, bad=2, failed=0):
    return {
        "source": {},
        "diagnosers": {
            diagnoser_uuid: {
                metric_key: {
                    "total": total,
                    "good": good,
                    "bad": bad,
                    "failed": failed,
                }
            }
        },
    }


def _seed_snapshot(
    signoz_inmem,
    *,
    task_uuid,
    job_uuid,
    diagnoser_uuid,
    metric_key,
    **counters,
):
    from agentlens.web import deps

    settings = deps.get_settings()
    TaskStatsStore(
        signoz_inmem,
        InMemoryAntcollectorClient(signoz_inmem._tables),
        table=settings.diagnosis_record_table,
    ).write_snapshot(
        TaskStatsSnapshot(
            task_uuid=task_uuid,
            job_uuid=job_uuid,
            attempt=1,
            status="succeeded",
            stats_scope="global",
            owner_id="",
            stats=_task_stats(diagnoser_uuid, metric_key, **counters),
            period=datetime.now() - timedelta(minutes=5),
            end_time=datetime.now(),
        )
    )


def _make_single_diagnoser(client, signoz_inmem, *, counters=None):
    metric_key = "output_achievement_rate"
    response = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": metric_key,
            "type": "custom",
            "mode": "rule",
            "spec": {
                "category": "outcome",
                "weight": 0.1,
                "code": (
                    "def evaluate(trace):\n"
                    f"    return {{'metrics': [{{'name': '{metric_key}', "
                    "'verdict': 'good', 'reason': 'ok', 'evidence': {}}]}\n"
                ),
                "metrics": [
                    {
                        "name": metric_key,
                        "desc": "产出达成率",
                    }
                ],
            },
            "version": "v2",
        },
    )
    assert response.status_code == 201, response.text
    diagnoser_uuid = response.json()["uuid"]
    dataset_uuid = client.post(
        "/api/v1/dataset",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "stats-dataset",
            "type": "signoz",
            "spec": {
                "query": {"table": "t"},
                "runtime_type": "custom-runtime",
            },
        },
    ).json()["uuid"]
    job_response = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "stats-job",
            "dataset_uuid": dataset_uuid,
            "diagnoser_uuids": [diagnoser_uuid],
            "trigger_type": "manual",
        },
    )
    assert job_response.status_code == 201, job_response.text
    job_uuid = job_response.json()["uuid"]
    if counters is not None:
        _seed_snapshot(
            signoz_inmem,
            task_uuid="task-stats",
            job_uuid=job_uuid,
            diagnoser_uuid=diagnoser_uuid,
            metric_key=metric_key,
            **counters,
        )
    return diagnoser_uuid, job_uuid


def test_diagnosis_stats_returns_task_oriented_nested_statistics(client, signoz_inmem):
    diagnoser_uuid, job_uuid = _make_single_diagnoser(
        client,
        signoz_inmem,
        counters={"total": 10, "good": 8, "bad": 1, "failed": 1},
    )

    response = client.get("/api/v1/diagnosis-stats", params=_job_params(job_uuid))

    assert response.status_code == 200, response.text
    assert [category["id"] for category in response.json()["categories"]] == [
        "outcome",
        "process",
        "safety",
        "system",
    ]
    task = response.json()["tasks"][0]
    assert task["task_uuid"] == "task-stats"
    assert task["target_type"] == "trace"
    assert task["target_count"] == 10
    assert task["schema_version"] is None
    assert task["source_complete"] is None
    assert task["definition_source"] == "legacy_current_definition"
    assert task["global"]["score"] == 88.9
    outcome = task["global"]["categories"][0]
    assert outcome["id"] == "outcome"
    assert outcome["score"] == 88.9
    assert outcome["diagnosers"] == [
        {
            "uuid": diagnoser_uuid,
            "name": "output_achievement_rate",
            "weight": 0.1,
            "score": 88.9,
            "score_role": None,
            "version": None,
            "revision": None,
            "digest": None,
            "key": None,
            "metrics": [
                {
                    "id": "output_achievement_rate",
                    "name": "产出达成率",
                    "score": 88.9,
                    "total": 10,
                    "good": 8,
                    "bad": 1,
                    "failed": 1,
                    "skipped": 0,
                }
            ],
        }
    ]


def test_diagnosis_stats_returns_category_skeleton_without_tasks(client, signoz_inmem):
    _, job_uuid = _make_single_diagnoser(client, signoz_inmem)

    response = client.get("/api/v1/diagnosis-stats", params=_job_params(job_uuid))

    assert response.status_code == 200, response.text
    assert response.json()["tasks"] == []
    assert response.json()["categories"] == [
        {"id": "outcome", "name": "结果质量"},
        {"id": "process", "name": "过程质量"},
        {"id": "safety", "name": "安全合规"},
        {"id": "system", "name": "系统性能"},
    ]


def test_diagnosis_stats_requires_existing_job_and_valid_time_range(client, signoz_inmem):
    _, job_uuid = _make_single_diagnoser(client, signoz_inmem)

    missing = client.get("/api/v1/diagnosis-stats", params={"job_uuid": "missing"})
    invalid = client.get(
        "/api/v1/diagnosis-stats",
        params={
            "job_uuid": job_uuid,
            "start_time": "2026-08-07T12:00:00",
            "end_time": "2026-08-07T12:00:00",
        },
    )

    assert missing.status_code == 404
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_time_range"


@pytest.mark.parametrize("source_complete", [True, False])
def test_v3_api_keeps_history_after_current_job_unbinds_diagnosers(
    client, signoz_inmem, source_complete
):
    from agentlens.web import deps
    from tests.unit.service.test_diagnosis_stats_service import _v3_stats

    _, job_uuid = _make_single_diagnoser(client, signoz_inmem)
    store = TaskStatsStore(
        signoz_inmem,
        InMemoryAntcollectorClient(signoz_inmem._tables),
        table=deps.get_settings().diagnosis_record_table,
    )
    stats = _v3_stats()
    stats["source_complete"] = source_complete
    if not source_complete:
        stats["source"]["invalid"] = 1
    store.write_snapshot(
        TaskStatsSnapshot(
            task_uuid="t-v3",
            job_uuid=job_uuid,
            attempt=1,
            status="succeeded",
            stats_scope="global",
            owner_id="",
            stats=stats,
            period=datetime.now() - timedelta(minutes=5),
            end_time=datetime.now(),
        )
    )
    patched = client.patch(f"/api/v1/job/{job_uuid}", json={"diagnoser_uuids": []})
    assert patched.status_code == 200
    response = client.get("/api/v1/diagnosis-stats", params=_job_params(job_uuid))
    assert response.status_code == 200
    task = response.json()["tasks"][0]
    assert task["schema_version"] == 3
    assert task["source_complete"] is source_complete
    assert task["global"]["score"] == (50.0 if source_complete else None)
    assert task["score_policy_version"] == "lightweight-v1"
    assert task["definition_source"] == "task_snapshot"
    diag = task["global"]["categories"][0]["diagnosers"][0]
    assert diag["name"] == "Frozen name"
    assert diag["version"] == "v1"
    assert diag["score_role"] == "quality"
    assert diag["metrics"][0]["name"] == "Frozen metric"
