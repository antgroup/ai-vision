# tool_call required-field and input-state rules.

package live_check_advice

import rego.v1

_tool_call_required_attrs := {
    "gen_ai.operation.name",
    "agentic.turn.number",
    "gen_ai.tool.name",
    "gen_ai.tool.call.id",
    "agentic.tool.type",
    "agentic.tool.name",
    "agentic.tool.id",
}

_tool_call_arguments_present if { has_log_attr("gen_ai.tool.call.arguments") }
_tool_call_missing_present if { has_log_attr("agentic.tool.input.missing") }
_tool_call_missing_valid if { get_log_attr("agentic.tool.input.missing") == "true" }
_tool_call_truncated_present if { has_log_attr("agentic.tool.input.truncated") }
_tool_call_truncated_valid if { get_log_attr("agentic.tool.input.truncated") == "true" }

# @weaver.rule.zh: tool_call 必须携带执行开始所需的身份、轮次与操作字段。
# @weaver.rule.check.zh: event.name=tool_call 且缺少任一必需字段时命中。
# @weaver.rule.fix.zh: 从有效 execution-start hook 与当前 Turn 补齐缺失字段。
# @weaver.rule.entity: event:tool_call
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: gen_ai.operation.name, agentic.turn.number, gen_ai.tool.name, gen_ai.tool.call.id, agentic.tool.type, agentic.tool.name, agentic.tool.id
# @weaver.rule.subject: field:gen_ai.tool.call.id
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "tool_call_missing_required_attribute",
    "violation",
    {"event_name": "tool_call", "attribute": attr},
    sprintf("Event 'tool_call': Missing required attribute '%s'.", [attr])
) if {
    _is_tool_call
    some attr in _tool_call_required_attrs
    not has_log_attr(attr)
}

# @weaver.rule.zh: tool_call 必须表达且仅表达一个合法输入状态。
# @weaver.rule.check.zh: arguments、missing、truncated 三个字段全部缺失时命中。
# @weaver.rule.fix.zh: 有 args 时写 arguments；无 args 时写字符串 missing=true。
# @weaver.rule.entity: event:tool_call
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: gen_ai.tool.call.arguments, agentic.tool.input.missing, agentic.tool.input.truncated
# @weaver.rule.subject: field:gen_ai.tool.call.arguments
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "tool_call_missing_input_state",
    "violation",
    {"event_name": "tool_call", "attribute": "gen_ai.tool.call.arguments"},
    "Event 'tool_call': Missing input state; provide arguments or string marker agentic.tool.input.missing=true."
) if {
    _is_tool_call
    not _tool_call_arguments_present
    not _tool_call_missing_present
    not _tool_call_truncated_present
}

# @weaver.rule.zh: arguments 与 missing=true 互斥。
# @weaver.rule.check.zh: arguments 存在且 missing 是合法字符串 true 时命中。
# @weaver.rule.fix.zh: 删除 missing；只有源 args 缺失时才写 missing=true。
# @weaver.rule.entity: event:tool_call
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: gen_ai.tool.call.arguments, agentic.tool.input.missing
# @weaver.rule.subject: field:agentic.tool.input.missing
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
deny contains make_advice(
    "tool_call_conflicting_input_state",
    "violation",
    {"event_name": "tool_call", "attribute": "agentic.tool.input.missing"},
    "Event 'tool_call': arguments conflicts with agentic.tool.input.missing=true."
) if {
    _is_tool_call
    _tool_call_arguments_present
    _tool_call_missing_valid
}

# @weaver.rule.zh: missing marker 只能是字符串 true。
# @weaver.rule.check.zh: missing marker 存在但值或类型不是字符串 true 时命中。
# @weaver.rule.fix.zh: 将 marker 写成字符串 true，不能使用 boolean true 或字符串 false。
# @weaver.rule.entity: event:tool_call
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: agentic.tool.input.missing
# @weaver.rule.subject: field:agentic.tool.input.missing
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: true
deny contains make_advice(
    "tool_call_invalid_missing_marker",
    "violation",
    {"event_name": "tool_call", "attribute": "agentic.tool.input.missing"},
    "Event 'tool_call': agentic.tool.input.missing must be the string 'true'."
) if {
    _is_tool_call
    _tool_call_missing_present
    not _tool_call_missing_valid
}

# @weaver.rule.zh: truncated marker 只能是字符串 true。
# @weaver.rule.check.zh: truncated marker 存在但值或类型不是字符串 true 时命中。
# @weaver.rule.fix.zh: 将 marker 写成字符串 true，不能使用 boolean true 或字符串 false。
# @weaver.rule.entity: event:tool_call
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: agentic.tool.input.truncated
# @weaver.rule.subject: field:agentic.tool.input.truncated
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: true
deny contains make_advice(
    "tool_call_invalid_truncated_marker",
    "violation",
    {"event_name": "tool_call", "attribute": "agentic.tool.input.truncated"},
    "Event 'tool_call': agentic.tool.input.truncated must be the string 'true'."
) if {
    _is_tool_call
    _tool_call_truncated_present
    not _tool_call_truncated_valid
}

# @weaver.rule.zh: truncated=true 必须同时存在 arguments。
# @weaver.rule.check.zh: truncated 是合法字符串 true 但 arguments 缺失时命中。
# @weaver.rule.fix.zh: 补齐截断后的 arguments，或删除无证据的 truncated marker。
# @weaver.rule.entity: event:tool_call
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: gen_ai.tool.call.arguments, agentic.tool.input.truncated
# @weaver.rule.subject: field:agentic.tool.input.truncated
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
deny contains make_advice(
    "tool_call_truncated_without_arguments",
    "violation",
    {"event_name": "tool_call", "attribute": "agentic.tool.input.truncated"},
    "Event 'tool_call': truncated=true requires gen_ai.tool.call.arguments."
) if {
    _is_tool_call
    not _tool_call_arguments_present
    _tool_call_truncated_valid
}
