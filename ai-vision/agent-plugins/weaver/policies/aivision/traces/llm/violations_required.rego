# LLM span required-field rules.
# Scope: gen_ai.span.kind=LLM or legacy generation spans.

package live_check_advice

import rego.v1

# Each source direction is independent. Only the exact registered string
# "true" permits omitting that direction; boolean/truthy values do not.
_llm_usage_unreported(direction) if {
    direction in {"input", "output"}
    get_span_attr(sprintf("agentic.usage.%s_tokens.missing", [direction])) == "true"
}

# @weaver.rule.zh: 要求 LLM span 的 `gen_ai.span.kind` 值固定为 `LLM`。
# @weaver.rule.check.zh: 当 LLM span 上 `gen_ai.span.kind` 存在但不是 `LLM` 时命中。
# @weaver.rule.fix.zh: 将 LLM span 的 `gen_ai.span.kind` 改为 `LLM`；CHAT/TOOL 应使用对应 span 类型。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: gen_ai.span.kind
# @weaver.rule.subject: field:gen_ai.span.kind
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: LLM
deny contains make_advice(
    "llm_invalid_span_kind",
    "violation",
    {"span_name": input.sample.span.name, "actual_value": get_span_attr("gen_ai.span.kind"), "expected_value": "LLM"},
    sprintf("LLM span '%s': gen_ai.span.kind must be 'LLM'.", [input.sample.span.name])
) if {
    is_llm_span
    has_span_attr("gen_ai.span.kind")
    get_span_attr("gen_ai.span.kind") != "LLM"
}

# @weaver.rule.zh: 要求 LLM span 的 `gen_ai.operation.name` 值固定为 `chat`。
# @weaver.rule.check.zh: 当 LLM span 上 `gen_ai.operation.name` 存在但不是 `chat` 时命中。
# @weaver.rule.fix.zh: 将模型调用 span 的 `gen_ai.operation.name` 改为 `chat`。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: gen_ai.operation.name
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: chat
deny contains make_advice(
    "llm_invalid_operation_name",
    "violation",
    {"span_name": input.sample.span.name, "actual_value": get_span_attr("gen_ai.operation.name"), "expected_value": "chat"},
    sprintf("LLM span '%s': gen_ai.operation.name must be 'chat'.", [input.sample.span.name])
) if {
    is_llm_span
    has_span_attr("gen_ai.operation.name")
    get_span_attr("gen_ai.operation.name") != "chat"
}

# @weaver.rule.zh: 要求 LLM span 包含请求模型 `gen_ai.request.model`。
# @weaver.rule.check.zh: 当 LLM span 缺少 `gen_ai.request.model` 时命中。
# @weaver.rule.fix.zh: 从实际 API 请求中提取模型名并写入 `gen_ai.request.model`。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: gen_ai.request.model
# @weaver.rule.subject: field:gen_ai.request.model
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "llm_missing_request_model",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("LLM span '%s': Missing required 'gen_ai.request.model'.", [input.sample.span.name])
) if {
    is_llm_span
    not has_span_attr("gen_ai.request.model")
}

# @weaver.rule.zh: 当 `gen_ai.request.model` 使用 `provider/model` 格式时，要求显式补充 `gen_ai.provider.name`。
# @weaver.rule.check.zh: 当 LLM span 的 request model 包含 `/` 但缺少 `gen_ai.provider.name` 时命中。
# @weaver.rule.fix.zh: 将 `/` 前的 provider 前缀解析到 `gen_ai.provider.name`，model 字段保留原始请求模型。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: gen_ai.provider.name, gen_ai.request.model
# @weaver.rule.subject: field:gen_ai.provider.name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
# @weaver.rule.when.zh: 当 `gen_ai.request.model` 使用 `provider/model` 格式时。
deny contains make_advice(
    "llm_missing_provider_name_slashed",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("LLM span '%s': Missing required 'gen_ai.provider.name'. When gen_ai.request.model contains '/' (e.g. 'antchat/GLM-5.1'), the provider prefix must be extracted and set.", [input.sample.span.name])
) if {
    is_llm_span
    model := get_span_attr("gen_ai.request.model")
    contains(model, "/")
    not has_span_attr("gen_ai.provider.name")
}

# @weaver.rule.zh: 要求 LLM span 包含源数据提供的输入 token 数；明确未知时使用对应字符串缺失标记。
# @weaver.rule.check.zh: 缺少 `gen_ai.usage.input_tokens` 且 `agentic.usage.input_tokens.missing` 不精确等于字符串 `true` 时命中。
# @weaver.rule.fix.zh: 从实际 API usage 写入输入 token（已知 0 也保留）；源数据无法提供时省略数值并将对应 missing 标记写为字符串 `true`，不要伪造 0。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: gen_ai.usage.input_tokens
# @weaver.rule.subject: field:gen_ai.usage.input_tokens
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "llm_missing_input_tokens",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("LLM span '%s': Missing required 'gen_ai.usage.input_tokens'.", [input.sample.span.name])
) if {
    is_llm_span
    not has_span_attr("gen_ai.usage.input_tokens")
    not _llm_usage_unreported("input")
}

# @weaver.rule.zh: 要求 LLM span 包含源数据提供的输出 token 数；明确未知时使用对应字符串缺失标记。
# @weaver.rule.check.zh: 缺少 `gen_ai.usage.output_tokens` 且 `agentic.usage.output_tokens.missing` 不精确等于字符串 `true` 时命中。
# @weaver.rule.fix.zh: 从实际 API usage 写入输出 token（已知 0 也保留）；源数据无法提供时省略数值并将对应 missing 标记写为字符串 `true`，输入 missing 不豁免输出字段。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: gen_ai.usage.output_tokens
# @weaver.rule.subject: field:gen_ai.usage.output_tokens
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "llm_missing_output_tokens",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("LLM span '%s': Missing required 'gen_ai.usage.output_tokens'.", [input.sample.span.name])
) if {
    is_llm_span
    not has_span_attr("gen_ai.usage.output_tokens")
    not _llm_usage_unreported("output")
}

# @weaver.rule.zh: 要求 LLM span 包含 `agentic.llm.duration_ms`，用于延迟分析。
# @weaver.rule.check.zh: 当 LLM span 缺少 `agentic.llm.duration_ms` 时命中。
# @weaver.rule.fix.zh: 用 LLM 请求开始/结束时间计算毫秒耗时并写入该字段。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.llm.duration_ms
# @weaver.rule.subject: field:agentic.llm.duration_ms
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "llm_missing_duration",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("LLM span '%s': Missing required 'agentic.llm.duration_ms'.", [input.sample.span.name])
) if {
    is_llm_span
    not has_span_attr("agentic.llm.duration_ms")
}
