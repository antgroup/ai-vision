from datetime import datetime
from types import SimpleNamespace

import pytest

from agentlens.service.diagnosis_stats_service import DiagnosisStatsService
from agentlens.service.task_stats_store import TaskStatsSnapshot


class FakeDiagnosers:
    def __init__(self, items):
        self.items = {item.uuid: item for item in items}

    def get_by_uuid(self, uuid):
        return self.items.get(uuid)


class FakeTaskStats:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.calls = []

    def query_succeeded(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.snapshots)


def _diagnoser(uuid, name, category, weight, metrics):
    return SimpleNamespace(
        uuid=uuid,
        name=name,
        spec={
            "category": category,
            "weight": weight,
            "metrics": metrics,
        },
    )


def _snapshot(task_uuid, period, stats):
    return TaskStatsSnapshot(
        task_uuid=task_uuid,
        job_uuid="j-1",
        attempt=1,
        status="succeeded",
        stats_scope="global",
        owner_id="",
        stats=stats,
        period=period,
    )


def test_lists_one_nested_point_per_task_from_one_snapshot_query():
    first = _diagnoser(
        "dg-1",
        "完成质量",
        "outcome",
        1,
        [
            {"name": "goal", "desc": "目标达成"},
            {"name": "grounding", "desc": "事实依据"},
            {"name": "missing", "desc": "新增指标"},
        ],
    )
    second = _diagnoser(
        "dg-2",
        "格式质量",
        "outcome",
        3,
        [{"name": "format", "desc": "格式正确"}],
    )
    stats = {
        "source": {},
        "selector": {
            "type": "legacy_experiment",
            "status": "partial",
            "scanned_targets": 12,
            "scanned_users": 11,
            "selected_targets": 10,
            "selected_users": 9,
            "rejected_targets": 1,
            "rejected_users": 1,
            "invalid_targets": 0,
            "unresolved_targets": 1,
            "unresolved_users": 1,
            "total_batches": 2,
            "failed_batches": 1,
        },
        "diagnosers": {
            "dg-1": {
                "legacy_internal": {
                    "total": 10,
                    "good": 0,
                    "bad": 10,
                    "failed": 0,
                },
                "goal": {
                    "total": 10,
                    "good": 8,
                    "bad": 2,
                    "failed": 0,
                    "evaluate": 3,
                    "skipped": 0,
                },
                "grounding": {"total": 10, "good": 5, "bad": 5, "failed": 0},
            },
            "dg-2": {
                "format": {"total": 10, "good": 9, "bad": 0, "failed": 1},
            },
        },
    }
    snapshots = [
        _snapshot("t-new", datetime(2026, 8, 7, 11), stats),
        _snapshot("t-old", datetime(2026, 8, 7, 9), stats),
    ]
    store = FakeTaskStats(snapshots)
    service = DiagnosisStatsService(FakeDiagnosers([first, second]), store)
    job = SimpleNamespace(uuid="j-1", diagnoser_uuids=["dg-1", "dg-2"])

    response = service.list_stats(
        job=job,
        owner_id="owner-1",
        start_time="2026-08-07T08:00:00",
        end_time="2026-08-07T12:00:00",
    )

    assert len(store.calls) == 1
    assert store.calls[0] == {
        "job_uuid": "j-1",
        "owner_id": "owner-1",
        "start_time": "2026-08-07T08:00:00",
        "end_time": "2026-08-07T12:00:00",
    }
    assert response["categories"] == [
        {"id": "outcome", "name": "结果质量"},
        {"id": "process", "name": "过程质量"},
        {"id": "safety", "name": "安全合规"},
        {"id": "system", "name": "系统性能"},
    ]
    assert [task["task_uuid"] for task in response["tasks"]] == ["t-old", "t-new"]

    task = response["tasks"][0]
    assert task["time"] == datetime(2026, 8, 7, 9)
    assert task["target_type"] == "trace"
    assert task["target_count"] == 10
    assert task["selector"] == stats["selector"]
    assert task["global"]["score"] == 91.2
    assert [category["id"] for category in task["global"]["categories"]] == [
        "outcome",
        "process",
        "safety",
        "system",
    ]

    outcome = task["global"]["categories"][0]
    assert outcome["score"] == 91.2
    assert [diagnoser["uuid"] for diagnoser in outcome["diagnosers"]] == ["dg-1", "dg-2"]
    assert outcome["diagnosers"][0]["score"] == 65.0
    assert outcome["diagnosers"][1]["score"] == 100.0
    assert [metric["score"] for metric in outcome["diagnosers"][0]["metrics"]] == [
        80.0,
        50.0,
        None,
    ]
    assert outcome["diagnosers"][0]["metrics"][2] == {
        "id": "missing",
        "name": "新增指标",
        "score": None,
        "total": 0,
        "good": 0,
        "bad": 0,
        "failed": 0,
        "skipped": 0,
    }
    assert all(
        category["score"] is None and category["diagnosers"] == []
        for category in task["global"]["categories"][1:]
    )


def test_keeps_task_when_all_metric_scores_are_empty():
    diagnoser = _diagnoser(
        "dg-1",
        "质量",
        "process",
        1,
        [{"name": "quality", "desc": "质量指标"}],
    )
    snapshot = _snapshot(
        "t-empty",
        datetime(2026, 8, 7, 10),
        {
            "source": {},
            "diagnosers": {
                "dg-1": {
                    "quality": {"total": 3, "good": 0, "bad": 0, "failed": 3},
                }
            },
        },
    )
    service = DiagnosisStatsService(FakeDiagnosers([diagnoser]), FakeTaskStats([snapshot]))
    job = SimpleNamespace(uuid="j-1", diagnoser_uuids=["dg-1"])

    task = service.list_stats(job=job)["tasks"][0]

    assert task["target_count"] == 3
    assert task["global"]["score"] is None
    process = task["global"]["categories"][1]
    assert process["score"] is None
    assert process["diagnosers"][0]["score"] is None
    assert process["diagnosers"][0]["metrics"][0]["failed"] == 3
    assert process["diagnosers"][0]["metrics"][0]["skipped"] == 0


@pytest.mark.parametrize("category", [None, "", "outcomes", "reasoning", "unknown"])
def test_non_standard_diagnoser_category_falls_back_to_outcome(category):
    diagnoser = _diagnoser(
        "dg-1",
        "质量",
        category,
        1,
        [{"name": "quality", "desc": "质量指标"}],
    )
    snapshot = _snapshot(
        "t-fallback",
        datetime(2026, 8, 7, 10),
        {
            "source": {},
            "diagnosers": {
                "dg-1": {
                    "quality": {"total": 1, "good": 1, "bad": 0, "failed": 0},
                }
            },
        },
    )
    service = DiagnosisStatsService(FakeDiagnosers([diagnoser]), FakeTaskStats([snapshot]))
    job = SimpleNamespace(uuid="j-1", diagnoser_uuids=["dg-1"])

    task = service.list_stats(job=job)["tasks"][0]

    assert [item["uuid"] for item in task["global"]["categories"][0]["diagnosers"]] == ["dg-1"]


def test_returns_category_skeleton_when_no_task_stats_exist():
    service = DiagnosisStatsService(FakeDiagnosers([]), FakeTaskStats([]))
    job = SimpleNamespace(uuid="j-1", diagnoser_uuids=[])

    response = service.list_stats(job=job)

    assert response["tasks"] == []
    assert [category["id"] for category in response["categories"]] == [
        "outcome",
        "process",
        "safety",
        "system",
    ]


def _v3_stats():
    return {
        "schema_version": 3,
        "score_policy_version": "lightweight-v1",
        "source_complete": True,
        "skip_reasons": {},
        "diagnoser_definitions": {
            "dg-old": {
                "uuid": "dg-old",
                "key": "old",
                "name": "Frozen name",
                "category": "outcome",
                "weight": 2,
                "score_role": "quality",
                "version": "v1",
                "revision": "1.0.0",
                "digest": "a" * 64,
                "metrics": [{"name": "quality", "desc": "Frozen metric"}],
            }
        },
        "source": {"total": 2, "invalid": 0, "projection_failed": 0},
        "diagnosers": {"dg-old": {"quality": {"total": 2, "good": 1, "bad": 1, "failed": 0}}},
    }


def test_v3_history_ignores_current_job_bindings():
    stats = _v3_stats()
    service = DiagnosisStatsService(
        FakeDiagnosers([]), FakeTaskStats([_snapshot("t-frozen", datetime(2026, 10, 9, 10), stats)])
    )
    task = service.list_stats(job=SimpleNamespace(uuid="j-1", diagnoser_uuids=[]))["tasks"][0]
    diag = task["global"]["categories"][0]["diagnosers"][0]
    assert task["schema_version"] == 3
    assert task["definition_source"] == "task_snapshot"
    assert task["global"]["score"] == 50.0
    assert diag["name"] == "Frozen name"
    assert diag["weight"] == 2
    assert diag["metrics"][0]["name"] == "Frozen metric"


@pytest.mark.parametrize(
    "cause", ["skipped", "failed", "incomplete", "signal", "missing", "projection"]
)
def test_v3_incomplete_quality_has_no_aggregate_score(cause):
    stats = _v3_stats()
    counters = stats["diagnosers"]["dg-old"]["quality"]
    if cause == "skipped":
        counters["skipped"] = 1
    if cause == "failed":
        counters.update(total=3, failed=1)
    if cause == "projection":
        stats["source"]["projection_failed"] = 1
    if cause == "incomplete":
        stats["source_complete"] = False
    if cause == "signal":
        stats["diagnoser_definitions"]["dg-old"]["score_role"] = "signal"
    if cause == "missing":
        stats["diagnoser_definitions"]["dg-old"]["metrics"].append({"name": "missing"})
    service = DiagnosisStatsService(
        FakeDiagnosers([]), FakeTaskStats([_snapshot("t-frozen", datetime(2026, 10, 9, 10), stats)])
    )
    task = service.list_stats(job=SimpleNamespace(uuid="j-1", diagnoser_uuids=[]))["tasks"][0]
    assert task["global"]["score"] is None
    diag = task["global"]["categories"][0]["diagnosers"][0]
    assert diag["metrics"][0]["good"] == 1
    assert diag["metrics"][0]["bad"] == 1


def test_v3_partial_counter_coverage_keeps_counts_without_quality_score():
    stats = _v3_stats()
    stats["source"]["total"] = 3
    service = DiagnosisStatsService(
        FakeDiagnosers([]),
        FakeTaskStats([_snapshot("t-partial", datetime(2026, 10, 9, 10), stats)]),
    )
    task = service.list_stats(job=SimpleNamespace(uuid="j-1", diagnoser_uuids=[]))["tasks"][0]
    assert task["target_count"] == 3
    assert task["global"]["score"] is None
    assert task["global"]["categories"][0]["diagnosers"][0]["metrics"][0]["good"] == 1


def test_v3_signal_does_not_hide_incomplete_quality_in_another_category():
    stats = _v3_stats()
    from copy import deepcopy

    signal = deepcopy(stats["diagnoser_definitions"]["dg-old"])
    signal.update(uuid="dg-signal", category="system", score_role="signal")
    stats["diagnoser_definitions"]["dg-signal"] = signal
    stats["diagnosers"]["dg-signal"] = deepcopy(stats["diagnosers"]["dg-old"])
    stats["diagnosers"]["dg-old"]["quality"]["skipped"] = 1
    service = DiagnosisStatsService(
        FakeDiagnosers([]), FakeTaskStats([_snapshot("t-signal", datetime(2026, 10, 9, 10), stats)])
    )
    task = service.list_stats(job=SimpleNamespace(uuid="j-1", diagnoser_uuids=[]))["tasks"][0]
    assert task["global"]["score"] is None
    assert task["global"]["categories"][3]["score"] is None
