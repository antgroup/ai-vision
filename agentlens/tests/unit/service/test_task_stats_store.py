import json
from datetime import datetime, timedelta

import pytest

from agentlens.domain.errors import InvalidTimeRange
from agentlens.infra.antcollector import InMemoryAntcollectorClient
from agentlens.infra.signoz.client import InMemorySignozClient
from agentlens.service.task_stats_store import (
    TaskStatsSnapshot,
    TaskStatsStore,
    resolve_task_stats_window,
)


def _stats(*, total=2, good=1, bad=1, failed=0, source=None):
    return {
        "source": source or {},
        "diagnosers": {
            "dg-1": {
                "quality": {
                    "total": total,
                    "good": good,
                    "bad": bad,
                    "failed": failed,
                }
            }
        },
    }


def _selector_stats():
    return {
        "type": "legacy_experiment",
        "status": "complete",
        "scanned_targets": 3,
        "scanned_users": 3,
        "selected_targets": 2,
        "selected_users": 2,
        "rejected_targets": 1,
        "rejected_users": 1,
        "invalid_targets": 0,
        "unresolved_targets": 0,
        "unresolved_users": 0,
        "total_batches": 1,
        "failed_batches": 0,
    }


def _store():
    rows = {}
    reader = InMemorySignozClient(rows)
    writer = InMemoryAntcollectorClient(rows)
    return TaskStatsStore(reader, writer, table="agentlens_trace"), rows


def _snapshot(
    task_uuid,
    *,
    attempt=1,
    period=None,
    end_time=None,
    stats=None,
):
    now = datetime.now()
    return TaskStatsSnapshot(
        task_uuid=task_uuid,
        job_uuid="j-1",
        attempt=attempt,
        status="succeeded",
        stats_scope="global",
        owner_id="",
        stats=stats or _stats(),
        period=period or now - timedelta(minutes=5),
        end_time=end_time or now,
    )


def test_write_and_query_global_and_owner_snapshots():
    store, rows = _store()
    business_time = datetime.now() - timedelta(minutes=5)
    store.write_terminal_snapshots(
        task_uuid="t-1",
        job_uuid="j-1",
        attempt=2,
        status="succeeded",
        stats=_stats(source={"total": 3, "invalid": 1, "projection_failed": 0}),
        business_time=business_time,
        owner_stats={"owner-1": _stats()},
    )

    assert [row["name"] for row in rows["agentlens_trace"]] == ["task_stats", "task_stats"]
    assert store.latest_succeeded(job_uuid="j-1").stats_scope == "global"
    owner = store.latest_succeeded(job_uuid="j-1", owner_id="owner-1")
    assert owner is not None
    assert owner.owner_id == "owner-1"
    assert owner.stats["source"] == {}
    assert owner.period == business_time


def test_global_snapshot_round_trip_preserves_selector_stats():
    store, _ = _store()
    stats = _stats(source={"total": 2, "invalid": 0, "projection_failed": 0})
    stats["selector"] = _selector_stats()

    store.write_snapshot(_snapshot("t-selector", stats=stats))

    restored = store.latest_succeeded(job_uuid="j-1")
    assert restored is not None
    assert restored.stats["selector"] == _selector_stats()


def test_global_snapshot_round_trip_preserves_evaluate_counter():
    store, _ = _store()
    stats = _stats()
    stats["diagnosers"]["dg-1"]["quality"]["evaluate"] = 1

    store.write_snapshot(_snapshot("t-evaluate", stats=stats))

    restored = store.latest_succeeded(job_uuid="j-1")
    assert restored is not None
    assert restored.stats["diagnosers"]["dg-1"]["quality"]["evaluate"] == 1


def test_latest_physical_row_wins_and_invalid_latest_does_not_fall_back():
    store, rows = _store()
    period = datetime.now() - timedelta(minutes=5)
    store.write_snapshot(_snapshot("t-1", attempt=1, period=period))
    store.write_snapshot(_snapshot("t-1", attempt=2, period=period))
    latest = rows["agentlens_trace"][-1]
    body = json.loads(latest["attributes.record.body"])
    body["job_uuid"] = "j-other"
    latest["attributes.record.body"] = json.dumps(body)

    assert store.latest_succeeded(job_uuid="j-1") is None


def test_bad_row_does_not_hide_other_logical_snapshot():
    store, rows = _store()
    now = datetime.now()
    store.write_snapshot(_snapshot("t-bad", period=now - timedelta(minutes=10)))
    store.write_snapshot(_snapshot("t-good", period=now - timedelta(minutes=5)))
    rows["agentlens_trace"][0]["attributes.record.body"] = "{"

    snapshots = store.query_succeeded(job_uuid="j-1")

    assert [snapshot.task_uuid for snapshot in snapshots] == ["t-good"]


def test_latest_successful_task_replaces_same_business_window():
    store, _ = _store()
    period = datetime.now() - timedelta(minutes=5)
    store.write_snapshot(_snapshot("t-old", period=period, end_time=period + timedelta(minutes=1)))
    store.write_snapshot(_snapshot("t-new", period=period, end_time=period + timedelta(minutes=2)))

    snapshots = store.query_succeeded(job_uuid="j-1")

    assert [snapshot.task_uuid for snapshot in snapshots] == ["t-new"]


def test_same_business_window_uses_task_uuid_as_stable_tiebreaker():
    store, _ = _store()
    period = datetime.now() - timedelta(minutes=5)
    end_time = period + timedelta(minutes=1)
    store.write_snapshot(_snapshot("t-a", period=period, end_time=end_time))
    store.write_snapshot(_snapshot("t-z", period=period, end_time=end_time))

    snapshots = store.query_succeeded(job_uuid="j-1")

    assert [snapshot.task_uuid for snapshot in snapshots] == ["t-z"]


def test_window_defaults_and_rejects_invalid_ranges():
    now = datetime(2026, 8, 5, 12, 0, 0)
    start, end = resolve_task_stats_window(None, None, now=now)
    assert end == now
    assert start == now - timedelta(hours=6)

    with pytest.raises(InvalidTimeRange):
        resolve_task_stats_window("2026-08-05T12:00:00", "2026-08-05T12:00:00")
    with pytest.raises(InvalidTimeRange):
        resolve_task_stats_window("2026-07-01T00:00:00", "2026-08-05T12:00:00")
    with pytest.raises(InvalidTimeRange):
        resolve_task_stats_window("not-a-time", None)


def test_owner_snapshot_rejects_source_stats():
    store, _ = _store()
    with pytest.raises(ValueError, match="empty source"):
        store.write_snapshot(
            TaskStatsSnapshot(
                "t-1",
                "j-1",
                1,
                "succeeded",
                "owner",
                "owner-1",
                _stats(source={"total": 1, "invalid": 0, "projection_failed": 0}),
                period=datetime.now() - timedelta(minutes=5),
            )
        )


def test_owner_snapshot_rejects_selector_stats():
    store, _ = _store()
    stats = _stats()
    stats["selector"] = _selector_stats()
    with pytest.raises(ValueError, match="canonical|selector"):
        store.write_snapshot(
            TaskStatsSnapshot(
                "t-1",
                "j-1",
                1,
                "succeeded",
                "owner",
                "owner-1",
                stats,
                period=datetime.now() - timedelta(minutes=5),
            )
        )


def test_owner_snapshots_force_flush_in_batches_of_five_hundred():
    class Writer:
        def __init__(self):
            self.writes = 0
            self.flushes = 0

        def write_span(self, *_args, **_kwargs):
            self.writes += 1

        def force_flush(self):
            self.flushes += 1
            return True

        def close(self):
            pass

    writer = Writer()
    store = TaskStatsStore(InMemorySignozClient(), writer, table="agentlens_trace")

    store.write_terminal_snapshots(
        task_uuid="t-1",
        job_uuid="j-1",
        attempt=1,
        status="succeeded",
        stats=_stats(),
        business_time=datetime.now() - timedelta(minutes=5),
        owner_stats={f"owner-{index}": _stats() for index in range(501)},
    )

    assert writer.writes == 502
    assert writer.flushes == 2


def test_signoz_query_failure_propagates_without_fallback():
    class Reader:
        def query(self, *_args, **_kwargs):
            raise RuntimeError("signoz unavailable")

    store = TaskStatsStore(
        Reader(),
        InMemoryAntcollectorClient(),
        table="agentlens_trace",
    )

    with pytest.raises(RuntimeError, match="signoz unavailable"):
        store.query_succeeded(job_uuid="j-1")


def test_v3_stats_store_roundtrip_preserves_metadata_and_legacy_numbers():
    from tests.unit.service.test_diagnosis_stats_service import _v3_stats

    store, rows = _store()
    v3 = _v3_stats()
    period = datetime.now() - timedelta(minutes=5)
    store.write_snapshot(_snapshot("t-v3", period=period, stats=v3))
    store.write_snapshot(
        _snapshot("t-legacy", period=period - timedelta(minutes=1), stats=_stats())
    )
    result = {
        snapshot.task_uuid: snapshot.stats for snapshot in store.query_succeeded(job_uuid="j-1")
    }
    assert result["t-v3"] == v3
    assert result["t-legacy"] == _stats()
    assert "schema_version" not in result["t-legacy"]
