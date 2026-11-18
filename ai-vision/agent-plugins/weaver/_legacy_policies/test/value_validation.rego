# Ant AI Agent Semconv v2.0 - Value Validation Policies
#
# Validates that enum/controlled-vocabulary attributes have valid values
# per the semconv specification.

package live_check_advice

import rego.v1

#==========================================
# gen_ai.span.kind - Valid Ant span types
#==========================================

valid_span_kinds := {"CHAT", "AGENT", "LLM", "TOOL", "MCP", "RETRIEVER", "RERANKER", "EMBEDDING", "GUARDRAIL"}

deny contains make_advice(
    "invalid_span_kind",
    "violation",
    {"span_name": input.sample.span.name, "value": kind},
    sprintf("Span '%s': Invalid gen_ai.span.kind value '%s'. Valid: CHAT, AGENT, LLM, TOOL, MCP, RETRIEVER, RERANKER, EMBEDDING, GUARDRAIL.", [input.sample.span.name, kind])
) if {
    has_span
    kind := get_span_attr("gen_ai.span.kind")
    not kind in valid_span_kinds
}

#==========================================
# gen_ai.provider.name - Known providers
#==========================================

valid_providers := {"anthropic", "openai", "qwen", "alibaba", "aws.bedrock", "deepseek", "azure.ai.openai", "gcp.gemini"}

deny contains make_advice(
    "unknown_provider_name",
    "improvement",
    {"span_name": input.sample.span.name, "value": provider},
    sprintf("Span '%s': Unknown gen_ai.provider.name '%s'. Known: anthropic, openai, qwen, alibaba, aws.bedrock, deepseek.", [input.sample.span.name, provider])
) if {
    has_span
    provider := get_span_attr("gen_ai.provider.name")
    not provider in valid_providers
}

#==========================================
# gen_ai.system - Known LLM systems
#==========================================

valid_systems := {"anthropic", "openai", "qwen", "alibaba", "azure.ai.openai", "gcp.gemini", "aws.bedrock", "deepseek"}

deny contains make_advice(
    "unknown_gen_ai_system",
    "improvement",
    {"span_name": input.sample.span.name, "value": system},
    sprintf("Span '%s': Unknown gen_ai.system value '%s'. Known: anthropic, openai, qwen, alibaba, aws.bedrock, deepseek.", [input.sample.span.name, system])
) if {
    has_span
    system := get_span_attr("gen_ai.system")
    not system in valid_systems
}

#==========================================
# gen_ai.operation.name - Valid operations
#==========================================

valid_operations := {"chat", "invoke_agent", "execute_tool", "embeddings", "create_agent", "retrieval"}

deny contains make_advice(
    "unknown_operation_name",
    "improvement",
    {"span_name": input.sample.span.name, "value": op},
    sprintf("Span '%s': Unknown gen_ai.operation.name '%s'. Known: chat, invoke_agent, execute_tool, embeddings, create_agent, retrieval.", [input.sample.span.name, op])
) if {
    has_span
    op := get_span_attr("gen_ai.operation.name")
    not op in valid_operations
}

#==========================================
# gen_ai.token.type - Valid token types
#==========================================

valid_token_types := {"input", "output", "cache_creation", "cache_read"}

deny contains make_advice(
    "invalid_token_type",
    "violation",
    {"span_name": input.sample.span.name, "value": tt},
    sprintf("Span '%s': Invalid gen_ai.token.type '%s'. Valid: input, output, cache_creation, cache_read.", [input.sample.span.name, tt])
) if {
    has_span
    tt := get_span_attr("gen_ai.token.type")
    not tt in valid_token_types
}

#==========================================
# Numeric validations
#==========================================

# Negative input tokens
deny contains make_advice(
    "negative_input_tokens",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Span '%s': gen_ai.usage.input_tokens must not be negative.", [input.sample.span.name])
) if {
    has_span
    tokens := get_span_attr_int("gen_ai.usage.input_tokens")
    tokens < 0
}

# Negative output tokens
deny contains make_advice(
    "negative_output_tokens",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Span '%s': gen_ai.usage.output_tokens must not be negative.", [input.sample.span.name])
) if {
    has_span
    tokens := get_span_attr_int("gen_ai.usage.output_tokens")
    tokens < 0
}

# Negative total tokens
deny contains make_advice(
    "negative_total_tokens",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Span '%s': gen_ai.usage.total_tokens must not be negative.", [input.sample.span.name])
) if {
    has_span
    tokens := get_span_attr_int("gen_ai.usage.total_tokens")
    tokens < 0
}

#==========================================
# ant.agent.type - Known agent types
#==========================================

valid_agent_types := {"claudecode", "opencode", "openclaw"}

deny contains make_advice(
    "unknown_agent_type",
    "improvement",
    {"value": agent_type},
    sprintf("Unknown ant.agent.type '%s'. Known: claudecode, opencode, openclaw.", [agent_type])
) if {
    has_span
    agent_type := get_span_attr("ant.agent.type")
    not agent_type in valid_agent_types
}

# Also check resource-level ant.agent.type
deny contains make_advice(
    "resource_unknown_agent_type",
    "improvement",
    {"value": agent_type},
    sprintf("Resource: Unknown ant.agent.type '%s'. Known: claudecode, opencode, openclaw.", [agent_type])
) if {
    has_resource
    agent_type := get_resource_attr("ant.agent.type")
    not agent_type in valid_agent_types
}

#==========================================
# guardrail.action - Valid actions
#==========================================

valid_guardrail_actions := {"pass", "block"}

deny contains make_advice(
    "invalid_guardrail_action",
    "violation",
    {"span_name": input.sample.span.name, "value": action},
    sprintf("GUARDRAIL span '%s': Invalid guardrail.action '%s'. Valid: pass, block.", [input.sample.span.name, action])
) if {
    is_span_kind("GUARDRAIL")
    action := get_span_attr("guardrail.action")
    not action in valid_guardrail_actions
}

#==========================================
# mcp.request_type - Valid MCP types
#==========================================

valid_mcp_types := {"tools/list", "tools/call", "resources/list", "resources/read", "prompts/list", "prompts/get"}

deny contains make_advice(
    "unknown_mcp_request_type",
    "improvement",
    {"span_name": input.sample.span.name, "value": req_type},
    sprintf("MCP span '%s': Unknown mcp.request_type '%s'. Known: tools/list, tools/call, resources/list, resources/read, prompts/list, prompts/get.", [input.sample.span.name, req_type])
) if {
    is_span_kind("MCP")
    req_type := get_span_attr("mcp.request_type")
    not req_type in valid_mcp_types
}
