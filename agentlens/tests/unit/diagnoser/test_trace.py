"""Shared sequence analysis and one-way Trace serialization contracts."""

from agentlens.service.datasource.projection import Projection
from agentlens.service.datasource.projection.analysis import (
    collapse_seq,
    loop_count,
    matches,
    steps_count,
)
from agentlens.service.datasource.projection.models import TraceOutcome
from agentlens.service.datasource.trace import Trace
from tests.unit.diagnoser.projection_factory import projection_from_dict

# ── 纯单元（不依赖语料）──────────────────────────────────────────────


def test_collapse_seq():
    assert collapse_seq(["a", "a", "b", "b", "a"]) == ("a", "b", "a")
    assert collapse_seq([]) == ()


def test_matches_superset():
    dt = Projection(
        timestamp=None,
        user_query="",
        agent_output="",
        outcome=TraceOutcome(
            has_error=False,
            result_state=None,
            total_latency_ms=None,
            interrupted=False,
        ),
        labels={},
        node_seq=["A", "B", "C"],
    )
    assert matches(dt, ["A", "C"]) is True  # 子集 ⊆ 实际
    assert matches(dt, ["A", "X"]) is False


def test_matches_strict_order():
    dt = Projection(
        timestamp=None,
        user_query="",
        agent_output="",
        outcome=TraceOutcome(
            has_error=False,
            result_state=None,
            total_latency_ms=None,
            interrupted=False,
        ),
        labels={},
        node_seq=["A", "B", "C"],
    )
    assert matches(dt, ["A", "C"], mode="strict") is True  # 有序子序列
    assert matches(dt, ["C", "A"], mode="strict") is False  # 逆序不符


def test_derive_counts():
    # 折叠步数(path_efficiency 用 node_seq)
    dt = projection_from_dict(
        {
            "trace_id": "t",
            "node_seq": ["x", "tool_executor", "x", "tool_executor"],
            "runtime_type": "codex",
        }
    )
    assert steps_count(dt) == 4  # x,tool,x,tool 相邻不同名，不折叠
    # loop_count 用 steps(同节点 + IO 相同才算无进展卡死);node_seq 只供 steps_count
    # 无 steps → loop_count=0(无法判 IO)
    assert loop_count(dt) == 0
    # 连续重复 + IO 相同 → 无进展循环
    s = {"kind": "execution", "name": "B", "input": "same", "output": "same"}
    dt2 = projection_from_dict(
        {
            "trace_id": "t",
            "node_seq": ["A", "B", "B", "B", "C", "B"],
            "steps": [
                {"kind": "execution", "name": "A", "input": "a", "output": "a"},
                s,
                s,
                s,
                {"kind": "execution", "name": "C", "input": "c", "output": "c"},
                {"kind": "execution", "name": "B", "input": "other", "output": "other"},
            ],
            "runtime_type": "codex",
        }
    )
    assert loop_count(dt2) == 3  # B 连续 3 次 + IO 相同


def test_projection_serialization_is_one_way():
    from agentlens.service.datasource.projection.serialization import (
        serialize_projection,
        serialize_trace_projection,
    )

    dt = projection_from_dict(
        {
            "trace_id": "t1",
            "timestamp": "2026",
            "user_query": "q",
            "agent_output": "a",
            "outcome": {"has_error": False},
            "labels": {"intent": "x"},
            "runtime_type": "codex",
        }
    )
    serialized = serialize_projection(dt)
    assert "trace_id" not in serialized and "runtime_type" not in serialized
    assert "session_id" not in serialized and "user_id" not in serialized
    trace_data = serialize_trace_projection(
        Trace(trace_id="t1", runtime_type="codex", spans=[], projection=dt)
    )
    assert trace_data["trace_id"] == "t1"
    assert trace_data["runtime_type"] == "codex"
    assert not hasattr(Projection, "from_dict")
    assert not hasattr(Projection, "to_dict")
