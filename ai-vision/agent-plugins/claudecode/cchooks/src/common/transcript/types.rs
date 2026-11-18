pub use agentic_otel_common::TurnTrigger;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone)]
pub struct TokenUsage {
    pub input: Option<i64>,
    pub output: Option<i64>,
    pub cache_creation_input_tokens: Option<i64>,
    pub cache_read_input_tokens: Option<i64>,
}

impl TokenUsage {
    pub fn has_any(&self) -> bool {
        self.input.is_some()
            || self.output.is_some()
            || self.cache_creation_input_tokens.is_some()
            || self.cache_read_input_tokens.is_some()
    }

    pub(super) fn all_zero(&self) -> bool {
        [
            self.input,
            self.output,
            self.cache_creation_input_tokens,
            self.cache_read_input_tokens,
        ]
        .into_iter()
        .all(|value| matches!(value, None | Some(0)))
    }

    pub fn input_total(&self) -> Option<i64> {
        let has_input_usage = self.input.is_some()
            || self.cache_creation_input_tokens.is_some()
            || self.cache_read_input_tokens.is_some();
        has_input_usage.then(|| {
            self.input.unwrap_or(0)
                + self.cache_creation_input_tokens.unwrap_or(0)
                + self.cache_read_input_tokens.unwrap_or(0)
        })
    }
}

#[derive(Debug, Clone)]
pub struct ToolUse {
    pub name: String,
    pub id: String,
    pub input: Value,
}

#[derive(Debug, Clone)]
pub struct ApiError {
    pub timestamp: String,
    pub error: Value,
    pub retry_attempt: u32,
    pub max_retries: u32,
    pub retry_in_ms: f64,
}

#[derive(Debug, Clone)]
pub struct TerminalLlmError {
    pub error_type: String,
    pub message: String,
    pub status_code: Option<i64>,
    pub request_model: Option<String>,
}

#[derive(Debug, Clone)]
pub struct CompactionEvent {
    /// Stable identity for this compact_boundary transcript event.
    /// Native transcript uuid wins; legacy fixtures fall back to a content hash.
    #[allow(dead_code)]
    pub identity: String,
    pub timestamp: String,
    pub trigger: String, // "auto" | "manual"
    pub pre_tokens: u64,
    pub post_tokens: u64, // compactMetadata.postTokens, or next assistant's usage fallback
    pub end_timestamp: String, // boundary timestamp when postTokens exists, otherwise next assistant timestamp
    pub sequence: u32,         // nth compaction in session (1-based)
}

#[derive(Debug, Clone, Default)]
pub struct TurnBuildResult {
    pub turns: Vec<Turn>,
    /// compact_boundary events seen in this parse window that could not be
    /// attached to a preceding turn because that turn was outside the window.
    #[allow(dead_code)]
    pub unattached_compactions: Vec<CompactionEvent>,
}

#[derive(Debug, Clone)]
pub struct ToolResultItem {
    pub tool_use_id: String,
    pub content: Value,
    /// `is_error` from the tool_result content item itself.
    pub is_error: bool,
    /// Claude Code top-level denial kind for rejected tool calls.
    #[allow(dead_code)]
    pub tool_denial_kind: Option<String>,
    /// String-form top-level `toolUseResult`, used by Claude Code for user rejection.
    pub tool_use_result_text: Option<String>,
    /// Normalized strong cancellation reason, when present.
    pub cancellation_reason: Option<String>,
    /// Tool name extracted from the top-level `toolUseResult` field in newer Claude Code
    /// transcript format. Used as fallback when `tool_uses` is empty (e.g. when the
    /// assistant message's content array lacks `type: "tool_use"` blocks).
    pub tool_name: Option<String>,
    /// Timestamp when the tool finished execution (from the tool_result event).
    /// Used as tool span endTimeUnixNano for accurate duration tracking.
    pub timestamp: String,
    /// Agent ID from `toolUseResult.agentId` (subagent tool results only).
    pub agent_id: Option<String>,
    /// Agent type from `toolUseResult.agentType` (e.g. "Explore", "general-purpose").
    pub agent_type: Option<String>,
    /// Whether the subagent was launched asynchronously.
    /// From `toolUseResult.isAsync` or `toolUseResult.status == "async_launched"`
    /// (Claude Code v2.1.199+; older versions use tool input `run_in_background`).
    pub is_async: bool,
}

#[derive(Debug, Clone)]
pub struct ToolResultMessage {
    #[allow(dead_code)]
    pub timestamp: String,
    pub items: Vec<ToolResultItem>,
}

#[derive(Debug, Clone)]
pub struct AssistantMessage {
    /// Timestamp of the first assistant chunk (startTime).
    pub timestamp: String,
    /// Timestamp of the last assistant chunk (endTime).
    /// For merged messages this is the newest chunk's timestamp.
    pub end_timestamp: String,
    pub text: String,
    pub media_content: Value,
    pub model: String,
    pub usage: TokenUsage,
    pub tool_uses: Vec<ToolUse>,
    /// API response identifier (message.id)
    pub message_id: String,
    /// Why the model stopped generating (e.g., "end_turn", "tool_use")
    pub stop_reason: String,
    /// Thinking content blocks from the LLM response.
    /// Each item: {"type": "thinking", "text": "..."}
    pub thinking_blocks: Vec<Value>,
    /// Whether this is a synthetic assistant message generated by the client
    /// when API retries are exhausted (isApiErrorMessage=true in transcript).
    pub is_synthetic: bool,
}

#[derive(Debug, Clone)]
pub struct Turn {
    /// Native prompt/chat identifier from the transcript when available.
    /// Claude Code fixtures often omit this, so OTel assembly falls back to trace_id.
    pub chat_id: Option<String>,
    pub trigger: TurnTrigger,
    pub user_timestamp: String,
    pub user_text: String,
    pub user_content: Value,
    pub assistants: Vec<AssistantMessage>,
    pub tool_results: Vec<ToolResultMessage>,
    /// Skill name extracted from isMeta user messages (command-style Skill invocation).
    /// Parsed from "Base directory for this skill: /path/to/<skill-name>" in the isMeta content.
    pub skill_name: Option<String>,
    /// API errors collected during this turn (type=system, subtype=api_error events).
    pub api_errors: Vec<ApiError>,
    /// Terminal synthetic LLM/API failure for this turn, if retries exhausted.
    pub terminal_llm_error: Option<TerminalLlmError>,
    /// Compaction events that occurred at the end of this turn (type=system, subtype=compact_boundary).
    pub compactions: Vec<CompactionEvent>,
    /// Whether this turn was cancelled/interrupted by the user (Escape key).
    /// Detected by "[Request interrupted by user]" marker in the following user message.
    pub cancelled: bool,
    /// Subagent task completion notifications (queue-operation lines) whose
    /// tool_use_id was launched in this turn. Used to backfill async subagent
    /// spans with real completion data (CC v2.1.199+).
    pub task_notifications: Vec<TaskNotification>,
}

/// A subagent task completion notification parsed from a `queue-operation`
/// transcript line (embedded `<task-notification>` XML, CC v2.1.199+).
#[derive(Debug, Clone)]
pub struct TaskNotification {
    pub task_id: String,
    pub tool_use_id: String,
    pub status: String,
    pub result: String,
    pub subagent_tokens: Option<i64>,
    pub tool_uses: Option<i64>,
    #[allow(dead_code)]
    pub duration_ms: Option<i64>,
    /// queue-operation line timestamp — approximate subagent completion time.
    pub timestamp: String,
}
#[derive(Debug, Clone)]
pub struct JsonlRecord {
    pub value: Value,
    pub start_offset: i64,
    pub end_offset: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TranscriptSourceEpoch {
    pub identity_digest: String,
    pub prefix_len: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranscriptSourceSnapshot {
    pub epoch: TranscriptSourceEpoch,
    pub file_len: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TurnTranscriptInspection {
    pub snapshot: TranscriptSourceSnapshot,
    pub canonical_path: String,
    pub prompt_start: Option<i64>,
}

#[derive(Debug, Clone)]
pub enum BoundedJsonlScan {
    Ready {
        records: Vec<JsonlRecord>,
        next_offset: i64,
    },
    CaughtUp {
        next_offset: i64,
    },
    WaitingForMoreData {
        record_start: i64,
    },
    Terminal {
        reason: &'static str,
        record_start: i64,
        observed_bytes: u64,
    },
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn token_usage_input_total_includes_cache_components() {
        let usage = TokenUsage {
            input: Some(10),
            output: Some(3),
            cache_creation_input_tokens: Some(4),
            cache_read_input_tokens: Some(6),
        };
        assert_eq!(usage.input_total(), Some(20));
    }
}
