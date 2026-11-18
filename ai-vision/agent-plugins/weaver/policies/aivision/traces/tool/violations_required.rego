# Tool span required-field rules.
# Scope: gen_ai.span.kind=TOOL or legacy tool spans.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 要求 Tool span 的 `gen_ai.span.kind` 值固定为 `TOOL`。
# @weaver.rule.check.zh: 当 Tool span 上 `gen_ai.span.kind` 存在但不是 `TOOL` 时命中。
# @weaver.rule.fix.zh: 将工具调用 span 的 `gen_ai.span.kind` 改为 `TOOL`；CHAT/LLM/COMPACTION 使用对应 span 类型。
# @weaver.rule.entity: span:tool
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: gen_ai.span.kind
# @weaver.rule.subject: field:gen_ai.span.kind
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: TOOL
deny contains make_advice(
    "tool_invalid_span_kind",
    "violation",
    {"span_name": input.sample.span.name, "actual_value": get_span_attr("gen_ai.span.kind"), "expected_value": "TOOL"},
    sprintf("Tool span '%s': gen_ai.span.kind must be 'TOOL'.", [input.sample.span.name])
) if {
    is_tool_span
    has_span_attr("gen_ai.span.kind")
    get_span_attr("gen_ai.span.kind") != "TOOL"
}

# @weaver.rule.zh: 要求 Tool span 的 `gen_ai.operation.name` 值固定为 `execute_tool`。
# @weaver.rule.check.zh: 当 Tool span 上 `gen_ai.operation.name` 存在但不是 `execute_tool` 时命中。
# @weaver.rule.fix.zh: 将工具调用 span 的 `gen_ai.operation.name` 改为 `execute_tool`；子 agent 调用应使用 `gen_ai.span.kind=AGENT` 与 `gen_ai.operation.name=invoke_agent`。
# @weaver.rule.note.zh: `agent {name}` span 表示 Agent/subagent invocation，不属于 Tool span；skill/mcp/tool 仍使用 TOOL/execute_tool。
# @weaver.rule.entity: span:tool
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: gen_ai.operation.name
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: execute_tool
deny contains make_advice(
    "tool_invalid_operation_name",
    "violation",
    {"span_name": input.sample.span.name, "actual_value": get_span_attr("gen_ai.operation.name"), "expected_value": "execute_tool"},
    sprintf("Tool span '%s': gen_ai.operation.name must be 'execute_tool'.", [input.sample.span.name])
) if {
    is_tool_span
    has_span_attr("gen_ai.operation.name")
    get_span_attr("gen_ai.operation.name") != "execute_tool"
}

# @weaver.rule.zh: 要求 Tool span 包含工具输入 `gen_ai.tool.call.arguments`。
# @weaver.rule.check.zh: 当 Tool span 缺少 `gen_ai.tool.call.arguments` 时命中。
# @weaver.rule.fix.zh: 将工具调用参数序列化写入该字段；默认页面可隐藏 payload，但上报数据需保留或显式脱敏。
# @weaver.rule.entity: span:tool
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.tool.duration_ms, error.type, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, gen_ai.tool.raw_name, span.name
# @weaver.rule.subject: field:gen_ai.tool.call.arguments
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "tool_missing_call_arguments",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Tool span '%s': Missing required 'gen_ai.tool.call.arguments' (tool input).", [input.sample.span.name])
) if {
    is_tool_span
    not has_span_attr("gen_ai.tool.call.arguments")
}

# @weaver.rule.zh: 要求 Tool span 包含工具输出 `gen_ai.tool.call.result`。
# @weaver.rule.check.zh: 当 Tool span 缺少 `gen_ai.tool.call.result` 时命中。
# @weaver.rule.fix.zh: 将工具结果序列化写入该字段；失败路径可写错误结构或显式脱敏结果。
# @weaver.rule.entity: span:tool
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.tool.duration_ms, error.type, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, gen_ai.tool.raw_name, span.name
# @weaver.rule.subject: field:gen_ai.tool.call.result
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "tool_missing_call_result",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Tool span '%s': Missing required 'gen_ai.tool.call.result' (tool output).", [input.sample.span.name])
) if {
    is_tool_span
    not has_span_attr("gen_ai.tool.call.result")
}

# @weaver.rule.zh: 要求 Tool span 包含 `agentic.tool.duration_ms`，用于工具耗时分析。
# @weaver.rule.check.zh: 当 Tool span 缺少 `agentic.tool.duration_ms` 时命中。
# @weaver.rule.fix.zh: 用工具调用开始/结束时间计算毫秒耗时并写入该字段。
# @weaver.rule.entity: span:tool
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.tool.duration_ms, error.type, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, gen_ai.tool.raw_name, span.name
# @weaver.rule.subject: field:agentic.tool.duration_ms
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "tool_missing_duration",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Tool span '%s': Missing required 'agentic.tool.duration_ms'.", [input.sample.span.name])
) if {
    is_tool_span
    not has_span_attr("agentic.tool.duration_ms")
}
