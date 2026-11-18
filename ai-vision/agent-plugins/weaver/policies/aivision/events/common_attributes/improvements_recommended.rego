# Event common recommended-field rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: Events 推荐携带 `event.sequence`，用于在同一 trace/span 内稳定排序事件。
# @weaver.rule.check.zh: 语义 event 已声明 `event.name` 但缺少 `event.sequence` 时命中。
# @weaver.rule.fix.zh: 从事件生成器维护单调递增序号，并写入 OTLP log attributes 的 `event.sequence`；无法保证顺序时可先缺失为 improvement。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: ant.agent.product, ant.agent.type, ant.username, api_request, compaction_result, event.name, event.sequence, gen_ai.conversation.id, gen_ai.operation.name, session_end, session_start, spanId, tool_result, traceId, user_prompt
# @weaver.rule.subject: field:event.sequence
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "event_missing_sequence",
    "improvement",
    {"event_name": get_log_attr("event.name"), "attribute": "event.sequence"},
    sprintf("Event '%s': Missing recommended 'event.sequence' for stable event ordering.", [get_log_attr("event.name")])
) if {
    has_log
    has_log_attr("event.name")
    not has_log_attr("event.sequence")
}
