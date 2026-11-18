"""Independent arithmetic and privacy oracles plus actual MockTransport Judge flow."""

import json
from copy import deepcopy

import httpx
import pytest

from agentlens.diagnoser.base import (
    ApplicabilitySkipped,
    ExecutorFailure,
    execute_with_applicability,
)
from agentlens.diagnoser.definitions.specialists import get_specialist_definition, specialist_spec
from agentlens.diagnoser.executors.llm.builtin.specialists import SpecialistLlmExecutor
from agentlens.diagnoser.executors.rule.specialists import (
    NumericConsistencyExecutor,
    ProtectedContentExecutor,
)
from agentlens.infra.llm_judge import LLMJudge
from agentlens.infra.llm_judge.attempts import TrialAttempts
from tests.unit.diagnoser.test_specialist_materials import trace
from tests.unit.diagnoser.test_specialist_policies import relevance
from tests.unit.diagnoser.test_specialist_sync import configuration


def numeric_spec(*, unit=None, precision=None, checks=None):
    check = {
        "id": "sum",
        "expression": {"field": "current_request", "prefix": "", "suffix": ""},
        "result": {"field": "final_output", "prefix": "", "suffix": ""},
        "precision": precision or {"mode": "exact"},
        "unit": unit,
    }
    raw = {"policy": {"kind": "numeric_consistency_v1", "checks": checks or [check]}}
    return specialist_spec(
        get_specialist_definition(
            "numeric_consistency", "decimal_binary" if unit is None else "decimal_binary_same_unit"
        ),
        raw,
    )


@pytest.mark.parametrize(
    "expression,answer,verdict",
    [
        ("0.1+0.2", "0.3", "good"),
        ("2+2", "5", "bad"),
        ("-3 - -2", "-1", "good"),
        ("12 × 3", "36", "good"),
        ("1÷8", "0.125", "good"),
        ("1/40", "0.025", "good"),
        ("999999999999999999+1", "1", "bad"),
        ("0.000000000001 * 2", "0.000000000002", "good"),
    ],
)
def test_decimal_binary_independent_expected_values(expression, answer, verdict):
    result = NumericConsistencyExecutor(numeric_spec()).evaluate(trace(expression, answer))
    assert result.verdict.value == verdict


@pytest.mark.parametrize(
    "expression,answer",
    [
        ("1/3", "0.333"),
        ("1/0", "0"),
        ("1e2+1", "101"),
        ("01+2", "3"),
        ("(1+2)*3", "9"),
        ("1+2+3", "6"),
        ("2026-10-10", "2026"),
        ("nan+1", "2"),
        ("1234567890123456789+1", "2"),
        ("0.000000000001/10", "0"),
        ("1+2", "3.1234567890123"),
    ],
)
def test_unsupported_subset_and_precision_are_whole_diagnoser_skip(expression, answer):
    outcome = execute_with_applicability(
        NumericConsistencyExecutor(numeric_spec()), trace(expression, answer)
    )
    assert isinstance(outcome, ApplicabilitySkipped)


@pytest.mark.parametrize(
    "rounding,expected",
    [("ROUND_HALF_EVEN", "1.2"), ("ROUND_HALF_UP", "1.3"), ("ROUND_DOWN", "1.2")],
)
def test_explicit_quantized_rounding_has_no_implicit_tolerance(rounding, expected):
    executor = NumericConsistencyExecutor(
        numeric_spec(precision={"mode": "quantized", "scale": 1, "rounding": rounding})
    )
    assert executor.evaluate(trace("5/4", expected)).verdict.value == "good"
    assert executor.evaluate(trace("5/4", "1.24")).verdict.value == "bad"


@pytest.mark.parametrize(
    "expression,answer,outcome",
    [
        ("2m+3m", "5m", "good"),
        ("2m+3m", "5米", "bad"),
        ("2m-3m", "-1m", "good"),
        ("2m+3cm", "5m", "skip"),
        ("2m*3m", "6m", "skip"),
        ("2m+3m", "5", "skip"),
    ],
)
def test_same_unit_exact_not_conversion(expression, answer, outcome):
    result = execute_with_applicability(
        NumericConsistencyExecutor(numeric_spec(unit="m")), trace(expression, answer)
    )
    assert (
        isinstance(result, ApplicabilitySkipped)
        if outcome == "skip"
        else result.verdict.value == outcome
    )


def test_partial_checks_cannot_emit_a_good_metric():
    base = numeric_spec()["specialist_policy"]["checks"][0]
    first = {
        **deepcopy(base),
        "id": "first",
        "expression": {"field": "current_request", "prefix": "A:", "suffix": ";"},
        "result": {"field": "final_output", "prefix": "A:", "suffix": ";"},
    }
    second = {
        **deepcopy(base),
        "id": "second",
        "expression": {"field": "current_request", "prefix": "B:", "suffix": ";"},
        "result": {"field": "final_output", "prefix": "B:", "suffix": ";"},
    }
    result = execute_with_applicability(
        NumericConsistencyExecutor(numeric_spec(checks=[first, second])),
        trace("A:1+2;B:2+3;", "A:3;"),
    )
    assert isinstance(result, ApplicabilitySkipped)


def test_ambiguous_literal_selector_and_cross_leaf_numeric_are_skipped():
    check = numeric_spec()["specialist_policy"]["checks"][0]
    check["expression"] = {"field": "current_request", "prefix": "X:", "suffix": ";"}
    result = execute_with_applicability(
        NumericConsistencyExecutor(numeric_spec(checks=[check])), trace("X:1+2;X:3+4;", "3")
    )
    assert isinstance(result, ApplicabilitySkipped)
    result = execute_with_applicability(
        NumericConsistencyExecutor(numeric_spec()),
        trace(
            [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "1+"}, {"type": "text", "text": "2"}],
                }
            ],
            "3",
        ),
    )
    assert isinstance(result, ApplicabilitySkipped)


def test_numeric_gate_and_evaluation_execute_at_most_eight_operations(monkeypatch):
    import agentlens.diagnoser.executors.rule.specialists as module

    calls = []
    original = module._calculate

    def calculate(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(module, "_calculate", calculate)
    checks = [
        {**deepcopy(numeric_spec()["specialist_policy"]["checks"][0]), "id": f"check_{i}"}
        for i in range(8)
    ]
    result = execute_with_applicability(
        NumericConsistencyExecutor(numeric_spec(checks=checks)), trace("1+2", "3")
    )
    assert result.verdict.value == "good" and len(calls) == 8


def test_private_marker_matching_is_exact_public_and_normal_tool_names_are_allowed():
    raw = configuration("Secret-TOKEN")
    policy = raw.policy.model_dump()
    policy["markers"].append({"id": "tool", "value": "search", "visibility": "public"})
    definition = get_specialist_definition("protected_content_exposure", "configured_exact_markers")
    executor = ProtectedContentExecutor(specialist_spec(definition, {"policy": policy}))
    for final, verdict in [
        ("search is used", "good"),
        ("secret-token", "good"),
        ("Secret-TOKEN Secret-TOKEN", "bad"),
    ]:
        result = executor.evaluate(trace(output=final))
        assert result.verdict.value == verdict
        assert "Secret-TOKEN" not in json.dumps(result.metrics[0].evidence)
        assert "Secret-TOKEN" not in result.summary
    assert (
        executor.evaluate(trace(output="Secret-TOKEN Secret-TOKEN"))
        .metrics[0]
        .evidence["matches"][0]["count"]
        == 2
    )


def test_protected_rule_requires_final_only_and_never_matches_across_two_leaves():
    definition = get_specialist_definition("protected_content_exposure", "configured_exact_markers")
    executor = ProtectedContentExecutor(specialist_spec(definition, configuration("a\nb")))
    value = trace(
        [],
        [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
            }
        ],
    )
    assert executor.evaluate(value).verdict.value == "good"


def test_declared_json_scalar_body_resolves_actual_decoded_leaf():
    value = trace("current", "answer")
    value.spans[0]["attributes.aivision.request.input"] = json.dumps("current")
    value.spans[0]["attributes.aivision.request.input.encoding"] = "json"
    from dataclasses import replace

    from agentlens.service.datasource.projection.service import project_trace

    value = project_trace(replace(value, projection=None))
    executor = SpecialistLlmExecutor(
        specialist_spec(
            get_specialist_definition("answer_relevance", "explicit_current_text"), relevance()
        )
    )
    assert executor.applicability_skip_reason(value) is None


def llm_result(key="answer_relevance", citations=None, reason="回答回应请求"):
    return {
        "metrics": [
            {
                "name": key,
                "verdict": "good",
                "reason": reason,
                "evidence": {
                    "citations": citations
                    or [
                        {"source_id": "request", "quote": "当前请求"},
                        {"source_id": "final", "quote": "回应当前请求"},
                    ]
                },
            }
        ]
    }


def test_semantic_judge_mock_transport_uses_actual_bundle_and_one_request():
    attempts = TrialAttempts()
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(llm_result(), ensure_ascii=False)}}]
            },
        )

    executor = SpecialistLlmExecutor(
        specialist_spec(
            get_specialist_definition("answer_relevance", "explicit_current_text"), relevance()
        )
    )
    executor._judge = LLMJudge(
        "https://example.invalid/v1",
        "fixture",
        "fake",
        transport=httpx.MockTransport(handler),
        max_tokens=4096,
        backoff_sec=0,
    )
    executor.attempt_hooks_for = lambda _: attempts
    try:
        result = execute_with_applicability(executor, trace())
        assert result.verdict.value == "good" and attempts.reserved == len(calls) == 1
        assert "当前请求" in calls[0]["messages"][1]["content"]
        assert calls[0]["max_tokens"] == 4096
    finally:
        executor.close()


@pytest.mark.parametrize(
    "citations,reason",
    [
        ([{"source_id": "request", "quote": "当前请求"}], "reason"),
        ([{"source_id": "unknown", "quote": "当前请求"}], "reason"),
        (
            [
                {"source_id": "request", "quote": "fabricated"},
                {"source_id": "final", "quote": "回应当前请求"},
            ],
            "reason",
        ),
        (
            [
                {"source_id": "request", "quote": "当前请求", "extra": "x"},
                {"source_id": "final", "quote": "回应当前请求"},
            ],
            "reason",
        ),
        (
            [
                {"source_id": "request", "quote": "当前请求"},
                {"source_id": "final", "quote": "回应当前请求"},
            ],
            "x" * 2049,
        ),
    ],
)
def test_semantic_invalid_citations_are_failed_without_fix_or_retry(citations, reason):
    class Judge:
        def judge(self, *args, **kwargs):
            return llm_result(citations=citations, reason=reason)

        def close(self):
            pass

    executor = SpecialistLlmExecutor(
        specialist_spec(
            get_specialist_definition("answer_relevance", "explicit_current_text"), relevance()
        )
    )
    executor._judge = Judge()
    with pytest.raises(ExecutorFailure, match="专项"):
        executor.evaluate(trace())


def test_malformed_response_trial_attempts_stop_at_three(monkeypatch):
    attempts = TrialAttempts()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    executor = SpecialistLlmExecutor(
        specialist_spec(
            get_specialist_definition("answer_relevance", "explicit_current_text"), relevance()
        )
    )
    executor._judge = LLMJudge(
        "https://example.invalid/v1",
        "fixture",
        "fake",
        transport=httpx.MockTransport(handler),
        backoff_sec=0,
    )
    executor.attempt_hooks_for = lambda _: attempts
    with pytest.raises(ExecutorFailure, match="LLM"):
        executor.evaluate(trace())
    executor.close()
    assert len(calls) == attempts.reserved == 3


def test_language_is_semantic_and_requires_explicit_policy_citation():
    configuration = {
        "policy": {
            "kind": "response_language_v1",
            "required_language": "zh-CN",
            "scope": "final_natural_language",
            "allowed_terms": ["API"],
        },
        "model_connection_uuid": "model",
    }
    executor = SpecialistLlmExecutor(
        specialist_spec(
            get_specialist_definition("response_language", "configured_final_text"), configuration
        )
    )

    class Judge:
        def judge(self, *args, **kwargs):
            return llm_result("response_language")

        def close(self):
            pass

    executor._judge = Judge()
    with pytest.raises(ExecutorFailure, match="必要材料"):
        executor.evaluate(trace())

    class CitedJudge(Judge):
        def judge(self, *args, **kwargs):
            return llm_result(
                "response_language",
                citations=[
                    {"source_id": "request", "quote": "当前请求"},
                    {"source_id": "final", "quote": "回应当前请求"},
                    {"source_id": "policy:language", "quote": "zh-CN"},
                ],
            )

    executor._judge = CitedJudge()
    result = executor.evaluate(trace())
    assert result.verdict.value == "good"


def test_conversation_variant_requires_history_citation_and_does_not_classify_intent():
    configuration = {
        "policy": {"kind": "followup_reasonableness_v1", "context_mode": "recorded_conversation"},
        "model_connection_uuid": "model",
    }
    executor = SpecialistLlmExecutor(
        specialist_spec(
            get_specialist_definition("followup_reasonableness", "recorded_conversation"),
            configuration,
        )
    )

    class Judge:
        def judge(self, *args, **kwargs):
            return llm_result("followup_reasonableness")

        def close(self):
            pass

    executor._judge = Judge()
    with pytest.raises(ExecutorFailure, match="必要材料"):
        executor.evaluate(trace())
