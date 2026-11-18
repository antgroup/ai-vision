# Signal-local compatibility rules for traces.
# This file is loaded only with this signal's policy engine.

package live_check_advice

import rego.v1

# Signal-local compatibility constants.
_aivision_traces_langfuse_allowed_span_attrs := {
    "langfuse.observation.input",
    "langfuse.observation.output",
    # Historical Langfuse trace tag compatibility; keep temporarily for existing trace display integrations.
    "langfuse.trace.tags",
}

# OTel standard attributes that are allowed outside the primary namespace allowlist.
_otel_standard_attrs := {
    "error.type",
    "otel.scope.name",
    "otel.scope.version",
    "otel.status_code",
    "otel.status_description",
    "error.message",
    "session.id",
    "langfuse.trace.tags",
    "code.function",
    "code.filepath",
    "code.lineno",
    "code.namespace",
    "code.tool.exit_code",
}
# @weaver.rule.title.zh: span:base 禁用字段: deprecated_gen_ai_prompt
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `deprecated_gen_ai_prompt`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.input.messages, gen_ai.prompt
# @weaver.rule.subject: field:gen_ai.prompt
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:gen_ai.input.messages
# @weaver.rule.check.zh: Rego 条件命中 `deprecated_gen_ai_prompt` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。


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
# @weaver.rule.title.zh: span:base 禁用字段: deprecated_gen_ai_completion
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `deprecated_gen_ai_completion`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.completion, gen_ai.output.messages
# @weaver.rule.subject: field:gen_ai.completion
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:gen_ai.output.messages
# @weaver.rule.check.zh: Rego 条件命中 `deprecated_gen_ai_completion` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

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
# @weaver.rule.title.zh: span:base 禁用字段: deprecated_gen_ai_finish_reason
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `deprecated_gen_ai_finish_reason`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.response.finish_reason, gen_ai.response.finish_reasons
# @weaver.rule.subject: field:gen_ai.response.finish_reason
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:gen_ai.response.finish_reasons
# @weaver.rule.check.zh: Rego 条件命中 `deprecated_gen_ai_finish_reason` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

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
# @weaver.rule.title.zh: span:base 禁用字段: deprecated_gen_ai_model
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `deprecated_gen_ai_model`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.model
# @weaver.rule.subject: field:gen_ai.model
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:gen_ai.request.model
# @weaver.rule.check.zh: Rego 条件命中 `deprecated_gen_ai_model` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

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
# @weaver.rule.title.zh: span:base 禁用字段: deprecated_gen_ai_model_id
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `deprecated_gen_ai_model_id`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.request.model, gen_ai.request.model_id
# @weaver.rule.subject: field:gen_ai.request.model_id
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:gen_ai.request.model
# @weaver.rule.check.zh: Rego 条件命中 `deprecated_gen_ai_model_id` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

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
# @weaver.rule.title.zh: span:base 禁用字段: deprecated_gen_ai_is_stream
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `deprecated_gen_ai_is_stream`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.request.is_stream
# @weaver.rule.subject: field:gen_ai.request.is_stream
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.check.zh: Rego 条件命中 `deprecated_gen_ai_is_stream` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

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
# Deprecated tool call attributes
#==========================================

# gen_ai.tool.call.parameters -> gen_ai.tool.call.arguments
# @weaver.rule.title.zh: span:base 禁用字段: deprecated_gen_ai_tool_call_parameters
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `deprecated_gen_ai_tool_call_parameters`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.tool.call.arguments, gen_ai.tool.call.parameters
# @weaver.rule.subject: field:gen_ai.tool.call.parameters
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:gen_ai.tool.call.arguments
# @weaver.rule.check.zh: Rego 条件命中 `deprecated_gen_ai_tool_call_parameters` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

deny contains make_advice(
    "deprecated_gen_ai_tool_call_parameters",
    "violation",
    {"deprecated": "gen_ai.tool.call.parameters", "replacement": "gen_ai.tool.call.arguments"},
    "Deprecated attribute 'gen_ai.tool.call.parameters' detected. Use 'gen_ai.tool.call.arguments' per semconv v2.0."
) if {
    has_span
    has_span_attr("gen_ai.tool.call.parameters")
}

#==========================================
# Legacy naming (pre-gen_ai namespace)
#==========================================

# llm.provider -> gen_ai.provider.name
# @weaver.rule.title.zh: span:base 禁用字段: deprecated_llm_provider
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `deprecated_llm_provider`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.provider.name, llm.provider
# @weaver.rule.subject: field:llm.provider
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:gen_ai.provider.name
# @weaver.rule.check.zh: Rego 条件命中 `deprecated_llm_provider` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

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
# @weaver.rule.title.zh: span:base 禁用字段: deprecated_llm_model
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `deprecated_llm_model`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.request.model, llm.model
# @weaver.rule.subject: field:llm.model
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:gen_ai.request.model
# @weaver.rule.check.zh: Rego 条件命中 `deprecated_llm_model` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

deny contains make_advice(
    "deprecated_llm_model",
    "violation",
    {"deprecated": "llm.model", "replacement": "gen_ai.request.model"},
    "Legacy attribute 'llm.model' detected. Use 'gen_ai.request.model' per semconv v2.0."
) if {
    has_span
    has_span_attr("llm.model")
}

# llm.system -> gen_ai.provider.name
# @weaver.rule.title.zh: span:base 禁用字段: deprecated_llm_system
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `deprecated_llm_system`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: llm.system
# @weaver.rule.subject: field:llm.system
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:gen_ai.provider.name
# @weaver.rule.check.zh: Rego 条件命中 `deprecated_llm_system` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

deny contains make_advice(
    "deprecated_llm_system",
    "violation",
    {"deprecated": "llm.system", "replacement": "gen_ai.provider.name"},
    "Legacy attribute 'llm.system' detected. Use 'gen_ai.provider.name' per semconv v2.0."
) if {
    has_span
    has_span_attr("llm.system")
}

# llm.request.* -> gen_ai.request.* (span)
# @weaver.rule.title.zh: span:base 禁用字段: deprecated_llm_namespace
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `deprecated_llm_namespace`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai., llm., llm.model, llm.provider, llm.system
# @weaver.rule.subject: prefix:llm.*
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: prefix:gen_ai.*
# @weaver.rule.check.zh: Rego 条件命中 `deprecated_llm_namespace` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

deny contains make_advice(
    "deprecated_llm_namespace",
    "violation",
    {"deprecated_prefix": "llm.", "replacement_prefix": "gen_ai."},
    sprintf("Legacy 'llm.*' namespace attribute '%s' detected. Migrate to 'gen_ai.*' namespace per semconv v2.0.", [key])
) if {
    has_span
    some key
    all_span_attr_keys[key]
    is_string(key)
    startswith(key, "llm.")
    not key in {"llm.provider", "llm.model", "llm.system"}  # already covered above
}

#==========================================
# Bare attribute names (must be namespaced)
#==========================================

# Bare "model" -> gen_ai.request.model
# @weaver.rule.title.zh: span:base 禁用字段: bare_attr_model
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `bare_attr_model`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.request.model
# @weaver.rule.subject: field:model
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:gen_ai.request.model
# @weaver.rule.check.zh: Rego 条件命中 `bare_attr_model` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

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
# @weaver.rule.title.zh: span:base 禁用字段: bare_attr_provider
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `bare_attr_provider`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.provider.name
# @weaver.rule.subject: field:provider
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:gen_ai.provider.name
# @weaver.rule.check.zh: Rego 条件命中 `bare_attr_provider` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

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
# @weaver.rule.title.zh: span:base 禁用字段: bare_attr_tool_name
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `bare_attr_tool_name`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.tool.name, tool.name, tool.use_id
# @weaver.rule.subject: field:tool.name
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:gen_ai.tool.name
# @weaver.rule.check.zh: Rego 条件命中 `bare_attr_tool_name` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

deny contains make_advice(
    "bare_attr_tool_name",
    "violation",
    {"deprecated": "tool.name", "replacement": "gen_ai.tool.name"},
    "Bare attribute 'tool.name' detected. Use 'gen_ai.tool.name' per semconv v2.0."
) if {
    has_span
    has_span_attr("tool.name")
}

# Bare "tool.use_id" -> gen_ai.tool.call.id
# @weaver.rule.title.zh: span:base 禁用字段: bare_attr_tool_use_id
# @weaver.rule.zh: 校验 span:base 的「禁用字段」规则 `bare_attr_tool_use_id`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.tool.call.id, tool.use_id
# @weaver.rule.subject: field:tool.use_id
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:gen_ai.tool.call.id
# @weaver.rule.check.zh: Rego 条件命中 `bare_attr_tool_use_id` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

deny contains make_advice(
    "bare_attr_tool_use_id",
    "violation",
    {"deprecated": "tool.use_id", "replacement": "gen_ai.tool.call.id"},
    "Bare attribute 'tool.use_id' detected. Use 'gen_ai.tool.call.id' per semconv v2.0."
) if {
    has_span
    has_span_attr("tool.use_id")
}

# @weaver.rule.zh: 禁止非兼容名单内的 `langfuse.*` vendor 字段；历史兼容字段 `langfuse.trace.tags` 暂时允许保留。
# @weaver.rule.check.zh: 当 span 属性名以 `langfuse.` 开头且不在 `_aivision_traces_langfuse_allowed_span_attrs` 中时命中。
# @weaver.rule.fix.zh: 将 prompt/tool payload 迁移到标准 `gen_ai.*` 字段；仅 trace 标签类历史集成可继续使用 `langfuse.trace.tags`。
# @weaver.rule.note.zh: 这是 vendor 兼容性强制规则，用于阻止新的 Langfuse 私有字段扩散。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 命名空间
# @weaver.rule.fields: langfuse.
# @weaver.rule.subject: prefix:langfuse.*
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: namespace
# @weaver.rule.replacement: prefix:gen_ai.*
# @weaver.rule.branch: occurrence-1
deny contains make_advice(
    "vendor_langfuse_attr",
    "violation",
    {"attribute": attr.key},
    sprintf("Vendor-specific attribute '%s' detected. Use semconv standard attributes instead (gen_ai.tool.call.arguments / gen_ai.tool.call.result for tool I/O, gen_ai.input.messages / gen_ai.output.messages for LLM I/O).", [attr.key])
) if {
    has_span
    some attr in input.sample.span.attributes
    is_string(attr.key)
    startswith(attr.key, "langfuse.")
    not attr.key in _aivision_traces_langfuse_allowed_span_attrs
}

# Block any langfuse.* attribute on span (Weaver name format) except allowed ones
# @weaver.rule.title.zh: span:base 命名空间: vendor_langfuse_attr
# @weaver.rule.zh: 禁止非兼容名单内的 `langfuse.*` vendor 字段；历史兼容字段 `langfuse.trace.tags` 暂时允许保留。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 命名空间
# @weaver.rule.fields: langfuse.
# @weaver.rule.subject: prefix:langfuse.*
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: namespace
# @weaver.rule.replacement: prefix:gen_ai.*
# @weaver.rule.check.zh: 当 span 属性名以 `langfuse.` 开头且不在 `_aivision_traces_langfuse_allowed_span_attrs` 中时命中。
# @weaver.rule.fix.zh: 将 prompt/tool payload 迁移到标准 `gen_ai.*` 字段；仅 trace 标签类历史集成可继续使用 `langfuse.trace.tags`。
# @weaver.rule.note.zh: 这是 vendor 兼容性强制规则，用于阻止新的 Langfuse 私有字段扩散。
# @weaver.rule.branch: occurrence-2

deny contains make_advice(
    "vendor_langfuse_attr",
    "violation",
    {"attribute": attr.name},
    sprintf("Vendor-specific attribute '%s' detected. Use semconv standard attributes instead.", [attr.name])
) if {
    has_span
    some attr in input.sample.span.attributes
    is_string(attr.name)
    startswith(attr.name, "langfuse.")
    not attr.name in _aivision_traces_langfuse_allowed_span_attrs
}

# --- Span attributes ---

_aivision_billing_span_attrs := {
    "aivision.billing.mode",
    "aivision.billing.estimate_list_price",
}

# Block span attributes with disallowed namespace prefix (OTLP key format)
# @weaver.rule.title.zh: span:base 命名空间: disallowed_attr_prefix
# @weaver.rule.zh: 校验 span:base 的「命名空间」规则 `disallowed_attr_prefix`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 命名空间
# @weaver.rule.fields: _internal., agentic., ant., gen_ai., service., telemetry., user., aivision.billing.mode, aivision.billing.estimate_list_price
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: namespace
# @weaver.rule.check.zh: Rego 条件命中 `disallowed_attr_prefix` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。
# @weaver.rule.branch: occurrence-1

deny contains make_advice(
    "disallowed_attr_prefix",
    "violation",
    {"attribute": attr.key, "span_name": input.sample.span.name},
    sprintf("Span '%s': Attribute '%s' uses a disallowed namespace. Use a registered AI Vision or OpenTelemetry span attribute.", [input.sample.span.name, attr.key])
) if {
    has_span
    some attr in input.sample.span.attributes
    is_string(attr.key)
    not startswith(attr.key, "gen_ai.")
    not startswith(attr.key, "ant.")
    not startswith(attr.key, "agentic.")
    not startswith(attr.key, "service.")
    not startswith(attr.key, "telemetry.")
    not startswith(attr.key, "user.")
    not startswith(attr.key, "_internal.")
    not attr.key in _otel_standard_attrs
    not attr.key in _aivision_billing_span_attrs
}

# Block span attributes with disallowed namespace prefix (Weaver name format)
# @weaver.rule.title.zh: span:base 命名空间: disallowed_attr_prefix
# @weaver.rule.zh: 校验 span:base 的「命名空间」规则 `disallowed_attr_prefix`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 命名空间
# @weaver.rule.fields: _internal., agentic., ant., gen_ai., service., telemetry., user., aivision.billing.mode, aivision.billing.estimate_list_price
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: namespace
# @weaver.rule.check.zh: Rego 条件命中 `disallowed_attr_prefix` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。
# @weaver.rule.branch: occurrence-2

deny contains make_advice(
    "disallowed_attr_prefix",
    "violation",
    {"attribute": attr.name, "span_name": input.sample.span.name},
    sprintf("Span '%s': Attribute '%s' uses a disallowed namespace. Use a registered AI Vision span attribute.", [input.sample.span.name, attr.name])
) if {
    has_span
    some attr in input.sample.span.attributes
    is_string(attr.name)
    not startswith(attr.name, "gen_ai.")
    not startswith(attr.name, "ant.")
    not startswith(attr.name, "agentic.")
    not startswith(attr.name, "service.")
    not startswith(attr.name, "telemetry.")
    not startswith(attr.name, "user.")
    not startswith(attr.name, "_internal.")
    not attr.name in _aivision_billing_span_attrs
    not attr.name in _otel_standard_attrs
}

# --- Resource attributes (shared across signals, check once with span) ---

# Block resource attributes with disallowed namespace prefix (OTLP key format)
# @weaver.rule.title.zh: span:base 命名空间: disallowed_resource_attr_prefix
# @weaver.rule.zh: 校验 span:base 的「命名空间」规则 `disallowed_resource_attr_prefix`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 命名空间
# @weaver.rule.fields: _internal., agentic., ant., deployment., gen_ai., service., telemetry., user., vcs.
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: namespace
# @weaver.rule.check.zh: Rego 条件命中 `disallowed_resource_attr_prefix` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。
# @weaver.rule.branch: occurrence-1

deny contains make_advice(
    "disallowed_resource_attr_prefix",
    "violation",
    {"attribute": attr.key, "span_name": input.sample.span.name},
    sprintf("Resource (span '%s'): Attribute '%s' uses disallowed namespace. Only gen_ai.*, ant.*, agentic.*, service.*, telemetry.*, user.*, _internal.*, vcs.* prefixes are allowed.", [input.sample.span.name, attr.key])
) if {
    has_span
    has_resource
    some attr in input.sample.resource.attributes
    is_string(attr.key)
    not startswith(attr.key, "gen_ai.")
    not startswith(attr.key, "ant.")
    not startswith(attr.key, "agentic.")
    not startswith(attr.key, "service.")
    not startswith(attr.key, "telemetry.")
    not startswith(attr.key, "user.")
    not startswith(attr.key, "deployment.")
    not startswith(attr.key, "_internal.")
    not startswith(attr.key, "vcs.")
}

# Block resource attributes with disallowed namespace prefix (Weaver name format)
# @weaver.rule.title.zh: span:base 命名空间: disallowed_resource_attr_prefix
# @weaver.rule.zh: 校验 span:base 的「命名空间」规则 `disallowed_resource_attr_prefix`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 命名空间
# @weaver.rule.fields: _internal., agentic., ant., deployment., gen_ai., service., telemetry., user., vcs.
# @weaver.rule.placement: resource_attributes
# @weaver.rule.constraint.type: namespace
# @weaver.rule.check.zh: Rego 条件命中 `disallowed_resource_attr_prefix` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。
# @weaver.rule.branch: occurrence-2

deny contains make_advice(
    "disallowed_resource_attr_prefix",
    "violation",
    {"attribute": attr.name, "span_name": input.sample.span.name},
    sprintf("Resource (span '%s'): Attribute '%s' uses disallowed namespace. Only gen_ai.*, ant.*, agentic.*, service.*, telemetry.*, user.*, _internal.*, vcs.* prefixes are allowed.", [input.sample.span.name, attr.name])
) if {
    has_span
    has_resource
    some attr in input.sample.resource.attributes
    is_string(attr.name)
    not startswith(attr.name, "gen_ai.")
    not startswith(attr.name, "ant.")
    not startswith(attr.name, "agentic.")
    not startswith(attr.name, "service.")
    not startswith(attr.name, "telemetry.")
    not startswith(attr.name, "user.")
    not startswith(attr.name, "deployment.")
    not startswith(attr.name, "_internal.")
    not startswith(attr.name, "vcs.")
}
