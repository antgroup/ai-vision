"""Tests for coding adapter: raw rows → CodingProjection.

Verifies Turn identity, tool action extraction, file mutation derivation,
verification detection, and source-trace projection.
"""

import json
from datetime import datetime

from agentlens.diagnoser.executors.rule.builtin.coding.test_execution import (
    TestExecutionExecutor as BuiltinTestExecutionExecutor,
)
from agentlens.domain.enums import Verdict
from agentlens.service.datasource.projection.projectors.coding.adapter import project_coding_raw
from agentlens.service.datasource.trace import Trace


def _chat_row(
    span_id: str,
    parent: str | None,
    query: str,
    output: str,
    kind: str = "SPAN_KIND_SERVER",
) -> dict:
    return {
        "span_id": span_id,
        "parent_span_id": parent or "",
        "trace_id": "trace-1",
        "name": "Turn 1" if kind == "SPAN_KIND_SERVER" else "chat model",
        "kind": kind,
        "start_time_unix_nano": "1716811200000000000",
        "end_time_unix_nano": "1716811210000000000",
        "status_code": 1,
        "attributes.gen_ai.input.messages": json.dumps([{"role": "user", "content": query}]),
        "attributes.gen_ai.output.messages": json.dumps([{"role": "assistant", "content": output}]),
        "attributes.gen_ai.span.kind": "CHAT" if kind == "SPAN_KIND_SERVER" else "LLM",
    }


def _tool_row(
    span_id: str,
    parent: str,
    name: str,
    call_id: str | None = None,
    args: dict | None = None,
    result: dict | None = None,
    status: int = 1,
) -> dict:
    row = {
        "span_id": span_id,
        "parent_span_id": parent,
        "trace_id": "trace-1",
        "name": f"tool {name}",
        "kind": "SPAN_KIND_INTERNAL",
        "start_time_unix_nano": "1716811201000000000",
        "end_time_unix_nano": "1716811202000000000",
        "status_code": status,
        "attributes.gen_ai.span.kind": "TOOL",
    }
    if call_id:
        row["attributes.gen_ai.tool.call.id"] = call_id
    if args is not None:
        row["attributes.gen_ai.tool.call.arguments"] = json.dumps(args)
    if result is not None:
        row["attributes.gen_ai.tool.call.result"] = json.dumps(result)
    return row


class TestSingleTurnProjection:
    def test_one_projection_per_trace(self):
        rows = [
            _chat_row("turn-1", None, "fix the bug", "done"),
            _tool_row(
                "tool-1",
                "turn-1",
                "edit",
                call_id="call-1",
                args={"path": "/a.py", "old_string": "x", "new_string": "y"},
            ),
        ]
        projections = project_coding_raw(rows, "trace-1")
        assert len(projections) == 1

    def test_user_query_extracted(self):
        rows = [_chat_row("turn-1", None, "fix the bug", "done")]
        projections = project_coding_raw(rows, "trace-1")
        assert projections[0].user_query == "fix the bug"

    def test_agent_output_extracted(self):
        rows = [_chat_row("turn-1", None, "fix the bug", "done")]
        projections = project_coding_raw(rows, "trace-1")
        assert projections[0].agent_output == "done"

    def test_agent_output_uses_latest_llm_output(self):
        turn = _chat_row("turn-1", None, "fix the bug", "")
        turn.pop("attributes.gen_ai.output.messages")
        first_llm = _chat_row("llm-1", "turn-1", "", "先理解问题", kind="SPAN_KIND_INTERNAL")
        last_llm = _chat_row("llm-2", "turn-1", "", "最终完成修复", kind="SPAN_KIND_INTERNAL")
        first_llm["start_time_unix_nano"] = "1716811202000000000"
        last_llm["start_time_unix_nano"] = "1716811203000000000"

        projection = project_coding_raw([turn, first_llm, last_llm], "trace-1")[0]

        assert projection.agent_output == "最终完成修复"
        assert projection.agent_output_span_id == "llm-2"

    def test_turn_envelope_output_precedes_llm_output(self):
        turn = _chat_row("turn-1", None, "fix the bug", "最终回答")
        llm = _chat_row("llm-1", "turn-1", "", "中间理解", kind="SPAN_KIND_INTERNAL")

        projection = project_coding_raw([llm, turn], "trace-1")[0]

        assert projection.agent_output == "最终回答"
        assert projection.agent_output_span_id == "turn-1"

    def test_empty_turn_envelope_preserves_current_role_boundary(self):
        turn = _chat_row("turn-1", None, "fix the bug", "")
        previous_llm = _chat_row("llm-1", "turn-1", "", "上一个非空输出", kind="SPAN_KIND_INTERNAL")
        last_llm = _chat_row("llm-2", "turn-1", "", "", kind="SPAN_KIND_INTERNAL")
        previous_llm["start_time_unix_nano"] = "1716811202000000000"
        last_llm["start_time_unix_nano"] = "1716811203000000000"

        projection = project_coding_raw([turn, previous_llm, last_llm], "trace-1")[0]

        assert projection.agent_output == ""
        assert projection.agent_output_span_id is None
        assert projection.final_output is None


class TestInterruptionExtraction:
    def test_antcc_cancelled_turn_name_marks_interrupted(self):
        row = _chat_row("turn-1", None, "q", "a")
        row["name"] = "Turn 1 (Cancelled)"

        projection = project_coding_raw([row], "trace-1")[0]

        assert projection.outcome.interrupted is True
        assert projection.outcome.has_error is False

    def test_codefuse_cancelled_root_status_marks_interrupted(self):
        row = _chat_row("turn-1", None, "q", "a")
        row["status_code"] = 2
        row["attributes.error.type"] = "cancelled"
        row["status_message"] = "cancelled"

        projection = project_coding_raw([row], "trace-1")[0]

        assert projection.outcome.interrupted is True
        assert projection.outcome.has_error is True

    def test_codex_regular_turn_does_not_infer_interruption(self):
        projection = project_coding_raw([_chat_row("turn-1", None, "q", "a")], "trace-1")[0]

        assert projection.outcome.interrupted is False


class TestToolActionExtraction:
    def test_tool_action_with_call_id(self):
        rows = [
            _chat_row("turn-1", None, "q", "a"),
            _tool_row("tool-1", "turn-1", "edit", call_id="call-1"),
        ]
        projections = project_coding_raw(rows, "trace-1")
        actions = projections[0].tool_actions
        assert len(actions) == 1
        assert actions[0].call_id == "call-1"
        assert actions[0].identity_source == "tool_call"

    def test_tool_action_without_call_id_uses_span_identity(self):
        rows = [
            _chat_row("turn-1", None, "q", "a"),
            _tool_row("tool-1", "turn-1", "bash", call_id=None),
        ]
        projections = project_coding_raw(rows, "trace-1")
        actions = projections[0].tool_actions
        assert actions[0].call_id is None
        assert actions[0].identity_source == "span"

    def test_tool_action_status_error(self):
        rows = [
            _chat_row("turn-1", None, "q", "a"),
            _tool_row("tool-1", "turn-1", "bash", status=2),
        ]
        projections = project_coding_raw(rows, "trace-1")
        assert projections[0].tool_actions[0].status == "error"


class TestFileMutationDerivation:
    def test_edit_derives_file_mutation(self):
        rows = [
            _chat_row("turn-1", None, "q", "a"),
            _tool_row(
                "tool-1",
                "turn-1",
                "edit",
                call_id="c1",
                args={"path": "/a.py", "old_string": "x", "new_string": "y"},
            ),
        ]
        projections = project_coding_raw(rows, "trace-1")
        assert len(projections[0].file_mutations) == 1
        mut = projections[0].file_mutations[0]
        assert mut.path == "/a.py"
        assert mut.evidence_level == "exact"

    def test_non_write_tool_no_mutation(self):
        rows = [
            _chat_row("turn-1", None, "q", "a"),
            _tool_row("tool-1", "turn-1", "bash", call_id="c1", args={"command": "ls"}),
        ]
        projections = project_coding_raw(rows, "trace-1")
        assert len(projections[0].file_mutations) == 0


class TestVerificationDetection:
    def test_pytest_is_verification(self):
        rows = [
            _chat_row("turn-1", None, "q", "a"),
            _tool_row("tool-1", "turn-1", "pytest", call_id="c1"),
        ]
        projections = project_coding_raw(rows, "trace-1")
        assert len(projections[0].verification_actions) == 1

    def test_edit_is_not_verification(self):
        rows = [
            _chat_row("turn-1", None, "q", "a"),
            _tool_row("tool-1", "turn-1", "edit", call_id="c1", args={"path": "/a.py"}),
        ]
        projections = project_coding_raw(rows, "trace-1")
        assert len(projections[0].verification_actions) == 0


def test_unsorted_rows_are_ordered_before_test_execution_evaluation():
    chat = _chat_row("turn-1", None, "fix the bug", "done")
    edit = _tool_row(
        "edit-1",
        "turn-1",
        "edit",
        call_id="edit-call",
        args={"path": "/a.py", "old_string": "before", "new_string": "after"},
    )
    verification = _tool_row(
        "verify-1",
        "turn-1",
        "bash",
        call_id="verify-call",
        args={"command": "pytest -q"},
    )
    verification["start_time_unix_nano"] = "1716811203000000000"
    verification["end_time_unix_nano"] = "1716811204000000000"
    rows = [verification, edit, chat]

    projection = project_coding_raw(rows, "trace-1")[0]
    spec = {"metrics": [{"name": "test_execution_rate"}]}
    trace = Trace(
        trace_id="trace-1",
        runtime_type="codex",
        spans=rows,
        projection=projection,
    )
    result = BuiltinTestExecutionExecutor(spec).evaluate(trace).metric("test_execution_rate")

    assert [row["span_id"] for row in rows] == ["verify-1", "edit-1", "turn-1"]
    assert [step.ref for step in projection.steps] == ["edit-1", "verify-1"]
    assert result is not None
    assert result.verdict is Verdict.GOOD


class TestTracePerTurnContract:
    def test_multiple_chat_spans_are_not_projected(self):
        rows = [
            _chat_row("turn-1", None, "q1", "a1"),
            _chat_row("turn-2", None, "q2", "a2"),
        ]

        assert project_coding_raw(rows, "trace-1") == []


class TestRealSigNozTimeFormat:
    """Verify adapter handles ISO-8601 timestamps from real SigNoz rows."""

    def test_iso_8601_timestamps_project_without_error(self):
        rows = [
            {
                "span_id": "turn-1",
                "parent_span_id": "",
                "trace_id": "t-1",
                "name": "Turn 1",
                "kind": "SPAN_KIND_SERVER",
                "start_time_unix_nano": "2026-07-23T14:21:35.976+08:00",
                "end_time_unix_nano": "2026-07-23T14:22:35.976+08:00",
                "status_code": 1,
                "attributes.gen_ai.input.messages": json.dumps(
                    [{"role": "user", "content": "hello"}]
                ),
                "attributes.gen_ai.output.messages": json.dumps(
                    [{"role": "assistant", "content": "hi"}]
                ),
            }
        ]
        projections = project_coding_raw(rows, "t-1")
        assert len(projections) == 1
        assert projections[0].user_query == "hello"
        assert projections[0].agent_output == "hi"
        assert projections[0].outcome.total_latency_ms is not None
        assert projections[0].outcome.total_latency_ms > 0

    def test_nanosecond_timestamps_project(self):
        rows = [
            {
                "span_id": "turn-1",
                "parent_span_id": "",
                "trace_id": "t-1",
                "name": "Turn 1",
                "kind": "SPAN_KIND_SERVER",
                "start_time_unix_nano": "1716811200000000000",
                "end_time_unix_nano": "1716811210000000000",
                "status_code": 1,
            }
        ]
        projections = project_coding_raw(rows, "t-1")
        assert len(projections) == 1
        assert datetime.fromisoformat(projections[0].timestamp).tzinfo is not None
        assert projections[0].outcome.total_latency_ms is not None
        assert projections[0].outcome.total_latency_ms > 0

    def test_mixed_time_formats_in_same_trace(self):
        """Some rows ISO, some nano — adapter handles both."""
        rows = [
            {
                "span_id": "turn-1",
                "parent_span_id": "",
                "trace_id": "t-1",
                "name": "Turn 1",
                "kind": "SPAN_KIND_SERVER",
                "start_time_unix_nano": "2026-07-23T14:21:35+08:00",
                "end_time_unix_nano": "2026-07-23T14:22:35+08:00",
                "status_code": 1,
            },
            {
                "span_id": "tool-1",
                "parent_span_id": "turn-1",
                "trace_id": "t-1",
                "name": "tool bash",
                "kind": "SPAN_KIND_INTERNAL",
                "start_time_unix_nano": "1716811201000000000",
                "end_time_unix_nano": "1716811202000000000",
                "status_code": 1,
                "attributes.gen_ai.tool.call.id": "call-1",
            },
        ]
        projections = project_coding_raw(rows, "t-1")
        assert len(projections) == 1
        assert len(projections[0].tool_actions) == 1
