//! types ownership module.

use super::Value;
use super::{
    CollabAgentEvent, InvocationEndSource, SkillInvocation, SubagentSourceInfo, TurnTerminalState,
};
pub use agentic_otel_common::TurnTrigger;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TokenUsageSource {
    NativeRecord,
    LegacyTokenCount,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct TokenUsage {
    pub input: Option<i64>,
    pub output: Option<i64>,
    pub total: Option<i64>,
    pub cached_input: Option<i64>,
    pub reasoning_output: Option<i64>,
    pub cache_creation_input_tokens: Option<i64>,
    pub cache_read_input_tokens: Option<i64>,
    pub response_id: Option<String>,
    pub source: Option<TokenUsageSource>,
}

impl TokenUsage {
    pub fn has_any(&self) -> bool {
        self.input.is_some()
            || self.output.is_some()
            || self.total.is_some()
            || self.cached_input.is_some()
            || self.reasoning_output.is_some()
            || self.cache_creation_input_tokens.is_some()
            || self.cache_read_input_tokens.is_some()
    }

    fn is_zero_or_missing(value: Option<i64>) -> bool {
        matches!(value, None | Some(0))
    }

    fn all_zero(&self) -> bool {
        Self::is_zero_or_missing(self.input)
            && Self::is_zero_or_missing(self.output)
            && Self::is_zero_or_missing(self.total)
            && Self::is_zero_or_missing(self.cached_input)
            && Self::is_zero_or_missing(self.reasoning_output)
            && Self::is_zero_or_missing(self.cache_creation_input_tokens)
            && Self::is_zero_or_missing(self.cache_read_input_tokens)
    }

    pub(super) fn from_usage_value(value: &Value) -> Self {
        Self {
            input: value.get("input_tokens").and_then(|v| v.as_i64()),
            output: value.get("output_tokens").and_then(|v| v.as_i64()),
            total: value.get("total_tokens").and_then(|v| v.as_i64()),
            cached_input: value.get("cached_input_tokens").and_then(|v| v.as_i64()),
            reasoning_output: value
                .get("reasoning_output_tokens")
                .and_then(|v| v.as_i64()),
            cache_creation_input_tokens: value
                .get("cache_creation_input_tokens")
                .or_else(|| value.get("cache_write_input_tokens"))
                .and_then(|v| v.as_i64()),
            cache_read_input_tokens: value
                .get("cache_read_input_tokens")
                .and_then(|v| v.as_i64()),
            response_id: None,
            source: None,
        }
    }

    pub(super) fn with_identity(
        mut self,
        response_id: Option<String>,
        source: TokenUsageSource,
    ) -> Self {
        self.response_id = response_id.filter(|value| !value.is_empty());
        self.source = Some(source);
        self
    }

    pub fn is_native_record(&self) -> bool {
        self.source == Some(TokenUsageSource::NativeRecord)
    }

    pub(super) fn delta(start: &Self, end: &Self) -> Option<Self> {
        let usage = Self {
            input: subtract_optional(end.input, start.input),
            output: subtract_optional(end.output, start.output),
            total: subtract_optional(end.total, start.total),
            cached_input: subtract_optional(end.cached_input, start.cached_input),
            reasoning_output: subtract_optional(end.reasoning_output, start.reasoning_output),
            cache_creation_input_tokens: subtract_optional(
                end.cache_creation_input_tokens,
                start.cache_creation_input_tokens,
            ),
            cache_read_input_tokens: subtract_optional(
                end.cache_read_input_tokens,
                start.cache_read_input_tokens,
            ),
            response_id: None,
            source: None,
        };
        (usage.has_any() && !usage.all_zero()).then_some(usage)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct CompactionUsageDisposition {
    pub post_tokens: Option<u64>,
    pub metadata_only: bool,
}

pub(super) fn classify_compaction_usage(usage: &TokenUsage) -> CompactionUsageDisposition {
    match usage.input {
        Some(input) if input > 0 => CompactionUsageDisposition {
            post_tokens: Some(input as u64),
            metadata_only: false,
        },
        Some(0) => CompactionUsageDisposition {
            post_tokens: usage
                .total
                .filter(|total| *total > 0)
                .map(|total| total as u64),
            metadata_only: true,
        },
        _ => CompactionUsageDisposition {
            post_tokens: None,
            metadata_only: false,
        },
    }
}

pub(super) fn subtract_optional(end: Option<i64>, start: Option<i64>) -> Option<i64> {
    match (end, start) {
        (Some(end), Some(start)) => Some((end - start).max(0)),
        (Some(end), None) => Some(end.max(0)),
        _ => None,
    }
}

/// A compaction (context compression) event parsed from the Codex transcript.
///
/// Codex emits two entries when compaction occurs:
///   1. `type: "compacted"` (top-level) — contains `replacement_history`
///   2. `event_msg.type: "context_compacted"` — simple marker
///
/// Token counts are inferred from surrounding `token_count` events:
///   - `pre_tokens`:  `last_token_usage.input_tokens` from the last token_count BEFORE compaction
///   - `post_tokens`: `last_token_usage.input_tokens` from the first token_count AFTER compaction
#[derive(Debug, Clone, PartialEq)]
pub struct CompactionEvent {
    pub timestamp: String,        // timestamp of the "compacted" event
    pub end_timestamp: String,    // timestamp of the first token_count after compaction
    pub pre_tokens: Option<u64>,  // trusted context tokens before compaction
    pub post_tokens: Option<u64>, // trusted context tokens after compaction (backfilled)
    pub sequence: u32,            // nth compaction in session (1-based)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TerminalError {
    pub error_type: String,
    pub message: String,
    pub status_code: Option<i64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AssistantMessage {
    pub timestamp: String,
    pub text: String,
    pub media_content: Value,
    pub phase: String,
    pub usage: TokenUsage,
    /// Timestamp of the `token_count` event that marks this LLM call's completion.
    /// Used as the precise LLM call boundary for generation span timing.
    pub token_count_timestamp: String,
    /// Earliest explicit model-origin event for this invocation.
    /// Empty on legacy compatibility records that have not been normalized.
    pub invocation_started_timestamp: String,
    /// Origin of the normalized end boundary.
    pub invocation_end_source: Option<InvocationEndSource>,
}

impl AssistantMessage {
    pub fn is_pending_generation(&self) -> bool {
        self.invocation_end_source == Some(InvocationEndSource::NativeUsagePending)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct ToolCall {
    pub call_id: String,
    pub name: String,
    pub input: Value,
    pub output: Value,
    pub started_timestamp: String,
    pub ended_timestamp: String,
    /// Index into `Turn::assistants` indicating which generation phase this tool belongs to.
    pub assistant_index: usize,
    /// Whether the tool call succeeded. None means unknown (legacy/default).
    pub success: Option<bool>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Turn {
    /// Native prompt/chat identifier from the transcript when available.
    /// Falls back to the trace id at OTel assembly time when absent.
    pub chat_id: Option<String>,
    pub turn_id: String,
    pub user_timestamp: String,
    pub user_text: String,
    pub user_content: Value,
    pub assistants: Vec<AssistantMessage>,
    pub tool_calls: Vec<ToolCall>,
    pub model: String,
    pub cwd: String,
    /// Session-level Codex instructions captured from
    /// `session_meta.payload.base_instructions.text`.
    pub system_instructions: String,
    pub completed: bool,
    pub completion_timestamp: String,
    pub terminal_state: TurnTerminalState,
    pub terminal_error: Option<TerminalError>,
    pub trigger: TurnTrigger,
    pub skill_invocations: Vec<SkillInvocation>,
    /// Legacy compatibility hint derived from markdown or command paths.
    /// Structured Codex v2 invocations live in `skill_invocations`.
    pub skill_name: Option<String>,
    pub compactions: Vec<CompactionEvent>,
    /// CollabAgent protocol events observed in this turn.
    pub collab_agent_events: Vec<CollabAgentEvent>,
    /// If this session is a sub-agent, its origin info from session_meta.
    pub subagent_source: Option<SubagentSourceInfo>,
}

impl Turn {
    pub fn has_semantic_assistant_at(&self, assistant_index: usize) -> bool {
        self.assistants
            .get(assistant_index)
            .is_some_and(|assistant| {
                !assistant.is_pending_generation()
                    && (!assistant.text.is_empty()
                        || assistant.usage.has_any()
                        || self
                            .tool_calls
                            .iter()
                            .any(|tool| tool.assistant_index == assistant_index))
            })
    }

    pub fn semantic_assistant_count(&self) -> usize {
        self.assistants
            .iter()
            .enumerate()
            .filter(|(index, _)| self.has_semantic_assistant_at(*index))
            .count()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compaction_usage_classification_is_shared_and_conservative() {
        let cases = [
            (
                TokenUsage {
                    input: Some(42),
                    total: Some(50),
                    ..TokenUsage::default()
                },
                Some(42),
                false,
            ),
            (
                TokenUsage {
                    input: Some(0),
                    total: Some(5552),
                    ..TokenUsage::default()
                },
                Some(5552),
                true,
            ),
            (
                TokenUsage {
                    input: Some(0),
                    total: Some(0),
                    ..TokenUsage::default()
                },
                None,
                true,
            ),
            (
                TokenUsage {
                    input: None,
                    output: Some(3),
                    total: Some(3),
                    ..TokenUsage::default()
                },
                None,
                false,
            ),
        ];

        for (usage, expected_post, expected_metadata_only) in cases {
            let disposition = classify_compaction_usage(&usage);
            assert_eq!(disposition.post_tokens, expected_post);
            assert_eq!(disposition.metadata_only, expected_metadata_only);
        }
    }
}
