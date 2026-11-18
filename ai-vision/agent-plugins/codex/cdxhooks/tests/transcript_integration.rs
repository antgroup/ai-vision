use std::fs;
use std::path::PathBuf;

use cdxhooks::common::message_format::{build_input_messages, build_output_messages};
use cdxhooks::common::otel::{
    build_otel_export_request, build_otel_export_request_for_turn, deterministic_tool_span_id,
    merge_subagent_sessions_into_otel, merge_subagent_sessions_into_otel_across_turns,
    SubagentChild, TurnReplayOpts,
};
use cdxhooks::common::transcript::{
    build_turns, collect_subagent_identities, correlate_subagent, extract_session_meta_ids,
    normalize_turn_semantics, CollabAgentEvent, CollabInvocationState, InvocationEndSource,
    InvocationStartSource, InvocationTerminalSource, SkillInvocationSource, SubagentIdentity,
    SubagentLifecycle, SubagentProtocol, Turn, TurnTerminalState, TurnTrigger,
};
use cdxhooks::hooks::langfuse::LangfuseConfig;

fn load_fixture_lines(fixture_name: &str) -> Vec<serde_json::Value> {
    let fixture_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(fixture_name);
    let content = fs::read_to_string(&fixture_path)
        .unwrap_or_else(|err| panic!("Failed to read fixture file {}: {}", fixture_name, err));
    content
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| {
            serde_json::from_str(line)
                .unwrap_or_else(|err| panic!("Failed to parse JSONL line '{}': {}", line, err))
        })
        .collect()
}

fn load_fixture_content(fixture_name: &str) -> String {
    let fixture_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(fixture_name);
    fs::read_to_string(&fixture_path)
        .unwrap_or_else(|err| panic!("Failed to read fixture file {}: {}", fixture_name, err))
}

fn spans(body: &serde_json::Value) -> &[serde_json::Value] {
    body.pointer("/resourceSpans/0/scopeSpans/0/spans")
        .and_then(serde_json::Value::as_array)
        .expect("spans")
}

fn span_attr<'a>(span: &'a serde_json::Value, key: &str) -> Option<&'a str> {
    span["attributes"].as_array()?.iter().find_map(|attr| {
        (attr["key"].as_str() == Some(key))
            .then(|| {
                attr["value"]["stringValue"]
                    .as_str()
                    .or_else(|| attr["value"]["intValue"].as_str())
            })
            .flatten()
    })
}

fn has_span_attr(span: &serde_json::Value, key: &str) -> bool {
    span["attributes"]
        .as_array()
        .is_some_and(|attrs| attrs.iter().any(|attr| attr["key"].as_str() == Some(key)))
}

fn child_from_fixture(
    fixture_name: &str,
    session_id: &str,
    nickname: &str,
    agent_path: &str,
) -> SubagentChild {
    SubagentChild {
        session_id: session_id.to_string(),
        turns: build_turns(&load_fixture_lines(fixture_name)),
        nickname: nickname.to_string(),
        role: String::new(),
        agent_path: Some(agent_path.to_string()),
    }
}

fn collab_parent_body(parent_turns: &[Turn]) -> serde_json::Value {
    let config = LangfuseConfig {
        public_key: None,
        secret_key: None,
        host: "http://127.0.0.1".to_string(),
        headers: None,
    };
    build_otel_export_request(
        parent_turns,
        "parent-collab",
        "fixture-user",
        &config,
        "2026-08-17T02:48:49.467Z",
        "test",
        0,
    )
}

fn turn_by_id<'a>(turns: &'a [Turn], turn_id: &str) -> &'a Turn {
    turns
        .iter()
        .find(|turn| turn.turn_id == turn_id)
        .unwrap_or_else(|| panic!("missing turn {turn_id}"))
}

fn skill_spans_named<'a>(
    body: &'a serde_json::Value,
    skill_name: &str,
) -> Vec<&'a serde_json::Value> {
    let expected = format!("skill {skill_name}");
    spans(body)
        .iter()
        .filter(|span| span["name"].as_str() == Some(expected.as_str()))
        .collect()
}

#[test]
fn collab_same_turn_fixture_locks_native_schema_without_parsing_outer_exec_text() {
    let parent_lines = load_fixture_lines("codex_collab_tool_call_same_turn_parent.jsonl");
    let child_content = load_fixture_content("codex_collab_tool_call_same_turn_child.jsonl");
    let parent_turns = build_turns(&parent_lines);

    assert_eq!(parent_turns.len(), 1);
    assert_eq!(parent_turns[0].turn_id, "turn-collab-parent");
    assert_eq!(parent_turns[0].tool_calls.len(), 2);
    assert!(parent_turns[0]
        .tool_calls
        .iter()
        .all(|tool| tool.name == "exec"));

    let collab_items: Vec<_> = parent_lines
        .iter()
        .filter_map(|line| line.pointer("/payload/item"))
        .filter(|item| {
            item.get("type").and_then(serde_json::Value::as_str) == Some("CollabAgentToolCall")
        })
        .collect();
    assert_eq!(collab_items.len(), 2);
    assert_eq!(collab_items[0]["tool"], "spawn_agent");
    assert_eq!(collab_items[1]["tool"], "wait");
    assert_eq!(collab_items[0]["receiver_thread_ids"][0], "child-collab");
    assert_eq!(
        collab_items[1]["agents_states"]["child-collab"]["completed"],
        "done"
    );

    let child_meta = extract_session_meta_ids(&child_content);
    assert_eq!(child_meta.session_id.as_deref(), Some("child-collab"));
    assert_eq!(
        child_meta.parent_thread_id.as_deref(),
        Some("parent-collab")
    );
}

#[test]
fn turn_trigger_live_and_single_turn_replay_paths_match() {
    let turns = build_turns(&load_fixture_lines(
        "codex_realtime_open_turn_exec_call.jsonl",
    ));
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].trigger, TurnTrigger::Interactive);

    let config = LangfuseConfig {
        public_key: None,
        secret_key: None,
        host: "http://127.0.0.1".to_string(),
        headers: None,
    };
    let live_body = build_otel_export_request(
        &turns,
        "session-redacted",
        "fixture-user",
        &config,
        "2026-07-29T01:00:05.000Z",
        "test",
        0,
    );
    let replay_body = build_otel_export_request_for_turn(
        &turns[0],
        1,
        "session-redacted",
        "fixture-user",
        "test",
        None,
    );

    assert_eq!(
        span_attr(&spans(&live_body)[0], "agentic.turn.trigger"),
        Some("interactive")
    );
    assert_eq!(
        span_attr(&spans(&replay_body)[0], "agentic.turn.trigger"),
        Some("interactive")
    );
}

#[test]
fn collab_same_turn_fixture_merges_one_agent_without_synthetic_collab_tools() {
    let parent_turns = build_turns(&load_fixture_lines(
        "codex_collab_tool_call_same_turn_parent.jsonl",
    ));
    let child = child_from_fixture(
        "codex_collab_tool_call_same_turn_child.jsonl",
        "child-collab",
        "Archimedes",
        "/root/foreground_child",
    );
    let mut body = collab_parent_body(&parent_turns);

    let result = merge_subagent_sessions_into_otel(
        &mut body,
        &parent_turns[0],
        std::slice::from_ref(&child),
        "fixture-user",
        "test",
    );

    assert_eq!(result.merged_sync_ids, vec!["child-collab"]);
    assert_eq!(
        result.merged_subagent_invocation_ids,
        vec!["child-collab:collab-spawn-1"]
    );
    assert_eq!(result.invocation_outcomes.len(), 1);
    assert_eq!(result.invocation_outcomes[0].status, "completed");

    let all_spans = spans(&body);
    let root = all_spans
        .iter()
        .find(|span| span_attr(span, "gen_ai.span.kind") == Some("CHAT"))
        .expect("parent CHAT root");
    let root_id = root["spanId"].as_str().expect("root span id");
    let agents = all_spans
        .iter()
        .filter(|span| span_attr(span, "gen_ai.span.kind") == Some("AGENT"))
        .collect::<Vec<_>>();
    assert_eq!(agents.len(), 1);
    let agent = agents[0];
    let agent_id = agent["spanId"].as_str().expect("agent span id");
    assert_eq!(agent["name"], "agent Archimedes");
    assert_eq!(agent["parentSpanId"], root_id);
    assert_eq!(
        span_attr(agent, "agentic.subagent.id"),
        Some("child-collab")
    );
    assert_eq!(
        span_attr(agent, "agentic.subagent.child_thread_id"),
        Some("child-collab")
    );
    assert_eq!(
        span_attr(agent, "agentic.subagent.parent_tool_call_id"),
        Some("collab-spawn-1")
    );
    assert_eq!(
        span_attr(agent, "agentic.subagent.nickname"),
        Some("Archimedes")
    );
    assert_eq!(span_attr(agent, "agentic.subagent.role"), Some("executor"));
    assert_eq!(
        span_attr(agent, "agentic.subagent.status"),
        Some("completed")
    );
    assert_eq!(span_attr(agent, "gen_ai.tool.call.result"), Some("done"));

    let outer_execs = all_spans
        .iter()
        .filter(|span| span_attr(span, "agentic.tool.raw_name") == Some("exec"))
        .collect::<Vec<_>>();
    assert_eq!(outer_execs.len(), 2);
    assert!(outer_execs
        .iter()
        .all(|span| span["parentSpanId"].as_str() == Some(root_id)));
    assert!(!all_spans.iter().any(|span| {
        matches!(
            span_attr(span, "agentic.tool.raw_name"),
            Some("spawn_agent" | "wait" | "wait_agent")
        )
    }));

    let child_tool = all_spans
        .iter()
        .find(|span| span_attr(span, "gen_ai.tool.call.id") == Some("call-child-command"))
        .expect("child command TOOL");
    assert_eq!(child_tool["parentSpanId"], agent_id);
    assert!(all_spans.iter().any(|span| {
        span_attr(span, "gen_ai.span.kind") == Some("LLM")
            && span["parentSpanId"].as_str() == Some(agent_id)
    }));

    let span_ids = all_spans
        .iter()
        .filter_map(|span| span["spanId"].as_str())
        .collect::<std::collections::HashSet<_>>();
    assert!(all_spans.iter().all(|span| {
        span["parentSpanId"]
            .as_str()
            .is_none_or(|parent| parent.is_empty() || span_ids.contains(parent))
    }));
}

#[test]
fn collab_same_turn_open_child_defers_without_partial_agent_tree() {
    let parent_turns = build_turns(&load_fixture_lines(
        "codex_collab_tool_call_same_turn_parent.jsonl",
    ));
    let mut child = child_from_fixture(
        "codex_collab_tool_call_same_turn_child.jsonl",
        "child-collab",
        "Archimedes",
        "/root/foreground_child",
    );
    let child_turn = child.turns.last_mut().expect("child turn");
    child_turn.terminal_state = TurnTerminalState::Open;
    child_turn.completed = false;
    let mut body = collab_parent_body(&parent_turns);

    let result = merge_subagent_sessions_into_otel(
        &mut body,
        &parent_turns[0],
        &[child],
        "fixture-user",
        "test",
    );

    assert_eq!(result.deferred_sync_ids, vec!["child-collab"]);
    assert!(result.merged_subagent_invocation_ids.is_empty());
    assert!(result
        .correlation_reasons
        .iter()
        .any(|(_, reason)| reason == "child_terminal_pending"));
    assert!(!spans(&body)
        .iter()
        .any(|span| span_attr(span, "gen_ai.span.kind") == Some("AGENT")));
    assert!(!spans(&body)
        .iter()
        .any(|span| { span_attr(span, "gen_ai.tool.call.id") == Some("call-child-command") }));
}

#[test]
fn collab_same_turn_error_emits_error_agent_without_completed_status() {
    let mut parent_lines = load_fixture_lines("codex_collab_tool_call_same_turn_parent.jsonl");
    let wait = parent_lines
        .iter_mut()
        .find(|line| {
            line.pointer("/payload/item/id")
                .and_then(serde_json::Value::as_str)
                == Some("collab-wait-1")
        })
        .expect("native wait event");
    wait["payload"]["item"]["agents_states"]["child-collab"] =
        serde_json::json!({"error": "child failed"});
    let parent_turns = build_turns(&parent_lines);
    let child = child_from_fixture(
        "codex_collab_tool_call_same_turn_child.jsonl",
        "child-collab",
        "Archimedes",
        "/root/foreground_child",
    );
    let mut body = collab_parent_body(&parent_turns);

    let result = merge_subagent_sessions_into_otel(
        &mut body,
        &parent_turns[0],
        &[child],
        "fixture-user",
        "test",
    );

    assert_eq!(result.invocation_outcomes.len(), 1);
    assert_eq!(result.invocation_outcomes[0].status, "error");
    let agent = spans(&body)
        .iter()
        .find(|span| span_attr(span, "gen_ai.span.kind") == Some("AGENT"))
        .expect("error AGENT span");
    assert_eq!(span_attr(agent, "agentic.subagent.status"), Some("error"));
    assert_eq!(span_attr(agent, "error.type"), Some("subagent_error"));
    assert_eq!(
        span_attr(agent, "gen_ai.tool.call.result"),
        Some("child failed")
    );
    assert_eq!(agent["status"]["code"], 2);
}

#[test]
fn collab_same_turn_malformed_timing_does_not_fabricate_agent_duration() {
    let mut parent_turns = build_turns(&load_fixture_lines(
        "codex_collab_tool_call_same_turn_parent.jsonl",
    ));
    let wait = parent_turns[0]
        .collab_agent_events
        .iter_mut()
        .find_map(|event| match event {
            CollabAgentEvent::ToolInvocation(invocation) if invocation.tool_name == "wait" => {
                Some(invocation)
            }
            _ => None,
        })
        .expect("native wait evidence");
    wait.state = CollabInvocationState::Malformed;
    wait.completed_at_unix_nano = None;
    wait.reason = "malformed_timing".to_string();
    let child = child_from_fixture(
        "codex_collab_tool_call_same_turn_child.jsonl",
        "child-collab",
        "Archimedes",
        "/root/foreground_child",
    );
    let mut body = collab_parent_body(&parent_turns);

    let result = merge_subagent_sessions_into_otel(
        &mut body,
        &parent_turns[0],
        &[child],
        "fixture-user",
        "test",
    );

    assert_eq!(result.deferred_sync_ids, vec!["child-collab"]);
    assert!(result
        .correlation_reasons
        .iter()
        .any(|(_, reason)| reason == "malformed_timing"));
    assert!(!spans(&body)
        .iter()
        .any(|span| span_attr(span, "gen_ai.span.kind") == Some("AGENT")));
}

#[test]
fn test_multi_turn_codex_fixture() {
    let parsed_lines = load_fixture_lines("codex_multi_turn.jsonl");
    let turns = build_turns(&parsed_lines);

    assert_eq!(turns.len(), 2);
    assert_eq!(turns[0].turn_id, "turn-codex-1");
    assert_eq!(turns[0].chat_id.as_deref(), Some("turn-codex-1"));
    assert_eq!(turns[0].user_text, "/compact please summarize");
    assert_eq!(turns[0].tool_calls.len(), 4);
    assert_eq!(turns[0].assistants.last().unwrap().text, "第一轮完成");
    assert_eq!(turns[1].turn_id, "turn-codex-2");
    assert_eq!(turns[1].chat_id.as_deref(), Some("turn-codex-2"));
    assert_eq!(turns[1].tool_calls.len(), 1);
    assert_eq!(turns[1].assistants.last().unwrap().text, "第二轮完成");
}

#[test]
fn test_realistic_commentary_fixture() {
    let parsed_lines = load_fixture_lines("codex_commentary.jsonl");
    let turns = build_turns(&parsed_lines);

    assert_eq!(turns.len(), 1);
    let turn = &turns[0];
    assert_eq!(turn.turn_id, "019d3378-cb9e-7e40-9d1c-4421824c814d");
    assert_eq!(
        turn.chat_id.as_deref(),
        Some("019d3378-cb9e-7e40-9d1c-4421824c814d")
    );
    assert_eq!(turn.tool_calls.len(), 2);
    assert_eq!(turn.assistants.len(), 3);
    assert_eq!(turn.assistants[0].phase, "commentary");
    assert!(turn.assistants[1].text.is_empty());
    assert_eq!(turn.assistants[2].phase, "final_answer");
    assert!(turn.assistants[2].text.contains("codex-hooks-overview.md"));
}

#[test]
fn test_realtime_open_turn_fixture_preserves_native_turn_and_tool_call_id() {
    let parsed_lines = load_fixture_lines("codex_realtime_open_turn_exec_call.jsonl");
    let turns = build_turns(&parsed_lines);

    assert_eq!(turns.len(), 1);
    let turn = &turns[0];
    assert_eq!(turn.turn_id, "turn-redacted");
    assert_eq!(turn.assistants.len(), 3);
    assert_eq!(turn.tool_calls.len(), 1);
    assert_eq!(turn.tool_calls[0].call_id, "call_redacted");
    assert!(!turn.tool_calls[0].ended_timestamp.is_empty());
    assert_eq!(turn.terminal_state, TurnTerminalState::Open);
}

#[test]
fn test_realtime_multi_write_stdin_fixture_preserves_distinct_callbacks() {
    let parsed_lines = load_fixture_lines("codex_realtime_multi_write_stdin.jsonl");
    let turns = build_turns(&parsed_lines);

    assert_eq!(turns.len(), 1);
    let turn = &turns[0];
    assert_eq!(turn.turn_id, "turn-pty-multi-write");
    assert_eq!(turn.tool_calls.len(), 3);
    assert_eq!(turn.tool_calls[0].call_id, "call-pty-1");
    assert_eq!(turn.tool_calls[0].name, "Bash");
    assert!(!turn.tool_calls[0].ended_timestamp.is_empty());
    assert_eq!(
        turn.tool_calls[0].output,
        serde_json::Value::String("Process running. session ID 42".to_string())
    );
    assert_eq!(turn.tool_calls[1].call_id, "call-pty-2");
    assert_eq!(turn.tool_calls[1].output, "first chunk\n");
    assert_eq!(turn.tool_calls[2].call_id, "call-pty-3");
    assert_eq!(turn.tool_calls[2].output, "second chunk\n");
    assert_eq!(turn.assistants.len(), 3);
}

#[test]
fn test_realtime_multi_write_stdin_prefix_is_immutable() {
    let parsed_lines = load_fixture_lines("codex_realtime_multi_write_stdin.jsonl");
    assert_eq!(parsed_lines.len(), 18);

    let prefix_after_first_output = &parsed_lines[..12];
    let prefix_turns = build_turns(prefix_after_first_output);
    assert_eq!(prefix_turns.len(), 1);
    assert_eq!(prefix_turns[0].turn_id, "turn-pty-multi-write");
    assert_eq!(prefix_turns[0].terminal_state, TurnTerminalState::Open);
    assert_eq!(prefix_turns[0].tool_calls.len(), 2);
    assert_eq!(
        prefix_turns[0].tool_calls[1].output,
        serde_json::Value::String("first chunk\n".to_string())
    );

    let full_turns = build_turns(&parsed_lines);
    assert_eq!(full_turns.len(), 1);
    assert_eq!(full_turns[0].terminal_state, TurnTerminalState::Completed);
    assert_eq!(full_turns[0].tool_calls.len(), 3);
    assert_eq!(
        prefix_turns[0].tool_calls,
        full_turns[0].tool_calls[..2],
        "appending later callbacks must not mutate completed callback records"
    );
}

#[test]
fn test_multi_write_stdin_prefix_and_stop_child_fingerprints_match() {
    let parsed_lines = load_fixture_lines("codex_realtime_multi_write_stdin.jsonl");
    let prefix_turn = build_turns(&parsed_lines[..12]).remove(0);
    let full_turn = build_turns(&parsed_lines).remove(0);
    let generation_ids = [
        (0, "1111111111111111".to_string()),
        (1, "2222222222222222".to_string()),
        (2, "3333333333333333".to_string()),
    ]
    .into_iter()
    .collect();
    let opts = TurnReplayOpts {
        trace_id: Some("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".to_string()),
        root_span_id: Some("bbbbbbbbbbbbbbbb".to_string()),
        parent_span_id: None,
        sofa_traceid: None,
        sofa_rpcid: None,
        billing_mode: None,
        generation_span_ids: generation_ids,
        subagent_span_ids: Default::default(),
    };
    let prefix = build_otel_export_request_for_turn(
        &prefix_turn,
        1,
        "session-redacted",
        "tester",
        "test",
        Some(&opts),
    );
    let full = build_otel_export_request_for_turn(
        &full_turn,
        1,
        "session-redacted",
        "tester",
        "test",
        Some(&opts),
    );

    let stable_child_ids = [
        "1111111111111111".to_string(),
        "2222222222222222".to_string(),
        deterministic_tool_span_id("call-pty-1"),
        deterministic_tool_span_id("call-pty-2"),
    ];
    for span_id in stable_child_ids {
        let prefix_span = spans(&prefix)
            .iter()
            .find(|span| span["spanId"].as_str() == Some(&span_id))
            .unwrap_or_else(|| panic!("prefix missing stable child {span_id}"));
        let full_span = spans(&full)
            .iter()
            .find(|span| span["spanId"].as_str() == Some(&span_id))
            .unwrap_or_else(|| panic!("full transcript missing stable child {span_id}"));
        assert_eq!(
            prefix_span, full_span,
            "realtime-accountable child {span_id} must be immutable at Stop"
        );
    }
}

#[test]
fn test_normalized_multi_write_stdin_keeps_callbacks_and_invocations_explicit() {
    let parsed_lines = load_fixture_lines("codex_realtime_multi_write_stdin.jsonl");
    let normalized = normalize_turn_semantics(&parsed_lines);

    assert_eq!(normalized.len(), 1);
    let turn = &normalized[0];
    assert_eq!(turn.turn_id, "turn-pty-multi-write");
    assert_eq!(turn.callbacks.len(), 3);
    assert_eq!(
        turn.callbacks
            .iter()
            .map(|callback| callback.callback_id.as_str())
            .collect::<Vec<_>>(),
        vec!["call-pty-1", "call-pty-2", "call-pty-3"]
    );
    assert_eq!(
        turn.callbacks[1].continuation_of.as_deref(),
        Some("call-pty-1")
    );
    assert_eq!(
        turn.callbacks[2].continuation_of.as_deref(),
        Some("call-pty-1")
    );
    assert!(turn.callbacks.iter().all(|callback| callback.terminal));
    assert_eq!(
        turn.invocations
            .iter()
            .map(|invocation| invocation.ordinal)
            .collect::<Vec<_>>(),
        vec![0, 1, 2]
    );
    assert!(turn
        .invocations
        .iter()
        .all(|invocation| invocation.terminal));
    assert_eq!(
        turn.invocations[0].terminal_source,
        Some(InvocationTerminalSource::UsageAndCallbacks)
    );

    let derived_tools = turn.derived_tool_calls();
    assert_eq!(derived_tools.len(), 3);
    assert_eq!(
        derived_tools
            .iter()
            .map(|tool| tool.call_id.as_str())
            .collect::<Vec<_>>(),
        vec!["call-pty-1", "call-pty-2", "call-pty-3"]
    );
    assert_eq!(
        derived_tools
            .iter()
            .map(|tool| tool.assistant_index)
            .collect::<Vec<_>>(),
        vec![0, 1, 2]
    );
}

#[test]
fn test_completed_reasoning_uses_native_turn_start_and_preserves_later_timing() {
    // Sanitized from Codex 0.161.0's actual rollout. The first reasoning item
    // was written only after the model response; it is not a request start.
    let parsed_lines = load_fixture_lines("codex_0161_late_reasoning_timing.jsonl");
    let normalized = normalize_turn_semantics(&parsed_lines);
    let turn = &normalized[0];
    assert!(turn.native_usage_mode);
    let timings = (0..3)
        .map(|ordinal| turn.invocation_timing(ordinal).unwrap())
        .collect::<Vec<_>>();
    assert_eq!(timings[0].started_at, "2026-10-08T14:49:49.746Z");
    assert_eq!(timings[0].start_source, InvocationStartSource::TurnStart);
    assert_eq!(timings[0].completed_at, "2026-10-08T14:52:27.581Z");
    assert_eq!(timings[1].started_at, "2026-10-08T14:52:27.684Z");
    assert_eq!(timings[2].started_at, "2026-10-08T14:54:52.244Z");
    assert!(timings[1..]
        .iter()
        .all(|timing| timing.start_source == InvocationStartSource::PreviousCallback));

    let turns = build_turns(&parsed_lines);
    let body = build_otel_export_request_for_turn(&turns[0], 1, "session", "tester", "test", None);
    let chats = spans(&body)
        .iter()
        .filter(|span| {
            span["name"]
                .as_str()
                .is_some_and(|name| name.starts_with("chat "))
        })
        .collect::<Vec<_>>();
    assert_eq!(chats.len(), 3);
    assert_eq!(
        span_attr(chats[0], "agentic.llm.duration_ms"),
        Some("157835")
    );
    assert_eq!(
        span_attr(chats[1], "agentic.llm.duration_ms"),
        Some("144380")
    );
    assert_eq!(
        span_attr(chats[2], "agentic.llm.duration_ms"),
        Some("99926")
    );
    assert_ne!(
        span_attr(chats[0], "agentic.llm.duration_ms"),
        Some("100154"),
        "native lifecycle time must not be replaced with provider HTTP time"
    );
}

#[test]
fn test_completed_reasoning_uses_user_submission_and_previous_callback() {
    let parsed_lines = vec![
        serde_json::json!({"timestamp":"2026-10-08T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-reasoning"}}),
        serde_json::json!({"timestamp":"2026-10-08T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"run it"}}),
        serde_json::json!({"timestamp":"2026-10-08T00:01:40.000Z","type":"response_item","payload":{"type":"reasoning","summary":[]}}),
        serde_json::json!({"timestamp":"2026-10-08T00:01:40.009Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"true\"}","call_id":"call-1"}}),
        serde_json::json!({"timestamp":"2026-10-08T00:01:40.100Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-1","output":"ok"}}),
        serde_json::json!({"timestamp":"2026-10-08T00:03:20.000Z","type":"response_item","payload":{"type":"reasoning","summary":[]}}),
        serde_json::json!({"timestamp":"2026-10-08T00:03:20.009Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"true\"}","call_id":"call-2"}}),
        serde_json::json!({"timestamp":"2026-10-08T00:03:20.100Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-2","output":"ok"}}),
    ];
    let normalized = normalize_turn_semantics(&parsed_lines);
    let first = normalized[0].invocation_timing(0).unwrap();
    let second = normalized[0].invocation_timing(1).unwrap();
    assert_eq!(first.started_at, "2026-10-08T00:00:00.100Z");
    assert_eq!(first.start_source, InvocationStartSource::UserSubmission);
    assert_eq!(second.started_at, "2026-10-08T00:01:40.100Z");
    assert_eq!(second.start_source, InvocationStartSource::PreviousCallback);
}

#[test]
fn test_completed_reasoning_without_causal_start_does_not_invent_earlier_time() {
    let parsed_lines = vec![
        serde_json::json!({"timestamp":"2026-10-08T00:00:00.000Z","type":"turn_context","payload":{"turn_id":"turn-missing-start"}}),
        serde_json::json!({"timestamp":"2026-10-08T00:01:40.000Z","type":"response_item","payload":{"type":"reasoning","summary":[]}}),
        serde_json::json!({"timestamp":"2026-10-08T00:01:40.009Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"true\"}","call_id":"call-1"}}),
    ];
    let normalized = normalize_turn_semantics(&parsed_lines);
    let timing = normalized[0].invocation_timing(0).unwrap();
    assert!(normalized[0].turn_started_at.is_empty());
    assert!(normalized[0].user_submitted_at.is_empty());
    assert_eq!(timing.started_at, "2026-10-08T00:01:40.000Z");
    assert_eq!(timing.completed_at, "2026-10-08T00:01:40.009Z");
}

#[test]
fn test_completed_reasoning_preserves_existing_model_event_start() {
    let parsed_lines = vec![
        serde_json::json!({"timestamp":"2026-10-08T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-event"}}),
        serde_json::json!({"timestamp":"2026-10-08T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"run it"}}),
        serde_json::json!({"timestamp":"2026-10-08T00:00:00.200Z","type":"event_msg","payload":{"type":"reasoning"}}),
        serde_json::json!({"timestamp":"2026-10-08T00:00:01.000Z","type":"response_item","payload":{"type":"reasoning","summary":[]}}),
        serde_json::json!({"timestamp":"2026-10-08T00:00:01.009Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"true\"}","call_id":"call-1"}}),
        serde_json::json!({"timestamp":"2026-10-08T00:00:01.100Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-1","output":"ok"}}),
        serde_json::json!({"timestamp":"2026-10-08T00:00:01.200Z","type":"event_msg","payload":{"type":"reasoning"}}),
        serde_json::json!({"timestamp":"2026-10-08T00:00:02.000Z","type":"response_item","payload":{"type":"reasoning","summary":[]}}),
        serde_json::json!({"timestamp":"2026-10-08T00:00:02.009Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"true\"}","call_id":"call-2"}}),
    ];
    let normalized = normalize_turn_semantics(&parsed_lines);
    for (ordinal, expected) in [
        (0, "2026-10-08T00:00:00.200Z"),
        (1, "2026-10-08T00:00:01.200Z"),
    ] {
        let timing = normalized[0].invocation_timing(ordinal).unwrap();
        assert_eq!(timing.started_at, expected);
        assert_eq!(timing.start_source, InvocationStartSource::ModelEvent);
    }
}

#[test]
fn test_normalized_multi_write_stdin_uses_invocation_local_timing() {
    let parsed_lines = load_fixture_lines("codex_realtime_multi_write_stdin.jsonl");
    let normalized = normalize_turn_semantics(&parsed_lines);
    let turn = &normalized[0];

    let timings = turn
        .invocations
        .iter()
        .map(|invocation| turn.invocation_timing(invocation.ordinal).unwrap())
        .collect::<Vec<_>>();
    assert_eq!(timings.len(), 3);
    assert_eq!(
        timings
            .iter()
            .map(|timing| timing.started_at.as_str())
            .collect::<Vec<_>>(),
        vec![
            "2026-07-30T10:00:01.050Z",
            "2026-07-30T10:00:01.300Z",
            "2026-07-30T10:00:01.540Z",
        ]
    );
    assert_eq!(
        timings
            .iter()
            .map(|timing| timing.completed_at.as_str())
            .collect::<Vec<_>>(),
        vec![
            "2026-07-30T10:00:01.120Z",
            "2026-07-30T10:00:01.520Z",
            "2026-07-30T10:00:01.720Z",
        ]
    );
    assert_eq!(
        timings[0].start_source,
        InvocationStartSource::UserSubmission
    );
    assert!(timings[1..]
        .iter()
        .all(|timing| timing.start_source == InvocationStartSource::PreviousCallback));
    assert!(timings
        .iter()
        .all(|timing| timing.end_source == InvocationEndSource::AssistantOutputOrAction));
    for (timing, tool) in timings.iter().zip(turn.derived_tool_calls()) {
        assert_eq!(timing.completed_at, tool.started_timestamp);
        assert_ne!(
            timing.started_at, turn.invocations[tool.assistant_index].usage_timestamp,
            "token_count must never become the next invocation start"
        );
    }

    let assistants = turn.derived_assistants();
    assert_eq!(assistants.len(), 3);
    assert_eq!(assistants[0].text, "starting command");
    assert!(assistants[1].text.is_empty());
    assert_eq!(assistants[2].text, "polling final output");
}

#[test]
fn test_output_only_invocations_start_at_causal_predecessor() {
    let parsed_lines = vec![
        serde_json::json!({"timestamp":"2026-07-30T11:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-tail-order"}}),
        serde_json::json!({"timestamp":"2026-07-30T11:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"run it"}}),
        serde_json::json!({"timestamp":"2026-07-30T11:00:00.500Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"true\"}","call_id":"call-1"}}),
        serde_json::json!({"timestamp":"2026-07-30T11:00:00.700Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-1","output":"ok"}}),
        serde_json::json!({"timestamp":"2026-07-30T11:00:01.500Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"pwd\"}","call_id":"call-2"}}),
        serde_json::json!({"timestamp":"2026-07-30T11:00:01.800Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-2","output":"/repo"}}),
        serde_json::json!({"timestamp":"2026-07-30T11:00:02.500Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"done"}}),
        serde_json::json!({"timestamp":"2026-07-30T11:00:02.600Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-tail-order","last_agent_message":"done"}}),
    ];

    let normalized = normalize_turn_semantics(&parsed_lines);
    let turn = &normalized[0];
    let timings = turn
        .invocations
        .iter()
        .map(|invocation| turn.invocation_timing(invocation.ordinal).unwrap())
        .collect::<Vec<_>>();

    assert_eq!(timings.len(), 3);
    assert_eq!(timings[0].started_at, "2026-07-30T11:00:00.100Z");
    assert_eq!(timings[0].completed_at, "2026-07-30T11:00:00.500Z");
    assert_eq!(
        timings[0].start_source,
        InvocationStartSource::UserSubmission
    );
    assert_eq!(timings[1].started_at, "2026-07-30T11:00:00.700Z");
    assert_eq!(timings[1].completed_at, "2026-07-30T11:00:01.500Z");
    assert_eq!(
        timings[1].start_source,
        InvocationStartSource::PreviousCallback
    );
    assert_eq!(timings[2].started_at, "2026-07-30T11:00:01.800Z");
    assert_eq!(timings[2].completed_at, "2026-07-30T11:00:02.500Z");
    assert_eq!(
        timings[2].start_source,
        InvocationStartSource::PreviousCallback
    );

    let built = build_turns(&parsed_lines);
    let body = build_otel_export_request_for_turn(&built[0], 1, "session", "tester", "test", None);
    let mut child_spans = spans(&body)
        .iter()
        .filter(|span| span["parentSpanId"].is_string())
        .collect::<Vec<_>>();
    child_spans.sort_by_key(|span| {
        span["startTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse::<u128>()
            .unwrap()
    });
    let ordered_names = child_spans
        .iter()
        .map(|span| span["name"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert!(ordered_names[0].starts_with("chat "));
    assert_eq!(ordered_names[1], "tool Bash");
    assert!(ordered_names[2].starts_with("chat "));
    assert_eq!(ordered_names[3], "tool Bash");
    assert!(ordered_names[4].starts_with("chat "));
    for pair in child_spans.windows(2) {
        let previous_start = pair[0]["startTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse::<u128>()
            .unwrap();
        let next_start = pair[1]["startTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse::<u128>()
            .unwrap();
        assert!(
            previous_start < next_start,
            "causal siblings must have strictly increasing start times"
        );
    }
}

#[test]
fn test_encrypted_new_task_anchors_child_input_and_generation_timing() {
    let parsed_lines = vec![
        serde_json::json!({"timestamp":"2026-08-04T09:53:08.259Z","type":"turn_context","payload":{"turn_id":"child-turn","model":"gpt-5.5"}}),
        serde_json::json!({"timestamp":"2026-08-04T09:53:08.273Z","type":"inter_agent_communication_metadata","payload":{"trigger_turn":true}}),
        serde_json::json!({"timestamp":"2026-08-04T09:53:08.273Z","type":"response_item","payload":{"type":"agent_message","author":"/root","recipient":"/root/foreground_sleep","content":[{"type":"input_text","text":"Message Type: NEW_TASK\nTask name: /root/foreground_sleep\nSender: /root\nPayload:\n"},{"type":"encrypted_content","encrypted_content":"opaque-ciphertext"}]}}),
        serde_json::json!({"timestamp":"2026-08-04T09:53:11.449Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"sleep 5 && echo done\"}","call_id":"call-sleep"}}),
        serde_json::json!({"timestamp":"2026-08-04T09:53:19.225Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-sleep","output":"done"}}),
        serde_json::json!({"timestamp":"2026-08-04T09:53:21.245Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}],"phase":"final_answer"}}),
        serde_json::json!({"timestamp":"2026-08-04T09:53:21.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"child-turn","last_agent_message":"done"}}),
    ];

    let normalized = normalize_turn_semantics(&parsed_lines);
    let first_timing = normalized[0].invocation_timing(0).unwrap();
    assert_eq!(first_timing.started_at, "2026-08-04T09:53:08.273Z");
    assert_eq!(
        first_timing.start_source,
        InvocationStartSource::UserSubmission
    );

    let turns = build_turns(&parsed_lines);
    assert_eq!(turns.len(), 1);
    assert_eq!(turns[0].user_timestamp, "2026-08-04T09:53:08.273Z");
    assert_eq!(
        turns[0].user_text,
        "Message Type: NEW_TASK\nTask name: /root/foreground_sleep\nSender: /root\nPayload:\n[ENCRYPTED]"
    );

    let body = build_otel_export_request_for_turn(&turns[0], 1, "child", "tester", "test", None);
    let mut causal_spans = spans(&body)
        .iter()
        .filter(|span| span["parentSpanId"].is_string())
        .collect::<Vec<_>>();
    causal_spans.sort_by_key(|span| {
        span["startTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse::<u128>()
            .unwrap()
    });
    assert!(causal_spans[0]["name"]
        .as_str()
        .unwrap()
        .starts_with("chat "));
    assert_eq!(causal_spans[1]["name"], "tool Bash");
    assert!(causal_spans[2]["name"]
        .as_str()
        .unwrap()
        .starts_with("chat "));
    assert_eq!(causal_spans[0]["startTimeUnixNano"], "1785837188274000000");
    for pair in causal_spans.windows(2) {
        let previous_start = pair[0]["startTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse::<u128>()
            .unwrap();
        let next_start = pair[1]["startTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse::<u128>()
            .unwrap();
        assert!(previous_start < next_start);
    }
}

#[test]
fn test_degenerate_chat_offsets_following_tool_by_one_millisecond() {
    let parsed_lines = vec![
        serde_json::json!({"timestamp":"2026-08-04T10:00:00.000Z","type":"turn_context","payload":{"turn_id":"child-without-task-anchor","model":"gpt-5.5"}}),
        serde_json::json!({"timestamp":"2026-08-04T10:00:01.000Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"true\"}","call_id":"call-fast"}}),
        serde_json::json!({"timestamp":"2026-08-04T10:00:01.100Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-fast","output":"ok"}}),
        serde_json::json!({"timestamp":"2026-08-04T10:00:01.200Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}],"phase":"final_answer"}}),
        serde_json::json!({"timestamp":"2026-08-04T10:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"child-without-task-anchor","last_agent_message":"done"}}),
    ];

    let turns = build_turns(&parsed_lines);
    let body = build_otel_export_request_for_turn(&turns[0], 1, "child", "tester", "test", None);
    let child_spans = spans(&body);
    let first_chat = child_spans
        .iter()
        .find(|span| {
            span["name"]
                .as_str()
                .is_some_and(|name| name.starts_with("chat "))
        })
        .unwrap();
    let tool = child_spans
        .iter()
        .find(|span| span["name"] == "tool Bash")
        .unwrap();
    let chat_start = first_chat["startTimeUnixNano"]
        .as_str()
        .unwrap()
        .parse::<u128>()
        .unwrap();
    let chat_end = first_chat["endTimeUnixNano"]
        .as_str()
        .unwrap()
        .parse::<u128>()
        .unwrap();
    let tool_start = tool["startTimeUnixNano"]
        .as_str()
        .unwrap()
        .parse::<u128>()
        .unwrap();

    assert!(chat_end.saturating_sub(chat_start) < 1_000_000);
    assert_eq!(tool_start, chat_start + 1_000_000);
}

#[test]
fn test_normalized_token_count_requires_output_and_terminal_callback() {
    let token_only = normalize_turn_semantics(&[
        serde_json::json!({"timestamp":"2026-07-30T10:59:59.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-token-only"}}),
        serde_json::json!({"timestamp":"2026-07-30T10:59:59.100Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":4,"output_tokens":1}}}}),
    ]);
    assert!(
        token_only[0].invocations.is_empty(),
        "token_count alone must not create an invocation"
    );

    let lines = vec![
        serde_json::json!({"timestamp":"2026-07-30T11:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-composite"}}),
        serde_json::json!({"timestamp":"2026-07-30T11:00:00.100Z","type":"response_item","payload":{"type":"reasoning","summary":[]}}),
        serde_json::json!({"timestamp":"2026-07-30T11:00:00.200Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":5,"output_tokens":2}}}}),
        serde_json::json!({"timestamp":"2026-07-30T11:00:00.300Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"true\"}","call_id":"call-composite"}}),
        serde_json::json!({"timestamp":"2026-07-30T11:00:00.400Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":6,"output_tokens":3}}}}),
    ];

    let reasoning_and_token = normalize_turn_semantics(&lines[..3]);
    assert_eq!(reasoning_and_token[0].invocations.len(), 1);
    assert!(!reasoning_and_token[0].invocations[0].terminal);
    assert!(!reasoning_and_token[0].invocations[0].usage.has_any());

    let pending_callback = normalize_turn_semantics(&lines);
    assert_eq!(pending_callback[0].invocations.len(), 1);
    assert!(pending_callback[0].invocations[0].usage.has_any());
    assert!(!pending_callback[0].invocations[0].terminal);

    let mut completed = lines;
    completed.push(serde_json::json!({"timestamp":"2026-07-30T11:00:00.500Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-composite","output":"ok"}}));
    let completed = normalize_turn_semantics(&completed);
    assert!(completed[0].invocations[0].terminal);
    assert_eq!(
        completed[0].invocations[0].terminal_source,
        Some(InvocationTerminalSource::UsageAndCallbacks)
    );
}

#[test]
fn test_compact_fixture_parses_compaction_events() {
    let parsed_lines = load_fixture_lines("compact_minimal.jsonl");
    let turns = build_turns(&parsed_lines);

    assert_eq!(turns.len(), 1, "should have exactly one turn");
    let turn = &turns[0];

    assert_eq!(
        turn.compactions.len(),
        1,
        "should have one compaction event"
    );
    let compact = &turn.compactions[0];

    assert_eq!(compact.timestamp, "2026-04-12T10:00:10.000Z");
    assert_eq!(
        compact.pre_tokens,
        Some(215600),
        "pre_tokens from last token_count before compaction"
    );
    assert_eq!(
        compact.post_tokens,
        Some(44649),
        "post_tokens backfilled from first non-zero token_count after compaction"
    );
    assert_eq!(compact.end_timestamp, "2026-04-12T10:00:16.000Z");
    assert_eq!(compact.sequence, 1, "first compaction in session");
}

#[test]
fn test_invalid_model_fixture_is_failed_and_sanitized() {
    let parsed_lines = load_fixture_lines("invalid_model_terminal_error.jsonl");
    let turns = build_turns(&parsed_lines);
    let normalized = normalize_turn_semantics(&parsed_lines);

    assert_eq!(turns.len(), 1);
    let turn = &turns[0];
    assert_eq!(turn.terminal_state, TurnTerminalState::Failed);
    assert_eq!(normalized[0].terminal_state, TurnTerminalState::Failed);
    assert!(!turn.completed);
    assert!(turn.assistants.is_empty());
    assert_eq!(turn.model, "aivision-regression-model-does-not-exist-0001");
    let error = turn.terminal_error.as_ref().expect("terminal error");
    assert_eq!(error.error_type, "invalid_request_error");
    assert_eq!(error.status_code, Some(400));
    assert!(error
        .message
        .contains("The requested model is not supported"));
    assert!(error.message.contains("[REDACTED]"));
    assert!(!error.message.contains("sk-sensitive-value"));
}

#[test]
fn test_compaction_only_turn_uses_total_tokens_fallback() {
    let parsed_lines = load_fixture_lines("compaction_manual_two_turns.jsonl");
    let turns = build_turns(&parsed_lines);

    assert_eq!(turns.len(), 2);
    assert_eq!(turns[0].turn_id, "ordinary-turn");
    assert!(turns[0].compactions.is_empty());
    assert_eq!(turns[1].turn_id, "compaction-turn");
    assert!(turns[1].user_text.is_empty());
    assert!(turns[1].assistants.is_empty());
    assert_eq!(turns[1].terminal_state, TurnTerminalState::Completed);
    assert_eq!(turns[1].compactions.len(), 1);
    let compaction = &turns[1].compactions[0];
    assert_eq!(compaction.pre_tokens, Some(36941));
    assert_eq!(compaction.post_tokens, Some(5552));
    assert!(compaction.end_timestamp.as_str() >= compaction.timestamp.as_str());
}

#[test]
fn test_codex_skill_invocation_v2_fixture_builds_exact_skill_span() {
    let parsed_lines = load_fixture_lines("codex_skill_invocation_v2.jsonl");
    let turns = build_turns(&parsed_lines);

    assert_eq!(turns.len(), 1);
    let turn = &turns[0];
    assert_eq!(turn.turn_id, "turn-skill-v2");
    assert_eq!(turn.terminal_state, TurnTerminalState::Completed);
    assert_eq!(turn.skill_invocations.len(), 1);
    let invocation = &turn.skill_invocations[0];
    assert_eq!(invocation.name, "analyze");
    assert_eq!(invocation.source, SkillInvocationSource::CodexTextElement);
    assert_eq!(invocation.resolved_workflow.as_deref(), Some("analyze"));
    assert_eq!(
        invocation.definition_path.as_deref(),
        Some("/home/user/.codex/skills/analyze/SKILL.md")
    );
    assert!(!turn
        .skill_invocations
        .iter()
        .any(|item| item.name == "omx-setup"));

    let body = build_otel_export_request_for_turn(
        turn,
        1,
        "session-skill-v2",
        "fixture-user",
        "test",
        None,
    );
    let skill_spans: Vec<_> = spans(&body)
        .iter()
        .filter(|span| span["name"].as_str() == Some("skill analyze"))
        .collect();
    assert_eq!(skill_spans.len(), 1);
    assert!(!spans(&body)
        .iter()
        .any(|span| span["name"].as_str() == Some("skill omx-setup")));

    let skill = skill_spans[0];
    for key in [
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
        "gen_ai.tool.call.id",
        "gen_ai.tool.name",
        "agentic.tool.type",
        "agentic.tool.name",
        "agentic.tool.id",
        "agentic.tool.duration_ms",
    ] {
        assert!(span_attr(skill, key).is_some(), "missing skill attr {key}");
    }
    assert_eq!(span_attr(skill, "gen_ai.span.kind"), Some("TOOL"));
    assert_eq!(
        span_attr(skill, "gen_ai.operation.name"),
        Some("execute_tool")
    );
    assert_eq!(span_attr(skill, "agentic.tool.type"), Some("skill"));
    assert_eq!(span_attr(skill, "agentic.tool.name"), Some("analyze"));
    assert!(span_attr(skill, "gen_ai.tool.call.id")
        .expect("synthetic call id")
        .starts_with("skill-invocation:turn-skill-v2:0:analyze:0-8"));

    let command = spans(&body)
        .iter()
        .find(|span| span_attr(span, "gen_ai.tool.call.id") == Some("call-read-analyze"))
        .expect("internal exec_command");
    assert_eq!(command["name"], "tool Bash");
    assert_ne!(span_attr(command, "agentic.tool.type"), Some("skill"));
}

#[test]
fn test_codex_skill_invocation_v2_implicit_route_without_placeholder() {
    let route = "OMX native UserPromptSubmit detected workflow keyword \"$analyze\" -> analyze. Follow AGENTS.md routing and preserve workflow transition and planning-safety rules.";
    let lines = vec![
        serde_json::json!({"timestamp":"2026-07-16T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-implicit"}}),
        serde_json::json!({"timestamp":"2026-07-16T08:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"please investigate","text_elements":[]}}),
        serde_json::json!({"timestamp":"2026-07-16T08:00:00.110Z","type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":route}]}}),
        serde_json::json!({"timestamp":"2026-07-16T08:00:01.000Z","type":"event_msg","payload":{"type":"agent_message","message":"done","phase":"final_answer"}}),
        serde_json::json!({"timestamp":"2026-07-16T08:00:01.100Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-implicit","last_agent_message":"done"}}),
    ];
    let turns = build_turns(&lines);
    assert_eq!(turns[0].skill_invocations.len(), 1);
    assert_eq!(
        turns[0].skill_invocations[0].source,
        SkillInvocationSource::OmxUserPromptSubmit
    );

    let body = build_otel_export_request_for_turn(
        &turns[0],
        1,
        "session-implicit",
        "fixture-user",
        "test",
        None,
    );
    assert_eq!(
        spans(&body)
            .iter()
            .filter(|span| span["name"].as_str() == Some("skill analyze"))
            .count(),
        1
    );
}

#[test]
fn test_codex_skill_compat_item_completed_user_message_builds_one_weather_span() {
    let parsed_lines = load_fixture_lines("codex_skill_transcript_compat.jsonl");
    let weather_item = parsed_lines
        .iter()
        .filter_map(|line| line.pointer("/payload/item"))
        .find(|item| {
            item.get("id").and_then(serde_json::Value::as_str) == Some("item-weather-user")
        })
        .expect("weather UserMessage item");

    assert_eq!(
        weather_item.get("type").and_then(serde_json::Value::as_str),
        Some("UserMessage")
    );
    assert_eq!(
        weather_item
            .pointer("/content/0/type")
            .and_then(serde_json::Value::as_str),
        Some("text")
    );
    assert_eq!(
        weather_item
            .pointer("/content/0/text_elements/0/placeholder")
            .and_then(serde_json::Value::as_str),
        Some("$hangzhou-weather")
    );
    assert_eq!(
        weather_item
            .pointer("/content/1/path")
            .and_then(serde_json::Value::as_str),
        Some("/Users/test/.agents/skills/hangzhou-weather/SKILL.md")
    );

    let turns = build_turns(&parsed_lines);
    let turn = turn_by_id(&turns, "turn-weather-native");
    assert_eq!(turn.skill_invocations.len(), 1);
    let invocation = &turn.skill_invocations[0];
    assert_eq!(invocation.name, "hangzhou-weather");
    assert_eq!(invocation.source, SkillInvocationSource::CodexTextElement);
    assert_eq!(invocation.byte_start, Some(0));
    assert_eq!(invocation.byte_end, Some(17));
    assert_eq!(
        invocation.definition_path.as_deref(),
        Some("/Users/test/.agents/skills/hangzhou-weather/SKILL.md")
    );

    let body = build_otel_export_request_for_turn(
        turn,
        1,
        "session-skill-compat",
        "fixture-user",
        "test",
        None,
    );
    let skill_spans = skill_spans_named(&body, "hangzhou-weather");
    assert_eq!(skill_spans.len(), 1);
    assert_eq!(
        span_attr(skill_spans[0], "agentic.tool.name"),
        Some("hangzhou-weather")
    );
    assert_eq!(
        span_attr(skill_spans[0], "agentic.tool.type"),
        Some("skill")
    );

    let chat = spans(&body)
        .iter()
        .find(|span| span_attr(span, "gen_ai.span.kind") == Some("CHAT"))
        .expect("CHAT root");
    assert_eq!(
        skill_spans[0]["parentSpanId"].as_str(),
        chat["spanId"].as_str()
    );
}

#[test]
fn test_codex_skill_compat_custom_exec_static_wrapper_builds_one_fallback_span() {
    let parsed_lines = load_fixture_lines("codex_skill_transcript_compat.jsonl");
    let wrapper = parsed_lines
        .iter()
        .find(|line| {
            line.pointer("/payload/id")
                .and_then(serde_json::Value::as_str)
                == Some("custom-exec-read-skill")
        })
        .and_then(|line| line.pointer("/payload/input"))
        .and_then(serde_json::Value::as_str)
        .expect("custom exec wrapper");
    assert!(wrapper.contains("tools.exec_command"));
    assert!(wrapper.contains("/Users/test/.agents/skills/hangzhou-weather/SKILL.md"));

    let turns = build_turns(&parsed_lines);
    let turn = turn_by_id(&turns, "turn-exec-fallback");
    assert_eq!(turn.skill_invocations.len(), 1);
    let invocation = &turn.skill_invocations[0];
    assert_eq!(invocation.name, "hangzhou-weather");
    assert_eq!(
        invocation.source,
        SkillInvocationSource::CommandPathFallback
    );
    assert_eq!(invocation.byte_start, None);
    assert_eq!(invocation.byte_end, None);
    assert_eq!(
        invocation.definition_path.as_deref(),
        Some("/Users/test/.agents/skills/hangzhou-weather/SKILL.md")
    );

    let body = build_otel_export_request_for_turn(
        turn,
        1,
        "session-skill-compat",
        "fixture-user",
        "test",
        None,
    );
    assert_eq!(skill_spans_named(&body, "hangzhou-weather").len(), 1);

    let inner_exec = spans(&body)
        .iter()
        .find(|span| span_attr(span, "gen_ai.tool.call.id") == Some("call-custom-exec-read-skill"))
        .expect("inner custom exec tool span");
    assert_eq!(inner_exec["name"], "tool exec");
    assert_ne!(span_attr(inner_exec, "agentic.tool.type"), Some("skill"));
}

#[test]
fn test_codex_skill_compat_definition_only_and_path_like_noise_build_zero_skill_spans() {
    let parsed_lines = load_fixture_lines("codex_skill_transcript_compat.jsonl");
    let noise_wrapper = parsed_lines
        .iter()
        .find(|line| {
            line.pointer("/payload/id")
                .and_then(serde_json::Value::as_str)
                == Some("custom-exec-noise")
        })
        .and_then(|line| line.pointer("/payload/input"))
        .and_then(serde_json::Value::as_str)
        .expect("noise wrapper");
    assert!(noise_wrapper.contains("/Users/test/.agents/skills/hangzhou-weather/SKILL.md"));
    assert!(noise_wrapper.contains("find /Users/test/.agents/skills/hangzhou-weather"));

    let turns = build_turns(&parsed_lines);
    let turn = turn_by_id(&turns, "turn-negative-noise");
    assert!(turn.skill_invocations.is_empty());

    let body = build_otel_export_request_for_turn(
        turn,
        1,
        "session-skill-compat",
        "fixture-user",
        "test",
        None,
    );
    assert!(spans(&body)
        .iter()
        .all(|span| span_attr(span, "agentic.tool.type") != Some("skill")));
}

#[test]
fn test_codex_skill_compat_mixed_user_message_and_collab_preserves_both_evidence() {
    let parsed_lines = load_fixture_lines("codex_skill_transcript_compat.jsonl");
    let mixed_items = parsed_lines
        .iter()
        .filter_map(|line| line.pointer("/payload/item"))
        .filter(|item| {
            matches!(
                item.get("id").and_then(serde_json::Value::as_str),
                Some("item-mixed-user" | "collab-mixed-spawn")
            )
        })
        .collect::<Vec<_>>();
    assert_eq!(mixed_items.len(), 2);

    let turns = build_turns(&parsed_lines);
    let turn = turn_by_id(&turns, "turn-mixed-skill-collab");
    assert_eq!(turn.skill_invocations.len(), 1);
    assert_eq!(turn.skill_invocations[0].name, "hangzhou-weather");
    assert!(turn.collab_agent_events.iter().any(|event| {
        matches!(
            event,
            CollabAgentEvent::ToolInvocation(invocation)
                if invocation.tool_name == "spawn_agent"
                    && invocation.child_thread_id.as_deref() == Some("child-mixed")
                    && invocation.nickname.as_deref() == Some("Curie")
        )
    }));

    let body = build_otel_export_request_for_turn(
        turn,
        1,
        "session-skill-compat",
        "fixture-user",
        "test",
        None,
    );
    assert_eq!(skill_spans_named(&body, "hangzhou-weather").len(), 1);
}

#[test]
fn test_multi_agent_v2_fixture_correlates_and_merges() {
    let parent_lines = load_fixture_lines("codex_multi_agent_v2_parent.jsonl");
    let child_content = load_fixture_content("codex_multi_agent_v2_child.jsonl");
    let child_lines: Vec<serde_json::Value> = child_content
        .lines()
        .filter_map(|line| serde_json::from_str(line).ok())
        .collect();
    let parent_turns = build_turns(&parent_lines);
    let child_turns = build_turns(&child_lines);

    assert_eq!(parent_turns.len(), 1);
    assert_eq!(child_turns.len(), 1);
    let meta = extract_session_meta_ids(&child_content);
    assert_eq!(meta.session_id.as_deref(), Some("child-v2"));
    assert_eq!(meta.parent_thread_id.as_deref(), Some("parent-v2"));
    assert_eq!(meta.agent_path.as_deref(), Some("/root/sleep_foreground"));

    let source = child_turns[0]
        .subagent_source
        .as_ref()
        .expect("child source");
    assert_eq!(source.depth, 1);
    assert_eq!(source.agent_nickname.as_deref(), Some("Hume"));
    assert_eq!(source.protocol_version.as_deref(), Some("v2"));
    assert_eq!(
        child_turns[0].tool_calls[0].input["command"],
        "sleep 5 && echo done"
    );

    let parent_turn = &parent_turns[0];
    assert_eq!(parent_turn.assistants.len(), 5);
    let terminal = parent_turn
        .collab_agent_events
        .iter()
        .find(|event| matches!(event, CollabAgentEvent::AgentMessage { .. }))
        .expect("typed child terminal");
    assert!(matches!(
        terminal,
        CollabAgentEvent::AgentMessage {
            author,
            recipient,
            message_type: Some(message_type),
            ..
        } if author == "/root/sleep_foreground"
            && recipient == "/root"
            && message_type == "FINAL_ANSWER"
    ));

    let identity = SubagentIdentity {
        session_id: "child-v2".to_string(),
        agent_path: Some("/root/sleep_foreground".to_string()),
    };
    let correlation = correlate_subagent(parent_turn, &identity, std::slice::from_ref(&identity));
    assert_eq!(correlation.lifecycle, SubagentLifecycle::CompletedSync);
    assert_eq!(correlation.protocol, Some(SubagentProtocol::V2));
    assert_eq!(correlation.spawn_call_id.as_deref(), Some("call-v2-spawn"));
    assert_eq!(
        correlation.wait_call_ids,
        vec!["call-v2-wait-timeout", "call-v2-wait-complete"]
    );

    let config = LangfuseConfig {
        public_key: None,
        secret_key: None,
        host: "http://127.0.0.1".to_string(),
        headers: None,
    };
    let mut body = build_otel_export_request(
        &parent_turns,
        "parent-v2",
        "fixture-user",
        &config,
        "2026-07-10T08:54:10.100Z",
        "test",
        0,
    );
    let child = SubagentChild {
        session_id: "child-v2".to_string(),
        turns: child_turns,
        nickname: "Hume".to_string(),
        role: String::new(),
        agent_path: Some("/root/sleep_foreground".to_string()),
    };
    let merged =
        merge_subagent_sessions_into_otel(&mut body, parent_turn, &[child], "fixture-user", "test");
    assert_eq!(merged.merged_sync_ids, vec!["child-v2"]);

    let spans = body
        .pointer("/resourceSpans/0/scopeSpans/0/spans")
        .and_then(serde_json::Value::as_array)
        .expect("spans");
    let sync_agents: Vec<_> = spans
        .iter()
        .filter(|span| span["name"] == "agent Hume")
        .collect();
    assert_eq!(sync_agents.len(), 1);
    let attrs = sync_agents[0]["attributes"].as_array().unwrap();
    let attr = |key: &str| {
        attrs.iter().find_map(|item| {
            (item["key"] == key)
                .then(|| item["value"]["stringValue"].as_str())
                .flatten()
        })
    };
    assert_eq!(attr("agentic.subagent.id"), Some("child-v2"));
    assert_eq!(attr("agentic.subagent.nickname"), Some("Hume"));
    assert_eq!(
        attr("agentic.subagent.agent_path"),
        Some("/root/sleep_foreground")
    );
    assert_eq!(attr("agentic.subagent.mode"), Some("sync"));
    assert!(attr("gen_ai.tool.call.result")
        .expect("typed result")
        .contains("done"));

    let wait_count = spans
        .iter()
        .filter(|span| {
            span["attributes"].as_array().is_some_and(|attrs| {
                attrs.iter().any(|item| {
                    item["key"] == "agentic.tool.raw_name"
                        && item["value"]["stringValue"] == "wait_agent"
                })
            })
        })
        .count();
    assert_eq!(wait_count, 2);
}

#[test]
fn codex_0148_role_user_only_turn_uses_first_real_input_and_last_output() {
    let turns = build_turns(&load_fixture_lines(
        "codex_0148_role_user_only_subagent_parent.jsonl",
    ));
    assert_eq!(turns.len(), 1);
    let turn = &turns[0];
    let expected_input =
        "启动一个前台子 agent 执行 sleep 5 && echo done，同时你继续告诉我当前时间。";
    let expected_output = "当前时间：`2026-08-15 15:58:35 CST`。\n\n前台子 agent 已完成：\n\n```text\ndone\n```\n\n退出状态：`0`。";

    assert_eq!(turn.user_text, expected_input);
    assert!(!turn.user_text.contains("AGENTS.md"));
    let input = build_input_messages(turn);
    assert_eq!(input.len(), 1);
    assert_eq!(input[0].content.as_deref(), Some(expected_input));
    let output = build_output_messages(turn);
    assert_eq!(output.len(), 1);
    assert_eq!(output[0].content.as_deref(), Some(expected_output));
}

#[test]
fn codex_0148_typed_subagent_activity_collects_exact_identity() {
    let turns = build_turns(&load_fixture_lines(
        "codex_0148_role_user_only_subagent_parent.jsonl",
    ));
    let identities = collect_subagent_identities(&turns);
    assert_eq!(identities.len(), 1);
    assert_eq!(
        identities[0].session_id,
        "01a0046d-f314-7941-a2fb-9a4c9e684b3e"
    );
    assert_eq!(
        identities[0].agent_path.as_deref(),
        Some("/root/foreground_sleep")
    );
    let correlation = correlate_subagent(&turns[0], &identities[0], &identities);
    assert_eq!(
        correlation.spawn_call_id.as_deref(),
        Some("call_AESxyJILd4wufl9232x4KGOR")
    );
    assert_eq!(correlation.lifecycle, SubagentLifecycle::CompletedSync);
    assert!(matches!(
        correlation.terminal_event,
        Some(CollabAgentEvent::AgentMessage {
            ref author,
            ref recipient,
            message_type: Some(ref message_type),
            ..
        }) if author == "/root/foreground_sleep"
            && recipient == "/root"
            && message_type == "FINAL_ANSWER"
    ));
}

#[test]
fn codex_0148_child_preserves_structured_subagent_source_and_new_task() {
    let turns = build_turns(&load_fixture_lines(
        "codex_0148_role_user_only_subagent_child.jsonl",
    ));
    assert_eq!(turns.len(), 1);
    assert!(turns[0].user_text.contains("Message Type: NEW_TASK"));
    let source = turns[0].subagent_source.as_ref().expect("child source");
    assert_eq!(source.agent_path.as_deref(), Some("/root/foreground_sleep"));
    assert_eq!(source.agent_nickname.as_deref(), Some("Bohr"));
    assert_eq!(source.agent_role.as_deref(), Some("executor"));
    assert_eq!(source.depth, 1);
    assert_eq!(source.protocol_version.as_deref(), Some("v2"));
}

#[test]
fn codex_0148_parent_child_fixture_builds_root_agent_child_topology() {
    let parent_turns = build_turns(&load_fixture_lines(
        "codex_0148_role_user_only_subagent_parent.jsonl",
    ));
    let child_turns = build_turns(&load_fixture_lines(
        "codex_0148_role_user_only_subagent_child.jsonl",
    ));
    let config = LangfuseConfig {
        public_key: None,
        secret_key: None,
        host: "http://127.0.0.1".to_string(),
        headers: None,
    };
    let mut body = build_otel_export_request(
        &parent_turns,
        "01a0046d-8c93-7d01-82b7-6fc34e0fda91",
        "fixture-user",
        &config,
        "2026-08-15T07:58:35.100Z",
        "test",
        0,
    );
    let child = SubagentChild {
        session_id: "01a0046d-f314-7941-a2fb-9a4c9e684b3e".to_string(),
        turns: child_turns,
        nickname: "Bohr".to_string(),
        role: "executor".to_string(),
        agent_path: Some("/root/foreground_sleep".to_string()),
    };
    let merged = merge_subagent_sessions_into_otel(
        &mut body,
        &parent_turns[0],
        &[child],
        "fixture-user",
        "test",
    );
    assert_eq!(
        merged.merged_sync_ids,
        vec!["01a0046d-f314-7941-a2fb-9a4c9e684b3e"]
    );

    let root = spans(&body)
        .iter()
        .find(|span| span_attr(span, "gen_ai.span.kind") == Some("CHAT"))
        .expect("CHAT root");
    let agent = spans(&body)
        .iter()
        .find(|span| span_attr(span, "gen_ai.span.kind") == Some("AGENT"))
        .expect("AGENT span");
    assert_eq!(
        spans(&body)
            .iter()
            .filter(|span| span_attr(span, "gen_ai.span.kind") == Some("CHAT"))
            .count(),
        1
    );
    assert_eq!(
        spans(&body)
            .iter()
            .filter(|span| span_attr(span, "gen_ai.span.kind") == Some("AGENT"))
            .count(),
        1
    );
    assert_eq!(agent["parentSpanId"], root["spanId"]);
    assert_eq!(span_attr(agent, "agentic.subagent.type"), Some("executor"));
    let spawn_spans: Vec<_> = spans(&body)
        .iter()
        .filter(|span| {
            span_attr(span, "gen_ai.span.kind") == Some("TOOL")
                && span_attr(span, "agentic.tool.raw_name") == Some("spawn_agent")
        })
        .collect();
    assert_eq!(
        spawn_spans.len(),
        1,
        "spawn invocation must remain visible once"
    );
    let spawn = spawn_spans[0];
    assert_eq!(spawn["parentSpanId"], root["spanId"]);
    assert_eq!(
        span_attr(agent, "agentic.subagent.parent_tool_call_id"),
        span_attr(spawn, "gen_ai.tool.call.id"),
        "AGENT execution must link back to its spawn invocation"
    );
    let spawn_start = spawn["startTimeUnixNano"]
        .as_str()
        .expect("spawn start")
        .parse::<u128>()
        .expect("numeric spawn start");
    let spawn_end = spawn["endTimeUnixNano"]
        .as_str()
        .expect("spawn end")
        .parse::<u128>()
        .expect("numeric spawn end");
    let root_llms: Vec<_> = spans(&body)
        .iter()
        .filter(|span| {
            span_attr(span, "gen_ai.span.kind") == Some("LLM")
                && span["parentSpanId"] == root["spanId"]
        })
        .collect();
    assert!(root_llms.iter().any(|span| {
        span["endTimeUnixNano"]
            .as_str()
            .and_then(|value| value.parse::<u128>().ok())
            .is_some_and(|end| end <= spawn_start)
    }));
    assert!(root_llms.iter().any(|span| {
        span["startTimeUnixNano"]
            .as_str()
            .and_then(|value| value.parse::<u128>().ok())
            .is_some_and(|start| start >= spawn_end)
    }));
    assert!(spans(&body)
        .iter()
        .any(|span| span["parentSpanId"] == agent["spanId"]));
}

#[test]
fn interrupted_subagent_invocation_fixture_merges_each_activation_once() {
    let parent_turns = build_turns(&load_fixture_lines(
        "codex_interrupted_invocations_parent.jsonl",
    ));
    let children = vec![
        child_from_fixture(
            "codex_interrupted_invocations_gibbs.jsonl",
            "child-gibbs",
            "Gibbs",
            "/root/gibbs",
        ),
        child_from_fixture(
            "codex_interrupted_invocations_lagrange.jsonl",
            "child-lagrange",
            "Lagrange",
            "/root/lagrange",
        ),
        child_from_fixture(
            "codex_interrupted_invocations_popper.jsonl",
            "child-popper",
            "Popper",
            "/root/popper",
        ),
    ];

    assert_eq!(parent_turns.len(), 6);
    let failed_spawn_body = build_otel_export_request_for_turn(
        &parent_turns[0],
        1,
        "parent-interrupted-fixture",
        "fixture-user",
        "test",
        None,
    );
    assert_eq!(
        spans(&failed_spawn_body)
            .iter()
            .filter(|span| span_attr(span, "gen_ai.span.kind") == Some("AGENT"))
            .count(),
        0,
        "failed spawn must not synthesize an AGENT span"
    );
    assert!(
        spans(&failed_spawn_body).iter().any(|span| {
            span_attr(span, "gen_ai.tool.call.id") == Some("call-failed-spawn")
                && span_attr(span, "agentic.tool.raw_name") == Some("spawn_agent")
        }),
        "failed spawn TOOL evidence must remain visible"
    );

    let config = LangfuseConfig {
        public_key: None,
        secret_key: None,
        host: "http://127.0.0.1".to_string(),
        headers: None,
    };
    let mut merged_spans = Vec::new();
    let mut merged_invocation_ids = Vec::new();
    let mut outcomes = Vec::new();
    let mut correlation_reasons = Vec::new();
    for anchor_index in 1..parent_turns.len() {
        let mut body = build_otel_export_request(
            &[parent_turns[anchor_index].clone()],
            "parent-interrupted-fixture",
            "fixture-user",
            &config,
            "2026-07-18T13:00:04.000Z",
            "test",
            0,
        );
        let result = merge_subagent_sessions_into_otel_across_turns(
            &mut body,
            &parent_turns,
            anchor_index,
            &children,
            "fixture-user",
            "test",
        );
        merged_invocation_ids.extend(result.merged_subagent_invocation_ids);
        outcomes.extend(result.invocation_outcomes);
        correlation_reasons.extend(result.correlation_reasons);
        merged_spans.extend(spans(&body).iter().cloned());
    }

    merged_invocation_ids.sort();
    assert_eq!(
        merged_invocation_ids,
        vec![
            "child-gibbs:call-gibbs-followup",
            "child-gibbs:call-gibbs-spawn",
            "child-lagrange:call-lagrange-followup",
            "child-lagrange:call-lagrange-spawn",
            "child-popper:call-popper-spawn",
        ],
        "each child activation must have a distinct invocation id"
    );
    assert!(correlation_reasons
        .iter()
        .all(|(_, reason)| reason == "v2_interrupted_sync" || reason == "v2_completed_sync"));
    let mut outcome_ids: Vec<_> = outcomes
        .iter()
        .map(|outcome| outcome.invocation_id.as_str())
        .collect();
    let mut reason_ids: Vec<_> = correlation_reasons
        .iter()
        .map(|(invocation_id, _)| invocation_id.as_str())
        .collect();
    outcome_ids.sort_unstable();
    reason_ids.sort_unstable();
    assert_eq!(
        reason_ids, outcome_ids,
        "diagnostic reasons must correspond exactly to emitted invocation outcomes"
    );

    let agent_spans: Vec<_> = merged_spans
        .iter()
        .filter(|span| span_attr(span, "gen_ai.span.kind") == Some("AGENT"))
        .collect();
    assert_eq!(agent_spans.len(), 5);
    assert_eq!(
        agent_spans
            .iter()
            .filter(
                |span| span["name"].as_str() == Some("agent Gibbs (Cancelled)")
                    && span_attr(span, "agentic.subagent.status") == Some("interrupted")
            )
            .count(),
        2
    );
    assert_eq!(
        agent_spans
            .iter()
            .filter(
                |span| span["name"].as_str() == Some("agent Lagrange (Cancelled)")
                    && span_attr(span, "agentic.subagent.status") == Some("interrupted")
            )
            .count(),
        2
    );
    assert_eq!(
        agent_spans
            .iter()
            .filter(|span| span["name"].as_str() == Some("agent Popper")
                && span_attr(span, "agentic.subagent.status") == Some("completed"))
            .count(),
        1
    );

    for interrupted in agent_spans
        .iter()
        .filter(|span| span_attr(span, "agentic.subagent.status") == Some("interrupted"))
    {
        assert_eq!(interrupted["status"]["code"], 1);
        assert!(interrupted["status"].get("message").is_none());
        assert_eq!(span_attr(interrupted, "error.type"), Some("cancelled"));
        assert!(
            !has_span_attr(interrupted, "gen_ai.tool.call.result"),
            "interrupted AGENT must not carry a successful result"
        );
    }
    let completed = agent_spans
        .iter()
        .find(|span| span["name"].as_str() == Some("agent Popper"))
        .expect("completed Popper AGENT");
    assert_eq!(completed["status"]["code"], 1);
    assert!(span_attr(completed, "gen_ai.tool.call.result")
        .expect("completed result")
        .contains("conjecture rejected"));

    for call_id in [
        "call-gibbs-spawn",
        "call-lagrange-spawn",
        "call-popper-spawn",
    ] {
        assert_eq!(
            merged_spans
                .iter()
                .filter(
                    |span| span_attr(span, "gen_ai.tool.call.id") == Some(call_id)
                        && span_attr(span, "agentic.tool.raw_name") == Some("spawn_agent")
                        && span_attr(span, "gen_ai.span.kind") == Some("TOOL")
                )
                .count(),
            1,
            "successful spawn TOOL {call_id} must remain exactly once as the invocation fact"
        );
    }
    for call_id in [
        "call-gibbs-wait-1",
        "call-gibbs-wait-2",
        "call-lagrange-wait-1",
        "call-lagrange-wait-2",
        "call-popper-wait",
        "call-gibbs-interrupt-1",
        "call-gibbs-interrupt-2",
        "call-lagrange-interrupt-1",
        "call-lagrange-interrupt-2",
    ] {
        assert!(
            merged_spans.iter().any(
                |span| span_attr(span, "gen_ai.tool.call.id") == Some(call_id)
                    && span_attr(span, "gen_ai.span.kind") == Some("TOOL")
            ),
            "wait_agent and interrupt_agent sibling TOOL span {call_id} must remain"
        );
    }
    assert_eq!(
        outcomes
            .iter()
            .filter(|outcome| outcome.status == "interrupted")
            .count(),
        4
    );
    assert_eq!(
        outcomes
            .iter()
            .filter(|outcome| outcome.status == "completed")
            .count(),
        1
    );
}

#[test]
fn item_completed_tools_emit_error_mcp_and_parallel_semantics() {
    let lines = vec![
        serde_json::json!({"timestamp":"2026-08-23T02:01:20.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-item-tools"}}),
        serde_json::json!({"timestamp":"2026-08-23T02:01:20.100Z","type":"event_msg","payload":{"type":"user_message","message":"run item tools"}}),
        serde_json::json!({"timestamp":"2026-08-23T02:01:20.200Z","type":"response_item","payload":{"type":"custom_tool_call","name":"exec","call_id":"wrapper-command","input":"parallel commands"}}),
        serde_json::json!({"timestamp":"2026-08-23T02:01:20.210Z","type":"response_item","payload":{"type":"custom_tool_call_output","call_id":"wrapper-command","output":null}}),
        serde_json::json!({"timestamp":"2026-08-23T02:01:20.300Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CommandExecution","id":"exec-failed","command":["/bin/sh","-c","missing"],"status":"failed","exit_code":1,"aggregated_output":"missing: command not found\n"}}}),
        serde_json::json!({"timestamp":"2026-08-23T02:01:20.310Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CommandExecution","id":"exec-success","command":["/bin/sh","-c","echo ok"],"status":"completed","exit_code":0,"aggregated_output":"ok\n"}}}),
        serde_json::json!({"timestamp":"2026-08-23T02:01:20.400Z","type":"response_item","payload":{"type":"custom_tool_call","name":"exec","call_id":"wrapper-mcp","input":"tools.mcp__demo__add({a: 2, b: 40})"}}),
        serde_json::json!({"timestamp":"2026-08-23T02:01:20.410Z","type":"response_item","payload":{"type":"custom_tool_call_output","call_id":"wrapper-mcp","output":null}}),
        serde_json::json!({"timestamp":"2026-08-23T02:01:20.500Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"McpToolCall","id":"mcp-item","server":"demo","tool":"add","arguments":{"a":2,"b":40},"status":"completed","result":{"content":[{"type":"text","text":"42"}],"isError":false}}}}),
        serde_json::json!({"timestamp":"2026-08-23T02:01:20.600Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-item-tools","last_agent_message":"done"}}),
    ];

    let turn = build_turns(&lines).pop().expect("item tools turn");
    let body = build_otel_export_request_for_turn(
        &turn,
        1,
        "item-tools-session",
        "test-user",
        "test",
        None,
    );
    let emitted = spans(&body);
    let bash_spans = emitted
        .iter()
        .filter(|span| span["name"].as_str() == Some("tool Bash"))
        .collect::<Vec<_>>();
    assert_eq!(bash_spans.len(), 2);
    assert_eq!(
        span_attr(bash_spans[0], "agentic.tool.parallel_group"),
        span_attr(bash_spans[1], "agentic.tool.parallel_group")
    );
    let failed = bash_spans
        .iter()
        .find(|span| span_attr(span, "gen_ai.tool.call.id") == Some("exec-failed"))
        .expect("failed command span");
    assert_eq!(failed["status"]["code"], 2);
    assert_eq!(span_attr(failed, "error.type"), Some("tool_error"));

    let mcp = emitted
        .iter()
        .find(|span| span["name"].as_str() == Some("mcp demo.add"))
        .expect("mcp span");
    assert_eq!(span_attr(mcp, "agentic.tool.type"), Some("mcp"));
    assert_eq!(span_attr(mcp, "agentic.tool.name"), Some("demo.add"));
    assert_eq!(
        span_attr(mcp, "agentic.tool.raw_name"),
        Some("mcp__demo__add")
    );
}
