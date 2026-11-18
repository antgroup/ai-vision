use std::collections::HashMap;

use serde_json::{json, Value};

use crate::common::otel::{
    build_realtime_generation_spans_for_batch, build_realtime_tool_span_for_call,
    wrap_realtime_child_spans,
};
use crate::common::payload_budget::MAX_SERIALIZED_SPAN_BYTES;
use crate::common::transcript::{ToolCall, Turn, TurnTerminalState};

/// Chunking target for a multi-span OTLP request body, not an absolute request
/// size limit. The hard per-span budget is measured on the bare serialized
/// span and intentionally excludes the resource/scope envelope, so a single
/// valid span may produce a larger one-span request.
pub const REALTIME_TARGET_SERIALIZED_BODY_BYTES: usize = MAX_SERIALIZED_SPAN_BYTES;
pub const REALTIME_MAX_CHUNKS_PER_CLAIM: usize = 4;

#[derive(Debug, Clone, PartialEq)]
pub struct RealtimeSemanticChild {
    pub child_id: String,
    pub span_id: String,
    pub body: Value,
    pub payload_digest: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RealtimePayloadChunk {
    pub cursor: u64,
    pub child_ids: Vec<String>,
    pub span_ids: Vec<String>,
    pub digests: Vec<String>,
    pub body: Value,
    pub body_len: usize,
    pub payload_digest: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RealtimePayloadChunkPlan {
    pub chunks: Vec<RealtimePayloadChunk>,
    pub oversized_children: Vec<RealtimeSemanticChild>,
}

pub fn extract_realtime_semantic_children(
    payload: &Value,
    identities: &[(String, String)],
) -> Result<Vec<RealtimeSemanticChild>, String> {
    let resource_spans = payload
        .get("resourceSpans")
        .and_then(Value::as_array)
        .ok_or("realtime OTLP payload has no resourceSpans array")?;
    let mut bodies = Vec::new();
    for resource_span in resource_spans {
        let scope_spans = resource_span
            .get("scopeSpans")
            .and_then(Value::as_array)
            .ok_or("realtime OTLP resource has no scopeSpans array")?;
        for scope_span in scope_spans {
            let spans = scope_span
                .get("spans")
                .and_then(Value::as_array)
                .ok_or("realtime OTLP scope has no spans array")?;
            for span in spans {
                let mut isolated_scope = scope_span.clone();
                isolated_scope["spans"] = Value::Array(vec![span.clone()]);
                let mut isolated_resource = resource_span.clone();
                isolated_resource["scopeSpans"] = Value::Array(vec![isolated_scope]);
                bodies.push(json!({"resourceSpans": [isolated_resource]}));
            }
        }
    }
    if bodies.len() != identities.len() {
        return Err(format!(
            "realtime semantic child identity mismatch: spans={} identities={}",
            bodies.len(),
            identities.len()
        ));
    }
    bodies
        .into_iter()
        .zip(identities)
        .map(|(body, (child_id, span_id))| {
            let payload_digest = crate::common::spool::payload_digest(&body)?;
            Ok(RealtimeSemanticChild {
                child_id: child_id.clone(),
                span_id: span_id.clone(),
                body,
                payload_digest,
            })
        })
        .collect()
}

/// Split a complete finalizer request into independently deliverable span
/// payloads while keeping the turn root last.  The root is the first span
/// emitted by the canonical turn builder; child-session merge may append
/// additional resource/scope groups, but does not reorder that first span.
/// Delivering the root last makes a committed root a durable indication that
/// every preceding child chunk was already accounted.
pub fn extract_finalizer_spans_root_last(
    payload: &Value,
) -> Result<(Vec<RealtimeSemanticChild>, String), String> {
    let resource_spans = payload
        .get("resourceSpans")
        .and_then(Value::as_array)
        .ok_or("finalizer OTLP payload has no resourceSpans array")?;
    let root_span_id = resource_spans
        .first()
        .and_then(|resource| resource.get("scopeSpans"))
        .and_then(Value::as_array)
        .and_then(|scopes| scopes.first())
        .and_then(|scope| scope.get("spans"))
        .and_then(Value::as_array)
        .and_then(|spans| spans.first())
        .and_then(|span| span.get("spanId"))
        .and_then(Value::as_str)
        .filter(|span_id| !span_id.is_empty())
        .ok_or("finalizer OTLP payload has no root span identity")?
        .to_string();

    let mut children = Vec::new();
    let mut root = None;
    for resource_span in resource_spans {
        let scope_spans = resource_span
            .get("scopeSpans")
            .and_then(Value::as_array)
            .ok_or("finalizer OTLP resource has no scopeSpans array")?;
        for scope_span in scope_spans {
            let spans = scope_span
                .get("spans")
                .and_then(Value::as_array)
                .ok_or("finalizer OTLP scope has no spans array")?;
            for span in spans {
                let span_id = span
                    .get("spanId")
                    .and_then(Value::as_str)
                    .filter(|span_id| !span_id.is_empty())
                    .ok_or("finalizer OTLP span has no spanId")?;
                let mut isolated_scope = scope_span.clone();
                isolated_scope["spans"] = Value::Array(vec![span.clone()]);
                let mut isolated_resource = resource_span.clone();
                isolated_resource["scopeSpans"] = Value::Array(vec![isolated_scope]);
                let body = json!({"resourceSpans": [isolated_resource]});
                let child = RealtimeSemanticChild {
                    child_id: format!("finalizer-span:{span_id}"),
                    span_id: span_id.to_string(),
                    payload_digest: crate::common::spool::payload_digest(&body)?,
                    body,
                };
                if span_id == root_span_id {
                    if root.replace(child).is_some() {
                        return Err(format!(
                            "finalizer OTLP payload contains duplicate root span: {root_span_id}"
                        ));
                    }
                } else {
                    children.push(child);
                }
            }
        }
    }
    children.push(root.ok_or_else(|| {
        format!("finalizer OTLP root span disappeared while splitting: {root_span_id}")
    })?);
    Ok((children, root_span_id))
}

pub fn finalizer_semantic_body(
    turn_id: &str,
    root_span_id: &str,
    body: &Value,
) -> Result<RealtimeSemanticChild, String> {
    Ok(RealtimeSemanticChild {
        child_id: format!("finalizer-turn:{turn_id}"),
        span_id: root_span_id.to_string(),
        payload_digest: crate::common::spool::payload_digest(body)?,
        body: body.clone(),
    })
}

pub fn validate_persisted_payload(
    body: &Value,
    expected_digest: &str,
    expected_len: usize,
) -> Result<(), String> {
    let actual_len = serde_json::to_vec(body)
        .map_err(|error| format!("failed to serialize persisted delivery payload: {error}"))?
        .len();
    if actual_len != expected_len {
        return Err(format!(
            "persisted delivery payload length mismatch: expected={expected_len} actual={actual_len}"
        ));
    }
    let actual_digest = crate::common::spool::payload_digest(body)?;
    if actual_digest != expected_digest {
        return Err(format!(
            "persisted delivery payload digest mismatch: expected={expected_digest} actual={actual_digest}"
        ));
    }
    Ok(())
}

pub fn plan_realtime_payload_chunks(
    children: Vec<RealtimeSemanticChild>,
    target_body_bytes: usize,
) -> Result<RealtimePayloadChunkPlan, String> {
    if target_body_bytes == 0 {
        return Err("realtime target body bytes must be positive".to_string());
    }
    let mut chunks = Vec::new();
    let mut oversized_children = Vec::new();
    let mut current = Vec::<RealtimeSemanticChild>::new();

    for child in children {
        serialized_span_len(&child.body, &child.span_id)?;
        if crate::common::payload_budget::validate_export_body_span_budget(&child.body).is_err() {
            oversized_children.push(child);
            continue;
        }
        let child_body = merge_realtime_payloads(std::slice::from_ref(&child.body))?;
        let child_body_len = serde_json::to_vec(&child_body)
            .map_err(|error| error.to_string())?
            .len();
        if child_body_len > target_body_bytes {
            if !current.is_empty() {
                chunks.push(build_payload_chunk(chunks.len() as u64, &current)?);
                current.clear();
            }
            // The body target controls chunking only. Resource and scope
            // wrapper bytes do not turn an otherwise valid bare span into a
            // poison message; deliver it alone instead.
            chunks.push(build_payload_chunk(chunks.len() as u64, &[child])?);
            continue;
        }
        let mut candidate = current.clone();
        candidate.push(child.clone());
        let candidate_payloads = candidate
            .iter()
            .map(|item| item.body.clone())
            .collect::<Vec<_>>();
        let candidate_body = merge_realtime_payloads(&candidate_payloads)?;
        let candidate_len = serde_json::to_vec(&candidate_body)
            .map_err(|error| error.to_string())?
            .len();
        if candidate_len <= target_body_bytes {
            current.push(child);
            continue;
        }
        chunks.push(build_payload_chunk(chunks.len() as u64, &current)?);
        current = vec![child];
    }
    if !current.is_empty() {
        chunks.push(build_payload_chunk(chunks.len() as u64, &current)?);
    }
    Ok(RealtimePayloadChunkPlan {
        chunks,
        oversized_children,
    })
}

fn serialized_span_len(body: &Value, span_id: &str) -> Result<usize, String> {
    let resource_spans = body
        .get("resourceSpans")
        .and_then(Value::as_array)
        .ok_or("OTLP payload has no resourceSpans array")?;
    for resource in resource_spans {
        let scopes = resource
            .get("scopeSpans")
            .and_then(Value::as_array)
            .ok_or("OTLP resource has no scopeSpans array")?;
        for scope in scopes {
            let spans = scope
                .get("spans")
                .and_then(Value::as_array)
                .ok_or("OTLP scope has no spans array")?;
            if let Some(span) = spans
                .iter()
                .find(|span| span.get("spanId").and_then(Value::as_str) == Some(span_id))
            {
                return serde_json::to_vec(span)
                    .map(|bytes| bytes.len())
                    .map_err(|error| format!("failed to serialize span {span_id}: {error}"));
            }
        }
    }
    Err(format!("OTLP payload has no span identity {span_id}"))
}

fn build_payload_chunk(
    cursor: u64,
    children: &[RealtimeSemanticChild],
) -> Result<RealtimePayloadChunk, String> {
    let payloads = children
        .iter()
        .map(|child| child.body.clone())
        .collect::<Vec<_>>();
    let body = merge_realtime_payloads(&payloads)?;
    let body_len = serde_json::to_vec(&body)
        .map_err(|error| error.to_string())?
        .len();
    Ok(RealtimePayloadChunk {
        cursor,
        child_ids: children
            .iter()
            .map(|child| child.child_id.clone())
            .collect(),
        span_ids: children.iter().map(|child| child.span_id.clone()).collect(),
        digests: children
            .iter()
            .map(|child| child.payload_digest.clone())
            .collect(),
        payload_digest: crate::common::spool::payload_digest(&body)?,
        body,
        body_len,
    })
}

#[derive(Debug, Clone, PartialEq)]
pub struct RealtimeToolCompletion {
    pub tool_use_id: String,
    pub tool_name: String,
    pub tool_input: Value,
    pub tool_output: Value,
    pub started_at: String,
    pub completed_at: String,
    pub success: Option<bool>,
    pub chat_id: Option<String>,
    pub cwd: String,
}

#[allow(clippy::too_many_arguments)]
pub fn build_realtime_tool_payload(
    completion: &RealtimeToolCompletion,
    trace_id: &str,
    root_span_id: &str,
    username: &str,
    session_id: &str,
    build_version: &str,
) -> Result<Value, String> {
    let turn = payload_only_tool_turn(completion);
    let span = build_realtime_tool_span_for_call(
        &turn,
        &completion.tool_use_id,
        trace_id,
        root_span_id,
        username,
        session_id,
    )
    .ok_or_else(|| {
        format!(
            "tool call not found for realtime export: {}",
            completion.tool_use_id
        )
    })?;
    Ok(wrap_realtime_child_spans(
        vec![span],
        session_id,
        build_version,
    ))
}

fn payload_only_tool_turn(completion: &RealtimeToolCompletion) -> Turn {
    Turn {
        chat_id: completion.chat_id.clone(),
        turn_id: completion.chat_id.clone().unwrap_or_default(),
        user_timestamp: completion.started_at.clone(),
        user_text: String::new(),
        user_content: serde_json::Value::Null,
        assistants: Vec::new(),
        tool_calls: vec![ToolCall {
            call_id: completion.tool_use_id.clone(),
            name: completion.tool_name.clone(),
            input: completion.tool_input.clone(),
            output: completion.tool_output.clone(),
            started_timestamp: completion.started_at.clone(),
            ended_timestamp: completion.completed_at.clone(),
            assistant_index: 0,
            success: completion.success,
        }],
        model: String::new(),
        cwd: completion.cwd.clone(),
        system_instructions: String::new(),
        completed: true,
        completion_timestamp: completion.completed_at.clone(),
        terminal_state: TurnTerminalState::Completed,
        terminal_error: None,
        trigger: Default::default(),
        skill_invocations: Vec::new(),
        skill_name: None,
        compactions: Vec::new(),
        collab_agent_events: Vec::new(),
        subagent_source: None,
    }
}

#[allow(clippy::too_many_arguments)]
pub fn build_realtime_generation_payload(
    turn: &Turn,
    selections: &[(usize, String)],
    batch_size: usize,
    trace_id: &str,
    root_span_id: &str,
    username: &str,
    session_id: &str,
    build_version: &str,
) -> Result<Value, String> {
    if batch_size == 0 || selections.is_empty() || selections.len() % batch_size != 0 {
        return Err(format!(
            "realtime generation export requires one or more full batches: selected={} batch_size={batch_size}",
            selections.len()
        ));
    }
    let spans = build_realtime_generation_spans_for_batch(
        turn,
        selections,
        trace_id,
        root_span_id,
        username,
        session_id,
        None,
    );
    if spans.len() != selections.len() {
        return Err(format!(
            "realtime generation batches contain incomplete selections: built={} selected={}",
            spans.len(),
            selections.len()
        ));
    }
    Ok(wrap_realtime_child_spans(spans, session_id, build_version))
}

#[allow(clippy::too_many_arguments)]
pub fn build_realtime_generation_payload_for_turns(
    turns: &[Turn],
    selections: &[(usize, usize, String)],
    turn_topologies: &HashMap<usize, (String, String)>,
    batch_size: usize,
    flush_partial: bool,
    username: &str,
    session_id: &str,
    build_version: &str,
    state: &Value,
) -> Result<Value, String> {
    if batch_size == 0
        || selections.is_empty()
        || (!flush_partial && !selections.len().is_multiple_of(batch_size))
    {
        return Err(format!(
            "realtime generation export requires one or more full batches unless the scan window is terminal: selected={} batch_size={batch_size} flush_partial={flush_partial}",
            selections.len()
        ));
    }

    let mut spans = Vec::with_capacity(selections.len());
    for (turn_index, turn) in turns.iter().enumerate() {
        let turn_selections = selections
            .iter()
            .filter(|(selected_turn, _, _)| *selected_turn == turn_index)
            .map(|(_, assistant_index, span_id)| (*assistant_index, span_id.clone()))
            .collect::<Vec<_>>();
        if turn_selections.is_empty() {
            continue;
        }
        let (trace_id, root_span_id) = turn_topologies
            .get(&turn_index)
            .ok_or_else(|| format!("realtime generation turn {turn_index} has no topology"))?;
        spans.extend(build_realtime_generation_spans_for_batch(
            turn,
            &turn_selections,
            trace_id,
            root_span_id,
            username,
            session_id,
            super::state::turn_billing_mode(state, session_id, &turn.turn_id),
        ));
    }
    if spans.len() != selections.len() {
        return Err(format!(
            "realtime generation batches contain incomplete multi-turn selections: built={} selected={}",
            spans.len(),
            selections.len()
        ));
    }
    Ok(wrap_realtime_child_spans(spans, session_id, build_version))
}

pub fn merge_realtime_payloads(payloads: &[Value]) -> Result<Value, String> {
    let mut resource_spans = Vec::new();
    for payload in payloads {
        let resources = payload
            .get("resourceSpans")
            .and_then(Value::as_array)
            .ok_or("realtime OTLP payload has no resourceSpans array")?;
        resource_spans.extend(resources.iter().cloned());
    }
    if resource_spans.is_empty() {
        return Err("realtime batch has no child spans".to_string());
    }
    Ok(json!({ "resourceSpans": resource_spans }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::otel::{
        build_otel_export_request_for_turn, deterministic_tool_span_id, TestPrivacyModeGuard,
        TurnReplayOpts,
    };
    use crate::common::transcript::{AssistantMessage, TokenUsage, ToolCall, TurnTerminalState};
    use serde_json::json;
    use std::collections::HashMap;

    #[test]
    fn live_model_spans_use_their_own_turn_billing_mode_and_replay_has_none() {
        let turn = make_turn();
        let state = json!({"billing-test-session": {"turn_billing_modes": [
            {"turn_id": "turn-1", "mode": "subscription"},
            {"turn_id": "turn-2", "mode": "pay_as_you_go"},
        ]}});
        let mode = |body: &Value| {
            spans(body)
                .iter()
                .flat_map(|span| span["attributes"].as_array().unwrap())
                .filter(|attribute| attribute["key"] == "aivision.billing.mode")
                .map(|attribute| {
                    attribute["value"]["stringValue"]
                        .as_str()
                        .unwrap()
                        .to_string()
                })
                .collect::<Vec<_>>()
        };
        let live = |turn: &Turn| {
            let opts = crate::hooks::langfuse::turn_trace_identity::build_turn_trace_opts(
                &state,
                "billing-test-session",
                &turn.turn_id,
            );
            build_otel_export_request_for_turn(
                turn,
                1,
                "billing-test-session",
                "tester",
                "v",
                opts.as_ref(),
            )
        };
        assert_eq!(mode(&live(&turn)), vec!["subscription", "subscription"]);
        let mut second = turn.clone();
        second.turn_id = "turn-2".to_string();
        assert_eq!(mode(&live(&second)), vec!["pay_as_you_go", "pay_as_you_go"]);
        second.turn_id = "unknown-turn".to_string();
        assert!(mode(&live(&second)).is_empty());
        let replay = build_otel_export_request_for_turn(
            &turn,
            1,
            "billing-test-session",
            "tester",
            "v",
            None,
        );
        assert!(mode(&replay).is_empty());
    }

    fn make_turn() -> Turn {
        Turn {
            chat_id: Some("chat-1".to_string()),
            turn_id: "turn-1".to_string(),
            user_timestamp: "2026-03-28T08:00:00.000Z".to_string(),
            user_text: "run diagnostics".to_string(),
            user_content: serde_json::Value::Null,
            assistants: vec![
                AssistantMessage {
                    timestamp: "2026-03-28T08:00:01.000Z".to_string(),
                    text: "I will inspect.".to_string(),
                    media_content: serde_json::Value::Null,
                    phase: "commentary".to_string(),
                    usage: TokenUsage {
                        input: Some(10),
                        output: Some(5),
                        total: Some(15),
                        ..TokenUsage::default()
                    },
                    token_count_timestamp: "2026-03-28T08:00:01.500Z".to_string(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
                AssistantMessage {
                    timestamp: "2026-03-28T08:00:04.000Z".to_string(),
                    text: "Done.".to_string(),
                    media_content: serde_json::Value::Null,
                    phase: "final_answer".to_string(),
                    usage: TokenUsage {
                        input: Some(20),
                        output: Some(6),
                        total: Some(26),
                        ..TokenUsage::default()
                    },
                    token_count_timestamp: "2026-03-28T08:00:04.500Z".to_string(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
            ],
            tool_calls: vec![ToolCall {
                call_id: "call-1".to_string(),
                name: "Bash".to_string(),
                input: json!({"command": "pwd"}),
                output: Value::String("/tmp/demo\n".to_string()),
                started_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
                ended_timestamp: "2026-03-28T08:00:03.000Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            }],
            model: "gpt-5.4".to_string(),
            cwd: "/repo".to_string(),
            system_instructions: "Follow realtime policy.".to_string(),
            completed: true,
            completion_timestamp: "2026-03-28T08:00:05.000Z".to_string(),
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

    fn spans(body: &Value) -> Vec<&Value> {
        body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .unwrap()
            .iter()
            .collect()
    }

    #[test]
    fn merged_realtime_payload_is_one_otlp_request_with_all_children() {
        let turn = make_turn();
        let generation = build_realtime_generation_payload(
            &turn,
            &[(0, "aaaaaaaaaaaaaaaa".to_string())],
            1,
            "11111111111111111111111111111111",
            "2222222222222222",
            "tester",
            "session",
            "1.0.0",
        )
        .unwrap();
        let tool = build_realtime_tool_payload(
            &RealtimeToolCompletion {
                tool_use_id: "call-1".to_string(),
                tool_name: "Bash".to_string(),
                tool_input: json!({"command": "pwd"}),
                tool_output: json!({"exit_code": 0}),
                started_at: "2026-03-28T08:00:02.000Z".to_string(),
                completed_at: "2026-03-28T08:00:03.000Z".to_string(),
                success: Some(true),
                chat_id: Some("chat-1".to_string()),
                cwd: "/repo".to_string(),
            },
            "11111111111111111111111111111111",
            "2222222222222222",
            "tester",
            "session",
            "1.0.0",
        )
        .unwrap();

        let merged = merge_realtime_payloads(&[generation, tool]).unwrap();
        let child_count = merged["resourceSpans"]
            .as_array()
            .unwrap()
            .iter()
            .flat_map(|resource| {
                resource["scopeSpans"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .flat_map(|scope| scope["spans"].as_array().into_iter().flatten())
            })
            .count();
        assert_eq!(child_count, 2);
    }

    fn attr_string(span: &Value, key: &str) -> Option<String> {
        span["attributes"].as_array()?.iter().find_map(|attr| {
            if attr["key"] == key {
                attr["value"]["stringValue"].as_str().map(str::to_string)
            } else {
                None
            }
        })
    }

    fn attr_value(span: &Value, key: &str) -> Option<Value> {
        span["attributes"]
            .as_array()?
            .iter()
            .find_map(|attr| (attr["key"] == key).then(|| attr["value"].clone()))
    }

    fn resource_attr_string(body: &Value, key: &str) -> Option<String> {
        body["resourceSpans"][0]["resource"]["attributes"]
            .as_array()?
            .iter()
            .find_map(|attr| {
                (attr["key"] == key)
                    .then(|| attr["value"]["stringValue"].as_str().map(str::to_string))
                    .flatten()
            })
    }

    fn attr_map_without_ids(span: &Value) -> HashMap<String, Value> {
        span["attributes"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|attr| {
                let key = attr["key"].as_str()?.to_string();
                Some((key, attr["value"].clone()))
            })
            .collect()
    }

    #[test]
    fn realtime_tool_payload_contains_only_one_tool_span_with_persisted_topology() {
        let turn = make_turn();
        let completion = completion_from_turn(&turn);
        let body = build_realtime_tool_payload(
            &completion,
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "bbbbbbbbbbbbbbbb",
            "tester",
            "session-1",
            "test-version",
        )
        .unwrap();
        let spans = spans(&body);

        assert_eq!(spans.len(), 1);
        assert_eq!(spans[0]["name"], "tool Bash");
        assert_eq!(spans[0]["traceId"], "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        assert_eq!(spans[0]["parentSpanId"], "bbbbbbbbbbbbbbbb");
        assert_eq!(spans[0]["spanId"], deterministic_tool_span_id("call-1"));
        assert_eq!(
            attr_string(spans[0], "gen_ai.span.kind").as_deref(),
            Some("TOOL")
        );
        assert_eq!(
            resource_attr_string(&body, "service.name").as_deref(),
            Some("Codex")
        );
        assert_eq!(
            resource_attr_string(&body, "service.version").as_deref(),
            Some("test-version")
        );
        assert_eq!(
            resource_attr_string(&body, "ant.agent.type").as_deref(),
            Some("codex")
        );
        assert!(
            resource_attr_string(&body, "ant.agent.instance").is_some(),
            "realtime requests must retain the deployed instance identity"
        );
        assert_eq!(
            resource_attr_string(&body, "agentic.collection.mode").as_deref(),
            Some("full")
        );
    }

    #[test]
    fn realtime_tool_semantics_match_stop_builder_for_same_completion() {
        let turn = make_turn();
        let completion = completion_from_turn(&turn);
        let trace_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        let root_span_id = "bbbbbbbbbbbbbbbb";
        let opts = TurnReplayOpts {
            trace_id: Some(trace_id.to_string()),
            root_span_id: Some(root_span_id.to_string()),
            parent_span_id: None,
            sofa_traceid: None,
            sofa_rpcid: None,
            billing_mode: None,
            generation_span_ids: HashMap::new(),
            subagent_span_ids: HashMap::new(),
        };
        let stop_body = build_otel_export_request_for_turn(
            &turn,
            1,
            "session-1",
            "tester",
            "test-version",
            Some(&opts),
        );
        let realtime_body = build_realtime_tool_payload(
            &completion,
            trace_id,
            root_span_id,
            "tester",
            "session-1",
            "test-version",
        )
        .unwrap();
        let stop_tool = spans(&stop_body)
            .into_iter()
            .find(|span| attr_string(span, "gen_ai.span.kind").as_deref() == Some("TOOL"))
            .expect("tool span");
        let realtime_tool = spans(&realtime_body)[0];

        assert_eq!(realtime_tool["name"], stop_tool["name"]);
        assert_eq!(
            realtime_tool["startTimeUnixNano"],
            stop_tool["startTimeUnixNano"]
        );
        assert_eq!(
            realtime_tool["endTimeUnixNano"],
            stop_tool["endTimeUnixNano"]
        );
        assert_eq!(realtime_tool["status"], stop_tool["status"]);
        assert_eq!(
            attr_map_without_ids(realtime_tool),
            attr_map_without_ids(stop_tool)
        );
    }

    #[test]
    fn realtime_tool_payload_preserves_exact_unix_nano_duration() {
        let mut completion = completion_from_turn(&make_turn());
        completion.started_at = "1767225600000000000".to_string();
        completion.completed_at = "1767225605250000000".to_string();

        let body = build_realtime_tool_payload(
            &completion,
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "bbbbbbbbbbbbbbbb",
            "tester",
            "session-1",
            "test-version",
        )
        .unwrap();
        let span = spans(&body)[0];

        assert_eq!(span["startTimeUnixNano"], "1767225600000000000");
        assert_eq!(span["endTimeUnixNano"], "1767225605250000000");
        assert_eq!(
            attr_string(span, "agentic.tool.duration_ms").as_deref(),
            Some("5250")
        );
    }

    #[test]
    fn realtime_generation_payload_contains_only_selected_generation_spans() {
        let turn = make_turn();
        let body = build_realtime_generation_payload(
            &turn,
            &[(1, "cccccccccccccccc".to_string())],
            1,
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "bbbbbbbbbbbbbbbb",
            "tester",
            "session-1",
            "test-version",
        )
        .unwrap();
        let spans = spans(&body);

        assert_eq!(spans.len(), 1);
        assert_eq!(spans[0]["name"], "chat gpt-5.4");
        assert_eq!(spans[0]["traceId"], "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        assert_eq!(spans[0]["parentSpanId"], "bbbbbbbbbbbbbbbb");
        assert_eq!(spans[0]["spanId"], "cccccccccccccccc");
        assert_eq!(
            attr_string(spans[0], "gen_ai.span.kind").as_deref(),
            Some("LLM")
        );
    }

    #[test]
    fn realtime_generation_payload_accepts_multiple_complete_batches() {
        let body = build_realtime_generation_payload(
            &make_turn(),
            &[
                (0, "cccccccccccccccc".to_string()),
                (1, "dddddddddddddddd".to_string()),
            ],
            1,
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "bbbbbbbbbbbbbbbb",
            "tester",
            "session-1",
            "test-version",
        )
        .unwrap();

        assert_eq!(spans(&body).len(), 2);
    }

    #[test]
    fn realtime_generation_payload_flushes_terminal_selections_across_turns() {
        let first = make_turn();
        let mut second = make_turn();
        second.turn_id = "turn-2".to_string();
        second.chat_id = Some("chat-2".to_string());
        second.assistants[0].text = "Second turn.".to_string();
        let body = build_realtime_generation_payload_for_turns(
            &[first, second],
            &[
                (0, 0, "1111111111111111".to_string()),
                (1, 0, "2222222222222222".to_string()),
            ],
            &HashMap::from([
                (
                    0,
                    (
                        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".to_string(),
                        "bbbbbbbbbbbbbbbb".to_string(),
                    ),
                ),
                (
                    1,
                    (
                        "cccccccccccccccccccccccccccccccc".to_string(),
                        "dddddddddddddddd".to_string(),
                    ),
                ),
            ]),
            3,
            true,
            "tester",
            "session-1",
            "test-version",
            &json!({"session-1": {"turn_billing_modes": [
                {"turn_id": "turn-1", "mode": "subscription"},
                {"turn_id": "turn-2", "mode": "pay_as_you_go"},
            ]}}),
        )
        .unwrap();

        let spans = spans(&body);
        assert_eq!(spans.len(), 2);
        assert_eq!(spans[0]["traceId"], "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        assert_eq!(spans[0]["parentSpanId"], "bbbbbbbbbbbbbbbb");
        assert_eq!(spans[1]["traceId"], "cccccccccccccccccccccccccccccccc");
        assert_eq!(spans[1]["parentSpanId"], "dddddddddddddddd");
        for (span, mode) in spans.iter().zip(["subscription", "pay_as_you_go"]) {
            assert!(span["attributes"].as_array().unwrap().iter().any(|attr| {
                attr["key"] == "aivision.billing.mode" && attr["value"]["stringValue"] == mode
            }));
        }
    }

    #[test]
    fn realtime_generation_semantics_match_stop_builder_for_same_assistant() {
        let turn = make_turn();
        let trace_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        let root_span_id = "bbbbbbbbbbbbbbbb";
        let opts = TurnReplayOpts {
            trace_id: Some(trace_id.to_string()),
            root_span_id: Some(root_span_id.to_string()),
            parent_span_id: None,
            sofa_traceid: None,
            sofa_rpcid: None,
            billing_mode: None,
            generation_span_ids: HashMap::new(),
            subagent_span_ids: HashMap::new(),
        };
        let stop_body = build_otel_export_request_for_turn(
            &turn,
            1,
            "session-1",
            "tester",
            "test-version",
            Some(&opts),
        );
        let realtime_body = build_realtime_generation_payload(
            &turn,
            &[(1, "cccccccccccccccc".to_string())],
            1,
            trace_id,
            root_span_id,
            "tester",
            "session-1",
            "test-version",
        )
        .unwrap();
        let stop_generation = spans(&stop_body)
            .into_iter()
            .filter(|span| attr_string(span, "gen_ai.span.kind").as_deref() == Some("LLM"))
            .nth(1)
            .expect("second generation span");
        let realtime_generation = spans(&realtime_body)[0];

        assert_eq!(realtime_generation["name"], stop_generation["name"]);
        assert_eq!(
            realtime_generation["startTimeUnixNano"],
            stop_generation["startTimeUnixNano"]
        );
        assert_eq!(
            realtime_generation["endTimeUnixNano"],
            stop_generation["endTimeUnixNano"]
        );
        assert_eq!(
            attr_map_without_ids(realtime_generation),
            attr_map_without_ids(stop_generation)
        );
        assert_eq!(
            attr_value(realtime_generation, "gen_ai.usage.input_tokens"),
            attr_value(stop_generation, "gen_ai.usage.input_tokens")
        );
        assert_eq!(
            attr_value(realtime_generation, "gen_ai.usage.output_tokens"),
            attr_value(stop_generation, "gen_ai.usage.output_tokens")
        );
    }

    #[test]
    fn realtime_generation_payload_rejects_partial_batches() {
        let turn = make_turn();
        let error = build_realtime_generation_payload(
            &turn,
            &[(0, "cccccccccccccccc".to_string())],
            2,
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "bbbbbbbbbbbbbbbb",
            "tester",
            "session-1",
            "test-version",
        )
        .unwrap_err();
        assert!(error.contains("requires one or more full batches"));
    }

    #[test]
    fn tool_failure_stays_on_tool_span_in_stop_semantics() {
        let mut turn = make_turn();
        turn.tool_calls[0].success = Some(false);
        turn.tool_calls[0].output = json!({"error": "command failed"});
        let body = build_otel_export_request_for_turn(
            &turn,
            1,
            "session-1",
            "tester",
            "test-version",
            None,
        );

        for span in spans(&body) {
            match attr_string(span, "gen_ai.span.kind").as_deref() {
                Some("TOOL") => {
                    assert_eq!(span["status"]["code"], 2);
                    assert_eq!(
                        attr_string(span, "error.type").as_deref(),
                        Some("tool_error")
                    );
                }
                Some("CHAT") | Some("LLM") => {
                    assert_eq!(span["status"]["code"], 1);
                    assert!(attr_string(span, "error.type").is_none());
                }
                _ => {}
            }
        }
    }

    #[test]
    fn aborted_turn_cancels_only_the_trailing_incomplete_generation() {
        let _privacy_mode = TestPrivacyModeGuard::set(false);

        let mut turn = make_turn();
        turn.terminal_state = TurnTerminalState::Aborted;
        let body = build_otel_export_request_for_turn(
            &turn,
            1,
            "session-1",
            "tester",
            "test-version",
            None,
        );

        for span in spans(&body) {
            match attr_string(span, "gen_ai.span.kind").as_deref() {
                Some("CHAT") => {
                    assert_eq!(span["status"]["code"], 2);
                    assert_eq!(
                        attr_string(span, "error.type").as_deref(),
                        Some("cancelled")
                    );
                    assert_eq!(
                        attr_string(span, "gen_ai.system_instructions"),
                        Some(
                            r#"[{"role":"system","content":"Follow realtime policy."}]"#
                                .to_string()
                        )
                    );
                }
                Some("LLM") => {
                    let response_index = attr_string(span, "agentic.response.index")
                        .expect("generation response index");
                    if response_index == "0" {
                        assert_eq!(span["status"]["code"], 1);
                        assert!(attr_string(span, "error.type").is_none());
                        assert_eq!(
                            attr_string(span, "gen_ai.response.finish_reasons").as_deref(),
                            Some("tool_calls")
                        );
                    } else {
                        assert_eq!(response_index, "1");
                        assert_eq!(span["status"]["code"], 2);
                        assert_eq!(
                            attr_string(span, "error.type").as_deref(),
                            Some("cancelled")
                        );
                        assert_eq!(
                            attr_string(span, "gen_ai.response.finish_reasons").as_deref(),
                            Some("cancelled")
                        );
                    }
                    assert_eq!(
                        attr_string(span, "gen_ai.system_instructions"),
                        Some(
                            r#"[{"role":"system","content":"Follow realtime policy."}]"#
                                .to_string()
                        )
                    );
                }
                _ => {}
            }
        }
    }

    #[test]
    fn aborted_turn_keeps_all_completed_tool_generations_successful() {
        let mut turn = make_turn();
        turn.terminal_state = TurnTerminalState::Aborted;
        turn.tool_calls.push(ToolCall {
            call_id: "call-2".to_string(),
            name: "Bash".to_string(),
            input: json!({"command": "echo done"}),
            output: Value::String("done\n".to_string()),
            started_timestamp: "2026-03-28T08:00:04.000Z".to_string(),
            ended_timestamp: "2026-03-28T08:00:04.250Z".to_string(),
            assistant_index: 1,
            success: Some(true),
        });
        let body = build_otel_export_request_for_turn(
            &turn,
            1,
            "session-1",
            "tester",
            "test-version",
            None,
        );

        let mut llm_count = 0;
        for span in spans(&body) {
            match attr_string(span, "gen_ai.span.kind").as_deref() {
                Some("CHAT") => assert_eq!(span["status"]["code"], 2),
                Some("LLM") => {
                    llm_count += 1;
                    assert_eq!(span["status"]["code"], 1);
                    assert!(attr_string(span, "error.type").is_none());
                    assert_eq!(
                        attr_string(span, "gen_ai.response.finish_reasons").as_deref(),
                        Some("tool_calls")
                    );
                }
                _ => {}
            }
        }
        assert_eq!(llm_count, 2);
    }

    #[test]
    fn realtime_chunk_plan_keeps_children_atomic_and_retains_suffix_beyond_four() {
        let spans = (0..5)
            .map(|index| {
                json!({
                    "traceId": "11111111111111111111111111111111",
                    "spanId": format!("{index:016x}"),
                    "name": format!("child-{index}"),
                    "attributes": [{
                        "key": "test.payload",
                        "value": {"stringValue": "x".repeat(256)}
                    }]
                })
            })
            .collect::<Vec<_>>();
        let payload = json!({
            "resourceSpans": [{
                "resource": {"attributes": []},
                "scopeSpans": [{"scope": {"name": "test"}, "spans": spans}]
            }]
        });
        let identities = (0..5)
            .map(|index| (format!("child-{index}"), format!("{index:016x}")))
            .collect::<Vec<_>>();
        let children = extract_realtime_semantic_children(&payload, &identities).unwrap();
        let single_len = serde_json::to_vec(&children[0].body).unwrap().len();
        let plan = plan_realtime_payload_chunks(children, single_len + 32).unwrap();

        assert_eq!(plan.chunks.len(), 5);
        assert!(plan.oversized_children.is_empty());
        assert!(plan
            .chunks
            .iter()
            .all(|chunk| chunk.child_ids.len() == 1 && chunk.body_len <= single_len + 32));
        assert_eq!(
            plan.chunks
                .iter()
                .flat_map(|chunk| chunk.child_ids.clone())
                .collect::<Vec<_>>(),
            identities
                .iter()
                .map(|(child_id, _)| child_id.clone())
                .collect::<Vec<_>>()
        );
        assert_eq!(REALTIME_MAX_CHUNKS_PER_CLAIM, 4);
        assert_eq!(REALTIME_TARGET_SERIALIZED_BODY_BYTES, 4_194_304);
    }

    #[test]
    fn finalizer_chunk_plan_keeps_root_in_the_final_chunk() {
        let payload = json!({
            "resourceSpans": [{
                "resource": {"attributes": []},
                "scopeSpans": [{
                    "scope": {"name": "test"},
                    "spans": [
                        {"traceId": "11", "spanId": "root", "name": "root"},
                        {"traceId": "11", "spanId": "child-a", "parentSpanId": "root", "name": "a", "attributes": [{"key": "payload", "value": {"stringValue": "x".repeat(256)}}]},
                        {"traceId": "11", "spanId": "child-b", "parentSpanId": "root", "name": "b", "attributes": [{"key": "payload", "value": {"stringValue": "y".repeat(256)}}]}
                    ]
                }]
            }]
        });
        let (children, root_span_id) = extract_finalizer_spans_root_last(&payload).unwrap();
        assert_eq!(root_span_id, "root");
        assert_eq!(children.last().unwrap().span_id, "root");
        let single_len = children
            .iter()
            .map(|child| serde_json::to_vec(&child.body).unwrap().len())
            .max()
            .unwrap();
        let plan = plan_realtime_payload_chunks(children, single_len + 32).unwrap();
        assert!(plan.oversized_children.is_empty());
        assert!(plan
            .chunks
            .iter()
            .all(|chunk| chunk.body_len <= single_len + 32));
        assert_eq!(
            plan.chunks
                .last()
                .unwrap()
                .span_ids
                .last()
                .map(String::as_str),
            Some("root")
        );
        assert!(!plan.chunks[..plan.chunks.len() - 1]
            .iter()
            .any(|chunk| chunk.span_ids.iter().any(|span_id| span_id == "root")));
    }

    #[test]
    fn realtime_chunk_plan_delivers_a_valid_bare_span_even_when_wrapper_exceeds_target() {
        let payload = json!({
            "resourceSpans": [{
                "resource": {"attributes": []},
                "scopeSpans": [{
                    "scope": {"name": "test"},
                    "spans": [{
                        "traceId": "11111111111111111111111111111111",
                        "spanId": "2222222222222222",
                        "name": "oversized",
                        "attributes": [{
                            "key": "test.payload",
                            "value": {"stringValue": "x".repeat(4096)}
                        }]
                    }]
                }]
            }]
        });
        let children = extract_realtime_semantic_children(
            &payload,
            &[(
                "generation:oversized".to_string(),
                "2222222222222222".to_string(),
            )],
        )
        .unwrap();
        let plan = plan_realtime_payload_chunks(children, 512).unwrap();

        assert_eq!(plan.chunks.len(), 1);
        assert_eq!(plan.chunks[0].child_ids, ["generation:oversized"]);
        assert!(plan.chunks[0].body_len > 512);
        assert!(plan.oversized_children.is_empty());
    }

    #[test]
    fn realtime_chunk_plan_terminalizes_only_a_bare_span_over_four_mib() {
        let payload = json!({
            "resourceSpans": [{
                "resource": {"attributes": []},
                "scopeSpans": [{
                    "scope": {"name": "test"},
                    "spans": [{
                        "traceId": "11111111111111111111111111111111",
                        "spanId": "2222222222222222",
                        "name": "oversized",
                        "attributes": [{
                            "key": "test.payload",
                            "value": {"stringValue": "x".repeat(MAX_SERIALIZED_SPAN_BYTES)}
                        }]
                    }]
                }]
            }]
        });
        let children = extract_realtime_semantic_children(
            &payload,
            &[(
                "generation:oversized".to_string(),
                "2222222222222222".to_string(),
            )],
        )
        .unwrap();
        let bare_len = serialized_span_len(&children[0].body, &children[0].span_id).unwrap();
        assert!(bare_len > MAX_SERIALIZED_SPAN_BYTES);

        let plan =
            plan_realtime_payload_chunks(children, REALTIME_TARGET_SERIALIZED_BODY_BYTES).unwrap();

        assert!(plan.chunks.is_empty());
        assert_eq!(plan.oversized_children.len(), 1);
        assert_eq!(plan.oversized_children[0].child_id, "generation:oversized");
    }

    fn completion_from_turn(turn: &Turn) -> RealtimeToolCompletion {
        let tool = &turn.tool_calls[0];
        RealtimeToolCompletion {
            tool_use_id: tool.call_id.clone(),
            tool_name: tool.name.clone(),
            tool_input: tool.input.clone(),
            tool_output: tool.output.clone(),
            started_at: tool.started_timestamp.clone(),
            completed_at: tool.ended_timestamp.clone(),
            success: tool.success,
            chat_id: turn.chat_id.clone(),
            cwd: turn.cwd.clone(),
        }
    }
}
