# Event common forbidden/value rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 只允许当前定义的 AiVision 事件名，避免旧事件或拼写错误进入诊断链路。
# @weaver.rule.check.zh: `event.name` 不在当前事件集合内时命中。
# @weaver.rule.fix.zh: 将事件名迁移到当前 AiVision 事件集合；工具执行开始使用 `tool_call`，旧 `llm_request` 使用 `api_request`。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 枚举值
# @weaver.rule.fields: ant.agent.product, ant.agent.type, ant.username, api_request, compaction_result, event.name, event.sequence, gen_ai.conversation.id, gen_ai.operation.name, session_end, session_start, spanId, tool_call, tool_decision, tool_result, traceId, user_prompt
# @weaver.rule.subject: field:event.name
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: enum
# @weaver.rule.allowed_values: user_prompt, api_request, tool_call, tool_result, tool_decision, subagent_spawned, subagent_ended, session_start, session_end, compaction_result
deny contains make_advice(
    "event_unknown_name",
    "violation",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s': Unknown event.name. Allowed: user_prompt, api_request, tool_call, tool_result, tool_decision, subagent_spawned, subagent_ended, session_start, session_end, compaction_result.", [get_log_attr("event.name")])
) if {
    _is_ant_event
    not get_log_attr("event.name") in valid_opencode_event_names
}

# @weaver.rule.zh: opencode v4 统一使用 `api_request` 表示 LLM/API 请求事件。
# @weaver.rule.check.zh: 发现 legacy `llm_request` 事件名时命中。
# @weaver.rule.fix.zh: 将事件名从 `llm_request` 改为 `api_request`，字段结构保持 api_request 规则要求。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 兼容性
# @weaver.rule.fields: ant.agent.product, ant.agent.type, ant.username, api_request, compaction_result, event.name, event.sequence, gen_ai.conversation.id, gen_ai.operation.name, session_end, session_start, spanId, tool_result, traceId, user_prompt
# @weaver.rule.subject: field:event.name
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: compatibility
# @weaver.rule.replacement: field:event.name=api_request
deny contains make_advice(
    "event_legacy_llm_request_name",
    "violation",
    {"event_name": "llm_request"},
    "Event 'llm_request': Legacy event name. Use current OpenCode event.name 'api_request'."
) if {
    is_event("llm_request")
}

# @weaver.rule.zh: 禁止在 event/log attributes 中放入只属于 Resource 的身份字段。
# @weaver.rule.check.zh: 当事件 attributes 上出现 `ant.agent.type`、`ant.agent.product`、`ant.agent.instance`、`ant.agent.env`、`ant.agent.scene`、`service.*` 等 resource-only 字段时命中。
# @weaver.rule.fix.zh: 将这些字段移动到 LogRecord Resource attributes；event/log attributes 只保留该事件自己的语义字段。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: ant.agent.type, ant.agent.product, ant.agent.instance, ant.agent.env, ant.agent.scene, service.name, service.version, service.instance.id
# @weaver.rule.subject: field:ant.agent.type
# @weaver.rule.subject: field:ant.agent.product
# @weaver.rule.subject: field:ant.agent.instance
# @weaver.rule.subject: field:ant.agent.env
# @weaver.rule.subject: field:ant.agent.scene
# @weaver.rule.subject: field:service.name
# @weaver.rule.subject: field:service.version
# @weaver.rule.subject: field:service.instance.id
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.branch: occurrence-1
deny contains make_advice(
    "event_placement_resource_only_in_log",
    "violation",
    {"attribute": attr.key, "event_name": get_log_attr("event.name")},
    sprintf("Event '%s': Attribute '%s' is resource-only and must NOT appear in log attributes.", [get_log_attr("event.name"), attr.key])
) if {
    _is_ant_event
    some attr in input.sample.log.attributes
    is_string(attr.key)
    _is_resource_only(attr.key)
}

# @weaver.rule.zh: 禁止在 Weaver `name` 格式的 event/log attributes 中放入只属于 Resource 的身份字段。
# @weaver.rule.check.zh: 当事件 attributes 上通过 `name` 写入 `ant.agent.type`、`ant.agent.product`、`ant.agent.instance`、`ant.agent.env`、`ant.agent.scene`、`service.*` 等 resource-only 字段时命中。
# @weaver.rule.fix.zh: 将这些字段移动到 LogRecord Resource attributes；event/log attributes 只保留该事件自己的语义字段。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: ant.agent.type, ant.agent.product, ant.agent.instance, ant.agent.env, ant.agent.scene, service.name, service.version, service.instance.id
# @weaver.rule.subject: field:ant.agent.type
# @weaver.rule.subject: field:ant.agent.product
# @weaver.rule.subject: field:ant.agent.instance
# @weaver.rule.subject: field:ant.agent.env
# @weaver.rule.subject: field:ant.agent.scene
# @weaver.rule.subject: field:service.name
# @weaver.rule.subject: field:service.version
# @weaver.rule.subject: field:service.instance.id
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.branch: occurrence-2
deny contains make_advice(
    "event_placement_resource_only_in_log",
    "violation",
    {"attribute": attr.name, "event_name": get_log_attr("event.name")},
    sprintf("Event '%s': Attribute '%s' is resource-only and must NOT appear in log attributes.", [get_log_attr("event.name"), attr.name])
) if {
    _is_ant_event
    some attr in input.sample.log.attributes
    is_string(attr.name)
    _is_resource_only(attr.name)
}

# @weaver.rule.zh: `agentic.turn.number` 必须是非负数，避免轮次排序字段不可解析。
# @weaver.rule.check.zh: `agentic.turn.number` 存在但不是数字，或数字小于 0 时命中。
# @weaver.rule.fix.zh: 将 `agentic.turn.number` 写成 OTel int/double 值，或可解析为数字的字符串。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: agentic.turn.number, ant.agent.product, ant.agent.type, ant.username, api_request, compaction_result, event.name, event.sequence, gen_ai.conversation.id, gen_ai.operation.name, session_end, session_start, spanId, tool_result, traceId, user_prompt
# @weaver.rule.subject: field:agentic.turn.number
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.branch: occurrence-1
deny contains make_advice(
    "event_invalid_turn_number",
    "violation",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s': Invalid 'agentic.turn.number'. It must be a non-negative number.", [get_log_attr("event.name")])
) if {
    _is_ant_event
    has_log_attr("agentic.turn.number")
    not has_log_attr_number("agentic.turn.number")
}
# @weaver.rule.title.zh: event:common 不变量: event_invalid_turn_number
# @weaver.rule.zh: 校验 event:common 的「不变量」规则 `event_invalid_turn_number`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: agentic.turn.number, ant.agent.product, ant.agent.type, ant.username, api_request, compaction_result, event.name, event.sequence, gen_ai.conversation.id, gen_ai.operation.name, session_end, session_start, spanId, tool_result, traceId, user_prompt
# @weaver.rule.subject: field:agentic.turn.number
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.check.zh: Rego 条件命中 `event_invalid_turn_number` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。
# @weaver.rule.branch: occurrence-2

deny contains make_advice(
    "event_invalid_turn_number",
    "violation",
    {"event_name": get_log_attr("event.name"), "value": turn_number},
    sprintf("Event '%s': Invalid 'agentic.turn.number' %v. It must be non-negative.", [get_log_attr("event.name"), turn_number])
) if {
    _is_ant_event
    turn_number := get_log_attr_number("agentic.turn.number")
    turn_number < 0
}

# @weaver.rule.zh: `event.sequence` 必须是非负数，避免排序字段不可解析。
# @weaver.rule.check.zh: `event.sequence` 存在但不是数字，或数字小于 0 时命中。
# @weaver.rule.fix.zh: 将 `event.sequence` 写成 OTel int/double 值，或可解析为数字的字符串。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: ant.agent.product, ant.agent.type, ant.username, api_request, compaction_result, event.name, event.sequence, gen_ai.conversation.id, gen_ai.operation.name, session_end, session_start, spanId, tool_result, traceId, user_prompt
# @weaver.rule.subject: field:event.sequence
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.branch: occurrence-1
deny contains make_advice(
    "event_invalid_sequence",
    "violation",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s': Invalid 'event.sequence'. It must be a non-negative number.", [get_log_attr("event.name")])
) if {
    _is_ant_event
    has_log_attr("event.sequence")
    not has_log_attr_number("event.sequence")
}
# @weaver.rule.title.zh: event:common 不变量: event_invalid_sequence
# @weaver.rule.zh: 校验 event:common 的「不变量」规则 `event_invalid_sequence`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: ant.agent.product, ant.agent.type, ant.username, api_request, compaction_result, event.name, event.sequence, gen_ai.conversation.id, gen_ai.operation.name, session_end, session_start, spanId, tool_result, traceId, user_prompt
# @weaver.rule.subject: field:event.sequence
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.check.zh: Rego 条件命中 `event_invalid_sequence` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。
# @weaver.rule.branch: occurrence-2

deny contains make_advice(
    "event_invalid_sequence",
    "violation",
    {"event_name": get_log_attr("event.name"), "value": seq},
    sprintf("Event '%s': Invalid 'event.sequence' %v. It must be non-negative.", [get_log_attr("event.name"), seq])
) if {
    _is_ant_event
    seq := get_log_attr_number("event.sequence")
    seq < 0
}

# @weaver.rule.zh: OpenCode 不再使用 `prompt.id`，turn 关联统一使用 `gen_ai.chat.id`。
# @weaver.rule.check.zh: 事件 attributes 中出现 legacy `prompt.id` 时命中。
# @weaver.rule.fix.zh: 删除 `prompt.id`，并在事件 common attributes 上写入 `gen_ai.chat.id`。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: ant.agent.product, ant.agent.type, ant.username, api_request, compaction_result, event.name, event.sequence, gen_ai.conversation.id, gen_ai.operation.name, prompt.id, session_end, session_start, spanId, tool_result, traceId, user_prompt
# @weaver.rule.subject: field:prompt.id
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: forbidden_presence
deny contains make_advice(
    "event_forbidden_prompt_id",
    "violation",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s': Forbidden attribute 'prompt.id' found. Use 'gen_ai.chat.id' instead.", [get_log_attr("event.name")])
) if {
    _is_ant_event
    has_log_attr("prompt.id")
}

# @weaver.rule.zh: OTel log record 已有原生时间戳，事件属性中不应重复写 `event.timestamp`。
# @weaver.rule.check.zh: 事件 attributes 中出现 `event.timestamp` 时命中。
# @weaver.rule.fix.zh: 删除 `event.timestamp`，使用 OTLP LogRecord timestamp/observedTimeUnixNano。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: ant.agent.product, ant.agent.type, ant.username, api_request, compaction_result, event.name, event.sequence, event.timestamp, gen_ai.conversation.id, gen_ai.operation.name, session_end, session_start, spanId, tool_result, traceId, user_prompt
# @weaver.rule.subject: field:event.timestamp
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: forbidden_presence
deny contains make_advice(
    "event_forbidden_event_timestamp",
    "violation",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s': Forbidden attribute 'event.timestamp' found. OTel log records have built-in timestamps.", [get_log_attr("event.name")])
) if {
    _is_ant_event
    has_log_attr("event.timestamp")
}

# @weaver.rule.zh: `duration_ms` 必须是非负数，保证延迟统计可用。
# @weaver.rule.check.zh: `duration_ms` 存在但不可解析为数字，或数字小于 0 时命中。
# @weaver.rule.fix.zh: 将 `duration_ms` 写成 OTel int/double 值，或可解析为数字的字符串。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: ant.agent.product, ant.agent.type, ant.username, api_request, compaction_result, event.name, event.sequence, gen_ai.conversation.id, gen_ai.operation.name, session_end, session_start, spanId, tool_result, traceId, user_prompt
# @weaver.rule.subject: field:duration_ms
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.branch: occurrence-1
deny contains make_advice(
    "event_invalid_duration",
    "violation",
    {"event_name": get_log_attr("event.name")},
    sprintf("Event '%s': Invalid 'duration_ms'. It must be a non-negative number.", [get_log_attr("event.name")])
) if {
    _is_ant_event
    has_log_attr("duration_ms")
    not has_log_attr_number("duration_ms")
}
# @weaver.rule.title.zh: event:common 不变量: event_invalid_duration
# @weaver.rule.zh: 校验 event:common 的「不变量」规则 `event_invalid_duration`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 不变量
# @weaver.rule.fields: ant.agent.product, ant.agent.type, ant.username, api_request, compaction_result, event.name, event.sequence, gen_ai.conversation.id, gen_ai.operation.name, session_end, session_start, spanId, tool_result, traceId, user_prompt
# @weaver.rule.subject: field:duration_ms
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.check.zh: Rego 条件命中 `event_invalid_duration` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。
# @weaver.rule.branch: occurrence-2

deny contains make_advice(
    "event_invalid_duration",
    "violation",
    {"event_name": get_log_attr("event.name"), "value": duration},
    sprintf("Event '%s': Invalid 'duration_ms' %v. It must be non-negative.", [get_log_attr("event.name"), duration])
) if {
    _is_ant_event
    duration := get_log_attr_number("duration_ms")
    duration < 0
}
