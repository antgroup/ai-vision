# AI Agent Hooks - Live Check Policies
# Version: 0.1.0
#
# These policies validate OTLP telemetry data against the semantic conventions
# defined in weaver/semconv/
#
# Weaver live-check provides the span directly as `input`:
#   input - the span object being validated
#   input.attributes - span attributes
#   input.name - span name
#   input.parentSpanId - parent span ID (empty for root spans)

package ai_agent_hooks

import future.keywords.if
import future.keywords.contains
import future.keywords.in

# ==========================================
# Policy Violations
# ==========================================

# Violation: Missing required session.id attribute on root spans
violation_missing_session_id if {
    is_root_span
    not has_attribute("session.id")
}

# Violation: Missing required turn_number attribute on root spans
violation_missing_turn_number if {
    is_root_span
    not has_attribute("turn_number")
}

# Violation: Missing required source attribute on root spans
violation_missing_source if {
    is_root_span
    not has_attribute("source")
}

# Violation: Missing required user.name attribute
violation_missing_username if {
    is_root_span
    # Accept both user.name and ant.username (backward compatibility)
    not has_attribute("user.name")
    not has_attribute("ant.username")
}

# Violation: Invalid source value
violation_invalid_source if {
    source := get_attribute("source")
    source != null
    not source in ["claude-code", "codex", "cursor", "aider", "openclaw", "opencode"]
}

# Violation: Invalid gen_ai.system value in child spans
violation_invalid_gen_ai_system if {
    is_child_span
    gen_ai_system := get_attribute("gen_ai.system")
    gen_ai_system != null
    not gen_ai_system in ["anthropic", "openai", "azure.ai.openai", "gcp.gemini", "aws.bedrock"]
}

# Violation: Negative token usage
violation_negative_tokens if {
    input_tokens := get_attribute_int("gen_ai.usage.input_tokens")
    input_tokens != null
    input_tokens < 0
}

violation_negative_output_tokens if {
    output_tokens := get_attribute_int("gen_ai.usage.output_tokens")
    output_tokens != null
    output_tokens < 0
}

# Violation: tool_count is negative
violation_negative_tool_count if {
    tool_count := get_attribute_int("tool_count")
    tool_count != null
    tool_count < 0
}

# ==========================================
# Warnings (Non-critical issues)
# ==========================================

# Warning: Missing gen_ai.request.model on generation spans
warning_missing_model if {
    is_child_span
    has_attribute("gen_ai.system")
    not has_attribute("gen_ai.request.model")
}

# Warning: Missing token usage on generation spans
warning_missing_token_usage if {
    is_child_span
    has_attribute("gen_ai.system")
    not has_attribute("gen_ai.usage.input_tokens")
    not has_attribute("gen_ai.usage.output_tokens")
}

# Warning: service.name missing (now injected into attributes)
warning_missing_service_name if {
    not has_attribute("service.name")
}

# Warning: Missing telemetry SDK info
warning_missing_telemetry_info if {
    not has_attribute("telemetry.sdk.name")
}

# ==========================================
# Helper Functions
# ==========================================

# Check if this is a root span (no parent)
is_root_span if {
    input.parentSpanId == ""
}

# Check if this is a child span (has parent)
is_child_span if {
    input.parentSpanId != ""
}

# Check if span has an attribute with given key
has_attribute(key) if {
    some attr in input.attributes
    attr.key == key
}

# Get span attribute value as string
get_attribute(key) = value if {
    some attr in input.attributes
    attr.key == key
    value := attr.value.stringValue
}

# Get span attribute value as integer
get_attribute_int(key) = value if {
    some attr in input.attributes
    attr.key == key
    # Values are stored as strings in this implementation
    value_str := attr.value.stringValue
    value := to_number(value_str)
}

# Alternative: try to get int value directly
get_attribute_int(key) = value if {
    some attr in input.attributes
    attr.key == key
    value := attr.value.intValue
}

# ==========================================
# Aggregated Results
# ==========================================

# All violations
violations contains msg if {
    violation_missing_session_id
    msg := sprintf("Span '%s': Missing required attribute 'session.id' on root span", [input.name])
}

violations contains msg if {
    violation_missing_turn_number
    msg := sprintf("Span '%s': Missing required attribute 'turn_number' on root span", [input.name])
}

violations contains msg if {
    violation_missing_source
    msg := sprintf("Span '%s': Missing required attribute 'source' on root span", [input.name])
}

violations contains msg if {
    violation_missing_username
    msg := sprintf("Span '%s': Missing required attribute 'user.name' (or 'ant.username') on root span", [input.name])
}

violations contains msg if {
    violation_invalid_source
    msg := sprintf("Span '%s': Invalid source value '%s'", [input.name, get_attribute("source")])
}

violations contains msg if {
    violation_invalid_gen_ai_system
    msg := sprintf("Span '%s': Invalid gen_ai.system value '%s'", [input.name, get_attribute("gen_ai.system")])
}

violations contains msg if {
    violation_negative_tokens
    msg := sprintf("Span '%s': Negative gen_ai.usage.input_tokens value", [input.name])
}

violations contains msg if {
    violation_negative_output_tokens
    msg := sprintf("Span '%s': Negative gen_ai.usage.output_tokens value", [input.name])
}

violations contains msg if {
    violation_negative_tool_count
    msg := sprintf("Span '%s': Negative tool_count value", [input.name])
}

# All warnings
warnings contains msg if {
    warning_missing_model
    msg := sprintf("Span '%s': Missing recommended attribute 'gen_ai.request.model' on generation span", [input.name])
}

warnings contains msg if {
    warning_missing_token_usage
    msg := sprintf("Span '%s': Missing token usage attributes on generation span", [input.name])
}

warnings contains msg if {
    warning_missing_service_name
    msg := sprintf("Span '%s': Missing 'service.name' attribute", [input.name])
}

warnings contains msg if {
    warning_missing_telemetry_info
    msg := sprintf("Span '%s': Missing telemetry SDK identification", [input.name])
}

# Overall pass/fail
pass if {
    count(violations) == 0
}