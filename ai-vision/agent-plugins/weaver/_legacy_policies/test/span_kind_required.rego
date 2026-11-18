# Ant AI Agent Semconv v2.0 - Span Kind Required Attributes
#
# Validates required attributes per gen_ai.span.kind value.
# Based on spans.yaml span definitions.

package live_check_advice

import rego.v1

#==========================================
# Common: All spans must have these
#==========================================

# ant.username required on all spans
deny contains make_advice(
    "span_missing_ant_username",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Span '%s': Missing required attribute 'ant.username'.", [input.sample.span.name])
) if {
    has_span
    not has_span_attr("ant.username")
}

# ant.agent.type required on all spans
deny contains make_advice(
    "span_missing_ant_agent_type",
    "violation",
    {"span_name": input.sample.span.name},
    sprintf("Span '%s': Missing required attribute 'ant.agent.type'.", [input.sample.span.name])
) if {
    has_span
    not has_span_attr("ant.agent.type")
}

#==========================================
# CHAT span (span.ant.chat)
#==========================================

# CHAT: gen_ai.conversation.id required
deny contains make_advice(
    "chat_missing_conversation_id",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "CHAT"},
    sprintf("CHAT span '%s': Missing required attribute 'gen_ai.conversation.id'.", [input.sample.span.name])
) if {
    is_span_kind("CHAT")
    not has_span_attr("gen_ai.conversation.id")
}

# CHAT: gen_ai.chat.id required
deny contains make_advice(
    "chat_missing_chat_id",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "CHAT"},
    sprintf("CHAT span '%s': Missing required attribute 'gen_ai.chat.id'.", [input.sample.span.name])
) if {
    is_span_kind("CHAT")
    not has_span_attr("gen_ai.chat.id")
}

# CHAT: gen_ai.span.kind must be "CHAT"
deny contains make_advice(
    "chat_missing_span_kind",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "CHAT"},
    sprintf("CHAT span '%s': Missing required attribute 'gen_ai.span.kind'.", [input.sample.span.name])
) if {
    is_span_kind("CHAT")
    not has_span_attr("gen_ai.span.kind")
}

#==========================================
# AGENT span (span.ant.agent)
#==========================================

# AGENT: gen_ai.agent.id required
deny contains make_advice(
    "agent_missing_agent_id",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "AGENT"},
    sprintf("AGENT span '%s': Missing required attribute 'gen_ai.agent.id'.", [input.sample.span.name])
) if {
    is_span_kind("AGENT")
    not has_span_attr("gen_ai.agent.id")
}

# AGENT: gen_ai.agent.name required
deny contains make_advice(
    "agent_missing_agent_name",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "AGENT"},
    sprintf("AGENT span '%s': Missing required attribute 'gen_ai.agent.name'.", [input.sample.span.name])
) if {
    is_span_kind("AGENT")
    not has_span_attr("gen_ai.agent.name")
}

# AGENT: gen_ai.response.time_to_first_token required
deny contains make_advice(
    "agent_missing_ttft",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "AGENT"},
    sprintf("AGENT span '%s': Missing required attribute 'gen_ai.response.time_to_first_token'.", [input.sample.span.name])
) if {
    is_span_kind("AGENT")
    not has_span_attr("gen_ai.response.time_to_first_token")
}

#==========================================
# LLM span (span.ant.llm)
#==========================================

# LLM: gen_ai.system required
deny contains make_advice(
    "llm_missing_gen_ai_system",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "LLM"},
    sprintf("LLM span '%s': Missing required attribute 'gen_ai.system'.", [input.sample.span.name])
) if {
    is_span_kind("LLM")
    not has_span_attr("gen_ai.system")
}

# LLM: gen_ai.request.model required
deny contains make_advice(
    "llm_missing_request_model",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "LLM"},
    sprintf("LLM span '%s': Missing required attribute 'gen_ai.request.model'.", [input.sample.span.name])
) if {
    is_span_kind("LLM")
    not has_span_attr("gen_ai.request.model")
}

# LLM: token usage recommended
deny contains make_advice(
    "llm_missing_token_usage",
    "improvement",
    {"span_name": input.sample.span.name, "span_kind": "LLM"},
    sprintf("LLM span '%s': Missing token usage attributes (gen_ai.usage.input_tokens / output_tokens).", [input.sample.span.name])
) if {
    is_span_kind("LLM")
    not has_span_attr("gen_ai.usage.input_tokens")
    not has_span_attr("gen_ai.usage.output_tokens")
}

#==========================================
# MCP span (span.ant.mcp)
#==========================================

# MCP: mcp.server_id required
deny contains make_advice(
    "mcp_missing_server_id",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "MCP"},
    sprintf("MCP span '%s': Missing required attribute 'mcp.server_id'.", [input.sample.span.name])
) if {
    is_span_kind("MCP")
    not has_span_attr("mcp.server_id")
}

# MCP: mcp.request_type required
deny contains make_advice(
    "mcp_missing_request_type",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "MCP"},
    sprintf("MCP span '%s': Missing required attribute 'mcp.request_type'.", [input.sample.span.name])
) if {
    is_span_kind("MCP")
    not has_span_attr("mcp.request_type")
}

#==========================================
# RETRIEVER span (span.ant.retriever)
#==========================================

# RETRIEVER: retrieval.kind required
deny contains make_advice(
    "retriever_missing_kind",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "RETRIEVER"},
    sprintf("RETRIEVER span '%s': Missing required attribute 'retrieval.kind'.", [input.sample.span.name])
) if {
    is_span_kind("RETRIEVER")
    not has_span_attr("retrieval.kind")
}

# RETRIEVER: retrieval.system required
deny contains make_advice(
    "retriever_missing_system",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "RETRIEVER"},
    sprintf("RETRIEVER span '%s': Missing required attribute 'retrieval.system'.", [input.sample.span.name])
) if {
    is_span_kind("RETRIEVER")
    not has_span_attr("retrieval.system")
}

# RETRIEVER: retrieval.request.top_k required
deny contains make_advice(
    "retriever_missing_top_k",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "RETRIEVER"},
    sprintf("RETRIEVER span '%s': Missing required attribute 'retrieval.request.top_k'.", [input.sample.span.name])
) if {
    is_span_kind("RETRIEVER")
    not has_span_attr("retrieval.request.top_k")
}

#==========================================
# RERANKER span (span.ant.reranker)
#==========================================

# RERANKER: reranker.model_name required
deny contains make_advice(
    "reranker_missing_model_name",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "RERANKER"},
    sprintf("RERANKER span '%s': Missing required attribute 'reranker.model_name'.", [input.sample.span.name])
) if {
    is_span_kind("RERANKER")
    not has_span_attr("reranker.model_name")
}

#==========================================
# GUARDRAIL span (span.ant.guardrail)
#==========================================

# GUARDRAIL: guardrail.input required
deny contains make_advice(
    "guardrail_missing_input",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "GUARDRAIL"},
    sprintf("GUARDRAIL span '%s': Missing required attribute 'guardrail.input'.", [input.sample.span.name])
) if {
    is_span_kind("GUARDRAIL")
    not has_span_attr("guardrail.input")
}

# GUARDRAIL: guardrail.result required
deny contains make_advice(
    "guardrail_missing_result",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "GUARDRAIL"},
    sprintf("GUARDRAIL span '%s': Missing required attribute 'guardrail.result'.", [input.sample.span.name])
) if {
    is_span_kind("GUARDRAIL")
    not has_span_attr("guardrail.result")
}

# GUARDRAIL: guardrail.action required
deny contains make_advice(
    "guardrail_missing_action",
    "violation",
    {"span_name": input.sample.span.name, "span_kind": "GUARDRAIL"},
    sprintf("GUARDRAIL span '%s': Missing required attribute 'guardrail.action'.", [input.sample.span.name])
) if {
    is_span_kind("GUARDRAIL")
    not has_span_attr("guardrail.action")
}
