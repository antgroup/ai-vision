//! collab ownership module.

use super::{HashSet, Value};
use super::{SubagentIdentity, SubagentSourceInfo};

/// CollabAgent event parsed from the Codex transcript.
///
/// Captures sub-agent lifecycle and interaction events emitted by the
/// Codex multi-agent collaboration protocol.
#[derive(Debug, Clone, PartialEq)]
pub enum CollabAgentEvent {
    /// First-class evidence emitted by `item_completed.item.type=CollabAgentToolCall`.
    /// It remains separate from ordinary `ToolCall` so the outer Codex `exec`
    /// span stays observable without inventing a synthetic spawn/wait TOOL.
    ToolInvocation(CollabAgentInvocationEvidence),
    /// Agent spawn completed — a new child thread was created.
    SpawnEnd {
        call_id: String,
        sender_thread_id: String,
        new_thread_id: Option<String>,
        nickname: Option<String>,
        role: Option<String>,
        prompt: String,
        model: String,
        status: String,
    },
    /// Agent interaction started — a message was sent from one agent to another.
    InteractionBegin {
        call_id: String,
        sender_thread_id: String,
        receiver_thread_id: String,
        prompt: String,
    },
    /// Agent interaction completed.
    InteractionEnd {
        call_id: String,
        sender_thread_id: String,
        receiver_thread_id: String,
        receiver_nickname: Option<String>,
        receiver_role: Option<String>,
        prompt: String,
        status: String,
    },
    /// Native multi-agent v2 activity mapping a spawn call to its child.
    SubagentActivity {
        event_id: String,
        agent_thread_id: String,
        agent_path: Option<String>,
        kind: String,
        timestamp: String,
    },
    /// Typed inter-agent message delivered to the parent transcript.
    AgentMessage {
        author: String,
        recipient: String,
        message_type: Option<String>,
        content: String,
        timestamp: String,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CollabInvocationSource {
    NativeToolCall,
    LegacyToolCall,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CollabInvocationState {
    Pending,
    Completed,
    Interrupted,
    Error,
    Malformed,
}

/// Receiver-scoped collaboration evidence. Native waits with multiple
/// receivers produce one value per child, preventing sibling terminal state
/// or output from leaking across identities.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CollabAgentInvocationEvidence {
    pub turn_index: usize,
    pub source: CollabInvocationSource,
    pub invocation_id: String,
    pub tool_name: String,
    pub sender_thread_id: Option<String>,
    pub child_thread_id: Option<String>,
    pub nickname: Option<String>,
    pub role: Option<String>,
    pub prompt: Option<String>,
    pub model: Option<String>,
    pub started_at_unix_nano: Option<String>,
    pub completed_at_unix_nano: Option<String>,
    pub state: CollabInvocationState,
    pub status: String,
    pub terminal_output: Option<String>,
    pub reason: String,
}

/// One child activation after receiver-scoped wait evidence has been folded
/// into its exact spawn. Native activation timing starts at the spawn item and
/// ends only at a terminal child state; pending waits never fabricate an end.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SubagentActivationEvidence {
    pub turn_index: usize,
    pub source: CollabInvocationSource,
    pub activation_id: String,
    pub tool_name: String,
    pub sender_thread_id: Option<String>,
    pub child_thread_id: Option<String>,
    pub nickname: Option<String>,
    pub role: Option<String>,
    pub prompt: Option<String>,
    pub model: Option<String>,
    pub started_at_unix_nano: Option<String>,
    pub completed_at_unix_nano: Option<String>,
    pub state: CollabInvocationState,
    pub status: String,
    pub terminal_output: Option<String>,
    pub wait_invocation_ids: Vec<String>,
    pub reason: String,
}

/// Whether a child has terminal evidence or needs independent recovery.
/// Deferred children never block unrelated ready children or their parent.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SubagentDiscoveryDisposition {
    Eligible,
    Deferred,
}

/// One child-discovery decision derived only from parsed transcript semantics.
/// File readiness, retry budgets, and checkpoint state remain adapter concerns.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SubagentDiscoveryDecision {
    pub child: Option<SubagentIdentity>,
    pub activation_id: Option<String>,
    pub source: CollabInvocationSource,
    pub disposition: SubagentDiscoveryDisposition,
    pub reason: String,
    pub activation: Option<SubagentActivationEvidence>,
}

/// A structured activation that is intentionally outside child discovery for
/// the current turn. This keeps spawn-only async evidence observable without
/// turning the wrapper's empty expected-id path into a silent drop.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SubagentObservation {
    pub child_session_id: String,
    pub activation_id: String,
    pub source: CollabInvocationSource,
    pub reason: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SubagentDiscoveryPlan {
    pub decisions: Vec<SubagentDiscoveryDecision>,
    pub observations: Vec<SubagentObservation>,
}

pub(super) fn extract_response_message_text(payload: &Value) -> String {
    payload
        .get("content")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.get("text").and_then(Value::as_str))
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default()
}

pub(crate) fn is_internal_role_user_envelope(text: &str) -> bool {
    let text = text.trim();
    (text.starts_with("<codex_internal_context") && text.ends_with("</codex_internal_context>"))
        || (text.starts_with("<environment_context>") && text.ends_with("</environment_context>"))
        || (text.starts_with("# AGENTS.md instructions for ")
            && text.contains("<environment_context>"))
}

pub(super) fn parse_subagent_activity(
    payload: &Value,
    timestamp: &str,
) -> Option<CollabAgentEvent> {
    let activity = match payload.get("type").and_then(Value::as_str) {
        Some("sub_agent_activity") => payload,
        Some("item_completed") => payload
            .get("item")
            .filter(|item| item.get("type").and_then(Value::as_str) == Some("SubAgentActivity"))?,
        _ => return None,
    };
    let event_id = activity
        .get("event_id")
        .or_else(|| activity.get("id"))
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())?;
    let agent_thread_id = activity
        .get("agent_thread_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())?;

    Some(CollabAgentEvent::SubagentActivity {
        event_id: event_id.to_string(),
        agent_thread_id: agent_thread_id.to_string(),
        agent_path: activity
            .get("agent_path")
            .and_then(Value::as_str)
            .map(str::to_string),
        kind: activity
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string(),
        timestamp: timestamp.to_string(),
    })
}

pub(super) fn non_empty_json_string(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

pub(super) fn millis_to_unix_nanos(value: Option<&Value>) -> Option<String> {
    let millis = value.and_then(|value| {
        value
            .as_u64()
            .or_else(|| value.as_str().and_then(|value| value.parse::<u64>().ok()))
    })?;
    (millis > 0)
        .then(|| millis.checked_mul(1_000_000))
        .flatten()
        .map(|nanos| nanos.to_string())
}

pub(super) fn terminal_value(value: &Value) -> Option<String> {
    if value.is_null() {
        None
    } else if let Some(value) = value.as_str() {
        (!value.is_empty()).then(|| value.to_string())
    } else {
        serde_json::to_string(value).ok()
    }
}

pub(super) fn collab_agent_state(
    value: Option<&Value>,
) -> (CollabInvocationState, String, Option<String>) {
    let Some(value) = value else {
        return (CollabInvocationState::Pending, "pending".to_string(), None);
    };
    if let Some(status) = value.as_str() {
        let normalized = status.trim().to_ascii_lowercase();
        let state = if normalized.starts_with("completed") {
            CollabInvocationState::Completed
        } else if normalized.starts_with("interrupted") || normalized == "cancelled" {
            CollabInvocationState::Interrupted
        } else if normalized.starts_with("error") || normalized.starts_with("failed") {
            CollabInvocationState::Error
        } else {
            CollabInvocationState::Pending
        };
        return (state, status.to_string(), None);
    }
    if let Some(object) = value.as_object() {
        for (key, state) in [
            ("error", CollabInvocationState::Error),
            ("failed", CollabInvocationState::Error),
            ("interrupted", CollabInvocationState::Interrupted),
            ("cancelled", CollabInvocationState::Interrupted),
            ("completed", CollabInvocationState::Completed),
        ] {
            if let Some(output) = object.get(key) {
                return (state, key.to_string(), terminal_value(output));
            }
        }
        if object
            .get("timed_out")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            return (
                CollabInvocationState::Pending,
                "timed_out".to_string(),
                None,
            );
        }
        if let Some(status) = object.get("status").and_then(Value::as_str) {
            return collab_agent_state(Some(&Value::String(status.to_string())));
        }
    }
    (CollabInvocationState::Pending, "pending".to_string(), None)
}

pub(super) fn malformed_collab_invocation(
    invocation_id: String,
    tool_name: String,
    sender_thread_id: Option<String>,
    reason: &str,
) -> CollabAgentEvent {
    CollabAgentEvent::ToolInvocation(CollabAgentInvocationEvidence {
        turn_index: usize::MAX,
        source: CollabInvocationSource::NativeToolCall,
        invocation_id,
        tool_name,
        sender_thread_id,
        child_thread_id: None,
        nickname: None,
        role: None,
        prompt: None,
        model: None,
        started_at_unix_nano: None,
        completed_at_unix_nano: None,
        state: CollabInvocationState::Malformed,
        status: "malformed".to_string(),
        terminal_output: None,
        reason: reason.to_string(),
    })
}

pub(super) fn parse_collab_agent_tool_call(payload: &Value) -> Option<Vec<CollabAgentEvent>> {
    if payload.get("type").and_then(Value::as_str) != Some("item_completed") {
        return None;
    }
    let item = payload
        .get("item")
        .filter(|item| item.get("type").and_then(Value::as_str) == Some("CollabAgentToolCall"))?;
    let invocation_id = non_empty_json_string(item.get("id"));
    let tool_name = non_empty_json_string(item.get("tool"));
    let sender_thread_id = non_empty_json_string(item.get("sender_thread_id"));
    let (invocation_id, tool_name) = match (invocation_id, tool_name) {
        (Some(invocation_id), Some(tool_name)) => (invocation_id, tool_name),
        (invocation_id, tool_name) => {
            return Some(vec![malformed_collab_invocation(
                invocation_id.unwrap_or_default(),
                tool_name.unwrap_or_default(),
                sender_thread_id,
                "malformed",
            )]);
        }
    };

    let raw_receivers = item
        .get("receiver_thread_ids")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let receivers = raw_receivers
        .iter()
        .filter_map(|value| non_empty_json_string(Some(value)))
        .collect::<Vec<_>>();
    let unique_receivers = receivers.iter().collect::<HashSet<_>>();
    let identity_reason = if receivers.is_empty() || receivers.len() != raw_receivers.len() {
        Some("identity_missing")
    } else if unique_receivers.len() != receivers.len()
        || (tool_name == "spawn_agent" && receivers.len() != 1)
    {
        Some("identity_ambiguous")
    } else {
        None
    };
    if let Some(reason) = identity_reason {
        return Some(vec![malformed_collab_invocation(
            invocation_id,
            tool_name,
            sender_thread_id,
            reason,
        )]);
    }

    let started_at_unix_nano = millis_to_unix_nanos(payload.get("started_at_ms"));
    let completed_at_unix_nano = millis_to_unix_nanos(payload.get("completed_at_ms"));
    let timing_valid = started_at_unix_nano
        .as_deref()
        .zip(completed_at_unix_nano.as_deref())
        .and_then(|(started, completed)| {
            Some((
                started.parse::<u128>().ok()?,
                completed.parse::<u128>().ok()?,
            ))
        })
        .is_some_and(|(started, completed)| completed >= started);
    let receiver_agents = item
        .get("receiver_agents")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let agents_states = item.get("agents_states").and_then(Value::as_object);
    let prompt = non_empty_json_string(item.get("prompt"));
    let model = non_empty_json_string(item.get("model"));

    let mut events = Vec::with_capacity(receivers.len());
    for child_thread_id in receivers {
        let matching_agents = receiver_agents
            .iter()
            .filter(|agent| {
                non_empty_json_string(agent.get("thread_id")).as_deref()
                    == Some(child_thread_id.as_str())
            })
            .collect::<Vec<_>>();
        if matching_agents.len() > 1 {
            return Some(vec![malformed_collab_invocation(
                invocation_id,
                tool_name,
                sender_thread_id,
                "identity_ambiguous",
            )]);
        }
        let receiver = matching_agents.first().copied();
        let (mut state, status, terminal_output) =
            collab_agent_state(agents_states.and_then(|states| states.get(&child_thread_id)));
        let reason = if !timing_valid {
            state = CollabInvocationState::Malformed;
            "malformed_timing"
        } else if state == CollabInvocationState::Pending {
            "terminal_pending"
        } else {
            "parsed"
        };
        events.push(CollabAgentEvent::ToolInvocation(
            CollabAgentInvocationEvidence {
                turn_index: usize::MAX,
                source: CollabInvocationSource::NativeToolCall,
                invocation_id: invocation_id.clone(),
                tool_name: tool_name.clone(),
                sender_thread_id: sender_thread_id.clone(),
                child_thread_id: Some(child_thread_id),
                nickname: receiver
                    .and_then(|agent| non_empty_json_string(agent.get("agent_nickname"))),
                role: receiver.and_then(|agent| non_empty_json_string(agent.get("agent_role"))),
                prompt: prompt.clone(),
                model: model.clone(),
                started_at_unix_nano: timing_valid.then(|| started_at_unix_nano.clone()).flatten(),
                completed_at_unix_nano: timing_valid
                    .then(|| completed_at_unix_nano.clone())
                    .flatten(),
                state,
                status,
                terminal_output,
                reason: reason.to_string(),
            },
        ));
    }
    Some(events)
}

/// Parse subagent origin info from a `session_meta` payload.
///
/// Tries in order:
/// 1. `payload.source.subagent.thread_spawn`  (codex format, key = `"subagent"`)
/// 2. `payload.source.sub_agent.thread_spawn` (legacy compat, key = `"sub_agent"`)
/// 3. `payload.thread_source == "subagent"` with top-level `agent_nickname`/`agent_role`
pub(super) fn parse_subagent_source(payload: &Value) -> Option<SubagentSourceInfo> {
    // Try structured source object first
    if let Some(source) = payload.get("source").and_then(|v| v.as_object()) {
        let thread_spawn = source
            .get("subagent")
            .or_else(|| source.get("sub_agent"))
            .and_then(|sa| sa.get("thread_spawn"));
        if let Some(ts) = thread_spawn {
            return Some(SubagentSourceInfo {
                parent_thread_id: ts
                    .get("parent_thread_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                depth: ts.get("depth").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
                agent_path: ts
                    .get("agent_path")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
                agent_nickname: ts
                    .get("agent_nickname")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
                agent_role: ts
                    .get("agent_role")
                    .and_then(|v| v.as_str())
                    .or_else(|| ts.get("agent_type").and_then(|v| v.as_str()))
                    .map(|s| s.to_string()),
                protocol_version: payload
                    .get("multi_agent_version")
                    .and_then(|v| v.as_str())
                    .map(str::to_string),
            });
        }
    }
    // Fallback: thread_source field at payload level
    if payload
        .get("thread_source")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        == "subagent"
    {
        return Some(SubagentSourceInfo {
            parent_thread_id: String::new(),
            depth: 1,
            agent_path: None,
            agent_nickname: payload
                .get("agent_nickname")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            agent_role: payload
                .get("agent_role")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            protocol_version: payload
                .get("multi_agent_version")
                .and_then(|v| v.as_str())
                .map(str::to_string),
        });
    }
    None
}
