import json
from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from agentlens.domain.diagnosis_record import DiagnosisRecord, Execution, Metric
from agentlens.domain.enums import ReviewStatus, TargetType, Verdict
from agentlens.domain.errors import ExternalUnavailable
from agentlens.infra.antcollector import InMemoryAntcollectorClient
from agentlens.infra.signoz.client import InMemorySignozClient, SignozQueryError
from agentlens.service.datasource.record_snapshot import build_record_snapshot
from agentlens.service.datasource.trace import Trace
from agentlens.service.diagnosis_record_store import DiagnosisRecordStore


def _make_store():
    store = defaultdict(list)
    return DiagnosisRecordStore(
        InMemorySignozClient(store),
        InMemoryAntcollectorClient(store),
        table="agentlens_trace",
    )


def _make(
    uuid="r-xx",
    verdict=Verdict.BAD,
    job_uuid="j-1",
    exp_ver_id="",
    model="",
) -> DiagnosisRecord:
    now = datetime.now()
    return DiagnosisRecord(
        uuid=uuid,
        task_uuid="t-1",
        job_uuid=job_uuid,
        diagnoser_uuid="dg-1",
        diagnoser_version="v1",
        target_type=TargetType.TRACE,
        target_id="trace-x",
        metrics=[
            Metric(
                name="m1",
                verdict=verdict,
                reason="below threshold" if verdict == Verdict.BAD else "",
            )
        ],
        execution=Execution.succeeded(),
        trace_snapshot={},
        review_status=ReviewStatus.PENDING,
        feedback=None,
        reviewed_by=None,
        reviewed_at=None,
        diagnosed_at=now,
        target_created_at="",
        owner_id="owner-1",
        exp_ver_id=exp_ver_id,
        model=model,
    )


@pytest.mark.parametrize(
    "runtime_type",
    ["legacy", "legacy001", "legacyv2-2", "legacyagent", " LEGACY001 ", "cc", "unknown"],
)
def test_new_record_snapshot_has_no_retired_context_producer(runtime_type):
    from agentlens.service.datasource.projection import project_trace
    from tests.unit.diagnoser.test_shared_evidence_projection import row

    spans = [
        row(
            "root",
            "CHAT",
            **{
                "gen_ai.input.messages": [{"role": "user", "content": "synthetic query"}],
                "gen_ai.output.messages": [{"role": "assistant", "content": "synthetic answer"}],
            },
        )
    ]
    trace = project_trace(Trace("t", runtime_type, spans))
    record = _make()
    record.trace_snapshot = build_record_snapshot(trace)
    store = _make_store()
    store.write_record(record)
    saved = store.reader.query("SELECT * FROM agentlens_trace WHERE name = 'diagnosis_record'")[0]
    body = json.loads(saved["attributes.record.body"])
    assert "context" not in body
    assert "context" not in body["trace_snapshot"]
    assert "attributes.context" not in saved
    if runtime_type == "cc":
        assert body["trace_snapshot"]["user_query"] == "synthetic query"
    else:
        assert body["trace_snapshot"]["runtime_type"] == runtime_type
        assert body["trace_snapshot"]["spans"] == spans


def _write_legacy_body(
    store: DiagnosisRecordStore,
    *,
    uuid: str,
    results: list[dict],
    summary: str = "legacy summary",
    evidence: dict | None = None,
    attempt: int = 2,
) -> None:
    now = datetime.now().isoformat()
    store.writer.write_span(
        store.table,
        "diagnosis_record",
        {
            "uuid": uuid,
            "job_uuid": "j-1",
            "task_uuid": "t-1",
            "diagnoser_uuid": "dg-1",
            "diagnoser_version": "v1",
            "target_type": "trace",
            "target_id": "trace-legacy",
            "owner_id": "owner-1",
            "exp_ver_id": "|1001|",
            "verdict": "bad",
            "review_status": "pending",
            "summary": summary,
            "attempt": attempt,
            "reviewed_at": "",
            "diagnosed_at": now,
            "target_created_at": now,
        },
        {
            "uuid": uuid,
            "task_uuid": "t-1",
            "job_uuid": "j-1",
            "diagnoser_uuid": "dg-1",
            "diagnoser_version": "v1",
            "target_type": "trace",
            "target_id": "trace-legacy",
            "verdict": "bad",
            "confidence": 0.8,
            "summary": summary,
            "displayed_score": 0.2,
            "evidence": evidence or {},
            "trace_snapshot": {"input": "legacy"},
            "review_status": "pending",
            "feedback": None,
            "reviewed_by": None,
            "reviewed_at": None,
            "diagnosed_at": now,
            "target_created_at": now,
            "results": results,
            "owner_id": "owner-1",
            "exp_ver_id": "|1001|",
            "attempt": attempt,
        },
    )


def test_write_then_get_by_uuid():
    s = _make_store()
    s.write_record(_make(uuid="r-1", exp_ver_id="|1001|1002|"))
    rec = s.get_record("r-1")
    assert rec is not None
    assert rec.uuid == "r-1"
    assert rec.verdict == Verdict.BAD
    assert rec.owner_id == "owner-1"
    assert rec.exp_ver_id == "|1001|1002|"


def test_get_record_converts_legacy_results_to_metrics_and_execution():
    store = _make_store()
    _write_legacy_body(
        store,
        uuid="r-legacy-mixed",
        summary="legacy bad summary",
        evidence={"agent_run": {"rendered_prompt": "legacy prompt"}},
        results=[
            {
                "name": "bad_metric",
                "status": "evaluated",
                "score": 0.2,
                "raw_value": 12,
                "threshold": {"value": 20},
                "passed": False,
                "reason": "",
                "evidence": {"source": "legacy rule"},
            },
            {
                "name": "good_metric",
                "status": "evaluated",
                "score": 1.0,
                "passed": True,
                "reason": "",
                "evidence": {},
            },
            {
                "name": "failed_metric",
                "status": "failed",
                "score": None,
                "passed": None,
                "reason": "metric failed",
                "evidence": {},
            },
        ],
    )

    record = store.get_record("r-legacy-mixed")

    assert record is not None
    assert [metric.verdict for metric in record.metrics] == [
        Verdict.BAD,
        Verdict.GOOD,
        Verdict.FAILED,
    ]
    assert record.metrics[0].reason == "legacy bad summary"
    assert record.metrics[0].evidence == {"source": "legacy rule"}
    assert record.execution == Execution.succeeded(
        {"agent_run": {"rendered_prompt": "legacy prompt"}}
    )
    assert record.verdict == Verdict.BAD
    assert record.summary == "legacy bad summary；metric failed"
    assert getattr(record, "_attempt", None) == 2


def test_get_record_converts_all_legacy_failures_to_failed_execution():
    store = _make_store()
    _write_legacy_body(
        store,
        uuid="r-legacy-failed",
        summary="legacy execution failed",
        evidence={"run": {"duration_ms": 10}},
        results=[
            {
                "name": "quality",
                "status": "failed",
                "score": None,
                "passed": None,
                "reason": "model response invalid",
                "evidence": {},
            }
        ],
    )

    record = store.get_record("r-legacy-failed")

    assert record is not None
    assert record.metrics[0].verdict == Verdict.FAILED
    assert record.execution == Execution.failed(
        "legacy_execution_failed",
        "legacy execution failed",
        {"run": {"duration_ms": 10}},
    )
    assert record.summary == "legacy execution failed"


def test_writing_converted_legacy_record_replaces_old_body_shape():
    store = _make_store()
    _write_legacy_body(
        store,
        uuid="r-legacy-rewrite",
        results=[
            {
                "name": "quality",
                "status": "evaluated",
                "score": 0.2,
                "passed": False,
                "reason": "legacy bad",
                "evidence": {},
            }
        ],
    )
    record = store.get_record("r-legacy-rewrite")
    assert record is not None

    store.write_record(record, attempt=2)

    body = json.loads(store.writer.records[store.table][-1]["attributes.record.body"])
    assert "metrics" in body
    assert "execution" in body
    assert "results" not in body
    assert "displayed_score" not in body


def test_diagnosis_queries_ignore_task_stats_spans():
    store = _make_store()
    store.write_record(_make(uuid="r-1"))
    store.writer.write_span(
        store.table,
        "task_stats",
        {
            "task_uuid": "t-1",
            "job_uuid": "j-1",
            "attempt": 1,
            "status": "succeeded",
            "stats_scope": "global",
            "owner_id": "",
        },
        {
            "task_uuid": "t-1",
            "job_uuid": "j-1",
            "attempt": 1,
            "status": "succeeded",
            "stats_scope": "global",
            "owner_id": "",
            "stats": {"source": {}, "diagnosers": {}},
        },
    )

    records = store.query_records("j-1")

    assert [record.uuid for record in records] == ["r-1"]


def test_write_then_read_preserves_metrics_and_execution():
    store = _make_store()
    record = _make(uuid="r-results")
    record.metrics = [
        Metric(
            name="m1",
            verdict=Verdict.BAD,
            reason="below threshold",
            evidence={"source": "rule"},
        ),
        Metric(
            name="m2",
            verdict=Verdict.GOOD,
            reason="not applicable",
        ),
        Metric(
            name="m3",
            verdict=Verdict.FAILED,
            reason="executor failed",
        ),
    ]

    store.write_record(record)
    restored = store.get_record("r-results")

    assert restored is not None
    assert restored.metrics == record.metrics
    assert restored.execution == record.execution


def test_get_record_not_found():
    s = _make_store()
    assert s.get_record("nope") is None


def test_get_record_reads_a_result_written_earlier_in_the_same_second(monkeypatch):
    import re

    import agentlens.service.diagnosis_record_store as module

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 28, 16, 10, 40, 996129)

    class TimeBoundedReader(InMemorySignozClient):
        def query(self, sql, **kwargs):
            rows = super().query(sql, **kwargs)
            upper = re.search(r"`period` < '([^']+)'", sql).group(1)
            return [
                row
                for row in rows
                if datetime.fromisoformat(row["period"]) < datetime.fromisoformat(upper)
            ]

    storage = defaultdict(list)
    store = DiagnosisRecordStore(
        TimeBoundedReader(storage), InMemoryAntcollectorClient(storage), table="records"
    )
    store.write_record(_make(uuid="fresh"))
    storage["records"][0]["period"] = "2026-10-28 16:10:40.116882"
    monkeypatch.setattr(module, "datetime", Clock)

    assert store.get_record("fresh").uuid == "fresh"


def test_write_record_returns_uuid():
    s = _make_store()
    uid = s.write_record(_make(uuid="r-abc"))
    assert uid == "r-abc"


def test_write_record_and_wait_visible_returns_persisted_feedback_version():
    store = _make_store()
    record = _make(uuid="r-feedback")
    record.review_status = ReviewStatus.CONFIRMED
    record.feedback = {"remark": "confirmed by reviewer"}
    record.reviewed_by = "alice"
    record.reviewed_at = datetime.now()
    record.exp_ver_id = "|1001|"

    visible = store.write_record_and_wait_visible(
        record,
        attempt=2,
        visibility_timeout_seconds=0,
    )

    assert visible is not None
    assert visible is not record
    assert visible.review_status == ReviewStatus.CONFIRMED
    assert visible.feedback == {"remark": "confirmed by reviewer"}
    assert visible.reviewed_by == "alice"
    assert visible.reviewed_at == record.reviewed_at
    assert visible.exp_ver_id == "|1001|"
    assert getattr(visible, "_attempt", None) == 2


def test_write_record_and_wait_visible_retries_until_exact_version_is_visible():
    store = _make_store()
    expected = _make(uuid="r-delayed")
    expected.review_status = ReviewStatus.SKIPPED
    expected.feedback = {"remark": "review later"}
    expected.reviewed_by = "alice"
    expected.reviewed_at = datetime.now()

    persisted = deepcopy(expected)
    persisted._attempt = 3
    reads = iter([None, persisted])
    store.get_record_version = (  # type: ignore[method-assign]
        lambda _uuid, *, attempt, reviewed_at: next(reads)
    )

    visible = store.write_record_and_wait_visible(
        expected,
        attempt=3,
        visibility_timeout_seconds=1,
        poll_interval_seconds=0,
    )

    assert visible is persisted


def test_write_record_and_wait_visible_returns_none_after_poll_exhaustion():
    store = _make_store()
    expected = _make(uuid="r-timeout")
    expected.review_status = ReviewStatus.CONFIRMED
    expected.feedback = {"remark": "confirmed by reviewer"}
    expected.reviewed_by = "alice"
    expected.reviewed_at = datetime.now()

    store.get_record_version = (  # type: ignore[method-assign]
        lambda _uuid, *, attempt, reviewed_at: None
    )

    visible = store.write_record_and_wait_visible(
        expected,
        attempt=1,
        visibility_timeout_seconds=0,
    )

    assert visible is None


def test_write_record_and_wait_visible_uses_deadline(monkeypatch):
    store = _make_store()
    expected = _make(uuid="r-deadline")
    expected.review_status = ReviewStatus.CONFIRMED
    expected.reviewed_at = datetime.now()

    read_count = 0

    def get_missing_version(_uuid, *, attempt, reviewed_at):
        nonlocal read_count
        read_count += 1
        return None

    clock = {"now": 0.0}
    monkeypatch.setattr(store, "get_record_version", get_missing_version)
    monkeypatch.setattr(
        "agentlens.service.diagnosis_record_store.time.monotonic",
        lambda: clock["now"],
    )
    monkeypatch.setattr(
        "agentlens.service.diagnosis_record_store.time.sleep",
        lambda delay: clock.__setitem__("now", clock["now"] + delay),
    )

    visible = store.write_record_and_wait_visible(
        expected,
        attempt=1,
        visibility_timeout_seconds=2.5,
        poll_interval_seconds=1,
    )

    assert visible is None
    assert clock["now"] == 2.5
    assert read_count == 4


def test_write_record_and_wait_visible_distinguishes_flush_failure():
    import pytest

    class FlushFailingWriter(InMemoryAntcollectorClient):
        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return False

    shared = defaultdict(list)
    store = DiagnosisRecordStore(
        InMemorySignozClient(shared),
        FlushFailingWriter(shared),
        table="agentlens_trace",
    )
    record = _make(uuid="r-flush-failed")
    record.review_status = ReviewStatus.CONFIRMED
    record.reviewed_at = datetime.now()

    with pytest.raises(ExternalUnavailable, match="failed to flush"):
        store.write_record_and_wait_visible(record, visibility_timeout_seconds=0)


def test_write_promotes_compact_list_fields_to_attributes():
    shared = defaultdict(list)
    store = DiagnosisRecordStore(
        InMemorySignozClient(shared),
        InMemoryAntcollectorClient(shared),
        table="agentlens_trace",
    )
    record = _make(uuid="r-promoted")
    record.metrics[0].reason = "compact summary"
    record.target_created_at = datetime(2026, 7, 19, 8, 30, tzinfo=UTC)

    store.write_record(record, attempt=3)

    row = shared["agentlens_trace"][0]
    assert row["attributes.uuid"] == "r-promoted"
    assert row["attributes.diagnoser_uuid"] == "dg-1"
    assert row["attributes.diagnoser_version"] == "v1"
    assert row["attributes.target_type"] == "trace"
    assert row["attributes.target_id"] == "trace-x"
    assert row["attributes.owner_id"] == "owner-1"
    assert row["attributes.review_status"] == "pending"
    assert row["attributes.summary"] == "compact summary"
    assert row["attributes.attempt"] == 3
    expected_target_time = (
        datetime(2026, 7, 19, 8, 30, tzinfo=UTC)
        .astimezone()
        .replace(tzinfo=None)
        .isoformat(sep=" ", timespec="microseconds")
    )
    assert row["attributes.target_created_at"] == expected_target_time


def test_query_record_list_filters_sorts_and_pages_promoted_rows():
    store = _make_store()
    first = _make(uuid="r-a")
    first.target_id = "trace-a"
    first.diagnosed_at = datetime(2026, 7, 19, 9, 0)
    second = _make(uuid="r-b")
    second.target_id = "trace-b"
    second.diagnosed_at = datetime(2026, 7, 19, 10, 0)
    ignored = _make(uuid="r-good", verdict=Verdict.GOOD)
    ignored.diagnosed_at = datetime(2026, 7, 19, 11, 0)
    for record in (first, second, ignored):
        store.write_record(record)

    items, total = store.query_record_list(
        job_uuid="j-1",
        verdict=["bad"],
        limit=1,
        offset=1,
        sort_by="diagnosed_at",
        sort_order="desc",
        start_time="2026-07-01 00:00:00",
        end_time="2026-07-19 23:59:59",
    )

    assert total == 2
    assert [item.uuid for item in items] == ["r-a"]


def test_query_record_list_pages_globally_across_multiple_jobs():
    store = _make_store()
    oldest = _make(uuid="r-oldest", job_uuid="j-1")
    oldest.diagnosed_at = datetime(2026, 7, 19, 9, 0)
    middle = _make(uuid="r-middle", job_uuid="j-1")
    middle.diagnosed_at = datetime(2026, 7, 19, 10, 0)
    newest = _make(uuid="r-newest", job_uuid="j-2")
    newest.diagnosed_at = datetime(2026, 7, 19, 11, 0)
    for record in (oldest, middle, newest):
        store.write_record(record)

    items, total = store.query_record_list(
        job_uuid=["j-1", "j-2"],
        verdict=["bad"],
        limit=2,
        offset=0,
        sort_by="diagnosed_at",
        sort_order="desc",
        start_time="2026-07-01 00:00:00",
        end_time="2026-07-19 23:59:59",
    )

    assert total == 3
    assert [item.uuid for item in items] == ["r-newest", "r-middle"]


def test_query_record_list_filters_owner_id():
    store = _make_store()
    owned = _make(uuid="r-owned")
    other = _make(uuid="r-other")
    other.owner_id = "owner-2"
    store.write_record(owned)
    store.write_record(other)

    items, total = store.query_record_list(
        job_uuid="j-1",
        owner_id="owner-1",
        start_time="2026-07-01 00:00:00",
        end_time="2026-07-19 23:59:59",
    )

    assert total == 1
    assert [item.uuid for item in items] == ["r-owned"]


def test_query_record_list_filters_experiment_groups_with_or_and_exact_tokens():
    store = _make_store()
    store.write_record(_make(uuid="r-1001", exp_ver_id="|1001|"))
    store.write_record(_make(uuid="r-1002", exp_ver_id="|1002|2000|"))
    store.write_record(_make(uuid="r-10010", exp_ver_id="|10010|"))

    items, total = store.query_record_list(
        job_uuid="j-1",
        exp_ver_id=["1001", "1002", "1001"],
    )

    assert total == 2
    assert {item.uuid for item in items} == {"r-1001", "r-1002"}
    assert all(item.exp_ver_id for item in items)


def test_query_record_list_filters_models_and_missing_model():
    store = _make_store()
    store.write_record(_make(uuid="r-a", model="model-a"))
    store.write_record(_make(uuid="r-b", model="model-b"))
    store.write_record(_make(uuid="r-missing"))

    items, total = store.query_record_list(job_uuid="j-1", model="model-a")
    assert total == 1
    assert items[0].uuid == "r-a"

    items, total = store.query_record_list(job_uuid="j-1", model="missing_model")
    assert total == 1
    assert items[0].uuid == "r-missing"


def test_query_record_list_pushes_or_experiment_filter_into_signoz_sql():
    reader = _ListReader([])
    store = DiagnosisRecordStore(
        reader,
        InMemoryAntcollectorClient(),
        table="agentlens_trace",
    )

    store.query_record_list(job_uuid="j-1", exp_ver_id=["1001", "1002"])

    assert "`attributes.exp_ver_id` LIKE '%|1001|%'" in reader.sql
    assert "`attributes.exp_ver_id` LIKE '%|1002|%'" in reader.sql
    assert "ESCAPE" not in reader.sql
    assert " OR " in reader.sql


def test_query_record_list_rejects_non_numeric_experiment_group():
    store = _make_store()

    with pytest.raises(ValueError, match="digits only"):
        store.query_record_list(job_uuid="j-1", exp_ver_id=["10%_2"])


def test_query_record_list_filters_review_status_after_latest_version_wins():
    store = _make_store()
    reviewed = _make(uuid="r-reviewed")
    store.write_record(reviewed)
    reviewed.review_status = ReviewStatus.CONFIRMED
    reviewed.reviewed_at = datetime(2026, 7, 19, 12, 0)
    store.write_record(reviewed)
    store.write_record(_make(uuid="r-pending"))

    items, total = store.query_record_list(
        job_uuid="j-1",
        review_status="pending",
        start_time="2026-07-01 00:00:00",
        end_time="2026-07-19 23:59:59",
    )

    assert total == 1
    assert [item.uuid for item in items] == ["r-pending"]


def test_query_record_list_sorts_mixed_timezone_target_created_at_values():
    rows = [
        {
            "attributes.uuid": "r-utc",
            "attributes.task_uuid": "t-1",
            "attributes.job_uuid": "j-1",
            "attributes.diagnoser_uuid": "dg-1",
            "attributes.diagnoser_version": "v1",
            "attributes.target_type": "trace",
            "attributes.target_id": "trace-utc",
            "attributes.verdict": "bad",
            "attributes.review_status": "pending",
            "attributes.summary": "utc",
            "attributes.attempt": 1,
            "attributes.diagnosed_at": "2026-08-13T10:00:00",
            "attributes.target_created_at": "2026-08-13T10:00:00+08:00",
            "period": "2026-08-13 10:05:00",
        },
        {
            "attributes.uuid": "r-naive",
            "attributes.task_uuid": "t-1",
            "attributes.job_uuid": "j-1",
            "attributes.diagnoser_uuid": "dg-1",
            "attributes.diagnoser_version": "v1",
            "attributes.target_type": "trace",
            "attributes.target_id": "trace-naive",
            "attributes.verdict": "bad",
            "attributes.review_status": "pending",
            "attributes.summary": "naive",
            "attributes.attempt": 1,
            "attributes.diagnosed_at": "2026-08-13T10:00:00",
            "attributes.target_created_at": "2026-08-13T11:00:00",
            "period": "2026-08-13 10:06:00",
        },
    ]
    store = DiagnosisRecordStore(
        _ListReader(rows),
        InMemoryAntcollectorClient(),
        table="agentlens_trace",
    )

    items, total = store.query_record_list(
        job_uuid="j-1",
        sort_by="target_created_at",
        sort_order="desc",
    )

    assert total == 2
    assert [item.uuid for item in items] == ["r-naive", "r-utc"]


class _ListReader:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""

    def query(self, sql: str, biz_domain: str = "", db: str = ""):
        self.sql = sql
        return self.rows


class _MissingExpVerIdReader(_ListReader):
    def __init__(self, rows):
        super().__init__(rows)
        self.calls = 0

    def query(self, sql: str, biz_domain: str = "", db: str = ""):
        self.calls += 1
        self.sql = sql
        if "attributes.exp_ver_id" in sql:
            raise SignozQueryError("unknown column attributes.exp_ver_id")
        return self.rows


class _MissingModelReader(_ListReader):
    def __init__(self, rows):
        super().__init__(rows)
        self.calls = 0

    def query(self, sql: str, biz_domain: str = "", db: str = ""):
        self.calls += 1
        self.sql = sql
        if "attributes.model" in sql:
            raise SignozQueryError(
                'Schema error: No field named "attributes.model". '
                'Valid fields are "attributes.owner_id", "attributes.exp_ver_id".'
            )
        return self.rows


def test_query_record_list_treats_missing_model_column_as_empty_without_filter():
    reader = _MissingModelReader([])
    store = DiagnosisRecordStore(reader, InMemoryAntcollectorClient(), table="agentlens_trace")

    items, total = store.query_record_list(job_uuid="j-1")

    assert items == []
    assert total == 0
    assert reader.calls == 2
    assert "attributes.model" not in reader.sql
    assert "attributes.owner_id" in reader.sql
    assert "attributes.exp_ver_id" in reader.sql


def test_query_record_list_preserves_model_without_filter():
    store = _make_store()
    store.write_record(_make(model="model-a"))

    items, total = store.query_record_list(job_uuid="j-1")

    assert total == 1
    assert items[0].model == "model-a"


def test_query_record_list_returns_empty_when_filtered_model_column_is_missing():
    reader = _MissingModelReader([])
    store = DiagnosisRecordStore(reader, InMemoryAntcollectorClient(), table="agentlens_trace")

    items, total = store.query_record_list(job_uuid="j-1", model="model-a")

    assert items == []
    assert total == 0
    assert reader.calls == 1


def test_query_record_list_treats_missing_experiment_column_as_empty_without_filter():
    row = {
        "attributes.uuid": "r-old",
        "attributes.task_uuid": "t-1",
        "attributes.job_uuid": "j-1",
        "attributes.diagnoser_uuid": "dg-1",
        "attributes.diagnoser_version": "v1",
        "attributes.target_type": "trace",
        "attributes.target_id": "trace-old",
        "attributes.verdict": "bad",
        "attributes.review_status": "pending",
        "attributes.summary": "old",
        "attributes.attempt": 1,
        "attributes.diagnosed_at": "2026-08-13T10:00:00",
        "attributes.target_created_at": "2026-08-13T10:00:00",
        "period": "2026-08-13 10:01:00",
    }
    reader = _MissingExpVerIdReader([row])
    store = DiagnosisRecordStore(
        reader,
        InMemoryAntcollectorClient(),
        table="agentlens_trace",
    )

    items, total = store.query_record_list(job_uuid="j-1")

    assert total == 1
    assert items[0].exp_ver_id == ""
    assert reader.calls == 2


def test_query_record_list_returns_empty_when_filtered_experiment_column_is_missing():
    reader = _MissingExpVerIdReader([])
    store = DiagnosisRecordStore(
        reader,
        InMemoryAntcollectorClient(),
        table="agentlens_trace",
    )

    items, total = store.query_record_list(job_uuid="j-1", exp_ver_id=["1001"])

    assert items == []
    assert total == 0
    assert reader.calls == 1


def test_query_record_list_keeps_promoted_fields_when_definition_body_is_invalid():
    rows = [
        {
            "attributes.uuid": "r-older-attempt",
            "attributes.task_uuid": "t-1",
            "attributes.job_uuid": "j-1",
            "attributes.diagnoser_uuid": "dg-1",
            "attributes.diagnoser_version": "v1",
            "attributes.target_type": "trace",
            "attributes.target_id": "trace-old",
            "attributes.verdict": "bad",
            "attributes.review_status": "pending",
            "attributes.summary": "late old attempt",
            "attributes.attempt": 1,
            "attributes.diagnosed_at": "2026-07-19T09:00:00",
            "attributes.target_created_at": "2026-07-19T07:00:00+00:00",
            "period": "2026-07-19 10:05:00",
            "attributes.record.body": "{not valid json",
        },
        {
            "attributes.uuid": "r-older-attempt",
            "attributes.task_uuid": "t-1",
            "attributes.job_uuid": "j-1",
            "attributes.diagnoser_uuid": "dg-1",
            "attributes.diagnoser_version": "v1",
            "attributes.target_type": "trace",
            "attributes.target_id": "trace-new",
            "attributes.verdict": "bad",
            "attributes.review_status": "pending",
            "attributes.summary": "winning attempt",
            "attributes.attempt": 2,
            "attributes.diagnosed_at": "2026-07-19T10:00:00",
            "attributes.target_created_at": "2026-07-19T08:00:00+00:00",
            "period": "2026-07-19 10:00:00",
            "attributes.record.body": "{also invalid json",
        },
        {
            "attributes.uuid": "r-latest",
            "attributes.task_uuid": "t-1",
            "attributes.job_uuid": "j-1",
            "attributes.diagnoser_uuid": "dg-2",
            "attributes.diagnoser_version": "v2",
            "attributes.target_type": "trace",
            "attributes.target_id": "trace-latest",
            "attributes.verdict": "bad",
            "attributes.review_status": "confirmed",
            "attributes.summary": "latest",
            "attributes.attempt": 1,
            "attributes.diagnosed_at": "2026-07-19T11:00:00",
            "attributes.target_created_at": None,
            "period": "2026-07-19 11:00:00",
            "attributes.record.body": "{still invalid json",
        },
    ]
    reader = _ListReader(rows)
    store = DiagnosisRecordStore(
        reader,
        InMemoryAntcollectorClient(),
        table="agentlens_trace",
    )

    items, total = store.query_record_list(
        job_uuid="j-1",
        verdict=["bad"],
        limit=1,
        offset=1,
        sort_by="diagnosed_at",
        sort_order="desc",
    )

    assert total == 2
    assert [item.uuid for item in items] == ["r-older-attempt"]
    assert items[0].summary == "winning attempt"
    assert items[0].diagnosed_at == datetime(2026, 7, 19, 10, 0)
    assert "record.body" in reader.sql
    assert "attributes.review_status" in reader.sql
    assert "`attributes.verdict` IN ('bad')" in reader.sql
    assert "LIMIT 50000" in reader.sql


def test_query_record_list_filters_verdict_before_attempt_dedup():
    store = _make_store()
    old = _make(uuid="r-1", verdict=Verdict.BAD)
    old.metrics[0].reason = "old bad"
    new = _make(uuid="r-1", verdict=Verdict.GOOD)
    store.write_record(old, attempt=1)
    store.write_record(new, attempt=2)

    items, total = store.query_record_list(job_uuid="j-1", verdict=["bad"])

    assert [item.uuid for item in items] == ["r-1"]
    assert total == 1
    assert items[0].summary == "old bad"


@pytest.mark.parametrize("method", ["query_record_list", "query_records", "query_records_multi"])
@pytest.mark.parametrize(
    "time_params,expects_window",
    [
        ({"task_uuid": "t-1"}, False),
        ({}, True),
        ({"task_uuid": ""}, True),
        ({"task_uuid": "t-1", "start_time": "2020-01-01 00:00:00"}, True),
        ({"task_uuid": "t-1", "end_time": "2020-01-02 00:00:00"}, True),
    ],
)
def test_record_queries_only_omit_implicit_window_for_an_exact_task(
    method, time_params, expects_window
):
    reader = _ListReader([])
    store = DiagnosisRecordStore(reader, InMemoryAntcollectorClient(), table="agentlens_trace")
    job_params = {"job_uuids": ["j-1"]} if method == "query_records_multi" else {"job_uuid": "j-1"}

    getattr(store, method)(**job_params, **time_params)

    assert "`attributes.job_uuid`" in reader.sql
    assert "'j-1'" in reader.sql
    if time_params.get("task_uuid"):
        assert "`attributes.task_uuid` = 't-1'" in reader.sql
    assert ("`period` >=" in reader.sql) is expects_window
    assert ("`attributes.target_created_at` >=" in reader.sql) is expects_window
    assert ("`attributes.target_created_at` <" in reader.sql) is expects_window
    if time_params.get("start_time"):
        assert "`attributes.target_created_at` >= '2020-01-01 00:00:00'" in reader.sql
    if time_params.get("end_time"):
        assert "`attributes.target_created_at` < '2020-01-02 00:00:00'" in reader.sql


def test_query_record_list_task_filter_keeps_target_time_window():
    reader = _ListReader([])
    store = DiagnosisRecordStore(
        reader,
        InMemoryAntcollectorClient(),
        table="agentlens_trace",
    )

    items, total = store.query_record_list(
        job_uuid="j-1",
        task_uuid="t-1",
        start_time="2020-01-01 00:00:00",
        end_time="2020-01-02 00:00:00",
    )

    assert items == []
    assert total == 0
    assert "`attributes.task_uuid` = 't-1'" in reader.sql
    assert "`period` >= '2020-01-01 00:00:00'" in reader.sql
    assert "`period` <" in reader.sql
    assert "`attributes.target_created_at` >= '2020-01-01 00:00:00'" in reader.sql
    assert "`attributes.target_created_at` < '2020-01-02 00:00:00'" in reader.sql


def test_query_records_task_filter_keeps_target_time_window():
    reader = _ListReader([])
    store = DiagnosisRecordStore(
        reader,
        InMemoryAntcollectorClient(),
        table="agentlens_trace",
    )

    records = store.query_records(
        job_uuid="j-1",
        task_uuid="t-1",
        start_time="2020-01-01 00:00:00",
        end_time="2020-01-02 00:00:00",
    )

    assert records == []
    assert "`attributes.task_uuid` = 't-1'" in reader.sql
    assert "`period` >= '2020-01-01 00:00:00'" in reader.sql
    assert "`period` <" in reader.sql
    assert "`attributes.target_created_at` >= '2020-01-01 00:00:00'" in reader.sql
    assert "`attributes.target_created_at` < '2020-01-02 00:00:00'" in reader.sql


def test_query_records_multi_task_filter_keeps_target_and_scan_time_windows():
    reader = _ListReader([])
    store = DiagnosisRecordStore(
        reader,
        InMemoryAntcollectorClient(),
        table="agentlens_trace",
    )

    records = store.query_records_multi(
        job_uuids=["j-1", "j-2"],
        task_uuid="t-1",
        start_time="2020-01-01 00:00:00",
        end_time="2020-01-02 00:00:00",
    )

    assert records == []
    assert "`attributes.task_uuid` = 't-1'" in reader.sql
    assert "`period` >= '2020-01-01 00:00:00'" in reader.sql
    assert "`period` <" in reader.sql
    assert "`attributes.target_created_at` >= '2020-01-01 00:00:00'" in reader.sql
    assert "`attributes.target_created_at` < '2020-01-02 00:00:00'" in reader.sql


def test_query_record_list_filters_by_target_created_at_without_task_uuid():
    reader = _ListReader([])
    store = DiagnosisRecordStore(
        reader,
        InMemoryAntcollectorClient(),
        table="agentlens_trace",
    )

    store.query_record_list(
        job_uuid="j-1",
        start_time="2026-07-19 08:00:00",
        end_time="2026-07-19 09:00:00",
    )

    assert "`attributes.target_created_at` >= '2026-07-19 08:00:00'" in reader.sql
    assert "`attributes.target_created_at` < '2026-07-19 09:00:00'" in reader.sql
    assert "`period` >= '2026-07-19 08:00:00'" in reader.sql
    assert "`period` <" in reader.sql


def test_query_record_list_pushes_owner_id_into_signoz_sql():
    reader = _ListReader([])
    store = DiagnosisRecordStore(
        reader,
        InMemoryAntcollectorClient(),
        table="agentlens_trace",
    )

    store.query_record_list(
        job_uuid="j-1",
        owner_id="owner-1",
    )

    assert "`attributes.owner_id` = 'owner-1'" in reader.sql


def test_query_record_list_filters_multiple_diagnosers_with_in():
    reader = _ListReader([])
    store = DiagnosisRecordStore(
        reader,
        InMemoryAntcollectorClient(),
        table="agentlens_trace",
    )

    store.query_record_list(
        job_uuid="j-1",
        diagnoser_uuid=["dg-1", "dg-2", "dg-1"],
    )

    assert "`attributes.diagnoser_uuid` IN ('dg-1','dg-2')" in reader.sql


def test_query_record_list_returns_empty_without_query_for_empty_diagnoser_set():
    reader = _ListReader([])
    store = DiagnosisRecordStore(
        reader,
        InMemoryAntcollectorClient(),
        table="agentlens_trace",
    )

    items, total = store.query_record_list(
        job_uuid="j-1",
        diagnoser_uuid=[],
    )

    assert items == []
    assert total == 0
    assert reader.sql == ""


def test_detail_round_trip_normalizes_all_record_timestamps():
    store = _make_store()
    record = _make(uuid="r-times")
    record.reviewed_at = datetime(2026, 7, 19, 9, 10, tzinfo=UTC)
    record.diagnosed_at = datetime(2026, 7, 19, 9, 0, tzinfo=UTC)
    record.target_created_at = "2026-07-19T08:00:00+00:00"
    record.feedback = {"remark": "full detail"}
    record.exp_ver_id = "|1001|1002|"

    store.write_record(record)
    restored = store.get_record("r-times")

    assert restored is not None
    assert restored.reviewed_at == datetime(2026, 7, 19, 9, 10, tzinfo=UTC)
    assert restored.diagnosed_at == datetime(2026, 7, 19, 9, 0, tzinfo=UTC)
    assert restored.target_created_at == datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
    assert restored.metrics == record.metrics
    assert restored.feedback == {"remark": "full detail"}
    assert restored.owner_id == "owner-1"
    assert restored.exp_ver_id == "|1001|1002|"


def test_esc_blocks_sql_injection():
    """_esc 拒绝含引号/分号等非法字符的输入。"""
    import pytest

    from agentlens.service.diagnosis_record_store import _SAFE_TS, _esc

    # 合法值通过
    assert _esc("j-abc12345") == "j-abc12345"
    assert _esc("good") == "good"
    assert _esc("2024-01-01 00:00:00", _SAFE_TS) == "2024-01-01 00:00:00"
    # 注入尝试被拒
    with pytest.raises(ValueError):
        _esc("'; DROP TABLE--")
    with pytest.raises(ValueError):
        _esc("bad OR 1=1")
    with pytest.raises(ValueError):
        _esc("2024-01-01'; DROP", _SAFE_TS)


def test_missing_optional_column_parses_signoz_no_field_message_only():
    from agentlens.service.diagnosis_record_store import _missing_optional_column

    error = SignozQueryError(
        "execute sql failed: Create logical plan failed, caused by: "
        'Schema error: No field named "attributes.model". '
        'Valid fields are "attributes.owner_id", "attributes.exp_ver_id".'
    )

    assert _missing_optional_column(error) == "attributes.model"


def test_missing_optional_column_keeps_legacy_error_formats():
    from agentlens.service.diagnosis_record_store import _missing_optional_column

    assert _missing_optional_column(SignozQueryError("unknown column attributes.model")) == (
        "attributes.model"
    )
    assert (
        _missing_optional_column(
            SignozQueryError("query validation failed for attributes.exp_ver_id")
        )
        == "attributes.exp_ver_id"
    )
