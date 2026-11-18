"""TrajectoryEvent 标准字段与序列化契约。"""

from agentlens.service.datasource.trajectory import SpanKind, TrajectoryEvent

# ── A2：type → SpanKind 枚举 ──────────────────────────────────────────────


def test_spankind_from_raw_normalizes():
    assert SpanKind.from_raw("GENERATION") is SpanKind.GENERATION
    assert SpanKind.from_raw("generation") is SpanKind.GENERATION  # 大小写归一
    assert SpanKind.from_raw("GUARDRAIL") is SpanKind.GUARDRAIL  # 补齐(原先漏→UNKNOWN)
    # gen_ai.span.kind 标准映射(对齐 build_observation_type)
    assert SpanKind.from_raw("LLM") is SpanKind.GENERATION
    assert SpanKind.from_raw("CHAT") is SpanKind.SPAN
    assert SpanKind.from_raw("MCP") is SpanKind.TOOL
    assert SpanKind.from_raw("RERANKER") is SpanKind.CHAIN
    assert SpanKind.from_raw("weird-thing") is SpanKind.UNKNOWN  # 未知兜底
    assert SpanKind.from_raw(None) is SpanKind.UNKNOWN


def test_spankind_str_compatible():
    assert SpanKind.GENERATION == "GENERATION"  # 仍可跟字符串比/作 key
    assert str(SpanKind.GENERATION) == "GENERATION"
    assert SpanKind.GENERATION in {"GENERATION", "TOOL"}


def _evt(**kw) -> TrajectoryEvent:
    base = dict(
        span_id="s1",
        parent_span_id=None,
        depth=0,
        seq_index=0,
        type="GENERATION",
        name="gen",
        start_time=None,
        end_time=None,
        duration=None,
        level="DEFAULT",
        is_error=False,
        status_message="",
        model=None,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        total_cost=None,
        input=None,
        output=None,
        outcome="ok",
        raw_ref=0,
        attributes={"gen_ai.request.temperature": 0.7},
        vendor_ext={"sofa_span_id": "sofa-1"},
    )
    base.update(kw)
    return TrajectoryEvent(**base)


def test_attributes_and_vendor_ext_are_fields():
    e = _evt()
    assert e.attributes["gen_ai.request.temperature"] == 0.7
    assert e.vendor_ext["sofa_span_id"] == "sofa-1"
    # sofa/meta 保持在 metadata 中
    assert not hasattr(e, "sofa_span_id")
    assert not hasattr(e, "meta_span_id")


def test_a4_otel_field_names():
    """A4：字段名对齐 OTel —— span_id / parent_span_id / input_tokens / output_tokens。"""
    e = _evt(span_id="x", input_tokens=3, output_tokens=5)
    assert e.span_id == "x" and not hasattr(e, "id")
    assert e.input_tokens == 3 and not hasattr(e, "prompt_tokens")
    assert e.output_tokens == 5 and not hasattr(e, "completion_tokens")
    assert hasattr(e, "parent_span_id")
