# Global span attribute payload/message format rules.
# Scope: span.attributes fields that carry OpenAI-compatible message payloads.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 要求 `gen_ai.input.messages` 必须是 OpenAI 兼容 JSON array 字符串。
# @weaver.rule.check.zh: 当 span 上存在非空 `gen_ai.input.messages` 但字符串不是以 `[` 开始时命中。
# @weaver.rule.fix.zh: 使用 JSON.stringify 后的数组，例如 `[{"role":"user","content":"..."}]`；需要脱敏时填 `[REDACTED]` 也应保持数组语义。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 格式校验
# @weaver.rule.fields: gen_ai.input.messages
# @weaver.rule.subject: field:gen_ai.input.messages
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: format
deny contains make_advice(
    "input_messages_not_json_array",
    "violation",
    {"span_name": input.sample.span.name, "value": substring(value, 0, 120)},
    sprintf(
        "Span '%s': gen_ai.input.messages must be a JSON array (start with '['). Got: '%s...'. Expected format: [{\"role\":\"user\",\"content\":\"...\"}]",
        [input.sample.span.name, substring(value, 0, 80)]
    )
) if {
    has_span
    value := get_span_attr("gen_ai.input.messages")
    count(value) > 0
    not startswith(value, "[")
}

# @weaver.rule.zh: 要求 `gen_ai.output.messages` 必须是 OpenAI 兼容 JSON array 字符串。
# @weaver.rule.check.zh: 当 span 上存在非空 `gen_ai.output.messages` 但字符串不是以 `[` 开始时命中。
# @weaver.rule.fix.zh: 使用 JSON.stringify 后的数组，例如 `[{"role":"assistant","content":"...","finish_reason":"stop"}]`。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 格式校验
# @weaver.rule.fields: gen_ai.output.messages
# @weaver.rule.subject: field:gen_ai.output.messages
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: format
deny contains make_advice(
    "output_messages_not_json_array",
    "violation",
    {"span_name": input.sample.span.name, "value": substring(value, 0, 120)},
    sprintf(
        "Span '%s': gen_ai.output.messages must be a JSON array (start with '['). Got: '%s...'. Expected format: [{\"role\":\"assistant\",\"content\":\"...\",\"finish_reason\":\"stop\"}]",
        [input.sample.span.name, substring(value, 0, 80)]
    )
) if {
    has_span
    value := get_span_attr("gen_ai.output.messages")
    count(value) > 0
    not startswith(value, "[")
}

# @weaver.rule.zh: 要求 `gen_ai.system_instructions` 必须是 JSON 序列化字符串或数组。
# @weaver.rule.check.zh: 当 span 上存在非空 `gen_ai.system_instructions` 但字符串不是以 `\"` 或 `[` 开始时命中。
# @weaver.rule.fix.zh: 对单条 system prompt 使用 JSON.stringify("...")；多条指令使用 JSON array 字符串。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 格式校验
# @weaver.rule.fields: gen_ai.system_instructions
# @weaver.rule.subject: field:gen_ai.system_instructions
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: format
deny contains make_advice(
    "system_instructions_not_json",
    "violation",
    {"span_name": input.sample.span.name, "value": substring(value, 0, 120)},
    sprintf(
        "Span '%s': gen_ai.system_instructions must be a JSON-serialized value (start with '\"' or '['). Got: '%s...'. Expected: JSON.stringify(\"You are...\") or [\"instruction1\",\"instruction2\"]",
        [input.sample.span.name, substring(value, 0, 80)]
    )
) if {
    has_span
    value := get_span_attr("gen_ai.system_instructions")
    count(value) > 0
    not startswith(value, "\"")
    not startswith(value, "[")
}
