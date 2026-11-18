# session_start forbidden/value rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: session_start 表示会话开始，operation 必须是 chat。
# @weaver.rule.check.zh: `event.name=session_start` 且 `gen_ai.operation.name` 不是 `chat` 时命中。
# @weaver.rule.fix.zh: session_start 事件写入 `gen_ai.operation.name=chat`。
# @weaver.rule.entity: event:session_start
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: event.name, gen_ai.operation.name, gen_ai.session.id
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: session_start
deny contains make_advice(
    "session_start_wrong_operation_name",
    "violation",
    {"event_name": "session_start"},
    sprintf("Event 'session_start': gen_ai.operation.name should be 'chat', got '%s'.", [get_log_attr("gen_ai.operation.name")])
) if {
    _is_session_start
    has_log_attr("gen_ai.operation.name")
    get_log_attr("gen_ai.operation.name") != "chat"
}
