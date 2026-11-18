//! OpenAI API message format types and serialization utilities.
//!
//! Provides a standard representation for `gen_ai.input.messages`,
//! `gen_ai.output.messages`, and `gen_ai.system_instructions` attributes
//! across all agent hooks (cchooks, cdxhooks, etc.).

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::time::Instant;

use crate::TruncationStats;

/// Maximum character length for serialized message JSON strings.
/// 4 MiB (4 * 1024 * 1024). Soft truncation threshold for gen_ai.input/output.messages.
const MAX_MESSAGE_CHARS: usize = 4 * 1024 * 1024;

// ── OpenAI API message types ────────────────────────────────────────

/// A single message in OpenAI API format.
///
/// Supports user, assistant (with optional tool_calls), tool result,
/// and system message roles.
#[derive(Clone, Debug)]
pub struct OpenAIMessage {
    pub role: String,
    pub content: Option<String>,
    pub content_blocks: Option<Vec<Value>>,
    pub reasoning_content: Option<String>,
    pub tool_calls: Option<Vec<ToolCall>>,
    pub tool_call_id: Option<String>,
    pub finish_reason: Option<String>,
}

impl<'de> Deserialize<'de> for OpenAIMessage {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        struct WireMessage {
            role: String,
            content: Option<Value>,
            reasoning_content: Option<String>,
            tool_calls: Option<Vec<ToolCall>>,
            tool_call_id: Option<String>,
            finish_reason: Option<String>,
        }
        let wire = WireMessage::deserialize(deserializer)?;
        let (content, content_blocks) = match wire.content {
            None | Some(Value::Null) => (None, None),
            Some(Value::String(text)) => (Some(text), None),
            Some(value @ Value::Array(_)) => {
                let blocks = crate::media::content_blocks(&value);
                let text = blocks.is_none().then(|| {
                    value
                        .as_array()
                        .unwrap()
                        .iter()
                        .filter_map(|part| part.get("text").and_then(Value::as_str))
                        .collect::<Vec<_>>()
                        .join("\n")
                });
                (text, blocks)
            }
            Some(_) => {
                return Err(serde::de::Error::custom(
                    "message content must be text or typed blocks",
                ))
            }
        };
        Ok(Self {
            role: wire.role,
            content,
            content_blocks,
            reasoning_content: wire.reasoning_content,
            tool_calls: wire.tool_calls,
            tool_call_id: wire.tool_call_id,
            finish_reason: wire.finish_reason,
        })
    }
}

impl Serialize for OpenAIMessage {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeMap;
        let mut map = serializer.serialize_map(None)?;
        map.serialize_entry("role", &self.role)?;
        if let Some(blocks) = &self.content_blocks {
            map.serialize_entry("content", blocks)?;
        } else if let Some(content) = &self.content {
            map.serialize_entry("content", content)?;
        }
        if let Some(v) = &self.reasoning_content {
            map.serialize_entry("reasoning_content", v)?;
        }
        if let Some(v) = &self.tool_calls {
            map.serialize_entry("tool_calls", v)?;
        }
        if let Some(v) = &self.tool_call_id {
            map.serialize_entry("tool_call_id", v)?;
        }
        if let Some(v) = &self.finish_reason {
            map.serialize_entry("finish_reason", v)?;
        }
        map.end()
    }
}

impl OpenAIMessage {
    pub fn with_content(mut self, content: &Value) -> Self {
        fn blocks(value: &Value, depth: usize) -> Option<Vec<Value>> {
            if depth > 16 {
                return None;
            }
            if let Some(parts) = crate::media::content_blocks(value) {
                return Some(parts);
            }
            if let Some(part) = crate::media::media_block(value) {
                return Some(vec![part]);
            }
            for key in ["content", "parts", "output", "data"] {
                if let Some(parts) = value.get(key).and_then(|nested| blocks(nested, depth + 1)) {
                    return Some(parts);
                }
            }
            None
        }
        self.content_blocks = blocks(content, 0);
        self
    }
}

/// A tool call within an assistant message.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ToolCall {
    pub id: String,
    #[serde(rename = "type")]
    pub call_type: String,
    pub function: ToolCallFunction,
}

/// Function details within a tool call.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ToolCallFunction {
    pub name: String,
    /// JSON-encoded arguments string, per OpenAI spec.
    pub arguments: String,
}

// ── Builder methods ─────────────────────────────────────────────────

impl OpenAIMessage {
    /// Create a user message.
    pub fn user(content: &str) -> Self {
        Self {
            role: "user".into(),
            content: Some(content.into()),
            content_blocks: None,
            reasoning_content: None,
            tool_calls: None,
            tool_call_id: None,
            finish_reason: None,
        }
    }

    /// Create an assistant message with text content.
    pub fn assistant(content: &str) -> Self {
        Self {
            role: "assistant".into(),
            content: Some(content.into()),
            content_blocks: None,
            reasoning_content: None,
            tool_calls: None,
            tool_call_id: None,
            finish_reason: None,
        }
    }

    /// Create an assistant message with text content and finish reason.
    pub fn assistant_with_finish(content: &str, finish_reason: &str) -> Self {
        Self {
            role: "assistant".into(),
            content: Some(content.into()),
            content_blocks: None,
            reasoning_content: None,
            tool_calls: None,
            tool_call_id: None,
            finish_reason: Some(finish_reason.into()),
        }
    }

    /// Create an assistant message with tool calls (and optional text).
    pub fn assistant_with_tool_calls(content: Option<&str>, tool_calls: Vec<ToolCall>) -> Self {
        Self {
            role: "assistant".into(),
            content: content.map(|s| s.into()),
            content_blocks: None,
            reasoning_content: None,
            tool_calls: if tool_calls.is_empty() {
                None
            } else {
                Some(tool_calls)
            },
            tool_call_id: None,
            finish_reason: None,
        }
    }

    /// Create a tool result message.
    pub fn tool_result(tool_call_id: &str, content: &str) -> Self {
        Self {
            role: "tool".into(),
            content: Some(content.into()),
            content_blocks: None,
            reasoning_content: None,
            tool_calls: None,
            tool_call_id: Some(tool_call_id.into()),
            finish_reason: None,
        }
    }

    /// Create a system message.
    pub fn system(content: &str) -> Self {
        Self {
            role: "system".into(),
            content: Some(content.into()),
            content_blocks: None,
            reasoning_content: None,
            tool_calls: None,
            tool_call_id: None,
            finish_reason: None,
        }
    }
}

impl ToolCall {
    /// Create a function-type tool call.
    pub fn function(id: &str, name: &str, arguments: &str) -> Self {
        Self {
            id: id.into(),
            call_type: "function".into(),
            function: ToolCallFunction {
                name: name.into(),
                arguments: arguments.into(),
            },
        }
    }

    /// Create a function-type tool call from a serde_json::Value for arguments.
    pub fn function_from_value(id: &str, name: &str, arguments: &serde_json::Value) -> Self {
        Self::function(
            id,
            name,
            &serde_json::to_string(arguments).unwrap_or_default(),
        )
    }
}

// ── Serialization with soft truncation ─────────────────────────────

/// Character threshold for soft truncation. Content with ≤ this many
/// characters is kept as-is; longer content keeps the first 100 chars
/// followed by `...[TRUNCATED]`.
const SOFT_TRUNCATE_CHARS: usize = 100;

/// Soft-truncate a string: keep first 100 characters + `...[TRUNCATED]`.
/// Strings with ≤ 100 characters are returned unchanged.
fn soft_truncate_str(s: &str) -> String {
    match s.char_indices().nth(SOFT_TRUNCATE_CHARS) {
        None => s.to_string(),
        Some((byte_pos, _)) => format!("{}...[TRUNCATED]", &s[..byte_pos]),
    }
}

/// Soft-truncate a message's text content, preserving full structure.
///
/// - `content` / `reasoning_content`: first 100 chars + `...[TRUNCATED]`
/// - `tool_calls.arguments`: **preserved as-is** (not truncated)
/// - `role`, `tool_call_id`, `finish_reason`: preserved
fn soft_truncate_message(msg: &OpenAIMessage) -> OpenAIMessage {
    let mut m = msg.clone();
    if let Some(ref c) = m.content {
        m.content = Some(soft_truncate_str(c));
    }
    if let Some(ref c) = m.reasoning_content {
        m.reasoning_content = Some(soft_truncate_str(c));
    }
    // tool_call arguments are preserved — callers need full args
    m
}

/// Serialize messages to a JSON string with soft truncation.
///
/// When the full serialization exceeds `max_chars`, messages are
/// soft-truncated from the beginning of the array, **skipping** the
/// last `protected_tail` messages which are never truncated.
///
/// The `max_chars` limit is **soft**: if all truncatable messages have
/// been processed and the result still exceeds `max_chars`, it is
/// returned as-is rather than dropping messages.
///
/// Soft truncation rules:
/// - `content` > 100 chars → first 100 + `...[TRUNCATED]`
/// - `content` ≤ 100 chars → unchanged
/// - `tool_call.arguments` → always preserved
pub fn serialize_messages(
    messages: &[OpenAIMessage],
    max_chars: usize,
    protected_tail: usize,
) -> String {
    let (json, _) = serialize_messages_with_stats(messages, max_chars, protected_tail, "messages");
    json
}

/// Serialize messages to JSON and return safe truncation cost metadata.
pub fn serialize_messages_with_stats(
    messages: &[OpenAIMessage],
    max_chars: usize,
    protected_tail: usize,
    field_kind: &str,
) -> (String, TruncationStats) {
    let started = Instant::now();
    let full = serde_json::to_string(messages).unwrap_or_else(|_| "[]".into());
    let original_bytes = full.len();
    if full.len() <= max_chars {
        let stats = TruncationStats::new(
            "serialize_messages",
            field_kind,
            original_bytes,
            full.len(),
            max_chars,
            false,
            "within_limit",
            0,
            false,
            started.elapsed().as_millis(),
        );
        return (full, stats);
    }

    let len = messages.len();
    let truncatable = len.saturating_sub(protected_tail);

    if truncatable == 0 {
        // All messages are protected — return as-is (soft limit).
        let stats = TruncationStats::new(
            "serialize_messages",
            field_kind,
            original_bytes,
            full.len(),
            max_chars,
            false,
            "protected_tail",
            0,
            false,
            started.elapsed().as_millis(),
        );
        return (full, stats);
    }

    // Progressive soft truncation from the beginning.
    // Each iteration truncates one more message's content, keeping
    // the rest intact so we preserve as much recent context as possible.
    let mut mixed = messages.to_vec();
    let mut iterations = 0usize;
    for i in 0..truncatable {
        mixed[i] = soft_truncate_message(&mixed[i]);
        iterations += 1;
        let json = serde_json::to_string(&mixed).unwrap_or_default();
        if json.len() <= max_chars {
            let stats = TruncationStats::new(
                "serialize_messages",
                field_kind,
                original_bytes,
                json.len(),
                max_chars,
                true,
                "soft_limit",
                iterations,
                false,
                started.elapsed().as_millis(),
            );
            return (json, stats);
        }
    }

    // All truncatable messages processed — return best effort (soft limit).
    let json = serde_json::to_string(&mixed).unwrap_or_else(|_| "[]".into());
    let stats = TruncationStats::new(
        "serialize_messages",
        field_kind,
        original_bytes,
        json.len(),
        max_chars,
        true,
        "soft_limit_exceeded",
        iterations,
        false,
        started.elapsed().as_millis(),
    );
    (json, stats)
}

/// Serialize messages with the default max character limit (20,000).
pub fn serialize_messages_default(messages: &[OpenAIMessage], protected_tail: usize) -> String {
    serialize_messages(messages, MAX_MESSAGE_CHARS, protected_tail)
}

/// Serialize messages with the default limit and return safe cost metadata.
pub fn serialize_messages_default_with_stats(
    messages: &[OpenAIMessage],
    protected_tail: usize,
    field_kind: &str,
) -> (String, TruncationStats) {
    serialize_messages_with_stats(messages, MAX_MESSAGE_CHARS, protected_tail, field_kind)
}

/// Serialize a single system instruction to `gen_ai.system_instructions` format.
pub fn serialize_system_instructions(content: &str, max_chars: usize) -> String {
    serialize_messages(&[OpenAIMessage::system(content)], max_chars, 0)
}

// ── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_container_media_becomes_ordered_message_content() {
        let content = serde_json::json!({"content":[{"type":"text","text":"before"},{"type":"input_audio","input_audio":{"format":"wav","data":"YWJj"}}]});
        let message = OpenAIMessage::tool_result("call-1", "fallback").with_content(&content);
        let wire = serde_json::to_value(message).unwrap();
        assert_eq!(wire["content"][0]["text"], "before");
        assert_eq!(wire["content"][1]["source"]["data"], "YWJj");
        assert_eq!(wire["tool_call_id"], "call-1");
    }

    #[test]
    fn test_user_message() {
        let msg = OpenAIMessage::user("hello");
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""role":"user"#));
        assert!(json.contains(r#""content":"hello"#));
        assert!(!json.contains("tool_calls"));
        assert!(!json.contains("tool_call_id"));
        assert!(!json.contains("finish_reason"));
    }

    #[test]
    fn test_assistant_with_tool_calls() {
        let tc = ToolCall::function("call_1", "get_weather", r#"{"location":"Paris"}"#);
        let msg = OpenAIMessage::assistant_with_tool_calls(None, vec![tc]);
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""role":"assistant"#));
        assert!(!json.contains(r#""content""#)); // None is skipped
        assert!(json.contains(r#""tool_calls":[{"id":"call_1""#));
        assert!(json.contains(r#""type":"function""#));
        assert!(json.contains(r#""name":"get_weather""#));
    }

    #[test]
    fn test_tool_result_message() {
        let msg = OpenAIMessage::tool_result("call_1", "rainy, 57°F");
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""role":"tool"#));
        assert!(json.contains(r#""tool_call_id":"call_1"#));
        assert!(json.contains(r#""content":"rainy, 57°F"#));
    }

    #[test]
    fn test_system_message() {
        let msg = OpenAIMessage::system("You are helpful.");
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""role":"system"#));
        assert!(json.contains(r#""content":"You are helpful."#));
    }

    #[test]
    fn test_serialize_messages_within_limit() {
        let msgs = vec![OpenAIMessage::user("hi"), OpenAIMessage::assistant("hello")];
        let json = serialize_messages(&msgs, 10000, 0);
        let parsed: Vec<OpenAIMessage> = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.len(), 2);
    }

    #[test]
    fn test_serialize_messages_soft_truncates_content() {
        // Each message ~220 chars when serialized; 10 messages ~2200 chars
        let msgs: Vec<OpenAIMessage> = (0..10)
            .map(|i| OpenAIMessage::user(&format!("message {} content: {}", i, "x".repeat(200))))
            .collect();
        let full_len = serde_json::to_string(&msgs).unwrap().len();
        assert!(full_len > 2000, "full should exceed limit");
        let json = serialize_messages(&msgs, 2000, 0);
        let parsed: Vec<OpenAIMessage> = serde_json::from_str(&json).unwrap();
        // All messages preserved (content soft-truncated, not dropped)
        assert_eq!(parsed.len(), 10);
        // Early messages have soft-truncated content (first 100 chars + ...[TRUNCATED])
        let first = parsed[0].content.as_deref().unwrap();
        assert!(
            first.starts_with("message 0 content: "),
            "should keep first 100 chars"
        );
        assert!(
            first.ends_with("...[TRUNCATED]"),
            "should end with ...[TRUNCATED]"
        );
        // Last message keeps original content
        assert!(parsed
            .last()
            .unwrap()
            .content
            .as_deref()
            .unwrap()
            .contains("message 9 content"));
    }

    #[test]
    fn test_serialize_messages_stats_record_iterations_without_payload() {
        let msgs: Vec<OpenAIMessage> = (0..4)
            .map(|i| OpenAIMessage::user(&format!("secret-{i}:{}", "x".repeat(300))))
            .collect();
        let (json, stats) = serialize_messages_with_stats(&msgs, 700, 0, "gen_ai.input.messages");
        let parsed: Vec<OpenAIMessage> = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.len(), 4);
        assert!(stats.truncated);
        assert!(stats.iterations > 0);
        assert_eq!(
            stats.original_bytes,
            serde_json::to_string(&msgs).unwrap().len()
        );
        assert_eq!(stats.kept_bytes, json.len());
        assert_eq!(stats.limit_bytes, 700);
        assert!(!stats.hash_present);

        let line = stats
            .with_log_context(
                "generation_messages",
                "Stop",
                "evt-1",
                "worker-1",
                "gen_ai.input.messages",
            )
            .to_safe_log_fields();
        assert!(line.contains("\"iterations\":"));
        assert!(!line.contains("secret-0"));
        assert!(!line.contains(&"x".repeat(64)));
    }

    #[test]
    fn test_serialize_messages_preserves_structure() {
        let msgs = vec![
            OpenAIMessage::assistant_with_tool_calls(
                Some("thinking..."),
                vec![ToolCall::function(
                    "call_1",
                    "Read",
                    r#"{"path":"big_file.rs"}"#,
                )],
            ),
            OpenAIMessage::tool_result("call_1", &"x".repeat(30000)),
            OpenAIMessage::user("What does this do?"),
        ];
        // protected_tail=1 to protect user message at end
        let json = serialize_messages(&msgs, 20000, 1);
        let parsed: Vec<OpenAIMessage> = serde_json::from_str(&json).unwrap();
        // All 3 messages preserved
        assert_eq!(parsed.len(), 3);
        assert_eq!(parsed[0].role, "assistant");
        // Tool call arguments preserved (never truncated)
        let tc = parsed[0].tool_calls.as_ref().unwrap();
        assert_eq!(tc[0].function.name, "Read");
        assert_eq!(tc[0].function.arguments, r#"{"path":"big_file.rs"}"#);
        // Tool result soft-truncated (first 100 chars kept)
        let tool_content = parsed[1].content.as_deref().unwrap();
        assert!(tool_content.ends_with("...[TRUNCATED]"));
        assert_eq!(parsed[1].tool_call_id.as_deref(), Some("call_1"));
        // User message preserved (protected tail)
        assert_eq!(parsed[2].role, "user");
        assert_eq!(parsed[2].content.as_deref(), Some("What does this do?"));
    }

    #[test]
    fn test_serialize_messages_single_large_content() {
        let big = "x".repeat(30000);
        let msgs = vec![OpenAIMessage::user(&big)];
        // With protected_tail=0, the single message gets soft-truncated
        let json = serialize_messages(&msgs, 20000, 0);
        let parsed: Vec<OpenAIMessage> = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.len(), 1);
        // Soft-truncated: first 100 chars + ...[TRUNCATED]
        let content = parsed[0].content.as_deref().unwrap();
        assert!(content.ends_with("...[TRUNCATED]"));
        assert!(content.len() < 200); // ~100 chars + marker
    }

    #[test]
    fn test_serialize_messages_single_large_protected() {
        let big = "x".repeat(30000);
        let msgs = vec![OpenAIMessage::user(&big)];
        // With protected_tail=1, the message is protected — soft limit exceeded
        let json = serialize_messages(&msgs, 20000, 1);
        let parsed: Vec<OpenAIMessage> = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.len(), 1);
        // Protected: full content preserved (soft limit)
        assert_eq!(parsed[0].content.as_deref().unwrap().len(), 30000);
    }

    #[test]
    fn test_serialize_messages_empty() {
        let json = serialize_messages(&[], 1000, 0);
        assert_eq!(json, "[]");
    }

    #[test]
    fn test_serialize_system_instructions() {
        let json = serialize_system_instructions("Be helpful.", 10000);
        assert!(json.contains(r#""role":"system"#));
        assert!(json.contains(r#""content":"Be helpful."#));
    }

    #[test]
    fn test_tool_call_from_value() {
        let args = serde_json::json!({"location": "Paris"});
        let tc = ToolCall::function_from_value("call_1", "get_weather", &args);
        assert_eq!(tc.function.arguments, r#"{"location":"Paris"}"#);
    }

    #[test]
    fn test_assistant_with_finish_reason() {
        let msg = OpenAIMessage::assistant_with_finish("Done.", "stop");
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""finish_reason":"stop"#));
    }

    #[test]
    fn test_full_conversation_roundtrip() {
        let msgs = vec![
            OpenAIMessage::user("Weather in Paris?"),
            OpenAIMessage::assistant_with_tool_calls(
                None,
                vec![ToolCall::function(
                    "call_1",
                    "get_weather",
                    r#"{"location":"Paris"}"#,
                )],
            ),
            OpenAIMessage::tool_result("call_1", "rainy, 57°F"),
        ];
        let json = serialize_messages(&msgs, 10000, 0);
        let parsed: Vec<OpenAIMessage> = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.len(), 3);
        assert_eq!(parsed[0].role, "user");
        assert_eq!(parsed[1].role, "assistant");
        assert!(parsed[1].tool_calls.is_some());
        assert_eq!(parsed[2].role, "tool");
        assert_eq!(parsed[2].tool_call_id.as_deref(), Some("call_1"));
    }

    #[test]
    fn test_protected_tail_prevents_truncation() {
        let msgs = vec![
            OpenAIMessage::tool_result("call_1", &"x".repeat(500)),
            OpenAIMessage::tool_result("call_2", &"y".repeat(500)),
            OpenAIMessage::user("short question"),
        ];
        // protected_tail=1: last message (user) is never truncated
        let json = serialize_messages(&msgs, 500, 1);
        let parsed: Vec<OpenAIMessage> = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.len(), 3);
        // User message at end preserved
        assert_eq!(parsed[2].content.as_deref(), Some("short question"));
        // Earlier messages soft-truncated
        let first = parsed[0].content.as_deref().unwrap();
        assert!(first.ends_with("...[TRUNCATED]"));
    }

    #[test]
    fn test_serialize_messages_default_with_protected_tail() {
        let msgs = vec![
            OpenAIMessage::user("hello"),
            OpenAIMessage::assistant("world"),
        ];
        let json = serialize_messages_default(&msgs, 0);
        let parsed: Vec<OpenAIMessage> = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.len(), 2);
    }
}

#[cfg(test)]
mod media_tests {
    use super::*;
    #[test]
    fn typed_content_roundtrip_and_soft_budget_preserve_media() {
        let raw = serde_json::json!([{"type":"text","text":"before"},{"type":"audio","mime_type":"audio/ogg","data":"YWJj"}]);
        let message = OpenAIMessage::user("before").with_content(&raw);
        let serialized = serialize_messages(&[message], 1, 0);
        let parsed: Vec<OpenAIMessage> = serde_json::from_str(&serialized).unwrap();
        assert_eq!(
            parsed[0].content_blocks.as_ref().unwrap()[1]["source"]["data"],
            "YWJj"
        );
        let value: Value = serde_json::from_str(&serialized).unwrap();
        assert_eq!(value[0]["content"][0]["text"], "before");
    }
}
