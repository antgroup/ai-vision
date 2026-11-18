"""Real raw SDK projection/material gates; labels are NOT fake Judge accuracy proof."""

import json
from collections import Counter
from pathlib import Path

import pytest

from agentlens.diagnoser.definitions.catalog import get_core_definition
from agentlens.diagnoser.executors.llm.builtin.general import GeneralLlmExecutor
from agentlens.service.datasource.projection.service import project_trace
from agentlens.service.datasource.trace import Trace

CORPUS = json.loads((Path(__file__).parent / "fixtures/core_oracle_candidates.json").read_text())


def test_each_variant_has_independent_positive_negative_and_missing_candidates():
    counts = Counter((c["canonical_variant"], c["oracle_label"]) for c in CORPUS["cases"])
    assert len(counts) == 12
    assert all(count == 2 for count in counts.values())
    assert all(c["rationale"] for c in CORPUS["cases"])


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda c: c["id"])
def test_raw_sdk_material_gate_matches_independent_candidate_scope(case):
    trace = project_trace(Trace(case["trace_id"], case["runtime_type"], case["spans"]))
    executor = GeneralLlmExecutor(
        get_core_definition(case["builtin_key"], case["canonical_variant"])["spec"]
    )
    reason = executor.applicability_skip_reason(trace)
    assert (reason is not None) == (case["gate_expected"] == "skip")
    if reason is None:
        bundle = executor._extra_prompt_inputs(trace, trace.require_projection())["evidence_bundle"]
        assert bundle["request"]["content"] and bundle["final"]["content"]
        assert all(item["sources"] for item in bundle.values())
    # No Judge receives oracle_label. Semantic GOOD/BAD requires root's second
    # annotation and real bounded model calibration; gate success is not accuracy.


@pytest.mark.parametrize(
    "case_id,field",
    [
        ("artifact-write-good", "content"),
        ("artifact-write-good", "target"),
        ("artifact-write-good", "result"),
        ("artifact-check-good", "result"),
        ("artifact-check-good", "partial_result"),
        ("report-pass-good", "result"),
        ("report-pass-good", "partial_result"),
    ],
)
@pytest.mark.parametrize("additional_visible_fact", [False, True])
def test_required_redacted_facts_skip_raw_projection_before_judge_and_claim(
    case_id, field, additional_visible_fact
):
    from copy import deepcopy

    import httpx

    from agentlens.diagnoser.base import execute_with_applicability
    from agentlens.infra.llm_judge import LLMJudge
    from agentlens.infra.llm_judge.attempts import TrialAttempts

    case = deepcopy(next(c for c in CORPUS["cases"] if c["id"] == case_id))
    tool = next(s for s in case["spans"] if s["span_id"] == "tool")
    if additional_visible_fact:
        visible = deepcopy(tool)
        visible["span_id"] = "visible-tool"
        visible["attributes.gen_ai.tool.call.id"] = "visible-call"
        case["spans"].append(visible)
    if field in {"content", "target"}:
        args = json.loads(tool["attributes.gen_ai.tool.call.arguments"])
        args["content" if field == "content" else "file_path"] = "[REDACTED]"
        tool["attributes.gen_ai.tool.call.arguments"] = json.dumps(args)
    else:
        tool["attributes.gen_ai.tool.call.result"] = json.dumps(
            {"exit_code": 0, "stdout": "[REDACTED]"} if field == "partial_result" else "[REDACTED]"
        )
    trace = project_trace(Trace(case["trace_id"], case["runtime_type"], case["spans"]))
    executor = GeneralLlmExecutor(
        get_core_definition(case["builtin_key"], case["canonical_variant"])["spec"]
    )
    posts = []
    hooks = TrialAttempts()
    judge = LLMJudge(
        "https://example.test/v1",
        "dummy",
        "fixture",
        backoff_sec=0,
        transport=httpx.MockTransport(
            lambda request: posts.append(request) or httpx.Response(200, json={"choices": []})
        ),
    )
    executor._judge = judge
    executor.attempt_hooks_for = lambda _trace_id: hooks
    try:
        result = execute_with_applicability(executor, trace)
        assert result.code == "privacy_redacted"
        assert hooks.reserved == 0 and posts == []
    finally:
        judge.close()


@pytest.mark.parametrize(
    "case_id", ["artifact-write-good", "artifact-check-good", "report-pass-good"]
)
def test_unredacted_required_fact_control_remains_judgable(case_id):
    case = next(c for c in CORPUS["cases"] if c["id"] == case_id)
    trace = project_trace(Trace(case["trace_id"], case["runtime_type"], case["spans"]))
    executor = GeneralLlmExecutor(
        get_core_definition(case["builtin_key"], case["canonical_variant"])["spec"]
    )
    assert executor.applicability_skip_reason(trace) is None


@pytest.mark.parametrize("case_id", ["text-csv-good", "ground-supported", "report-pass-good"])
def test_redacted_unrelated_tool_material_does_not_change_other_core_gate(case_id):
    from copy import deepcopy

    case = deepcopy(next(c for c in CORPUS["cases"] if c["id"] == case_id))
    case["spans"].append(
        {
            "trace_id": "oracle",
            "span_id": "unrelated",
            "parent_span_id": "root",
            "name": "TOOL",
            "status_code": 1,
            "start_time_unix_nano": 250,
            "end_time_unix_nano": 260,
            "attributes.gen_ai.span.kind": "TOOL",
            "attributes.gen_ai.tool.name": "shell",
            "attributes.gen_ai.operation.name": "execute_tool",
            "attributes.gen_ai.tool.call.id": "other-call",
            "attributes.gen_ai.tool.call.arguments": json.dumps({"command": "unrelated-command"}),
            "attributes.gen_ai.tool.call.result": json.dumps("[REDACTED]"),
        }
    )
    trace = project_trace(Trace(case["trace_id"], case["runtime_type"], case["spans"]))
    executor = GeneralLlmExecutor(
        get_core_definition(case["builtin_key"], case["canonical_variant"])["spec"]
    )
    assert executor.applicability_skip_reason(trace) is None
