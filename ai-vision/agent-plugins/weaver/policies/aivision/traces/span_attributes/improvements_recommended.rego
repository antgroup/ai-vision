# Global span attribute recommended/value advisory rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 推荐 `gen_ai.provider.name` 使用已知 LLM provider 枚举，便于聚合分析。
# @weaver.rule.check.zh: 当 span 上的 `gen_ai.provider.name` 不在已知集合内时命中。
# @weaver.rule.fix.zh: 使用 anthropic、openai、qwen、alibaba、azure.ai.openai、gcp.gemini、aws.bedrock、deepseek、gemini、moonshot 等规范值；新增 provider 需同步扩展语义规范。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 枚举值
# @weaver.rule.fields: gen_ai.provider.name
# @weaver.rule.subject: field:gen_ai.provider.name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: enum
# @weaver.rule.allowed_values: anthropic, openai, qwen, alibaba, azure.ai.openai, gcp.gemini, aws.bedrock, deepseek, theta, Theta, gemini, moonshot
deny contains make_advice(
    "unknown_gen_ai_provider_name",
    "improvement",
    {"span_name": input.sample.span.name, "value": provider},
    sprintf("Span '%s': Unknown gen_ai.provider.name '%s'. Known: anthropic, openai, qwen, alibaba, aws.bedrock, deepseek, theta, Theta.", [input.sample.span.name, provider])
) if {
    has_span
    provider := get_span_attr("gen_ai.provider.name")
    not provider in valid_providers
}
