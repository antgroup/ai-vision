from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class SpanKind(StrEnum):
    """span 类型闭集（对齐观测层标准：ai-vision build_observation_type / infer_observation_type
    的输出词表）。StrEnum → 仍可跟字符串比/作 dict key。OTel 正名（GENERATION→LLM）留 A4，
    未知值 → UNKNOWN。
    """

    GENERATION = "GENERATION"
    RETRIEVER = "RETRIEVER"
    AGENT = "AGENT"
    TOOL = "TOOL"
    CHAIN = "CHAIN"
    EMBEDDING = "EMBEDDING"
    GUARDRAIL = "GUARDRAIL"
    SPAN = "SPAN"
    EVENT = "EVENT"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_raw(cls, raw: str | None) -> SpanKind:
        if not raw:
            return cls.UNKNOWN
        key = str(raw).strip().upper()
        try:
            return cls(key)
        except ValueError:
            return _SPANKIND_ALIASES.get(key, cls.UNKNOWN)


# gen_ai.span.kind → 输出词表（逐字对齐观测层标准 span_kind_mapping；
# 上游 otel_mapper.build_observation_type 已做主映射，这里是枚举边界的防御层）
_SPANKIND_ALIASES = {
    "CHAT": SpanKind.SPAN,
    "LLM": SpanKind.GENERATION,
    "MCP": SpanKind.TOOL,
    "RERANKER": SpanKind.CHAIN,
}


@dataclass
class TrajectoryEvent:
    span_id: str  # OTel span_id（原 id）
    parent_span_id: str | None  # OTel parent_span_id（原 parent_id）
    depth: int
    seq_index: int
    type: SpanKind
    name: str
    start_time: datetime | None
    end_time: datetime | None
    duration: float | None
    level: str
    is_error: bool
    status_message: str
    model: str | None
    input_tokens: int  # OTel gen_ai.usage.input_tokens（原 prompt_tokens）
    output_tokens: int  # OTel gen_ai.usage.output_tokens（原 completion_tokens）
    total_tokens: int
    total_cost: float | None
    input: Any
    output: Any
    outcome: str
    attributes: dict[str, Any]  # OTel 开放袋：未映射的 span 属性无损透传（gen_ai.* 等）
    vendor_ext: dict[str, Any]  # 厂商内部字段收编（sofa_span_id/meta_span_id），不占顶层
    raw_ref: int
