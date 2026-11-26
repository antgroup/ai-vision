# Global span attribute forbidden/value rules.
# Scope: span.attributes shared by all AiVision trace/span types.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 禁止在 span attributes 中放入只属于 Resource 的身份字段。
# @weaver.rule.check.zh: 当 span 上出现 `ant.agent.type`、`ant.agent.product`、`ant.agent.instance`、`ant.agent.env`、`ant.agent.scene`、`service.*` 等 resource-only 字段时命中。
# @weaver.rule.fix.zh: 将这些字段移动到 Resource attributes；span 上只保留该调用/步骤自己的语义字段。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: ant.agent.type, ant.agent.product, ant.agent.instance, ant.agent.env, ant.agent.scene, service.name, service.version, service.instance.id
# @weaver.rule.subject: field:ant.agent.type
# @weaver.rule.subject: field:ant.agent.product
# @weaver.rule.subject: field:ant.agent.instance
# @weaver.rule.subject: field:ant.agent.env
# @weaver.rule.subject: field:ant.agent.scene
# @weaver.rule.subject: field:service.name
# @weaver.rule.subject: field:service.version
# @weaver.rule.subject: field:service.instance.id
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.branch: occurrence-1
deny contains make_advice(
    "placement_resource_only_in_span",
    "violation",
    {"attribute": attr.key, "span_name": input.sample.span.name},
    sprintf("Span '%s': Attribute '%s' is resource-only (entity.ant.agent) and must NOT appear in span attributes.", [input.sample.span.name, attr.key])
) if {
    has_span
    some attr in input.sample.span.attributes
    is_string(attr.key)
    _is_resource_only(attr.key)
}

# Weaver name format.
# @weaver.rule.title.zh: span:common 禁用字段: placement_resource_only_in_span
# @weaver.rule.zh: 校验 span:common 的「禁用字段」规则 `placement_resource_only_in_span`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: ant.agent.type, ant.agent.product, ant.agent.instance, ant.agent.env, ant.agent.scene, service.name, service.version, service.instance.id
# @weaver.rule.subject: field:ant.agent.type
# @weaver.rule.subject: field:ant.agent.product
# @weaver.rule.subject: field:ant.agent.instance
# @weaver.rule.subject: field:ant.agent.env
# @weaver.rule.subject: field:ant.agent.scene
# @weaver.rule.subject: field:service.name
# @weaver.rule.subject: field:service.version
# @weaver.rule.subject: field:service.instance.id
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.check.zh: Rego 条件命中 `placement_resource_only_in_span` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。
# @weaver.rule.branch: occurrence-2
deny contains make_advice(
    "placement_resource_only_in_span",
    "violation",
    {"attribute": attr.name, "span_name": input.sample.span.name},
    sprintf("Span '%s': Attribute '%s' is resource-only (entity.ant.agent) and must NOT appear in span attributes.", [input.sample.span.name, attr.name])
) if {
    has_span
    some attr in input.sample.span.attributes
    is_string(attr.name)
    _is_resource_only(attr.name)
}

# @weaver.rule.zh: 限制 `agentic.runtime.name` 必须使用 AiVision 支持的 runtime 枚举。
# @weaver.rule.check.zh: 当 span 上的 `agentic.runtime.name` 不在允许集合内时命中。
# @weaver.rule.fix.zh: 改为 claude-code、codex、cursor、aider、openclaw、opencode、gemini-cli、hermes、kimi-cli、codefuse、antigravity-cli、pi、dsh 或 minimax-code；新增 runtime 需先扩展语义规范和规则枚举。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 枚举值
# @weaver.rule.fields: agentic.runtime.name
# @weaver.rule.subject: field:agentic.runtime.name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: enum
# @weaver.rule.allowed_values: claude-code, codex, cursor, aider, openclaw, opencode, gemini-cli, hermes, kimi-cli, codefuse, antigravity-cli, pi, dsh, minimax-code
deny contains make_advice(
    "invalid_runtime_name",
    "violation",
    {"span_name": input.sample.span.name, "value": runtime_name},
    sprintf("Span '%s': Invalid runtime_name '%s'. Valid: claude-code, codex, cursor, aider, openclaw, opencode, gemini-cli, hermes, kimi-cli, codefuse, antigravity-cli, pi, dsh, minimax-code.", [input.sample.span.name, runtime_name])
) if {
    has_span
    runtime_name := get_span_attr("agentic.runtime.name")
    not runtime_name in valid_runtime_names
}

# @weaver.rule.zh: 禁止通用 token 计数字段为负数。
# @weaver.rule.check.zh: 当任意 span 的 `gen_ai.usage.input_tokens` 小于 0 时命中。
# @weaver.rule.fix.zh: 修复采集侧 token 提取逻辑；无法获得 token 时不要写负数，应缺省或使用 0。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: gen_ai.usage.input_tokens
# @weaver.rule.subject: field:gen_ai.usage.input_tokens
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: invariant
deny contains make_advice(
    "negative_input_tokens",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Span '%s': gen_ai.usage.input_tokens must not be negative.", [input.sample.span.name])
) if {
    has_span
    tokens := get_span_attr_int("gen_ai.usage.input_tokens")
    tokens < 0
}

# @weaver.rule.zh: 禁止通用 token 计数字段为负数。
# @weaver.rule.check.zh: 当任意 span 的 `gen_ai.usage.output_tokens` 小于 0 时命中。
# @weaver.rule.fix.zh: 修复采集侧 token 提取逻辑；无法获得 token 时不要写负数，应缺省或使用 0。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: gen_ai.usage.output_tokens
# @weaver.rule.subject: field:gen_ai.usage.output_tokens
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: invariant
deny contains make_advice(
    "negative_output_tokens",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Span '%s': gen_ai.usage.output_tokens must not be negative.", [input.sample.span.name])
) if {
    has_span
    tokens := get_span_attr_int("gen_ai.usage.output_tokens")
    tokens < 0
}
