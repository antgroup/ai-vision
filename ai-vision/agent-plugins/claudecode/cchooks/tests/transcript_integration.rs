use std::fs;
use std::path::PathBuf;

use cchooks::common::transcript::{build_turns, split_non_empty_lines};

/// 辅助函数：读取 JSONL fixture 文件并解析为 Vec<Value>
fn load_fixture_lines(fixture_name: &str) -> Vec<serde_json::Value> {
    let fixture_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(fixture_name);
    let content = fs::read_to_string(&fixture_path)
        .unwrap_or_else(|e| panic!("Failed to read fixture file {}: {}", fixture_name, e));
    content
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| {
            serde_json::from_str(line)
                .unwrap_or_else(|e| panic!("Failed to parse JSONL line: {}", e))
        })
        .collect()
}

#[test]
fn test_simple_conversation_parsing() {
    let parsed_lines = load_fixture_lines("simple_conversation.jsonl");
    let turns = build_turns(&parsed_lines, None);

    assert_eq!(turns.len(), 1, "Should have exactly 1 turn");

    let turn = &turns[0];
    assert!(
        turn.user_text.contains("Hello"),
        "user_text should contain 'Hello'"
    );
    assert_eq!(
        turn.assistants.len(),
        1,
        "Should have exactly 1 assistant message"
    );

    let assistant = &turn.assistants[0];
    assert_eq!(assistant.model, "claude-sonnet-4-20250514");
    assert!(assistant.text.contains("I'm doing well"));
}

#[test]
fn test_multi_turn_with_tools() {
    let parsed_lines = load_fixture_lines("multi_turn_with_tools.jsonl");
    let turns = build_turns(&parsed_lines, None);

    assert_eq!(turns.len(), 2, "Should have 2 turns");

    // 第一轮：简单的问答
    let turn1 = &turns[0];
    assert!(turn1.user_text.contains("2+2"));
    assert_eq!(turn1.assistants.len(), 1);
    assert!(turn1.assistants[0].text.contains("4"));

    // 第二轮：包含工具调用
    // tool_result user 消息不触发新 turn，而是被收集到 tool_results 中
    // 所以第二轮有 2 个 assistant 消息（msg_002 带工具调用 + msg_003 带工具结果回复）
    let turn2 = &turns[1];
    assert!(turn2.user_text.contains("Read the file config.json"));
    assert_eq!(
        turn2.assistants.len(),
        2,
        "Should have 2 assistant messages in tool turn"
    );

    // 第一个 assistant 包含工具调用
    let assistant1 = &turn2.assistants[0];
    assert_eq!(assistant1.tool_uses.len(), 1);
    let tool_use = &assistant1.tool_uses[0];
    assert_eq!(tool_use.name, "Read");
    assert_eq!(tool_use.id, "toolu_001");

    // 验证工具结果
    assert_eq!(turn2.tool_results.len(), 1);
    let tool_result = &turn2.tool_results[0];
    assert_eq!(tool_result.items.len(), 1);
    assert_eq!(tool_result.items[0].tool_use_id, "toolu_001");

    // 第二个 assistant 是工具结果后的回复
    let assistant2 = &turn2.assistants[1];
    assert!(assistant2.text.contains("config.json contains"));
}

#[test]
fn test_skill_invocation_parsing() {
    let parsed_lines = load_fixture_lines("skill_invocation.jsonl");
    let turns = build_turns(&parsed_lines, None);

    assert_eq!(turns.len(), 1, "Should have exactly 1 turn");

    let turn = &turns[0];
    assert!(
        turn.skill_name.is_some(),
        "skill_name should be extracted from isMeta message"
    );

    let skill_name = turn.skill_name.as_ref().expect("skill_name should be Some");
    assert_eq!(skill_name, "my-skill", "skill_name should be 'my-skill'");
}

#[test]
fn test_merged_assistant_messages() {
    let parsed_lines = load_fixture_lines("merged_assistant.jsonl");
    let turns = build_turns(&parsed_lines, None);

    assert_eq!(turns.len(), 1, "Should have exactly 1 turn");

    let turn = &turns[0];
    assert_eq!(
        turn.assistants.len(),
        1,
        "Two assistant messages with same id should be merged"
    );

    let assistant = &turn.assistants[0];
    assert!(
        assistant.text.contains("Once upon a time"),
        "Should contain first text"
    );
    assert!(
        assistant.text.contains("there was a brave knight"),
        "Should contain second text"
    );

    assert_eq!(assistant.tool_uses.len(), 1, "Should have 1 tool use");
    let tool_use = &assistant.tool_uses[0];
    assert_eq!(tool_use.name, "Write");
    assert_eq!(tool_use.id, "toolu_001");
}

#[test]
fn test_async_subagent_parsing() {
    let parsed_lines = load_fixture_lines("async_subagent.jsonl");
    let turns = build_turns(&parsed_lines, None);

    assert_eq!(turns.len(), 1, "Should have exactly 1 turn");
    let turn = &turns[0];

    // is_async extraction: toolUseResult.isAsync / status=async_launched
    let items: Vec<_> = turn
        .tool_results
        .iter()
        .flat_map(|r| r.items.iter())
        .collect();
    let async1 = items
        .iter()
        .find(|i| i.tool_use_id == "tool_async1")
        .unwrap();
    assert!(
        async1.is_async,
        "tool_async1 should be async (isAsync=true)"
    );
    assert_eq!(async1.agent_id.as_deref(), Some("agent_a"));
    let async2 = items
        .iter()
        .find(|i| i.tool_use_id == "tool_async2")
        .unwrap();
    assert!(
        async2.is_async,
        "tool_async2 should be async (async_launched)"
    );
    let sync1 = items
        .iter()
        .find(|i| i.tool_use_id == "tool_sync1")
        .unwrap();
    assert!(!sync1.is_async, "tool_sync1 should not be async");

    // Task notification parsed and distributed to the launching turn
    assert_eq!(
        turn.task_notifications.len(),
        1,
        "one task-notification should attach to the turn"
    );
    let n = &turn.task_notifications[0];
    assert_eq!(n.task_id, "agent_a");
    assert_eq!(n.tool_use_id, "tool_async1");
    assert_eq!(n.status, "completed");
    assert_eq!(n.subagent_tokens, Some(12345));
    assert_eq!(n.tool_uses, Some(7));
    assert_eq!(n.duration_ms, Some(60000));
    assert_eq!(n.timestamp, "2026-07-30T08:01:10.000Z");
    assert!(n.result.contains("x.rs"));
    assert!(
        n.result.contains("literal </result> must survive"),
        "embedded closing tag must not truncate the task result"
    );
}

#[test]
fn test_api_error_invalid_model_terminal_parsing() {
    let parsed_lines = load_fixture_lines("api_error_invalid_model.jsonl");
    let turns = build_turns(&parsed_lines, None);

    assert_eq!(turns.len(), 1, "Should have exactly 1 turn");
    let turn = &turns[0];
    assert_eq!(turn.assistants.len(), 1);
    assert!(turn.assistants[0].is_synthetic);
    assert_eq!(turn.assistants[0].model, "<synthetic>");

    let terminal = turn
        .terminal_llm_error
        .as_ref()
        .expect("invalid-model fixture should produce terminal LLM error");
    assert_eq!(terminal.error_type, "invalid_model");
    assert_eq!(terminal.status_code, Some(400));
    assert_eq!(
        terminal.request_model.as_deref(),
        Some("claude-regression-invalid-20260824")
    );
}

#[test]
fn test_cancelled_foreground_tool_parsing() {
    let parsed_lines = load_fixture_lines("cancelled_foreground_tool.jsonl");
    let turns = build_turns(&parsed_lines, None);

    assert_eq!(turns.len(), 1, "Should have exactly 1 turn");
    let turn = &turns[0];
    assert!(
        turn.cancelled,
        "strong user rejection evidence should cancel the turn"
    );
    assert_eq!(turn.tool_results.len(), 1);
    let item = &turn.tool_results[0].items[0];
    assert!(item.is_error);
    assert_eq!(item.tool_denial_kind.as_deref(), Some("user-rejected"));
    assert_eq!(
        item.tool_use_result_text.as_deref(),
        Some("User rejected tool use")
    );
    assert_eq!(item.cancellation_reason.as_deref(), Some("user_rejected"));
}

#[test]
fn test_split_non_empty_lines() {
    let result = split_non_empty_lines("");
    assert!(result.is_empty(), "Empty string should return empty vec");

    let result = split_non_empty_lines("line1");
    assert_eq!(result.len(), 1);
    assert_eq!(result[0], "line1");

    let result = split_non_empty_lines("line1\nline2\nline3");
    assert_eq!(result.len(), 3);
    assert_eq!(result[0], "line1");
    assert_eq!(result[1], "line2");
    assert_eq!(result[2], "line3");

    // split_non_empty_lines 只过滤 is_empty() 的行，不过滤纯空白行
    let result = split_non_empty_lines("line1\n\nline2\n   \nline3");
    assert_eq!(
        result.len(),
        4,
        "Only truly empty lines are filtered, whitespace-only lines are kept"
    );
    assert_eq!(result[0], "line1");
    assert_eq!(result[1], "line2");
    assert_eq!(result[2], "   ");
    assert_eq!(result[3], "line3");

    // 测试只有真正的空行
    let result = split_non_empty_lines("\n\n\n");
    assert!(
        result.is_empty(),
        "Only empty lines should return empty vec"
    );

    // 空白行不算空行
    let result = split_non_empty_lines("\n\n   \n");
    assert_eq!(result.len(), 1, "Whitespace-only line is not empty");
    assert_eq!(result[0], "   ");
}
