# Tool span forbidden/value rules.
# Scope: gen_ai.span.kind=TOOL or legacy tool spans.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 禁止 Tool span 使用旧字段 `gen_ai.tool.raw_name`。
# @weaver.rule.check.zh: 当 Tool span 上存在 `gen_ai.tool.raw_name` 时命中。
# @weaver.rule.fix.zh: 改用 `gen_ai.tool.name`，并在 span name 上使用带类型前缀的工具名。
# @weaver.rule.entity: span:tool
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: agentic.tool.duration_ms, error.type, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, gen_ai.tool.raw_name, span.name
# @weaver.rule.subject: field:gen_ai.tool.raw_name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
deny contains make_advice(
    "tool_deprecated_raw_name",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Tool span '%s': Forbidden attribute 'gen_ai.tool.raw_name'. Use 'gen_ai.tool.name' with the prefixed name (e.g., 'mcp server.method', 'skill name', 'tool name').", [input.sample.span.name])
) if {
    is_tool_span
    has_span_attr("gen_ai.tool.raw_name")
}

# @weaver.rule.zh: 禁止 Tool 耗时为负数。
# @weaver.rule.check.zh: 当 `agentic.tool.duration_ms` 小于 0 时命中。
# @weaver.rule.fix.zh: 修复开始/结束时间采集或单位换算，确保毫秒耗时非负。
# @weaver.rule.entity: span:tool
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: agentic.tool.duration_ms, error.type, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, gen_ai.tool.raw_name, span.name
# @weaver.rule.subject: field:agentic.tool.duration_ms
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: invariant
deny contains make_advice(
    "tool_negative_duration",
    "violation",
    {"span_name": input.sample.span.name, "duration_ms": duration},
    sprintf(
        "Tool span '%s': agentic.tool.duration_ms = %v is negative.",
        [input.sample.span.name, duration],
    ),
) if {
    is_tool_span
    has_span_attr("agentic.tool.duration_ms")
    duration := get_span_attr_int("agentic.tool.duration_ms")
    duration < 0
}
