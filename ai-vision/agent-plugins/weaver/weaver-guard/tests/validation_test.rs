//! Validation integration tests for the current `policies/aivision` layout.

use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::path::PathBuf;
use weaver_guard::jsonl::SignalType;
use weaver_guard::semconv::{
    diff_attributes_from_signal_type, load_semconv_registry, SemconvFindingType,
};
use weaver_guard::ValidationEngine;

fn get_policies_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("policies")
}

fn get_semconv_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("docs/semconv")
}

fn engine() -> ValidationEngine {
    let policies_dir = get_policies_dir();
    ValidationEngine::new(&policies_dir)
        .unwrap_or_else(|err| panic!("failed to load policies at {:?}: {err:?}", policies_dir))
}

fn valid_resource() -> Value {
    json!({
        "attributes": [
            {"key": "ant.agent.type", "value": {"stringValue": "opencode"}},
            {"key": "service.name", "value": {"stringValue": "observ-opencode"}},
            {"key": "ant.agent.env", "value": {"stringValue": "test"}},
            {"key": "ant.agent.scene", "value": {"stringValue": "default"}},
            {"key": "agentic.collection.mode", "value": {"stringValue": "full"}}
        ]
    })
}

fn cchooks_resource() -> Value {
    json!({
        "attributes": [
            {"key": "ant.agent.type", "value": {"stringValue": "claudecode"}},
            {"key": "service.name", "value": {"stringValue": "cchooks"}},
            {"key": "telemetry.sdk.name", "value": {"stringValue": "cchooks"}},
            {"key": "ant.agent.env", "value": {"stringValue": "test"}},
            {"key": "ant.agent.scene", "value": {"stringValue": "default"}},
            {"key": "agentic.collection.mode", "value": {"stringValue": "full"}},
            {"key": "ant.meta.trigger", "value": {"stringValue": "unknown"}}
        ]
    })
}

fn cdxhooks_resource() -> Value {
    json!({
        "attributes": [
            {"key": "ant.agent.type", "value": {"stringValue": "cdxhooks"}},
            {"key": "service.name", "value": {"stringValue": "cdxhooks"}},
            {"key": "telemetry.sdk.name", "value": {"stringValue": "cdxhooks"}},
            {"key": "ant.agent.env", "value": {"stringValue": "test"}},
            {"key": "ant.agent.scene", "value": {"stringValue": "default"}},
            {"key": "agentic.collection.mode", "value": {"stringValue": "full"}},
            {"key": "ant.meta.trigger", "value": {"stringValue": "unknown"}}
        ]
    })
}

fn base_span_attrs() -> Vec<Value> {
    span_attrs("COMMON", "chat")
}

fn span_attrs(kind: &str, operation: &str) -> Vec<Value> {
    vec![
        json!({"key": "user.id", "value": {"stringValue": "user-1"}}),
        json!({"key": "ant.username", "value": {"stringValue": "test-user"}}),
        json!({"key": "gen_ai.conversation.id", "value": {"stringValue": "conversation-1"}}),
        json!({"key": "gen_ai.chat.id", "value": {"stringValue": "chat-1"}}),
        json!({"key": "gen_ai.operation.name", "value": {"stringValue": operation}}),
        json!({"key": "gen_ai.span.kind", "value": {"stringValue": kind}}),
    ]
}

fn turn_span_attrs(runtime: &str, trigger: &str) -> Vec<Value> {
    let mut attrs = span_attrs("CHAT", "invoke_agent");
    attrs.extend([
        json!({"key": "agentic.runtime.name", "value": {"stringValue": runtime}}),
        json!({"key": "agentic.turn.number", "value": {"intValue": 1}}),
        json!({"key": "agentic.turn.trigger", "value": {"stringValue": trigger}}),
    ]);
    attrs
}

fn full_tool_span_attrs(tool_type: &str, tool_name: &str) -> Vec<Value> {
    let mut attrs = span_attrs("TOOL", "execute_tool");
    attrs.extend([
        json!({"key": "gen_ai.tool.name", "value": {"stringValue": tool_name}}),
        json!({"key": "gen_ai.tool.call.id", "value": {"stringValue": "toolu_1"}}),
        json!({"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "{}"}}),
        json!({"key": "gen_ai.tool.call.result", "value": {"stringValue": "{}"}}),
        json!({"key": "agentic.tool.duration_ms", "value": {"intValue": 10}}),
        json!({"key": "agentic.tool.type", "value": {"stringValue": tool_type}}),
    ]);
    attrs
}

fn without_span_attr(attributes: Vec<Value>, missing_key: &str) -> Vec<Value> {
    attributes
        .into_iter()
        .filter(|attr| attr.get("key").and_then(Value::as_str) != Some(missing_key))
        .collect()
}

fn span_sample(name: &str, attributes: Vec<Value>, resource: Value) -> Value {
    json!({
        "sample": {
            "span": {
                "name": name,
                "attributes": attributes
            },
            "resource": resource
        }
    })
}

fn log_attr(key: &str, value: Value) -> Value {
    json!({ "key": key, "value": value })
}

fn string_value(value: &str) -> Value {
    json!({ "stringValue": value })
}

fn int_value(value: i64) -> Value {
    json!({ "intValue": value })
}

fn api_request_log_sample(attributes: Vec<Value>) -> Value {
    json!({
        "sample": {
            "log": {
                "traceId": "0123456789abcdef0123456789abcdef",
                "spanId": "0123456789abcdef",
                "attributes": attributes
            }
        }
    })
}

fn metric_sample(metric: Value) -> Value {
    json!({
        "sample": {
            "metric": metric,
            "resource": valid_resource()
        }
    })
}

fn metric_attr(key: &str, value: &str) -> Value {
    json!({ "key": key, "value": { "stringValue": value } })
}

fn metric_attr_raw(key: &str, value: Value) -> Value {
    json!({ "key": key, "value": value })
}

fn api_metric_attrs() -> Vec<Value> {
    vec![
        metric_attr("ant.username", "test-user"),
        metric_attr("gen_ai.request.model", "openai/gpt-4.1"),
    ]
}

fn tool_metric_attrs() -> Vec<Value> {
    vec![
        metric_attr("ant.username", "test-user"),
        metric_attr("agentic.tool.type", "builtin"),
    ]
}

fn api_error_metric_attrs() -> Vec<Value> {
    let mut attrs = api_metric_attrs();
    attrs.push(metric_attr("agentic.error.category", "timeout"));
    attrs
}

fn tool_error_metric_attrs() -> Vec<Value> {
    let mut attrs = tool_metric_attrs();
    attrs.push(metric_attr("agentic.error.category", "tool_error"));
    attrs
}

fn sum_data_point(attributes: Vec<Value>, value: i64) -> Value {
    json!({
        "attributes": attributes,
        "asInt": value
    })
}

fn histogram_data_point(attributes: Vec<Value>, sum: i64) -> Value {
    json!({
        "attributes": attributes,
        "count": 1,
        "sum": sum,
        "bucketCounts": [1],
        "explicitBounds": [100]
    })
}

fn sum_metric(name: &str, unit: &str, data_points: Vec<Value>, is_monotonic: bool) -> Value {
    json!({
        "name": name,
        "unit": unit,
        "sum": {
            "aggregationTemporality": 2,
            "isMonotonic": is_monotonic,
            "dataPoints": data_points
        }
    })
}

fn histogram_metric(name: &str, unit: &str, data_points: Vec<Value>) -> Value {
    json!({
        "name": name,
        "unit": unit,
        "histogram": {
            "aggregationTemporality": 2,
            "dataPoints": data_points
        }
    })
}

fn full_api_request_log_attrs() -> Vec<Value> {
    vec![
        log_attr("event.name", string_value("api_request")),
        log_attr("gen_ai.operation.name", string_value("chat")),
        log_attr("ant.username", string_value("test-user")),
        log_attr("gen_ai.conversation.id", string_value("conversation-1")),
        log_attr("agentic.runtime.name", string_value("opencode")),
        log_attr("gen_ai.chat.id", string_value("chat-1")),
        log_attr("agentic.turn.number", int_value(1)),
        log_attr("gen_ai.request.model", string_value("openai/gpt-4.1")),
        log_attr("duration_ms", int_value(42)),
    ]
}

fn full_success_api_request_log_attrs() -> Vec<Value> {
    let mut attrs = full_api_request_log_attrs();
    attrs.extend([
        log_attr("gen_ai.response.model", string_value("openai/gpt-4.1")),
        log_attr("gen_ai.usage.input_tokens", int_value(10)),
        log_attr("gen_ai.usage.output_tokens", int_value(5)),
        log_attr("gen_ai.usage.total_tokens", int_value(15)),
        log_attr("gen_ai.input.messages", string_value("[]")),
        log_attr("gen_ai.output.messages", string_value("[]")),
        log_attr("gen_ai.system_instructions", string_value("[]")),
    ]);
    attrs
}

fn native_auxiliary_api_request_attrs(agent: &str) -> Vec<Value> {
    let mut attrs = without_log_attr(full_success_api_request_log_attrs(), "agentic.turn.number");
    attrs = without_log_attr(attrs, "gen_ai.chat.id");
    attrs.extend([
        log_attr("gen_ai.chat.id", string_value(&format!("aux:{agent}:msg_official"))),
        log_attr("gen_ai.agent.name", string_value(agent)),
        log_attr("agentic.llm.auxiliary", json!({"boolValue": true})),
    ]);
    attrs
}

#[test]
fn test_api_request_only_strict_native_auxiliary_calls_omit_turn_number() {
    let mut engine = engine();
    for agent in ["title", "summary", "compaction"] {
        for direct in [false, true] {
            let mut attrs = native_auxiliary_api_request_attrs(agent);
            if direct {
                attrs = without_log_attr(attrs, "agentic.llm.auxiliary");
                attrs.push(json!({"name": "agentic.llm.auxiliary", "value": true}));
            }
            let findings = engine.validate(&api_request_log_sample(attrs)).unwrap();
            assert!(!has_finding_attr(&findings, "event_missing_required_attribute", "agentic.turn.number"),
                "Known {agent} background call with a real source ID has no Turn ordinal: {findings:?}");
        }
    }

    let mut negatives: Vec<(&str, Vec<Value>)> = vec![
        ("missing marker", without_log_attr(native_auxiliary_api_request_attrs("title"), "agentic.llm.auxiliary")),
        ("unknown agent", native_auxiliary_api_request_attrs("unknown-background-agent")),
        ("primary agent", native_auxiliary_api_request_attrs("build")),
        ("missing source agent", without_log_attr(native_auxiliary_api_request_attrs("title"), "gen_ai.agent.name")),
        ("missing runtime", without_log_attr(native_auxiliary_api_request_attrs("title"), "agentic.runtime.name")),
    ];
    for marker in [json!({"boolValue": false}), string_value("true"), int_value(1)] {
        let mut attrs = without_log_attr(native_auxiliary_api_request_attrs("title"), "agentic.llm.auxiliary");
        attrs.push(log_attr("agentic.llm.auxiliary", marker));
        negatives.push(("invalid marker type/value", attrs));
    }
    for value in [json!(false), json!("true"), json!(1)] {
        let mut attrs = without_log_attr(native_auxiliary_api_request_attrs("title"), "agentic.llm.auxiliary");
        attrs.push(json!({"name": "agentic.llm.auxiliary", "value": value}));
        negatives.push(("invalid direct marker type/value", attrs));
    }
    for chat in ["chat-1", "aux:summary:msg_official", "aux:title:", "aux:title:msg with space"] {
        let mut attrs = without_log_attr(native_auxiliary_api_request_attrs("title"), "gen_ai.chat.id");
        attrs.push(log_attr("gen_ai.chat.id", string_value(chat)));
        negatives.push(("unrelated/missing source ID", attrs));
    }
    let mut duplicate = native_auxiliary_api_request_attrs("title");
    duplicate.push(log_attr("agentic.llm.auxiliary", json!({"boolValue": false})));
    negatives.push(("conflicting duplicate marker", duplicate));
    for (name, attrs) in negatives {
        let findings = engine.validate(&api_request_log_sample(attrs)).unwrap();
        assert!(has_finding_attr(&findings, "event_missing_required_attribute", "agentic.turn.number"),
            "{name} must retain the primary-request Turn requirement: {findings:?}");
    }
    for missing in ["duration_ms", "gen_ai.request.model", "gen_ai.usage.input_tokens", "gen_ai.output.messages"] {
        let attrs = without_log_attr(native_auxiliary_api_request_attrs("title"), missing);
        let findings = engine.validate(&api_request_log_sample(attrs)).unwrap();
        assert!(has_finding_attr(&findings, "event_missing_required_attribute", missing),
            "Auxiliary marker cannot waive {missing}: {findings:?}");
    }
}

#[derive(Clone, Copy)]
struct ToolCallProfile {
    runtime: &'static str,
    agent_type: &'static str,
    agent_product: &'static str,
    service_name: &'static str,
    tool_type: &'static str,
    raw_tool_name: &'static str,
    logical_tool_name: &'static str,
    call_id: &'static str,
}

const OPENCLAW_TOOL_CALL: ToolCallProfile = ToolCallProfile {
    runtime: "openclaw",
    agent_type: "openclaw",
    agent_product: "OpenClaw",
    service_name: "openclaw",
    tool_type: "builtin",
    raw_tool_name: "read",
    logical_tool_name: "read",
    call_id: "ocw-call-1",
};

const OPENCODE_TOOL_CALL: ToolCallProfile = ToolCallProfile {
    runtime: "opencode",
    agent_type: "opencode",
    agent_product: "OpenCode",
    service_name: "opencode",
    tool_type: "mcp",
    raw_tool_name: "mcp__filesystem__read_file",
    logical_tool_name: "read_file",
    call_id: "ocd-call-1",
};

const PI_TOOL_CALL: ToolCallProfile = ToolCallProfile {
    runtime: "pi",
    agent_type: "pi",
    agent_product: "Pi",
    service_name: "pi",
    tool_type: "skill",
    raw_tool_name: "skill:weather",
    logical_tool_name: "weather",
    call_id: "pi-call-1",
};

const TOOL_CALL_REQUIRED_ATTRS: [&str; 7] = [
    "gen_ai.operation.name",
    "agentic.turn.number",
    "gen_ai.tool.name",
    "gen_ai.tool.call.id",
    "agentic.tool.type",
    "agentic.tool.name",
    "agentic.tool.id",
];

enum ToolCallInput<'a> {
    Arguments(&'a str),
    Missing,
    Truncated(&'a str),
}

fn tool_call_resource(profile: ToolCallProfile, collection_mode: &str) -> Value {
    json!({
        "attributes": [
            log_attr("ant.agent.type", string_value(profile.agent_type)),
            log_attr("ant.agent.product", string_value(profile.agent_product)),
            log_attr("ant.agent.instance", string_value("test-instance")),
            log_attr("ant.agent.env", string_value("test")),
            log_attr("ant.agent.scene", string_value("default")),
            log_attr("service.name", string_value(profile.service_name)),
            log_attr("service.version", string_value("test-version")),
            log_attr("agentic.collection.mode", string_value(collection_mode))
        ]
    })
}

fn tool_call_log_attrs(profile: ToolCallProfile, input: ToolCallInput<'_>) -> Vec<Value> {
    let mut attrs = vec![
        log_attr("event.name", string_value("tool_call")),
        log_attr("gen_ai.operation.name", string_value("execute_tool")),
        log_attr("ant.username", string_value("test-user")),
        log_attr("gen_ai.conversation.id", string_value("conversation-1")),
        log_attr("gen_ai.chat.id", string_value("chat-1")),
        log_attr("agentic.runtime.name", string_value(profile.runtime)),
        log_attr("event.sequence", int_value(1)),
        log_attr("agentic.turn.number", int_value(1)),
        log_attr("gen_ai.tool.name", string_value(profile.raw_tool_name)),
        log_attr("gen_ai.tool.call.id", string_value(profile.call_id)),
        log_attr("agentic.tool.type", string_value(profile.tool_type)),
        log_attr("agentic.tool.name", string_value(profile.logical_tool_name)),
        log_attr("agentic.tool.raw_name", string_value(profile.raw_tool_name)),
        log_attr("agentic.tool.id", string_value(profile.call_id)),
    ];
    match input {
        ToolCallInput::Arguments(value) => {
            attrs.push(log_attr("gen_ai.tool.call.arguments", string_value(value)))
        }
        ToolCallInput::Missing => {
            attrs.push(log_attr("agentic.tool.input.missing", string_value("true")))
        }
        ToolCallInput::Truncated(value) => attrs.extend([
            log_attr("gen_ai.tool.call.arguments", string_value(value)),
            log_attr("agentic.tool.input.truncated", string_value("true")),
        ]),
    }
    attrs
}

fn tool_call_sample(
    profile: ToolCallProfile,
    collection_mode: &str,
    input: ToolCallInput<'_>,
) -> Value {
    let mut sample = api_request_log_sample(tool_call_log_attrs(profile, input));
    sample["sample"]["resource"] = tool_call_resource(profile, collection_mode);
    sample
}

fn full_tool_call_log_attrs() -> Vec<Value> {
    tool_call_log_attrs(PI_TOOL_CALL, ToolCallInput::Arguments("{}"))
}

fn tool_call_finding_ids(findings: &[weaver_guard::ValidationFinding]) -> BTreeSet<String> {
    findings
        .iter()
        .filter(|finding| finding.advice_type.starts_with("tool_call_"))
        .map(|finding| finding.advice_type.clone())
        .collect()
}

fn without_log_attr(attributes: Vec<Value>, missing_key: &str) -> Vec<Value> {
    attributes
        .into_iter()
        .filter(|attr| attr.get("key").and_then(Value::as_str) != Some(missing_key))
        .collect()
}

fn has_finding(findings: &[weaver_guard::ValidationFinding], id: &str, level: &str) -> bool {
    findings
        .iter()
        .any(|finding| finding.advice_type == id && finding.advice_level == level)
}

fn has_finding_id(findings: &[weaver_guard::ValidationFinding], id: &str) -> bool {
    findings.iter().any(|finding| finding.advice_type == id)
}

fn has_finding_attr(findings: &[weaver_guard::ValidationFinding], id: &str, attr: &str) -> bool {
    findings.iter().any(|finding| {
        finding.advice_type == id
            && finding.context.get("attribute").and_then(Value::as_str) == Some(attr)
    })
}

fn has_finding_field(findings: &[weaver_guard::ValidationFinding], id: &str, field: &str) -> bool {
    findings.iter().any(|finding| {
        finding.advice_type == id
            && finding.context.get("field").and_then(Value::as_str) == Some(field)
    })
}

fn assert_tool_call_finding_set(
    findings: &[weaver_guard::ValidationFinding],
    expected: &[&str],
    case: &str,
) {
    let expected_ids = expected.iter().map(|id| (*id).to_owned()).collect();
    assert_eq!(
        tool_call_finding_ids(findings),
        expected_ids,
        "Unexpected tool_call finding set for {case}: {findings:?}",
    );
    for expected_id in expected {
        assert!(
            findings.iter().any(|finding| {
                finding.advice_type == *expected_id
                    && finding.advice_level == "violation"
                    && finding.context.get("event_name").and_then(Value::as_str)
                        == Some("tool_call")
            }),
            "Expected violation {expected_id} with tool_call context for {case}: {findings:?}",
        );
    }
}

#[test]
fn test_validation_engine_loads_policies() {
    let _ = engine();
}

#[test]
fn test_missing_resource_identity_violation() {
    let mut engine = engine();
    let sample = span_sample(
        "custom span",
        base_span_attrs(),
        json!({ "attributes": [] }),
    );

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "resource_missing_agent_type", "violation"),
        "Expected resource_missing_agent_type violation, got: {:?}",
        findings
    );
}

#[test]
fn test_cchooks_missing_ant_meta_trigger_violation() {
    let mut engine = engine();
    let mut resource = cchooks_resource();
    resource["attributes"]
        .as_array_mut()
        .unwrap()
        .retain(|attr| attr.get("key").and_then(Value::as_str) != Some("ant.meta.trigger"));
    let sample = span_sample("custom span", base_span_attrs(), resource);

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "resource_missing_ant_meta_trigger", "violation"),
        "Expected resource_missing_ant_meta_trigger violation, got: {:?}",
        findings
    );
}

#[test]
fn test_cdxhooks_missing_ant_meta_trigger_violation() {
    let mut engine = engine();
    let mut resource = cdxhooks_resource();
    resource["attributes"]
        .as_array_mut()
        .unwrap()
        .retain(|attr| attr.get("key").and_then(Value::as_str) != Some("ant.meta.trigger"));
    let sample = span_sample("custom span", base_span_attrs(), resource);

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "resource_missing_ant_meta_trigger", "violation"),
        "Expected cdxhooks resource_missing_ant_meta_trigger violation, got: {:?}",
        findings
    );
}

#[test]
fn test_non_supported_resource_producer_missing_ant_meta_trigger_has_no_trigger_specific_violation()
{
    let mut engine = engine();
    let sample = span_sample("custom span", base_span_attrs(), valid_resource());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        !has_finding_id(&findings, "resource_missing_ant_meta_trigger"),
        "Non-supported Resource producers must not require ant.meta.trigger: {:?}",
        findings
    );
}

#[test]
fn test_valid_span_no_violations() {
    let mut engine = engine();
    let sample = span_sample("custom span", base_span_attrs(), valid_resource());

    let findings = engine.validate(&sample).unwrap();

    let violations: Vec<_> = findings
        .iter()
        .filter(|finding| finding.advice_level == "violation")
        .collect();
    assert!(
        violations.is_empty(),
        "Unexpected violations: {:?}",
        violations
    );
}

#[test]
fn test_trace_common_required_attrs_are_validated_by_common_rules() {
    let cases = [
        ("custom span", base_span_attrs()),
        ("Turn 1", {
            let mut attrs = span_attrs("CHAT", "invoke_agent");
            attrs.extend([
                json!({"key": "agentic.runtime.name", "value": {"stringValue": "opencode"}}),
                json!({"key": "agentic.turn.number", "value": {"intValue": 1}}),
            ]);
            attrs
        }),
        ("tool Bash", {
            let mut attrs = span_attrs("TOOL", "execute_tool");
            attrs.extend([
                json!({"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "{}"}}),
                json!({"key": "gen_ai.tool.call.result", "value": {"stringValue": "{}"}}),
                json!({"key": "agentic.tool.duration_ms", "value": {"intValue": 10}}),
            ]);
            attrs
        }),
        ("chat gpt-4.1", {
            let mut attrs = span_attrs("LLM", "chat");
            attrs.extend([
                json!({"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4.1"}}),
                json!({"key": "gen_ai.usage.input_tokens", "value": {"intValue": 10}}),
                json!({"key": "gen_ai.usage.output_tokens", "value": {"intValue": 5}}),
                json!({"key": "agentic.llm.duration_ms", "value": {"intValue": 100}}),
            ]);
            attrs
        }),
    ];

    for missing in [
        ("ant.username", "semconv_all_missing_username"),
        ("gen_ai.conversation.id", "span_missing_conversation_id"),
        ("gen_ai.operation.name", "span_missing_operation_name"),
        ("gen_ai.span.kind", "span_missing_span_kind"),
    ] {
        for (span_name, attrs) in cases.clone() {
            let mut engine = engine();
            let sample = span_sample(
                span_name,
                without_span_attr(attrs, missing.0),
                valid_resource(),
            );

            let findings = engine.validate(&sample).unwrap();

            assert!(
                has_finding(&findings, missing.1, "violation"),
                "Expected common violation {} for {span_name}, got: {:?}",
                missing.1,
                findings
            );
            for obsolete_id in [
                "turn_missing_username",
                "tool_missing_username",
                "turn_missing_session_id",
                "turn_missing_operation_name",
                "semconv_turn_missing_span_kind",
                "tool_missing_operation_name",
                "semconv_tool_missing_span_kind",
                "llm_missing_operation_name",
                "semconv_llm_missing_span_kind",
            ] {
                assert!(
                    !has_finding_id(&findings, obsolete_id),
                    "Obsolete span-kind-specific common rule {obsolete_id} should not exist for {span_name}: {:?}",
                    findings
                );
            }
        }
    }
}

#[test]
fn test_trace_kind_specific_fixed_values_are_validated_by_kind_rules() {
    let cases = [
        (
            "Turn 1",
            {
                let mut attrs = span_attrs("COMMON", "invoke_agent");
                attrs.extend([
                    json!({"key": "agentic.runtime.name", "value": {"stringValue": "opencode"}}),
                    json!({"key": "agentic.turn.number", "value": {"intValue": 1}}),
                ]);
                attrs
            },
            "turn_invalid_span_kind",
        ),
        (
            "Turn 1",
            {
                let mut attrs = span_attrs("CHAT", "chat");
                attrs.extend([
                    json!({"key": "agentic.runtime.name", "value": {"stringValue": "opencode"}}),
                    json!({"key": "agentic.turn.number", "value": {"intValue": 1}}),
                ]);
                attrs
            },
            "turn_invalid_operation_name",
        ),
        (
            "tool Bash",
            {
                let mut attrs = span_attrs("TOOL", "chat");
                attrs.extend([
                    json!({"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "{}"}}),
                    json!({"key": "gen_ai.tool.call.result", "value": {"stringValue": "{}"}}),
                    json!({"key": "agentic.tool.duration_ms", "value": {"intValue": 10}}),
                ]);
                attrs
            },
            "tool_invalid_operation_name",
        ),
        (
            "skill Commit",
            {
                let mut attrs = span_attrs("TOOL", "invoke_agent");
                attrs.extend([
                    json!({"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "{}"}}),
                    json!({"key": "gen_ai.tool.call.result", "value": {"stringValue": "{}"}}),
                    json!({"key": "agentic.tool.duration_ms", "value": {"intValue": 10}}),
                ]);
                attrs
            },
            "tool_invalid_operation_name",
        ),
        (
            "agent SyncAgent",
            {
                let mut attrs = span_attrs("TOOL", "invoke_agent");
                attrs.extend([
                    json!({"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "{}"}}),
                    json!({"key": "gen_ai.tool.call.result", "value": {"stringValue": "{}"}}),
                    json!({"key": "agentic.tool.duration_ms", "value": {"intValue": 10}}),
                ]);
                attrs
            },
            "tool_invalid_operation_name",
        ),
        (
            "chat gpt-4.1",
            {
                let mut attrs = span_attrs("LLM", "execute_tool");
                attrs.extend([
                    json!({"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4.1"}}),
                    json!({"key": "gen_ai.usage.input_tokens", "value": {"intValue": 10}}),
                    json!({"key": "gen_ai.usage.output_tokens", "value": {"intValue": 5}}),
                    json!({"key": "agentic.llm.duration_ms", "value": {"intValue": 100}}),
                ]);
                attrs
            },
            "llm_invalid_operation_name",
        ),
        (
            "compaction",
            span_attrs("COMMON", "chat"),
            "compaction_invalid_span_kind",
        ),
    ];

    for (span_name, attrs, expected_rule) in cases {
        let mut engine = engine();
        let sample = span_sample(span_name, attrs, valid_resource());

        let findings = engine.validate(&sample).unwrap();

        assert!(
            has_finding(&findings, expected_rule, "violation"),
            "Expected fixed-value violation {expected_rule} for {span_name}, got: {:?}",
            findings
        );
    }
}

#[test]
fn test_agent_invocation_span_is_not_validated_as_tool_span() {
    let mut engine = engine();
    let attrs = span_attrs("AGENT", "invoke_agent");
    let sample = span_sample("agent SyncAgent", attrs, valid_resource());

    let findings = engine.validate(&sample).unwrap();

    for tool_rule in [
        "tool_invalid_operation_name",
        "tool_missing_call_arguments",
        "tool_missing_call_result",
        "tool_missing_duration",
        "tool_missing_tool_name",
        "tool_missing_call_id",
        "tool_span_naming_convention",
    ] {
        assert!(
            !has_finding_id(&findings, tool_rule),
            "Agent invocation span should not be classified as Tool for rule {tool_rule}: {:?}",
            findings
        );
    }
}

#[test]
fn test_builtin_tool_span_name_prefix_is_valid() {
    let mut engine = engine();
    let sample = span_sample(
        "builtin exec",
        full_tool_span_attrs("builtin", "exec"),
        valid_resource(),
    );

    let findings = engine.validate(&sample).unwrap();

    assert!(
        !has_finding_id(&findings, "tool_span_naming_convention"),
        "builtin-prefixed Tool span names should satisfy the naming convention: {:?}",
        findings
    );
}

#[test]
fn test_raw_tool_span_name_still_reports_naming_improvement() {
    let mut engine = engine();
    let sample = span_sample(
        "exec",
        full_tool_span_attrs("builtin", "exec"),
        valid_resource(),
    );

    let findings = engine.validate(&sample).unwrap();

    let finding = findings
        .iter()
        .find(|finding| finding.advice_type == "tool_span_naming_convention")
        .expect("raw Tool span name should still report the naming improvement");
    assert_eq!(finding.advice_level, "improvement");
    assert!(
        finding.message.contains("{tool|builtin|mcp|skill}"),
        "message should document builtin as an allowed prefix: {:?}",
        finding
    );
    assert!(
        finding
            .context
            .get("allowed_prefixes")
            .and_then(Value::as_array)
            .is_some_and(|prefixes| prefixes.iter().any(|value| value == "builtin")),
        "allowed_prefixes context should include builtin: {:?}",
        finding
    );
}

#[test]
fn test_turn_invoke_agent_operation_name_is_valid() {
    let mut engine = engine();
    let mut attrs = span_attrs("CHAT", "invoke_agent");
    attrs.extend([
        json!({"key": "agentic.runtime.name", "value": {"stringValue": "opencode"}}),
        json!({"key": "agentic.turn.number", "value": {"intValue": 1}}),
    ]);
    let sample = span_sample("Turn 1", attrs, valid_resource());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        !has_finding_id(&findings, "turn_invalid_operation_name"),
        "Turn root span should allow gen_ai.operation.name=invoke_agent: {:?}",
        findings
    );
}

#[test]
fn test_dsh_runtime_name_is_valid() {
    let mut engine = engine();
    let mut attrs = span_attrs("CHAT", "invoke_agent");
    attrs.extend([
        json!({"key": "agentic.runtime.name", "value": {"stringValue": "dsh"}}),
        json!({"key": "agentic.turn.number", "value": {"intValue": 1}}),
    ]);
    let sample = span_sample("Turn 1", attrs, valid_resource());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        !has_finding_id(&findings, "invalid_runtime_name"),
        "dsh must be accepted as a supported runtime: {:?}",
        findings
    );
}

#[test]
fn test_turn_trigger_values_are_valid() {
    for trigger in [
        "interactive",
        "programmatic",
        "queued",
        "system",
        "subagent",
        "unknown",
    ] {
        let mut engine = engine();
        let sample = span_sample(
            "Turn 1",
            turn_span_attrs("codex", trigger),
            valid_resource(),
        );

        let findings = engine.validate(&sample).unwrap();

        assert!(
            !has_finding_id(&findings, "turn_invalid_trigger")
                && !has_finding_id(&findings, "turn_missing_trigger"),
            "Expected valid turn trigger {trigger}, got: {:?}",
            findings
        );
    }
}

#[test]
fn test_claude_code_turn_missing_trigger_violation() {
    let mut engine = engine();
    let attrs = without_span_attr(
        turn_span_attrs("claude-code", "unknown"),
        "agentic.turn.trigger",
    );
    let sample = span_sample("Turn 1", attrs, valid_resource());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "turn_missing_trigger", "violation"),
        "Expected turn_missing_trigger violation, got: {:?}",
        findings
    );
}

#[test]
fn test_codex_turn_missing_trigger_violation() {
    let mut engine = engine();
    let attrs = without_span_attr(turn_span_attrs("codex", "unknown"), "agentic.turn.trigger");
    let sample = span_sample("Turn 1", attrs, valid_resource());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "turn_missing_trigger", "violation"),
        "Expected codex turn_missing_trigger violation, got: {:?}",
        findings
    );
}

#[test]
fn test_non_supported_turn_runtime_missing_trigger_has_no_trigger_specific_violation() {
    let mut engine = engine();
    let mut attrs = span_attrs("CHAT", "invoke_agent");
    attrs.extend([
        json!({"key": "agentic.runtime.name", "value": {"stringValue": "opencode"}}),
        json!({"key": "agentic.turn.number", "value": {"intValue": 1}}),
    ]);
    let sample = span_sample("Turn 1", attrs, valid_resource());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        !has_finding_id(&findings, "turn_missing_trigger"),
        "Non-supported Turn runtimes must not require agentic.turn.trigger: {:?}",
        findings
    );
}

#[test]
fn test_turn_invalid_trigger_violation() {
    let mut engine = engine();
    let sample = span_sample(
        "Turn 1",
        turn_span_attrs("opencode", "automation"),
        valid_resource(),
    );

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "turn_invalid_trigger", "violation"),
        "Expected turn_invalid_trigger violation, got: {:?}",
        findings
    );
}

#[test]
fn test_turn_missing_runtime_name_violation() {
    let mut engine = engine();
    let mut attrs = span_attrs("CHAT", "invoke_agent");
    attrs.extend([
        json!({"key": "agentic.turn.number", "value": {"intValue": 1}}),
        json!({"key": "ant.agent.scene", "value": {"stringValue": "default"}}),
    ]);
    let sample = span_sample("Turn 1", attrs, valid_resource());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "turn_missing_source", "violation"),
        "Expected turn_missing_source violation, got: {:?}",
        findings
    );
}

#[test]
fn test_multiple_violations() {
    let mut engine = engine();
    let sample = span_sample("custom span", vec![], json!({ "attributes": [] }));

    let findings = engine.validate(&sample).unwrap();

    let violations: Vec<_> = findings
        .iter()
        .filter(|finding| finding.advice_level == "violation")
        .collect();
    assert!(!violations.is_empty(), "Expected violations, got none");
}

#[test]
fn test_custom_agent_type_and_product_are_allowed() {
    let mut engine = engine();
    let sample = span_sample(
        "custom span",
        base_span_attrs(),
        json!({
            "attributes": [
                {"key": "ant.agent.type", "value": {"stringValue": "acme-custom-agent"}},
                {"key": "ant.agent.product", "value": {"stringValue": "acme-agent-enterprise"}},
                {"key": "service.name", "value": {"stringValue": "acme-agent-enterprise"}},
                {"key": "ant.agent.env", "value": {"stringValue": "test"}},
                {"key": "ant.agent.scene", "value": {"stringValue": "default"}},
                {"key": "agentic.collection.mode", "value": {"stringValue": "full"}}
            ]
        }),
    );

    let findings = engine.validate(&sample).unwrap();

    assert!(
        !has_finding_id(&findings, "resource_invalid_agent_type"),
        "Custom ant.agent.type/product values should be accepted: {:?}",
        findings
    );
}

#[test]
fn test_event_resource_allows_custom_agent_type_and_product() {
    let mut engine = engine();
    let sample = json!({
        "sample": {
            "log": {
                "traceId": "0123456789abcdef0123456789abcdef",
                "spanId": "0123456789abcdef",
                "attributes": full_api_request_log_attrs()
            },
            "resource": {
                "attributes": [
                    {"key": "ant.agent.type", "value": {"stringValue": "acme-custom-agent"}},
                    {"key": "ant.agent.product", "value": {"stringValue": "acme-agent-enterprise"}},
                    {"key": "service.name", "value": {"stringValue": "acme-agent-enterprise"}},
                    {"key": "ant.agent.env", "value": {"stringValue": "test"}},
                    {"key": "ant.agent.scene", "value": {"stringValue": "default"}},
                    {"key": "agentic.collection.mode", "value": {"stringValue": "full"}}
                ]
            }
        }
    });

    let findings = engine.validate(&sample).unwrap();

    assert!(
        !has_finding_id(&findings, "event_resource_invalid_agent_type"),
        "Custom event resource agent identity should be accepted: {:?}",
        findings
    );
}

#[test]
fn test_resource_forbids_span_identity_fields() {
    let mut engine = engine();
    let sample = span_sample(
        "custom span",
        base_span_attrs(),
        json!({
            "attributes": [
                {"key": "ant.agent.type", "value": {"stringValue": "opencode"}},
                {"key": "service.name", "value": {"stringValue": "observ-opencode"}},
                {"key": "ant.agent.env", "value": {"stringValue": "test"}},
                {"key": "ant.agent.scene", "value": {"stringValue": "default"}},
                {"key": "agentic.collection.mode", "value": {"stringValue": "full"}},
                {"key": "ant.username", "value": {"stringValue": "test-user"}},
                {"key": "user.id", "value": {"stringValue": "user-1"}}
            ]
        }),
    );

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "placement_span_only_in_resource", "violation"),
        "Expected placement_span_only_in_resource violation, got: {:?}",
        findings
    );
}

#[test]
fn test_resource_forbids_turn_trigger() {
    let mut engine = engine();
    let mut resource = valid_resource();
    resource["attributes"]
        .as_array_mut()
        .unwrap()
        .push(json!({"key": "agentic.turn.trigger", "value": {"stringValue": "interactive"}}));
    let sample = span_sample("custom span", base_span_attrs(), resource);

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding_attr(
            &findings,
            "placement_span_only_in_resource",
            "agentic.turn.trigger"
        ),
        "Expected placement_span_only_in_resource violation for agentic.turn.trigger, got: {:?}",
        findings
    );
}

#[test]
fn test_ant_meta_trigger_values_are_valid() {
    for trigger in ["", "automation", "interactive", "unknown"] {
        let mut engine = engine();
        let mut resource = cchooks_resource();
        resource["attributes"]
            .as_array_mut()
            .unwrap()
            .retain(|attr| attr.get("key").and_then(Value::as_str) != Some("ant.meta.trigger"));
        resource["attributes"]
            .as_array_mut()
            .unwrap()
            .push(json!({"key": "ant.meta.trigger", "value": {"stringValue": trigger}}));
        let sample = span_sample("custom span", base_span_attrs(), resource);

        let findings = engine.validate(&sample).unwrap();

        assert!(
            !has_finding_id(&findings, "resource_invalid_ant_meta_trigger")
                && !has_finding_id(&findings, "resource_missing_ant_meta_trigger"),
            "Expected valid ant.meta.trigger {trigger}, got: {:?}",
            findings
        );
    }
}

#[test]
fn test_invalid_ant_meta_trigger_violation() {
    let mut engine = engine();
    let mut resource = valid_resource();
    resource["attributes"]
        .as_array_mut()
        .unwrap()
        .retain(|attr| attr.get("key").and_then(Value::as_str) != Some("ant.meta.trigger"));
    resource["attributes"]
        .as_array_mut()
        .unwrap()
        .push(json!({"key": "ant.meta.trigger", "value": {"stringValue": "scheduled"}}));
    let sample = span_sample("custom span", base_span_attrs(), resource);

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "resource_invalid_ant_meta_trigger", "violation"),
        "Expected resource_invalid_ant_meta_trigger violation, got: {:?}",
        findings
    );
}

#[test]
fn test_span_forbids_resource_only_agent_scene() {
    let mut engine = engine();
    let mut attrs = base_span_attrs();
    attrs.push(json!({"key": "ant.agent.scene", "value": {"stringValue": "default"}}));
    let sample = span_sample("custom span", attrs, valid_resource());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding_attr(
            &findings,
            "placement_resource_only_in_span",
            "ant.agent.scene"
        ),
        "Expected placement_resource_only_in_span violation for ant.agent.scene, got: {:?}",
        findings
    );
}

#[test]
fn test_span_forbids_ant_meta_trigger() {
    let mut engine = engine();
    let mut attrs = base_span_attrs();
    attrs.push(json!({"key": "ant.meta.trigger", "value": {"stringValue": "automation"}}));
    let sample = span_sample("custom span", attrs, valid_resource());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding_attr(
            &findings,
            "placement_resource_only_in_span",
            "ant.meta.trigger"
        ),
        "Expected placement_resource_only_in_span violation for ant.meta.trigger, got: {:?}",
        findings
    );
}

#[test]
fn test_deprecated_gen_ai_prompt_violation() {
    let mut engine = engine();
    let mut attrs = base_span_attrs();
    attrs.push(json!({"key": "gen_ai.prompt", "value": {"stringValue": "hello"}}));
    let sample = span_sample("custom span", attrs, valid_resource());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "deprecated_gen_ai_prompt", "violation"),
        "Expected deprecated_gen_ai_prompt violation, got: {:?}",
        findings
    );
}

#[test]
fn test_unknown_gen_ai_provider_name_improvement() {
    let mut engine = engine();
    let mut attrs = base_span_attrs();
    attrs
        .push(json!({"key": "gen_ai.provider.name", "value": {"stringValue": "unknown-provider"}}));
    let sample = span_sample("custom span", attrs, valid_resource());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "unknown_gen_ai_provider_name", "improvement"),
        "Expected unknown_gen_ai_provider_name improvement, got: {:?}",
        findings
    );
}

#[test]
fn test_negative_input_tokens_violation() {
    let mut engine = engine();
    let mut attrs = base_span_attrs();
    attrs.push(json!({"key": "gen_ai.usage.input_tokens", "value": {"intValue": -1}}));
    let sample = span_sample("custom span", attrs, valid_resource());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "negative_input_tokens", "violation"),
        "Expected negative_input_tokens violation, got: {:?}",
        findings
    );
}

#[test]
fn test_custom_namespace_is_flagged_by_namespace_gate() {
    let mut engine = engine();
    let mut attrs = base_span_attrs();
    attrs.push(json!({"key": "custom.codex.phase", "value": {"stringValue": "post_tool"}}));
    let sample = span_sample("custom span", attrs, valid_resource());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "disallowed_attr_prefix", "violation"),
        "Expected disallowed_attr_prefix violation, got: {:?}",
        findings
    );
}

#[test]
fn test_event_common_required_attrs_are_validated_by_common_rule() {
    for missing_attr in [
        "gen_ai.operation.name",
        "ant.username",
        "gen_ai.conversation.id",
        "gen_ai.chat.id",
        "agentic.runtime.name",
    ] {
        let mut engine = engine();
        let sample =
            api_request_log_sample(without_log_attr(full_api_request_log_attrs(), missing_attr));

        let findings = engine.validate(&sample).unwrap();

        assert!(
            has_finding_attr(
                &findings,
                "event_missing_common_required_attribute",
                missing_attr
            ),
            "Expected common required attribute violation for {missing_attr}, got: {:?}",
            findings
        );
        assert!(
            !has_finding_attr(&findings, "event_missing_required_attribute", missing_attr),
            "{missing_attr} should not be checked by api_request-specific required rule: {:?}",
            findings
        );
    }
}

#[test]
fn test_event_specific_required_attrs_stay_event_specific() {
    let mut engine = engine();
    let sample = api_request_log_sample(without_log_attr(
        full_api_request_log_attrs(),
        "duration_ms",
    ));

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding_attr(&findings, "event_missing_required_attribute", "duration_ms"),
        "Expected api_request-specific violation for duration_ms, got: {:?}",
        findings
    );
    assert!(
        !has_finding(
            &findings,
            "event_missing_common_required_attribute",
            "violation"
        ),
        "No common required attributes should be missing: {:?}",
        findings
    );
}

#[test]
fn test_event_resource_only_attrs_are_forbidden_in_log_attributes() {
    let mut engine = engine();
    let mut attrs = full_api_request_log_attrs();
    attrs.push(log_attr("ant.agent.scene", string_value("default")));
    let sample = api_request_log_sample(attrs);

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding_attr(
            &findings,
            "event_placement_resource_only_in_log",
            "ant.agent.scene"
        ),
        "Expected resource-only placement violation for ant.agent.scene, got: {:?}",
        findings
    );
}

#[test]
fn test_event_namespace_gate_allows_name_format_bare_duration() {
    let mut engine = engine();
    let mut attrs = without_log_attr(full_api_request_log_attrs(), "duration_ms");
    attrs.push(json!({"name": "duration_ms", "value": 42}));
    let sample = api_request_log_sample(attrs);

    let findings = engine.validate(&sample).unwrap();

    assert!(
        !has_finding_attr(&findings, "disallowed_event_attr_prefix", "duration_ms"),
        "duration_ms is a registered event bare field and should be allowed in name format: {:?}",
        findings
    );
    assert!(
        !has_finding_attr(&findings, "event_missing_required_attribute", "duration_ms"),
        "duration_ms in name format should satisfy api_request required attrs: {:?}",
        findings
    );
}

#[test]
fn test_valid_tool_call_producer_profiles_pass_policy_and_semconv() {
    let cases = [
        (
            "openclaw arguments",
            OPENCLAW_TOOL_CALL,
            "full",
            ToolCallInput::Arguments(r#"{"path":"/tmp/example"}"#),
        ),
        (
            "openclaw privacy redaction",
            OPENCLAW_TOOL_CALL,
            "privacy",
            ToolCallInput::Arguments("[REDACTED]"),
        ),
        (
            "opencode arguments",
            OPENCODE_TOOL_CALL,
            "full",
            ToolCallInput::Arguments(r#"{"path":"/tmp/example"}"#),
        ),
        (
            "opencode privacy redaction",
            OPENCODE_TOOL_CALL,
            "privacy",
            ToolCallInput::Arguments("[REDACTED]"),
        ),
        (
            "pi arguments",
            PI_TOOL_CALL,
            "full",
            ToolCallInput::Arguments(r#"{"city":"Hangzhou"}"#),
        ),
        (
            "pi explicit null",
            PI_TOOL_CALL,
            "full",
            ToolCallInput::Arguments("null"),
        ),
        (
            "pi missing arguments",
            PI_TOOL_CALL,
            "full",
            ToolCallInput::Missing,
        ),
        (
            "pi truncated arguments",
            PI_TOOL_CALL,
            "full",
            ToolCallInput::Truncated(r#"{"city":"Hang"#),
        ),
    ];
    let registry = load_semconv_registry(&get_semconv_dir()).unwrap();

    for (name, profile, collection_mode, input) in cases {
        let sample = tool_call_sample(profile, collection_mode, input);
        let mut engine = engine();
        let findings = engine.validate(&sample).unwrap();
        assert!(
            findings
                .iter()
                .all(|finding| finding.advice_level != "violation"),
            "Unexpected policy violation for {name}: {findings:?}",
        );
        assert!(
            tool_call_finding_ids(&findings).is_empty(),
            "Unexpected tool_call finding for {name}: {findings:?}",
        );

        let semconv_findings =
            diff_attributes_from_signal_type(&registry, SignalType::Logs, &sample);
        assert!(
            semconv_findings
                .iter()
                .all(|finding| finding.finding_type != SemconvFindingType::UnknownAttribute),
            "Unexpected semconv finding for {name}: {semconv_findings:?}",
        );
    }
}

#[test]
fn test_tool_call_input_state_rules_are_disjoint() {
    struct Case {
        name: &'static str,
        arguments: bool,
        missing: Option<Value>,
        truncated: Option<Value>,
        expected: &'static [&'static str],
    }

    let cases = [
        Case { name: "arguments", arguments: true, missing: None, truncated: None, expected: &[] },
        Case { name: "missing marker", arguments: false, missing: Some(string_value("true")), truncated: None, expected: &[] },
        Case { name: "truncated arguments", arguments: true, missing: None, truncated: Some(string_value("true")), expected: &[] },
        Case { name: "no input state", arguments: false, missing: None, truncated: None, expected: &["tool_call_missing_input_state"] },
        Case { name: "arguments conflict with missing", arguments: true, missing: Some(string_value("true")), truncated: None, expected: &["tool_call_conflicting_input_state"] },
        Case { name: "boolean missing with arguments", arguments: true, missing: Some(json!({"boolValue": true})), truncated: None, expected: &["tool_call_invalid_missing_marker"] },
        Case { name: "boolean missing without arguments", arguments: false, missing: Some(json!({"boolValue": true})), truncated: None, expected: &["tool_call_invalid_missing_marker"] },
        Case { name: "false missing marker", arguments: false, missing: Some(string_value("false")), truncated: None, expected: &["tool_call_invalid_missing_marker"] },
        Case { name: "truncated without arguments", arguments: false, missing: None, truncated: Some(string_value("true")), expected: &["tool_call_truncated_without_arguments"] },
        Case { name: "missing plus truncated without arguments", arguments: false, missing: Some(string_value("true")), truncated: Some(string_value("true")), expected: &["tool_call_truncated_without_arguments"] },
        Case { name: "boolean truncated without arguments", arguments: false, missing: None, truncated: Some(json!({"boolValue": true})), expected: &["tool_call_invalid_truncated_marker"] },
        Case { name: "boolean truncated with arguments", arguments: true, missing: None, truncated: Some(json!({"boolValue": true})), expected: &["tool_call_invalid_truncated_marker"] },
        Case { name: "false truncated marker", arguments: true, missing: None, truncated: Some(string_value("false")), expected: &["tool_call_invalid_truncated_marker"] },
        Case { name: "arguments missing and truncated", arguments: true, missing: Some(string_value("true")), truncated: Some(string_value("true")), expected: &["tool_call_conflicting_input_state"] },
        Case { name: "invalid missing and truncated without arguments", arguments: false, missing: Some(json!({"boolValue": true})), truncated: Some(string_value("true")), expected: &["tool_call_invalid_missing_marker", "tool_call_truncated_without_arguments"] },
        Case { name: "both markers invalid", arguments: false, missing: Some(json!({"boolValue": true})), truncated: Some(json!({"boolValue": true})), expected: &["tool_call_invalid_missing_marker", "tool_call_invalid_truncated_marker"] },
    ];

    for case in cases {
        let mut attrs = full_tool_call_log_attrs();
        if !case.arguments {
            attrs = without_log_attr(attrs, "gen_ai.tool.call.arguments");
        }
        if let Some(value) = case.missing {
            attrs.push(log_attr("agentic.tool.input.missing", value));
        }
        if let Some(value) = case.truncated {
            attrs.push(log_attr("agentic.tool.input.truncated", value));
        }
        let mut engine = engine();
        let findings = engine.validate(&api_request_log_sample(attrs)).unwrap();
        assert_tool_call_finding_set(&findings, case.expected, case.name);
        for expected_id in case.expected {
            let expected_attribute = match *expected_id {
                "tool_call_missing_input_state" => "gen_ai.tool.call.arguments",
                "tool_call_conflicting_input_state" | "tool_call_invalid_missing_marker" => {
                    "agentic.tool.input.missing"
                }
                "tool_call_invalid_truncated_marker"
                | "tool_call_truncated_without_arguments" => "agentic.tool.input.truncated",
                unexpected => panic!("missing attribute expectation for {unexpected}"),
            };
            assert!(
                has_finding_attr(&findings, expected_id, expected_attribute),
                "Expected {expected_id} to identify {expected_attribute}: {findings:?}",
            );
        }
    }
}

#[test]
fn test_tool_call_dedicated_rules_use_stable_ids() {
    let cases: [(&str, Value, &str); 10] = [
        ("gen_ai.operation.name", string_value("chat"), "tool_call_wrong_operation_name"),
        ("agentic.tool.type", string_value("unknown"), "tool_call_invalid_tool_type"),
        ("duration_ms", int_value(1), "tool_call_forbidden_duration"),
        ("agentic.tool.duration_ms", int_value(1), "tool_call_forbidden_duration"),
        ("gen_ai.tool.call.result", string_value("ok"), "tool_call_forbidden_result"),
        ("error.type", string_value("failure"), "tool_call_forbidden_error"),
        ("error.message", string_value("failed"), "tool_call_forbidden_error"),
        ("success", json!({"boolValue": true}), "tool_call_forbidden_success"),
        ("agentic.tool.decision", string_value("allow"), "tool_call_forbidden_decision"),
        ("agentic.tool.decision.source", string_value("policy"), "tool_call_forbidden_decision"),
    ];

    for (attribute, value, expected_id) in cases {
        let mut attrs = without_log_attr(full_tool_call_log_attrs(), attribute);
        attrs.push(log_attr(attribute, value));
        let mut engine = engine();
        let findings = engine.validate(&api_request_log_sample(attrs)).unwrap();
        assert_tool_call_finding_set(&findings, &[expected_id], attribute);
        assert!(
            has_finding_attr(&findings, expected_id, attribute),
            "Expected {expected_id} to identify {attribute}: {findings:?}",
        );
    }

    for required_attribute in TOOL_CALL_REQUIRED_ATTRS {
        let mut engine = engine();
        let findings = engine
            .validate(&api_request_log_sample(without_log_attr(
                full_tool_call_log_attrs(),
                required_attribute,
            )))
            .unwrap();
        assert_tool_call_finding_set(
            &findings,
            &["tool_call_missing_required_attribute"],
            required_attribute,
        );
        assert!(
            has_finding_attr(
                &findings,
                "tool_call_missing_required_attribute",
                required_attribute,
            ),
            "Missing-field finding must identify {required_attribute}: {findings:?}",
        );
    }
}

#[test]
fn test_tool_call_common_envelope_rules_keep_stable_ids() {
    let cases = [
        (
            "unknown event name",
            {
                let mut attrs = without_log_attr(full_tool_call_log_attrs(), "event.name");
                attrs.push(log_attr("event.name", string_value("tool_started")));
                attrs
            },
            "event_unknown_name",
        ),
        (
            "resource-only field in log",
            {
                let mut attrs = full_tool_call_log_attrs();
                attrs.push(log_attr("ant.agent.scene", string_value("default")));
                attrs
            },
            "event_placement_resource_only_in_log",
        ),
        (
            "negative turn number",
            {
                let mut attrs = without_log_attr(full_tool_call_log_attrs(), "agentic.turn.number");
                attrs.push(log_attr("agentic.turn.number", int_value(-1)));
                attrs
            },
            "event_invalid_turn_number",
        ),
        (
            "non-numeric turn number",
            {
                let mut attrs = without_log_attr(full_tool_call_log_attrs(), "agentic.turn.number");
                attrs.push(log_attr("agentic.turn.number", string_value("not-a-number")));
                attrs
            },
            "event_invalid_turn_number",
        ),
        (
            "negative sequence",
            {
                let mut attrs = without_log_attr(full_tool_call_log_attrs(), "event.sequence");
                attrs.push(log_attr("event.sequence", int_value(-1)));
                attrs
            },
            "event_invalid_sequence",
        ),
        (
            "non-numeric sequence",
            {
                let mut attrs = without_log_attr(full_tool_call_log_attrs(), "event.sequence");
                attrs.push(log_attr("event.sequence", string_value("not-a-number")));
                attrs
            },
            "event_invalid_sequence",
        ),
    ];

    for (name, attrs, expected_id) in cases {
        let mut engine = engine();
        let findings = engine.validate(&api_request_log_sample(attrs)).unwrap();
        assert!(
            has_finding(&findings, expected_id, "violation"),
            "Expected {expected_id} for {name}, got: {:?}",
            findings
        );
    }

    for (field, expected_id) in [
        ("traceId", "event_missing_trace_id"),
        ("spanId", "event_missing_span_id"),
    ] {
        let mut sample = api_request_log_sample(full_tool_call_log_attrs());
        sample["sample"]["log"].as_object_mut().unwrap().remove(field);
        let mut engine = engine();
        let findings = engine.validate(&sample).unwrap();
        assert!(
            has_finding(&findings, expected_id, "violation")
                && has_finding_field(&findings, expected_id, field)
                && findings.iter().any(|finding| {
                    finding.advice_type == expected_id
                        && finding.context.get("event_name").and_then(Value::as_str)
                            == Some("tool_call")
                }),
            "Expected {expected_id} with {field} and tool_call context: {findings:?}",
        );
    }

    for (attribute, value) in [
        ("event.sequence", int_value(1)),
        ("gen_ai.tool.call.id", string_value("call-in-resource")),
    ] {
        let mut resource = valid_resource();
        resource["attributes"]
            .as_array_mut()
            .unwrap()
            .push(log_attr(attribute, value));
        let mut sample = api_request_log_sample(full_tool_call_log_attrs());
        sample["sample"]["resource"] = resource;
        let mut engine = engine();
        let findings = engine.validate(&sample).unwrap();
        assert!(
            has_finding(
                &findings,
                "event_placement_event_only_in_resource",
                "violation",
            ) && has_finding_attr(
                &findings,
                "event_placement_event_only_in_resource",
                attribute,
            ),
            "Expected event-only Resource finding for {attribute}: {findings:?}",
        );
    }
}

#[test]
fn test_tool_call_semconv_accepts_canonical_fields_and_reports_unknown_attribute() {
    let registry = load_semconv_registry(&get_semconv_dir()).unwrap();
    let valid = api_request_log_sample(full_tool_call_log_attrs());
    let valid_findings = diff_attributes_from_signal_type(&registry, SignalType::Logs, &valid);
    assert!(
        valid_findings
            .iter()
            .all(|finding| finding.finding_type != SemconvFindingType::UnknownAttribute),
        "Canonical tool_call fields must be registered: {:?}",
        valid_findings
    );

    let mut attrs = full_tool_call_log_attrs();
    attrs.push(log_attr(
        "agentic.tool.unregistered",
        string_value("unexpected"),
    ));
    let invalid = api_request_log_sample(attrs);
    let unknown: Vec<_> = diff_attributes_from_signal_type(&registry, SignalType::Logs, &invalid)
        .into_iter()
        .filter(|finding| finding.finding_type == SemconvFindingType::UnknownAttribute)
        .collect();
    assert_eq!(unknown.len(), 1, "Expected one unknown attribute: {unknown:?}");
    assert_eq!(unknown[0].attribute_id, "agentic.tool.unregistered");
}

#[test]
fn test_api_request_removed_cached_tokens_key_is_unknown_and_cannot_satisfy_recommendation() {
    let removed_key = ["agentic.usage", "cached_tokens"].join(".");
    let mut attrs = full_success_api_request_log_attrs();
    attrs.push(log_attr(&removed_key, int_value(10)));
    let sample = api_request_log_sample(attrs);

    let registry = load_semconv_registry(&get_semconv_dir()).unwrap();
    let unknown: Vec<_> = diff_attributes_from_signal_type(&registry, SignalType::Logs, &sample)
        .into_iter()
        .filter(|finding| finding.finding_type == SemconvFindingType::UnknownAttribute)
        .collect();
    assert_eq!(
        unknown.len(),
        1,
        "Expected one unknown attribute: {unknown:?}"
    );
    assert_eq!(unknown[0].attribute_id, removed_key);

    let mut engine = engine();
    let findings = engine.validate(&sample).unwrap();
    assert!(
        has_finding(
            &findings,
            "api_request_missing_cached_tokens",
            "improvement"
        ),
        "Removed cache field must not satisfy the canonical recommendation: {:?}",
        findings
    );
}

#[test]
fn test_api_request_cached_tokens_must_not_exceed_input_tokens() {
    let mut engine = engine();
    let mut attrs = full_success_api_request_log_attrs();
    attrs.push(log_attr("agentic.usage.cache_read_tokens", int_value(11)));
    let sample = api_request_log_sample(attrs);

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(
            &findings,
            "api_request_cached_tokens_exceeds_input_tokens",
            "violation"
        ),
        "Expected cached token upper-bound violation, got: {:?}",
        findings
    );
}

#[test]
fn test_api_request_cached_tokens_must_be_non_negative() {
    let mut engine = engine();
    let mut attrs = full_success_api_request_log_attrs();
    attrs.push(log_attr("agentic.usage.cache_read_tokens", int_value(-1)));
    let sample = api_request_log_sample(attrs);

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "api_request_invalid_cached_tokens", "violation"),
        "Expected cached_tokens non-negative violation, got: {:?}",
        findings
    );
}

#[test]
fn test_api_request_missing_cached_tokens_is_improvement() {
    let mut engine = engine();
    let sample = api_request_log_sample(full_success_api_request_log_attrs());

    let findings = engine.validate(&sample).unwrap();

    assert!(
        has_finding(
            &findings,
            "api_request_missing_cached_tokens",
            "improvement"
        ),
        "Expected missing cached_tokens improvement, got: {:?}",
        findings
    );
}

#[test]
fn test_api_request_cached_tokens_within_input_tokens_is_valid() {
    let mut engine = engine();
    let mut attrs = full_success_api_request_log_attrs();
    attrs.push(log_attr("agentic.usage.cache_read_tokens", int_value(10)));
    let sample = api_request_log_sample(attrs);

    let findings = engine.validate(&sample).unwrap();

    assert!(
        !has_finding(
            &findings,
            "api_request_cached_tokens_exceeds_input_tokens",
            "violation"
        ),
        "cached_tokens <= input_tokens should not violate: {:?}",
        findings
    );
    assert!(
        !has_finding(
            &findings,
            "api_request_missing_cached_tokens",
            "improvement"
        ),
        "cached_tokens present should not report missing improvement: {:?}",
        findings
    );
}

#[test]
fn test_valid_red_golden_v1_shapes_have_no_violations() {
    let metrics = [
        sum_metric(
            "agent.api_request.count",
            "{count}",
            vec![sum_data_point(api_metric_attrs(), 1)],
            true,
        ),
        sum_metric(
            "agent.api_request.error.count",
            "{count}",
            vec![sum_data_point(api_error_metric_attrs(), 1)],
            true,
        ),
        histogram_metric(
            "agent.api_request.duration",
            "ms",
            vec![histogram_data_point(api_metric_attrs(), 123)],
        ),
        sum_metric(
            "agent.tool_call.count",
            "{count}",
            vec![sum_data_point(tool_metric_attrs(), 1)],
            true,
        ),
        sum_metric(
            "agent.tool_call.error.count",
            "{count}",
            vec![sum_data_point(tool_error_metric_attrs(), 1)],
            true,
        ),
        histogram_metric(
            "agent.tool_call.duration",
            "ms",
            vec![histogram_data_point(tool_metric_attrs(), 42)],
        ),
    ];

    for metric in metrics {
        let mut engine = engine();
        let sample = metric_sample(metric);

        let findings = engine.validate(&sample).unwrap();

        assert!(
            findings.is_empty(),
            "Valid minimal RED golden metric should not produce findings of any level: {:?}",
            findings
        );
    }
}

#[test]
fn test_metric_required_attrs_are_checked_per_data_point() {
    let mut valid_attrs = api_metric_attrs();
    let mut missing_model_attrs = api_metric_attrs();
    missing_model_attrs
        .retain(|attr| attr.get("key").and_then(Value::as_str) != Some("gen_ai.request.model"));
    valid_attrs.push(metric_attr("agentic.error.category", "timeout"));

    let sample = metric_sample(sum_metric(
        "agent.api_request.count",
        "{count}",
        vec![
            sum_data_point(valid_attrs, 1),
            sum_data_point(missing_model_attrs, 1),
        ],
        true,
    ));

    let mut validation_engine = engine();
    let findings = validation_engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "api_request_metric_missing_model", "violation"),
        "Expected per-data-point missing model violation, got: {:?}",
        findings
    );
}

#[test]
fn test_error_count_metrics_require_error_category_per_data_point() {
    let cases = [
        ("agent.api_request.error.count", api_metric_attrs()),
        ("agent.tool_call.error.count", tool_metric_attrs()),
    ];

    for (metric_name, attrs) in cases {
        let sample = metric_sample(sum_metric(
            metric_name,
            "{count}",
            vec![sum_data_point(attrs, 1)],
            true,
        ));

        let mut engine = engine();
        let findings = engine.validate(&sample).unwrap();

        assert!(
            has_finding(
                &findings,
                "metric_error_count_missing_category",
                "violation"
            ),
            "Expected missing error category violation for {metric_name}, got: {:?}",
            findings
        );
    }
}

#[test]
fn test_non_normative_agent_metric_is_ignored_by_red_golden_rules() {
    let sample = metric_sample(sum_metric(
        "agent.cache.read_tokens",
        "tokens",
        vec![sum_data_point(
            vec![
                metric_attr("model", "legacy-short"),
                metric_attr("status", "error"),
                metric_attr("success", "false"),
                metric_attr("error.type", "VeryLongProviderSpecificExceptionName"),
                metric_attr("error.message", "full diagnostic detail"),
                metric_attr("custom.metric.dimension", "value"),
            ],
            1,
        )],
        false,
    ));

    let mut validation_engine = engine();
    let findings = validation_engine.validate(&sample).unwrap();

    assert!(
        findings.is_empty(),
        "Non-normative agent metric must not produce RED golden metric warnings, violations, prohibitions, or improvements: {:?}",
        findings
    );
}

#[test]
fn test_red_golden_metric_rejects_raw_error_details_and_invalid_error_category() {
    let mut attrs = api_metric_attrs();
    attrs.extend([
        metric_attr(
            "agentic.error.category",
            "OpenAIQuotaExceededErrorWithRequestSpecificDetails",
        ),
        metric_attr(
            "error.type",
            "OpenAIQuotaExceededErrorWithRequestSpecificDetails",
        ),
        metric_attr("error.message", "quota exceeded for request req-123"),
    ]);
    let sample = metric_sample(sum_metric(
        "agent.api_request.error.count",
        "{count}",
        vec![sum_data_point(attrs, 1)],
        true,
    ));

    let mut engine = engine();
    let findings = engine.validate(&sample).unwrap();

    for expected in [
        "metric_invalid_error_category",
        "metric_forbidden_error_type",
        "metric_forbidden_error_message",
    ] {
        assert!(
            has_finding(&findings, expected, "violation"),
            "Expected {expected} violation, got: {:?}",
            findings
        );
    }
    assert!(
        !has_finding(&findings, "disallowed_metric_attr_prefix", "violation"),
        "Raw error fields should be handled by specific RED golden error rules, not by the namespace gate: {:?}",
        findings
    );
}

#[test]
fn test_red_golden_metric_rejects_non_error_standard_namespace_attrs() {
    for (key, value) in [
        ("code.filepath", "/tmp/tool.rs"),
        ("session.id", "session-123"),
    ] {
        let mut attrs = api_metric_attrs();
        attrs.push(metric_attr(key, value));
        let sample = metric_sample(sum_metric(
            "agent.api_request.count",
            "{count}",
            vec![sum_data_point(attrs, 1)],
            true,
        ));

        let mut engine = engine();
        let findings = engine.validate(&sample).unwrap();

        assert!(
            has_finding(&findings, "disallowed_metric_attr_prefix", "violation"),
            "Non-error OTel/span attribute {key} must not bypass the RED golden metric namespace gate: {:?}",
            findings
        );
    }
}

#[test]
fn test_red_golden_metric_accepts_other_error_category() {
    let mut attrs = api_metric_attrs();
    attrs.push(metric_attr("agentic.error.category", "other"));
    let sample = metric_sample(sum_metric(
        "agent.api_request.error.count",
        "{count}",
        vec![sum_data_point(attrs, 1)],
        true,
    ));

    let mut engine = engine();
    let findings = engine.validate(&sample).unwrap();

    assert!(
        findings.is_empty(),
        "`other` should be a valid fallback error category for error count metrics: {:?}",
        findings
    );
}

#[test]
fn test_red_golden_metric_rejects_non_string_enum_values() {
    let mut api_attrs = api_metric_attrs();
    api_attrs.push(metric_attr_raw(
        "agentic.error.category",
        json!({ "intValue": 7 }),
    ));
    let sample = metric_sample(sum_metric(
        "agent.api_request.error.count",
        "{count}",
        vec![sum_data_point(api_attrs, 1)],
        true,
    ));

    let mut validation_engine = engine();
    let findings = validation_engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "metric_invalid_error_category", "violation"),
        "Non-string error category should violate bounded enum validation: {:?}",
        findings
    );

    let mut tool_attrs = tool_metric_attrs();
    tool_attrs.retain(|attr| attr.get("key").and_then(Value::as_str) != Some("agentic.tool.type"));
    tool_attrs.push(metric_attr_raw(
        "agentic.tool.type",
        json!({ "boolValue": true }),
    ));
    let sample = metric_sample(histogram_metric(
        "agent.tool_call.duration",
        "ms",
        vec![histogram_data_point(tool_attrs, 10)],
    ));

    let mut validation_engine = engine();
    let findings = validation_engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "tool_call_metric_invalid_tool_type", "violation"),
        "Non-string tool type should violate bounded enum validation: {:?}",
        findings
    );
}

#[test]
fn test_tool_duration_requires_histogram_and_valid_tool_type() {
    let mut attrs = tool_metric_attrs();
    attrs.retain(|attr| attr.get("key").and_then(Value::as_str) != Some("agentic.tool.type"));
    let sample = metric_sample(sum_metric(
        "agent.tool_call.duration",
        "ms",
        vec![sum_data_point(attrs, 1)],
        true,
    ));

    let mut validation_engine = engine();
    let findings = validation_engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "tool_call_duration_wrong_type", "violation"),
        "Expected tool duration type violation, got: {:?}",
        findings
    );
    assert!(
        has_finding(&findings, "tool_call_metric_missing_tool_type", "violation"),
        "Expected tool type required violation, got: {:?}",
        findings
    );

    let mut invalid_tool_attrs = tool_metric_attrs();
    invalid_tool_attrs
        .retain(|attr| attr.get("key").and_then(Value::as_str) != Some("agentic.tool.type"));
    invalid_tool_attrs.push(metric_attr("agentic.tool.type", "unknown"));
    let sample = metric_sample(histogram_metric(
        "agent.tool_call.duration",
        "ms",
        vec![histogram_data_point(invalid_tool_attrs, 10)],
    ));

    let mut validation_engine = engine();
    let findings = validation_engine.validate(&sample).unwrap();

    assert!(
        has_finding(&findings, "tool_call_metric_invalid_tool_type", "violation"),
        "Expected invalid tool type violation, got: {:?}",
        findings
    );
}

#[test]
fn test_error_count_metrics_require_counter_shape() {
    let cases = [
        (
            "agent.api_request.error.count",
            api_error_metric_attrs(),
            "api_request_error_count_wrong_type",
        ),
        (
            "agent.tool_call.error.count",
            tool_error_metric_attrs(),
            "tool_call_error_count_wrong_type",
        ),
    ];

    for (metric_name, attrs, expected_rule) in cases {
        let sample = metric_sample(histogram_metric(
            metric_name,
            "{count}",
            vec![histogram_data_point(attrs, 1)],
        ));

        let mut engine = engine();
        let findings = engine.validate(&sample).unwrap();

        assert!(
            has_finding(&findings, expected_rule, "violation"),
            "Expected {expected_rule} for {metric_name}, got: {:?}",
            findings
        );
    }
}

#[test]
fn test_count_metrics_validate_unit_monotonicity_and_negative_values() {
    let cases = [
        ("agent.api_request.count", api_metric_attrs()),
        ("agent.api_request.error.count", api_error_metric_attrs()),
        ("agent.tool_call.count", tool_metric_attrs()),
        ("agent.tool_call.error.count", tool_error_metric_attrs()),
    ];

    for (metric_name, attrs) in cases {
        let sample = metric_sample(sum_metric(
            metric_name,
            "1",
            vec![sum_data_point(attrs, -1)],
            false,
        ));

        let mut engine = engine();
        let findings = engine.validate(&sample).unwrap();

        for expected in [
            "metric_count_wrong_unit",
            "metric_count_non_monotonic",
            "metric_negative_value",
        ] {
            assert!(
                has_finding(&findings, expected, "violation"),
                "Expected {expected} violation for {metric_name}, got: {:?}",
                findings
            );
        }
    }
}
