//! correlation ownership module.

use super::{iso_to_nanos, Value};
use super::{
    non_empty_json_string, timestamp_nanos, CollabAgentEvent, CollabAgentInvocationEvidence,
    CollabInvocationSource, CollabInvocationState, SubagentActivationEvidence, ToolCall, Turn,
    TurnTerminalState,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SubagentIdentitySource {
    V2Activity,
    LegacyAgentId,
    LegacyNewThreadId,
    V2PathFallback,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SubagentProtocol {
    Legacy,
    V2,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SubagentLifecycle {
    IdentityCandidate,
    CompletedSync,
    FailedSync,
    InterruptedSync,
    AmbiguousOrOrphan,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SubagentIdentity {
    pub session_id: String,
    pub agent_path: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CorrelationMatch {
    pub lifecycle: SubagentLifecycle,
    pub protocol: Option<SubagentProtocol>,
    pub identity_source: Option<SubagentIdentitySource>,
    pub spawn_call_id: Option<String>,
    pub activation_call_id: Option<String>,
    pub wait_call_ids: Vec<String>,
    pub terminal_event: Option<CollabAgentEvent>,
    pub interruption_evidence: Vec<SubagentInterruptionEvidence>,
    pub reason: String,
}

/// Invocation-scoped correlation result for one activation of a child thread.
///
/// A child session can be activated more than once: the initial activation is
/// the exact `spawn_agent` call and later activations are exact
/// `followup_task` calls.  The invocation key deliberately excludes nickname
/// so reused display names cannot collapse distinct executions.
#[derive(Debug, Clone, PartialEq)]
pub struct SubagentInvocationMatch {
    pub invocation_key: String,
    pub activation_turn_index: usize,
    pub activation_tool_name: String,
    pub correlation: CorrelationMatch,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SubagentInterruptionEvidence {
    pub activity_event: CollabAgentEvent,
    pub interrupt_call_id: String,
    pub interrupt_timestamp: String,
    pub child_terminal_timestamp: Option<String>,
}

pub(super) fn correlation_result(
    lifecycle: SubagentLifecycle,
    protocol: Option<SubagentProtocol>,
    identity_source: Option<SubagentIdentitySource>,
    spawn_call_id: Option<String>,
    wait_call_ids: Vec<String>,
    terminal_event: Option<CollabAgentEvent>,
    reason: &str,
) -> CorrelationMatch {
    CorrelationMatch {
        lifecycle,
        protocol,
        identity_source,
        activation_call_id: spawn_call_id.clone(),
        spawn_call_id,
        wait_call_ids,
        terminal_event,
        interruption_evidence: Vec::new(),
        reason: reason.to_string(),
    }
}

pub(super) fn interruption_candidate_result(
    protocol: Option<SubagentProtocol>,
    identity_source: Option<SubagentIdentitySource>,
    spawn_call_id: Option<String>,
    activation_call_id: Option<String>,
    wait_call_ids: Vec<String>,
    evidence: Vec<SubagentInterruptionEvidence>,
    reason: &str,
) -> CorrelationMatch {
    CorrelationMatch {
        lifecycle: SubagentLifecycle::IdentityCandidate,
        protocol,
        identity_source,
        spawn_call_id,
        activation_call_id,
        wait_call_ids,
        terminal_event: None,
        interruption_evidence: evidence,
        reason: reason.to_string(),
    }
}

pub(super) fn tool_output_field<'a>(tool: &'a ToolCall, field: &str) -> Option<&'a str> {
    tool.output.get(field).and_then(Value::as_str)
}

pub(super) fn spawn_has_strong_identity_for_other_child(
    turn: &Turn,
    spawn: &ToolCall,
    child_session_id: &str,
) -> bool {
    ["agent_id", "new_thread_id"].iter().any(|field| {
        tool_output_field(spawn, field).is_some_and(|session_id| session_id != child_session_id)
    }) || turn.collab_agent_events.iter().any(|event| match event {
        CollabAgentEvent::SubagentActivity {
            event_id,
            agent_thread_id,
            kind,
            ..
        } => event_id == &spawn.call_id && kind == "started" && agent_thread_id != child_session_id,
        CollabAgentEvent::SpawnEnd {
            call_id,
            new_thread_id: Some(new_thread_id),
            ..
        } => call_id == &spawn.call_id && new_thread_id != child_session_id,
        _ => false,
    })
}

pub(super) fn parent_agent_path(child_path: &str) -> Option<&str> {
    let (parent, _) = child_path.rsplit_once('/')?;
    Some(if parent.is_empty() { "/" } else { parent })
}

pub(super) fn wait_targets_child(wait: &ToolCall, child: &SubagentIdentity) -> bool {
    wait.input
        .get("targets")
        .and_then(Value::as_array)
        .is_some_and(|targets| {
            targets.iter().any(|target| {
                target.as_str().is_some_and(|target| {
                    target == child.session_id || child.agent_path.as_deref() == Some(target)
                })
            })
        })
}

pub(super) fn tool_target_matches_child(
    tool: &ToolCall,
    child: &SubagentIdentity,
    canonical_path: &str,
) -> bool {
    let canonical_task_name = canonical_path
        .rsplit('/')
        .find(|segment| !segment.is_empty());
    let child_task_name = child
        .agent_path
        .as_deref()
        .and_then(|path| path.rsplit('/').find(|segment| !segment.is_empty()));
    let matches_target = |target: &str| {
        target == child.session_id
            || target == canonical_path
            || child.agent_path.as_deref() == Some(target)
            || canonical_task_name == Some(target)
            || child_task_name == Some(target)
    };
    [
        "target",
        "target_agent",
        "agent",
        "agent_id",
        "agent_path",
        "path",
        "task_name",
        "session_id",
    ]
    .iter()
    .any(|key| {
        tool.input
            .get(*key)
            .and_then(Value::as_str)
            .is_some_and(matches_target)
    }) || tool
        .input
        .get("targets")
        .and_then(Value::as_array)
        .is_some_and(|targets| targets.iter().filter_map(Value::as_str).any(matches_target))
}

pub(super) fn timestamp_in_window(timestamp: &str, start: &str, end: &str) -> bool {
    let timestamp = iso_to_nanos(timestamp).parse::<u128>().unwrap_or(0);
    let start = iso_to_nanos(start).parse::<u128>().unwrap_or(0);
    let end = iso_to_nanos(end).parse::<u128>().unwrap_or(0);
    timestamp > 0 && start > 0 && end > 0 && timestamp >= start && timestamp <= end
}

pub(super) fn timestamp_in_half_open_window(
    timestamp: &str,
    start: &str,
    end: Option<&str>,
) -> bool {
    let timestamp = iso_to_nanos(timestamp).parse::<u128>().unwrap_or(0);
    let start = iso_to_nanos(start).parse::<u128>().unwrap_or(0);
    let end = end
        .map(|value| iso_to_nanos(value).parse::<u128>().unwrap_or(0))
        .unwrap_or(0);
    timestamp > 0 && start > 0 && timestamp >= start && (end == 0 || timestamp < end)
}

pub(super) fn child_terminal_for_window(
    child_turns: &[Turn],
    start: &str,
    end: Option<&str>,
) -> Option<(TurnTerminalState, String)> {
    child_turns
        .iter()
        .filter(|turn| timestamp_in_half_open_window(&turn.user_timestamp, start, end))
        .filter(|turn| {
            matches!(
                turn.terminal_state,
                TurnTerminalState::Completed
                    | TurnTerminalState::Failed
                    | TurnTerminalState::Aborted
            )
        })
        .map(|turn| (turn.terminal_state, turn.completion_timestamp.clone()))
        .max_by_key(|(_, timestamp)| timestamp_nanos(timestamp))
}

pub(crate) fn subagent_invocation_key(child_session_id: &str, activation_call_id: &str) -> String {
    format!("{child_session_id}:{activation_call_id}")
}

#[derive(Clone, Copy)]
pub(super) struct SubagentActivation<'a> {
    turn_index: usize,
    tool: &'a ToolCall,
}

pub(super) fn activity_path_matches(event_path: Option<&str>, canonical_path: &str) -> bool {
    event_path.is_none_or(|path| path == canonical_path)
}

/// Correlate one discovered child against a parsed parent turn.
///
/// Identity is capability-based and ordered from strongest to weakest:
/// v2 activity UUID, legacy `agent_id`, legacy `new_thread_id`, then a unique
/// exact canonical-path fallback. Completion remains protocol-specific:
/// legacy requires a targeted wait. V2 completion requires a typed
/// `FINAL_ANSWER` from the canonical child path; a targeted wait is retained
/// when present, while exact activity identity also supports Codex 0.148
/// foreground children that complete without a separate wait call.
pub fn correlate_subagent(
    turn: &Turn,
    child: &SubagentIdentity,
    siblings: &[SubagentIdentity],
) -> CorrelationMatch {
    if child.session_id.is_empty() {
        return correlation_result(
            SubagentLifecycle::AmbiguousOrOrphan,
            None,
            None,
            None,
            vec![],
            None,
            "missing_child_session_id",
        );
    }

    let spawn_tools: Vec<&ToolCall> = turn
        .tool_calls
        .iter()
        .filter(|tool| tool.name == "spawn_agent")
        .collect();

    let activities: Vec<&CollabAgentEvent> = turn
        .collab_agent_events
        .iter()
        .filter(|event| matches!(event, CollabAgentEvent::SubagentActivity { .. }))
        .collect();

    let mut selected: Option<(SubagentIdentitySource, &ToolCall, Option<String>)> = None;
    let mut activity_matches = Vec::new();
    let mut child_activity_without_spawn = false;
    for event in &activities {
        let CollabAgentEvent::SubagentActivity {
            event_id,
            agent_thread_id,
            agent_path,
            kind,
            ..
        } = event
        else {
            continue;
        };
        if kind != "started" || agent_thread_id != &child.session_id {
            continue;
        }
        let Some(spawn) = spawn_tools
            .iter()
            .copied()
            .find(|spawn| spawn.call_id == *event_id)
        else {
            child_activity_without_spawn = true;
            continue;
        };
        if spawn_has_strong_identity_for_other_child(turn, spawn, &child.session_id) {
            return correlation_result(
                SubagentLifecycle::AmbiguousOrOrphan,
                Some(SubagentProtocol::V2),
                None,
                Some(spawn.call_id.clone()),
                vec![],
                None,
                "activity_strong_identity_conflict",
            );
        }
        if let (Some(expected), Some(observed)) =
            (child.agent_path.as_deref(), agent_path.as_deref())
        {
            if expected != observed {
                return correlation_result(
                    SubagentLifecycle::AmbiguousOrOrphan,
                    None,
                    None,
                    Some(spawn.call_id.clone()),
                    vec![],
                    None,
                    "activity_path_conflict",
                );
            }
        }
        if let Some(spawn_path) = tool_output_field(spawn, "task_name") {
            let canonical_path = child.agent_path.as_deref().or(agent_path.as_deref());
            if canonical_path.is_some_and(|canonical_path| canonical_path != spawn_path) {
                return correlation_result(
                    SubagentLifecycle::AmbiguousOrOrphan,
                    Some(SubagentProtocol::V2),
                    None,
                    Some(spawn.call_id.clone()),
                    vec![],
                    None,
                    "activity_spawn_path_conflict",
                );
            }
        }
        activity_matches.push((spawn, agent_path.clone()));
    }
    activity_matches.sort_by(|left, right| left.0.call_id.cmp(&right.0.call_id));
    activity_matches.dedup_by(|left, right| left.0.call_id == right.0.call_id);
    match activity_matches.as_slice() {
        [(spawn, path)] => {
            selected = Some((SubagentIdentitySource::V2Activity, *spawn, path.clone()));
        }
        [_, _, ..] => {
            return correlation_result(
                SubagentLifecycle::AmbiguousOrOrphan,
                None,
                None,
                None,
                vec![],
                None,
                "multiple_activity_spawn_matches",
            );
        }
        [] => {}
    }
    if selected.is_none() && child_activity_without_spawn {
        return correlation_result(
            SubagentLifecycle::AmbiguousOrOrphan,
            Some(SubagentProtocol::V2),
            None,
            None,
            vec![],
            None,
            "activity_spawn_missing",
        );
    }

    if selected.is_none() {
        let legacy_agent_id: Vec<&ToolCall> = spawn_tools
            .iter()
            .copied()
            .filter(|spawn| tool_output_field(spawn, "agent_id") == Some(&child.session_id))
            .collect();
        match legacy_agent_id.as_slice() {
            [spawn] => {
                if spawn_has_strong_identity_for_other_child(turn, spawn, &child.session_id) {
                    return correlation_result(
                        SubagentLifecycle::AmbiguousOrOrphan,
                        None,
                        None,
                        Some(spawn.call_id.clone()),
                        vec![],
                        None,
                        "legacy_strong_identity_conflict",
                    );
                }
                selected = Some((SubagentIdentitySource::LegacyAgentId, *spawn, None));
            }
            [_, _, ..] => {
                return correlation_result(
                    SubagentLifecycle::AmbiguousOrOrphan,
                    None,
                    None,
                    None,
                    vec![],
                    None,
                    "multiple_legacy_agent_id_matches",
                );
            }
            [] => {}
        }
    }

    if selected.is_none() {
        let mut legacy_new_thread: Vec<&ToolCall> = spawn_tools
            .iter()
            .copied()
            .filter(|spawn| tool_output_field(spawn, "new_thread_id") == Some(&child.session_id))
            .collect();
        for event in &turn.collab_agent_events {
            let CollabAgentEvent::SpawnEnd {
                call_id,
                new_thread_id: Some(new_thread_id),
                ..
            } = event
            else {
                continue;
            };
            if new_thread_id != &child.session_id {
                continue;
            }
            if let Some(spawn) = spawn_tools
                .iter()
                .copied()
                .find(|spawn| spawn.call_id == *call_id)
            {
                legacy_new_thread.push(spawn);
            }
        }
        legacy_new_thread.sort_by(|left, right| left.call_id.cmp(&right.call_id));
        legacy_new_thread.dedup_by(|left, right| left.call_id == right.call_id);
        match legacy_new_thread.as_slice() {
            [spawn] => {
                if spawn_has_strong_identity_for_other_child(turn, spawn, &child.session_id) {
                    return correlation_result(
                        SubagentLifecycle::AmbiguousOrOrphan,
                        None,
                        None,
                        Some(spawn.call_id.clone()),
                        vec![],
                        None,
                        "legacy_strong_identity_conflict",
                    );
                }
                selected = Some((SubagentIdentitySource::LegacyNewThreadId, *spawn, None));
            }
            [_, _, ..] => {
                return correlation_result(
                    SubagentLifecycle::AmbiguousOrOrphan,
                    None,
                    None,
                    None,
                    vec![],
                    None,
                    "multiple_legacy_new_thread_matches",
                );
            }
            [] => {}
        }
    }

    if selected.is_none() {
        let Some(path) = child.agent_path.as_deref() else {
            return correlation_result(
                SubagentLifecycle::AmbiguousOrOrphan,
                None,
                None,
                None,
                vec![],
                None,
                "no_identity_match",
            );
        };
        let sibling_path_count = siblings
            .iter()
            .filter(|candidate| candidate.agent_path.as_deref() == Some(path))
            .count();
        if sibling_path_count != 1 {
            return correlation_result(
                SubagentLifecycle::AmbiguousOrOrphan,
                None,
                None,
                None,
                vec![],
                None,
                "ambiguous_path_fallback",
            );
        }
        let path_matches: Vec<&ToolCall> = spawn_tools
            .iter()
            .copied()
            .filter(|spawn| tool_output_field(spawn, "task_name") == Some(path))
            .collect();
        match path_matches.as_slice() {
            [spawn] => {
                if spawn_has_strong_identity_for_other_child(turn, spawn, &child.session_id) {
                    return correlation_result(
                        SubagentLifecycle::AmbiguousOrOrphan,
                        Some(SubagentProtocol::V2),
                        None,
                        Some(spawn.call_id.clone()),
                        vec![],
                        None,
                        "path_conflicts_with_strong_identity",
                    );
                }
                selected = Some((
                    SubagentIdentitySource::V2PathFallback,
                    *spawn,
                    Some(path.to_string()),
                ));
            }
            [_, _, ..] => {
                return correlation_result(
                    SubagentLifecycle::AmbiguousOrOrphan,
                    Some(SubagentProtocol::V2),
                    None,
                    None,
                    vec![],
                    None,
                    "multiple_path_spawn_matches",
                );
            }
            [] => {
                return correlation_result(
                    SubagentLifecycle::AmbiguousOrOrphan,
                    None,
                    None,
                    None,
                    vec![],
                    None,
                    "no_identity_match",
                );
            }
        }
    }

    let (identity_source, spawn, activity_path) =
        selected.expect("identity selection is exhaustive");
    if matches!(
        identity_source,
        SubagentIdentitySource::LegacyAgentId | SubagentIdentitySource::LegacyNewThreadId
    ) {
        let waits: Vec<String> = turn
            .tool_calls
            .iter()
            .filter(|tool| matches!(tool.name.as_str(), "wait" | "wait_agent"))
            .filter(|wait| wait_targets_child(wait, child))
            .map(|wait| wait.call_id.clone())
            .collect();
        let lifecycle = if waits.is_empty() {
            SubagentLifecycle::IdentityCandidate
        } else {
            SubagentLifecycle::CompletedSync
        };
        let reason = if waits.is_empty() {
            "legacy_wait_missing"
        } else {
            "legacy_completed_sync"
        };
        return correlation_result(
            lifecycle,
            Some(SubagentProtocol::Legacy),
            Some(identity_source),
            Some(spawn.call_id.clone()),
            waits,
            None,
            reason,
        );
    }

    let canonical_path = child.agent_path.as_ref().or(activity_path.as_ref());
    let Some(canonical_path) = canonical_path else {
        return correlation_result(
            SubagentLifecycle::IdentityCandidate,
            Some(SubagentProtocol::V2),
            Some(identity_source),
            Some(spawn.call_id.clone()),
            vec![],
            None,
            "v2_terminal_path_missing",
        );
    };
    let Some(parent_path) = parent_agent_path(canonical_path) else {
        return correlation_result(
            SubagentLifecycle::IdentityCandidate,
            Some(SubagentProtocol::V2),
            Some(identity_source),
            Some(spawn.call_id.clone()),
            vec![],
            None,
            "v2_parent_path_missing",
        );
    };
    let interruption_evidence: Vec<SubagentInterruptionEvidence> = turn
        .collab_agent_events
        .iter()
        .filter_map(|event| {
            let CollabAgentEvent::SubagentActivity {
                event_id,
                agent_thread_id,
                agent_path,
                kind,
                timestamp,
            } = event
            else {
                return None;
            };
            if kind != "interrupted" || agent_thread_id != &child.session_id {
                return None;
            }
            if let Some(agent_path) = agent_path.as_deref() {
                if agent_path != canonical_path {
                    return None;
                }
            }
            if !timestamp_in_half_open_window(timestamp, &spawn.started_timestamp, None) {
                return None;
            }
            let interrupt_call = turn.tool_calls.iter().find(|tool| {
                tool.call_id == *event_id
                    && tool.name == "interrupt_agent"
                    && tool_target_matches_child(tool, child, canonical_path)
            })?;
            Some(SubagentInterruptionEvidence {
                activity_event: event.clone(),
                interrupt_call_id: interrupt_call.call_id.clone(),
                interrupt_timestamp: timestamp.clone(),
                child_terminal_timestamp: None,
            })
        })
        .collect();

    let terminal_event = turn.collab_agent_events.iter().rev().find(|event| {
        matches!(
            event,
            CollabAgentEvent::AgentMessage {
                author,
                recipient,
                message_type: Some(message_type),
                ..
            } if author == canonical_path
                && recipient == parent_path
                && message_type == "FINAL_ANSWER"
        )
    });

    if !interruption_evidence.is_empty() {
        let first_interrupt_timestamp = interruption_evidence
            .iter()
            .map(|evidence| evidence.interrupt_timestamp.as_str())
            .min_by_key(|timestamp| timestamp_nanos(timestamp))
            .unwrap_or("");
        let waits: Vec<String> = turn
            .tool_calls
            .iter()
            .filter(|tool| matches!(tool.name.as_str(), "wait" | "wait_agent"))
            .filter(|wait| {
                timestamp_in_window(
                    &wait.started_timestamp,
                    &spawn.started_timestamp,
                    first_interrupt_timestamp,
                )
            })
            .map(|wait| wait.call_id.clone())
            .collect();
        if waits.is_empty() {
            return correlation_result(
                SubagentLifecycle::IdentityCandidate,
                Some(SubagentProtocol::V2),
                Some(identity_source),
                Some(spawn.call_id.clone()),
                vec![],
                None,
                "v2_interrupt_wait_intent_missing",
            );
        }
        return interruption_candidate_result(
            Some(SubagentProtocol::V2),
            Some(identity_source),
            Some(spawn.call_id.clone()),
            Some(spawn.call_id.clone()),
            waits,
            interruption_evidence,
            "v2_interruption_candidate",
        );
    }

    let Some(terminal_event) = terminal_event else {
        return correlation_result(
            SubagentLifecycle::IdentityCandidate,
            Some(SubagentProtocol::V2),
            Some(identity_source),
            Some(spawn.call_id.clone()),
            vec![],
            None,
            "v2_terminal_message_missing",
        );
    };
    let terminal_timestamp = match terminal_event {
        CollabAgentEvent::AgentMessage { timestamp, .. } => timestamp,
        _ => unreachable!(),
    };
    let waits: Vec<String> = turn
        .tool_calls
        .iter()
        .filter(|tool| matches!(tool.name.as_str(), "wait" | "wait_agent"))
        .filter(|wait| {
            timestamp_in_window(
                &wait.started_timestamp,
                &spawn.started_timestamp,
                terminal_timestamp,
            )
        })
        .map(|wait| wait.call_id.clone())
        .collect();
    if waits.is_empty() && identity_source != SubagentIdentitySource::V2Activity {
        return correlation_result(
            SubagentLifecycle::IdentityCandidate,
            Some(SubagentProtocol::V2),
            Some(identity_source),
            Some(spawn.call_id.clone()),
            vec![],
            Some(terminal_event.clone()),
            "v2_wait_intent_missing",
        );
    }

    let completion_reason = if waits.is_empty() {
        "v2_completed_sync_without_wait"
    } else {
        "v2_completed_sync"
    };
    correlation_result(
        SubagentLifecycle::CompletedSync,
        Some(SubagentProtocol::V2),
        Some(identity_source),
        Some(spawn.call_id.clone()),
        waits,
        Some(terminal_event.clone()),
        completion_reason,
    )
}

/// Return all child identities that can be discovered from a parent session.
///
/// Current Codex transcripts record the durable child UUID on
/// `sub_agent_activity`, while legacy transcripts may only expose it through
/// spawn outputs. Collecting identities at session scope lets replay and live
/// export correlate a child that is spawned in one parent turn and completed
/// after one or more `followup_task` turns.
pub fn collect_subagent_identities(turns: &[Turn]) -> Vec<SubagentIdentity> {
    let mut identities = Vec::<SubagentIdentity>::new();
    let mut add_identity = |session_id: &str, agent_path: Option<&str>| {
        if session_id.is_empty() {
            return;
        }
        if let Some(existing) = identities
            .iter_mut()
            .find(|identity| identity.session_id == session_id)
        {
            if existing.agent_path.is_none() {
                existing.agent_path = agent_path.map(str::to_string);
            }
            return;
        }
        identities.push(SubagentIdentity {
            session_id: session_id.to_string(),
            agent_path: agent_path.map(str::to_string),
        });
    };

    for turn in turns {
        for event in &turn.collab_agent_events {
            match event {
                CollabAgentEvent::ToolInvocation(invocation)
                    if invocation.tool_name == "spawn_agent"
                        && invocation.state != CollabInvocationState::Malformed =>
                {
                    if let Some(child_thread_id) = invocation.child_thread_id.as_deref() {
                        add_identity(child_thread_id, None);
                    }
                }
                CollabAgentEvent::SubagentActivity {
                    agent_thread_id,
                    agent_path,
                    kind,
                    ..
                } if matches!(kind.as_str(), "started" | "interacted") => {
                    add_identity(agent_thread_id, agent_path.as_deref());
                }
                CollabAgentEvent::SpawnEnd {
                    new_thread_id: Some(new_thread_id),
                    ..
                } => add_identity(new_thread_id, None),
                _ => {}
            }
        }
        for spawn in turn
            .tool_calls
            .iter()
            .filter(|tool| tool.name == "spawn_agent")
        {
            for key in ["agent_id", "new_thread_id"] {
                if let Some(session_id) = tool_output_field(spawn, key) {
                    add_identity(session_id, None);
                }
            }
        }
    }

    identities
}

/// Return receiver-scoped collaboration evidence from both native
/// `CollabAgentToolCall` items and legacy spawn/activity records. The adapter
/// only consumes structured fields; opaque outer tool input is never parsed
/// for nested collaboration syntax.
pub fn collect_subagent_invocation_evidence(turns: &[Turn]) -> Vec<CollabAgentInvocationEvidence> {
    let mut evidence = turns
        .iter()
        .enumerate()
        .flat_map(|(turn_index, turn)| {
            turn.collab_agent_events
                .iter()
                .filter_map(move |event| match event {
                    CollabAgentEvent::ToolInvocation(invocation) => {
                        let mut invocation = invocation.clone();
                        invocation.turn_index = turn_index;
                        Some(invocation)
                    }
                    _ => None,
                })
        })
        .collect::<Vec<_>>();
    let identities = collect_subagent_identities(turns);
    let siblings = identities.clone();

    for (turn_index, turn) in turns.iter().enumerate() {
        for spawn in turn
            .tool_calls
            .iter()
            .filter(|tool| tool.name == "spawn_agent")
        {
            let mut child_ids = ["agent_id", "new_thread_id"]
                .iter()
                .filter_map(|key| tool_output_field(spawn, key).map(str::to_string))
                .collect::<Vec<_>>();
            for event in &turn.collab_agent_events {
                match event {
                    CollabAgentEvent::SubagentActivity {
                        event_id,
                        agent_thread_id,
                        kind,
                        ..
                    } if event_id == &spawn.call_id && kind == "started" => {
                        child_ids.push(agent_thread_id.clone());
                    }
                    CollabAgentEvent::SpawnEnd {
                        call_id,
                        new_thread_id: Some(child_thread_id),
                        ..
                    } if call_id == &spawn.call_id => child_ids.push(child_thread_id.clone()),
                    _ => {}
                }
            }
            child_ids.retain(|child_id| !child_id.trim().is_empty());
            child_ids.sort();
            child_ids.dedup();
            if child_ids.len() != 1 {
                evidence.push(CollabAgentInvocationEvidence {
                    turn_index,
                    source: CollabInvocationSource::LegacyToolCall,
                    invocation_id: spawn.call_id.clone(),
                    tool_name: spawn.name.clone(),
                    sender_thread_id: None,
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
                    reason: if child_ids.is_empty() {
                        "identity_missing".to_string()
                    } else {
                        "identity_ambiguous".to_string()
                    },
                });
                continue;
            }
            let child_thread_id = child_ids.pop().expect("one legacy child identity");
            let identity = identities
                .iter()
                .find(|identity| identity.session_id == child_thread_id);
            let correlation = identity.map(|identity| {
                correlate_subagent_across_turns(turns, turn_index, identity, &siblings)
            });
            let (mut state, status, reason) =
                match correlation.as_ref().map(|value| value.lifecycle) {
                    Some(SubagentLifecycle::CompletedSync) => (
                        CollabInvocationState::Completed,
                        "completed".to_string(),
                        "legacy_adapted".to_string(),
                    ),
                    Some(SubagentLifecycle::FailedSync) => (
                        CollabInvocationState::Error,
                        "error".to_string(),
                        "legacy_adapted".to_string(),
                    ),
                    Some(SubagentLifecycle::InterruptedSync) => (
                        CollabInvocationState::Interrupted,
                        "interrupted".to_string(),
                        "legacy_adapted".to_string(),
                    ),
                    Some(SubagentLifecycle::AmbiguousOrOrphan) => (
                        CollabInvocationState::Malformed,
                        "malformed".to_string(),
                        correlation
                            .as_ref()
                            .map(|value| value.reason.clone())
                            .unwrap_or_else(|| "malformed".to_string()),
                    ),
                    _ if spawn.success == Some(false) => (
                        CollabInvocationState::Error,
                        "error".to_string(),
                        "legacy_tool_failed".to_string(),
                    ),
                    _ => (
                        CollabInvocationState::Pending,
                        "pending".to_string(),
                        "terminal_pending".to_string(),
                    ),
                };
            let started_nanos = timestamp_nanos(&spawn.started_timestamp);
            let completed_nanos = timestamp_nanos(&spawn.ended_timestamp);
            let timing_valid = started_nanos > 0 && completed_nanos >= started_nanos;
            if !timing_valid {
                state = CollabInvocationState::Malformed;
            }
            let spawn_end = turn
                .collab_agent_events
                .iter()
                .find_map(|event| match event {
                    CollabAgentEvent::SpawnEnd {
                        call_id,
                        nickname,
                        role,
                        prompt,
                        model,
                        ..
                    } if call_id == &spawn.call_id => Some((
                        nickname.clone(),
                        role.clone(),
                        prompt.clone(),
                        model.clone(),
                    )),
                    _ => None,
                });
            let terminal_output =
                correlation
                    .and_then(|value| value.terminal_event)
                    .and_then(|event| match event {
                        CollabAgentEvent::AgentMessage { content, .. } => Some(content),
                        _ => None,
                    });
            evidence.push(CollabAgentInvocationEvidence {
                turn_index,
                source: CollabInvocationSource::LegacyToolCall,
                invocation_id: spawn.call_id.clone(),
                tool_name: spawn.name.clone(),
                sender_thread_id: None,
                child_thread_id: Some(child_thread_id),
                nickname: spawn_end.as_ref().and_then(|value| value.0.clone()),
                role: spawn_end.as_ref().and_then(|value| value.1.clone()),
                prompt: spawn_end
                    .as_ref()
                    .map(|value| value.2.clone())
                    .filter(|value| !value.is_empty())
                    .or_else(|| {
                        non_empty_json_string(
                            spawn
                                .input
                                .get("message")
                                .or_else(|| spawn.input.get("prompt")),
                        )
                    }),
                model: spawn_end
                    .as_ref()
                    .map(|value| value.3.clone())
                    .filter(|value| !value.is_empty())
                    .or_else(|| non_empty_json_string(spawn.input.get("model"))),
                started_at_unix_nano: timing_valid.then(|| started_nanos.to_string()),
                completed_at_unix_nano: timing_valid.then(|| completed_nanos.to_string()),
                state,
                status,
                terminal_output,
                reason: if timing_valid {
                    reason
                } else {
                    "malformed_timing".to_string()
                },
            });
        }
    }
    evidence
}

pub fn normalized_subagent_activations(turns: &[Turn]) -> Vec<SubagentActivationEvidence> {
    let evidence = collect_subagent_invocation_evidence(turns)
        .into_iter()
        .fold(Vec::new(), |mut unique, candidate| {
            if !unique.contains(&candidate) {
                unique.push(candidate);
            }
            unique
        });
    let mut activations = Vec::new();

    for (index, spawn) in evidence
        .iter()
        .enumerate()
        .filter(|(_, value)| value.tool_name == "spawn_agent")
    {
        let child_thread_id = spawn.child_thread_id.as_deref();
        let start = spawn
            .started_at_unix_nano
            .as_deref()
            .and_then(|value| value.parse::<u128>().ok());
        let next_start = evidence
            .iter()
            .skip(index + 1)
            .filter(|candidate| {
                candidate.tool_name == "spawn_agent"
                    && candidate.child_thread_id.as_deref() == child_thread_id
            })
            .filter_map(|candidate| {
                candidate
                    .started_at_unix_nano
                    .as_deref()
                    .and_then(|value| value.parse::<u128>().ok())
            })
            .min();
        let mut waits = evidence
            .iter()
            .filter(|candidate| {
                matches!(candidate.tool_name.as_str(), "wait" | "wait_agent")
                    && candidate.child_thread_id.as_deref() == child_thread_id
            })
            .filter(|candidate| {
                let candidate_start = candidate
                    .started_at_unix_nano
                    .as_deref()
                    .and_then(|value| value.parse::<u128>().ok());
                match (start, candidate_start) {
                    (Some(start), Some(candidate_start)) => {
                        candidate_start >= start
                            && next_start.is_none_or(|next| candidate_start < next)
                    }
                    _ => false,
                }
            })
            .collect::<Vec<_>>();
        waits.sort_by_key(|wait| {
            wait.completed_at_unix_nano
                .as_deref()
                .and_then(|value| value.parse::<u128>().ok())
                .unwrap_or(0)
        });
        let terminal = waits.iter().rev().copied().find(|wait| {
            matches!(
                wait.state,
                CollabInvocationState::Completed
                    | CollabInvocationState::Interrupted
                    | CollabInvocationState::Error
            )
        });
        let malformed_wait = waits
            .iter()
            .rev()
            .copied()
            .find(|wait| wait.state == CollabInvocationState::Malformed);
        let (state, status, terminal_output, completed_at_unix_nano, reason) =
            if spawn.state == CollabInvocationState::Malformed {
                (
                    spawn.state,
                    spawn.status.clone(),
                    None,
                    None,
                    spawn.reason.clone(),
                )
            } else if let Some(wait) = terminal {
                (
                    wait.state,
                    wait.status.clone(),
                    wait.terminal_output.clone(),
                    wait.completed_at_unix_nano.clone(),
                    "parsed".to_string(),
                )
            } else if let Some(wait) = malformed_wait {
                (
                    CollabInvocationState::Malformed,
                    wait.status.clone(),
                    None,
                    None,
                    wait.reason.clone(),
                )
            } else {
                (
                    CollabInvocationState::Pending,
                    "pending".to_string(),
                    None,
                    None,
                    "terminal_pending".to_string(),
                )
            };
        activations.push(SubagentActivationEvidence {
            turn_index: spawn.turn_index,
            source: spawn.source,
            activation_id: spawn.invocation_id.clone(),
            tool_name: spawn.tool_name.clone(),
            sender_thread_id: spawn.sender_thread_id.clone(),
            child_thread_id: spawn.child_thread_id.clone(),
            nickname: spawn.nickname.clone(),
            role: spawn.role.clone(),
            prompt: spawn.prompt.clone(),
            model: spawn.model.clone(),
            started_at_unix_nano: spawn.started_at_unix_nano.clone(),
            completed_at_unix_nano,
            state,
            status,
            terminal_output,
            wait_invocation_ids: waits
                .iter()
                .map(|wait| wait.invocation_id.clone())
                .collect(),
            reason,
        });
    }

    for orphan in evidence.iter().filter(|value| {
        matches!(value.tool_name.as_str(), "wait" | "wait_agent")
            && value.child_thread_id.as_deref().is_some_and(|child| {
                !evidence.iter().any(|candidate| {
                    candidate.tool_name == "spawn_agent"
                        && candidate.child_thread_id.as_deref() == Some(child)
                })
            })
    }) {
        activations.push(SubagentActivationEvidence {
            turn_index: orphan.turn_index,
            source: orphan.source,
            activation_id: orphan.invocation_id.clone(),
            tool_name: orphan.tool_name.clone(),
            sender_thread_id: orphan.sender_thread_id.clone(),
            child_thread_id: orphan.child_thread_id.clone(),
            nickname: orphan.nickname.clone(),
            role: orphan.role.clone(),
            prompt: orphan.prompt.clone(),
            model: orphan.model.clone(),
            started_at_unix_nano: orphan.started_at_unix_nano.clone(),
            completed_at_unix_nano: None,
            state: CollabInvocationState::Malformed,
            status: "malformed".to_string(),
            terminal_output: None,
            wait_invocation_ids: vec![orphan.invocation_id.clone()],
            reason: "orphan_wait".to_string(),
        });
    }

    activations
}

/// Locate the parent turn that owns a child's initial `spawn_agent`.
///
/// `followup_task` and `send_message` create later `interacted` activity
/// records, but they do not replace the original child identity. The initial
/// spawn remains the stable anchor for the child session.
pub fn subagent_anchor_turn_index(
    turns: &[Turn],
    child: &SubagentIdentity,
    siblings: &[SubagentIdentity],
) -> Option<usize> {
    turns.iter().position(|turn| {
        let matched = correlate_subagent(turn, child, siblings);
        matched.spawn_call_id.is_some()
            && matches!(
                matched.lifecycle,
                SubagentLifecycle::IdentityCandidate
                    | SubagentLifecycle::CompletedSync
                    | SubagentLifecycle::FailedSync
                    | SubagentLifecycle::InterruptedSync
            )
    })
}

/// Correlate a child using its spawning turn as the identity anchor while
/// allowing waits, follow-up activity, and typed terminal messages to arrive
/// in later parent turns.
///
/// The returned spawn still belongs to `anchor_turn_index`; only lifecycle
/// evidence is accumulated from subsequent turns. This preserves one
/// SyncAgent span per child session without treating every unrelated parent
/// turn as a failed identity candidate.
pub fn correlate_subagent_across_turns(
    turns: &[Turn],
    anchor_turn_index: usize,
    child: &SubagentIdentity,
    siblings: &[SubagentIdentity],
) -> CorrelationMatch {
    let Some(anchor_turn) = turns.get(anchor_turn_index) else {
        return correlation_result(
            SubagentLifecycle::AmbiguousOrOrphan,
            None,
            None,
            None,
            vec![],
            None,
            "anchor_turn_missing",
        );
    };

    let anchor_match = correlate_subagent(anchor_turn, child, siblings);
    if anchor_match.spawn_call_id.is_none()
        || anchor_match.lifecycle == SubagentLifecycle::AmbiguousOrOrphan
    {
        return anchor_match;
    }

    let mut lifecycle_turn = anchor_turn.clone();
    for turn in turns.iter().skip(anchor_turn_index + 1) {
        lifecycle_turn.tool_calls.extend(turn.tool_calls.clone());
        lifecycle_turn
            .collab_agent_events
            .extend(turn.collab_agent_events.clone());
    }

    correlate_subagent(&lifecycle_turn, child, siblings)
}

/// Return one exact lifecycle result per child activation.
///
/// The initial activation is the identity-bridged `spawn_agent`; subsequent
/// activations are identity-bridged `followup_task` calls. `send_message`
/// interactions remain inside the active invocation because Codex can deliver
/// them while the same child turn is still running. Parent and child evidence
/// is evaluated in the same half-open activation window, preventing a later
/// completion from rewriting an earlier aborted invocation.
pub fn correlate_subagent_invocations(
    parent_turns: &[Turn],
    child: &SubagentIdentity,
    siblings: &[SubagentIdentity],
    child_turns: &[Turn],
) -> Vec<SubagentInvocationMatch> {
    let Some(anchor_turn_index) = subagent_anchor_turn_index(parent_turns, child, siblings) else {
        return vec![];
    };
    let anchor = correlate_subagent_across_turns(parent_turns, anchor_turn_index, child, siblings);
    let Some(spawn_call_id) = anchor.spawn_call_id.clone() else {
        return vec![];
    };

    if anchor.protocol == Some(SubagentProtocol::Legacy) {
        let activation_call_id = anchor
            .activation_call_id
            .clone()
            .unwrap_or_else(|| spawn_call_id.clone());
        return vec![SubagentInvocationMatch {
            invocation_key: subagent_invocation_key(&child.session_id, &activation_call_id),
            activation_turn_index: anchor_turn_index,
            activation_tool_name: "spawn_agent".to_string(),
            correlation: CorrelationMatch {
                activation_call_id: Some(activation_call_id),
                ..anchor
            },
        }];
    }

    let activity_paths: Vec<&str> = parent_turns
        .iter()
        .flat_map(|turn| turn.collab_agent_events.iter())
        .filter_map(|event| match event {
            CollabAgentEvent::SubagentActivity {
                agent_thread_id,
                agent_path: Some(agent_path),
                ..
            } if agent_thread_id == &child.session_id => Some(agent_path.as_str()),
            _ => None,
        })
        .collect();
    let canonical_path = child
        .agent_path
        .as_deref()
        .or_else(|| activity_paths.first().copied());
    let Some(canonical_path) = canonical_path else {
        return vec![];
    };
    if activity_paths.iter().any(|path| *path != canonical_path) {
        return vec![];
    }
    let Some(parent_path) = parent_agent_path(canonical_path) else {
        return vec![];
    };

    let mut activations = Vec::new();
    for (turn_index, turn) in parent_turns.iter().enumerate() {
        for tool in &turn.tool_calls {
            let exact_spawn = tool.name == "spawn_agent" && tool.call_id == spawn_call_id;
            let exact_followup = tool.name == "followup_task"
                && tool_target_matches_child(tool, child, canonical_path)
                && turn.collab_agent_events.iter().any(|event| {
                    matches!(
                        event,
                        CollabAgentEvent::SubagentActivity {
                            event_id,
                            agent_thread_id,
                            agent_path,
                            kind,
                            ..
                        } if event_id == &tool.call_id
                            && agent_thread_id == &child.session_id
                            && kind == "interacted"
                            && activity_path_matches(agent_path.as_deref(), canonical_path)
                    )
                });
            if exact_spawn || exact_followup {
                activations.push(SubagentActivation { turn_index, tool });
            }
        }
    }
    activations.sort_by_key(|activation| timestamp_nanos(&activation.tool.started_timestamp));
    activations.dedup_by(|left, right| left.tool.call_id == right.tool.call_id);

    activations
        .iter()
        .enumerate()
        .map(|(activation_index, activation)| {
            let start = activation.tool.started_timestamp.as_str();
            let end = activations
                .get(activation_index + 1)
                .map(|next| next.tool.started_timestamp.as_str());
            let wait_call_ids: Vec<String> = parent_turns
                .iter()
                .flat_map(|turn| turn.tool_calls.iter())
                .filter(|tool| matches!(tool.name.as_str(), "wait" | "wait_agent"))
                .filter(|tool| timestamp_in_half_open_window(&tool.started_timestamp, start, end))
                .map(|tool| tool.call_id.clone())
                .collect();
            let terminal_event = parent_turns
                .iter()
                .flat_map(|turn| turn.collab_agent_events.iter())
                .filter(|event| {
                    matches!(
                        event,
                        CollabAgentEvent::AgentMessage {
                            author,
                            recipient,
                            message_type: Some(message_type),
                            timestamp,
                            ..
                        } if author == canonical_path
                            && recipient == parent_path
                            && message_type == "FINAL_ANSWER"
                            && timestamp_in_half_open_window(timestamp, start, end)
                    )
                })
                .max_by_key(|event| match event {
                    CollabAgentEvent::AgentMessage { timestamp, .. } => timestamp_nanos(timestamp),
                    _ => 0,
                })
                .cloned();
            let child_terminal = child_terminal_for_window(child_turns, start, end);
            let interrupted_events: Vec<&CollabAgentEvent> = parent_turns
                .iter()
                .flat_map(|turn| turn.collab_agent_events.iter())
                .filter(|event| {
                    matches!(
                        event,
                        CollabAgentEvent::SubagentActivity {
                            agent_thread_id,
                            kind,
                            timestamp,
                            ..
                        } if agent_thread_id == &child.session_id
                            && kind == "interrupted"
                            && timestamp_in_half_open_window(timestamp, start, end)
                    )
                })
                .collect();

            let activation_call_id = activation.tool.call_id.clone();
            let mut correlation = CorrelationMatch {
                lifecycle: SubagentLifecycle::IdentityCandidate,
                protocol: Some(SubagentProtocol::V2),
                identity_source: anchor.identity_source,
                spawn_call_id: Some(spawn_call_id.clone()),
                activation_call_id: Some(activation_call_id.clone()),
                wait_call_ids: wait_call_ids.clone(),
                terminal_event: None,
                interruption_evidence: vec![],
                reason: "v2_invocation_terminal_missing".to_string(),
            };

            if interrupted_events.iter().any(|event| {
                matches!(
                    event,
                    CollabAgentEvent::SubagentActivity { agent_path, .. }
                        if !activity_path_matches(agent_path.as_deref(), canonical_path)
                )
            }) {
                correlation.lifecycle = SubagentLifecycle::AmbiguousOrOrphan;
                correlation.reason = "v2_interrupt_activity_path_conflict".to_string();
            } else if let Some(interruption_event) = interrupted_events
                .iter()
                .max_by_key(|event| match event {
                    CollabAgentEvent::SubagentActivity { timestamp, .. } => {
                        timestamp_nanos(timestamp)
                    }
                    _ => 0,
                })
                .copied()
            {
                let (event_id, interrupt_timestamp) = match interruption_event {
                    CollabAgentEvent::SubagentActivity {
                        event_id,
                        timestamp,
                        ..
                    } => (event_id, timestamp),
                    _ => unreachable!("interrupted_events only contains activity events"),
                };
                let interrupt_tool = parent_turns
                    .iter()
                    .flat_map(|turn| turn.tool_calls.iter())
                    .find(|tool| tool.call_id == *event_id && tool.name == "interrupt_agent");
                match interrupt_tool {
                    None => {
                        correlation.lifecycle = SubagentLifecycle::AmbiguousOrOrphan;
                        correlation.reason = "v2_interrupt_call_missing".to_string();
                    }
                    Some(tool) if !tool_target_matches_child(tool, child, canonical_path) => {
                        correlation.lifecycle = SubagentLifecycle::AmbiguousOrOrphan;
                        correlation.reason = "v2_interrupt_target_conflict".to_string();
                    }
                    Some(_) if wait_call_ids.is_empty() => {
                        correlation.reason = "v2_interrupt_wait_intent_missing".to_string();
                    }
                    Some(tool) => match child_terminal {
                        Some((TurnTerminalState::Aborted, child_terminal_timestamp)) => {
                            correlation.lifecycle = SubagentLifecycle::InterruptedSync;
                            correlation.interruption_evidence =
                                vec![SubagentInterruptionEvidence {
                                    activity_event: interruption_event.clone(),
                                    interrupt_call_id: tool.call_id.clone(),
                                    interrupt_timestamp: interrupt_timestamp.clone(),
                                    child_terminal_timestamp: Some(child_terminal_timestamp),
                                }];
                            correlation.reason = "v2_interrupted_sync".to_string();
                        }
                        Some((TurnTerminalState::Completed, _))
                            if terminal_event.is_some() || end.is_some() =>
                        {
                            correlation.lifecycle = SubagentLifecycle::CompletedSync;
                            correlation.terminal_event = terminal_event;
                            correlation.reason = if correlation.terminal_event.is_some() {
                                "v2_completed_sync".to_string()
                            } else {
                                "v2_completed_before_reactivation".to_string()
                            };
                        }
                        Some((TurnTerminalState::Failed, _)) => {
                            correlation.lifecycle = SubagentLifecycle::FailedSync;
                            correlation.terminal_event = terminal_event;
                            correlation.reason = "v2_failed_sync".to_string();
                        }
                        _ => {
                            correlation.reason = "v2_interruption_child_abort_missing".to_string();
                        }
                    },
                }
            } else if matches!(child_terminal, Some((TurnTerminalState::Aborted, _))) {
                correlation.lifecycle = SubagentLifecycle::AmbiguousOrOrphan;
                correlation.reason = "v2_child_abort_without_parent_interrupt".to_string();
            } else if matches!(child_terminal, Some((TurnTerminalState::Failed, _)))
                && (terminal_event.is_some() || end.is_some())
            {
                correlation.lifecycle = SubagentLifecycle::FailedSync;
                correlation.terminal_event = terminal_event;
                correlation.reason = if correlation.terminal_event.is_some() {
                    "v2_failed_sync".to_string()
                } else {
                    "v2_failed_before_reactivation".to_string()
                };
            } else if matches!(child_terminal, Some((TurnTerminalState::Completed, _)))
                && (terminal_event.is_some() || end.is_some())
            {
                correlation.lifecycle = SubagentLifecycle::CompletedSync;
                correlation.terminal_event = terminal_event;
                correlation.reason = if correlation.terminal_event.is_some() {
                    if wait_call_ids.is_empty() {
                        "v2_completed_sync_without_wait".to_string()
                    } else {
                        "v2_completed_sync".to_string()
                    }
                } else {
                    "v2_completed_before_reactivation".to_string()
                };
            } else if wait_call_ids.is_empty() {
                correlation.reason = "v2_wait_intent_missing".to_string();
            } else if terminal_event.is_none() {
                correlation.reason = "v2_terminal_message_missing".to_string();
            } else {
                correlation.reason = "v2_child_terminal_missing".to_string();
            }

            SubagentInvocationMatch {
                invocation_key: subagent_invocation_key(&child.session_id, &activation_call_id),
                activation_turn_index: activation.turn_index,
                activation_tool_name: activation.tool.name.clone(),
                correlation,
            }
        })
        .collect()
}

/// Return strongly identified children whose parent-side lifecycle is already
/// complete. This lets the hook distinguish "no children" from "a completed
/// child transcript has not become readable yet" before exporting the parent.
pub fn completed_subagent_identities(turn: &Turn) -> Vec<SubagentIdentity> {
    let identities = collect_subagent_identities(std::slice::from_ref(turn));
    let siblings = identities.clone();
    identities
        .into_iter()
        .filter(|identity| {
            matches!(
                correlate_subagent(turn, identity, &siblings).lifecycle,
                SubagentLifecycle::CompletedSync | SubagentLifecycle::FailedSync
            )
        })
        .collect()
}

/// Whether exact parent-side evidence is sufficient to discover and retry a
/// child transcript. This is deliberately weaker than a verified child
/// lifecycle: interrupted invocations become terminal only after the child
/// transcript proves `TurnTerminalState::Aborted`.
pub fn parent_correlation_needs_child_transcript(correlation: &CorrelationMatch) -> bool {
    correlation.spawn_call_id.is_some()
        && !correlation.wait_call_ids.is_empty()
        && (matches!(
            correlation.lifecycle,
            SubagentLifecycle::CompletedSync
                | SubagentLifecycle::FailedSync
                | SubagentLifecycle::InterruptedSync
        ) || !correlation.interruption_evidence.is_empty())
}
