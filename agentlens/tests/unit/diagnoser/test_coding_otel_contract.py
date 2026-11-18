"""OTel semantic contract tests for CodingProjector span classification.

These tests define what CAN be treated as CHAT, LLM, TOOL, etc.
They do NOT test implementation — they test the contract that the
adapter MUST satisfy. Production code is not changed in this commit.

Key principle: SPAN_KIND_INTERNAL alone is NEVER sufficient to classify
a span as a tool. Standard gen_ai.* attributes are the primary signal;
runtime-specific fallbacks require cross-evidence.
"""

import json
from dataclasses import replace

import pytest

from agentlens.diagnoser.base import ApplicabilitySkipped
from agentlens.diagnoser.executors.rule.builtin.base import get_tool_actions
from agentlens.diagnoser.executors.rule.builtin.coding.rewrite_overlap import (
    RewriteOverlapExecutor,
)
from agentlens.diagnoser.executors.rule.builtin.coding.test_execution import (
    TestExecutionExecutor as BuiltinTestExecutionExecutor,
)
from agentlens.diagnoser.executors.rule.builtin.common.high_risk_command import (
    HighRiskCommandExecutor,
)
from agentlens.diagnoser.executors.rule.builtin.common.sensitive_leakage import (
    SensitiveLeakageExecutor,
)
from agentlens.diagnoser.executors.rule.builtin.common.stuck_loop import StuckLoopExecutor
from agentlens.diagnoser.executors.rule.builtin.common.tool_success import ToolSuccessExecutor
from agentlens.domain.enums import Verdict
from agentlens.domain.metric_definition import metric_definitions
from agentlens.service.datasource.projection.projectors.coding.adapter import (
    _extract_tool_action,
    _is_chat_span,
    _is_llm_span,
    _is_tool_span,
    project_coding_raw,
)
from agentlens.service.datasource.trace import Trace


def _measure(executor_type, metric_name, projection, spec):
    executor_spec = {"metrics": [{"name": metric_name}]}
    executor = executor_type(executor_spec)
    metric = metric_definitions(executor_spec)[0]
    trace = Trace(
        trace_id="test-trace",
        runtime_type="codex",
        spans=[],
        projection=projection,
    )
    return executor.measure(trace, projection, metric)


def _measure_tool_success_rate(projection, spec):
    return _measure(ToolSuccessExecutor, "tool_success_rate", projection, spec)


def _measure_high_risk_command_execution(projection, spec):
    return _measure(
        HighRiskCommandExecutor,
        "high_risk_command_execution",
        projection,
        spec,
    )


def _measure_rewrite_overlap_rate(projection, spec):
    return _measure(RewriteOverlapExecutor, "rewrite_overlap_rate", projection, spec)


def _measure_secret_leakage_block_rate(projection, spec):
    return _measure(
        SensitiveLeakageExecutor,
        "secret_leakage_block_rate",
        projection,
        spec,
    )


def _measure_stuck_loop_health(projection, spec):
    return _measure(StuckLoopExecutor, "stuck_loop_health", projection, spec)


def _measure_test_execution_rate(projection, spec):
    return _measure(BuiltinTestExecutionExecutor, "test_execution_rate", projection, spec)


# ── Sanitized fixtures derived from real corpus shapes ────────────────


def _chat_span(span_id="chat-1", parent=None, query="hello", output="hi") -> dict:
    """CHAT turn span — SERVER span with gen_ai input/output messages."""
    return {
        "span_id": span_id,
        "parent_span_id": parent or "",
        "trace_id": "t-1",
        "name": "Turn 1",
        "kind": "SPAN_KIND_SERVER",
        "start_time_unix_nano": "2026-07-23T14:21:35.976+08:00",
        "end_time_unix_nano": "2026-07-23T14:22:35.976+08:00",
        "status_code": 1,
        "attributes.gen_ai.span.kind": "CHAT",
        "attributes.gen_ai.operation.name": "invoke_agent",
        "attributes.gen_ai.input.messages": json.dumps([{"role": "user", "content": query}]),
        "attributes.gen_ai.output.messages": json.dumps([{"role": "assistant", "content": output}]),
    }


def _llm_span(span_id="llm-1", parent="chat-1") -> dict:
    """LLM generation span — INTERNAL with gen_ai.span.kind=LLM."""
    return {
        "span_id": span_id,
        "parent_span_id": parent,
        "trace_id": "t-1",
        "name": "chat gpt-5.6-sol",
        "kind": "SPAN_KIND_INTERNAL",
        "start_time_unix_nano": "2026-07-23T14:21:36.000+08:00",
        "end_time_unix_nano": "2026-07-23T14:21:38.000+08:00",
        "status_code": 1,
        "attributes.gen_ai.span.kind": "LLM",
        "attributes.gen_ai.operation.name": "chat",
        "attributes.gen_ai.request.model": "gpt-5.6-sol",
        "attributes.gen_ai.usage.input_tokens": 100,
        "attributes.gen_ai.usage.output_tokens": 50,
    }


def _tool_span(
    span_id="tool-1",
    parent="chat-1",
    name="tool Edit",
    call_id="call-1",
    args=None,
    result=None,
    status_code=1,
) -> dict:
    """TOOL span — INTERNAL with gen_ai.span.kind=TOOL."""
    row = {
        "span_id": span_id,
        "parent_span_id": parent,
        "trace_id": "t-1",
        "name": name,
        "kind": "SPAN_KIND_INTERNAL",
        "start_time_unix_nano": "2026-07-23T14:21:38.000+08:00",
        "end_time_unix_nano": "2026-07-23T14:21:39.000+08:00",
        "status_code": status_code,
        "attributes.gen_ai.span.kind": "TOOL",
        "attributes.gen_ai.operation.name": "execute_tool",
        "attributes.gen_ai.tool.call.id": call_id,
    }
    if args is not None:
        row["attributes.gen_ai.tool.call.arguments"] = json.dumps(args)
    if result is not None:
        row["attributes.gen_ai.tool.call.result"] = json.dumps(result)
    return row


def _internal_non_tool(span_id="wrapper-1", parent="chat-1", name="wrapper") -> dict:
    """INTERNAL span that is NOT a tool — control/wrapper/lifecycle."""
    return {
        "span_id": span_id,
        "parent_span_id": parent,
        "trace_id": "t-1",
        "name": name,
        "kind": "SPAN_KIND_INTERNAL",
        "start_time_unix_nano": "2026-07-23T14:21:37.000+08:00",
        "end_time_unix_nano": "2026-07-23T14:21:37.500+08:00",
        "status_code": 1,
    }


def _compaction_span(span_id="compact-1", parent="chat-1") -> dict:
    """Compaction span — has agentic.compaction.* attributes."""
    return {
        "span_id": span_id,
        "parent_span_id": parent,
        "trace_id": "t-1",
        "name": "compaction",
        "kind": "SPAN_KIND_INTERNAL",
        "start_time_unix_nano": "2026-07-23T14:21:40.000+08:00",
        "end_time_unix_nano": "2026-07-23T14:21:40.500+08:00",
        "status_code": 1,
        "attributes.agentic.compaction.trigger": "token_limit",
        "attributes.agentic.compaction.tokens.before": 50000,
        "attributes.agentic.compaction.tokens.after": 20000,
        "attributes.agentic.compaction.tokens.saved": 30000,
    }


def _subagent_span(span_id="sub-1", parent="chat-1") -> dict:
    """Subagent span — has agentic.subagent.* or agentic.tool.type=subagent."""
    return {
        "span_id": span_id,
        "parent_span_id": parent,
        "trace_id": "t-1",
        "name": "subagent",
        "kind": "SPAN_KIND_INTERNAL",
        "start_time_unix_nano": "2026-07-23T14:21:41.000+08:00",
        "end_time_unix_nano": "2026-07-23T14:21:42.000+08:00",
        "status_code": 1,
        "attributes.gen_ai.span.kind": "TOOL",
        "attributes.gen_ai.operation.name": "execute_tool",
        "attributes.gen_ai.tool.call.id": "sub-call-1",
        "attributes.agentic.tool.type": "subagent",
        "attributes.agentic.subagent.id": "sub-1",
        "attributes.agentic.subagent.description": "research helper",
    }


# ── CHAT classification ──────────────────────────────────────────────


class TestChatClassification:
    def test_server_with_gen_ai_chat_kind_is_chat(self):
        assert _is_chat_span(_chat_span())

    def test_server_without_ai_kind_but_turn_name_is_chat(self):
        row = _chat_span()
        del row["attributes.gen_ai.span.kind"]
        assert _is_chat_span(row)

    def test_internal_llm_is_not_chat(self):
        assert not _is_chat_span(_llm_span())

    def test_internal_wrapper_is_not_chat(self):
        assert not _is_chat_span(_internal_non_tool())


# ── LLM classification ───────────────────────────────────────────────


class TestLlmClassification:
    def test_internal_with_gen_ai_llm_kind_is_llm(self):
        assert _is_llm_span(_llm_span())

    def test_server_chat_is_not_llm(self):
        assert not _is_llm_span(_chat_span())

    def test_internal_tool_is_not_llm(self):
        assert not _is_llm_span(_tool_span())

    def test_internal_wrapper_is_not_llm(self):
        assert not _is_llm_span(_internal_non_tool())


# ── TOOL classification — the critical fix ──────────────────────────


class TestToolClassification:
    """SPAN_KIND_INTERNAL alone is NEVER sufficient for TOOL."""

    def test_internal_with_gen_ai_tool_kind_is_tool(self):
        assert _is_tool_span(_tool_span())

    def test_internal_llm_is_not_tool(self):
        """LLM spans are INTERNAL but must NOT be classified as tools."""
        assert not _is_tool_span(_llm_span())

    def test_internal_wrapper_is_not_tool(self):
        """Control/wrapper spans are INTERNAL but must NOT be tools."""
        assert not _is_tool_span(_internal_non_tool())

    def test_internal_compaction_is_not_tool(self):
        """Compaction spans are INTERNAL but must NOT be tools."""
        assert not _is_tool_span(_compaction_span())

    def test_server_chat_is_not_tool(self):
        assert not _is_tool_span(_chat_span())

    def test_internal_with_call_id_but_no_ai_kind(self):
        """INTERNAL span with tool call ID but no gen_ai.span.kind=TOOL.

        This is a runtime fallback case — call_id provides cross-evidence.
        """
        row = _tool_span()
        del row["attributes.gen_ai.span.kind"]
        # Has call_id → cross-evidence → may be tool
        assert _is_tool_span(row)

    def test_agent_span_with_tool_fields_is_not_tool(self):
        row = _tool_span()
        row["attributes.gen_ai.span.kind"] = "AGENT"
        assert not _is_tool_span(row)

    def test_internal_with_only_name_prefix_is_insufficient(self):
        """name starts with 'tool ' but no gen_ai.span.kind or call_id.

        This MUST NOT be classified as a tool without cross-evidence.
        """
        row = _internal_non_tool(name="tool mystery")
        assert not _is_tool_span(row)


# ── ToolAction extraction ────────────────────────────────────────────


class TestToolActionExtraction:
    def test_real_tool_span_produces_action(self):
        action = _extract_tool_action(_tool_span(args={"path": "/a.py"}))
        assert action is not None
        assert action.call_id == "call-1"
        assert action.identity_source == "tool_call"

    def test_llm_span_does_not_produce_action(self):
        assert _extract_tool_action(_llm_span()) is None

    def test_wrapper_span_does_not_produce_action(self):
        assert _extract_tool_action(_internal_non_tool()) is None

    def test_compaction_span_does_not_produce_action(self):
        assert _extract_tool_action(_compaction_span()) is None

    def test_tool_error_status(self):
        row = _tool_span(status_code=2)
        action = _extract_tool_action(row)
        assert action is not None
        assert action.status == "error"

    def test_tool_string_status_code_is_normalized(self):
        action = _extract_tool_action(_tool_span(status_code="2"))
        assert action is not None
        assert action.status == "error"


# ── Full projection: INTERNAL wrapper must not pollute ───────────────


class TestProjectionNoPollution:
    def test_llm_step_keeps_standard_message_io(self):
        row = _llm_span()
        row["attributes.gen_ai.input.messages"] = json.dumps(
            [{"role": "user", "content": "inspect the failing test"}]
        )
        row["attributes.gen_ai.output.messages"] = json.dumps(
            [{"role": "assistant", "content": "I found the cause"}]
        )

        projection = project_coding_raw([row], "t-1")[0]
        step = projection.steps[0]

        assert step.input == row["attributes.gen_ai.input.messages"]
        assert step.output == row["attributes.gen_ai.output.messages"]

    def test_steps_preserve_observed_timing_model_and_usage(self):
        projection = project_coding_raw([_chat_span(), _llm_span(), _tool_span()], "t-1")[0]
        llm_step = next(step for step in projection.steps if step.type == "LLM")
        tool_step = next(step for step in projection.steps if step.type == "TOOL")

        assert llm_step.latency_ms == 2_000
        assert llm_step.model == "gpt-5.6-sol"
        assert llm_step.usage == {"input": 100.0, "output": 50.0, "total": 150.0}
        assert tool_step.latency_ms == 1_000
        assert tool_step.model is None
        assert tool_step.usage is None

    def test_llm_spans_not_counted_as_tool_actions(self):
        """A trace with CHAT + LLM only → 0 tool actions."""
        rows = [_chat_span(), _llm_span()]
        projections = project_coding_raw(rows, "t-1")
        assert len(projections) == 1
        assert len(projections[0].tool_actions) == 0

    def test_wrapper_spans_not_counted_as_tool_actions(self):
        """A trace with CHAT + wrapper INTERNAL → 0 tool actions."""
        rows = [_chat_span(), _internal_non_tool()]
        projections = project_coding_raw(rows, "t-1")
        assert len(projections) == 1
        assert len(projections[0].tool_actions) == 0

    def test_real_tools_still_extracted(self):
        """A trace with CHAT + LLM + TOOL → 1 tool action."""
        rows = [
            _chat_span(),
            _llm_span(),
            _tool_span(args={"path": "/a.py"}),
        ]
        projections = project_coding_raw(rows, "t-1")
        assert len(projections) == 1
        assert len(projections[0].tool_actions) == 1
        assert projections[0].tool_actions[0].name == "Edit"

    def test_compaction_not_counted_as_tool_action(self):
        """Compaction span must not produce a ToolAction."""
        rows = [_chat_span(), _compaction_span()]
        projections = project_coding_raw(rows, "t-1")
        assert len(projections[0].tool_actions) == 0

    def test_failed_tool_only_marks_that_tool(self):
        """A failed TOOL span does not make other tools fail."""
        rows = [
            _chat_span(),
            _tool_span(span_id="tool-ok", call_id="ok-1", args={"path": "/a.py"}),
            _tool_span(
                span_id="tool-fail",
                call_id="fail-1",
                name="tool Bash",
                args={"command": "bad"},
                status_code=2,
            ),
        ]
        projections = project_coding_raw(rows, "t-1")
        actions = projections[0].tool_actions
        assert len(actions) == 2
        ok = [a for a in actions if a.call_id == "ok-1"][0]
        fail = [a for a in actions if a.call_id == "fail-1"][0]
        assert ok.status == "success"
        assert fail.status == "error"

    def test_missing_arguments_does_not_produce_fake_file_mutation(self):
        """A TOOL span without path in arguments → no file mutation."""
        rows = [
            _chat_span(),
            _tool_span(args={"command": "ls"}),  # no 'path' key
        ]
        projections = project_coding_raw(rows, "t-1")
        assert len(projections[0].tool_actions) == 1
        assert len(projections[0].file_mutations) == 0


# ── Trace-per-turn contract ──────────────────────────────────────────


class TestSourceProjection:
    def test_multiple_chat_spans_are_not_projected(self):
        rows = [
            _chat_span(span_id="chat-1", parent=None, query="q1", output="a1"),
            _tool_span(span_id="tool-1", parent="chat-1", call_id="call-1"),
            _chat_span(span_id="chat-2", parent=None, query="q2", output="a2"),
            _tool_span(span_id="tool-2", parent="chat-2", call_id="call-2"),
        ]
        projections = project_coding_raw(rows, "t-1")

        assert projections == []

    def test_non_codefuse_single_chat_one_projection(self):
        rows = [_chat_span(), _llm_span(), _tool_span()]
        projections = project_coding_raw(rows, "t-1")
        assert len(projections) == 1


class TestTraceIdentity:
    def test_multi_chat_is_rejected_by_trace_per_turn_contract(self):
        rows = [
            _chat_span(span_id="chat-1", parent=None, query="q1", output="a1"),
            _chat_span(span_id="chat-2", parent=None, query="q2", output="a2"),
        ]
        projections = project_coding_raw(rows, "trace-1")
        assert projections == []

    def test_single_chat_no_split(self):
        """Single CHAT → one projection, regardless of runtime."""
        rows = [_chat_span(), _llm_span(), _tool_span()]
        projections = project_coding_raw(rows, "trace-1")
        assert len(projections) == 1


# ── Enhanced ToolAction fields ────────────────────────────────────────


class TestEnhancedToolActionFields:
    def test_raw_name_preserved(self):
        row = _tool_span(name="tool Edit", call_id="c1")
        action = _extract_tool_action(row)
        assert action is not None
        assert action.raw_name == "tool Edit"
        assert action.name == "Edit"

    def test_tool_type_extracted(self):
        row = _tool_span()
        row["attributes.agentic.tool.type"] = "mcp"
        action = _extract_tool_action(row)
        assert action is not None
        assert action.tool_type == "mcp"

    def test_operation_name_extracted(self):
        row = _tool_span()
        action = _extract_tool_action(row)
        assert action is not None
        assert action.operation_name == "execute_tool"

    def test_error_type_extracted(self):
        row = _tool_span(status_code=2)
        row["attributes.error.type"] = "timeout"
        action = _extract_tool_action(row)
        assert action is not None
        assert action.status == "error"
        assert action.error_type == "timeout"

    def test_duration_ms_calculated(self):
        row = _tool_span()
        action = _extract_tool_action(row)
        assert action is not None
        assert action.duration_ms is not None
        assert action.duration_ms > 0

    def test_truncation_flags_extracted(self):
        row = _tool_span()
        row["attributes.agentic.tool.input.truncated"] = True
        row["attributes.agentic.tool.output.truncated"] = False
        action = _extract_tool_action(row)
        assert action is not None
        assert action.input_truncated is True
        assert action.output_truncated is False

    def test_missing_fields_are_none(self):
        """Fields absent from source are None, not fabricated."""
        row = _tool_span()
        # Remove optional attributes
        for k in list(row.keys()):
            if "agentic" in k or "error" in k:
                del row[k]
        action = _extract_tool_action(row)
        assert action is not None
        assert action.raw_name is None or action.raw_name == "tool Edit"
        assert action.tool_type is None
        assert action.error_type is None
        assert action.input_truncated is None
        assert action.output_truncated is None


# ── Canonical ToolAction source ───────────────────────────────────────


class TestCanonicalToolActionSource:
    """GT rules must use get_tool_actions, which reads projection
    tool_actions when available, not extract_tool_actions from steps."""

    def test_get_tool_actions_uses_projection_when_available(self):
        from agentlens.service.datasource.projection.models import TraceOutcome
        from agentlens.service.datasource.projection.projectors.coding.models import (
            CodingProjection,
        )
        from agentlens.service.datasource.projection.projectors.coding.models import (
            ToolAction as ProjToolAction,
        )

        # Build a CodingProjection with explicit tool_actions
        proj_actions = [
            ProjToolAction(
                action_id="call-1",
                source_span_id="s1",
                call_id="call-1",
                name="edit",
                identity_source="tool_call",
                arguments={"path": "/a.py", "old_string": "x", "new_string": "y"},
                result=None,
                status="success",
            ),
        ]
        proj = CodingProjection(
            timestamp=None,
            user_query="q",
            agent_output="a",
            outcome=TraceOutcome(
                has_error=False, result_state="SUCCESS", total_latency_ms=100, interrupted=False
            ),
            labels={},
            steps=[],  # empty steps — must NOT be the source
            tool_actions=proj_actions,
        )

        result = get_tool_actions(proj)
        assert len(result) == 1
        assert result[0].tool_name == "edit"
        assert "edit" in result[0].operations
        assert result[0].paths == ("/a.py",)

    def test_get_tool_actions_falls_back_to_steps_for_non_coding(self):
        from agentlens.service.datasource.projection.models import (
            ExecutionStep,
            Projection,
            TraceOutcome,
        )

        # Build a plain Projection (no tool_actions attribute)
        proj = Projection(
            timestamp=None,
            user_query="q",
            agent_output="a",
            outcome=TraceOutcome(
                has_error=False, result_state="SUCCESS", total_latency_ms=100, interrupted=False
            ),
            labels={},
            steps=[
                ExecutionStep(
                    ref="s1",
                    name="tool Edit",
                    type="TOOL",
                    status="OK",
                    input={"path": "/b.py", "old_string": "x", "new_string": "y"},
                    output=None,
                ),
            ],
        )

        result = get_tool_actions(proj)
        assert len(result) == 1
        assert result[0].tool_name == "tool Edit"


# ── GT rule regression: confirmed ToolAction only ─────────────────────


class TestGtRulesConsumeConfirmedActions:
    """GT coding rules must only fire on confirmed ToolAction, not
    INTERNAL wrapper spans or LLM spans."""

    def _coding_proj(self, tool_actions=None, steps=None):
        from agentlens.service.datasource.projection.models import TraceOutcome
        from agentlens.service.datasource.projection.projectors.coding.models import (
            CodingProjection,
        )

        return CodingProjection(
            timestamp=None,
            user_query="q",
            agent_output="a",
            outcome=TraceOutcome(
                has_error=False,
                result_state="SUCCESS",
                total_latency_ms=100,
                interrupted=False,
            ),
            labels={},
            steps=steps or [],
            tool_actions=tool_actions or [],
        )

    def _gt_action(self, name="edit", args=None, status="success"):
        from agentlens.service.datasource.projection.projectors.coding.models import ToolAction

        return ToolAction(
            action_id="call-1",
            source_span_id="s1",
            call_id="call-1",
            name=name,
            identity_source="tool_call",
            arguments=args or {},
            result=None,
            status=status,
        )

    def test_no_tool_actions_exempt_not_pass(self):
        """Trace with 0 tool actions → exempt, not false pass."""
        proj = self._coding_proj(tool_actions=[])
        actions = get_tool_actions(proj)
        assert len(actions) == 0

    def test_edit_with_path_produces_file_mutation(self):
        """Real edit with path → file mutation in projection."""
        proj = self._coding_proj(
            tool_actions=[
                self._gt_action(
                    name="edit",
                    args={
                        "path": "/a.py",
                        "old_string": "x",
                        "new_string": "y",
                    },
                ),
            ]
        )
        assert len(proj.tool_actions) == 1
        assert proj.tool_actions[0].name == "edit"

    def test_failed_tool_only_marks_that_tool(self):
        """A failed tool doesn't make other tools fail."""
        proj = self._coding_proj(
            tool_actions=[
                self._gt_action(name="edit", args={"path": "/a.py"}, status="success"),
                self._gt_action(name="bash", args={"command": "bad"}, status="error"),
            ]
        )
        actions = proj.tool_actions
        assert actions[0].status == "success"
        assert actions[1].status == "error"

    def test_missing_arguments_no_fake_mutation(self):
        """Tool without path in arguments → no file mutation."""
        from agentlens.service.datasource.projection.projectors.coding.adapter import (
            _derive_file_mutation,
        )

        action = self._gt_action(name="bash", args={"command": "ls"})
        mut = _derive_file_mutation(action)
        assert mut is None

    def test_pytest_identified_as_verification(self):
        """pytest command → verification action."""
        from agentlens.service.datasource.projection.projectors.coding.adapter import (
            _derive_verification,
        )

        action = self._gt_action(name="bash", args={"command": "pytest"})
        ver = _derive_verification(action)
        assert ver is not None

    def test_internal_wrapper_not_in_tool_actions(self):
        """LLM/wrapper spans produce no tool actions in projection."""
        import json

        from agentlens.service.datasource.projection.projectors.coding.adapter import (
            project_coding_raw,
        )

        rows = [
            {
                "span_id": "chat-1",
                "parent_span_id": "",
                "trace_id": "t-1",
                "name": "Turn 1",
                "kind": "SPAN_KIND_SERVER",
                "start_time_unix_nano": "2026-07-23T14:21:35+08:00",
                "end_time_unix_nano": "2026-07-23T14:22:35+08:00",
                "status_code": 1,
                "attributes.gen_ai.span.kind": "CHAT",
                "attributes.gen_ai.input.messages": json.dumps(
                    [{"role": "user", "content": "hello"}]
                ),
                "attributes.gen_ai.output.messages": json.dumps(
                    [{"role": "assistant", "content": "hi"}]
                ),
            },
            {
                "span_id": "wrapper-1",
                "parent_span_id": "chat-1",
                "trace_id": "t-1",
                "name": "wrapper",
                "kind": "SPAN_KIND_INTERNAL",
                "start_time_unix_nano": "2026-07-23T14:21:36+08:00",
                "end_time_unix_nano": "2026-07-23T14:21:37+08:00",
                "status_code": 1,
            },
        ]
        projections = project_coding_raw(rows, "t-1")
        assert len(projections) == 1
        assert len(projections[0].tool_actions) == 0
        assert len(projections[0].file_mutations) == 0
        assert len(projections[0].verification_actions) == 0


# ── GT rule end-to-end with CodingProjection ────────────────────


class TestGtRulesEndToEnd:
    """Verify GT rule functions work correctly when fed
    CodingProjection with confirmed tool_actions."""

    def _proj(self, tool_actions=None):
        from agentlens.service.datasource.projection.models import TraceOutcome
        from agentlens.service.datasource.projection.projectors.coding.models import (
            CodingProjection,
        )

        return CodingProjection(
            timestamp=None,
            user_query="q",
            agent_output="a",
            outcome=TraceOutcome(
                has_error=False,
                result_state="SUCCESS",
                total_latency_ms=100,
                interrupted=False,
            ),
            labels={},
            steps=[],
            tool_actions=tool_actions or [],
        )

    def _action(self, name, args=None, status="success", result=None, source_span_id=None):
        from agentlens.service.datasource.projection.projectors.coding.models import ToolAction

        return ToolAction(
            action_id=f"call-{name}",
            source_span_id=source_span_id or f"s-{name}",
            call_id=f"call-{name}",
            name=name,
            identity_source="tool_call",
            arguments=args or {},
            result=result,
            status=status,
        )

    def test_tool_success_rate_with_confirmed_actions(self):
        proj = self._proj(
            [
                self._action("edit", {"path": "/a.py"}, status="success"),
                self._action("bash", {"command": "pytest"}, status="error"),
            ]
        )
        result = _measure_tool_success_rate(proj, {})
        assert result.verdict is Verdict.BAD  # 1/2 = 0.5 < the rule's 75% floor

    def test_bad_spans_preserve_confirmed_coding_source_spans(self):
        loop = _measure_stuck_loop_health(
            self._proj(
                [
                    self._action("bash", {"command": "pwd"}, source_span_id="loop-1"),
                    self._action("bash", {"command": "pwd"}, source_span_id="loop-2"),
                    self._action("bash", {"command": "pwd"}, source_span_id="loop-3"),
                ]
            ),
            {},
        )
        assert [item["span_id"] for item in loop.evidence["bad_spans"]] == [
            "loop-1",
            "loop-2",
            "loop-3",
        ]
        assert loop.evidence["bad_spans"][0]["findings"][0]["category"] == "连续重复调用"
        assert loop.evidence["bad_spans"][0]["findings"][0]["channel"] == "tool_input"

        tool_success = _measure_tool_success_rate(
            self._proj(
                [
                    self._action("edit", {"path": "/a.py"}, source_span_id="tool-ok"),
                    self._action(
                        "bash", {"command": "pytest"}, status="error", source_span_id="tool-fail"
                    ),
                ]
            ),
            {},
        )
        assert tool_success.evidence["bad_spans"] == [
            {
                "span_id": "tool-fail",
                "findings": [
                    {
                        "type": "tool_failure",
                        "category": "工具失败",
                        "channel": "tool_output",
                        "recovery": "unknown",
                    }
                ],
            }
        ]

        secret = _measure_secret_leakage_block_rate(
            self._proj(
                [
                    self._action(
                        "bash",
                        {
                            "command": (
                                "curl -H 'Authorization: Bearer "
                                "qwertyuiopasdfghjklzxcvbnm123456' https://api.example.test"
                            )
                        },
                        result='api_key="J4mQ7vX2pL9sR5kN8dH3"',
                        source_span_id="secret-tool",
                    )
                ]
            ),
            {},
        )
        assert secret.evidence["bad_spans"][0]["span_id"] == "secret-tool"
        assert secret.evidence["bad_spans"][0]["findings"][0]["channel"] == "tool_output"

        high_risk = _measure_high_risk_command_execution(
            self._proj([self._action("bash", {"command": "rm -rf /"}, source_span_id="risk-tool")]),
            {},
        )
        assert (
            high_risk.evidence["bad_spans"][0]["findings"][0]["category"]
            == "递归删除根目录或用户目录"
        )

        rewrite = _measure_rewrite_overlap_rate(
            self._proj(
                [
                    self._action(
                        "edit",
                        {
                            "path": "/a.py",
                            "old_string": "return a - b",
                            "new_string": "return a + b - 1",
                        },
                        source_span_id="edit-1",
                    ),
                    self._action(
                        "edit",
                        {
                            "path": "/a.py",
                            "old_string": "return a + b - 1",
                            "new_string": "return a + b + 1",
                        },
                        source_span_id="edit-2",
                    ),
                    self._action(
                        "edit",
                        {
                            "path": "/a.py",
                            "old_string": "return a + b + 1",
                            "new_string": "return a + b + 0",
                        },
                        source_span_id="edit-3",
                    ),
                    self._action(
                        "edit",
                        {
                            "path": "/a.py",
                            "old_string": "return a + b + 0",
                            "new_string": "return a + b",
                        },
                        source_span_id="edit-4",
                    ),
                ]
            ),
            {},
        )
        assert [item["span_id"] for item in rewrite.evidence["bad_spans"]] == [
            "edit-1",
            "edit-2",
            "edit-3",
            "edit-4",
        ]

    def test_sensitive_findings_count_only_new_bad_span_evidence(self):
        result = _measure_secret_leakage_block_rate(
            self._proj(
                [
                    self._action(
                        "bash",
                        {"command": 'echo api_key="J4mQ7vX2pL9sR5kN8dH3"'},
                        result='api_key="J4mQ7vX2pL9sR5kN8dH3"',
                        source_span_id="secret-tool",
                    ),
                    self._action(
                        "bash",
                        {"command": 'echo api_key="J4mQ7vX2pL9sR5kN8dH3"'},
                        result='api_key="J4mQ7vX2pL9sR5kN8dH3"',
                        source_span_id="secret-tool",
                    ),
                ]
            ),
            {},
        )

        assert result.verdict is Verdict.BAD
        assert len(result.evidence["bad_spans"][0]["findings"]) == 1

    def test_tool_success_rate_ignores_compaction_tool(self):
        result = _measure_tool_success_rate(
            self._proj(
                [
                    self._action("compaction", status="error", source_span_id="compact-fail"),
                    self._action("bash", {"command": "pwd"}, source_span_id="tool-ok"),
                ]
            ),
            {},
        )

        assert result.verdict is Verdict.GOOD
        assert result.evidence["counts"] == {"success": 1, "failure": 0, "unknown": 0}
        assert not result.evidence["findings"]

    def test_tool_success_rate_compaction_only_is_exempt(self):
        with pytest.raises(ApplicabilitySkipped) as raised:
            _measure_tool_success_rate(
                self._proj([self._action("tool compaction", status="error")]),
                {},
            )

        assert raised.value.code == "not_applicable"

    def test_tool_success_rate_no_actions_exempt(self):
        proj = self._proj([])
        spec = {}
        with pytest.raises(ApplicabilitySkipped) as raised:
            _measure_tool_success_rate(proj, spec)

        assert raised.value.code == "not_applicable"

    def test_test_execution_rate_with_edit_and_test(self):
        proj = self._proj(
            [
                self._action("edit", {"path": "/a.py", "old_string": "x", "new_string": "y"}),
                self._action("bash", {"command": "pytest -v"}),
            ]
        )
        spec = {}
        result = _measure_test_execution_rate(proj, spec)
        assert result.verdict is Verdict.GOOD
        assert not result.evidence.get("bad_spans")

    def test_test_execution_rate_edit_no_test_fails(self):
        proj = self._proj(
            [
                self._action(
                    "edit",
                    {"path": "/a.py", "old_string": "before", "new_string": "after"},
                    source_span_id="unverified-edit",
                ),
            ]
        )
        spec = {}
        result = _measure_test_execution_rate(proj, spec)
        assert result.verdict is Verdict.BAD
        assert result.evidence["bad_spans"] == [
            {
                "span_id": "unverified-edit",
                "findings": [
                    {
                        "type": "missing_verification",
                        "category": "修改后缺少验证",
                        "channel": "tool_input",
                    }
                ],
            }
        ]

    def test_test_execution_rate_missing_source_span_does_not_fabricate_evidence(self):
        action = replace(
            self._action(
                "edit",
                {"path": "/a.py", "old_string": "before", "new_string": "after"},
            ),
            source_span_id="",
        )

        result = _measure_test_execution_rate(self._proj([action]), {})

        assert result.verdict is Verdict.BAD
        assert not result.evidence.get("bad_spans")

        assert result.evidence["findings"][0]["location_status"] == "unavailable"

    def test_test_execution_rate_no_changes_exempt(self):
        proj = self._proj(
            [
                self._action("bash", {"command": "ls"}),
            ]
        )
        spec = {}
        with pytest.raises(ApplicabilitySkipped) as raised:
            _measure_test_execution_rate(proj, spec)

        assert raised.value.code == "not_applicable"

    def test_test_execution_rate_file_only_change_requires_verification(self):
        proj = self._proj([self._action("edit", {"path": "/a.py"})])

        result = _measure_test_execution_rate(proj, {})
        assert result.verdict is Verdict.BAD
        assert result.evidence["verification"]["result"] == "missing"

    def test_rewrite_overlap_no_comparable_edit_exempt(self):
        proj = self._proj(
            [
                self._action("bash", {"command": "ls"}),
            ]
        )
        spec = {}
        with pytest.raises(ApplicabilitySkipped) as raised:
            _measure_rewrite_overlap_rate(proj, spec)

        assert raised.value.code == "not_applicable"

    def test_test_execution_rate_requires_verification_after_change(self):
        proj = self._proj(
            [
                self._action("bash", {"command": "pytest -q"}, source_span_id="verification"),
                self._action(
                    "edit",
                    {"path": "/a.py", "old_string": "before", "new_string": "after"},
                    source_span_id="last-change",
                ),
            ]
        )
        result = _measure_test_execution_rate(proj, {})

        assert result.verdict is Verdict.BAD
        assert result.evidence["bad_spans"][0]["span_id"] == "last-change"

    def test_rewrite_overlap_missing_edit_evidence_is_exempt(self):
        proj = self._proj(
            [
                self._action("bash", {"command": "git apply changes.patch"}),
            ]
        )
        with pytest.raises(ApplicabilitySkipped) as raised:
            _measure_rewrite_overlap_rate(proj, {})

        assert raised.value.code == "missing_required_evidence"


def test_coding_projection_derives_shell_patch_and_verification_facts():
    rows = [
        _chat_span(),
        _tool_span(
            span_id="patch-1",
            name="tool Bash",
            args={
                "command": (
                    "apply_patch <<'PATCH'\n*** Update File: src/app.py\n*** End Patch\nPATCH"
                ),
            },
        ),
        _tool_span(
            span_id="check-1",
            name="tool Bash",
            args={"command": "ruff check src/app.py"},
            result={"exitCode": 0},
        ),
    ]

    projection = project_coding_raw(rows, "t-1")[0]

    mutations = [
        (fact.path, fact.operation, fact.evidence_level) for fact in projection.file_mutations
    ]
    assert mutations == [("src/app.py", "patch", "file_only")]
    assert [(fact.command, fact.result) for fact in projection.verification_actions] == [
        ("ruff check src/app.py", "passed"),
    ]


def test_coding_projection_does_not_promote_non_user_or_non_assistant_messages():
    row = _chat_span()
    row["attributes.gen_ai.input.messages"] = json.dumps(
        [
            {"role": "system", "content": "context"},
        ]
    )
    row["attributes.gen_ai.output.messages"] = json.dumps(
        [
            {"role": "tool", "content": "tool result"},
        ]
    )

    projection = project_coding_raw([row], "t-1")[0]

    assert projection.user_query == ""
    assert projection.agent_output == ""
