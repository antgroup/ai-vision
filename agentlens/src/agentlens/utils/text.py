"""文本工具：智能截断（头尾保留 + 中间省略）。

诊断喂 LLM 的长文本（context/history/RAG/IO）超预算时，盲头切会丢结尾结论
（医疗回答的结论常在末尾）。head+tail 保住"开头 + 结尾"，中间打省略标记。
"""

from __future__ import annotations

from typing import Any


def smart_truncate(text: Any, budget: int, *, head_ratio: float = 0.6) -> Any:
    """超预算文本头尾保留 + 中间省略，否则原样返回。

    - budget：保留字符总预算（不含省略标记）。<=0 → 返回空串。
    - head_ratio：头部占预算比例（0~1），其余给尾部。
    - 非字符串原样返回（防御）。
    """
    if not isinstance(text, str):
        return text
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    head = int(budget * head_ratio)
    tail = budget - head
    omitted = len(text) - budget
    tail_part = text[-tail:] if tail > 0 else ""
    return f"{text[:head]}…[省略 {omitted} 字]…{tail_part}"
