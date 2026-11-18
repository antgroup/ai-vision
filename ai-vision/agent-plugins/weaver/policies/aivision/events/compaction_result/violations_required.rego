# compaction_result required-field rules.

package live_check_advice

import rego.v1

_compaction_result_required_attrs contains attr if {
    some attr in {
        "agentic.turn.number",
        "gen_ai.tool.name",
        "agentic.tool.type",
        "duration_ms",
    }
}

# @weaver.rule.zh: `compaction_result` 事件必须满足该事件类型定义的必需字段矩阵。
# @weaver.rule.check.zh: `event.name=compaction_result` 缺少本文件声明的必需 attribute 时命中。
# @weaver.rule.fix.zh: 按 context 中 `attribute` 指定的字段补齐；关闭内容采集时写入 `[REDACTED]` 标记。
# @weaver.rule.entity: event:compaction_result
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.turn.number, gen_ai.tool.name, agentic.tool.type, duration_ms
# @weaver.rule.subject: field:agentic.turn.number
# @weaver.rule.subject: field:gen_ai.tool.name
# @weaver.rule.subject: field:agentic.tool.type
# @weaver.rule.subject: field:duration_ms
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "event_missing_required_attribute",
    "violation",
    {"event_name": "compaction_result", "attribute": attr},
    sprintf("Event 'compaction_result': Missing required attribute '%s'.", [attr])
) if {
    _is_compaction_result
    some attr in _compaction_result_required_attrs
    not has_log_attr(attr)
}
