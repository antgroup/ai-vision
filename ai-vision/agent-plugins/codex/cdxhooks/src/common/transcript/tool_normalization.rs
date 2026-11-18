//! tool normalization ownership module.

use super::{json, Value};
use super::{AssistantMessage, TokenUsage};

pub(super) fn parse_jsonish_value(raw: &Value) -> Value {
    match raw {
        Value::String(text) => {
            let trimmed = text.trim();
            if trimmed.starts_with('{') || trimmed.starts_with('[') {
                serde_json::from_str::<Value>(trimmed)
                    .unwrap_or_else(|_| Value::String(text.clone()))
            } else {
                Value::String(text.clone())
            }
        }
        other => other.clone(),
    }
}

pub(super) fn json_string_lossless(value: &Value) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| value.to_string())
}

pub(super) fn mcp_tool_result_output(result: &Value) -> Value {
    if let Some(ok) = result.get("Ok") {
        if let Some(text) = ok
            .get("content")
            .and_then(Value::as_array)
            .filter(|content| content.len() == 1)
            .and_then(|content| content.first())
            .and_then(|item| item.get("text"))
            .and_then(Value::as_str)
        {
            return Value::String(text.to_string());
        }

        return Value::String(json_string_lossless(ok));
    }

    if let Some(err) = result.get("Err") {
        if let Some(text) = err.as_str() {
            return Value::String(text.to_string());
        }

        return Value::String(json_string_lossless(err));
    }

    result.clone()
}

pub(super) fn extract_agent_message_content(payload: &Value) -> String {
    let Some(items) = payload.get("content").and_then(Value::as_array) else {
        return String::new();
    };

    let mut parts = items
        .iter()
        .filter_map(|item| item.get("text").and_then(Value::as_str))
        .map(str::trim_end)
        .filter(|text| !text.is_empty())
        .map(str::to_string)
        .collect::<Vec<_>>();
    if items
        .iter()
        .any(|item| item.get("type").and_then(Value::as_str) == Some("encrypted_content"))
    {
        parts.push("[ENCRYPTED]".to_string());
    }
    parts.join("\n").trim().to_string()
}

pub(super) fn extract_agent_message_type(content: &str) -> Option<String> {
    content.lines().find_map(|line| {
        line.trim()
            .strip_prefix("Message Type:")
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
    })
}

pub(super) fn response_tool_name(payload: &Value) -> String {
    let name = payload
        .get("name")
        .or_else(|| payload.get("tool_name"))
        .or_else(|| payload.get("toolName"))
        .and_then(Value::as_str)
        .unwrap_or("");
    if !name.is_empty() && !name.starts_with("mcp__") {
        if let Some(namespace) = payload.get("namespace").and_then(Value::as_str) {
            if namespace
                .strip_prefix("mcp__")
                .is_some_and(|server| !server.is_empty())
            {
                return format!("{namespace}__{name}");
            }
        }
    }
    name.to_string()
}

pub(super) fn normalize_tool_call(raw_name: &str, parsed_args: &Value) -> (String, Value) {
    match raw_name {
        "exec_command" | "shell_command" | "read_thread_terminal" | "write_stdin" => {
            let command = parsed_args
                .get("command")
                .or_else(|| parsed_args.get("cmd"))
                .or_else(|| parsed_args.get("chars"))
                .cloned()
                .unwrap_or(Value::Null);
            if command.is_null() {
                ("Bash".to_string(), parsed_args.clone())
            } else {
                ("Bash".to_string(), json!({"command": command}))
            }
        }
        "spawn_agent" | "wait" | "wait_agent" | "interrupt_agent" | "send_input"
        | "resume_agent" | "close_agent" | "followup_task" | "send_message" | "list_agents" => (
            raw_name.to_string(),
            merge_name_into_args(raw_name, parsed_args),
        ),
        "" => ("tool".to_string(), parsed_args.clone()),
        other => (other.to_string(), parsed_args.clone()),
    }
}

pub(super) fn merge_name_into_args(name: &str, args: &Value) -> Value {
    if let Some(object) = args.as_object() {
        let mut merged = serde_json::Map::new();
        merged.insert("name".to_string(), Value::String(name.to_string()));
        for (key, value) in object {
            merged.insert(key.clone(), value.clone());
        }
        Value::Object(merged)
    } else {
        json!({"name": name, "arguments": args})
    }
}

pub(super) fn extract_pty_session_id(output: &str) -> Option<String> {
    let lower = output.to_ascii_lowercase();
    let pos = lower.find("session id ")?;
    let digits: String = output[pos + "session ID ".len()..]
        .chars()
        .take_while(|ch| ch.is_ascii_digit())
        .collect();
    if digits.is_empty() {
        None
    } else {
        Some(digits)
    }
}

pub(super) fn upsert_assistant_message(
    assistants: &mut Vec<AssistantMessage>,
    timestamp: &str,
    text: &str,
    phase: &str,
) {
    if let Some(existing) = assistants
        .iter_mut()
        .find(|assistant| assistant.phase == phase && assistant.text == text)
    {
        if existing.timestamp.is_empty() {
            existing.timestamp = timestamp.to_string();
        }
        return;
    }

    if phase == "final_answer" {
        if let Some(existing) = assistants
            .iter_mut()
            .find(|assistant| assistant.phase == "final_answer")
        {
            existing.timestamp = timestamp.to_string();
            existing.text = text.to_string();
            return;
        }
    }

    assistants.push(AssistantMessage {
        timestamp: timestamp.to_string(),
        text: text.to_string(),
        media_content: serde_json::Value::Null,
        phase: phase.to_string(),
        usage: TokenUsage::default(),
        token_count_timestamp: String::new(),
        invocation_started_timestamp: String::new(),
        invocation_end_source: None,
    });
}
