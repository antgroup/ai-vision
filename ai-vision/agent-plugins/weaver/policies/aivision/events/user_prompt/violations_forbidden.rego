# user_prompt forbidden/value rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: user_prompt 表示用户输入事件，operation 必须是 chat。
# @weaver.rule.check.zh: `event.name=user_prompt` 且 `gen_ai.operation.name` 不是 `chat` 时命中。
# @weaver.rule.fix.zh: user_prompt 事件写入 `gen_ai.operation.name=chat`。
# @weaver.rule.entity: event:user_prompt
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: agentic.turn.number, event.name, gen_ai.chat.id, gen_ai.input.messages, gen_ai.operation.name
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: user_prompt
deny contains make_advice(
    "user_prompt_wrong_operation_name",
    "violation",
    {"event_name": "user_prompt"},
    sprintf("Event 'user_prompt': gen_ai.operation.name should be 'chat', got '%s'.", [get_log_attr("gen_ai.operation.name")])
) if {
    _is_user_prompt
    has_log_attr("gen_ai.operation.name")
    get_log_attr("gen_ai.operation.name") != "chat"
}
