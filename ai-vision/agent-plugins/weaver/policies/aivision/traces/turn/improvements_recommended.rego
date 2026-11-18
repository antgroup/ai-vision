# Turn span recommended-field rules.
# Scope: gen_ai.span.kind=CHAT or legacy "Turn N" spans.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 推荐 Turn span 名称使用 `Turn {N}` 格式，便于人工阅读和 UI 聚合。
# @weaver.rule.check.zh: 当 Turn span 名称不是以 `Turn ` 开头时命中。
# @weaver.rule.fix.zh: 将 Turn span name 设置为 `Turn 1`、`Turn 2` 等格式。
# @weaver.rule.entity: span:turn
# @weaver.rule.category.zh: 格式校验
# @weaver.rule.fields: agentic.runtime.name, agentic.turn.number, gen_ai.chat.id, span.name
# @weaver.rule.subject: field:span.name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: format
deny contains make_advice(
    "turn_span_naming_convention",
    "improvement",
    {"span_name": input.sample.span.name, "suggested": "Turn {N}"},
    sprintf("Turn span '%s': Recommended span name format is 'Turn {N}' (e.g., 'Turn 1', 'Turn 2').", [input.sample.span.name])
) if {
    is_turn_span
    not startswith(input.sample.span.name, "Turn ")
}
