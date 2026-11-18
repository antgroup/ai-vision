# Compaction span recommended-field rules.
# @weaver.spec.span_kind: COMPACTION

package live_check_advice

import rego.v1

# @weaver.rule.zh: 推荐 compaction span 包含压缩前 token 数。
# @weaver.rule.check.zh: 当 compaction span 缺少 `agentic.compaction.tokens.before` 时命中。
# @weaver.rule.fix.zh: 在上下文压缩记录中写入压缩前 token 数，用于计算压缩率。
# @weaver.rule.entity: span:compaction
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: COMPACTION, agentic.compaction.tokens.after, agentic.compaction.tokens.before, agentic.compaction.trigger, gen_ai.span.kind
# @weaver.rule.subject: field:agentic.compaction.tokens.before
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "compact_missing_tokens_before",
    "improvement",
    {"span_name": input.sample.span.name},
    sprintf(
        "Compaction span '%s': Missing recommended 'agentic.compaction.tokens.before'. Helps downstream analyse compression ratio.",
        [input.sample.span.name],
    ),
) if {
    _capability_d1_is_compaction_span
    not has_span_attr("agentic.compaction.tokens.before")
}

# @weaver.rule.zh: 推荐 compaction span 包含压缩后 token 数。
# @weaver.rule.check.zh: 当 compaction span 缺少 `agentic.compaction.tokens.after` 时命中。
# @weaver.rule.fix.zh: 在上下文压缩记录中写入压缩后 token 数，用于计算压缩率。
# @weaver.rule.entity: span:compaction
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: COMPACTION, agentic.compaction.tokens.after, agentic.compaction.tokens.before, agentic.compaction.trigger, gen_ai.span.kind
# @weaver.rule.subject: field:agentic.compaction.tokens.after
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "compact_missing_tokens_after",
    "improvement",
    {"span_name": input.sample.span.name},
    sprintf(
        "Compaction span '%s': Missing recommended 'agentic.compaction.tokens.after'.",
        [input.sample.span.name],
    ),
) if {
    _capability_d1_is_compaction_span
    not has_span_attr("agentic.compaction.tokens.after")
}

# @weaver.rule.zh: 推荐 compaction span 包含压缩触发原因。
# @weaver.rule.check.zh: 当 compaction span 缺少 `agentic.compaction.trigger` 时命中。
# @weaver.rule.fix.zh: 写入 auto、manual、threshold 等触发原因，便于分析上下文压缩来源。
# @weaver.rule.entity: span:compaction
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.fields: COMPACTION, agentic.compaction.tokens.after, agentic.compaction.tokens.before, agentic.compaction.trigger, gen_ai.span.kind
# @weaver.rule.subject: field:agentic.compaction.trigger
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "compact_missing_trigger",
    "improvement",
    {"span_name": input.sample.span.name},
    sprintf(
        "Compaction span '%s': Missing recommended 'agentic.compaction.trigger' (e.g. auto, manual, threshold).",
        [input.sample.span.name],
    ),
) if {
    _capability_d1_is_compaction_span
    not has_span_attr("agentic.compaction.trigger")
}
