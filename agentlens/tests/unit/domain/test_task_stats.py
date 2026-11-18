import pytest

from agentlens.domain.task_stats import (
    canonical_task_stats,
    new_task_stats,
    record_metric_skip,
    task_target_count,
)


def _stats():
    return {
        "source": {
            "total": 5,
            "invalid": 3,
            "projection_failed": 1,
        },
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


def _selector_stats():
    return {
        "type": "legacy_experiment",
        "status": "partial",
        "scanned_targets": 5,
        "scanned_users": 4,
        "selected_targets": 2,
        "selected_users": 1,
        "rejected_targets": 1,
        "rejected_users": 1,
        "invalid_targets": 1,
        "unresolved_targets": 1,
        "unresolved_users": 2,
        "total_batches": 2,
        "failed_batches": 1,
    }


def test_canonical_task_stats_accepts_source_invariant():
    stats = _stats()

    assert canonical_task_stats(stats) == stats


def test_canonical_task_stats_accepts_optional_evaluate_counter():
    stats = _stats()
    stats["diagnosers"]["dg-1"]["quality"]["evaluate"] = 1

    assert canonical_task_stats(stats) == stats


def test_skipped_counter_is_optional_and_excluded_from_total():
    stats = new_task_stats()

    record_metric_skip(stats, "dg-1", "quality")
    record_metric_skip(stats, "dg-1", "quality")

    assert stats["diagnosers"]["dg-1"]["quality"] == {
        "total": 0,
        "good": 0,
        "bad": 0,
        "failed": 0,
        "skipped": 2,
    }
    assert canonical_task_stats(stats) == stats
    assert task_target_count(stats) == 2


def test_task_target_count_uses_common_total_plus_skipped_coverage():
    stats = _stats()
    stats["diagnosers"]["dg-1"]["quality"]["skipped"] = 4
    stats["diagnosers"]["dg-2"] = {
        "safety": {"total": 3, "good": 3, "bad": 0, "failed": 0, "skipped": 2}
    }

    assert task_target_count(stats) == 5

    stats["diagnosers"]["dg-2"]["safety"]["skipped"] = 1
    with pytest.raises(ValueError, match="inconsistent metric totals"):
        task_target_count(stats)


def test_canonical_task_stats_rejects_flat_and_broken_source_invariant():
    assert canonical_task_stats({"dg-1": {"quality": {"total": 1}}}) is None
    old_source = _stats()
    old_source["source"] = {
        "rows_read": 1,
        "accepted": 1,
        "missing_trace_id": 0,
        "missing_owner_id": 0,
        "invalid_json": 0,
        "projection_attempted": 1,
        "projection_failed": 0,
    }
    assert canonical_task_stats(old_source) is None
    stats = _stats()
    stats["source"]["invalid"] = 6

    assert canonical_task_stats(stats) is None


def test_canonical_task_stats_rejects_invalid_projection_counts():
    stats = _stats()
    stats["source"]["projection_failed"] = 3
    assert canonical_task_stats(stats) is None


def test_canonical_task_stats_rejects_invalid_metric_counters():
    stats = _stats()
    stats["diagnosers"]["dg-1"]["quality"]["total"] = 2
    assert canonical_task_stats(stats) is None

    stats = _stats()
    stats["diagnosers"]["dg-1"]["quality"]["bad"] = -1
    assert canonical_task_stats(stats) is None

    for value in (True, -1, 2, "1"):
        stats = _stats()
        stats["diagnosers"]["dg-1"]["quality"]["evaluate"] = value
        assert canonical_task_stats(stats) is None

    for value in (True, -1, "1"):
        stats = _stats()
        stats["diagnosers"]["dg-1"]["quality"]["skipped"] = value
        assert canonical_task_stats(stats) is None


def test_canonical_task_stats_accepts_selector_stats_and_returns_a_copy():
    stats = _stats()
    stats["source"]["total"] = 2
    stats["source"]["invalid"] = 0
    stats["source"]["projection_failed"] = 0
    stats["selector"] = _selector_stats()

    canonical = canonical_task_stats(stats)

    assert canonical == stats
    assert canonical is not stats
    assert canonical["selector"] is not stats["selector"]


def test_canonical_task_stats_rejects_broken_selector_invariants():
    stats = _stats()
    stats["source"] = {"total": 2, "invalid": 0, "projection_failed": 0}
    stats["selector"] = _selector_stats()
    stats["selector"]["selected_targets"] = 3
    assert canonical_task_stats(stats) is None

    stats["selector"] = _selector_stats()
    stats["selector"]["status"] = "complete"
    assert canonical_task_stats(stats) is None

    stats["selector"] = _selector_stats()
    stats["source"]["total"] = 1
    assert canonical_task_stats(stats) is None


def test_v3_task_stats_preserves_frozen_metadata_and_rejects_unknown_versions():
    from tests.unit.service.test_diagnosis_stats_service import _v3_stats

    stats = _v3_stats()
    assert canonical_task_stats(stats) == stats
    stats["schema_version"] = 4
    assert canonical_task_stats(stats) is None
    stats["schema_version"] = 3
    for role in ["latency", [], {}]:
        stats["diagnoser_definitions"]["dg-old"]["score_role"] = role
        assert canonical_task_stats(stats) is None
