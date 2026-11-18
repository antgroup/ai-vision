//! Unknown usage is a source fact, not a fabricated zero or a blanket waiver.
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::path::PathBuf;
use weaver_guard::{ValidationEngine, ValidationFinding};

fn attr(key: &str, value: Value) -> Value {
    json!({"key": key, "value": value})
}

fn sample(log: bool, usage: &[(&str, i64)], markers: &[(&str, Value)]) -> Value {
    let mut attrs = vec![
        attr("ant.username", json!({"stringValue": "native-test"})),
        attr("gen_ai.conversation.id", json!({"stringValue": "session-test"})),
        attr("gen_ai.chat.id", json!({"stringValue": "chat-test"})),
        attr("gen_ai.operation.name", json!({"stringValue": "chat"})),
        attr("gen_ai.request.model", json!({"stringValue": "test-model"})),
    ];
    if log {
        attrs.extend([
            attr("event.name", json!({"stringValue": "api_request"})),
            attr("agentic.runtime.name", json!({"stringValue": "opencode"})),
            attr("agentic.turn.number", json!({"intValue": 1})),
            attr("duration_ms", json!({"intValue": 1})),
            attr("gen_ai.response.model", json!({"stringValue": "test-model"})),
            attr("gen_ai.input.messages", json!({"stringValue": "[]"})),
            attr("gen_ai.output.messages", json!({"stringValue": "[]"})),
            attr("gen_ai.system_instructions", json!({"stringValue": "[]"})),
        ]);
    } else {
        attrs.extend([
            attr("gen_ai.span.kind", json!({"stringValue": "LLM"})),
            attr("agentic.llm.duration_ms", json!({"intValue": 1})),
        ]);
    }
    attrs.extend(usage.iter().map(|(direction, value)| {
        attr(&format!("gen_ai.usage.{direction}_tokens"), json!({"intValue": value}))
    }));
    attrs.extend(markers.iter().map(|(direction, value)| {
        attr(&format!("agentic.usage.{direction}_tokens.missing"), value.clone())
    }));
    let resource = json!({"attributes": [
        attr("ant.agent.type", json!({"stringValue": "opencode"})),
        attr("service.name", json!({"stringValue": "observ-opencode"})),
        attr("ant.agent.env", json!({"stringValue": "test"})),
        attr("ant.agent.scene", json!({"stringValue": "default"})),
        attr("agentic.collection.mode", json!({"stringValue": "full"})),
    ]});
    if log {
        json!({"sample": {"resource": resource, "log": {
            "traceId": "trace-test", "spanId": "span-test", "attributes": attrs,
        }}})
    } else {
        json!({"sample": {"resource": resource, "span": {
            "name": "chat test-model", "attributes": attrs,
        }}})
    }
}

fn validate(sample: &Value) -> Vec<ValidationFinding> {
    let policies = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join("policies");
    ValidationEngine::new(&policies).unwrap().validate(sample).unwrap()
}

fn missing_usage(findings: &[ValidationFinding], log: bool) -> BTreeSet<String> {
    findings.iter().filter(|f| f.advice_level == "violation").filter_map(|f| {
        if log && f.advice_type == "event_missing_required_attribute" {
            f.context.get("attribute").and_then(Value::as_str)
                .and_then(|s| s.strip_prefix("gen_ai.usage."))
                .and_then(|s| s.strip_suffix("_tokens")).map(str::to_owned)
        } else {
            match f.advice_type.as_str() {
                "llm_missing_input_tokens" => Some("input".into()),
                "llm_missing_output_tokens" => Some("output".into()),
                _ => None,
            }
        }
    }).collect()
}

#[test]
fn explicit_unknown_and_known_zero_preserve_independent_directions() {
    for log in [false, true] {
        let known_zero = sample(log, &[("input", 0), ("output", 0), ("total", 0)], &[]);
        assert!(missing_usage(&validate(&known_zero), log).is_empty());
        let unknown = sample(log, &[], &[
            ("input", json!({"stringValue": "true"})), ("output", json!({"stringValue": "true"})),
        ]);
        assert!(missing_usage(&validate(&unknown), log).is_empty());
        for (missing, known) in [("input", "output"), ("output", "input")] {
            let partial = sample(log, &[(known, 7)], &[(missing, json!({"stringValue": "true"}))]);
            assert!(missing_usage(&validate(&partial), log).is_empty());
            let opposite_missing = sample(log, &[(missing, 7)], &[(missing, json!({"stringValue": "true"}))]);
            assert_eq!(missing_usage(&validate(&opposite_missing), log), BTreeSet::from([known.to_owned()]));
        }
    }
}

#[test]
fn unreported_usage_requires_the_exact_string_marker() {
    for log in [false, true] {
        let expected = if log { vec!["input", "output", "total"] } else { vec!["input", "output"] };
        let expected: BTreeSet<_> = expected.into_iter().map(str::to_owned).collect();
        assert_eq!(missing_usage(&validate(&sample(log, &[], &[])), log), expected);
        for value in [json!({"stringValue": "false"}), json!({"stringValue": ""}),
                      json!({"stringValue": "TRUE"}), json!({"stringValue": " true"}),
                      json!({"stringValue": "unknown"}), json!({"boolValue": true}), json!({"intValue": 1})] {
            let invalid = sample(log, &[], &[("input", value.clone()), ("output", value)]);
            assert_eq!(missing_usage(&validate(&invalid), log), expected);
        }
    }
}

#[test]
fn known_directions_still_require_success_event_total() {
    let known = sample(true, &[("input", 0), ("output", 0)], &[]);
    assert_eq!(missing_usage(&validate(&known), true), BTreeSet::from(["total".to_owned()]));
}

#[test]
fn unknown_usage_never_waives_other_required_fields() {
    for log in [false, true] {
        let mut unknown = sample(log, &[], &[
            ("input", json!({"stringValue": "true"})), ("output", json!({"stringValue": "true"})),
        ]);
        let row = if log { "log" } else { "span" };
        let required = if log { "gen_ai.response.model" } else { "gen_ai.request.model" };
        unknown["sample"][row]["attributes"].as_array_mut().unwrap()
            .retain(|a| a["key"].as_str() != Some(required));
        let findings = validate(&unknown);
        assert!(findings.iter().any(|f| f.advice_level == "violation" &&
            (f.advice_type == "llm_missing_request_model" ||
             f.context.get("attribute").and_then(Value::as_str) == Some(required))));
    }
}
