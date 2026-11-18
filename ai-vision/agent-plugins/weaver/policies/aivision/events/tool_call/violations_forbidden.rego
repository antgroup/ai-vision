# tool_call fixed-value, enum, and forbidden-field rules.

package live_check_advice

import rego.v1

_valid_tool_call_types := {"builtin", "mcp", "skill"}
_tool_call_duration_attrs := {"duration_ms", "agentic.tool.duration_ms"}
_tool_call_error_attrs := {"error.type", "error.message"}
_tool_call_decision_attrs := {"agentic.tool.decision", "agentic.tool.decision.source"}

# @weaver.rule.zh: tool_call 表示工具开始执行，operation 必须是 execute_tool。
# @weaver.rule.check.zh: `event.name=tool_call` 且 `gen_ai.operation.name` 不是 `execute_tool` 时命中。
# @weaver.rule.fix.zh: tool_call 事件写入 `gen_ai.operation.name=execute_tool`。
# @weaver.rule.entity: event:tool_call
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: gen_ai.operation.name
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: execute_tool
deny contains make_advice(
    "tool_call_wrong_operation_name",
    "violation",
    {"event_name": "tool_call", "attribute": "gen_ai.operation.name"},
    sprintf("Event 'tool_call': gen_ai.operation.name should be 'execute_tool', got '%s'.", [get_log_attr("gen_ai.operation.name")])
) if {
    _is_tool_call
    has_log_attr("gen_ai.operation.name")
    get_log_attr("gen_ai.operation.name") != "execute_tool"
}

# @weaver.rule.zh: tool_call 的工具类型只能是 builtin、mcp 或 skill；subagent 使用 AGENT 语义。
# @weaver.rule.check.zh: `agentic.tool.type` 不在 builtin/mcp/skill 集合内时命中。
# @weaver.rule.fix.zh: 修正工具分类；委派任务不要发出 tool_call。
# @weaver.rule.entity: event:tool_call
# @weaver.rule.category.zh: 枚举值
# @weaver.rule.fields: agentic.tool.type
# @weaver.rule.subject: field:agentic.tool.type
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: enum
# @weaver.rule.allowed_values: builtin, mcp, skill
deny contains make_advice(
    "tool_call_invalid_tool_type",
    "violation",
    {"event_name": "tool_call", "attribute": "agentic.tool.type", "value": get_log_attr("agentic.tool.type")},
    sprintf("Event 'tool_call': Invalid agentic.tool.type '%s'. Valid values: builtin, mcp, skill.", [get_log_attr("agentic.tool.type")])
) if {
    _is_tool_call
    has_log_attr("agentic.tool.type")
    not get_log_attr("agentic.tool.type") in _valid_tool_call_types
}

# @weaver.rule.zh: tool_call 是开始事件，禁止携带 duration。
# @weaver.rule.check.zh: 出现 duration_ms 或 agentic.tool.duration_ms 时命中。
# @weaver.rule.fix.zh: 删除 duration；耗时只写入 tool_result 与 TOOL span end。
# @weaver.rule.entity: event:tool_call
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: duration_ms, agentic.tool.duration_ms
# @weaver.rule.subject: field:duration_ms
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: forbidden_presence
deny contains make_advice("tool_call_forbidden_duration", "violation", {"event_name": "tool_call", "attribute": attr}, sprintf("Event 'tool_call': Forbidden duration attribute '%s'.", [attr])) if {
    _is_tool_call
    some attr in _tool_call_duration_attrs
    has_log_attr(attr)
}

# @weaver.rule.zh: tool_call 是开始事件，禁止携带结果。
# @weaver.rule.check.zh: 出现 gen_ai.tool.call.result 时命中。
# @weaver.rule.fix.zh: 将结果只写入 tool_result。
# @weaver.rule.entity: event:tool_call
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.tool.call.result
# @weaver.rule.subject: field:gen_ai.tool.call.result
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: forbidden_presence
deny contains make_advice("tool_call_forbidden_result", "violation", {"event_name": "tool_call", "attribute": "gen_ai.tool.call.result"}, "Event 'tool_call': Forbidden result attribute.") if {
    _is_tool_call
    has_log_attr("gen_ai.tool.call.result")
}

# @weaver.rule.zh: tool_call 是开始事件，禁止携带错误结果。
# @weaver.rule.check.zh: 出现 error.type 或 error.message 时命中。
# @weaver.rule.fix.zh: 将错误只写入 tool_result 与 TOOL span end。
# @weaver.rule.entity: event:tool_call
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: error.type, error.message
# @weaver.rule.subject: field:error.type
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: forbidden_presence
deny contains make_advice("tool_call_forbidden_error", "violation", {"event_name": "tool_call", "attribute": attr}, sprintf("Event 'tool_call': Forbidden error attribute '%s'.", [attr])) if {
    _is_tool_call
    some attr in _tool_call_error_attrs
    has_log_attr(attr)
}

# @weaver.rule.zh: tool_call 不携带完成态 success。
# @weaver.rule.check.zh: 出现 success 时命中。
# @weaver.rule.fix.zh: 删除 success；完成态由 tool_result 表达。
# @weaver.rule.entity: event:tool_call
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: success
# @weaver.rule.subject: field:success
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: forbidden_presence
deny contains make_advice("tool_call_forbidden_success", "violation", {"event_name": "tool_call", "attribute": "success"}, "Event 'tool_call': Forbidden success attribute.") if {
    _is_tool_call
    has_log_attr("success")
}

# @weaver.rule.zh: tool_call 表示执行开始，不表示权限决策。
# @weaver.rule.check.zh: 出现 decision 或 decision.source 时命中。
# @weaver.rule.fix.zh: 将真实权限决策写入独立 tool_decision 事件。
# @weaver.rule.entity: event:tool_call
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: agentic.tool.decision, agentic.tool.decision.source
# @weaver.rule.subject: field:agentic.tool.decision
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: forbidden_presence
deny contains make_advice("tool_call_forbidden_decision", "violation", {"event_name": "tool_call", "attribute": attr}, sprintf("Event 'tool_call': Forbidden decision attribute '%s'.", [attr])) if {
    _is_tool_call
    some attr in _tool_call_decision_attrs
    has_log_attr(attr)
}
