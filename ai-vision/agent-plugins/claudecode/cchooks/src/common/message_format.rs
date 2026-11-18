//! Convert cchooks transcript data structures to OpenAI API message format.
//!
//! Bridges `Turn` / `AssistantMessage` / `ToolUse` / `ToolResultItem` from
//! `transcript.rs` into the standard `OpenAIMessage` / `ToolCall` types from
//! `agentic-otel-common`, enabling unified `gen_ai.input.messages` and
//! `gen_ai.output.messages` attributes across all agent hooks.

use agentic_otel_common::message::{OpenAIMessage, ToolCall};

use crate::common::transcript::{AssistantMessage, ToolResultItem, ToolUse, Turn};

// ── Input messages ─────────────────────────────────────────────────

/// Build `gen_ai.input.messages` from a Turn.
///
/// Produces the conversation context for this turn's root span. Intermediate
/// assistant/tool exchanges come first, with the user message at the END so
/// that Langfuse (which picks the last message) correctly identifies it as
/// the "Input".
pub fn build_input_messages(turn: &Turn) -> Vec<OpenAIMessage> {
    // Turn root span only carries the user-facing view: what the user asked.
    // Intermediate assistant/tool exchanges belong to generation spans (see C9 原则 2).
    vec![OpenAIMessage::user(&turn.user_text).with_content(&turn.user_content)]
}

// ── Per-generation input messages ──────────────────────────────────

/// Build accumulated `gen_ai.input.messages` for a specific generation span.
///
/// For assistant at index `i`, includes the user message first (natural order),
/// then all prior assistant/tool exchanges. This reflects the actual LLM input
/// context: user prompt followed by tool interaction history.
///
/// Turn root span only carries `[user]` (see `build_input_messages`);
/// generation spans carry the full accumulated context per LLM call.
pub fn build_generation_accumulated_input(
    turn: &Turn,
    assistant_index: usize,
) -> Vec<OpenAIMessage> {
    let mut messages = Vec::new();

    // 1. User message first (natural chronological order)
    messages.push(OpenAIMessage::user(&turn.user_text).with_content(&turn.user_content));

    // 2. All prior assistant + tool_result exchanges (before this assistant)
    if assistant_index > 0 {
        let all_results: Vec<&ToolResultItem> = turn
            .tool_results
            .iter()
            .flat_map(|tr| tr.items.iter())
            .collect();

        for prior in &turn.assistants[..assistant_index] {
            messages.push(assistant_to_openai_message(prior));

            for tu in &prior.tool_uses {
                if let Some(result) = all_results.iter().find(|r| r.tool_use_id == tu.id) {
                    let content = tool_result_content_to_string(&result.content);
                    messages.push(OpenAIMessage::tool_result(&tu.id, &content).with_content(&result.content));
                }
            }
        }
    }

    messages
}

/// Build delta input for a specific generation span.
///
/// For the first generation (index 0): returns `[user_message]`
/// For subsequent generations: returns the immediately preceding assistant
/// response + its tool results (i.e., the new context since the last call).
pub fn build_generation_delta_input(turn: &Turn, assistant_index: usize) -> Vec<OpenAIMessage> {
    if assistant_index == 0 {
        return vec![OpenAIMessage::user(&turn.user_text).with_content(&turn.user_content)];
    }

    let mut messages = Vec::new();
    let prior = &turn.assistants[assistant_index - 1];

    messages.push(assistant_to_openai_message(prior));

    let all_results: Vec<&ToolResultItem> = turn
        .tool_results
        .iter()
        .flat_map(|tr| tr.items.iter())
        .collect();

    for tu in &prior.tool_uses {
        if let Some(result) = all_results.iter().find(|r| r.tool_use_id == tu.id) {
            let content = tool_result_content_to_string(&result.content);
            messages.push(OpenAIMessage::tool_result(&tu.id, &content).with_content(&result.content));
        }
    }

    messages
}

// ── Output messages ────────────────────────────────────────────────

/// Build `gen_ai.output.messages` from a Turn.
///
/// Takes the last `AssistantMessage` and converts it to a single-element
/// array with `finish_reason` set from `stop_reason`.
pub fn build_output_messages(turn: &Turn) -> Vec<OpenAIMessage> {
    let Some(assistant) = turn.assistants.last() else {
        return Vec::new();
    };

    let mut msg = assistant_to_openai_message(assistant);

    // Set finish_reason from stop_reason
    if !assistant.stop_reason.is_empty() {
        msg.finish_reason = Some(assistant.stop_reason.clone());
    } else {
        msg.finish_reason = Some("stop".to_string());
    }

    vec![msg]
}

// ── Helpers ────────────────────────────────────────────────────────

/// Convert an AssistantMessage to an OpenAIMessage (public for use in otel.rs).
pub fn assistant_to_openai_message_pub(assistant: &AssistantMessage) -> OpenAIMessage {
    assistant_to_openai_message(assistant)
}

/// Convert an AssistantMessage to an OpenAIMessage.
///
/// If the assistant has tool_uses, they become `tool_calls`.
/// If the text is empty and there are tool_calls, content is set to None.
fn assistant_to_openai_message(assistant: &AssistantMessage) -> OpenAIMessage {
    let mut msg = if assistant.tool_uses.is_empty() {
        OpenAIMessage::assistant(&assistant.text)
    } else {
        let tool_calls: Vec<ToolCall> = assistant.tool_uses.iter().map(tool_use_to_call).collect();
        let content = if assistant.text.is_empty() {
            None
        } else {
            Some(assistant.text.as_str())
        };
        OpenAIMessage::assistant_with_tool_calls(content, tool_calls)
    };

    // Set reasoning_content from thinking blocks
    if !assistant.thinking_blocks.is_empty() {
        let texts: Vec<&str> = assistant
            .thinking_blocks
            .iter()
            .filter_map(|b| b.get("content").and_then(|v| v.as_str()))
            .collect();
        if !texts.is_empty() {
            msg.reasoning_content = Some(texts.join("\n\n"));
        }
    }

    msg.with_content(&assistant.media_content)
}

/// Convert a ToolUse to a ToolCall.
fn tool_use_to_call(tu: &ToolUse) -> ToolCall {
    ToolCall::function_from_value(&tu.id, &tu.name, &tu.input)
}

/// Convert tool result content (Value) to a string for the tool message.
fn tool_result_content_to_string(content: &serde_json::Value) -> String {
    match content {
        serde_json::Value::String(s) => s.clone(),
        serde_json::Value::Null => String::new(),
        other => serde_json::to_string(other).unwrap_or_default(),
    }
}

// ── Tests ──────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::transcript::{TokenUsage, ToolResultMessage};
    use serde_json::json;

    fn make_usage() -> TokenUsage {
        TokenUsage {
            input: Some(100),
            output: Some(50),
            cache_creation_input_tokens: None,
            cache_read_input_tokens: None,
        }
    }

    #[test]
    fn test_simple_turn_input_output() {
        let turn = Turn {
            chat_id: None,
            trigger: Default::default(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Hello".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: "2026-04-10T10:00:01.000Z".into(),
                end_timestamp: "2026-04-10T10:00:02.000Z".into(),
                text: "Hi there!".into(),
                media_content: serde_json::Value::Null,
                model: "claude-sonnet-4-20250514".into(),
                usage: make_usage(),
                tool_uses: vec![],
                message_id: "msg_001".into(),
                stop_reason: "end_turn".into(),
                thinking_blocks: vec![],
                is_synthetic: false,
            }],
            tool_results: vec![],
            skill_name: None,
            api_errors: vec![],
            terminal_llm_error: None,
            compactions: vec![],
            cancelled: false,
            task_notifications: Vec::new(),
        };

        let input = build_input_messages(&turn);
        assert_eq!(input.len(), 1);
        assert_eq!(input[0].role, "user");
        assert_eq!(input[0].content.as_deref(), Some("Hello"));

        let output = build_output_messages(&turn);
        assert_eq!(output.len(), 1);
        assert_eq!(output[0].role, "assistant");
        assert_eq!(output[0].content.as_deref(), Some("Hi there!"));
        assert_eq!(output[0].finish_reason.as_deref(), Some("end_turn"));
        assert!(output[0].tool_calls.is_none());
    }

    #[test]
    fn test_turn_with_tool_calls() {
        let turn = Turn {
            chat_id: None,
            trigger: Default::default(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Read file test.txt".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![
                // Intermediate assistant with tool_use
                AssistantMessage {
                    timestamp: "2026-04-10T10:00:01.000Z".into(),
                    end_timestamp: "2026-04-10T10:00:01.500Z".into(),
                    text: "Let me read that file.".into(),
                    media_content: serde_json::Value::Null,
                    model: "claude-sonnet-4-20250514".into(),
                    usage: make_usage(),
                    tool_uses: vec![ToolUse {
                        name: "Read".into(),
                        id: "toolu_001".into(),
                        input: json!({"file_path": "/tmp/test.txt"}),
                    }],
                    message_id: "msg_001".into(),
                    stop_reason: "tool_use".into(),
                    thinking_blocks: vec![],
                    is_synthetic: false,
                },
                // Final assistant response
                AssistantMessage {
                    timestamp: "2026-04-10T10:00:03.000Z".into(),
                    end_timestamp: "2026-04-10T10:00:04.000Z".into(),
                    text: "The file contains: hello world".into(),
                    media_content: serde_json::Value::Null,
                    model: "claude-sonnet-4-20250514".into(),
                    usage: make_usage(),
                    tool_uses: vec![],
                    message_id: "msg_002".into(),
                    stop_reason: "end_turn".into(),
                    thinking_blocks: vec![],
                    is_synthetic: false,
                },
            ],
            tool_results: vec![ToolResultMessage {
                timestamp: "2026-04-10T10:00:02.000Z".into(),
                items: vec![ToolResultItem {
                    tool_use_id: "toolu_001".into(),
                    content: json!("hello world"),
                    is_error: false,
                    tool_denial_kind: None,
                    tool_use_result_text: None,
                    cancellation_reason: None,
                    tool_name: Some("Read".into()),
                    timestamp: "2026-04-10T10:00:02.000Z".into(),
                    agent_id: None,
                    agent_type: None,
                    is_async: false,
                }],
            }],
            skill_name: None,
            api_errors: vec![],
            terminal_llm_error: None,
            compactions: vec![],
            cancelled: false,
            task_notifications: Vec::new(),
        };

        let input = build_input_messages(&turn);
        // Turn root span: only user message (C9 原則 2 — intermediate exchanges go to generation spans)
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
        assert_eq!(output[0].finish_reason.as_deref(), Some("end_turn"));
    }

    #[test]
    fn test_output_with_tool_calls_in_last_assistant() {
        // When the last assistant has tool_uses (stop_reason = "tool_use"),
        // the output message should include tool_calls.
        let turn = Turn {
            chat_id: None,
            trigger: Default::default(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Search for files".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: "2026-04-10T10:00:01.000Z".into(),
                end_timestamp: "2026-04-10T10:00:02.000Z".into(),
                text: "".into(),
                media_content: serde_json::Value::Null,
                model: "claude-sonnet-4-20250514".into(),
                usage: make_usage(),
                tool_uses: vec![ToolUse {
                    name: "Glob".into(),
                    id: "toolu_002".into(),
                    input: json!({"pattern": "*.rs"}),
                }],
                message_id: "msg_001".into(),
                stop_reason: "tool_use".into(),
                thinking_blocks: vec![],
                is_synthetic: false,
            }],
            tool_results: vec![],
            skill_name: None,
            api_errors: vec![],
            terminal_llm_error: None,
            compactions: vec![],
            cancelled: false,
            task_notifications: Vec::new(),
        };

        let output = build_output_messages(&turn);
        assert_eq!(output.len(), 1);
        assert_eq!(output[0].role, "assistant");
        // Empty text → content should be None
        assert!(output[0].content.is_none());
        assert!(output[0].tool_calls.is_some());
        assert_eq!(output[0].finish_reason.as_deref(), Some("tool_use"));
    }

    #[test]
    fn test_empty_turn() {
        let turn = Turn {
            chat_id: None,
            trigger: Default::default(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Hello".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![],
            tool_results: vec![],
            skill_name: None,
            api_errors: vec![],
            terminal_llm_error: None,
            compactions: vec![],
            cancelled: false,
            task_notifications: Vec::new(),
        };

        let input = build_input_messages(&turn);
        assert_eq!(input.len(), 1); // just the user message

        let output = build_output_messages(&turn);
        assert!(output.is_empty());
    }

    #[test]
    fn test_serialization_roundtrip() {
        let turn = Turn {
            chat_id: None,
            trigger: Default::default(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Weather in Paris?".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: "2026-04-10T10:00:01.000Z".into(),
                end_timestamp: "2026-04-10T10:00:02.000Z".into(),
                text: "It's rainy.".into(),
                media_content: serde_json::Value::Null,
                model: "claude-sonnet-4-20250514".into(),
                usage: make_usage(),
                tool_uses: vec![],
                message_id: "msg_001".into(),
                stop_reason: "end_turn".into(),
                thinking_blocks: vec![],
                is_synthetic: false,
            }],
            tool_results: vec![],
            skill_name: None,
            api_errors: vec![],
            terminal_llm_error: None,
            compactions: vec![],
            cancelled: false,
            task_notifications: Vec::new(),
        };

        let input = build_input_messages(&turn);
        let json_str = agentic_otel_common::message::serialize_messages_default(&input, 0);
        let parsed: Vec<serde_json::Value> = serde_json::from_str(&json_str).unwrap();
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0]["role"], "user");
        assert_eq!(parsed[0]["content"], "Weather in Paris?");
    }

    // ── Tests for per-generation accumulated & delta input ─────────

    /// Helper: build a multi-step turn with 3 assistants and tool calls.
    fn make_multi_step_turn() -> Turn {
        Turn {
            chat_id: None,
            trigger: Default::default(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Find and read test.txt".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![
                // Assistant 0: calls Glob tool
                AssistantMessage {
                    timestamp: "2026-04-10T10:00:01.000Z".into(),
                    end_timestamp: "2026-04-10T10:00:01.500Z".into(),
                    text: "Let me search for that file.".into(),
                    media_content: serde_json::Value::Null,
                    model: "claude-sonnet-4-20250514".into(),
                    usage: make_usage(),
                    tool_uses: vec![ToolUse {
                        name: "Glob".into(),
                        id: "toolu_001".into(),
                        input: json!({"pattern": "**/test.txt"}),
                    }],
                    message_id: "msg_001".into(),
                    stop_reason: "tool_use".into(),
                    thinking_blocks: vec![],
                    is_synthetic: false,
                },
                // Assistant 1: calls Read tool
                AssistantMessage {
                    timestamp: "2026-04-10T10:00:03.000Z".into(),
                    end_timestamp: "2026-04-10T10:00:03.500Z".into(),
                    text: "Found it, reading now.".into(),
                    media_content: serde_json::Value::Null,
                    model: "claude-sonnet-4-20250514".into(),
                    usage: make_usage(),
                    tool_uses: vec![ToolUse {
                        name: "Read".into(),
                        id: "toolu_002".into(),
                        input: json!({"file_path": "/tmp/test.txt"}),
                    }],
                    message_id: "msg_002".into(),
                    stop_reason: "tool_use".into(),
                    thinking_blocks: vec![],
                    is_synthetic: false,
                },
                // Assistant 2: final response
                AssistantMessage {
                    timestamp: "2026-04-10T10:00:05.000Z".into(),
                    end_timestamp: "2026-04-10T10:00:06.000Z".into(),
                    text: "The file contains: hello world".into(),
                    media_content: serde_json::Value::Null,
                    model: "claude-sonnet-4-20250514".into(),
                    usage: make_usage(),
                    tool_uses: vec![],
                    message_id: "msg_003".into(),
                    stop_reason: "end_turn".into(),
                    thinking_blocks: vec![],
                    is_synthetic: false,
                },
            ],
            tool_results: vec![
                ToolResultMessage {
                    timestamp: "2026-04-10T10:00:02.000Z".into(),
                    items: vec![ToolResultItem {
                        tool_use_id: "toolu_001".into(),
                        content: json!(["/tmp/test.txt"]),
                        is_error: false,
                        tool_denial_kind: None,
                        tool_use_result_text: None,
                        cancellation_reason: None,
                        tool_name: Some("Glob".into()),
                        timestamp: "2026-04-10T10:00:02.000Z".into(),
                        agent_id: None,
                        agent_type: None,
                        is_async: false,
                    }],
                },
                ToolResultMessage {
                    timestamp: "2026-04-10T10:00:04.000Z".into(),
                    items: vec![ToolResultItem {
                        tool_use_id: "toolu_002".into(),
                        content: json!("hello world"),
                        is_error: false,
                        tool_denial_kind: None,
                        tool_use_result_text: None,
                        cancellation_reason: None,
                        tool_name: Some("Read".into()),
                        timestamp: "2026-04-10T10:00:04.000Z".into(),
                        agent_id: None,
                        agent_type: None,
                        is_async: false,
                    }],
                },
            ],
            skill_name: None,
            api_errors: vec![],
            terminal_llm_error: None,
            compactions: vec![],
            cancelled: false,
            task_notifications: Vec::new(),
        }
    }

    #[test]
    fn test_generation_accumulated_input_first() {
        let turn = make_multi_step_turn();
        // Generation 0: only user message
        let acc = build_generation_accumulated_input(&turn, 0);
        assert_eq!(acc.len(), 1);
        assert_eq!(acc[0].role, "user");
        assert_eq!(acc[0].content.as_deref(), Some("Find and read test.txt"));
    }

    #[test]
    fn test_generation_accumulated_input_second() {
        let turn = make_multi_step_turn();
        // Generation 1: user + assistant_0 + tool_result_0 = 3 messages
        // User message at BEGINNING (natural chronological order)
        let acc = build_generation_accumulated_input(&turn, 1);
        assert_eq!(acc.len(), 3);
        assert_eq!(acc[0].role, "user");
        assert_eq!(acc[0].content.as_deref(), Some("Find and read test.txt"));
        assert_eq!(acc[1].role, "assistant");
        assert!(acc[1].tool_calls.is_some());
        assert_eq!(acc[1].tool_calls.as_ref().unwrap()[0].function.name, "Glob");
        assert_eq!(acc[2].role, "tool");
        assert_eq!(acc[2].tool_call_id.as_deref(), Some("toolu_001"));
    }

    #[test]
    fn test_generation_accumulated_input_third() {
        let turn = make_multi_step_turn();
        // Generation 2: user + assistant_0 + tool_0 + assistant_1 + tool_1 = 5
        // User message at BEGINNING (natural chronological order)
        let acc = build_generation_accumulated_input(&turn, 2);
        assert_eq!(acc.len(), 5);
        assert_eq!(acc[0].role, "user");
        assert_eq!(acc[0].content.as_deref(), Some("Find and read test.txt"));
        assert_eq!(acc[1].role, "assistant"); // assistant_0
        assert_eq!(acc[2].role, "tool"); // tool_result for Glob
        assert_eq!(acc[3].role, "assistant"); // assistant_1
        assert_eq!(acc[3].tool_calls.as_ref().unwrap()[0].function.name, "Read");
        assert_eq!(acc[4].role, "tool"); // tool_result for Read
        assert_eq!(acc[4].tool_call_id.as_deref(), Some("toolu_002"));
    }

    #[test]
    fn test_generation_delta_first() {
        let turn = make_multi_step_turn();
        // Delta for generation 0: just user message
        let delta = build_generation_delta_input(&turn, 0);
        assert_eq!(delta.len(), 1);
        assert_eq!(delta[0].role, "user");
    }

    #[test]
    fn test_generation_delta_second() {
        let turn = make_multi_step_turn();
        // Delta for generation 1: assistant_0 + tool_result_0
        let delta = build_generation_delta_input(&turn, 1);
        assert_eq!(delta.len(), 2);
        assert_eq!(delta[0].role, "assistant");
        assert!(delta[0].tool_calls.is_some());
        assert_eq!(delta[1].role, "tool");
        assert_eq!(delta[1].tool_call_id.as_deref(), Some("toolu_001"));
    }

    #[test]
    fn test_generation_delta_third() {
        let turn = make_multi_step_turn();
        // Delta for generation 2: assistant_1 + tool_result_1
        let delta = build_generation_delta_input(&turn, 2);
        assert_eq!(delta.len(), 2);
        assert_eq!(delta[0].role, "assistant");
        assert_eq!(
            delta[0].tool_calls.as_ref().unwrap()[0].function.name,
            "Read"
        );
        assert_eq!(delta[1].role, "tool");
        assert_eq!(delta[1].tool_call_id.as_deref(), Some("toolu_002"));
    }

    #[test]
    fn test_generation_single_assistant_accumulated_equals_delta() {
        // With only one assistant, accumulated and delta should be the same
        let turn = Turn {
            chat_id: None,
            trigger: Default::default(),
            user_timestamp: "2026-04-10T10:00:00.000Z".into(),
            user_text: "Hello".into(),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: "2026-04-10T10:00:01.000Z".into(),
                end_timestamp: "2026-04-10T10:00:02.000Z".into(),
                text: "Hi!".into(),
                media_content: serde_json::Value::Null,
                model: "claude-sonnet-4-20250514".into(),
                usage: make_usage(),
                tool_uses: vec![],
                message_id: "msg_001".into(),
                stop_reason: "end_turn".into(),
                thinking_blocks: vec![],
                is_synthetic: false,
            }],
            tool_results: vec![],
            skill_name: None,
            api_errors: vec![],
            terminal_llm_error: None,
            compactions: vec![],
            cancelled: false,
            task_notifications: Vec::new(),
        };

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
                json!({"timestamp":"2026-11-13T10:00:01Z","type":"user","originalPrompt":[{"type":"text","text":"inspect"}],"message":{"role":"user","content":[{"type":"text","text":"inspect"},media]}}),
                json!({"timestamp":"2026-11-13T10:00:03Z","type":"assistant","message":{"id":"a","role":"assistant","content":[{"type":"text","text":"ok"},media.clone()],"model":"test"}}),
            ];
            let turns=build_turns(&lines,None);
            assert_eq!(turns.len(),1);
            let value=serde_json::to_value(build_input_messages(&turns[0])).unwrap();
            assert_eq!(value[0]["content"][1]["source"]["media_type"],mime);
            assert_eq!(value[0]["content"][1]["source"]["data"],"YWJj");
            let output=serde_json::to_value(build_output_messages(&turns[0])).unwrap();
            assert_eq!(output[0]["content"][1]["source"]["media_type"],mime);
            assert_eq!(output[0]["content"][1]["source"]["data"],"YWJj");
        }
    }
}
