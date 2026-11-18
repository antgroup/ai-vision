"""评判输入解析器 —— resolve_vars 按变量引用自动取值 + build_snapshot 落盘。"""

from agentlens.service.datasource.projection.registry import get_dedicated_projector
from agentlens.service.datasource.variables import (
    build_snapshot,
    resolve_vars,
)
from tests.unit.diagnoser.projection_factory import trace_from_dict

AVAILABLE_VARS = set(get_dedicated_projector("codex").available_vars)


def _trace(**kw):
    d = {
        "trace_id": "t",
        "timestamp": None,
        "user_query": "",
        "agent_output": "",
        "outcome": {"has_error": False, "result_state": "SUCCESS"},
        "labels": {"intent": "x"},
        "runtime_type": "codex",
        "node_seq": [],
        "steps": [],
    }
    d.update(kw)
    return trace_from_dict(d)


def test_resolve_single_var():
    t = _trace(user_query="我能吃药吗")
    out = resolve_vars(t, {"user_query"})
    assert out == {"user_query": "我能吃药吗"}
    assert "agent_output" not in out


def test_resolve_multiple_vars():
    t = _trace(user_query="q", agent_output="a")
    out = resolve_vars(t, {"user_query", "agent_output"})
    assert out == {"user_query": "q", "agent_output": "a"}


def test_resolve_history_preserves_sourced_message_roles():
    t = _trace(
        conversation_history=[
            {
                "role": "user",
                "content": "q1",
                "sources": [{"trace_id": "t", "span_id": "s", "field_path": "input[0].content"}],
            },
            {
                "role": "assistant",
                "content": "a1",
                "sources": [{"trace_id": "t", "span_id": "s", "field_path": "input[1].content"}],
            },
        ]
    )
    history = resolve_vars(t, {"history"})["history"]
    assert [item["content"] for item in history] == ["q1", "a1"]
    assert history[1]["sources"][0]["field_path"] == "input[1].content"


def test_resolve_history_field_expression_from_semantic_projection():
    t = _trace(conversation_history=[{"role": "assistant", "content": "a2", "sources": []}])
    assert resolve_vars(t, {"history[0].content"}) == {"history[0].content": "a2"}


def test_resolve_steps():
    t = _trace(
        steps=[
            {
                "kind": "execution",
                "name": "A",
                "type": "AGENT",
                "status": "OK",
                "input": "in",
                "output": "out",
            }
        ]
    )
    out = resolve_vars(t, {"steps"})
    assert len(out["steps"]) == 1
    assert out["steps"][0]["node"] == "A"
    assert out["steps"][0]["input"] == "in"


def test_resolve_step_field_expression_from_semantic_projection():
    t = _trace(
        steps=[
            {
                "kind": "execution",
                "name": "A",
                "type": "AGENT",
                "status": "OK",
                "input": "in",
                "output": "out",
            }
        ]
    )

    out = resolve_vars(t, {"steps[0].output", "outcome.result_state"})

    assert out["steps[0].output"] == "out"
    assert out["outcome.result_state"] == "SUCCESS"


def test_resolve_steps_io_head_tail_truncated():
    """超安全阀的 step IO 走头尾保留（保住结尾结论），非盲头切。"""
    long_in = "H" * 30000 + "结论在最后" + "T" * 30000  # 远超 50K 安全阀
    t = _trace(
        steps=[
            {
                "kind": "execution",
                "name": "A",
                "type": "AGENT",
                "status": "OK",
                "input": long_in,
                "output": "out",
            }
        ]
    )
    io = resolve_vars(t, {"steps"})["steps"][0]["input"]
    assert "省略" in io  # 中间省略标记
    assert io.startswith("H")  # 头保留
    assert io.endswith("T")  # 尾保留（盲头切会丢这段）
    assert len(io) < len(long_in)


def test_resolve_steps_io_under_budget_unchanged():
    """常规体量（配置信息 56KB 量级以下）完整保留。"""
    normal = "x" * 8000  # 多轮历史量级，< 50K 安全阀
    t = _trace(
        steps=[
            {
                "kind": "execution",
                "name": "A",
                "type": "AGENT",
                "status": "OK",
                "input": normal,
                "output": "out",
            }
        ]
    )
    assert resolve_vars(t, {"steps"})["steps"][0]["input"] == normal  # 原样，不截


def test_resolve_ignores_unknown_vars():
    t = _trace(user_query="q")
    out = resolve_vars(t, {"user_query", "nonexistent_var"})
    assert "user_query" in out
    assert "nonexistent_var" not in out


def test_resolve_empty_set():
    t = _trace(user_query="q")
    out = resolve_vars(t, set())
    assert out == {}


def test_resolve_all_vars():
    t = _trace(
        user_query="q", agent_output="a", node_seq=["A"], steps=[{"kind": "execution", "name": "A"}]
    )
    out = resolve_vars(t, AVAILABLE_VARS)
    assert set(out.keys()) == AVAILABLE_VARS


def test_snapshot():
    t = _trace(user_query="q", agent_output="a", node_seq=["A", "A", "B"])
    snap = build_snapshot(t)
    assert snap["user_query"] == "q"
    assert snap["agent_output"] == "a"
    assert snap["node_sequence"] == ["A", "A", "B"]
    assert "outcome" in snap
    assert "labels" in snap


def test_coding_shared_default_inputs_have_source_positive_and_missing_cases():
    from agentlens.service.datasource.projection.service import project_trace
    from agentlens.service.datasource.trace import Trace
    from agentlens.service.datasource.variables import resolve_prompt_vars
    from tests.unit.diagnoser.test_shared_evidence_projection import row

    projection = project_trace(
        Trace(
            "t",
            "codex",
            [
                row(
                    "root",
                    "CHAT",
                    **{
                        "gen_ai.input.messages": [
                            {"role": "user", "content": "old"},
                            {"role": "assistant", "content": "old answer"},
                            {"role": "user", "content": "current"},
                        ],
                        "gen_ai.output.messages": [{"role": "assistant", "content": "final"}],
                    },
                ),
                row(
                    "shell",
                    "TOOL",
                    **{
                        "gen_ai.tool.name": "shell",
                        "gen_ai.tool.call.id": "v1",
                        "gen_ai.tool.call.arguments": {"command": "pytest"},
                        "gen_ai.tool.call.result": "passed",
                    },
                ),
            ],
        )
    )
    values = resolve_prompt_vars(
        projection,
        {"current_request", "final_output", "history", "tool_actions", "verification_actions"},
    )
    assert values["current_request"]["sources"][0]["span_id"] == "root"
    assert values["final_output"]["content"] == "final"
    assert values["history"][0]["content"] == "old"
    assert values["tool_actions"][0]["call_id"] == "v1"
    assert values["verification_actions"][0]["source_span_id"] == "shell"
    missing = resolve_prompt_vars(
        project_trace(Trace("t", "codex", [row("root", "CHAT")])), set(values)
    )
    assert missing == {
        "current_request": None,
        "final_output": None,
        "history": None,
        "tool_actions": [],
        "verification_actions": [],
    }
