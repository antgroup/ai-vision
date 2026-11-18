# Resource attribute required-field rules for trace/span samples.
# Scope: resource.attributes carrying ant.* / service.* / collection-mode identity.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 要求 resource attributes 必须包含 `ant.agent.type`，用于识别上报数据来自哪个 AI Agent。
# @weaver.rule.check.zh: 当 trace/span 样本存在 resource 但缺少 `ant.agent.type` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充非空的 `ant.agent.type` 字符串；允许使用任意自定义 agent 标识。
# @weaver.rule.entity: resource:traces
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.collection.mode, agentic.tool.duration_ms, agentic.turn.number, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, gen_ai.span.kind, service.name, service.version
# @weaver.rule.subject: field:ant.agent.type
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "resource_missing_agent_type",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Resource (span '%s'): Missing required 'ant.agent.type'. Custom agent identifiers are allowed.", [input.sample.span.name])
) if {
    has_span
    has_resource
    not has_resource_attr("ant.agent.type")
}

# @weaver.rule.zh: 要求 resource attributes 必须包含 OTel 标准字段 `service.name`。
# @weaver.rule.check.zh: 当 trace/span 样本存在 resource 但缺少 `service.name` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充稳定的服务名，例如 agent runtime 或插件服务名。
# @weaver.rule.entity: resource:traces
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.collection.mode, agentic.tool.duration_ms, agentic.turn.number, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, gen_ai.span.kind, service.name, service.version
# @weaver.rule.subject: field:service.name
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "resource_missing_service_name",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Resource (span '%s'): Missing required 'service.name'.", [input.sample.span.name])
) if {
    has_span
    has_resource
    not has_resource_attr("service.name")
}

# @weaver.rule.zh: 要求 resource attributes 必须包含 `ant.agent.env`，用于区分 dev/staging/prod/default 等环境。
# @weaver.rule.check.zh: 当 trace/span 样本存在 resource 但缺少 `ant.agent.env` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充 `ant.agent.env`；本地测试可使用 `default` 或 `dev`。
# @weaver.rule.entity: resource:traces
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.collection.mode, agentic.tool.duration_ms, agentic.turn.number, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, gen_ai.span.kind, service.name, service.version
# @weaver.rule.subject: field:ant.agent.env
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "resource_missing_agent_env",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Resource (span '%s'): Missing required 'ant.agent.env'. Must indicate deployment environment (e.g. prod, staging, dev, default).", [input.sample.span.name])
) if {
    has_span
    has_resource
    not has_resource_attr("ant.agent.env")
}

# @weaver.rule.zh: 要求 resource attributes 必须包含 `ant.agent.scene`，用于区分业务场景。
# @weaver.rule.check.zh: 当 trace/span 样本存在 resource 但缺少 `ant.agent.scene` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充 `ant.agent.scene`，例如 default、code_review、daily_dev。
# @weaver.rule.entity: resource:traces
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.collection.mode, agentic.tool.duration_ms, agentic.turn.number, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, gen_ai.span.kind, service.name, service.version
# @weaver.rule.subject: field:ant.agent.scene
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "resource_missing_agent_scene",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Resource (span '%s'): Missing required 'ant.agent.scene'. Must indicate agent scene (e.g. default, code_review, daily_dev).", [input.sample.span.name])
) if {
    has_span
    has_resource
    not has_resource_attr("ant.agent.scene")
}

# @weaver.rule.zh: 要求 resource attributes 必须包含 `agentic.collection.mode`，声明 full/privacy 采集模式。
# @weaver.rule.check.zh: 仅检查 resource attributes；span attributes 上不会因为缺少该字段而违规。
# @weaver.rule.fix.zh: 在 Resource 上补充 `agentic.collection.mode`，未实现隐私模式时使用 `full`。
# @weaver.rule.entity: resource:traces
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.collection.mode, agentic.tool.duration_ms, agentic.turn.number, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, gen_ai.span.kind, service.name, service.version
# @weaver.rule.subject: field:agentic.collection.mode
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "resource_missing_collection_mode",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Resource (span '%s'): Missing required 'agentic.collection.mode'. Must be either 'full' or 'privacy'; use 'full' when privacy mode is not implemented.", [input.sample.span.name])
) if {
    has_span
    has_resource
    not has_resource_attr("agentic.collection.mode")
}

# @weaver.rule.zh: 要求 cchooks/cdxhooks resource attributes 必须包含 `ant.meta.trigger`，声明外部调用方判定的自动化/交互式触发上下文。
# @weaver.rule.check.zh: 当 trace/span 样本来自 `telemetry.sdk.name` 为 cchooks 或 cdxhooks 且 resource 缺少 `ant.meta.trigger` 时命中。
# @weaver.rule.fix.zh: 在 Resource 上补充 `ant.meta.trigger`；调用方未声明时使用空字符串，显式非法值使用 `unknown`，不要从 transcript 推断自动化。
# @weaver.rule.entity: resource:traces
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: ant.meta.trigger
# @weaver.rule.subject: field:ant.meta.trigger
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "resource_missing_ant_meta_trigger",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Resource (span '%s'): Missing required 'ant.meta.trigger'. Use empty string when unspecified, or automation, interactive, unknown.", [input.sample.span.name])
) if {
    has_span
    has_resource
    get_resource_attr("telemetry.sdk.name") in trigger_required_resource_sdk_names
    not has_resource_attr("ant.meta.trigger")
}
