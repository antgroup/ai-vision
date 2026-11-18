# session_end required-field rules.

package live_check_advice

import rego.v1

_session_end_required_attrs contains attr if {
    some attr in {
        "gen_ai.session.id",
        "agentic.turn.number",
    }
}

# @weaver.rule.zh: `session_end` 事件必须满足该事件类型定义的必需字段矩阵。
# @weaver.rule.check.zh: `event.name=session_end` 缺少本文件声明的必需 attribute 时命中。
# @weaver.rule.fix.zh: 按 context 中 `attribute` 指定的字段补齐；关闭内容采集时写入 `[REDACTED]` 标记。
# @weaver.rule.entity: event:session_end
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: gen_ai.session.id, agentic.turn.number
# @weaver.rule.subject: field:gen_ai.session.id
# @weaver.rule.subject: field:agentic.turn.number
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "event_missing_required_attribute",
    "violation",
    {"event_name": "session_end", "attribute": attr},
    sprintf("Event 'session_end': Missing required attribute '%s'.", [attr])
) if {
    _is_session_end
    some attr in _session_end_required_attrs
    not has_log_attr(attr)
}
