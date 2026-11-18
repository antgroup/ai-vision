"""Real source leaf, alias and ordered-history boundaries, without a Provider."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from agentlens.diagnoser.base import ApplicabilitySkipped, ExecutorFailure
from agentlens.diagnoser.definitions.specialists import get_specialist_definition, specialist_spec
from agentlens.service.datasource.projection.models import SourceReference
from agentlens.service.datasource.projection.service import project_trace
from agentlens.service.datasource.specialist_materials import (
    build_specialist_materials,
    resolve_source_leaf,
)
from agentlens.service.datasource.trace import Trace
from tests.unit.diagnoser.test_specialist_policies import relevance


def trace(messages=None, output="回应当前请求", *, encoding=None):
    messages = (
        messages
        if messages is not None
        else [
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
            {"role": "user", "content": "当前请求"},
        ]
    )
    row = {
        "trace_id": "t",
        "span_id": "s",
        "parent_span_id": "",
        "name": "invoke",
        "attributes.aivision.request.input": messages,
        "attributes.aivision.request.output": output,
    }
    if encoding:
        row["attributes.aivision.request.input"] = json.dumps(messages, ensure_ascii=False)
        row["attributes.aivision.request.input.encoding"] = encoding
    return project_trace(Trace("t", "langchain", [row]), require_text_evidence=False)


def spec(variant="recorded_conversation", key="answer_relevance"):
    raw = relevance()
    raw["policy"] = {"kind": f"{key}_v1", "context_mode": variant}
    return specialist_spec(get_specialist_definition(key, variant), raw)


def test_ordered_history_uses_actual_indices_even_with_repeated_equal_messages():
    value = trace(
        [
            {"role": "user", "content": "重复"},
            {"role": "assistant", "content": "回答"},
            {"role": "user", "content": "重复"},
        ]
    )
    bundle = build_specialist_materials(value, spec())
    assert bundle["history:0"]["content"] == "重复"
    assert bundle["request"]["sources"][0]["field_path"].endswith("[2].content")
    assert bundle["history:1"]["message_index"] == 1


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"role": "user", "content": "first"}],
        [{"role": "system", "content": "policy"}, {"role": "user", "content": "current"}],
        [
            {"role": "tool", "content": "result"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "current"},
        ],
    ],
)
def test_unknown_or_empty_history_is_not_a_proven_first_turn(items):
    with pytest.raises(ApplicabilitySkipped):
        build_specialist_materials(trace(items), spec(key="followup_reasonableness"))


def test_oversized_history_is_skipped_without_taking_last_ten():
    items = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": str(i)} for i in range(42)
    ] + [{"role": "user", "content": "current"}]
    with pytest.raises(ApplicabilitySkipped, match="history_limit_exceeded"):
        build_specialist_materials(trace(items), spec())


def test_explicit_application_json_envelope_is_resolved_and_unmarked_body_is_literal():
    value = trace(encoding="json")
    bundle = build_specialist_materials(value, spec())
    assert bundle["request"]["content"] == "当前请求"
    body = '[{"role":"user","content":"literal"}]'
    literal = trace(body)
    assert (
        build_specialist_materials(literal, spec("explicit_current_text"))["request"]["content"]
        == body
    )


def test_partly_visible_image_request_does_not_degrade_to_text_only():
    value = trace(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图片"},
                    {"type": "image_url", "image_url": "https://example.invalid/image"},
                ],
            }
        ]
    )
    with pytest.raises(ApplicabilitySkipped, match="unsupported_required_evidence"):
        build_specialist_materials(value, spec("explicit_current_text"))


def test_latest_tool_only_assistant_is_not_older_visible_final():
    value = trace(
        output=[
            {"role": "assistant", "content": "older"},
            {"role": "assistant", "tool_calls": [{"id": "x"}]},
        ]
    )
    with pytest.raises(ApplicabilitySkipped, match="missing_required_final"):
        build_specialist_materials(value, spec("explicit_current_text"))


def test_projection_text_cannot_manufacture_a_nonexistent_leaf():
    value = trace()
    value.projection.current_request = replace(
        value.projection.current_request, content="fabricated"
    )
    with pytest.raises(ExecutorFailure, match="实际 leaf"):
        build_specialist_materials(value, spec("explicit_current_text"))


@pytest.mark.parametrize(
    "path",
    [
        "missing.content",
        "attributes.aivision.request.input[*]",
        "attributes.aivision.request.input[10001].content",
        "attributes.aivision.request.input.__class__",
        "attributes.aivision.request.input[0].unknown",
    ],
)
def test_source_paths_are_bounded_actual_leaves(path):
    with pytest.raises(ExecutorFailure):
        resolve_source_leaf(trace(), SourceReference("t", "s", path))


def test_duplicate_span_and_foreign_trace_sources_fail():
    value = trace()
    value.spans.append(deepcopy(value.spans[0]))
    with pytest.raises(ExecutorFailure):
        build_specialist_materials(value, spec("explicit_current_text"))
    with pytest.raises(ExecutorFailure):
        resolve_source_leaf(
            trace(), SourceReference("foreign", "s", "attributes.aivision.request.output")
        )


def test_sdk_normalization_alias_citations_resolve_original_field():
    row = {
        "trace_id": "t",
        "span_id": "s",
        "name": "invoke",
        "parent_span_id": "",
        "attributes.gen_ai.input.messages": [{"role": "user", "content": "current"}],
        "attributes.langfuse.observation.input": json.dumps(
            [{"role": "user", "content": "current"}]
        ),
        "attributes.gen_ai.output.messages": [{"role": "assistant", "content": "answer"}],
        "attributes.langfuse.observation.output": json.dumps(
            [{"role": "assistant", "content": "answer"}]
        ),
        "attributes.aivision.normalization.sources": {
            "gen_ai.input.messages": "langfuse.observation.input",
            "gen_ai.output.messages": "langfuse.observation.output",
        },
    }
    value = project_trace(Trace("t", "langchain", [row]), require_text_evidence=False)
    bundle = build_specialist_materials(value, spec("explicit_current_text"))
    assert "langfuse.observation.input" in bundle["request"]["sources"][0]["field_path"]


def test_corrupt_projector_is_failed_and_privacy_is_zero_judge_skip():
    broken = trace(encoding="not-json")
    with pytest.raises(ExecutorFailure, match="投影失败"):
        build_specialist_materials(broken, spec())
    with pytest.raises(ApplicabilitySkipped, match="privacy_redacted"):
        build_specialist_materials(trace(output="[REDACTED]"), spec())
