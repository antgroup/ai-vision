use serde_json::Value;

use crate::common::logger::looks_like_iso_timestamp;
#[allow(unused_imports)]
use crate::common::skill_keywords::SkillKeywordConfig;

use super::types::{
    AssistantMessage, TerminalLlmError, TokenUsage, ToolResultItem, ToolResultMessage, ToolUse,
};

// --- JSON field helpers ---

pub fn get_obj_str<'a>(msg: &'a Value, key: &str) -> &'a str {
    msg.get(key).and_then(|v| v.as_str()).unwrap_or("")
}

pub(super) fn get_content_value(msg: &Value) -> Option<&Value> {
    if let Some(message) = msg.get("message") {
        if let Some(content) = message.get("content") {
            return Some(content);
        }
    }
    msg.get("content")
}

pub fn get_role(msg: &Value) -> &str {
    let type_val = get_obj_str(msg, "type");
    if type_val == "user" || type_val == "assistant" {
        return type_val;
    }
    if let Some(message) = msg.get("message") {
        let role = get_obj_str(message, "role");
        if role == "user" || role == "assistant" {
            return role;
        }
    }
    ""
}

pub fn get_timestamp(msg: &Value) -> String {
    let raw = get_obj_str(msg, "timestamp");
    if raw.is_empty() || !looks_like_iso_timestamp(raw) {
        return String::new();
    }
    raw.to_string()
}

pub fn get_model(msg: &Value) -> String {
    if let Some(message) = msg.get("message") {
        let model = get_obj_str(message, "model");
        if !model.is_empty() {
            return model.to_string();
        }
    }
    "claude".to_string()
}

pub fn get_message_id(msg: &Value) -> String {
    if let Some(message) = msg.get("message") {
        return get_obj_str(message, "id").to_string();
    }
    String::new()
}

pub fn get_prompt_chat_id(msg: &Value) -> Option<String> {
    for key in ["uuid", "id", "requestId", "request_id"] {
        if let Some(value) = msg.get(key).and_then(|v| v.as_str()) {
            if !value.is_empty() {
                return Some(value.to_string());
            }
        }
    }

    if let Some(message) = msg.get("message") {
        for key in ["uuid", "id", "requestId", "request_id"] {
            if let Some(value) = message.get(key).and_then(|v| v.as_str()) {
                if !value.is_empty() {
                    return Some(value.to_string());
                }
            }
        }
    }

    None
}

pub fn get_stop_reason(msg: &Value) -> String {
    if let Some(message) = msg.get("message") {
        return get_obj_str(message, "stop_reason").to_string();
    }
    String::new()
}

pub fn is_stop_hook_summary(msg: &Value) -> bool {
    get_obj_str(msg, "type") == "system" && get_obj_str(msg, "subtype") == "stop_hook_summary"
}

pub fn is_tool_result(msg: &Value) -> bool {
    if get_role(msg) != "user" {
        return false;
    }
    let Some(content) = get_content_value(msg) else {
        return false;
    };
    let Some(arr) = content.as_array() else {
        return false;
    };
    arr.iter()
        .any(|item| item.get("type").and_then(|v| v.as_str()) == Some("tool_result"))
}

pub fn extract_text(content: &Value) -> String {
    if let Some(s) = content.as_str() {
        return s.to_string();
    }
    let Some(arr) = content.as_array() else {
        return String::new();
    };
    let mut parts: Vec<String> = Vec::new();
    for item in arr {
        if let Some(s) = item.as_str() {
            if !s.is_empty() {
                parts.push(s.to_string());
            }
        } else if let Some(obj) = item.as_object() {
            if obj.get("type").and_then(|v| v.as_str()) == Some("text") {
                if let Some(text) = obj.get("text").and_then(|v| v.as_str()) {
                    if !text.is_empty() {
                        parts.push(text.to_string());
                    }
                }
            }
        }
    }
    parts.join("\n")
}

/// Extract thinking content blocks from a content array.
/// Returns a Vec of OpenAI-format messages: [{"role": "assistant", "content": "..."}]
pub fn extract_thinking_blocks(content: &Value) -> Vec<Value> {
    let Some(arr) = content.as_array() else {
        return Vec::new();
    };
    let mut blocks = Vec::new();
    for item in arr {
        let Some(obj) = item.as_object() else {
            continue;
        };
        if obj.get("type").and_then(|v| v.as_str()) != Some("thinking") {
            continue;
        }
        // Claude API uses "thinking" field for thinking block content
        let text = obj.get("thinking").and_then(|v| v.as_str()).unwrap_or("");
        if !text.is_empty() {
            blocks.push(serde_json::json!({
                "role": "assistant",
                "content": text
            }));
        }
    }
    blocks
}

const SKILL_BASE_DIR_PREFIX: &str = "Base directory for this skill: ";

/// Extract skill name from isMeta user message content.
/// Looks for "Base directory for this skill: /path/to/<skill-name>" and returns the last
/// path segment as the skill name.
pub(super) fn extract_skill_name_from_meta(text: &str) -> Option<String> {
    let line = text.lines().next().unwrap_or("");
    let path = line.strip_prefix(SKILL_BASE_DIR_PREFIX)?;
    let path = path.trim();
    if path.is_empty() {
        return None;
    }
    // Extract last path segment: "/Users/x/.claude/skills/theta-statusline-integration" → "theta-statusline-integration"
    let name = path.rsplit('/').next().unwrap_or(path);
    if name.is_empty() {
        return None;
    }
    Some(name.to_string())
}

fn get_usage(msg: &Value) -> TokenUsage {
    let usage_obj = msg.get("usage").and_then(|u| u.as_object()).or_else(|| {
        msg.get("message")
            .and_then(|m| m.get("usage"))
            .and_then(|u| u.as_object())
    });

    let Some(usage_obj) = usage_obj else {
        return TokenUsage {
            input: None,
            output: None,
            cache_creation_input_tokens: None,
            cache_read_input_tokens: None,
        };
    };

    TokenUsage {
        input: usage_obj.get("input_tokens").and_then(|v| v.as_i64()),
        output: usage_obj.get("output_tokens").and_then(|v| v.as_i64()),
        cache_creation_input_tokens: usage_obj
            .get("cache_creation_input_tokens")
            .and_then(|v| v.as_i64()),
        cache_read_input_tokens: usage_obj
            .get("cache_read_input_tokens")
            .and_then(|v| v.as_i64()),
    }
}

// --- Transcript parsing ---

pub(super) fn parse_tool_result_message(msg: &Value) -> ToolResultMessage {
    let timestamp = get_timestamp(msg);
    let mut items = Vec::new();

    let Some(content) = get_content_value(msg) else {
        return ToolResultMessage { timestamp, items };
    };
    let Some(arr) = content.as_array() else {
        return ToolResultMessage { timestamp, items };
    };

    for item in arr {
        let Some(obj) = item.as_object() else {
            continue;
        };
        if obj.get("type").and_then(|v| v.as_str()) != Some("tool_result") {
            continue;
        }
        let tool_use_id = obj
            .get("tool_use_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if tool_use_id.is_empty() {
            continue;
        }
        let is_error = obj
            .get("is_error")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let tool_denial_kind = msg
            .get("toolDenialKind")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string());
        let tool_use_result_text = msg
            .get("toolUseResult")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string());
        let cancellation_reason = classify_tool_cancellation(
            tool_denial_kind.as_deref(),
            tool_use_result_text.as_deref(),
        )
        .map(|s| s.to_string());
        // Try to extract tool name from the top-level `toolUseResult` field.
        // Strategy:
        // 1. Object format: try toolUseResult.toolName (future Claude Code versions)
        // 2. Array format (MCP content blocks): parse response JSON for meta.tool
        // 3. If array but no meta.tool: label as "mcp_tool" (better than raw UUID)
        let tool_name = extract_tool_name_from_result(msg);

        // Extract agentId and agentType from toolUseResult metadata (subagent results)
        let agent_id = msg
            .get("toolUseResult")
            .and_then(|r| r.get("agentId"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        let agent_type = msg
            .get("toolUseResult")
            .and_then(|r| r.get("agentType"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        // Async subagent launch: v2.1.199+ marks it in toolUseResult
        // (`isAsync: true` / `status: "async_launched"`), not in tool input.
        let is_async = tool_result_marks_async(msg);

        items.push(ToolResultItem {
            tool_use_id,
            content: obj.get("content").cloned().unwrap_or(Value::Null),
            is_error,
            tool_denial_kind,
            tool_use_result_text,
            cancellation_reason,
            tool_name,
            timestamp: timestamp.clone(),
            agent_id,
            agent_type,
            is_async,
        });
    }

    ToolResultMessage { timestamp, items }
}

/// Return whether a raw Claude transcript event marks a subagent launch as async.
/// Claude Code v2.1.199+ may expose this only on the top-level toolUseResult.
pub fn tool_result_marks_async(msg: &Value) -> bool {
    msg.get("toolUseResult").is_some_and(|result| {
        result
            .get("isAsync")
            .and_then(Value::as_bool)
            .unwrap_or(false)
            || result.get("status").and_then(Value::as_str) == Some("async_launched")
    })
}

pub(super) fn classify_tool_cancellation(
    tool_denial_kind: Option<&str>,
    tool_use_result_text: Option<&str>,
) -> Option<&'static str> {
    if tool_denial_kind == Some("user-rejected") {
        return Some("user_rejected");
    }
    if tool_use_result_text == Some("User rejected tool use") {
        return Some("user_rejected");
    }
    None
}

pub(super) fn is_user_interruption_system_message(msg: &Value) -> bool {
    get_obj_str(msg, "type") == "system"
        && msg
            .get("interruptedMessageId")
            .and_then(|v| v.as_str())
            .is_some_and(|s| !s.is_empty())
        && get_obj_str(msg, "content").contains("[Request interrupted by user")
}

pub(super) fn build_terminal_llm_error(
    assistants: &[AssistantMessage],
    api_errors: &[super::types::ApiError],
) -> Option<TerminalLlmError> {
    let last = assistants.last()?;
    if !last.is_synthetic {
        return None;
    }
    let error = api_errors.last().map(|e| &e.error).unwrap_or(&Value::Null);
    let error_type = normalize_llm_error_type(error);
    let message =
        sanitize_llm_error_message(extract_llm_error_message(error).unwrap_or(&last.text));
    let status_code = extract_llm_error_status(error);
    let request_model = extract_request_model(error)
        .or_else(|| extract_model_fragment(&message))
        .or_else(|| {
            assistants
                .iter()
                .rev()
                .find(|a| !a.is_synthetic && is_safe_model_id(&a.model))
                .map(|a| a.model.clone())
        });

    Some(TerminalLlmError {
        error_type,
        message,
        status_code,
        request_model,
    })
}

fn normalize_llm_error_type(error: &Value) -> String {
    let raw = error
        .pointer("/error/type")
        .or_else(|| error.get("type"))
        .and_then(|v| v.as_str())
        .unwrap_or("api_error");
    let message = extract_llm_error_message(error).unwrap_or_default();
    let has_invalid_model_evidence = extract_request_model(error).is_some()
        || extract_model_fragment(message).is_some()
        || message.to_ascii_lowercase().contains("invalid model");
    match raw {
        "invalid_request_error" if has_invalid_model_evidence => "invalid_model".to_string(),
        "invalid_request_error" => "invalid_request".to_string(),
        "authentication_error" => "authentication".to_string(),
        "permission_error" => "permission".to_string(),
        "rate_limit_error" => "rate_limit".to_string(),
        "api_error" | "overloaded_error" | "server_error" => "api_error".to_string(),
        other if other.contains("timeout") => "timeout".to_string(),
        other if other.contains("invalid") && has_invalid_model_evidence => {
            "invalid_model".to_string()
        }
        other if other.contains("invalid") => "invalid_request".to_string(),
        _ => "api_error".to_string(),
    }
}

fn extract_llm_error_message(error: &Value) -> Option<&str> {
    error
        .pointer("/error/message")
        .or_else(|| error.pointer("/error/error"))
        .or_else(|| error.get("message"))
        .or_else(|| error.get("error"))
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
}

fn extract_llm_error_status(error: &Value) -> Option<i64> {
    error
        .get("status")
        .or_else(|| error.get("status_code"))
        .or_else(|| error.pointer("/error/status"))
        .and_then(|v| v.as_i64())
}

fn extract_request_model(error: &Value) -> Option<String> {
    for pointer in [
        "/error/model",
        "/error/requested_model",
        "/error/requestModel",
        "/model",
        "/requested_model",
        "/requestModel",
    ] {
        if let Some(model) = error
            .pointer(pointer)
            .and_then(|v| v.as_str())
            .filter(|s| is_safe_model_id(s))
        {
            return Some(model.to_string());
        }
    }
    None
}

fn sanitize_llm_error_message(message: &str) -> String {
    let single_line = message
        .chars()
        .map(|c| {
            if c == '\n' || c == '\r' || c == '\t' {
                ' '
            } else {
                c
            }
        })
        .collect::<String>();
    redact_llm_error_secrets(&single_line)
        .chars()
        .take(512)
        .collect()
}

fn redact_llm_error_secrets(message: &str) -> String {
    const SENSITIVE_KEYS: &[&str] = &[
        "authorization",
        "x-api-key",
        "api_key",
        "api-key",
        "apikey",
        "access_token",
        "access-token",
        "auth_token",
        "auth-token",
        "password",
        "secret_key",
        "secret-key",
    ];

    fn value_end(message: &str, start: usize) -> usize {
        message[start..]
            .char_indices()
            .find_map(|(offset, ch)| {
                (ch.is_whitespace() || matches!(ch, ',' | ';' | '"' | '\'' | '}' | ']'))
                    .then_some(start + offset)
            })
            .unwrap_or(message.len())
    }

    fn next_sensitive_range(message: &str, cursor: usize) -> Option<(usize, usize)> {
        let lower = message.to_ascii_lowercase();
        let mut candidate: Option<(usize, usize)> = None;
        let mut consider = |start: usize, end: usize| {
            if end <= start {
                return;
            }
            if candidate.is_none_or(|(current, _)| start < current) {
                candidate = Some((start, end));
            }
        };

        for key in SENSITIVE_KEYS {
            let mut search_from = cursor;
            while let Some(relative) = lower[search_from..].find(key) {
                let key_start = search_from + relative;
                let mut value_start = key_start + key.len();
                while message[value_start..]
                    .chars()
                    .next()
                    .is_some_and(char::is_whitespace)
                {
                    value_start += message[value_start..].chars().next().unwrap().len_utf8();
                }
                let Some(separator) = message[value_start..].chars().next() else {
                    break;
                };
                if !matches!(separator, ':' | '=') {
                    search_from = key_start + key.len();
                    continue;
                }
                value_start += separator.len_utf8();
                while let Some(ch) = message[value_start..].chars().next() {
                    if ch.is_whitespace() || matches!(ch, '"' | '\'') {
                        value_start += ch.len_utf8();
                    } else {
                        break;
                    }
                }
                let redact_start = value_start;
                if lower[value_start..].starts_with("bearer ") {
                    value_start += "bearer ".len();
                }
                consider(redact_start, value_end(message, value_start));
                break;
            }
        }

        if let Some(relative) = lower[cursor..].find("bearer ") {
            let start = cursor + relative;
            let value_start = start + "bearer ".len();
            consider(start, value_end(message, value_start));
        }
        for prefix in ["sk-", "sk_"] {
            if let Some(relative) = lower[cursor..].find(prefix) {
                let start = cursor + relative;
                consider(start, value_end(message, start));
            }
        }
        candidate
    }

    let mut output = String::with_capacity(message.len());
    let mut cursor = 0;
    while let Some((start, end)) = next_sensitive_range(message, cursor) {
        output.push_str(&message[cursor..start]);
        output.push_str("[REDACTED]");
        cursor = end;
    }
    output.push_str(&message[cursor..]);
    output
}

fn extract_model_fragment(message: &str) -> Option<String> {
    let lower = message.to_ascii_lowercase();
    let marker_start = lower.find("model:")?;
    let candidate_start = marker_start + "model:".len();
    let candidate = message[candidate_start..]
        .trim_start()
        .trim_matches(|c: char| c == '"' || c == '\'' || c == '`')
        .chars()
        .take_while(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | ':'))
        .collect::<String>();
    is_safe_model_id(&candidate).then_some(candidate)
}

fn is_safe_model_id(model: &str) -> bool {
    !model.is_empty()
        && model != "<synthetic>"
        && model.len() <= 128
        && model
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | ':'))
}

/// Extract tool name from the top-level `toolUseResult` field.
///
/// Handles two formats:
/// - **Object format** (built-in tools): tries `toolUseResult.toolName`
/// - **Array format** (MCP tools): `[{type:"text", text:"..."}]` — parses the JSON text
///   and looks for `meta.tool` (common MCP convention, e.g. Yuque returns `skylark_user_recent`).
///   Falls back to `"mcp_tool"` when the array format is detected but tool name can't be extracted.
pub(super) fn extract_tool_name_from_result(msg: &Value) -> Option<String> {
    let result = msg.get("toolUseResult")?;

    // 1. Object format: try toolName directly (future Claude Code versions may add this)
    if let Some(name) = result
        .get("toolName")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        return Some(name.to_string());
    }

    // 2. Array format (MCP content blocks): [{type:"text", text:"..."}]
    //    MCP tool results come as content block arrays, not structured objects.
    //    The assistant tool_use message (which has the tool name) is often missing
    //    from transcripts for MCP calls, so we try to recover the name from the response.
    if let Some(arr) = result.as_array() {
        for block in arr {
            if block.get("type").and_then(|v| v.as_str()) == Some("text") {
                if let Some(text) = block.get("text").and_then(|v| v.as_str()) {
                    // Try to parse as JSON and extract meta.tool
                    if let Ok(parsed) = serde_json::from_str::<Value>(text) {
                        if let Some(tool) = parsed
                            .get("meta")
                            .and_then(|m| m.get("tool"))
                            .and_then(|v| v.as_str())
                            .filter(|s| !s.is_empty())
                        {
                            // Prefix with mcp__ so parse_agentic_tool recognizes it
                            // as MCP type → span name becomes "mcp {tool_name}"
                            return Some(format!("mcp__{}", tool));
                        }
                    }
                }
            }
        }
        // Detected MCP format (array) but couldn't extract specific tool name
        return Some("mcp__mcp_tool".to_string());
    }

    None
}

pub(super) fn build_assistant_from_merged(msg: &Value) -> AssistantMessage {
    let timestamp = get_timestamp(msg);
    // _end_timestamp is set by merge_assistant_messages; fall back to timestamp for single-chunk
    let end_timestamp = msg
        .get("_end_timestamp")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .unwrap_or(&timestamp)
        .to_string();
    let model = get_model(msg);
    let usage = get_usage(msg);
    let message_id = get_message_id(msg);
    let stop_reason = get_stop_reason(msg);
    let is_synthetic = msg
        .get("isApiErrorMessage")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    let mut text = String::new();
    let mut tool_uses = Vec::new();
    let mut thinking_blocks = Vec::new();

    if let Some(content) = get_content_value(msg) {
        text = extract_text(content);
        thinking_blocks = extract_thinking_blocks(content);
        if let Some(arr) = content.as_array() {
            for item in arr {
                let Some(obj) = item.as_object() else {
                    continue;
                };
                if obj.get("type").and_then(|v| v.as_str()) != Some("tool_use") {
                    continue;
                }
                let name = obj
                    .get("name")
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
                    .unwrap_or("unknown")
                    .to_string();
                let id = obj
                    .get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let input = obj.get("input").cloned().unwrap_or(Value::Null);
                tool_uses.push(ToolUse { name, id, input });
            }
        }
    }

    AssistantMessage {
        media_content: get_content_value(msg).filter(|v| agentic_otel_common::media::content_blocks(v).is_some()).cloned().unwrap_or(Value::Null),
        timestamp,
        end_timestamp,
        text,
        model,
        usage,
        tool_uses,
        message_id,
        stop_reason,
        thinking_blocks,
        is_synthetic,
    }
}

/// Merge two assistant message JSON values with the same message.id.
/// Content arrays are merged (deduplicated by type+id for tool_use, by type for others).
/// Usage and timestamp are taken from the newer message if present.
pub(super) fn merge_assistant_messages(existing: &Value, newer: &Value) -> Value {
    let mut result = existing.clone();

    // Merge content arrays
    let existing_content = get_content_value(existing)
        .and_then(|c| c.as_array())
        .cloned()
        .unwrap_or_default();
    let newer_content = get_content_value(newer)
        .and_then(|c| c.as_array())
        .cloned()
        .unwrap_or_default();

    // Build a set of existing content block identifiers for dedup
    let mut seen_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut merged_content: Vec<Value> = Vec::new();

    let content_block_key = |item: &Value| -> String {
        let item_type = item.get("type").and_then(|v| v.as_str()).unwrap_or("");
        let item_id = item.get("id").and_then(|v| v.as_str()).unwrap_or("");
        if !item_id.is_empty() {
            format!("{}:{}", item_type, item_id)
        } else {
            // For text blocks use "text" field, for thinking blocks use "thinking" field
            let text = match item_type {
                "thinking" => item.get("thinking").and_then(|v| v.as_str()).unwrap_or(""),
                _ => item.get("text").and_then(|v| v.as_str()).unwrap_or(""),
            };
            // Safe UTF-8 truncation: find the char boundary at or before byte 100
            let safe_end = text
                .char_indices()
                .take_while(|&(i, _)| i < 100)
                .last()
                .map(|(i, c)| i + c.len_utf8())
                .unwrap_or(0);
            format!("{}:{}", item_type, &text[..safe_end])
        }
    };

    // Add existing content blocks first
    for item in &existing_content {
        let key = content_block_key(item);
        if seen_ids.insert(key) {
            merged_content.push(item.clone());
        }
    }

    // Add newer content blocks (dedup)
    for item in &newer_content {
        let key = content_block_key(item);
        if seen_ids.insert(key) {
            merged_content.push(item.clone());
        }
    }

    // Set merged content on result
    if let Some(message) = result.get_mut("message") {
        message["content"] = Value::Array(merged_content);
    } else {
        result["content"] = Value::Array(merged_content);
    }

    // Take usage from newer if it has one (it's typically the final message)
    let newer_usage = newer
        .get("usage")
        .or_else(|| newer.get("message").and_then(|m| m.get("usage")));
    if let Some(usage) = newer_usage {
        if let Some(message) = result.get_mut("message") {
            message["usage"] = usage.clone();
        }
        result["usage"] = usage.clone();
    }

    // Take stop_reason from newer if present (final message typically has it)
    let newer_stop = get_stop_reason(newer);
    if !newer_stop.is_empty() {
        if let Some(message) = result.get_mut("message") {
            message["stop_reason"] = Value::String(newer_stop);
        }
    }

    // Track the newest timestamp as _end_timestamp (for generation span endTime).
    // Keep the original timestamp as-is (startTime = first chunk).
    let newer_ts = get_timestamp(newer);
    if !newer_ts.is_empty() {
        result["_end_timestamp"] = Value::String(newer_ts);
    }

    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    // --- get_obj_str ---
    #[test]
    fn test_get_obj_str_existing_key() {
        let obj = json!({"name": "test", "value": "hello"});
        assert_eq!(get_obj_str(&obj, "name"), "test");
        assert_eq!(get_obj_str(&obj, "value"), "hello");
    }

    #[test]
    fn test_get_obj_str_missing_key() {
        let obj = json!({"name": "test"});
        assert_eq!(get_obj_str(&obj, "missing"), "");
    }

    #[test]
    fn test_get_obj_str_non_string_value() {
        let obj = json!({"count": 42, "flag": true});
        assert_eq!(get_obj_str(&obj, "count"), "");
    }

    // --- get_role ---
    #[test]
    fn test_get_role_user() {
        let obj = json!({"type": "user", "message": {"role": "user"}});
        assert_eq!(get_role(&obj), "user");
    }

    #[test]
    fn test_get_role_assistant() {
        let obj = json!({"type": "assistant", "message": {"role": "assistant"}});
        assert_eq!(get_role(&obj), "assistant");
    }

    #[test]
    fn test_get_role_missing() {
        let obj = json!({"type": "system"});
        assert_eq!(get_role(&obj), "");
    }

    // --- get_timestamp ---
    #[test]
    fn test_get_timestamp_valid() {
        let obj = json!({"timestamp": "2026-03-27T10:00:00.000Z"});
        assert_eq!(get_timestamp(&obj), "2026-03-27T10:00:00.000Z");
    }

    #[test]
    fn test_get_timestamp_missing() {
        let obj = json!({"type": "user"});
        assert_eq!(get_timestamp(&obj), "");
    }

    // --- get_model ---
    #[test]
    fn test_get_model() {
        let obj = json!({"message": {"model": "claude-sonnet-4-20250514"}});
        assert_eq!(get_model(&obj), "claude-sonnet-4-20250514");
    }

    #[test]
    fn test_get_model_default() {
        let obj = json!({"message": {}});
        assert_eq!(get_model(&obj), "claude");
    }

    // --- get_message_id ---
    #[test]
    fn test_get_message_id() {
        let obj = json!({"message": {"id": "msg_001"}});
        assert_eq!(get_message_id(&obj), "msg_001");
    }

    #[test]
    fn test_get_message_id_missing() {
        let obj = json!({"type": "user"});
        assert_eq!(get_message_id(&obj), "");
    }

    #[test]
    fn test_get_prompt_chat_id_from_user_uuid() {
        let obj = json!({"type": "user", "uuid": "prompt-123", "message": {"role": "user", "content": "Hello"}});
        assert_eq!(get_prompt_chat_id(&obj).as_deref(), Some("prompt-123"));
    }

    #[test]
    fn test_get_prompt_chat_id_from_nested_message_id() {
        let obj = json!({"type": "user", "message": {"role": "user", "id": "prompt-456", "content": "Hello"}});
        assert_eq!(get_prompt_chat_id(&obj).as_deref(), Some("prompt-456"));
    }

    #[test]
    fn test_get_prompt_chat_id_missing() {
        let obj = json!({"type": "user", "message": {"role": "user", "content": "Hello"}});
        assert_eq!(get_prompt_chat_id(&obj), None);
    }

    #[test]
    fn test_sanitize_llm_error_message_redacts_secrets_and_truncates() {
        let message = format!(
            "request failed\nAuthorization: Bearer sk-ant-secret, api_key=another-secret {}",
            "x".repeat(600)
        );
        let sanitized = sanitize_llm_error_message(&message);

        assert!(!sanitized.contains("sk-ant-secret"));
        assert!(!sanitized.contains("another-secret"));
        assert!(sanitized.matches("[REDACTED]").count() >= 2);
        assert!(!sanitized.contains('\n'));
        assert_eq!(sanitized.chars().count(), 512);
    }

    // --- is_stop_hook_summary ---
    #[test]
    fn test_is_stop_hook_summary_true() {
        let obj = json!({"type": "system", "subtype": "stop_hook_summary"});
        assert!(is_stop_hook_summary(&obj));
    }

    #[test]
    fn test_is_stop_hook_summary_false() {
        let obj = json!({"type": "user"});
        assert!(!is_stop_hook_summary(&obj));
    }

    // --- is_tool_result ---
    #[test]
    fn test_is_tool_result_true() {
        let obj = json!({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_001", "content": "result"}]}});
        assert!(is_tool_result(&obj));
    }

    #[test]
    fn test_is_tool_result_false_string_content() {
        let obj = json!({"type": "user", "message": {"role": "user", "content": "Hello"}});
        assert!(!is_tool_result(&obj));
    }

    #[test]
    fn test_is_tool_result_false_assistant() {
        let obj = json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}});
        assert!(!is_tool_result(&obj));
    }

    // --- extract_text ---
    #[test]
    fn test_extract_text_string_content() {
        let content = json!("Hello world");
        assert_eq!(extract_text(&content), "Hello world");
    }

    #[test]
    fn test_extract_text_array_content() {
        let content = json!([{"type": "text", "text": "Hello"}, {"type": "text", "text": "world"}]);
        let text = extract_text(&content);
        assert!(text.contains("Hello"));
        assert!(text.contains("world"));
    }

    #[test]
    fn test_extract_text_empty_array() {
        let content = json!([]);
        assert_eq!(extract_text(&content), "");
    }

    // --- extract_skill_name_from_meta ---
    #[test]
    fn test_extract_skill_name_from_meta() {
        let text =
            "Base directory for this skill: /Users/x/.claude/skills/my-skill\nSome instructions";
        assert_eq!(
            extract_skill_name_from_meta(text),
            Some("my-skill".to_string())
        );
    }

    #[test]
    fn test_extract_skill_name_from_meta_no_prefix() {
        let text = "Just some regular text";
        assert_eq!(extract_skill_name_from_meta(text), None);
    }

    // --- extract_tool_name_from_result ---

    #[test]
    fn test_extract_tool_name_object_with_tool_name() {
        let msg = json!({
            "toolUseResult": {"toolName": "Read", "stdout": "hello"}
        });
        assert_eq!(
            extract_tool_name_from_result(&msg),
            Some("Read".to_string())
        );
    }

    #[test]
    fn test_extract_tool_name_object_without_tool_name() {
        // Built-in tool result (object format, no toolName field)
        let msg = json!({
            "toolUseResult": {"stdout": "output", "stderr": ""}
        });
        assert_eq!(extract_tool_name_from_result(&msg), None);
    }

    #[test]
    fn test_extract_tool_name_mcp_with_meta_tool() {
        // MCP tool result with meta.tool in response JSON
        let msg = json!({
            "toolUseResult": [
                {
                    "type": "text",
                    "text": "{\"ok\":true,\"data\":[],\"meta\":{\"tool\":\"skylark_user_recent\",\"pagination\":{}}}"
                }
            ]
        });
        assert_eq!(
            extract_tool_name_from_result(&msg),
            Some("mcp__skylark_user_recent".to_string())
        );
    }

    #[test]
    fn test_extract_tool_name_mcp_without_meta_tool() {
        // MCP tool result without meta.tool — fallback to "mcp_tool"
        let msg = json!({
            "toolUseResult": [
                {"type": "text", "text": "plain text response without JSON"}
            ]
        });
        assert_eq!(
            extract_tool_name_from_result(&msg),
            Some("mcp__mcp_tool".to_string())
        );
    }

    #[test]
    fn test_extract_tool_name_mcp_json_without_meta() {
        // MCP result is valid JSON but has no meta.tool field
        let msg = json!({
            "toolUseResult": [
                {"type": "text", "text": "{\"result\":\"success\",\"count\":42}"}
            ]
        });
        assert_eq!(
            extract_tool_name_from_result(&msg),
            Some("mcp__mcp_tool".to_string())
        );
    }

    #[test]
    fn test_extract_tool_name_no_tool_use_result() {
        let msg = json!({"type": "user"});
        assert_eq!(extract_tool_name_from_result(&msg), None);
    }
}
