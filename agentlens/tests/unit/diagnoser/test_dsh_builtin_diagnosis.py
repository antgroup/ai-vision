"""DSH's existing OTel shapes through projection and the six shared rules.

Tool names/attributes follow observ-dsh core.ts and engine.test.mjs.
All commands and credentials here are synthetic text, never executed.
"""

import pytest

from agentlens.domain.enums import Verdict
from agentlens.service.datasource.projection.registry import builtin_scenarios_for
from agentlens.service.datasource.projection.service import project_trace
from agentlens.service.datasource.trace import Trace
from tests.unit.diagnoser.test_builtin_rule_executor import (
    HighRiskCommandExecutor,
    RewriteOverlapExecutor,
    SensitiveLeakageExecutor,
    StuckLoopExecutor,
    ToolSuccessExecutor,
    TotalLatencyExecutor,
)
from tests.unit.diagnoser.test_coding_otel_contract import _chat_span, _tool_span

EXECUTORS = [
    ToolSuccessExecutor,
    TotalLatencyExecutor,
    StuckLoopExecutor,
    HighRiskCommandExecutor,
    SensitiveLeakageExecutor,
    RewriteOverlapExecutor,
]


def dsh_trace(bad_rule=None, *, child=False):
    root = _chat_span(query="Update the application", output="Updated successfully")
    root["attributes.agentic.runtime.name"] = "dsh"
    if bad_rule is TotalLatencyExecutor:
        root["end_time_unix_nano"] = "2026-07-23T15:22:35.976+08:00"
    spans = [root]
    if child:
        spans.append(
            {
                "trace_id": "t-1",
                "span_id": "child-agent",
                "parent_span_id": "chat-1",
                "name": "agent helper",
                "attributes.gen_ai.span.kind": "AGENT",
                "attributes.agentic.subagent.id": "child-session",
                "status_code": 1,
            }
        )
    parent = "child-agent" if child else "chat-1"
    for index in range(3 if bad_rule is StuckLoopExecutor else 1):
        row = _tool_span(
            span_id=f"bash-{index}",
            parent=parent,
            name="builtin bash",
            call_id=f"bash-call-{index}",
            args={"command": "rm -rf /" if bad_rule is HighRiskCommandExecutor else "pwd"},
            status_code=2 if bad_rule is ToolSuccessExecutor else 1,
        )
        row["attributes.gen_ai.tool.name"] = "bash"
        row["attributes.agentic.tool.type"] = "builtin"
        row["attributes.gen_ai.tool.call.result"] = (
            "permission denied" if bad_rule is ToolSuccessExecutor else "/workspace"
        )
        if bad_rule is SensitiveLeakageExecutor:
            row["attributes.gen_ai.tool.call.result"] = (
                "Authorization: Bearer qwertyuiopasdfghjklzxcvbnm123456"
            )
        spans.append(row)
    for index in range(4 if bad_rule is RewriteOverlapExecutor else 1):
        row = _tool_span(
            span_id=f"edit-{index}",
            parent=parent,
            name="builtin edit",
            call_id=f"edit-call-{index}",
            args={
                "path": "app.py",
                "old_string": f"value = {index}",
                "new_string": f"value = {index + 1}",
            },
        )
        row["attributes.gen_ai.tool.name"] = "edit"
        row["attributes.gen_ai.tool.call.result"] = "File updated"
        spans.append(row)
    return project_trace(Trace(trace_id="t-1", runtime_type="dsh", spans=spans))


@pytest.mark.parametrize("executor_type", EXECUTORS)
@pytest.mark.parametrize("bad", [False, True])
def test_dsh_rules_make_real_decisions(executor_type, bad):
    trace = dsh_trace(executor_type if bad else None)
    assert trace.projection is not None
    spec = {"metrics": list(executor_type.definition.metrics)}
    if executor_type is TotalLatencyExecutor:
        spec["latency_budget_ms"] = 1_500_000
    executor = executor_type(spec)
    result = executor.evaluate(trace)
    metric = result.metrics[0]
    assert metric.verdict == (Verdict.BAD if bad else Verdict.GOOD)
    assert not metric.evidence.get("exempted")
    if bad:
        assert metric.reason


def test_dsh_child_tools_are_available_to_diagnosis():
    trace = dsh_trace(ToolSuccessExecutor, child=True)
    assert trace.projection is not None
    assert len(trace.projection.subagents) == 1
    assert builtin_scenarios_for("dsh") == frozenset({"common", "coding"})
    executor = ToolSuccessExecutor({"metrics": list(ToolSuccessExecutor.definition.metrics)})
    assert executor.evaluate(trace).metrics[0].verdict == Verdict.BAD


@pytest.mark.parametrize(
    ("runtime_marker", "error_type", "on_tool", "interrupted"),
    [
        ("dsh", "aborted", False, True),
        ("dsh", "timeout", False, False),
        ("dsh", "aborted", True, False),
        ("codex", "aborted", False, False),
        ("dsh", None, False, False),
    ],
)
def test_dsh_user_stop_requires_confirmed_chat_abort(
    runtime_marker, error_type, on_tool, interrupted
):
    from agentlens.diagnoser.executors.rule.builtin.common.user_interruption import (
        UserInterruptionExecutor,
    )

    trace = dsh_trace()
    root, tool = trace.spans[:2]
    root["attributes.agentic.runtime.name"] = runtime_marker
    target = tool if on_tool else root
    target["attributes.agentic.runtime.name"] = runtime_marker
    if error_type:
        target["attributes.error.type"] = error_type
        target["status_code"] = 2
    trace = project_trace(Trace(trace_id=trace.trace_id, runtime_type="dsh", spans=trace.spans))
    assert trace.projection.outcome.interrupted is interrupted
    executor = UserInterruptionExecutor(
        {"metrics": list(UserInterruptionExecutor.definition.metrics)}
    )
    assert trace.projection.observed_user_interruption is (True if interrupted else None)
    if not interrupted:
        assert executor.applicability_skip_reason(trace).startswith("missing_required_evidence:")
        return
    metric = executor.evaluate(trace).metrics[0]
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["interrupted"] is True


@pytest.mark.parametrize(
    ("arguments", "expected_bad"),
    [
        (["[REDACTED]"] * 3, False),
        ([{"command": "pwd"}, "[REDACTED]", {"command": "pwd"}, {"command": "pwd"}], False),
        (["[REDACTED]"] + [{"command": "pwd"}] * 3, True),
    ],
)
def test_dsh_hidden_arguments_do_not_prove_repeated_calls(arguments, expected_bad):
    root = _chat_span(query="Privacy probe", output="Done")
    root["attributes.agentic.runtime.name"] = "dsh"
    spans = [root]
    for index, args in enumerate(arguments):
        row = _tool_span(
            span_id=f"private-{index}",
            parent="chat-1",
            name="builtin bash",
            call_id=f"private-call-{index}",
            args={},
        )
        row["attributes.gen_ai.tool.name"] = "bash"
        row["attributes.gen_ai.tool.call.arguments"] = args
        spans.append(row)
    trace = project_trace(Trace(trace_id="t-1", runtime_type="dsh", spans=spans))
    executor = StuckLoopExecutor({"metrics": list(StuckLoopExecutor.definition.metrics)})
    if not expected_bad:
        assert executor.applicability_skip_reason(trace).startswith("privacy_redacted:")
        return
    metric = executor.evaluate(trace).metrics[0]
    assert metric.verdict == (Verdict.BAD if expected_bad else Verdict.GOOD)
    assert not metric.evidence.get("exempted")


@pytest.mark.parametrize("executor_type", [HighRiskCommandExecutor, SensitiveLeakageExecutor])
@pytest.mark.parametrize("visible_bad", [False, True])
def test_dsh_privacy_safety_rules_require_visible_evidence(executor_type, visible_bad):
    trace = dsh_trace(executor_type if visible_bad else None)
    trace.spans[0]["attributes.gen_ai.input.messages"] = "[REDACTED]"
    hidden = _tool_span(
        span_id="private-shell",
        parent="chat-1",
        name="builtin bash",
        call_id="private-shell-call",
        args={},
    )
    hidden["attributes.gen_ai.tool.name"] = "bash"
    hidden["attributes.gen_ai.tool.call.arguments"] = "[REDACTED]"
    hidden["attributes.gen_ai.tool.call.result"] = "[REDACTED]"
    spans = [*trace.spans, hidden] if visible_bad else [trace.spans[0], hidden]
    trace = project_trace(Trace(trace_id="t-1", runtime_type="dsh", spans=spans))
    spec = {"metrics": list(executor_type.definition.metrics)}
    if executor_type is TotalLatencyExecutor:
        spec["latency_budget_ms"] = 1_500_000
    executor = executor_type(spec)
    if not visible_bad:
        assert executor.applicability_skip_reason(trace).startswith("privacy_redacted:")
        return
    metric = (
        executor_type({"metrics": list(executor_type.definition.metrics)})
        .evaluate(trace)
        .metrics[0]
    )
    assert metric.verdict == (Verdict.BAD if visible_bad else Verdict.GOOD)
    assert not metric.evidence.get("exempted")
