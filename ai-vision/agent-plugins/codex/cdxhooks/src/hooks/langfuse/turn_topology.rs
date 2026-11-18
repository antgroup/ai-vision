use std::collections::{HashMap, HashSet};
use std::path::Path;

use serde_json::Value;

use crate::common::transcript::Turn;
use crate::hooks::{RetryPolicy, StopBudget};

use super::realtime_state;
use super::state::{
    claim_finalizer_recovery_attempt, durably_accounted_turn_ids_for_history,
    record_finite_finalizer_deferred, record_pending_export, record_terminal_quarantined_turn,
    DEFERRED_EVIDENCE_EXPIRED_REASON, REALTIME_TOPOLOGY_AMBIGUOUS_REASON,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum FinalizerTurnTopology {
    Exact {
        trace_id: String,
        root_span_id: String,
    },
    LegacyFallback,
    Ambiguous {
        reason: String,
        terminal: bool,
        root_identities: Vec<String>,
    },
}

impl FinalizerTurnTopology {
    // The binary worker consumes this method; the library target does not.
    #[allow(dead_code)]
    pub(crate) fn into_exact_ids(self) -> Option<(String, String)> {
        match self {
            Self::Exact {
                trace_id,
                root_span_id,
            } => Some((trace_id, root_span_id)),
            Self::LegacyFallback | Self::Ambiguous { .. } => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum FinalizerTurnTopologyOutcome {
    Resolved(FinalizerTurnTopology),
    BudgetExhausted,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum FinalizerDecision {
    Exact {
        trace_id: String,
        root_span_id: String,
    },
    LegacyFallback,
    TerminalQuarantined {
        reason: String,
        appended: bool,
    },
    Deferred {
        reason: String,
    },
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn persist_finalizer_topology_decision(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    turn_id: &str,
    topology: FinalizerTurnTopology,
    now: &str,
    recovery_key: Option<&str>,
    finalizer_high_water: u64,
    terminal_high_water_offset: Option<i64>,
    transcript_path: &str,
) -> Result<FinalizerDecision, String> {
    match topology {
        FinalizerTurnTopology::Exact {
            trace_id,
            root_span_id,
        } => Ok(FinalizerDecision::Exact {
            trace_id,
            root_span_id,
        }),
        FinalizerTurnTopology::LegacyFallback => Ok(FinalizerDecision::LegacyFallback),
        FinalizerTurnTopology::Ambiguous {
            reason,
            terminal,
            root_identities,
        } if terminal => {
            let attempt = if let Some(recovery_key) = recovery_key {
                claim_finalizer_recovery_attempt(
                    state_file,
                    state,
                    session_id,
                    now,
                    transcript_path,
                    recovery_key,
                )?
                .attempt
            } else {
                1
            };
            let transition = record_terminal_quarantined_turn(
                state_file,
                state,
                session_id,
                turn_id,
                &reason,
                &root_identities,
                now,
                attempt,
                recovery_key,
                terminal_high_water_offset,
            )?;
            Ok(FinalizerDecision::TerminalQuarantined {
                reason,
                appended: transition.appended,
            })
        }
        FinalizerTurnTopology::Ambiguous { reason, .. } => {
            if let Some(recovery_key) = recovery_key {
                let attempt = claim_finalizer_recovery_attempt(
                    state_file,
                    state,
                    session_id,
                    now,
                    transcript_path,
                    recovery_key,
                )?
                .attempt;
                let observed_offset = terminal_high_water_offset.unwrap_or_default();
                let deferred = record_finite_finalizer_deferred(
                    state_file,
                    state,
                    session_id,
                    &reason,
                    now,
                    transcript_path,
                    recovery_key,
                    attempt,
                    finalizer_high_water,
                    observed_offset,
                )?;
                if deferred.repeated_boundary {
                    let transition = record_terminal_quarantined_turn(
                        state_file,
                        state,
                        session_id,
                        turn_id,
                        DEFERRED_EVIDENCE_EXPIRED_REASON,
                        &[],
                        now,
                        deferred.attempt,
                        Some(recovery_key),
                        terminal_high_water_offset,
                    )?;
                    return Ok(FinalizerDecision::TerminalQuarantined {
                        reason: DEFERRED_EVIDENCE_EXPIRED_REASON.to_string(),
                        appended: transition.appended,
                    });
                }
            } else {
                record_pending_export(
                    state_file,
                    state,
                    session_id,
                    REALTIME_TOPOLOGY_AMBIGUOUS_REASON,
                    &[],
                    now,
                    transcript_path,
                )?;
            }
            Ok(FinalizerDecision::Deferred { reason })
        }
    }
}

#[cfg(test)]
pub(crate) fn finalizer_trace_root_for_turn_with_scan_base(
    state: &Value,
    session_id: &str,
    turns: &[Turn],
    turn_index: usize,
    scan_base_ordinal: u64,
) -> Result<Option<(String, String)>, String> {
    let topology = resolve_finalizer_turn_topology_with_scan_base(
        state,
        session_id,
        turns,
        turn_index,
        scan_base_ordinal,
    );
    if let FinalizerTurnTopology::Ambiguous { reason, .. } = &topology {
        return Err(reason.clone());
    }
    Ok(topology.into_exact_ids())
}

// The binary worker consumes this compatibility wrapper; the library target
// uses the explicit accounting-history entry point.
#[allow(dead_code)]
pub(crate) fn resolve_finalizer_turn_topology_with_scan_base(
    state: &Value,
    session_id: &str,
    turns: &[Turn],
    turn_index: usize,
    scan_base_ordinal: u64,
) -> FinalizerTurnTopology {
    resolve_finalizer_turn_topology_with_accounting_history(
        state,
        session_id,
        turns,
        turn_index,
        scan_base_ordinal,
        turns,
    )
}

pub(crate) fn resolve_finalizer_turn_topology_with_accounting_history(
    state: &Value,
    session_id: &str,
    turns: &[Turn],
    turn_index: usize,
    scan_base_ordinal: u64,
    accounting_history: &[Turn],
) -> FinalizerTurnTopology {
    match resolve_finalizer_turn_topology_with_accounting_history_inner(
        state,
        session_id,
        turns,
        turn_index,
        scan_base_ordinal,
        accounting_history,
        None,
    ) {
        FinalizerTurnTopologyOutcome::Resolved(topology) => topology,
        FinalizerTurnTopologyOutcome::BudgetExhausted => {
            unreachable!("unbudgeted topology resolution cannot exhaust a budget")
        }
    }
}

pub(crate) fn resolve_finalizer_turn_topology_with_accounting_history_budgeted(
    state: &Value,
    session_id: &str,
    turns: &[Turn],
    turn_index: usize,
    scan_base_ordinal: u64,
    accounting_history: &[Turn],
    budget: StopBudget,
) -> FinalizerTurnTopologyOutcome {
    resolve_finalizer_turn_topology_with_accounting_history_inner(
        state,
        session_id,
        turns,
        turn_index,
        scan_base_ordinal,
        accounting_history,
        Some(budget),
    )
}

#[allow(clippy::too_many_arguments)]
fn resolve_finalizer_turn_topology_with_accounting_history_inner(
    state: &Value,
    session_id: &str,
    turns: &[Turn],
    turn_index: usize,
    scan_base_ordinal: u64,
    accounting_history: &[Turn],
    budget: Option<StopBudget>,
) -> FinalizerTurnTopologyOutcome {
    let resolved = |topology| FinalizerTurnTopologyOutcome::Resolved(topology);
    if topology_budget_exhausted(budget) {
        return FinalizerTurnTopologyOutcome::BudgetExhausted;
    }
    let Some(turn) = turns.get(turn_index) else {
        return resolved(FinalizerTurnTopology::Ambiguous {
            reason: "turn_index_out_of_range".to_string(),
            terminal: true,
            root_identities: Vec::new(),
        });
    };
    let session = state.get(session_id);
    let durably_accounted_turn_ids = session
        .map(|session| {
            durably_accounted_turn_ids_for_history(
                session,
                accounting_history.iter().map(|turn| turn.turn_id.as_str()),
            )
        })
        .unwrap_or_default();
    if topology_budget_exhausted(budget) {
        return FinalizerTurnTopologyOutcome::BudgetExhausted;
    }

    let mut accounted_other_tool_ids = HashSet::new();
    for candidate in accounting_history {
        if topology_budget_exhausted(budget) {
            return FinalizerTurnTopologyOutcome::BudgetExhausted;
        }
        if candidate.turn_id != turn.turn_id
            && durably_accounted_turn_ids.contains(&candidate.turn_id)
        {
            accounted_other_tool_ids.extend(
                candidate
                    .tool_calls
                    .iter()
                    .map(|tool_call| tool_call.call_id.as_str()),
            );
        }
    }

    let all_contexts = realtime_state::contexts(state, session_id);
    if topology_budget_exhausted(budget) {
        return FinalizerTurnTopologyOutcome::BudgetExhausted;
    }
    let mut accounted_context_ids = HashSet::new();
    for context in &all_contexts {
        if topology_budget_exhausted(budget) {
            return FinalizerTurnTopologyOutcome::BudgetExhausted;
        }
        let belongs_to_accounted_turn = context.turn_id.as_ref().is_some_and(|turn_id| {
            turn_id != &turn.turn_id && durably_accounted_turn_ids.contains(turn_id)
        });
        let has_accounted_tool = context
            .tool_use_ids
            .iter()
            .any(|tool_use_id| accounted_other_tool_ids.contains(tool_use_id.as_str()));
        if belongs_to_accounted_turn || has_accounted_tool {
            accounted_context_ids.insert(context.context_id.clone());
        }
    }
    let Some(accounted_generation_context_ids) =
        accounted_generation_context_ids_for_other_turns_budgeted(
            state,
            session_id,
            turns,
            turn_index,
            scan_base_ordinal,
            &durably_accounted_turn_ids,
            budget,
        )
    else {
        return FinalizerTurnTopologyOutcome::BudgetExhausted;
    };
    let mut contexts = Vec::new();
    for context in all_contexts {
        if topology_budget_exhausted(budget) {
            return FinalizerTurnTopologyOutcome::BudgetExhausted;
        }
        if !accounted_context_ids.contains(&context.context_id)
            && !accounted_generation_context_ids.contains(&context.context_id)
        {
            contexts.push(context);
        }
    }
    let mut generation_rounds = Vec::new();
    for round in realtime_generation_rounds(state, session_id) {
        if topology_budget_exhausted(budget) {
            return FinalizerTurnTopologyOutcome::BudgetExhausted;
        }
        if !accounted_context_ids.contains(&round.context_id)
            && !accounted_generation_context_ids.contains(&round.context_id)
        {
            generation_rounds.push(round);
        }
    }

    let mut matches = HashSet::new();
    let turn_tool_ids = turn
        .tool_calls
        .iter()
        .map(|tool_call| tool_call.call_id.as_str())
        .collect::<HashSet<_>>();
    for context in &contexts {
        if topology_budget_exhausted(budget) {
            return FinalizerTurnTopologyOutcome::BudgetExhausted;
        }
        if context.turn_id.as_deref() == Some(turn.turn_id.as_str())
            || context
                .tool_use_ids
                .iter()
                .any(|tool_use_id| turn_tool_ids.contains(tool_use_id.as_str()))
        {
            matches.insert(context.context_id.clone());
        }
    }
    let Some(turn_ordinals) =
        generation_ordinals_for_turn_budgeted(turns, turn_index, scan_base_ordinal, budget)
    else {
        return FinalizerTurnTopologyOutcome::BudgetExhausted;
    };
    for round in &generation_rounds {
        if topology_budget_exhausted(budget) {
            return FinalizerTurnTopologyOutcome::BudgetExhausted;
        }
        if round.status == realtime_state::GenerationRoundStatus::Accounted
            && turn_ordinals.contains(&round.ordinal)
        {
            matches.insert(round.context_id.clone());
        }
    }

    if matches.len() == 1 {
        let context_id = matches.into_iter().next().unwrap_or_default();
        if let Some(context) = contexts
            .iter()
            .find(|context| context.context_id == context_id)
        {
            return resolved(FinalizerTurnTopology::Exact {
                trace_id: context.trace_id.clone(),
                root_span_id: context.root_span_id.clone(),
            });
        }
        return resolved(FinalizerTurnTopology::Ambiguous {
            reason: "realtime_context_disappeared".to_string(),
            terminal: true,
            root_identities: vec![context_id],
        });
    }

    if matches.len() > 1 {
        resolved(FinalizerTurnTopology::Ambiguous {
            reason: "multiple_realtime_matches".to_string(),
            terminal: true,
            root_identities: root_identities_for_matches(&contexts, &matches),
        })
    } else if contexts.iter().any(|context| {
        context.turn_id.as_deref() == Some(turn.turn_id.as_str())
            || context
                .tool_use_ids
                .iter()
                .any(|tool_use_id| turn_tool_ids.contains(tool_use_id.as_str()))
    }) || generation_rounds.iter().any(|round| {
        turn_ordinals.contains(&round.ordinal)
            && !matches!(
                round.status,
                realtime_state::GenerationRoundStatus::Accounted
                    | realtime_state::GenerationRoundStatus::PolicyTerminal
            )
    }) {
        resolved(FinalizerTurnTopology::Ambiguous {
            reason: "realtime_evidence_without_unique_match".to_string(),
            terminal: false,
            root_identities: Vec::new(),
        })
    } else {
        resolved(FinalizerTurnTopology::LegacyFallback)
    }
}

fn topology_budget_exhausted(budget: Option<StopBudget>) -> bool {
    budget.is_some_and(|budget| !budget.can_start_turn())
}

pub(crate) fn accounted_realtime_child_span_ids(
    state: &Value,
    session_id: &str,
    high_water: u64,
) -> HashSet<String> {
    let mut span_ids = HashSet::new();
    for context in realtime_state::contexts(state, session_id) {
        for (tool_use_id, snapshot) in context.tool_jobs {
            let (Some(job_id), Some(payload_digest), Some(span_id)) = (
                snapshot.job_id.as_deref(),
                snapshot.payload_digest.as_deref(),
                context.tool_span_ids.get(&tool_use_id),
            ) else {
                continue;
            };
            if realtime_registration_omitted_by_finalizer(
                state,
                session_id,
                job_id,
                payload_digest,
                high_water,
            ) {
                span_ids.insert(span_id.clone());
            }
        }
    }

    for round in realtime_generation_rounds(state, session_id) {
        let (Some(job_id), Some(payload_digest)) =
            (round.job_id.as_deref(), round.payload_digest.as_deref())
        else {
            continue;
        };
        if realtime_registration_omitted_by_finalizer(
            state,
            session_id,
            job_id,
            payload_digest,
            high_water,
        ) {
            span_ids.insert(round.span_id);
        }
    }
    span_ids
}

pub(crate) fn finalizer_generation_span_ids_for_turn_with_scan_base(
    state: &Value,
    session_id: &str,
    turns: &[Turn],
    turn_index: usize,
    high_water: u64,
    scan_base_ordinal: u64,
) -> HashMap<usize, String> {
    let eligible_rounds = realtime_generation_rounds(state, session_id)
        .into_iter()
        .filter(|round| {
            if round.status != realtime_state::GenerationRoundStatus::Accounted {
                return false;
            }
            let (Some(job_id), Some(payload_digest)) =
                (round.job_id.as_deref(), round.payload_digest.as_deref())
            else {
                return false;
            };
            realtime_state::registered_realtime_job(state, session_id, job_id).is_some_and(|job| {
                job.payload_digest == payload_digest && job.sequence <= high_water
            })
        })
        .map(|round| (round.ordinal, round.span_id))
        .collect::<HashMap<_, _>>();
    if eligible_rounds.is_empty() {
        return HashMap::new();
    }

    let mut assistant_ordinal = scan_base_ordinal;
    let mut result = HashMap::new();
    for (idx, turn) in turns.iter().enumerate() {
        for (assistant_index, _) in turn.assistants.iter().enumerate() {
            if !turn.has_semantic_assistant_at(assistant_index) {
                continue;
            }
            if idx == turn_index {
                if let Some(span_id) = eligible_rounds.get(&assistant_ordinal) {
                    result.insert(assistant_index, span_id.clone());
                }
            }
            assistant_ordinal = assistant_ordinal.saturating_add(1);
        }
    }
    result
}

fn root_identities_for_matches(
    contexts: &[realtime_state::RealtimeContext],
    matches: &HashSet<String>,
) -> Vec<String> {
    let mut root_identities = contexts
        .iter()
        .filter(|context| matches.contains(&context.context_id))
        .map(|context| {
            format!(
                "{}:{}:{}",
                context.context_id, context.trace_id, context.root_span_id
            )
        })
        .collect::<Vec<_>>();
    root_identities.sort();
    root_identities
}

fn accounted_generation_context_ids_for_other_turns_budgeted(
    state: &Value,
    session_id: &str,
    turns: &[Turn],
    turn_index: usize,
    scan_base_ordinal: u64,
    durably_accounted_turn_ids: &HashSet<String>,
    budget: Option<StopBudget>,
) -> Option<HashSet<String>> {
    let mut assistant_ordinal = scan_base_ordinal;
    let mut other_accounted_ordinals = HashSet::new();
    for (idx, turn) in turns.iter().enumerate() {
        if topology_budget_exhausted(budget) {
            return None;
        }
        let accounted_other_turn =
            idx != turn_index && durably_accounted_turn_ids.contains(&turn.turn_id);
        for (assistant_index, _) in turn.assistants.iter().enumerate() {
            if !turn.has_semantic_assistant_at(assistant_index) {
                continue;
            }
            if accounted_other_turn {
                other_accounted_ordinals.insert(assistant_ordinal);
            }
            assistant_ordinal = assistant_ordinal.saturating_add(1);
        }
    }
    let mut context_ids = HashSet::new();
    for round in realtime_generation_rounds(state, session_id) {
        if topology_budget_exhausted(budget) {
            return None;
        }
        if round.status == realtime_state::GenerationRoundStatus::Accounted
            && other_accounted_ordinals.contains(&round.ordinal)
        {
            context_ids.insert(round.context_id);
        }
    }
    Some(context_ids)
}

fn realtime_registration_omitted_by_finalizer(
    state: &Value,
    session_id: &str,
    job_id: &str,
    payload_digest: &str,
    high_water: u64,
) -> bool {
    realtime_state::registered_realtime_job(state, session_id, job_id).is_some_and(|job| {
        job.payload_digest == payload_digest
            && (job.sequence > high_water
                || realtime_registered_status_is_suppressible(state, session_id, &job))
    })
}

fn realtime_registered_status_is_suppressible(
    state: &Value,
    session_id: &str,
    job: &realtime_state::RealtimeRegisteredJob,
) -> bool {
    match job.status {
        realtime_state::RealtimeRegisteredJobStatus::Accounted
        | realtime_state::RealtimeRegisteredJobStatus::Compensated => true,
        realtime_state::RealtimeRegisteredJobStatus::PolicyTerminal => {
            realtime_state::retry_policy_for_context(state, session_id, &job.context_id)
                == Some(RetryPolicy::AtMostOnce)
        }
        realtime_state::RealtimeRegisteredJobStatus::Failed
        | realtime_state::RealtimeRegisteredJobStatus::PermanentFailed => false,
        realtime_state::RealtimeRegisteredJobStatus::Queued
        | realtime_state::RealtimeRegisteredJobStatus::Inflight => false,
    }
}

fn realtime_generation_rounds(
    state: &Value,
    session_id: &str,
) -> Vec<realtime_state::GenerationRound> {
    state
        .get(session_id)
        .and_then(|session| session.get("realtime_export"))
        .and_then(|realtime| realtime.get("generation"))
        .and_then(|generation| generation.get("rounds"))
        .and_then(Value::as_array)
        .map(|rounds| {
            rounds
                .iter()
                .filter_map(|round| serde_json::from_value(round.clone()).ok())
                .collect()
        })
        .unwrap_or_default()
}

fn generation_ordinals_for_turn_budgeted(
    turns: &[Turn],
    turn_index: usize,
    scan_base_ordinal: u64,
    budget: Option<StopBudget>,
) -> Option<HashSet<u64>> {
    let mut assistant_ordinal = scan_base_ordinal;
    let mut ordinals = HashSet::new();
    for (idx, turn) in turns.iter().enumerate() {
        if topology_budget_exhausted(budget) {
            return None;
        }
        for (assistant_index, _) in turn.assistants.iter().enumerate() {
            if !turn.has_semantic_assistant_at(assistant_index) {
                continue;
            }
            if idx == turn_index {
                ordinals.insert(assistant_ordinal);
            }
            assistant_ordinal = assistant_ordinal.saturating_add(1);
        }
    }
    Some(ordinals)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::transcript::{AssistantMessage, ToolCall, TurnTerminalState};
    use serde_json::json;
    use std::time::{Duration, Instant};

    fn turn_with_tool(turn_id: &str, call_id: &str) -> Turn {
        Turn {
            chat_id: Some(turn_id.to_string()),
            turn_id: turn_id.to_string(),
            user_timestamp: "2026-08-15T07:58:20.000Z".to_string(),
            user_text: "hello".to_string(),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: "2026-08-15T07:58:35.000Z".to_string(),
                text: "done".to_string(),
                media_content: serde_json::Value::Null,
                phase: "final_answer".to_string(),
                usage: Default::default(),
                token_count_timestamp: String::new(),
                invocation_started_timestamp: String::new(),
                invocation_end_source: None,
            }],
            tool_calls: vec![ToolCall {
                call_id: call_id.to_string(),
                name: "noop".to_string(),
                input: json!({}),
                output: json!(null),
                started_timestamp: "2026-08-15T07:58:30.000Z".to_string(),
                ended_timestamp: "2026-08-15T07:58:31.000Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            }],
            model: "gpt-5.5".to_string(),
            cwd: "/workspace".to_string(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: "2026-08-15T07:58:35.100Z".to_string(),
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
    fn budgeted_topology_yields_before_starting_when_budget_is_exhausted() {
        let turn = turn_with_tool("turn-1", "call-1");
        let budget = StopBudget::new(
            Instant::now() - Duration::from_secs(1),
            Duration::from_millis(1),
            Duration::from_millis(1),
            Duration::from_millis(1),
        );

        let outcome = resolve_finalizer_turn_topology_with_accounting_history_budgeted(
            &json!({}),
            "session",
            std::slice::from_ref(&turn),
            0,
            0,
            std::slice::from_ref(&turn),
            budget,
        );

        assert_eq!(outcome, FinalizerTurnTopologyOutcome::BudgetExhausted);
    }

    #[test]
    fn resolve_finalizer_turn_topology_reuses_unique_realtime_root() {
        let turns = vec![turn_with_tool("turn-1", "tool-1")];
        let state = json!({
            "sess": {
                "realtime_export": {
                    "current": {
                        "schema_version": 1,
                        "context_id": "context-1",
                        "trace_id": "11111111111111111111111111111111",
                        "root_span_id": "3333333333333333",
                        "turn_id": "turn-1",
                        "tool_use_ids": ["tool-1"],
                        "tool_span_ids": {"tool-1": "4444444444444444"},
                        "tool_jobs": {},
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "created_at": "2026-08-16T00:00:00Z"
                    }
                }
            }
        });

        assert_eq!(
            resolve_finalizer_turn_topology_with_scan_base(&state, "sess", &turns, 0, 0),
            FinalizerTurnTopology::Exact {
                trace_id: "11111111111111111111111111111111".to_string(),
                root_span_id: "3333333333333333".to_string(),
            }
        );
        assert_eq!(
            finalizer_trace_root_for_turn_with_scan_base(&state, "sess", &turns, 0, 0).unwrap(),
            Some((
                "11111111111111111111111111111111".to_string(),
                "3333333333333333".to_string(),
            ))
        );
    }

    #[test]
    fn resolve_finalizer_turn_topology_is_fail_closed_when_evidence_is_ambiguous() {
        let turns = vec![turn_with_tool("turn-1", "tool-1")];
        let state = json!({
            "sess": {
                "realtime_export": {
                    "current": {
                        "schema_version": 1,
                        "context_id": "context-1",
                        "trace_id": "11111111111111111111111111111111",
                        "root_span_id": "3333333333333333",
                        "turn_id": "turn-1",
                        "tool_use_ids": ["tool-1"],
                        "tool_span_ids": {"tool-1": "4444444444444444"},
                        "tool_jobs": {},
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "created_at": "2026-08-16T00:00:00Z"
                    },
                    "pending": [{
                        "schema_version": 1,
                        "context_id": "context-2",
                        "trace_id": "22222222222222222222222222222222",
                        "root_span_id": "4444444444444444",
                        "turn_id": "turn-1",
                        "tool_use_ids": [],
                        "tool_span_ids": {},
                        "tool_jobs": {},
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "created_at": "2026-08-16T00:00:00Z"
                    }]
                }
            }
        });

        assert!(matches!(
            resolve_finalizer_turn_topology_with_scan_base(&state, "sess", &turns, 0, 0),
            FinalizerTurnTopology::Ambiguous { .. }
        ));
        assert_eq!(
            finalizer_trace_root_for_turn_with_scan_base(&state, "sess", &turns, 0, 0),
            Err("multiple_realtime_matches".to_string())
        );
    }

    #[test]
    fn resolve_finalizer_turn_topology_unions_all_current_turn_evidence() {
        let turns = vec![turn_with_tool("turn-1", "tool-1")];
        let state = json!({
            "sess": {
                "realtime_export": {
                    "current": {
                        "schema_version": 1,
                        "context_id": "context-by-turn",
                        "trace_id": "11111111111111111111111111111111",
                        "root_span_id": "1111111111111111",
                        "turn_id": "turn-1",
                        "tool_use_ids": [],
                        "tool_span_ids": {},
                        "tool_jobs": {},
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "created_at": "2026-08-16T00:00:00Z"
                    },
                    "pending": [{
                        "schema_version": 1,
                        "context_id": "context-by-tool",
                        "trace_id": "22222222222222222222222222222222",
                        "root_span_id": "2222222222222222",
                        "turn_id": null,
                        "tool_use_ids": ["tool-1"],
                        "tool_span_ids": {"tool-1": "3333333333333333"},
                        "tool_jobs": {},
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "created_at": "2026-08-16T00:00:00Z"
                    }]
                }
            }
        });

        assert_eq!(
            finalizer_trace_root_for_turn_with_scan_base(&state, "sess", &turns, 0, 0),
            Err("multiple_realtime_matches".to_string())
        );
    }

    #[test]
    fn resolve_finalizer_turn_topology_allows_legacy_fallback_without_realtime_evidence() {
        let turns = vec![turn_with_tool("turn-legacy", "tool-legacy")];

        assert_eq!(
            resolve_finalizer_turn_topology_with_scan_base(&json!({}), "sess", &turns, 0, 0),
            FinalizerTurnTopology::LegacyFallback
        );
        assert_eq!(
            finalizer_trace_root_for_turn_with_scan_base(&json!({}), "sess", &turns, 0, 0).unwrap(),
            None
        );
    }

    #[test]
    fn terminally_accounted_context_does_not_block_later_legacy_turn() {
        let turns = vec![
            turn_with_tool("turn-bad", "tool-bad"),
            turn_with_tool("turn-clean", "tool-clean"),
        ];
        let state = json!({
            "sess": {
                "export_accounting": {
                    "terminal_turn_ids": ["turn-bad"]
                },
                "realtime_export": {
                    "current": {
                        "schema_version": 1,
                        "context_id": "context-bad",
                        "trace_id": "11111111111111111111111111111111",
                        "root_span_id": "3333333333333333",
                        "turn_id": "turn-bad",
                        "tool_use_ids": ["tool-bad"],
                        "tool_span_ids": {"tool-bad": "4444444444444444"},
                        "tool_jobs": {},
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "created_at": "2026-08-16T00:00:00Z"
                    },
                    "generation": {
                        "rounds": [{
                            "generation_id": "generation-bad",
                            "span_id": "5555555555555555",
                            "context_id": "context-bad",
                            "ordinal": 1,
                            "status": "accounted"
                        }]
                    }
                }
            }
        });

        assert_eq!(
            resolve_finalizer_turn_topology_with_scan_base(&state, "sess", &turns, 1, 0),
            FinalizerTurnTopology::LegacyFallback
        );
    }

    #[test]
    fn durably_accounted_success_context_outside_posted_window_does_not_block_current_turn() {
        let mut turns = Vec::new();
        for index in 0..205 {
            turns.push(turn_with_tool(
                &format!("turn-{index}"),
                &format!("tool-{index}"),
            ));
        }
        let state = json!({
            "sess": {
                "turn_count": 204,
                "posted_turn_ids": ["turn-203"],
                "realtime_export": {
                    "current": {
                        "schema_version": 1,
                        "context_id": "context-aged-out",
                        "trace_id": "11111111111111111111111111111111",
                        "root_span_id": "3333333333333333",
                        "turn_id": "turn-0",
                        "tool_use_ids": ["tool-0"],
                        "tool_span_ids": {"tool-0": "4444444444444444"},
                        "tool_jobs": {},
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "created_at": "2026-08-16T00:00:00Z"
                    }
                }
            }
        });

        assert_eq!(
            resolve_finalizer_turn_topology_with_scan_base(&state, "sess", &turns, 204, 0),
            FinalizerTurnTopology::LegacyFallback
        );
    }

    #[test]
    fn complete_accounting_history_excludes_aged_out_context_from_incremental_window() {
        let mut accounting_history = (0..205)
            .map(|index| turn_with_tool(&format!("turn-{index}"), &format!("tool-{index}")))
            .collect::<Vec<_>>();
        accounting_history[0] = turn_with_tool("turn-0", "shared-tool");
        accounting_history[204] = turn_with_tool("turn-204", "shared-tool");
        let current_window = vec![accounting_history[204].clone()];
        let state = json!({
            "sess": {
                "turn_count": 204,
                "posted_turn_ids": ["turn-203"],
                "realtime_export": {
                    "current": {
                        "schema_version": 1,
                        "context_id": "context-aged-out",
                        "trace_id": "11111111111111111111111111111111",
                        "root_span_id": "3333333333333333",
                        "turn_id": "turn-0",
                        "tool_use_ids": ["shared-tool"],
                        "tool_span_ids": {"shared-tool": "4444444444444444"},
                        "tool_jobs": {},
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "created_at": "2026-08-16T00:00:00Z"
                    }
                }
            }
        });

        assert_eq!(
            resolve_finalizer_turn_topology_with_accounting_history(
                &state,
                "sess",
                &current_window,
                0,
                204,
                &accounting_history,
            ),
            FinalizerTurnTopology::LegacyFallback
        );
    }

    #[test]
    fn terminally_accounted_context_does_not_hide_later_turn_ambiguity() {
        let turns = vec![
            turn_with_tool("turn-bad", "tool-bad"),
            turn_with_tool("turn-clean", "tool-clean"),
        ];
        let context = |context_id: &str, trace_id: &str, root_span_id: &str, turn_id: &str| {
            json!({
                "schema_version": 1,
                "context_id": context_id,
                "trace_id": trace_id,
                "root_span_id": root_span_id,
                "turn_id": turn_id,
                "tool_use_ids": [],
                "tool_span_ids": {},
                "tool_jobs": {},
                "generation_batch_size": 3,
                "retry_policy": "at_least_once",
                "created_at": "2026-08-16T00:00:00Z"
            })
        };
        let state = json!({
            "sess": {
                "export_accounting": {
                    "terminal_turn_ids": ["turn-bad"]
                },
                "realtime_export": {
                    "current": context(
                        "context-clean-a",
                        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "aaaaaaaaaaaaaaaa",
                        "turn-clean"
                    ),
                    "pending": [
                        context(
                            "context-bad",
                            "11111111111111111111111111111111",
                            "1111111111111111",
                            "turn-bad"
                        ),
                        context(
                            "context-clean-b",
                            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                            "bbbbbbbbbbbbbbbb",
                            "turn-clean"
                        )
                    ]
                }
            }
        });

        assert_eq!(
            finalizer_trace_root_for_turn_with_scan_base(&state, "sess", &turns, 1, 0),
            Err("multiple_realtime_matches".to_string())
        );
    }

    #[test]
    fn generation_match_without_live_context_is_terminally_ambiguous() {
        let turns = vec![turn_with_tool("turn-1", "unmatched-tool")];
        let state = json!({
            "sess": {
                "realtime_export": {
                    "generation": {
                        "rounds": [{
                            "generation_id": "generation-0",
                            "span_id": "5555555555555555",
                            "context_id": "missing-context",
                            "ordinal": 0,
                            "status": "accounted"
                        }]
                    }
                }
            }
        });

        assert_eq!(
            resolve_finalizer_turn_topology_with_scan_base(&state, "sess", &turns, 0, 0),
            FinalizerTurnTopology::Ambiguous {
                reason: "realtime_context_disappeared".to_string(),
                terminal: true,
                root_identities: vec!["missing-context".to_string()],
            }
        );
        assert_eq!(
            finalizer_trace_root_for_turn_with_scan_base(&state, "sess", &turns, 0, 0),
            Err("realtime_context_disappeared".to_string())
        );
    }

    #[test]
    fn oversized_generation_terminal_round_does_not_defer_finalizer() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript_path = "/tmp/session.jsonl";
        let turns = vec![turn_with_tool("turn-1", "unmatched-tool")];
        let mut state = json!({
            "sess": {
                "realtime_export": {
                    "delivery_plans": {
                        "plan-1": {
                            "schema_version": 1,
                            "plan_id": "plan-1",
                            "context_id": "context-1",
                            "trigger_id": "flush-1",
                            "chunks": [],
                            "oversized_child_ids": ["generation:g-oversize"],
                            "next_cursor": 0,
                            "complete": true,
                            "terminal_reason": "realtime_child_oversize"
                        }
                    },
                    "generation": {
                        "rounds": [{
                            "generation_id": "g-oversize",
                            "span_id": "5555555555555555",
                            "context_id": "context-1",
                            "ordinal": 0,
                            "status": "policy_terminal",
                            "terminal_reason": "realtime_child_oversize",
                            "job_id": "flush-job",
                            "payload_digest": "flush-digest"
                        }]
                    }
                }
            }
        });

        let topology = resolve_finalizer_turn_topology_with_scan_base(&state, "sess", &turns, 0, 0);
        assert_eq!(topology, FinalizerTurnTopology::LegacyFallback);
        assert_eq!(
            persist_finalizer_topology_decision(
                &state_file,
                &mut state,
                "sess",
                "turn-1",
                topology,
                "2026-08-22T00:00:00Z",
                Some("recovery-key"),
                7,
                Some(123),
                transcript_path,
            )
            .unwrap(),
            FinalizerDecision::LegacyFallback
        );
        assert!(state["sess"].get("finalizer_deferred").is_none());
        assert!(state["sess"].get("pending_export_reason").is_none());
        assert_eq!(
            state["sess"]["realtime_export"]["delivery_plans"]["plan-1"]["terminal_reason"],
            json!("realtime_child_oversize")
        );
    }

    #[test]
    fn transport_terminal_generation_round_does_not_defer_finalizer() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript_path = "/tmp/session.jsonl";
        let turns = vec![turn_with_tool("turn-1", "unmatched-tool")];
        let mut state = json!({
            "sess": {
                "realtime_export": {
                    "generation": {
                        "rounds": [{
                            "generation_id": "g-terminal",
                            "span_id": "5555555555555555",
                            "context_id": "context-1",
                            "ordinal": 0,
                            "status": "policy_terminal",
                            "terminal_reason": "at_most_once_transport_failure",
                            "job_id": "batch-terminal",
                            "payload_digest": "batch-digest"
                        }]
                    }
                }
            }
        });

        let topology = resolve_finalizer_turn_topology_with_scan_base(&state, "sess", &turns, 0, 0);
        assert_eq!(topology, FinalizerTurnTopology::LegacyFallback);
        assert_eq!(
            persist_finalizer_topology_decision(
                &state_file,
                &mut state,
                "sess",
                "turn-1",
                topology,
                "2026-08-22T00:00:00Z",
                Some("recovery-key"),
                7,
                Some(123),
                transcript_path,
            )
            .unwrap(),
            FinalizerDecision::LegacyFallback
        );
        assert!(state["sess"].get("finalizer_deferred").is_none());
        assert!(state["sess"].get("pending_export_reason").is_none());
    }

    #[test]
    fn repeated_identical_deferred_boundary_terminally_quarantines_once() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript_path = "/tmp/session.jsonl";
        let recovery_key =
            super::super::state::finalizer_recovery_key(transcript_path, "turn-1", 123);
        let topology = || FinalizerTurnTopology::Ambiguous {
            reason: "realtime_evidence_without_unique_match".to_string(),
            terminal: false,
            root_identities: Vec::new(),
        };
        let mut state = json!({
            "sess": {
                "pending_export_reason": super::super::state::REALTIME_TOPOLOGY_AMBIGUOUS_REASON,
                "transcript_path": transcript_path,
            }
        });
        super::super::state::arm_legacy_finalizer_recovery(
            &state_file,
            &mut state,
            "sess",
            "2026-08-21T23:59:59Z",
            transcript_path,
        )
        .unwrap()
        .expect("legacy stuck state must be armed once before migration");

        let first = persist_finalizer_topology_decision(
            &state_file,
            &mut state,
            "sess",
            "turn-1",
            topology(),
            "2026-08-22T00:00:00Z",
            Some(&recovery_key),
            7,
            Some(123),
            transcript_path,
        )
        .unwrap();

        assert_eq!(
            first,
            FinalizerDecision::Deferred {
                reason: "realtime_evidence_without_unique_match".to_string()
            }
        );
        assert_eq!(
            state["sess"]["pending_export_reason"],
            json!(super::super::state::FINALIZER_EVIDENCE_DEFERRED_REASON)
        );
        assert_eq!(
            state["sess"]["finalizer_deferred"],
            json!({
                "schema_version": 1,
                "reason": "realtime_evidence_without_unique_match",
                "future_source": "transcript_growth",
                "retry_boundary": "explicit_terminal",
                "recovery_key": recovery_key,
                "attempt": 1,
                "observed_boundary": {
                    "finalizer_high_water": 7,
                    "terminal_high_water_offset": 123,
                },
                "updated": "2026-08-22T00:00:00Z",
            })
        );

        let second = persist_finalizer_topology_decision(
            &state_file,
            &mut state,
            "sess",
            "turn-1",
            topology(),
            "2026-08-22T00:00:01Z",
            Some(&recovery_key),
            7,
            Some(123),
            transcript_path,
        )
        .unwrap();

        assert_eq!(
            second,
            FinalizerDecision::TerminalQuarantined {
                reason: super::super::state::DEFERRED_EVIDENCE_EXPIRED_REASON.to_string(),
                appended: true,
            }
        );
        assert!(state["sess"].get("pending_export_reason").is_none());
        assert!(state["sess"].get("finalizer_deferred").is_none());
        assert_eq!(state["sess"]["quarantined_turns"][0]["attempt"], json!(2));
        assert!(super::super::state::is_turn_terminally_accounted(
            &state["sess"],
            "turn-1"
        ));
    }

    #[test]
    fn trace_root_for_turn_preserves_nonzero_generation_scan_base() {
        let turns = vec![turn_with_tool("turn-1", "unmatched-tool")];
        let state = json!({
            "sess": {
                "realtime_export": {
                    "current": {
                        "schema_version": 1,
                        "context_id": "context-1",
                        "trace_id": "11111111111111111111111111111111",
                        "root_span_id": "3333333333333333",
                        "turn_id": null,
                        "tool_use_ids": [],
                        "tool_span_ids": {},
                        "tool_jobs": {},
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "created_at": "2026-08-16T00:00:00Z"
                    },
                    "generation": {
                        "rounds": [{
                            "generation_id": "generation-5",
                            "span_id": "5555555555555555",
                            "context_id": "context-1",
                            "ordinal": 5,
                            "status": "accounted"
                        }]
                    }
                }
            }
        });

        assert_eq!(
            finalizer_trace_root_for_turn_with_scan_base(&state, "sess", &turns, 0, 5).unwrap(),
            Some((
                "11111111111111111111111111111111".to_string(),
                "3333333333333333".to_string(),
            ))
        );
    }

    #[test]
    fn generation_span_ids_for_turn_preserve_nonzero_scan_base() {
        let turns = vec![turn_with_tool("turn-1", "unmatched-tool")];
        let state = json!({
            "sess": {
                "realtime_export": {
                    "registered_jobs": {
                        "generation-job": {
                            "job_id": "generation-job",
                            "payload_digest": "generation-digest",
                            "context_id": "context-1",
                            "kind": "generation",
                            "sequence": 1,
                            "status": "accounted"
                        }
                    },
                    "generation": {
                        "rounds": [{
                            "generation_id": "generation-5",
                            "span_id": "5555555555555555",
                            "context_id": "context-1",
                            "ordinal": 5,
                            "status": "accounted",
                            "job_id": "generation-job",
                            "payload_digest": "generation-digest"
                        }]
                    }
                }
            }
        });

        assert_eq!(
            finalizer_generation_span_ids_for_turn_with_scan_base(
                &state,
                "sess",
                &turns,
                0,
                u64::MAX,
                5,
            ),
            HashMap::from([(0, "5555555555555555".to_string())])
        );
        assert!(finalizer_generation_span_ids_for_turn_with_scan_base(
            &state,
            "sess",
            &turns,
            0,
            u64::MAX,
            0,
        )
        .is_empty());
    }

    #[test]
    fn policy_terminal_generation_rounds_do_not_supply_finalizer_span_ids() {
        let turns = vec![turn_with_tool("turn-1", "unmatched-tool")];
        let state = json!({
            "sess": {
                "realtime_export": {
                    "registered_jobs": {
                        "generation-job": {
                            "job_id": "generation-job",
                            "payload_digest": "generation-digest",
                            "context_id": "context-1",
                            "kind": "generation",
                            "sequence": 1,
                            "status": "accounted"
                        }
                    },
                    "generation": {
                        "rounds": [{
                            "generation_id": "generation-5",
                            "span_id": "5555555555555555",
                            "context_id": "context-1",
                            "ordinal": 5,
                            "status": "policy_terminal",
                            "terminal_reason": "realtime_child_oversize",
                            "job_id": "generation-job",
                            "payload_digest": "generation-digest"
                        }]
                    }
                }
            }
        });

        assert!(finalizer_generation_span_ids_for_turn_with_scan_base(
            &state,
            "sess",
            &turns,
            0,
            u64::MAX,
            5,
        )
        .is_empty());
    }
}
