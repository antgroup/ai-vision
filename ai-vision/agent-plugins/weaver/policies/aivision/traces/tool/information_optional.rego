# Tool span optional diagnostic rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 提示 Tool span 仍在使用 Langfuse vendor 字段，可同步写入通用 GenAI semconv 字段。
# @weaver.rule.check.zh: 当 Tool span 上存在 `langfuse.observation.input` 时提示信息。
# @weaver.rule.fix.zh: 保留 vendor 字段可以；需要通用分析时同步写入 `gen_ai.tool.call.arguments` 和 `gen_ai.tool.call.result`。
# @weaver.rule.note.zh: `langfuse.observation.input` 是 vendor 兼容字段，不属于推荐字段目录。
# @weaver.rule.entity: span:tool
# @weaver.rule.category.zh: 兼容性
# @weaver.rule.fields: langfuse.observation.input, langfuse.observation.output, gen_ai.tool.call.arguments, gen_ai.tool.call.result
# @weaver.rule.subject: field:langfuse.observation.input
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: compatibility
deny contains make_advice(
    "tool_using_langfuse_attrs",
    "information",
    {"span_name": input.sample.span.name},
    sprintf("Tool span '%s': Uses vendor-specific 'langfuse.observation.input/output'. Consider also using semconv 'gen_ai.tool.call.arguments' and 'gen_ai.tool.call.result'.", [input.sample.span.name])
) if {
    is_tool_span
    has_span_attr("langfuse.observation.input")
}
