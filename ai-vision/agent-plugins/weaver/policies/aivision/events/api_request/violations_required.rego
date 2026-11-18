# api_request required-field rules.

package live_check_advice

import rego.v1

_api_request_common_required_attrs contains attr if {
    some attr in {
        "agentic.turn.number",
        "gen_ai.request.model",
        "duration_ms",
    }
}

# Only strict native boolean markers are accepted in both wire formats.
# String "true", false, unknown source agents, and unrelated IDs do not exempt
# a primary request from the real Turn ordinal requirement.
_api_request_auxiliary_markers := [attr |
    some attr in input.sample.log.attributes
    object.get(attr, "key", object.get(attr, "name", "")) == "agentic.llm.auxiliary"
]

_api_request_has_auxiliary_marker if {
    count(_api_request_auxiliary_markers) == 1
    attr := _api_request_auxiliary_markers[0]
    attr.key == "agentic.llm.auxiliary"
    attr.value.boolValue == true
}

_api_request_has_auxiliary_marker if {
    count(_api_request_auxiliary_markers) == 1
    attr := _api_request_auxiliary_markers[0]
    attr.name == "agentic.llm.auxiliary"
    attr.value == true
}

_api_request_native_auxiliary if {
    _api_request_has_auxiliary_marker
    get_log_attr("agentic.runtime.name") == "opencode"
    agent := get_log_attr("gen_ai.agent.name")
    agent in {"title", "summary", "compaction"}
    chat_id := get_log_attr("gen_ai.chat.id")
    prefix := sprintf("aux:%s:", [agent])
    startswith(chat_id, prefix)
    count(chat_id) > count(prefix)
    not regex.match(`\s`, chat_id)
}

_api_request_common_field_unavailable(attr) if {
    attr == "agentic.turn.number"
    _api_request_native_auxiliary
}

_api_request_success_required_attrs contains attr if {
    some attr in {
        "gen_ai.response.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.total_tokens",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.system_instructions",
    }
}

_api_request_usage_unreported(attr) if {
    some direction in {"input", "output"}
    attr == sprintf("gen_ai.usage.%s_tokens", [direction])
    get_log_attr(sprintf("agentic.usage.%s_tokens.missing", [direction])) == "true"
}

# An unknown direction also makes an input+output total unknown. This helper
# never exempts response model, messages, runtime identity or other fields.
_api_request_usage_unreported("gen_ai.usage.total_tokens") if {
    some direction in {"input", "output"}
    get_log_attr(sprintf("agentic.usage.%s_tokens.missing", [direction])) == "true"
}

# @weaver.rule.zh: `api_request` 事件必须携带请求关联和基础运行时字段。
# @weaver.rule.check.zh: `event.name=api_request` 缺少通用必需 attribute 时命中；只有严格 boolean 辅助标记、OpenCode Runtime、已知源 agent 和对应 aux chat ID 允许缺失真实不存在的 Turn 编号。
# @weaver.rule.fix.zh: 按 context 中 `attribute` 指定的字段补齐；原生辅助调用使用真实源 message ID，不伪造用户 Turn 编号；关闭内容采集时写入 `[REDACTED]` 标记。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.condition.key: common
# @weaver.rule.condition.zh: 通用约束
# @weaver.rule.display.order: 10
# @weaver.rule.fields: agentic.turn.number, gen_ai.request.model, duration_ms
# @weaver.rule.subject: field:agentic.turn.number
# @weaver.rule.subject: field:gen_ai.request.model
# @weaver.rule.subject: field:duration_ms
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: required_presence
# @weaver.rule.branch: common-required
deny contains make_advice(
    "event_missing_required_attribute",
    "violation",
    {"event_name": "api_request", "attribute": attr},
    sprintf("Event 'api_request': Missing required attribute '%s'.", [attr])
) if {
    _is_api_request
    some attr in _api_request_common_required_attrs
    not has_log_attr(attr)
    not _api_request_common_field_unavailable(attr)
}

# @weaver.rule.zh: 正确完成的 `api_request` 必须携带模型响应、token 用量和输入/输出消息。
# @weaver.rule.check.zh: 成功 api_request 缺少成功路径必需 attribute 时命中；仅精确字符串 `true` 的对应 missing 标记允许省略 input/output，任一方向明确未知时允许省略 total；其它响应字段仍必填。
# @weaver.rule.fix.zh: 从实际响应写入 response model、已知 token（包括 0）与 messages；未知方向省略数值并写对应 missing 字符串 `true`，不要将未知补 0；关闭内容采集时写入 `[REDACTED]`。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.condition.key: success
# @weaver.rule.condition.zh: 正确请求
# @weaver.rule.display.order: 20
# @weaver.rule.fields: gen_ai.input.messages, gen_ai.output.messages, gen_ai.response.model, gen_ai.system_instructions, gen_ai.usage.input_tokens, gen_ai.usage.output_tokens, gen_ai.usage.total_tokens
# @weaver.rule.subject: field:gen_ai.response.model
# @weaver.rule.subject: field:gen_ai.usage.input_tokens
# @weaver.rule.subject: field:gen_ai.usage.output_tokens
# @weaver.rule.subject: field:gen_ai.usage.total_tokens
# @weaver.rule.subject: field:gen_ai.input.messages
# @weaver.rule.subject: field:gen_ai.output.messages
# @weaver.rule.subject: field:gen_ai.system_instructions
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: required_presence
# @weaver.rule.branch: success-required
deny contains make_advice(
    "event_missing_required_attribute",
    "violation",
    {"event_name": "api_request", "attribute": attr},
    sprintf("Event 'api_request': Missing required attribute '%s'.", [attr])
) if {
    _is_success_api_request
    some attr in _api_request_success_required_attrs
    not has_log_attr(attr)
    not _api_request_usage_unreported(attr)
}
