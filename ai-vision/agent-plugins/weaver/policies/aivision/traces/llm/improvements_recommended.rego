# LLM span recommended-field rules.
# Scope: gen_ai.span.kind=LLM or legacy generation spans.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 推荐 LLM span 包含实际响应模型 `gen_ai.response.model`。
# @weaver.rule.check.zh: 当 LLM span 缺少 `gen_ai.response.model` 时命中。
# @weaver.rule.fix.zh: 从 API response 或 provider 返回值中提取实际使用模型；如果与请求模型一致也建议写入。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: gen_ai.response.model
# @weaver.rule.subject: field:gen_ai.response.model
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "llm_missing_response_model",
    "improvement",
    {"span_name": input.sample.span.name},
    sprintf("LLM span '%s': Missing recommended 'gen_ai.response.model' (the actual model used, may differ from request.model).", [input.sample.span.name])
) if {
    is_llm_span
    not has_span_attr("gen_ai.response.model")
}

# @weaver.rule.zh: 推荐 LLM span 包含 `gen_ai.response.finish_reasons`。
# @weaver.rule.check.zh: 当 LLM span 缺少 `gen_ai.response.finish_reasons` 时命中。
# @weaver.rule.fix.zh: 从 provider 响应中提取 finish reason，按数组语义写入，例如 stop、tool_calls。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: gen_ai.response.finish_reasons
# @weaver.rule.subject: field:gen_ai.response.finish_reasons
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "llm_missing_finish_reasons",
    "improvement",
    {"span_name": input.sample.span.name},
    sprintf("LLM span '%s': Missing recommended 'gen_ai.response.finish_reasons' (e.g., stop, tool_calls).", [input.sample.span.name])
) if {
    is_llm_span
    not has_span_attr("gen_ai.response.finish_reasons")
}

# @weaver.rule.zh: 推荐 LLM ERROR span 包含 `error.type`。
# @weaver.rule.check.zh: 当 LLM span status.code 为 ERROR 但缺少 `error.type` 时命中。
# @weaver.rule.fix.zh: 在异常路径同时写入 `status.code=ERROR` 与 `error.type`，必要时补充 `error.message`。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: error.type
# @weaver.rule.subject: field:error.type
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: recommended_presence
# @weaver.rule.when.zh: 当 LLM span status.code 为 ERROR 时。
deny contains make_advice(
    "llm_error_missing_error_type",
    "improvement",
    {"span_name": input.sample.span.name},
    sprintf(
        "LLM span '%s': status.code is ERROR but missing recommended 'error.type'. Implementations satisfying C2 should set both together.",
        [input.sample.span.name],
    ),
) if {
    is_llm_span
    _capability_c2_is_error_status
    not has_span_attr("error.type")
}

# @weaver.rule.zh: 推荐 LLM span 名称使用 `chat {model}` 格式。
# @weaver.rule.check.zh: 当 LLM span 名称不是以 `chat ` 开头时命中。
# @weaver.rule.fix.zh: 将 LLM span name 设置为 `chat {gen_ai.request.model}`，例如 `chat gpt-4.1`。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 格式校验
# @weaver.rule.fields: span.name
# @weaver.rule.subject: field:span.name
# @weaver.rule.placement: span_name
# @weaver.rule.constraint.type: format
deny contains make_advice(
    "llm_span_naming_convention",
    "improvement",
    {"span_name": input.sample.span.name, "suggested": "chat {gen_ai.request.model}"},
    sprintf("LLM span '%s': Recommended span name format is 'chat {model}' (e.g., 'chat o3-pro'). See OTel GenAI semconv.", [input.sample.span.name])
) if {
    is_llm_span
    not startswith(input.sample.span.name, "chat ")
}
