# compaction_result forbidden/value rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: compaction_result 是系统压缩工具结果，operation 必须是 execute_tool。
# @weaver.rule.check.zh: `event.name=compaction_result` 且 `gen_ai.operation.name` 不是 `execute_tool` 时命中。
# @weaver.rule.fix.zh: compaction_result 事件写入 `gen_ai.operation.name=execute_tool`。
# @weaver.rule.entity: event:compaction_result
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: agentic.tool.type, agentic.turn.number, duration_ms, event.name, gen_ai.operation.name, gen_ai.tool.name
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: execute_tool
deny contains make_advice(
    "compaction_result_wrong_operation_name",
    "violation",
    {"event_name": "compaction_result"},
    sprintf("Event 'compaction_result': gen_ai.operation.name should be 'execute_tool', got '%s'.", [get_log_attr("gen_ai.operation.name")])
) if {
    _is_compaction_result
    has_log_attr("gen_ai.operation.name")
    get_log_attr("gen_ai.operation.name") != "execute_tool"
}

# @weaver.rule.zh: compaction_result 的工具名必须是 compaction。
# @weaver.rule.check.zh: `event.name=compaction_result` 且 `gen_ai.tool.name` 不是 `compaction` 时命中。
# @weaver.rule.fix.zh: 压缩事件固定写入 `gen_ai.tool.name=compaction`。
# @weaver.rule.entity: event:compaction_result
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: agentic.tool.type, agentic.turn.number, duration_ms, event.name, gen_ai.operation.name, gen_ai.tool.name
# @weaver.rule.subject: field:gen_ai.tool.name
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: compaction
deny contains make_advice(
    "compaction_result_wrong_tool_name",
    "violation",
    {"event_name": "compaction_result", "value": get_log_attr("gen_ai.tool.name")},
    sprintf("Event 'compaction_result': gen_ai.tool.name should be 'compaction', got '%s'.", [get_log_attr("gen_ai.tool.name")])
) if {
    _is_compaction_result
    has_log_attr("gen_ai.tool.name")
    get_log_attr("gen_ai.tool.name") != "compaction"
}

# @weaver.rule.zh: compaction_result 的工具类型必须是 system。
# @weaver.rule.check.zh: `event.name=compaction_result` 且 `agentic.tool.type` 不是 `system` 时命中。
# @weaver.rule.fix.zh: 压缩事件固定写入 `agentic.tool.type=system`。
# @weaver.rule.entity: event:compaction_result
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: agentic.tool.type, agentic.turn.number, duration_ms, event.name, gen_ai.operation.name, gen_ai.tool.name
# @weaver.rule.subject: field:agentic.tool.type
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: system
deny contains make_advice(
    "compaction_result_wrong_tool_type",
    "violation",
    {"event_name": "compaction_result", "value": get_log_attr("agentic.tool.type")},
    sprintf("Event 'compaction_result': agentic.tool.type should be 'system', got '%s'.", [get_log_attr("agentic.tool.type")])
) if {
    _is_compaction_result
    has_log_attr("agentic.tool.type")
    get_log_attr("agentic.tool.type") != "system"
}
