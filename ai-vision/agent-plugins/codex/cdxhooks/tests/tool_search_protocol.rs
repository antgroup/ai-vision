use cdxhooks::common::transcript::{build_turns, normalize_turn_semantics};
use serde_json::{json, Value};

// Source-derived, synthetic values; no user transcripts. Codex rust-v0.160.0:
// https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/core/tests/common/responses.rs#L999
// https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/core/src/tools/context.rs#L222
fn client_search_prefix() -> Vec<Value> {
    vec![
        json!({"timestamp":"2026-10-01T12:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"search-turn"}}),
        json!({"timestamp":"2026-10-01T12:00:00.001Z","type":"event_msg","payload":{"type":"user_message","message":"Find a tool"}}),
        json!({"timestamp":"2026-10-01T12:00:01.000Z","type":"response_item","payload":{"type":"tool_search_call","call_id":"search-a","execution":"client","arguments":{"query":"lookup"}}}),
    ]
}

fn search_output(call_id: &str, timestamp: &str, tools: Value) -> Value {
    json!({"timestamp":timestamp,"type":"response_item","payload":{"type":"tool_search_output","call_id":call_id,"execution":"client","status":"completed","tools":tools}})
}

#[test]
fn client_tool_search_waits_for_its_result() {
    let lines = client_search_prefix();
    let normalized = normalize_turn_semantics(&lines);
    let call = &normalized[0].callbacks[0];
    assert!(!call.terminal, "a client search request is not its result");
    assert!(call.completed_at.is_empty());
    assert_eq!(call.success, None);
    let turns = build_turns(&lines);
    assert!(turns[0].tool_calls[0].ended_timestamp.is_empty());
    assert_eq!(turns[0].tool_calls[0].success, None);
}

#[test]
fn client_tool_search_pairs_outputs_by_call_id() {
    let mut lines = client_search_prefix();
    lines.push(json!({"timestamp":"2026-10-01T12:00:01.100Z","type":"response_item","payload":{"type":"tool_search_call","call_id":"search-b","execution":"client","arguments":{"query":"other"}}}));
    let result =
        json!([{"type":"namespace","name":"lookup","description":"Fixture tools","tools":[]}]);
    lines.push(search_output(
        "search-b",
        "2026-10-01T12:00:01.500Z",
        json!([]),
    ));
    lines.push(search_output(
        "search-a",
        "2026-10-01T12:00:02.000Z",
        result.clone(),
    ));
    let normalized = normalize_turn_semantics(&lines);
    assert_eq!(normalized[0].callbacks.len(), 2);
    let call = &normalized[0].callbacks[0];
    assert_eq!(call.callback_id, "search-a");
    assert_eq!(call.requested_at, "2026-10-01T12:00:01.000Z");
    assert_eq!(call.completed_at, "2026-10-01T12:00:02.000Z");
    assert!(call.terminal);
    assert_eq!(call.success, Some(true));
    assert_eq!(call.output["tools"], result);
    assert_eq!(normalized[0].callbacks[1].output["tools"], json!([]));
    let turns = build_turns(&lines);
    assert_eq!(turns[0].tool_calls.len(), 2);
    assert_eq!(turns[0].tool_calls[0].ended_timestamp, call.completed_at);
    assert_eq!(turns[0].tool_calls[0].output["tools"], result);
    assert_eq!(turns[0].tool_calls[0].success, Some(true));
}

#[test]
fn legacy_single_record_tool_search_remains_complete() {
    for execution in [None, Some("server")] {
        let mut lines = client_search_prefix();
        let payload = lines[2]["payload"].as_object_mut().unwrap();
        payload.remove("execution");
        if let Some(execution) = execution {
            payload.insert("execution".into(), json!(execution));
        }
        payload.insert("status".into(), json!("completed"));
        let normalized = normalize_turn_semantics(&lines);
        let call = &normalized[0].callbacks[0];
        assert!(call.terminal);
        assert_eq!(call.completed_at, call.requested_at);
        assert_eq!(call.success, Some(true));
        assert_eq!(build_turns(&lines)[0].tool_calls[0].success, Some(true));
    }
}

#[test]
fn completed_client_call_status_still_requires_output() {
    let mut lines = client_search_prefix();
    lines[2]["payload"]["status"] = json!("completed");
    let normalized = normalize_turn_semantics(&lines);
    assert!(!normalized[0].callbacks[0].terminal);
    assert_eq!(build_turns(&lines)[0].tool_calls[0].success, None);
}

#[test]
fn repeated_search_output_preserves_first_completion() {
    let mut lines = client_search_prefix();
    lines.push(search_output(
        "search-a",
        "2026-10-01T12:00:02.000Z",
        json!([]),
    ));
    lines.push(search_output(
        "search-a",
        "2026-10-01T12:00:03.000Z",
        json!([{"name":"late"}]),
    ));
    let normalized = normalize_turn_semantics(&lines);
    let call = &normalized[0].callbacks[0];
    assert_eq!(call.completed_at, "2026-10-01T12:00:02.000Z");
    assert_eq!(call.output["tools"], json!([]));
    assert_eq!(
        build_turns(&lines)[0].tool_calls[0].ended_timestamp,
        call.completed_at
    );
}

#[test]
fn orphan_search_output_does_not_complete_another_call() {
    let mut lines = client_search_prefix();
    lines.push(search_output(
        "unknown-search",
        "2026-10-01T12:00:02.000Z",
        json!([]),
    ));
    let normalized = normalize_turn_semantics(&lines);
    assert_eq!(normalized[0].callbacks.len(), 1);
    assert!(!normalized[0].callbacks[0].terminal);
    assert!(build_turns(&lines)[0].tool_calls[0]
        .ended_timestamp
        .is_empty());
}
