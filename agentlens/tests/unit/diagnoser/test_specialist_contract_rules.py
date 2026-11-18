"""User-configured JSON and real tool completion, with source/privacy regressions."""

import json

import pytest

from agentlens.diagnoser.base import ApplicabilitySkipped, execute_with_applicability
from agentlens.diagnoser.definitions.specialists import get_specialist_definition, specialist_spec
from agentlens.diagnoser.registry import ExecutorRegistry
from agentlens.service.datasource.projection.service import project_trace
from agentlens.service.datasource.trace import Trace
from tests.unit.diagnoser.test_specialist_materials import trace


def execute(key, variant, policy, value):
    definition = get_specialist_definition(key, variant)
    spec = specialist_spec(definition, {"policy": {"kind": f"{key}_v1", **policy}})
    executor = ExecutorRegistry.create("rule", spec["executor"], spec)
    return execute_with_applicability(executor, value)


def json_check(value):
    return execute(
        "structured_output_contract",
        "json_object_fields",
        {"fields": [{"name": "approved", "type": "boolean"}, {"name": "count", "type": "integer"}]},
        value,
    )


@pytest.mark.parametrize(
    "output,verdict,codes",
    [
        ('{"approved":true,"count":1,"extra":"private-value"}', "good", []),
        ('{"approved":"false","count":false}', "bad", ["invalid_type", "invalid_type"]),
        ('{"approved":true}', "bad", ["missing_field"]),
        ('{"approved":true,"count":2.5}', "bad", ["invalid_type"]),
        ('{"approved":true,"count":2.0}', "good", []),
        ('{"approved":true,"count":NaN}', "bad", ["invalid_json"]),
        ('{"approved":true,"count":3,"count":1}', "bad", ["invalid_json"]),
        ('```json\n{"approved":true,"count":3}\n```', "bad", ["invalid_json"]),
        ('[{"approved":true,"count":3}]', "bad", ["not_object"]),
    ],
)
def test_whole_final_json_object_type_contract(output, verdict, codes):
    result = json_check(trace(output=output))
    assert result.verdict.value == verdict
    evidence = result.metrics[0].evidence
    assert [issue["code"] for issue in evidence["issues"]] == codes
    assert evidence["source"]["field_path"] == "attributes.aivision.request.output"
    assert "private-value" not in json.dumps(evidence)


def test_application_json_is_checked_without_promoting_unmarked_sdk_state():
    value = trace(output='{"approved":true,"count":7}')
    [row] = value.spans
    explicit = project_trace(
        Trace("t", "langchain", [{**row, "attributes.aivision.request.output.encoding": "json"}])
    )
    assert json_check(explicit).verdict.value == "good"
    raw = {key: value for key, value in row.items() if key != "attributes.aivision.request.output"}
    raw["attributes.langfuse.observation.output"] = '{"approved":true,"count":7}'
    assert isinstance(
        json_check(project_trace(Trace("t", "langchain", [raw]))), ApplicabilitySkipped
    )


def test_captured_length_cutoff_skips_even_if_the_prefix_parses():
    [row] = trace(output='{"approved":true,"count":7}').spans
    row["attributes.gen_ai.response.finish_reasons"] = '["length"]'
    result = json_check(project_trace(Trace("t", "langchain", [row])))
    assert isinstance(result, ApplicabilitySkipped)
    assert result.detail == "unsupported_incomplete_final_output"


@pytest.mark.parametrize(
    "output",
    [
        None,
        '{"approved":"[REDACTED]","count":4}',
        '{"approved":true...[TRUNCATED]',
        '{"approved":true,"count":4,"long":"' + "a" * 32768 + '"}',
        [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "{}"}, {"type": "text", "text": "{}"}],
            }
        ],
    ],
    ids=["missing", "redacted", "truncated", "oversized", "multiple-parts"],
)
def test_incomplete_or_ambiguous_output_skips(output):
    assert isinstance(json_check(trace(output=output)), ApplicabilitySkipped)


def completed_tool(*, name="tenant.retrieve", terminal="success", number=1, **extra):
    start = 1_800_000_000_000_000_000
    row = {
        "trace_id": "t",
        "span_id": f"tool{number}",
        "parent_span_id": "s",
        "name": name,
        "status_code": 2 if terminal == "error" else 0,
        "start_time_unix_nano": start,
        "end_time_unix_nano": start + 1_000_000,
        "attributes.langfuse.observation.type": "tool",
        "attributes.langfuse.observation.output": json.dumps(
            {
                "type": "tool",
                "name": name,
                "content": "private-tool-output",
                "tool_call_id": f"call{number}",
                "status": terminal,
            }
        ),
    }
    row.update(extra)
    return row


def tool_check(*rows, runtime="langchain"):
    value = project_trace(Trace("t", runtime, [*trace().spans, *rows]))
    return execute(
        "required_tool_execution",
        "observed_tool_completion",
        {"tool_name": "tenant.retrieve"},
        value,
    )


def test_actual_sdk_terminal_success_after_a_failed_attempt():
    result = tool_check(completed_tool(terminal="error"), completed_tool(number=2))
    assert result.verdict.value == "good"
    evidence = result.metrics[0].evidence
    assert evidence["source"]["span_id"] == "tool2"
    assert evidence["source"]["call_id"] == "call2"
    assert evidence["source"]["field_path"] == "attributes.langfuse.observation.output.status"
    assert "private-tool-output" not in json.dumps(evidence)


@pytest.mark.parametrize("overlaps", [False, True])
def test_sdk_retry_can_reuse_the_logical_call_id(overlaps):
    failed = completed_tool(terminal="error")
    succeeded = completed_tool(number=2)
    result_key = "attributes.langfuse.observation.output"
    payload = json.loads(succeeded[result_key])
    payload["tool_call_id"] = "call1"
    succeeded[result_key] = json.dumps(payload)
    succeeded["start_time_unix_nano"] = failed["end_time_unix_nano"] + (
        -500_000 if overlaps else 1_000_000
    )
    succeeded["end_time_unix_nano"] = succeeded["start_time_unix_nano"] + 1_000_000
    result = tool_check(failed, succeeded)
    if overlaps:
        assert (
            isinstance(result, ApplicabilitySkipped)
            and result.detail == "missing_required_tool_result"
        )
    else:
        assert result.verdict.value == "good"
        assert result.metrics[0].evidence["source"]["span_id"] == "tool2"


def native_tool(**extra):
    row = {
        key: value
        for key, value in completed_tool().items()
        if not key.startswith("attributes.langfuse.")
    }
    row.update(
        {
            "attributes.gen_ai.operation.name": "execute_tool",
            "attributes.gen_ai.tool.name": "tenant.retrieve",
            "attributes.gen_ai.tool.call.id": "call1",
            "attributes.gen_ai.tool.call.result": '{"storage_gib":140}',
            "status_code": 1,
            **extra,
        }
    )
    return row


def test_native_framework_tool_needs_exact_name_call_result_and_status():
    row = native_tool()
    result = tool_check(row)
    assert result.verdict.value == "good"
    assert (
        result.metrics[0].evidence["source"]["field_path"] == "attributes.gen_ai.tool.call.result"
    )
    row["attributes.agentic.tool.raw_name"] = "other.retrieve"
    assert isinstance(tool_check(row), ApplicabilitySkipped)


@pytest.mark.parametrize(
    "row",
    [
        native_tool(**{"attributes.error.type": "tool_error"}),
        native_tool(**{"attributes.gen_ai.tool.error.type": "ToolError"}),
        native_tool(
            **{
                "attributes.gen_ai.tool.call.result": (
                    '{"isError":true,"content":[{"type":"text","text":"fixture failure"}]}'
                ),
            }
        ),
        completed_tool(**{"attributes.error.type": "tool_error"}),
    ],
    ids=["otel-error-type", "gen-ai-tool-error-type", "mcp-tool-error", "langfuse-tool-error"],
)
def test_success_status_conflicting_with_explicit_tool_error_does_not_pass(row):
    assert isinstance(tool_check(row), ApplicabilitySkipped)


@pytest.mark.parametrize("overlaps", [True, False], ids=["overlapping", "sequential"])
@pytest.mark.parametrize("runtime_shape", ["native", "langfuse"])
def test_conflicting_terminal_blocks_only_an_overlapping_success(runtime_shape, overlaps):
    if runtime_shape == "native":
        conflicted = native_tool(**{"attributes.error.type": "tool_error"})
        successful = native_tool(span_id="tool2")
    else:
        conflicted = completed_tool(**{"attributes.error.type": "tool_error"})
        successful = completed_tool(span_id="tool2")
    successful["start_time_unix_nano"] = conflicted["end_time_unix_nano"] + (
        -500_000 if overlaps else 1_000_000
    )
    successful["end_time_unix_nano"] = successful["start_time_unix_nano"] + 1_000_000

    result = tool_check(conflicted, successful)
    if overlaps:
        assert (
            isinstance(result, ApplicabilitySkipped)
            and result.detail == "missing_required_tool_result"
        )
    else:
        assert result.verdict.value == "good"
        assert result.metrics[0].evidence["source"]["span_id"] == "tool2"


def test_native_tool_result_prose_is_not_interpreted_as_status():
    row = native_tool(**{"attributes.gen_ai.tool.call.result": 'Example: {"isError":true}'})
    assert tool_check(row).verdict.value == "good"


@pytest.mark.parametrize(
    "rows,detail",
    [
        ([], "missing_required_tool_result"),
        ([completed_tool(name="other.retrieve")], "missing_required_tool_result"),
        (
            [completed_tool(**{"attributes.langfuse.observation.metadata.tool_call_id": "other"})],
            "missing_required_tool_result",
        ),
        (
            [completed_tool(**{"status_code": 1, "attributes.langfuse.observation.output": None})],
            "missing_required_tool_result",
        ),
        (
            [completed_tool(**{"attributes.agentic.tool.output.truncated": True})],
            "missing_required_tool_result",
        ),
        ([completed_tool(terminal="error")], "observed_tool_failure_without_confirmed_success"),
        (
            [completed_tool(number=1), completed_tool(number=1, terminal="error")],
            "missing_required_tool_result",
        ),
    ],
)
def test_proposed_wrong_name_truncated_conflicting_or_failed_attempts_do_not_pass(rows, detail):
    result = tool_check(*rows)
    assert isinstance(result, ApplicabilitySkipped) and result.detail == detail
