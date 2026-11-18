# Signal-local compatibility rules for metrics.
# This file is loaded only with this signal's policy engine.

package live_check_advice

import rego.v1
# @weaver.rule.title.zh: metric:base 禁用字段: deprecated_gen_ai_prompt
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `deprecated_gen_ai_prompt`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.input.messages, gen_ai.prompt
# @weaver.rule.subject: field:gen_ai.prompt
# @weaver.rule.placement: metric_data_point_attributes
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
    _is_red_golden_metric
    has_metric_dp_attr("gen_ai.prompt")
}
# @weaver.rule.title.zh: metric:base 禁用字段: deprecated_gen_ai_completion
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `deprecated_gen_ai_completion`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.completion, gen_ai.output.messages
# @weaver.rule.subject: field:gen_ai.completion
# @weaver.rule.placement: metric_data_point_attributes
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
    _is_red_golden_metric
    has_metric_dp_attr("gen_ai.completion")
}
# @weaver.rule.title.zh: metric:base 禁用字段: deprecated_gen_ai_finish_reason
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `deprecated_gen_ai_finish_reason`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.response.finish_reason, gen_ai.response.finish_reasons
# @weaver.rule.subject: field:gen_ai.response.finish_reason
# @weaver.rule.placement: metric_data_point_attributes
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
    _is_red_golden_metric
    has_metric_dp_attr("gen_ai.response.finish_reason")
}
# @weaver.rule.title.zh: metric:base 禁用字段: deprecated_gen_ai_model
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `deprecated_gen_ai_model`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.model
# @weaver.rule.subject: field:gen_ai.model
# @weaver.rule.placement: metric_data_point_attributes
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
    _is_red_golden_metric
    has_metric_dp_attr("gen_ai.model")
}
# @weaver.rule.title.zh: metric:base 禁用字段: deprecated_gen_ai_model_id
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `deprecated_gen_ai_model_id`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.request.model, gen_ai.request.model_id
# @weaver.rule.subject: field:gen_ai.request.model_id
# @weaver.rule.placement: metric_data_point_attributes
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
    _is_red_golden_metric
    has_metric_dp_attr("gen_ai.request.model_id")
}
# @weaver.rule.title.zh: metric:base 禁用字段: deprecated_gen_ai_is_stream
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `deprecated_gen_ai_is_stream`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.request.is_stream
# @weaver.rule.subject: field:gen_ai.request.is_stream
# @weaver.rule.placement: metric_data_point_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.check.zh: Rego 条件命中 `deprecated_gen_ai_is_stream` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

deny contains make_advice(
    "deprecated_gen_ai_is_stream",
    "violation",
    {"deprecated": "gen_ai.request.is_stream"},
    "Non-standard attribute 'gen_ai.request.is_stream' detected. Not defined in semconv v2.0."
) if {
    _is_red_golden_metric
    has_metric_dp_attr("gen_ai.request.is_stream")
}
# @weaver.rule.title.zh: metric:base 禁用字段: deprecated_gen_ai_tool_call_parameters
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `deprecated_gen_ai_tool_call_parameters`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.tool.call.arguments, gen_ai.tool.call.parameters
# @weaver.rule.subject: field:gen_ai.tool.call.parameters
# @weaver.rule.placement: metric_data_point_attributes
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
    _is_red_golden_metric
    has_metric_dp_attr("gen_ai.tool.call.parameters")
}
# @weaver.rule.title.zh: metric:base 禁用字段: deprecated_llm_provider
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `deprecated_llm_provider`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.provider.name, llm.provider
# @weaver.rule.subject: field:llm.provider
# @weaver.rule.placement: metric_data_point_attributes
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
    _is_red_golden_metric
    has_metric_dp_attr("llm.provider")
}
# @weaver.rule.title.zh: metric:base 禁用字段: deprecated_llm_model
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `deprecated_llm_model`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.request.model, llm.model
# @weaver.rule.subject: field:llm.model
# @weaver.rule.placement: metric_data_point_attributes
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
    _is_red_golden_metric
    has_metric_dp_attr("llm.model")
}
# @weaver.rule.title.zh: metric:base 禁用字段: deprecated_llm_system
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `deprecated_llm_system`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: llm.system
# @weaver.rule.subject: field:llm.system
# @weaver.rule.placement: metric_data_point_attributes
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
    _is_red_golden_metric
    has_metric_dp_attr("llm.system")
}

# llm.request.* -> gen_ai.request.* (metric)
# @weaver.rule.title.zh: metric:base 禁用字段: deprecated_llm_namespace
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `deprecated_llm_namespace`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai., llm., llm.model, llm.provider, llm.system
# @weaver.rule.subject: prefix:llm.*
# @weaver.rule.placement: datapoint_attributes
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
    _is_red_golden_metric
    some dp in _all_data_points
    some attr in dp.attributes
    key := attr.key
    is_string(key)
    startswith(key, "llm.")
    not key in {"llm.provider", "llm.model", "llm.system"}
}
# @weaver.rule.title.zh: metric:base 禁用字段: bare_attr_model
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `bare_attr_model`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.request.model
# @weaver.rule.subject: field:model
# @weaver.rule.placement: metric_data_point_attributes
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
    _is_red_golden_metric
    has_metric_dp_attr("model")
}
# @weaver.rule.title.zh: metric:base 禁用字段: bare_attr_provider
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `bare_attr_provider`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.provider.name
# @weaver.rule.subject: field:provider
# @weaver.rule.placement: metric_data_point_attributes
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
    _is_red_golden_metric
    has_metric_dp_attr("provider")
}
# @weaver.rule.title.zh: metric:base 禁用字段: bare_attr_tool_name
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `bare_attr_tool_name`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.tool.name, tool.name
# @weaver.rule.subject: field:tool.name
# @weaver.rule.placement: metric_data_point_attributes
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
    _is_red_golden_metric
    has_metric_dp_attr("tool.name")
}
# @weaver.rule.title.zh: metric:base 禁用字段: bare_attr_tool_use_id
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `bare_attr_tool_use_id`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.tool.call.id, tool.use_id
# @weaver.rule.subject: field:tool.use_id
# @weaver.rule.placement: metric_data_point_attributes
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
    _is_red_golden_metric
    has_metric_dp_attr("tool.use_id")
}

#==========================================
# Vendor-specific namespace: langfuse.*
#==========================================

# Block any langfuse.* attribute on metric data point
# @weaver.rule.title.zh: metric:base 禁用字段: vendor_langfuse_attr
# @weaver.rule.zh: 校验 metric:base 的「禁用字段」规则 `vendor_langfuse_attr`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: code.filepath, code.function, code.lineno, code.namespace, code.tool.exit_code, error.message, error.type, langfuse., langfuse.trace.tags, otel.scope.name, otel.scope.version, otel.status_code, otel.status_description, session.id
# @weaver.rule.subject: prefix:langfuse.*
# @weaver.rule.placement: metric_data_point_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.check.zh: Rego 条件命中 `vendor_langfuse_attr` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

deny contains make_advice(
    "vendor_langfuse_attr",
    "violation",
    {"attribute": attr.key},
    sprintf("Vendor-specific attribute '%s' detected. Use semconv standard attributes instead.", [attr.key])
) if {
    _is_red_golden_metric
    some dp in _all_data_points
    some attr in dp.attributes
    is_string(attr.key)
    startswith(attr.key, "langfuse.")
}

#==========================================
# Attribute Namespace Gate (allowlist)
# Only allowed prefixes: gen_ai.*, ant.*, agentic.*, service.*, telemetry.*, user.*, _internal.*
# Plus OTel standard attributes explicitly exempted below.
# Scope attributes (instrumentationScope name/version) are excluded.
#==========================================

# RED golden metric attributes that are handled by dedicated policy rules instead of
# the generic namespace gate. Keep this narrow so common span/log attributes do
# not silently become metric dimensions.
_metric_namespace_exempt_attrs := {
    "error.type",
    "error.message",
}

# --- Metric data point attributes ---

# Block metric dp attributes with disallowed namespace prefix
# @weaver.rule.title.zh: metric:base 命名空间: disallowed_metric_attr_prefix
# @weaver.rule.zh: 校验 metric:base 的「命名空间」规则 `disallowed_metric_attr_prefix`，确保遥测满足 AiVision 语义约定。
# @weaver.rule.entity: metric:base
# @weaver.rule.category.zh: 命名空间
# @weaver.rule.fields: _internal., agentic., ant., gen_ai., service., telemetry., user.
# @weaver.rule.placement: metric_data_point_attributes
# @weaver.rule.constraint.type: namespace
# @weaver.rule.check.zh: Rego 条件命中 `disallowed_metric_attr_prefix` 并产生 advice 时报告。
# @weaver.rule.fix.zh: 按 AiVision 语义约定补齐、重命名或移动相关字段；不要通过降低策略标准消除告警。

deny contains make_advice(
    "disallowed_metric_attr_prefix",
    "violation",
    {"attribute": attr.key, "metric_name": get_metric_name},
    sprintf("Metric '%s': Attribute '%s' uses disallowed namespace. Only gen_ai.*, ant.*, agentic.*, service.*, telemetry.*, user.*, _internal.* prefixes are allowed.", [get_metric_name, attr.key])
) if {
    _is_red_golden_metric
    some dp in _all_data_points
    some attr in dp.attributes
    is_string(attr.key)
    not attr.key in _metric_namespace_exempt_attrs
    not startswith(attr.key, "gen_ai.")
    not startswith(attr.key, "ant.")
    not startswith(attr.key, "agentic.")
    not startswith(attr.key, "service.")
    not startswith(attr.key, "telemetry.")
    not startswith(attr.key, "user.")
    not startswith(attr.key, "_internal.")
}
