//! Convert cdxhooks transcript data structures to OpenAI API message format.
//!
//! Bridges `Turn` / `AssistantMessage` / `ToolCall` from `transcript.rs` into
//! the standard `OpenAIMessage` / `ToolCall` types from `agentic-otel-common`,
//! enabling unified `gen_ai.input.messages` and `gen_ai.output.messages`
//! attributes across all agent hooks.

use agentic_otel_common::message::{OpenAIMessage, ToolCall as OtelToolCall};

use crate::common::payload_budget::{filter_inline_base64_images, InlineImageFilterStats};
use crate::common::transcript::{AssistantMessage, Turn};

// ── Input messages ─────────────────────────────────────────────────

/// Build `gen_ai.input.messages` for the Turn root span.
///
/// Per C9 原则 2, the Turn root span only carries the user-facing view:
/// what the user asked. Intermediate assistant/tool exchanges belong to
/// generation spans (see `build_generation_accumulated_input`).
pub fn build_input_messages(turn: &Turn) -> Vec<OpenAIMessage> {
    vec![OpenAIMessage::user(&turn.user_text).with_content(&turn.user_content)]
}

// ── Output messages ────────────────────────────────────────────────

/// Build `gen_ai.output.messages` from a Turn.
///
/// Takes the last non-empty `AssistantMessage` and converts it to a
/// single-element array. Trailing usage/tool placeholders remain available
/// to generation spans but must not replace the user-facing terminal output.
/// `finish_reason` is inferred from tool calls belonging to the selected
/// assistant index.
pub fn build_output_messages(turn: &Turn) -> Vec<OpenAIMessage> {
    build_output_messages_with_stats(turn).0
}

pub fn has_output_messages(turn: &Turn) -> bool {
    turn.assistants
        .iter()
        .enumerate()
        .rev()
        .any(|(index, assistant)| {
            !assistant.is_pending_generation()
                && ((!assistant.text.trim().is_empty() || agentic_otel_common::media::content_blocks(&assistant.media_content).is_some())
                    || turn
                        .tool_calls
                        .iter()
                        .any(|tool| tool.assistant_index == index))
        })
}

pub(crate) fn build_output_messages_with_stats(
    turn: &Turn,
) -> (Vec<OpenAIMessage>, InlineImageFilterStats) {
    let last_nonempty = turn
        .assistants
        .iter()
        .enumerate()
        .rev()
        .find(|(_, assistant)| !assistant.text.trim().is_empty() || agentic_otel_common::media::content_blocks(&assistant.media_content).is_some());
    let tool_only = || {
        turn.assistants.iter().enumerate().rev().find(|(index, _)| {
            !turn.assistants[*index].is_pending_generation()
                && turn
                    .tool_calls
                    .iter()
                    .any(|tool| tool.assistant_index == *index)
        })
    };
    let Some((last_idx, assistant)) = last_nonempty.or_else(tool_only) else {
        return (Vec::new(), InlineImageFilterStats::default());
    };

    let (mut msg, stats) = assistant_to_openai_message_with_stats(assistant, last_idx, turn);

    // Infer finish_reason from whether the last assistant triggered tool calls
    let has_tools = turn
        .tool_calls
        .iter()
        .any(|tc| tc.assistant_index == last_idx);
    msg.finish_reason = Some(if has_tools { "tool_calls" } else { "stop" }.to_string());

    (vec![msg], stats)
}

// ── Helpers ────────────────────────────────────────────────────────

/// Convert an AssistantMessage to an OpenAIMessage, attaching any tool_calls
/// that belong to this assistant_index.
fn assistant_to_openai_message_with_stats(
    assistant: &AssistantMessage,
    assistant_index: usize,
    turn: &Turn,
) -> (OpenAIMessage, InlineImageFilterStats) {
    let mut stats = InlineImageFilterStats::default();
    let tools_for_assistant: Vec<OtelToolCall> = turn
        .tool_calls
        .iter()
        .filter(|tc| tc.assistant_index == assistant_index)
        .map(|tc| {
            let (filtered_input, item_stats) = filter_inline_base64_images(&tc.input);
            stats.add(item_stats);
            OtelToolCall::function_from_value(&tc.call_id, &tc.name, &filtered_input)
        })
        .collect();

    if tools_for_assistant.is_empty() {
        (OpenAIMessage::assistant(&assistant.text).with_content(&assistant.media_content), stats)
    } else {
        let content = if assistant.text.is_empty() {
            None
        } else {
            Some(assistant.text.as_str())
        };
        (
            OpenAIMessage::assistant_with_tool_calls(content, tools_for_assistant).with_content(&assistant.media_content),
            stats,
        )
    }
}

/// Build accumulated input messages for a specific generation span.
///
/// Returns the full conversation context up to (but not including) the given
/// assistant_index: user message, then for each prior assistant its response
/// (with tool_calls) and corresponding tool results.
pub fn build_generation_accumulated_input(
    turn: &Turn,
    assistant_index: usize,
) -> Vec<OpenAIMessage> {
    build_generation_accumulated_input_with_stats(turn, assistant_index).0
}

pub(crate) fn build_generation_accumulated_input_with_stats(
    turn: &Turn,
    assistant_index: usize,
) -> (Vec<OpenAIMessage>, InlineImageFilterStats) {
    let mut messages = Vec::new();
    let mut stats = InlineImageFilterStats::default();
    messages.push(OpenAIMessage::user(&turn.user_text).with_content(&turn.user_content));

    if assistant_index > 0 {
        for idx in 0..assistant_index {
            let prior = &turn.assistants[idx];
            let (message, message_stats) = assistant_to_openai_message_with_stats(prior, idx, turn);
            stats.add(message_stats);
            messages.push(message);

            // Tool results belonging to this assistant
            for tc in turn
                .tool_calls
                .iter()
                .filter(|tc| tc.assistant_index == idx)
            {
                let (result_text, result_stats) = tool_output_to_string(&tc.output);
                stats.add(result_stats);
                messages.push(OpenAIMessage::tool_result(&tc.call_id, &result_text).with_content(&tc.output));
            }
        }
    }

    (messages, stats)
}

/// Build delta input messages for a specific generation span.
///
/// For the first generation (index 0), returns just the user message.
/// For subsequent generations, returns only the immediately preceding
/// assistant response and its tool results — the incremental context
/// since the last LLM call.
pub fn build_generation_delta_input(turn: &Turn, assistant_index: usize) -> Vec<OpenAIMessage> {
    build_generation_delta_input_with_stats(turn, assistant_index).0
}

pub(crate) fn build_generation_delta_input_with_stats(
    turn: &Turn,
    assistant_index: usize,
) -> (Vec<OpenAIMessage>, InlineImageFilterStats) {
    if assistant_index == 0 {
        return (
            vec![OpenAIMessage::user(&turn.user_text).with_content(&turn.user_content)],
            InlineImageFilterStats::default(),
        );
    }

    let mut messages = Vec::new();
    let mut stats = InlineImageFilterStats::default();
    let prior_idx = assistant_index - 1;
    let prior = &turn.assistants[prior_idx];
    let (message, message_stats) = assistant_to_openai_message_with_stats(prior, prior_idx, turn);
    stats.add(message_stats);
    messages.push(message);

    for tc in turn
        .tool_calls
        .iter()
        .filter(|tc| tc.assistant_index == prior_idx)
    {
        let (result_text, result_stats) = tool_output_to_string(&tc.output);
        stats.add(result_stats);
        messages.push(OpenAIMessage::tool_result(&tc.call_id, &result_text).with_content(&tc.output));
    }

    (messages, stats)
}

/// Build an OpenAIMessage for a single generation span output (public for otel.rs).
pub fn build_generation_output_message(
    assistant: &AssistantMessage,
    assistant_index: usize,
    turn: &Turn,
) -> OpenAIMessage {
    build_generation_output_message_with_stats(assistant, assistant_index, turn).0
}

pub(crate) fn build_generation_output_message_with_stats(
    assistant: &AssistantMessage,
    assistant_index: usize,
    turn: &Turn,
) -> (OpenAIMessage, InlineImageFilterStats) {
    let (mut msg, stats) = assistant_to_openai_message_with_stats(assistant, assistant_index, turn);
    let has_tools = turn
        .tool_calls
        .iter()
        .any(|tc| tc.assistant_index == assistant_index);
    msg.finish_reason = Some(if has_tools { "tool_calls" } else { "stop" }.to_string());
    (msg, stats)
}

/// Convert tool output (Value) to a string for the tool message.
fn tool_output_to_string(output: &serde_json::Value) -> (String, InlineImageFilterStats) {
    let (filtered_output, stats) = filter_inline_base64_images(output);
    let rendered = match filtered_output.as_ref() {
        serde_json::Value::String(s) => s.clone(),
        serde_json::Value::Null => String::new(),
        other => serde_json::to_string(other).unwrap_or_default(),
    };
    (rendered, stats)
}

// ── Tests ──────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::transcript::{TokenUsage, ToolCall, TurnTerminalState};
    use serde_json::json;

    fn make_usage() -> TokenUsage {
        TokenUsage {
            input: Some(100),
            output: Some(50),
            ..TokenUsage::default()
        }
    }

    #[test]
    fn test_simple_turn_input_output() {
        let turn = Turn {
            chat_id: None,
            turn_id: "turn-1".into(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Hello".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: "2026-04-10T10:00:01.000Z".into(),
                text: "Hi there!".into(),
                media_content: serde_json::Value::Null,
                phase: "final_answer".into(),
                usage: make_usage(),
                token_count_timestamp: String::new(),
                invocation_started_timestamp: String::new(),
                invocation_end_source: None,
            }],
            tool_calls: vec![],
            model: "gpt-5.4".into(),
            cwd: "/tmp".into(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: String::new(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events: vec![],
            subagent_source: None,
        };

        let input = build_input_messages(&turn);
        assert_eq!(input.len(), 1);
        assert_eq!(input[0].role, "user");
        assert_eq!(input[0].content.as_deref(), Some("Hello"));

        let output = build_output_messages(&turn);
        assert_eq!(output.len(), 1);
        assert_eq!(output[0].role, "assistant");
        assert_eq!(output[0].content.as_deref(), Some("Hi there!"));
        assert_eq!(output[0].finish_reason.as_deref(), Some("stop"));
        assert!(output[0].tool_calls.is_none());
    }

    #[test]
    fn test_turn_with_tool_calls() {
        let turn = Turn {
            chat_id: None,
            turn_id: "turn-1".into(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Read file test.txt".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![
                AssistantMessage {
                    timestamp: "2026-04-10T10:00:01.000Z".into(),
                    text: "Let me read that file.".into(),
                    media_content: serde_json::Value::Null,
                    phase: "commentary".into(),
                    usage: TokenUsage::default(),
                    token_count_timestamp: String::new(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
                AssistantMessage {
                    timestamp: "2026-04-10T10:00:03.000Z".into(),
                    text: "The file contains: hello world".into(),
                    media_content: serde_json::Value::Null,
                    phase: "final_answer".into(),
                    usage: make_usage(),
                    token_count_timestamp: String::new(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
            ],
            tool_calls: vec![ToolCall {
                call_id: "call-1".into(),
                name: "Bash".into(),
                input: json!({"command": "cat /tmp/test.txt"}),
                output: json!("hello world"),
                started_timestamp: "2026-04-10T10:00:01.500Z".into(),
                ended_timestamp: "2026-04-10T10:00:02.000Z".into(),
                assistant_index: 0,
                success: None,
            }],
            model: "gpt-5.4".into(),
            cwd: "/tmp".into(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: String::new(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events: vec![],
            subagent_source: None,
        };

        let input = build_input_messages(&turn);
        // Root span input: user message only (C9 原则 2)
        assert_eq!(input.len(), 1);
        assert_eq!(input[0].role, "user");
        assert_eq!(input[0].content.as_deref(), Some("Read file test.txt"));

        let output = build_output_messages(&turn);
        assert_eq!(output.len(), 1);
        assert_eq!(output[0].role, "assistant");
        assert_eq!(
            output[0].content.as_deref(),
            Some("The file contains: hello world")
        );
        assert_eq!(output[0].finish_reason.as_deref(), Some("stop"));
    }

    #[test]
    fn test_output_with_tool_calls_in_last_assistant() {
        // When the last assistant has tool_calls, finish_reason = "tool_calls"
        let turn = Turn {
            chat_id: None,
            turn_id: "turn-1".into(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Search for files".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: "2026-04-10T10:00:01.000Z".into(),
                text: "".into(),
                media_content: serde_json::Value::Null,
                phase: "commentary".into(),
                usage: TokenUsage::default(),
                token_count_timestamp: String::new(),
                invocation_started_timestamp: String::new(),
                invocation_end_source: None,
            }],
            tool_calls: vec![ToolCall {
                call_id: "call-1".into(),
                name: "Bash".into(),
                input: json!({"command": "find . -name '*.rs'"}),
                output: json!("src/main.rs"),
                started_timestamp: "2026-04-10T10:00:01.500Z".into(),
                ended_timestamp: "2026-04-10T10:00:02.000Z".into(),
                assistant_index: 0,
                success: None,
            }],
            model: "gpt-5.4".into(),
            cwd: "/tmp".into(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: String::new(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events: vec![],
            subagent_source: None,
        };

        let output = build_output_messages(&turn);
        assert_eq!(output.len(), 1);
        assert_eq!(output[0].role, "assistant");
        // Empty text → content should be None
        assert!(output[0].content.is_none());
        assert!(output[0].tool_calls.is_some());
        assert_eq!(output[0].finish_reason.as_deref(), Some("tool_calls"));
    }

    #[test]
    fn test_empty_turn() {
        let turn = Turn {
            chat_id: None,
            turn_id: "turn-1".into(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Hello".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![],
            tool_calls: vec![],
            model: "gpt-5.4".into(),
            cwd: "/tmp".into(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: String::new(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events: vec![],
            subagent_source: None,
        };

        let input = build_input_messages(&turn);
        assert_eq!(input.len(), 1); // just the user message

        let output = build_output_messages(&turn);
        assert!(output.is_empty());
    }

    #[test]
    fn test_root_output_uses_last_nonempty_assistant_message() {
        let mut turn = Turn {
            chat_id: None,
            turn_id: "turn-last-output".into(),
            user_timestamp: "2026-08-15T07:58:20.020Z".into(),
            user_text: "run child".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![
                AssistantMessage {
                    timestamp: "2026-08-15T07:58:35.000Z".into(),
                    text: "final answer".into(),
                    media_content: serde_json::Value::Null,
                    phase: "final_answer".into(),
                    usage: make_usage(),
                    token_count_timestamp: String::new(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
                AssistantMessage {
                    timestamp: "2026-08-15T07:58:35.050Z".into(),
                    text: String::new(),
                    media_content: serde_json::Value::Null,
                    phase: "assistant".into(),
                    usage: make_usage(),
                    token_count_timestamp: String::new(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
            ],
            tool_calls: vec![],
            model: "gpt-5.5".into(),
            cwd: "/workspace".into(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: "2026-08-15T07:58:35.100Z".into(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events: vec![],
            subagent_source: None,
        };
        turn.tool_calls.push(ToolCall {
            call_id: "trailing-call".into(),
            name: "noop".into(),
            input: json!({}),
            output: json!(null),
            started_timestamp: String::new(),
            ended_timestamp: String::new(),
            assistant_index: 1,
            success: Some(true),
        });

        let semantic_count = turn.semantic_assistant_count();
        let output = build_output_messages(&turn);
        assert_eq!(output.len(), 1);
        assert_eq!(output[0].content.as_deref(), Some("final answer"));
        assert_eq!(output[0].finish_reason.as_deref(), Some("stop"));
        assert!(output[0].tool_calls.is_none());
        assert_eq!(turn.semantic_assistant_count(), semantic_count);
        assert_eq!(semantic_count, 2);
    }

    #[test]
    fn test_serialization_roundtrip() {
        let turn = Turn {
            chat_id: None,
            turn_id: "turn-1".into(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Weather in Paris?".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: "2026-04-10T10:00:01.000Z".into(),
                text: "It's rainy.".into(),
                media_content: serde_json::Value::Null,
                phase: "final_answer".into(),
                usage: make_usage(),
                token_count_timestamp: String::new(),
                invocation_started_timestamp: String::new(),
                invocation_end_source: None,
            }],
            tool_calls: vec![],
            model: "gpt-5.4".into(),
            cwd: "/tmp".into(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: String::new(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events: vec![],
            subagent_source: None,
        };

        let input = build_input_messages(&turn);
        let json_str = agentic_otel_common::message::serialize_messages_default(&input, 0);
        let parsed: Vec<serde_json::Value> = serde_json::from_str(&json_str).unwrap();
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0]["role"], "user");
        assert_eq!(parsed[0]["content"], "Weather in Paris?");
    }

    #[test]
    fn test_multi_generation_tool_grouping() {
        // Two intermediate assistants, each with their own tool_calls
        let turn = Turn {
            chat_id: None,
            turn_id: "turn-1".into(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Do two things".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![
                AssistantMessage {
                    timestamp: "2026-04-10T10:00:01.000Z".into(),
                    text: "Step one".into(),
                    media_content: serde_json::Value::Null,
                    phase: "commentary".into(),
                    usage: TokenUsage::default(),
                    token_count_timestamp: String::new(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
                AssistantMessage {
                    timestamp: "2026-04-10T10:00:03.000Z".into(),
                    text: "Step two".into(),
                    media_content: serde_json::Value::Null,
                    phase: "commentary".into(),
                    usage: TokenUsage::default(),
                    token_count_timestamp: String::new(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
                AssistantMessage {
                    timestamp: "2026-04-10T10:00:05.000Z".into(),
                    text: "All done".into(),
                    media_content: serde_json::Value::Null,
                    phase: "final_answer".into(),
                    usage: make_usage(),
                    token_count_timestamp: String::new(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
            ],
            tool_calls: vec![
                ToolCall {
                    call_id: "call-a".into(),
                    name: "Bash".into(),
                    input: json!({"command": "pwd"}),
                    output: json!("/tmp"),
                    started_timestamp: "2026-04-10T10:00:01.500Z".into(),
                    ended_timestamp: "2026-04-10T10:00:02.000Z".into(),
                    assistant_index: 0,
                    success: None,
                },
                ToolCall {
                    call_id: "call-b".into(),
                    name: "Bash".into(),
                    input: json!({"command": "ls"}),
                    output: json!("file.txt"),
                    started_timestamp: "2026-04-10T10:00:03.500Z".into(),
                    ended_timestamp: "2026-04-10T10:00:04.000Z".into(),
                    assistant_index: 1,
                    success: None,
                },
            ],
            model: "gpt-5.4".into(),
            cwd: "/tmp".into(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: String::new(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events: vec![],
            subagent_source: None,
        };

        let input = build_input_messages(&turn);
        // Root span input: user message only (C9 原则 2)
        assert_eq!(input.len(), 1);
        assert_eq!(input[0].role, "user");
        assert_eq!(input[0].content.as_deref(), Some("Do two things"));
    }

    // ── Accumulated / Delta tests ─────────────────────────────────

    fn make_multi_gen_turn() -> Turn {
        Turn {
            chat_id: None,
            turn_id: "turn-1".into(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Do two things".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![
                AssistantMessage {
                    timestamp: "2026-04-10T10:00:01.000Z".into(),
                    text: "Step one".into(),
                    media_content: serde_json::Value::Null,
                    phase: "commentary".into(),
                    usage: TokenUsage::default(),
                    token_count_timestamp: String::new(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
                AssistantMessage {
                    timestamp: "2026-04-10T10:00:03.000Z".into(),
                    text: "Step two".into(),
                    media_content: serde_json::Value::Null,
                    phase: "commentary".into(),
                    usage: TokenUsage::default(),
                    token_count_timestamp: String::new(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
                AssistantMessage {
                    timestamp: "2026-04-10T10:00:05.000Z".into(),
                    text: "All done".into(),
                    media_content: serde_json::Value::Null,
                    phase: "final_answer".into(),
                    usage: make_usage(),
                    token_count_timestamp: String::new(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
            ],
            tool_calls: vec![
                ToolCall {
                    call_id: "call-a".into(),
                    name: "Bash".into(),
                    input: json!({"command": "pwd"}),
                    output: json!("/tmp"),
                    started_timestamp: "2026-04-10T10:00:01.500Z".into(),
                    ended_timestamp: "2026-04-10T10:00:02.000Z".into(),
                    assistant_index: 0,
                    success: None,
                },
                ToolCall {
                    call_id: "call-b".into(),
                    name: "Bash".into(),
                    input: json!({"command": "ls"}),
                    output: json!("file.txt"),
                    started_timestamp: "2026-04-10T10:00:03.500Z".into(),
                    ended_timestamp: "2026-04-10T10:00:04.000Z".into(),
                    assistant_index: 1,
                    success: None,
                },
            ],
            model: "gpt-5.4".into(),
            cwd: "/tmp".into(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: String::new(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events: vec![],
            subagent_source: None,
        }
    }

    #[test]
    fn test_accumulated_input_first_generation() {
        let turn = make_multi_gen_turn();
        let acc = build_generation_accumulated_input(&turn, 0);
        // First generation: just user message
        assert_eq!(acc.len(), 1);
        assert_eq!(acc[0].role, "user");
    }

    #[test]
    fn test_accumulated_input_second_generation() {
        let turn = make_multi_gen_turn();
        let acc = build_generation_accumulated_input(&turn, 1);
        // user + assistant[0](with tool_calls) + tool_result = 3
        assert_eq!(acc.len(), 3);
        assert_eq!(acc[0].role, "user");
        assert_eq!(acc[1].role, "assistant");
        assert!(acc[1].tool_calls.is_some());
        assert_eq!(acc[2].role, "tool");
        assert_eq!(acc[2].tool_call_id.as_deref(), Some("call-a"));
    }

    #[test]
    fn test_accumulated_input_third_generation() {
        let turn = make_multi_gen_turn();
        let acc = build_generation_accumulated_input(&turn, 2);
        // user + assistant[0] + tool[call-a] + assistant[1] + tool[call-b] = 5
        assert_eq!(acc.len(), 5);
        assert_eq!(acc[0].role, "user");
        assert_eq!(acc[1].role, "assistant");
        assert_eq!(acc[2].role, "tool");
        assert_eq!(acc[2].tool_call_id.as_deref(), Some("call-a"));
        assert_eq!(acc[3].role, "assistant");
        assert_eq!(acc[4].role, "tool");
        assert_eq!(acc[4].tool_call_id.as_deref(), Some("call-b"));
    }

    #[test]
    fn test_delta_input_first_generation() {
        let turn = make_multi_gen_turn();
        let delta = build_generation_delta_input(&turn, 0);
        // First generation delta = user message only
        assert_eq!(delta.len(), 1);
        assert_eq!(delta[0].role, "user");
    }

    #[test]
    fn test_delta_input_second_generation() {
        let turn = make_multi_gen_turn();
        let delta = build_generation_delta_input(&turn, 1);
        // Delta: previous assistant[0] + its tool result = 2
        assert_eq!(delta.len(), 2);
        assert_eq!(delta[0].role, "assistant");
        assert!(delta[0].tool_calls.is_some());
        assert_eq!(delta[1].role, "tool");
        assert_eq!(delta[1].tool_call_id.as_deref(), Some("call-a"));
    }

    #[test]
    fn test_delta_input_third_generation() {
        let turn = make_multi_gen_turn();
        let delta = build_generation_delta_input(&turn, 2);
        // Delta: previous assistant[1] + its tool result = 2
        assert_eq!(delta.len(), 2);
        assert_eq!(delta[0].role, "assistant");
        assert!(delta[0].tool_calls.is_some());
        assert_eq!(delta[1].role, "tool");
        assert_eq!(delta[1].tool_call_id.as_deref(), Some("call-b"));
    }

    #[test]
    fn test_accumulated_equals_delta_for_first_generation() {
        let turn = make_multi_gen_turn();
        let acc = build_generation_accumulated_input(&turn, 0);
        let delta = build_generation_delta_input(&turn, 0);
        assert_eq!(acc.len(), delta.len());
        assert_eq!(acc[0].role, delta[0].role);
        assert_eq!(acc[0].content, delta[0].content);
    }
}

#[cfg(test)]
mod media_contract_tests {
    use super::*;
    use crate::common::transcript::build_turns;
    use serde_json::json;
    #[test]
    fn real_transcript_shapes_keep_typed_user_media() {
        for mime in ["image/avif", "audio/ogg", "video/mp4", "application/pdf"] {
            let media = json!({"type":"media","source":{"type":"base64","media_type":mime,"data":"YWJj"}});
            let lines=vec![
                json!({"timestamp":"2026-11-13T10:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"t"}}),
                json!({"timestamp":"2026-11-13T10:00:00.500Z","type":"turn_context","payload":{"turn_id":"t","model":"test"}}),
                json!({"timestamp":"2026-11-13T10:00:01Z","type":"event_msg","payload":{"type":"user_message","message":"inspect"}}),
                json!({"timestamp":"2026-11-13T10:00:02Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"inspect"},media]}}),
                json!({"timestamp":"2026-11-13T10:00:02.500Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"},media.clone()]}}),
                json!({"timestamp":"2026-11-13T10:00:03Z","type":"event_msg","payload":{"type":"agent_message","message":"ok"}}),
                json!({"timestamp":"2026-11-13T10:00:04Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"t"}}),
            ];
            let turns=build_turns(&lines);
            assert_eq!(turns.len(),1);
            assert!(!turns[0].user_content.is_null(), "native turn id={} media was dropped", turns[0].turn_id);
            let value=serde_json::to_value(build_input_messages(&turns[0])).unwrap();
            assert_eq!(value[0]["content"][1]["source"]["media_type"],mime);
            assert_eq!(value[0]["content"][1]["source"]["data"],"YWJj");
            let output=serde_json::to_value(build_output_messages(&turns[0])).unwrap();
            assert_eq!(output[0]["content"][1]["source"]["media_type"],mime);
            assert_eq!(output[0]["content"][1]["source"]["data"],"YWJj");
        }
    }
}
