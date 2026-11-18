//! turn builder ownership module.

use super::session_meta::resolve_session_origin_turn_trigger;
use super::types::classify_compaction_usage;
use super::{
    apply_omx_route, detect_leading_skill_placeholder, detect_skill_from_tool_input,
    detect_skill_from_user_message, extract_agent_message_content, extract_agent_message_type,
    extract_pty_session_id, extract_response_message_text, finalize_skill_invocations,
    is_internal_role_user_envelope, mcp_tool_result_output,
    merge_user_message_evidence_into_finalized_turn, normalize_tool_call,
    normalize_turn_semantics_with_usage_mode, parse_collab_agent_tool_call,
    parse_item_completed_user_message, parse_jsonish_value, parse_omx_user_prompt_submit_route,
    parse_skill_definition, parse_skill_invocations_from_text_elements, parse_subagent_activity,
    parse_subagent_source, push_skill_invocation_dedup, resolve_turn_trigger,
    upsert_assistant_message, AssistantMessage, CollabAgentEvent, CompactionEvent,
    LoadedSkillDefinition, NormalizedTurnSemantics, SkillInvocation, SkillInvocationSource,
    SubagentSourceInfo, TerminalError, TokenUsage, ToolCall, Turn, TurnTerminalState, TurnTrigger,
};
use super::{iso_to_nanos, json, looks_like_iso_timestamp, HashMap, Value};

#[derive(Debug, Clone, Default)]
pub(super) struct TurnBuilder {
    chat_id: Option<String>,
    turn_id: String,
    user_timestamp: String,
    user_text: String,
    user_content: Value,
    assistants: Vec<AssistantMessage>,
    tool_calls: Vec<ToolCall>,
    model: String,
    cwd: String,
    completed: bool,
    completion_timestamp: String,
    terminal_state: TurnTerminalState,
    terminal_error: Option<TerminalError>,
    skill_invocations: Vec<SkillInvocation>,
    skill_definitions: Vec<LoadedSkillDefinition>,
    skill_name: Option<String>,
    last_usage: Option<TokenUsage>,
    total_usage_start: Option<TokenUsage>,
    total_usage_end: Option<TokenUsage>,
    compactions: Vec<CompactionEvent>,
    collab_agent_events: Vec<CollabAgentEvent>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum LegacyInvocationBoundary {
    Active,
    UsageSealed,
}

fn timestamp_max(left: &str, right: &str) -> String {
    if left.is_empty() {
        return right.to_string();
    }
    if right.is_empty() {
        return left.to_string();
    }
    let left_ns = iso_to_nanos(left).parse::<u128>().unwrap_or(0);
    let right_ns = iso_to_nanos(right).parse::<u128>().unwrap_or(0);
    if right_ns > left_ns || (right_ns == left_ns && right > left) {
        right.to_string()
    } else {
        left.to_string()
    }
}

fn merge_terminal_last_agent_message(
    assistants: &mut Vec<AssistantMessage>,
    timestamp: &str,
    text: &str,
) {
    let text = text.trim();
    if text.is_empty() {
        return;
    }

    let text_already_present = assistants
        .iter()
        .any(|assistant| assistant.phase == "final_answer" && assistant.text == text);
    let last_is_empty_placeholder = assistants.last().map_or(false, |assistant| {
        assistant.text.is_empty()
            && (assistant.phase.starts_with("generation_") || assistant.phase == "assistant")
    });
    if text_already_present && last_is_empty_placeholder {
        assistants.pop();
        return;
    }

    let merged = if assistants.last().map_or(false, |assistant| {
        assistant.phase.starts_with("generation_") || assistant.phase == "assistant"
    }) {
        if let Some(last) = assistants.last_mut() {
            if last.text.is_empty() || last.text == text {
                last.text = text.to_string();
                last.phase = "final_answer".to_string();
                last.timestamp = timestamp_max(&last.timestamp, timestamp);
                true
            } else {
                false
            }
        } else {
            false
        }
    } else {
        false
    };
    if !merged {
        upsert_assistant_message(assistants, timestamp, text, "final_answer");
    }
}

fn terminal_state_for_payload(
    payload_type: &str,
    payload: &Value,
) -> (bool, TurnTerminalState, Option<TerminalError>) {
    let terminal_error = (payload_type == "task_complete")
        .then(|| parse_terminal_error(payload))
        .flatten();
    let completed = payload_type == "task_complete" && terminal_error.is_none();
    let terminal_state = if payload_type == "task_complete" {
        if terminal_error.is_some() {
            TurnTerminalState::Failed
        } else {
            TurnTerminalState::Completed
        }
    } else {
        TurnTerminalState::Aborted
    };
    (completed, terminal_state, terminal_error)
}

fn apply_terminal_to_completed_turn(
    turn: &mut Turn,
    payload_type: &str,
    payload: &Value,
    timestamp: &str,
) {
    if let Some(text) = payload
        .get("last_agent_message")
        .and_then(|value| value.as_str())
    {
        merge_terminal_last_agent_message(&mut turn.assistants, timestamp, text);
    }
    let (completed, terminal_state, terminal_error) =
        terminal_state_for_payload(payload_type, payload);
    turn.completed = completed;
    turn.completion_timestamp = timestamp.to_string();
    turn.terminal_state = terminal_state;
    turn.terminal_error = terminal_error;
}

impl TurnBuilder {
    fn last_observed_timestamp(&self) -> String {
        let mut latest = self.user_timestamp.clone();
        latest = timestamp_max(&latest, &self.completion_timestamp);
        for assistant in &self.assistants {
            latest = timestamp_max(&latest, &assistant.timestamp);
            latest = timestamp_max(&latest, &assistant.token_count_timestamp);
            latest = timestamp_max(&latest, &assistant.invocation_started_timestamp);
        }
        for tool in &self.tool_calls {
            latest = timestamp_max(&latest, &tool.started_timestamp);
            latest = timestamp_max(&latest, &tool.ended_timestamp);
        }
        for compaction in &self.compactions {
            latest = timestamp_max(&latest, &compaction.timestamp);
            latest = timestamp_max(&latest, &compaction.end_timestamp);
        }
        latest
    }

    fn mark_superseded(&mut self) {
        self.completed = false;
        self.terminal_state = TurnTerminalState::Superseded;
        self.terminal_error = None;
        self.completion_timestamp = self.last_observed_timestamp();
    }

    fn accepts_terminal_for(&self, payload_turn_id: Option<&str>) -> bool {
        let Some(payload_turn_id) = payload_turn_id else {
            return true;
        };
        if payload_turn_id.is_empty() || self.turn_id == payload_turn_id {
            return true;
        }
        self.chat_id.is_none() && self.turn_id.starts_with("turn-")
    }

    fn apply_terminal(&mut self, payload_type: &str, payload: &Value, timestamp: &str) {
        if let Some(turn_id) = payload
            .get("turn_id")
            .and_then(|value| value.as_str())
            .filter(|value| !value.is_empty())
        {
            if self.turn_id.is_empty()
                || self.chat_id.is_none() && self.turn_id.starts_with("turn-")
            {
                self.turn_id = turn_id.to_string();
                self.chat_id = Some(turn_id.to_string());
            }
        }
        if let Some(text) = payload
            .get("last_agent_message")
            .and_then(|value| value.as_str())
        {
            merge_terminal_last_agent_message(&mut self.assistants, timestamp, text);
        }
        let (completed, terminal_state, terminal_error) =
            terminal_state_for_payload(payload_type, payload);
        self.completed = completed;
        self.completion_timestamp = timestamp.to_string();
        self.terminal_state = terminal_state;
        self.terminal_error = terminal_error;
    }
}

pub fn build_turns(parsed_lines: &[Value]) -> Vec<Turn> {
    build_turns_with_initial_input_tokens(parsed_lines, None)
}

pub(crate) fn build_turns_with_initial_input_tokens(
    parsed_lines: &[Value],
    initial_last_input_tokens: Option<u64>,
) -> Vec<Turn> {
    build_turns_with_usage_context(parsed_lines, initial_last_input_tokens, false)
}

pub(crate) fn build_turns_with_usage_context(
    parsed_lines: &[Value],
    initial_last_input_tokens: Option<u64>,
    initial_native_usage_mode: bool,
) -> Vec<Turn> {
    let normalized_turns =
        normalize_turn_semantics_with_usage_mode(parsed_lines, initial_native_usage_mode);
    let mut turns = Vec::new();
    let mut current_turn: Option<TurnBuilder> = None;
    let mut pending_user_text = String::new();
    let mut pending_user_timestamp = String::new();
    let mut call_index: HashMap<String, usize> = HashMap::new();
    let mut pty_call_by_session: HashMap<String, usize> = HashMap::new();
    let mut current_assistant_index: usize = 0;
    let mut invocation_boundary = LegacyInvocationBoundary::Active;
    // Compaction tracking
    let mut session_compact_count: u32 = 0;
    let mut last_input_tokens = initial_last_input_tokens;
    let mut pending_compaction_backfill: bool = false;
    // Sub-agent origin info (session-level, set once from session_meta)
    let mut subagent_source: Option<SubagentSourceInfo> = None;
    let mut session_turn_trigger: Option<TurnTrigger> = None;
    let mut session_system_instructions = String::new();
    let mut active_turn_context_id: Option<String> = None;
    let mut response_role_user_turn_id: Option<String> = None;

    let ensure_current_turn = |current_turn: &mut Option<TurnBuilder>,
                               turns: &[Turn],
                               at: &str,
                               pending_user_text: &mut String,
                               pending_user_timestamp: &mut String| {
        if current_turn.is_none() {
            let mut turn = TurnBuilder {
                turn_id: format!("turn-{}", turns.len() + 1),
                ..TurnBuilder::default()
            };
            if !pending_user_text.is_empty() {
                turn.user_text = pending_user_text.clone();
                turn.user_timestamp = pending_user_timestamp.clone();
                pending_user_text.clear();
                pending_user_timestamp.clear();
            } else {
                turn.user_timestamp = at.to_string();
            }
            *current_turn = Some(turn);
        }
    };

    let finalize_turn = |turn: TurnBuilder| -> Option<Turn> {
        // Native usage and response-item output are normalized separately from
        // the legacy builder. Keep their turn shell so normalization can attach
        // the evidence, or Stop can retain an incomplete turn for recovery.
        let has_normalized_evidence = normalized_turns.iter().any(|normalized| {
            normalized.turn_id == turn.turn_id
                && (!normalized.invocations.is_empty() || !normalized.callbacks.is_empty())
        });
        if turn.user_text.is_empty()
            && turn.assistants.is_empty()
            && turn.tool_calls.is_empty()
            && turn.compactions.is_empty()
            && turn.terminal_error.is_none()
            && !has_normalized_evidence
            && turn.terminal_state != TurnTerminalState::Aborted
        {
            return None;
        }

        let mut assistants = turn.assistants;
        if let Some(usage) = turn
            .total_usage_start
            .as_ref()
            .zip(turn.total_usage_end.as_ref())
            .and_then(|(start, end)| TokenUsage::delta(start, end))
            .or(turn.last_usage)
        {
            if let Some(final_assistant) = assistants.iter_mut().rev().find(|assistant| {
                assistant.phase == "final_answer" || assistant.phase == "assistant"
            }) {
                final_assistant.usage = usage;
            }
        }

        let skill_invocations =
            finalize_skill_invocations(turn.skill_invocations, &turn.skill_definitions);
        let skill_name = skill_invocations
            .first()
            .map(|invocation| invocation.name.clone())
            .or(turn.skill_name);

        Some(Turn {
            chat_id: turn.chat_id,
            turn_id: turn.turn_id,
            user_timestamp: turn.user_timestamp,
            user_text: turn.user_text,
            user_content: turn.user_content,
            assistants,
            tool_calls: turn.tool_calls,
            model: turn.model,
            cwd: turn.cwd,
            system_instructions: String::new(),
            completed: turn.completed,
            completion_timestamp: turn.completion_timestamp,
            terminal_state: turn.terminal_state,
            terminal_error: turn.terminal_error,
            trigger: TurnTrigger::Unknown,
            skill_invocations,
            skill_name,
            compactions: turn.compactions,
            collab_agent_events: turn.collab_agent_events,
            subagent_source: None, // Set at session level, not per-turn
        })
    };

    for entry in parsed_lines {
        let timestamp = entry
            .get("timestamp")
            .and_then(|value| value.as_str())
            .filter(|value| looks_like_iso_timestamp(value))
            .unwrap_or("")
            .to_string();
        let entry_type = entry
            .get("type")
            .and_then(|value| value.as_str())
            .unwrap_or("");
        let payload = entry.get("payload").cloned().unwrap_or(Value::Null);

        if entry_type == "session_meta" {
            if session_system_instructions.is_empty() {
                session_system_instructions = payload
                    .get("base_instructions")
                    .and_then(|value| value.get("text"))
                    .and_then(Value::as_str)
                    .filter(|value| !value.is_empty())
                    .unwrap_or("")
                    .to_string();
            }
            // Parse sub-agent origin info from session_meta.
            // Codex uses source.subagent.thread_spawn ("subagent", no underscore).
            // Legacy kept as fallback: source.sub_agent.thread_spawn.
            // Final fallback: thread_source == "subagent" at payload level.
            if subagent_source.is_none() {
                subagent_source = parse_subagent_source(&payload);
            }
            if session_turn_trigger.is_none() {
                session_turn_trigger = Some(resolve_session_origin_turn_trigger(&payload));
            }
            continue;
        }

        if entry_type == "turn_context" {
            ensure_current_turn(
                &mut current_turn,
                &turns,
                &timestamp,
                &mut pending_user_text,
                &mut pending_user_timestamp,
            );
            if let Some(turn) = current_turn.as_mut() {
                if let Some(turn_id) = payload.get("turn_id").and_then(|value| value.as_str()) {
                    if !turn_id.is_empty() {
                        turn.turn_id = turn_id.to_string();
                        turn.chat_id = Some(turn_id.to_string());
                        active_turn_context_id = Some(turn_id.to_string());
                    }
                }
                if let Some(model) = payload.get("model").and_then(|value| value.as_str()) {
                    if !model.is_empty() {
                        turn.model = model.to_string();
                    }
                }
                if let Some(cwd) = payload.get("cwd").and_then(|value| value.as_str()) {
                    if !cwd.is_empty() {
                        turn.cwd = cwd.to_string();
                    }
                }
            }
            continue;
        }

        if entry_type == "event_msg" {
            let payload_type = payload
                .get("type")
                .and_then(|value| value.as_str())
                .unwrap_or("");
            if let Some(invocations) = parse_collab_agent_tool_call(&payload) {
                ensure_current_turn(
                    &mut current_turn,
                    &turns,
                    &timestamp,
                    &mut pending_user_text,
                    &mut pending_user_timestamp,
                );
                if let Some(turn) = current_turn.as_mut() {
                    turn.collab_agent_events.extend(invocations);
                }
                continue;
            }
            if let Some(activity) = parse_subagent_activity(&payload, &timestamp) {
                ensure_current_turn(
                    &mut current_turn,
                    &turns,
                    &timestamp,
                    &mut pending_user_text,
                    &mut pending_user_timestamp,
                );
                if let Some(turn) = current_turn.as_mut() {
                    turn.collab_agent_events.push(activity);
                }
                continue;
            }
            if let Some(user_evidence) = parse_item_completed_user_message(&payload, &timestamp) {
                let declared_turn_id = payload
                    .get("turn_id")
                    .and_then(Value::as_str)
                    .filter(|turn_id| !turn_id.is_empty());
                if let Some(finalized_turn) = declared_turn_id
                    .and_then(|turn_id| turns.iter_mut().find(|turn| turn.turn_id == turn_id))
                {
                    merge_user_message_evidence_into_finalized_turn(
                        finalized_turn,
                        user_evidence,
                        &timestamp,
                    );
                    continue;
                }
                ensure_current_turn(
                    &mut current_turn,
                    &turns,
                    &timestamp,
                    &mut pending_user_text,
                    &mut pending_user_timestamp,
                );
                if let Some(turn) = current_turn.as_mut() {
                    if let Some(declared_turn_id) = declared_turn_id {
                        if turn.chat_id.is_none() && turn.turn_id.starts_with("turn-") {
                            turn.turn_id = declared_turn_id.to_string();
                            turn.chat_id = Some(declared_turn_id.to_string());
                        } else if turn.turn_id != declared_turn_id {
                            // A delayed/mirrored UserMessage must not leak skill
                            // evidence into the currently active turn.
                            continue;
                        }
                    }
                    let text = user_evidence.text.trim().to_string();
                    if !text.is_empty() {
                        let replace_response_fallback =
                            response_role_user_turn_id.as_deref() == Some(turn.turn_id.as_str());
                        if turn.user_text.is_empty() || replace_response_fallback {
                            turn.user_text = text;
                            turn.user_timestamp = timestamp.clone();
                            response_role_user_turn_id = None;
                        }
                    }
                    for definition in user_evidence.definitions {
                        if !turn.skill_definitions.iter().any(|existing| {
                            existing.name == definition.name && existing.path == definition.path
                        }) {
                            turn.skill_definitions.push(definition);
                        }
                    }
                    for invocation in user_evidence.invocations {
                        push_skill_invocation_dedup(&mut turn.skill_invocations, invocation);
                    }
                }
                continue;
            }
            match payload_type {
                "thread_goal_updated" => {
                    let goal = payload.get("goal").unwrap_or(&Value::Null);
                    let status = goal.get("status").and_then(Value::as_str).unwrap_or("");
                    let objective = goal
                        .get("objective")
                        .and_then(Value::as_str)
                        .map(str::trim)
                        .filter(|value| !value.is_empty());
                    if status == "active" {
                        if let Some(objective) = objective {
                            pending_user_text = format!("/goal {objective}");
                            pending_user_timestamp = timestamp.clone();
                        }
                    }
                }
                "task_started" => {
                    let native_turn_id = payload
                        .get("turn_id")
                        .and_then(|value| value.as_str())
                        .filter(|value| !value.is_empty())
                        .map(str::to_string);
                    if current_turn.as_ref().is_some_and(|turn| {
                        native_turn_id
                            .as_deref()
                            .is_some_and(|turn_id| turn.turn_id == turn_id)
                    }) {
                        continue;
                    }
                    active_turn_context_id = None;
                    response_role_user_turn_id = None;
                    if let Some(mut turn) = current_turn.take() {
                        turn.mark_superseded();
                        if let Some(turn) = finalize_turn(turn) {
                            turns.push(turn);
                        }
                    }
                    call_index.clear();
                    pty_call_by_session.clear();
                    let mut turn = TurnBuilder {
                        turn_id: native_turn_id.clone().unwrap_or_default(),
                        chat_id: native_turn_id,
                        user_timestamp: timestamp.clone(),
                        ..TurnBuilder::default()
                    };
                    if turn.turn_id.is_empty() {
                        turn.turn_id = format!("turn-{}", turns.len() + 1);
                    }
                    if !pending_user_text.is_empty() {
                        turn.user_text = pending_user_text.clone();
                        turn.user_timestamp = pending_user_timestamp.clone();
                        pending_user_text.clear();
                        pending_user_timestamp.clear();
                    }
                    current_turn = Some(turn);
                }
                "user_message" => {
                    let raw_text = payload
                        .get("message")
                        .and_then(|value| value.as_str())
                        .unwrap_or("");
                    let text = raw_text.trim().to_string();
                    if text.is_empty() {
                        continue;
                    }
                    if let Some(turn) = current_turn.as_mut() {
                        let replace_response_fallback =
                            response_role_user_turn_id.as_deref() == Some(turn.turn_id.as_str());
                        if turn.user_text.is_empty() || replace_response_fallback {
                            turn.user_text = text.clone();
                            turn.user_timestamp = timestamp.clone();
                            response_role_user_turn_id = None;
                        }
                        let structured = parse_skill_invocations_from_text_elements(
                            raw_text,
                            payload.get("text_elements").and_then(Value::as_array),
                            &timestamp,
                        );
                        if structured.is_empty() {
                            if let Some(name) = detect_skill_from_user_message(&text) {
                                if turn.skill_name.is_none() {
                                    turn.skill_name = Some(name.clone());
                                }
                                push_skill_invocation_dedup(
                                    &mut turn.skill_invocations,
                                    SkillInvocation {
                                        raw_placeholder: format!("${name}"),
                                        name,
                                        source: SkillInvocationSource::LegacyMarkdown,
                                        timestamp: timestamp.clone(),
                                        byte_start: None,
                                        byte_end: None,
                                        resolved_workflow: None,
                                        definition_path: None,
                                        original_text_element_index: None,
                                        ordinal: 0,
                                    },
                                );
                            }
                        } else {
                            for invocation in structured {
                                push_skill_invocation_dedup(
                                    &mut turn.skill_invocations,
                                    invocation,
                                );
                            }
                        }
                    } else {
                        pending_user_text = text;
                        pending_user_timestamp = timestamp.clone();
                    }
                }
                "token_count" => {
                    ensure_current_turn(
                        &mut current_turn,
                        &turns,
                        &timestamp,
                        &mut pending_user_text,
                        &mut pending_user_timestamp,
                    );
                    if let Some(turn) = current_turn.as_mut() {
                        if let Some(info) = payload.get("info") {
                            if let Some(last) = info.get("last_token_usage") {
                                let usage = TokenUsage::from_usage_value(last);
                                if usage.has_any() {
                                    let mut compaction_metadata_only = false;
                                    if pending_compaction_backfill {
                                        let disposition = classify_compaction_usage(&usage);
                                        if let Some(post_tokens) = disposition.post_tokens {
                                            if let Some(compact) = turn.compactions.last_mut() {
                                                compact.post_tokens = Some(post_tokens);
                                                compact.end_timestamp = timestamp.clone();
                                            }
                                        }
                                        compaction_metadata_only = disposition.metadata_only;
                                        pending_compaction_backfill = false;
                                    }
                                    if compaction_metadata_only {
                                        continue;
                                    }
                                    // Track last_input_tokens for next compaction's pre_tokens
                                    if let Some(input) = usage.input {
                                        if input > 0 {
                                            last_input_tokens = Some(input as u64);
                                        }
                                    }
                                    turn.last_usage = Some(usage.clone());

                                    // Codex emits `token_count` AFTER each model
                                    // invocation completes — `last_token_usage`
                                    // describes the invocation that just ended.
                                    // If the prior `agent_message` already created
                                    // a text-bearing assistant with no usage yet,
                                    // attach this usage to it (correct attribution).
                                    // Otherwise (no agent_message this gen — e.g.
                                    // reasoning-only or tool-only) create an empty
                                    // placeholder that carries the usage; the
                                    // OTel emitter filters empty-text assistants
                                    // so this placeholder stays invisible while
                                    // still contributing to total token counts.
                                    let mut filled_existing = false;
                                    if let Some(last) = turn.assistants.last_mut() {
                                        if !last.usage.has_any() {
                                            last.usage = usage.clone();
                                            last.token_count_timestamp = timestamp.clone();
                                            filled_existing = true;
                                        }
                                    }
                                    if filled_existing {
                                        // This token_count completed a LLM call.
                                        // Any subsequent function_call belongs to
                                        // the NEXT LLM call (tool-only, no
                                        // agent_message), so we flag that a new
                                        // generation placeholder is needed.
                                        invocation_boundary = LegacyInvocationBoundary::UsageSealed;
                                    } else {
                                        let gen_phase =
                                            format!("generation_{}", turn.assistants.len());
                                        turn.assistants.push(AssistantMessage {
                                            timestamp: timestamp.clone(),
                                            text: String::new(),
                                            media_content: serde_json::Value::Null,
                                            phase: gen_phase,
                                            usage,
                                            token_count_timestamp: timestamp.clone(),
                                            invocation_started_timestamp: String::new(),
                                            invocation_end_source: None,
                                        });
                                        current_assistant_index =
                                            turn.assistants.len().saturating_sub(1);
                                        invocation_boundary = LegacyInvocationBoundary::Active;
                                    }
                                }
                            }
                            if let Some(total) = info.get("total_token_usage") {
                                let usage = TokenUsage::from_usage_value(total);
                                if usage.has_any() {
                                    if turn.total_usage_start.is_none() {
                                        turn.total_usage_start = Some(usage.clone());
                                    }
                                    turn.total_usage_end = Some(usage);
                                }
                            }
                        }
                    }
                }
                "agent_message" => {
                    ensure_current_turn(
                        &mut current_turn,
                        &turns,
                        &timestamp,
                        &mut pending_user_text,
                        &mut pending_user_timestamp,
                    );
                    if let Some(turn) = current_turn.as_mut() {
                        let text = payload
                            .get("message")
                            .and_then(|value| value.as_str())
                            .unwrap_or("")
                            .trim()
                            .to_string();
                        if text.is_empty() {
                            continue;
                        }
                        let phase = payload
                            .get("phase")
                            .and_then(|value| value.as_str())
                            .unwrap_or("assistant")
                            .to_string();
                        // If we have generation-based assistants (from token_count), merge
                        // the agent_message text into the last generation rather than
                        // creating a duplicate entry. Also advance the placeholder's
                        // timestamp to the agent_message's own timestamp when the latter
                        // is later — span ordering in Langfuse depends on this timestamp,
                        // and the placeholder originally inherits the token_count's ts
                        // which can be earlier than the actual message emission.
                        //
                        // Guard: do NOT merge when the prior invocation is usage-sealed — that
                        // flag means the prior token_count already closed the previous
                        // LLM call, so this agent_message belongs to a NEW LLM invocation.
                        let merged = if invocation_boundary != LegacyInvocationBoundary::UsageSealed
                            && !turn.assistants.is_empty()
                            && turn
                                .assistants
                                .last()
                                .map_or(false, |a| a.phase.starts_with("generation_"))
                        {
                            if let Some(last) = turn.assistants.last_mut() {
                                last.text = text.clone();
                                last.phase = phase.clone();
                                if !timestamp.is_empty() {
                                    let new_ns: u128 =
                                        iso_to_nanos(&timestamp).parse().unwrap_or(0);
                                    let old_ns: u128 =
                                        iso_to_nanos(&last.timestamp).parse().unwrap_or(0);
                                    if new_ns > old_ns {
                                        last.timestamp = timestamp.clone();
                                    }
                                }
                                true
                            } else {
                                false
                            }
                        } else {
                            false
                        };
                        if !merged {
                            upsert_assistant_message(
                                &mut turn.assistants,
                                &timestamp,
                                &text,
                                &phase,
                            );
                        }
                        current_assistant_index = turn.assistants.len().saturating_sub(1);
                        invocation_boundary = LegacyInvocationBoundary::Active;
                    }
                }
                "task_complete" | "turn_aborted" => {
                    let payload_turn_id = payload
                        .get("turn_id")
                        .and_then(|value| value.as_str())
                        .filter(|value| !value.is_empty());
                    if current_turn
                        .as_ref()
                        .is_some_and(|turn| !turn.accepts_terminal_for(payload_turn_id))
                    {
                        if let Some(turn_id) = payload_turn_id {
                            if let Some(previous_turn) =
                                turns.iter_mut().rev().find(|turn| turn.turn_id == turn_id)
                            {
                                apply_terminal_to_completed_turn(
                                    previous_turn,
                                    payload_type,
                                    &payload,
                                    &timestamp,
                                );
                            }
                        }
                        continue;
                    }
                    ensure_current_turn(
                        &mut current_turn,
                        &turns,
                        &timestamp,
                        &mut pending_user_text,
                        &mut pending_user_timestamp,
                    );
                    if let Some(turn) = current_turn.as_mut() {
                        turn.apply_terminal(payload_type, &payload, &timestamp);
                    }
                    if let Some(turn) = current_turn.take().and_then(finalize_turn) {
                        turns.push(turn);
                    }
                    call_index.clear();
                    pty_call_by_session.clear();
                    current_assistant_index = 0;
                    invocation_boundary = LegacyInvocationBoundary::Active;
                    active_turn_context_id = None;
                    response_role_user_turn_id = None;
                }
                "dynamic_tool_call_response" => {
                    if let Some(turn) = current_turn.as_mut() {
                        let call_id = payload
                            .get("call_id")
                            .and_then(|v| v.as_str())
                            .unwrap_or("");
                        if let Some(index) = call_index.get(call_id).copied() {
                            if let Some(call) = turn.tool_calls.get_mut(index) {
                                call.success = payload.get("success").and_then(|v| v.as_bool());
                                call.ended_timestamp = timestamp.clone();
                                if call.output.is_null() {
                                    if let Some(items) =
                                        payload.get("content_items").and_then(|v| v.as_array())
                                    {
                                        if agentic_otel_common::media::content_blocks(&Value::Array(items.clone())).is_some() {
                                            call.output = Value::Array(items.clone());
                                        } else if let Some(text) = items.first().and_then(|v| v.get("text")).and_then(Value::as_str) {
                                            call.output = Value::String(text.to_string());
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                "mcp_tool_call_end" => {
                    if let Some(turn) = current_turn.as_mut() {
                        let call_id = payload
                            .get("call_id")
                            .and_then(|v| v.as_str())
                            .unwrap_or("");
                        if let Some(index) = call_index.get(call_id).copied() {
                            if let Some(call) = turn.tool_calls.get_mut(index) {
                                if let Some(result) = payload.get("result") {
                                    if result.get("Ok").is_some() {
                                        call.success = Some(true);
                                        if call.output.is_null() {
                                            call.output = mcp_tool_result_output(result);
                                        }
                                    } else if result.get("Err").is_some() {
                                        call.success = Some(false);
                                        if call.output.is_null() {
                                            call.output = mcp_tool_result_output(result);
                                        }
                                    }
                                }
                                call.ended_timestamp = timestamp.clone();
                            }
                        }
                    }
                }
                // --- CollabAgent protocol events ---
                "collab_agent_spawn_end" => {
                    ensure_current_turn(
                        &mut current_turn,
                        &turns,
                        &timestamp,
                        &mut pending_user_text,
                        &mut pending_user_timestamp,
                    );
                    if let Some(turn) = current_turn.as_mut() {
                        let status = payload
                            .get("status")
                            .and_then(|v| v.as_str())
                            .unwrap_or("unknown");
                        // Skip non-terminal statuses (only capture final state)
                        if status == "completed"
                            || status == "interrupted"
                            || status == "error"
                            || status.starts_with("completed")
                        {
                            turn.collab_agent_events.push(CollabAgentEvent::SpawnEnd {
                                call_id: payload
                                    .get("call_id")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                sender_thread_id: payload
                                    .get("sender_thread_id")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                new_thread_id: payload
                                    .get("new_thread_id")
                                    .and_then(|v| v.as_str())
                                    .map(|s| s.to_string()),
                                nickname: payload
                                    .get("new_agent_nickname")
                                    .and_then(|v| v.as_str())
                                    .map(|s| s.to_string()),
                                role: payload
                                    .get("new_agent_role")
                                    .and_then(|v| v.as_str())
                                    .map(|s| s.to_string()),
                                prompt: payload
                                    .get("prompt")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                model: payload
                                    .get("model")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                status: status.to_string(),
                            });
                        }
                    }
                }
                "collab_agent_interaction_begin" => {
                    ensure_current_turn(
                        &mut current_turn,
                        &turns,
                        &timestamp,
                        &mut pending_user_text,
                        &mut pending_user_timestamp,
                    );
                    if let Some(turn) = current_turn.as_mut() {
                        turn.collab_agent_events
                            .push(CollabAgentEvent::InteractionBegin {
                                call_id: payload
                                    .get("call_id")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                sender_thread_id: payload
                                    .get("sender_thread_id")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                receiver_thread_id: payload
                                    .get("receiver_thread_id")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                prompt: payload
                                    .get("prompt")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                            });
                    }
                }
                "collab_agent_interaction_end" => {
                    ensure_current_turn(
                        &mut current_turn,
                        &turns,
                        &timestamp,
                        &mut pending_user_text,
                        &mut pending_user_timestamp,
                    );
                    if let Some(turn) = current_turn.as_mut() {
                        turn.collab_agent_events
                            .push(CollabAgentEvent::InteractionEnd {
                                call_id: payload
                                    .get("call_id")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                sender_thread_id: payload
                                    .get("sender_thread_id")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                receiver_thread_id: payload
                                    .get("receiver_thread_id")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                receiver_nickname: payload
                                    .get("receiver_agent_nickname")
                                    .and_then(|v| v.as_str())
                                    .map(|s| s.to_string()),
                                receiver_role: payload
                                    .get("receiver_agent_role")
                                    .and_then(|v| v.as_str())
                                    .map(|s| s.to_string()),
                                prompt: payload
                                    .get("prompt")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                status: payload
                                    .get("status")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("unknown")
                                    .to_string(),
                            });
                    }
                }
                _ => {}
            }
            continue;
        }

        // Handle top-level "compacted" event (Codex context compression)
        if entry_type == "compacted" {
            ensure_current_turn(
                &mut current_turn,
                &turns,
                &timestamp,
                &mut pending_user_text,
                &mut pending_user_timestamp,
            );
            if let Some(turn) = current_turn.as_mut() {
                session_compact_count += 1;
                turn.compactions.push(CompactionEvent {
                    timestamp: timestamp.clone(),
                    end_timestamp: String::new(),
                    pre_tokens: last_input_tokens,
                    post_tokens: None,
                    sequence: session_compact_count,
                });
                pending_compaction_backfill = true;
            }
            continue;
        }

        if entry_type != "response_item" {
            continue;
        }

        let payload_type = payload
            .get("type")
            .and_then(|value| value.as_str())
            .unwrap_or("");
        if payload_type == "message"
            && payload.get("role").and_then(Value::as_str) == Some("user")
            && current_turn.is_none()
        {
            // Codex 0.148 writes session bootstrap as role=user before any
            // native turn. It is initialization state, not a user turn.
            continue;
        }
        ensure_current_turn(
            &mut current_turn,
            &turns,
            &timestamp,
            &mut pending_user_text,
            &mut pending_user_timestamp,
        );
        let Some(turn) = current_turn.as_mut() else {
            continue;
        };

        match payload_type {
            "message" => {
                if let Some(turn_id) = payload
                    .get("internal_chat_message_metadata_passthrough")
                    .and_then(|metadata| metadata.get("turn_id"))
                    .and_then(Value::as_str)
                    .filter(|turn_id| !turn_id.is_empty())
                {
                    if turn.turn_id.is_empty() || turn.turn_id.starts_with("turn-") {
                        turn.turn_id = turn_id.to_string();
                        turn.chat_id = Some(turn_id.to_string());
                    }
                }

                let role = payload.get("role").and_then(Value::as_str).unwrap_or("");
                let text = extract_response_message_text(&payload);
                match role {
                    "developer" => {
                        if let Some((raw_placeholder, resolved_workflow)) =
                            parse_omx_user_prompt_submit_route(&text)
                        {
                            apply_omx_route(
                                &mut turn.skill_invocations,
                                &raw_placeholder,
                                &resolved_workflow,
                                &timestamp,
                            );
                        }
                    }
                    "user" => {
                        if let Some(definition) = parse_skill_definition(&text, &timestamp) {
                            if !turn.skill_definitions.iter().any(|existing| {
                                existing.name == definition.name && existing.path == definition.path
                            }) {
                                turn.skill_definitions.push(definition);
                            }
                        } else if !is_internal_role_user_envelope(&text) {
                            let metadata_turn_id = payload
                                .get("internal_chat_message_metadata_passthrough")
                                .and_then(|metadata| metadata.get("turn_id"))
                                .and_then(Value::as_str)
                                .filter(|turn_id| !turn_id.is_empty());
                            let belongs_to_active_turn =
                                active_turn_context_id.as_deref().is_some_and(|active| {
                                    metadata_turn_id.map_or(true, |candidate| candidate == active)
                                        && turn.turn_id == active
                                });
                            if belongs_to_active_turn && turn.user_text.is_empty() {
                                let candidate = text.trim();
                                if !candidate.is_empty() {
                                    turn.user_text = candidate.to_string();
                                    turn.user_timestamp = timestamp.clone();
                                    response_role_user_turn_id = Some(turn.turn_id.clone());
                                }
                            }
                            if belongs_to_active_turn {
                                if let Some(content) = payload.get("content").filter(|v| agentic_otel_common::media::content_blocks(v).is_some()) { turn.user_content = content.clone(); }
                                if let Some(name) = detect_leading_skill_placeholder(&text) {
                                    push_skill_invocation_dedup(
                                        &mut turn.skill_invocations,
                                        SkillInvocation {
                                            raw_placeholder: format!("${name}"),
                                            name,
                                            source: SkillInvocationSource::LegacyMarkdown,
                                            timestamp: timestamp.clone(),
                                            byte_start: None,
                                            byte_end: None,
                                            resolved_workflow: None,
                                            definition_path: None,
                                            original_text_element_index: None,
                                            ordinal: 0,
                                        },
                                    );
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
            "agent_message" => {
                let author = payload.get("author").and_then(Value::as_str).unwrap_or("");
                let recipient = payload
                    .get("recipient")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                let content = extract_agent_message_content(&payload);
                let message_type = extract_agent_message_type(&content);
                if message_type.as_deref() == Some("NEW_TASK") && turn.user_text.is_empty() {
                    turn.user_text = content.clone();
                    turn.user_timestamp = timestamp.clone();
                }
                if !author.is_empty() && !recipient.is_empty() && !content.is_empty() {
                    turn.collab_agent_events
                        .push(CollabAgentEvent::AgentMessage {
                            author: author.to_string(),
                            recipient: recipient.to_string(),
                            message_type,
                            content,
                            timestamp: timestamp.clone(),
                        });
                }
            }
            "function_call" | "custom_tool_call" => {
                let tool_name = super::response_tool_name(&payload);
                let raw_name = tool_name.as_str();
                let raw_args = if payload_type == "custom_tool_call" {
                    payload.get("input").cloned().unwrap_or(Value::Null)
                } else {
                    payload.get("arguments").cloned().unwrap_or(Value::Null)
                };
                let parsed_args = parse_jsonish_value(&raw_args);

                // Lowest-precedence compatibility evidence: a supported tool
                // envelope statically reads one complete SKILL.md path.
                if let Some(invocation) =
                    detect_skill_from_tool_input(raw_name, &raw_args, &parsed_args, &timestamp)
                {
                    if turn.skill_name.is_none() {
                        turn.skill_name = Some(invocation.name.clone());
                    }
                    push_skill_invocation_dedup(&mut turn.skill_invocations, invocation);
                }

                let call_id = payload
                    .get("call_id")
                    .or_else(|| payload.get("callId"))
                    .and_then(|value| value.as_str())
                    .unwrap_or("")
                    .to_string();
                if call_id.is_empty() {
                    continue;
                }

                if raw_name == "write_stdin" {
                    if let Some(session_id) = parsed_args
                        .get("session_id")
                        .and_then(|value| value.as_i64())
                        .map(|value| value.to_string())
                        .or_else(|| {
                            parsed_args
                                .get("session_id")
                                .and_then(|value| value.as_str())
                                .map(|value| value.to_string())
                        })
                    {
                        if let Some(existing_index) = pty_call_by_session.get(&session_id).copied()
                        {
                            call_index.insert(call_id, existing_index);
                            continue;
                        }
                    }
                }

                // If a previous token_count signalled a completed LLM call
                // but no agent_message followed, this function_call belongs
                // to a new (tool-only) LLM invocation. Create a placeholder
                // AssistantMessage so the tool is attributed correctly.
                if invocation_boundary == LegacyInvocationBoundary::UsageSealed {
                    let gen_phase = format!("generation_{}", turn.assistants.len());
                    turn.assistants.push(AssistantMessage {
                        timestamp: timestamp.clone(),
                        text: String::new(),
                        media_content: serde_json::Value::Null,
                        phase: gen_phase,
                        usage: TokenUsage::default(),
                        token_count_timestamp: String::new(),
                        invocation_started_timestamp: String::new(),
                        invocation_end_source: None,
                    });
                    current_assistant_index = turn.assistants.len().saturating_sub(1);
                    invocation_boundary = LegacyInvocationBoundary::Active;
                }

                let (name, input) = normalize_tool_call(raw_name, &parsed_args);
                let index = turn.tool_calls.len();
                turn.tool_calls.push(ToolCall {
                    call_id: call_id.clone(),
                    name,
                    input,
                    output: Value::Null,
                    started_timestamp: timestamp.clone(),
                    ended_timestamp: String::new(),
                    assistant_index: current_assistant_index,
                    success: None,
                });
                call_index.insert(call_id, index);
            }
            "function_call_output" | "custom_tool_call_output" => {
                let call_id = payload
                    .get("call_id")
                    .or_else(|| payload.get("callId"))
                    .and_then(|value| value.as_str())
                    .unwrap_or("");
                let Some(index) = call_index.get(call_id).copied() else {
                    continue;
                };
                let Some(call) = turn.tool_calls.get_mut(index) else {
                    continue;
                };
                let output =
                    parse_jsonish_value(&payload.get("output").cloned().unwrap_or(Value::Null));
                call.output = output.clone();
                call.ended_timestamp = timestamp.clone();

                if call.name == "Bash" {
                    if let Some(output_text) = output.as_str() {
                        if let Some(session_id) = extract_pty_session_id(output_text) {
                            pty_call_by_session.insert(session_id, index);
                        }
                    }
                }
            }
            "tool_search_call" => {
                if payload.get("execution").and_then(Value::as_str) == Some("client") {
                    let call_id = payload.get("call_id").and_then(Value::as_str).unwrap_or("");
                    if !call_id.is_empty() && !call_index.contains_key(call_id) {
                        call_index.insert(call_id.to_string(), turn.tool_calls.len());
                        turn.tool_calls.push(ToolCall {
                            call_id: call_id.to_string(),
                            name: "ToolSearch".to_string(),
                            input: payload.get("arguments").cloned().unwrap_or(Value::Null),
                            output: Value::Null,
                            started_timestamp: timestamp.clone(),
                            ended_timestamp: String::new(),
                            assistant_index: current_assistant_index,
                            success: None,
                        });
                    }
                    continue;
                }
                let status = payload
                    .get("status")
                    .and_then(|value| value.as_str())
                    .unwrap_or("unknown");
                let call_id = payload
                    .get("call_id")
                    .and_then(|value| value.as_str())
                    .unwrap_or("")
                    .to_string();
                let query = payload
                    .get("arguments")
                    .and_then(|v| v.get("query"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let id = if call_id.is_empty() {
                    format!("tool-search-{}", turn.tool_calls.len() + 1)
                } else {
                    call_id
                };
                turn.tool_calls.push(ToolCall {
                    call_id: id,
                    name: "ToolSearch".to_string(),
                    input: payload.get("arguments").cloned().unwrap_or(Value::Null),
                    output: json!({"status": status, "query": query}),
                    started_timestamp: timestamp.clone(),
                    ended_timestamp: timestamp.clone(),
                    assistant_index: current_assistant_index,
                    success: Some(status == "completed"),
                });
            }
            "tool_search_output" => {
                let call_id = payload.get("call_id").and_then(Value::as_str).unwrap_or("");
                if let Some(call) = call_index
                    .get(call_id)
                    .and_then(|index| turn.tool_calls.get_mut(*index))
                    .filter(|call| call.name == "ToolSearch" && call.ended_timestamp.is_empty())
                {
                    call.ended_timestamp = timestamp.clone();
                    call.output =
                        json!({"status": payload.get("status"), "tools": payload.get("tools")});
                    call.success = payload
                        .get("status")
                        .and_then(Value::as_str)
                        .map(|status| status == "completed");
                }
            }
            "web_search_call" => {
                let status = payload
                    .get("status")
                    .and_then(|value| value.as_str())
                    .unwrap_or("unknown");
                let query = payload
                    .get("action")
                    .and_then(|value| value.get("query"))
                    .cloned()
                    .unwrap_or(Value::String(String::new()));
                turn.tool_calls.push(ToolCall {
                    call_id: format!("web-search-{}", turn.tool_calls.len() + 1),
                    name: "WebSearch".to_string(),
                    input: payload.get("action").cloned().unwrap_or(Value::Null),
                    output: json!({"status": status, "query": query}),
                    started_timestamp: timestamp.clone(),
                    ended_timestamp: timestamp.clone(),
                    assistant_index: current_assistant_index,
                    success: Some(status == "completed"),
                });
            }
            _ => {}
        }
    }

    if let Some(turn) = current_turn.and_then(finalize_turn) {
        turns.push(turn);
    }

    // Apply session-level subagent_source to all turns
    if let Some(ref src) = subagent_source {
        for turn in &mut turns {
            turn.subagent_source = Some(src.clone());
        }
    }
    if !session_system_instructions.is_empty() {
        for turn in &mut turns {
            turn.system_instructions = session_system_instructions.clone();
        }
    }
    let session_turn_trigger = session_turn_trigger.unwrap_or_default();
    for turn in &mut turns {
        turn.trigger = resolve_turn_trigger(None, session_turn_trigger);
    }
    apply_normalized_semantics(&mut turns, &normalized_turns);


    turns
}

fn parse_terminal_error(payload: &Value) -> Option<TerminalError> {
    let error = payload.get("error")?;
    let raw_message = error.get("message").and_then(Value::as_str).unwrap_or("");
    let nested = serde_json::from_str::<Value>(raw_message).ok();
    let nested_error = nested.as_ref().and_then(|value| value.get("error"));

    let raw_type = nested_error
        .and_then(|value| value.get("type"))
        .and_then(Value::as_str)
        .or_else(|| error.get("type").and_then(Value::as_str))
        .or_else(|| {
            error
                .get("codex_error_info")
                .and_then(Value::as_str)
                .filter(|value| *value != "other")
        })
        .unwrap_or("llm_api_error");
    let message = nested_error
        .and_then(|value| value.get("message"))
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .or_else(|| (!raw_message.trim().is_empty()).then_some(raw_message))
        .unwrap_or("LLM request failed");
    let status_code = nested
        .as_ref()
        .and_then(|value| value.get("status"))
        .and_then(Value::as_i64)
        .or_else(|| error.get("status").and_then(Value::as_i64));

    Some(TerminalError {
        error_type: normalize_terminal_error_type(raw_type),
        message: sanitize_terminal_error_message(message),
        status_code,
    })
}

fn normalize_terminal_error_type(raw: &str) -> String {
    let mut normalized = String::new();
    let mut previous_separator = false;
    for character in raw.chars().take(64) {
        if character.is_ascii_alphanumeric() {
            normalized.push(character.to_ascii_lowercase());
            previous_separator = false;
        } else if !previous_separator && !normalized.is_empty() {
            normalized.push('_');
            previous_separator = true;
        }
    }
    while normalized.ends_with('_') {
        normalized.pop();
    }
    if normalized.is_empty() {
        "llm_api_error".to_string()
    } else {
        normalized
    }
}

fn sanitize_terminal_error_message(raw: &str) -> String {
    const MAX_CHARS: usize = 512;
    let mut sanitized = Vec::new();
    let mut redact_next = false;
    for token in raw.split_whitespace() {
        let lower = token.to_ascii_lowercase();
        if redact_next {
            sanitized.push("[REDACTED]".to_string());
            redact_next = false;
            continue;
        }
        if lower == "bearer" || lower == "authorization:" {
            sanitized.push(token.to_string());
            redact_next = true;
            continue;
        }
        let sensitive_assignment = [
            "api_key=",
            "api-key=",
            "token=",
            "secret=",
            "authorization=",
        ]
        .iter()
        .find(|prefix| lower.starts_with(**prefix));
        if let Some(prefix) = sensitive_assignment {
            sanitized.push(format!("{}[REDACTED]", &token[..prefix.len()]));
        } else if lower.starts_with("sk-") {
            sanitized.push("[REDACTED]".to_string());
        } else {
            sanitized.push(token.to_string());
        }
    }
    let sanitized = sanitized.join(" ");
    if sanitized.chars().count() <= MAX_CHARS {
        return sanitized;
    }
    let head = sanitized.chars().take(MAX_CHARS).collect::<String>();
    format!("{head}...[truncated]")
}

pub(super) fn apply_normalized_semantics(
    turns: &mut [Turn],
    normalized_turns: &[NormalizedTurnSemantics],
) {
    for (turn_index, turn) in turns.iter_mut().enumerate() {
        let normalized = normalized_turns
            .iter()
            .find(|normalized| {
                !turn.turn_id.is_empty()
                    && !normalized.turn_id.is_empty()
                    && normalized.turn_id == turn.turn_id
            })
            .or_else(|| normalized_turns.get(turn_index));
        let Some(normalized) = normalized else {
            continue;
        };
        if turn.terminal_state == TurnTerminalState::Superseded
            && normalized.terminal_state == TurnTerminalState::Superseded
            && !normalized.terminal_at.is_empty()
        {
            turn.completed = false;
            turn.completion_timestamp =
                timestamp_max(&turn.completion_timestamp, &normalized.terminal_at);
        }

        let (assistants, mut tool_calls) =
            normalized.derived_turn_parts(normalized.native_usage_mode);
        if !assistants.is_empty() || normalized.native_usage_mode {
            turn.assistants = assistants;
        }
        if !normalized.callbacks.is_empty() {
            let legacy_success = turn
                .tool_calls
                .iter()
                .map(|tool| (tool.call_id.clone(), tool.success))
                .collect::<HashMap<_, _>>();
            for tool in &mut tool_calls {
                if tool.success.is_none() {
                    tool.success = legacy_success.get(&tool.call_id).copied().flatten();
                }
            }
            turn.tool_calls = tool_calls;
        }
    }
}
