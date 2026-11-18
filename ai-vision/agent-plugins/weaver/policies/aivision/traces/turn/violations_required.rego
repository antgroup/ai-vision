# Turn span required-field rules.
# Scope: gen_ai.span.kind=CHAT or legacy "Turn N" spans.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 要求 Turn span 的 `gen_ai.span.kind` 值固定为 `CHAT`。
# @weaver.rule.check.zh: 当 Turn span 上 `gen_ai.span.kind` 存在但不是 `CHAT` 时命中。
# @weaver.rule.fix.zh: 将 Turn/chat 入口 span 的 `gen_ai.span.kind` 改为 `CHAT`；LLM/TOOL/COMPACTION 使用对应 span 类型。
# @weaver.rule.entity: span:turn
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: gen_ai.span.kind
# @weaver.rule.subject: field:gen_ai.span.kind
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: CHAT
deny contains make_advice(
    "turn_invalid_span_kind",
    "violation",
    {"span_name": input.sample.span.name, "actual_value": get_span_attr("gen_ai.span.kind"), "expected_value": "CHAT"},
    sprintf("Turn span '%s': gen_ai.span.kind must be 'CHAT'.", [input.sample.span.name])
) if {
    is_turn_span
    has_span_attr("gen_ai.span.kind")
    get_span_attr("gen_ai.span.kind") != "CHAT"
}

# @weaver.rule.zh: 要求 Turn span 的 `gen_ai.operation.name` 值固定为 `invoke_agent`。
# @weaver.rule.check.zh: 当 Turn span 上 `gen_ai.operation.name` 存在但不是 `invoke_agent` 时命中。
# @weaver.rule.fix.zh: 将 Turn/root 入口 span 的 `gen_ai.operation.name` 改为 `invoke_agent`；LLM 子 span 使用 `chat`。
# @weaver.rule.entity: span:turn
# @weaver.rule.category.zh: 固定值
# @weaver.rule.fields: gen_ai.operation.name
# @weaver.rule.subject: field:gen_ai.operation.name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: invoke_agent
deny contains make_advice(
    "turn_invalid_operation_name",
    "violation",
    {"span_name": input.sample.span.name, "actual_value": get_span_attr("gen_ai.operation.name"), "expected_value": "invoke_agent"},
    sprintf("Turn span '%s': gen_ai.operation.name must be 'invoke_agent'.", [input.sample.span.name])
) if {
    is_turn_span
    has_span_attr("gen_ai.operation.name")
    get_span_attr("gen_ai.operation.name") != "invoke_agent"
}

# @weaver.rule.zh: 要求 Turn span 包含 `agentic.runtime.name`，标识 agent runtime。
# @weaver.rule.check.zh: 当 Turn span 缺少 `agentic.runtime.name` 时命中。
# @weaver.rule.fix.zh: 在 Turn span 上补充 runtime 名称，例如 codex、opencode、claude-code。
# @weaver.rule.entity: span:turn
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.runtime.name, agentic.turn.number, gen_ai.chat.id, span.name
# @weaver.rule.subject: field:agentic.runtime.name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "turn_missing_source",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Turn span '%s': Missing required 'agentic.runtime.name' attribute.", [input.sample.span.name])
) if {
    is_turn_span
    not has_span_attr("agentic.runtime.name")
}

# @weaver.rule.zh: 要求 Turn span 包含 `agentic.turn.number`，用于 turn 顺序分析。
# @weaver.rule.check.zh: 当 Turn span 缺少 `agentic.turn.number` 时命中。
# @weaver.rule.fix.zh: 从会话内 turn 计数生成该字段，确保从 1 开始递增或遵循 agent 原生序号。
# @weaver.rule.entity: span:turn
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.runtime.name, agentic.turn.number, gen_ai.chat.id, span.name
# @weaver.rule.subject: field:agentic.turn.number
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "turn_missing_turn_number",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Turn span '%s': Missing required 'agentic.turn.number' attribute.", [input.sample.span.name])
) if {
    is_turn_span
    not has_span_attr("agentic.turn.number")
}

# @weaver.rule.zh: 要求 ClaudeCode/Codex Turn span 包含 `agentic.turn.trigger`，记录该轮从 transcript 观察到的触发机制。
# @weaver.rule.check.zh: 当 `agentic.runtime.name` 为 claude-code 或 codex 的 Turn span 缺少 `agentic.turn.trigger` 时命中。
# @weaver.rule.fix.zh: 在 Turn span 上补充规范值；缺少可靠原生证据时填 `unknown`。
# @weaver.rule.entity: span:turn
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: agentic.turn.trigger
# @weaver.rule.subject: field:agentic.turn.trigger
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: required_presence
deny contains make_advice(
    "turn_missing_trigger",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Turn span '%s': Missing required 'agentic.turn.trigger' attribute.", [input.sample.span.name])
) if {
    is_turn_span
    get_span_attr("agentic.runtime.name") in trigger_required_turn_runtime_names
    not has_span_attr("agentic.turn.trigger")
}

# @weaver.rule.zh: 限制 Turn span 的 `agentic.turn.trigger` 只能使用规范触发机制枚举。
# @weaver.rule.check.zh: 当 Turn span 上的 `agentic.turn.trigger` 不在允许集合内时命中。
# @weaver.rule.fix.zh: 改为 interactive、programmatic、queued、system、subagent 或 unknown；不要用 automation 表示单轮触发机制。
# @weaver.rule.entity: span:turn
# @weaver.rule.category.zh: 枚举值
# @weaver.rule.fields: agentic.turn.trigger
# @weaver.rule.subject: field:agentic.turn.trigger
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: enum
# @weaver.rule.allowed_values: interactive, programmatic, queued, system, subagent, unknown
deny contains make_advice(
    "turn_invalid_trigger",
    "violation",
    {"span_name": input.sample.span.name, "value": turn_trigger},
    sprintf("Turn span '%s': Invalid agentic.turn.trigger '%s'. Valid values: interactive, programmatic, queued, system, subagent, unknown.", [input.sample.span.name, turn_trigger])
) if {
    is_turn_span
    turn_trigger := get_span_attr("agentic.turn.trigger")
    not turn_trigger in valid_turn_triggers
}
