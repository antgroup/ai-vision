import pytest

from agentlens.service.datasource.projection.registry import get_dedicated_projector
from agentlens.service.datasource.projection.service import project_trace
from agentlens.service.datasource.trace import Trace


@pytest.mark.parametrize("runtime_type", ["codex", "claudecode", "codefuse", "dsh"])
def test_supported_coding_runtime_routes_to_coding_projector(runtime_type):
    assert get_dedicated_projector(runtime_type).runtime_type == "cc"


@pytest.mark.parametrize(
    "runtime_type",
    [
        "opencode",
        "openclaw",
        "kimicli",
        "pi",
        "eddy",
        "antigravity",
        "lingguangagent",
    ],
)
def test_unverified_runtime_has_no_dedicated_projector(runtime_type):
    assert get_dedicated_projector(runtime_type) is None


def test_unverified_runtime_keeps_source_spans_for_custom_input():
    rows = [{"trace_id": "t-1", "span_id": "s-1", "name": "raw"}]
    trace = project_trace(Trace(trace_id="t-1", runtime_type="antigravity", spans=rows))

    assert trace.projection is None
    assert trace.spans is rows
    assert trace.to_python_value()["spans"] == rows


@pytest.mark.parametrize("runtime_type", ["antcodex", "antcc"])
def test_product_coding_alias_routes_without_becoming_a_projector_alias(runtime_type):
    assert get_dedicated_projector(runtime_type).runtime_type == "cc"


def test_multiple_chat_spans_are_not_projected_as_one_turn():
    rows = [
        {
            "trace_id": "t-1",
            "span_id": "chat-1",
            "parent_span_id": "",
            "name": "Turn 1",
            "kind": "SPAN_KIND_SERVER",
            "status_code": 1,
            "attributes.gen_ai.span.kind": "CHAT",
            "attributes.gen_ai.input.messages": '[{"role":"user","content":"first"}]',
        },
        {
            "trace_id": "t-1",
            "span_id": "chat-2",
            "parent_span_id": "",
            "name": "Turn 2",
            "kind": "SPAN_KIND_SERVER",
            "status_code": 1,
            "attributes.gen_ai.span.kind": "CHAT",
            "attributes.gen_ai.input.messages": '[{"role":"user","content":"second"}]',
        },
    ]
    projection = project_trace(
        Trace(trace_id="t-1", runtime_type="codefuse", spans=rows)
    ).projection

    assert projection is None
