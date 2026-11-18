# AI Agent Hooks - Live Check Policies
# Version: 0.2.0
#
# Weaver live-check 策略格式说明:
# - 包名必须是 `live_check_advice` 或其他非 `data` 开头的名称
# - 输入格式: input.sample.span, input.sample.attribute 等
# - 使用 `deny` 规则输出 `make_advice()` 格式的建议
# - advice_level: violation | improvement | information

package live_check_advice

import rego.v1

#==========================================
# Helper Functions
#==========================================

make_advice(advice_type, advice_level, advice_context, message) := {
    "type": "advice",
    "advice_type": advice_type,
    "advice_level": advice_level,
    "advice_context": advice_context,
    "message": message,
}

# Check if attribute exists in span
has_attr(name) if {
    some attr in input.sample.span.attributes
    attr.name == name
}

# Get attribute value by name
get_attr(name) = value if {
    some attr in input.sample.span.attributes
    attr.name == name
    value := attr.value
}

# Check if this is a root span (AI agent turn span)
is_root_span if {
    input.sample.span
    input.sample.span.name == "ai_agent.turn"
}

# Check if this is a generation span
is_generation_span if {
    input.sample.span
    input.sample.span.name == "ai_agent.generation"
}

#==========================================
# Violations - Root Span Required Attributes
#==========================================

# Missing session.id on root span
deny contains make_advice("missing_session_id", "violation", {"span_name": input.sample.span.name}, msg) if {
    is_root_span
    not has_attr("session.id")
    msg := sprintf("Span '%s': Missing required attribute 'session.id' on root span", [input.sample.span.name])
}

# Missing turn_number on root span
deny contains make_advice("missing_turn_number", "violation", {"span_name": input.sample.span.name}, msg) if {
    is_root_span
    not has_attr("turn_number")
    msg := sprintf("Span '%s': Missing required attribute 'turn_number' on root span", [input.sample.span.name])
}

# Missing source on root span
deny contains make_advice("missing_source", "violation", {"span_name": input.sample.span.name}, msg) if {
    is_root_span
    not has_attr("source")
    msg := sprintf("Span '%s': Missing required attribute 'source' on root span", [input.sample.span.name])
}

# Missing user.name on root span
deny contains make_advice("missing_username", "violation", {"span_name": input.sample.span.name}, msg) if {
    is_root_span
    not has_attr("user.name")
    not has_attr("ant.username")
    msg := sprintf("Span '%s': Missing required attribute 'user.name' on root span", [input.sample.span.name])
}

#==========================================
# Violations - Invalid Values
#==========================================

# Invalid source value
deny contains make_advice("invalid_source", "violation", {"span_name": input.sample.span.name, "source": get_attr("source")}, msg) if {
    input.sample.span
    has_attr("source")
    source := get_attr("source")
    not source in ["claude-code", "codex", "cursor", "aider", "openclaw", "opencode"]
    msg := sprintf("Span '%s': Invalid source value '%s'", [input.sample.span.name, source])
}

# Invalid gen_ai.system value
deny contains make_advice("invalid_gen_ai_system", "violation", {"span_name": input.sample.span.name, "gen_ai_system": get_attr("gen_ai.system")}, msg) if {
    is_generation_span
    has_attr("gen_ai.system")
    gen_ai_system := get_attr("gen_ai.system")
    not gen_ai_system in ["anthropic", "openai", "azure.ai.openai", "gcp.gemini", "aws.bedrock"]
    msg := sprintf("Span '%s': Invalid gen_ai.system value '%s'", [input.sample.span.name, gen_ai_system])
}

# Negative token usage
deny contains make_advice("negative_tokens", "violation", {"span_name": input.sample.span.name}, msg) if {
    input.sample.span
    has_attr("gen_ai.usage.input_tokens")
    tokens := get_attr("gen_ai.usage.input_tokens")
    tokens < 0
    msg := sprintf("Span '%s': Negative gen_ai.usage.input_tokens value", [input.sample.span.name])
}

deny contains make_advice("negative_output_tokens", "violation", {"span_name": input.sample.span.name}, msg) if {
    input.sample.span
    has_attr("gen_ai.usage.output_tokens")
    tokens := get_attr("gen_ai.usage.output_tokens")
    tokens < 0
    msg := sprintf("Span '%s': Negative gen_ai.usage.output_tokens value", [input.sample.span.name])
}

#==========================================
# Warnings - Missing Recommended Attributes
#==========================================

# Missing model on generation span
deny contains make_advice("missing_model", "improvement", {"span_name": input.sample.span.name}, msg) if {
    is_generation_span
    has_attr("gen_ai.system")
    not has_attr("gen_ai.request.model")
    msg := sprintf("Span '%s': Missing recommended attribute 'gen_ai.request.model' on generation span", [input.sample.span.name])
}

# Missing token usage on generation span
deny contains make_advice("missing_token_usage", "improvement", {"span_name": input.sample.span.name}, msg) if {
    is_generation_span
    has_attr("gen_ai.system")
    not has_attr("gen_ai.usage.input_tokens")
    not has_attr("gen_ai.usage.output_tokens")
    msg := sprintf("Span '%s': Missing token usage attributes on generation span", [input.sample.span.name])
}