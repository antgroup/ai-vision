# session_end forbidden/value rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: session_end 表示会话/idle 结束，operation 必须是 chat。
# @weaver.rule.check.zh: `event.name=session_end` 且 `gen_ai.operation.name` 不是 `chat` 时命中。
# @weaver.rule.fix.zh: session_end 事件写入 `gen_ai.operation.name=chat`。
# @weaver.rule.entity: event:session_end
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: agentic.turn.number, event.name, gen_ai.operation.name, gen_ai.session.id
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: session_end
deny contains make_advice(
    "session_end_wrong_operation_name",
    "violation",
    {"event_name": "session_end"},
    sprintf("Event 'session_end': gen_ai.operation.name should be 'chat', got '%s'.", [get_log_attr("gen_ai.operation.name")])
) if {
    _is_session_end
    has_log_attr("gen_ai.operation.name")
    get_log_attr("gen_ai.operation.name") != "chat"
}
