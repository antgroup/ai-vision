"""Optional per-Diagnoser Chat generation limits, shared by API and execution."""

import math
from typing import Any

REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})


def llm_generation_options(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate explicitly supplied limits without adding provider defaults."""
    options = {}
    if "reasoning_effort" in spec:
        effort = spec["reasoning_effort"]
        valid_name = isinstance(effort, str) and effort in REASONING_EFFORTS
        valid_number = type(effort) is int and 1 <= effort <= 100
        if not (valid_name or valid_number):
            raise ValueError(
                "reasoning_effort 必须是 none/minimal/low/medium/high/xhigh/max 字符串"
                "或 1..100 的非布尔整数"
            )
        options["reasoning_effort"] = effort
    if "max_tokens" in spec:
        max_tokens = spec["max_tokens"]
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError("max_tokens 必须是非布尔的正整数")
        options["max_tokens"] = max_tokens
    return options


def llm_runtime_options(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate optional generation and transport configuration from a Spec."""
    options = llm_generation_options(spec)
    if "request_timeout_sec" in spec:
        timeout = spec["request_timeout_sec"]
        if (
            type(timeout) not in (int, float)
            or not 0 < timeout <= 300
            or not math.isfinite(timeout)
        ):
            raise ValueError("request_timeout_sec 必须是大于 0 且不超过 300 的有限非布尔数值")
        options["request_timeout_sec"] = timeout
    return options
