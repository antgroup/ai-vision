from __future__ import annotations

from agentlens.service.datasource.otel_mapper import (
    build_observation_level,
    build_observation_type,
    build_span_linkage,
    build_usage_details,
    map_root_span_row_to_trace,
    map_span_row_to_observation,
    resolve_semantic_value,
    select_root_span_row,
)


def _row(**overrides):
    base = {
        "trace_id": "tid",
        "span_id": "sid",
        "parent_span_id": None,
        "name": "step",
        "kind": "INTERNAL",
        "period": 1_716_811_200_000_000_000,
        "start_time_unix_nano": 1_716_811_200_000_000_000,
        "end_time_unix_nano": 1_716_811_200_500_000_000,
    }
    base.update(overrides)
    return base


def test_observation_type_from_langfuse_wins() -> None:
    row = _row(**{"attributes.langfuse.observation.type": "TOOL"})
    assert build_observation_type(row) == "TOOL"


def test_observation_type_from_gen_ai_span_kind() -> None:
    assert build_observation_type(_row(**{"attributes.gen_ai.span.kind": "LLM"})) == "GENERATION"
    assert build_observation_type(_row(**{"attributes.gen_ai.span.kind": "TOOL"})) == "TOOL"


def test_observation_type_from_operation_name() -> None:
    tool_row = _row(**{"attributes.gen_ai.operation.name": "execute_tool"})
    agent_row = _row(**{"attributes.gen_ai.operation.name": "invoke_agent"})
    assert build_observation_type(tool_row) == "TOOL"
    assert build_observation_type(agent_row) == "AGENT"


def test_observation_type_default_span() -> None:
    assert build_observation_type(_row()) == "SPAN"


def test_observation_level_error_when_status_code_2() -> None:
    assert build_observation_level(_row(**{"status.code": 2})) == "ERROR"


def test_observation_level_langfuse_overrides_status() -> None:
    row = _row(**{"attributes.langfuse.observation.level": "WARNING", "status.code": 2})
    assert build_observation_level(row) == "WARNING"


def test_resolve_semantic_value_falls_back_to_candidates() -> None:
    row = _row(**{"attributes.gen_ai.request.model": "gpt-4"})
    assert resolve_semantic_value(row, "model") == "gpt-4"
    # response.model takes priority over request.model
    row["attributes.gen_ai.response.model"] = "gpt-4-turbo"
    assert resolve_semantic_value(row, "model") == "gpt-4-turbo"


def test_build_span_linkage_uses_meta_span_id() -> None:
    row = _row(**{"attributes.__meta__.spanId": "0.1.2"})
    assert build_span_linkage(row) == ("0.1.2", None)


def test_build_span_linkage_uses_sofa_for_parent_role() -> None:
    row = _row(
        **{
            "attributes._internal.span_link.role": "parent",
            "attributes._internal.sofa.span_id": "0.5",
        }
    )
    assert build_span_linkage(row) == ("0.5", None)


def test_build_span_linkage_uses_sofa_for_child_role() -> None:
    row = _row(
        **{
            "attributes._internal.span_link.role": "child",
            "attributes._internal.sofa.span_id": "0.5",
        }
    )
    assert build_span_linkage(row) == (None, "0.5")


def test_build_usage_details_computes_total_if_missing() -> None:
    row = _row(
        **{
            "attributes.gen_ai.usage.input_tokens": 10,
            "attributes.gen_ai.usage.output_tokens": 5,
        }
    )
    usage = build_usage_details(row)
    assert usage == {"input": 10.0, "output": 5.0, "total": 15.0}


def test_select_root_span_row_picks_null_parent() -> None:
    rows = [
        _row(span_id="child", parent_span_id="root", period=2_000_000_000_000_000_000),
        _row(span_id="root", parent_span_id=None, period=1_000_000_000_000_000_000),
    ]
    assert select_root_span_row(rows)["span_id"] == "root"


def test_select_root_span_row_picks_earliest_when_no_null_parent() -> None:
    # start_time_unix_nano is consulted first by _trace_start_value; override it to
    # exercise the "earliest start" tiebreaker when no row has a null parent.
    rows = [
        _row(
            span_id="a",
            parent_span_id="other",
            start_time_unix_nano=2_000_000_000_000_000_000,
            period=2_000_000_000_000_000_000,
        ),
        _row(
            span_id="b",
            parent_span_id="other",
            start_time_unix_nano=1_000_000_000_000_000_000,
            period=1_000_000_000_000_000_000,
        ),
    ]
    assert select_root_span_row(rows)["span_id"] == "b"


def test_map_span_row_to_observation_basic_shape() -> None:
    row = _row(
        **{
            "attributes.gen_ai.input.messages": "in",
            "attributes.gen_ai.output.messages": "out",
            "attributes.gen_ai.response.model": "gpt-4",
            "attributes.gen_ai.usage.input_tokens": 10,
            "attributes.gen_ai.usage.output_tokens": 4,
        }
    )
    obs = map_span_row_to_observation(row)
    assert obs["id"] == "sid"
    assert obs["traceId"] == "tid"
    assert obs["name"] == "step"
    assert obs["type"] == "SPAN"
    assert obs["startTime"] == "2024-05-27T12:00:00Z"
    assert obs["endTime"] == "2024-05-27T12:00:00.500000Z"
    assert obs["input"] == "in"
    assert obs["output"] == "out"
    assert obs["model"] == "gpt-4"
    assert obs["promptTokens"] == 10
    assert obs["completionTokens"] == 4
    assert obs["totalTokens"] == 14
    assert obs["latency"] == 0.5


def test_map_root_span_row_to_trace_uses_observation_count() -> None:
    row = _row(observation_count=3)
    trace = map_root_span_row_to_trace(row, observation_ids=["a", "b", "c"], latency=1.25)
    assert trace["id"] == "tid"
    assert trace["observations"] == ["a", "b", "c"]
    assert trace["observationCount"] == 3
    assert trace["latency"] == 1.25
