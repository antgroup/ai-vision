//! Correlation discovery policy implementation.

use super::collab::{
    CollabAgentEvent, CollabInvocationSource, CollabInvocationState, SubagentActivationEvidence,
    SubagentDiscoveryDecision, SubagentDiscoveryDisposition, SubagentDiscoveryPlan,
    SubagentObservation,
};
use super::correlation::{
    collect_subagent_identities, correlate_subagent_across_turns, normalized_subagent_activations,
    parent_correlation_needs_child_transcript, subagent_anchor_turn_index, SubagentIdentity,
};
use super::{HashMap, HashSet, Turn};

pub(super) fn native_discovery_reason(activation: &SubagentActivationEvidence) -> &'static str {
    match activation.state {
        CollabInvocationState::Completed => "native_terminal",
        CollabInvocationState::Interrupted => "native_interrupted",
        CollabInvocationState::Error => "native_error_terminal",
        CollabInvocationState::Pending => "native_terminal_pending",
        CollabInvocationState::Malformed => match activation.reason.as_str() {
            "identity_missing" => "identity_missing",
            "identity_ambiguous" => "identity_ambiguous",
            "malformed_timing" => "malformed_timing",
            "orphan_wait" => "orphan_wait",
            _ => "identity_missing",
        },
    }
}

pub(super) fn activation_has_valid_terminal_timing(
    activation: &SubagentActivationEvidence,
) -> bool {
    activation
        .started_at_unix_nano
        .as_deref()
        .zip(activation.completed_at_unix_nano.as_deref())
        .and_then(|(started, completed)| {
            Some((
                started.parse::<u128>().ok()?,
                completed.parse::<u128>().ok()?,
            ))
        })
        .is_some_and(|(started, completed)| started > 0 && completed >= started)
}

pub(super) fn native_discovery_decision(
    activation: SubagentActivationEvidence,
) -> Result<SubagentDiscoveryDecision, SubagentObservation> {
    let child_session_id = activation
        .child_thread_id
        .as_deref()
        .unwrap_or_default()
        .trim();
    let activation_id = activation.activation_id.trim();
    let child = (!child_session_id.is_empty()).then(|| SubagentIdentity {
        session_id: child_session_id.to_string(),
        agent_path: None,
    });
    if child.is_none() || activation_id.is_empty() {
        let reason = if activation.state == CollabInvocationState::Malformed {
            native_discovery_reason(&activation)
        } else {
            "identity_missing"
        };
        return Ok(SubagentDiscoveryDecision {
            child,
            activation_id: (!activation_id.is_empty()).then(|| activation_id.to_string()),
            source: CollabInvocationSource::NativeToolCall,
            disposition: SubagentDiscoveryDisposition::Deferred,
            reason: reason.to_string(),
            activation: Some(activation),
        });
    }

    let mut reason = native_discovery_reason(&activation).to_string();
    let mut disposition = match activation.state {
        CollabInvocationState::Completed
        | CollabInvocationState::Interrupted
        | CollabInvocationState::Error => SubagentDiscoveryDisposition::Eligible,
        CollabInvocationState::Pending | CollabInvocationState::Malformed => {
            SubagentDiscoveryDisposition::Deferred
        }
    };
    if matches!(
        activation.state,
        CollabInvocationState::Completed
            | CollabInvocationState::Interrupted
            | CollabInvocationState::Error
    ) && !activation_has_valid_terminal_timing(&activation)
    {
        disposition = SubagentDiscoveryDisposition::Deferred;
        reason = "malformed_timing".to_string();
    }

    Ok(SubagentDiscoveryDecision {
        child,
        activation_id: Some(activation_id.to_string()),
        source: CollabInvocationSource::NativeToolCall,
        disposition,
        reason,
        activation: Some(activation),
    })
}

pub(super) fn legacy_current_activation(
    turn: &Turn,
    child_session_id: &str,
) -> Option<(String, &'static str)> {
    turn.collab_agent_events.iter().find_map(|event| {
        let CollabAgentEvent::SubagentActivity {
            event_id,
            agent_thread_id,
            kind,
            ..
        } = event
        else {
            return None;
        };
        if agent_thread_id != child_session_id || !matches!(kind.as_str(), "started" | "interacted")
        {
            return None;
        }
        turn.tool_calls
            .iter()
            .find(|tool| {
                tool.call_id == *event_id
                    && matches!(tool.name.as_str(), "spawn_agent" | "followup_task")
            })
            .map(|tool| {
                (
                    tool.call_id.clone(),
                    if kind == "interacted" {
                        "legacy_followup_activation"
                    } else {
                        "legacy_current_activation"
                    },
                )
            })
    })
}

/// Build one pure, deterministic discovery plan for a parent turn.
///
/// Native `CollabAgentToolCall` evidence is classified from normalized exact
/// activation identity. Legacy/v2 evidence retains the existing correlator.
/// The result intentionally performs no file I/O, retry, state, or checkpoint
/// work so live and replay adapters can consume the same semantic policy.
pub fn subagent_discovery_plan(turns: &[Turn], anchor_turn_index: usize) -> SubagentDiscoveryPlan {
    let mut plan = SubagentDiscoveryPlan::default();
    let native_activations = normalized_subagent_activations(turns)
        .into_iter()
        .filter(|activation| {
            activation.source == CollabInvocationSource::NativeToolCall
                && activation.turn_index == anchor_turn_index
        })
        .collect::<Vec<_>>();
    let mut activation_ids_by_child = HashMap::<String, HashSet<String>>::new();
    for activation in &native_activations {
        let Some(child_session_id) = activation
            .child_thread_id
            .as_deref()
            .filter(|child| !child.trim().is_empty())
        else {
            continue;
        };
        if activation.activation_id.trim().is_empty() {
            continue;
        }
        activation_ids_by_child
            .entry(child_session_id.to_string())
            .or_default()
            .insert(activation.activation_id.clone());
    }
    let mut conflicting_child_ids = activation_ids_by_child
        .into_iter()
        .filter_map(|(child_session_id, activation_ids)| {
            (activation_ids.len() > 1).then_some(child_session_id)
        })
        .collect::<Vec<_>>();
    conflicting_child_ids.sort();
    let conflicting_child_id_set = conflicting_child_ids
        .iter()
        .cloned()
        .collect::<HashSet<_>>();
    for child_session_id in &conflicting_child_ids {
        let activation = native_activations
            .iter()
            .find(|activation| {
                activation.child_thread_id.as_deref() == Some(child_session_id.as_str())
            })
            .expect("conflicting native child has activation evidence")
            .clone();
        plan.decisions.push(SubagentDiscoveryDecision {
            child: Some(SubagentIdentity {
                session_id: child_session_id.clone(),
                agent_path: None,
            }),
            activation_id: Some(activation.activation_id.clone()),
            source: CollabInvocationSource::NativeToolCall,
            disposition: SubagentDiscoveryDisposition::Deferred,
            reason: "identity_ambiguous".to_string(),
            activation: Some(activation),
        });
    }
    let mut grouped_native = Vec::<(String, Vec<SubagentActivationEvidence>)>::new();
    for activation in native_activations {
        let group_key = if activation.activation_id.trim().is_empty() {
            format!("<missing:{}>", grouped_native.len())
        } else {
            activation.activation_id.clone()
        };
        if let Some((_, values)) = grouped_native
            .iter_mut()
            .find(|(existing, _)| existing == &group_key)
        {
            values.push(activation);
        } else {
            grouped_native.push((group_key, vec![activation]));
        }
    }

    let mut native_child_ids = HashSet::new();
    for (_, group) in grouped_native {
        let first = group.first().expect("native activation group is non-empty");
        let child_ids = group
            .iter()
            .filter_map(|activation| activation.child_thread_id.as_deref())
            .filter(|child| !child.trim().is_empty())
            .collect::<HashSet<_>>();
        native_child_ids.extend(child_ids.iter().map(|child| (*child).to_string()));
        let has_reverse_conflict = child_ids
            .iter()
            .any(|child| conflicting_child_id_set.contains(*child));
        if has_reverse_conflict && child_ids.len() == 1 {
            continue;
        }
        let conflicting =
            child_ids.len() > 1 || group.iter().skip(1).any(|activation| activation != first);
        if conflicting {
            plan.decisions.push(SubagentDiscoveryDecision {
                child: (child_ids.len() == 1).then(|| SubagentIdentity {
                    session_id: (*child_ids.iter().next().expect("one child")).to_string(),
                    agent_path: None,
                }),
                activation_id: (!first.activation_id.trim().is_empty())
                    .then(|| first.activation_id.clone()),
                source: CollabInvocationSource::NativeToolCall,
                disposition: SubagentDiscoveryDisposition::Deferred,
                reason: "identity_ambiguous".to_string(),
                activation: Some(first.clone()),
            });
            continue;
        }
        match native_discovery_decision(first.clone()) {
            Ok(decision) => plan.decisions.push(decision),
            Err(observation) => plan.observations.push(observation),
        }
    }

    let identities = collect_subagent_identities(turns);
    let siblings = identities.clone();
    let Some(anchor_turn) = turns.get(anchor_turn_index) else {
        return plan;
    };
    for identity in identities
        .into_iter()
        .filter(|identity| !native_child_ids.contains(&identity.session_id))
    {
        let anchor = subagent_anchor_turn_index(turns, &identity, &siblings);
        let current_activation = legacy_current_activation(anchor_turn, &identity.session_id);
        if current_activation
            .as_ref()
            .is_some_and(|(_, reason)| *reason == "legacy_followup_activation")
            && anchor.is_none()
        {
            plan.decisions.push(SubagentDiscoveryDecision {
                child: Some(identity),
                activation_id: current_activation.map(|(id, _)| id),
                source: CollabInvocationSource::LegacyToolCall,
                disposition: SubagentDiscoveryDisposition::Deferred,
                reason: "parent_spawn_anchor_not_ready".to_string(),
                activation: None,
            });
            continue;
        }

        let correlation =
            correlate_subagent_across_turns(turns, anchor_turn_index, &identity, &siblings);
        let initial_candidate = anchor == Some(anchor_turn_index)
            && parent_correlation_needs_child_transcript(&correlation);
        if initial_candidate || current_activation.is_some() {
            let activation_id = current_activation
                .as_ref()
                .map(|(id, _)| id.clone())
                .or_else(|| correlation.activation_call_id.clone())
                .or_else(|| correlation.spawn_call_id.clone());
            let reason = current_activation
                .map(|(_, reason)| reason.to_string())
                .unwrap_or_else(|| format!("legacy_{}", correlation.reason));
            plan.decisions.push(SubagentDiscoveryDecision {
                child: Some(identity),
                activation_id,
                source: CollabInvocationSource::LegacyToolCall,
                disposition: SubagentDiscoveryDisposition::Eligible,
                reason,
                activation: None,
            });
        }
    }

    plan
}
