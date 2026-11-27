# AI Vision Agent Semconv - Base Helpers
# Provides attribute access for OTLP format and span classification by name pattern.
# Designed for cchooks debug output validation.

package live_check_advice

import rego.v1

_is_number_like(value) if {
    is_number(value)
}

_is_number_like(value) if {
    is_string(value)
    regex.match(`^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$`, value)
}

#==========================================
# Advice Builder
#==========================================

make_advice(advice_type, advice_level, advice_context, message) := {
    "type": "advice",
    "advice_type": advice_type,
    "advice_level": advice_level,
    "advice_context": advice_context,
    "message": message,
}

#==========================================
# Span Attribute Helpers (OTLP key format)
#==========================================

# Check if span has attribute (OTLP key format)
has_span_attr(name) if {
    some attr in input.sample.span.attributes
    attr.key == name
}

# Check if span has attribute (Weaver name format)
has_span_attr(name) if {
    some attr in input.sample.span.attributes
    attr.name == name
}

# Get span attribute string value (OTLP stringValue)
get_span_attr(name) = value if {
    some attr in input.sample.span.attributes
    attr.key == name
    value := attr.value.stringValue
}

# Get span attribute string value (Weaver direct)
get_span_attr(name) = value if {
    some attr in input.sample.span.attributes
    attr.name == name
    is_string(attr.value)
    value := attr.value
}

# Get span attribute int value (OTLP intValue)
get_span_attr_int(name) = value if {
    some attr in input.sample.span.attributes
    attr.key == name
    value := to_number(attr.value.intValue)
}

# Get span attribute int value (OTLP stringValue as number)
get_span_attr_int(name) = value if {
    some attr in input.sample.span.attributes
    attr.key == name
    value := to_number(attr.value.stringValue)
}

# Get span attribute int value (Weaver direct number)
get_span_attr_int(name) = value if {
    some attr in input.sample.span.attributes
    attr.name == name
    is_number(attr.value)
    value := attr.value
}

#==========================================
# Resource Attribute Helpers
#==========================================

has_resource_attr(name) if {
    some attr in input.sample.resource.attributes
    attr.key == name
}

has_resource_attr(name) if {
    some attr in input.sample.resource.attributes
    attr.name == name
}

get_resource_attr(name) = value if {
    some attr in input.sample.resource.attributes
    attr.key == name
    value := attr.value.stringValue
}

get_resource_attr(name) = value if {
    some attr in input.sample.resource.attributes
    attr.name == name
    is_string(attr.value)
    value := attr.value
}

_is_resource_only(name) if {
    name in {
        "ant.agent.type",
        "ant.agent.product",
        "ant.agent.instance",
        "ant.agent.env",
        "ant.agent.scene",
        "service.name",
        "service.version",
        "service.instance.id",
    }
}

_is_span_only(name) if {
    name in {
        "gen_ai.span.kind",
        "agentic.turn.number",
        "agentic.turn.id",
        "agentic.tool.id",
        "agentic.tool.name",
        "agentic.tool.type",
    }
}

#==========================================
# Span Existence Check
#==========================================

has_span if {
    input.sample.span
}

has_resource if {
    input.sample.resource
    input.sample.resource.attributes
}

#==========================================
# Span Classification
# Primary: gen_ai.span.kind attribute (CHAT/TOOL/LLM)
# Fallback: name pattern matching for legacy spans
#   cchooks legacy:  "Turn N" / "Tool: X" / "assistant-N-N"
#   semconv style:   "chat ..." / "execute_tool ..." / "chat {model}-N"
#==========================================

# Turn/Chat span: gen_ai.span.kind == "CHAT" or legacy "Turn N" name
is_turn_span if {
    has_span
    get_span_attr("gen_ai.span.kind") == "CHAT"
}

is_turn_span if {
    has_span
    startswith(input.sample.span.name, "Turn ")
}

# Tool span: gen_ai.span.kind == "TOOL" or legacy "Tool: X" name
is_tool_span if {
    has_span
    get_span_attr("gen_ai.span.kind") == "TOOL"
}

is_tool_span if {
    has_span
    startswith(input.sample.span.name, "Tool: ")
}

is_tool_span if {
    has_span
    startswith(input.sample.span.name, "tool ")
}

# LLM span: gen_ai.span.kind == "LLM" or legacy "assistant-N-N" name
is_llm_span if {
    has_span
    get_span_attr("gen_ai.span.kind") == "LLM"
}

is_llm_span if {
    has_span
    startswith(input.sample.span.name, "assistant-")
}

is_llm_span if {
    has_span
    startswith(input.sample.span.name, "Generation ")
}

# Check if root span (no parent)
is_root_span if {
    has_span
    input.sample.span.parentSpanId == ""
}

is_root_span if {
    has_span
    not input.sample.span.parentSpanId
}

# Collect all span attribute keys
all_span_attr_keys[key] if {
    some attr in input.sample.span.attributes
    key := attr.key
    is_string(key)
}

all_span_attr_keys[name] if {
    some attr in input.sample.span.attributes
    name := attr.name
    is_string(name)
}

#==========================================
# Log/Event Attribute Helpers
#==========================================

has_log if {
    input.sample.log
}

has_log_attr(name) if {
    some attr in input.sample.log.attributes
    attr.key == name
}

has_log_attr(name) if {
    some attr in input.sample.log.attributes
    attr.name == name
}

get_log_attr(name) = value if {
    some attr in input.sample.log.attributes
    attr.key == name
    value := attr.value.stringValue
}

get_log_attr(name) = value if {
    some attr in input.sample.log.attributes
    attr.name == name
    is_string(attr.value)
    value := attr.value
}

get_log_attr_number(name) = value if {
    some attr in input.sample.log.attributes
    attr.key == name
    _is_number_like(attr.value.intValue)
    value := to_number(attr.value.intValue)
}

get_log_attr_number(name) = value if {
    some attr in input.sample.log.attributes
    attr.key == name
    _is_number_like(attr.value.doubleValue)
    value := to_number(attr.value.doubleValue)
}

get_log_attr_number(name) = value if {
    some attr in input.sample.log.attributes
    attr.key == name
    _is_number_like(attr.value.stringValue)
    value := to_number(attr.value.stringValue)
}

get_log_attr_number(name) = value if {
    some attr in input.sample.log.attributes
    attr.name == name
    is_number(attr.value)
    value := attr.value
}

get_log_attr_number(name) = value if {
    some attr in input.sample.log.attributes
    attr.name == name
    is_string(attr.value)
    _is_number_like(attr.value)
    value := to_number(attr.value)
}

has_log_attr_number(name) if {
    _ := get_log_attr_number(name)
}

# Classify event by event.name attribute
is_event(event_name) if {
    has_log
    get_log_attr("event.name") == event_name
}

valid_opencode_event_names := {
    "user_prompt",
    "api_request",
    "tool_call",
    "tool_result",
    "tool_decision",
    "subagent_spawned",
    "subagent_ended",
    "session_start",
    "session_end",
    "compaction_result",
}

_event_common_required_attrs := {
    "gen_ai.operation.name",
    "ant.username",
    "ant.agent.type",
    "ant.agent.product",
    "gen_ai.conversation.id",
    "agentic.runtime.name",
}

_is_event_only(name) if {
    name in {
        "event.name",
        "event.sequence",
        "event.timestamp",
        "duration_ms",
        "prompt.id",
        "gen_ai.operation.name",
        "gen_ai.conversation.id",
        "gen_ai.chat.id",
        "gen_ai.session.id",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.system_instructions",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.total_tokens",
        "gen_ai.tool.name",
        "gen_ai.tool.call.id",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
        "agentic.tool.input.missing",
        "agentic.tool.input.truncated",
        "agentic.turn.number",
        "agentic.runtime.name",
        "agentic.tool.type",
        "agentic.tool.name",
        "agentic.tool.id",
        "error.type",
        "error.message",
        "success",
    }
}

_is_ant_event if {
    has_log
    has_log_attr("event.name")
}

_is_user_prompt if {
    is_event("user_prompt")
}

_is_api_request if {
    is_event("api_request")
}

_is_tool_result if {
    is_event("tool_result")
}

_is_tool_call if {
    is_event("tool_call")
}

_is_session_start if {
    is_event("session_start")
}

_is_session_end if {
    is_event("session_end")
}

_is_compaction_result if {
    is_event("compaction_result")
}

_is_success_api_request if {
    _is_api_request
    not has_log_attr("error.type")
}

_is_success_tool_result if {
    _is_tool_result
    not has_log_attr("error.type")
}

_is_turn_scoped_event if {
    _is_user_prompt
}

_is_turn_scoped_event if {
    _is_api_request
}

_is_turn_scoped_event if {
    _is_tool_result
}

_is_turn_scoped_event if {
    _is_tool_call
}

_event_requires_duration if {
    _is_api_request
}

_event_requires_duration if {
    _is_tool_result
}

_event_requires_duration if {
    _is_compaction_result
}

_event_requires_sequence if {
    _is_user_prompt
}

_event_requires_sequence if {
    _is_api_request
}

_event_requires_sequence if {
    _is_tool_result
}

_event_requires_sequence if {
    _is_compaction_result
}

_event_requires_chat_id if {
    _is_turn_scoped_event
}

_event_requires_turn_number if {
    _is_turn_scoped_event
}

_event_requires_turn_number if {
    _is_session_end
}

_event_requires_turn_number if {
    _is_compaction_result
}

# Collect all log attribute keys
all_log_attr_keys[key] if {
    some attr in input.sample.log.attributes
    key := attr.key
    is_string(key)
}

all_log_attr_keys[name] if {
    some attr in input.sample.log.attributes
    name := attr.name
    is_string(name)
}

# Collect all resource attribute keys
all_resource_attr_keys[key] if {
    some attr in input.sample.resource.attributes
    key := attr.key
    is_string(key)
}

all_resource_attr_keys[name] if {
    some attr in input.sample.resource.attributes
    name := attr.name
    is_string(name)
}

#==========================================
# Metric Helpers
#==========================================

has_metric if {
    input.sample.metric
}

# Get metric name
get_metric_name = name if {
    has_metric
    name := input.sample.metric.name
}

# Check if metric is a specific name
is_metric(metric_name) if {
    has_metric
    input.sample.metric.name == metric_name
}

_is_agent_metric if {
    has_metric
    startswith(get_metric_name, "agent.")
}

_is_red_golden_metric if {
    _is_api_request_count
}

_is_red_golden_metric if {
    _is_api_request_error_count
}

_is_red_golden_metric if {
    _is_api_request_duration
}

_is_red_golden_metric if {
    _is_tool_call_count
}

_is_red_golden_metric if {
    _is_tool_call_error_count
}

_is_red_golden_metric if {
    _is_tool_call_duration
}

_is_api_request_count if {
    is_metric("agent.api_request.count")
}

_is_api_request_error_count if {
    is_metric("agent.api_request.error.count")
}

_is_api_request_duration if {
    is_metric("agent.api_request.duration")
}

_is_api_request_metric if {
    _is_api_request_count
}

_is_api_request_metric if {
    _is_api_request_error_count
}

_is_api_request_metric if {
    _is_api_request_duration
}

_is_tool_call_count if {
    is_metric("agent.tool_call.count")
}

_is_tool_call_error_count if {
    is_metric("agent.tool_call.error.count")
}

_is_tool_call_duration if {
    is_metric("agent.tool_call.duration")
}

_is_tool_call_metric if {
    _is_tool_call_count
}

_is_tool_call_metric if {
    _is_tool_call_error_count
}

_is_tool_call_metric if {
    _is_tool_call_duration
}

_is_count_metric if {
    _is_api_request_count
}

_is_count_metric if {
    _is_api_request_error_count
}

_is_count_metric if {
    _is_tool_call_count
}

_is_count_metric if {
    _is_tool_call_error_count
}

_is_error_count_metric if {
    _is_api_request_error_count
}

_is_error_count_metric if {
    _is_tool_call_error_count
}

# Check if metric is a counter (has "sum" field)
is_counter if {
    has_metric
    input.sample.metric.sum
}

# Check if metric is a histogram (has "histogram" field)
is_histogram if {
    has_metric
    input.sample.metric.histogram
}

# Get metric unit
get_metric_unit = unit if {
    has_metric
    unit := input.sample.metric.unit
}

# Collect all data points across metric types (sum + histogram)
_all_data_points contains dp if {
    has_metric
    some dp in input.sample.metric.sum.dataPoints
}

_all_data_points contains dp if {
    has_metric
    some dp in input.sample.metric.histogram.dataPoints
}

metric_dp_has_attr(dp, name) if {
    some attr in dp.attributes
    attr.key == name
}

metric_dp_has_attr(dp, name) if {
    some attr in dp.attributes
    attr.name == name
}

metric_dp_attr_string(dp, name) = value if {
    some attr in dp.attributes
    attr.key == name
    value := attr.value.stringValue
}

metric_dp_attr_string(dp, name) = value if {
    some attr in dp.attributes
    attr.name == name
    is_string(attr.value)
    value := attr.value
}

metric_dp_missing_attr(name) if {
    some dp in _all_data_points
    not metric_dp_has_attr(dp, name)
}

metric_dp_has_forbidden_attr(name) if {
    some dp in _all_data_points
    metric_dp_has_attr(dp, name)
}

valid_metric_tool_types := {"builtin", "mcp", "skill"}

valid_metric_error_categories := {
    "api_error",
    "network_error",
    "timeout",
    "rate_limit",
    "auth",
    "permission",
    "validation",
    "tool_error",
    "cancelled",
    "other",
}

metric_attr_allowed_string_value(attr, valid_values) if {
    value := attr.value.stringValue
    value in valid_values
}

metric_attr_allowed_string_value(attr, valid_values) if {
    is_string(attr.value)
    value := attr.value
    value in valid_values
}

metric_dp_attr_invalid_value(name, valid_values) if {
    some dp in _all_data_points
    some attr in dp.attributes
    attr.key == name
    not metric_attr_allowed_string_value(attr, valid_values)
}

metric_dp_attr_invalid_value(name, valid_values) if {
    some dp in _all_data_points
    some attr in dp.attributes
    attr.name == name
    not metric_attr_allowed_string_value(attr, valid_values)
}

metric_sum_value_is_negative if {
    _is_red_golden_metric
    some dp in input.sample.metric.sum.dataPoints
    value := dp.asInt
    to_number(value) < 0
}

metric_sum_value_is_negative if {
    _is_red_golden_metric
    some dp in input.sample.metric.sum.dataPoints
    value := dp.asDouble
    to_number(value) < 0
}

metric_histogram_value_is_negative if {
    _is_red_golden_metric
    some dp in input.sample.metric.histogram.dataPoints
    some field in {"count", "sum", "min", "max"}
    value := dp[field]
    to_number(value) < 0
}

metric_histogram_value_is_negative if {
    _is_red_golden_metric
    some dp in input.sample.metric.histogram.dataPoints
    some value in dp.bucketCounts
    to_number(value) < 0
}

# Check if any data point has attribute (OTLP key format, sum)
has_metric_dp_attr(name) if {
    has_metric
    some dp in input.sample.metric.sum.dataPoints
    some attr in dp.attributes
    attr.key == name
}

# Check if any data point has attribute (OTLP key format, histogram)
has_metric_dp_attr(name) if {
    has_metric
    some dp in input.sample.metric.histogram.dataPoints
    some attr in dp.attributes
    attr.key == name
}

# Get metric data point attribute value (OTLP key format, sum)
get_metric_dp_attr(name) = value if {
    has_metric
    some dp in input.sample.metric.sum.dataPoints
    some attr in dp.attributes
    attr.key == name
    value := attr.value.stringValue
}

# Get metric data point attribute value (OTLP key format, histogram)
get_metric_dp_attr(name) = value if {
    has_metric
    some dp in input.sample.metric.histogram.dataPoints
    some attr in dp.attributes
    attr.key == name
    value := attr.value.stringValue
}

#==========================================
# Split Policy Classification Helpers
#==========================================
# These predicates are intentionally shared from base/ because policy files are
# classified by signal/domain/severity paths. They preserve the guard helpers
# that used to live at the top of monolithic span/metric/event policy files.

# Controlled-vocabulary constants used across split severity files.
valid_runtime_names := {"claude-code", "codex", "cursor", "aider", "openclaw", "opencode", "gemini-cli", "hermes", "kimi-cli", "codefuse", "antigravity-cli", "pi", "dsh", "minimax-code"}

valid_collection_modes := {"full", "privacy"}

valid_systems := {"anthropic", "openai", "qwen", "alibaba", "azure.ai.openai", "gcp.gemini", "aws.bedrock", "deepseek", "theta", "Theta", "gemini", "moonshot"}

# Metric guards.
_is_agent_metric if {
    has_metric
    startswith(get_metric_name, "agent.")
}

_is_api_request_count if {
    is_metric("agent.api_request.count")
}

_is_api_request_duration if {
    is_metric("agent.api_request.duration")
}

_is_api_request_metric if {
    _is_api_request_count
}

_is_api_request_metric if {
    _is_api_request_duration
}

_is_tool_call_count if {
    is_metric("agent.tool_call.count")
}

# Event guards.
_is_ant_event if {
    has_log
    has_log_attr("event.name")
}

_is_api_request if {
    is_event("api_request")
}

_is_tool_result if {
    is_event("tool_result")
}

# Trace guards split from capability-specific files.
_capability_d1_is_compaction_span if {
    has_span
    input.sample.span.name == "compaction"
}

_capability_d1_is_compaction_span if {
    has_span
    get_span_attr("gen_ai.tool.name") == "compaction"
}

_capability_c2_is_error_status if {
    has_span
    input.sample.span.status.code == 2
}

_capability_c2_is_error_status if {
    has_span
    input.sample.span.status.code == "STATUS_CODE_ERROR"
}

_capability_c2_is_error_status if {
    has_span
    input.sample.span.status.code == "ERROR"
}

_capability_b2_is_error_status if {
    has_span
    input.sample.span.status.code == 2
}

_capability_b2_is_error_status if {
    has_span
    input.sample.span.status.code == "STATUS_CODE_ERROR"
}

_capability_b2_is_error_status if {
    has_span
    input.sample.span.status.code == "ERROR"
}
