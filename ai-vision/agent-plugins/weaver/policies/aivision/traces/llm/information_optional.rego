# LLM span optional diagnostic rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 可选补充 `gen_ai.provider.name`，用于识别实际 LLM provider。
# @weaver.rule.check.zh: 当 request model 不含 provider 前缀且 LLM span 缺少 `gen_ai.provider.name` 时提示信息。
# @weaver.rule.fix.zh: 如果 provider 可知，写入规范 provider 名称；provider/model 格式时该字段由 violation 规则强制要求。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 可选信息
# @weaver.rule.fields: gen_ai.provider.name
# @weaver.rule.subject: field:gen_ai.provider.name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: optional_info
deny contains make_advice(
    "llm_missing_provider_name",
    "information",
    {"span_name": input.sample.span.name},
    sprintf("LLM span '%s': Missing optional 'gen_ai.provider.name'. Set when the actual LLM provider is known.", [input.sample.span.name])
) if {
    is_llm_span
    not has_span_attr("gen_ai.provider.name")
    model := get_span_attr("gen_ai.request.model")
    not contains(model, "/")
}

# @weaver.rule.zh: 可选补充 `gen_ai.response.id`，用于关联 provider 响应对象。
# @weaver.rule.check.zh: 当 LLM span 缺少 `gen_ai.response.id` 时提示信息。
# @weaver.rule.fix.zh: 如果 API response 中有 request/response id，将其写入该字段。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 可选信息
# @weaver.rule.fields: gen_ai.response.id
# @weaver.rule.subject: field:gen_ai.response.id
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: optional_info
deny contains make_advice(
    "llm_missing_response_id",
    "information",
    {"span_name": input.sample.span.name},
    sprintf("LLM span '%s': Missing opt-in 'gen_ai.response.id' (API response identifier).", [input.sample.span.name])
) if {
    is_llm_span
    not has_span_attr("gen_ai.response.id")
}
