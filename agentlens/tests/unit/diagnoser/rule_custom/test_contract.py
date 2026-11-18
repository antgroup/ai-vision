import pytest

from agentlens.diagnoser.base import ExecutorFailure
from agentlens.diagnoser.executors.rule.custom.contract import validate_result
from agentlens.domain.enums import Verdict


def _metric(name: str = "quality", **overrides):
    value = {
        "name": name,
        "verdict": "good",
        "reason": "",
        "evidence": {},
    }
    value.update(overrides)
    return value


def test_valid_metrics_keep_returned_order_and_ignore_unknown_fields():
    metrics = validate_result(
        {
            "metrics": [
                _metric("safety", unknown=True),
                _metric("quality", verdict="bad", reason="bad quality"),
            ],
            "unknown": True,
        },
        ["quality", "safety"],
    )
    assert [metric.name for metric in metrics] == ["safety", "quality"]
    assert metrics[1].verdict is Verdict.BAD


@pytest.mark.parametrize("raw", ["not an object", [1, 2], None])
def test_non_object_result_rejected(raw):
    with pytest.raises(ExecutorFailure, match="必须返回对象"):
        validate_result(raw, ["quality"])


def test_missing_extra_duplicate_and_invalid_known_fields_rejected():
    with pytest.raises(ExecutorFailure, match="缺少声明指标"):
        validate_result({"metrics": [_metric("quality")]}, ["quality", "safety"])
    with pytest.raises(ExecutorFailure, match="缺少声明指标"):
        validate_result({"metrics": [_metric("extra")]}, ["quality"])
    with pytest.raises(ExecutorFailure, match="不能重复"):
        validate_result({"metrics": [_metric(), _metric()]}, ["quality"])
    with pytest.raises(ExecutorFailure, match="不能由用户协议设为 failed"):
        validate_result({"metrics": [_metric(verdict="failed")]}, ["quality"])
    with pytest.raises(ExecutorFailure, match="evidence"):
        validate_result({"metrics": [_metric(evidence=[])]}, ["quality"])
    with pytest.raises(ExecutorFailure, match="reason"):
        validate_result({"metrics": [_metric(reason=None)]}, ["quality"])


def test_optional_fields_default_for_good_and_bad_reason_is_required():
    metrics = validate_result(
        {"metrics": [{"name": "quality", "verdict": "good"}]},
        ["quality"],
    )
    assert metrics[0].reason == ""
    assert metrics[0].evidence == {}

    for metric in (
        {"name": "quality", "verdict": "bad"},
        _metric(verdict="bad", reason=" "),
    ):
        with pytest.raises(ExecutorFailure, match="reason"):
            validate_result({"metrics": [metric]}, ["quality"])


@pytest.mark.parametrize("missing", ["name", "verdict"])
def test_name_and_verdict_are_required(missing):
    value = _metric()
    del value[missing]
    with pytest.raises(ExecutorFailure, match="缺少字段"):
        validate_result({"metrics": [value]}, ["quality"])
