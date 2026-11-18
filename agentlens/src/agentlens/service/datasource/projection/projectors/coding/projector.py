"""Projection and variable resolution for coding-agent runtimes."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from agentlens.service.datasource.projection.analysis import collapse_seq
from agentlens.service.datasource.projection.contracts import Projector
from agentlens.service.datasource.projection.models import ExecutionStep, Projection
from agentlens.service.datasource.projection.projectors.coding.models import CodingProjection
from agentlens.service.datasource.projection.serialization import resolve_projection_expression
from agentlens.utils.text import smart_truncate

_STEP_IO_PREVIEW = 50_000


def _text(value: Any, limit: int) -> str | None:
    if value in (None, ""):
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=repr)
    return smart_truncate(text, limit)


def _prompt_fact_list(trace: Projection, field_name: str) -> list[dict[str, Any]]:
    """Serialize confirmed Coding-only facts for prompt rendering.

    Non-Coding projections deliberately expose no synthetic replacement facts.
    """
    if not isinstance(trace, CodingProjection):
        return []

    def _json_value(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(key): _json_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_json_value(item) for item in value]
        return value

    if field_name == "file_mutations":
        from agentlens.service.datasource.projection.projectors.coding.adapter import (
            derive_file_mutations,
        )

        facts = derive_file_mutations(trace.tool_actions)
    elif field_name == "verification_actions":
        from agentlens.service.datasource.projection.projectors.coding.adapter import (
            derive_verification_actions,
        )

        facts = derive_verification_actions(trace.tool_actions)
    else:
        facts = getattr(trace, field_name)
    return [_json_value(asdict(item)) for item in facts]


def _resolve_var(trace: Projection, var: str) -> Any:
    if var.strip() == "*" or "." in var or "[" in var:
        return resolve_projection_expression(trace, var)
    if var in {"current_request", "final_output", "provided_context", "model_instructions"}:
        value = getattr(trace, var)
        return (
            [asdict(item) for item in value]
            if isinstance(value, list)
            else asdict(value)
            if value
            else None
        )
    if var == "history":
        return resolve_projection_expression(trace, "history")
    if var == "user_query":
        return trace.user_query or ""
    if var == "agent_output":
        return trace.agent_output or ""
    if var == "outcome":
        return asdict(trace.outcome)
    if var == "node_sequence":
        return " → ".join(collapse_seq(trace.node_seq))
    if var == "steps":
        return [
            {
                "node": step.name,
                "type": step.type,
                "status": step.status,
                "input": _text(step.input, _STEP_IO_PREVIEW),
                "output": _text(step.output, _STEP_IO_PREVIEW),
            }
            for step in trace.steps
            if isinstance(step, ExecutionStep)
        ]
    if var == "tool_actions":
        return _prompt_fact_list(trace, "tool_actions")
    if var == "file_mutations":
        return _prompt_fact_list(trace, "file_mutations")
    if var == "verification_actions":
        return _prompt_fact_list(trace, "verification_actions")
    if var == "subagents":
        return _prompt_fact_list(trace, "subagents")
    return ""


class CodingProjector(Projector):
    """Projector for Claude Code, Codex, and compatible coding agents."""

    canonical_runtime = "cc"
    available_vars = {
        "current_request": "当前请求及其原始字段来源",
        "final_output": "最终输出及其原始字段来源",
        "history": "当前请求前实际提供的历史消息；未知时为空",
        "provided_context": "生成输入中实际提供的工具资料，含来源",
        "model_instructions": "实际提供的系统指令，不作为检索依据",
        "user_query": "用户指令(当前 CHAT turn 的 input.messages 取 user content)",
        "agent_output": (
            "agent 最终回答(CHAT anchor 的最新 assistant 优先，否则取最后一个 LLM assistant；"
            "无可见文本保持缺失)"
        ),
        "outcome": "执行结果(has_error/result_state/latency/interrupted)",
        "node_sequence": "当前 Turn 的 CHAT/LLM/TOOL 执行时序，折叠连续重复节点",
        "steps": "关键步骤(LLM/tool IO,含工具参数与结果)",
        "tool_actions": "已确认的工具调用（名称、参数、结果、状态、错误、耗时和类型）",
        "file_mutations": "由已确认工具调用直接提取的代码或配置文件修改事实",
        "verification_actions": "由已确认工具调用直接提取的测试、Lint、类型检查或构建事实",
        "subagents": "有明确来源 span 的子 Agent 委派事实",
    }

    def project(
        self,
        raw_rows: list[dict[str, Any]],
        trace_id: str,
    ) -> CodingProjection | None:
        """Project one raw source trace into one Coding diagnosis target."""
        from agentlens.service.datasource.projection.projectors.coding.adapter import (
            project_coding_raw,
        )

        projections = project_coding_raw(raw_rows, trace_id)
        return projections[0] if projections else None

    def resolve_var(self, trace: Any, var: str) -> Any:
        return _resolve_var(trace, var)
