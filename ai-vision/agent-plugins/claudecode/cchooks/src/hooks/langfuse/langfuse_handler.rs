use agentic_otel_common::otlp::OtelAttribute;

use crate::common::dirs;
use crate::common::hook::{
    extract_session_id, extract_transcript_path, find_latest_transcript, HookEvent,
};
use crate::common::logger::now_iso8601_pub;
use crate::common::otel::{
    build_compaction_export_request, build_otel_export_request_for_turn_with_full_context,
    compaction_parent_anchor_from_turn_export, inject_vcs_resource_attrs,
    post_otel_to_langfuse_once_with_context, CompactionParentAnchor, LangfuseAttemptContext,
    StoredTraceContext,
};
use crate::common::transcript::{
    build_turns, build_turns_detailed, classify_postable_turns,
    classify_postable_turns_at_session_end, inspect_transcript_source, read_new_jsonl_bounded,
    read_new_jsonl_limited_bounded, read_next_jsonl_record, stable_turn_id, CompactionEvent,
    TranscriptSourceEpoch, TRANSCRIPT_RECORD_HARD_LIMIT_BYTES,
};
use crate::hooks::langfuse::state::{
    checkpoint_turn_accounted_in_memory, commit_export_success_in_memory, get_int_field,
    get_session_state, get_str_field, get_string_array_field, get_vcs_for_cwd_from_state,
    record_pending_export_in_memory, SessionStateStore, StateTxnError, PARTIAL_EXPORT_REASON,
};
use crate::hooks::timing::{load_subagent_timing_from_state, load_timing_from_state};
use crate::hooks::{
    BoolSetting, CommonContext, HandlerError, HookHandler, RetryPolicy, StopBudget, StopOutcome,
    DEFAULT_STOP_TRANSCRIPT_SETTLE_MS, STOP_RECOVERY_TRANSCRIPT_SCAN_MAX_BYTES,
    STOP_TERMINAL_POLL_INTERVAL_MS,
};

const TRANSCRIPT_CAPTURE_ENV: &str = "CCHOOKS_TRANSCRIPT_CAPTURE";

pub struct LangfuseHandler {
    stop_transcript_settle_timeout: std::time::Duration,
}

struct TranscriptIngestAckGuard<'a> {
    session_id: String,
    receipt: Option<&'a str>,
    logger: &'a crate::common::logger::Logger,
    acknowledged: bool,
}

impl TranscriptIngestAckGuard<'_> {
    fn acknowledge(&mut self) {
        if self.acknowledged {
            return;
        }
        self.acknowledged = true;
        if let Err(error) =
            crate::hooks::acknowledge_transcript_ingest(&self.session_id, self.receipt)
        {
            self.logger.warn(&format!(
                "Transcript ingest acknowledgement failed; export continues fail-open: {error}"
            ));
        }
    }
}

impl Drop for TranscriptIngestAckGuard<'_> {
    fn drop(&mut self) {
        self.acknowledge();
    }
}

impl Default for LangfuseHandler {
    fn default() -> Self {
        Self::new()
    }
}

impl LangfuseHandler {
    pub fn new() -> Self {
        let settle_ms = crate::common::env::var("CCHOOKS_STOP_TRANSCRIPT_SETTLE_MS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(DEFAULT_STOP_TRANSCRIPT_SETTLE_MS);
        Self {
            stop_transcript_settle_timeout: std::time::Duration::from_millis(settle_ms),
        }
    }
}

impl HookHandler for LangfuseHandler {
    fn name(&self) -> &str {
        "langfuse"
    }

    fn supported_events(&self) -> &[HookEvent] {
        &[
            HookEvent::Stop,
            HookEvent::StopFailure,
            HookEvent::SessionEnd,
        ]
    }

    fn handle(
        &self,
        event: &HookEvent,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        let mut transcript_ingest_ack = TranscriptIngestAckGuard {
            session_id: extract_session_id(payload),
            receipt: context.transcript_ingest_receipt.as_deref(),
            logger: &context.logger,
            acknowledged: false,
        };
        let config = context
            .langfuse_config
            .as_ref()
            .ok_or_else(|| HandlerError {
                message: "Missing Langfuse configuration".to_string(),
            })?;

        if crate::hooks::active_stop_should_skip(payload) {
            context.set_stop_outcome(StopOutcome::IntentionallySkipped);
            context
                .logger
                .debug("stop_hook_active=true, skipping to avoid infinite loop");
            return Ok(());
        }

        let payload_session = extract_session_id(payload);
        let payload_transcript = extract_transcript_path(payload);
        let (session_id, transcript_path) =
            if !payload_session.is_empty() && !payload_transcript.is_empty() {
                (payload_session, payload_transcript)
            } else {
                match find_latest_transcript(&context.projects_dir) {
                    Some(latest) => (latest.session_id, latest.transcript_path),
                    None => {
                        context.set_stop_outcome(StopOutcome::IntentionallySkipped);
                        context.logger.warn("No transcript found");
                        return Ok(());
                    }
                }
            };
        if session_id.is_empty() || transcript_path.is_empty() {
            context.set_stop_outcome(StopOutcome::IntentionallySkipped);
            context
                .logger
                .warn("Empty session_id or transcript_path, skipping");
            return Ok(());
        }

        let canonical_path = std::path::Path::new(&transcript_path)
            .canonicalize()
            .unwrap_or_else(|_| std::path::PathBuf::from(&transcript_path))
            .to_string_lossy()
            .to_string();
        context
            .logger
            .info(&format!("Processing transcript={canonical_path}"));

        let Some(budget) = context.stop_budget else {
            context.set_stop_outcome(StopOutcome::deferred("hook_budget_missing"));
            context
                .logger
                .error("Hook budget missing; leaving transcript retryable");
            return Ok(());
        };
        let scheduled_recovery = payload
            .get("cchooks_recovered_stop_intent")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false);
        if budget.processing_time_remaining().is_none() {
            context.set_stop_outcome(StopOutcome::deferred("hook_budget_exhausted"));
            context
                .logger
                .warn("Hook budget exhausted before state lock; leaving transcript retryable");
            persist_preprocessing_budget_barrier(context, &session_id, &canonical_path, budget);
            return Ok(());
        }
        let lock_timeout_ms = budget
            .processing_time_remaining()
            .map(|remaining| remaining.as_millis().min(2_000) as u64)
            .unwrap_or(0);
        let state_store =
            SessionStateStore::new(context.state_file.clone(), &session_id, lock_timeout_ms);
        let turn_window =
            validated_turn_window(&canonical_path, context.turn_seed.as_ref(), &context.logger);
        let state_snapshot = state_store.update(|state| {
            if let Some(turn_window) = turn_window.as_ref() {
                apply_turn_window(state, &session_id, &canonical_path, turn_window)?;
            }
            Ok(state.clone())
        });
        let state = match state_snapshot {
            Ok(state) => state,
            Err(error) => {
                context.set_stop_outcome(StopOutcome::deferred("state_snapshot_unavailable"));
                context.logger.warn(&format!(
                    "State snapshot unavailable; leaving transcript retryable: {error}"
                ));
                return Ok(());
            }
        };
        let session_state = get_session_state(&state, &session_id).cloned();
        let prev_offset = session_state
            .as_ref()
            .map(|session| get_int_field(session, "offset"))
            .unwrap_or(0);
        let prev_buffer = session_state
            .as_ref()
            .map(|session| get_str_field(session, "buffer").to_string())
            .unwrap_or_default();
        let posted_ids = session_state
            .as_ref()
            .map(|session| get_string_array_field(session, "posted_turn_ids"))
            .unwrap_or_default();
        let posted_compaction_ids = session_state
            .as_ref()
            .map(|session| get_string_array_field(session, "posted_compaction_ids"))
            .unwrap_or_default();
        if !posted_ids.is_empty() || !posted_compaction_ids.is_empty() {
            let checkpoint_path = session_state
                .as_ref()
                .and_then(|session| session.get("transcript_path"))
                .and_then(serde_json::Value::as_str);
            if checkpoint_path != Some(canonical_path.as_str()) {
                context.set_stop_outcome(StopOutcome::deferred("checkpoint_source_mismatch"));
                context
                    .logger
                    .error("Checkpoint source-window mismatch; refusing export and state mutation");
                return Ok(());
            }
        }
        if budget.processing_time_remaining().is_none() {
            context.set_stop_outcome(StopOutcome::deferred("hook_budget_exhausted"));
            persist_barrier(
                &state_store,
                &session_id,
                "hook_budget_exhausted",
                &canonical_path,
                context,
            );
            return Ok(());
        }

        let (
            messages,
            current_offset,
            current_buffer,
            recovery_scan_has_more,
            recovery_scan_terminal,
        ) = if scheduled_recovery {
            read_scheduled_recovery_slice(
                std::path::Path::new(&canonical_path),
                session_state.as_ref(),
                prev_offset,
                &prev_buffer,
                context.stop_transcript_end_offset,
            )
        } else {
            let (messages, offset, buffer) = poll_transcript_until_terminal(
                std::path::Path::new(&canonical_path),
                prev_offset,
                &prev_buffer,
                event,
                TranscriptPollWindow {
                    max_wait: self.stop_transcript_settle_timeout,
                    end_offset: context.stop_transcript_end_offset,
                    turn_seed: context.turn_seed.as_ref(),
                },
                budget,
                &context.logger,
            );
            (messages, offset, buffer, false, None)
        };

        if let Some(reason) = recovery_scan_terminal {
            context.set_stop_outcome(StopOutcome::deferred(reason));
            persist_barrier(&state_store, &session_id, reason, &canonical_path, context);
            return Ok(());
        }

        if scheduled_recovery && current_buffer.len() > TRANSCRIPT_RECORD_HARD_LIMIT_BYTES {
            context.set_stop_outcome(StopOutcome::deferred("record_too_large"));
            persist_barrier(
                &state_store,
                &session_id,
                "record_too_large",
                &canonical_path,
                context,
            );
            return Ok(());
        }

        transcript_ingest_ack.acknowledge();

        if messages.is_empty() && posted_ids.is_empty() && posted_compaction_ids.is_empty() {
            if scheduled_recovery && recovery_scan_has_more {
                checkpoint_recovery_scan(
                    &state_store,
                    &session_id,
                    current_offset,
                    &current_buffer,
                    &[],
                )?;
                context.set_stop_outcome(StopOutcome::deferred("recovery_scan_pending"));
                return Ok(());
            }
            if !current_buffer.is_empty() {
                if scheduled_recovery {
                    checkpoint_recovery_scan(
                        &state_store,
                        &session_id,
                        current_offset,
                        &current_buffer,
                        &[],
                    )?;
                }
                context.set_stop_outcome(StopOutcome::deferred("incomplete_tail"));
                persist_barrier(
                    &state_store,
                    &session_id,
                    "incomplete_tail",
                    &canonical_path,
                    context,
                );
                return Ok(());
            }
            context.set_stop_outcome(StopOutcome::Completed);
            context.logger.debug("No new messages");
            return Ok(());
        }

        if !scheduled_recovery {
            copy_transcript_to_session_dir(
                &dirs::transcript_dir(),
                &session_id,
                &canonical_path,
                &context.logger,
            );
        }

        let turn_build = build_turns_detailed(&messages, None);
        let turns = turn_build.turns;
        let mut compaction_candidates = turn_build.unattached_compactions;
        let (postable_count, incomplete_tail) = if matches!(event, HookEvent::SessionEnd) {
            classify_postable_turns_at_session_end(&messages, &turns)
        } else {
            classify_postable_turns(&messages, &turns)
        };
        compaction_candidates.extend(
            turns
                .iter()
                .take(postable_count)
                .flat_map(|turn| turn.compactions.iter().cloned()),
        );

        let cwd = payload
            .get("cwd")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("");
        let vcs_info = get_vcs_for_cwd_from_state(&state, &session_id, cwd);
        let timing = load_timing_from_state(&state, &session_id);
        let subagent_timing = load_subagent_timing_from_state(&state, &session_id);
        // Persisted realtime state, not the current process environment, owns
        // finalization. This also drains sessions whose realtime flag changed
        // between tool hooks and Stop/SessionEnd.
        let realtime_contexts =
            crate::hooks::langfuse::realtime_state::contexts(&state, &session_id);
        let accounted_child_max_end_times =
            crate::hooks::langfuse::realtime_state::accounted_child_max_end_times(
                &state,
                &session_id,
            );
        let mut trace_context_history =
            stored_trace_contexts_from_realtime(&realtime_contexts, &accounted_child_max_end_times);
        trace_context_history.extend(load_trace_context_history(&state, &session_id));
        let matched_realtime_context_ids =
            matched_realtime_context_ids(&realtime_contexts, &turns[..postable_count]);
        let window_end_offset = (current_offset > prev_offset).then_some(current_offset);

        for (ordinal, turn) in turns.iter().take(postable_count).enumerate() {
            let turn_id = stable_turn_id(turn, &session_id, ordinal);
            'plan_turn: loop {
                if !budget.can_start_turn() {
                    context.set_stop_outcome(StopOutcome::deferred("hook_budget_exhausted"));
                    persist_barrier(
                        &state_store,
                        &session_id,
                        "hook_budget_exhausted",
                        &canonical_path,
                        context,
                    );
                    return Ok(());
                }

                let preparation = match prepare_turn_export(
                    &state_store,
                    &session_id,
                    &turn_id,
                    &canonical_path,
                    window_end_offset,
                ) {
                    Ok(preparation) => preparation,
                    Err(error) => {
                        context.set_stop_outcome(StopOutcome::deferred("turn_preparation_failed"));
                        context.logger.error(&format!(
                            "Turn preparation failed; refusing to POST id={turn_id}: {error}"
                        ));
                        return Ok(());
                    }
                };
                let TurnPreparation::Send {
                    persisted_count,
                    turn_number,
                    persisted_compaction_count,
                } = preparation
                else {
                    break 'plan_turn;
                };

                let mut turn = turn.clone();
                for (compaction_ordinal, compaction) in turn.compactions.iter_mut().enumerate() {
                    compaction.sequence =
                        (persisted_compaction_count + compaction_ordinal + 1) as u32;
                }

                let (resolved_trace_context, trace_mismatch) = resolve_stop_trace_context(
                    &turn,
                    &turn_id,
                    ordinal + 1 == postable_count,
                    incomplete_tail,
                    &trace_context_history,
                    context.turn_seed.as_ref(),
                );
                if let Some((tool_trace_id, seed_trace_id)) = trace_mismatch {
                    context.logger.warn(&format!(
                        "Stop trace context reconciled tool/seed mismatch: turn_id={turn_id} tool_trace_id={tool_trace_id} seed_trace_id={seed_trace_id}"
                    ));
                }
                let now = now_iso8601_pub();
                let mut body = build_otel_export_request_for_turn_with_full_context(
                    &turn,
                    turn_number,
                    &session_id,
                    &context.username,
                    config,
                    &now,
                    &context.hook_version,
                    cwd,
                    &canonical_path,
                    &timing,
                    &subagent_timing,
                    resolved_trace_context.as_ref(),
                );
                crate::common::otel::stabilize_turn_export_identities_with_selected_trace(
                    &mut body,
                    &turn_id,
                    &trace_context_history,
                    resolved_trace_context
                        .as_ref()
                        .map(|resolved| resolved.trace_id.as_str()),
                );
                let anchor = match compaction_parent_anchor_from_turn_export(
                    &body,
                    &turn_id,
                    turn_number,
                    &canonical_path,
                    u64::try_from(current_offset).unwrap_or(0),
                ) {
                    Ok(anchor) => anchor,
                    Err(error) => {
                        context.set_stop_outcome(StopOutcome::deferred(
                            "compaction_anchor_build_failed",
                        ));
                        context.logger.error(&format!(
                            "Turn export missing compaction parent anchor id={turn_id}: {error}"
                        ));
                        return Ok(());
                    }
                };
                let export_checkpoint = TurnExportCheckpoint {
                    anchor,
                    compaction_ids: turn
                        .compactions
                        .iter()
                        .map(|compaction| compaction.identity.clone())
                        .collect(),
                    expected_compaction_count: persisted_compaction_count,
                };
                if let Some(vcs) = vcs_info.as_ref() {
                    inject_vcs_resource_attrs(&mut body, &vcs.remote_url, &vcs.branch);
                }

                let Some(timeout) = budget.http_timeout() else {
                    context.set_stop_outcome(StopOutcome::deferred("hook_budget_exhausted"));
                    persist_barrier(
                        &state_store,
                        &session_id,
                        "hook_budget_exhausted",
                        &canonical_path,
                        context,
                    );
                    return Ok(());
                };

                if context.retry_policy == RetryPolicy::AtMostOnce {
                    match claim_at_most_once_turn(
                        &state_store,
                        &session_id,
                        &turn_id,
                        &canonical_path,
                        window_end_offset,
                        persisted_count,
                        Some(&export_checkpoint),
                    ) {
                        Ok(AtMostOnceClaim::Skip) => break 'plan_turn,
                        Ok(AtMostOnceClaim::Retry) => continue 'plan_turn,
                        Ok(AtMostOnceClaim::Claimed) => {
                            context.logger.info(&format!(
                                "At-most-once claim checkpointed; proceeding to POST id={turn_id} number={turn_number}"
                            ));
                        }
                        Err(error) => {
                            context.set_stop_outcome(StopOutcome::deferred(
                                "at_most_once_checkpoint_failed",
                            ));
                            context.logger.error(&format!(
                                "At-most-once checkpoint failed; refusing to POST id={turn_id}: {error}"
                            ));
                            return Ok(());
                        }
                    }
                }

                context.logger.info(&format!(
                    "Exporting turn id={turn_id} number={turn_number} timeout={}ms",
                    timeout.as_millis()
                ));
                if let Err(error) = post_otel_to_langfuse_once_with_context(
                    config,
                    &body,
                    &context.logger,
                    persisted_count == 0,
                    &session_id,
                    &canonical_path,
                    timeout,
                    LangfuseAttemptContext::stop(&turn_id, turn_number),
                ) {
                    context.set_stop_outcome(StopOutcome::deferred("otel_export_failed"));
                    let error = error.to_string();
                    context.logger.error(&format!(
                        "Turn export failed id={turn_id} number={turn_number}: {error}"
                    ));
                    if let Some(metrics) = context.metrics.as_ref() {
                        metrics.borrow_mut().inc(
                            "aivision.agentic.export_error",
                            "Export error",
                            "{count}",
                            vec![OtelAttribute::string("error.type", &error)],
                        );
                    }
                    if context.retry_policy == RetryPolicy::AtMostOnce {
                        context.logger.warn(&format!(
                            "Dropping failed at-most-once attempt without retry id={turn_id} number={turn_number}"
                        ));
                        break 'plan_turn;
                    }
                    persist_barrier(
                        &state_store,
                        &session_id,
                        "otel_export_failed",
                        &canonical_path,
                        context,
                    );
                    return Ok(());
                }

                if context.retry_policy == RetryPolicy::AtMostOnce {
                    if let Err(error) = checkpoint_successful_turn_anchor(
                        &state_store,
                        &session_id,
                        &turn_id,
                        &canonical_path,
                        &export_checkpoint.anchor,
                    ) {
                        context.set_stop_outcome(StopOutcome::deferred(
                            "successful_turn_anchor_checkpoint_failed",
                        ));
                        context.logger.error(&format!(
                            "At-most-once Turn POST succeeded but parent anchor checkpoint failed id={turn_id}: {error}"
                        ));
                        return Ok(());
                    }
                    context.logger.info(&format!(
                        "At-most-once POST accepted after pre-checkpoint id={turn_id} number={turn_number}"
                    ));
                    break 'plan_turn;
                }

                if let Err(error) = checkpoint_accepted_turn(
                    &state_store,
                    &session_id,
                    &turn_id,
                    &canonical_path,
                    window_end_offset,
                    Some(&export_checkpoint),
                ) {
                    context.set_stop_outcome(StopOutcome::deferred("turn_checkpoint_failed"));
                    context.logger.error(&format!(
                        "Turn accepted but checkpoint failed; turn may repeat: {error}"
                    ));
                    return Ok(());
                }
                break 'plan_turn;
            }
        }

        // Retry every compaction visible in this window, including events that
        // the parser attached to a Turn already checkpointed by an earlier
        // attempt. The compaction ledger makes events already included in a
        // successful Turn export cheap no-ops.
        for parsed_compaction in &compaction_candidates {
            'plan_compaction: loop {
                if !budget.can_start_turn() {
                    context.set_stop_outcome(StopOutcome::deferred("hook_budget_exhausted"));
                    persist_barrier(
                        &state_store,
                        &session_id,
                        "hook_budget_exhausted",
                        &canonical_path,
                        context,
                    );
                    return Ok(());
                }

                let preparation = match prepare_compaction_export(
                    &state_store,
                    &session_id,
                    &parsed_compaction.identity,
                    &canonical_path,
                    window_end_offset,
                    context.retry_policy == RetryPolicy::AtMostOnce,
                ) {
                    Ok(preparation) => preparation,
                    Err(error) => {
                        context.set_stop_outcome(StopOutcome::deferred(
                            "compaction_preparation_failed",
                        ));
                        context.logger.error(&format!(
                            "Compaction preparation failed; retaining transcript cursor id={}: {error}",
                            parsed_compaction.identity
                        ));
                        persist_barrier(
                            &state_store,
                            &session_id,
                            "compaction_preparation_failed",
                            &canonical_path,
                            context,
                        );
                        return Ok(());
                    }
                };
                if let CompactionPreparation::Drop {
                    persisted_count,
                    sequence,
                } = &preparation
                {
                    match claim_at_most_once_compaction(
                        &state_store,
                        &session_id,
                        &parsed_compaction.identity,
                        &canonical_path,
                        window_end_offset,
                        *persisted_count,
                    ) {
                        Ok(AtMostOnceClaim::Skip) => break 'plan_compaction,
                        Ok(AtMostOnceClaim::Retry) => continue 'plan_compaction,
                        Ok(AtMostOnceClaim::Claimed) => {
                            context.logger.warn(&format!(
                                "Dropping at-most-once compaction without a successfully exported parent id={} sequence={sequence}",
                                parsed_compaction.identity
                            ));
                            break 'plan_compaction;
                        }
                        Err(error) => {
                            context.set_stop_outcome(StopOutcome::deferred(
                                "at_most_once_compaction_drop_checkpoint_failed",
                            ));
                            context.logger.error(&format!(
                                "Failed to checkpoint dropped at-most-once compaction id={}: {error}",
                                parsed_compaction.identity
                            ));
                            return Ok(());
                        }
                    }
                }
                let CompactionPreparation::Send {
                    anchor,
                    persisted_count,
                    sequence,
                } = preparation
                else {
                    break 'plan_compaction;
                };

                let mut compaction: CompactionEvent = parsed_compaction.clone();
                compaction.sequence = sequence;
                let mut body = match build_compaction_export_request(
                    &anchor,
                    &compaction,
                    &session_id,
                    &context.username,
                    &context.hook_version,
                ) {
                    Ok(body) => body,
                    Err(error) => {
                        context.set_stop_outcome(StopOutcome::deferred(
                            "compaction_export_build_failed",
                        ));
                        context.logger.error(&format!(
                            "Compaction export build failed id={}: {error}",
                            compaction.identity
                        ));
                        return Ok(());
                    }
                };
                if let Some(vcs) = vcs_info.as_ref() {
                    inject_vcs_resource_attrs(&mut body, &vcs.remote_url, &vcs.branch);
                }

                let Some(timeout) = budget.http_timeout() else {
                    context.set_stop_outcome(StopOutcome::deferred("hook_budget_exhausted"));
                    persist_barrier(
                        &state_store,
                        &session_id,
                        "hook_budget_exhausted",
                        &canonical_path,
                        context,
                    );
                    return Ok(());
                };

                if context.retry_policy == RetryPolicy::AtMostOnce {
                    match claim_at_most_once_compaction(
                        &state_store,
                        &session_id,
                        &compaction.identity,
                        &canonical_path,
                        window_end_offset,
                        persisted_count,
                    ) {
                        Ok(AtMostOnceClaim::Skip) => break 'plan_compaction,
                        Ok(AtMostOnceClaim::Retry) => continue 'plan_compaction,
                        Ok(AtMostOnceClaim::Claimed) => {
                            context.logger.info(&format!(
                                "At-most-once compaction claim checkpointed id={} sequence={sequence}",
                                compaction.identity
                            ));
                        }
                        Err(error) => {
                            context.set_stop_outcome(StopOutcome::deferred(
                                "at_most_once_compaction_checkpoint_failed",
                            ));
                            context.logger.error(&format!(
                                "At-most-once compaction checkpoint failed; refusing POST id={}: {error}",
                                compaction.identity
                            ));
                            return Ok(());
                        }
                    }
                }

                context.logger.info(&format!(
                    "Exporting late compaction id={} sequence={sequence} tokens_before={} tokens_after={} timeout={}ms",
                    compaction.identity,
                    compaction.pre_tokens,
                    compaction.post_tokens,
                    timeout.as_millis()
                ));
                if let Err(error) = post_otel_to_langfuse_once_with_context(
                    config,
                    &body,
                    &context.logger,
                    false,
                    &session_id,
                    &canonical_path,
                    timeout,
                    LangfuseAttemptContext::stop(&anchor.turn_id, anchor.turn_number),
                ) {
                    context.set_stop_outcome(StopOutcome::deferred("compaction_export_failed"));
                    let error = error.to_string();
                    context.logger.error(&format!(
                        "Compaction export failed id={} sequence={sequence}: {error}",
                        compaction.identity
                    ));
                    if let Some(metrics) = context.metrics.as_ref() {
                        metrics.borrow_mut().inc(
                            "aivision.agentic.export_error",
                            "Export error",
                            "{count}",
                            vec![OtelAttribute::string("error.type", &error)],
                        );
                    }
                    if context.retry_policy == RetryPolicy::AtMostOnce {
                        context.logger.warn(&format!(
                            "Dropping failed at-most-once compaction attempt id={}",
                            compaction.identity
                        ));
                        break 'plan_compaction;
                    }
                    persist_barrier(
                        &state_store,
                        &session_id,
                        "compaction_export_failed",
                        &canonical_path,
                        context,
                    );
                    return Ok(());
                }

                if context.retry_policy == RetryPolicy::AtMostOnce {
                    break 'plan_compaction;
                }
                if let Err(error) = checkpoint_accepted_compaction(
                    &state_store,
                    &session_id,
                    &compaction.identity,
                    &canonical_path,
                    window_end_offset,
                    persisted_count,
                ) {
                    context.set_stop_outcome(StopOutcome::deferred("compaction_checkpoint_failed"));
                    context.logger.error(&format!(
                        "Compaction accepted but checkpoint failed; compaction may repeat: {error}"
                    ));
                    return Ok(());
                }
                break 'plan_compaction;
            }
        }

        if scheduled_recovery && recovery_scan_has_more {
            let pending_messages = if incomplete_tail {
                retain_incomplete_turn_messages(&messages)
            } else {
                Vec::new()
            };
            checkpoint_recovery_scan(
                &state_store,
                &session_id,
                current_offset,
                &current_buffer,
                &pending_messages,
            )?;
            context.set_stop_outcome(StopOutcome::deferred("recovery_scan_pending"));
            return Ok(());
        }

        if incomplete_tail {
            if scheduled_recovery {
                checkpoint_recovery_scan(
                    &state_store,
                    &session_id,
                    current_offset,
                    &current_buffer,
                    &retain_incomplete_turn_messages(&messages),
                )?;
            }
            context.set_stop_outcome(StopOutcome::deferred("incomplete_tail"));
            persist_barrier(
                &state_store,
                &session_id,
                "incomplete_tail",
                &canonical_path,
                context,
            );
            return Ok(());
        }

        let archive_path = timing_archive_path(context.debug_enabled, &session_id, &canonical_path);
        let commit_result = state_store.update(|latest_state| {
            let archive_state = archive_path.as_ref().map(|_| latest_state.clone());
            let committed = commit_export_success_in_memory(
                latest_state,
                &session_id,
                current_offset,
                &current_buffer,
                &now_iso8601_pub(),
                Some(matched_realtime_context_ids.as_slice()),
            );
            let turn_count = get_session_state(latest_state, &session_id)
                .map(|session| get_int_field(session, "turn_count"))
                .unwrap_or(0);
            Ok((
                committed,
                turn_count,
                committed.then_some(archive_state).flatten(),
            ))
        });
        let (committed, turn_count, archive_state) = match commit_result {
            Ok(result) => result,
            Err(error) => {
                context.set_stop_outcome(StopOutcome::deferred("final_cursor_commit_failed"));
                context.logger.error(&format!(
                    "All turns checkpointed but final cursor commit failed; retry will finalize: {error}"
                ));
                return Ok(());
            }
        };
        if let (Some(path), Some(mut archive_state)) = (archive_path, archive_state) {
            crate::hooks::timing::cleanup_timing(&mut archive_state, &session_id, Some(&path));
        }
        context.logger.info(&format!(
            "Progressive export finalized committed={committed} total_turns={turn_count} offset={current_offset}"
        ));
        context.set_stop_outcome(StopOutcome::Completed);
        Ok(())
    }
}

fn persist_preprocessing_budget_barrier(
    context: &CommonContext,
    session_id: &str,
    transcript_path: &str,
    budget: StopBudget,
) {
    let lock_timeout_ms = budget.remaining().as_millis().min(50) as u64;
    let state_store =
        SessionStateStore::new(context.state_file.clone(), session_id, lock_timeout_ms);
    persist_barrier(
        &state_store,
        session_id,
        "hook_budget_exhausted",
        transcript_path,
        context,
    );
}

fn read_scheduled_recovery_slice(
    path: &std::path::Path,
    session_state: Option<&serde_json::Value>,
    committed_offset: i64,
    committed_buffer: &str,
    snapshot_end_offset: Option<u64>,
) -> (
    Vec<serde_json::Value>,
    i64,
    String,
    bool,
    Option<&'static str>,
) {
    let recovery = session_state.and_then(|session| session.get("stop_recovery"));
    let mut messages = recovery
        .and_then(|record| record.get("pending_messages"))
        .and_then(serde_json::Value::as_array)
        .cloned()
        .unwrap_or_default();
    let offset = recovery
        .and_then(|record| record.get("scan_offset"))
        .and_then(serde_json::Value::as_i64)
        .unwrap_or(committed_offset);
    let buffer = recovery
        .and_then(|record| record.get("scan_buffer"))
        .and_then(serde_json::Value::as_str)
        .unwrap_or(committed_buffer);

    let scanning_snapshot = snapshot_end_offset.is_none_or(|end| (offset.max(0) as u64) < end);
    if scanning_snapshot {
        let (batch, next_offset, next_buffer, has_more, terminal) = read_new_jsonl_limited_bounded(
            path,
            offset,
            buffer,
            STOP_RECOVERY_TRANSCRIPT_SCAN_MAX_BYTES,
            snapshot_end_offset,
        );
        messages.extend(batch);
        return (messages, next_offset, next_buffer, has_more, terminal);
    }

    // The original Stop snapshot has been consumed. Read at most the one JSONL
    // record needed to complete its tail, and refuse to cross into a later
    // user turn.
    let (batch, next_offset, next_buffer) = read_next_jsonl_record(path, offset, buffer);
    let already_has_user_turn = messages.iter().any(is_non_meta_user_message);
    let reached_next_user = already_has_user_turn && batch.iter().any(is_non_meta_user_message);
    if reached_next_user {
        return (messages, offset, buffer.to_string(), false, None);
    }
    messages.extend(batch);
    (messages, next_offset, next_buffer, false, None)
}

fn is_non_meta_user_message(message: &serde_json::Value) -> bool {
    message
        .get("message")
        .and_then(|value| value.get("role"))
        .and_then(serde_json::Value::as_str)
        == Some("user")
        && !crate::common::transcript::is_tool_result(message)
        && !message
            .get("isMeta")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false)
}

fn retain_incomplete_turn_messages(messages: &[serde_json::Value]) -> Vec<serde_json::Value> {
    messages
        .iter()
        .rposition(is_non_meta_user_message)
        .map(|start| messages[start..].to_vec())
        .unwrap_or_else(|| messages.to_vec())
}

fn scan_record_boundary(scan_offset: i64, scan_buffer: &str) -> i64 {
    scan_offset.saturating_sub(scan_buffer.len() as i64)
}

fn checkpoint_recovery_scan(
    state_store: &SessionStateStore,
    session_id: &str,
    scan_offset: i64,
    scan_buffer: &str,
    pending_messages: &[serde_json::Value],
) -> Result<(), String> {
    state_store
        .update(|state| {
            let root = state
                .as_object_mut()
                .ok_or_else(|| StateTxnError::Schema("state root must be an object".to_string()))?;
            let session = root
                .entry(session_id.to_string())
                .or_insert_with(|| serde_json::json!({}));
            let session = session.as_object_mut().ok_or_else(|| {
                StateTxnError::Schema("session state must be an object".to_string())
            })?;
            let recovery = session
                .entry("stop_recovery".to_string())
                .or_insert_with(|| serde_json::json!({}));
            let recovery = recovery.as_object_mut().ok_or_else(|| {
                StateTxnError::Schema("stop recovery state must be an object".to_string())
            })?;
            recovery.insert("scan_offset".to_string(), serde_json::json!(scan_offset));
            recovery.insert(
                "scan_record_boundary".to_string(),
                serde_json::json!(scan_record_boundary(scan_offset, scan_buffer)),
            );
            recovery.insert("scan_buffer".to_string(), serde_json::json!(scan_buffer));
            recovery.insert(
                "pending_messages".to_string(),
                serde_json::json!(pending_messages),
            );
            Ok(())
        })
        .map_err(|error| format!("checkpoint Stop recovery scan: {error}"))
}

#[derive(Clone, Copy)]
struct TranscriptPollWindow<'a> {
    max_wait: std::time::Duration,
    end_offset: Option<u64>,
    turn_seed: Option<&'a crate::hooks::turn_seed::TurnSeed>,
}

fn poll_transcript_until_terminal(
    path: &std::path::Path,
    offset: i64,
    buffer: &str,
    event: &HookEvent,
    window: TranscriptPollWindow<'_>,
    budget: StopBudget,
    logger: &crate::common::logger::Logger,
) -> (Vec<serde_json::Value>, i64, String) {
    const POLL_INTERVAL: std::time::Duration =
        std::time::Duration::from_millis(STOP_TERMINAL_POLL_INTERVAL_MS);

    let poll_for_terminal = matches!(event, HookEvent::Stop | HookEvent::StopFailure);
    let poll_timeout = budget
        .processing_time_remaining()
        .unwrap_or_default()
        .min(window.max_wait);
    let deadline = std::time::Instant::now() + poll_timeout;
    let mut current_offset = offset;
    let mut current_buffer = buffer.to_string();
    let mut messages = Vec::new();
    let mut poll_count = 0u32;
    let mut initial_snapshot_consumed = window.end_offset.is_none();

    loop {
        let reading_growth = window.end_offset.is_some() && initial_snapshot_consumed;
        let (new_messages, new_offset, new_buffer) = if reading_growth {
            read_next_jsonl_record(path, current_offset, &current_buffer)
        } else {
            initial_snapshot_consumed = true;
            read_new_jsonl_bounded(path, current_offset, &current_buffer, window.end_offset)
        };
        let already_has_user_turn = !build_turns(&messages, None).is_empty();
        let growth_reached_next_user = reading_growth
            && already_has_user_turn
            && new_messages.iter().any(|message| {
                message
                    .get("message")
                    .and_then(|value| value.get("role"))
                    .and_then(serde_json::Value::as_str)
                    == Some("user")
                    && !crate::common::transcript::is_tool_result(message)
                    && !message
                        .get("isMeta")
                        .and_then(serde_json::Value::as_bool)
                        .unwrap_or(false)
            });
        if growth_reached_next_user {
            logger.warn(
                "Stop transcript growth reached the next user Turn; preserving event boundary",
            );
            break;
        }
        messages.extend(new_messages);
        current_offset = new_offset;
        current_buffer = new_buffer;

        let turns = build_turns(&messages, None);
        let (postable_count, incomplete_tail) = classify_postable_turns(&messages, &turns);
        let target_terminal_seen = window.turn_seed.map_or_else(
            || !turns.is_empty() && !incomplete_tail,
            |seed| {
                turns.iter().take(postable_count).any(|turn| {
                    turn.chat_id.as_deref() == Some(seed.turn_id.as_str())
                        || seed.prompt_digest.as_deref()
                            == Some(
                                crate::hooks::turn_seed::prompt_digest(&turn.user_text).as_str(),
                            )
                            && prompt_times_are_close(&seed.created_at, &turn.user_timestamp)
                })
            },
        );
        if target_terminal_seen
            || !poll_for_terminal
            || !budget.can_start_turn()
            || std::time::Instant::now() >= deadline
        {
            if poll_count > 0 {
                logger.debug(&format!(
                    "Transcript terminal poll finished after {} checks: target_terminal_seen={}",
                    poll_count, target_terminal_seen
                ));
            }
            break;
        }

        let sleep_for = deadline
            .saturating_duration_since(std::time::Instant::now())
            .min(POLL_INTERVAL);
        if sleep_for.is_zero() {
            break;
        }
        poll_count += 1;
        logger.debug(&format!(
            "Transcript terminal not visible; poll={} waiting {}ms",
            poll_count,
            sleep_for.as_millis()
        ));
        std::thread::sleep(sleep_for);
    }

    (messages, current_offset, current_buffer)
}

#[cfg(test)]
mod transcript_poll_tests {
    use super::*;
    use std::io::Write;
    use std::time::{Duration, Instant};

    fn budget() -> StopBudget {
        StopBudget::new(
            Instant::now(),
            Duration::from_secs(3),
            Duration::from_secs(1),
            Duration::from_millis(100),
        )
    }

    #[test]
    fn stop_handler_event_contract_is_unchanged() {
        assert_eq!(
            LangfuseHandler::new().supported_events(),
            &[
                HookEvent::Stop,
                HookEvent::StopFailure,
                HookEvent::SessionEnd,
            ]
        );
    }

    #[test]
    fn stop_poll_ignores_zero_usage_chunk_and_captures_late_terminal() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("session.jsonl");
        let initial = r#"{"type":"user","uuid":"turn-1","message":{"role":"user","content":"first"},"timestamp":"2026-07-13T00:00:00.000Z"}
{"type":"assistant","message":{"role":"assistant","id":"msg-1","content":[{"type":"text","text":"one"}],"stop_reason":"end_turn"},"timestamp":"2026-07-13T00:00:01.000Z"}
{"type":"user","uuid":"turn-2","message":{"role":"user","content":"second"},"timestamp":"2026-07-13T00:00:02.000Z"}
{"type":"assistant","message":{"role":"assistant","id":"msg-2","model":"GLM-5.2","content":[{"type":"text","text":"partial"}],"usage":{"input_tokens":0,"output_tokens":0}},"timestamp":"2026-07-13T00:00:02.500Z"}
"#;
        std::fs::write(&transcript_path, initial).unwrap();

        let writer_path = transcript_path.clone();
        let writer = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(250));
            let mut file = std::fs::OpenOptions::new()
                .append(true)
                .open(writer_path)
                .unwrap();
            writeln!(
                file,
                r#"{{"type":"assistant","message":{{"role":"assistant","id":"msg-2","model":"GLM-5.2","content":[{{"type":"text","text":"two"}}],"stop_reason":"end_turn","usage":{{"input_tokens":1682,"cache_creation_input_tokens":0,"cache_read_input_tokens":78464,"output_tokens":956}}}},"timestamp":"2026-07-13T00:00:03.000Z"}}"#
            )
            .unwrap();
            file.flush().unwrap();
        });

        let logger = crate::common::logger::Logger::new(dir.path().join("poll.log"), true);
        let started = Instant::now();
        let (messages, offset, buffer) = poll_transcript_until_terminal(
            &transcript_path,
            0,
            "",
            &HookEvent::Stop,
            TranscriptPollWindow {
                max_wait: Duration::from_secs(2),
                end_offset: None,
                turn_seed: None,
            },
            budget(),
            &logger,
        );
        let elapsed = started.elapsed();
        writer.join().unwrap();

        let turns = build_turns(&messages, None);
        assert_eq!(classify_postable_turns(&messages, &turns), (2, false));
        assert_eq!(turns[1].assistants[0].usage.input, Some(1682));
        assert_eq!(
            turns[1].assistants[0].usage.cache_read_input_tokens,
            Some(78464)
        );
        assert_eq!(turns[1].assistants[0].usage.output, Some(956));
        assert_eq!(
            offset,
            std::fs::metadata(&transcript_path).unwrap().len() as i64
        );
        assert!(buffer.is_empty());
        assert!(
            elapsed < Duration::from_secs(1),
            "terminal polling should return early, elapsed={elapsed:?}"
        );
    }

    #[test]
    fn session_end_does_not_wait_for_incomplete_tail() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"type":"user","uuid":"turn-1","message":{"role":"user","content":"unfinished"},"timestamp":"2026-07-13T00:00:00.000Z"}
"#,
        )
        .unwrap();
        let logger = crate::common::logger::Logger::new(dir.path().join("session-end.log"), true);

        let started = Instant::now();
        let (messages, _, _) = poll_transcript_until_terminal(
            &transcript_path,
            0,
            "",
            &HookEvent::SessionEnd,
            TranscriptPollWindow {
                max_wait: Duration::from_secs(2),
                end_offset: None,
                turn_seed: None,
            },
            budget(),
            &logger,
        );

        assert_eq!(messages.len(), 1);
        assert!(
            started.elapsed() < Duration::from_millis(500),
            "SessionEnd must not wait for terminal polling"
        );
    }

    #[test]
    fn stop_snapshot_does_not_read_or_checkpoint_a_later_turn() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("session.jsonl");
        let first = concat!(
            "{\"type\":\"user\",\"uuid\":\"turn-1\",\"message\":{\"role\":\"user\",\"content\":\"one\"},\"timestamp\":\"2026-08-24T00:00:00Z\"}\n",
            "{\"type\":\"assistant\",\"message\":{\"role\":\"assistant\",\"id\":\"msg-1\",\"content\":[{\"type\":\"text\",\"text\":\"done one\"}],\"stop_reason\":\"end_turn\"},\"timestamp\":\"2026-08-24T00:00:01Z\"}\n"
        );
        std::fs::write(&transcript_path, first).unwrap();
        let first_stop_end = std::fs::metadata(&transcript_path).unwrap().len();
        let second = concat!(
            "{\"type\":\"user\",\"uuid\":\"turn-2\",\"message\":{\"role\":\"user\",\"content\":\"two\"},\"timestamp\":\"2026-08-24T00:01:00Z\"}\n",
            "{\"type\":\"assistant\",\"message\":{\"role\":\"assistant\",\"id\":\"msg-2\",\"content\":[{\"type\":\"text\",\"text\":\"done two\"}],\"stop_reason\":\"end_turn\"},\"timestamp\":\"2026-08-24T00:01:01Z\"}\n"
        );
        std::fs::OpenOptions::new()
            .append(true)
            .open(&transcript_path)
            .unwrap()
            .write_all(second.as_bytes())
            .unwrap();
        let logger = crate::common::logger::Logger::new(dir.path().join("bounded.log"), true);

        let (messages, offset, buffer) = poll_transcript_until_terminal(
            &transcript_path,
            0,
            "",
            &HookEvent::Stop,
            TranscriptPollWindow {
                max_wait: Duration::from_millis(0),
                end_offset: Some(first_stop_end),
                turn_seed: None,
            },
            budget(),
            &logger,
        );

        let turns = build_turns(&messages, None);
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].chat_id.as_deref(), Some("turn-1"));
        assert_eq!(offset, first_stop_end as i64);
        assert!(buffer.is_empty());
    }

    #[test]
    fn stop_snapshot_allows_delayed_terminal_but_stops_before_next_user() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("session-delayed.jsonl");
        let user_timestamp = "2026-08-24T00:00:00Z";
        let user = format!(
            "{{\"type\":\"user\",\"uuid\":\"turn-1\",\"message\":{{\"role\":\"user\",\"content\":\"one\"}},\"timestamp\":\"{user_timestamp}\"}}\n"
        );
        let assistant = "{\"type\":\"assistant\",\"message\":{\"role\":\"assistant\",\"id\":\"msg-1\",\"content\":[{\"type\":\"text\",\"text\":\"done one\"}],\"stop_reason\":\"end_turn\"},\"timestamp\":\"2026-08-24T00:00:01Z\"}";
        let split = assistant.len() / 2;
        std::fs::write(&transcript_path, format!("{user}{}", &assistant[..split])).unwrap();
        let first_stop_end = std::fs::metadata(&transcript_path).unwrap().len();
        let writer_path = transcript_path.clone();
        let assistant_tail = assistant[split..].to_string();
        let writer = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(50));
            let later = concat!(
                "{\"type\":\"user\",\"uuid\":\"turn-2\",\"message\":{\"role\":\"user\",\"content\":\"two\"},\"timestamp\":\"2026-08-24T00:01:00Z\"}\n",
                "{\"type\":\"assistant\",\"message\":{\"role\":\"assistant\",\"id\":\"msg-2\",\"content\":[{\"type\":\"text\",\"text\":\"done two\"}],\"stop_reason\":\"end_turn\"},\"timestamp\":\"2026-08-24T00:01:01Z\"}\n"
            );
            let mut file = std::fs::OpenOptions::new()
                .append(true)
                .open(writer_path)
                .unwrap();
            write!(file, "{assistant_tail}\n{later}").unwrap();
            file.flush().unwrap();
        });
        let logger = crate::common::logger::Logger::new(dir.path().join("delayed.log"), true);
        let seed = crate::hooks::turn_seed::TurnSeed {
            schema_version: crate::hooks::turn_seed::TURN_SEED_SCHEMA_VERSION,
            turn_id: "seed-without-native-id".to_string(),
            trace_id: "41049c989ea3510bafe6d446672d7b1c".to_string(),
            upstream_traceparent: None,
            prompt_timestamp: None,
            prompt_digest: Some(crate::hooks::turn_seed::prompt_digest("one")),
            transcript_boundary: None,
            created_at: user_timestamp.to_string(),
        };

        let (messages, offset, buffer) = poll_transcript_until_terminal(
            &transcript_path,
            0,
            "",
            &HookEvent::Stop,
            TranscriptPollWindow {
                max_wait: Duration::from_secs(1),
                end_offset: Some(first_stop_end),
                turn_seed: Some(&seed),
            },
            budget(),
            &logger,
        );
        writer.join().unwrap();

        let turns = build_turns(&messages, None);
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].chat_id.as_deref(), Some("turn-1"));
        assert!(offset > first_stop_end as i64);
        assert!(offset < std::fs::metadata(&transcript_path).unwrap().len() as i64);
        assert!(buffer.is_empty());
    }

    #[test]
    fn stop_snapshot_keeps_delayed_tool_result_inside_the_current_turn() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("session-tool-delayed.jsonl");
        let user_timestamp = "2026-08-24T00:00:00Z";
        let initial = concat!(
            "{\"type\":\"user\",\"uuid\":\"turn-tool\",\"message\":{\"role\":\"user\",\"content\":\"read\"},\"timestamp\":\"2026-08-24T00:00:00Z\"}\n",
            "{\"type\":\"assistant\",\"message\":{\"role\":\"assistant\",\"id\":\"msg-tool\",\"content\":[{\"type\":\"tool_use\",\"id\":\"tool-1\",\"name\":\"Read\",\"input\":{}}],\"stop_reason\":\"tool_use\"},\"timestamp\":\"2026-08-24T00:00:01Z\"}\n"
        );
        std::fs::write(&transcript_path, initial).unwrap();
        let first_stop_end = std::fs::metadata(&transcript_path).unwrap().len();
        let writer_path = transcript_path.clone();
        let writer = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(50));
            let growth = concat!(
                "{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"tool_result\",\"tool_use_id\":\"tool-1\",\"content\":\"ok\"}]},\"timestamp\":\"2026-08-24T00:00:02Z\"}\n",
                "{\"type\":\"assistant\",\"message\":{\"role\":\"assistant\",\"id\":\"msg-final\",\"content\":[{\"type\":\"text\",\"text\":\"done\"}],\"stop_reason\":\"end_turn\"},\"timestamp\":\"2026-08-24T00:00:03Z\"}\n",
                "{\"type\":\"user\",\"uuid\":\"turn-next\",\"message\":{\"role\":\"user\",\"content\":\"next\"},\"timestamp\":\"2026-08-24T00:01:00Z\"}\n"
            );
            let mut file = std::fs::OpenOptions::new()
                .append(true)
                .open(writer_path)
                .unwrap();
            file.write_all(growth.as_bytes()).unwrap();
            file.flush().unwrap();
        });
        let logger = crate::common::logger::Logger::new(dir.path().join("tool.log"), true);
        let seed = crate::hooks::turn_seed::TurnSeed {
            schema_version: crate::hooks::turn_seed::TURN_SEED_SCHEMA_VERSION,
            turn_id: "seed-without-native-id".to_string(),
            trace_id: "41049c989ea3510bafe6d446672d7b1c".to_string(),
            upstream_traceparent: None,
            prompt_timestamp: None,
            prompt_digest: Some(crate::hooks::turn_seed::prompt_digest("read")),
            transcript_boundary: None,
            created_at: user_timestamp.to_string(),
        };

        let (messages, offset, buffer) = poll_transcript_until_terminal(
            &transcript_path,
            0,
            "",
            &HookEvent::Stop,
            TranscriptPollWindow {
                max_wait: Duration::from_secs(1),
                end_offset: Some(first_stop_end),
                turn_seed: Some(&seed),
            },
            budget(),
            &logger,
        );
        writer.join().unwrap();

        let turns = build_turns(&messages, None);
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].tool_results.len(), 1);
        assert_eq!(turns[0].assistants.len(), 2);
        assert!(offset > first_stop_end as i64);
        assert!(offset < std::fs::metadata(&transcript_path).unwrap().len() as i64);
        assert!(buffer.is_empty());
    }

    #[test]
    fn scan_record_boundary_excludes_partial_tail_bytes() {
        assert_eq!(scan_record_boundary(100, "partial"), 93);
        assert_eq!(scan_record_boundary(100, ""), 100);
    }
}

#[cfg(test)]
mod delivery_transaction_tests {
    use super::*;
    use serde_json::json;
    use std::sync::{Arc, Barrier};

    fn store(path: std::path::PathBuf) -> SessionStateStore {
        SessionStateStore::new(path, "session-1", 2_000)
    }

    #[test]
    fn concurrent_at_most_once_claim_allows_one_sender() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        crate::hooks::langfuse::state::save_state(
            &path,
            &json!({"session-1": {"offset": 0, "buffer": "", "turn_count": 0}}),
        )
        .unwrap();
        let barrier = Arc::new(Barrier::new(3));
        let mut handles = Vec::new();
        for _ in 0..2 {
            let path = path.clone();
            let barrier = Arc::clone(&barrier);
            handles.push(std::thread::spawn(move || {
                let preparation = prepare_turn_export(
                    &store(path.clone()),
                    "session-1",
                    "turn-1",
                    "/tmp/t.jsonl",
                    Some(100),
                )
                .unwrap();
                let TurnPreparation::Send {
                    persisted_count, ..
                } = preparation
                else {
                    panic!("turn should be available before either claim");
                };
                barrier.wait();
                claim_at_most_once_turn(
                    &store(path),
                    "session-1",
                    "turn-1",
                    "/tmp/t.jsonl",
                    Some(100),
                    persisted_count,
                    None,
                )
                .unwrap()
            }));
        }
        barrier.wait();
        let decisions: Vec<_> = handles
            .into_iter()
            .map(|handle| handle.join().unwrap())
            .collect();
        assert_eq!(
            decisions
                .iter()
                .filter(|decision| matches!(decision, AtMostOnceClaim::Claimed))
                .count(),
            1
        );
        let state = agentic_otel_common::state::load_state(&path);
        assert_eq!(state["session-1"]["turn_count"], 1);
        assert_eq!(state["session-1"]["posted_turn_ids"], json!(["turn-1"]));
    }

    #[test]
    fn at_least_once_duplicate_window_uses_one_turn_number_and_one_checkpoint() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        crate::hooks::langfuse::state::save_state(
            &path,
            &json!({"session-1": {"offset": 0, "buffer": "", "turn_count": 0}}),
        )
        .unwrap();

        let first = prepare_turn_export(
            &store(path.clone()),
            "session-1",
            "turn-1",
            "/tmp/t.jsonl",
            Some(100),
        )
        .unwrap();
        let second = prepare_turn_export(
            &store(path.clone()),
            "session-1",
            "turn-1",
            "/tmp/t.jsonl",
            Some(100),
        )
        .unwrap();
        assert_eq!(
            first,
            TurnPreparation::Send {
                persisted_count: 0,
                turn_number: 1,
                persisted_compaction_count: 0,
            }
        );
        assert_eq!(second, first);

        checkpoint_accepted_turn(
            &store(path.clone()),
            "session-1",
            "turn-1",
            "/tmp/t.jsonl",
            Some(100),
            None,
        )
        .unwrap();
        checkpoint_accepted_turn(
            &store(path.clone()),
            "session-1",
            "turn-1",
            "/tmp/t.jsonl",
            Some(100),
            None,
        )
        .unwrap();
        let state = agentic_otel_common::state::load_state(&path);
        assert_eq!(state["session-1"]["turn_count"], 1);
        assert_eq!(state["session-1"]["posted_turn_ids"], json!(["turn-1"]));
    }

    #[test]
    fn stale_at_most_once_plan_retries_without_checkpointing() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        crate::hooks::langfuse::state::save_state(
            &path,
            &json!({"session-1": {"offset": 0, "buffer": "", "turn_count": 0}}),
        )
        .unwrap();

        let stale_plan = prepare_turn_export(
            &store(path.clone()),
            "session-1",
            "turn-2",
            "/tmp/t.jsonl",
            Some(100),
        )
        .unwrap();
        let TurnPreparation::Send {
            persisted_count, ..
        } = stale_plan
        else {
            panic!("turn should be available");
        };
        let state = agentic_otel_common::state::load_state(&path);
        assert_eq!(state["session-1"]["turn_count"], 0);
        assert!(state["session-1"]["posted_turn_ids"].is_null());

        checkpoint_accepted_turn(
            &store(path.clone()),
            "session-1",
            "turn-1",
            "/tmp/t.jsonl",
            Some(100),
            None,
        )
        .unwrap();
        assert_eq!(
            claim_at_most_once_turn(
                &store(path.clone()),
                "session-1",
                "turn-2",
                "/tmp/t.jsonl",
                Some(100),
                persisted_count,
                None,
            )
            .unwrap(),
            AtMostOnceClaim::Retry
        );

        let state = agentic_otel_common::state::load_state(&path);
        assert_eq!(state["session-1"]["turn_count"], 1);
        assert_eq!(state["session-1"]["posted_turn_ids"], json!(["turn-1"]));

        let fresh_plan = prepare_turn_export(
            &store(path.clone()),
            "session-1",
            "turn-2",
            "/tmp/t.jsonl",
            Some(100),
        )
        .unwrap();
        let TurnPreparation::Send {
            persisted_count, ..
        } = fresh_plan
        else {
            panic!("turn should remain available after a stale plan");
        };
        assert_eq!(
            claim_at_most_once_turn(
                &store(path.clone()),
                "session-1",
                "turn-2",
                "/tmp/t.jsonl",
                Some(100),
                persisted_count,
                None,
            )
            .unwrap(),
            AtMostOnceClaim::Claimed
        );
        let state = agentic_otel_common::state::load_state(&path);
        assert_eq!(state["session-1"]["turn_count"], 2);
        assert_eq!(
            state["session-1"]["posted_turn_ids"],
            json!(["turn-1", "turn-2"])
        );
    }

    #[test]
    fn committed_turn_anchor_drives_late_compaction_checkpoint() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        crate::hooks::langfuse::state::save_state(
            &path,
            &json!({"session-1": {"offset": 0, "buffer": "", "turn_count": 0}}),
        )
        .unwrap();
        let state_store = store(path.clone());
        let anchor = CompactionParentAnchor {
            turn_id: "turn-1".to_string(),
            turn_number: 1,
            trace_id: "a".repeat(32),
            root_span_id: "b".repeat(16),
            chat_id: "chat-1".to_string(),
            transcript_path: "/tmp/t.jsonl".to_string(),
            source_end_offset: 100,
        };
        let checkpoint = TurnExportCheckpoint {
            anchor: anchor.clone(),
            compaction_ids: Vec::new(),
            expected_compaction_count: 0,
        };

        checkpoint_accepted_turn(
            &state_store,
            "session-1",
            "turn-1",
            "/tmp/t.jsonl",
            Some(100),
            Some(&checkpoint),
        )
        .unwrap();
        state_store
            .update(|state| {
                assert!(commit_export_success_in_memory(
                    state,
                    "session-1",
                    100,
                    "",
                    "2026-08-28T00:00:00Z",
                    None,
                ));
                Ok(())
            })
            .unwrap();

        assert_eq!(
            prepare_compaction_export(
                &state_store,
                "session-1",
                "compact-1",
                "/tmp/t.jsonl",
                Some(150),
                false,
            )
            .unwrap(),
            CompactionPreparation::Send {
                anchor,
                persisted_count: 0,
                sequence: 1,
            }
        );
        checkpoint_accepted_compaction(
            &state_store,
            "session-1",
            "compact-1",
            "/tmp/t.jsonl",
            Some(150),
            0,
        )
        .unwrap();
        assert_eq!(
            prepare_compaction_export(
                &state_store,
                "session-1",
                "compact-1",
                "/tmp/t.jsonl",
                Some(150),
                false,
            )
            .unwrap(),
            CompactionPreparation::Skip
        );

        let state = agentic_otel_common::state::load_state(&path);
        assert_eq!(state["session-1"]["compaction_count"], 1);
        assert_eq!(
            state["session-1"]["posted_compaction_ids"],
            json!(["compact-1"])
        );
        assert_eq!(
            state["session-1"]["last_exported_turn_anchor"]["turn_id"],
            "turn-1"
        );
    }

    #[test]
    fn late_compaction_without_anchor_remains_retryable() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        crate::hooks::langfuse::state::save_state(
            &path,
            &json!({"session-1": {"offset": 100, "buffer": "", "turn_count": 1}}),
        )
        .unwrap();

        let error = prepare_compaction_export(
            &store(path),
            "session-1",
            "compact-1",
            "/tmp/t.jsonl",
            Some(150),
            false,
        )
        .unwrap_err();
        assert!(error
            .to_string()
            .contains("compaction parent anchor missing"));
    }

    #[test]
    fn at_most_once_anchor_is_published_only_after_turn_post_succeeds() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        crate::hooks::langfuse::state::save_state(
            &path,
            &json!({"session-1": {
                "offset": 100, "buffer": "", "turn_count": 1,
                "last_exported_turn_anchor": {
                    "turn_id": "old-turn", "turn_number": 1,
                    "trace_id": "11111111111111111111111111111111",
                    "root_span_id": "2222222222222222", "chat_id": "old-chat",
                    "transcript_path": "/tmp/t.jsonl", "source_end_offset": 100
                }
            }}),
        )
        .unwrap();
        let state_store = store(path.clone());
        let new_anchor = CompactionParentAnchor {
            turn_id: "turn-2".to_string(),
            turn_number: 2,
            trace_id: "a".repeat(32),
            root_span_id: "b".repeat(16),
            chat_id: "chat-2".to_string(),
            transcript_path: "/tmp/t.jsonl".to_string(),
            source_end_offset: 150,
        };
        let export_checkpoint = TurnExportCheckpoint {
            anchor: new_anchor.clone(),
            compaction_ids: Vec::new(),
            expected_compaction_count: 0,
        };

        assert_eq!(
            claim_at_most_once_turn(
                &state_store,
                "session-1",
                "turn-2",
                "/tmp/t.jsonl",
                Some(150),
                1,
                Some(&export_checkpoint),
            )
            .unwrap(),
            AtMostOnceClaim::Claimed
        );
        assert!(agentic_otel_common::state::load_state(&path)["session-1"]
            .get("last_exported_turn_anchor")
            .is_none());
        assert_eq!(
            prepare_compaction_export(
                &state_store,
                "session-1",
                "compact-1",
                "/tmp/t.jsonl",
                Some(175),
                true,
            )
            .unwrap(),
            CompactionPreparation::Drop {
                persisted_count: 0,
                sequence: 1,
            }
        );

        checkpoint_successful_turn_anchor(
            &state_store,
            "session-1",
            "turn-2",
            "/tmp/t.jsonl",
            &new_anchor,
        )
        .unwrap();
        assert_eq!(
            prepare_compaction_export(
                &state_store,
                "session-1",
                "compact-1",
                "/tmp/t.jsonl",
                Some(175),
                true,
            )
            .unwrap(),
            CompactionPreparation::Send {
                anchor: new_anchor,
                persisted_count: 0,
                sequence: 1,
            }
        );
    }

    #[test]
    fn concurrent_at_most_once_compaction_claim_allows_one_sender() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        crate::hooks::langfuse::state::save_state(
            &path,
            &json!({"session-1": {
                "offset": 100, "buffer": "", "turn_count": 1,
                "compaction_count": 0,
                "last_exported_turn_anchor": {
                    "turn_id": "turn-1", "turn_number": 1,
                    "trace_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "root_span_id": "bbbbbbbbbbbbbbbb", "chat_id": "chat-1",
                    "transcript_path": "/tmp/t.jsonl", "source_end_offset": 100
                }
            }}),
        )
        .unwrap();
        let barrier = Arc::new(Barrier::new(3));
        let mut handles = Vec::new();
        for _ in 0..2 {
            let path = path.clone();
            let barrier = Arc::clone(&barrier);
            handles.push(std::thread::spawn(move || {
                let CompactionPreparation::Send {
                    persisted_count, ..
                } = prepare_compaction_export(
                    &store(path.clone()),
                    "session-1",
                    "compact-1",
                    "/tmp/t.jsonl",
                    Some(150),
                    true,
                )
                .unwrap()
                else {
                    panic!("compaction should be available before either claim");
                };
                barrier.wait();
                claim_at_most_once_compaction(
                    &store(path),
                    "session-1",
                    "compact-1",
                    "/tmp/t.jsonl",
                    Some(150),
                    persisted_count,
                )
                .unwrap()
            }));
        }
        barrier.wait();
        let decisions: Vec<_> = handles
            .into_iter()
            .map(|handle| handle.join().unwrap())
            .collect();
        assert_eq!(
            decisions
                .iter()
                .filter(|decision| matches!(decision, AtMostOnceClaim::Claimed))
                .count(),
            1
        );
        let state = agentic_otel_common::state::load_state(&path);
        assert_eq!(state["session-1"]["compaction_count"], 1);
        assert_eq!(
            state["session-1"]["posted_compaction_ids"],
            json!(["compact-1"])
        );
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ValidatedTurnWindow {
    turn_id: String,
    start_offset: i64,
    source_epoch: TranscriptSourceEpoch,
}

fn validated_turn_window(
    transcript_path: &str,
    turn_seed: Option<&crate::hooks::turn_seed::TurnSeed>,
    logger: &crate::common::logger::Logger,
) -> Option<ValidatedTurnWindow> {
    let turn_seed = turn_seed?;
    let boundary = turn_seed.transcript_boundary.as_ref()?;
    if boundary.transcript_path != transcript_path {
        logger
            .warn("Turn transcript path changed after UserPromptSubmit; preserving legacy cursor");
        return None;
    }
    let snapshot = match inspect_transcript_source(
        std::path::Path::new(transcript_path),
        Some(boundary.source_epoch.prefix_len),
    ) {
        Ok(snapshot) => snapshot,
        Err(error) => {
            logger.warn(&format!(
                "Turn transcript source is unavailable after UserPromptSubmit; preserving legacy cursor: {error}"
            ));
            return None;
        }
    };
    if snapshot.epoch != boundary.source_epoch
        || boundary.start_offset < 0
        || u64::try_from(boundary.start_offset).ok()? > snapshot.file_len
    {
        logger.warn(
            "Turn transcript was replaced or truncated after UserPromptSubmit; preserving legacy cursor",
        );
        return None;
    }
    Some(ValidatedTurnWindow {
        turn_id: turn_seed.turn_id.clone(),
        start_offset: boundary.start_offset,
        source_epoch: snapshot.epoch,
    })
}

fn apply_turn_window(
    state: &mut serde_json::Value,
    session_id: &str,
    transcript_path: &str,
    window: &ValidatedTurnWindow,
) -> Result<(), StateTxnError> {
    let mut session = get_session_state(state, session_id)
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));
    if !session.is_object() {
        return Err(StateTxnError::Schema(
            "turn-scoped Stop state must be an object".to_string(),
        ));
    }
    let stored_epoch = session
        .get("cursor_source_epoch")
        .cloned()
        .and_then(|value| serde_json::from_value::<TranscriptSourceEpoch>(value).ok());
    let same_source = session
        .get("cursor_transcript_path")
        .and_then(serde_json::Value::as_str)
        == Some(transcript_path)
        && stored_epoch.as_ref() == Some(&window.source_epoch);
    let same_turn = session
        .get("active_turn_id")
        .and_then(serde_json::Value::as_str)
        == Some(window.turn_id.as_str());

    if !same_source || !same_turn {
        session["offset"] = serde_json::json!(window.start_offset);
        session["buffer"] = serde_json::json!("");
        if let Some(object) = session.as_object_mut() {
            object.remove("posted_turn_ids");
            object.remove("posted_compaction_ids");
            object.remove("pending_export_reason");
            object.remove("pending_window_end_offset");
            object.remove("transcript_path");
        }
    } else if get_int_field(&session, "offset") < window.start_offset {
        session["offset"] = serde_json::json!(window.start_offset);
        session["buffer"] = serde_json::json!("");
    }
    session["cursor_transcript_path"] = serde_json::json!(transcript_path);
    session["cursor_source_epoch"] = serde_json::to_value(&window.source_epoch)
        .map_err(|error| StateTxnError::Schema(error.to_string()))?;
    session["active_turn_id"] = serde_json::json!(window.turn_id);
    state[session_id] = session;
    Ok(())
}

#[cfg(test)]
mod turn_scoped_window_tests {
    use super::*;
    use serde_json::json;
    use std::collections::BTreeMap;

    #[test]
    fn rebuilt_transcript_resets_old_cursor_and_skips_synthetic_history() {
        let dir = tempfile::tempdir().unwrap();
        let transcript = dir.path().join("rebuilt.jsonl");
        let synthetic_history = [
            json!({
                "type": "user",
                "uuid": "history-user",
                "message": {"role": "user", "content": "old prompt"},
                "timestamp": "2026-08-31T00:00:00Z"
            }),
            json!({
                "type": "assistant",
                "uuid": "history-assistant",
                "message": {
                    "role": "assistant",
                    "id": "msg-history",
                    "model": "claude-sonnet-4-6",
                    "content": [{"type": "text", "text": "old answer"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1}
                },
                "timestamp": "2026-08-31T00:00:00Z"
            }),
        ]
        .into_iter()
        .map(|value| format!("{value}\n"))
        .collect::<String>();
        let current_turn = [
            json!({
                "type": "user",
                "uuid": "current-user",
                "message": {"role": "user", "content": "current prompt"},
                "timestamp": "2026-08-31T00:01:00Z"
            }),
            json!({
                "type": "assistant",
                "uuid": "current-assistant",
                "message": {
                    "role": "assistant",
                    "id": "msg-current",
                    "model": "GLM-5.2",
                    "content": [{"type": "text", "text": "current answer"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 20, "output_tokens": 4}
                },
                "timestamp": "2026-08-31T00:01:01Z"
            }),
        ]
        .into_iter()
        .map(|value| format!("{value}\n"))
        .collect::<String>();
        std::fs::write(&transcript, format!("{synthetic_history}{current_turn}")).unwrap();
        let snapshot = inspect_transcript_source(&transcript, None).unwrap();
        let canonical = transcript
            .canonicalize()
            .unwrap()
            .to_string_lossy()
            .into_owned();
        let window = ValidatedTurnWindow {
            turn_id: "current-user".to_string(),
            start_offset: synthetic_history.len() as i64,
            source_epoch: snapshot.epoch,
        };
        let mut state = json!({
            "session": {
                "offset": 999999,
                "buffer": "stale",
                "turn_count": 1,
                "cursor_transcript_path": "/tmp/old.jsonl",
                "cursor_source_epoch": {"identity_digest": "old", "prefix_len": 1},
                "active_turn_id": "old-user"
            }
        });

        apply_turn_window(&mut state, "session", &canonical, &window).unwrap();
        let offset = get_int_field(&state["session"], "offset");
        let (messages, _, _) = read_new_jsonl_bounded(&transcript, offset, "", None);
        let turns = build_turns(&messages, None);

        assert_eq!(offset, synthetic_history.len() as i64);
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].user_text, "current prompt");
        assert_eq!(turns[0].assistants[0].model, "GLM-5.2");
        assert_eq!(state["session"]["turn_count"], 1);
    }

    #[test]
    fn duplicate_stop_on_same_turn_keeps_forward_cursor() {
        let epoch = TranscriptSourceEpoch {
            identity_digest: "same-source".to_string(),
            prefix_len: 10,
        };
        let mut state = json!({
            "session": {
                "offset": 250,
                "buffer": "",
                "cursor_transcript_path": "/tmp/same.jsonl",
                "cursor_source_epoch": epoch,
                "active_turn_id": "turn-1"
            }
        });
        let window = ValidatedTurnWindow {
            turn_id: "turn-1".to_string(),
            start_offset: 100,
            source_epoch: serde_json::from_value(state["session"]["cursor_source_epoch"].clone())
                .unwrap(),
        };

        apply_turn_window(&mut state, "session", "/tmp/same.jsonl", &window).unwrap();

        assert_eq!(state["session"]["offset"], 250);
    }

    #[test]
    fn new_turn_window_preserves_session_compaction_state() {
        let epoch = TranscriptSourceEpoch {
            identity_digest: "new-source".to_string(),
            prefix_len: 10,
        };
        let anchor = json!({
            "turn_id": "prior-turn",
            "trace_id": "41049c989ea3510bafe6d446672d7b1c",
            "span_id": "61c42769ba435f72"
        });
        let pending_intent = json!({"intent_id": "durable-stop-intent"});
        let mut state = json!({
            "pending_final_export": pending_intent,
            "session": {
                "offset": 250,
                "buffer": "stale",
                "compaction_count": 3,
                "last_exported_turn_anchor": anchor,
                "cursor_transcript_path": "/tmp/old.jsonl",
                "cursor_source_epoch": {"identity_digest": "old-source", "prefix_len": 10},
                "active_turn_id": "prior-turn"
            }
        });
        let window = ValidatedTurnWindow {
            turn_id: "new-turn".to_string(),
            start_offset: 100,
            source_epoch: epoch,
        };

        apply_turn_window(&mut state, "session", "/tmp/new.jsonl", &window).unwrap();

        assert_eq!(state["session"]["compaction_count"], 3);
        assert_eq!(state["session"]["last_exported_turn_anchor"], anchor);
        assert_eq!(state["pending_final_export"], pending_intent);
    }

    #[test]
    fn transcript_capture_remains_enabled_when_setting_is_unset() {
        let dir = tempfile::tempdir().unwrap();
        let project_dir = dir.path().join("project");
        std::fs::create_dir(&project_dir).unwrap();
        let transcript = project_dir.join("session.jsonl");
        std::fs::write(&transcript, "{\"type\":\"user\"}\n").unwrap();
        let capture_dir = dir.path().join("capture");
        let logger = crate::common::logger::Logger::new(dir.path().join("capture.log"), true);

        crate::common::env::with_worker_env(&BTreeMap::new(), || {
            copy_transcript_to_session_dir(
                &capture_dir,
                "bb22fb91-3a0a-564e-8606-a1c7ff942c6b",
                transcript.to_str().unwrap(),
                &logger,
            );
        });

        let captured = capture_dir.join("session_project_bb22fb91.jsonl");
        assert_eq!(
            std::fs::read_to_string(captured).unwrap(),
            "{\"type\":\"user\"}\n"
        );
    }

    #[test]
    fn transcript_capture_can_be_explicitly_disabled() {
        let dir = tempfile::tempdir().unwrap();
        let project_dir = dir.path().join("project");
        std::fs::create_dir(&project_dir).unwrap();
        let transcript = project_dir.join("session.jsonl");
        std::fs::write(&transcript, "{\"type\":\"user\"}\n").unwrap();
        let capture_dir = dir.path().join("capture");
        let logger = crate::common::logger::Logger::new(dir.path().join("capture.log"), true);
        let env = BTreeMap::from([(TRANSCRIPT_CAPTURE_ENV.to_string(), "false".to_string())]);

        crate::common::env::with_worker_env(&env, || {
            copy_transcript_to_session_dir(
                &capture_dir,
                "bb22fb91-3a0a-564e-8606-a1c7ff942c6b",
                transcript.to_str().unwrap(),
                &logger,
            );
        });

        assert!(!capture_dir.exists());
    }
}

fn persist_barrier(
    state_store: &SessionStateStore,
    session_id: &str,
    reason: &str,
    transcript_path: &str,
    context: &CommonContext,
) {
    if let Err(error) = state_store.update(|state| {
        record_pending_export_in_memory(
            state,
            session_id,
            reason,
            &now_iso8601_pub(),
            transcript_path,
        );
        Ok(())
    }) {
        context.logger.error(&format!(
            "Failed to persist export barrier reason={reason}: {error}"
        ));
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TurnPreparation {
    Skip,
    Send {
        persisted_count: usize,
        turn_number: usize,
        persisted_compaction_count: usize,
    },
}

#[derive(Debug, Clone)]
struct TurnExportCheckpoint {
    anchor: CompactionParentAnchor,
    compaction_ids: Vec<String>,
    expected_compaction_count: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum AtMostOnceClaim {
    Skip,
    Retry,
    Claimed,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum CompactionPreparation {
    Skip,
    Drop {
        persisted_count: usize,
        sequence: u32,
    },
    Send {
        anchor: CompactionParentAnchor,
        persisted_count: usize,
        sequence: u32,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CompactionCheckpoint {
    Inserted,
    AlreadyAccounted,
    Conflict,
}

fn prepare_turn_export(
    state_store: &SessionStateStore,
    session_id: &str,
    turn_id: &str,
    transcript_path: &str,
    window_end_offset: Option<i64>,
) -> Result<TurnPreparation, StateTxnError> {
    state_store.read(|state| {
        validate_checkpoint_source(state, session_id, transcript_path)?;
        let session = get_session_state(state, session_id);
        let persisted_count = session
            .map(|value| get_int_field(value, "turn_count") as usize)
            .unwrap_or(0);
        let persisted_compaction_count = session
            .map(|value| get_int_field(value, "compaction_count") as usize)
            .unwrap_or(0);
        let finalized = window_end_offset.is_some_and(|window_end| {
            session
                .map(|value| get_int_field(value, "offset"))
                .unwrap_or(0)
                >= window_end
        });
        let already_posted = session
            .map(|value| get_string_array_field(value, "posted_turn_ids"))
            .unwrap_or_default()
            .iter()
            .any(|posted| posted == turn_id);
        if finalized || already_posted {
            return Ok(TurnPreparation::Skip);
        }
        Ok(TurnPreparation::Send {
            persisted_count,
            turn_number: persisted_count + 1,
            persisted_compaction_count,
        })
    })?
}

#[allow(clippy::too_many_arguments)]
fn claim_at_most_once_turn(
    state_store: &SessionStateStore,
    session_id: &str,
    turn_id: &str,
    transcript_path: &str,
    window_end_offset: Option<i64>,
    expected_persisted_count: usize,
    export_checkpoint: Option<&TurnExportCheckpoint>,
) -> Result<AtMostOnceClaim, StateTxnError> {
    state_store.update(|state| {
        validate_checkpoint_source(state, session_id, transcript_path)?;
        let session = get_session_state(state, session_id);
        let persisted_count = session
            .map(|value| get_int_field(value, "turn_count") as usize)
            .unwrap_or(0);
        let finalized = window_end_offset.is_some_and(|window_end| {
            session
                .map(|value| get_int_field(value, "offset"))
                .unwrap_or(0)
                >= window_end
        });
        let already_posted = session
            .map(|value| get_string_array_field(value, "posted_turn_ids"))
            .unwrap_or_default()
            .iter()
            .any(|posted| posted == turn_id);
        if finalized || already_posted {
            return Ok(AtMostOnceClaim::Skip);
        }
        if persisted_count != expected_persisted_count {
            return Ok(AtMostOnceClaim::Retry);
        }

        let checkpoint = checkpoint_turn_accounted_in_memory(
            state,
            session_id,
            turn_id,
            &now_iso8601_pub(),
            transcript_path,
            window_end_offset,
        );
        if !checkpoint.inserted {
            return Ok(AtMostOnceClaim::Skip);
        }
        if let Some(export_checkpoint) = export_checkpoint {
            checkpoint_turn_export_metadata_in_memory(state, session_id, export_checkpoint, false)?;
        }
        debug_assert_eq!(checkpoint.turn_count, persisted_count + 1);
        Ok(AtMostOnceClaim::Claimed)
    })
}

fn checkpoint_accepted_turn(
    state_store: &SessionStateStore,
    session_id: &str,
    turn_id: &str,
    transcript_path: &str,
    window_end_offset: Option<i64>,
    export_checkpoint: Option<&TurnExportCheckpoint>,
) -> Result<(), StateTxnError> {
    state_store.update(|state| {
        validate_checkpoint_source(state, session_id, transcript_path)?;
        let _ = checkpoint_turn_accounted_in_memory(
            state,
            session_id,
            turn_id,
            &now_iso8601_pub(),
            transcript_path,
            window_end_offset,
        );
        if let Some(export_checkpoint) = export_checkpoint {
            checkpoint_turn_export_metadata_in_memory(state, session_id, export_checkpoint, true)?;
        }
        Ok(())
    })
}

fn checkpoint_turn_export_metadata_in_memory(
    state: &mut serde_json::Value,
    session_id: &str,
    export_checkpoint: &TurnExportCheckpoint,
    turn_delivery_succeeded: bool,
) -> Result<(), StateTxnError> {
    let mut session = get_session_state(state, session_id)
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));
    let persisted_compaction_count = get_int_field(&session, "compaction_count") as usize;
    if persisted_compaction_count != export_checkpoint.expected_compaction_count {
        return Err(StateTxnError::Schema(
            "compaction checkpoint changed while exporting turn".to_string(),
        ));
    }

    let mut posted_ids = get_string_array_field(&session, "posted_compaction_ids");
    let mut inserted_count = 0usize;
    for identity in &export_checkpoint.compaction_ids {
        if !posted_ids.iter().any(|posted| posted == identity) {
            posted_ids.push(identity.clone());
            inserted_count += 1;
        }
    }
    session["posted_compaction_ids"] = serde_json::json!(posted_ids);
    session["compaction_count"] = serde_json::json!(persisted_compaction_count + inserted_count);
    if turn_delivery_succeeded {
        session["last_exported_turn_anchor"] = serde_json::to_value(&export_checkpoint.anchor)
            .map_err(|error| StateTxnError::Schema(error.to_string()))?;
    } else if let Some(object) = session.as_object_mut() {
        // A pre-claimed at-most-once Turn is not a valid parent until its POST
        // succeeds. Remove the older anchor so a later compaction cannot be
        // attached to the wrong Turn when this POST is policy-dropped.
        object.remove("last_exported_turn_anchor");
    }
    state[session_id] = session;
    Ok(())
}

fn checkpoint_successful_turn_anchor(
    state_store: &SessionStateStore,
    session_id: &str,
    turn_id: &str,
    transcript_path: &str,
    anchor: &CompactionParentAnchor,
) -> Result<(), StateTxnError> {
    state_store.update(|state| {
        validate_checkpoint_source(state, session_id, transcript_path)?;
        let mut session = get_session_state(state, session_id)
            .cloned()
            .ok_or_else(|| StateTxnError::Schema("claimed turn state missing".to_string()))?;
        let claimed = get_string_array_field(&session, "posted_turn_ids")
            .iter()
            .any(|posted| posted == turn_id);
        if !claimed {
            return Err(StateTxnError::Schema(
                "successful turn anchor has no matching claim".to_string(),
            ));
        }
        session["last_exported_turn_anchor"] = serde_json::to_value(anchor)
            .map_err(|error| StateTxnError::Schema(error.to_string()))?;
        state[session_id] = session;
        Ok(())
    })
}

fn prepare_compaction_export(
    state_store: &SessionStateStore,
    session_id: &str,
    compaction_id: &str,
    transcript_path: &str,
    window_end_offset: Option<i64>,
    drop_if_anchor_missing: bool,
) -> Result<CompactionPreparation, StateTxnError> {
    state_store.read(|state| {
        validate_checkpoint_source(state, session_id, transcript_path)?;
        let session = get_session_state(state, session_id);
        let finalized = window_end_offset.is_some_and(|window_end| {
            session
                .map(|value| get_int_field(value, "offset"))
                .unwrap_or(0)
                >= window_end
        });
        let already_posted = session
            .map(|value| get_string_array_field(value, "posted_compaction_ids"))
            .unwrap_or_default()
            .iter()
            .any(|posted| posted == compaction_id);
        if finalized || already_posted {
            return Ok(CompactionPreparation::Skip);
        }

        let session = session.ok_or_else(|| {
            StateTxnError::Schema("compaction parent anchor missing session state".to_string())
        })?;
        let persisted_count = get_int_field(session, "compaction_count") as usize;
        let sequence = u32::try_from(persisted_count + 1).map_err(|_| {
            StateTxnError::Schema("compaction sequence exceeds u32 range".to_string())
        })?;
        let Some(anchor_value) = session.get("last_exported_turn_anchor").cloned() else {
            if drop_if_anchor_missing {
                return Ok(CompactionPreparation::Drop {
                    persisted_count,
                    sequence,
                });
            }
            return Err(StateTxnError::Schema(
                "compaction parent anchor missing".to_string(),
            ));
        };
        let anchor: CompactionParentAnchor =
            serde_json::from_value(anchor_value).map_err(|error| {
                StateTxnError::Schema(format!("invalid compaction anchor: {error}"))
            })?;
        if anchor.transcript_path != transcript_path {
            return Err(StateTxnError::Schema(
                "compaction anchor transcript mismatch".to_string(),
            ));
        }
        if window_end_offset.is_some_and(|window_end| {
            anchor.source_end_offset > u64::try_from(window_end).unwrap_or(0)
        }) {
            return Err(StateTxnError::Schema(
                "compaction anchor is newer than the parse window".to_string(),
            ));
        }

        Ok(CompactionPreparation::Send {
            anchor,
            persisted_count,
            sequence,
        })
    })?
}

fn checkpoint_compaction_in_memory(
    state: &mut serde_json::Value,
    session_id: &str,
    compaction_id: &str,
    expected_persisted_count: usize,
    transcript_path: &str,
    window_end_offset: Option<i64>,
) -> Result<CompactionCheckpoint, StateTxnError> {
    let mut session = get_session_state(state, session_id)
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));
    let mut posted_ids = get_string_array_field(&session, "posted_compaction_ids");
    if posted_ids.iter().any(|posted| posted == compaction_id) {
        return Ok(CompactionCheckpoint::AlreadyAccounted);
    }
    let persisted_count = get_int_field(&session, "compaction_count") as usize;
    if persisted_count != expected_persisted_count {
        return Ok(CompactionCheckpoint::Conflict);
    }

    posted_ids.push(compaction_id.to_string());
    session["posted_compaction_ids"] = serde_json::json!(posted_ids);
    session["compaction_count"] = serde_json::json!(persisted_count + 1);
    session["updated"] = serde_json::json!(now_iso8601_pub());
    session["pending_export_reason"] = serde_json::json!(PARTIAL_EXPORT_REASON);
    session["transcript_path"] = serde_json::json!(transcript_path);
    if let Some(window_end_offset) = window_end_offset {
        session["pending_window_end_offset"] = serde_json::json!(window_end_offset);
    }
    state[session_id] = session;
    Ok(CompactionCheckpoint::Inserted)
}

#[allow(clippy::too_many_arguments)]
fn claim_at_most_once_compaction(
    state_store: &SessionStateStore,
    session_id: &str,
    compaction_id: &str,
    transcript_path: &str,
    window_end_offset: Option<i64>,
    expected_persisted_count: usize,
) -> Result<AtMostOnceClaim, StateTxnError> {
    state_store.update(|state| {
        validate_checkpoint_source(state, session_id, transcript_path)?;
        match checkpoint_compaction_in_memory(
            state,
            session_id,
            compaction_id,
            expected_persisted_count,
            transcript_path,
            window_end_offset,
        )? {
            CompactionCheckpoint::Inserted => Ok(AtMostOnceClaim::Claimed),
            CompactionCheckpoint::AlreadyAccounted => Ok(AtMostOnceClaim::Skip),
            CompactionCheckpoint::Conflict => Ok(AtMostOnceClaim::Retry),
        }
    })
}

#[allow(clippy::too_many_arguments)]
fn checkpoint_accepted_compaction(
    state_store: &SessionStateStore,
    session_id: &str,
    compaction_id: &str,
    transcript_path: &str,
    window_end_offset: Option<i64>,
    expected_persisted_count: usize,
) -> Result<(), StateTxnError> {
    state_store.update(|state| {
        validate_checkpoint_source(state, session_id, transcript_path)?;
        match checkpoint_compaction_in_memory(
            state,
            session_id,
            compaction_id,
            expected_persisted_count,
            transcript_path,
            window_end_offset,
        )? {
            CompactionCheckpoint::Inserted | CompactionCheckpoint::AlreadyAccounted => Ok(()),
            CompactionCheckpoint::Conflict => Err(StateTxnError::Schema(
                "compaction checkpoint changed after export".to_string(),
            )),
        }
    })
}

fn validate_checkpoint_source(
    state: &serde_json::Value,
    session_id: &str,
    transcript_path: &str,
) -> Result<(), StateTxnError> {
    let Some(session) = get_session_state(state, session_id) else {
        return Ok(());
    };
    if get_string_array_field(session, "posted_turn_ids").is_empty()
        && get_string_array_field(session, "posted_compaction_ids").is_empty()
    {
        return Ok(());
    }
    let checkpoint_path = session
        .get("transcript_path")
        .and_then(serde_json::Value::as_str);
    if checkpoint_path == Some(transcript_path) {
        Ok(())
    } else {
        Err(StateTxnError::Schema(
            "checkpoint source-window mismatch".to_string(),
        ))
    }
}

fn matched_realtime_context_ids(
    contexts: &[crate::hooks::langfuse::realtime_state::RealtimeContext],
    turns: &[crate::common::transcript::Turn],
) -> Vec<String> {
    let mut matched = Vec::new();
    for turn in turns {
        let tool_ids = turn.tool_use_ids();
        if let Some(context) = contexts.iter().find(|context| {
            context
                .tool_use_ids
                .iter()
                .any(|id| tool_ids.contains(&id.as_str()))
        }) {
            if !matched.contains(&context.context_id) {
                matched.push(context.context_id.clone());
            }
        }
    }
    matched
}

/// Resolve the single trace context used to assemble one Stop-family turn.
/// Tool/realtime contexts own any identity already emitted. A TurnSeed supplies
/// the trace for pure-LLM turns, or backfills a missing parent only when it is
/// already on the same trace as a tool context.
fn resolve_stop_trace_context(
    turn: &crate::common::transcript::Turn,
    stable_turn_id: &str,
    is_last_postable_turn: bool,
    incomplete_tail: bool,
    trace_context_history: &[StoredTraceContext],
    turn_seed: Option<&crate::hooks::turn_seed::TurnSeed>,
) -> (Option<StoredTraceContext>, Option<(String, String)>) {
    let tool_ids = turn.tool_use_ids();
    let tool_context = trace_context_history.iter().find(|context| {
        context
            .tool_use_ids
            .iter()
            .any(|id| tool_ids.contains(&id.as_str()))
    });

    let seed = turn_seed.filter(|seed| {
        let exact_match = seed.turn_id == stable_turn_id;
        let created_at_matches = prompt_times_are_close(&seed.created_at, &turn.user_timestamp);
        let timestamp_matches = seed.prompt_timestamp.as_deref()
            == Some(turn.user_timestamp.as_str())
            && strict_rfc3339_nanos(&turn.user_timestamp).is_some();
        let freshness_matches = timestamp_matches
            || seed.prompt_digest.as_deref()
                == Some(crate::hooks::turn_seed::prompt_digest(&turn.user_text).as_str())
                && created_at_matches;
        let safe_evidence_fallback = is_last_postable_turn
            && !incomplete_tail
            && freshness_matches
            && !(turn.cancelled && tool_context.is_none());
        exact_match || safe_evidence_fallback
    });

    match (tool_context, seed) {
        (Some(tool_context), Some(seed)) => {
            let mismatch = (tool_context.trace_id != seed.trace_id)
                .then(|| (tool_context.trace_id.clone(), seed.trace_id.clone()));
            let mut resolved = tool_context.clone();
            // Realtime/tool context owns identities that may already have been
            // exported. Never move those span IDs to another trace at Stop.
            // A same-trace seed may only backfill a missing upstream parent.
            if mismatch.is_none() && resolved.traceparent.is_none() {
                resolved.traceparent = seed.upstream_traceparent.clone();
            }
            (Some(resolved), mismatch)
        }
        (Some(tool_context), None) => (Some(tool_context.clone()), None),
        (None, Some(seed)) if !turn.cancelled => (
            Some(StoredTraceContext {
                trace_id: seed.trace_id.clone(),
                traceparent: seed.upstream_traceparent.clone(),
                ..StoredTraceContext::default()
            }),
            None,
        ),
        (None, _) => (None, None),
    }
}

fn prompt_times_are_close(seed_created_at: &str, turn_timestamp: &str) -> bool {
    const MAX_PROMPT_CLOCK_SKEW_NS: u128 = 30_000_000_000;
    let Some(seed_ns) = strict_rfc3339_nanos(seed_created_at) else {
        return false;
    };
    let Some(turn_ns) = strict_rfc3339_nanos(turn_timestamp) else {
        return false;
    };
    seed_ns.abs_diff(turn_ns) <= MAX_PROMPT_CLOCK_SKEW_NS
}

fn strict_rfc3339_nanos(value: &str) -> Option<u128> {
    let bytes = value.as_bytes();
    if bytes.len() < 20
        || bytes.get(4) != Some(&b'-')
        || bytes.get(7) != Some(&b'-')
        || bytes.get(10) != Some(&b'T')
        || bytes.get(13) != Some(&b':')
        || bytes.get(16) != Some(&b':')
    {
        return None;
    }
    let number = |start: usize, end: usize| -> Option<u32> {
        let digits = value.get(start..end)?;
        digits
            .bytes()
            .all(|byte| byte.is_ascii_digit())
            .then(|| digits.parse().ok())
            .flatten()
    };
    let year = number(0, 4)?;
    let month = number(5, 7)?;
    let day = number(8, 10)?;
    let hour = number(11, 13)?;
    let minute = number(14, 16)?;
    let second = number(17, 19)?;
    let leap = year.is_multiple_of(4) && !year.is_multiple_of(100) || year.is_multiple_of(400);
    if year == 0 {
        return None;
    }
    let max_day = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if leap => 29,
        2 => 28,
        _ => return None,
    };
    if day == 0 || day > max_day || hour > 23 || minute > 59 || second > 60 {
        return None;
    }
    let suffix = value.get(19..)?;
    let suffix = if let Some(fraction_and_zone) = suffix.strip_prefix('.') {
        let zone_start = fraction_and_zone
            .bytes()
            .position(|byte| matches!(byte, b'Z' | b'+' | b'-'))?;
        let digits = fraction_and_zone.get(..zone_start)?;
        if digits.is_empty() || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
            return None;
        }
        fraction_and_zone.get(zone_start..)?
    } else {
        suffix
    };
    if suffix != "Z" {
        let sign = suffix.as_bytes().first().copied();
        if !matches!(sign, Some(b'+' | b'-'))
            || suffix.len() != 6
            || suffix.as_bytes().get(3) != Some(&b':')
            || number_in(suffix, 1, 3)? > 23
            || number_in(suffix, 4, 6)? > 59
        {
            return None;
        }
    }
    agentic_otel_common::iso_to_nanos(value).parse().ok()
}

fn number_in(value: &str, start: usize, end: usize) -> Option<u32> {
    let digits = value.get(start..end)?;
    digits
        .bytes()
        .all(|byte| byte.is_ascii_digit())
        .then(|| digits.parse().ok())
        .flatten()
}

#[cfg(test)]
mod stop_trace_context_tests {
    use super::*;
    use crate::common::transcript::{build_turns, stable_turn_id};
    use crate::hooks::turn_seed::{TurnSeed, TURN_SEED_SCHEMA_VERSION};
    use serde_json::json;

    fn seed(turn_id: &str, trace_id: &str) -> TurnSeed {
        TurnSeed {
            schema_version: TURN_SEED_SCHEMA_VERSION,
            turn_id: turn_id.to_string(),
            trace_id: trace_id.to_string(),
            upstream_traceparent: Some(format!("00-{trace_id}-61c42769ba435f72-01")),
            prompt_timestamp: Some("2026-08-24T00:00:00Z".to_string()),
            prompt_digest: Some(crate::hooks::turn_seed::prompt_digest("hello")),
            transcript_boundary: None,
            created_at: "2026-08-24T00:00:00Z".to_string(),
        }
    }

    fn pure_turn(native_id: Option<&str>) -> crate::common::transcript::Turn {
        let mut user = json!({
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "timestamp": "2026-08-24T00:00:00Z"
        });
        if let Some(native_id) = native_id {
            user["uuid"] = json!(native_id);
        }
        build_turns(
            &[
                user,
                json!({
                    "type": "assistant",
                    "message": {"role": "assistant", "id": "msg-1", "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"},
                    "timestamp": "2026-08-24T00:00:01Z"
                }),
            ],
            None,
        )
        .remove(0)
    }

    #[test]
    fn fractional_offset_timestamp_matches_equivalent_utc_timestamp() {
        let offset = strict_rfc3339_nanos("2026-08-24T00:00:00.123+08:00").unwrap();
        let utc = strict_rfc3339_nanos("2026-08-23T16:00:00.123Z").unwrap();

        assert_eq!(offset, utc);
        assert!(prompt_times_are_close(
            "2026-08-24T00:00:00.123+08:00",
            "2026-08-23T16:00:00.123Z",
        ));
    }

    #[test]
    fn fractional_offset_timestamp_allows_prompt_digest_seed_binding() {
        let mut turn = pure_turn(None);
        turn.user_timestamp = "2026-08-24T00:00:00.123+08:00".to_string();
        let turn_id = stable_turn_id(&turn, "session", 0);
        let mut seed = seed(
            "seed-generated-without-native-id",
            "41049c989ea3510bafe6d446672d7b1c",
        );
        seed.prompt_timestamp = None;
        seed.created_at = "2026-08-23T16:00:00.123Z".to_string();

        assert!(
            resolve_stop_trace_context(&turn, &turn_id, true, false, &[], Some(&seed),)
                .0
                .is_some()
        );
    }

    #[test]
    fn native_turn_seed_binds_only_exact_turn() {
        let turn = pure_turn(Some("turn-native"));
        let matching = seed("turn-native", "41049c989ea3510bafe6d446672d7b1c");
        let (resolved, mismatch) =
            resolve_stop_trace_context(&turn, "turn-native", false, false, &[], Some(&matching));
        assert_eq!(resolved.unwrap().trace_id, matching.trace_id);
        assert_eq!(mismatch, None);
    }

    #[test]
    fn prompt_digest_binds_official_hook_input_without_uuid_or_timestamp() {
        let turn = pure_turn(Some("turn-from-transcript"));
        let mut seed = seed(
            "seed-generated-without-native-id",
            "41049c989ea3510bafe6d446672d7b1c",
        );
        seed.prompt_timestamp = None;

        assert!(resolve_stop_trace_context(
            &turn,
            "turn-from-transcript",
            true,
            false,
            &[],
            Some(&seed),
        )
        .0
        .is_some());
        seed.prompt_digest = Some(crate::hooks::turn_seed::prompt_digest("different prompt"));
        assert!(resolve_stop_trace_context(
            &turn,
            "turn-from-transcript",
            true,
            false,
            &[],
            Some(&seed),
        )
        .0
        .is_none());

        let mut invalid_timestamp_turn = turn;
        invalid_timestamp_turn.user_timestamp = "2026-99-99T99:99:99Z".to_string();
        seed.prompt_digest = Some(crate::hooks::turn_seed::prompt_digest("hello"));
        assert!(resolve_stop_trace_context(
            &invalid_timestamp_turn,
            "turn-from-transcript",
            true,
            false,
            &[],
            Some(&seed),
        )
        .0
        .is_none());
    }

    #[test]
    fn fallback_turn_seed_requires_matching_prompt_evidence() {
        let turn = pure_turn(None);
        let turn_id = stable_turn_id(&turn, "session", 0);
        let seed = seed("seed-does-not-match", "41049c989ea3510bafe6d446672d7b1c");
        assert!(
            resolve_stop_trace_context(&turn, &turn_id, true, false, &[], Some(&seed))
                .0
                .is_some()
        );
        assert!(
            resolve_stop_trace_context(&turn, &turn_id, false, false, &[], Some(&seed))
                .0
                .is_none()
        );
        assert!(
            resolve_stop_trace_context(&turn, &turn_id, true, true, &[], Some(&seed))
                .0
                .is_none()
        );
        let mut stale = seed;
        stale.prompt_timestamp = Some("2026-08-23T23:59:00Z".to_string());
        stale.prompt_digest = Some(crate::hooks::turn_seed::prompt_digest("stale prompt"));
        assert!(
            resolve_stop_trace_context(&turn, &turn_id, true, false, &[], Some(&stale))
                .0
                .is_none()
        );
    }

    #[test]
    fn cancelled_pure_llm_turn_keeps_local_trace_semantics() {
        let mut turn = pure_turn(Some("turn-cancelled"));
        turn.cancelled = true;
        let seed = seed("turn-cancelled", "41049c989ea3510bafe6d446672d7b1c");
        assert!(
            resolve_stop_trace_context(&turn, "turn-cancelled", true, false, &[], Some(&seed))
                .0
                .is_none()
        );
    }

    #[test]
    fn realtime_context_keeps_its_trace_and_parent_when_seed_mismatches() {
        let turn = build_turns(
            &[
                json!({"type":"user","uuid":"turn-tool","message":{"role":"user","content":"read"},"timestamp":"2026-08-24T00:00:00Z"}),
                json!({"type":"assistant","message":{"role":"assistant","id":"msg-tool","content":[{"type":"tool_use","id":"tool-1","name":"Read","input":{}}],"stop_reason":"tool_use"},"timestamp":"2026-08-24T00:00:01Z"})
            ],
            None,
        )
        .remove(0);
        let tool_context = StoredTraceContext {
            trace_id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".to_string(),
            root_span_id: Some("1111111111111111".to_string()),
            traceparent: Some(
                "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01".to_string(),
            ),
            tool_use_ids: vec!["tool-1".to_string()],
            tool_span_ids: std::collections::HashMap::from([(
                "tool-1".to_string(),
                "2222222222222222".to_string(),
            )]),
            generation_span_ids: std::collections::HashMap::from([(
                "msg-tool".to_string(),
                "3333333333333333".to_string(),
            )]),
            ..StoredTraceContext::default()
        };
        let seed = seed("turn-tool", "41049c989ea3510bafe6d446672d7b1c");

        let (resolved, mismatch) = resolve_stop_trace_context(
            &turn,
            "turn-tool",
            true,
            false,
            &[tool_context],
            Some(&seed),
        );
        let resolved = resolved.unwrap();
        assert_eq!(resolved.trace_id, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        assert_eq!(
            resolved.traceparent.as_deref(),
            Some("00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01")
        );
        assert_eq!(resolved.root_span_id.as_deref(), Some("1111111111111111"));
        assert_eq!(resolved.tool_span_ids["tool-1"], "2222222222222222");
        assert_eq!(resolved.generation_span_ids["msg-tool"], "3333333333333333");
        assert_eq!(
            mismatch,
            Some((
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".to_string(),
                "41049c989ea3510bafe6d446672d7b1c".to_string()
            ))
        );
    }

    #[test]
    fn same_trace_seed_only_backfills_missing_tool_traceparent() {
        let turn = build_turns(
            &[
                json!({"type":"user","uuid":"turn-tool","message":{"role":"user","content":"read"},"timestamp":"2026-08-24T00:00:00Z"}),
                json!({"type":"assistant","message":{"role":"assistant","id":"msg-tool","content":[{"type":"tool_use","id":"tool-1","name":"Read","input":{}}],"stop_reason":"tool_use"},"timestamp":"2026-08-24T00:00:01Z"})
            ],
            None,
        )
        .remove(0);
        let trace_id = "41049c989ea3510bafe6d446672d7b1c";
        let tool_context = StoredTraceContext {
            trace_id: trace_id.to_string(),
            tool_use_ids: vec!["tool-1".to_string()],
            ..StoredTraceContext::default()
        };
        let seed = seed("turn-tool", trace_id);

        let (resolved, mismatch) = resolve_stop_trace_context(
            &turn,
            "turn-tool",
            true,
            false,
            &[tool_context],
            Some(&seed),
        );
        assert_eq!(mismatch, None);
        assert_eq!(resolved.unwrap().traceparent, seed.upstream_traceparent);
    }
}

fn stored_trace_contexts_from_realtime(
    contexts: &[crate::hooks::langfuse::realtime_state::RealtimeContext],
    accounted_child_max_end_times: &std::collections::HashMap<String, String>,
) -> Vec<crate::common::otel::StoredTraceContext> {
    contexts
        .iter()
        .map(|context| crate::common::otel::StoredTraceContext {
            trace_id: context.trace_id.clone(),
            root_span_id: Some(context.root_span_id.clone()),
            traceparent: context.traceparent.clone(),
            sofa_traceid: context.sofa_traceid.clone(),
            sofa_rpcid: context.sofa_rpcid.clone(),
            tool_use_ids: context.tool_use_ids.clone(),
            tool_span_ids: context.tool_span_ids.clone(),
            emitted_tool_use_ids: context.emitted_tool_use_ids.clone(),
            generation_span_ids: context.generation_span_ids.clone(),
            accounted_generation_ids: context.accounted_generation_ids.clone(),
            max_accounted_child_end_time_unix_nano: accounted_child_max_end_times
                .get(&context.context_id)
                .cloned(),
        })
        .collect()
}

#[cfg(test)]
mod realtime_trace_snapshot_tests {
    use super::stored_trace_contexts_from_realtime;
    use crate::hooks::langfuse::realtime_state::RealtimeContext;

    #[test]
    fn stop_history_preserves_original_upstream_and_sofa_snapshot() {
        let context = RealtimeContext {
            context_id: "context".to_string(),
            trace_id: "trace".to_string(),
            root_span_id: "root".to_string(),
            traceparent: Some("traceparent".to_string()),
            sofa_traceid: Some("sofa-trace".to_string()),
            sofa_rpcid: Some("0.1".to_string()),
            ..RealtimeContext::default()
        };
        let max_end_times = std::collections::HashMap::from([(
            context.context_id.clone(),
            "1772112855365000000".to_string(),
        )]);

        let stored = stored_trace_contexts_from_realtime(&[context], &max_end_times);

        assert_eq!(stored[0].traceparent.as_deref(), Some("traceparent"));
        assert_eq!(stored[0].sofa_traceid.as_deref(), Some("sofa-trace"));
        assert_eq!(stored[0].sofa_rpcid.as_deref(), Some("0.1"));
        assert_eq!(
            stored[0].max_accounted_child_end_time_unix_nano.as_deref(),
            Some("1772112855365000000")
        );
    }
}

#[cfg(all(feature = "tool_context", not(windows)))]
fn load_trace_context_history(
    state: &serde_json::Value,
    session_id: &str,
) -> Vec<crate::common::otel::StoredTraceContext> {
    use crate::common::otel::StoredTraceContext;
    fn parse(value: &serde_json::Value) -> Option<StoredTraceContext> {
        Some(StoredTraceContext {
            trace_id: value.get("trace_id")?.as_str()?.to_string(),
            root_span_id: None,
            traceparent: value
                .get("traceparent")
                .and_then(|v| v.as_str())
                .map(str::to_string),
            sofa_traceid: value
                .get("sofa_traceid")
                .and_then(|v| v.as_str())
                .map(str::to_string),
            sofa_rpcid: value
                .get("sofa_rpcid")
                .and_then(|v| v.as_str())
                .map(str::to_string),
            tool_use_ids: value
                .get("tool_use_ids")
                .and_then(|v| v.as_array())
                .map(|ids| {
                    ids.iter()
                        .filter_map(|id| id.as_str().map(str::to_string))
                        .collect()
                })
                .unwrap_or_default(),
            tool_span_ids: std::collections::HashMap::new(),
            emitted_tool_use_ids: Vec::new(),
            generation_span_ids: std::collections::HashMap::new(),
            accounted_generation_ids: Vec::new(),
            max_accounted_child_end_time_unix_nano: None,
        })
    }
    let mut history: Vec<StoredTraceContext> = state
        .get(session_id)
        .and_then(|session| session.get("trace_context_history"))
        .and_then(|value| value.as_array())
        .map(|values| values.iter().filter_map(parse).collect())
        .unwrap_or_default();
    if let Some(current) = state
        .get(session_id)
        .and_then(|session| session.get("current_trace_context"))
        .and_then(parse)
    {
        history.push(current);
    }
    history
}

#[cfg(not(all(feature = "tool_context", not(windows))))]
fn load_trace_context_history(
    _state: &serde_json::Value,
    _session_id: &str,
) -> Vec<crate::common::otel::StoredTraceContext> {
    Vec::new()
}

fn timing_archive_path(
    debug_enabled: bool,
    session_id: &str,
    transcript_path: &str,
) -> Option<std::path::PathBuf> {
    if !debug_enabled {
        return None;
    }
    let session_dir = dirs::transcript_dir();
    let project = std::path::Path::new(transcript_path)
        .parent()
        .and_then(|path| path.file_name())
        .and_then(|name| name.to_str())
        .unwrap_or("unknown");
    let short_session = &session_id[..session_id.len().min(8)];
    Some(session_dir.join(format!("session_{project}_{short_session}.timing.jsonl")))
}

fn copy_transcript_to_session_dir(
    session_dir: &std::path::Path,
    session_id: &str,
    transcript_path: &str,
    logger: &crate::common::logger::Logger,
) {
    if !transcript_capture_enabled(logger) {
        logger.debug("Transcript capture disabled by CCHOOKS_TRANSCRIPT_CAPTURE");
        return;
    }

    let project = std::path::Path::new(transcript_path)
        .parent()
        .and_then(|path| path.file_name())
        .and_then(|name| name.to_str())
        .unwrap_or("unknown");
    let short_session = &session_id[..session_id.len().min(8)];
    if let Err(error) = crate::common::dirs::ensure_private_dir(session_dir) {
        logger.debug(&format!("Failed to create session dir: {error}"));
        return;
    }
    let destination = session_dir.join(format!("session_{project}_{short_session}.jsonl"));
    if let Err(error) = std::fs::copy(transcript_path, &destination) {
        logger.debug(&format!("Failed to copy transcript: {error}"));
    }
}

fn transcript_capture_enabled(logger: &crate::common::logger::Logger) -> bool {
    if crate::common::otel::is_privacy_mode() { return false; }
    match crate::hooks::resolve_bool_setting(crate::common::env::var(TRANSCRIPT_CAPTURE_ENV).ok()) {
        BoolSetting::Valid(enabled) => enabled,
        BoolSetting::Unset => true,
        BoolSetting::Invalid(value) => {
            logger.warn(&format!(
                "Invalid {TRANSCRIPT_CAPTURE_ENV}={value}; preserving transcript capture"
            ));
            true
        }
    }
}
