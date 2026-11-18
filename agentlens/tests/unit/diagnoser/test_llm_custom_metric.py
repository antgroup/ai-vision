import pytest

from agentlens.diagnoser.base import ExecutorFailure
from agentlens.diagnoser.executors.llm.custom.executor import LlmCustomExecutor
from agentlens.diagnoser.registry import ExecutorRegistry
from agentlens.domain.enums import Verdict
from agentlens.domain.metric_definition import metric_definitions
from agentlens.infra.llm_judge import JudgeUnavailable
from agentlens.service.datasource.trace import Trace
from tests.unit.diagnoser.projection_factory import trace_from_dict


class _FakeJudge:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def judge(self, prompt):
        self.calls.append(prompt)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _trace(**overrides):
    data = dict(
        trace_id="trace-1",
        timestamp="2026-07-11T10:00:00+08:00",
        user_query="胸痛而且喘不上气怎么办",
        agent_output="先在家休息观察",
        outcome={"has_error": False, "total_latency_ms": 1000},
        labels={"intent": "疾病诊治咨询"},
        runtime_type="codex",
        node_seq=["流式回答"],
        steps=[{"kind": "execution", "name": "流式回答", "type": "GENERATION", "status": "OK"}],
        history=[],
        retrieved=[],
    )
    data.update(overrides)
    return trace_from_dict(data)


def _spec():
    return {
        "prompt": "问题：{{user_query}}\n回答：{{agent_output}}",
        "required_vars": ["user_query", "agent_output"],
        "metrics": [
            {"name": "medical_safety", "desc": "医疗安全"},
            {"name": "urgent_triage", "desc": "紧急分诊"},
            {"name": "content_compliance", "desc": "内容合规"},
        ],
    }


def _result(*, medical_safety="good", urgent_triage="good", content_compliance="good"):
    return {
        "metrics": [
            {
                "name": "medical_safety",
                "verdict": medical_safety,
                "reason": "医疗安全结论",
                "evidence": {},
            },
            {
                "name": "urgent_triage",
                "verdict": urgent_triage,
                "reason": "分诊结论",
                "evidence": {},
            },
            {
                "name": "content_compliance",
                "verdict": content_compliance,
                "reason": "内容结论",
                "evidence": {},
            },
        ],
    }


def test_registered():
    assert ("llm", "llm_custom") in ExecutorRegistry.known_keys()


def test_single_metric_requires_exact_declared_name():
    spec = {
        "prompt": "问题：{{user_query}}\n回答：{{agent_output}}",
        "metrics": [{"name": "test", "desc": "测试指标"}],
    }
    executor = LlmCustomExecutor(spec)
    executor._judge = _FakeJudge(
        {"metrics": [{"name": "相关性", "verdict": "good", "reason": "相关", "evidence": {}}]}
    )

    with pytest.raises(ExecutorFailure) as raised:
        executor.evaluate(_trace())

    assert raised.value.code == "invalid_result"


def test_raw_only_custom_llm_resolves_spans_without_projection():
    spec = {
        "prompt": "原始问题：{{spans[0].attributes.query}}",
        "required_vars": ["spans[0].attributes.query"],
        "metrics": [{"name": "quality", "desc": "质量"}],
    }
    executor = LlmCustomExecutor(spec)
    executor._judge = _FakeJudge(
        {
            "metrics": [
                {
                    "name": "quality",
                    "verdict": "good",
                    "reason": "raw ok",
                    "evidence": {},
                }
            ]
        }
    )
    trace = Trace(
        trace_id="raw-trace",
        runtime_type="other",
        spans=[{"name": "pipeline", "attributes": {"query": "raw question"}}],
    )

    result = executor.evaluate(trace)

    assert result.verdict == Verdict.GOOD
    assert "原始问题：raw question" in executor._judge.calls[0]
    assert result.execution.evidence["agent_run"]["rendered_prompt"].startswith(
        "原始问题：raw question"
    )


def test_multiple_failed_metrics_are_preserved():
    executor = LlmCustomExecutor(_spec())
    executor._judge = _FakeJudge(
        _result(
            medical_safety="bad",
            urgent_triage="bad",
        )
    )

    result = executor.evaluate(_trace())

    assert result.verdict == Verdict.BAD
    metrics = {metric.name: metric for metric in result.metrics}
    assert set(metrics) == {"medical_safety", "urgent_triage", "content_compliance"}
    assert metrics["medical_safety"].verdict is Verdict.BAD
    assert metrics["urgent_triage"].verdict is Verdict.BAD
    assert metrics["content_compliance"].verdict is Verdict.GOOD
    assert result.summary == "医疗安全结论；分诊结论"
    assert (
        "<metric_keys>medical_safety, urgent_triage, content_compliance</metric_keys>"
        in executor._judge.calls[0]
    )


def test_all_metrics_good_uses_derived_summary():
    spec = _spec()
    spec["pass_summary"] = "未发现明确过程正确性问题"
    executor = LlmCustomExecutor(spec)
    executor._judge = _FakeJudge(_result())

    result = executor.evaluate(_trace(agent_output="请立即拨打120或前往急诊"))

    assert result.verdict == Verdict.GOOD
    assert all(metric.verdict is Verdict.GOOD for metric in result.metrics)
    assert result.summary == "医疗安全结论；分诊结论；内容结论"


def test_all_metrics_good_accepts_minimal_metric_fields():
    executor = LlmCustomExecutor(_spec())
    executor._judge = _FakeJudge(
        {
            "metrics": [
                {"name": "medical_safety", "verdict": "good"},
                {"name": "urgent_triage", "verdict": "good"},
                {"name": "content_compliance", "verdict": "good"},
            ]
        }
    )

    result = executor.evaluate(_trace())

    assert result.verdict is Verdict.GOOD
    assert all(metric.reason == "" for metric in result.metrics)
    assert all(metric.evidence == {} for metric in result.metrics)
    assert result.summary == "全部诊断项通过"


def test_missing_or_unknown_or_duplicate_metrics_raise_structured_failure():
    outputs = [
        {"metrics": _result()["metrics"][:2]},
        {
            "metrics": _result()["metrics"]
            + [{"name": "unknown", "verdict": "good", "reason": "x", "evidence": {}}]
        },
        {"metrics": _result()["metrics"] + [_result()["metrics"][0]]},
    ]
    for output in outputs:
        executor = LlmCustomExecutor(_spec())
        executor._judge = _FakeJudge(output)

        with pytest.raises(ExecutorFailure) as raised:
            executor.evaluate(_trace())

        assert raised.value.code == "invalid_result"


def test_unavailable_judge_raises_structured_failure():
    executor = LlmCustomExecutor(_spec())
    executor._judge = _FakeJudge(JudgeUnavailable("down"))

    with pytest.raises(ExecutorFailure) as raised:
        executor.evaluate(_trace())

    assert raised.value.code == "judge_unavailable"
    assert raised.value.msg == "LLM 评判失败"


def test_missing_required_input_is_skipped_without_judge():
    executor = LlmCustomExecutor(_spec())
    executor._judge = _FakeJudge({})

    reason = executor.applicability_skip_reason(_trace(agent_output=""))
    assert reason == "missing_required_evidence: agent_output"
    assert executor._judge.calls == []


def test_metric_definitions_require_object_items():
    definitions = metric_definitions({"metrics": [{"name": "quality"}]})
    assert [definition.name for definition in definitions] == ["quality"]

    with pytest.raises(ValueError, match="metrics items must be objects"):
        metric_definitions({"metrics": ["quality"]})
