# Ant AI Agent Semconv v2.0 - Deprecated GenAI Attribute Policies
#
# Blocks usage of deprecated/legacy gen_ai attributes that are NOT
# in the v2.0 semconv specification. Maps each deprecated attribute
# to its v2.0 replacement.

package live_check_advice

import rego.v1

#==========================================
# Deprecated gen_ai attributes
#==========================================

# gen_ai.prompt -> gen_ai.input.messages
deny contains make_advice(
    "deprecated_gen_ai_prompt",
    "violation",
    {"deprecated": "gen_ai.prompt", "replacement": "gen_ai.input.messages"},
    "Deprecated attribute 'gen_ai.prompt' detected. Use 'gen_ai.input.messages' (opt-in) instead per semconv v2.0."
) if {
    has_span
    has_span_attr("gen_ai.prompt")
}

# gen_ai.completion -> gen_ai.output.messages
deny contains make_advice(
    "deprecated_gen_ai_completion",
    "violation",
    {"deprecated": "gen_ai.completion", "replacement": "gen_ai.output.messages"},
    "Deprecated attribute 'gen_ai.completion' detected. Use 'gen_ai.output.messages' (opt-in) instead per semconv v2.0."
) if {
    has_span
    has_span_attr("gen_ai.completion")
}

# gen_ai.response.finish_reason (singular) -> gen_ai.response.finish_reasons (plural)
deny contains make_advice(
    "deprecated_gen_ai_finish_reason",
    "violation",
    {"deprecated": "gen_ai.response.finish_reason", "replacement": "gen_ai.response.finish_reasons"},
    "Deprecated attribute 'gen_ai.response.finish_reason' (singular) detected. Use 'gen_ai.response.finish_reasons' (plural array) per semconv v2.0."
) if {
    has_span
    has_span_attr("gen_ai.response.finish_reason")
}

# gen_ai.model -> gen_ai.request.model
deny contains make_advice(
    "deprecated_gen_ai_model",
    "violation",
    {"deprecated": "gen_ai.model", "replacement": "gen_ai.request.model / gen_ai.response.model"},
    "Deprecated attribute 'gen_ai.model' detected. Use 'gen_ai.request.model' or 'gen_ai.response.model' per semconv v2.0."
) if {
    has_span
    has_span_attr("gen_ai.model")
}

# gen_ai.request.model_id -> not in spec, use gen_ai.request.model
deny contains make_advice(
    "deprecated_gen_ai_model_id",
    "violation",
    {"deprecated": "gen_ai.request.model_id", "replacement": "gen_ai.request.model"},
    "Non-standard attribute 'gen_ai.request.model_id' detected. Use 'gen_ai.request.model' per semconv v2.0."
) if {
    has_span
    has_span_attr("gen_ai.request.model_id")
}

# gen_ai.request.is_stream -> not in semconv v2.0
deny contains make_advice(
    "deprecated_gen_ai_is_stream",
    "violation",
    {"deprecated": "gen_ai.request.is_stream"},
    "Non-standard attribute 'gen_ai.request.is_stream' detected. Not defined in semconv v2.0."
) if {
    has_span
    has_span_attr("gen_ai.request.is_stream")
}

#==========================================
# Legacy naming (pre-gen_ai namespace)
#==========================================

# llm.provider -> gen_ai.provider.name
deny contains make_advice(
    "deprecated_llm_provider",
    "violation",
    {"deprecated": "llm.provider", "replacement": "gen_ai.provider.name"},
    "Legacy attribute 'llm.provider' detected. Use 'gen_ai.provider.name' per semconv v2.0."
) if {
    has_span
    has_span_attr("llm.provider")
}

# llm.model -> gen_ai.request.model
deny contains make_advice(
    "deprecated_llm_model",
    "violation",
    {"deprecated": "llm.model", "replacement": "gen_ai.request.model"},
    "Legacy attribute 'llm.model' detected. Use 'gen_ai.request.model' per semconv v2.0."
) if {
    has_span
    has_span_attr("llm.model")
}

# llm.system -> gen_ai.system / gen_ai.provider.name
deny contains make_advice(
    "deprecated_llm_system",
    "violation",
    {"deprecated": "llm.system", "replacement": "gen_ai.system / gen_ai.provider.name"},
    "Legacy attribute 'llm.system' detected. Use 'gen_ai.system' or 'gen_ai.provider.name' per semconv v2.0."
) if {
    has_span
    has_span_attr("llm.system")
}

# llm.request.* -> gen_ai.request.*
deny contains make_advice(
    "deprecated_llm_namespace",
    "violation",
    {"deprecated_prefix": "llm.", "replacement_prefix": "gen_ai."},
    sprintf("Legacy 'llm.*' namespace attribute '%s' detected. Migrate to 'gen_ai.*' namespace per semconv v2.0.", [key])
) if {
    has_span
    some key in all_span_attr_keys
    is_string(key)
    startswith(key, "llm.")
    not key in {"llm.provider", "llm.model", "llm.system"}  # already covered above
}

#==========================================
# Bare attribute names (must be namespaced)
#==========================================

# Bare "model" -> gen_ai.request.model
deny contains make_advice(
    "bare_attr_model",
    "violation",
    {"deprecated": "model", "replacement": "gen_ai.request.model"},
    "Bare attribute 'model' detected. Use namespaced 'gen_ai.request.model' per semconv v2.0."
) if {
    has_span
    has_span_attr("model")
}

# Bare "provider" -> gen_ai.provider.name
deny contains make_advice(
    "bare_attr_provider",
    "violation",
    {"deprecated": "provider", "replacement": "gen_ai.provider.name"},
    "Bare attribute 'provider' detected. Use namespaced 'gen_ai.provider.name' per semconv v2.0."
) if {
    has_span
    has_span_attr("provider")
}

# Bare "username" -> ant.username
deny contains make_advice(
    "bare_attr_username",
    "improvement",
    {"deprecated": "username", "replacement": "ant.username"},
    "Bare attribute 'username' detected. Use namespaced 'ant.username' per semconv v2.0."
) if {
    has_span
    has_span_attr("username")
}
