# Event resource attribute recommended-field rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: Event Resource 推荐补充 `ant.agent.instance`，用于区分本机、容器或 agent 实例。
# @weaver.rule.check.zh: log sample 存在 resource 但缺少 `ant.agent.instance` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充稳定实例标识；本地可使用 hostname、instance id 或匿名实例 id。
# @weaver.rule.entity: resource:events
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: agentic.collection.mode, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, duration_ms, event.name, event.sequence, gen_ai.chat.id, service.name, service.version
# @weaver.rule.subject: field:ant.agent.instance
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "event_resource_missing_agent_instance",
    "improvement",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s' Resource: Missing recommended 'ant.agent.instance'.", [get_log_attr("event.name")])
) if {
    has_log
    has_resource
    not has_resource_attr("ant.agent.instance")
}

# @weaver.rule.zh: Event Resource 推荐补充 `ant.agent.product`，用于区分产品/发行版。
# @weaver.rule.check.zh: log sample 存在 resource 但缺少 `ant.agent.product` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充产品名，例如 AiVision、OpenCode 或插件名。
# @weaver.rule.entity: resource:events
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: agentic.collection.mode, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, duration_ms, event.name, event.sequence, gen_ai.chat.id, service.name, service.version
# @weaver.rule.subject: field:ant.agent.product
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "event_resource_missing_agent_product",
    "improvement",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s' Resource: Missing recommended 'ant.agent.product'.", [get_log_attr("event.name")])
) if {
    has_log
    has_resource
    not has_resource_attr("ant.agent.product")
}

# @weaver.rule.zh: Event Resource 推荐补充 `service.version`，用于定位不同版本遥测质量差异。
# @weaver.rule.check.zh: log sample 存在 resource 但缺少 `service.version` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充 hook/plugin/agent 的版本号或构建版本。
# @weaver.rule.entity: resource:events
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: agentic.collection.mode, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, duration_ms, event.name, event.sequence, gen_ai.chat.id, service.name, service.version
# @weaver.rule.subject: field:service.version
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "event_resource_missing_service_version",
    "improvement",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s' Resource: Missing recommended 'service.version'.", [get_log_attr("event.name")])
) if {
    has_log
    has_resource
    not has_resource_attr("service.version")
}
