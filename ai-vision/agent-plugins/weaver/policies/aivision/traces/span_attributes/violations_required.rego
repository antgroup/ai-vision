# Global span attribute required-field rules.
# Scope: span.attributes shared by all AiVision trace/span types.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 要求采集端的 span 提供 `user.id`。AI Vision 入库后的 Trace 可使用网关写入的资源身份。
# @weaver.rule.check.zh: 当 span 缺少 `user.id`，且资源未同时提供非空的 `aivision.space.id`、`aivision.user.id` 和 `aivision.ingest.key_id` 时命中。
# @weaver.rule.fix.zh: 采集端填写稳定、脱敏的 `user.id`。校验入库 Trace 时保留网关返回的资源身份字段。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: user.id, ant.username, gen_ai.conversation.id, gen_ai.chat.id, gen_ai.operation.name, gen_ai.span.kind
# @weaver.rule.subject: field:user.id
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "span_missing_user_id",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Span '%s': Missing 'user.id' and complete AI Vision gateway resource identity.", [input.sample.span.name])
) if {
    has_span
    not has_span_attr("user.id")
    not has_ingested_trace_identity
}

# @weaver.rule.zh: 要求采集端的 span 提供 `ant.username`。AI Vision 入库后的 Trace 可使用网关写入的资源身份。
# @weaver.rule.check.zh: 当 span 缺少 `ant.username`，且资源未同时提供非空的 `aivision.space.id`、`aivision.user.id` 和 `aivision.ingest.key_id` 时命中。
# @weaver.rule.fix.zh: 采集端填写脱敏后的 `ant.username` 或稳定别名。校验入库 Trace 时保留网关返回的资源身份字段。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: user.id, ant.username, gen_ai.conversation.id, gen_ai.chat.id, gen_ai.operation.name, gen_ai.span.kind
# @weaver.rule.subject: field:ant.username
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "semconv_all_missing_username",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Span '%s': Missing 'ant.username' and complete AI Vision gateway resource identity.", [input.sample.span.name])
) if {
    has_span
    not has_span_attr("ant.username")
    not has_ingested_trace_identity
}

# @weaver.rule.zh: 要求所有 span attributes 必须包含 `gen_ai.conversation.id`，保证 trace 内 span 可以按会话关联。
# @weaver.rule.check.zh: 当任意 span 缺少 `gen_ai.conversation.id` 时命中。
# @weaver.rule.fix.zh: 在每个 span 上补充会话 id；同一会话内 Turn/LLM/Tool/Compaction 应保持一致。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: user.id, ant.username, gen_ai.conversation.id, gen_ai.chat.id, gen_ai.operation.name, gen_ai.span.kind
# @weaver.rule.subject: field:gen_ai.conversation.id
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "span_missing_conversation_id",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Span '%s': Missing required 'gen_ai.conversation.id' in span attributes. All spans must carry conversation identity.", [input.sample.span.name])
) if {
    has_span
    not has_span_attr("gen_ai.conversation.id")
}

# @weaver.rule.zh: 要求所有 span attributes 必须包含 `gen_ai.chat.id`，保证 trace 内每个 span 都能关联到 prompt/chat 级标识。
# @weaver.rule.check.zh: 当任意 span 缺少 `gen_ai.chat.id` 时命中。
# @weaver.rule.fix.zh: 优先写入 transcript/session 中的原生 prompt/chat id；若数据源拿不到，则写入当前 span 所属 trace id。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: user.id, ant.username, gen_ai.conversation.id, gen_ai.chat.id, gen_ai.operation.name, gen_ai.span.kind
# @weaver.rule.subject: field:gen_ai.chat.id
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "span_missing_chat_id",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Span '%s': Missing required 'gen_ai.chat.id' in span attributes. All spans must carry prompt/chat identity.", [input.sample.span.name])
) if {
    has_span
    not has_span_attr("gen_ai.chat.id")
}

# @weaver.rule.zh: 要求所有 span attributes 必须包含 `gen_ai.operation.name`，保证 trace 内所有 span 都能按操作类型归类。
# @weaver.rule.check.zh: 当任意 span 缺少 `gen_ai.operation.name` 时命中。
# @weaver.rule.fix.zh: 在每个 span 上补充 GenAI 操作类型；CHAT/LLM 通常为 `chat`，TOOL 通常为 `execute_tool`。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: user.id, ant.username, gen_ai.conversation.id, gen_ai.chat.id, gen_ai.operation.name, gen_ai.span.kind
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "span_missing_operation_name",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Span '%s': Missing required 'gen_ai.operation.name' in span attributes. All spans must carry operation identity.", [input.sample.span.name])
) if {
    has_span
    not has_span_attr("gen_ai.operation.name")
}

# @weaver.rule.zh: 要求所有 span attributes 必须包含 `gen_ai.span.kind`，保证 trace 内所有 span 都能按语义类型归类。
# @weaver.rule.check.zh: 当任意 span 缺少 `gen_ai.span.kind` 时命中。
# @weaver.rule.fix.zh: 在每个 span 上补充 span kind，例如 `CHAT`、`LLM`、`TOOL`、`COMPACTION` 或 `COMMON`。
# @weaver.rule.entity: span:common
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: user.id, ant.username, gen_ai.conversation.id, gen_ai.chat.id, gen_ai.operation.name, gen_ai.span.kind
# @weaver.rule.subject: field:gen_ai.span.kind
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "span_missing_span_kind",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Span '%s': Missing required 'gen_ai.span.kind' in span attributes. All spans must carry semantic span kind.", [input.sample.span.name])
) if {
    has_span
    not has_span_attr("gen_ai.span.kind")
}
