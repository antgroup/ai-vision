# Event common required envelope rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 所有 AiVision 语义事件必须声明 `event.name`，否则无法识别事件类型。
# @weaver.rule.check.zh: OTLP log record 存在但 attributes 中没有 `event.name` 时命中。
# @weaver.rule.fix.zh: 上报事件时通过 logger attributes 写入 `event.name`，并保持 body.event_name 与其一致。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: event.name
# @weaver.rule.subject: field:event.name
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "event_missing_name",
    "violation",
    {},
    "Log record: Missing required 'event.name'. Events must set one of user_prompt, api_request, tool_call, tool_result, tool_decision, subagent_spawned, subagent_ended, session_start, session_end, compaction_result."
) if {
    has_log
    not has_log_attr("event.name")
}

# @weaver.rule.zh: 所有 AiVision 语义事件必须携带 OTLP log record 顶层 `traceId`，用于把事件/日志关联回所属 trace。
# @weaver.rule.check.zh: OTLP log record 存在但顶层缺少 `traceId` 时命中；`trace_id` 不是合法 OTLP JSON key，不能替代。
# @weaver.rule.fix.zh: 上报 OTLP logs 时在 log record 顶层写入当前 span/trace 的 `traceId`，不要只放在 attributes 里。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: traceId
# @weaver.rule.subject: field:traceId
# @weaver.rule.placement: log_record_top_level
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "event_missing_trace_id",
    "violation",
    {"event_name": log_event_name, "field": "traceId"},
    sprintf("Event '%s': Missing required OTLP log record 'traceId'. Events/logs must carry trace correlation id at the log record top level.", [log_event_name])
) if {
    has_log
    not has_log_trace_id
}

# @weaver.rule.zh: 所有 AiVision 语义事件必须携带 OTLP log record 顶层 `spanId`，用于把事件/日志关联回具体 span。
# @weaver.rule.check.zh: OTLP log record 存在但顶层缺少 `spanId` 时命中；`span_id` 不是合法 OTLP JSON key，不能替代。
# @weaver.rule.fix.zh: 上报 OTLP logs 时在 log record 顶层写入当前 span 的 `spanId`，不要只放在 attributes 里。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: spanId
# @weaver.rule.subject: field:spanId
# @weaver.rule.placement: log_record_top_level
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "event_missing_span_id",
    "violation",
    {"event_name": log_event_name, "field": "spanId"},
    sprintf("Event '%s': Missing required OTLP log record 'spanId'. Events/logs must carry span correlation id at the log record top level.", [log_event_name])
) if {
    has_log
    not has_log_span_id
}

# @weaver.rule.zh: 所有已识别的 AiVision event/log attributes 都必须携带请求关联和基础运行时字段。
# @weaver.rule.check.zh: `event.name` 属于 AiVision 事件集合但缺少通用必需 attribute 时命中。
# @weaver.rule.fix.zh: 在 log attributes 上补齐 context 中 `attribute` 指定的字段；这些字段属于 event 通用 attributes，不要在每个 event-specific 规则里重复声明。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: gen_ai.operation.name, ant.username, gen_ai.conversation.id, gen_ai.chat.id, agentic.runtime.name
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.subject: field:ant.username
# @weaver.rule.subject: field:gen_ai.conversation.id
# @weaver.rule.subject: field:gen_ai.chat.id
# @weaver.rule.subject: field:agentic.runtime.name
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "event_missing_common_required_attribute",
    "violation",
    {"event_name": log_event_name, "attribute": attr},
    sprintf("Event '%s': Missing common required attribute '%s'.", [log_event_name, attr])
) if {
    has_log
    valid_opencode_event_names[log_event_name]
    some attr in _aivision_event_common_required_attrs
    not has_log_attr(attr)
}
