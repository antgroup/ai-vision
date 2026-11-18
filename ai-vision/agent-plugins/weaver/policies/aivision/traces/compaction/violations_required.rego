# Compaction span fixed-value rules.
# Scope: gen_ai.span.kind=COMPACTION or legacy compaction spans.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 要求 Compaction span 的 `gen_ai.span.kind` 值固定为 `COMPACTION`。
# @weaver.rule.check.zh: 当 compaction span 上 `gen_ai.span.kind` 存在但不是 `COMPACTION` 时命中。
# @weaver.rule.fix.zh: 将上下文压缩 span 的 `gen_ai.span.kind` 改为 `COMPACTION`。
# @weaver.rule.entity: span:compaction
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: gen_ai.span.kind
# @weaver.rule.subject: field:gen_ai.span.kind
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: COMPACTION
deny contains make_advice(
    "compaction_invalid_span_kind",
    "violation",
    {"span_name": input.sample.span.name, "actual_value": get_span_attr("gen_ai.span.kind"), "expected_value": "COMPACTION"},
    sprintf("Compaction span '%s': gen_ai.span.kind must be 'COMPACTION'.", [input.sample.span.name])
) if {
    _capability_d1_is_compaction_span
    has_span_attr("gen_ai.span.kind")
    get_span_attr("gen_ai.span.kind") != "COMPACTION"
}
