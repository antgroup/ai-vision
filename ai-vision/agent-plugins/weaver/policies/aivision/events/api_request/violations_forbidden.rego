# api_request forbidden/value rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: api_request 表示一次 LLM/API 请求，operation 必须是 chat。
# @weaver.rule.check.zh: `event.name=api_request` 且 `gen_ai.operation.name` 不是 `chat` 时命中。
# @weaver.rule.fix.zh: api_request 事件写入 `gen_ai.operation.name=chat`。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 固定值
# @weaver.rule.condition.key: common
# @weaver.rule.condition.zh: 通用约束
# @weaver.rule.display.order: 30
# @weaver.rule.fields: gen_ai.operation.name
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: chat
deny contains make_advice(
    "api_request_wrong_operation_name",
    "violation",
    {"event_name": "api_request"},
    sprintf("Event 'api_request': gen_ai.operation.name should be 'chat', got '%s'.", [get_log_attr("gen_ai.operation.name")])
) if {
    _is_api_request
    has_log_attr("gen_ai.operation.name")
    get_log_attr("gen_ai.operation.name") != "chat"
}

# @weaver.rule.zh: `gen_ai.usage.input_tokens` 必须是非负数。
# @weaver.rule.check.zh: api_request 中该字段存在但不可解析为数字或小于 0 时命中。
# @weaver.rule.fix.zh: 将输入 token 写成 OTel int/double 值，或可解析为数字的字符串。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 不变量
# @weaver.rule.condition.key: success
# @weaver.rule.condition.zh: 正确请求
# @weaver.rule.display.order: 40
# @weaver.rule.fields: gen_ai.usage.input_tokens
# @weaver.rule.subject: field:gen_ai.usage.input_tokens
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.branch: occurrence-1
deny contains make_advice(
    "api_request_invalid_input_tokens",
    "violation",
    {"event_name": "api_request"},
    "Event 'api_request': Invalid 'gen_ai.usage.input_tokens'. It must be a non-negative number."
) if {
    _is_api_request
    has_log_attr("gen_ai.usage.input_tokens")
    not has_log_attr_number("gen_ai.usage.input_tokens")
}
# @weaver.rule.title.zh: event:api_request 不变量: api_request_invalid_input_tokens
# @weaver.rule.zh: 校验 event:api_request 的「不变量」规则 `api_request_invalid_input_tokens`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 不变量
# @weaver.rule.condition.key: success
# @weaver.rule.condition.zh: 正确请求
# @weaver.rule.display.order: 41
# @weaver.rule.fields: gen_ai.usage.input_tokens
# @weaver.rule.subject: field:gen_ai.usage.input_tokens
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.check.zh: Rego 条件命中 `api_request_invalid_input_tokens` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。
# @weaver.rule.branch: occurrence-2

deny contains make_advice(
    "api_request_invalid_input_tokens",
    "violation",
    {"event_name": "api_request", "value": input_tokens},
    sprintf("Event 'api_request': Invalid 'gen_ai.usage.input_tokens' %v. It must be non-negative.", [input_tokens])
) if {
    _is_api_request
    input_tokens := get_log_attr_number("gen_ai.usage.input_tokens")
    input_tokens < 0
}

# @weaver.rule.zh: `gen_ai.usage.output_tokens` 必须是非负数。
# @weaver.rule.check.zh: api_request 中该字段存在但不可解析为数字或小于 0 时命中。
# @weaver.rule.fix.zh: 将输出 token 写成 OTel int/double 值，或可解析为数字的字符串。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 不变量
# @weaver.rule.condition.key: success
# @weaver.rule.condition.zh: 正确请求
# @weaver.rule.display.order: 50
# @weaver.rule.fields: gen_ai.usage.output_tokens
# @weaver.rule.subject: field:gen_ai.usage.output_tokens
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.branch: occurrence-1
deny contains make_advice(
    "api_request_invalid_output_tokens",
    "violation",
    {"event_name": "api_request"},
    "Event 'api_request': Invalid 'gen_ai.usage.output_tokens'. It must be a non-negative number."
) if {
    _is_api_request
    has_log_attr("gen_ai.usage.output_tokens")
    not has_log_attr_number("gen_ai.usage.output_tokens")
}
# @weaver.rule.title.zh: event:api_request 不变量: api_request_invalid_output_tokens
# @weaver.rule.zh: 校验 event:api_request 的「不变量」规则 `api_request_invalid_output_tokens`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 不变量
# @weaver.rule.condition.key: success
# @weaver.rule.condition.zh: 正确请求
# @weaver.rule.display.order: 51
# @weaver.rule.fields: gen_ai.usage.output_tokens
# @weaver.rule.subject: field:gen_ai.usage.output_tokens
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.check.zh: Rego 条件命中 `api_request_invalid_output_tokens` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。
# @weaver.rule.branch: occurrence-2

deny contains make_advice(
    "api_request_invalid_output_tokens",
    "violation",
    {"event_name": "api_request", "value": output_tokens},
    sprintf("Event 'api_request': Invalid 'gen_ai.usage.output_tokens' %v. It must be non-negative.", [output_tokens])
) if {
    _is_api_request
    output_tokens := get_log_attr_number("gen_ai.usage.output_tokens")
    output_tokens < 0
}

# @weaver.rule.zh: `gen_ai.usage.total_tokens` 必须是非负数。
# @weaver.rule.check.zh: api_request 中该字段存在但不可解析为数字或小于 0 时命中。
# @weaver.rule.fix.zh: 将总 token 写成 OTel int/double 值，或可解析为数字的字符串。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 不变量
# @weaver.rule.condition.key: success
# @weaver.rule.condition.zh: 正确请求
# @weaver.rule.display.order: 60
# @weaver.rule.fields: gen_ai.usage.total_tokens
# @weaver.rule.subject: field:gen_ai.usage.total_tokens
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.branch: occurrence-1
deny contains make_advice(
    "api_request_invalid_total_tokens",
    "violation",
    {"event_name": "api_request"},
    "Event 'api_request': Invalid 'gen_ai.usage.total_tokens'. It must be a non-negative number."
) if {
    _is_api_request
    has_log_attr("gen_ai.usage.total_tokens")
    not has_log_attr_number("gen_ai.usage.total_tokens")
}
# @weaver.rule.title.zh: event:api_request 不变量: api_request_invalid_total_tokens
# @weaver.rule.zh: 校验 event:api_request 的「不变量」规则 `api_request_invalid_total_tokens`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 不变量
# @weaver.rule.condition.key: success
# @weaver.rule.condition.zh: 正确请求
# @weaver.rule.display.order: 61
# @weaver.rule.fields: gen_ai.usage.total_tokens
# @weaver.rule.subject: field:gen_ai.usage.total_tokens
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.check.zh: Rego 条件命中 `api_request_invalid_total_tokens` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。
# @weaver.rule.branch: occurrence-2

deny contains make_advice(
    "api_request_invalid_total_tokens",
    "violation",
    {"event_name": "api_request", "value": total_tokens},
    sprintf("Event 'api_request': Invalid 'gen_ai.usage.total_tokens' %v. It must be non-negative.", [total_tokens])
) if {
    _is_api_request
    total_tokens := get_log_attr_number("gen_ai.usage.total_tokens")
    total_tokens < 0
}

# @weaver.spec.id: token_total_equals_input_plus_output
# @weaver.rule.zh: 成功 api_request 的 total tokens 必须等于 input + output，避免聚合口径分裂。
# @weaver.rule.check.zh: 三个 token 字段都存在且可解析，但 `total != input + output` 时命中。
# @weaver.rule.fix.zh: 按当前 opencode 口径令 total = `gen_ai.usage.input_tokens + gen_ai.usage.output_tokens`。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 不变量
# @weaver.rule.condition.key: success
# @weaver.rule.condition.zh: 正确请求
# @weaver.rule.display.order: 70
# @weaver.rule.fields: gen_ai.usage.input_tokens, gen_ai.usage.output_tokens, gen_ai.usage.total_tokens
# @weaver.rule.subject: field:gen_ai.usage.input_tokens
# @weaver.rule.subject: field:gen_ai.usage.output_tokens
# @weaver.rule.subject: field:gen_ai.usage.total_tokens
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
deny contains make_advice(
    "api_request_total_tokens_mismatch",
    "violation",
    {"event_name": "api_request", "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens},
    sprintf("Event 'api_request': gen_ai.usage.total_tokens (%v) must equal input_tokens (%v) + output_tokens (%v).", [total_tokens, input_tokens, output_tokens])
) if {
    _is_success_api_request
    input_tokens := get_log_attr_number("gen_ai.usage.input_tokens")
    output_tokens := get_log_attr_number("gen_ai.usage.output_tokens")
    total_tokens := get_log_attr_number("gen_ai.usage.total_tokens")
    total_tokens != input_tokens + output_tokens
}

# @weaver.rule.zh: `agentic.usage.cache_read_tokens` 是可选字段；存在时必须是非负数。
# @weaver.rule.check.zh: api_request 中该字段存在但不可解析为数字或小于 0 时命中。
# @weaver.rule.fix.zh: 将缓存 token 写成 OTel int/double 值，或可解析为数字的字符串；没有该信息时省略字段。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 不变量
# @weaver.rule.condition.key: success
# @weaver.rule.condition.zh: 正确请求
# @weaver.rule.display.order: 72
# @weaver.rule.fields: agentic.usage.cache_read_tokens
# @weaver.rule.subject: field:agentic.usage.cache_read_tokens
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.branch: occurrence-1
deny contains make_advice(
    "api_request_invalid_cached_tokens",
    "violation",
    {"event_name": "api_request"},
    "Event 'api_request': Invalid 'agentic.usage.cache_read_tokens'. It must be a non-negative number."
) if {
    _is_api_request
    has_log_attr("agentic.usage.cache_read_tokens")
    not has_log_attr_number("agentic.usage.cache_read_tokens")
}

# @weaver.rule.title.zh: event:api_request 不变量: api_request_invalid_cached_tokens
# @weaver.rule.zh: 校验 event:api_request 的「不变量」规则 `api_request_invalid_cached_tokens`，确保缓存 token 数非负。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 不变量
# @weaver.rule.condition.key: success
# @weaver.rule.condition.zh: 正确请求
# @weaver.rule.display.order: 73
# @weaver.rule.fields: agentic.usage.cache_read_tokens
# @weaver.rule.subject: field:agentic.usage.cache_read_tokens
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
# @weaver.rule.check.zh: `agentic.usage.cache_read_tokens` 可解析为数字但小于 0 时报告。
# @weaver.rule.fix.zh: 修正缓存 token 提取逻辑；token count 不能为负数。
# @weaver.rule.branch: occurrence-2
deny contains make_advice(
    "api_request_invalid_cached_tokens",
    "violation",
    {"event_name": "api_request", "value": cached_tokens},
    sprintf("Event 'api_request': Invalid 'agentic.usage.cache_read_tokens' %v. It must be non-negative.", [cached_tokens])
) if {
    _is_api_request
    cached_tokens := get_log_attr_number("agentic.usage.cache_read_tokens")
    cached_tokens < 0
}

# @weaver.spec.id: cached_tokens_within_input_tokens
# @weaver.rule.zh: 成功 api_request 的 cached tokens 数必须小于等于 input tokens。
# @weaver.rule.check.zh: `agentic.usage.cache_read_tokens` 和 `gen_ai.usage.input_tokens` 都存在且可解析，但 cached_tokens 大于 input_tokens 时命中。
# @weaver.rule.fix.zh: 确认缓存 token 口径；`agentic.usage.cache_read_tokens` 只能表示 input tokens 的缓存读取命中子集。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 不变量
# @weaver.rule.condition.key: success
# @weaver.rule.condition.zh: 正确请求
# @weaver.rule.display.order: 75
# @weaver.rule.fields: agentic.usage.cache_read_tokens, gen_ai.usage.input_tokens
# @weaver.rule.subject: field:agentic.usage.cache_read_tokens
# @weaver.rule.subject: field:gen_ai.usage.input_tokens
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: invariant
deny contains make_advice(
    "api_request_cached_tokens_exceeds_input_tokens",
    "violation",
    {"event_name": "api_request", "cached_tokens": cached_tokens, "input_tokens": input_tokens},
    sprintf("Event 'api_request': agentic.usage.cache_read_tokens (%v) must be <= gen_ai.usage.input_tokens (%v).", [cached_tokens, input_tokens])
) if {
    _is_success_api_request
    cached_tokens := get_log_attr_number("agentic.usage.cache_read_tokens")
    input_tokens := get_log_attr_number("gen_ai.usage.input_tokens")
    cached_tokens > input_tokens
}

# @weaver.rule.zh: api_request 错误事件必须同时携带错误消息，保证诊断可读。
# @weaver.rule.check.zh: api_request 有 `error.type` 但缺少 `error.message` 时命中。
# @weaver.rule.fix.zh: 从 SDK/API error data.message 写入 `error.message`。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.condition.key: error
# @weaver.rule.condition.zh: 错误请求
# @weaver.rule.display.order: 80
# @weaver.rule.fields: error.type, error.message
# @weaver.rule.subject: field:error.message
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "api_request_error_missing_message",
    "violation",
    {"event_name": "api_request"},
    "Event 'api_request': Missing required 'error.message' when 'error.type' is present."
) if {
    _is_api_request
    has_log_attr("error.type")
    not has_log_attr("error.message")
}

# @weaver.rule.zh: api_request 错误消息不能脱离错误类型单独出现。
# @weaver.rule.check.zh: api_request 有 `error.message` 但缺少 `error.type` 时命中。
# @weaver.rule.fix.zh: 失败请求同时写入 `error.type` 和 `error.message`；成功请求不要写 `error.message`。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.condition.key: error
# @weaver.rule.condition.zh: 错误请求
# @weaver.rule.display.order: 81
# @weaver.rule.fields: error.type, error.message
# @weaver.rule.subject: field:error.type
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "api_request_error_missing_type",
    "violation",
    {"event_name": "api_request"},
    "Event 'api_request': Missing required 'error.type' when 'error.message' is present."
) if {
    _is_api_request
    has_log_attr("error.message")
    not has_log_attr("error.type")
}
