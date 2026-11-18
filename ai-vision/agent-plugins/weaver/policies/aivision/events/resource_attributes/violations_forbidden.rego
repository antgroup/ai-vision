# Event resource attribute forbidden/value rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: Event Resource 上的 `agentic.collection.mode` 只能是 `full` 或 `privacy`。
# @weaver.rule.check.zh: log Resource 上的 `agentic.collection.mode` 不是允许值时命中。
# @weaver.rule.fix.zh: 未实现隐私模式时填 `full`；实现脱敏/最小化采集时填 `privacy`。
# @weaver.rule.entity: resource:events
# @weaver.rule.category.zh: 枚举值
# @weaver.rule.fields: agentic.collection.mode, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, duration_ms, event.name, event.sequence, gen_ai.chat.id, service.name, service.version
# @weaver.rule.subject: field:agentic.collection.mode
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: enum
# @weaver.rule.allowed_values: full, privacy
deny contains make_advice(
    "event_resource_invalid_collection_mode",
    "violation",
    {"event_name": get_log_attr("event.name"), "value": collection_mode},
    sprintf("Event '%s' Resource: Invalid agentic.collection.mode '%s'.", [get_log_attr("event.name"), collection_mode])
) if {
    has_log
    has_resource
    collection_mode := get_resource_attr("agentic.collection.mode")
    not collection_mode in valid_collection_modes
}

# @weaver.rule.zh: 禁止把事件专属字段放到 log Resource attributes 中。
# @weaver.rule.check.zh: Resource 上出现 `event.name`、`event.sequence`、`duration_ms`、`gen_ai.chat.id`、tool call payload 等 event-only 字段时命中。
# @weaver.rule.fix.zh: 将事件专属字段移动到 LogRecord attributes；Resource 只保留服务和 agent 身份字段。
# @weaver.rule.entity: resource:events
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: agentic.collection.mode, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, duration_ms, event.name, event.sequence, gen_ai.chat.id, service.name, service.version
# @weaver.rule.subject: field:event.name
# @weaver.rule.subject: field:event.sequence
# @weaver.rule.subject: field:event.timestamp
# @weaver.rule.subject: field:duration_ms
# @weaver.rule.subject: field:prompt.id
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.subject: field:gen_ai.conversation.id
# @weaver.rule.subject: field:gen_ai.chat.id
# @weaver.rule.subject: field:agentic.turn.number
# @weaver.rule.subject: field:agentic.tool.id
# @weaver.rule.subject: field:agentic.tool.name
# @weaver.rule.subject: field:agentic.tool.type
# @weaver.rule.subject: field:gen_ai.tool.name
# @weaver.rule.subject: field:gen_ai.tool.call.id
# @weaver.rule.subject: field:gen_ai.tool.call.arguments
# @weaver.rule.subject: field:gen_ai.tool.call.result
# @weaver.rule.subject: field:error.type
# @weaver.rule.subject: field:error.message
# @weaver.rule.subject: field:success
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.branch: occurrence-1
deny contains make_advice(
    "event_placement_event_only_in_resource",
    "violation",
    {"attribute": attr.key, "event_name": get_log_attr("event.name")},
    sprintf("Event '%s' Resource: Attribute '%s' is event-only and must NOT appear in resource attributes.", [get_log_attr("event.name"), attr.key])
) if {
    has_log
    has_resource
    some attr in input.sample.resource.attributes
    is_string(attr.key)
    _is_event_only(attr.key)
}
# @weaver.rule.title.zh: resource:events 禁用字段: event_placement_event_only_in_resource
# @weaver.rule.zh: 校验 resource:events 的「禁用字段」规则 `event_placement_event_only_in_resource`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: resource:events
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: agentic.collection.mode, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, duration_ms, event.name, event.sequence, gen_ai.chat.id, service.name, service.version
# @weaver.rule.subject: field:event.name
# @weaver.rule.subject: field:event.sequence
# @weaver.rule.subject: field:event.timestamp
# @weaver.rule.subject: field:duration_ms
# @weaver.rule.subject: field:prompt.id
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.subject: field:gen_ai.conversation.id
# @weaver.rule.subject: field:gen_ai.chat.id
# @weaver.rule.subject: field:agentic.turn.number
# @weaver.rule.subject: field:agentic.tool.id
# @weaver.rule.subject: field:agentic.tool.name
# @weaver.rule.subject: field:agentic.tool.type
# @weaver.rule.subject: field:gen_ai.tool.name
# @weaver.rule.subject: field:gen_ai.tool.call.id
# @weaver.rule.subject: field:gen_ai.tool.call.arguments
# @weaver.rule.subject: field:gen_ai.tool.call.result
# @weaver.rule.subject: field:error.type
# @weaver.rule.subject: field:error.message
# @weaver.rule.subject: field:success
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.check.zh: Rego 条件命中 `event_placement_event_only_in_resource` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。
# @weaver.rule.branch: occurrence-2

deny contains make_advice(
    "event_placement_event_only_in_resource",
    "violation",
    {"attribute": attr.name, "event_name": get_log_attr("event.name")},
    sprintf("Event '%s' Resource: Attribute '%s' is event-only and must NOT appear in resource attributes.", [get_log_attr("event.name"), attr.name])
) if {
    has_log
    has_resource
    some attr in input.sample.resource.attributes
    is_string(attr.name)
    _is_event_only(attr.name)
}
