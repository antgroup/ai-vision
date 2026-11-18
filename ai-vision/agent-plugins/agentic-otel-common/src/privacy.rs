//! Local content boundary shared by production hook exporters.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::cell::RefCell;

thread_local! {
    static WORKER_MODE: RefCell<Option<String>> = const { RefCell::new(None) };
}

/// A queued event keeps its captured mode even when a different process
/// recovers it. Never mutate process-wide environment variables in workers.
pub fn with_worker_mode<R>(mode: &str, run: impl FnOnce() -> R) -> R {
    struct Guard(Option<String>);
    impl Drop for Guard {
        fn drop(&mut self) {
            WORKER_MODE.with(|current| *current.borrow_mut() = self.0.take());
        }
    }
    let _guard = Guard(WORKER_MODE.with(|current| current.replace(Some(mode.to_owned()))));
    run()
}

pub fn resolve_mode(legacy: &str) -> Result<&'static str, &'static str> {
    let value = WORKER_MODE
        .with(|current| current.borrow().clone())
        .unwrap_or_else(|| {
            std::env::var("AIVISION_COLLECTION_MODE")
                .or_else(|_| std::env::var(legacy))
                .unwrap_or_else(|_| "full".into())
        });
    match value.trim().to_ascii_lowercase().as_str() {
        "full" => Ok("full"),
        "privacy" => Ok("privacy"),
        _ => {
            Err("AI Vision: invalid collection mode; telemetry disabled. Expected full or privacy.")
        }
    }
}

pub fn validate_mode(legacy: &str) -> bool {
    match resolve_mode(legacy) {
        Ok(_) => true,
        Err(message) => {
            eprintln!("{message}");
            false
        }
    }
}

pub fn private_process() -> bool {
    [
        "CDXHOOKS_COLLECTION_MODE",
        "CCHOOKS_COLLECTION_MODE",
        "KIMI_COLLECTION_MODE",
    ]
    .iter()
    .any(|key| resolve_mode(key) != Ok("full"))
}

fn structural(key: &str) -> bool {
    matches!(
        key,
        "ant.username"
            | "ant.uid"
            | "ant.meta.trigger"
            | "event_name"
            | "event.name"
            | "error.type"
            | "otel.status_code"
            | "_internal.meta.root"
            | "agentic.collection.mode"
            | "gen_ai.system"
            | "gen_ai.provider.name"
            | "gen_ai.operation.name"
            | "gen_ai.request.model"
            | "gen_ai.response.model"
            | "gen_ai.response.finish_reasons"
            | "service.name"
            | "service.version"
            | "service.namespace"
            | "deployment.environment"
            | "deployment.environment.name"
            | "agentic.runtime.name"
            | "agentic.turn.trigger"
            | "agentic.tool.raw_name"
            | "data.source"
    ) || key.starts_with("ant.agent.")
        || key.starts_with("telemetry.sdk.")
        || key.ends_with(".id")
        || key.ends_with("_id")
        || key.ends_with(".type")
        || key.ends_with(".kind")
        || key == "gen_ai.tool.name"
        || key == "agentic.tool.name"
}

fn numeric_statistic(key: &str, value: &Value) -> bool {
    let statistic = key.starts_with("gen_ai.usage.")
        || ((key.starts_with("agentic.") || key.starts_with("gen_ai."))
            && [
                "_tokens", ".count", ".number", ".index", "_ms", "_ns", "_bytes", ".score",
                ".ratio",
            ]
            .iter()
            .any(|suffix| key.ends_with(suffix)));
    statistic
        && value
            .get("stringValue")
            .and_then(Value::as_str)
            .and_then(|text| text.parse::<f64>().ok())
            .is_some_and(f64::is_finite)
}

fn content_key(key: &str) -> bool {
    let key = key.to_ascii_lowercase();
    if key.starts_with("gen_ai.usage.") || key.ends_with("_tokens") {
        return false;
    }
    [
        "prompt",
        "message",
        "content",
        "thinking",
        "reasoning",
        "arguments",
        "result",
        "password",
        "secret",
        "header",
        "stacktrace",
    ]
    .iter()
    .any(|part| key.contains(part))
        || matches!(
            key.as_str(),
            "input" | "output" | "tool_input" | "tool_response" | "response" | "error"
        )
}

fn redacted(key: &str) -> Value {
    let text = match key {
        "gen_ai.input.messages" => r#"[{"role":"user","content":"[REDACTED]"}]"#,
        "gen_ai.output.messages" => r#"[{"role":"assistant","content":"[REDACTED]"}]"#,
        "gen_ai.tool.call.arguments" | "gen_ai.tool.call.result" => r#""[REDACTED]""#,
        _ => "[REDACTED]",
    };
    json!({"stringValue": text})
}

/// Scrub raw hook content before a durable queue receives it. Host paths and
/// IDs remain available so the worker can read the host-owned transcript.
pub fn scrub_hook_payload(value: &mut Value) {
    match value {
        Value::Object(map) => {
            for (key, value) in map {
                if key == "tool_response" {
                    // Preserve exit/latency metadata in JSON-encoded host results.
                    if let Some(mut decoded) = value
                        .as_str()
                        .and_then(|text| serde_json::from_str::<Value>(text).ok())
                        .filter(Value::is_object)
                    {
                        scrub_tool_result(&mut decoded);
                        *value = json!(decoded.to_string());
                    } else if value.is_object() {
                        scrub_tool_result(value);
                    } else {
                        *value = json!("[REDACTED]");
                    }
                } else if content_key(key) {
                    // Keep prompt/input identity stable for deduplication without
                    // persisting raw text in the worker's durable envelope.
                    *value = json!(format!(
                        "[REDACTED sha256:{}]",
                        Sha256::digest(value.to_string().as_bytes())
                            .iter()
                            .map(|byte| format!("{byte:02x}"))
                            .collect::<String>()
                    ));
                } else {
                    scrub_hook_payload(value);
                }
            }
        }
        Value::Array(items) => items.iter_mut().for_each(scrub_hook_payload),
        _ => {}
    }
}

fn scrub_tool_result(value: &mut Value) {
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                // Numeric/boolean execution metadata is not tool output. All
                // other result fields (including unknown names) are content.
                if matches!(
                    key.as_str(),
                    "exit_code"
                        | "exitCode"
                        | "code"
                        | "is_error"
                        | "success"
                        | "ok"
                        | "duration_ms"
                        | "wall_time_seconds"
                        | "elapsed_ms"
                ) && (child.is_number() || child.is_boolean())
                {
                    continue;
                }
                if matches!(key.as_str(), "metadata" | "status") && child.is_object() {
                    scrub_tool_result(child);
                } else {
                    *child = json!("[REDACTED]");
                }
            }
        }
        _ => *value = json!("[REDACTED]"),
    }
}

/// Scrub all three OTLP signals before queueing, file output or HTTP export.
/// Numeric statistics, IDs and topology retain their original types/values.
pub fn protect_export(mut value: Value, mode: &str) -> Value {
    if mode == "privacy" {
        scrub_otlp(&mut value);
    }
    value
}

fn scrub_otlp(value: &mut Value) {
    match value {
        Value::Array(items) => items.iter_mut().for_each(scrub_otlp),
        Value::Object(map) => {
            if let Some(key) = map.get("key").and_then(Value::as_str).map(str::to_owned) {
                if let Some(data) = map.get_mut("value") {
                    let structural_flag = (key.ends_with(".missing")
                        || key.ends_with(".truncated"))
                        && matches!(
                            data.get("stringValue").and_then(Value::as_str),
                            Some("true" | "false")
                        );
                    if !structural_flag
                        && !numeric_statistic(&key, data)
                        && (content_key(&key)
                            || (!structural(&key) && data.get("stringValue").is_some())
                            || (!structural(&key)
                                && (data.get("arrayValue").is_some()
                                    || data.get("kvlistValue").is_some())))
                    {
                        *data = redacted(&key);
                    }
                }
            }
            if map.contains_key("spanId") && map.contains_key("startTimeUnixNano") {
                // Content-derived display names cannot safely pass through.
                let attr = |key: &str| {
                    map.get("attributes")
                        .and_then(Value::as_array)
                        .and_then(|attrs| attrs.iter().find(|attr| attr["key"] == key))
                        .and_then(|attr| attr["value"]["stringValue"].as_str())
                };
                let name = if let Some(op) = attr("gen_ai.operation.name") {
                    let suffix = attr("gen_ai.request.model")
                        .or_else(|| attr("gen_ai.tool.name"))
                        .unwrap_or("");
                    format!("{op} {suffix}").trim().to_owned()
                } else {
                    attr("gen_ai.span.kind")
                        .or_else(|| attr("agentic.span.kind"))
                        .unwrap_or("[REDACTED]")
                        .to_owned()
                };
                map.insert("name".into(), json!(name));
            }
            if let Some(body) = map.get_mut("body") {
                if let Some(text) = body.get("stringValue").and_then(Value::as_str) {
                    if !matches!(
                        text,
                        "user_prompt"
                            | "api_request"
                            | "tool_call"
                            | "tool_result"
                            | "session_start"
                            | "session_end"
                            | "compaction_result"
                            | "subagent_spawned"
                            | "subagent_ended"
                            | "tool_decision"
                    ) {
                        *body = json!({"stringValue":"[REDACTED]"});
                    }
                }
            }
            if let Some(status) = map.get_mut("status").and_then(Value::as_object_mut) {
                if status.contains_key("message") {
                    status.insert("message".into(), json!("[REDACTED]"));
                }
            }
            if let Some(events) = map.get_mut("events").and_then(Value::as_array_mut) {
                for event in events {
                    if event.get("name").and_then(Value::as_str) != Some("exception")
                        && event.get("name").is_some()
                    {
                        event["name"] = json!("[REDACTED]");
                    }
                }
            }
            for child in map.values_mut() {
                scrub_otlp(child);
            }
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn queued_mode_is_scoped_and_raw_results_preserve_only_execution_metadata() {
        with_worker_mode("privacy", || {
            assert_eq!(resolve_mode("IGNORED_MODE"), Ok("privacy"));
            with_worker_mode("full", || {
                assert_eq!(resolve_mode("IGNORED_MODE"), Ok("full"))
            });
            assert_eq!(resolve_mode("IGNORED_MODE"), Ok("privacy"));
            with_worker_mode("invalid", || assert!(resolve_mode("IGNORED_MODE").is_err()));
        });
        let mut raw = json!({"session_id":"session", "prompt":"PRIVATE", "tool_input":{"command":"PRIVATE"},
            "tool_response":json!({"stdout":"PRIVATE", "stderr":"PRIVATE", "unknown":{"text":"PRIVATE"},
                "exit_code":1,"metadata":{"exit_code":1,"message":"PRIVATE"},"wall_time_seconds":0.2}).to_string()});
        scrub_hook_payload(&mut raw);
        assert!(!raw.to_string().contains("PRIVATE"));
        assert_eq!(raw["session_id"], "session");
        let result: Value = serde_json::from_str(raw["tool_response"].as_str().unwrap()).unwrap();
        assert_eq!(result["exit_code"], 1);
        assert_eq!(result["wall_time_seconds"], 0.2);
    }
    #[test]
    fn filters_content_and_keeps_usage_and_topology() {
        let raw = json!({"traceId":"abc", "spanId":"def", "startTimeUnixNano":"1", "endTimeUnixNano":"5",
            "name":"PRIVATE", "attributes":[
                {"key":"gen_ai.input.messages","value":{"stringValue":"PRIVATE"}},
                {"key":"gen_ai.usage.input_tokens","value":{"intValue":"12"}},
                {"key":"gen_ai.conversation.id","value":{"stringValue":"session"}},
                {"key":"custom.secret","value":{"stringValue":"PRIVATE"}}],
            "body":{"stringValue":"PRIVATE"}, "status":{"code":2,"message":"PRIVATE"}});
        assert_eq!(protect_export(raw.clone(), "full"), raw);
        let redacted = protect_export(raw, "privacy");
        assert!(!redacted.to_string().contains("PRIVATE"));
        assert_eq!(redacted["attributes"][1]["value"]["intValue"], "12");
        assert_eq!(redacted["traceId"], "abc");
        assert_eq!(redacted["status"]["code"], 2);
    }

    #[test]
    fn preserves_legacy_numeric_strings_and_structural_flags() {
        let raw = json!({"attributes":[
            {"key":"gen_ai.usage.input_tokens","value":{"stringValue":"63221"}},
            {"key":"agentic.tool.duration_ms","value":{"stringValue":"12.5"}},
            {"key":"agentic.turn.number","value":{"stringValue":"1"}},
            {"key":"agentic.usage.input_tokens.missing","value":{"stringValue":"false"}},
            {"key":"agentic.runtime.name","value":{"stringValue":"codex"}},
            {"key":"agentic.turn.trigger","value":{"stringValue":"interactive"}}
        ]});
        assert_eq!(protect_export(raw.clone(), "privacy"), raw);
        let malformed =
            json!({"key":"gen_ai.usage.input_tokens","value":{"stringValue":"PRIVATE"}});
        assert!(!protect_export(malformed, "privacy")
            .to_string()
            .contains("PRIVATE"));
    }
}
