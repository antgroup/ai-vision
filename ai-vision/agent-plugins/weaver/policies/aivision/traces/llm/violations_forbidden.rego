# LLM span forbidden/value rules.
# Scope: gen_ai.span.kind=LLM or legacy generation spans.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 禁止 LLM 耗时为负数。
# @weaver.rule.check.zh: 当 `agentic.llm.duration_ms` 小于 0 时命中。
# @weaver.rule.fix.zh: 修复开始/结束时间采集或单位换算，确保毫秒耗时非负。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: agentic.llm.duration_ms
# @weaver.rule.subject: field:agentic.llm.duration_ms
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: invariant
deny contains make_advice(
    "llm_negative_duration",
    "violation",
    {"span_name": input.sample.span.name, "duration_ms": duration},
    sprintf(
        "LLM span '%s': agentic.llm.duration_ms = %v is negative.",
        [input.sample.span.name, duration],
    ),
) if {
    is_llm_span
    has_span_attr("agentic.llm.duration_ms")
    duration := get_span_attr_int("agentic.llm.duration_ms")
    duration < 0
}

# @weaver.rule.zh: 禁止 LLM 输入 token 数为负数。
# @weaver.rule.check.zh: 当 LLM span 的 `gen_ai.usage.input_tokens` 小于 0 时命中。
# @weaver.rule.fix.zh: 修复 usage 提取逻辑；无法获得 token 时不要写负数。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: gen_ai.usage.input_tokens
# @weaver.rule.subject: field:gen_ai.usage.input_tokens
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: invariant
deny contains make_advice(
    "llm_negative_input_tokens",
    "violation",
    {"span_name": input.sample.span.name, "value": v},
    sprintf(
        "LLM span '%s': gen_ai.usage.input_tokens = %v is negative.",
        [input.sample.span.name, v],
    ),
) if {
    is_llm_span
    has_span_attr("gen_ai.usage.input_tokens")
    v := get_span_attr_int("gen_ai.usage.input_tokens")
    v < 0
}

# @weaver.rule.zh: 禁止 LLM 输出 token 数为负数。
# @weaver.rule.check.zh: 当 LLM span 的 `gen_ai.usage.output_tokens` 小于 0 时命中。
# @weaver.rule.fix.zh: 修复 usage 提取逻辑；无法获得 token 时不要写负数。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: gen_ai.usage.output_tokens
# @weaver.rule.subject: field:gen_ai.usage.output_tokens
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: invariant
deny contains make_advice(
    "llm_negative_output_tokens",
    "violation",
    {"span_name": input.sample.span.name, "value": v},
    sprintf(
        "LLM span '%s': gen_ai.usage.output_tokens = %v is negative.",
        [input.sample.span.name, v],
    ),
) if {
    is_llm_span
    has_span_attr("gen_ai.usage.output_tokens")
    v := get_span_attr_int("gen_ai.usage.output_tokens")
    v < 0
}
