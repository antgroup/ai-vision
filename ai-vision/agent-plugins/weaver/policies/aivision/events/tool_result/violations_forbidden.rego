# tool_result forbidden/value rules.

package live_check_advice

import rego.v1

valid_tool_result_types := {"builtin", "mcp", "skill"}

# @weaver.rule.zh: tool_result 表示工具执行结果，operation 必须是 execute_tool。
# @weaver.rule.check.zh: `event.name=tool_result` 且 `gen_ai.operation.name` 不是 `execute_tool` 时命中。
# @weaver.rule.fix.zh: tool_result 事件写入 `gen_ai.operation.name=execute_tool`。
# @weaver.rule.entity: event:tool_result
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: agentic.tool.id, agentic.tool.name, agentic.tool.type, agentic.turn.number, duration_ms, error.message, error.type, event.name, gen_ai.chat.id, gen_ai.operation.name, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, success
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: execute_tool
deny contains make_advice(
    "tool_result_wrong_operation_name",
    "violation",
    {"event_name": "tool_result"},
    sprintf("Event 'tool_result': gen_ai.operation.name should be 'execute_tool', got '%s'.", [get_log_attr("gen_ai.operation.name")])
) if {
    _is_tool_result
    has_log_attr("gen_ai.operation.name")
    get_log_attr("gen_ai.operation.name") != "execute_tool"
}

# @weaver.rule.zh: tool_result 的工具类型只能是 builtin、mcp 或 skill。
# @weaver.rule.check.zh: `agentic.tool.type` 不在 builtin/mcp/skill 集合内时命中。
# @weaver.rule.fix.zh: 修正 classifyTool 分类；系统压缩事件应使用 compaction_result，不应伪装为 tool_result。
# @weaver.rule.entity: event:tool_result
# @weaver.rule.category.zh: 枚举值
# @weaver.rule.fields: agentic.tool.id, agentic.tool.name, agentic.tool.type, agentic.turn.number, duration_ms, error.message, error.type, event.name, gen_ai.chat.id, gen_ai.operation.name, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, success
# @weaver.rule.subject: field:agentic.tool.type
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: enum
# @weaver.rule.allowed_values: builtin, mcp, skill
deny contains make_advice(
    "tool_result_invalid_tool_type",
    "violation",
    {"event_name": "tool_result", "value": get_log_attr("agentic.tool.type")},
    sprintf("Event 'tool_result': Invalid agentic.tool.type '%s'. Valid values: builtin, mcp, skill.", [get_log_attr("agentic.tool.type")])
) if {
    _is_tool_result
    has_log_attr("agentic.tool.type")
    not get_log_attr("agentic.tool.type") in valid_tool_result_types
}

# @weaver.rule.zh: tool_result 错误事件必须携带错误消息，保证诊断可读。
# @weaver.rule.check.zh: tool_result 有 `error.type` 但缺少 `error.message` 时命中。
# @weaver.rule.fix.zh: 从 tool metadata.error、stderr/output 或 error state 写入 `error.message`。
# @weaver.rule.entity: event:tool_result
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.tool.id, agentic.tool.name, agentic.tool.type, agentic.turn.number, duration_ms, error.message, error.type, event.name, gen_ai.chat.id, gen_ai.operation.name, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, success
# @weaver.rule.subject: field:error.message
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "tool_result_error_missing_message",
    "violation",
    {"event_name": "tool_result"},
    "Event 'tool_result': Missing required 'error.message' when 'error.type' is present."
) if {
    _is_tool_result
    has_log_attr("error.type")
    not has_log_attr("error.message")
}

# @weaver.rule.zh: tool_result 错误消息不能脱离错误类型单独出现。
# @weaver.rule.check.zh: tool_result 有 `error.message` 但缺少 `error.type` 时命中。
# @weaver.rule.fix.zh: 失败工具事件同时写入 `error.type` 和 `error.message`；成功事件不要写 `error.message`。
# @weaver.rule.entity: event:tool_result
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.tool.id, agentic.tool.name, agentic.tool.type, agentic.turn.number, duration_ms, error.message, error.type, event.name, gen_ai.chat.id, gen_ai.operation.name, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, success
# @weaver.rule.subject: field:error.type
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "tool_result_error_missing_type",
    "violation",
    {"event_name": "tool_result"},
    "Event 'tool_result': Missing required 'error.type' when 'error.message' is present."
) if {
    _is_tool_result
    has_log_attr("error.message")
    not has_log_attr("error.type")
}

# @weaver.rule.zh: tool_result 不再使用布尔 `success`，成功由缺少 error.type 表示，失败由 error.type 表示。
# @weaver.rule.check.zh: tool_result attributes 中出现 `success` 时命中。
# @weaver.rule.fix.zh: 删除 `success`；失败时写入 `error.type` 和 `error.message`。
# @weaver.rule.entity: event:tool_result
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: agentic.tool.id, agentic.tool.name, agentic.tool.type, agentic.turn.number, duration_ms, error.message, error.type, event.name, gen_ai.chat.id, gen_ai.operation.name, gen_ai.tool.call.arguments, gen_ai.tool.call.id, gen_ai.tool.call.result, gen_ai.tool.name, success
# @weaver.rule.subject: field:success
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: forbidden_presence
deny contains make_advice(
    "tool_result_forbidden_success",
    "violation",
    {"event_name": "tool_result"},
    "Event 'tool_result': Forbidden attribute 'success' found. Use conditional 'error.type' instead."
) if {
    _is_tool_result
    has_log_attr("success")
}
