# tool_result required-field rules.

package live_check_advice

import rego.v1

_tool_result_required_attrs contains attr if {
    some attr in {
        "agentic.turn.number",
        "gen_ai.tool.name",
        "duration_ms",
        "agentic.tool.type",
        "agentic.tool.name",
        "agentic.tool.id",
        "gen_ai.tool.call.id",
    }
}

_tool_result_required_attrs contains attr if {
    _is_success_tool_result
    some attr in {
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
    }
}

# @weaver.rule.zh: `tool_result` 事件必须满足该事件类型定义的必需字段矩阵。
# @weaver.rule.check.zh: `event.name=tool_result` 缺少本文件声明的必需 attribute 时命中。
# @weaver.rule.fix.zh: 按 context 中 `attribute` 指定的字段补齐；关闭内容采集时写入 `[REDACTED]` 标记。
# @weaver.rule.entity: event:tool_result
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.turn.number, gen_ai.tool.name, duration_ms, agentic.tool.type, agentic.tool.name, agentic.tool.id, gen_ai.tool.call.id, gen_ai.tool.call.arguments, gen_ai.tool.call.result
# @weaver.rule.subject: field:agentic.turn.number
# @weaver.rule.subject: field:gen_ai.tool.name
# @weaver.rule.subject: field:duration_ms
# @weaver.rule.subject: field:agentic.tool.type
# @weaver.rule.subject: field:agentic.tool.name
# @weaver.rule.subject: field:agentic.tool.id
# @weaver.rule.subject: field:gen_ai.tool.call.id
# @weaver.rule.subject: field:gen_ai.tool.call.arguments
# @weaver.rule.subject: field:gen_ai.tool.call.result
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "event_missing_required_attribute",
    "violation",
    {"event_name": "tool_result", "attribute": attr},
    sprintf("Event 'tool_result': Missing required attribute '%s'.", [attr])
) if {
    _is_tool_result
    some attr in _tool_result_required_attrs
    not has_log_attr(attr)
}
