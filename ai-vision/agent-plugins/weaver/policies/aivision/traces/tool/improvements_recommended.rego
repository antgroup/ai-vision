# Tool span recommended-field rules.
# Scope: gen_ai.span.kind=TOOL or legacy tool spans.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 推荐 Tool span 包含规范工具名 `gen_ai.tool.name`。
# @weaver.rule.check.zh: 当 Tool span 缺少 `gen_ai.tool.name` 时命中。
# @weaver.rule.fix.zh: 从工具调用元数据中提取工具名；MCP/skill 工具建议带清晰命名空间。子 agent 调用应使用 AGENT/invoke_agent，不归入 Tool span。
# @weaver.rule.entity: span:tool
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: agentic.tool.duration_ms, error.type, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, gen_ai.tool.raw_name, span.name
# @weaver.rule.subject: field:gen_ai.tool.name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "tool_missing_tool_name",
    "improvement",
    {"span_name": input.sample.span.name},
    sprintf("Tool span '%s': Missing 'gen_ai.tool.name'. Tool name unavailable when transcript lacks tool_use blocks.", [input.sample.span.name])
) if {
    is_tool_span
    not has_span_attr("gen_ai.tool.name")
}

# @weaver.rule.zh: 推荐 Tool span 包含 `gen_ai.tool.call.id`，用于请求/响应配对。
# @weaver.rule.check.zh: 当 Tool span 缺少 `gen_ai.tool.call.id` 时命中。
# @weaver.rule.fix.zh: 使用 agent 原生 tool_use_id、call_id 或稳定派生 id 写入该字段。
# @weaver.rule.entity: span:tool
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: agentic.tool.duration_ms, error.type, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, gen_ai.tool.raw_name, span.name
# @weaver.rule.subject: field:gen_ai.tool.call.id
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "tool_missing_call_id",
    "improvement",
    {"span_name": input.sample.span.name},
    sprintf("Tool span '%s': Missing recommended 'gen_ai.tool.call.id'. Use tool.use_id as mapping.", [input.sample.span.name])
) if {
    is_tool_span
    not has_span_attr("gen_ai.tool.call.id")
}

# @weaver.rule.zh: 推荐 Tool ERROR span 包含 `error.type`。
# @weaver.rule.check.zh: 当 Tool span status.code 为 ERROR 但缺少 `error.type` 时命中。
# @weaver.rule.fix.zh: 在异常路径同时写入 `status.code=ERROR` 与 `error.type`，必要时补充 `error.message`。
# @weaver.rule.entity: span:tool
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: agentic.tool.duration_ms, error.type, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, gen_ai.tool.raw_name, span.name
# @weaver.rule.subject: field:error.type
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "tool_error_missing_error_type",
    "improvement",
    {"span_name": input.sample.span.name},
    sprintf(
        "Tool span '%s': status.code is ERROR but missing recommended 'error.type'. Implementations satisfying B2 should set both together.",
        [input.sample.span.name],
    ),
) if {
    is_tool_span
    _capability_b2_is_error_status
    not has_span_attr("error.type")
}

# @weaver.rule.zh: 推荐 Tool span 使用带类型前缀的 span name。
# @weaver.rule.check.zh: 当 Tool span name 不是 `tool `、`builtin `、`mcp `、`skill ` 前缀，且不是 compaction/subagent 例外时作为推荐项命中。
# @weaver.rule.fix.zh: 将工具 span name 改为 `{tool|builtin|mcp|skill} {name}`，例如 `tool Bash`、`builtin exec`、`mcp filesystem.read`；子 agent 调用使用 `agent {name}` 但应归类为 AGENT span。
# @weaver.rule.entity: span:tool
# @weaver.rule.category.zh: 格式校验
# @weaver.rule.fields: agentic.tool.duration_ms, error.type, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, gen_ai.tool.raw_name, span.name
# @weaver.rule.subject: field:span.name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: format
deny contains make_advice(
    "tool_span_naming_convention",
    "improvement",
    {"span_name": input.sample.span.name, "allowed_prefixes": ["tool", "builtin", "mcp", "skill"]},
    sprintf("Tool span '%s': Recommended span name uses prefix '{tool|builtin|mcp|skill} {name}' (e.g., 'tool Bash', 'builtin exec', 'mcp Read', 'skill commit'). Raw tool names without prefix are discouraged; 'agent {name}' is reserved for AGENT/invoke_agent spans.", [input.sample.span.name])
) if {
    is_tool_span
    not startswith(input.sample.span.name, "tool ")
    not startswith(input.sample.span.name, "builtin ")
    not startswith(input.sample.span.name, "mcp ")
    not startswith(input.sample.span.name, "skill ")
    not input.sample.span.name == "compaction"
    not startswith(input.sample.span.name, "subagent ")
}
