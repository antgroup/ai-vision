"""Public Coding prompt variables are backed by confirmed projection facts."""

from __future__ import annotations

import json

from agentlens.service.datasource.projection.projectors.coding.adapter import project_coding_raw
from agentlens.service.datasource.projection.projectors.coding.projector import CodingProjector
from agentlens.service.datasource.trace import Trace
from agentlens.service.datasource.variables import available_vars_for, resolve_vars


def _chat() -> dict:
    return {
        "span_id": "chat-1",
        "parent_span_id": "",
        "trace_id": "trace-1",
        "name": "Turn 1",
        "kind": "SPAN_KIND_SERVER",
        "start_time_unix_nano": "2026-07-24T10:00:00+08:00",
        "end_time_unix_nano": "2026-07-24T10:00:10+08:00",
        "status_code": 1,
        "attributes.gen_ai.span.kind": "CHAT",
        "attributes.gen_ai.input.messages": json.dumps([{"role": "user", "content": "fix it"}]),
        "attributes.gen_ai.output.messages": json.dumps([{"role": "assistant", "content": "done"}]),
    }


def _tool(name: str, arguments: dict, *, tool_type: str | None = None) -> dict:
    row = {
        "span_id": f"tool-{name}",
        "parent_span_id": "chat-1",
        "trace_id": "trace-1",
        "name": f"tool {name}",
        "kind": "SPAN_KIND_INTERNAL",
        "start_time_unix_nano": "2026-07-24T10:00:01+08:00",
        "end_time_unix_nano": "2026-07-24T10:00:02+08:00",
        "status_code": 1,
        "attributes.gen_ai.span.kind": "TOOL",
        "attributes.gen_ai.operation.name": "execute_tool",
        "attributes.gen_ai.tool.call.id": f"call-{name}",
        "attributes.gen_ai.tool.call.arguments": json.dumps(arguments),
        "attributes.gen_ai.tool.call.result": json.dumps({"ok": True}),
    }
    if tool_type:
        row["attributes.agentic.tool.type"] = tool_type
    return row


def _trace(rows: list[dict]) -> Trace:
    projection = project_coding_raw(rows, "trace-1")[0]
    return Trace(
        trace_id="trace-1",
        runtime_type="codex",
        spans=rows,
        projection=projection,
    )


def test_coding_available_vars_are_stable_prompt_facts():
    assert set(CodingProjector.available_vars) == {
        "user_query",
        "agent_output",
        "outcome",
        "node_sequence",
        "steps",
        "tool_actions",
        "file_mutations",
        "verification_actions",
        "subagents",
        "current_request",
        "final_output",
        "history",
        "provided_context",
        "model_instructions",
    }
    available = available_vars_for("codex")
    assert set(CodingProjector.available_vars) <= set(available)
    assert {"trace_id", "runtime_type", "source_type", "source_meta", "spans"} <= set(available)


def test_coding_prompt_vars_are_populated_from_confirmed_facts():
    subagent = _tool("Task", {"description": "review"}, tool_type="subagent")
    subagent["attributes.agentic.subagent.id"] = "sub-1"
    rows = [
        _chat(),
        _tool("edit", {"path": "/repo/a.py", "old_string": "x", "new_string": "y"}),
        _tool("pytest", {"command": "pytest -q"}),
        subagent,
    ]
    trace = _trace(rows)

    result = resolve_vars(trace, set(CodingProjector.available_vars))

    assert set(result) == set(CodingProjector.available_vars)
    assert result["user_query"] == "fix it"
    assert result["agent_output"] == "done"
    assert result["node_sequence"] == "Turn 1 → tool Task → tool edit → tool pytest"
    assert result["tool_actions"][0]["name"] == "Task"
    assert result["file_mutations"][0]["path"] == "/repo/a.py"
    assert result["verification_actions"][0]["command"] == "pytest -q"
    assert result["subagents"][0]["subagent_id"] == "sub-1"


def test_coding_prompt_field_expression_reads_semantic_projection_only():
    trace = _trace([_chat(), _tool("edit", {"path": "/repo/a.py", "new_string": "y"})])

    result = resolve_vars(trace, {"tool_actions[0].arguments", "steps[0].node"})

    assert result["tool_actions[0].arguments"] == {
        "path": "/repo/a.py",
        "new_string": "y",
    }
    assert result["steps[0].node"] == "tool edit"


def test_non_coding_projection_does_not_receive_synthetic_coding_facts():
    from agentlens.service.datasource.projection.models import Projection, TraceOutcome

    projection = Projection(
        timestamp=None,
        user_query="q",
        agent_output="a",
        outcome=TraceOutcome(
            has_error=False,
            result_state="SUCCESS",
            total_latency_ms=1,
            interrupted=False,
        ),
        labels={},
    )

    assert CodingProjector().resolve_var(projection, "tool_actions") == []
