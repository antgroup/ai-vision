package live_check_advice

import rego.v1

# ==========================================
# GenAI semantic contract
# Based on docs/semconv/attribute_groups.yaml
# Only the reserved `gen_ai.*` namespace is strict.
# Agent-specific custom fields should live under other namespaces.
# ==========================================

allowed_gen_ai_attrs := {
    "gen_ai.agent.description",
    "gen_ai.agent.id",
    "gen_ai.agent.name",
    "gen_ai.agent.version",
    "gen_ai.chat.id",
    "gen_ai.conversation.id",
    "gen_ai.data_source.id",
    "gen_ai.embeddings.dimension.count",
    "gen_ai.evaluation.explanation",
    "gen_ai.evaluation.name",
    "gen_ai.evaluation.score.label",
    "gen_ai.evaluation.score.value",
    "gen_ai.input.messages",
    "gen_ai.operation.name",
    "gen_ai.output.messages",
    "gen_ai.output.type",
    "gen_ai.provider.name",
    "gen_ai.request.choice.count",
    "gen_ai.request.encoding_formats",
    "gen_ai.request.frequency_penalty",
    "gen_ai.request.max_tokens",
    "gen_ai.request.model",
    "gen_ai.request.presence_penalty",
    "gen_ai.request.seed",
    "gen_ai.request.stop_sequences",
    "gen_ai.request.temperature",
    "gen_ai.request.top_k",
    "gen_ai.request.top_p",
    "gen_ai.response.finish_reasons",
    "gen_ai.response.id",
    "gen_ai.response.model",
    "gen_ai.response.time_to_first_token",
    "gen_ai.retrieval.documents",
    "gen_ai.retrieval.query.text",
    "gen_ai.span.kind",
    "gen_ai.system",
    "gen_ai.system_instructions",
    "gen_ai.token.type",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.id",
    "gen_ai.tool.call.result",
    "gen_ai.tool.definitions",
    "gen_ai.tool.description",
    "gen_ai.tool.name",
    "gen_ai.tool.type",
    "gen_ai.usage.cache_creation.input_tokens",
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.details.cache_creation_input_tokens",
    "gen_ai.usage.details.cache_hit_rate",
    "gen_ai.usage.details.cache_read_input_tokens",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.total_tokens",
}

deprecated_gen_ai_replacements := {
    "gen_ai.completion": "gen_ai.output.messages",
    "gen_ai.model": "gen_ai.request.model / gen_ai.response.model",
    "gen_ai.prompt": "gen_ai.input.messages",
    "gen_ai.request.is_stream": "",
    "gen_ai.request.model_id": "gen_ai.request.model",
    "gen_ai.response.finish_reason": "gen_ai.response.finish_reasons",
    "gen_ai.tool.call.parameters": "gen_ai.tool.call.arguments",
    "gen_ai.usage.cache_creation_input_tokens": "gen_ai.usage.cache_creation.input_tokens",
    "gen_ai.usage.cache_read_input_tokens": "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_read_tokens": "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_write_tokens": "gen_ai.usage.cache_creation.input_tokens",
    "gen_ai.usage.cached_input_tokens": "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.reasoning_output_tokens": "",
}

deprecated_non_gen_ai_replacements := {
    "llm.model": "gen_ai.request.model",
    "llm.provider": "gen_ai.provider.name",
    "llm.system": "gen_ai.system / gen_ai.provider.name",
}

non_negative_gen_ai_numeric_attrs := {
    "gen_ai.response.time_to_first_token",
    "gen_ai.usage.cache_creation.input_tokens",
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.details.cache_creation_input_tokens",
    "gen_ai.usage.details.cache_hit_rate",
    "gen_ai.usage.details.cache_read_input_tokens",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.total_tokens",
}

deprecated_gen_ai_attr(attr_name) if {
    deprecated_gen_ai_replacements[attr_name]
}

tool_payload_attr_present if {
    span_attr_exists("gen_ai.tool.call.arguments")
}

tool_payload_attr_present if {
    span_attr_exists("gen_ai.tool.call.result")
}

deny contains make_advice(
    "deprecated_gen_ai_attribute",
    "violation",
    {"attribute": attr_name, "replacement": replacement},
    deprecated_gen_ai_message(attr_name, replacement)
) if {
    has_span_sample
    some attr_name
    all_span_attr_names[attr_name]
    replacement := deprecated_gen_ai_replacements[attr_name]
}

deny contains make_advice(
    "deprecated_gen_ai_alias_attribute",
    "violation",
    {"attribute": attr_name, "replacement": replacement},
    sprintf(
        "Span '%s': Deprecated alias attribute '%s' detected. Use '%s' instead.",
        [span_name, attr_name, replacement]
    )
) if {
    has_span_sample
    some attr_name
    all_span_attr_names[attr_name]
    replacement := deprecated_non_gen_ai_replacements[attr_name]
}

deny contains make_advice(
    "unknown_gen_ai_attribute",
    "violation",
    {"attribute": attr_name},
    sprintf(
        "Span '%s': Unknown gen_ai attribute '%s'. Custom extensions must not use the reserved 'gen_ai.*' namespace; use 'ant.*', 'code.*', 'mcp.*' or an agent-specific namespace instead.",
        [span_name, attr_name]
    )
) if {
    has_span_sample
    some attr_name
    all_span_attr_names[attr_name]
    startswith(attr_name, "gen_ai.")
    not allowed_gen_ai_attrs[attr_name]
    not deprecated_gen_ai_attr(attr_name)
}

deny contains make_advice(
    "gen_ai_signal_missing_ant_username",
    "violation",
    {"span_name": span_name},
    sprintf("Span '%s': Signals carrying gen_ai semantics must include 'ant.username'.", [span_name])
) if {
    has_span_sample
    some attr_name
    all_span_attr_names[attr_name]
    startswith(attr_name, "gen_ai.")
    not span_attr_exists("ant.username")
}

deny contains make_advice(
    "gen_ai_execute_tool_missing_tool_name",
    "violation",
    {"span_name": span_name},
    sprintf("Span '%s': gen_ai.operation.name='execute_tool' requires 'gen_ai.tool.name'.", [span_name])
) if {
    has_span_sample
    get_span_attr("gen_ai.operation.name") == "execute_tool"
    not span_attr_exists("gen_ai.tool.name")
}

deny contains make_advice(
    "gen_ai_tool_payload_missing_call_id",
    "violation",
    {"span_name": span_name},
    sprintf("Span '%s': 'gen_ai.tool.call.arguments' or 'gen_ai.tool.call.result' requires 'gen_ai.tool.call.id'.", [span_name])
) if {
    has_span_sample
    tool_payload_attr_present
    not span_attr_exists("gen_ai.tool.call.id")
}

deny contains make_advice(
    "gen_ai_negative_numeric_value",
    "violation",
    {"span_name": span_name, "attribute": attr_name},
    sprintf("Span '%s': Attribute '%s' must not be negative.", [span_name, attr_name])
) if {
    has_span_sample
    some attr_name
    non_negative_gen_ai_numeric_attrs[attr_name]
    span_attr_exists(attr_name)
    span_attr_number(attr_name) < 0
}

deny contains make_advice(
    "gen_ai_usage_total_mismatch",
    "violation",
    {
        "span_name": span_name,
        "input_tokens": span_attr_number("gen_ai.usage.input_tokens"),
        "output_tokens": span_attr_number("gen_ai.usage.output_tokens"),
        "total_tokens": span_attr_number("gen_ai.usage.total_tokens"),
    },
    sprintf(
        "Span '%s': 'gen_ai.usage.total_tokens' must equal input_tokens + output_tokens.",
        [span_name]
    )
) if {
    has_span_sample
    span_attr_exists("gen_ai.usage.input_tokens")
    span_attr_exists("gen_ai.usage.output_tokens")
    span_attr_exists("gen_ai.usage.total_tokens")
    span_attr_number("gen_ai.usage.total_tokens") != span_attr_number("gen_ai.usage.input_tokens") + span_attr_number("gen_ai.usage.output_tokens")
}

deprecated_gen_ai_message(attr_name, replacement) = msg if {
    replacement != ""
    msg := sprintf(
        "Span '%s': Deprecated gen_ai attribute '%s' detected. Use '%s' instead.",
        [span_name, attr_name, replacement]
    )
}

deprecated_gen_ai_message(attr_name, replacement) = msg if {
    replacement == ""
    msg := sprintf(
        "Span '%s': Deprecated or non-standard gen_ai attribute '%s' detected. This field is not part of the current semconv contract.",
        [span_name, attr_name]
    )
}
