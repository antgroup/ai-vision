# Resource attribute forbidden/value rules for trace/span samples.
# Scope: resource.attributes carrying ant.* / service.* / collection-mode identity.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 禁止在 resource attributes 中放入只属于 span 的语义字段。
# @weaver.rule.check.zh: 当 Resource 出现 `gen_ai.span.kind`、`gen_ai.operation.name`、`gen_ai.conversation.id`、`ant.username`、`user.id`、`agentic.turn.*`、`agentic.tool.*` 等 span-only 字段时命中。
# @weaver.rule.fix.zh: 将该字段移动到具体 span 的 attributes 上，Resource 只保留进程/服务/agent 身份字段。
# @weaver.rule.entity: resource:traces
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.span.kind, gen_ai.operation.name, gen_ai.conversation.id, ant.username, user.id, agentic.turn.number, agentic.turn.id, agentic.turn.trigger, agentic.tool.id, agentic.tool.name, agentic.tool.type, agentic.tool.duration_ms
# @weaver.rule.subject: field:gen_ai.span.kind
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.subject: field:gen_ai.conversation.id
# @weaver.rule.subject: field:ant.username
# @weaver.rule.subject: field:user.id
# @weaver.rule.subject: field:agentic.turn.number
# @weaver.rule.subject: field:agentic.turn.id
# @weaver.rule.subject: field:agentic.turn.trigger
# @weaver.rule.subject: field:agentic.tool.id
# @weaver.rule.subject: field:agentic.tool.name
# @weaver.rule.subject: field:agentic.tool.type
# @weaver.rule.subject: field:agentic.tool.duration_ms
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.branch: occurrence-1
deny contains make_advice(
    "placement_span_only_in_resource",
    "violation",
    {"attribute": attr.key, "span_name": input.sample.span.name},
    sprintf("Resource (span '%s'): Attribute '%s' is span-only and must NOT appear in resource attributes.", [input.sample.span.name, attr.key])
) if {
    has_span
    has_resource
    some attr in input.sample.resource.attributes
    is_string(attr.key)
    _is_span_only(attr.key)
}

# Weaver name format.
# @weaver.rule.title.zh: resource:traces 禁用字段: placement_span_only_in_resource
# @weaver.rule.zh: 校验 resource:traces 的「禁用字段」规则 `placement_span_only_in_resource`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: resource:traces
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.span.kind, gen_ai.operation.name, gen_ai.conversation.id, ant.username, user.id, agentic.turn.number, agentic.turn.id, agentic.turn.trigger, agentic.tool.id, agentic.tool.name, agentic.tool.type, agentic.tool.duration_ms
# @weaver.rule.subject: field:gen_ai.span.kind
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.subject: field:gen_ai.conversation.id
# @weaver.rule.subject: field:ant.username
# @weaver.rule.subject: field:user.id
# @weaver.rule.subject: field:agentic.turn.number
# @weaver.rule.subject: field:agentic.turn.id
# @weaver.rule.subject: field:agentic.turn.trigger
# @weaver.rule.subject: field:agentic.tool.id
# @weaver.rule.subject: field:agentic.tool.name
# @weaver.rule.subject: field:agentic.tool.type
# @weaver.rule.subject: field:agentic.tool.duration_ms
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.check.zh: Rego 条件命中 `placement_span_only_in_resource` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。
# @weaver.rule.branch: occurrence-2
deny contains make_advice(
    "placement_span_only_in_resource",
    "violation",
    {"attribute": attr.name, "span_name": input.sample.span.name},
    sprintf("Resource (span '%s'): Attribute '%s' is span-only and must NOT appear in resource attributes.", [input.sample.span.name, attr.name])
) if {
    has_span
    has_resource
    some attr in input.sample.resource.attributes
    is_string(attr.name)
    _is_span_only(attr.name)
}

# @weaver.rule.zh: 限制 `agentic.collection.mode` 只能是 `full` 或 `privacy`。
# @weaver.rule.check.zh: 当 Resource 上的 `agentic.collection.mode` 不是允许值时命中。
# @weaver.rule.fix.zh: 未实现隐私模式时填 `full`；实现脱敏/最小化采集时填 `privacy`。
# @weaver.rule.entity: resource:traces
# @weaver.rule.category.zh: 枚举值
# @weaver.rule.fields: agentic.collection.mode, agentic.tool.duration_ms, agentic.turn.number, ant.agent.env, ant.agent.instance, ant.agent.product, ant.agent.scene, ant.agent.type, gen_ai.span.kind, service.name, service.version
# @weaver.rule.subject: field:agentic.collection.mode
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: enum
# @weaver.rule.allowed_values: full, privacy
deny contains make_advice(
    "resource_invalid_collection_mode",
    "violation",
    {"value": collection_mode},
    sprintf("Resource: Invalid agentic.collection.mode '%s'. Valid values: full, privacy. Use 'full' when privacy mode is not implemented.", [collection_mode])
) if {
    has_resource
    collection_mode := get_resource_attr("agentic.collection.mode")
    not collection_mode in valid_collection_modes
}

# @weaver.rule.zh: 限制 Resource 上的 `ant.meta.trigger` 只能是空字符串、`automation`、`interactive` 或 `unknown`。
# @weaver.rule.check.zh: 当 Resource 上的 `ant.meta.trigger` 不是允许值时命中。
# @weaver.rule.fix.zh: 改为空字符串、automation、interactive 或 unknown；调用方未声明时使用空字符串，显式非法值使用 unknown。
# @weaver.rule.entity: resource:traces
# @weaver.rule.category.zh: 枚举值
# @weaver.rule.fields: ant.meta.trigger
# @weaver.rule.subject: field:ant.meta.trigger
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: enum
# @weaver.rule.allowed_values: empty string, automation, interactive, unknown
deny contains make_advice(
    "resource_invalid_ant_meta_trigger",
    "violation",
    {"value": meta_trigger},
    sprintf("Resource: Invalid ant.meta.trigger '%s'. Valid values: empty string, automation, interactive, unknown.", [meta_trigger])
) if {
    has_resource
    meta_trigger := get_resource_attr("ant.meta.trigger")
    not meta_trigger in valid_ant_meta_triggers
}
