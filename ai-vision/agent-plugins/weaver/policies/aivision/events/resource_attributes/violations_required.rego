# Event resource attribute required-field rules.
# These checks run when an OTLP log Resource is present.

package live_check_advice

import rego.v1

# @weaver.rule.zh: Event Resource 存在时要求包含 `ant.agent.type`，用于识别上报数据来自哪个 AI Agent。
# @weaver.rule.check.zh: log sample 存在 resource 但缺少 `ant.agent.type` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充 `ant.agent.type`；不要在 log attributes 中重复放置该 Resource 身份字段。
# @weaver.rule.entity: resource:events
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.collection.mode, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, duration_ms, event.name, event.sequence, gen_ai.chat.id, service.name, service.version
# @weaver.rule.subject: field:ant.agent.type
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "event_resource_missing_agent_type",
    "violation",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s' Resource: Missing required 'ant.agent.type'.", [get_log_attr("event.name")])
) if {
    has_log
    has_resource
    not has_resource_attr("ant.agent.type")
}

# @weaver.rule.zh: Event Resource 存在时要求包含 OTel 标准字段 `service.name`。
# @weaver.rule.check.zh: log sample 存在 resource 但缺少 `service.name` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充稳定服务名，例如 opencode plugin、agent runtime 或 collector 名称。
# @weaver.rule.entity: resource:events
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.collection.mode, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, duration_ms, event.name, event.sequence, gen_ai.chat.id, service.name, service.version
# @weaver.rule.subject: field:service.name
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "event_resource_missing_service_name",
    "violation",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s' Resource: Missing required 'service.name'.", [get_log_attr("event.name")])
) if {
    has_log
    has_resource
    not has_resource_attr("service.name")
}

# @weaver.rule.zh: Event Resource 存在时要求包含 `ant.agent.env`，用于区分 dev/staging/prod/default 等环境。
# @weaver.rule.check.zh: log sample 存在 resource 但缺少 `ant.agent.env` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充 `ant.agent.env`；本地测试可使用 `default` 或 `dev`。
# @weaver.rule.entity: resource:events
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.collection.mode, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, duration_ms, event.name, event.sequence, gen_ai.chat.id, service.name, service.version
# @weaver.rule.subject: field:ant.agent.env
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "event_resource_missing_agent_env",
    "violation",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s' Resource: Missing required 'ant.agent.env'.", [get_log_attr("event.name")])
) if {
    has_log
    has_resource
    not has_resource_attr("ant.agent.env")
}

# @weaver.rule.zh: Event Resource 存在时要求包含 `ant.agent.scene`，用于区分业务场景。
# @weaver.rule.check.zh: log sample 存在 resource 但缺少 `ant.agent.scene` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充 `ant.agent.scene`，例如 default、code_review、daily_dev。
# @weaver.rule.entity: resource:events
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.collection.mode, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, duration_ms, event.name, event.sequence, gen_ai.chat.id, service.name, service.version
# @weaver.rule.subject: field:ant.agent.scene
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "event_resource_missing_agent_scene",
    "violation",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s' Resource: Missing required 'ant.agent.scene'.", [get_log_attr("event.name")])
) if {
    has_log
    has_resource
    not has_resource_attr("ant.agent.scene")
}

# @weaver.rule.zh: Event Resource 存在时要求包含 `agentic.collection.mode`，声明 full/privacy 采集模式。
# @weaver.rule.check.zh: log sample 存在 resource 但缺少 `agentic.collection.mode` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充 `agentic.collection.mode`；未实现隐私模式时使用 `full`。
# @weaver.rule.entity: resource:events
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.collection.mode, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, duration_ms, event.name, event.sequence, gen_ai.chat.id, service.name, service.version
# @weaver.rule.subject: field:agentic.collection.mode
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "event_resource_missing_collection_mode",
    "violation",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s' Resource: Missing required 'agentic.collection.mode'.", [get_log_attr("event.name")])
) if {
    has_log
    has_resource
    not has_resource_attr("agentic.collection.mode")
}
