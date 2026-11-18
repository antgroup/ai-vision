from datetime import UTC, datetime

import pytest

from agentlens.domain.diagnosis_record import (
    DiagnosisRecord,
    DiagnosisResult,
    Execution,
    Metric,
    derive_summary,
    derive_verdict,
    normalize_record_datetime,
)
from agentlens.domain.enums import ReviewStatus, TargetType, Verdict


def _metric(name: str, verdict: Verdict, reason: str = "") -> Metric:
    return Metric(name=name, verdict=verdict, reason=reason)


def _record(result: DiagnosisResult) -> DiagnosisRecord:
    return DiagnosisRecord(
        uuid="r-1",
        task_uuid="t-1",
        job_uuid="j-1",
        diagnoser_uuid="dg-1",
        diagnoser_version="v1",
        target_type=TargetType.TRACE,
        target_id="trace-1",
        metrics=result.metrics,
        execution=result.execution,
        trace_snapshot={},
        review_status=ReviewStatus.PENDING,
        feedback=None,
        reviewed_by=None,
        reviewed_at=None,
        diagnosed_at=datetime.now(),
        target_created_at="",
    )


@pytest.mark.parametrize("verdict", [Verdict.BAD, Verdict.FAILED])
def test_bad_and_failed_metric_require_reason(verdict: Verdict):
    with pytest.raises(ValueError, match="reason"):
        _metric("quality", verdict)


def test_good_metric_allows_empty_reason_and_evidence_must_be_object():
    assert _metric("quality", Verdict.GOOD).reason == ""
    with pytest.raises(ValueError, match="evidence"):
        Metric(name="quality", verdict=Verdict.GOOD, evidence=[])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("metrics", "verdict"),
    [
        ([_metric("a", Verdict.GOOD)], Verdict.GOOD),
        ([_metric("a", Verdict.BAD, "bad")], Verdict.BAD),
        ([_metric("a", Verdict.FAILED, "failed")], Verdict.FAILED),
        (
            [
                _metric("a", Verdict.GOOD),
                _metric("b", Verdict.FAILED, "failed"),
                _metric("c", Verdict.BAD, "bad"),
            ],
            Verdict.BAD,
        ),
    ],
)
def test_verdict_is_derived_with_bad_precedence(metrics: list[Metric], verdict: Verdict):
    assert derive_verdict(metrics) is verdict


def test_summary_is_derived_from_execution_or_unique_metric_reasons():
    failed = DiagnosisResult(
        [_metric("a", Verdict.FAILED, "metric failed")],
        Execution.failed("timeout", "execution timeout"),
    )
    mixed = DiagnosisResult(
        [
            _metric("a", Verdict.BAD, "same"),
            _metric("b", Verdict.FAILED, "same"),
            _metric("c", Verdict.BAD, "other"),
            _metric("d", Verdict.GOOD, "passed"),
        ],
        Execution.succeeded(),
    )
    good = DiagnosisResult(
        [
            _metric("a", Verdict.GOOD, "passed"),
            _metric("b", Verdict.GOOD, "passed"),
            _metric("c", Verdict.GOOD, "other"),
        ],
        Execution.succeeded(),
    )
    assert derive_summary(failed) == "execution timeout"
    assert derive_summary(mixed) == "same；other"
    assert derive_summary(good) == "passed；other"
    assert (
        derive_summary(DiagnosisResult([_metric("a", Verdict.GOOD)], Execution.succeeded()))
        == "全部诊断项通过"
    )


def test_record_derives_verdict_and_summary_and_rejects_duplicate_metrics():
    result = DiagnosisResult([_metric("a", Verdict.BAD, "bad")], Execution.succeeded())
    record = _record(result)
    assert record.verdict is Verdict.BAD
    assert record.summary == "bad"
    with pytest.raises(ValueError, match="duplicate"):
        DiagnosisResult(
            [_metric("a", Verdict.GOOD), _metric("a", Verdict.BAD, "bad")],
            Execution.succeeded(),
        )


def test_failed_execution_requires_all_metrics_failed():
    with pytest.raises(ValueError, match="all metrics"):
        DiagnosisResult(
            [_metric("a", Verdict.GOOD)],
            Execution.failed("timeout", "timeout"),
        )


def test_record_datetime_accepts_iso_and_numeric_epoch():
    assert normalize_record_datetime("2026-07-19T09:00:00+08:00") == datetime.fromisoformat(
        "2026-07-19T09:00:00+08:00"
    )
    expected = datetime(2025, 7, 19, 8, 0, tzinfo=UTC)
    assert normalize_record_datetime(1_752_912_000) == expected
    assert normalize_record_datetime("1752912000000") == expected
    assert normalize_record_datetime(1_752_912_000_000_000) == expected
    assert normalize_record_datetime("1752912000000000000") == expected
