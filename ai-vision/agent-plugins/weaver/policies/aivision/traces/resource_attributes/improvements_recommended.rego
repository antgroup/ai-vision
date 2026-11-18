# Resource attribute recommended-field rules for trace/span samples.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 推荐 Resource 补充 `ant.agent.instance`，用于区分本机、容器或 agent 实例。
# @weaver.rule.check.zh: 当 trace/span 样本存在 resource 但缺少 `ant.agent.instance` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充稳定实例标识；本地可使用 hostname、instance id 或匿名实例 id。
# @weaver.rule.entity: resource:traces
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: agentic.collection.mode, agentic.tool.duration_ms, agentic.turn.number, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, gen_ai.span.kind, service.name, service.version
# @weaver.rule.subject: field:ant.agent.instance
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "resource_missing_agent_instance",
    "improvement",
    {"span_name": input.sample.span.name},
    sprintf("Resource (span '%s'): Missing recommended 'ant.agent.instance' (machine/instance identifier).", [input.sample.span.name])
) if {
    has_span
    has_resource
    not has_resource_attr("ant.agent.instance")
}

# @weaver.rule.zh: 推荐 Resource 补充 `ant.agent.product`，用于区分产品/发行版。
# @weaver.rule.check.zh: 当 trace/span 样本存在 resource 但缺少 `ant.agent.product` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充产品名，例如 AiVision、内部 agent 产品名或插件名。
# @weaver.rule.entity: resource:traces
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: agentic.collection.mode, agentic.tool.duration_ms, agentic.turn.number, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, gen_ai.span.kind, service.name, service.version
# @weaver.rule.subject: field:ant.agent.product
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "resource_missing_agent_product",
    "improvement",
    {"span_name": input.sample.span.name},
    sprintf("Resource (span '%s'): Missing recommended 'ant.agent.product' (product name).", [input.sample.span.name])
) if {
    has_span
    has_resource
    not has_resource_attr("ant.agent.product")
}

# @weaver.rule.zh: 推荐 Resource 补充 `service.version`，用于定位不同版本遥测质量差异。
# @weaver.rule.check.zh: 当 trace/span 样本存在 resource 但缺少 `service.version` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充 hook/plugin/agent 的版本号或构建版本。
# @weaver.rule.entity: resource:traces
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: agentic.collection.mode, agentic.tool.duration_ms, agentic.turn.number, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, gen_ai.span.kind, service.name, service.version
# @weaver.rule.subject: field:service.version
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "resource_missing_service_version",
    "improvement",
    {"span_name": input.sample.span.name},
    sprintf("Resource (span '%s'): Missing recommended 'service.version'.", [input.sample.span.name])
) if {
    has_span
    has_resource
    not has_resource_attr("service.version")
}
