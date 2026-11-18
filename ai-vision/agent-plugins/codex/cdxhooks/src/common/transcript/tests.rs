use super::*;
use serde_json::json;

#[test]
fn test_build_turns_codex_fixture() {
    let lines = vec![
        json!({"timestamp":"2026-03-03T14:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-codex-1"}}),
        json!({"timestamp":"2026-03-03T14:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"run diagnostics\n"}}),
        json!({"timestamp":"2026-03-03T14:00:00.130Z","type":"turn_context","payload":{"turn_id":"turn-codex-1","cwd":"/tmp/demo","model":"gpt-5.3-codex"}}),
        json!({"timestamp":"2026-03-03T14:00:00.200Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"pwd\"}","call_id":"call-1"}}),
        json!({"timestamp":"2026-03-03T14:00:00.210Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-1","output":"Exit code: 0\nOutput:\n/tmp/demo\n"}}),
        json!({"timestamp":"2026-03-03T14:00:00.260Z","type":"response_item","payload":{"type":"web_search_call","status":"completed","action":{"type":"search","query":"spanory"}}}),
        json!({"timestamp":"2026-03-03T14:00:00.300Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":20,"output_tokens":10,"total_tokens":30},"total_token_usage":{"input_tokens":100,"output_tokens":50,"total_tokens":150}}}}),
        json!({"timestamp":"2026-03-03T14:00:00.350Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"Collecting data"}}),
        json!({"timestamp":"2026-03-03T14:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-codex-1","last_agent_message":"Diagnostics complete"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    let turn = &turns[0];
    assert_eq!(turn.turn_id, "turn-codex-1");
    assert_eq!(turn.user_text, "run diagnostics");
    assert_eq!(turn.model, "gpt-5.3-codex");
    assert_eq!(turn.tool_calls.len(), 2);
    assert_eq!(turn.tool_calls[0].name, "Bash");
    assert_eq!(turn.tool_calls[1].name, "WebSearch");
    assert_eq!(turn.assistants.len(), 3);
    assert!(turn.assistants[0].text.is_empty());
    assert!(turn.assistants[1].text.is_empty());
    assert_eq!(turn.assistants[2].phase, "final_answer");
    assert_eq!(turn.assistants[1].usage.input, Some(20));
    assert_eq!(turn.assistants[1].usage.output, Some(10));
}

#[test]
fn test_build_turns_maps_goal_update_to_next_user_prompt() {
    let lines = vec![
        json!({"timestamp":"2026-08-13T03:07:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"previous-turn"}}),
        json!({"timestamp":"2026-08-13T03:07:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"inspect the issue"}}),
        json!({"timestamp":"2026-08-13T03:07:01.000Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"confirmed"}}),
        json!({"timestamp":"2026-08-13T03:07:01.010Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"previous-turn","last_agent_message":"confirmed"}}),
        json!({"timestamp":"2026-08-13T03:07:39.090Z","type":"event_msg","payload":{"type":"thread_goal_updated","threadId":"session-1","goal":{"threadId":"session-1","objective":"同样修复这个问题","status":"active"}}}),
        json!({"timestamp":"2026-08-13T03:07:39.098Z","type":"event_msg","payload":{"type":"task_started","turn_id":"goal-turn"}}),
        json!({"timestamp":"2026-08-13T03:07:41.545Z","type":"turn_context","payload":{"turn_id":"goal-turn","model":"gpt-5.6-sol"}}),
        json!({"timestamp":"2026-08-13T03:07:41.549Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"<codex_internal_context source=\"goal\">\nContinue working toward the active thread goal.\n<objective>\n同样修复这个问题\n</objective>\ninternal-only instructions\n</codex_internal_context>"}],"internal_chat_message_metadata_passthrough":{"turn_id":"goal-turn"}}}),
        json!({"timestamp":"2026-08-13T03:07:56.419Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"working"}}),
    ];

    let turns = build_turns(&lines);

    assert_eq!(turns.len(), 2);
    assert_eq!(turns[1].turn_id, "goal-turn");
    assert_eq!(turns[1].user_text, "/goal 同样修复这个问题");
    assert!(!turns[1].user_text.contains("internal-only instructions"));
}

#[test]
fn test_response_role_user_requires_active_turn_context() {
    let lines = vec![
        json!({"timestamp":"2026-08-15T07:58:03.100Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"# AGENTS.md instructions for /workspace\n<environment_context>bootstrap</environment_context>"}]}}),
        json!({"timestamp":"2026-08-15T07:58:20.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-real"}}),
        json!({"timestamp":"2026-08-15T07:58:20.010Z","type":"turn_context","payload":{"turn_id":"turn-real"}}),
        json!({"timestamp":"2026-08-15T07:58:20.020Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"real prompt"}],"internal_chat_message_metadata_passthrough":{"turn_id":"turn-real"}}}),
        json!({"timestamp":"2026-08-15T07:58:21.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-real","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].user_text, "real prompt");
}

#[test]
fn test_legacy_user_message_overrides_mirrored_role_user_fallback() {
    let lines = vec![
        json!({"timestamp":"2026-08-15T07:58:20.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-mirror"}}),
        json!({"timestamp":"2026-08-15T07:58:20.010Z","type":"turn_context","payload":{"turn_id":"turn-mirror"}}),
        json!({"timestamp":"2026-08-15T07:58:20.020Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"mirrored prompt"}],"internal_chat_message_metadata_passthrough":{"turn_id":"turn-mirror"}}}),
        json!({"timestamp":"2026-08-15T07:58:20.030Z","type":"event_msg","payload":{"type":"user_message","message":"canonical prompt"}}),
        json!({"timestamp":"2026-08-15T07:58:21.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-mirror","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].user_text, "canonical prompt");
}

#[test]
fn test_active_prompt_with_plain_agents_and_xml_words_is_not_filtered() {
    let prompt = "Please inspect AGENTS and explain <ordinary> XML.";
    let lines = vec![
        json!({"timestamp":"2026-08-15T07:58:20.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-plain-text"}}),
        json!({"timestamp":"2026-08-15T07:58:20.010Z","type":"turn_context","payload":{"turn_id":"turn-plain-text"}}),
        json!({"timestamp":"2026-08-15T07:58:20.020Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":prompt}]}}),
        json!({"timestamp":"2026-08-15T07:58:21.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-plain-text","last_agent_message":"done"}}),
    ];

    assert_eq!(build_turns(&lines)[0].user_text, prompt);
}

#[test]
fn test_native_usage_mode_clears_later_stale_legacy_usage_placeholder() {
    let lines = vec![
        json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-native"}}),
        json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"native prompt"}}),
        json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"native output"}]}}),
        json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"token_usage_record","payload":{"turn_id":"turn-native","response_id":"resp-native","usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120}}}),
        json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-native","last_agent_message":"native output"}}),
        json!({"timestamp":"2026-09-05T00:01:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-aborted"}}),
        json!({"timestamp":"2026-09-05T00:01:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"aborted prompt"}}),
        json!({"timestamp":"2026-09-05T00:01:01.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":111000,"output_tokens":437,"total_tokens":111437}}}}),
        json!({"timestamp":"2026-09-05T00:01:02.000Z","type":"event_msg","payload":{"type":"turn_aborted","turn_id":"turn-aborted"}}),
    ];

    let turns = build_turns(&lines);

    assert_eq!(turns.len(), 2);
    assert_eq!(turns[0].assistants.len(), 1);
    assert_eq!(turns[0].assistants[0].usage.total, Some(120));
    assert_eq!(turns[1].turn_id, "turn-aborted");
    assert!(turns[1].assistants.is_empty());
}

#[test]
fn test_initial_native_usage_mode_clears_incremental_stale_legacy_placeholder() {
    let lines = vec![
        json!({"timestamp":"2026-09-05T00:01:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-aborted"}}),
        json!({"timestamp":"2026-09-05T00:01:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"aborted prompt"}}),
        json!({"timestamp":"2026-09-05T00:01:01.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":111000,"output_tokens":437,"total_tokens":111437}}}}),
        json!({"timestamp":"2026-09-05T00:01:02.000Z","type":"event_msg","payload":{"type":"turn_aborted","turn_id":"turn-aborted"}}),
    ];

    let turns = build_turns_with_usage_context(&lines, None, true);

    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].turn_id, "turn-aborted");
    assert!(turns[0].assistants.is_empty());
}

#[test]
fn test_delayed_prior_turn_native_usage_updates_superseded_turn_once() {
    let lines = vec![
        json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"old"}}),
        json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"old prompt"}}),
        json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"old output"}]}}),
        json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"next"}}),
        json!({"timestamp":"2026-09-05T00:00:02.010Z","type":"event_msg","payload":{"type":"user_message","message":"next prompt"}}),
        json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"token_usage_record","payload":{"turn_id":"old","response_id":"resp-old-late","usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120}}}),
        json!({"timestamp":"2026-09-05T00:00:04.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"next","last_agent_message":"next done"}}),
    ];

    let turns = build_turns(&lines);

    assert_eq!(turns.len(), 2);
    assert_eq!(turns[0].turn_id, "old");
    assert_eq!(turns[0].terminal_state, TurnTerminalState::Superseded);
    assert_eq!(turns[0].completion_timestamp, "2026-09-05T00:00:03.000Z");
    assert_eq!(turns[0].assistants.len(), 1);
    assert_eq!(turns[0].assistants[0].text, "old output");
    assert_eq!(turns[0].assistants[0].usage.total, Some(120));
    assert_eq!(
        turns[0].assistants[0].usage.response_id.as_deref(),
        Some("resp-old-late")
    );
    assert_eq!(turns[1].turn_id, "next");
    assert_eq!(turns[1].terminal_state, TurnTerminalState::Completed);
    assert!(turns[1]
        .assistants
        .iter()
        .all(|assistant| assistant.usage.response_id.as_deref() != Some("resp-old-late")));
}

#[test]
fn test_native_usage_mode_preserves_superseded_response_text_before_usage_arrives() {
    let lines = vec![
        json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"old"}}),
        json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"old prompt"}}),
        json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"old output"}]}}),
        json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"next"}}),
        json!({"timestamp":"2026-09-05T00:00:02.010Z","type":"event_msg","payload":{"type":"user_message","message":"next prompt"}}),
    ];

    let turns = build_turns_with_usage_context(&lines, None, true);

    assert_eq!(turns.len(), 2);
    assert_eq!(turns[0].turn_id, "old");
    assert_eq!(turns[0].terminal_state, TurnTerminalState::Superseded);
    assert_eq!(turns[0].assistants.len(), 1);
    assert_eq!(turns[0].assistants[0].text, "old output");
    assert!(!turns[0].assistants[0].usage.has_any());
    assert_eq!(
        turns[0].assistants[0].invocation_end_source,
        Some(InvocationEndSource::NativeUsagePending)
    );
    assert!(!turns[0].has_semantic_assistant_at(0));
    assert_eq!(turns[1].turn_id, "next");
    assert!(turns[1].assistants.is_empty());
}

#[test]
fn test_write_stdin_is_a_distinct_continuation_call() {
    let lines = vec![
        json!({"timestamp":"2026-03-03T14:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-codex-1"}}),
        json!({"timestamp":"2026-03-03T14:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"continue bash\n"}}),
        json!({"timestamp":"2026-03-03T14:00:00.200Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"bash\",\"yield_time_ms\":1000}","call_id":"call-1"}}),
        json!({"timestamp":"2026-03-03T14:00:00.210Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-1","output":"Process running. session ID 42"}}),
        json!({"timestamp":"2026-03-03T14:00:00.220Z","type":"response_item","payload":{"type":"function_call","name":"write_stdin","arguments":"{\"session_id\":42,\"chars\":\"ls\\n\"}","call_id":"call-2"}}),
        json!({"timestamp":"2026-03-03T14:00:00.230Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-2","output":"README.md\n"}}),
        json!({"timestamp":"2026-03-03T14:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-codex-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].tool_calls.len(), 2);
    assert_eq!(
        turns[0].tool_calls[0].output,
        Value::String("Process running. session ID 42".to_string())
    );
    assert_eq!(
        turns[0].tool_calls[1].output,
        Value::String("README.md\n".to_string())
    );
    assert_eq!(turns[0].tool_calls[1].call_id, "call-2");
}

#[test]
fn test_write_stdin_multiple_calls_remain_independently_observable() {
    let lines = vec![
        json!({"timestamp":"2026-03-03T14:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-codex-2"}}),
        json!({"timestamp":"2026-03-03T14:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"continue bash\n"}}),
        json!({"timestamp":"2026-03-03T14:00:00.200Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"bash\",\"yield_time_ms\":1000}","call_id":"call-1"}}),
        json!({"timestamp":"2026-03-03T14:00:00.210Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-1","output":"Process running. session ID 42"}}),
        json!({"timestamp":"2026-03-03T14:00:00.220Z","type":"response_item","payload":{"type":"function_call","name":"write_stdin","arguments":"{\"session_id\":42,\"chars\":\"\\u0003\"}","call_id":"call-2"}}),
        json!({"timestamp":"2026-03-03T14:00:00.230Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-2","output":"first chunk\n"}}),
        json!({"timestamp":"2026-03-03T14:00:00.240Z","type":"response_item","payload":{"type":"function_call","name":"write_stdin","arguments":"{\"session_id\":42,\"chars\":\"\\u0004\"}","call_id":"call-3"}}),
        json!({"timestamp":"2026-03-03T14:00:00.250Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-3","output":"second chunk\n"}}),
        json!({"timestamp":"2026-03-03T14:00:00.260Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-codex-2","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].tool_calls.len(), 3);
    assert_eq!(
        turns[0].tool_calls[0].output,
        Value::String("Process running. session ID 42".to_string())
    );
    assert_eq!(
        turns[0].tool_calls[1].output,
        Value::String("first chunk\n".to_string())
    );
    assert_eq!(
        turns[0].tool_calls[2].output,
        Value::String("second chunk\n".to_string())
    );
    assert_eq!(
        turns[0]
            .tool_calls
            .iter()
            .map(|tool| tool.call_id.as_str())
            .collect::<Vec<_>>(),
        vec!["call-1", "call-2", "call-3"]
    );
}

fn mcp_result_turn_lines(result: Value) -> Vec<Value> {
    vec![
        json!({"timestamp":"2026-03-03T14:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-mcp"}}),
        json!({"timestamp":"2026-03-03T14:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"call mcp\n"}}),
        json!({"timestamp":"2026-03-03T14:00:00.200Z","type":"response_item","payload":{"type":"function_call","name":"mcp_tool","arguments":"{\"name\":\"demo\"}","call_id":"call-mcp"}}),
        json!({"timestamp":"2026-03-03T14:00:00.250Z","type":"event_msg","payload":{"type":"mcp_tool_call_end","call_id":"call-mcp","result":result}}),
        json!({"timestamp":"2026-03-03T14:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-mcp","last_agent_message":"done"}}),
    ]
}

#[test]
fn test_mcp_ok_text_result_output_is_string() {
    let lines = mcp_result_turn_lines(json!({
        "Ok": {
            "content": [
                {"type": "text", "text": "plain mcp output"}
            ]
        }
    }));

    let normalized = normalize_turn_semantics(&lines);
    assert_eq!(
        normalized[0].callbacks[0].output,
        Value::String("plain mcp output".to_string())
    );
    assert_eq!(normalized[0].callbacks[0].success, Some(true));

    let turns = build_turns(&lines);
    assert_eq!(
        turns[0].tool_calls[0].output,
        Value::String("plain mcp output".to_string())
    );
    assert_eq!(turns[0].tool_calls[0].success, Some(true));
}

#[test]
fn test_mcp_err_result_output_is_error_string() {
    let lines = mcp_result_turn_lines(json!({
        "Err": "mcp failed"
    }));

    let normalized = normalize_turn_semantics(&lines);
    assert_eq!(
        normalized[0].callbacks[0].output,
        Value::String("mcp failed".to_string())
    );
    assert_eq!(normalized[0].callbacks[0].success, Some(false));

    let turns = build_turns(&lines);
    assert_eq!(
        turns[0].tool_calls[0].output,
        Value::String("mcp failed".to_string())
    );
    assert_eq!(turns[0].tool_calls[0].success, Some(false));
}

#[test]
fn test_mcp_multi_or_non_text_ok_result_falls_back_without_data_loss() {
    let ok = json!({
        "content": [
            {"type": "text", "text": "first"},
            {"type": "image", "data": "abc", "mimeType": "image/png"}
        ],
        "structuredContent": {"count": 2}
    });
    let lines = mcp_result_turn_lines(json!({ "Ok": ok.clone() }));

    let normalized = normalize_turn_semantics(&lines);
    let normalized_output = normalized[0].callbacks[0]
        .output
        .as_str()
        .expect("normalized fallback remains string");
    assert_eq!(
        serde_json::from_str::<Value>(normalized_output).unwrap(),
        ok
    );

    let turns = build_turns(&lines);
    let legacy_output = turns[0].tool_calls[0]
        .output
        .as_str()
        .expect("build_turns fallback remains string");
    assert_eq!(serde_json::from_str::<Value>(legacy_output).unwrap(), ok);
    assert_eq!(turns[0].tool_calls[0].success, Some(true));
}

#[test]
fn test_item_completed_command_execution_becomes_failed_bash_tool() {
    let lines = vec![
        json!({"timestamp":"2026-08-23T02:01:20.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-command-item"}}),
        json!({"timestamp":"2026-08-23T02:01:20.100Z","type":"event_msg","payload":{"type":"user_message","message":"run command"}}),
        json!({"timestamp":"2026-08-23T02:01:20.200Z","type":"response_item","payload":{"type":"custom_tool_call","name":"exec","call_id":"wrapper-command","input":"tools.exec_command({cmd: \"missing\"})"}}),
        json!({"timestamp":"2026-08-23T02:01:20.210Z","type":"response_item","payload":{"type":"custom_tool_call_output","call_id":"wrapper-command","output":null}}),
        json!({"timestamp":"2026-08-23T02:01:20.300Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CommandExecution","id":"exec-command-item","command":["/bin/sh","-c","missing"],"status":"failed","exit_code":1,"aggregated_output":"missing: command not found\n"},"started_at_ms":1787440880123_u64,"completed_at_ms":1787440880299_u64}}),
        json!({"timestamp":"2026-08-23T02:01:20.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-command-item","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].tool_calls.len(), 1);
    assert_eq!(turns[0].tool_calls[0].name, "Bash");
    assert_eq!(turns[0].tool_calls[0].call_id, "exec-command-item");
    assert_eq!(turns[0].tool_calls[0].success, Some(false));
    assert_eq!(
        turns[0].tool_calls[0].started_timestamp,
        "1787440880123000000"
    );
    assert_eq!(
        turns[0].tool_calls[0].ended_timestamp,
        "1787440880299000000"
    );
    assert_eq!(
        turns[0].tool_calls[0].output,
        Value::String("missing: command not found\n".to_string())
    );
}

#[test]
fn test_response_tool_namespace_preserves_existing_and_builtin_names() {
    assert_eq!(super::response_tool_name(&json!({"name":"mcp__powercontext__search_memory","namespace":"mcp__powercontext"})), "mcp__powercontext__search_memory");
    assert_eq!(super::response_tool_name(&json!({"name":"exec_command","namespace":"functions"})), "exec_command");
    assert_eq!(super::response_tool_name(&json!({"name":"search_memory","namespace":"mcp__"})), "search_memory");
    assert_eq!(super::response_tool_name(&json!({"name":"","namespace":"mcp__powercontext"})), "");
}

#[test]
fn test_namespaced_function_call_preserves_mcp_identity() {
    let lines = vec![
        json!({"timestamp":"2026-11-02T02:29:40.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"namespace-turn"}}),
        json!({"timestamp":"2026-11-02T02:29:40.100Z","type":"event_msg","payload":{"type":"user_message","message":"search memory"}}),
        json!({"timestamp":"2026-11-02T02:29:41.000Z","type":"response_item","payload":{"type":"function_call","name":"search_memory","namespace":"mcp__powercontext","call_id":"memory-call","arguments":"{\"query\":\"amount\"}"}}),
        json!({"timestamp":"2026-11-02T02:29:42.000Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"McpToolCall","id":"memory-call","server":"powercontext","tool":"search_memory","arguments":{"query":"amount"},"status":"completed","result":{"content":[{"type":"text","text":"{\"hits\":[]}"}],"isError":false}}}}),
        json!({"timestamp":"2026-11-02T02:29:42.100Z","type":"response_item","payload":{"type":"function_call_output","call_id":"memory-call","output":"{\"hits\":[]}"}}),
        json!({"timestamp":"2026-11-02T02:29:43.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"namespace-turn","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].tool_calls.len(), 1);
    assert_eq!(
        turns[0].tool_calls[0].name,
        "mcp__powercontext__search_memory"
    );
    assert_eq!(turns[0].tool_calls[0].input, json!({"query":"amount"}));
}

#[test]
fn test_item_completed_mcp_tool_call_preserves_server_and_tool_identity() {
    let lines = vec![
        json!({"timestamp":"2026-08-23T02:17:40.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-mcp-item"}}),
        json!({"timestamp":"2026-08-23T02:17:40.100Z","type":"event_msg","payload":{"type":"user_message","message":"call mcp"}}),
        json!({"timestamp":"2026-08-23T02:17:40.200Z","type":"response_item","payload":{"type":"custom_tool_call","name":"exec","call_id":"wrapper-mcp","input":"tools.mcp__demo__add({a: 2, b: 40})"}}),
        json!({"timestamp":"2026-08-23T02:17:40.210Z","type":"response_item","payload":{"type":"custom_tool_call_output","call_id":"wrapper-mcp","output":null}}),
        json!({"timestamp":"2026-08-23T02:17:40.300Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"McpToolCall","id":"mcp-item","server":"demo","tool":"add","arguments":{"a":2,"b":40},"status":"completed","result":{"content":[{"type":"text","text":"42"}],"isError":false}}}}),
        json!({"timestamp":"2026-08-23T02:17:40.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-mcp-item","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns[0].tool_calls.len(), 1);
    assert_eq!(turns[0].tool_calls[0].name, "mcp__demo__add");
    assert_eq!(turns[0].tool_calls[0].success, Some(true));
    assert_eq!(
        turns[0].tool_calls[0].output,
        Value::String("42".to_string())
    );
}

#[test]
fn test_item_completed_command_execution_batches_follow_outer_exec_wrappers() {
    let lines = vec![
        json!({"timestamp":"2026-08-23T02:09:40.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-command-batches"}}),
        json!({"timestamp":"2026-08-23T02:09:40.100Z","type":"event_msg","payload":{"type":"user_message","message":"run batches"}}),
        json!({"timestamp":"2026-08-23T02:09:40.200Z","type":"response_item","payload":{"type":"custom_tool_call","name":"exec","call_id":"wrapper-one","input":"parallel round one"}}),
        json!({"timestamp":"2026-08-23T02:09:40.210Z","type":"response_item","payload":{"type":"custom_tool_call_output","call_id":"wrapper-one","output":null}}),
        json!({"timestamp":"2026-08-23T02:09:41.000Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CommandExecution","id":"exec-one-a","command":["/bin/sh","-c","echo one-a"],"status":"completed","exit_code":0,"aggregated_output":"one-a\n"}}}),
        json!({"timestamp":"2026-08-23T02:09:41.010Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CommandExecution","id":"exec-one-b","command":["/bin/sh","-c","echo one-b"],"status":"completed","exit_code":0,"aggregated_output":"one-b\n"}}}),
        json!({"timestamp":"2026-08-23T02:09:42.000Z","type":"response_item","payload":{"type":"custom_tool_call","name":"exec","call_id":"wrapper-two","input":"parallel round two"}}),
        json!({"timestamp":"2026-08-23T02:09:42.010Z","type":"response_item","payload":{"type":"custom_tool_call_output","call_id":"wrapper-two","output":null}}),
        json!({"timestamp":"2026-08-23T02:09:43.000Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CommandExecution","id":"exec-two-a","command":["/bin/sh","-c","echo two-a"],"status":"completed","exit_code":0,"aggregated_output":"two-a\n"}}}),
        json!({"timestamp":"2026-08-23T02:09:43.010Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CommandExecution","id":"exec-two-b","command":["/bin/sh","-c","echo two-b"],"status":"completed","exit_code":0,"aggregated_output":"two-b\n"}}}),
        json!({"timestamp":"2026-08-23T02:09:44.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-command-batches","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns[0].tool_calls.len(), 4);
    assert_eq!(
        turns[0]
            .tool_calls
            .iter()
            .map(|tool| tool.call_id.as_str())
            .collect::<Vec<_>>(),
        vec!["exec-one-a", "exec-one-b", "exec-two-a", "exec-two-b"]
    );
    assert_eq!(
        turns[0]
            .tool_calls
            .iter()
            .map(|tool| tool.assistant_index)
            .collect::<Vec<_>>(),
        vec![0, 0, 1, 1]
    );
}

#[test]
fn test_non_exec_callback_does_not_break_outer_exec_wrapper_parentage() {
    let lines = vec![
        json!({"timestamp":"2026-08-23T02:09:40.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-command-interleaved"}}),
        json!({"timestamp":"2026-08-23T02:09:40.100Z","type":"event_msg","payload":{"type":"user_message","message":"run command and inspect metadata"}}),
        json!({"timestamp":"2026-08-23T02:09:40.200Z","type":"response_item","payload":{"type":"custom_tool_call","name":"exec","call_id":"wrapper-exec","input":"tools.exec_command({cmd: \"echo ok\"})"}}),
        json!({"timestamp":"2026-08-23T02:09:40.210Z","type":"response_item","payload":{"type":"custom_tool_call","name":"metadata_lookup","call_id":"metadata-call","input":"{}"}}),
        json!({"timestamp":"2026-08-23T02:09:40.300Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CommandExecution","id":"exec-command","command":["/bin/sh","-c","echo ok"],"status":"completed","exit_code":0,"aggregated_output":"ok\n"}}}),
        json!({"timestamp":"2026-08-23T02:09:40.310Z","type":"response_item","payload":{"type":"custom_tool_call_output","call_id":"wrapper-exec","output":null}}),
        json!({"timestamp":"2026-08-23T02:09:40.320Z","type":"response_item","payload":{"type":"custom_tool_call_output","call_id":"metadata-call","output":"metadata"}}),
        json!({"timestamp":"2026-08-23T02:09:41.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-command-interleaved","last_agent_message":"done"}}),
    ];

    let normalized = normalize_turn_semantics(&lines);
    let wrapper = normalized[0]
        .callbacks
        .iter()
        .find(|callback| callback.callback_id == "wrapper-exec")
        .expect("exec wrapper callback");
    let concrete = normalized[0]
        .callbacks
        .iter()
        .find(|callback| callback.callback_id == "exec-command")
        .expect("concrete command callback");
    assert!(wrapper.suppress_if_nested);
    assert_eq!(concrete.invocation_ordinal, wrapper.invocation_ordinal);

    let turns = build_turns(&lines);
    assert_eq!(
        turns[0]
            .tool_calls
            .iter()
            .map(|tool| tool.call_id.as_str())
            .collect::<Vec<_>>(),
        vec!["metadata-call", "exec-command"]
    );
    assert_eq!(turns[0].tool_calls[1].name, "Bash");
}

#[test]
fn test_unknown_compaction_usage_clears_pending_marker_without_false_backfill() {
    let lines = vec![
        json!({"timestamp":"2026-08-23T03:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-compaction-unknown"}}),
        json!({"timestamp":"2026-08-23T03:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"compact"}}),
        json!({"timestamp":"2026-08-23T03:00:00.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"before"}}),
        json!({"timestamp":"2026-08-23T03:00:00.300Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"output_tokens":10,"total_tokens":110}}}}),
        json!({"timestamp":"2026-08-23T03:00:00.400Z","type":"compacted","payload":{"replacement_history":[]}}),
        json!({"timestamp":"2026-08-23T03:00:00.500Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"after unknown usage"}}),
        json!({"timestamp":"2026-08-23T03:00:00.600Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"output_tokens":3,"total_tokens":3}}}}),
        json!({"timestamp":"2026-08-23T03:00:00.700Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"later invocation"}}),
        json!({"timestamp":"2026-08-23T03:00:00.800Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":0,"output_tokens":0,"total_tokens":5552}}}}),
        json!({"timestamp":"2026-08-23T03:00:00.900Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-compaction-unknown","last_agent_message":"later invocation"}}),
    ];

    let normalized = normalize_turn_semantics(&lines);
    assert_eq!(normalized[0].invocations.len(), 3);
    assert_eq!(normalized[0].invocations[1].usage.output, Some(3));
    assert_eq!(normalized[0].invocations[2].usage.total, Some(5552));

    let turns = build_turns(&lines);
    assert_eq!(turns[0].compactions.len(), 1);
    assert_eq!(turns[0].compactions[0].pre_tokens, Some(100));
    assert_eq!(turns[0].compactions[0].post_tokens, None);
    assert_eq!(turns[0].assistants[1].usage.output, Some(3));
    assert_eq!(turns[0].assistants[2].usage.total, Some(5552));
}

#[test]
fn test_split_completed_messages_keeps_trailing_incomplete_turn() {
    let lines = vec![
        json!({"timestamp":"2026-03-03T14:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-03-03T14:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"first\n"}}),
        json!({"timestamp":"2026-03-03T14:00:00.200Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
        json!({"timestamp":"2026-03-03T14:00:01.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-2"}}),
        json!({"timestamp":"2026-03-03T14:00:01.120Z","type":"event_msg","payload":{"type":"user_message","message":"second\n"}}),
        json!({"timestamp":"2026-03-03T14:00:01.200Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"pwd\"}","call_id":"call-2"}}),
    ];

    let (completed, pending) = split_completed_messages(&lines);
    let turns = build_turns(&completed);

    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].turn_id, "turn-1");
    assert_eq!(turns[0].user_text, "first");
    assert_eq!(pending.len(), 3);
    assert_eq!(pending[0]["payload"]["turn_id"], "turn-2");
}

#[test]
fn test_split_completed_messages_treats_distinct_start_as_superseding_prior_open_turn() {
    let lines = vec![
        json!({"timestamp":"2026-03-03T14:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-03-03T14:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"first"}}),
        json!({"timestamp":"2026-03-03T14:00:00.200Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15}}}}),
        json!({"timestamp":"2026-03-03T14:00:01.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-2"}}),
        json!({"timestamp":"2026-03-03T14:00:01.120Z","type":"event_msg","payload":{"type":"user_message","message":"second"}}),
    ];

    let (completed, pending) = split_completed_messages(&lines);

    assert_eq!(completed.len(), 3);
    assert_eq!(pending[0]["payload"]["turn_id"], "turn-2");
    let turns = build_turns(&lines);
    assert_eq!(turns[0].terminal_state, TurnTerminalState::Superseded);
    assert_eq!(turns[0].completion_timestamp, "2026-03-03T14:00:00.200Z");
    assert_eq!(turns[0].assistants[0].usage.total, Some(15));
    assert_eq!(turns[1].terminal_state, TurnTerminalState::Open);
}

#[test]
fn test_duplicate_task_started_same_id_does_not_split_turn() {
    let lines = vec![
        json!({"timestamp":"2026-03-03T14:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"same-turn"}}),
        json!({"timestamp":"2026-03-03T14:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"first"}}),
        json!({"timestamp":"2026-03-03T14:00:00.200Z","type":"event_msg","payload":{"type":"task_started","turn_id":"same-turn"}}),
        json!({"timestamp":"2026-03-03T14:00:00.250Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}]}}),
        json!({"timestamp":"2026-03-03T14:00:00.300Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":20,"output_tokens":7,"total_tokens":27}}}}),
        json!({"timestamp":"2026-03-03T14:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"same-turn","last_agent_message":"done"}}),
    ];

    let (completed, pending) = split_completed_messages(&lines);
    assert_eq!(completed.len(), lines.len());
    assert!(pending.is_empty());

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].terminal_state, TurnTerminalState::Completed);
    assert_eq!(turns[0].user_text, "first");
    assert_eq!(turns[0].assistants[0].usage.total, Some(27));
}

#[test]
fn native_usage_without_visible_messages_keeps_its_turn() {
    let lines = vec![
        json!({"timestamp":"2026-09-05T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"usage-only"}}),
        json!({"timestamp":"2026-09-05T00:00:02Z","type":"token_usage_record","payload":{"turn_id":"usage-only","response_id":"resp-usage-only","usage":{"input_tokens":20,"output_tokens":5,"total_tokens":25}}}),
        json!({"timestamp":"2026-09-05T00:00:03Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"usage-only"}}),
    ];
    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].assistants.len(), 1);
    assert_eq!(turns[0].assistants[0].usage.total, Some(25));
    assert_eq!(
        turns[0].assistants[0].usage.response_id.as_deref(),
        Some("resp-usage-only")
    );
}

#[test]
fn test_late_terminal_for_previous_turn_does_not_close_current_turn() {
    let lines = vec![
        json!({"timestamp":"2026-03-03T14:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-03-03T14:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"first"}}),
        json!({"timestamp":"2026-03-03T14:00:00.200Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-2"}}),
        json!({"timestamp":"2026-03-03T14:00:00.220Z","type":"event_msg","payload":{"type":"user_message","message":"second"}}),
        json!({"timestamp":"2026-03-03T14:00:00.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"first done"}}),
    ];

    let (completed, pending) = split_completed_messages(&lines);
    assert_eq!(completed.len(), 2);
    assert_eq!(pending[0]["payload"]["turn_id"], "turn-2");

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 2);
    assert_eq!(turns[0].terminal_state, TurnTerminalState::Completed);
    assert_eq!(turns[0].assistants[0].text, "first done");
    assert_eq!(turns[1].terminal_state, TurnTerminalState::Open);
    assert_eq!(turns[1].user_text, "second");
}

// --- skill detection unit tests ---

#[test]
fn test_detect_skill_from_exec_command_cli() {
    let raw_args =
        json!("{\"cmd\":\"sed -n '1,40p' /Users/testuser/.codex/skills/eve-beta/SKILL.md\"}");
    let parsed_args = parse_jsonish_value(&raw_args);
    let invocation = detect_skill_from_tool_input(
        "exec_command",
        &raw_args,
        &parsed_args,
        "2026-08-17T00:00:00Z",
    )
    .expect("legacy exec_command skill");
    assert_eq!(invocation.name, "eve-beta");
    assert_eq!(
        invocation.source,
        SkillInvocationSource::CommandPathFallback
    );
}

#[test]
fn test_detect_skill_from_exec_command_system() {
    let raw_args = json!(
        "{\"cmd\":\"sed -n '1,220p' /Users/testuser/.codex/skills/.system/openai-docs/SKILL.md\"}"
    );
    let invocation = detect_skill_from_tool_input(
        "exec_command",
        &raw_args,
        &parse_jsonish_value(&raw_args),
        "2026-08-17T00:00:00Z",
    )
    .expect("system skill");
    assert_eq!(invocation.name, "openai-docs");
}

#[test]
fn test_detect_skill_from_exec_command_rejects_directory_followed_by_shell_args() {
    let parsed_args = json!({
        "cmd": "find /Users/testuser/.codex/skills/atc-cli -maxdepth 2 -name atc-lock.json -print"
    });
    assert!(detect_skill_from_tool_input(
        "exec_command",
        &Value::Null,
        &parsed_args,
        "2026-08-17T00:00:00Z"
    )
    .is_none());
}

#[test]
fn test_extract_skill_name_from_command_text_skips_invalid_match_and_finds_valid_path() {
    let command = "printf '.codex/skills/invalid name' && sed -n '1,20p' /Users/testuser/.codex/skills/analyze/SKILL.md";
    assert_eq!(
        extract_skill_name_from_command_text(command),
        Some("analyze".to_string())
    );
    assert_eq!(
        extract_skill_name_from_command_text(
            "node /workspace/.agents/skills/hangzhou-weather/scripts/query.js"
        ),
        Some("hangzhou-weather".to_string())
    );
}

#[test]
fn test_command_path_invocation_fallback_requires_skill_definition_file() {
    let parsed_args = json!({
        "cmd": "node /Users/test/.codex/skills/eve-beta/scripts/query.js"
    });
    assert!(detect_skill_from_tool_input(
        "exec_command",
        &Value::Null,
        &parsed_args,
        "2026-08-17T00:00:00Z"
    )
    .is_none());
}

#[test]
fn test_detect_skill_from_exec_command_no_match() {
    let parsed_args = json!({"cmd": "pwd"});
    assert!(detect_skill_from_tool_input(
        "exec_command",
        &Value::Null,
        &parsed_args,
        "2026-08-17T00:00:00Z"
    )
    .is_none());
}

#[test]
fn test_detect_skill_from_exec_command_wrong_tool() {
    let parsed_args =
        json!({"cmd": "sed -n '1,20p' /Users/testuser/.codex/skills/eve-beta/SKILL.md"});
    assert!(detect_skill_from_tool_input(
        "shell_command",
        &Value::Null,
        &parsed_args,
        "2026-08-17T00:00:00Z"
    )
    .is_none());
}

#[test]
fn test_custom_exec_static_wrapper_detects_agents_skill() {
    let raw_args = json!(
            "const r = await tools.exec_command({\n  cmd: \"sed -n '1,220p' /Users/test/.agents/skills/hangzhou-weather/SKILL.md\",\n  yield_time_ms: 10000\n}); text(r.output);"
        );
    let invocation = detect_skill_from_tool_input(
        "exec",
        &raw_args,
        &parse_jsonish_value(&raw_args),
        "2026-08-17T00:00:00Z",
    )
    .expect("custom exec skill");
    assert_eq!(invocation.name, "hangzhou-weather");
    assert_eq!(
        invocation.definition_path.as_deref(),
        Some("/Users/test/.agents/skills/hangzhou-weather/SKILL.md")
    );
}

#[test]
fn test_custom_exec_rejects_dynamic_or_commented_wrappers() {
    for source in [
            "const cmd = \"sed /Users/test/.agents/skills/weather/SKILL.md\"; tools.exec_command({cmd});",
            "tools.exec_command({cmd: `sed /Users/test/.agents/skills/weather/SKILL.md`});",
            "tools.exec_command({cmd: \"sed \" + path});",
            "tools.exec_command({[\"cmd\"]: \"sed /Users/test/.agents/skills/weather/SKILL.md\"});",
            "const run = tools.exec_command; run({cmd: \"sed /Users/test/.agents/skills/weather/SKILL.md\"});",
            "mytools.exec_command({cmd: \"sed /Users/test/.agents/skills/weather/SKILL.md\"});",
            "// tools.exec_command({cmd: \"sed /Users/test/.agents/skills/weather/SKILL.md\"});",
            "tools.exec_command({cmd: \"# /Users/test/.agents/skills/weather/SKILL.md\"});",
            "tools.exec_command({cmd: \"sed /Users/test/.agents/skills/one/SKILL.md /Users/test/.agents/skills/two/SKILL.md\"});",
        ] {
            let raw_args = json!(source);
            assert!(detect_skill_from_tool_input(
                "exec",
                &raw_args,
                &parse_jsonish_value(&raw_args),
                "2026-08-17T00:00:00Z"
            )
            .is_none(), "unexpected match for {source}");
        }
}

#[test]
fn test_detect_skill_from_user_message_simple() {
    assert_eq!(
        detect_skill_from_user_message("[$openai-docs](SKILL.md) 你支持哪些模型?"),
        Some("openai-docs".to_string())
    );
}

#[test]
fn test_detect_skill_from_user_message_full_path() {
    assert_eq!(
        detect_skill_from_user_message(
            "[$openai-docs](/Users/testuser/.codex/skills/.system/openai-docs/SKILL.md) query"
        ),
        Some("openai-docs".to_string())
    );
}

#[test]
fn test_detect_skill_from_user_message_no_match() {
    assert_eq!(detect_skill_from_user_message("hello world"), None);
}

#[test]
fn test_detect_skill_from_user_message_normal_link() {
    // Normal markdown link without $ prefix should not match
    assert_eq!(detect_skill_from_user_message("[readme](README.md)"), None);
}

#[test]
fn test_detect_skill_from_skill_tag() {
    let text = "<skill><name>openai-docs</name><content>some content</content></skill>";
    assert_eq!(
        detect_skill_from_skill_tag(text),
        Some("openai-docs".to_string())
    );
}

#[test]
fn test_detect_skill_from_skill_tag_no_match() {
    assert_eq!(detect_skill_from_skill_tag("just normal text"), None);
}

#[test]
fn test_detect_skill_from_skill_tag_empty_name() {
    assert_eq!(
        detect_skill_from_skill_tag("<skill><name></name></skill>"),
        None
    );
}

// --- build_turns integration test with skill detection ---

#[test]
fn test_build_turns_detects_skill_from_user_message() {
    let lines = vec![
        json!({"timestamp":"2026-04-04T11:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-04-04T11:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"[$openai-docs](SKILL.md) 你支持哪些模型?\n"}}),
        json!({"timestamp":"2026-04-04T11:00:00.200Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"pwd\"}","call_id":"call-1"}}),
        json!({"timestamp":"2026-04-04T11:00:00.210Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-1","output":"/tmp\n"}}),
        json!({"timestamp":"2026-04-04T11:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].skill_name, Some("openai-docs".to_string()));
    assert_eq!(turns[0].skill_invocations.len(), 1);
    assert_eq!(
        turns[0].skill_invocations[0].source,
        SkillInvocationSource::LegacyMarkdown
    );
}

#[test]
fn test_build_turns_detects_skill_from_exec_command() {
    let lines = vec![
        json!({"timestamp":"2026-04-04T11:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-04-04T11:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"run eve query\n"}}),
        json!({"timestamp":"2026-04-04T11:00:00.200Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"sed -n '1,80p' /Users/testuser/.codex/skills/eve-beta/SKILL.md\"}","call_id":"call-1"}}),
        json!({"timestamp":"2026-04-04T11:00:00.210Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-1","output":"result\n"}}),
        json!({"timestamp":"2026-04-04T11:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].skill_name, Some("eve-beta".to_string()));
    assert_eq!(turns[0].skill_invocations.len(), 1);
    assert_eq!(
        turns[0].skill_invocations[0].source,
        SkillInvocationSource::CommandPathFallback
    );
}

#[test]
fn test_build_turns_does_not_activate_skill_from_assistant_skill_tag() {
    let lines = vec![
        json!({"timestamp":"2026-04-04T11:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-04-04T11:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"hello\n"}}),
        json!({"timestamp":"2026-04-04T11:00:00.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"<skill><name>imagegen</name><content>generate image</content></skill>"}}),
        json!({"timestamp":"2026-04-04T11:00:00.300Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"echo hi\"}","call_id":"call-1"}}),
        json!({"timestamp":"2026-04-04T11:00:00.310Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-1","output":"hi\n"}}),
        json!({"timestamp":"2026-04-04T11:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].skill_name, None);
    assert!(turns[0].skill_invocations.is_empty());
}

#[test]
fn test_build_turns_no_skill_for_normal_turn() {
    let lines = vec![
        json!({"timestamp":"2026-04-04T11:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-04-04T11:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"run diagnostics\n"}}),
        json!({"timestamp":"2026-04-04T11:00:00.200Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"pwd\"}","call_id":"call-1"}}),
        json!({"timestamp":"2026-04-04T11:00:00.210Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-1","output":"/tmp\n"}}),
        json!({"timestamp":"2026-04-04T11:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].skill_name, None);
    assert!(turns[0].skill_invocations.is_empty());
}

fn omx_route_message(keyword: &str, workflow: &str) -> String {
    format!(
            "OMX native UserPromptSubmit detected workflow keyword \"${keyword}\" -> {workflow}. Follow AGENTS.md routing and preserve workflow transition and planning-safety rules."
        )
}

#[test]
fn test_v2_text_element_creates_validated_skill_invocation() {
    let lines = vec![
        json!({"timestamp":"2026-07-16T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-v2"}}),
        json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"$analyze why","text_elements":[{"byte_range":{"start":0,"end":8},"placeholder":"$analyze"}]}}),
        json!({"timestamp":"2026-07-16T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-v2","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    let invocation = &turns[0].skill_invocations[0];
    assert_eq!(invocation.name, "analyze");
    assert_eq!(invocation.source, SkillInvocationSource::CodexTextElement);
    assert_eq!(invocation.byte_start, Some(0));
    assert_eq!(invocation.byte_end, Some(8));
    assert_eq!(invocation.ordinal, 0);
}

#[test]
fn test_v2_text_element_utf8_ranges_are_safe_and_exact() {
    let valid = parse_skill_invocations_from_text_elements(
        "前$analyze后",
        Some(&vec![json!({
            "byte_range":{"start":3,"end":11},
            "placeholder":"$analyze"
        })]),
        "2026-07-16T08:00:00Z",
    );
    assert_eq!(valid.len(), 1);

    let invalid = parse_skill_invocations_from_text_elements(
        "前$analyze后",
        Some(&vec![
            json!({"byte_range":{"start":4,"end":11},"placeholder":"$analyze"}),
            json!({"byte_range":{"start":3,"end":99},"placeholder":"$analyze"}),
            json!({"byte_range":{"start":3,"end":11},"placeholder":"$another"}),
        ]),
        "2026-07-16T08:00:00Z",
    );
    assert!(invalid.is_empty());
}

#[test]
fn test_v2_text_element_rejects_invalid_skill_name() {
    let invocations = parse_skill_invocations_from_text_elements(
        "$bad/name",
        Some(&vec![json!({
            "byte_range":{"start":0,"end":9},
            "placeholder":"$bad/name"
        })]),
        "2026-07-16T08:00:00Z",
    );

    assert!(invocations.is_empty());
}

#[test]
fn test_item_completed_user_message_rejects_metadata_mismatch_and_invalid_range() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-mismatch"}}),
        json!({"timestamp":"2026-08-17T08:00:00.100Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"UserMessage","content":[
            {"type":"text","text":"$ralplan plan","text_elements":[{"byte_range":{"start":0,"end":8},"placeholder":"$ralplan"}]},
            {"type":"skill","name":"ultragoal","path":"/Users/test/.codex/skills/ultragoal/SKILL.md"}
        ]}}}),
        json!({"timestamp":"2026-08-17T08:00:00.200Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"UserMessage","content":[
            {"type":"text","text":"$ralplan plan","text_elements":[{"byte_range":{"start":1,"end":8},"placeholder":"$ralplan"}]},
            {"type":"skill","name":"ralplan","path":"/Users/test/.codex/skills/ralplan/SKILL.md"}
        ]}}}),
        json!({"timestamp":"2026-08-17T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-mismatch","last_agent_message":"done"}}),
    ];

    let turn = &build_turns(&lines)[0];
    assert!(turn.skill_invocations.is_empty());
    assert_eq!(turn.skill_name, None);
}

#[test]
fn test_item_completed_user_message_dedupes_mirrors_and_derives_skill_name() {
    let user_item = json!({"type":"item_completed","item":{"type":"UserMessage","content":[
        {"type":"text","text":"$hangzhou-weather 查看天气","text_elements":[{"byte_range":{"start":0,"end":17},"placeholder":"$hangzhou-weather"}]},
        {"type":"skill","name":"hangzhou-weather","path":"/Users/test/.agents/skills/hangzhou-weather/SKILL.md"}
    ]}});
    let lines = vec![
        json!({"timestamp":"2026-08-17T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-mirrors"}}),
        json!({"timestamp":"2026-08-17T08:00:00.100Z","type":"event_msg","payload":user_item.clone()}),
        json!({"timestamp":"2026-08-17T08:00:00.110Z","type":"event_msg","payload":user_item}),
        json!({"timestamp":"2026-08-17T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-mirrors","last_agent_message":"done"}}),
    ];

    let turn = &build_turns(&lines)[0];
    assert_eq!(turn.skill_invocations.len(), 1);
    assert_eq!(turn.skill_name.as_deref(), Some("hangzhou-weather"));
    assert_eq!(
        turn.skill_invocations[0].definition_path.as_deref(),
        Some("/Users/test/.agents/skills/hangzhou-weather/SKILL.md")
    );
}

#[test]
fn test_delayed_item_completed_user_message_merges_into_finalized_turn() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-first"}}),
        json!({"timestamp":"2026-08-17T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-first","last_agent_message":"done"}}),
        json!({"timestamp":"2026-08-17T08:00:01.100Z","type":"event_msg","payload":{"type":"item_completed","turn_id":"turn-first","item":{"type":"UserMessage","content":[
            {"type":"text","text":"$ralplan plan","text_elements":[{"byte_range":{"start":0,"end":8},"placeholder":"$ralplan"}]},
            {"type":"skill","name":"ralplan","path":"/Users/test/.codex/skills/ralplan/SKILL.md"}
        ]}}}),
        json!({"timestamp":"2026-08-17T08:00:02.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-second"}}),
        json!({"timestamp":"2026-08-17T08:00:03.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-second","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 2);
    assert_eq!(turns[0].turn_id, "turn-first");
    assert_eq!(turns[0].skill_invocations.len(), 1);
    assert_eq!(turns[0].skill_name.as_deref(), Some("ralplan"));
    assert_eq!(turns[1].turn_id, "turn-second");
    assert!(turns[1].skill_invocations.is_empty());
}

#[test]
fn test_delayed_item_completed_user_message_does_not_leak_into_next_turn() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-first"}}),
        json!({"timestamp":"2026-08-17T08:00:00.100Z","type":"event_msg","payload":{"type":"item_completed","turn_id":"turn-first","item":{"type":"UserMessage","content":[
            {"type":"text","text":"$ralplan plan","text_elements":[{"byte_range":{"start":0,"end":8},"placeholder":"$ralplan"}]},
            {"type":"skill","name":"ralplan","path":"/Users/test/.codex/skills/ralplan/SKILL.md"}
        ]}}}),
        json!({"timestamp":"2026-08-17T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-first","last_agent_message":"done"}}),
        json!({"timestamp":"2026-08-17T08:00:02.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-second"}}),
        json!({"timestamp":"2026-08-17T08:00:02.100Z","type":"event_msg","payload":{"type":"item_completed","turn_id":"turn-first","item":{"type":"UserMessage","content":[
            {"type":"text","text":"$ultragoal","text_elements":[{"byte_range":{"start":0,"end":10},"placeholder":"$ultragoal"}]},
            {"type":"skill","name":"ultragoal","path":"/Users/test/.codex/skills/ultragoal/SKILL.md"}
        ]}}}),
        json!({"timestamp":"2026-08-17T08:00:03.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-second","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 2);
    assert_eq!(turns[0].skill_name.as_deref(), Some("ralplan"));
    assert!(turns[1].skill_invocations.is_empty());
    assert_eq!(turns[1].skill_name, None);
}

#[test]
fn test_item_completed_structured_skill_wins_over_all_lower_priority_evidence() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-ralplan"}}),
        json!({"timestamp":"2026-08-17T08:00:00.010Z","type":"turn_context","payload":{"turn_id":"turn-ralplan"}}),
        json!({"timestamp":"2026-08-17T08:00:00.050Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"$ralplan plan"}],"internal_chat_message_metadata_passthrough":{"turn_id":"turn-ralplan"}}}),
        json!({"timestamp":"2026-08-17T08:00:00.100Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"UserMessage","content":[
            {"type":"text","text":"$ralplan plan","text_elements":[{"byte_range":{"start":0,"end":8},"placeholder":"$ralplan"}]},
            {"type":"skill","name":"ralplan","path":"/Users/test/.codex/skills/ralplan/SKILL.md"}
        ]}}}),
        json!({"timestamp":"2026-08-17T08:00:00.110Z","type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":omx_route_message("ralplan", "ralplan")}]}}),
        json!({"timestamp":"2026-08-17T08:00:00.120Z","type":"response_item","payload":{"type":"custom_tool_call","name":"exec","input":"tools.exec_command({cmd: \"sed -n '1,220p' /Users/test/.codex/skills/plan/SKILL.md\"});","call_id":"call-read-plan"}}),
        json!({"timestamp":"2026-08-17T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-ralplan","last_agent_message":"done"}}),
    ];

    let turn = &build_turns(&lines)[0];
    assert_eq!(turn.skill_invocations.len(), 1);
    let invocation = &turn.skill_invocations[0];
    assert_eq!(invocation.name, "ralplan");
    assert_eq!(invocation.source, SkillInvocationSource::CodexTextElement);
    assert_eq!(invocation.resolved_workflow.as_deref(), Some("ralplan"));
    assert_eq!(
        invocation.definition_path.as_deref(),
        Some("/Users/test/.codex/skills/ralplan/SKILL.md")
    );
    assert_eq!(turn.skill_name.as_deref(), Some("ralplan"));
}

#[test]
fn test_item_completed_multiple_skills_preserve_placeholder_order() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-multi-skill"}}),
        json!({"timestamp":"2026-08-17T08:00:00.100Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"UserMessage","content":[
            {"type":"text","text":"$ralplan then $ultragoal","text_elements":[
                {"byte_range":{"start":14,"end":24},"placeholder":"$ultragoal"},
                {"byte_range":{"start":0,"end":8},"placeholder":"$ralplan"}
            ]},
            {"type":"skill","name":"ralplan","path":"/Users/test/.codex/skills/ralplan/SKILL.md"},
            {"type":"skill","name":"ultragoal","path":"/Users/test/.codex/skills/ultragoal/SKILL.md"}
        ]}}}),
        json!({"timestamp":"2026-08-17T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-multi-skill","last_agent_message":"done"}}),
    ];

    let invocations = &build_turns(&lines)[0].skill_invocations;
    assert_eq!(invocations.len(), 2);
    assert_eq!(invocations[0].name, "ralplan");
    assert_eq!(invocations[0].ordinal, 0);
    assert_eq!(invocations[1].name, "ultragoal");
    assert_eq!(invocations[1].ordinal, 1);
}

#[test]
fn test_v2_invocations_sort_by_byte_range_and_deduplicate() {
    let lines = vec![
        json!({"timestamp":"2026-07-16T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-v2"}}),
        json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{
            "type":"user_message",
            "message":"$foo xx $bar",
            "text_elements":[
                {"byte_range":{"start":8,"end":12},"placeholder":"$bar"},
                {"byte_range":{"start":0,"end":4},"placeholder":"$foo"},
                {"byte_range":{"start":0,"end":4},"placeholder":"$foo"}
            ]
        }}),
        json!({"timestamp":"2026-07-16T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-v2","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    let invocations = &turns[0].skill_invocations;
    assert_eq!(invocations.len(), 2);
    assert_eq!(invocations[0].name, "foo");
    assert_eq!(invocations[0].original_text_element_index, Some(1));
    assert_eq!(invocations[0].ordinal, 0);
    assert_eq!(invocations[1].name, "bar");
    assert_eq!(invocations[1].ordinal, 1);
}

#[test]
fn test_builder_local_dedupe_survives_late_native_turn_id() {
    let lines = vec![
        json!({"timestamp":"2026-07-16T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started"}}),
        json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{
            "type":"user_message",
            "message":"$analyze why",
            "text_elements":[
                {"byte_range":{"start":0,"end":8},"placeholder":"$analyze"},
                {"byte_range":{"start":0,"end":8},"placeholder":"$analyze"}
            ]
        }}),
        json!({"timestamp":"2026-07-16T08:00:00.110Z","type":"response_item","payload":{
            "type":"message",
            "role":"user",
            "content":[{"type":"input_text","text":"ordinary mirrored user content"}],
            "internal_chat_message_metadata_passthrough":{"turn_id":"native-turn-id"}
        }}),
        json!({"timestamp":"2026-07-16T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"native-turn-id","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns[0].turn_id, "native-turn-id");
    assert_eq!(turns[0].skill_invocations.len(), 1);
}

#[test]
fn test_literal_dollar_skill_without_text_element_does_not_activate() {
    let lines = vec![
        json!({"timestamp":"2026-07-16T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-v2"}}),
        json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"write `$analyze` literally","text_elements":[]}}),
        json!({"timestamp":"2026-07-16T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-v2","last_agent_message":"done"}}),
    ];

    assert!(build_turns(&lines)[0].skill_invocations.is_empty());
}

#[test]
fn test_legacy_markdown_is_suppressed_when_v2_evidence_exists() {
    let lines = vec![
        json!({"timestamp":"2026-07-16T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-v2"}}),
        json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"[$legacy](SKILL.md)"}}),
        json!({"timestamp":"2026-07-16T08:00:00.110Z","type":"event_msg","payload":{"type":"user_message","message":"$analyze why","text_elements":[{"byte_range":{"start":0,"end":8},"placeholder":"$analyze"}]}}),
        json!({"timestamp":"2026-07-16T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-v2","last_agent_message":"done"}}),
    ];

    let invocations = &build_turns(&lines)[0].skill_invocations;
    assert_eq!(invocations.len(), 1);
    assert_eq!(invocations[0].name, "analyze");
    assert_eq!(
        invocations[0].source,
        SkillInvocationSource::CodexTextElement
    );
}

#[test]
fn test_omx_route_corroborates_explicit_invocation_without_duplicate() {
    let lines = vec![
        json!({"timestamp":"2026-07-16T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-v2"}}),
        json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"$analyze why","text_elements":[{"byte_range":{"start":0,"end":8},"placeholder":"$analyze"}]}}),
        json!({"timestamp":"2026-07-16T08:00:00.110Z","type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":omx_route_message("analyze", "analyze")}]}}),
        json!({"timestamp":"2026-07-16T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-v2","last_agent_message":"done"}}),
    ];

    let invocations = &build_turns(&lines)[0].skill_invocations;
    assert_eq!(invocations.len(), 1);
    assert_eq!(invocations[0].resolved_workflow.as_deref(), Some("analyze"));
    assert_eq!(
        invocations[0].source,
        SkillInvocationSource::CodexTextElement
    );
}

#[test]
fn test_explicit_invocation_suppresses_unmatched_omx_route_for_entire_turn() {
    let lines = vec![
        json!({"timestamp":"2026-07-16T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-v2"}}),
        json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"$analyze why","text_elements":[{"byte_range":{"start":0,"end":8},"placeholder":"$analyze"}]}}),
        json!({"timestamp":"2026-07-16T08:00:00.110Z","type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":omx_route_message("ultragoal", "ultragoal")}]}}),
        json!({"timestamp":"2026-07-16T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-v2","last_agent_message":"done"}}),
    ];

    let invocations = &build_turns(&lines)[0].skill_invocations;
    assert_eq!(invocations.len(), 1);
    assert_eq!(invocations[0].name, "analyze");
    assert_eq!(
        invocations[0].source,
        SkillInvocationSource::CodexTextElement
    );
}

#[test]
fn test_omx_route_without_placeholder_creates_implicit_invocation() {
    let lines = vec![
        json!({"timestamp":"2026-07-16T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-v2"}}),
        json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"please investigate","text_elements":[]}}),
        json!({"timestamp":"2026-07-16T08:00:00.110Z","type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":omx_route_message("analyze", "analyze")}]}}),
        json!({"timestamp":"2026-07-16T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-v2","last_agent_message":"done"}}),
    ];

    let invocations = &build_turns(&lines)[0].skill_invocations;
    assert_eq!(invocations.len(), 1);
    assert_eq!(
        invocations[0].source,
        SkillInvocationSource::OmxUserPromptSubmit
    );
}

#[test]
fn test_non_exact_omx_route_is_ignored() {
    assert_eq!(
        parse_omx_user_prompt_submit_route(
            "prefix OMX native UserPromptSubmit detected workflow keyword \"$analyze\" -> analyze."
        ),
        None
    );
    assert_eq!(
        parse_omx_user_prompt_submit_route(
            "OMX native UserPromptSubmit detected workflow keyword \"$analyze\" -> analyze."
        ),
        None
    );
    assert_eq!(
        parse_omx_user_prompt_submit_route(&omx_route_message("../analyze", "analyze")),
        None
    );
    assert_eq!(
        parse_omx_user_prompt_submit_route(&omx_route_message("analyze", "../analyze")),
        None
    );
    assert_eq!(
        parse_omx_user_prompt_submit_route(&omx_route_message("foo.bar", "foo.bar")),
        Some(("$foo.bar".to_string(), "foo.bar".to_string()))
    );
}

#[test]
fn test_skill_definitions_are_metadata_only_and_match_by_name() {
    let lines = vec![
        json!({"timestamp":"2026-07-16T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-v2"}}),
        json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"$analyze why","text_elements":[{"byte_range":{"start":0,"end":8},"placeholder":"$analyze"}]}}),
        json!({"timestamp":"2026-07-16T08:00:00.110Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"<skill>\n<name>omx-setup</name>\n<path>/skills/omx-setup/SKILL.md</path>\n</skill>"}]}}),
        json!({"timestamp":"2026-07-16T08:00:00.120Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"<skill>\n<name>analyze</name>\n<path>/skills/analyze/SKILL.md</path>\n</skill>"}]}}),
        json!({"timestamp":"2026-07-16T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-v2","last_agent_message":"done"}}),
    ];

    let invocations = &build_turns(&lines)[0].skill_invocations;
    assert_eq!(invocations.len(), 1);
    assert_eq!(
        invocations[0].definition_path.as_deref(),
        Some("/skills/analyze/SKILL.md")
    );

    let definition_only = vec![
        json!({"timestamp":"2026-07-16T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-definition-only"}}),
        json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello","text_elements":[]}}),
        json!({"timestamp":"2026-07-16T08:00:00.120Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"<skill>\n<name>analyze</name>\n<path>/skills/analyze/SKILL.md</path>\n</skill>"}]}}),
        json!({"timestamp":"2026-07-16T08:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-definition-only","last_agent_message":"done"}}),
    ];
    assert!(build_turns(&definition_only)[0]
        .skill_invocations
        .is_empty());
}

#[test]
fn test_session_meta_base_instructions_are_attached_to_every_turn() {
    let turns = build_turns(&[
        json!({"timestamp":"2026-07-16T08:00:00Z","type":"session_meta","payload":{"id":"session","base_instructions":{"text":"You are Codex."}}}),
        json!({"timestamp":"2026-07-16T08:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"one"}}),
        json!({"timestamp":"2026-07-16T08:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"one"}}),
        json!({"timestamp":"2026-07-16T08:00:02Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"one","last_agent_message":"done"}}),
        json!({"timestamp":"2026-07-16T08:00:03Z","type":"event_msg","payload":{"type":"task_started","turn_id":"two"}}),
        json!({"timestamp":"2026-07-16T08:00:03.100Z","type":"event_msg","payload":{"type":"user_message","message":"two"}}),
        json!({"timestamp":"2026-07-16T08:00:04Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"two","last_agent_message":"done"}}),
    ]);

    assert_eq!(turns.len(), 2);
    assert!(turns
        .iter()
        .all(|turn| turn.system_instructions == "You are Codex."));
}

#[test]
fn test_terminal_state_and_completion_timestamp_are_explicit() {
    let completed = build_turns(&[
        json!({"timestamp":"2026-07-16T08:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"completed"}}),
        json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}),
        json!({"timestamp":"2026-07-16T08:00:01Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"completed","last_agent_message":"done"}}),
    ]);
    assert_eq!(completed[0].terminal_state, TurnTerminalState::Completed);
    assert_eq!(completed[0].completion_timestamp, "2026-07-16T08:00:01Z");

    let aborted = build_turns(&[
        json!({"timestamp":"2026-07-16T08:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"aborted"}}),
        json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}),
        json!({"timestamp":"2026-07-16T08:00:01Z","type":"event_msg","payload":{"type":"turn_aborted","turn_id":"aborted"}}),
    ]);
    assert_eq!(aborted[0].terminal_state, TurnTerminalState::Aborted);
    assert_eq!(aborted[0].completion_timestamp, "2026-07-16T08:00:01Z");

    let open = build_turns(&[
        json!({"timestamp":"2026-07-16T08:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"open"}}),
        json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}),
    ]);
    assert_eq!(open[0].terminal_state, TurnTerminalState::Open);
    assert!(open[0].completion_timestamp.is_empty());
}

#[test]
fn test_normalize_tool_call_subagent_preserves_name() {
    // V1 subagent tools should preserve their original name (not renamed to "Task")
    assert_eq!(
        normalize_tool_call("spawn_agent", &json!({"message": "hello"})),
        (
            "spawn_agent".to_string(),
            json!({"name": "spawn_agent", "message": "hello"})
        )
    );
    assert_eq!(
        normalize_tool_call("wait_agent", &json!({"targets": []})),
        (
            "wait_agent".to_string(),
            json!({"name": "wait_agent", "targets": []})
        )
    );
    assert_eq!(
        normalize_tool_call("send_input", &json!({"target": "/root/child"})),
        (
            "send_input".to_string(),
            json!({"name": "send_input", "target": "/root/child"})
        )
    );
    assert_eq!(
        normalize_tool_call("resume_agent", &json!({})),
        ("resume_agent".to_string(), json!({"name": "resume_agent"}))
    );
    assert_eq!(
        normalize_tool_call("close_agent", &json!({})),
        ("close_agent".to_string(), json!({"name": "close_agent"}))
    );
    // V2 subagent tools
    assert_eq!(
        normalize_tool_call("followup_task", &json!({})),
        (
            "followup_task".to_string(),
            json!({"name": "followup_task"})
        )
    );
    assert_eq!(
        normalize_tool_call("send_message", &json!({})),
        ("send_message".to_string(), json!({"name": "send_message"}))
    );
    assert_eq!(
        normalize_tool_call("list_agents", &json!({})),
        ("list_agents".to_string(), json!({"name": "list_agents"}))
    );
}

#[test]
fn test_tool_calls_track_assistant_index() {
    // Simulate interleaved agent_message and function_call events:
    // agent_message[0] → tool A, tool B → agent_message[1] → tool C → agent_message[2](final)
    let lines = vec![
        json!({"timestamp":"2026-04-04T12:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-idx"}}),
        json!({"timestamp":"2026-04-04T12:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"multi-gen test"}}),
        // First generation
        json!({"timestamp":"2026-04-04T12:00:00.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"Phase one"}}),
        json!({"timestamp":"2026-04-04T12:00:00.300Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"pwd\"}","call_id":"call-a"}}),
        json!({"timestamp":"2026-04-04T12:00:00.310Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-a","output":"/tmp\n"}}),
        json!({"timestamp":"2026-04-04T12:00:00.320Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"ls\"}","call_id":"call-b"}}),
        json!({"timestamp":"2026-04-04T12:00:00.330Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-b","output":"file.txt\n"}}),
        // Second generation
        json!({"timestamp":"2026-04-04T12:00:00.400Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"Phase two"}}),
        json!({"timestamp":"2026-04-04T12:00:00.500Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"cat file.txt\"}","call_id":"call-c"}}),
        json!({"timestamp":"2026-04-04T12:00:00.510Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-c","output":"hello\n"}}),
        // Final answer
        json!({"timestamp":"2026-04-04T12:00:00.600Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-idx","last_agent_message":"Done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    let turn = &turns[0];
    assert_eq!(turn.assistants.len(), 4); // commentary/tool, tool-only, commentary/tool, final
    assert_eq!(turn.tool_calls.len(), 3);

    // Every callback following a completed callback belongs to a new invocation.
    assert_eq!(
        turn.tool_calls[0].assistant_index, 0,
        "tool A should be under first generation"
    );
    assert_eq!(
        turn.tool_calls[1].assistant_index, 1,
        "tool B should be under its own tool-only generation"
    );
    // Tool C belongs to the second generation (assistant_index 1)
    assert_eq!(
        turn.tool_calls[2].assistant_index, 2,
        "tool C should be under the third generation"
    );
}

#[test]
fn test_tool_calls_remap_after_timestampless_native_usage_prefix() {
    for superseded in [false, true] {
        let mut lines = vec![
            json!({"timestamp":"2026-09-06T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-remap"}}),
            json!({"timestamp":"2026-09-06T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"run one tool"}}),
            json!({"type":"token_usage_record","payload":{"turn_id":"turn-remap","response_id":"resp-prefix","usage":{"input_tokens":10,"output_tokens":1,"total_tokens":11}}}),
            json!({"timestamp":"2026-09-06T00:00:01.000Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"pwd\"}","call_id":"call-remap"}}),
            json!({"timestamp":"2026-09-06T00:00:01.500Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-remap","output":"/tmp\n"}}),
            json!({"timestamp":"2026-09-06T00:00:02.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-remap","last_agent_message":"done"}}),
        ];

        lines.insert(5, json!({"timestamp":"2026-09-06T00:00:01.600Z","type":"token_usage_record","payload":{"turn_id":"turn-remap","response_id":"resp-tool","usage":{"input_tokens":20,"output_tokens":2,"total_tokens":22}}}));
        if superseded {
            lines.pop();
            lines.push(json!({"timestamp":"2026-09-06T00:00:02Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}]}}));
            lines.push(json!({"timestamp":"2026-09-06T00:00:03Z","type":"event_msg","payload":{"type":"task_started","turn_id":"next"}}));
            lines.push(json!({"timestamp":"2026-09-06T00:00:03Z","type":"event_msg","payload":{"type":"user_message","message":"next prompt"}}));
        }
        let turns = build_turns(&lines);
        assert_eq!(turns.len(), 1 + usize::from(superseded));
        let turn = &turns[0];
        assert_eq!(turn.assistants.len(), 2);
        assert!(turn.assistants[0].text.is_empty());
        assert_eq!(turn.assistants[1].text, "done");
        assert_eq!(turn.tool_calls.len(), 1);
        assert_eq!(turn.tool_calls[0].call_id, "call-remap");
        assert_eq!(
        turn.tool_calls[0].assistant_index, 0,
        "tool should point at the compacted assistant index after a timestampless native usage prefix is skipped"
    );
    }
}

// --- CollabAgent event parsing tests ---

#[test]
fn test_tool_without_derived_assistant_is_retained_unassociated() {
    let lines = vec![
        json!({"type":"event_msg","payload":{"type":"task_started","turn_id":"unassociated"}}),
        json!({"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{}","call_id":"missing-time"}}),
        json!({"type":"response_item","payload":{"type":"function_call_output","call_id":"missing-time","output":"retained output"}}),
        json!({"type":"token_usage_record","payload":{"turn_id":"unassociated","response_id":"no-time","usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}),
        json!({"timestamp":"2026-09-06T00:00:01Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{}","call_id":"timed"}}),
        json!({"timestamp":"2026-09-06T00:00:02Z","type":"response_item","payload":{"type":"function_call_output","call_id":"timed","output":"timed output"}}),
        json!({"timestamp":"2026-09-06T00:00:03Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"unassociated"}}),
    ];
    let turns = build_turns(&lines);
    let turn = &turns[0];
    assert_eq!(turn.assistants.len(), 1);
    assert_eq!(turn.tool_calls.len(), 2);
    assert!(turn.tool_calls[0].assistant_index >= turn.assistants.len());
    assert_eq!(turn.tool_calls[0].output, json!("retained output"));
    assert_eq!(turn.tool_calls[1].assistant_index, 0);
    assert_eq!(turn.tool_calls[1].output, json!("timed output"));
}

#[test]
fn test_parse_native_collab_spawn_and_wait_fixture() {
    let lines =
        include_str!("../../../tests/fixtures/codex_collab_tool_call_same_turn_parent.jsonl")
            .lines()
            .map(|line| serde_json::from_str::<Value>(line).unwrap())
            .collect::<Vec<_>>();
    let turns = build_turns(&lines);
    let evidence = collect_subagent_invocation_evidence(&turns);
    let spawn = evidence
        .iter()
        .find(|value| value.invocation_id == "collab-spawn-1")
        .expect("native spawn evidence");
    assert_eq!(spawn.source, CollabInvocationSource::NativeToolCall);
    assert_eq!(spawn.tool_name, "spawn_agent");
    assert_eq!(spawn.sender_thread_id.as_deref(), Some("parent-collab"));
    assert_eq!(spawn.child_thread_id.as_deref(), Some("child-collab"));
    assert_eq!(spawn.nickname.as_deref(), Some("Archimedes"));
    assert_eq!(spawn.role.as_deref(), Some("executor"));
    assert_eq!(
        spawn.prompt.as_deref(),
        Some("Run one command and report its output.")
    );
    assert_eq!(spawn.model.as_deref(), Some("gpt-5.5"));
    assert_eq!(
        spawn.started_at_unix_nano.as_deref(),
        Some("1786934901875000000")
    );
    assert_eq!(
        spawn.completed_at_unix_nano.as_deref(),
        Some("1786934901998000000")
    );
    assert_eq!(spawn.state, CollabInvocationState::Pending);
    assert_eq!(spawn.reason, "terminal_pending");

    let wait = evidence
        .iter()
        .find(|value| value.invocation_id == "collab-wait-1")
        .expect("native wait evidence");
    assert_eq!(wait.child_thread_id.as_deref(), Some("child-collab"));
    assert_eq!(wait.state, CollabInvocationState::Completed);
    assert_eq!(wait.status, "completed");
    assert_eq!(wait.terminal_output.as_deref(), Some("done"));
    assert_eq!(wait.reason, "parsed");

    let activations = normalized_subagent_activations(&turns);
    assert_eq!(activations.len(), 1);
    assert_eq!(activations[0].activation_id, "collab-spawn-1");
    assert_eq!(
        activations[0].child_thread_id.as_deref(),
        Some("child-collab")
    );
    assert_eq!(activations[0].state, CollabInvocationState::Completed);
    assert_eq!(activations[0].terminal_output.as_deref(), Some("done"));
    assert_eq!(
        activations[0].completed_at_unix_nano.as_deref(),
        Some("1786934918055000000")
    );
    assert_eq!(activations[0].wait_invocation_ids, vec!["collab-wait-1"]);
}

#[test]
fn test_native_collab_wait_splits_terminal_state_per_receiver() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
        json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"wait"}}),
        json!({"timestamp":"2026-08-17T00:00:00.200Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-a","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-a"],"receiver_agents":[{"thread_id":"child-a","agent_nickname":"A","agent_role":"executor"}],"agents_states":{"child-a":"pending_init"}},"started_at_ms":200,"completed_at_ms":300}}),
        json!({"timestamp":"2026-08-17T00:00:00.400Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-b","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-b"],"receiver_agents":[{"thread_id":"child-b","agent_nickname":"B","agent_role":"reviewer"}],"agents_states":{"child-b":"pending_init"}},"started_at_ms":400,"completed_at_ms":500}}),
        json!({"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"wait-many","tool":"wait","status":"completed","sender_thread_id":"parent","receiver_thread_ids":["child-a","child-b"],"receiver_agents":[{"thread_id":"child-a","agent_nickname":"A","agent_role":"executor"},{"thread_id":"child-b","agent_nickname":"B","agent_role":"reviewer"}],"agents_states":{"child-a":{"completed":"output-a"},"child-b":{"error":"failure-b"}}},"started_at_ms":1000,"completed_at_ms":2000}}),
        json!({"timestamp":"2026-08-17T00:00:02Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    let mut evidence = collect_subagent_invocation_evidence(&turns)
        .into_iter()
        .filter(|value| value.invocation_id == "wait-many")
        .collect::<Vec<_>>();
    evidence.sort_by(|left, right| left.child_thread_id.cmp(&right.child_thread_id));

    assert_eq!(evidence.len(), 2);
    assert_eq!(evidence[0].child_thread_id.as_deref(), Some("child-a"));
    assert_eq!(evidence[0].state, CollabInvocationState::Completed);
    assert_eq!(evidence[0].terminal_output.as_deref(), Some("output-a"));
    assert_eq!(evidence[1].child_thread_id.as_deref(), Some("child-b"));
    assert_eq!(evidence[1].state, CollabInvocationState::Error);
    assert_eq!(evidence[1].terminal_output.as_deref(), Some("failure-b"));

    let mut activations = normalized_subagent_activations(&turns);
    activations.sort_by(|left, right| left.child_thread_id.cmp(&right.child_thread_id));
    assert_eq!(activations.len(), 2);
    assert_eq!(activations[0].activation_id, "spawn-a");
    assert_eq!(activations[0].state, CollabInvocationState::Completed);
    assert_eq!(activations[0].terminal_output.as_deref(), Some("output-a"));
    assert_eq!(activations[1].activation_id, "spawn-b");
    assert_eq!(activations[1].state, CollabInvocationState::Error);
    assert_eq!(activations[1].terminal_output.as_deref(), Some("failure-b"));
}

#[test]
fn test_native_collab_pending_and_malformed_evidence_fail_closed() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
        json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"collab"}}),
        json!({"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"wait-pending","tool":"wait","sender_thread_id":"parent","receiver_thread_ids":["child-a"],"agents_states":{"child-a":{"timed_out":true}}},"started_at_ms":1000,"completed_at_ms":2000}}),
        json!({"timestamp":"2026-08-17T00:00:02Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-duplicate","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-a","child-a"]},"started_at_ms":2000,"completed_at_ms":3000}}),
        json!({"timestamp":"2026-08-17T00:00:03Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-bad-time","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-b"]},"started_at_ms":4000,"completed_at_ms":3000}}),
        json!({"timestamp":"2026-08-17T00:00:04Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    let evidence = collect_subagent_invocation_evidence(&turns);
    let pending = evidence
        .iter()
        .find(|value| value.invocation_id == "wait-pending")
        .unwrap();
    assert_eq!(pending.state, CollabInvocationState::Pending);
    assert_eq!(pending.status, "timed_out");
    assert_eq!(pending.reason, "terminal_pending");
    let duplicate = evidence
        .iter()
        .find(|value| value.invocation_id == "spawn-duplicate")
        .unwrap();
    assert_eq!(duplicate.child_thread_id, None);
    assert_eq!(duplicate.state, CollabInvocationState::Malformed);
    assert_eq!(duplicate.reason, "identity_ambiguous");
    let bad_time = evidence
        .iter()
        .find(|value| value.invocation_id == "spawn-bad-time")
        .unwrap();
    assert_eq!(bad_time.child_thread_id.as_deref(), Some("child-b"));
    assert_eq!(bad_time.state, CollabInvocationState::Malformed);
    assert_eq!(bad_time.started_at_unix_nano, None);
    assert_eq!(bad_time.completed_at_unix_nano, None);
    assert_eq!(bad_time.reason, "malformed_timing");
}

#[test]
fn test_native_collab_interrupted_and_error_states_are_terminal() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
        json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"wait"}}),
        json!({"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"wait-terminal","tool":"wait","sender_thread_id":"parent","receiver_thread_ids":["child-a","child-b"],"agents_states":{"child-a":{"interrupted":"stopped"},"child-b":{"error":"boom"}}},"started_at_ms":1000,"completed_at_ms":2000}}),
        json!({"timestamp":"2026-08-17T00:00:02Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&lines);
    let evidence = collect_subagent_invocation_evidence(&turns);
    assert!(evidence.iter().any(|value| {
        value.child_thread_id.as_deref() == Some("child-a")
            && value.state == CollabInvocationState::Interrupted
            && value.terminal_output.as_deref() == Some("stopped")
    }));
    assert!(evidence.iter().any(|value| {
        value.child_thread_id.as_deref() == Some("child-b")
            && value.state == CollabInvocationState::Error
            && value.terminal_output.as_deref() == Some("boom")
    }));
}

#[test]
fn test_legacy_spawn_adapter_preserves_exact_identity_and_ignores_outer_exec_text() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
        json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"spawn"}}),
        json!({"timestamp":"2026-08-17T00:00:01Z","type":"response_item","payload":{"type":"function_call","name":"exec","call_id":"outer-exec","arguments":"{\"cmd\":\"const text = 'spawn_agent child-fake';\"}"}}),
        json!({"timestamp":"2026-08-17T00:00:01.100Z","type":"response_item","payload":{"type":"function_call_output","call_id":"outer-exec","output":"ok"}}),
        json!({"timestamp":"2026-08-17T00:00:02Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","call_id":"legacy-spawn","arguments":"{\"message\":\"work\"}"}}),
        json!({"timestamp":"2026-08-17T00:00:02.100Z","type":"response_item","payload":{"type":"function_call_output","call_id":"legacy-spawn","output":"{\"agent_id\":\"legacy-child\"}"}}),
        json!({"timestamp":"2026-08-17T00:00:03Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    let evidence = collect_subagent_invocation_evidence(&turns);

    assert_eq!(evidence.len(), 1);
    assert_eq!(evidence[0].source, CollabInvocationSource::LegacyToolCall);
    assert_eq!(evidence[0].invocation_id, "legacy-spawn");
    assert_eq!(evidence[0].child_thread_id.as_deref(), Some("legacy-child"));
    assert!(!evidence
        .iter()
        .any(|value| value.invocation_id == "outer-exec"));
}

#[test]
fn test_discovery_plan_marks_native_terminal_fixture_eligible() {
    let lines =
        include_str!("../../../tests/fixtures/codex_collab_tool_call_same_turn_parent.jsonl")
            .lines()
            .map(|line| serde_json::from_str::<Value>(line).unwrap())
            .collect::<Vec<_>>();
    let turns = build_turns(&lines);

    let plan = subagent_discovery_plan(&turns, 0);

    assert!(plan.observations.is_empty());
    assert_eq!(plan.decisions.len(), 1);
    let decision = &plan.decisions[0];
    assert_eq!(
        decision
            .child
            .as_ref()
            .map(|child| child.session_id.as_str()),
        Some("child-collab")
    );
    assert_eq!(decision.activation_id.as_deref(), Some("collab-spawn-1"));
    assert_eq!(decision.source, CollabInvocationSource::NativeToolCall);
    assert_eq!(decision.disposition, SubagentDiscoveryDisposition::Eligible);
    assert_eq!(decision.reason, "native_terminal");
    let activation = decision.activation.as_ref().expect("native activation");
    assert_eq!(activation.state, CollabInvocationState::Completed);
    assert_eq!(activation.terminal_output.as_deref(), Some("done"));
}

#[test]
fn test_discovery_plan_distinguishes_async_observation_from_deferred_wait() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
        json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"collab"}}),
        json!({"timestamp":"2026-08-17T00:00:00.200Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-async","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-async"],"agents_states":{"child-async":"pending_init"}},"started_at_ms":200,"completed_at_ms":300}}),
        json!({"timestamp":"2026-08-17T00:00:00.400Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-waited","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-waited"],"agents_states":{"child-waited":"pending_init"}},"started_at_ms":400,"completed_at_ms":500}}),
        json!({"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"wait-pending","tool":"wait","sender_thread_id":"parent","receiver_thread_ids":["child-waited"],"agents_states":{"child-waited":{"timed_out":true}}},"started_at_ms":1000,"completed_at_ms":2000}}),
        json!({"timestamp":"2026-08-17T00:00:02Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&lines);

    let plan = subagent_discovery_plan(&turns, 0);

    assert!(plan.observations.is_empty());
    assert_eq!(plan.decisions.len(), 2);
    let mut decisions = plan.decisions;
    decisions.sort_by_key(|decision| {
        decision
            .child
            .as_ref()
            .map(|child| child.session_id.clone())
            .unwrap_or_default()
    });
    for decision in &decisions {
        assert_eq!(decision.source, CollabInvocationSource::NativeToolCall);
        assert_eq!(decision.disposition, SubagentDiscoveryDisposition::Deferred);
        assert_eq!(decision.reason, "native_terminal_pending");
    }
    assert_eq!(
        decisions[0]
            .child
            .as_ref()
            .map(|child| child.session_id.as_str()),
        Some("child-async")
    );
    assert_eq!(decisions[0].activation_id.as_deref(), Some("spawn-async"));
    assert_eq!(
        decisions[1]
            .child
            .as_ref()
            .map(|child| child.session_id.as_str()),
        Some("child-waited")
    );
    assert_eq!(decisions[1].activation_id.as_deref(), Some("spawn-waited"));
}

#[test]
fn test_discovery_plan_keeps_multi_child_terminal_payloads_isolated() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
        json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"wait"}}),
        json!({"timestamp":"2026-08-17T00:00:00.200Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-a","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-a"],"receiver_agents":[{"thread_id":"child-a","agent_nickname":"A","agent_role":"executor"}],"agents_states":{"child-a":"pending_init"}},"started_at_ms":200,"completed_at_ms":300}}),
        json!({"timestamp":"2026-08-17T00:00:00.400Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-b","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-b"],"receiver_agents":[{"thread_id":"child-b","agent_nickname":"B","agent_role":"reviewer"}],"agents_states":{"child-b":"pending_init"}},"started_at_ms":400,"completed_at_ms":500}}),
        json!({"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"wait-many","tool":"wait","sender_thread_id":"parent","receiver_thread_ids":["child-a","child-b"],"agents_states":{"child-a":{"completed":"output-a"},"child-b":{"error":"failure-b"}}},"started_at_ms":1000,"completed_at_ms":2000}}),
        json!({"timestamp":"2026-08-17T00:00:02Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&lines);

    let mut decisions = subagent_discovery_plan(&turns, 0).decisions;
    decisions.sort_by(|left, right| {
        left.child
            .as_ref()
            .map(|child| child.session_id.as_str())
            .cmp(&right.child.as_ref().map(|child| child.session_id.as_str()))
    });

    assert_eq!(decisions.len(), 2);
    assert_eq!(decisions[0].reason, "native_terminal");
    assert_eq!(
        decisions[0]
            .activation
            .as_ref()
            .and_then(|activation| activation.terminal_output.as_deref()),
        Some("output-a")
    );
    assert_eq!(decisions[1].reason, "native_error_terminal");
    assert_eq!(
        decisions[1]
            .activation
            .as_ref()
            .and_then(|activation| activation.terminal_output.as_deref()),
        Some("failure-b")
    );
}

#[test]
fn test_discovery_plan_defers_malformed_identity_timing_and_orphan_wait() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
        json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"collab"}}),
        json!({"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-duplicate","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-a","child-a"]},"started_at_ms":1000,"completed_at_ms":2000}}),
        json!({"timestamp":"2026-08-17T00:00:02Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-bad-time","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-b"]},"started_at_ms":3000,"completed_at_ms":2000}}),
        json!({"timestamp":"2026-08-17T00:00:03Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"wait-orphan","tool":"wait","sender_thread_id":"parent","receiver_thread_ids":["child-orphan"],"agents_states":{"child-orphan":{"completed":"orphan"}}},"started_at_ms":3000,"completed_at_ms":4000}}),
        json!({"timestamp":"2026-08-17T00:00:04Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&lines);

    let plan = subagent_discovery_plan(&turns, 0);
    let reasons = plan
        .decisions
        .iter()
        .map(|decision| decision.reason.as_str())
        .collect::<HashSet<_>>();

    assert!(plan.observations.is_empty());
    assert_eq!(plan.decisions.len(), 3);
    assert_eq!(
        reasons,
        HashSet::from(["identity_ambiguous", "malformed_timing", "orphan_wait"])
    );
    assert!(plan
        .decisions
        .iter()
        .all(|decision| { decision.disposition == SubagentDiscoveryDisposition::Deferred }));
}

#[test]
fn test_discovery_plan_dedupes_identical_native_evidence_and_defers_conflicts() {
    let duplicate_spawn = json!({"timestamp":"2026-08-17T00:00:00.200Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-same","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-same"],"agents_states":{"child-same":"pending_init"}},"started_at_ms":200,"completed_at_ms":300}});
    let lines = vec![
        json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
        json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"collab"}}),
        duplicate_spawn.clone(),
        duplicate_spawn,
        json!({"timestamp":"2026-08-17T00:00:00.400Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"wait-same","tool":"wait","sender_thread_id":"parent","receiver_thread_ids":["child-same"],"agents_states":{"child-same":{"completed":"same"}}},"started_at_ms":400,"completed_at_ms":500}}),
        json!({"timestamp":"2026-08-17T00:00:00.600Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-conflict","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-a"],"agents_states":{"child-a":"pending_init"}},"started_at_ms":600,"completed_at_ms":700}}),
        json!({"timestamp":"2026-08-17T00:00:00.800Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-conflict","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-b"],"agents_states":{"child-b":"pending_init"}},"started_at_ms":800,"completed_at_ms":900}}),
        json!({"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&lines);

    let plan = subagent_discovery_plan(&turns, 0);

    assert_eq!(plan.decisions.len(), 2);
    assert_eq!(
        plan.decisions
            .iter()
            .filter(|decision| decision.activation_id.as_deref() == Some("spawn-same"))
            .count(),
        1
    );
    assert!(plan.decisions.iter().any(|decision| {
        decision.activation_id.as_deref() == Some("spawn-same")
            && decision.disposition == SubagentDiscoveryDisposition::Eligible
    }));
    assert!(plan.decisions.iter().any(|decision| {
        decision.activation_id.as_deref() == Some("spawn-conflict")
            && decision.disposition == SubagentDiscoveryDisposition::Deferred
            && decision.reason == "identity_ambiguous"
    }));
}

#[test]
fn test_discovery_plan_defers_one_child_with_conflicting_native_activations() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
        json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"collab"}}),
        json!({"timestamp":"2026-08-17T00:00:00.200Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-first","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-shared"],"agents_states":{"child-shared":"pending_init"}},"started_at_ms":200,"completed_at_ms":300}}),
        json!({"timestamp":"2026-08-17T00:00:00.400Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-second","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-shared"],"agents_states":{"child-shared":"pending_init"}},"started_at_ms":400,"completed_at_ms":500}}),
        json!({"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&lines);

    let plan = subagent_discovery_plan(&turns, 0);

    assert!(plan.observations.is_empty());
    assert_eq!(plan.decisions.len(), 1);
    let decision = &plan.decisions[0];
    assert_eq!(
        decision
            .child
            .as_ref()
            .map(|child| child.session_id.as_str()),
        Some("child-shared")
    );
    assert_eq!(decision.activation_id.as_deref(), Some("spawn-first"));
    assert_eq!(decision.disposition, SubagentDiscoveryDisposition::Deferred);
    assert_eq!(decision.reason, "identity_ambiguous");
}

#[test]
fn test_discovery_plan_preserves_legacy_activity_and_ignores_outer_exec_text() {
    let lines = vec![
        json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
        json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"spawn"}}),
        json!({"timestamp":"2026-08-17T00:00:01Z","type":"response_item","payload":{"type":"function_call","name":"exec","call_id":"outer-exec","arguments":"{\"cmd\":\"const text = 'spawn_agent child-fake';\"}"}}),
        json!({"timestamp":"2026-08-17T00:00:01.100Z","type":"response_item","payload":{"type":"function_call_output","call_id":"outer-exec","output":"ok"}}),
        json!({"timestamp":"2026-08-17T00:00:02Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","call_id":"legacy-spawn","arguments":"{\"message\":\"work\"}"}}),
        json!({"timestamp":"2026-08-17T00:00:02.100Z","type":"response_item","payload":{"type":"function_call_output","call_id":"legacy-spawn","output":"{\"agent_id\":\"legacy-child\"}"}}),
        json!({"timestamp":"2026-08-17T00:00:02.200Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"legacy-spawn","agent_thread_id":"legacy-child","kind":"started"}}),
        json!({"timestamp":"2026-08-17T00:00:03Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&lines);

    let plan = subagent_discovery_plan(&turns, 0);

    assert!(plan.observations.is_empty());
    assert_eq!(plan.decisions.len(), 1);
    assert_eq!(
        plan.decisions[0].source,
        CollabInvocationSource::LegacyToolCall
    );
    assert_eq!(
        plan.decisions[0].activation_id.as_deref(),
        Some("legacy-spawn")
    );
    assert_eq!(plan.decisions[0].reason, "legacy_current_activation");
    assert_eq!(
        plan.decisions[0]
            .child
            .as_ref()
            .map(|child| child.session_id.as_str()),
        Some("legacy-child")
    );
    assert!(!plan.decisions.iter().any(|decision| {
        decision
            .child
            .as_ref()
            .is_some_and(|child| child.session_id == "child-fake")
    }));
}

#[test]
fn test_parse_collab_agent_spawn_end() {
    let lines = vec![
        json!({"timestamp":"2026-05-23T10:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"spawn a helper agent\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.200Z","type":"event_msg","payload":{"type":"collab_agent_spawn_end","call_id":"call-spawn-1","completed_at_ms":1000,"sender_thread_id":"thread-parent","new_thread_id":"thread-child-1","new_agent_nickname":"worker","new_agent_role":"coder","prompt":"analyze the code","model":"gpt-5.4","status":"completed"}}),
        json!({"timestamp":"2026-05-23T10:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].collab_agent_events.len(), 1);
    match &turns[0].collab_agent_events[0] {
        CollabAgentEvent::SpawnEnd {
            call_id,
            sender_thread_id,
            new_thread_id,
            nickname,
            role,
            prompt,
            model,
            status,
        } => {
            assert_eq!(call_id, "call-spawn-1");
            assert_eq!(sender_thread_id, "thread-parent");
            assert_eq!(new_thread_id, &Some("thread-child-1".to_string()));
            assert_eq!(nickname, &Some("worker".to_string()));
            assert_eq!(role, &Some("coder".to_string()));
            assert_eq!(prompt, "analyze the code");
            assert_eq!(model, "gpt-5.4");
            assert_eq!(status, "completed");
        }
        other => panic!("expected SpawnEnd, got {:?}", other),
    }
}

#[test]
fn test_parse_collab_agent_interaction_events() {
    let lines = vec![
        json!({"timestamp":"2026-05-23T10:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"send message to agent\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.200Z","type":"event_msg","payload":{"type":"collab_agent_interaction_begin","call_id":"call-interact-1","started_at_ms":2000,"sender_thread_id":"thread-parent","receiver_thread_id":"thread-child-1","prompt":"please review"}}),
        json!({"timestamp":"2026-05-23T10:00:00.300Z","type":"event_msg","payload":{"type":"collab_agent_interaction_end","call_id":"call-interact-1","completed_at_ms":3000,"sender_thread_id":"thread-parent","receiver_thread_id":"thread-child-1","receiver_agent_nickname":"worker","receiver_agent_role":"coder","prompt":"please review","status":"completed"}}),
        json!({"timestamp":"2026-05-23T10:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].collab_agent_events.len(), 2);

    match &turns[0].collab_agent_events[0] {
        CollabAgentEvent::InteractionBegin {
            call_id,
            sender_thread_id,
            receiver_thread_id,
            prompt,
        } => {
            assert_eq!(call_id, "call-interact-1");
            assert_eq!(sender_thread_id, "thread-parent");
            assert_eq!(receiver_thread_id, "thread-child-1");
            assert_eq!(prompt, "please review");
        }
        other => panic!("expected InteractionBegin, got {:?}", other),
    }

    match &turns[0].collab_agent_events[1] {
        CollabAgentEvent::InteractionEnd {
            call_id,
            sender_thread_id,
            receiver_thread_id,
            receiver_nickname,
            receiver_role,
            prompt,
            status,
        } => {
            assert_eq!(call_id, "call-interact-1");
            assert_eq!(sender_thread_id, "thread-parent");
            assert_eq!(receiver_thread_id, "thread-child-1");
            assert_eq!(receiver_nickname, &Some("worker".to_string()));
            assert_eq!(receiver_role, &Some("coder".to_string()));
            assert_eq!(prompt, "please review");
            assert_eq!(status, "completed");
        }
        other => panic!("expected InteractionEnd, got {:?}", other),
    }
}

#[test]
fn test_parse_spawn_end_non_terminal_skipped() {
    // Running status should be skipped — we only capture terminal states
    let lines = vec![
        json!({"timestamp":"2026-05-23T10:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"spawn\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.200Z","type":"event_msg","payload":{"type":"collab_agent_spawn_end","call_id":"call-spawn-1","completed_at_ms":1000,"sender_thread_id":"thread-parent","new_thread_id":"thread-child-1","new_agent_nickname":"worker","new_agent_role":"coder","prompt":"analyze","model":"gpt-5.4","status":"running"}}),
        json!({"timestamp":"2026-05-23T10:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    // Running status is not terminal, so spawn_end should be skipped
    assert_eq!(turns[0].collab_agent_events.len(), 0);
}

#[test]
fn test_parse_subagent_source_from_session_meta() {
    // Legacy key: "sub_agent" (kept for backward compat)
    let lines = vec![
        json!({"timestamp":"2026-05-23T10:00:00.000Z","type":"session_meta","payload":{"session_id":"sess-sub-1","thread_id":"thread-child-1","source":{"sub_agent":{"thread_spawn":{"parent_thread_id":"thread-parent","depth":1,"agent_nickname":"worker","agent_role":"coder"}}},"model":"gpt-5.4","cwd":"/tmp/demo"}}),
        json!({"timestamp":"2026-05-23T10:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"do work\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);

    let src = turns[0]
        .subagent_source
        .as_ref()
        .expect("subagent_source should be set");
    assert_eq!(src.parent_thread_id, "thread-parent");
    assert_eq!(src.depth, 1);
    assert_eq!(src.agent_nickname, Some("worker".to_string()));
    assert_eq!(src.agent_role, Some("coder".to_string()));
    assert_eq!(src.agent_path, None);
    assert_eq!(turns[0].trigger, TurnTrigger::Subagent);
}

#[test]
fn test_parse_subagent_source_codex_format() {
    // Real codex format: key is "subagent" (no underscore), from agent_subagent.jsonl
    let lines = vec![
        json!({"timestamp":"2026-05-23T11:44:17.384Z","type":"session_meta","payload":{
            "id":"019e54a6-7b9e-7f50-8024-2bc88c07f89b",
            "source":{"subagent":{"thread_spawn":{
                "parent_thread_id":"019e54a5-e6c6-7432-978e-f9620b5a2e8b",
                "depth":1,
                "agent_path":null,
                "agent_nickname":"Dewey",
                "agent_role":"worker"
            }}},
            "thread_source":"subagent",
            "agent_nickname":"Dewey",
            "agent_role":"worker",
            "model_provider":"openai"
        }}),
        json!({"timestamp":"2026-05-23T11:44:17.387Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-sub-1"}}),
        json!({"timestamp":"2026-05-23T11:44:17.400Z","type":"event_msg","payload":{"type":"user_message","message":"do subagent work\n"}}),
        json!({"timestamp":"2026-05-23T11:44:40.257Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-sub-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);

    let src = turns[0]
        .subagent_source
        .as_ref()
        .expect("subagent_source should be set for codex subagent format");
    assert_eq!(src.parent_thread_id, "019e54a5-e6c6-7432-978e-f9620b5a2e8b");
    assert_eq!(src.depth, 1);
    assert_eq!(src.agent_nickname, Some("Dewey".to_string()));
    assert_eq!(src.agent_role, Some("worker".to_string()));
    assert_eq!(src.agent_path, None);
    assert_eq!(turns[0].trigger, TurnTrigger::Subagent);
}

#[test]
fn test_parse_subagent_source_thread_source_fallback() {
    // Fallback: source is a plain string ("cli" for root, some other value for subagent)
    // but thread_source == "subagent" is set at payload level
    let lines = vec![
        json!({"timestamp":"2026-05-23T10:00:00.000Z","type":"session_meta","payload":{
            "id":"sess-fallback-1",
            "source":"codex-tui",
            "thread_source":"subagent",
            "agent_nickname":"helper",
            "agent_role":"analyst",
            "model_provider":"openai"
        }}),
        json!({"timestamp":"2026-05-23T10:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-fb-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"help\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-fb-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);

    let src = turns[0]
        .subagent_source
        .as_ref()
        .expect("thread_source fallback should set subagent_source");
    assert_eq!(src.agent_nickname, Some("helper".to_string()));
    assert_eq!(src.agent_role, Some("analyst".to_string()));
    assert_eq!(src.depth, 1);
    assert!(
        src.parent_thread_id.is_empty(),
        "parent_thread_id unknown in fallback path"
    );
    assert_eq!(turns[0].trigger, TurnTrigger::Subagent);
}

#[test]
fn test_root_session_not_detected_as_subagent() {
    // Root codex session: source == "cli" (string), thread_source == "user"
    let lines = vec![
        json!({"timestamp":"2026-05-23T10:00:00.000Z","type":"session_meta","payload":{
            "id":"019e54a5-e6c6-7432-978e-f9620b5a2e8b",
            "source":"cli",
            "thread_source":"user",
            "originator":"codex-tui",
            "model_provider":"openai"
        }}),
        json!({"timestamp":"2026-05-23T10:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-root-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"hi\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-root-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert!(
        turns[0].subagent_source.is_none(),
        "root session must not be detected as subagent"
    );
    assert_eq!(turns[0].trigger, TurnTrigger::Interactive);
}

#[test]
fn test_turn_trigger_user_exec_tool_remains_interactive() {
    let lines = vec![
        json!({"timestamp":"2026-05-23T10:00:00.000Z","type":"session_meta","payload":{
            "id":"root-with-exec-tool",
            "source":"cli",
            "thread_source":"user",
            "originator":"codex-tui"
        }}),
        json!({"timestamp":"2026-05-23T10:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"run pwd\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.200Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"pwd\"}","call_id":"call-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.210Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-1","output":"/tmp\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].trigger, TurnTrigger::Interactive);
}

#[test]
fn test_turn_trigger_user_without_source_is_interactive() {
    let lines = minimal_turn_lines(json!({
        "id":"user-without-source",
        "thread_source":"user"
    }));

    let turns = build_turns(&lines);
    assert_eq!(turns[0].trigger, TurnTrigger::Interactive);
}

#[test]
fn test_turn_trigger_conflicting_user_source_exec_is_unknown() {
    let lines = minimal_turn_lines(json!({
        "id":"conflicting-source",
        "source":"exec",
        "thread_source":"user",
        "originator":"codex-tui"
    }));

    let turns = build_turns(&lines);
    assert_eq!(turns[0].trigger, TurnTrigger::Unknown);
}

#[test]
fn test_turn_trigger_channel_without_thread_source_is_unknown() {
    for source in ["cli", "vscode"] {
        let lines = minimal_turn_lines(json!({
            "id":format!("channel-only-{source}"),
            "source":source,
            "originator":"codex-tui"
        }));

        let turns = build_turns(&lines);
        assert_eq!(
            turns[0].trigger,
            TurnTrigger::Unknown,
            "source={source} alone must not imply interactive"
        );
    }
}

#[test]
fn test_turn_trigger_unsupported_thread_source_blocks_weak_fallback() {
    let lines = minimal_turn_lines(json!({
        "id":"unsupported-thread-source",
        "source":"cli",
        "thread_source":"queued",
        "originator":"codex-tui"
    }));

    let turns = build_turns(&lines);
    assert_eq!(turns[0].trigger, TurnTrigger::Unknown);
}

#[test]
fn test_turn_trigger_missing_session_meta_is_unknown() {
    let lines = vec![
        json!({"timestamp":"2026-05-23T10:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"hello\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns[0].trigger, TurnTrigger::Unknown);
}

#[test]
fn test_turn_trigger_uses_first_session_meta_snapshot() {
    let lines = vec![
        json!({"timestamp":"2026-05-23T10:00:00.000Z","type":"session_meta","payload":{
            "id":"child",
            "source":{"subagent":{"thread_spawn":{"parent_thread_id":"parent","depth":1}}},
            "thread_source":"subagent"
        }}),
        json!({"timestamp":"2026-05-23T10:00:00.010Z","type":"session_meta","payload":{
            "id":"inherited-root-context",
            "source":"cli",
            "thread_source":"user"
        }}),
        json!({"timestamp":"2026-05-23T10:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"child work\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns[0].trigger, TurnTrigger::Subagent);
}

#[test]
fn test_turn_trigger_falls_back_to_legacy_sub_agent_when_canonical_value_is_null() {
    let lines = minimal_turn_lines(json!({
        "id":"legacy-child",
        "source":{
            "subagent":null,
            "sub_agent":{"thread_spawn":{"parent_thread_id":"legacy-parent"}}
        },
        "thread_source":"user"
    }));

    let turns = build_turns(&lines);
    assert_eq!(turns[0].trigger, TurnTrigger::Subagent);
}

#[test]
fn test_resolve_turn_trigger_per_turn_direct_evidence_overrides_snapshot() {
    assert_eq!(
        resolve_turn_trigger(Some(TurnTrigger::Programmatic), TurnTrigger::Interactive),
        TurnTrigger::Programmatic
    );
    assert_eq!(
        resolve_turn_trigger(None, TurnTrigger::Subagent),
        TurnTrigger::Subagent
    );
}

fn minimal_turn_lines(session_meta_payload: Value) -> Vec<Value> {
    vec![
        json!({"timestamp":"2026-05-23T10:00:00.000Z","type":"session_meta","payload":session_meta_payload}),
        json!({"timestamp":"2026-05-23T10:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"hello\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ]
}

#[test]
fn test_no_subagent_source_for_root_session() {
    let lines = vec![
        json!({"timestamp":"2026-05-23T10:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"hello\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert!(
        turns[0].subagent_source.is_none(),
        "root session should have no subagent_source"
    );
}

#[test]
fn test_collab_agent_events_empty_for_normal_turn() {
    let lines = vec![
        json!({"timestamp":"2026-05-23T10:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.120Z","type":"event_msg","payload":{"type":"user_message","message":"hello\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.200Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"pwd\"}","call_id":"call-1"}}),
        json!({"timestamp":"2026-05-23T10:00:00.210Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-1","output":"/tmp\n"}}),
        json!({"timestamp":"2026-05-23T10:00:00.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    assert!(turns[0].collab_agent_events.is_empty());
}

#[test]
fn test_extract_session_meta_ids_codex_subagent_format() {
    let content = r#"{"type":"session_meta","payload":{"id":"child-1","source":{"subagent":{"thread_spawn":{"parent_thread_id":"parent-1"}}},"thread_source":"subagent"}}
{"type":"event_msg","payload":{"type":"task_started"}}"#;
    let meta = extract_session_meta_ids(content);
    assert_eq!(meta.session_id.as_deref(), Some("child-1"));
    assert_eq!(meta.parent_thread_id.as_deref(), Some("parent-1"));
}

#[test]
fn test_extract_session_meta_ids_prefers_id_when_session_id_is_parent() {
    // Current Codex Desktop child transcripts carry both `id` (child) and
    // `session_id` (parent/root). Subagent correlation must use the child id.
    let content = r#"{"type":"session_meta","payload":{"session_id":"parent-1","id":"child-1","parent_thread_id":"parent-1","source":{"subagent":{"thread_spawn":{"parent_thread_id":"parent-1"}}},"thread_source":"subagent"}}
{"type":"event_msg","payload":{"type":"task_started"}}"#;
    let meta = extract_session_meta_ids(content);
    assert_eq!(meta.session_id.as_deref(), Some("child-1"));
    assert_eq!(meta.parent_thread_id.as_deref(), Some("parent-1"));
}

#[test]
fn test_extract_session_meta_ids_legacy_sub_agent_key() {
    // Legacy underscored "sub_agent" key must still parse.
    let content = r#"{"type":"session_meta","payload":{"session_id":"child-x","thread_id":"tid-x","source":{"sub_agent":{"thread_spawn":{"parent_thread_id":"parent-x"}}}}}"#;
    let meta = extract_session_meta_ids(content);
    assert_eq!(meta.session_id.as_deref(), Some("child-x"));
    assert_eq!(meta.thread_id.as_deref(), Some("tid-x"));
    assert_eq!(meta.parent_thread_id.as_deref(), Some("parent-x"));
}

#[test]
fn test_extract_session_meta_ids_falls_back_when_canonical_subagent_is_null() {
    let content = r#"{"type":"session_meta","payload":{"id":"child-x","source":{"subagent":null,"sub_agent":{"thread_spawn":{"parent_thread_id":"parent-x","agent_path":"/root/legacy"}}}}}"#;
    let meta = extract_session_meta_ids(content);
    assert_eq!(meta.session_id.as_deref(), Some("child-x"));
    assert_eq!(meta.parent_thread_id.as_deref(), Some("parent-x"));
    assert_eq!(meta.agent_path.as_deref(), Some("/root/legacy"));
}

#[test]
fn test_extract_session_meta_ids_parent_session() {
    // Root parent session: no parent_thread_id should be returned.
    let content = r#"{"type":"session_meta","payload":{"id":"root-1","thread_source":"user","source":"cli"}}"#;
    let meta = extract_session_meta_ids(content);
    assert_eq!(meta.session_id.as_deref(), Some("root-1"));
    assert!(meta.parent_thread_id.is_none());
}

#[test]
fn test_extract_session_meta_ids_no_session_meta() {
    let content = r#"{"type":"event_msg","payload":{"type":"task_started"}}"#;
    let meta = extract_session_meta_ids(content);
    assert!(meta.session_id.is_none());
    assert!(meta.parent_thread_id.is_none());
}

#[test]
fn test_is_child_sync_in_parent_finds_wait_agent_escaped() {
    let dir = tempfile::tempdir().unwrap();
    let parent_path = dir.path().join("rollout-parent.jsonl");
    // wait_agent arguments come escaped inside a JSON string field.
    let parent_jsonl = r#"{"type":"session_meta","payload":{"id":"parent-1"}}
{"timestamp":"2026-07-10T00:00:00Z","type":"event_msg","payload":{"type":"user_message","message":"run child"}}
{"timestamp":"2026-07-10T00:00:01Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","call_id":"spawn-1","arguments":"{\"message\":\"work\"}"}}
{"timestamp":"2026-07-10T00:00:02Z","type":"response_item","payload":{"type":"function_call_output","call_id":"spawn-1","output":"{\"agent_id\":\"child-1\"}"}}
{"timestamp":"2026-07-10T00:00:03Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","call_id":"wait-1","arguments":"{\"targets\":[\"child-1\"],\"timeout_ms\":30000}"}}
{"timestamp":"2026-07-10T00:00:04Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait-1","output":"{\"timed_out\":false}"}}
"#;
    std::fs::write(&parent_path, parent_jsonl).unwrap();
    assert!(is_child_sync_in_parent(
        dir.path(),
        "parent-1",
        "child-1",
        None
    ));
}

#[test]
fn test_is_child_sync_in_parent_returns_false_when_no_wait_agent() {
    let dir = tempfile::tempdir().unwrap();
    let parent_path = dir.path().join("rollout-parent.jsonl");
    // Parent only spawned the child; never called wait_agent => async.
    let parent_jsonl = r#"{"type":"session_meta","payload":{"id":"parent-1"}}
{"type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{\"agent_type\":\"worker\"}"}}
"#;
    std::fs::write(&parent_path, parent_jsonl).unwrap();
    assert!(!is_child_sync_in_parent(
        dir.path(),
        "parent-1",
        "child-1",
        None
    ));
}

#[test]
fn test_is_child_sync_in_parent_safe_default_when_parent_missing() {
    // No matching parent transcript in dir -> safe default = true (skip).
    let dir = tempfile::tempdir().unwrap();
    assert!(is_child_sync_in_parent(
        dir.path(),
        "parent-missing",
        "child-1",
        None
    ));
}

#[test]
fn test_is_child_sync_in_parent_empty_child_id_returns_true() {
    let dir = tempfile::tempdir().unwrap();
    assert!(is_child_sync_in_parent(dir.path(), "parent-1", "", None));
}

#[test]
fn test_is_child_sync_in_parent_finds_interrupted_candidate() {
    let dir = tempfile::tempdir().unwrap();
    let parent_path = dir.path().join("rollout-parent.jsonl");
    let parent_jsonl = r#"{"type":"session_meta","payload":{"id":"parent-1"}}
{"timestamp":"2026-07-10T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-10T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"run child"}}
{"timestamp":"2026-07-10T00:00:01Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","call_id":"spawn-1","arguments":"{\"task_name\":\"reviewer\"}"}}
{"timestamp":"2026-07-10T00:00:01.100Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"spawn-1","agent_thread_id":"child-1","agent_path":"/root/reviewer","kind":"started"}}
{"timestamp":"2026-07-10T00:00:01.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"spawn-1","output":"{\"task_name\":\"/root/reviewer\"}"}}
{"timestamp":"2026-07-10T00:00:02Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","call_id":"wait-1","arguments":"{\"targets\":[\"child-1\"]}"}}
{"timestamp":"2026-07-10T00:00:03Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait-1","output":"{\"timed_out\":true}"}}
{"timestamp":"2026-07-10T00:00:04Z","type":"response_item","payload":{"type":"function_call","name":"interrupt_agent","call_id":"interrupt-1","arguments":"{\"target\":\"/root/reviewer\"}"}}
{"timestamp":"2026-07-10T00:00:04.100Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"interrupt-1","agent_thread_id":"child-1","agent_path":"/root/reviewer","kind":"interrupted"}}
{"timestamp":"2026-07-10T00:00:04.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"interrupt-1","output":"{\"interrupted\":true}"}}
{"timestamp":"2026-07-10T00:00:05Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"stopped"}}
"#;
    std::fs::write(&parent_path, parent_jsonl).unwrap();

    assert!(is_child_sync_in_parent(
        dir.path(),
        "parent-1",
        "child-1",
        Some("/root/reviewer")
    ));
}

/// Build a Turn for unit testing the `turn_looks_complete` heuristic.
fn turn_for_heuristic(
    assistants: Vec<(&str, &str)>, // (text, phase)
    tool_calls: Vec<(&str, &str)>, // (name, ended_timestamp)
) -> Turn {
    Turn {
        chat_id: None,
        turn_id: "t-1".to_string(),
        user_timestamp: "2026-05-24T11:45:20.000Z".to_string(),
        user_text: "hello".to_string(),
        user_content: serde_json::Value::Null,
        assistants: assistants
            .into_iter()
            .map(|(text, phase)| AssistantMessage {
                timestamp: "2026-05-24T11:45:21.000Z".to_string(),
                text: text.to_string(),
                media_content: serde_json::Value::Null,
                phase: phase.to_string(),
                usage: TokenUsage::default(),
                token_count_timestamp: String::new(),
                invocation_started_timestamp: String::new(),
                invocation_end_source: None,
            })
            .collect(),
        tool_calls: tool_calls
            .into_iter()
            .enumerate()
            .map(|(i, (name, ended))| ToolCall {
                call_id: format!("call-{}", i),
                name: name.to_string(),
                input: json!({}),
                output: json!(""),
                started_timestamp: "2026-05-24T11:45:21.000Z".to_string(),
                ended_timestamp: ended.to_string(),
                assistant_index: 0,
                success: None,
            })
            .collect(),
        model: "gpt-5.4".to_string(),
        cwd: String::new(),
        system_instructions: String::new(),
        completed: false,
        completion_timestamp: String::new(),
        terminal_state: TurnTerminalState::Open,
        terminal_error: None,
        trigger: Default::default(),
        skill_invocations: Vec::new(),
        skill_name: None,
        compactions: Vec::new(),
        collab_agent_events: Vec::new(),
        subagent_source: None,
    }
}

#[test]
fn test_turn_looks_complete_hello_no_tools_with_final_text() {
    // Simple "hello" turn: no tool_calls, final_answer with text present.
    let t = turn_for_heuristic(vec![("hi there", "final_answer")], vec![]);
    assert!(turn_looks_complete(&t));
}

#[test]
fn test_turn_looks_complete_subagent_wait_pending_returns_false() {
    // Parent mid-task: commentary text + spawn_agent done, wait_agent
    // still pending (no ended_timestamp).
    let t = turn_for_heuristic(
        vec![("kicking off subagent", "commentary")],
        vec![
            ("spawn_agent", "2026-05-24T11:45:22.000Z"),
            ("wait_agent", ""), // pending
        ],
    );
    assert!(!turn_looks_complete(&t));
}

#[test]
fn test_turn_looks_complete_subagent_wait_done_requires_authoritative_terminal() {
    // Even after wait_agent returns and a final answer is visible, the
    // parent/child lifecycle must wait for an authoritative terminal.
    let t = turn_for_heuristic(
        vec![
            ("kicking off", "commentary"),
            ("here is the result", "final_answer"),
        ],
        vec![
            ("spawn_agent", "2026-05-24T11:45:22.000Z"),
            ("wait_agent", "2026-05-24T11:45:30.000Z"),
        ],
    );
    assert!(!turn_looks_complete(&t));
}

#[test]
fn test_turn_looks_complete_only_reasoning_returns_false() {
    // Codex wrote reasoning text but no final_answer phase — must not flush.
    let t = turn_for_heuristic(vec![("thinking through options", "reasoning")], vec![]);
    assert!(!turn_looks_complete(&t));
}

#[test]
fn test_turn_looks_complete_only_commentary_returns_false() {
    // Commentary text appears mid-task before the final answer — must not flush.
    let t = turn_for_heuristic(vec![("let me work on this", "commentary")], vec![]);
    assert!(!turn_looks_complete(&t));
}

#[test]
fn test_turn_looks_complete_trailing_empty_generation_after_final_answer_returns_true() {
    // Real codex pattern: token_count event inserts an empty placeholder
    // assistant entry AFTER the actual final_answer. Heuristic must look
    // for a final_answer with text, not at the LAST assistant.
    let t = turn_for_heuristic(
        vec![
            ("partial commentary", "commentary"),
            ("the actual answer", "final_answer"),
            ("", "generation_7"), // empty placeholder from token_count
        ],
        vec![],
    );
    assert!(turn_looks_complete(&t));
}

#[test]
fn test_turn_looks_complete_no_assistant_yet_returns_false() {
    // Very early Stop: only user_message + task_started seen, no assistant.
    let t = turn_for_heuristic(vec![], vec![]);
    assert!(!turn_looks_complete(&t));
}

#[test]
fn test_turn_looks_complete_parallel_tools_all_done_returns_true() {
    // Parallel tool calls all returned, final_answer present.
    let t = turn_for_heuristic(
        vec![("done", "final_answer")],
        vec![
            ("exec_command", "2026-05-24T11:45:22.000Z"),
            ("read_file", "2026-05-24T11:45:22.500Z"),
            ("grep", "2026-05-24T11:45:23.000Z"),
        ],
    );
    assert!(turn_looks_complete(&t));
}

#[test]
fn test_mark_turn_heuristic_complete_uses_chronological_mixed_precision_timestamp() {
    let mut turn = turn_for_heuristic(
        vec![("done", "final_answer")],
        vec![("exec_command", "2026-05-24T11:45:21.100Z")],
    );
    turn.assistants[0].timestamp = "2026-05-24T11:45:21Z".to_string();

    mark_turn_heuristic_complete(&mut turn);

    assert_eq!(
        turn.completion_timestamp, "2026-05-24T11:45:21.100Z",
        "timestamp selection must compare parsed time, not ISO string length"
    );
    assert_eq!(turn.terminal_state, TurnTerminalState::HeuristicComplete);
}

#[test]
fn test_token_count_placeholder_uses_own_timestamp() {
    // Codex agentic-loop pattern: user_msg → agent_msg → tool → token_count
    // → agent_msg → tool → token_count. The placeholder created at
    // each `token_count` must use that event's own timestamp so the
    // subsequent merged assistant doesn't carry an earlier (gen_start)
    // timestamp that sorts BEFORE the prior direct agent_message.
    let lines = vec![
        json!({"timestamp":"2026-05-24T11:00:10.000Z","type":"session_meta","payload":{"id":"sess-1"}}),
        json!({"timestamp":"2026-05-24T11:00:10.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"t1"}}),
        json!({"timestamp":"2026-05-24T11:00:10.000Z","type":"event_msg","payload":{"type":"user_message","message":"go"}}),
        json!({"timestamp":"2026-05-24T11:00:15.000Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"first answer"}}),
        json!({"timestamp":"2026-05-24T11:00:16.000Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{}","call_id":"c1"}}),
        json!({"timestamp":"2026-05-24T11:00:17.000Z","type":"response_item","payload":{"type":"function_call_output","call_id":"c1","output":"ok"}}),
        json!({"timestamp":"2026-05-24T11:00:18.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"output_tokens":20}}}}),
        json!({"timestamp":"2026-05-24T11:00:25.000Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"second answer"}}),
        json!({"timestamp":"2026-05-24T11:00:26.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":120,"output_tokens":30}}}}),
        json!({"timestamp":"2026-05-24T11:00:27.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"t1","last_agent_message":"second answer"}}),
    ];
    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    let turn = &turns[0];

    // assistant[0] = first agent_message direct (ts = 11:00:15)
    // assistant[1] = token_count[0] placeholder, then merged with the
    //   second agent_message (ts must advance to >= 11:00:25)
    assert!(
        turn.assistants.len() >= 2,
        "expected >=2 assistants, got {}",
        turn.assistants.len()
    );
    let a0_ns: u128 = iso_to_nanos(&turn.assistants[0].timestamp).parse().unwrap();
    let a1_ns: u128 = iso_to_nanos(&turn.assistants[1].timestamp).parse().unwrap();
    assert!(
            a1_ns > a0_ns,
            "assistants[1].ts ({}) must be later than assistants[0].ts ({}) for chronological span order",
            turn.assistants[1].timestamp, turn.assistants[0].timestamp
        );
    // Specifically: assistants[1] must reflect the second agent_message's
    // emission time (>= 11:00:25), NOT the gen_start time (11:00:10).
    let merge_threshold: u128 = iso_to_nanos("2026-05-24T11:00:25.000Z").parse().unwrap();
    assert!(
        a1_ns >= merge_threshold,
        "assistants[1].ts ({}) must be advanced to the second agent_message's timestamp",
        turn.assistants[1].timestamp
    );
}

#[test]
fn test_chat_spans_in_chronological_order_after_merge() {
    // Stronger version: every adjacent pair of assistants must have
    // monotonically non-decreasing timestamps. Regression-guards the
    // "two chat spans appear in reverse order" bug observed at session
    // 019e5a20.
    let lines = vec![
        json!({"timestamp":"2026-05-24T13:16:03.060Z","type":"session_meta","payload":{"id":"sess-x"}}),
        json!({"timestamp":"2026-05-24T13:16:03.060Z","type":"event_msg","payload":{"type":"task_started","turn_id":"t1"}}),
        json!({"timestamp":"2026-05-24T13:16:03.060Z","type":"event_msg","payload":{"type":"user_message","message":"do things"}}),
        json!({"timestamp":"2026-05-24T13:16:08.811Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"step one"}}),
        json!({"timestamp":"2026-05-24T13:16:09.279Z","type":"response_item","payload":{"type":"function_call","name":"tool_a","arguments":"{}","call_id":"c1"}}),
        json!({"timestamp":"2026-05-24T13:16:09.520Z","type":"response_item","payload":{"type":"function_call_output","call_id":"c1","output":"ok"}}),
        json!({"timestamp":"2026-05-24T13:16:09.529Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":10,"output_tokens":5}}}}),
        json!({"timestamp":"2026-05-24T13:16:13.649Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"step two"}}),
        json!({"timestamp":"2026-05-24T13:16:14.000Z","type":"response_item","payload":{"type":"function_call","name":"tool_b","arguments":"{}","call_id":"c2"}}),
        json!({"timestamp":"2026-05-24T13:16:14.500Z","type":"response_item","payload":{"type":"function_call_output","call_id":"c2","output":"ok"}}),
        json!({"timestamp":"2026-05-24T13:16:15.108Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":20,"output_tokens":7}}}}),
        json!({"timestamp":"2026-05-24T13:16:20.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"t1","last_agent_message":"final"}}),
    ];
    let turns = build_turns(&lines);
    assert_eq!(turns.len(), 1);
    let assistants = &turns[0].assistants;
    for w in assistants.windows(2) {
        let a_ns: u128 = iso_to_nanos(&w[0].timestamp).parse().unwrap();
        let b_ns: u128 = iso_to_nanos(&w[1].timestamp).parse().unwrap();
        assert!(
            b_ns >= a_ns,
            "assistants must be in chronological order: {} -> {} broke monotonicity",
            w[0].timestamp,
            w[1].timestamp
        );
    }
}

fn v2_parent_lines(activity_child: &str, activity_path: &str) -> Vec<Value> {
    vec![
        json!({"timestamp":"2026-07-10T08:53:46.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-v2"}}),
        json!({"timestamp":"2026-07-10T08:53:46.100Z","type":"event_msg","payload":{"type":"user_message","message":"run child"}}),
        json!({"timestamp":"2026-07-10T08:53:47.000Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{\"task_name\":\"sleep_foreground\"}","call_id":"call-spawn"}}),
        json!({"timestamp":"2026-07-10T08:53:47.100Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-spawn","agent_thread_id":activity_child,"agent_path":activity_path,"kind":"started"}}),
        json!({"timestamp":"2026-07-10T08:53:47.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-spawn","output":"{\"task_name\":\"/root/sleep_foreground\"}"}}),
        json!({"timestamp":"2026-07-10T08:53:48.000Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"timeout_ms\":1000}","call_id":"call-wait"}}),
        json!({"timestamp":"2026-07-10T08:53:49.000Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-wait","output":"{\"message\":\"Wait timed out.\"}"}}),
        json!({"timestamp":"2026-07-10T08:54:09.000Z","type":"response_item","payload":{"type":"agent_message","author":"/root/sleep_foreground","recipient":"/root","content":[{"type":"input_text","text":"Message Type: FINAL_ANSWER\nTask name: /root\nSender: /root/sleep_foreground\nPayload:\ndone"}]}}),
        json!({"timestamp":"2026-07-10T08:54:10.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-v2","last_agent_message":"done"}}),
    ]
}

fn v2_interrupted_parent_lines() -> Vec<Value> {
    vec![
        json!({"timestamp":"2026-07-10T08:53:46.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-v2-interrupted"}}),
        json!({"timestamp":"2026-07-10T08:53:46.100Z","type":"event_msg","payload":{"type":"user_message","message":"run child"}}),
        json!({"timestamp":"2026-07-10T08:53:47.000Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{\"task_name\":\"sleep_foreground\"}","call_id":"call-spawn"}}),
        json!({"timestamp":"2026-07-10T08:53:47.100Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-spawn","agent_thread_id":"child-v2","agent_path":"/root/sleep_foreground","kind":"started"}}),
        json!({"timestamp":"2026-07-10T08:53:47.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-spawn","output":"{\"task_name\":\"/root/sleep_foreground\"}"}}),
        json!({"timestamp":"2026-07-10T08:53:48.000Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"timeout_ms\":1000}","call_id":"call-wait"}}),
        json!({"timestamp":"2026-07-10T08:53:49.000Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-wait","output":"{\"message\":\"Wait timed out.\"}"}}),
        json!({"timestamp":"2026-07-10T08:53:50.000Z","type":"response_item","payload":{"type":"function_call","name":"interrupt_agent","arguments":"{\"target\":\"/root/sleep_foreground\"}","call_id":"call-interrupt"}}),
        json!({"timestamp":"2026-07-10T08:53:50.100Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-interrupt","agent_thread_id":"child-v2","agent_path":"/root/sleep_foreground","kind":"interrupted"}}),
        json!({"timestamp":"2026-07-10T08:53:50.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-interrupt","output":"{\"interrupted\":true}"}}),
        json!({"timestamp":"2026-07-10T08:54:10.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-v2-interrupted","last_agent_message":"stopped"}}),
    ]
}

fn child_identity(session_id: &str, path: &str) -> SubagentIdentity {
    SubagentIdentity {
        session_id: session_id.to_string(),
        agent_path: Some(path.to_string()),
    }
}

#[test]
fn test_parse_v2_activity_and_typed_agent_message() {
    let turns = build_turns(&v2_parent_lines("child-v2", "/root/sleep_foreground"));
    assert_eq!(turns.len(), 1);
    assert!(turns[0].collab_agent_events.iter().any(|event| matches!(
        event,
        CollabAgentEvent::SubagentActivity {
            event_id,
            agent_thread_id,
            ..
        } if event_id == "call-spawn" && agent_thread_id == "child-v2"
    )));
    assert!(turns[0].collab_agent_events.iter().any(|event| matches!(
        event,
        CollabAgentEvent::AgentMessage {
            author,
            recipient,
            message_type: Some(message_type),
            ..
        } if author == "/root/sleep_foreground"
            && recipient == "/root"
            && message_type == "FINAL_ANSWER"
    )));
    assert_eq!(
        turns[0]
            .assistants
            .iter()
            .filter(|assistant| assistant.text.contains("Message Type:"))
            .count(),
        0,
        "typed inter-agent messages must not become assistant LLM output"
    );
}

#[test]
fn test_first_child_session_meta_identity_is_preserved() {
    let mut lines = vec![
        json!({"timestamp":"2026-07-10T08:53:47.000Z","type":"session_meta","payload":{"id":"child-v2","multi_agent_version":"v2","source":{"subagent":{"thread_spawn":{"parent_thread_id":"parent-v2","depth":1,"agent_path":"/root/sleep_foreground","agent_nickname":"Hume"}}}}}),
        json!({"timestamp":"2026-07-10T08:53:47.100Z","type":"session_meta","payload":{"id":"parent-v2","source":"cli","thread_source":"user"}}),
    ];
    lines.extend(v2_parent_lines("child-v2", "/root/sleep_foreground"));
    let turns = build_turns(&lines);
    let source = turns[0].subagent_source.as_ref().expect("child source");
    assert_eq!(source.parent_thread_id, "parent-v2");
    assert_eq!(source.agent_path.as_deref(), Some("/root/sleep_foreground"));
    assert_eq!(source.protocol_version.as_deref(), Some("v2"));
}

#[test]
fn test_v2_activity_uuid_correlation_completes_with_timeout_wait() {
    let turns = build_turns(&v2_parent_lines("child-v2", "/root/sleep_foreground"));
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::CompletedSync);
    assert_eq!(matched.protocol, Some(SubagentProtocol::V2));
    assert_eq!(
        matched.identity_source,
        Some(SubagentIdentitySource::V2Activity)
    );
    assert_eq!(matched.spawn_call_id.as_deref(), Some("call-spawn"));
    assert_eq!(matched.wait_call_ids, vec!["call-wait"]);
    assert_eq!(matched.reason, "v2_completed_sync");
}

#[test]
fn test_v2_failed_child_is_a_terminal_failed_sync_invocation() {
    let parent_turns = build_turns(&v2_parent_lines("child-v2", "/root/sleep_foreground"));
    let child_turns = build_turns(&[
        json!({"timestamp":"2026-07-10T08:53:47.300Z","type":"event_msg","payload":{"type":"task_started","turn_id":"child-turn"}}),
        json!({"timestamp":"2026-07-10T08:53:47.400Z","type":"event_msg","payload":{"type":"user_message","message":"do task"}}),
        json!({"timestamp":"2026-07-10T08:53:48.500Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"child-turn","error":{"type":"invalid_request_error","message":"model is unavailable"}}}),
    ]);
    assert_eq!(child_turns[0].terminal_state, TurnTerminalState::Failed);

    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matches = correlate_subagent_invocations(
        &parent_turns,
        &child,
        std::slice::from_ref(&child),
        &child_turns,
    );

    assert_eq!(matches.len(), 1);
    assert_eq!(
        matches[0].correlation.lifecycle,
        SubagentLifecycle::FailedSync
    );
    assert_eq!(matches[0].correlation.reason, "v2_failed_sync");
}

#[test]
fn test_v2_parent_interruption_is_candidate_until_child_abort_is_verified() {
    let turns = build_turns(&v2_interrupted_parent_lines());
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::IdentityCandidate);
    assert_eq!(matched.protocol, Some(SubagentProtocol::V2));
    assert_eq!(matched.spawn_call_id.as_deref(), Some("call-spawn"));
    assert_eq!(matched.activation_call_id.as_deref(), Some("call-spawn"));
    assert_eq!(matched.wait_call_ids, vec!["call-wait"]);
    assert_eq!(matched.reason, "v2_interruption_candidate");
    let evidence = matched
        .interruption_evidence
        .first()
        .expect("interruption evidence");
    assert_eq!(evidence.interrupt_call_id, "call-interrupt");
    assert_eq!(evidence.interrupt_timestamp, "2026-07-10T08:53:50.100Z");
}

#[test]
fn test_v2_short_target_send_message_stays_in_spawn_invocation() {
    let mut parent_lines = v2_interrupted_parent_lines();
    let interrupt_index = parent_lines
        .iter()
        .position(|entry| {
            entry["payload"]["type"] == "function_call"
                && entry["payload"]["name"] == "interrupt_agent"
        })
        .expect("interrupt call");
    parent_lines.insert(
            interrupt_index,
            json!({"timestamp":"2026-07-10T08:53:49.500Z","type":"response_item","payload":{"type":"function_call","name":"send_message","arguments":"{\"target\":\"sleep_foreground\",\"message\":\"finish now\"}","call_id":"call-send"}}),
        );
    parent_lines.insert(
            interrupt_index + 1,
            json!({"timestamp":"2026-07-10T08:53:49.600Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-send","agent_thread_id":"child-v2","agent_path":"/root/sleep_foreground","kind":"interacted"}}),
        );
    parent_lines.insert(
            interrupt_index + 2,
            json!({"timestamp":"2026-07-10T08:53:49.700Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-send","output":"{\"delivered\":true}"}}),
        );
    for entry in &mut parent_lines {
        if entry["payload"]["type"] == "function_call"
            && entry["payload"]["name"] == "interrupt_agent"
        {
            entry["payload"]["arguments"] = json!("{\"target\":\"sleep_foreground\"}");
        }
    }
    let parent_turns = build_turns(&parent_lines);
    let child_turns = build_turns(&[
        json!({"timestamp":"2026-07-10T08:53:47.300Z","type":"event_msg","payload":{"type":"task_started","turn_id":"child-aborted"}}),
        json!({"timestamp":"2026-07-10T08:53:47.400Z","type":"event_msg","payload":{"type":"user_message","message":"work"}}),
        json!({"timestamp":"2026-07-10T08:53:50.300Z","type":"event_msg","payload":{"type":"turn_aborted","turn_id":"child-aborted"}}),
    ]);
    let child = child_identity("child-v2", "/root/sleep_foreground");

    let matches = correlate_subagent_invocations(
        &parent_turns,
        &child,
        std::slice::from_ref(&child),
        &child_turns,
    );

    assert_eq!(
        matches.len(),
        1,
        "send_message must not split the invocation"
    );
    assert_eq!(matches[0].activation_tool_name, "spawn_agent");
    assert_eq!(
        matches[0].correlation.lifecycle,
        SubagentLifecycle::InterruptedSync
    );
    assert_eq!(matches[0].correlation.reason, "v2_interrupted_sync");
}

#[test]
fn test_v2_parent_interruption_with_completed_child_stays_fail_closed() {
    let parent_turns = build_turns(&v2_interrupted_parent_lines());
    let child_turns = build_turns(&[
        json!({"timestamp":"2026-07-10T08:53:47.300Z","type":"event_msg","payload":{"type":"task_started","turn_id":"child-completed"}}),
        json!({"timestamp":"2026-07-10T08:53:47.400Z","type":"event_msg","payload":{"type":"user_message","message":"work"}}),
        json!({"timestamp":"2026-07-10T08:53:50.300Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"completed despite interrupt"}}),
        json!({"timestamp":"2026-07-10T08:53:50.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"child-completed","last_agent_message":"completed despite interrupt"}}),
    ]);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matches = correlate_subagent_invocations(
        &parent_turns,
        &child,
        std::slice::from_ref(&child),
        &child_turns,
    );

    assert_eq!(matches.len(), 1);
    assert_eq!(
        matches[0].correlation.lifecycle,
        SubagentLifecycle::IdentityCandidate
    );
    assert_eq!(
        matches[0].correlation.reason,
        "v2_interruption_child_abort_missing"
    );
}

#[test]
fn test_v2_parent_interrupt_with_completed_child_and_final_answer_is_completed_sync() {
    let mut parent_lines = v2_interrupted_parent_lines();
    // Insert a FINAL_ANSWER from the child after the interrupt
    let insert_at = parent_lines.len() - 1;
    parent_lines.insert(
            insert_at,
            json!({"timestamp":"2026-07-10T08:53:51.000Z","type":"response_item","payload":{"type":"agent_message","author":"/root/sleep_foreground","recipient":"/root","content":[{"type":"input_text","text":"Message Type: FINAL_ANSWER\nPayload: completed despite interrupt"}]}}),
        );
    let parent_turns = build_turns(&parent_lines);
    let child_turns = build_turns(&[
        json!({"timestamp":"2026-07-10T08:53:47.300Z","type":"event_msg","payload":{"type":"task_started","turn_id":"child-completed"}}),
        json!({"timestamp":"2026-07-10T08:53:47.400Z","type":"event_msg","payload":{"type":"user_message","message":"work"}}),
        json!({"timestamp":"2026-07-10T08:53:50.300Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"completed despite interrupt"}}),
        json!({"timestamp":"2026-07-10T08:53:50.400Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"child-completed","last_agent_message":"completed despite interrupt"}}),
    ]);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matches = correlate_subagent_invocations(
        &parent_turns,
        &child,
        std::slice::from_ref(&child),
        &child_turns,
    );

    assert_eq!(matches.len(), 1);
    assert_eq!(
        matches[0].correlation.lifecycle,
        SubagentLifecycle::CompletedSync
    );
    assert_eq!(matches[0].correlation.reason, "v2_completed_sync");
    assert!(matches[0].correlation.terminal_event.is_some());
}

#[test]
fn test_is_child_sync_in_parent_false_without_wait_intent() {
    let dir = tempfile::tempdir().unwrap();
    let parent_path = dir.path().join("pending-parent.jsonl");
    // Parent has spawn + started activity but no FINAL_ANSWER or wait yet
    let parent_jsonl = r#"{"type":"session_meta","payload":{"id":"parent-1"}}
{"timestamp":"2026-07-10T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-10T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"run child"}}
{"timestamp":"2026-07-10T00:00:01Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","call_id":"spawn-1","arguments":"{\"task_name\":\"reviewer\"}"}}
{"timestamp":"2026-07-10T00:00:01.100Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"spawn-1","agent_thread_id":"child-1","agent_path":"/root/reviewer","kind":"started"}}
{"timestamp":"2026-07-10T00:00:01.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"spawn-1","output":"{\"task_name\":\"/root/reviewer\"}"}}
{"timestamp":"2026-07-10T00:00:05Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"waiting"}}
"#;
    std::fs::write(&parent_path, parent_jsonl).unwrap();

    assert!(!is_child_sync_in_parent(
        dir.path(),
        "parent-1",
        "child-1",
        Some("/root/reviewer")
    ));
}

#[test]
fn test_v2_interruption_target_conflict_fails_closed() {
    let mut lines = v2_interrupted_parent_lines();
    for entry in &mut lines {
        if entry["payload"]["type"] == "function_call"
            && entry["payload"]["name"] == "interrupt_agent"
        {
            entry["payload"]["arguments"] = json!("{\"target\":\"/root/other\"}");
        }
    }
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::IdentityCandidate);
    assert_eq!(matched.reason, "v2_terminal_message_missing");
}

#[test]
fn test_v2_interruption_without_wait_fails_closed() {
    let mut lines = v2_interrupted_parent_lines();
    lines.retain(|entry| {
        entry["payload"]["name"] != "wait_agent" && entry["payload"]["call_id"] != "call-wait"
    });
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::IdentityCandidate);
    assert_eq!(matched.reason, "v2_interrupt_wait_intent_missing");
}

#[test]
fn test_v2_repeated_interruptions_preserve_all_exact_evidence() {
    let mut lines = v2_interrupted_parent_lines();
    let insert_at = lines.len() - 1;
    lines.insert(
            insert_at,
            json!({"timestamp":"2026-07-10T08:53:51.000Z","type":"response_item","payload":{"type":"function_call","name":"interrupt_agent","arguments":"{\"target\":\"child-v2\"}","call_id":"call-interrupt-2"}}),
        );
    lines.insert(
            insert_at + 1,
            json!({"timestamp":"2026-07-10T08:53:51.100Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-interrupt-2","agent_thread_id":"child-v2","agent_path":"/root/sleep_foreground","kind":"interrupted"}}),
        );
    lines.insert(
            insert_at + 2,
            json!({"timestamp":"2026-07-10T08:53:51.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-interrupt-2","output":"{\"interrupted\":true}"}}),
        );
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::IdentityCandidate);
    assert_eq!(matched.reason, "v2_interruption_candidate");
    assert_eq!(
        matched
            .interruption_evidence
            .iter()
            .map(|evidence| evidence.interrupt_call_id.as_str())
            .collect::<Vec<_>>(),
        vec!["call-interrupt", "call-interrupt-2"]
    );
}

#[test]
fn test_invocation_correlation_uses_activation_call_id_and_preserves_each_terminal() {
    let parent_lines = vec![
        json!({"timestamp":"2026-07-16T09:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"parent-spawn"}}),
        json!({"timestamp":"2026-07-16T09:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"start reviewer"}}),
        json!({"timestamp":"2026-07-16T09:00:01.000Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{\"task_name\":\"reviewer\"}","call_id":"call-spawn"}}),
        json!({"timestamp":"2026-07-16T09:00:01.100Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-spawn","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"started"}}),
        json!({"timestamp":"2026-07-16T09:00:01.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-spawn","output":"{\"task_name\":\"/root/reviewer\"}"}}),
        json!({"timestamp":"2026-07-16T09:00:02.000Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"timeout_ms\":1000}","call_id":"wait-spawn"}}),
        json!({"timestamp":"2026-07-16T09:00:03.000Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait-spawn","output":"{\"timed_out\":true}"}}),
        json!({"timestamp":"2026-07-16T09:00:04.100Z","type":"response_item","payload":{"type":"function_call","name":"interrupt_agent","arguments":"{\"target\":\"/root/reviewer\"}","call_id":"call-interrupt"}}),
        json!({"timestamp":"2026-07-16T09:00:04.200Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-interrupt","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"interrupted"}}),
        json!({"timestamp":"2026-07-16T09:00:04.300Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-interrupt","output":"{\"interrupted\":true}"}}),
        json!({"timestamp":"2026-07-16T09:00:05.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"parent-spawn","last_agent_message":"interrupted"}}),
        json!({"timestamp":"2026-07-16T10:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"parent-followup"}}),
        json!({"timestamp":"2026-07-16T10:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"resume reviewer"}}),
        json!({"timestamp":"2026-07-16T10:00:01.000Z","type":"response_item","payload":{"type":"function_call","name":"followup_task","arguments":"{\"target\":\"/root/reviewer\",\"message\":\"resume\"}","call_id":"call-followup"}}),
        json!({"timestamp":"2026-07-16T10:00:01.100Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-followup","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"interacted"}}),
        json!({"timestamp":"2026-07-16T10:00:01.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-followup","output":"{\"delivered\":true}"}}),
        json!({"timestamp":"2026-07-16T10:00:02.000Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"timeout_ms\":1000}","call_id":"wait-followup"}}),
        json!({"timestamp":"2026-07-16T10:00:03.000Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait-followup","output":"{\"timed_out\":false}"}}),
        json!({"timestamp":"2026-07-16T10:00:04.000Z","type":"response_item","payload":{"type":"agent_message","author":"/root/reviewer","recipient":"/root","content":[{"type":"input_text","text":"Message Type: FINAL_ANSWER\nPayload: approved"}]}}),
        json!({"timestamp":"2026-07-16T10:00:05.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"parent-followup","last_agent_message":"done"}}),
    ];
    let child_lines = vec![
        json!({"timestamp":"2026-07-16T09:00:01.000Z","type":"session_meta","payload":{"id":"child-reviewer","multi_agent_version":"v2","source":{"subagent":{"thread_spawn":{"parent_thread_id":"parent","depth":1,"agent_path":"/root/reviewer","agent_nickname":"Gibbs"}}}}}),
        json!({"timestamp":"2026-07-16T09:00:01.300Z","type":"event_msg","payload":{"type":"task_started","turn_id":"child-aborted"}}),
        json!({"timestamp":"2026-07-16T09:00:01.400Z","type":"event_msg","payload":{"type":"user_message","message":"review"}}),
        json!({"timestamp":"2026-07-16T09:00:04.000Z","type":"event_msg","payload":{"type":"turn_aborted","turn_id":"child-aborted"}}),
        json!({"timestamp":"2026-07-16T10:00:01.300Z","type":"event_msg","payload":{"type":"task_started","turn_id":"child-completed"}}),
        json!({"timestamp":"2026-07-16T10:00:01.400Z","type":"event_msg","payload":{"type":"user_message","message":"resume"}}),
        json!({"timestamp":"2026-07-16T10:00:03.500Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"approved"}}),
        json!({"timestamp":"2026-07-16T10:00:04.500Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"child-completed","last_agent_message":"approved"}}),
    ];
    let parent_turns = build_turns(&parent_lines);
    let child_turns = build_turns(&child_lines);
    let child = child_identity("child-reviewer", "/root/reviewer");
    let matches = correlate_subagent_invocations(
        &parent_turns,
        &child,
        std::slice::from_ref(&child),
        &child_turns,
    );

    assert_eq!(matches.len(), 2);
    assert_eq!(matches[0].invocation_key, "child-reviewer:call-spawn");
    assert_eq!(matches[0].activation_tool_name, "spawn_agent");
    assert_eq!(
        matches[0].correlation.activation_call_id.as_deref(),
        Some("call-spawn")
    );
    assert_eq!(
        matches[0].correlation.lifecycle,
        SubagentLifecycle::InterruptedSync
    );
    assert_eq!(
        matches[0].correlation.interruption_evidence[0]
            .child_terminal_timestamp
            .as_deref(),
        Some("2026-07-16T09:00:04.000Z")
    );
    assert_eq!(matches[1].invocation_key, "child-reviewer:call-followup");
    assert_eq!(matches[1].activation_tool_name, "followup_task");
    assert_eq!(
        matches[1].correlation.activation_call_id.as_deref(),
        Some("call-followup")
    );
    assert_eq!(
        matches[1].correlation.lifecycle,
        SubagentLifecycle::CompletedSync
    );
}

#[test]
fn test_v2_activity_path_conflict_fails_closed() {
    let turns = build_turns(&v2_parent_lines("child-v2", "/root/other"));
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::AmbiguousOrOrphan);
    assert_eq!(matched.reason, "activity_path_conflict");
}

#[test]
fn test_v2_activity_spawn_output_path_conflict_fails_closed() {
    let mut lines = v2_parent_lines("child-v2", "/root/sleep_foreground");
    for entry in &mut lines {
        if entry["payload"]["type"] == "function_call_output"
            && entry["payload"]["call_id"] == "call-spawn"
        {
            entry["payload"]["output"] = json!("{\"task_name\":\"/root/other\"}");
        }
    }
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::AmbiguousOrOrphan);
    assert_eq!(matched.reason, "activity_spawn_path_conflict");
}

#[test]
fn test_v2_activity_conflicting_legacy_identity_fails_closed() {
    let mut lines = v2_parent_lines("child-v2", "/root/sleep_foreground");
    for entry in &mut lines {
        if entry["payload"]["type"] == "function_call_output"
            && entry["payload"]["call_id"] == "call-spawn"
        {
            entry["payload"]["output"] =
                json!("{\"task_name\":\"/root/sleep_foreground\",\"agent_id\":\"other-child\"}");
        }
    }
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::AmbiguousOrOrphan);
    assert_eq!(matched.reason, "activity_strong_identity_conflict");
}

#[test]
fn test_v2_terminal_for_other_recipient_is_ignored() {
    let mut lines = v2_parent_lines("child-v2", "/root/sleep_foreground");
    for entry in &mut lines {
        if entry["payload"]["type"] == "agent_message" {
            entry["payload"]["recipient"] = json!("/root/other");
        }
    }
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::IdentityCandidate);
    assert_eq!(matched.reason, "v2_terminal_message_missing");
}

#[test]
fn test_v2_exact_path_fallback_without_activity() {
    let mut lines = v2_parent_lines("child-v2", "/root/sleep_foreground");
    lines.retain(|entry| entry["payload"]["type"] != "sub_agent_activity");
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::CompletedSync);
    assert_eq!(
        matched.identity_source,
        Some(SubagentIdentitySource::V2PathFallback)
    );
}

#[test]
fn test_v2_path_fallback_rejects_conflicting_new_thread_id() {
    let mut lines = v2_parent_lines("child-v2", "/root/sleep_foreground");
    lines.retain(|entry| entry["payload"]["type"] != "sub_agent_activity");
    for entry in &mut lines {
        if entry["payload"]["type"] == "function_call_output"
            && entry["payload"]["call_id"] == "call-spawn"
        {
            entry["payload"]["output"] = json!(
                "{\"task_name\":\"/root/sleep_foreground\",\"new_thread_id\":\"other-child\"}"
            );
        }
    }
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::AmbiguousOrOrphan);
    assert_eq!(matched.reason, "path_conflicts_with_strong_identity");
}

#[test]
fn test_v2_terminal_from_unknown_author_is_ignored() {
    let mut lines = v2_parent_lines("child-v2", "/root/sleep_foreground");
    for entry in &mut lines {
        if entry["payload"]["type"] == "agent_message" {
            entry["payload"]["author"] = json!("/root/unknown");
        }
    }
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::IdentityCandidate);
    assert_eq!(matched.reason, "v2_terminal_message_missing");
}

#[test]
fn test_v2_multiple_children_correlate_when_terminals_arrive_in_reverse_order() {
    let lines = vec![
        json!({"timestamp":"2026-07-10T08:53:46Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-v2"}}),
        json!({"timestamp":"2026-07-10T08:53:47Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{\"task_name\":\"first\"}","call_id":"spawn-first"}}),
        json!({"timestamp":"2026-07-10T08:53:47.1Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"spawn-first","agent_thread_id":"child-first","agent_path":"/root/first","kind":"started"}}),
        json!({"timestamp":"2026-07-10T08:53:47.2Z","type":"response_item","payload":{"type":"function_call_output","call_id":"spawn-first","output":"{\"task_name\":\"/root/first\"}"}}),
        json!({"timestamp":"2026-07-10T08:53:48Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{\"task_name\":\"second\"}","call_id":"spawn-second"}}),
        json!({"timestamp":"2026-07-10T08:53:48.1Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"spawn-second","agent_thread_id":"child-second","agent_path":"/root/second","kind":"started"}}),
        json!({"timestamp":"2026-07-10T08:53:48.2Z","type":"response_item","payload":{"type":"function_call_output","call_id":"spawn-second","output":"{\"task_name\":\"/root/second\"}"}}),
        json!({"timestamp":"2026-07-10T08:53:49Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"timeout_ms\":1000}","call_id":"wait"}}),
        json!({"timestamp":"2026-07-10T08:53:50Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait","output":"{\"timed_out\":false}"}}),
        json!({"timestamp":"2026-07-10T08:53:51Z","type":"response_item","payload":{"type":"agent_message","author":"/root/second","recipient":"/root","content":[{"type":"input_text","text":"Message Type: FINAL_ANSWER\nPayload: second"}]}}),
        json!({"timestamp":"2026-07-10T08:53:52Z","type":"response_item","payload":{"type":"agent_message","author":"/root/first","recipient":"/root","content":[{"type":"input_text","text":"Message Type: FINAL_ANSWER\nPayload: first"}]}}),
        json!({"timestamp":"2026-07-10T08:53:53Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-v2","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&lines);
    let first = child_identity("child-first", "/root/first");
    let second = child_identity("child-second", "/root/second");
    let siblings = vec![first.clone(), second.clone()];

    for child in [&first, &second] {
        let matched = correlate_subagent(&turns[0], child, &siblings);
        assert_eq!(matched.lifecycle, SubagentLifecycle::CompletedSync);
        assert_eq!(
            matched.identity_source,
            Some(SubagentIdentitySource::V2Activity)
        );
        assert_eq!(matched.reason, "v2_completed_sync");
    }
}

#[test]
fn test_v2_path_fallback_is_exact_not_prefix() {
    let mut lines = v2_parent_lines("child-v2", "/root/sleep_foreground");
    lines.retain(|entry| entry["payload"]["type"] != "sub_agent_activity");
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::AmbiguousOrOrphan);
    assert_eq!(matched.reason, "no_identity_match");
}

#[test]
fn test_v2_typed_terminal_is_required_after_timeout_wait() {
    let mut lines = v2_parent_lines("child-v2", "/root/sleep_foreground");
    lines.retain(|entry| entry["payload"]["type"] != "agent_message");
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::IdentityCandidate);
    assert_eq!(matched.reason, "v2_terminal_message_missing");
}

#[test]
fn test_v2_ordinary_assistant_final_answer_text_cannot_complete_child() {
    let mut lines = v2_parent_lines("child-v2", "/root/sleep_foreground");
    lines.retain(|entry| entry["payload"]["type"] != "agent_message");
    lines.insert(
            lines.len() - 1,
            json!({"timestamp":"2026-07-10T08:54:08.500Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"Message Type: FINAL_ANSWER from /root/sleep_foreground"}}),
        );
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::IdentityCandidate);
    assert_eq!(matched.reason, "v2_terminal_message_missing");
}

#[test]
fn test_v2_exact_activity_and_typed_terminal_complete_without_wait() {
    let mut lines = v2_parent_lines("child-v2", "/root/sleep_foreground");
    lines.retain(|entry| {
        entry["payload"]["name"] != "wait_agent" && entry["payload"]["call_id"] != "call-wait"
    });
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::CompletedSync);
    assert_eq!(matched.reason, "v2_completed_sync_without_wait");
    assert!(matched.wait_call_ids.is_empty());
}

#[test]
fn test_v2_cross_turn_followup_completes_original_spawn() {
    let lines = vec![
        json!({"timestamp":"2026-07-16T09:46:30Z","type":"event_msg","payload":{"type":"task_started","turn_id":"parent-spawn"}}),
        json!({"timestamp":"2026-07-16T09:46:31Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{\"task_name\":\"reviewer\"}","call_id":"call-spawn"}}),
        json!({"timestamp":"2026-07-16T09:46:31.1Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-spawn","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"started"}}),
        json!({"timestamp":"2026-07-16T09:46:31.2Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-spawn","output":"{\"task_name\":\"/root/reviewer\"}"}}),
        json!({"timestamp":"2026-07-16T09:46:32Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"timeout_ms\":1000}","call_id":"wait-spawn"}}),
        json!({"timestamp":"2026-07-16T09:46:33Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait-spawn","output":"{\"timed_out\":true}"}}),
        json!({"timestamp":"2026-07-16T09:58:36Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"parent-spawn","last_agent_message":"blocked"}}),
        json!({"timestamp":"2026-07-16T09:58:53Z","type":"event_msg","payload":{"type":"task_started","turn_id":"parent-followup"}}),
        json!({"timestamp":"2026-07-16T10:07:44Z","type":"response_item","payload":{"type":"function_call","name":"followup_task","arguments":"{\"target\":\"/root/reviewer\",\"message\":\"recheck\"}","call_id":"call-followup"}}),
        json!({"timestamp":"2026-07-16T10:07:44.1Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-followup","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"interacted"}}),
        json!({"timestamp":"2026-07-16T10:07:44.2Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-followup","output":"{\"delivered\":true}"}}),
        json!({"timestamp":"2026-07-16T10:07:45Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"timeout_ms\":1000}","call_id":"wait-followup"}}),
        json!({"timestamp":"2026-07-16T10:07:46Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait-followup","output":"{\"timed_out\":false}"}}),
        json!({"timestamp":"2026-07-16T10:07:47Z","type":"response_item","payload":{"type":"agent_message","author":"/root/reviewer","recipient":"/root","content":[{"type":"input_text","text":"Message Type: FINAL_ANSWER\nPayload: approved"}]}}),
        json!({"timestamp":"2026-07-16T10:07:48Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"parent-followup","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&lines);
    let child = child_identity("child-reviewer", "/root/reviewer");
    let siblings = vec![child.clone()];

    assert_eq!(
        correlate_subagent(&turns[0], &child, &siblings).reason,
        "v2_terminal_message_missing"
    );
    assert_eq!(
        correlate_subagent(&turns[1], &child, &siblings).reason,
        "no_identity_match"
    );
    assert_eq!(
        subagent_anchor_turn_index(&turns, &child, &siblings),
        Some(0)
    );

    let matched = correlate_subagent_across_turns(&turns, 0, &child, &siblings);
    assert_eq!(matched.lifecycle, SubagentLifecycle::CompletedSync);
    assert_eq!(matched.reason, "v2_completed_sync");
    assert_eq!(
        matched.wait_call_ids,
        vec!["wait-spawn".to_string(), "wait-followup".to_string()]
    );
    assert!(matches!(
        matched.terminal_event,
        Some(CollabAgentEvent::AgentMessage { ref content, .. }) if content.contains("approved")
    ));
}

#[test]
fn test_interacted_activity_is_strong_identity_evidence_without_spawn_window() {
    let messages = vec![
        json!({"timestamp":"2026-07-16T10:07:44Z","type":"event_msg","payload":{"type":"task_started","turn_id":"followup-turn"}}),
        json!({"timestamp":"2026-07-16T10:07:44.1Z","type":"response_item","payload":{"type":"function_call","name":"followup_task","arguments":"{\"target\":\"/root/reviewer\",\"message\":\"recheck\"}","call_id":"call-followup"}}),
        json!({"timestamp":"2026-07-16T10:07:44.2Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-followup","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"interacted"}}),
        json!({"timestamp":"2026-07-16T10:07:45Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"followup-turn","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&messages);

    assert_eq!(
        collect_subagent_identities(&turns),
        vec![SubagentIdentity {
            session_id: "child-reviewer".to_string(),
            agent_path: Some("/root/reviewer".to_string()),
        }]
    );
    assert_eq!(
        subagent_anchor_turn_index(
            &turns,
            &SubagentIdentity {
                session_id: "child-reviewer".to_string(),
                agent_path: Some("/root/reviewer".to_string()),
            },
            &collect_subagent_identities(&turns),
        ),
        None,
        "followup identity evidence must not invent an initial spawn anchor"
    );
}

#[test]
fn test_v2_reused_path_fallback_is_ambiguous() {
    let mut lines = v2_parent_lines("child-v2", "/root/sleep_foreground");
    lines.retain(|entry| entry["payload"]["type"] != "sub_agent_activity");
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let sibling = child_identity("child-other", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, &[child.clone(), sibling]);
    assert_eq!(matched.lifecycle, SubagentLifecycle::AmbiguousOrOrphan);
    assert_eq!(matched.reason, "ambiguous_path_fallback");
}

#[test]
fn test_activity_uuid_without_matching_spawn_blocks_path_fallback() {
    let mut lines = v2_parent_lines("child-v2", "/root/sleep_foreground");
    for entry in &mut lines {
        if entry["payload"]["type"] == "sub_agent_activity" {
            entry["payload"]["event_id"] = json!("missing-spawn-call");
        }
    }
    let turns = build_turns(&lines);
    let child = child_identity("child-v2", "/root/sleep_foreground");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::AmbiguousOrOrphan);
    assert_eq!(matched.reason, "activity_spawn_missing");
}

#[test]
fn test_legacy_agent_id_and_targeted_wait_need_no_typed_terminal() {
    let lines = vec![
        json!({"timestamp":"2026-07-10T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"legacy"}}),
        json!({"timestamp":"2026-07-10T08:00:00.100Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{}","call_id":"legacy-spawn"}}),
        json!({"timestamp":"2026-07-10T08:00:00.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"legacy-spawn","output":"{\"agent_id\":\"legacy-child\"}"}}),
        json!({"timestamp":"2026-07-10T08:00:00.300Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"targets\":[\"legacy-child\"]}","call_id":"legacy-wait"}}),
        json!({"timestamp":"2026-07-10T08:00:01.000Z","type":"response_item","payload":{"type":"function_call_output","call_id":"legacy-wait","output":"done"}}),
        json!({"timestamp":"2026-07-10T08:00:02.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"legacy","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&lines);
    let child = child_identity("legacy-child", "/root/legacy");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::CompletedSync);
    assert_eq!(matched.protocol, Some(SubagentProtocol::Legacy));
    assert_eq!(
        matched.identity_source,
        Some(SubagentIdentitySource::LegacyAgentId)
    );
    assert!(matched.terminal_event.is_none());
}

#[test]
fn test_legacy_new_thread_id_output_is_supported() {
    let lines = vec![
        json!({"timestamp":"2026-07-10T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"legacy"}}),
        json!({"timestamp":"2026-07-10T08:00:00.100Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{}","call_id":"legacy-spawn"}}),
        json!({"timestamp":"2026-07-10T08:00:00.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"legacy-spawn","output":"{\"new_thread_id\":\"legacy-child\"}"}}),
        json!({"timestamp":"2026-07-10T08:00:00.300Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"targets\":[\"legacy-child\"]}","call_id":"legacy-wait"}}),
        json!({"timestamp":"2026-07-10T08:00:01.000Z","type":"response_item","payload":{"type":"function_call_output","call_id":"legacy-wait","output":"done"}}),
        json!({"timestamp":"2026-07-10T08:00:02.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"legacy","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&lines);
    let child = child_identity("legacy-child", "/root/legacy");
    let matched = correlate_subagent(&turns[0], &child, std::slice::from_ref(&child));
    assert_eq!(matched.lifecycle, SubagentLifecycle::CompletedSync);
    assert_eq!(
        matched.identity_source,
        Some(SubagentIdentitySource::LegacyNewThreadId)
    );
}
