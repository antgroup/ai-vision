import pytest

from agentlens.diagnoser.base import ExecutorFailure
from agentlens.diagnoser.executors.llm.base import (
    _extract_var_names,
    _render_prompt,
    _resolve_llm_inputs,
)
from agentlens.diagnoser.executors.llm.builtin.one_shot import LlmBuiltinExecutor
from agentlens.domain.enums import Verdict
from agentlens.infra.llm_judge import JudgeUnavailable
from agentlens.service.datasource.projection.expressions import render
from agentlens.service.datasource.projection.models import TraceOutcome
from agentlens.service.datasource.projection.projectors.coding.models import CodingProjection
from agentlens.service.datasource.trace import ProjectionUnavailable, Trace
from agentlens.web.routes.diagnoser import _trial_prompt_inputs


class _FakeJudge:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def judge(self, prompt):
        self.calls.append(prompt)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _trace() -> Trace:
    return Trace(
        trace_id="trace-1",
        runtime_type="codex",
        spans=[],
        projection=CodingProjection(
            timestamp=None,
            user_query="question",
            agent_output="answer",
            outcome=TraceOutcome(
                has_error=False,
                result_state="SUCCESS",
                total_latency_ms=100,
                interrupted=False,
            ),
            labels={},
            node_seq=[],
            steps=[],
        ),
    )


def _executor(result) -> LlmBuiltinExecutor:
    executor = LlmBuiltinExecutor(
        {
            "prompt": "{{user_query}}",
            "metrics": [{"name": "safety", "desc": "安全性"}],
        }
    )
    executor._judge = _FakeJudge(result)
    return executor


def test_builtin_llm_still_requires_projection():
    trace = Trace(trace_id="raw-trace", runtime_type="other", spans=[])

    with pytest.raises(ProjectionUnavailable):
        _executor(
            {"metrics": [{"name": "safety", "verdict": "good", "reason": "safe", "evidence": {}}]}
        ).evaluate(trace)


def test_missing_required_evidence_skips_before_judge_call():
    executor = _executor({"metrics": [{"name": "safety", "verdict": "good"}]})
    executor.spec["required_vars"] = ["user_query", "agent_output"]
    trace = _trace()
    trace.projection.agent_output = ""
    assert executor.applicability_skip_reason(trace) == "missing_required_evidence: agent_output"
    assert executor._judge.calls == []


def test_present_required_evidence_is_applicable():
    executor = _executor({"metrics": [{"name": "safety", "verdict": "good"}]})
    executor.spec["required_vars"] = ["user_query", "agent_output"]
    assert executor.applicability_skip_reason(_trace()) is None


def test_builtin_llm_emits_good_metric():
    result = _executor(
        {"metrics": [{"name": "safety", "verdict": "good", "reason": "safe", "evidence": {}}]}
    ).evaluate(_trace())

    assert result.verdict is Verdict.GOOD
    assert len(result.metrics) == 1
    metric = result.metrics[0]
    assert metric.name == "safety"
    assert metric.verdict is Verdict.GOOD
    assert metric.reason == "safe"
    assert (
        result.execution.evidence["agent_run"]["rendered_prompt"]
        == "question\n\n<metric_keys>safety</metric_keys>"
    )
    assert "rendered_prompt" not in metric.evidence


def test_builtin_llm_rejects_unmapped_image_variable():
    trace = _trace()
    executor = LlmBuiltinExecutor(
        {
            "prompt": "{{user_query}}",
            "image_vars": ["user_images"],
            "metrics": [{"name": "safety", "desc": "安全性"}],
        }
    )

    with pytest.raises(ExecutorFailure) as raised:
        executor.evaluate(trace)

    assert raised.value.code == "invalid_image_input"


def test_builtin_llm_raises_structured_failure_when_judge_is_unavailable():
    with pytest.raises(ExecutorFailure) as raised:
        _executor(JudgeUnavailable("judge offline")).evaluate(_trace())

    assert raised.value.code == "judge_unavailable"
    assert raised.value.msg == "LLM 评判失败"
    assert raised.value.evidence["error"] == "judge offline"


def test_trial_and_formal_llm_render_the_same_raw_and_semantic_variables():
    trace = _trace()
    trace.spans.append(
        {
            "name": "v4.pipeline",
            "attributes": {"query": "raw question"},
        }
    )
    prompt = (
        "节点：{{spans[0].name}}\n"
        "原始问题：{{spans[name:v4.pipeline].attributes.query}}\n"
        "语义问题：{{user_query}}"
    )

    formal = _render_prompt(prompt, _resolve_llm_inputs(trace, _extract_var_names(prompt)))
    trial = _render_prompt(prompt, _trial_prompt_inputs(prompt, trace))

    assert formal == trial
    assert formal == "节点：v4.pipeline\n原始问题：raw question\n语义问题：question"


def test_prompt_resolver_keeps_raw_spans_without_projection():
    trace = Trace(
        trace_id="raw-trace",
        runtime_type="other",
        spans=[{"name": "raw-root", "attributes": {"query": "raw"}}],
    )

    inputs = _resolve_llm_inputs(trace, {"spans[0].name", "trace_id", "user_query"})

    assert inputs == {"spans[0].name": "raw-root", "trace_id": "raw-trace"}


def test_prompt_placeholders_preserve_line_breaks_and_projection_paths():
    prompt = "{{user_query}}\n{{agent_output}}\nExample literal: {{}}"
    assert _extract_var_names(prompt) == {"user_query", "agent_output"}
    assert _render_prompt(prompt, {"user_query": "question", "agent_output": "answer"}) == (
        "question\nanswer\nExample literal: {{}}"
    )
    assert (
        render(
            "{{spans[0].name}} / {{spans[-1].name}}",
            {"spans": [{"name": "start"}, {"name": "finish"}]},
        )
        == "start / finish"
    )
