from agentlens.service.datasource.projection.models import (
    ContextEvidence,
    ExecutionStep,
    MessageEvidence,
    Projection,
    SourceReference,
    TraceOutcome,
)
from agentlens.service.datasource.projection.serialization import (
    serialize_projection,
    serialize_snapshot,
)


def _trace():
    return Projection(
        timestamp="2026",
        user_query="q",
        agent_output="a",
        outcome=TraceOutcome(False, "SUCCESS", 1, False),
        labels={"intent": "x", "nested": {"user_id": "secret", "ok": "value"}},
        node_seq=["A", "B"],
        steps=[
            ExecutionStep(
                name="tool",
                input={"session_uuid": "secret", "query": "q"},
                output={"context": {"health_records": ["secret"]}, "answer": "ok"},
            )
        ],
        conversation_history=[
            MessageEvidence(
                content="old question",
                role="user",
                sources=[SourceReference("t", "s", "input[0].content")],
            )
        ],
        provided_context=[
            ContextEvidence(
                content="doc", kind="tool", sources=[SourceReference("t", "s", "input[1].content")]
            )
        ],
    )


def test_projection_serializer_omits_top_level_identity_and_preserves_open_payloads():
    data = serialize_projection(_trace())
    assert "user_id" not in data and "session_id" not in data
    assert data["conversation_history"][0]["sources"][0]["field_path"] == "input[0].content"
    assert data["provided_context"][0]["content"] == "doc"
    assert data["steps"][0]["input"] == {"session_uuid": "secret", "query": "q"}
    assert data["steps"][0]["output"] == {
        "context": {"health_records": ["secret"]},
        "answer": "ok",
    }
    assert "model" not in data["steps"][0]


def test_coding_projection_serializer_preserves_execution_span_id():
    from agentlens.service.datasource.projection.projectors.coding.models import CodingProjection

    trace = CodingProjection(
        timestamp="2026",
        user_query="q",
        agent_output="a",
        outcome=TraceOutcome(False, "SUCCESS", 1, False),
        labels={},
        node_seq=["tool"],
        steps=[
            ExecutionStep(
                name="tool",
                type="TOOL",
                model="gpt-5.6-sol",
                ref="source-span-1",
            )
        ],
    )

    data = serialize_projection(trace)["steps"][0]
    assert data["span_id"] == "source-span-1"
    assert data["model"] == "gpt-5.6-sol"


def test_projection_serializer_bounds_nested_text_deterministically():
    trace = _trace()
    trace.labels["long"] = "x" * 30_000
    first = serialize_projection(trace)
    second = serialize_projection(trace)
    assert first == second
    assert first["labels"]["long"].endswith("…(truncated)")
    assert len(first["labels"]["long"]) < 30_000


def test_snapshot_uses_one_shared_shape_without_top_level_identity():
    snapshot = serialize_snapshot(_trace())
    assert snapshot == {
        "user_query": "q",
        "agent_output": "a",
        "outcome": {
            "has_error": False,
            "result_state": "SUCCESS",
            "total_latency_ms": 1,
            "interrupted": False,
        },
        "labels": {"intent": "x", "nested": {"user_id": "secret", "ok": "value"}},
        "node_sequence": ["A", "B"],
    }


def test_coding_projection_serializer_preserves_typed_fact_lists():
    from agentlens.service.datasource.projection.projectors.coding.models import (
        CodingProjection,
        SubagentExecution,
        ToolAction,
    )

    trace = CodingProjection(
        timestamp=None,
        user_query="q",
        agent_output="a",
        outcome=TraceOutcome(False, "SUCCESS", 1, False),
        labels={},
        tool_actions=[
            ToolAction(
                action_id="tool-1",
                source_span_id="span-write",
                name="edit",
                arguments={"path": "a.py", "old_string": "x", "new_string": "y"},
            ),
            ToolAction(
                action_id="tool-2",
                source_span_id="span-check",
                name="pytest",
                arguments={"command": "pytest -q"},
                status="success",
            ),
        ],
        subagents=[SubagentExecution(source_span_id="span-subagent")],
    )

    data = serialize_projection(trace)
    assert data["tool_actions"][0]["source_span_id"] == "span-write"
    assert data["file_mutations"][0]["source_span_id"] == "span-write"
    assert data["verification_actions"][0]["source_span_id"] == "span-check"
    assert data["subagents"][0]["source_span_id"] == "span-subagent"
