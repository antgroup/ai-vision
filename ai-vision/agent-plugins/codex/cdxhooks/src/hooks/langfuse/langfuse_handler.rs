use crate::common::dirs;
use crate::common::hook::{
    extract_session_id, extract_transcript_path, wait_for_file_stable, HookEvent,
};
use crate::common::logger::now_iso8601_pub;
use crate::common::otel::{
    build_otel_export_request_for_turn, inject_vcs_resource_attrs, isolate_session_vcs_resource,
    post_otel_to_langfuse_once_classified, suppress_child_spans_by_id, TurnReplayOpts,
};
use crate::common::session_discovery::{
    discover_child_session_transcripts, discover_root_session_transcript, session_search_roots,
};
use crate::common::transcript::{
    build_turns, collect_subagent_identities, correlate_subagent_across_turns,
    is_child_sync_in_parent, is_internal_role_user_envelope, read_new_jsonl,
    read_new_jsonl_limited, split_completed_messages, subagent_anchor_turn_index,
    subagent_discovery_plan, try_extract_session_meta_ids_from_path, CollabInvocationState,
    SubagentDiscoveryDisposition, SubagentLifecycle, SubagentProtocol, Turn, TurnTerminalState,
};
use crate::hooks::langfuse::realtime::{
    extract_finalizer_spans_root_last, finalizer_semantic_body, plan_realtime_payload_chunks,
    validate_persisted_payload, REALTIME_TARGET_SERIALIZED_BODY_BYTES,
};
use crate::hooks::langfuse::realtime_state;
use crate::hooks::langfuse::state::{
    adopt_stop_recovery_delivery_intent, arm_legacy_finalizer_recovery,
    checkpoint_stop_recovery_build_cursor, checkpoint_stop_recovery_delivery,
    checkpoint_stop_recovery_topology, checkpoint_turn_export_attempt,
    checkpoint_turn_export_success, claim_finalizer_recovery_attempt, clear_stop_recovery_delivery,
    commit_export_success_with_compaction_cursor, durably_accounted_turn_ids_for_history,
    finalize_realtime_high_water, finalizer_recovery_key, get_array_field, get_int_field,
    get_session_state, get_str_field, get_string_array_field, get_vcs_for_cwd,
    is_turn_terminally_accounted, load_state, persist_compaction_cursor_for_offset,
    record_pending_export, record_terminal_quarantined_turn, stop_recovery_intent_id,
    stop_recovery_record, structural_topology_quarantine,
    update_state_and_save_with_compaction_cursor, update_state_transaction_fallible,
    StopRecoveryDeliveryAdoption, StopRecoveryDeliveryCheckpoint, StopRecoveryTopologyCheckpoint,
    StopRecoveryTopologyKind, VcsInfo, HOOK_BUDGET_EXHAUSTED_REASON, PARTIAL_EXPORT_REASON,
};
#[cfg(test)]
use crate::hooks::langfuse::turn_topology::finalizer_trace_root_for_turn_with_scan_base;
use crate::hooks::langfuse::turn_topology::{
    accounted_realtime_child_span_ids, finalizer_generation_span_ids_for_turn_with_scan_base,
    persist_finalizer_topology_decision, resolve_finalizer_turn_topology_with_accounting_history,
    resolve_finalizer_turn_topology_with_accounting_history_budgeted, FinalizerDecision,
    FinalizerTurnTopologyOutcome,
};
use crate::hooks::{CommonContext, HandlerError, HookHandler, RetryPolicy, StopBudget};
use agentic_otel_common::otlp::OtelAttribute;
use std::collections::HashSet;

/// Langfuse observability handler.
///
/// Processes Stop and SessionEnd hook events by reading the Codex transcript,
/// building OTel spans, and sending them to Langfuse.
pub struct LangfuseHandler {}

const STOP_TERMINAL_POLL_MAX_MS: u64 = 10_000;
const STOP_TERMINAL_POLL_INTERVAL_MS: u64 = 500;
const STOP_RECOVERY_TRANSCRIPT_SCAN_MAX_BYTES: usize = 256 * 1024;

#[cfg(test)]
struct ChildFixturePolling {
    publish_on_retry: Option<Box<dyn FnOnce()>>,
}

#[cfg(test)]
thread_local! {
    static CHILD_FIXTURE_POLLING: std::cell::RefCell<Option<ChildFixturePolling>> = const {
        std::cell::RefCell::new(None)
    };
}

#[cfg(test)]
pub(crate) struct ChildFixturePollingGuard(Option<ChildFixturePolling>);

#[cfg(test)]
impl Drop for ChildFixturePollingGuard {
    fn drop(&mut self) {
        let _ = CHILD_FIXTURE_POLLING.with(|polling| polling.replace(self.0.take()));
    }
}

#[cfg(test)]
pub(crate) fn child_fixture_polling(
    publish_on_retry: Option<Box<dyn FnOnce()>>,
) -> ChildFixturePollingGuard {
    let previous = CHILD_FIXTURE_POLLING
        .with(|polling| polling.replace(Some(ChildFixturePolling { publish_on_retry })));
    ChildFixturePollingGuard(previous)
}

#[cfg(test)]
static TEST_TURN_SUCCESS_CHECKPOINT_FAILURES: std::sync::OnceLock<
    std::sync::Mutex<std::collections::HashSet<String>>,
> = std::sync::OnceLock::new();

#[cfg(test)]
fn turn_success_checkpoint_failure_key(
    state_file: &std::path::Path,
    session_id: &str,
    turn_id: &str,
) -> String {
    format!("{}\n{session_id}\n{turn_id}", state_file.display())
}

#[cfg(test)]
fn fail_next_turn_success_checkpoint_for_test(
    state_file: &std::path::Path,
    session_id: &str,
    turn_id: &str,
) -> TurnSuccessCheckpointFailureGuard {
    let key = turn_success_checkpoint_failure_key(state_file, session_id, turn_id);
    TEST_TURN_SUCCESS_CHECKPOINT_FAILURES
        .get_or_init(|| std::sync::Mutex::new(std::collections::HashSet::new()))
        .lock()
        .unwrap()
        .insert(key.clone());
    TurnSuccessCheckpointFailureGuard { key }
}

#[cfg(test)]
struct TurnSuccessCheckpointFailureGuard {
    key: String,
}

#[cfg(test)]
impl Drop for TurnSuccessCheckpointFailureGuard {
    fn drop(&mut self) {
        TEST_TURN_SUCCESS_CHECKPOINT_FAILURES
            .get_or_init(|| std::sync::Mutex::new(std::collections::HashSet::new()))
            .lock()
            .unwrap()
            .remove(&self.key);
    }
}

#[cfg(test)]
fn should_fail_turn_success_checkpoint_for_test(
    state_file: &std::path::Path,
    session_id: &str,
    turn_id: &str,
) -> bool {
    TEST_TURN_SUCCESS_CHECKPOINT_FAILURES
        .get_or_init(|| std::sync::Mutex::new(std::collections::HashSet::new()))
        .lock()
        .unwrap()
        .remove(&turn_success_checkpoint_failure_key(
            state_file, session_id, turn_id,
        ))
}

#[allow(clippy::too_many_arguments)]
fn checkpoint_turn_export_success_maybe_faulted(
    state_file: &std::path::Path,
    state: &mut serde_json::Value,
    session_id: &str,
    turn_id: &str,
    now: &str,
    transcript_path: Option<&str>,
    merged_subagent_ids: &[String],
    merged_subagent_invocation_ids: &[String],
) -> Result<usize, String> {
    #[cfg(test)]
    if should_fail_turn_success_checkpoint_for_test(state_file, session_id, turn_id) {
        return Err("injected turn success checkpoint failure".to_string());
    }

    checkpoint_turn_export_success(
        state_file,
        state,
        session_id,
        turn_id,
        now,
        transcript_path,
        merged_subagent_ids,
        merged_subagent_invocation_ids,
    )
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct CompactionTokenCursor {
    offset: i64,
    last_trusted_input_tokens: Option<u64>,
    pending_seed_tokens: Option<u64>,
    native_usage_mode: bool,
    pending_native_usage_mode: bool,
}

impl CompactionTokenCursor {
    fn fresh(offset: i64) -> Self {
        Self {
            offset,
            last_trusted_input_tokens: None,
            pending_seed_tokens: None,
            native_usage_mode: false,
            pending_native_usage_mode: false,
        }
    }

    fn from_session(session: Option<&serde_json::Value>, expected_offset: i64) -> Option<Self> {
        let value = session?.get("compaction_token_cursor")?;
        if value
            .get("initialized")
            .and_then(serde_json::Value::as_bool)
            != Some(true)
            || value.get("offset").and_then(serde_json::Value::as_i64) != Some(expected_offset)
        {
            return None;
        }
        Some(Self {
            offset: expected_offset,
            last_trusted_input_tokens: value
                .get("last_trusted_input_tokens")
                .and_then(serde_json::Value::as_u64),
            pending_seed_tokens: value
                .get("pending_seed_tokens")
                .and_then(serde_json::Value::as_u64),
            // Legacy cursors are upgraded by the existing one-time prefix scan.
            native_usage_mode: value.get("native_usage_mode")?.as_bool()?,
            pending_native_usage_mode: value.get("pending_native_usage_mode")?.as_bool()?,
        })
    }

    fn to_value(self) -> serde_json::Value {
        serde_json::json!({
            "version": 2,
            "initialized": true,
            "offset": self.offset,
            "last_trusted_input_tokens": self.last_trusted_input_tokens,
            "pending_seed_tokens": self.pending_seed_tokens,
            "native_usage_mode": self.native_usage_mode,
            "pending_native_usage_mode": self.pending_native_usage_mode,
        })
    }
}

fn trusted_input_observation(entry: &serde_json::Value) -> Option<u64> {
    (entry.get("type").and_then(serde_json::Value::as_str) == Some("event_msg")
        && entry
            .pointer("/payload/type")
            .and_then(serde_json::Value::as_str)
            == Some("token_count"))
    .then(|| {
        entry
            .pointer("/payload/info/last_token_usage/input_tokens")
            .and_then(serde_json::Value::as_i64)
            .or_else(|| {
                entry
                    .pointer("/payload/info/last_token_usage/input")
                    .and_then(serde_json::Value::as_i64)
            })
    })
    .flatten()
    .filter(|value| *value > 0)
    .map(|value| value as u64)
}

fn trusted_input_after(initial: Option<u64>, messages: &[serde_json::Value]) -> Option<u64> {
    messages.iter().fold(initial, |last, entry| {
        trusted_input_observation(entry).or(last)
    })
}

fn scan_compaction_cursor_prefix(
    path: &std::path::Path,
    end_offset: i64,
    pending_messages: &[serde_json::Value],
) -> (Option<u64>, Option<u64>, bool, bool) {
    use std::collections::VecDeque;
    use std::io::{BufRead, BufReader, Read};

    if end_offset <= 0 {
        return (None, None, false, false);
    }
    let Ok(file) = std::fs::File::open(path) else {
        return (None, None, false, false);
    };
    let mut reader = BufReader::new(file.take(end_offset as u64));
    let pending_len = pending_messages.len();
    let mut trailing = VecDeque::with_capacity(pending_len);
    let mut last_trusted_input_tokens = None;
    let mut native_usage_mode = false;
    let mut line = String::new();

    loop {
        line.clear();
        let Ok(bytes_read) = reader.read_line(&mut line) else {
            return (None, None, false, false);
        };
        if bytes_read == 0 {
            break;
        }
        let Ok(entry) = serde_json::from_str::<serde_json::Value>(line.trim()) else {
            continue;
        };
        let trusted_before_entry = last_trusted_input_tokens;
        let native_before_entry = native_usage_mode;
        native_usage_mode |= crate::common::transcript::has_native_usage_record(&entry);
        if let Some(input_tokens) = trusted_input_observation(&entry) {
            last_trusted_input_tokens = Some(input_tokens);
        }
        if pending_len > 0 {
            if trailing.len() == pending_len {
                trailing.pop_front();
            }
            trailing.push_back((entry, trusted_before_entry, native_before_entry));
        }
    }

    let pending_matches = trailing.len() == pending_len
        && trailing
            .iter()
            .zip(pending_messages)
            .all(|((entry, _, _), pending)| entry == pending);
    let pending_seed_tokens = pending_matches
        .then(|| trailing.front().and_then(|(_, seed, _)| *seed))
        .flatten();
    let pending_native_usage_mode =
        pending_matches && trailing.front().is_some_and(|(_, _, native)| *native);
    (
        last_trusted_input_tokens,
        pending_seed_tokens,
        native_usage_mode,
        pending_native_usage_mode,
    )
}

fn initialize_compaction_cursor(
    transcript_path: &std::path::Path,
    offset: i64,
    buffer: &str,
    pending_messages: &[serde_json::Value],
) -> CompactionTokenCursor {
    if offset <= 0 {
        return CompactionTokenCursor::fresh(offset);
    }
    let parsed_end = offset.saturating_sub(buffer.len() as i64);
    let (
        last_trusted_input_tokens,
        pending_seed_tokens,
        native_usage_mode,
        pending_native_usage_mode,
    ) = scan_compaction_cursor_prefix(transcript_path, parsed_end, pending_messages);
    CompactionTokenCursor {
        offset,
        last_trusted_input_tokens,
        pending_seed_tokens,
        native_usage_mode,
        pending_native_usage_mode,
    }
}

fn advance_compaction_cursor(
    previous: CompactionTokenCursor,
    new_offset: i64,
    build_seed: Option<u64>,
    previous_pending: &[serde_json::Value],
    new_messages: &[serde_json::Value],
    pending_messages: &[serde_json::Value],
) -> CompactionTokenCursor {
    let last_trusted_input_tokens =
        trusted_input_after(previous.last_trusted_input_tokens, new_messages);
    let native_usage_mode = previous.native_usage_mode
        || new_messages
            .iter()
            .any(crate::common::transcript::has_native_usage_record);
    if pending_messages.is_empty() {
        return CompactionTokenCursor {
            offset: new_offset,
            last_trusted_input_tokens,
            pending_seed_tokens: None,
            native_usage_mode,
            pending_native_usage_mode: native_usage_mode,
        };
    }

    let mut combined = previous_pending.to_vec();
    combined.extend_from_slice(new_messages);
    let native_seed = if previous_pending.is_empty() {
        previous.native_usage_mode
    } else {
        previous.pending_native_usage_mode
    };
    let pending_native_usage_mode = combined
        .len()
        .checked_sub(pending_messages.len())
        .filter(|prefix_len| combined[*prefix_len..] == *pending_messages)
        .map(|prefix_len| {
            native_seed
                || combined[..prefix_len]
                    .iter()
                    .any(crate::common::transcript::has_native_usage_record)
        })
        .unwrap_or(previous.pending_native_usage_mode);
    let pending_seed_tokens = combined
        .len()
        .checked_sub(pending_messages.len())
        .filter(|prefix_len| combined[*prefix_len..] == *pending_messages)
        .map(|prefix_len| trusted_input_after(build_seed, &combined[..prefix_len]))
        .unwrap_or(previous.pending_seed_tokens);
    CompactionTokenCursor {
        offset: new_offset,
        last_trusted_input_tokens,
        pending_seed_tokens,
        native_usage_mode,
        pending_native_usage_mode,
    }
}

impl LangfuseHandler {
    pub fn new() -> Self {
        Self {}
    }
}

impl HookHandler for LangfuseHandler {
    fn name(&self) -> &str {
        "langfuse"
    }

    fn supported_events(&self) -> &[HookEvent] {
        // Subagents are exported through the owning parent turn. The child's
        // own Stop (or a mismatched session/transcript) is filtered by
        // `handle_stop`; Codex no longer emits the historical `SubagentStop`.
        &[HookEvent::Stop, HookEvent::SessionEnd]
    }

    fn handle(
        &self,
        event: &HookEvent,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        match event {
            // The hook parent has already detached this session-owned worker.
            // Run finalization here; do not create a second spool/worker layer.
            HookEvent::Stop | HookEvent::SessionEnd => self
                .handle_stop_event(event, payload, context, true)
                .map(|_| ()),
            _ => Ok(()),
        }
    }
}

impl LangfuseHandler {
    pub fn handle_stop_with_outcome(
        &self,
        event: &HookEvent,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Result<StopDispatchOutcome, HandlerError> {
        let wait_for_terminal = payload
            .get("recovery_source")
            .and_then(serde_json::Value::as_str)
            != Some("stop_recovery");
        self.handle_stop_event(event, payload, context, wait_for_terminal)
    }

    #[cfg(test)]
    fn handle_stop(
        &self,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        self.handle_stop_event(&HookEvent::Stop, payload, context, true)
            .map(|_| ())
    }

    /// Handle Stop or SessionEnd for the parent session.
    fn handle_stop_event(
        &self,
        event: &HookEvent,
        payload: &serde_json::Value,
        context: &CommonContext,
        wait_for_terminal: bool,
    ) -> Result<StopDispatchOutcome, HandlerError> {
        if context.langfuse_config.is_none() {
            context.logger.error(
                "No Langfuse configuration found. \
                 Set CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT, \
                 CDXHOOKS_LANGFUSE_BASE_URL, or LANGFUSE_BASE_URL \
                 (LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are optional).",
            );
            return Err(HandlerError::from(
                "Missing Langfuse configuration".to_string(),
            ));
        }

        // Guard against infinite loop: if stop_hook_active is true, a stop hook
        // is already running (triggered by this hook's own output). Skip to avoid recursion.
        if payload
            .get("stop_hook_active")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
        {
            context
                .logger
                .debug("stop_hook_active=true, skipping to avoid infinite loop");
            return Ok(StopDispatchOutcome::IntentionallySkipped);
        }

        let session_id = extract_session_id(payload);
        let transcript_path = extract_transcript_path(payload);
        let target_turn_id = payload
            .get("target_turn_id")
            .and_then(serde_json::Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string);

        let (session_id, transcript_path) = if !session_id.is_empty() && !transcript_path.is_empty()
        {
            (session_id, transcript_path)
        } else {
            context.logger.warn("Stop payload missing session_id or transcript_path; refusing cross-session latest-transcript fallback");
            return Ok(StopDispatchOutcome::Deferred(
                "missing session_id or transcript_path".to_string(),
            ));
        };

        if session_id.is_empty() || transcript_path.is_empty() {
            context
                .logger
                .warn("Empty session_id or transcript_path, skipping");
            return Ok(StopDispatchOutcome::Deferred(
                "empty session_id or transcript_path".to_string(),
            ));
        }

        context
            .logger
            .info(&format!("Processing transcript={}", transcript_path));

        // Codex Desktop fires `Stop` (not `SubagentStop`) for child sessions,
        // and can ship a parent's `session_id` with a child's `transcript_path`.
        // Reconcile here before any state mutation: trust the transcript's session_meta as
        // the source of truth, and suppress standalone child export.
        let mut session_id = session_id;
        let meta =
            match try_extract_session_meta_ids_from_path(std::path::Path::new(&transcript_path)) {
                Ok(meta) => meta,
                Err(err) => {
                    context.logger.warn(&format!(
                    "Failed to read transcript identity; leaving it eligible for retry: {} ({})",
                    transcript_path, err
                ));
                    return Ok(StopDispatchOutcome::Deferred(format!(
                        "transcript identity unavailable: {err}"
                    )));
                }
            };
        if let Some(ref transcript_sid) = meta.session_id {
            if transcript_sid != &session_id {
                context.logger.warn(&format!(
                    "Stop: session/transcript mismatch (payload session={} transcript session={}), using transcript id",
                    session_id, transcript_sid
                ));
                if let Some(ref metrics_cell) = context.metrics {
                    metrics_cell.borrow_mut().inc(
                        "aivision.cdxhooks.session_transcript_mismatch",
                        "Session/transcript id mismatch",
                        "{count}",
                        vec![],
                    );
                }
                session_id = transcript_sid.clone();
            }
        }
        if let Some(ref parent_tid) = meta.parent_thread_id {
            // A correlated child's spans belong in the parent trace. Suppress
            // the standalone child export, then opportunistically reprocess
            // the parent so a previously deferred parent does not depend on a
            // second parent Stop arriving after the child becomes readable.
            let transcript_dir = std::path::Path::new(&transcript_path)
                .parent()
                .unwrap_or(std::path::Path::new("."));
            let is_sync = is_child_sync_in_parent(
                transcript_dir,
                parent_tid,
                &session_id,
                meta.agent_path.as_deref(),
            );
            if is_sync {
                context.logger.info(&format!(
                    "Stop: detected subagent child session={} parent={}, skipping (parent will inline)",
                    session_id, parent_tid
                ));
                if let Some(ref metrics_cell) = context.metrics {
                    metrics_cell.borrow_mut().inc(
                        "aivision.cdxhooks.child_stop_skipped",
                        "Subagent child Stop skipped (parent will inline)",
                        "{count}",
                        vec![],
                    );
                }
                if let Some(parent_path) =
                    find_session_transcript(transcript_dir, Some(&context.sessions_dir), parent_tid)
                {
                    context.logger.info(&format!(
                        "Stop: child completion triggering parent retry session={} transcript={}",
                        parent_tid,
                        parent_path.display()
                    ));
                    let mut parent_payload = payload.clone();
                    parent_payload["session_id"] = serde_json::json!(parent_tid);
                    parent_payload["transcript_path"] =
                        serde_json::json!(parent_path.to_string_lossy().to_string());
                    if let Some(parent_payload) = parent_payload.as_object_mut() {
                        // This is an opportunistic parent retry, not the
                        // dispatch represented by the child's durable intent.
                        // A child turn id can never become terminal in the
                        // parent's transcript.
                        for child_intent_field in
                            ["intent_id", "target_turn_id", "observed_file_len"]
                        {
                            parent_payload.remove(child_intent_field);
                        }
                    }
                    let _parent_owner =
                        crate::common::runtime_state::SessionOwner::acquire_blocking(parent_tid)
                            .map_err(HandlerError::from)?;
                    let parent_state_file =
                        dirs::session_state_file(parent_tid).ok_or_else(|| {
                            HandlerError::from(
                                "Stop: invalid parent session state path".to_string(),
                            )
                        })?;
                    let parent_log_file = dirs::session_log_file(parent_tid).ok_or_else(|| {
                        HandlerError::from("Stop: invalid parent session log path".to_string())
                    })?;
                    let parent_context = CommonContext {
                        logger: crate::common::logger::Logger::new(
                            parent_log_file,
                            crate::common::hook::env_is_truthy("CDXHOOKS_DEBUG"),
                        )
                        .with_event(match event {
                            HookEvent::SessionEnd => "SessionEnd",
                            _ => "Stop",
                        }),
                        state_file: parent_state_file,
                        langfuse_config: context.langfuse_config.clone(),
                        username: context.username.clone(),
                        sessions_dir: context.sessions_dir.clone(),
                        hook_version: context.hook_version.clone(),
                        stop_budget: context.stop_budget,
                        retry_policy: context.retry_policy,
                        export_mode: context.export_mode,
                        realtime_llm_batch_size: context.realtime_llm_batch_size,
                        metrics: None,
                    };
                    let parent_outcome =
                        self.handle_stop_event(event, &parent_payload, &parent_context, false)?;
                    if let StopDispatchOutcome::Deferred(reason) = parent_outcome {
                        return Ok(StopDispatchOutcome::Deferred(format!(
                            "correlated parent Stop deferred: {reason}"
                        )));
                    }
                } else {
                    context.logger.warn(&format!(
                        "Stop: correlated parent transcript not found for child session={} parent={}; parent remains retryable",
                        session_id, parent_tid
                    ));
                    return Ok(StopDispatchOutcome::Deferred(
                        "correlated parent transcript not found".to_string(),
                    ));
                }
            } else {
                context.logger.warn(&format!(
                    "Stop: detected subagent child session={} parent={} with NO matching wait_agent in parent transcript — dropping (no upload). \
                     This indicates an orphan child (parent transcript missing or never called wait_agent on this child).",
                    session_id, parent_tid
                ));
                if let Some(ref metrics_cell) = context.metrics {
                    metrics_cell.borrow_mut().inc(
                        "aivision.cdxhooks.child_stop_orphan_dropped",
                        "Subagent child Stop dropped (no matching parent wait_agent)",
                        "{count}",
                        vec![],
                    );
                }
            }
            return Ok(StopDispatchOutcome::IntentionallySkipped);
        }

        let mut state = load_state(&context.state_file);
        let Some(budget) = context.stop_budget else {
            context
                .logger
                .error("Hook budget missing; leaving transcript retryable");
            let now = now_iso8601_pub();
            if let Err(err) = record_pending_export(
                &context.state_file,
                &mut state,
                &session_id,
                "hook_budget_missing",
                &[],
                &now,
                &transcript_path,
            ) {
                context.logger.error(&format!(
                    "Failed to persist missing-budget retry state: {err}"
                ));
            }
            return Ok(StopDispatchOutcome::Deferred(
                "hook budget missing".to_string(),
            ));
        };

        let recovery_intent_id = stop_recovery_intent_id(payload);
        let scheduled_recovery = payload
            .get("recovery_source")
            .and_then(serde_json::Value::as_str)
            == Some("stop_recovery");
        match resume_pending_finalizer_delivery(
            &mut state,
            &session_id,
            &recovery_intent_id,
            &transcript_path,
            context,
            budget,
        ) {
            PendingFinalizerResume::Deferred(reason) => {
                return Ok(StopDispatchOutcome::Deferred(reason));
            }
            PendingFinalizerResume::Completed | PendingFinalizerResume::None => {}
        }

        let session_state = get_session_state(&state, &session_id).cloned();
        let finalizer_high_water = realtime_state::finalizer_high_water(&state, &session_id);

        let prev_offset = session_state
            .as_ref()
            .map(|s| get_int_field(s, "offset"))
            .unwrap_or(0);
        let prev_buffer = session_state
            .as_ref()
            .map(|s| get_str_field(s, "buffer").to_string())
            .unwrap_or_default();
        let prev_pending_messages = session_state
            .as_ref()
            .map(|s| get_array_field(s, "pending_messages"))
            .unwrap_or_default();
        let prev_posted_turn_ids = session_state
            .as_ref()
            .map(|s| get_string_array_field(s, "posted_turn_ids"))
            .unwrap_or_default();
        let target_already_accounted = target_turn_id.as_deref().is_some_and(|turn_id| {
            prev_posted_turn_ids.iter().any(|posted| posted == turn_id)
                || session_state
                    .as_ref()
                    .is_some_and(|session| is_turn_terminally_accounted(session, turn_id))
        });
        let prev_turn_count = session_state
            .as_ref()
            .map(|s| get_int_field(s, "turn_count") as usize)
            .unwrap_or(0);
        let transcript_path_ref = std::path::Path::new(&transcript_path);
        let compaction_cursor =
            match CompactionTokenCursor::from_session(session_state.as_ref(), prev_offset) {
                Some(cursor) => cursor,
                None => {
                    let cursor = initialize_compaction_cursor(
                        transcript_path_ref,
                        prev_offset,
                        &prev_buffer,
                        &prev_pending_messages,
                    );
                    if prev_offset > 0 {
                        persist_compaction_cursor_for_offset(
                            &context.state_file,
                            &mut state,
                            &session_id,
                            prev_offset,
                            &cursor.to_value(),
                        )
                        .map_err(|error| {
                            HandlerError::from(format!(
                                "failed to persist legacy compaction cursor: {error}"
                            ))
                        })?;
                    }
                    cursor
                }
            };
        let initial_compaction_input = if prev_pending_messages.is_empty() {
            compaction_cursor.last_trusted_input_tokens
        } else {
            compaction_cursor.pending_seed_tokens
        };
        let initial_native_usage_mode = if prev_pending_messages.is_empty() {
            compaction_cursor.native_usage_mode
        } else {
            compaction_cursor.pending_native_usage_mode
        };
        let had_partial_checkpoint = session_state
            .as_ref()
            .and_then(|session| session.get("pending_export_reason"))
            .and_then(serde_json::Value::as_str)
            == Some(PARTIAL_EXPORT_REASON);
        let has_pending_export = session_state
            .as_ref()
            .and_then(|session| session.get("pending_export_reason"))
            .and_then(serde_json::Value::as_str)
            .is_some();
        if let Some(recovery) = arm_legacy_finalizer_recovery(
            &context.state_file,
            &mut state,
            &session_id,
            &now_iso8601_pub(),
            &transcript_path,
        )
        .map_err(|error| {
            HandlerError::from(format!("failed to arm legacy finalizer recovery: {error}"))
        })? {
            context.logger.info(&format!(
                "Armed legacy finalizer recovery session={} recovery_key={} attempt={} status={} newly_armed={}",
                session_id, recovery.recovery_key, recovery.attempt, recovery.status, recovery.armed
            ));
        }

        context.logger.debug(&format!(
            "State: offset={}, prev_turns={}",
            prev_offset, prev_turn_count
        ));

        let (
            new_messages,
            new_offset,
            new_buffer,
            pending_messages,
            mut turns,
            build_diagnostics,
            target_terminal_seen,
            transcript_scan_truncated,
        ) = {
            // Poll the current transcript window until its latest turn has an
            // authoritative terminal marker. This avoids a fixed two-second
            // sleep while still covering delayed `task_complete` writes.
            // The shared Stop budget still governs whether a turn build or POST
            // may start after this transcript-settling window.
            // Only authoritative terminal markers make a turn postable.
            // The bounded retry window covers the race where Codex appends
            // `task_complete` / `turn_aborted` just after firing the hook.
            let poll_interval = std::time::Duration::from_millis(STOP_TERMINAL_POLL_INTERVAL_MS);
            let poll_for_terminal =
                wait_for_terminal && matches!(event, HookEvent::Stop | HookEvent::SessionEnd);
            let poll_timeout = budget
                .processing_time_remaining()
                .unwrap_or_default()
                .min(std::time::Duration::from_millis(STOP_TERMINAL_POLL_MAX_MS));
            let poll_deadline = std::time::Instant::now() + poll_timeout;
            let mut poll_count = 0u32;
            let mut current_offset = prev_offset;
            let mut current_buffer = prev_buffer.clone();
            let mut all_messages = Vec::new();
            let mut cached_build: Option<PostableTurnBuild> = None;
            let mut transcript_scan_truncated = false;
            loop {
                let (msgs, off, buf, has_more) = if scheduled_recovery {
                    read_new_jsonl_limited(
                        std::path::Path::new(&transcript_path),
                        current_offset,
                        &current_buffer,
                        STOP_RECOVERY_TRANSCRIPT_SCAN_MAX_BYTES,
                    )
                } else {
                    let (msgs, off, buf) = read_new_jsonl(
                        std::path::Path::new(&transcript_path),
                        current_offset,
                        &current_buffer,
                    );
                    (msgs, off, buf, false)
                };
                transcript_scan_truncated |= has_more;

                let messages_changed = !msgs.is_empty();
                if !msgs.is_empty() {
                    all_messages.extend(msgs);
                }
                current_offset = off;
                current_buffer = buf;

                refresh_postable_turn_cache(
                    &mut cached_build,
                    messages_changed,
                    &prev_pending_messages,
                    &all_messages,
                    &prev_posted_turn_ids,
                    initial_compaction_input,
                    initial_native_usage_mode,
                );
                let final_build = cached_build
                    .as_ref()
                    .expect("retry build must be initialized");

                // A complete prefix is not enough: the latest retained turn
                // must also be terminal, otherwise Stop can miss its own tail.
                let target_terminal_seen = target_already_accounted
                    || target_turn_id
                        .as_deref()
                        .map_or(final_build.terminal_seen, |turn_id| {
                            has_authoritative_terminal_for_turn(&prev_pending_messages, turn_id)
                                || has_authoritative_terminal_for_turn(&all_messages, turn_id)
                                || final_build.turns.iter().any(|turn| {
                                    turn.turn_id == turn_id
                                        && turn.terminal_state == TurnTerminalState::Superseded
                                })
                        });
                let checkpoint_window_resolved =
                    had_partial_checkpoint && final_build.pending_messages.is_empty();
                let legacy_complete_prefix_ready =
                    target_turn_id.is_none() && !final_build.turns.is_empty();
                let legacy_terminal_evidence = target_turn_id.is_none()
                    && (has_authoritative_terminal(&prev_pending_messages)
                        || has_authoritative_terminal(&all_messages));
                let legacy_no_pending_work = target_turn_id.is_none()
                    && prev_pending_messages.is_empty()
                    && all_messages.is_empty()
                    && current_buffer.is_empty();

                if target_terminal_seen
                    || checkpoint_window_resolved
                    || legacy_complete_prefix_ready
                    || legacy_terminal_evidence
                    || legacy_no_pending_work
                    || !poll_for_terminal
                    || !budget.can_start_turn()
                    || std::time::Instant::now() >= poll_deadline
                {
                    if poll_count > 0 {
                        context.logger.debug(&format!(
                            "Transcript terminal poll finished after {} checks: target_terminal_seen={}",
                            poll_count, target_terminal_seen
                        ));
                    }
                    break;
                }

                let sleep_for = poll_deadline
                    .saturating_duration_since(std::time::Instant::now())
                    .min(poll_interval);
                if sleep_for.is_zero() {
                    break;
                }
                poll_count += 1;
                context.logger.debug(&format!(
                    "Transcript terminal not visible; poll={} waiting {}ms",
                    poll_count,
                    sleep_for.as_millis()
                ));
                std::thread::sleep(sleep_for);
            }

            let final_build = cached_build.unwrap_or_else(|| {
                build_postable_turn_outcome_with_initial_input(
                    &prev_pending_messages,
                    &all_messages,
                    &prev_posted_turn_ids,
                    initial_compaction_input,
                    initial_native_usage_mode,
                )
            });
            let target_terminal_seen = target_already_accounted
                || target_turn_id
                    .as_deref()
                    .map_or(final_build.terminal_seen, |turn_id| {
                        has_authoritative_terminal_for_turn(&prev_pending_messages, turn_id)
                            || has_authoritative_terminal_for_turn(&all_messages, turn_id)
                            || final_build.turns.iter().any(|turn| {
                                turn.turn_id == turn_id
                                    && turn.terminal_state == TurnTerminalState::Superseded
                            })
                    });
            (
                all_messages,
                current_offset,
                current_buffer,
                final_build.pending_messages,
                final_build.turns,
                final_build.diagnostics,
                target_terminal_seen,
                transcript_scan_truncated,
            )
        };

        let next_compaction_cursor = advance_compaction_cursor(
            compaction_cursor,
            new_offset,
            initial_compaction_input,
            &prev_pending_messages,
            &new_messages,
            &pending_messages,
        );

        let mut finalizer_accounting_history = None;
        let has_realtime_history = !realtime_state::contexts(&state, &session_id).is_empty()
            || state
                .get(&session_id)
                .and_then(|session| session.get("realtime_export"))
                .and_then(|realtime| realtime.get("generation"))
                .and_then(|generation| generation.get("rounds"))
                .and_then(serde_json::Value::as_array)
                .is_some_and(|rounds| !rounds.is_empty());
        if !turns.is_empty()
            && prev_turn_count > prev_posted_turn_ids.len()
            && (prev_offset == 0 || had_partial_checkpoint || has_realtime_history)
        {
            if scheduled_recovery {
                return Ok(StopDispatchOutcome::Deferred(
                    "scheduled recovery requires unbounded retention history rebuild".to_string(),
                ));
            }
            let (history_messages, _, _) =
                read_new_jsonl(std::path::Path::new(&transcript_path), 0, "");
            let (completed_history, _) = split_completed_messages(&history_messages);
            let history_turns = build_turns(&completed_history);
            let Some(session) = session_state.as_ref() else {
                return Ok(StopDispatchOutcome::Deferred(
                    "durable success history unavailable for retention recovery".to_string(),
                ));
            };
            let historical_success_candidates = history_turns
                .iter()
                .filter(|turn| !is_turn_terminally_accounted(session, &turn.turn_id))
                .count();
            if historical_success_candidates < prev_turn_count {
                return Ok(StopDispatchOutcome::Deferred(
                    "durable success history is incomplete for retention recovery".to_string(),
                ));
            }
            let accounted = durably_accounted_turn_ids_for_history(
                session,
                history_turns.iter().map(|turn| turn.turn_id.as_str()),
            );
            turns.retain(|turn| !accounted.contains(&turn.turn_id));
            finalizer_accounting_history = Some(history_turns);
        }

        if new_messages.is_empty()
            && turns.is_empty()
            && !had_partial_checkpoint
            && prev_pending_messages.is_empty()
            && !transcript_scan_truncated
        {
            context.logger.debug("No new messages");
            return if let Some(reason) =
                target_terminal_pending_reason(target_turn_id.as_deref(), target_terminal_seen)
            {
                Ok(StopDispatchOutcome::Deferred(reason))
            } else if has_pending_export {
                Ok(StopDispatchOutcome::Deferred(
                    "pending export checkpoint still requires recovery".to_string(),
                ))
            } else {
                Ok(StopDispatchOutcome::Completed)
            };
        }

        context
            .logger
            .debug(&format!("Read {} new messages", new_messages.len()));

        if turns.is_empty() {
            if !build_diagnostics.is_empty() {
                context.logger.debug(&format!(
                    "Postable turn diagnostics: {}",
                    build_diagnostics.join(", ")
                ));
            }
            context.logger.debug(&format!(
                "No complete turns built, saving offset with {} pending messages",
                pending_messages.len()
            ));
            let now = now_iso8601_pub();
            let save_result = if had_partial_checkpoint {
                commit_export_success_with_compaction_cursor(
                    &context.state_file,
                    &mut state,
                    &session_id,
                    new_offset,
                    &new_buffer,
                    &pending_messages,
                    &prev_posted_turn_ids,
                    prev_turn_count,
                    &now,
                    Some(&transcript_path),
                    &[],
                    &[],
                    &next_compaction_cursor.to_value(),
                )
            } else {
                update_state_and_save_with_compaction_cursor(
                    &context.state_file,
                    &mut state,
                    &session_id,
                    new_offset,
                    &new_buffer,
                    &pending_messages,
                    &prev_posted_turn_ids,
                    prev_turn_count,
                    &now,
                    Some(&transcript_path),
                    &next_compaction_cursor.to_value(),
                )
            };
            if let Err(err) = save_result {
                context
                    .logger
                    .error(&format!("Failed to save pending transcript state: {}", err));
                return Ok(StopDispatchOutcome::Deferred(format!(
                    "pending transcript state save failed: {err}"
                )));
            }
            return if transcript_scan_truncated {
                Ok(StopDispatchOutcome::Deferred(
                    "transcript scan slice exhausted".to_string(),
                ))
            } else if let Some(reason) =
                target_terminal_pending_reason(target_turn_id.as_deref(), target_terminal_seen)
            {
                Ok(StopDispatchOutcome::Deferred(reason))
            } else if pending_messages.is_empty() {
                Ok(StopDispatchOutcome::Completed)
            } else {
                Ok(StopDispatchOutcome::Deferred(format!(
                    "transcript still has pending messages{}",
                    if build_diagnostics.is_empty() {
                        String::new()
                    } else {
                        format!(": {}", build_diagnostics.join(", "))
                    }
                )))
            };
        }

        context
            .logger
            .info(&format!("Built {} new turns", turns.len()));

        let payload_cwd = payload
            .get("cwd")
            .and_then(serde_json::Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty());
        let vcs_info = get_vcs_for_cwd(&state, &session_id, payload_cwd);

        let transcript_dir = std::path::Path::new(&transcript_path)
            .parent()
            .unwrap_or(std::path::Path::new("."));
        let outcome = export_turns_serially(
            &turns,
            finalizer_accounting_history.as_deref().unwrap_or(&turns),
            &mut state,
            &session_id,
            transcript_dir,
            &transcript_path,
            new_offset,
            finalizer_high_water,
            Some(&recovery_intent_id),
            scheduled_recovery,
            Some(&context.sessions_dir),
            vcs_info.as_ref(),
            context,
        );
        if !outcome.finalized_window() {
            return Ok(StopDispatchOutcome::Deferred(format!(
                "turn export incomplete: {outcome:?}"
            )));
        }
        let terminal_accounted = outcome.terminal_accounted();
        let exported_turns = outcome.exported_turns();

        let final_session = get_session_state(&state, &session_id)
            .cloned()
            .unwrap_or_default();
        let posted_turn_ids = get_string_array_field(&final_session, "posted_turn_ids");
        let new_turn_count = get_int_field(&final_session, "turn_count") as usize;
        let now = now_iso8601_pub();
        if let Err(err) = commit_export_success_with_compaction_cursor(
            &context.state_file,
            &mut state,
            &session_id,
            new_offset,
            &new_buffer,
            &pending_messages,
            &posted_turn_ids,
            new_turn_count,
            &now,
            Some(&transcript_path),
            &[],
            &[],
            &next_compaction_cursor.to_value(),
        ) {
            context.logger.error(&format!(
                "All turn checkpoints succeeded but final offset commit failed; next Stop will finalize from posted_turn_ids: {}",
                err
            ));
            return Ok(StopDispatchOutcome::Deferred(format!(
                "final export checkpoint failed: {err}"
            )));
        }

        context.logger.debug(&format!(
            "Done. total_turns={}, offset={}",
            new_turn_count, new_offset
        ));

        if transcript_scan_truncated {
            Ok(StopDispatchOutcome::Deferred(
                "transcript scan slice exhausted".to_string(),
            ))
        } else if let Some(reason) =
            target_terminal_pending_reason(target_turn_id.as_deref(), target_terminal_seen)
        {
            Ok(StopDispatchOutcome::Deferred(reason))
        } else if !pending_messages.is_empty() {
            Ok(StopDispatchOutcome::Deferred(
                "transcript still has pending messages after export".to_string(),
            ))
        } else if terminal_accounted {
            Ok(StopDispatchOutcome::TerminallyAccounted { exported_turns })
        } else {
            Ok(StopDispatchOutcome::Completed)
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StopDispatchOutcome {
    Completed,
    Deferred(String),
    IntentionallySkipped,
    TerminallyAccounted { exported_turns: bool },
}

impl StopDispatchOutcome {
    pub fn clears_intent(&self) -> bool {
        matches!(
            self,
            Self::Completed | Self::IntentionallySkipped | Self::TerminallyAccounted { .. }
        )
    }

    pub fn completed_export(&self) -> bool {
        matches!(
            self,
            Self::Completed
                | Self::TerminallyAccounted {
                    exported_turns: true
                }
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TurnExportOutcome {
    Completed,
    TerminallyAccounted { exported_turns: bool },
    SessionBlocked,
    BudgetExhausted,
    GlobalFailure,
}

impl TurnExportOutcome {
    fn finalized_window(&self) -> bool {
        matches!(self, Self::Completed | Self::TerminallyAccounted { .. })
    }

    fn terminal_accounted(&self) -> bool {
        matches!(self, Self::TerminallyAccounted { .. })
    }

    fn exported_turns(&self) -> bool {
        matches!(
            self,
            Self::Completed
                | Self::TerminallyAccounted {
                    exported_turns: true
                }
        )
    }
}

pub(crate) enum ChildMergeOutcome {
    Completed(crate::common::otel::MergeResult),
    BudgetExhausted,
}

use super::turn_trace_identity::apply_exact_trace_root_metadata;
pub(crate) use super::turn_trace_identity::build_turn_trace_opts;

#[cfg(test)]
fn build_finalizer_turn_trace_opts(
    state: &serde_json::Value,
    session_id: &str,
    turns: &[Turn],
    turn_index: usize,
) -> Result<Option<TurnReplayOpts>, String> {
    let scan_base_ordinal =
        crate::hooks::langfuse::realtime_state::generation_scan_base_ordinal(state, session_id);
    let mut trace_opts = build_turn_trace_opts(state, session_id, &turns[turn_index].turn_id);
    if let Some((trace_id, root_span_id)) = finalizer_trace_root_for_turn_with_scan_base(
        state,
        session_id,
        turns,
        turn_index,
        scan_base_ordinal,
    )? {
        apply_exact_trace_root_metadata(state, session_id, &mut trace_opts, trace_id, root_span_id);
    }
    let generation_span_ids = finalizer_generation_span_ids_for_turn_with_scan_base(
        state,
        session_id,
        turns,
        turn_index,
        u64::MAX,
        scan_base_ordinal,
    );
    if !generation_span_ids.is_empty() {
        trace_opts
            .get_or_insert_with(TurnReplayOpts::default)
            .generation_span_ids
            .extend(generation_span_ids);
    }
    Ok(trace_opts)
}

fn load_parent_correlation_turns(transcript_path: &str, current_turns: &[Turn]) -> Vec<Turn> {
    let (messages, _, _) = read_new_jsonl(std::path::Path::new(transcript_path), 0, "");
    let (completed_messages, _) = split_completed_messages(&messages);
    let mut turns = build_turns(&completed_messages);

    // The full snapshot may include a trailing in-progress turn that should
    // not influence parent/child correlation. Retain already parsed postable
    // current turns if the writer changes between the incremental read and
    // this full snapshot.
    for current in current_turns {
        if !turns.iter().any(|turn| turn.turn_id == current.turn_id) {
            turns.push(current.clone());
        }
    }
    turns
}

fn turns_need_parent_correlation(turns: &[Turn]) -> bool {
    turns.iter().any(|turn| {
        !turn.collab_agent_events.is_empty()
            || turn.tool_calls.iter().any(|tool| {
                matches!(
                    tool.name.as_str(),
                    "spawn_agent"
                        | "followup_task"
                        | "send_message"
                        | "wait_agent"
                        | "wait"
                        | "interrupt_agent"
                )
            })
    })
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum FinalizerDeliveryOutcome {
    Completed,
    Retryable(String),
    Terminal(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum PendingFinalizerResume {
    None,
    Completed,
    Deferred(String),
}

#[derive(Debug, Clone, Copy)]
struct FinalizerRecoveryDeliveryContext<'a> {
    intent_id: &'a str,
    observed_file_len: u64,
    window_end_offset: i64,
    finalizer_high_water: u64,
    generation_count: u64,
    merged_sync_ids: &'a [String],
    merged_subagent_invocation_ids: &'a [String],
}

#[allow(clippy::too_many_arguments)]
fn deliver_finalizer_body_in_chunks(
    body: &serde_json::Value,
    state: &mut serde_json::Value,
    session_id: &str,
    turn_id: &str,
    trigger_id: &str,
    context: &CommonContext,
    budget: StopBudget,
    recovery: Option<FinalizerRecoveryDeliveryContext<'_>>,
) -> FinalizerDeliveryOutcome {
    let (split_spans, root_span_id) = match extract_finalizer_spans_root_last(body) {
        Ok(result) => result,
        Err(error) => return FinalizerDeliveryOutcome::Terminal(error),
    };
    let serialized_body_len = match serde_json::to_vec(body) {
        Ok(serialized) => serialized.len(),
        Err(error) => {
            return FinalizerDeliveryOutcome::Terminal(format!(
                "failed to serialize finalizer body: {error}"
            ));
        }
    };
    let semantic_spans = if serialized_body_len <= REALTIME_TARGET_SERIALIZED_BODY_BYTES {
        let semantic_body = match finalizer_semantic_body(turn_id, &root_span_id, body) {
            Ok(body) => body,
            Err(error) => return FinalizerDeliveryOutcome::Terminal(error),
        };
        vec![semantic_body]
    } else {
        split_spans
    };
    let child_ids = semantic_spans
        .iter()
        .map(|child| child.child_id.clone())
        .collect::<Vec<_>>();
    let child_digests = semantic_spans
        .iter()
        .map(|child| child.payload_digest.clone())
        .collect::<Vec<_>>();
    let plan_id = realtime_state::stable_realtime_delivery_plan_id(
        session_id,
        turn_id,
        trigger_id,
        &child_ids,
        &child_digests,
    );
    let payload_plan =
        match plan_realtime_payload_chunks(semantic_spans, REALTIME_TARGET_SERIALIZED_BODY_BYTES) {
            Ok(plan) => plan,
            Err(error) => return FinalizerDeliveryOutcome::Terminal(error),
        };
    if let Some(oversized) = payload_plan.oversized_children.first() {
        let body_bytes = match serde_json::to_vec(&oversized.body) {
            Ok(serialized) => serialized.len(),
            Err(error) => {
                return FinalizerDeliveryOutcome::Terminal(format!(
                    "failed to serialize oversized finalizer span: {error}"
                ));
            }
        };
        return FinalizerDeliveryOutcome::Terminal(format!(
            "stop_finalizer_span_oversize: span_id={} body_bytes={} limit_bytes={}",
            oversized.span_id, body_bytes, REALTIME_TARGET_SERIALIZED_BODY_BYTES
        ));
    }
    let chunks = payload_plan.chunks;
    let plan = realtime_state::RealtimeDeliveryPlan {
        schema_version: realtime_state::REALTIME_DELIVERY_PLAN_SCHEMA_VERSION,
        plan_id: plan_id.clone(),
        context_id: turn_id.to_string(),
        trigger_id: trigger_id.to_string(),
        chunks: chunks
            .iter()
            .map(|chunk| realtime_state::RealtimeDeliveryChunkPlanEntry {
                cursor: chunk.cursor,
                batch_id: format!("finalizer:{plan_id}:{}", chunk.cursor),
                payload_digest: chunk.payload_digest.clone(),
                body_len: chunk.body_len,
                child_ids: chunk.child_ids.clone(),
                span_ids: chunk.span_ids.clone(),
                digests: chunk.digests.clone(),
                body: Some(chunk.body.clone()),
                status: realtime_state::RealtimeDeliveryChunkStatus::Queued,
                last_error: None,
            })
            .collect(),
        oversized_child_ids: Vec::new(),
        next_cursor: 0,
        complete: false,
        terminal_reason: None,
    };
    if let Err(error) = update_state_transaction_fallible(&context.state_file, state, |candidate| {
        realtime_state::prepare_realtime_delivery_plan_in_state(candidate, session_id, &plan)
            .map(|_| ())
    }) {
        return FinalizerDeliveryOutcome::Retryable(format!(
            "failed to persist finalizer delivery plan: {error}"
        ));
    }

    if let Some(recovery) = recovery {
        let current_cursor = realtime_state::realtime_delivery_plan(state, session_id, &plan_id)
            .map(|current| current.next_cursor)
            .unwrap_or(0);
        let checkpoint = StopRecoveryDeliveryCheckpoint {
            plan_id: plan_id.clone(),
            chunk_cursor: current_cursor,
            payload_digest: plan_id.clone(),
            turn_id: turn_id.to_string(),
            root_span_id: root_span_id.clone(),
            window_end_offset: recovery.window_end_offset,
            finalizer_high_water: recovery.finalizer_high_water,
            generation_count: recovery.generation_count,
            merged_sync_ids: recovery.merged_sync_ids.to_vec(),
            merged_subagent_invocation_ids: recovery.merged_subagent_invocation_ids.to_vec(),
        };
        if let Err(error) = checkpoint_stop_recovery_delivery(
            &context.state_file,
            state,
            session_id,
            recovery.intent_id,
            recovery.observed_file_len,
            &checkpoint,
            &now_iso8601_pub(),
        ) {
            return FinalizerDeliveryOutcome::Retryable(format!(
                "failed to checkpoint Stop delivery plan: {error}"
            ));
        }
    }

    deliver_prepared_finalizer_plan(
        state,
        session_id,
        turn_id,
        &plan_id,
        &root_span_id,
        context,
        budget,
        recovery,
    )
}

#[allow(clippy::too_many_arguments)]
fn deliver_prepared_finalizer_plan(
    state: &mut serde_json::Value,
    session_id: &str,
    turn_id: &str,
    plan_id: &str,
    root_span_id: &str,
    context: &CommonContext,
    budget: StopBudget,
    recovery: Option<FinalizerRecoveryDeliveryContext<'_>>,
) -> FinalizerDeliveryOutcome {
    let Some(langfuse_config) = context.langfuse_config.as_ref() else {
        return FinalizerDeliveryOutcome::Retryable("missing Langfuse config".to_string());
    };
    let retry_policy = context.retry_policy;
    loop {
        let Some(current) = realtime_state::realtime_delivery_plan(state, session_id, plan_id)
        else {
            return FinalizerDeliveryOutcome::Retryable(format!(
                "finalizer delivery plan disappeared: {plan_id}"
            ));
        };
        if retry_policy == RetryPolicy::AtMostOnce
            && current
                .chunks
                .get(current.next_cursor as usize)
                .is_some_and(|chunk| {
                    chunk.status == realtime_state::RealtimeDeliveryChunkStatus::PolicyTerminal
                })
        {
            if let Err(error) =
                update_state_transaction_fallible(&context.state_file, state, |candidate| {
                    realtime_state::settle_interrupted_policy_terminal_plan_in_state(
                        candidate, session_id, &plan_id,
                    )
                    .map(|_| ())
                })
            {
                return FinalizerDeliveryOutcome::Retryable(format!(
                    "failed to settle interrupted at-most-once finalizer chunk: {error}"
                ));
            }
            continue;
        }
        if current.complete {
            let terminal = current.chunks.iter().any(|chunk| {
                matches!(
                    chunk.status,
                    realtime_state::RealtimeDeliveryChunkStatus::PolicyTerminal
                        | realtime_state::RealtimeDeliveryChunkStatus::PermanentFailed
                )
            });
            return if terminal {
                if retry_policy == RetryPolicy::AtMostOnce {
                    FinalizerDeliveryOutcome::Completed
                } else {
                    FinalizerDeliveryOutcome::Terminal(format!(
                        "stop_finalizer_delivery_terminal: plan_id={plan_id} root_span_id={root_span_id}"
                    ))
                }
            } else {
                FinalizerDeliveryOutcome::Completed
            };
        }
        let cursor = current.next_cursor;
        let Some(chunk) = current.chunks.get(cursor as usize) else {
            return FinalizerDeliveryOutcome::Retryable(format!(
                "finalizer delivery chunk body missing: plan_id={plan_id} cursor={cursor}"
            ));
        };
        let Some(chunk_body) = chunk.body.as_ref() else {
            return FinalizerDeliveryOutcome::Retryable(format!(
                "finalizer delivery chunk payload is not durable: plan_id={plan_id} cursor={cursor}"
            ));
        };
        if let Err(error) =
            validate_persisted_payload(chunk_body, &chunk.payload_digest, chunk.body_len)
        {
            return FinalizerDeliveryOutcome::Terminal(format!(
                "stop_finalizer_delivery_plan_corrupt: plan_id={plan_id} cursor={cursor} {error}"
            ));
        }
        let Some(timeout) = budget.http_timeout() else {
            return FinalizerDeliveryOutcome::Retryable(format!(
                "{HOOK_BUDGET_EXHAUSTED_REASON}: finalizer delivery plan_id={plan_id} next_cursor={cursor}"
            ));
        };
        if let Err(error) =
            update_state_transaction_fallible(&context.state_file, state, |candidate| {
                realtime_state::begin_realtime_delivery_plan_chunks_in_state(
                    candidate,
                    session_id,
                    plan_id,
                    &[cursor],
                    retry_policy,
                )
                .map(|_| ())
            })
        {
            return FinalizerDeliveryOutcome::Retryable(format!(
                "failed to checkpoint finalizer chunk begin: {error}"
            ));
        }
        context.logger.info(&format!(
            "event=stop_finalizer_chunk operation=delivery outcome=attempt plan_id={} cursor={} body_bytes={} root_last={}",
            plan_id,
            cursor,
            chunk.body_len,
            chunk.span_ids.iter().any(|span_id| span_id == &root_span_id)
        ));
        let posted = post_otel_to_langfuse_once_classified(
            langfuse_config,
            chunk_body,
            &context.logger,
            timeout,
        );
        let (accepted, permanent, error_message) = match posted {
            Ok(()) => (true, false, None),
            Err(error) => {
                context.logger.error(&format!(
                    "Failed to post session={} turn_id={} to Langfuse (OTel): {}",
                    session_id, turn_id, error
                ));
                // Preserve the established finalizer contract: transport and
                // HTTP failures remain retryable under AtLeastOnce. Permanent
                // terminal accounting is reserved for structural/oversize
                // payload failures; AtMostOnce is terminal by policy.
                let permanent = false;
                (false, permanent, Some(error.to_string()))
            }
        };
        if let Err(error) =
            update_state_transaction_fallible(&context.state_file, state, |candidate| {
                realtime_state::finish_realtime_delivery_plan_chunk_in_state(
                    candidate,
                    session_id,
                    plan_id,
                    cursor,
                    retry_policy,
                    accepted,
                    permanent,
                    error_message.as_deref(),
                )
                .map(|_| ())
            })
        {
            return FinalizerDeliveryOutcome::Retryable(format!(
                "failed to checkpoint finalizer chunk finish: {error}"
            ));
        }
        if let Some(recovery) = recovery {
            let next_cursor = realtime_state::realtime_delivery_plan(state, session_id, plan_id)
                .map(|plan| plan.next_cursor)
                .unwrap_or(cursor);
            let checkpoint = StopRecoveryDeliveryCheckpoint {
                plan_id: plan_id.to_string(),
                chunk_cursor: next_cursor,
                payload_digest: plan_id.to_string(),
                turn_id: turn_id.to_string(),
                root_span_id: root_span_id.to_string(),
                window_end_offset: recovery.window_end_offset,
                finalizer_high_water: recovery.finalizer_high_water,
                generation_count: recovery.generation_count,
                merged_sync_ids: recovery.merged_sync_ids.to_vec(),
                merged_subagent_invocation_ids: recovery.merged_subagent_invocation_ids.to_vec(),
            };
            if let Err(error) = checkpoint_stop_recovery_delivery(
                &context.state_file,
                state,
                session_id,
                recovery.intent_id,
                recovery.observed_file_len,
                &checkpoint,
                &now_iso8601_pub(),
            ) {
                return FinalizerDeliveryOutcome::Retryable(format!(
                    "failed to checkpoint Stop delivery cursor: {error}"
                ));
            }
        }
        if !accepted && retry_policy == RetryPolicy::AtLeastOnce && !permanent {
            return FinalizerDeliveryOutcome::Retryable(
                error_message.unwrap_or_else(|| "finalizer chunk delivery failed".to_string()),
            );
        }
    }
}

fn resume_pending_finalizer_delivery(
    state: &mut serde_json::Value,
    session_id: &str,
    intent_id: &str,
    transcript_path: &str,
    context: &CommonContext,
    budget: StopBudget,
) -> PendingFinalizerResume {
    let Some(record) = stop_recovery_record(state, session_id) else {
        return PendingFinalizerResume::None;
    };
    let (Some(plan_id), Some(turn_id), Some(root_span_id)) = (
        record.delivery_plan_id.as_deref(),
        record.delivery_turn_id.as_deref(),
        record.delivery_root_span_id.as_deref(),
    ) else {
        return PendingFinalizerResume::None;
    };
    if record.intent_id != intent_id {
        let previous_intent_id = record.intent_id.clone();
        match adopt_stop_recovery_delivery_intent(
            &context.state_file,
            state,
            session_id,
            &previous_intent_id,
            plan_id,
            intent_id,
            &now_iso8601_pub(),
        ) {
            Ok(StopRecoveryDeliveryAdoption::Adopted) => context.logger.info(&format!(
                "Adopted persisted finalizer delivery across Stop intents session={} previous_intent={} current_intent={} plan_id={}",
                session_id, previous_intent_id, intent_id, plan_id
            )),
            Ok(StopRecoveryDeliveryAdoption::AlreadyOwned) => context.logger.info(&format!(
                "Persisted finalizer delivery already belongs to current Stop intent session={} current_intent={} plan_id={}",
                session_id, intent_id, plan_id
            )),
            Ok(StopRecoveryDeliveryAdoption::Cleared) => {
                return PendingFinalizerResume::None;
            }
            Ok(StopRecoveryDeliveryAdoption::Changed) => {
                return PendingFinalizerResume::Deferred(
                    "pending finalizer delivery ownership changed during cross-intent adoption"
                        .to_string(),
                );
            }
            Err(error) => {
                return PendingFinalizerResume::Deferred(format!(
                    "failed to adopt pending finalizer delivery for current Stop intent: {error}"
                ));
            }
        }
    }
    let recovery = FinalizerRecoveryDeliveryContext {
        intent_id,
        observed_file_len: record.observed_file_len,
        window_end_offset: record.delivery_window_end_offset,
        finalizer_high_water: record.delivery_finalizer_high_water,
        generation_count: record.delivery_generation_count,
        merged_sync_ids: &record.delivery_merged_sync_ids,
        merged_subagent_invocation_ids: &record.delivery_merged_subagent_invocation_ids,
    };
    match deliver_prepared_finalizer_plan(
        state,
        session_id,
        turn_id,
        plan_id,
        root_span_id,
        context,
        budget,
        Some(recovery),
    ) {
        FinalizerDeliveryOutcome::Retryable(reason) => PendingFinalizerResume::Deferred(reason),
        FinalizerDeliveryOutcome::Terminal(reason) => {
            let now = now_iso8601_pub();
            if let Err(error) = record_terminal_quarantined_turn(
                &context.state_file,
                state,
                session_id,
                turn_id,
                &reason,
                &[],
                &now,
                1,
                Some(plan_id),
                Some(record.delivery_window_end_offset),
            ) {
                return PendingFinalizerResume::Deferred(format!(
                    "failed to persist resumed finalizer quarantine: {error}"
                ));
            }
            if let Err(error) = clear_stop_recovery_delivery(
                &context.state_file,
                state,
                session_id,
                intent_id,
                &now,
            ) {
                return PendingFinalizerResume::Deferred(format!(
                    "failed to clear terminal finalizer delivery: {error}"
                ));
            }
            PendingFinalizerResume::Completed
        }
        FinalizerDeliveryOutcome::Completed => {
            let now = now_iso8601_pub();
            if let Err(error) = checkpoint_turn_export_success_maybe_faulted(
                &context.state_file,
                state,
                session_id,
                turn_id,
                &now,
                Some(transcript_path),
                &record.delivery_merged_sync_ids,
                &record.delivery_merged_subagent_invocation_ids,
            ) {
                return PendingFinalizerResume::Deferred(format!(
                    "resumed finalizer succeeded but turn checkpoint failed: {error}"
                ));
            }
            if let Err(error) = finalize_realtime_high_water(
                &context.state_file,
                state,
                session_id,
                record.delivery_finalizer_high_water,
                record.delivery_window_end_offset.max(0) as u64,
                record.delivery_generation_count,
            ) {
                return PendingFinalizerResume::Deferred(format!(
                    "resumed finalizer high-water checkpoint failed: {error}"
                ));
            }
            if let Err(error) = clear_stop_recovery_delivery(
                &context.state_file,
                state,
                session_id,
                intent_id,
                &now,
            ) {
                return PendingFinalizerResume::Deferred(format!(
                    "failed to clear completed finalizer delivery: {error}"
                ));
            }
            context.logger.info(&format!(
                "Resumed persisted finalizer delivery without transcript rebuild session={} turn_id={} plan_id={}",
                session_id, turn_id, plan_id
            ));
            PendingFinalizerResume::Completed
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn export_turns_serially(
    turns: &[Turn],
    accounting_history: &[Turn],
    state: &mut serde_json::Value,
    session_id: &str,
    transcript_dir: &std::path::Path,
    transcript_path: &str,
    window_end_offset: i64,
    finalizer_high_water: u64,
    recovery_intent_id: Option<&str>,
    scheduled_recovery: bool,
    sessions_dir: Option<&str>,
    vcs: Option<&VcsInfo>,
    context: &CommonContext,
) -> TurnExportOutcome {
    let Some(budget) = context.stop_budget else {
        context
            .logger
            .error("Hook budget missing from hook context; leaving turns eligible for retry");
        return TurnExportOutcome::GlobalFailure;
    };
    if context.langfuse_config.is_none() {
        return TurnExportOutcome::GlobalFailure;
    }
    let generation_count = accounting_history
        .iter()
        .map(Turn::semantic_assistant_count)
        .sum::<usize>() as u64;
    if !budget.can_start_turn() {
        let now = now_iso8601_pub();
        let _ = record_pending_export(
            &context.state_file,
            state,
            session_id,
            HOOK_BUDGET_EXHAUSTED_REASON,
            &[],
            &now,
            transcript_path,
        );
        return TurnExportOutcome::BudgetExhausted;
    }
    let parent_correlation_turns = if turns_need_parent_correlation(turns) {
        load_parent_correlation_turns(transcript_path, turns)
    } else {
        Vec::new()
    };
    if !budget.can_start_turn() {
        let now = now_iso8601_pub();
        let _ = record_pending_export(
            &context.state_file,
            state,
            session_id,
            HOOK_BUDGET_EXHAUSTED_REASON,
            &[],
            &now,
            transcript_path,
        );
        return TurnExportOutcome::BudgetExhausted;
    }
    let mut finalizer_trace_opts = Vec::with_capacity(turns.len());
    let mut terminally_accounted_turn_ids = std::collections::HashSet::new();
    let mut recovery_topology_checkpoints = recovery_intent_id
        .and_then(|intent_id| {
            stop_recovery_record(state, session_id).filter(|record| record.intent_id == intent_id)
        })
        .map(|record| record.topology_checkpoints)
        .unwrap_or_default();
    for (turn_index, turn) in turns.iter().enumerate() {
        if !budget.can_start_turn() {
            let now = now_iso8601_pub();
            context.logger.warn(&format!(
                "Hook budget exhausted before topology build: session={} turn_id={} remaining={}ms",
                session_id,
                turn.turn_id,
                budget.remaining().as_millis(),
            ));
            let _ = record_pending_export(
                &context.state_file,
                state,
                session_id,
                HOOK_BUDGET_EXHAUSTED_REASON,
                &[],
                &now,
                transcript_path,
            );
            return TurnExportOutcome::BudgetExhausted;
        }
        if let Some(checkpoint) = recovery_topology_checkpoints
            .iter()
            .find(|checkpoint| checkpoint.turn_id == turn.turn_id)
            .cloned()
        {
            match checkpoint.kind {
                StopRecoveryTopologyKind::TerminalQuarantined => {
                    terminally_accounted_turn_ids.insert(turn.turn_id.clone());
                    finalizer_trace_opts.push(None);
                }
                StopRecoveryTopologyKind::LegacyFallback | StopRecoveryTopologyKind::Exact => {
                    let scan_base_ordinal =
                        realtime_state::generation_scan_base_ordinal(state, session_id);
                    let mut trace_opts = build_turn_trace_opts(state, session_id, &turn.turn_id);
                    let generation_span_ids = finalizer_generation_span_ids_for_turn_with_scan_base(
                        state,
                        session_id,
                        turns,
                        turn_index,
                        finalizer_high_water,
                        scan_base_ordinal,
                    );
                    if !generation_span_ids.is_empty() {
                        trace_opts
                            .get_or_insert_with(TurnReplayOpts::default)
                            .generation_span_ids
                            .extend(generation_span_ids);
                    }
                    if checkpoint.kind == StopRecoveryTopologyKind::Exact
                        || (checkpoint.trace_id.is_some() && checkpoint.root_span_id.is_some())
                    {
                        let Some(trace_id) = checkpoint.trace_id else {
                            return TurnExportOutcome::GlobalFailure;
                        };
                        let Some(root_span_id) = checkpoint.root_span_id else {
                            return TurnExportOutcome::GlobalFailure;
                        };
                        apply_exact_trace_root_metadata(
                            state,
                            session_id,
                            &mut trace_opts,
                            trace_id,
                            root_span_id,
                        );
                    } else if let Some(intent_id) = recovery_intent_id {
                        // Older fallback checkpoints had no identity. Persist the
                        // turn-scoped replacement before preparing any HTTP body.
                        let upgraded = StopRecoveryTopologyCheckpoint {
                            turn_id: turn.turn_id.clone(),
                            kind: StopRecoveryTopologyKind::LegacyFallback,
                            trace_id: trace_opts.as_ref().and_then(|opts| opts.trace_id.clone()),
                            root_span_id: trace_opts
                                .as_ref()
                                .and_then(|opts| opts.root_span_id.clone()),
                        };
                        if let Err(error) = checkpoint_stop_recovery_topology(
                            &context.state_file,
                            state,
                            session_id,
                            intent_id,
                            window_end_offset.max(0) as u64,
                            (turn_index + 1) as u64,
                            upgraded,
                            &now_iso8601_pub(),
                        ) {
                            context
                                .logger
                                .error(&format!("Failed to upgrade fallback topology: {error}"));
                            return TurnExportOutcome::GlobalFailure;
                        }
                    }
                    finalizer_trace_opts.push(trace_opts);
                }
            }
            context.logger.debug(&format!(
                "Resumed persisted finalizer topology session={} turn_id={} cursor={}",
                session_id,
                turn.turn_id,
                turn_index + 1
            ));
            continue;
        }
        match realtime_state::canonicalize_turn_contexts_in_state(
            state,
            session_id,
            &turn.turn_id,
        ) {
            Ok(Some(canonical)) => context.logger.debug(&format!(
                "Realtime finalizer canonicalization action={} session={} turn_id={} context_id={} aliases={}",
                canonical.action.as_str(),
                session_id,
                turn.turn_id,
                canonical.context.context_id,
                canonical.aliases.len()
            )),
            Ok(None) => {}
            Err(error) => {
                let now = now_iso8601_pub();
                let (reason, root_identities) = structural_topology_quarantine(
                    &error,
                    state,
                    session_id,
                    &turn.turn_id,
                );
                let recovery_key =
                    finalizer_recovery_key(transcript_path, &turn.turn_id, window_end_offset);
                let attempt = match claim_finalizer_recovery_attempt(
                    &context.state_file,
                    state,
                    session_id,
                    &now,
                    transcript_path,
                    &recovery_key,
                ) {
                    Ok(transition) => transition.attempt,
                    Err(err) => {
                        context.logger.error(&format!(
                            "Failed to claim terminal realtime quarantine attempt session={} turn_id={}: {}",
                            session_id, turn.turn_id, err
                        ));
                        return TurnExportOutcome::GlobalFailure;
                    }
                };
                match record_terminal_quarantined_turn(
                    &context.state_file,
                    state,
                    session_id,
                    &turn.turn_id,
                    reason,
                    &root_identities,
                    &now,
                    attempt,
                    Some(&recovery_key),
                    Some(window_end_offset),
                ) {
                    Ok(transition) => {
                        context.logger.warn(&format!(
                            "Direct Stop quarantined terminal realtime topology session={} turn_id={} reason={} appended={} root_identities={} terminal_high_water_offset={} error={}",
                            session_id,
                            turn.turn_id,
                            reason,
                            transition.appended,
                            root_identities.join(","),
                            transition
                                .terminal_high_water_offset
                                .map(|offset| offset.to_string())
                                .unwrap_or_else(|| "none".to_string()),
                            error
                        ));
                        terminally_accounted_turn_ids.insert(turn.turn_id.clone());
                        finalizer_trace_opts.push(None);
                        continue;
                    }
                    Err(err) => {
                        context.logger.error(&format!(
                            "Failed to persist terminal realtime quarantine session={} turn_id={}: {}",
                            session_id, turn.turn_id, err
                        ));
                        return TurnExportOutcome::GlobalFailure;
                    }
                }
            }
        }
        let scan_base_ordinal = realtime_state::generation_scan_base_ordinal(state, session_id);
        let mut trace_opts = build_turn_trace_opts(state, session_id, &turn.turn_id);
        let generation_span_ids = finalizer_generation_span_ids_for_turn_with_scan_base(
            state,
            session_id,
            turns,
            turn_index,
            finalizer_high_water,
            scan_base_ordinal,
        );
        if !generation_span_ids.is_empty() {
            trace_opts
                .get_or_insert_with(TurnReplayOpts::default)
                .generation_span_ids
                .extend(generation_span_ids);
        }
        let topology = if scheduled_recovery {
            match resolve_finalizer_turn_topology_with_accounting_history_budgeted(
                state,
                session_id,
                turns,
                turn_index,
                scan_base_ordinal,
                accounting_history,
                budget,
            ) {
                FinalizerTurnTopologyOutcome::Resolved(topology) => topology,
                FinalizerTurnTopologyOutcome::BudgetExhausted => {
                    let now = now_iso8601_pub();
                    let _ = record_pending_export(
                        &context.state_file,
                        state,
                        session_id,
                        HOOK_BUDGET_EXHAUSTED_REASON,
                        &[],
                        &now,
                        transcript_path,
                    );
                    return TurnExportOutcome::BudgetExhausted;
                }
            }
        } else {
            resolve_finalizer_turn_topology_with_accounting_history(
                state,
                session_id,
                turns,
                turn_index,
                scan_base_ordinal,
                accounting_history,
            )
        };
        let recovery_key =
            finalizer_recovery_key(transcript_path, &turn.turn_id, window_end_offset);
        let now = now_iso8601_pub();
        match persist_finalizer_topology_decision(
            &context.state_file,
            state,
            session_id,
            &turn.turn_id,
            topology,
            &now,
            Some(&recovery_key),
            finalizer_high_water,
            Some(window_end_offset),
            transcript_path,
        ) {
            Ok(FinalizerDecision::Exact {
                trace_id,
                root_span_id,
            }) => {
                let checkpoint = StopRecoveryTopologyCheckpoint {
                    turn_id: turn.turn_id.clone(),
                    kind: StopRecoveryTopologyKind::Exact,
                    trace_id: Some(trace_id.clone()),
                    root_span_id: Some(root_span_id.clone()),
                };
                apply_exact_trace_root_metadata(
                    state,
                    session_id,
                    &mut trace_opts,
                    trace_id,
                    root_span_id,
                );
                finalizer_trace_opts.push(trace_opts);
                if let Some(intent_id) = recovery_intent_id {
                    if let Err(error) = checkpoint_stop_recovery_topology(
                        &context.state_file,
                        state,
                        session_id,
                        intent_id,
                        window_end_offset.max(0) as u64,
                        (turn_index + 1) as u64,
                        checkpoint.clone(),
                        &now,
                    ) {
                        context.logger.error(&format!(
                            "Failed to checkpoint Stop topology session={} turn_id={}: {}",
                            session_id, turn.turn_id, error
                        ));
                        return TurnExportOutcome::GlobalFailure;
                    }
                    recovery_topology_checkpoints.push(checkpoint);
                }
            }
            Ok(FinalizerDecision::LegacyFallback) => {
                context.logger.info(&format!(
                    "Realtime finalizer using LegacyFallback session={} turn_id={} trace_id={} root_span_id={} identity_source=turn_identity",
                    session_id, turn.turn_id,
                    trace_opts.as_ref().and_then(|opts| opts.trace_id.as_deref()).unwrap_or(""),
                    trace_opts.as_ref().and_then(|opts| opts.root_span_id.as_deref()).unwrap_or("")
                ));
                let checkpoint = StopRecoveryTopologyCheckpoint {
                    turn_id: turn.turn_id.clone(),
                    kind: StopRecoveryTopologyKind::LegacyFallback,
                    trace_id: trace_opts.as_ref().and_then(|opts| opts.trace_id.clone()),
                    root_span_id: trace_opts
                        .as_ref()
                        .and_then(|opts| opts.root_span_id.clone()),
                };
                finalizer_trace_opts.push(trace_opts);
                if let Some(intent_id) = recovery_intent_id {
                    if let Err(error) = checkpoint_stop_recovery_topology(
                        &context.state_file,
                        state,
                        session_id,
                        intent_id,
                        window_end_offset.max(0) as u64,
                        (turn_index + 1) as u64,
                        checkpoint.clone(),
                        &now,
                    ) {
                        context.logger.error(&format!(
                            "Failed to checkpoint Stop topology session={} turn_id={}: {}",
                            session_id, turn.turn_id, error
                        ));
                        return TurnExportOutcome::GlobalFailure;
                    }
                    recovery_topology_checkpoints.push(checkpoint);
                }
            }
            Ok(FinalizerDecision::TerminalQuarantined { reason, appended }) => {
                context.logger.warn(&format!(
                    "Direct Stop terminally quarantined realtime finalizer topology session={} turn_id={} reason={} appended={}",
                    session_id, turn.turn_id, reason, appended
                ));
                terminally_accounted_turn_ids.insert(turn.turn_id.clone());
                finalizer_trace_opts.push(None);
                let checkpoint = StopRecoveryTopologyCheckpoint {
                    turn_id: turn.turn_id.clone(),
                    kind: StopRecoveryTopologyKind::TerminalQuarantined,
                    trace_id: None,
                    root_span_id: None,
                };
                if let Some(intent_id) = recovery_intent_id {
                    if let Err(error) = checkpoint_stop_recovery_topology(
                        &context.state_file,
                        state,
                        session_id,
                        intent_id,
                        window_end_offset.max(0) as u64,
                        (turn_index + 1) as u64,
                        checkpoint.clone(),
                        &now,
                    ) {
                        context.logger.error(&format!(
                            "Failed to checkpoint Stop topology session={} turn_id={}: {}",
                            session_id, turn.turn_id, error
                        ));
                        return TurnExportOutcome::GlobalFailure;
                    }
                    recovery_topology_checkpoints.push(checkpoint);
                }
            }
            Ok(FinalizerDecision::Deferred { reason }) => {
                context.logger.warn(&format!(
                    "Realtime finalizer topology deferred session={} turn_id={}: {}",
                    session_id, turn.turn_id, reason
                ));
                return TurnExportOutcome::SessionBlocked;
            }
            Err(err) => {
                context.logger.error(&format!(
                    "Failed to persist realtime finalizer decision session={} turn_id={}: {}",
                    session_id, turn.turn_id, err
                ));
                return TurnExportOutcome::GlobalFailure;
            }
        }
    }

    let accounted_realtime_children =
        accounted_realtime_child_span_ids(state, session_id, finalizer_high_water);
    let mut exported_turns = false;
    for (turn_index, turn) in turns.iter().enumerate() {
        if terminally_accounted_turn_ids.contains(&turn.turn_id) {
            context.logger.info(&format!(
                "Direct Stop skipping terminally quarantined turn session={} turn_id={}",
                session_id, turn.turn_id
            ));
            continue;
        }
        if !budget.can_start_turn() {
            let now = now_iso8601_pub();
            context.logger.warn(&format!(
                "Hook budget exhausted before turn build: session={} turn_id={} remaining={}ms",
                session_id,
                turn.turn_id,
                budget.remaining().as_millis(),
            ));
            if let Err(err) = record_pending_export(
                &context.state_file,
                state,
                session_id,
                HOOK_BUDGET_EXHAUSTED_REASON,
                &[],
                &now,
                transcript_path,
            ) {
                context
                    .logger
                    .error(&format!("Failed to persist budget retry reason: {}", err));
            }
            return TurnExportOutcome::BudgetExhausted;
        }

        let turn_number = get_session_state(state, session_id)
            .map(|session| get_int_field(session, "turn_count") as usize)
            .unwrap_or(0)
            + 1;
        let trace_opts = finalizer_trace_opts
            .get(turn_index)
            .and_then(Option::as_ref);
        if let Some(opts) = trace_opts.filter(|opts| opts.root_span_id.is_some()) {
            context.logger.info(&format!(
                "Direct Stop reusing realtime topology session={} turn_id={} trace_id={} root_span_id={}",
                session_id,
                turn.turn_id,
                opts.trace_id.as_deref().unwrap_or(""),
                opts.root_span_id.as_deref().unwrap_or("")
            ));
        }
        let mut body = build_otel_export_request_for_turn(
            turn,
            turn_number,
            session_id,
            &context.username,
            &context.hook_version,
            trace_opts,
        );
        if let Some(vcs) = vcs {
            inject_vcs_resource_attrs(&mut body, &vcs.remote_url, &vcs.branch);
        }
        let result = match merge_child_sessions_into_body(
            &mut body,
            turns,
            &parent_correlation_turns,
            turn_index,
            session_id,
            transcript_dir,
            transcript_path,
            sessions_dir,
            &context.username,
            &context.hook_version,
            &context.logger,
            Some(budget),
        ) {
            ChildMergeOutcome::Completed(result) => result,
            ChildMergeOutcome::BudgetExhausted => {
                let now = now_iso8601_pub();
                context.logger.warn(&format!(
                    "Hook budget exhausted during child merge: session={} turn_id={} remaining={}ms",
                    session_id,
                    turn.turn_id,
                    budget.remaining().as_millis(),
                ));
                if let Err(err) = record_pending_export(
                    &context.state_file,
                    state,
                    session_id,
                    HOOK_BUDGET_EXHAUSTED_REASON,
                    &[],
                    &now,
                    transcript_path,
                ) {
                    context
                        .logger
                        .error(&format!("Failed to persist budget retry reason: {}", err));
                }
                return TurnExportOutcome::BudgetExhausted;
            }
        };

        let track_subagents = !result.deferred_sync_ids.is_empty()
            || !result.merged_sync_ids.is_empty()
            || crate::hooks::langfuse::subagent_progress::records(state, session_id)
                .iter()
                .any(|record| record.parent_turn_id == turn.turn_id);
        if track_subagents {
            if let Err(error) = crate::hooks::langfuse::subagent_progress::register(
                &context.state_file,
                state,
                session_id,
                &turn.turn_id,
                transcript_path,
                &body,
                &result.deferred_sync_ids,
                &context.username,
                &context.hook_version,
                context.langfuse_config.as_ref().map(|config| {
                    crate::hooks::langfuse::subagent_progress::delivery_context(
                        config,
                        context.retry_policy,
                    )
                }),
            ) {
                context
                    .logger
                    .error(&format!("Failed to preserve subagent progress: {error}"));
                return TurnExportOutcome::GlobalFailure;
            }
            if let Some(record) =
                crate::hooks::langfuse::subagent_progress::records(state, session_id)
                    .into_iter()
                    .find(|record| record.parent_turn_id == turn.turn_id)
            {
                crate::hooks::langfuse::subagent_progress::suppress_accounted_children(
                    &mut body, &record, session_id,
                );
            }
            context.logger.info(&format!(
                "Subagent incremental delivery session={} turn={} pending={}",
                session_id,
                turn.turn_id,
                result.deferred_sync_ids.len()
            ));
        }

        let removed = suppress_child_spans_by_id(&mut body, &accounted_realtime_children);
        if removed > 0 {
            context.logger.info(&format!(
                "Direct Stop suppressed {removed} accounted realtime child span(s) session={session_id} turn_id={}",
                turn.turn_id
            ));
        }

        if context.retry_policy == RetryPolicy::AtMostOnce {
            let now = now_iso8601_pub();
            match checkpoint_turn_export_attempt(
                &context.state_file,
                state,
                session_id,
                &turn.turn_id,
                &now,
                Some(transcript_path),
                &result.merged_sync_ids,
                &result.merged_subagent_invocation_ids,
            ) {
                Ok(new_turn_count) => context.logger.info(&format!(
                    "Checkpointed at-most-once attempt before POST: session={} turn_id={} turn_number={} total_turns={}",
                    session_id, turn.turn_id, turn_number, new_turn_count
                )),
                Err(err) => {
                    context.logger.error(&format!(
                        "At-most-once checkpoint failed; refusing to POST session={} turn_id={}: {}",
                        session_id, turn.turn_id, err
                    ));
                    return TurnExportOutcome::GlobalFailure;
                }
            }
        }

        if track_subagents && context.retry_policy == RetryPolicy::AtMostOnce {
            if let Err(error) = crate::hooks::langfuse::subagent_progress::account(
                &context.state_file,
                state,
                session_id,
                &turn.turn_id,
                &body,
                &result.deferred_sync_ids,
            ) {
                context
                    .logger
                    .error(&format!("Failed to checkpoint subagent attempt: {error}"));
                return TurnExportOutcome::GlobalFailure;
            }
        }

        let delivery_trigger =
            finalizer_recovery_key(transcript_path, &turn.turn_id, window_end_offset);
        let recovery_delivery =
            recovery_intent_id.map(|intent_id| FinalizerRecoveryDeliveryContext {
                intent_id,
                observed_file_len: window_end_offset.max(0) as u64,
                window_end_offset,
                finalizer_high_water,
                generation_count,
                merged_sync_ids: &result.merged_sync_ids,
                merged_subagent_invocation_ids: &result.merged_subagent_invocation_ids,
            });
        match deliver_finalizer_body_in_chunks(
            &body,
            state,
            session_id,
            &turn.turn_id,
            &delivery_trigger,
            context,
            budget,
            recovery_delivery,
        ) {
            FinalizerDeliveryOutcome::Completed => {
                if track_subagents && context.retry_policy != RetryPolicy::AtMostOnce {
                    if let Err(error) = crate::hooks::langfuse::subagent_progress::account(
                        &context.state_file,
                        state,
                        session_id,
                        &turn.turn_id,
                        &body,
                        &result.deferred_sync_ids,
                    ) {
                        context
                            .logger
                            .error(&format!("Failed to checkpoint subagent delivery: {error}"));
                        return TurnExportOutcome::GlobalFailure;
                    }
                }
            }
            FinalizerDeliveryOutcome::Retryable(err) => {
                context.logger.error(&format!(
                    "Failed to deliver session={} turn_id={} finalizer chunks: {}",
                    session_id, turn.turn_id, err
                ));
                if let Some(ref metrics_cell) = context.metrics {
                    metrics_cell.borrow_mut().inc(
                        "aivision.agentic.export_error",
                        "Export error",
                        "{count}",
                        vec![OtelAttribute::string("error.type", &err)],
                    );
                }
                if context.retry_policy == RetryPolicy::AtMostOnce {
                    context.logger.warn(&format!(
                        "Dropping failed at-most-once finalizer without retry: session={} turn_id={}",
                        session_id, turn.turn_id
                    ));
                    continue;
                }
                let reason = if err.contains(HOOK_BUDGET_EXHAUSTED_REASON) {
                    HOOK_BUDGET_EXHAUSTED_REASON
                } else {
                    "otel_export_failed"
                };
                let now = now_iso8601_pub();
                if let Err(state_err) = record_pending_export(
                    &context.state_file,
                    state,
                    session_id,
                    reason,
                    &result.merged_sync_ids,
                    &now,
                    transcript_path,
                ) {
                    context.logger.error(&format!(
                        "Failed to persist export retry reason: {}",
                        state_err
                    ));
                }
                return if reason == HOOK_BUDGET_EXHAUSTED_REASON {
                    TurnExportOutcome::BudgetExhausted
                } else {
                    TurnExportOutcome::SessionBlocked
                };
            }
            FinalizerDeliveryOutcome::Terminal(reason) => {
                let now = now_iso8601_pub();
                context.logger.warn(&format!(
                    "Terminally quarantining finalizer delivery session={} turn_id={} reason={}",
                    session_id, turn.turn_id, reason
                ));
                if let Err(error) = record_terminal_quarantined_turn(
                    &context.state_file,
                    state,
                    session_id,
                    &turn.turn_id,
                    &reason,
                    &[],
                    &now,
                    1,
                    Some(&delivery_trigger),
                    Some(window_end_offset),
                ) {
                    context.logger.error(&format!(
                        "Failed to persist terminal finalizer delivery quarantine: {error}"
                    ));
                    return TurnExportOutcome::GlobalFailure;
                }
                continue;
            }
        }

        let now = now_iso8601_pub();
        match checkpoint_turn_export_success_maybe_faulted(
            &context.state_file,
            state,
            session_id,
            &turn.turn_id,
            &now,
            Some(transcript_path),
            &result.merged_sync_ids,
            &result.merged_subagent_invocation_ids,
        ) {
            Ok(new_turn_count) => context.logger.info(&format!(
                "Checkpointed session={} turn_id={} turn_number={} total_turns={}",
                session_id, turn.turn_id, turn_number, new_turn_count
            )),
            Err(err) => {
                context.logger.error(&format!(
                    "OTLP turn export succeeded but checkpoint failed; current turn may be retried (at-least-once delivery): {}",
                    err
                ));
                return TurnExportOutcome::GlobalFailure;
            }
        }
        if let Some(intent_id) = recovery_intent_id {
            if let Err(error) = checkpoint_stop_recovery_build_cursor(
                &context.state_file,
                state,
                session_id,
                intent_id,
                window_end_offset.max(0) as u64,
                (turn_index + 1) as u64,
                &now,
            ) {
                context.logger.error(&format!(
                    "Failed to checkpoint Stop build cursor session={} turn_id={}: {}",
                    session_id, turn.turn_id, error
                ));
                return TurnExportOutcome::GlobalFailure;
            }
            if let Err(error) = clear_stop_recovery_delivery(
                &context.state_file,
                state,
                session_id,
                intent_id,
                &now,
            ) {
                context.logger.error(&format!(
                    "Failed to clear completed Stop delivery session={} turn_id={}: {}",
                    session_id, turn.turn_id, error
                ));
                return TurnExportOutcome::GlobalFailure;
            }
        }
        exported_turns = true;
    }

    if let Err(error) = finalize_realtime_high_water(
        &context.state_file,
        state,
        session_id,
        finalizer_high_water,
        window_end_offset.max(0) as u64,
        generation_count,
    ) {
        context.logger.error(&format!(
            "Failed to finalize realtime high-water transition session={} high_water={}: {}",
            session_id, finalizer_high_water, error
        ));
        return TurnExportOutcome::GlobalFailure;
    }

    if terminally_accounted_turn_ids.is_empty() {
        TurnExportOutcome::Completed
    } else {
        TurnExportOutcome::TerminallyAccounted { exported_turns }
    }
}

pub(crate) fn merge_child_sessions_into_body(
    body: &mut serde_json::Value,
    parent_turns: &[Turn],
    parent_correlation_turns: &[Turn],
    anchor_turn_index: usize,
    parent_session_id: &str,
    transcript_dir: &std::path::Path,
    parent_transcript_path: &str,
    sessions_dir: Option<&str>,
    username: &str,
    version: &str,
    logger: &crate::common::logger::Logger,
    budget: Option<StopBudget>,
) -> ChildMergeOutcome {
    use crate::common::otel::{
        merge_subagent_sessions_into_otel_across_turns, MergeResult, SubagentChild,
    };
    use std::collections::HashSet;

    if !budget_allows_work(budget) {
        return ChildMergeOutcome::BudgetExhausted;
    }

    let current_turn_id = parent_turns
        .get(anchor_turn_index)
        .map(|turn| turn.turn_id.as_str());
    let full_anchor_index = current_turn_id.and_then(|turn_id| {
        parent_correlation_turns
            .iter()
            .position(|turn| turn.turn_id == turn_id)
    });
    let (correlation_turns, correlation_anchor_index) =
        if let Some(full_anchor_index) = full_anchor_index {
            (parent_correlation_turns, full_anchor_index)
        } else {
            (parent_turns, anchor_turn_index)
        };

    let discovery_plan = subagent_discovery_plan(correlation_turns, correlation_anchor_index);
    for observation in &discovery_plan.observations {
        logger.debug(&format!(
            "Subagent discovery observation: child={} activation={} reason={}",
            observation.child_session_id, observation.activation_id, observation.reason
        ));
    }

    let deferred_decisions = discovery_plan
        .decisions
        .iter()
        .filter(|decision| decision.disposition == SubagentDiscoveryDisposition::Deferred)
        .collect::<Vec<_>>();
    // Identity failures stay pending, but must not block unrelated ready children.
    let discovery_deferred_ids: Vec<String> = deferred_decisions
        .iter()
        .filter_map(|decision| {
            decision
                .child
                .as_ref()
                .map(|child| child.session_id.clone())
                .or_else(|| decision.activation_id.clone())
        })
        .collect();
    let discovery_reasons: Vec<(String, String)> = deferred_decisions
        .iter()
        .map(|decision| {
            (
                decision
                    .child
                    .as_ref()
                    .map(|child| child.session_id.clone())
                    .or_else(|| decision.activation_id.clone())
                    .unwrap_or_else(|| "unknown-subagent".to_string()),
                decision.reason.clone(),
            )
        })
        .collect();
    let eligible_decisions = discovery_plan
        .decisions
        .iter()
        .filter(|decision| {
            decision.disposition == SubagentDiscoveryDisposition::Eligible
                || decision.reason == "native_terminal_pending"
        })
        .collect::<Vec<_>>();
    let mut expected_children = Vec::new();
    for decision in &eligible_decisions {
        let Some(child) = decision.child.as_ref() else {
            continue;
        };
        if !expected_children.iter().any(
            |existing: &&crate::common::transcript::SubagentIdentity| {
                existing.session_id == child.session_id
            },
        ) {
            expected_children.push(child);
        }
    }
    let expected_ids: HashSet<String> = expected_children
        .iter()
        .map(|identity| identity.session_id.clone())
        .collect();
    if expected_ids.is_empty() {
        return ChildMergeOutcome::Completed(MergeResult {
            merged_sync_ids: vec![],
            merged_subagent_invocation_ids: vec![],
            invocation_outcomes: vec![],
            correlation_reasons: discovery_reasons,
            deferred_sync_ids: discovery_deferred_ids,
        });
    }

    let search_roots = session_search_roots(transcript_dir, sessions_dir.map(std::path::Path::new));
    let mut known_child_paths = std::collections::HashMap::<String, std::path::PathBuf>::new();
    let mut ready_children = std::collections::HashMap::<String, SubagentChild>::new();
    let mut deferred_sync_ids: Vec<String> = Vec::new();
    if !budget_allows_work(budget) {
        return ChildMergeOutcome::BudgetExhausted;
    }

    // Parent and child transcripts are separate writers. A v2 FINAL_ANSWER can
    // be visible in the parent a few milliseconds before the child's final
    // JSONL line is readable, so retry only when the parent proves a completed
    // sync child exists. The bound stays well below Codex's hook timeout.
    for attempt in 0..=5 {
        if !budget_allows_work(budget) {
            return ChildMergeOutcome::BudgetExhausted;
        }
        let missing_paths: HashSet<String> = expected_ids
            .iter()
            .filter(|session_id| !known_child_paths.contains_key(*session_id))
            .cloned()
            .collect();
        if !missing_paths.is_empty() {
            let discovery = discover_child_session_transcripts(
                &search_roots,
                &missing_paths,
                parent_session_id,
                Some(std::path::Path::new(parent_transcript_path)),
                attempt == 0 || attempt == 5,
            );
            logger.debug(&format!(
                "Child transcript discovery attempt={}: requested={} found={} exact={} metadata_reads={} directories={}",
                attempt,
                missing_paths.len(),
                discovery.matches.len(),
                discovery.exact_filename_matches,
                discovery.metadata_files_read,
                discovery.directories_visited,
            ));
            for child in discovery.matches {
                known_child_paths
                    .entry(child.identity_id)
                    .or_insert(child.path);
            }
            if !budget_allows_work(budget) {
                return ChildMergeOutcome::BudgetExhausted;
            }
        }

        let mut known_ids: Vec<_> = known_child_paths.keys().cloned().collect();
        known_ids.sort();
        for child_identity_id in known_ids {
            if !budget_allows_work(budget) {
                return ChildMergeOutcome::BudgetExhausted;
            }
            if ready_children.contains_key(&child_identity_id) {
                continue;
            }
            let Some(path) = known_child_paths.get(&child_identity_id) else {
                continue;
            };

            match wait_for_file_stable_with_budget(&path.to_string_lossy(), 25, 150, budget) {
                Ok(true) => {}
                Ok(false) => continue,
                Err(()) => return ChildMergeOutcome::BudgetExhausted,
            }
            let (messages, _, _trailing_buffer) = read_new_jsonl(path, 0, "");
            if !budget_allows_work(budget) {
                return ChildMergeOutcome::BudgetExhausted;
            }
            if messages.is_empty() {
                continue;
            }

            let turns = build_turns(&messages);
            // A readable complete prefix can contain finished calls even while
            // the latest activation is open. The shared merger decides readiness
            // per activation rather than gating the entire child file here.
            if turns.is_empty() {
                continue;
            }

            let src = turns.first().and_then(|t| t.subagent_source.as_ref());
            let nickname = src
                .and_then(|s| s.agent_nickname.as_deref())
                .unwrap_or_default()
                .to_string();
            let role = src
                .and_then(|s| s.agent_role.as_deref())
                .unwrap_or("")
                .to_string();
            let agent_path = src.and_then(|s| s.agent_path.clone());
            let protocol_version = src.and_then(|s| s.protocol_version.clone());

            logger.debug(&format!(
                "Discovered ready child session: {} (session={}, protocol={}, {} turns)",
                path.file_name().unwrap_or_default().to_string_lossy(),
                child_identity_id,
                protocol_version.as_deref().unwrap_or("legacy"),
                turns.len(),
            ));

            ready_children.insert(
                child_identity_id.clone(),
                SubagentChild {
                    session_id: child_identity_id,
                    turns,
                    nickname,
                    role,
                    agent_path,
                },
            );
        }

        // Native error is terminal even when Codex never creates a readable
        // child transcript. Prefer a ready transcript from the first
        // discovery attempt; otherwise preserve exact activation metadata in
        // an adapter-local empty shell. The shared OTel merger already models
        // an error activation without child turns.
        for decision in &eligible_decisions {
            let Some(activation) = decision.activation.as_ref() else {
                continue;
            };
            if activation.state != CollabInvocationState::Error {
                continue;
            }
            let Some(child) = decision.child.as_ref() else {
                continue;
            };
            ready_children
                .entry(child.session_id.clone())
                .or_insert_with(|| SubagentChild {
                    session_id: child.session_id.clone(),
                    turns: vec![],
                    nickname: activation.nickname.clone().unwrap_or_default(),
                    role: activation.role.clone().unwrap_or_default(),
                    agent_path: child.agent_path.clone(),
                });
        }

        deferred_sync_ids = expected_ids
            .iter()
            .filter(|session_id| !ready_children.contains_key(*session_id))
            .cloned()
            .collect();
        deferred_sync_ids.sort();
        if deferred_sync_ids.is_empty() || attempt == 5 {
            break;
        }
        logger.debug(&format!(
            "Child transcript retry {}/5: waiting for {}",
            attempt + 1,
            deferred_sync_ids.join(",")
        ));
        let retry_delay = std::time::Duration::from_millis(50);
        let Some(remaining) = budget_remaining(budget) else {
            return ChildMergeOutcome::BudgetExhausted;
        };
        if remaining <= retry_delay {
            return ChildMergeOutcome::BudgetExhausted;
        }
        #[cfg(test)]
        if let Some(publish) = CHILD_FIXTURE_POLLING.with(|polling| {
            polling
                .borrow_mut()
                .as_mut()
                .and_then(|fixture| fixture.publish_on_retry.take())
        }) {
            publish();
        }
        std::thread::sleep(retry_delay);
    }

    deferred_sync_ids.extend(discovery_deferred_ids);
    deferred_sync_ids.sort();
    deferred_sync_ids.dedup();

    let children: Vec<SubagentChild> = expected_children
        .iter()
        .filter_map(|identity| ready_children.remove(&identity.session_id))
        .collect();
    if children.is_empty() {
        return ChildMergeOutcome::Completed(MergeResult {
            merged_sync_ids: vec![],
            merged_subagent_invocation_ids: vec![],
            invocation_outcomes: vec![],
            correlation_reasons: vec![],
            deferred_sync_ids,
        });
    }

    logger.info(&format!(
        "Merging {} child session(s) into parent OTel",
        children.len()
    ));

    if !budget_allows_work(budget) {
        return ChildMergeOutcome::BudgetExhausted;
    }
    let mut result = merge_subagent_sessions_into_otel_across_turns(
        body,
        correlation_turns,
        correlation_anchor_index,
        &children,
        username,
        version,
    );
    for child in &children {
        let child_cwd = child
            .turns
            .iter()
            .rev()
            .map(|turn| turn.cwd.trim())
            .find(|cwd| !cwd.is_empty());
        let child_vcs = dirs::session_state_file(&child.session_id).and_then(|state_file| {
            let child_state = load_state(&state_file);
            get_vcs_for_cwd(&child_state, &child.session_id, child_cwd)
        });
        let (remote_url, branch) = child_vcs
            .as_ref()
            .map(|vcs| (vcs.remote_url.as_str(), vcs.branch.as_str()))
            .unwrap_or(("", ""));
        let isolated = isolate_session_vcs_resource(body, &child.session_id, remote_url, branch);
        if isolated > 0 {
            logger.debug(&format!(
                "Isolated {isolated} span(s) under child session resource: {}",
                child.session_id
            ));
        }
    }
    if !budget_allows_work(budget) {
        return ChildMergeOutcome::BudgetExhausted;
    }
    result.deferred_sync_ids.extend(deferred_sync_ids);
    result.deferred_sync_ids.sort();
    result.deferred_sync_ids.dedup();
    result.correlation_reasons.extend(discovery_reasons);
    result.correlation_reasons.sort();
    result.correlation_reasons.dedup();
    for (subject_id, reason) in &result.correlation_reasons {
        logger.debug(&format!(
            "Subagent correlation: subject={} reason={}",
            subject_id, reason
        ));
    }
    ChildMergeOutcome::Completed(result)
}

fn wait_for_file_stable_with_budget(
    path: &str,
    poll_interval_ms: u64,
    max_wait_ms: u64,
    budget: Option<StopBudget>,
) -> Result<bool, ()> {
    let remaining = budget_remaining(budget).ok_or(())?;
    let minimum_stability_window = std::time::Duration::from_millis(
        poll_interval_ms
            .saturating_mul(3)
            .saturating_add(poll_interval_ms),
    );
    if remaining < minimum_stability_window {
        return Err(());
    }
    // Keep the real stability samples and Stop budget. Give fixture tests
    // more time for their polling threads to be scheduled by hosted runners.
    #[cfg(test)]
    let max_wait_ms = CHILD_FIXTURE_POLLING.with(|polling| {
        if polling.borrow().is_some() {
            max_wait_ms.max(1_500)
        } else {
            max_wait_ms
        }
    });
    let capped_wait = std::cmp::min(remaining, std::time::Duration::from_millis(max_wait_ms));
    let capped_wait_ms = capped_wait.as_millis().min(u64::MAX as u128) as u64;
    let stable = wait_for_file_stable(path, poll_interval_ms, capped_wait_ms);
    if budget_remaining(budget).is_none() {
        return Err(());
    }
    Ok(stable)
}

fn budget_allows_work(budget: Option<StopBudget>) -> bool {
    budget.map(|value| value.can_start_turn()).unwrap_or(true)
}

fn budget_remaining(budget: Option<StopBudget>) -> Option<std::time::Duration> {
    budget
        .map(|value| value.processing_time_remaining())
        .unwrap_or(Some(std::time::Duration::MAX))
}

fn find_session_transcript(
    transcript_dir: &std::path::Path,
    sessions_dir: Option<&str>,
    session_id: &str,
) -> Option<std::path::PathBuf> {
    let roots = session_search_roots(transcript_dir, sessions_dir.map(std::path::Path::new));
    discover_root_session_transcript(&roots, session_id, None)
        .matches
        .into_iter()
        .next()
        .map(|discovered| discovered.path)
}

fn turn_has_output(turn: &Turn) -> bool {
    crate::common::message_format::has_output_messages(turn)
}

fn turn_is_postable(turn: &Turn) -> bool {
    // A completed sibling call must not checkpoint away a native invocation
    // whose usage has not arrived yet. Keep the whole turn recoverable.
    if turn
        .assistants
        .iter()
        .any(|assistant| assistant.is_pending_generation())
    {
        return false;
    }
    let terminal = matches!(
        turn.terminal_state,
        TurnTerminalState::Completed
            | TurnTerminalState::Failed
            | TurnTerminalState::Aborted
            | TurnTerminalState::Superseded
    );
    let ordinary_turn = !turn.user_text.is_empty() && turn_has_output(turn);
    let observed_usage = turn
        .assistants
        .iter()
        .any(|assistant| assistant.usage.is_native_record() && assistant.usage.has_any());
    terminal
        && (ordinary_turn
            || observed_usage
            || turn.terminal_state == TurnTerminalState::Aborted
            || !turn.compactions.is_empty()
            || turn.terminal_error.is_some())
}

fn native_usage_record_turn_id(entry: &serde_json::Value) -> Option<&str> {
    match entry.get("type").and_then(serde_json::Value::as_str) {
        Some("token_usage_record") => entry
            .get("payload")
            .and_then(|payload| payload.get("turn_id"))
            .and_then(serde_json::Value::as_str)
            .filter(|turn_id| !turn_id.is_empty()),
        Some("compacted") => entry
            .get("payload")
            .and_then(|payload| payload.get("latest_token_usage_record"))
            .and_then(|record| record.get("turn_id"))
            .and_then(serde_json::Value::as_str)
            .filter(|turn_id| !turn_id.is_empty()),
        _ => None,
    }
}

fn first_unresolved_v2_subagent_turn(turns: &[Turn], posted_turn_ids: &[String]) -> Option<usize> {
    let identities = collect_subagent_identities(turns);
    identities
        .iter()
        .filter_map(|identity| {
            let anchor = subagent_anchor_turn_index(turns, identity, &identities);
            let anchor = anchor?;
            if posted_turn_ids
                .iter()
                .any(|posted| posted == &turns[anchor].turn_id)
            {
                return None;
            }
            let matched = correlate_subagent_across_turns(turns, anchor, identity, &identities);
            (matched.protocol == Some(SubagentProtocol::V2)
                && matched.lifecycle == SubagentLifecycle::IdentityCandidate
                && matched.reason == "v2_terminal_message_missing")
                .then_some(anchor)
        })
        .min()
}

fn turn_start_message_index(messages: &[serde_json::Value], turn_id: &str) -> Option<usize> {
    messages.iter().position(|entry| {
        entry.get("type").and_then(serde_json::Value::as_str) == Some("event_msg")
            && entry
                .pointer("/payload/type")
                .and_then(serde_json::Value::as_str)
                == Some("task_started")
            && entry
                .pointer("/payload/turn_id")
                .and_then(serde_json::Value::as_str)
                == Some(turn_id)
    })
}

struct PostableTurnBuild {
    turns: Vec<Turn>,
    pending_messages: Vec<serde_json::Value>,
    terminal_seen: bool,
    diagnostics: Vec<String>,
}

fn has_authoritative_terminal(messages: &[serde_json::Value]) -> bool {
    messages.iter().any(|entry| {
        entry.get("type").and_then(serde_json::Value::as_str) == Some("event_msg")
            && matches!(
                entry
                    .pointer("/payload/type")
                    .and_then(serde_json::Value::as_str),
                Some("task_complete" | "turn_aborted")
            )
    })
}

fn has_authoritative_terminal_for_turn(messages: &[serde_json::Value], turn_id: &str) -> bool {
    messages.iter().any(|entry| {
        entry.get("type").and_then(serde_json::Value::as_str) == Some("event_msg")
            && matches!(
                entry
                    .pointer("/payload/type")
                    .and_then(serde_json::Value::as_str),
                Some("task_complete" | "turn_aborted")
            )
            && entry
                .pointer("/payload/turn_id")
                .and_then(serde_json::Value::as_str)
                == Some(turn_id)
    })
}

fn target_terminal_pending_reason(
    target_turn_id: Option<&str>,
    target_terminal_seen: bool,
) -> Option<String> {
    target_turn_id
        .filter(|_| !target_terminal_seen)
        .map(|turn_id| format!("target turn awaiting terminal: {turn_id}"))
}

fn messages_are_classified_internal_only(messages: &[serde_json::Value]) -> bool {
    !messages.is_empty()
        && messages.iter().all(|entry| {
            match entry.get("type").and_then(serde_json::Value::as_str) {
                Some("session_meta") => true,
                Some("response_item") => {
                    let Some(payload) = entry.get("payload") else {
                        return false;
                    };
                    if payload.get("type").and_then(serde_json::Value::as_str) != Some("message")
                        || payload.get("role").and_then(serde_json::Value::as_str) != Some("user")
                    {
                        return false;
                    }
                    let text = payload
                        .get("content")
                        .and_then(serde_json::Value::as_array)
                        .into_iter()
                        .flatten()
                        .filter_map(|item| item.get("text").and_then(serde_json::Value::as_str))
                        .collect::<Vec<_>>()
                        .join("\n");
                    is_internal_role_user_envelope(&text)
                }
                _ => false,
            }
        })
}

#[cfg(test)]
fn build_postable_turn_outcome(
    prev_pending_messages: &[serde_json::Value],
    all_messages: &[serde_json::Value],
    prev_posted_turn_ids: &[String],
) -> PostableTurnBuild {
    build_postable_turn_outcome_with_initial_input(
        prev_pending_messages,
        all_messages,
        prev_posted_turn_ids,
        None,
        false,
    )
}

fn build_postable_turn_outcome_with_initial_input(
    prev_pending_messages: &[serde_json::Value],
    all_messages: &[serde_json::Value],
    prev_posted_turn_ids: &[String],
    initial_compaction_input: Option<u64>,
    initial_native_usage_mode: bool,
) -> PostableTurnBuild {
    let mut combined_messages = prev_pending_messages.to_vec();
    combined_messages.extend_from_slice(all_messages);

    let (mut completed_messages, initial_pending_messages) =
        split_completed_messages(&combined_messages);
    let mut completed_messages_cover_session = initial_pending_messages.is_empty();
    let terminal_seen =
        initial_pending_messages.is_empty() && has_authoritative_terminal(&combined_messages);
    let mut pending_messages = initial_pending_messages;
    let session_turns = crate::common::transcript::build_turns_with_usage_context(
        &combined_messages,
        initial_compaction_input,
        initial_native_usage_mode,
    );
    let mut diagnostics = Vec::new();
    if let Some(anchor_index) =
        first_unresolved_v2_subagent_turn(&session_turns, prev_posted_turn_ids)
    {
        if let Some(cutoff) =
            turn_start_message_index(&combined_messages, &session_turns[anchor_index].turn_id)
        {
            completed_messages = combined_messages[..cutoff].to_vec();
            completed_messages_cover_session = false;
            pending_messages = combined_messages[cutoff..].to_vec();
            diagnostics.push(format!(
                "subagent_terminal_pending:{}",
                session_turns[anchor_index].turn_id
            ));
        }
    }

    let all_known_turns_already_posted = !session_turns.is_empty()
        && session_turns.iter().all(|turn| {
            prev_posted_turn_ids
                .iter()
                .any(|posted| posted == &turn.turn_id)
        });
    let completed_turns = if completed_messages_cover_session {
        session_turns
    } else {
        // Keep the boundary evidence from the full window: rebuilding only the
        // completed prefix would lose the task_started that superseded its last
        // turn, and silently turn that turn back into Open.
        let completed_ids: HashSet<_> = crate::common::transcript::build_turns_with_usage_context(
            &completed_messages,
            initial_compaction_input,
            initial_native_usage_mode,
        )
        .into_iter()
        .map(|turn| turn.turn_id)
        .collect();
        session_turns
            .into_iter()
            .filter(|turn| completed_ids.contains(&turn.turn_id))
            .collect()
    };
    let mut turns = Vec::new();
    let mut first_unpostable_turn_id = None;
    for turn in completed_turns {
        let already_posted = prev_posted_turn_ids
            .iter()
            .any(|posted| posted == &turn.turn_id);
        if already_posted {
            continue;
        }
        if turn_is_postable(&turn) {
            turns.push(turn);
            continue;
        }
        if !matches!(
            turn.terminal_state,
            TurnTerminalState::Open | TurnTerminalState::HeuristicComplete
        ) {
            let special_terminal = !turn.compactions.is_empty() || turn.terminal_error.is_some();
            if turn.user_text.is_empty() && !special_terminal {
                diagnostics.push(format!("terminal_missing_input:{}", turn.turn_id));
            }
            if !turn_has_output(&turn) && !special_terminal {
                diagnostics.push(format!("terminal_missing_output:{}", turn.turn_id));
            }
        }
        // Every unposted turn must remain recoverable, even when it lacks a
        // terminal marker or enough data to build a span.
        diagnostics.push(format!("unposted_turn_pending:{}", turn.turn_id));
        first_unpostable_turn_id.get_or_insert(turn.turn_id);
    }

    if let Some(turn_id) = first_unpostable_turn_id {
        if let Some(cutoff) = turn_start_message_index(&combined_messages, &turn_id) {
            pending_messages = combined_messages[cutoff..].to_vec();
        } else {
            pending_messages = combined_messages.clone();
        }
    }

    if !turns.is_empty() && !pending_messages.is_empty() {
        let posted_this_round = turns
            .iter()
            .map(|turn| turn.turn_id.as_str())
            .collect::<HashSet<_>>();
        pending_messages.retain(|entry| {
            !native_usage_record_turn_id(entry)
                .is_some_and(|turn_id| posted_this_round.contains(turn_id))
        });
    }

    if turns.is_empty()
        && pending_messages.is_empty()
        && !combined_messages.is_empty()
        && !all_known_turns_already_posted
        && !messages_are_classified_internal_only(&combined_messages)
    {
        pending_messages = combined_messages.clone();
        diagnostics.push("unknown_or_incomplete_transcript_window".to_string());
    }

    // Compatibility cleanup: an older cdxhooks version may already have
    // checkpointed a heuristic open turn but exited before committing the
    // cursor. Consume that retained window without issuing another POST.
    if !pending_messages.is_empty() {
        let pending_turns = build_turns(&pending_messages);
        if !pending_turns.is_empty()
            && pending_turns.iter().all(|turn| {
                prev_posted_turn_ids
                    .iter()
                    .any(|posted| posted == &turn.turn_id)
            })
        {
            pending_messages.clear();
        }
    }

    PostableTurnBuild {
        turns,
        pending_messages,
        terminal_seen,
        diagnostics,
    }
}

#[cfg(test)]
fn build_postable_turns(
    prev_pending_messages: &[serde_json::Value],
    all_messages: &[serde_json::Value],
    prev_posted_turn_ids: &[String],
) -> (Vec<Turn>, Vec<serde_json::Value>) {
    let outcome =
        build_postable_turn_outcome(prev_pending_messages, all_messages, prev_posted_turn_ids);
    (outcome.turns, outcome.pending_messages)
}

fn refresh_postable_turn_cache(
    cache: &mut Option<PostableTurnBuild>,
    messages_changed: bool,
    prev_pending_messages: &[serde_json::Value],
    all_messages: &[serde_json::Value],
    prev_posted_turn_ids: &[String],
    initial_compaction_input: Option<u64>,
    initial_native_usage_mode: bool,
) -> bool {
    if !messages_changed && cache.is_some() {
        return false;
    }
    *cache = Some(build_postable_turn_outcome_with_initial_input(
        prev_pending_messages,
        all_messages,
        prev_posted_turn_ids,
        initial_compaction_input,
        initial_native_usage_mode,
    ));
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::hook::HookEvent;
    use crate::hooks::langfuse::state::{
        commit_export_success, ALIAS_CYCLE_QUARANTINE_REASON, FACT_CONFLICT_QUARANTINE_REASON,
        MISSING_ALIAS_TARGET_QUARANTINE_REASON, MULTIPLE_PUBLISHED_ROOTS_QUARANTINE_REASON,
    };
    use crate::hooks::langfuse::LangfuseConfig;
    use crate::hooks::StopBudget;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::thread::JoinHandle;
    use std::time::{Duration, Instant};

    #[test]
    fn test_interrupt_agent_turn_needs_parent_correlation() {
        let turns = build_turns(&[
            serde_json::json!({"timestamp":"2026-07-10T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"interrupt child"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:01Z","type":"response_item","payload":{"type":"function_call","name":"interrupt_agent","call_id":"interrupt-1","arguments":"{\"target\":\"/root/reviewer\"}"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:02Z","type":"response_item","payload":{"type":"function_call_output","call_id":"interrupt-1","output":"{\"interrupted\":false}"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:03Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}),
        ]);

        assert!(turns_need_parent_correlation(&turns));
    }

    fn span_string_attr(span: &serde_json::Value, key: &str) -> Option<String> {
        span.get("attributes")?
            .as_array()?
            .iter()
            .find(|attr| attr.get("key").and_then(serde_json::Value::as_str) == Some(key))?
            .pointer("/value/stringValue")?
            .as_str()
            .map(str::to_string)
    }

    fn all_otel_spans(body: &serde_json::Value) -> Vec<&serde_json::Value> {
        body.get("resourceSpans")
            .and_then(serde_json::Value::as_array)
            .into_iter()
            .flatten()
            .flat_map(|resource| {
                resource
                    .get("scopeSpans")
                    .and_then(serde_json::Value::as_array)
                    .into_iter()
                    .flatten()
            })
            .flat_map(|scope| {
                scope
                    .get("spans")
                    .and_then(serde_json::Value::as_array)
                    .into_iter()
                    .flatten()
            })
            .collect()
    }

    /// Compare live/replay behavior without coupling the assertion to random
    /// trace/span identifiers or resource attributes supplied by the runtime.
    fn semantic_span_signature(body: &serde_json::Value) -> Vec<serde_json::Value> {
        let spans = all_otel_spans(body);
        assert!(!spans.is_empty(), "OTel body must contain spans");
        let root_span_id = spans
            .first()
            .and_then(|span| span.get("spanId"))
            .and_then(serde_json::Value::as_str)
            .expect("OTel body must contain a root span");
        let agent_parents: std::collections::HashMap<String, String> = spans
            .iter()
            .filter(|span| span_string_attr(span, "gen_ai.span.kind").as_deref() == Some("AGENT"))
            .filter_map(|span| {
                let span_id = span.get("spanId")?.as_str()?.to_string();
                let child_id = span_string_attr(span, "agentic.subagent.id")
                    .unwrap_or_else(|| "unknown-child".to_string());
                let parent_call_id = span_string_attr(span, "agentic.subagent.parent_tool_call_id")
                    .unwrap_or_else(|| "unknown-call".to_string());
                Some((span_id, format!("agent:{parent_call_id}:{child_id}")))
            })
            .collect();
        let semantic_attr_keys = [
            "gen_ai.span.kind",
            "gen_ai.operation.name",
            "agentic.tool.type",
            "agentic.tool.raw_name",
            "gen_ai.tool.call.id",
            "gen_ai.tool.call.arguments",
            "gen_ai.tool.call.result",
            "agentic.subagent.id",
            "agentic.subagent.agent_path",
            "agentic.subagent.parent_tool_call_id",
        ];

        spans
            .iter()
            .map(|span| {
                let parent_span_id = span
                    .get("parentSpanId")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or_default();
                let parent = if parent_span_id.is_empty() {
                    "none".to_string()
                } else if parent_span_id == root_span_id {
                    "root".to_string()
                } else {
                    agent_parents
                        .get(parent_span_id)
                        .cloned()
                        .unwrap_or_else(|| "other".to_string())
                };
                let attributes: Vec<_> = semantic_attr_keys
                    .iter()
                    .filter_map(|key| {
                        span_string_attr(span, key)
                            .map(|value| serde_json::json!({"key": key, "value": value}))
                    })
                    .collect();

                serde_json::json!({
                    "name": span.get("name"),
                    "parent": parent,
                    "startTimeUnixNano": span.get("startTimeUnixNano"),
                    "endTimeUnixNano": span.get("endTimeUnixNano"),
                    "status": span.get("status"),
                    "attributes": attributes,
                })
            })
            .collect()
    }

    fn three_turn_transcript() -> String {
        r#"{"timestamp":"2026-07-13T00:00:00Z","type":"session_meta","payload":{"id":"parent","thread_source":"user"}}
{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-13T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"first"}}
{"timestamp":"2026-07-13T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"one"}}
{"timestamp":"2026-07-13T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"one"}}
{"timestamp":"2026-07-13T00:00:02Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-2"}}
{"timestamp":"2026-07-13T00:00:02.100Z","type":"event_msg","payload":{"type":"user_message","message":"second"}}
{"timestamp":"2026-07-13T00:00:02.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"two"}}
{"timestamp":"2026-07-13T00:00:02.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-2","last_agent_message":"two"}}
{"timestamp":"2026-07-13T00:00:03Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-3"}}
{"timestamp":"2026-07-13T00:00:03.100Z","type":"event_msg","payload":{"type":"user_message","message":"third"}}
{"timestamp":"2026-07-13T00:00:03.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"three"}}
{"timestamp":"2026-07-13T00:00:03.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-3","last_agent_message":"three"}}
"#
        .to_string()
    }

    fn two_turn_transcript() -> String {
        r#"{"timestamp":"2026-07-13T00:00:00Z","type":"session_meta","payload":{"id":"parent","thread_source":"user"}}
{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-13T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"first"}}
{"timestamp":"2026-07-13T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"one"}}
{"timestamp":"2026-07-13T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"one"}}
{"timestamp":"2026-07-13T00:00:02Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-2"}}
{"timestamp":"2026-07-13T00:00:02.100Z","type":"event_msg","payload":{"type":"user_message","message":"second"}}
{"timestamp":"2026-07-13T00:00:02.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"two"}}
{"timestamp":"2026-07-13T00:00:02.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-2","last_agent_message":"two"}}
"#
        .to_string()
    }

    fn one_turn_transcript() -> String {
        r#"{"timestamp":"2026-07-13T00:00:00Z","type":"session_meta","payload":{"id":"parent","thread_source":"user"}}
{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-13T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"first"}}
{"timestamp":"2026-07-13T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"one"}}
{"timestamp":"2026-07-13T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"one"}}
"#
        .to_string()
    }

    fn retention_boundary_transcript(successful_turn_count: usize) -> (String, Vec<String>) {
        let mut entries = vec![
            serde_json::json!({"timestamp":"2026-08-16T00:00:00Z","type":"session_meta","payload":{"id":"parent","thread_source":"user"}}),
            serde_json::json!({"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-bad"}}),
            serde_json::json!({"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"user_message","message":"bad"}}),
            serde_json::json!({"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"bad done"}}),
            serde_json::json!({"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-bad","last_agent_message":"bad done"}}),
        ];
        let successful_turn_ids = (0..successful_turn_count)
            .map(|index| format!("turn-clean-{index}"))
            .collect::<Vec<_>>();
        for turn_id in &successful_turn_ids {
            entries.extend([
                serde_json::json!({"timestamp":"2026-08-16T00:00:02Z","type":"event_msg","payload":{"type":"task_started","turn_id":turn_id}}),
                serde_json::json!({"timestamp":"2026-08-16T00:00:02Z","type":"event_msg","payload":{"type":"user_message","message":"clean"}}),
                serde_json::json!({"timestamp":"2026-08-16T00:00:02Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"clean done"}}),
                serde_json::json!({"timestamp":"2026-08-16T00:00:02Z","type":"event_msg","payload":{"type":"task_complete","turn_id":turn_id,"last_agent_message":"clean done"}}),
            ]);
        }
        let transcript = entries
            .into_iter()
            .map(|entry| serde_json::to_string(&entry).unwrap())
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        (transcript, successful_turn_ids)
    }

    fn add_published_realtime_root(
        state: &mut serde_json::Value,
        session_id: &str,
        turn_id: &str,
        tool_use_id: &str,
        trace_id: &str,
        child_span_id: &str,
    ) {
        let context = realtime_state::claim_tool_in_state(
            state,
            session_id,
            tool_use_id,
            tool_use_id,
            trace_id,
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            state,
            session_id,
            &context.context_id,
            Some(turn_id),
        )
        .unwrap();
        let batch = realtime_state::prepare_realtime_batch_in_state(
            state,
            session_id,
            &context.context_id,
            "tool",
            &[child_span_id.to_string()],
            &format!("digest-{child_span_id}"),
        )
        .unwrap();
        realtime_state::publish_realtime_batch_in_state(state, session_id, &batch.batch_id)
            .unwrap();
        realtime_state::succeed_realtime_batch_in_state(state, session_id, &batch.batch_id)
            .unwrap();
        realtime_state::rotate_current_in_state(state, session_id);
    }

    fn add_accounted_realtime_tool(
        state: &mut serde_json::Value,
        session_id: &str,
        turn_id: &str,
        tool_use_id: &str,
        trace_id: &str,
    ) {
        let context = realtime_state::claim_tool_in_state(
            state,
            session_id,
            tool_use_id,
            tool_use_id,
            trace_id,
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            state,
            session_id,
            &context.context_id,
            Some(turn_id),
        )
        .unwrap();
        let child_id = format!("tool:{tool_use_id}");
        let digest = format!("digest-{tool_use_id}");
        let batch = realtime_state::prepare_realtime_batch_in_state(
            state,
            session_id,
            &context.context_id,
            "tool",
            &[child_id],
            &digest,
        )
        .unwrap();
        realtime_state::register_realtime_job_in_state(
            state,
            session_id,
            &context.context_id,
            "batch",
            &batch.batch_id,
            &digest,
        )
        .unwrap();
        realtime_state::persist_tool_job_registration_in_state(
            state,
            session_id,
            &context.context_id,
            tool_use_id,
            &batch.batch_id,
            &digest,
        );
        realtime_state::publish_realtime_batch_in_state(state, session_id, &batch.batch_id)
            .unwrap();
        realtime_state::begin_realtime_delivery_in_state(
            state,
            session_id,
            &batch.batch_id,
            &digest,
            RetryPolicy::AtLeastOnce,
            "claim-test",
            1,
        )
        .unwrap();
        realtime_state::complete_realtime_delivery_in_state(
            state,
            session_id,
            &batch.batch_id,
            &digest,
        )
        .unwrap();
        realtime_state::succeed_realtime_batch_in_state(state, session_id, &batch.batch_id)
            .unwrap();
        realtime_state::rotate_current_in_state(state, session_id);
    }

    fn save_multiple_published_roots_state(
        state_file: &std::path::Path,
        session_id: &str,
        turn_id: &str,
    ) {
        let mut state = serde_json::json!({});
        add_published_realtime_root(
            &mut state,
            session_id,
            turn_id,
            "tool-a",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "child-a",
        );
        add_published_realtime_root(
            &mut state,
            session_id,
            turn_id,
            "tool-b",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "child-b",
        );
        crate::hooks::langfuse::state::save_state(state_file, &state).unwrap();
    }

    fn save_fact_conflict_state(state_file: &std::path::Path, session_id: &str, turn_id: &str) {
        let mut state = serde_json::json!({});
        let first = realtime_state::claim_tool_in_state(
            &mut state,
            session_id,
            "tool-a",
            "first",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            session_id,
            &first.context_id,
            Some(turn_id),
        )
        .unwrap();
        realtime_state::rotate_current_in_state(&mut state, session_id);
        let second = realtime_state::claim_tool_in_state(
            &mut state,
            session_id,
            "tool-b",
            "second",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            session_id,
            &second.context_id,
            Some(turn_id),
        )
        .unwrap();
        state[session_id]["realtime_export"]["pending"][0]["tool_use_ids"] =
            serde_json::json!(["shared"]);
        state[session_id]["realtime_export"]["pending"][0]["tool_span_ids"] =
            serde_json::json!({"shared": "1111111111111111"});
        state[session_id]["realtime_export"]["current"]["tool_use_ids"] =
            serde_json::json!(["shared"]);
        state[session_id]["realtime_export"]["current"]["tool_span_ids"] =
            serde_json::json!({"shared": "2222222222222222"});
        crate::hooks::langfuse::state::save_state(state_file, &state).unwrap();
    }

    fn save_alias_cycle_state(state_file: &std::path::Path, session_id: &str, turn_id: &str) {
        let mut state = serde_json::json!({});
        let first = realtime_state::claim_tool_in_state(
            &mut state,
            session_id,
            "tool-a",
            "first",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            session_id,
            &first.context_id,
            Some(turn_id),
        )
        .unwrap();
        realtime_state::rotate_current_in_state(&mut state, session_id);
        let second = realtime_state::claim_tool_in_state(
            &mut state,
            session_id,
            "tool-b",
            "second",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        let first_context_id = first.context_id;
        let second_context_id = second.context_id;
        state[session_id]["realtime_export"]["context_aliases"] = serde_json::json!({
            (first_context_id.clone()): second_context_id.clone(),
            (second_context_id): first_context_id,
        });
        crate::hooks::langfuse::state::save_state(state_file, &state).unwrap();
    }

    fn save_missing_alias_target_state(
        state_file: &std::path::Path,
        session_id: &str,
        turn_id: &str,
    ) {
        let mut state = serde_json::json!({});
        let context = realtime_state::claim_tool_in_state(
            &mut state,
            session_id,
            "tool-a",
            "first",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            session_id,
            &context.context_id,
            Some(turn_id),
        )
        .unwrap();
        state[session_id]["realtime_export"]["context_aliases"] = serde_json::json!({
            (context.context_id): "missing-context",
        });
        crate::hooks::langfuse::state::save_state(state_file, &state).unwrap();
    }

    fn read_http_body(stream: &mut TcpStream) -> serde_json::Value {
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let mut bytes = Vec::new();
        let mut buffer = [0_u8; 4096];
        let header_end = loop {
            let read = stream.read(&mut buffer).unwrap();
            assert!(read > 0, "request closed before headers completed");
            bytes.extend_from_slice(&buffer[..read]);
            if let Some(index) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
                break index + 4;
            }
        };
        let headers = std::str::from_utf8(&bytes[..header_end]).unwrap();
        let content_length: usize = headers
            .lines()
            .find_map(|line| {
                let (name, value) = line.split_once(':')?;
                name.eq_ignore_ascii_case("content-length")
                    .then(|| value.trim().parse().unwrap())
            })
            .unwrap();
        while bytes.len() - header_end < content_length {
            let read = stream.read(&mut buffer).unwrap();
            assert!(read > 0, "request closed before body completed");
            bytes.extend_from_slice(&buffer[..read]);
        }
        serde_json::from_slice(&bytes[header_end..header_end + content_length]).unwrap()
    }

    fn spawn_http_sink(statuses: Vec<u16>) -> (String, JoinHandle<Vec<serde_json::Value>>) {
        spawn_http_sink_with_deadline(statuses, Duration::from_secs(5))
    }

    fn spawn_http_sink_with_deadline(
        statuses: Vec<u16>,
        accept_timeout: Duration,
    ) -> (String, JoinHandle<Vec<serde_json::Value>>) {
        spawn_http_sink_with_optional_start_gate(statuses, accept_timeout, None)
    }

    fn spawn_gated_http_sink_with_deadline(
        statuses: Vec<u16>,
        accept_timeout: Duration,
    ) -> (
        String,
        std::sync::mpsc::Sender<()>,
        JoinHandle<Vec<serde_json::Value>>,
    ) {
        let (start_tx, start_rx) = std::sync::mpsc::channel();
        let (host, handle) =
            spawn_http_sink_with_optional_start_gate(statuses, accept_timeout, Some(start_rx));
        (host, start_tx, handle)
    }

    fn spawn_http_sink_with_optional_start_gate(
        statuses: Vec<u16>,
        accept_timeout: Duration,
        start_rx: Option<std::sync::mpsc::Receiver<()>>,
    ) -> (String, JoinHandle<Vec<serde_json::Value>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let handle = std::thread::spawn(move || {
            if let Some(start_rx) = start_rx {
                start_rx.recv().expect("test sink start gate must open");
            }
            listener.set_nonblocking(true).unwrap();
            let mut bodies = Vec::new();
            let deadline = Instant::now() + accept_timeout;
            while bodies.len() < statuses.len() && Instant::now() < deadline {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        stream.set_nonblocking(false).unwrap();
                        let body = read_http_body(&mut stream);
                        // The trace exporter fires a best-effort companion POST to
                        // /v1/logs after each successful traces POST. Those
                        // `resourceLogs` bodies share this sink but are not what the
                        // assertions inspect, so answer them 200 without counting them
                        // and keep `statuses` mapped 1:1 onto the trace bodies.
                        let is_trace_body = body.get("resourceSpans").is_some();
                        let status = if is_trace_body {
                            statuses[bodies.len()]
                        } else {
                            200
                        };
                        let reason = if (200..300).contains(&status) {
                            "OK"
                        } else {
                            "Internal Server Error"
                        };
                        let response = format!(
                            "HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                        );
                        stream.write_all(response.as_bytes()).unwrap();
                        stream.flush().unwrap();
                        if is_trace_body {
                            bodies.push(body);
                        }
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(Duration::from_millis(5));
                    }
                    Err(error) => panic!("test sink accept failed: {error}"),
                }
            }
            bodies
        });
        (format!("http://{address}"), handle)
    }

    fn spawn_redirect_sink() -> (String, JoinHandle<usize>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        listener.set_nonblocking(true).unwrap();
        let handle = std::thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(2);
            let mut requests = 0;
            while Instant::now() < deadline {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        stream.set_nonblocking(false).unwrap();
                        requests += 1;
                        if requests == 1 {
                            let _ = read_http_body(&mut stream);
                            let response = format!(
                                "HTTP/1.1 302 Found\r\nLocation: http://{address}/redirected\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                            );
                            stream.write_all(response.as_bytes()).unwrap();
                            stream.flush().unwrap();
                        } else {
                            stream
                                .write_all(
                                    b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                                )
                                .unwrap();
                            stream.flush().unwrap();
                            break;
                        }
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(Duration::from_millis(5));
                    }
                    Err(error) => panic!("redirect sink accept failed: {error}"),
                }
            }
            requests
        });
        (format!("http://{address}"), handle)
    }

    fn test_context(dir: &tempfile::TempDir, host: String) -> CommonContext {
        CommonContext {
            logger: crate::common::logger::Logger::new(dir.path().join("test.log"), true),
            state_file: dir.path().join("state.json"),
            langfuse_config: Some(LangfuseConfig {
                public_key: None,
                secret_key: None,
                host,
                headers: None,
            }),
            username: "tester".to_string(),
            sessions_dir: dir.path().to_string_lossy().to_string(),
            hook_version: "test-version".to_string(),
            stop_budget: Some(StopBudget::new(
                Instant::now(),
                Duration::from_secs(10),
                Duration::from_secs(1),
                Duration::from_millis(100),
            )),
            retry_policy: crate::hooks::RetryPolicy::AtLeastOnce,
            export_mode: crate::hooks::RealtimeExportMode::RealtimeWithStopFinalizer,
            realtime_llm_batch_size: crate::hooks::DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
            metrics: None,
        }
    }

    fn ample_budget() -> StopBudget {
        StopBudget::new(
            Instant::now(),
            Duration::from_secs(10),
            Duration::from_secs(1),
            Duration::from_millis(100),
        )
    }

    fn test_handler() -> LangfuseHandler {
        LangfuseHandler::new()
    }

    #[test]
    fn direct_stop_recovers_split_turn_with_single_published_owner() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"timestamp":"2026-08-16T00:00:00Z","type":"session_meta","payload":{"id":"session","thread_source":"user"}}
{"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-08-16T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}
{"timestamp":"2026-08-16T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"done"}}
{"timestamp":"2026-08-16T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}
"#,
        )
        .unwrap();
        let (host, sink) = spawn_http_sink(vec![200]);
        let context = test_context(&dir, host);
        let published_trace_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        let mut state = serde_json::json!({});
        let owner = realtime_state::claim_tool_in_state(
            &mut state,
            "session",
            "tool-owner",
            "first",
            published_trace_id,
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            "session",
            &owner.context_id,
            Some("turn-1"),
        )
        .unwrap();
        let owner_batch = realtime_state::prepare_realtime_batch_in_state(
            &mut state,
            "session",
            &owner.context_id,
            "tool",
            &["published-child".to_string()],
            "published-digest",
        )
        .unwrap();
        realtime_state::publish_realtime_batch_in_state(
            &mut state,
            "session",
            &owner_batch.batch_id,
        )
        .unwrap();
        realtime_state::succeed_realtime_batch_in_state(
            &mut state,
            "session",
            &owner_batch.batch_id,
        )
        .unwrap();
        realtime_state::rotate_current_in_state(&mut state, "session");
        let loser = realtime_state::claim_tool_in_state(
            &mut state,
            "session",
            "tool-loser",
            "second",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            "session",
            &loser.context_id,
            Some("turn-1"),
        )
        .unwrap();
        crate::hooks::langfuse::state::save_state(&context.state_file, &state).unwrap();

        test_handler()
            .handle_stop(&stop_payload(&transcript_path), &context)
            .unwrap();

        let bodies = sink.join().unwrap();
        assert_eq!(bodies.len(), 1);
        let spans = all_otel_spans(&bodies[0]);
        assert!(!spans.is_empty());
        assert!(spans
            .iter()
            .all(|span| span["traceId"] == serde_json::json!(published_trace_id)));
        let final_state = load_state(&context.state_file);
        assert_ne!(
            final_state["session"]["pending_export_reason"],
            serde_json::json!("realtime_topology_ambiguous")
        );
    }

    fn stop_payload(transcript_path: &std::path::Path) -> serde_json::Value {
        serde_json::json!({
            "hook_event_name": "Stop",
            "session_id": "parent",
            "transcript_path": transcript_path,
            "cwd": ""
        })
    }

    #[test]
    fn stop_outcome_distinguishes_completed_deferred_and_intentional_skip() {
        let dir = tempfile::tempdir().unwrap();
        let context = test_context(&dir, "http://127.0.0.1:9".to_string());
        let missing = dir.path().join("missing.jsonl");
        let deferred = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &stop_payload(&missing), &context)
            .unwrap();
        assert!(matches!(deferred, StopDispatchOutcome::Deferred(_)));
        assert!(!deferred.clears_intent());

        let empty = dir.path().join("empty.jsonl");
        std::fs::write(&empty, "").unwrap();
        let completed = test_handler()
            .handle_stop_with_outcome(&HookEvent::SessionEnd, &stop_payload(&empty), &context)
            .unwrap();
        assert_eq!(completed, StopDispatchOutcome::Completed);
        assert!(completed.clears_intent());
        assert!(completed.completed_export());

        let mut recursive = stop_payload(&empty);
        recursive["stop_hook_active"] = serde_json::json!(true);
        let skipped = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &recursive, &context)
            .unwrap();
        assert_eq!(skipped, StopDispatchOutcome::IntentionallySkipped);
        assert!(skipped.clears_intent());
        assert!(!skipped.completed_export());
    }

    #[test]
    fn missing_stop_budget_is_explicitly_deferred() {
        let dir = tempfile::tempdir().unwrap();
        let transcript = dir.path().join("session.jsonl");
        std::fs::write(&transcript, "{}\n").unwrap();
        let mut context = test_context(&dir, "http://127.0.0.1:9".to_string());
        context.stop_budget = None;

        let outcome = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &stop_payload(&transcript), &context)
            .unwrap();
        assert!(matches!(outcome, StopDispatchOutcome::Deferred(_)));
        assert!(!outcome.clears_intent());
        let state = load_state(&context.state_file);
        assert_eq!(
            state["parent"]["pending_export_reason"],
            "hook_budget_missing"
        );
    }

    #[test]
    fn finalizer_delivery_chunks_large_body_and_commits_root_last() {
        let dir = tempfile::tempdir().unwrap();
        let (host, sink) = spawn_http_sink(vec![200, 200]);
        let context = test_context(&dir, host);
        let body = serde_json::json!({
            "resourceSpans": [{
                "resource": {"attributes": []},
                "scopeSpans": [{
                    "scope": {"name": "test"},
                    "spans": [
                        {
                            "traceId": "11111111111111111111111111111111",
                            "spanId": "aaaaaaaaaaaaaaaa",
                            "name": "root",
                            "attributes": [{"key": "payload", "value": {"stringValue": "r".repeat(2_200_000)}}]
                        },
                        {
                            "traceId": "11111111111111111111111111111111",
                            "spanId": "bbbbbbbbbbbbbbbb",
                            "parentSpanId": "aaaaaaaaaaaaaaaa",
                            "name": "child",
                            "attributes": [{"key": "payload", "value": {"stringValue": "c".repeat(2_200_000)}}]
                        }
                    ]
                }]
            }]
        });
        let mut state = serde_json::json!({});
        let outcome = deliver_finalizer_body_in_chunks(
            &body,
            &mut state,
            "parent",
            "turn-large",
            "trigger-large",
            &context,
            ample_budget(),
            None,
        );
        assert_eq!(outcome, FinalizerDeliveryOutcome::Completed);

        let posted = sink.join().unwrap();
        assert_eq!(posted.len(), 2);
        assert!(posted
            .iter()
            .all(|chunk| serde_json::to_vec(chunk).unwrap().len()
                <= REALTIME_TARGET_SERIALIZED_BODY_BYTES));
        let posted_span_ids = posted
            .iter()
            .map(|chunk| {
                chunk["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["spanId"]
                    .as_str()
                    .unwrap()
                    .to_string()
            })
            .collect::<Vec<_>>();
        assert_eq!(
            posted_span_ids,
            vec!["bbbbbbbbbbbbbbbb", "aaaaaaaaaaaaaaaa"]
        );
        let plan = state["parent"]["realtime_export"]["delivery_plans"]
            .as_object()
            .unwrap()
            .values()
            .next()
            .unwrap();
        assert_eq!(plan["next_cursor"], 2);
        assert_eq!(plan["complete"], true);
        assert!(plan["chunks"]
            .as_array()
            .unwrap()
            .iter()
            .all(|chunk| chunk.get("body").is_some()));
    }

    #[test]
    fn finalizer_delivery_resumes_persisted_chunk_without_rebuilding_body() {
        let dir = tempfile::tempdir().unwrap();
        let transcript = dir.path().join("session.jsonl");
        std::fs::write(&transcript, "{}\n").unwrap();
        let body = serde_json::json!({
            "resourceSpans": [{
                "resource": {"attributes": []},
                "scopeSpans": [{
                    "scope": {"name": "test"},
                    "spans": [
                        {
                            "traceId": "11111111111111111111111111111111",
                            "spanId": "aaaaaaaaaaaaaaaa",
                            "name": "root",
                            "attributes": [{"key": "payload", "value": {"stringValue": "r".repeat(2_200_000)}}]
                        },
                        {
                            "traceId": "11111111111111111111111111111111",
                            "spanId": "bbbbbbbbbbbbbbbb",
                            "parentSpanId": "aaaaaaaaaaaaaaaa",
                            "name": "child",
                            "attributes": [{"key": "payload", "value": {"stringValue": "c".repeat(2_200_000)}}]
                        }
                    ]
                }]
            }]
        });
        let (first_host, first_sink) = spawn_http_sink(vec![200, 500]);
        let first_context = test_context(&dir, first_host);
        let mut state = serde_json::json!({});
        let merged_sync_ids = Vec::new();
        let merged_invocation_ids = Vec::new();
        let first = deliver_finalizer_body_in_chunks(
            &body,
            &mut state,
            "parent",
            "turn-resume",
            "trigger-resume",
            &first_context,
            ample_budget(),
            Some(FinalizerRecoveryDeliveryContext {
                intent_id: "intent-resume",
                observed_file_len: 3,
                window_end_offset: 3,
                finalizer_high_water: 0,
                generation_count: 0,
                merged_sync_ids: &merged_sync_ids,
                merged_subagent_invocation_ids: &merged_invocation_ids,
            }),
        );
        assert!(matches!(first, FinalizerDeliveryOutcome::Retryable(_)));
        assert_eq!(first_sink.join().unwrap().len(), 2);

        let plan = state["parent"]["realtime_export"]["delivery_plans"]
            .as_object()
            .unwrap()
            .values()
            .next()
            .cloned()
            .unwrap();
        let plan_id = plan["plan_id"].as_str().unwrap().to_string();
        assert_eq!(plan["next_cursor"], 1);
        assert_eq!(plan["chunks"][0]["status"], "accounted");
        assert!(plan["chunks"][1].get("body").is_some());

        let (resume_host, resume_sink) = spawn_http_sink(vec![200]);
        let resume_context = test_context(&dir, resume_host);
        let resumed = resume_pending_finalizer_delivery(
            &mut state,
            "parent",
            "intent-resume",
            transcript.to_str().unwrap(),
            &resume_context,
            ample_budget(),
        );
        assert_eq!(resumed, PendingFinalizerResume::Completed);
        let resumed_bodies = resume_sink.join().unwrap();
        assert_eq!(resumed_bodies.len(), 1);
        assert_eq!(
            resumed_bodies[0]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["spanId"],
            "aaaaaaaaaaaaaaaa"
        );
        let resumed_plan = realtime_state::realtime_delivery_plan(&state, "parent", &plan_id)
            .expect("persisted plan must remain readable");
        assert!(resumed_plan.complete);
        assert_eq!(resumed_plan.next_cursor, 2);
        let recovery = stop_recovery_record(&state, "parent").unwrap();
        assert!(recovery.delivery_plan_id.is_none());
        assert_eq!(
            state["parent"]["posted_turn_ids"],
            serde_json::json!(["turn-resume"])
        );
    }

    #[test]
    fn direct_stop_adopts_completed_delivery_plan_across_stop_intents_after_checkpoint_failure() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let transcript = one_turn_transcript();
        std::fs::write(&transcript_path, &transcript).unwrap();
        let (messages, _, _) = read_new_jsonl(&transcript_path, 0, "");
        let (completed_messages, _) = split_completed_messages(&messages);
        let turn_id = build_turns(&completed_messages)
            .first()
            .expect("one-turn fixture must contain a completed turn")
            .turn_id
            .clone();
        let mut payload = stop_payload(&transcript_path);
        payload["intent_id"] = serde_json::json!("intent-checkpoint-fail");

        let (first_host, first_sink) = spawn_http_sink(vec![200]);
        let first_context = test_context(&dir, first_host);
        let _checkpoint_failure = fail_next_turn_success_checkpoint_for_test(
            &first_context.state_file,
            "parent",
            &turn_id,
        );

        let first = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &payload, &first_context)
            .unwrap();

        assert!(matches!(first, StopDispatchOutcome::Deferred(_)));
        let first_bodies = first_sink.join().unwrap();
        assert_eq!(first_bodies.len(), 1);
        let state_after_failure = load_state(&first_context.state_file);
        let recovery = stop_recovery_record(&state_after_failure, "parent").unwrap();
        let topology = recovery
            .topology_checkpoints
            .iter()
            .find(|checkpoint| checkpoint.turn_id == turn_id)
            .expect("fallback identity must be checkpointed before sending");
        let root = all_otel_spans(&first_bodies[0])
            .into_iter()
            .find(|span| {
                span["name"]
                    .as_str()
                    .is_some_and(|name| name.starts_with("Turn "))
            })
            .unwrap();
        assert_eq!(topology.trace_id.as_deref(), root["traceId"].as_str());
        assert_eq!(topology.root_span_id.as_deref(), root["spanId"].as_str());
        let plan_id = recovery
            .delivery_plan_id
            .as_deref()
            .expect("delivery plan remains durable after checkpoint failure");
        let plan = realtime_state::realtime_delivery_plan(&state_after_failure, "parent", plan_id)
            .expect("persisted delivery plan");
        assert!(plan.complete);
        assert_eq!(plan.next_cursor, 1);
        assert_eq!(plan.chunks.len(), 1);
        assert_eq!(
            plan.chunks[0].status,
            realtime_state::RealtimeDeliveryChunkStatus::Accounted
        );
        assert_eq!(plan.chunks[0].body.as_ref(), Some(&first_bodies[0]));
        assert!(state_after_failure["parent"]["posted_turn_ids"].is_null());

        let (second_host, start_unexpected_sink, unexpected_sink) =
            spawn_gated_http_sink_with_deadline(vec![500], Duration::from_millis(250));
        let second_context = test_context(&dir, second_host);
        let mut second_payload = payload.clone();
        second_payload["intent_id"] = serde_json::json!("intent-checkpoint-fail-next-stop");
        start_unexpected_sink.send(()).unwrap();
        let second = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &second_payload, &second_context)
            .unwrap();

        assert_eq!(second, StopDispatchOutcome::Completed);
        assert!(
            unexpected_sink.join().unwrap().is_empty(),
            "completed persisted finalizer delivery must not rebuild or POST another payload"
        );
        let final_state = load_state(&second_context.state_file);
        assert_eq!(
            final_state["parent"]["posted_turn_ids"],
            serde_json::json!([turn_id])
        );
        let final_recovery = stop_recovery_record(&final_state, "parent").unwrap();
        assert_eq!(final_recovery.intent_id, "intent-checkpoint-fail-next-stop");
        assert!(final_recovery.delivery_plan_id.is_none());
        assert_eq!(final_state["parent"]["offset"], transcript.len() as i64);
    }

    fn assert_single_turn_bodies(bodies: &[serde_json::Value], expected: &[&str]) {
        assert_eq!(bodies.len(), expected.len());
        for (body, expected_name) in bodies.iter().zip(expected) {
            assert_eq!(body["resourceSpans"].as_array().unwrap().len(), 1);
            let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
                .as_array()
                .unwrap();
            assert!(spans.iter().any(|span| {
                span.get("name").and_then(serde_json::Value::as_str) == Some(*expected_name)
            }));
        }
    }

    #[test]
    fn test_three_turns_post_individually_then_advance_offset() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let transcript = three_turn_transcript();
        std::fs::write(&transcript_path, &transcript).unwrap();
        let (host, sink) = spawn_http_sink(vec![200, 200, 200]);
        let context = test_context(&dir, host);

        test_handler()
            .handle_stop(&stop_payload(&transcript_path), &context)
            .unwrap();

        let bodies = sink.join().unwrap();
        assert_single_turn_bodies(&bodies, &["Turn 1", "Turn 2", "Turn 3"]);
        let state = load_state(&context.state_file);
        assert_eq!(state["parent"]["offset"], transcript.len() as i64);
        assert_eq!(state["parent"]["turn_count"], 3);
        assert_eq!(
            state["parent"]["posted_turn_ids"],
            serde_json::json!(["turn-1", "turn-2", "turn-3"])
        );
        assert!(state["parent"].get("pending_export_reason").is_none());
    }

    #[test]
    fn direct_stop_quarantines_multiple_published_roots_and_clears_retry_path() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let transcript = one_turn_transcript();
        std::fs::write(&transcript_path, &transcript).unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let context = test_context(&dir, format!("http://{}", listener.local_addr().unwrap()));
        save_multiple_published_roots_state(&context.state_file, "parent", "turn-1");

        let outcome = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &stop_payload(&transcript_path), &context)
            .unwrap();

        assert_eq!(
            outcome,
            StopDispatchOutcome::TerminallyAccounted {
                exported_turns: false
            }
        );
        assert!(outcome.clears_intent());
        assert!(!outcome.completed_export());
        assert!(listener.accept().is_err(), "bad turn must not POST");
        let state = load_state(&context.state_file);
        assert_eq!(state["parent"]["offset"], transcript.len() as i64);
        assert!(!get_string_array_field(&state["parent"], "posted_turn_ids")
            .iter()
            .any(|posted| posted == "turn-1"));
        assert_eq!(
            state["parent"]["quarantined_turns"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            state["parent"]["quarantined_turns"][0]["turn_id"],
            serde_json::json!("turn-1")
        );
        assert_eq!(
            state["parent"]["export_accounting"]["terminal_turn_ids"],
            serde_json::json!(["turn-1"])
        );
        assert_eq!(
            state["parent"]["export_accounting"]["terminal_high_water_offset"],
            serde_json::json!(transcript.len() as i64)
        );
        assert!(state["parent"].get("pending_export_reason").is_none());

        let duplicate = test_handler()
            .handle_stop_with_outcome(
                &HookEvent::SessionEnd,
                &stop_payload(&transcript_path),
                &context,
            )
            .unwrap();
        assert_eq!(duplicate, StopDispatchOutcome::Completed);
        let deduped = load_state(&context.state_file);
        assert_eq!(
            deduped["parent"]["quarantined_turns"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }

    #[test]
    fn direct_stop_terminally_quarantines_all_structural_claim_errors() {
        type Fixture = fn(&std::path::Path, &str, &str);
        let cases: [(&str, Fixture, &str); 3] = [
            (
                "fact-conflict",
                save_fact_conflict_state,
                FACT_CONFLICT_QUARANTINE_REASON,
            ),
            (
                "alias-cycle",
                save_alias_cycle_state,
                ALIAS_CYCLE_QUARANTINE_REASON,
            ),
            (
                "missing-alias-target",
                save_missing_alias_target_state,
                MISSING_ALIAS_TARGET_QUARANTINE_REASON,
            ),
        ];

        for (name, configure, expected_reason) in cases {
            let dir = tempfile::tempdir().unwrap();
            let transcript_path = dir.path().join(format!("{name}.jsonl"));
            let transcript = one_turn_transcript();
            std::fs::write(&transcript_path, &transcript).unwrap();
            let context = test_context(&dir, "http://127.0.0.1:9".to_string());
            configure(&context.state_file, "parent", "turn-1");

            let outcome = test_handler()
                .handle_stop_with_outcome(
                    &HookEvent::Stop,
                    &stop_payload(&transcript_path),
                    &context,
                )
                .unwrap();

            assert_eq!(
                outcome,
                StopDispatchOutcome::TerminallyAccounted {
                    exported_turns: false
                },
                "{name}"
            );
            assert!(outcome.clears_intent(), "{name}");
            let state = load_state(&context.state_file);
            assert_eq!(
                state["parent"]["quarantined_turns"][0]["reason"],
                serde_json::json!(expected_reason),
                "{name}"
            );
            assert_eq!(
                state["parent"]["export_accounting"]["terminal_turn_ids"],
                serde_json::json!(["turn-1"]),
                "{name}"
            );
            assert!(
                state["parent"].get("pending_export_reason").is_none(),
                "{name}"
            );
        }
    }

    #[test]
    fn direct_stop_skips_quarantined_turn_and_exports_later_clean_turn() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let transcript = two_turn_transcript();
        std::fs::write(&transcript_path, &transcript).unwrap();
        let (host, sink) = spawn_http_sink(vec![200]);
        let context = test_context(&dir, host);
        save_multiple_published_roots_state(&context.state_file, "parent", "turn-1");

        let outcome = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &stop_payload(&transcript_path), &context)
            .unwrap();

        assert_eq!(
            outcome,
            StopDispatchOutcome::TerminallyAccounted {
                exported_turns: true
            }
        );
        assert!(outcome.completed_export());
        assert_single_turn_bodies(&sink.join().unwrap(), &["Turn 1"]);
        let state = load_state(&context.state_file);
        assert_eq!(state["parent"]["offset"], transcript.len() as i64);
        assert_eq!(state["parent"]["turn_count"], 1);
        assert_eq!(
            state["parent"]["posted_turn_ids"],
            serde_json::json!(["turn-2"])
        );
        assert_eq!(
            state["parent"]["quarantined_turns"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            state["parent"]["export_accounting"]["terminal_turn_ids"],
            serde_json::json!(["turn-1"])
        );
        assert!(state["parent"].get("pending_export_reason").is_none());
    }

    #[test]
    fn direct_stop_skips_successes_pruned_from_posted_ids_after_quarantine() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let successful_turn_count = crate::hooks::langfuse::state::POSTED_TURN_ID_RETENTION + 1;
        let (transcript, successful_turn_ids) =
            retention_boundary_transcript(successful_turn_count);
        std::fs::write(&transcript_path, &transcript).unwrap();
        let context = test_context(&dir, "http://127.0.0.1:9".to_string());
        let mut state = serde_json::json!({});
        record_terminal_quarantined_turn(
            &context.state_file,
            &mut state,
            "parent",
            "turn-bad",
            MULTIPLE_PUBLISHED_ROOTS_QUARANTINE_REASON,
            &["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/1111111111111111".to_string()],
            "2026-08-16T00:00:03Z",
            1,
            Some("direct-retention-boundary"),
            Some(transcript.len() as i64),
        )
        .unwrap();
        commit_export_success(
            &context.state_file,
            &mut state,
            "parent",
            0,
            "",
            &[],
            &successful_turn_ids,
            successful_turn_count,
            "2026-08-16T00:00:04Z",
            Some(transcript_path.to_string_lossy().as_ref()),
            &[],
            &[],
        )
        .unwrap();
        assert!(!get_string_array_field(&state["parent"], "posted_turn_ids")
            .contains(&successful_turn_ids[0]));

        let outcome = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &stop_payload(&transcript_path), &context)
            .unwrap();

        assert_eq!(outcome, StopDispatchOutcome::Completed);
        let final_state = load_state(&context.state_file);
        assert_eq!(
            get_int_field(&final_state["parent"], "turn_count") as usize,
            successful_turn_count
        );
        assert_eq!(
            final_state["parent"]["quarantined_turns"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            final_state["parent"]["posted_turn_ids"]
                .as_array()
                .unwrap()
                .len(),
            crate::hooks::langfuse::state::POSTED_TURN_ID_RETENTION
        );
    }

    #[test]
    fn direct_stop_uses_complete_history_for_aged_out_realtime_contexts() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let successful_turn_count = crate::hooks::langfuse::state::POSTED_TURN_ID_RETENTION + 1;
        let (mut transcript, successful_turn_ids) =
            retention_boundary_transcript(successful_turn_count);
        let historical_end_offset = transcript.len() as i64;
        transcript.push_str(
            r#"{"timestamp":"2026-08-16T00:01:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-current"}}
{"timestamp":"2026-08-16T00:01:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"current"}}
{"timestamp":"2026-08-16T00:01:00.200Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"true\"}","call_id":"shared-tool"}}
{"timestamp":"2026-08-16T00:01:00.300Z","type":"response_item","payload":{"type":"function_call_output","call_id":"shared-tool","output":"ok"}}
{"timestamp":"2026-08-16T00:01:00.400Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"current done"}}
{"timestamp":"2026-08-16T00:01:00.500Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-current","last_agent_message":"current done"}}
"#,
        );
        std::fs::write(&transcript_path, &transcript).unwrap();

        let (host, sink) = spawn_http_sink(vec![200]);
        let context = test_context(&dir, host);
        let historical_trace_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        let mut state = serde_json::json!({});
        commit_export_success(
            &context.state_file,
            &mut state,
            "parent",
            historical_end_offset,
            "",
            &[],
            &successful_turn_ids,
            successful_turn_count,
            "2026-08-16T00:00:59Z",
            Some(transcript_path.to_string_lossy().as_ref()),
            &[],
            &[],
        )
        .unwrap();
        assert!(!get_string_array_field(&state["parent"], "posted_turn_ids")
            .contains(&successful_turn_ids[0]));
        add_published_realtime_root(
            &mut state,
            "parent",
            &successful_turn_ids[0],
            "shared-tool",
            historical_trace_id,
            "historical-child",
        );
        crate::hooks::langfuse::state::save_state(&context.state_file, &state).unwrap();

        let outcome = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &stop_payload(&transcript_path), &context)
            .unwrap();

        assert_eq!(outcome, StopDispatchOutcome::Completed);
        let bodies = sink.join().unwrap();
        assert_single_turn_bodies(&bodies, &["Turn 202"]);
        let spans = all_otel_spans(&bodies[0]);
        assert!(spans
            .iter()
            .all(|span| span["traceId"] != serde_json::json!(historical_trace_id)));
        let final_state = load_state(&context.state_file);
        assert_eq!(
            get_int_field(&final_state["parent"], "turn_count") as usize,
            successful_turn_count + 1
        );
        assert!(final_state["parent"].get("pending_export_reason").is_none());
    }

    #[test]
    fn direct_stop_terminally_quarantines_fact_conflict() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let transcript = one_turn_transcript();
        std::fs::write(&transcript_path, &transcript).unwrap();
        let context = test_context(&dir, "http://127.0.0.1:9".to_string());
        let mut state = serde_json::json!({});
        let first = realtime_state::claim_tool_in_state(
            &mut state,
            "parent",
            "shared",
            "first",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            "parent",
            &first.context_id,
            Some("turn-1"),
        )
        .unwrap();
        realtime_state::rotate_current_in_state(&mut state, "parent");
        let second = realtime_state::claim_tool_in_state(
            &mut state,
            "parent",
            "shared",
            "second",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            "parent",
            &second.context_id,
            Some("turn-1"),
        )
        .unwrap();
        state["parent"]["realtime_export"]["pending"][0]["tool_use_ids"] =
            serde_json::json!(["shared"]);
        state["parent"]["realtime_export"]["pending"][0]["tool_span_ids"] =
            serde_json::json!({"shared": "1111111111111111"});
        state["parent"]["realtime_export"]["current"]["tool_use_ids"] =
            serde_json::json!(["shared"]);
        state["parent"]["realtime_export"]["current"]["tool_span_ids"] =
            serde_json::json!({"shared": "2222222222222222"});
        crate::hooks::langfuse::state::save_state(&context.state_file, &state).unwrap();

        let outcome = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &stop_payload(&transcript_path), &context)
            .unwrap();

        assert!(matches!(
            outcome,
            StopDispatchOutcome::TerminallyAccounted {
                exported_turns: false
            }
        ));
        let state = load_state(&context.state_file);
        assert_eq!(
            state["parent"]["quarantined_turns"][0]["reason"],
            serde_json::json!(FACT_CONFLICT_QUARANTINE_REASON)
        );
        assert_eq!(
            state["parent"]["export_accounting"]["terminal_turn_ids"],
            serde_json::json!(["turn-1"])
        );
        assert!(state["parent"].get("pending_export_reason").is_none());
        assert_eq!(
            state["parent"]["offset"],
            serde_json::json!(transcript.len() as i64)
        );
    }

    #[test]
    fn test_stop_terminal_poll_captures_three_second_late_terminal_and_returns_early() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let initial = r#"{"timestamp":"2026-07-13T00:00:00Z","type":"session_meta","payload":{"id":"parent","thread_source":"user"}}
{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-13T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"first"}}
{"timestamp":"2026-07-13T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"one"}}
{"timestamp":"2026-07-13T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"one"}}
{"timestamp":"2026-07-13T00:00:02Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-2"}}
{"timestamp":"2026-07-13T00:00:02.100Z","type":"event_msg","payload":{"type":"user_message","message":"second"}}
{"timestamp":"2026-07-13T00:00:02.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"two"}}
"#;
        std::fs::write(&transcript_path, initial).unwrap();

        let writer_path = transcript_path.clone();
        let writer = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_secs(3));
            let mut file = std::fs::OpenOptions::new()
                .append(true)
                .open(writer_path)
                .unwrap();
            writeln!(
                file,
                r#"{{"timestamp":"2026-07-13T00:00:02.300Z","type":"event_msg","payload":{{"type":"task_complete","turn_id":"turn-2","last_agent_message":"two"}}}}"#
            )
            .unwrap();
            file.flush().unwrap();
        });

        let (host, sink) = spawn_http_sink_with_deadline(
            vec![200, 200],
            Duration::from_millis(STOP_TERMINAL_POLL_MAX_MS),
        );
        let context = test_context(&dir, host);
        let stop_budget = context.stop_budget.unwrap().total_budget();
        let started = Instant::now();
        let mut payload = stop_payload(&transcript_path);
        payload["target_turn_id"] = serde_json::json!("turn-2");
        LangfuseHandler::new()
            .handle_stop(&payload, &context)
            .unwrap();
        let elapsed = started.elapsed();
        writer.join().unwrap();

        assert_single_turn_bodies(&sink.join().unwrap(), &["Turn 1", "Turn 2"]);
        let log = std::fs::read_to_string(dir.path().join("test.log")).unwrap();
        assert!(
            log.contains("Transcript terminal not visible; poll="),
            "Stop should poll while the target turn is still open"
        );
        assert!(
            log.lines().any(|line| {
                line.contains("Transcript terminal poll finished after")
                    && line.ends_with("target_terminal_seen=true")
            }),
            "Stop should observe the target terminal marker during polling"
        );
        assert!(
            elapsed < stop_budget,
            "Stop should finish before exhausting its budget, elapsed={elapsed:?}"
        );
        let state = load_state(&context.state_file);
        let transcript_len = std::fs::metadata(&transcript_path).unwrap().len() as i64;
        assert_eq!(state["parent"]["offset"], transcript_len);
        assert!(state["parent"]["pending_messages"]
            .as_array()
            .is_some_and(Vec::is_empty));
    }

    #[test]
    fn scheduled_recovery_scans_large_transcript_in_durable_byte_slices() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let line = concat!(
            "{\"timestamp\":\"2026-08-28T00:00:00Z\",",
            "\"type\":\"session_meta\",",
            "\"payload\":{\"id\":\"parent\",\"thread_source\":\"user\"}}\n"
        );
        let transcript = line.repeat(
            STOP_RECOVERY_TRANSCRIPT_SCAN_MAX_BYTES
                .saturating_mul(3)
                .div_ceil(line.len()),
        );
        std::fs::write(&transcript_path, transcript.as_bytes()).unwrap();

        let mut context = test_context(&dir, "http://127.0.0.1:9".to_string());
        context.stop_budget = Some(StopBudget::new(
            Instant::now(),
            Duration::from_millis(600),
            Duration::from_millis(400),
            Duration::from_millis(100),
        ));
        let mut payload = stop_payload(&transcript_path);
        payload["intent_id"] = serde_json::json!("intent-1");
        payload["target_turn_id"] = serde_json::json!("turn-final");
        payload["recovery_source"] = serde_json::json!("stop_recovery");

        let outcome = LangfuseHandler::new()
            .handle_stop_with_outcome(&HookEvent::Stop, &payload, &context)
            .unwrap();

        assert!(matches!(
            outcome,
            StopDispatchOutcome::Deferred(ref reason)
                if reason == "transcript scan slice exhausted"
        ));
        let state = load_state(&context.state_file);
        let offset = state["parent"]["offset"].as_i64().unwrap();
        assert!(offset > 0);
        assert!(offset < transcript.len() as i64);
    }

    #[test]
    fn target_turn_poll_is_bounded_and_exports_complete_prefix() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let transcript = r#"{"timestamp":"2026-07-13T00:00:00Z","type":"session_meta","payload":{"id":"parent","thread_source":"user"}}
{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-13T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"first"}}
{"timestamp":"2026-07-13T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"one"}}
{"timestamp":"2026-07-13T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"one"}}
{"timestamp":"2026-07-13T00:00:02Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-2"}}
{"timestamp":"2026-07-13T00:00:02.100Z","type":"event_msg","payload":{"type":"user_message","message":"second"}}
{"timestamp":"2026-07-13T00:00:02.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"two"}}
"#;
        std::fs::write(&transcript_path, transcript).unwrap();
        let (host, sink) = spawn_http_sink_with_deadline(vec![200], Duration::from_secs(12));
        let mut context = test_context(&dir, host);
        context.stop_budget = Some(StopBudget::new(
            Instant::now(),
            Duration::from_secs(12),
            Duration::from_secs(1),
            Duration::from_millis(100),
        ));
        let mut payload = stop_payload(&transcript_path);
        payload["target_turn_id"] = serde_json::json!("turn-2");
        let started = Instant::now();

        let outcome = LangfuseHandler::new()
            .handle_stop_with_outcome(&HookEvent::Stop, &payload, &context)
            .unwrap();

        assert!(
            matches!(outcome, StopDispatchOutcome::Deferred(ref reason) if reason.contains("target turn awaiting terminal")),
            "unexpected bounded poll outcome: {outcome:?}"
        );
        assert!(
            started.elapsed() >= Duration::from_secs(9)
                && started.elapsed() < Duration::from_secs(12),
            "target polling must be bounded"
        );
        assert_single_turn_bodies(&sink.join().unwrap(), &["Turn 1"]);
        let state = load_state(&context.state_file);
        assert_eq!(state["parent"]["turn_count"], serde_json::json!(1));
        assert!(state["parent"]["pending_messages"]
            .as_array()
            .is_some_and(|messages| !messages.is_empty()));
    }

    #[test]
    fn target_turn_missing_after_committed_offset_stays_deferred() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let transcript = one_turn_transcript();
        std::fs::write(&transcript_path, &transcript).unwrap();
        let (host, sink) = spawn_http_sink(vec![200]);
        let mut context = test_context(&dir, host);

        let first = LangfuseHandler::new()
            .handle_stop_with_outcome(&HookEvent::Stop, &stop_payload(&transcript_path), &context)
            .unwrap();
        assert!(first.completed_export());

        context.stop_budget = Some(StopBudget::new(
            Instant::now(),
            Duration::from_secs(12),
            Duration::from_secs(1),
            Duration::from_millis(100),
        ));
        let mut payload = stop_payload(&transcript_path);
        payload["target_turn_id"] = serde_json::json!("turn-2");
        let started = Instant::now();
        let second = LangfuseHandler::new()
            .handle_stop_with_outcome(&HookEvent::Stop, &payload, &context)
            .unwrap();

        assert!(
            matches!(second, StopDispatchOutcome::Deferred(ref reason) if reason.contains("target turn awaiting terminal: turn-2"))
        );
        assert!(
            started.elapsed() >= Duration::from_secs(9)
                && started.elapsed() < Duration::from_secs(12)
        );
        assert_single_turn_bodies(&sink.join().unwrap(), &["Turn 1"]);
        let state = load_state(&context.state_file);
        assert_eq!(state["parent"]["offset"], transcript.len() as i64);
    }

    #[test]
    fn test_failed_second_turn_checkpoints_first_and_restart_sends_remaining() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let transcript = three_turn_transcript();
        std::fs::write(&transcript_path, &transcript).unwrap();
        let payload = stop_payload(&transcript_path);
        let (first_host, first_sink) = spawn_http_sink(vec![200, 500]);
        let first_context = test_context(&dir, first_host);

        test_handler()
            .handle_stop(&payload, &first_context)
            .unwrap();

        assert_single_turn_bodies(&first_sink.join().unwrap(), &["Turn 1", "Turn 2"]);
        let partial = load_state(&first_context.state_file);
        assert_eq!(partial["parent"]["offset"], 0);
        assert_eq!(partial["parent"]["turn_count"], 1);
        assert_eq!(
            partial["parent"]["posted_turn_ids"],
            serde_json::json!(["turn-1"])
        );

        let (second_host, second_sink) = spawn_http_sink(vec![200, 200]);
        let second_context = test_context(&dir, second_host);
        test_handler()
            .handle_stop(&payload, &second_context)
            .unwrap();

        assert_single_turn_bodies(&second_sink.join().unwrap(), &["Turn 2", "Turn 3"]);
        let final_state = load_state(&second_context.state_file);
        assert_eq!(final_state["parent"]["offset"], transcript.len() as i64);
        assert_eq!(final_state["parent"]["turn_count"], 3);
    }

    #[test]
    fn test_all_turns_checkpointed_next_stop_only_advances_offset() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let transcript = three_turn_transcript();
        std::fs::write(&transcript_path, &transcript).unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let host = format!("http://{}", listener.local_addr().unwrap());
        let context = test_context(&dir, host);
        let state = serde_json::json!({
            "parent": {
                "offset": 0,
                "buffer": "",
                "pending_messages": [],
                "posted_turn_ids": ["turn-1", "turn-2", "turn-3"],
                "turn_count": 3,
                "pending_export_reason": PARTIAL_EXPORT_REASON,
                "transcript_path": transcript_path
            }
        });
        crate::hooks::langfuse::state::save_state(&context.state_file, &state).unwrap();

        test_handler()
            .handle_stop(&stop_payload(&transcript_path), &context)
            .unwrap();

        assert!(
            listener.accept().is_err(),
            "finalization must not POST again"
        );
        let final_state = load_state(&context.state_file);
        assert_eq!(final_state["parent"]["offset"], transcript.len() as i64);
        assert!(final_state["parent"].get("pending_export_reason").is_none());
    }

    #[test]
    fn test_checkpointed_open_turn_with_no_new_bytes_finalizes_without_post() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let transcript = r#"{"timestamp":"2026-07-13T00:00:00Z","type":"session_meta","payload":{"id":"parent","thread_source":"user"}}
{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-13T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"first"}}
{"timestamp":"2026-07-13T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"one"}}
"#;
        std::fs::write(&transcript_path, transcript).unwrap();
        let pending_messages: Vec<serde_json::Value> = transcript
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let host = format!("http://{}", listener.local_addr().unwrap());
        let context = test_context(&dir, host);
        let state = serde_json::json!({
            "parent": {
                "offset": transcript.len(),
                "buffer": "",
                "pending_messages": pending_messages,
                "posted_turn_ids": ["turn-1"],
                "turn_count": 1,
                "pending_export_reason": PARTIAL_EXPORT_REASON,
                "transcript_path": transcript_path
            }
        });
        crate::hooks::langfuse::state::save_state(&context.state_file, &state).unwrap();

        test_handler()
            .handle_stop(&stop_payload(&transcript_path), &context)
            .unwrap();

        assert!(
            listener.accept().is_err(),
            "checkpointed open turn must not POST again"
        );
        let final_state = load_state(&context.state_file);
        assert_eq!(final_state["parent"]["offset"], transcript.len());
        assert_eq!(
            final_state["parent"]["pending_messages"],
            serde_json::json!([])
        );
        assert!(final_state["parent"].get("pending_export_reason").is_none());
    }

    #[test]
    fn test_pending_child_does_not_block_parent_or_later_turns() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let transcript = r#"{"timestamp":"2026-07-13T00:00:00Z","type":"session_meta","payload":{"id":"parent","thread_source":"user"}}
{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-13T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"first"}}
{"timestamp":"2026-07-13T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"one"}}
{"timestamp":"2026-07-13T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"one"}}
{"timestamp":"2026-07-13T00:00:02Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-2"}}
{"timestamp":"2026-07-13T00:00:02.050Z","type":"event_msg","payload":{"type":"user_message","message":"run child"}}
{"timestamp":"2026-07-13T00:00:02.100Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","call_id":"spawn","arguments":"{\"message\":\"work\"}"}}
{"timestamp":"2026-07-13T00:00:02.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"spawn","output":"{\"agent_id\":\"missing-child\"}"}}
{"timestamp":"2026-07-13T00:00:02.300Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","call_id":"wait","arguments":"{\"targets\":[\"missing-child\"]}"}}
{"timestamp":"2026-07-13T00:00:02.400Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait","output":"{\"timed_out\":false}"}}
{"timestamp":"2026-07-13T00:00:02.500Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"two"}}
{"timestamp":"2026-07-13T00:00:02.600Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-2","last_agent_message":"two"}}
{"timestamp":"2026-07-13T00:00:03Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-3"}}
{"timestamp":"2026-07-13T00:00:03.100Z","type":"event_msg","payload":{"type":"user_message","message":"third"}}
{"timestamp":"2026-07-13T00:00:03.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"three"}}
{"timestamp":"2026-07-13T00:00:03.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-3","last_agent_message":"three"}}
"#;
        let parsed_messages: Vec<serde_json::Value> = transcript
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        let parsed_turns = build_turns(&parsed_messages);
        assert_eq!(
            crate::common::transcript::completed_subagent_identities(&parsed_turns[1])
                .into_iter()
                .map(|identity| identity.session_id)
                .collect::<Vec<_>>(),
            vec!["missing-child"]
        );
        std::fs::write(&transcript_path, transcript).unwrap();
        let (host, sink) = spawn_http_sink(vec![200, 200, 200]);
        let context = test_context(&dir, host);

        test_handler()
            .handle_stop(&stop_payload(&transcript_path), &context)
            .unwrap();

        assert_single_turn_bodies(&sink.join().unwrap(), &["Turn 1", "Turn 2", "Turn 3"]);
        let state = load_state(&context.state_file);
        assert_eq!(state["parent"]["offset"], transcript.len());
        assert_eq!(
            state["parent"]["posted_turn_ids"],
            serde_json::json!(["turn-1", "turn-2", "turn-3"])
        );
        assert_eq!(state["parent"]["turn_count"], 3);
        let progress = crate::hooks::langfuse::subagent_progress::records(&state, "parent");
        assert_eq!(progress.len(), 1);
        assert_eq!(progress[0].parent_turn_id, "turn-2");
        assert_eq!(progress[0].pending_child_ids, vec!["missing-child"]);
    }

    #[test]
    fn test_direct_stop_merges_accounted_realtime_spawn_then_checkpoints_idempotently() {
        let _fixture_polling = child_fixture_polling(None);
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("rollout-parent-v2.jsonl");
        let child_path = dir.path().join("rollout-child-v2.jsonl");
        std::fs::write(
            &transcript_path,
            include_str!("../../../tests/fixtures/codex_multi_agent_v2_parent.jsonl"),
        )
        .unwrap();
        let (host, sink) = spawn_http_sink(vec![200]);
        let context = test_context(&dir, host);
        let payload = serde_json::json!({
            "hook_event_name": "Stop",
            "session_id": "parent-v2",
            "transcript_path": transcript_path,
            "cwd": ""
        });

        let parent_messages =
            include_str!("../../../tests/fixtures/codex_multi_agent_v2_parent.jsonl")
                .lines()
                .map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap())
                .collect::<Vec<_>>();
        let parent_turns = build_turns(&parent_messages);
        let parent_turn = parent_turns.first().expect("parent turn");
        let mut state_with_realtime_accounting = serde_json::json!({});
        add_accounted_realtime_tool(
            &mut state_with_realtime_accounting,
            "parent-v2",
            "turn-v2-parent",
            "call-v2-spawn",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        );
        let realtime_context =
            realtime_state::contexts(&state_with_realtime_accounting, "parent-v2")
                .into_iter()
                .next()
                .expect("published realtime context");
        let realtime_spawn = crate::common::otel::build_realtime_tool_span_for_call(
            parent_turn,
            "call-v2-spawn",
            &realtime_context.trace_id,
            &realtime_context.root_span_id,
            "tester",
            "parent-v2",
        )
        .expect("realtime spawn TOOL span");
        let realtime_body = crate::common::otel::wrap_realtime_child_spans(
            vec![realtime_spawn],
            "parent-v2",
            "test-version",
        );
        assert!(accounted_realtime_child_span_ids(
            &state_with_realtime_accounting,
            "parent-v2",
            u64::MAX,
        )
        .contains(&crate::common::otel::deterministic_tool_span_id(
            "call-v2-spawn"
        )));
        crate::hooks::langfuse::state::save_state(
            &context.state_file,
            &state_with_realtime_accounting,
        )
        .unwrap();

        std::fs::write(
            &child_path,
            include_str!("../../../tests/fixtures/codex_multi_agent_v2_child.jsonl"),
        )
        .unwrap();

        let second = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &payload, &context)
            .unwrap();
        assert_eq!(second, StopDispatchOutcome::Completed);
        let posted_bodies = sink.join().unwrap();
        assert_eq!(posted_bodies.len(), 1);
        let stop_body = &posted_bodies[0];
        let stop_spans = all_otel_spans(stop_body);
        let agent = stop_spans
            .iter()
            .find(|span| span.get("name").and_then(serde_json::Value::as_str) == Some("agent Hume"))
            .expect("merged AGENT span");
        assert_eq!(
            span_string_attr(agent, "gen_ai.conversation.id").as_deref(),
            Some("child-v2")
        );
        assert_eq!(
            span_string_attr(agent, "agentic.subagent.id").as_deref(),
            Some("child-v2")
        );
        assert_eq!(
            span_string_attr(agent, "agentic.subagent.child_thread_id").as_deref(),
            Some("child-v2")
        );
        let agent_span_id = agent["spanId"].as_str().expect("AGENT span id");
        let descendants = stop_spans
            .iter()
            .filter(|span| span["parentSpanId"] == agent_span_id)
            .collect::<Vec<_>>();
        assert!(!descendants.is_empty(), "merged child descendants");
        assert!(descendants.iter().all(|span| {
            span_string_attr(span, "gen_ai.conversation.id").as_deref() == Some("child-v2")
                && span_string_attr(span, "agentic.subagent.id").as_deref() == Some("child-v2")
        }));
        assert!(!stop_spans.iter().any(|span| {
            span_string_attr(span, "gen_ai.tool.call.id").as_deref() == Some("call-v2-spawn")
        }));

        let mut replay_body = build_otel_export_request_for_turn(
            parent_turn,
            1,
            "parent-v2",
            "tester",
            "test-version",
            None,
        );
        let logger = crate::common::logger::Logger::new(dir.path().join("replay.log"), true);
        let replay_merge = merge_child_sessions_into_body(
            &mut replay_body,
            &parent_turns,
            &parent_turns,
            0,
            "parent-v2",
            dir.path(),
            transcript_path.to_str().unwrap(),
            None,
            "tester",
            "test-version",
            &logger,
            Some(ample_budget()),
        );
        let ChildMergeOutcome::Completed(replay_merge) = replay_merge else {
            panic!("offline replay merge must complete");
        };
        assert_eq!(replay_merge.merged_sync_ids, vec!["child-v2"]);

        let mut aggregate_live_body = stop_body.clone();
        aggregate_live_body["resourceSpans"]
            .as_array_mut()
            .expect("aggregate resource spans")
            .extend(
                realtime_body["resourceSpans"]
                    .as_array()
                    .expect("realtime resource spans")
                    .iter()
                    .cloned(),
            );
        let aggregate_spawn_count = all_otel_spans(&aggregate_live_body)
            .iter()
            .filter(|span| {
                span_string_attr(span, "gen_ai.tool.call.id").as_deref() == Some("call-v2-spawn")
            })
            .count();
        assert_eq!(aggregate_spawn_count, 1);
        let mut live_signature = semantic_span_signature(&aggregate_live_body);
        let mut replay_signature = semantic_span_signature(&replay_body);
        live_signature.sort_by_key(|value| serde_json::to_string(value).unwrap());
        replay_signature.sort_by_key(|value| serde_json::to_string(value).unwrap());
        assert_eq!(live_signature, replay_signature);

        let completed = load_state(&context.state_file);
        assert_eq!(
            completed["parent-v2"]["posted_turn_ids"],
            serde_json::json!(["turn-v2-parent"])
        );
        assert_eq!(
            completed["parent-v2"]["merged_subagent_ids"],
            serde_json::json!(["child-v2"])
        );
        assert_eq!(
            completed["parent-v2"]["merged_subagent_invocation_ids"],
            serde_json::json!(["child-v2:call-v2-spawn"])
        );
        assert!(completed["parent-v2"]
            .get("pending_export_reason")
            .is_none());

        let third = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &payload, &context)
            .unwrap();
        assert_eq!(third, StopDispatchOutcome::Completed);
        assert_eq!(
            load_state(&context.state_file)["parent-v2"]["posted_turn_ids"],
            serde_json::json!(["turn-v2-parent"])
        );
    }

    #[test]
    fn test_child_recovery_posts_late_native_child_after_parent_checkpoint_without_stop() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("rollout-parent-collab.jsonl");
        let child_path = dir.path().join("rollout-child-collab.jsonl");
        std::fs::write(
            &transcript_path,
            include_str!("../../../tests/fixtures/codex_collab_tool_call_same_turn_parent.jsonl"),
        )
        .unwrap();
        let (host, sink) = spawn_http_sink(vec![200, 200]);
        let context = test_context(&dir, host);
        let payload = serde_json::json!({
            "hook_event_name": "Stop",
            "session_id": "parent-collab",
            "transcript_path": transcript_path,
            "cwd": ""
        });

        let first = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &payload, &context)
            .unwrap();
        assert_eq!(first, StopDispatchOutcome::Completed);
        let pending = load_state(&context.state_file);
        let record =
            crate::hooks::langfuse::subagent_progress::records(&pending, "parent-collab").remove(0);
        assert_eq!(record.pending_child_ids, vec!["child-collab"]);
        assert_eq!(
            pending["parent-collab"]["posted_turn_ids"],
            serde_json::json!(["turn-collab-parent"])
        );

        std::fs::write(
            &child_path,
            include_str!("../../../tests/fixtures/codex_collab_tool_call_same_turn_child.jsonl"),
        )
        .unwrap();
        let _fixture_polling = child_fixture_polling(None);
        crate::cli::worker::run_subagent_recovery_with_context(
            "parent-collab",
            &record,
            &context.state_file,
            context.langfuse_config.as_ref().unwrap(),
            &context.logger,
            context.retry_policy,
        )
        .unwrap();
        let posted_bodies = sink.join().unwrap();
        assert_eq!(posted_bodies.len(), 2);
        assert!(all_otel_spans(&posted_bodies[1]).iter().any(|span| {
            span.get("name").and_then(serde_json::Value::as_str) == Some("agent Archimedes")
        }));
        assert!(all_otel_spans(&posted_bodies[1])
            .iter()
            .all(
                |span| span_string_attr(span, "gen_ai.conversation.id").as_deref()
                    == Some("child-collab")
            ));
        let recovered = crate::hooks::langfuse::subagent_progress::records(
            &load_state(&context.state_file),
            "parent-collab",
        )
        .remove(0);
        assert!(recovered.pending_child_ids.is_empty());
        // A repeated recovery uses its freshly persisted ledger and makes no POST.
        crate::cli::worker::run_subagent_recovery_with_context(
            "parent-collab",
            &record,
            &context.state_file,
            context.langfuse_config.as_ref().unwrap(),
            &context.logger,
            context.retry_policy,
        )
        .unwrap();

        let completed = load_state(&context.state_file);
        assert_eq!(
            completed["parent-collab"]["posted_turn_ids"],
            serde_json::json!(["turn-collab-parent"])
        );
        assert_eq!(
            completed["parent-collab"]["merged_subagent_ids"],
            serde_json::json!(["child-collab"])
        );
        assert_eq!(
            completed["parent-collab"]["merged_subagent_invocation_ids"],
            serde_json::json!(["child-collab:collab-spawn-1"])
        );
        assert!(completed["parent-collab"]
            .get("pending_export_reason")
            .is_none());

        let third = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &payload, &context)
            .unwrap();
        assert_eq!(third, StopDispatchOutcome::Completed);
        assert_eq!(
            load_state(&context.state_file)["parent-collab"]["posted_turn_ids"],
            serde_json::json!(["turn-collab-parent"])
        );
    }

    #[test]
    fn test_child_merge_stops_when_checkpoint_reserve_is_reached() {
        let dir = tempfile::tempdir().unwrap();
        let parent_path = dir.path().join("parent.jsonl");
        let child_path = dir.path().join("child.jsonl");
        std::fs::write(&parent_path, "{}\n").unwrap();
        std::fs::write(
            &child_path,
            r#"{"timestamp":"2026-07-13T00:00:00Z","type":"session_meta","payload":{"id":"child-1","parent_thread_id":"parent"}}
{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"child-turn"}}
{"timestamp":"2026-07-13T00:00:02Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"done"}}
{"timestamp":"2026-07-13T00:00:03Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"child-turn","last_agent_message":"done"}}
"#,
        )
        .unwrap();
        let parent_messages = vec![
            serde_json::json!({"timestamp":"2026-07-13T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"parent-turn"}}),
            serde_json::json!({"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"user_message","message":"work"}}),
            serde_json::json!({"timestamp":"2026-07-13T00:00:01.100Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","call_id":"spawn","arguments":"{\"message\":\"work\"}"}}),
            serde_json::json!({"timestamp":"2026-07-13T00:00:01.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"spawn","output":"{\"agent_id\":\"child-1\"}"}}),
            serde_json::json!({"timestamp":"2026-07-13T00:00:01.300Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","call_id":"wait","arguments":"{\"targets\":[\"child-1\"]}"}}),
            serde_json::json!({"timestamp":"2026-07-13T00:00:01.400Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait","output":"{\"timed_out\":false}"}}),
            serde_json::json!({"timestamp":"2026-07-13T00:00:02Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"done"}}),
            serde_json::json!({"timestamp":"2026-07-13T00:00:03Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"parent-turn","last_agent_message":"done"}}),
        ];
        let parent_turn = build_turns(&parent_messages).remove(0);
        let mut body = build_otel_export_request_for_turn(
            &parent_turn,
            1,
            "parent",
            "tester",
            "test-version",
            None,
        );
        let logger = crate::common::logger::Logger::new(dir.path().join("budget.log"), true);
        let budget = StopBudget::new(
            Instant::now(),
            Duration::from_millis(170),
            Duration::from_secs(1),
            Duration::from_millis(100),
        );
        let started = Instant::now();

        let result = merge_child_sessions_into_body(
            &mut body,
            std::slice::from_ref(&parent_turn),
            std::slice::from_ref(&parent_turn),
            0,
            "parent",
            dir.path(),
            parent_path.to_str().unwrap(),
            Some(dir.path().to_str().unwrap()),
            "tester",
            "test-version",
            &logger,
            Some(budget),
        );

        assert!(matches!(result, ChildMergeOutcome::BudgetExhausted));
        assert!(
            started.elapsed() < Duration::from_millis(150),
            "merge should stop before consuming the checkpoint reserve"
        );
    }

    #[test]
    fn test_non_2xx_responses_never_checkpoint() {
        for status in [302, 400, 500] {
            let dir = tempfile::tempdir().unwrap();
            let transcript_path = dir.path().join("parent.jsonl");
            let transcript = three_turn_transcript()
                .lines()
                .take(5)
                .collect::<Vec<_>>()
                .join("\n")
                + "\n";
            std::fs::write(&transcript_path, transcript).unwrap();
            let (host, sink) = spawn_http_sink(vec![status]);
            let context = test_context(&dir, host);

            test_handler()
                .handle_stop(&stop_payload(&transcript_path), &context)
                .unwrap();

            assert_single_turn_bodies(&sink.join().unwrap(), &["Turn 1"]);
            let state = load_state(&context.state_file);
            assert_eq!(state["parent"]["turn_count"], serde_json::Value::Null);
            assert_eq!(
                state["parent"]["pending_export_reason"],
                "otel_export_failed"
            );
        }
    }

    #[test]
    fn test_at_most_once_failure_checkpoints_before_post_and_is_not_retried() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let transcript = three_turn_transcript()
            .lines()
            .take(5)
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        std::fs::write(&transcript_path, &transcript).unwrap();
        let (host, sink) = spawn_http_sink(vec![500]);
        let mut context = test_context(&dir, host);
        context.retry_policy = RetryPolicy::AtMostOnce;

        test_handler()
            .handle_stop(&stop_payload(&transcript_path), &context)
            .unwrap();

        assert_single_turn_bodies(&sink.join().unwrap(), &["Turn 1"]);
        let state = load_state(&context.state_file);
        assert_eq!(state["parent"]["turn_count"], 1);
        assert_eq!(
            state["parent"]["posted_turn_ids"],
            serde_json::json!(["turn-1"])
        );
        assert_eq!(state["parent"]["offset"], transcript.len() as i64);
        assert!(state["parent"].get("pending_export_reason").is_none());

        context.langfuse_config.as_mut().unwrap().host = "http://127.0.0.1:9".to_string();
        context.stop_budget = Some(ample_budget());
        let payload = serde_json::json!({
            "hook_event_name": "SessionEnd",
            "session_id": "parent",
            "transcript_path": transcript_path,
            "cwd": ""
        });
        LangfuseHandler::new()
            .handle(&HookEvent::SessionEnd, &payload, &context)
            .unwrap();

        let log = std::fs::read_to_string(dir.path().join("test.log")).unwrap();
        assert_eq!(
            log.matches("Failed to post session=parent turn_id=turn-1")
                .count(),
            1,
            "SessionEnd must not retry a turn already consumed by at_most_once"
        );
    }

    #[test]
    fn test_stop_does_not_follow_redirect_to_success() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let transcript = three_turn_transcript()
            .lines()
            .take(5)
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        std::fs::write(&transcript_path, transcript).unwrap();
        let (host, sink) = spawn_redirect_sink();
        let context = test_context(&dir, host);

        test_handler()
            .handle_stop(&stop_payload(&transcript_path), &context)
            .unwrap();

        assert_eq!(sink.join().unwrap(), 1, "Stop must not follow the redirect");
        let state = load_state(&context.state_file);
        assert_eq!(state["parent"]["turn_count"], serde_json::Value::Null);
        assert_eq!(
            state["parent"]["pending_export_reason"],
            "otel_export_failed"
        );
    }

    #[test]
    fn direct_stop_fallback_turns_do_not_reuse_stale_tool_trace() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("session.jsonl");
        let mut records = vec![
            serde_json::json!({"timestamp":"2026-09-03T00:00:00Z","type":"session_meta","payload":{"id":"parent","thread_source":"user"}}),
        ];
        for index in 1..=3 {
            let turn_id = format!("fallback-{index}");
            let timestamp = format!("2026-09-03T00:00:0{index}Z");
            records.push(serde_json::json!({"timestamp":timestamp,"type":"event_msg","payload":{"type":"task_started","turn_id":turn_id}}));
            records.push(serde_json::json!({"timestamp":timestamp,"type":"event_msg","payload":{"type":"user_message","message":"continue"}}));
            if index == 3 {
                records.push(serde_json::json!({"timestamp":timestamp,"type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"done"}}));
            }
            let mut terminal = serde_json::json!({"type":"task_complete","turn_id":turn_id,"last_agent_message":null});
            if index < 3 {
                terminal["error"] =
                    serde_json::json!({"message":"404 Not Found","codex_error_info":"other"});
            } else {
                terminal["last_agent_message"] = serde_json::json!("done");
            }
            records.push(
                serde_json::json!({"timestamp":timestamp,"type":"event_msg","payload":terminal}),
            );
        }
        std::fs::write(
            &transcript_path,
            records.iter().map(|v| format!("{v}\n")).collect::<String>(),
        )
        .unwrap();
        let (host, sink) = spawn_http_sink(vec![200, 200, 200]);
        let context = test_context(&dir, host);
        let stale_trace = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        let stale_context = serde_json::json!({"trace_id":stale_trace,"traceparent":"00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"});
        let state = serde_json::json!({"parent":{"trace_context_history":[stale_context.clone(), stale_context.clone()],"current_trace_context":stale_context}});
        crate::hooks::langfuse::state::save_state(&context.state_file, &state).unwrap();
        test_handler()
            .handle_stop(&stop_payload(&transcript_path), &context)
            .unwrap();
        let bodies = sink.join().unwrap();
        assert_eq!(bodies.len(), 3);
        let roots: Vec<_> = bodies
            .iter()
            .flat_map(all_otel_spans)
            .filter(|span| {
                span["name"]
                    .as_str()
                    .is_some_and(|name| name.starts_with("Turn "))
            })
            .collect();
        assert_eq!(roots.len(), 3);
        let traces: std::collections::HashSet<_> = roots
            .iter()
            .map(|span| span["traceId"].as_str().unwrap())
            .collect();
        assert_eq!(traces.len(), 3);
        assert!(!traces.contains(stale_trace));
    }

    #[test]
    fn test_supported_events_include_stop_and_session_end() {
        // Codex sub-agents are always sync (spawn_agent + wait_agent). The
        // child's own Stop (or a mismatched Stop) is routed via `handle_stop`'s
        // subagent detection; the SubagentStop/SubagentStart enum variants no
        // longer exist (removed alongside the async sub-agent path).
        let handler = LangfuseHandler::new();
        let events = handler.supported_events();
        assert_eq!(events, &[HookEvent::Stop, HookEvent::SessionEnd]);
    }

    #[test]
    fn direct_stop_persists_superseded_turn_before_advancing_cursor() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("parent.jsonl");
        let mut records = vec![
            serde_json::json!({"type":"session_meta","payload":{"id":"parent","thread_source":"user"}}),
        ];
        for index in 1..=2 {
            let turn_id = format!("native-{index}");
            let timestamp = format!("2026-09-04T00:00:0{index}Z");
            records.push(serde_json::json!({"timestamp":timestamp,"type":"event_msg","payload":{"type":"task_started","turn_id":turn_id}}));
            records.push(serde_json::json!({"timestamp":timestamp,"type":"event_msg","payload":{"type":"user_message","message":"continue"}}));
            records.push(serde_json::json!({"timestamp":timestamp,"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"observed output"}]}}));
            records.push(serde_json::json!({"timestamp":timestamp,"type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"output_tokens":2}}}}));
            if index == 2 {
                records.push(serde_json::json!({"timestamp":timestamp,"type":"event_msg","payload":{"type":"task_complete","turn_id":turn_id,"last_agent_message":"observed output"}}));
            }
        }
        let transcript = records
            .iter()
            .map(|record| format!("{record}\n"))
            .collect::<String>();
        std::fs::write(&transcript_path, &transcript).unwrap();
        let (host, sink) = spawn_http_sink(vec![200, 200]);
        let context = test_context(&dir, host);
        let result = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &stop_payload(&transcript_path), &context)
            .unwrap();
        assert_eq!(result, StopDispatchOutcome::Completed);
        let bodies = sink.join().unwrap();
        assert_eq!(bodies.len(), 2);
        let state = load_state(&context.state_file);
        assert_eq!(
            state["parent"]["posted_turn_ids"],
            serde_json::json!(["native-1", "native-2"])
        );
        assert_eq!(state["parent"]["offset"], transcript.len() as i64);
        assert!(state["parent"]["pending_messages"]
            .as_array()
            .unwrap()
            .is_empty());

        // Loading persisted state in a new Stop must not attempt another POST
        // (the mock endpoint has already closed).
        let retry = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &stop_payload(&transcript_path), &context)
            .unwrap();
        assert_eq!(retry, StopDispatchOutcome::Completed);
    }

    #[test]
    fn direct_stop_native_usage_fixture_counts_each_response_once() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("parent.jsonl");
        let source = include_str!("../../../tests/fixtures/token_usage_alignment.jsonl");
        let records = source
            .lines()
            .map(|line| {
                let mut record: serde_json::Value = serde_json::from_str(line).unwrap();
                if record["type"] == "session_meta" {
                    record["payload"]["id"] = serde_json::json!("parent");
                    record["payload"]["session_id"] = serde_json::json!("parent");
                }
                format!("{record}\n")
            })
            .collect::<String>();
        std::fs::write(&path, &records).unwrap();
        let (host, sink) = spawn_http_sink(vec![200, 200]);
        let context = test_context(&dir, host);
        let result = test_handler()
            .handle_stop_with_outcome(&HookEvent::Stop, &stop_payload(&path), &context)
            .unwrap();
        assert_eq!(result, StopDispatchOutcome::Completed);
        let bodies = sink.join().unwrap();
        assert_eq!(bodies.len(), 2);
        let mut response_ids = HashSet::new();
        let mut total = 0_i64;
        for span in bodies.iter().flat_map(all_otel_spans) {
            let Some(response_id) = span_string_attr(span, "gen_ai.response.id") else {
                continue;
            };
            assert!(
                response_ids.insert(response_id),
                "native response exported twice"
            );
            for attr in span["attributes"].as_array().unwrap() {
                if matches!(
                    attr["key"].as_str(),
                    Some("gen_ai.usage.input_tokens" | "gen_ai.usage.output_tokens")
                ) {
                    let value = attr["value"]
                        .get("intValue")
                        .or_else(|| attr["value"].get("stringValue"))
                        .expect("usage has a numeric wire value");
                    total += value
                        .as_i64()
                        .unwrap_or_else(|| value.as_str().unwrap().parse().unwrap());
                }
            }
        }
        assert_eq!(response_ids.len(), 6);
        assert_eq!(total, 693_893);
        let state = load_state(&context.state_file);
        assert_eq!(state["parent"]["offset"], records.len() as i64);
        assert_eq!(
            state["parent"]["compaction_token_cursor"]["native_usage_mode"],
            true
        );
    }

    #[test]
    fn test_direct_stop_reuses_the_unique_persisted_realtime_root() {
        let turns = vec![crate::common::transcript::Turn {
            chat_id: Some("turn-1".to_string()),
            turn_id: "turn-1".to_string(),
            user_timestamp: "2026-08-16T00:00:00Z".to_string(),
            user_text: "hello".to_string(),
            user_content: serde_json::Value::Null,
            assistants: vec![],
            tool_calls: vec![],
            model: "gpt-5.5".to_string(),
            cwd: "/workspace".to_string(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: "2026-08-16T00:00:01Z".to_string(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events: vec![],
            subagent_source: None,
        }];
        let state = serde_json::json!({
            "sess": {
                "realtime_export": {
                    "current": {
                        "schema_version": 1,
                        "context_id": "context-1",
                        "trace_id": "11111111111111111111111111111111",
                        "root_span_id": "3333333333333333",
                        "turn_id": "turn-1",
                        "tool_use_ids": [],
                        "tool_span_ids": {},
                        "tool_jobs": {},
                        "tool_timings": {},
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "created_at": "2026-08-16T00:00:00Z"
                    }
                }
            }
        });

        let opts = build_finalizer_turn_trace_opts(&state, "sess", &turns, 0)
            .expect("unique realtime topology")
            .expect("realtime trace opts");
        assert_eq!(
            opts.trace_id.as_deref(),
            Some("11111111111111111111111111111111")
        );
        assert_eq!(opts.root_span_id.as_deref(), Some("3333333333333333"));
    }

    #[test]
    fn test_direct_stop_reuses_generation_identity_after_nonzero_scan_base() {
        let turns = vec![crate::common::transcript::Turn {
            chat_id: Some("turn-6".to_string()),
            turn_id: "turn-6".to_string(),
            user_timestamp: "2026-08-16T00:00:00Z".to_string(),
            user_text: "hello again".to_string(),
            user_content: serde_json::Value::Null,
            assistants: vec![crate::common::transcript::AssistantMessage {
                timestamp: "2026-08-16T00:00:01Z".to_string(),
                text: "done".to_string(),
                media_content: serde_json::Value::Null,
                phase: "final_answer".to_string(),
                usage: Default::default(),
                token_count_timestamp: String::new(),
                invocation_started_timestamp: String::new(),
                invocation_end_source: None,
            }],
            tool_calls: vec![],
            model: "gpt-5.5".to_string(),
            cwd: "/workspace".to_string(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: "2026-08-16T00:00:01Z".to_string(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events: vec![],
            subagent_source: None,
        }];
        let state = serde_json::json!({
            "sess": {
                "realtime_export": {
                    "current": {
                        "schema_version": 1,
                        "context_id": "context-6",
                        "trace_id": "11111111111111111111111111111111",
                        "root_span_id": "3333333333333333",
                        "turn_id": null,
                        "tool_use_ids": [],
                        "tool_span_ids": {},
                        "tool_jobs": {},
                        "tool_timings": {},
                        "generation_batch_size": 3,
                        "retry_policy": "at_least_once",
                        "created_at": "2026-08-16T00:00:00Z"
                    },
                    "registered_jobs": {
                        "generation-job-5": {
                            "job_id": "generation-job-5",
                            "payload_digest": "generation-digest-5",
                            "context_id": "context-6",
                            "kind": "generation",
                            "sequence": 1,
                            "status": "accounted"
                        }
                    },
                    "generation": {
                        "scan_base_ordinal": 5,
                        "rounds": [{
                            "generation_id": "generation-5",
                            "span_id": "5555555555555555",
                            "context_id": "context-6",
                            "ordinal": 5,
                            "status": "accounted",
                            "job_id": "generation-job-5",
                            "payload_digest": "generation-digest-5"
                        }]
                    }
                }
            }
        });

        let opts = build_finalizer_turn_trace_opts(&state, "sess", &turns, 0)
            .expect("scan-base-aware realtime topology")
            .expect("realtime trace opts");
        assert_eq!(
            opts.trace_id.as_deref(),
            Some("11111111111111111111111111111111")
        );
        assert_eq!(opts.root_span_id.as_deref(), Some("3333333333333333"));
        assert_eq!(
            opts.generation_span_ids.get(&0).map(String::as_str),
            Some("5555555555555555")
        );
    }

    #[test]
    fn test_handle_stop_decision_skips_sync_child_with_mismatched_payload() {
        use crate::common::transcript::{extract_session_meta_ids, is_child_sync_in_parent};

        let dir = tempfile::tempdir().unwrap();
        let parent_path = dir.path().join("rollout-parent.jsonl");
        std::fs::write(
            &parent_path,
            r#"{"type":"session_meta","payload":{"id":"parent-1"}}
{"timestamp":"2026-07-10T00:00:00Z","type":"event_msg","payload":{"type":"user_message","message":"run child"}}
{"timestamp":"2026-07-10T00:00:01Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","call_id":"spawn-1","arguments":"{\"message\":\"work\"}"}}
{"timestamp":"2026-07-10T00:00:02Z","type":"response_item","payload":{"type":"function_call_output","call_id":"spawn-1","output":"{\"agent_id\":\"child-1\"}"}}
{"timestamp":"2026-07-10T00:00:03Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","call_id":"wait-1","arguments":"{\"targets\":[\"child-1\"]}"}}
{"timestamp":"2026-07-10T00:00:04Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait-1","output":"{\"timed_out\":false}"}}
"#,
        )
        .unwrap();
        std::fs::write(
            dir.path().join("rollout-child.jsonl"),
            r#"{"type":"session_meta","payload":{"id":"child-1","source":{"subagent":{"thread_spawn":{"parent_thread_id":"parent-1"}}}}}
"#,
        )
        .unwrap();

        // Codex Desktop bug shape: payload says parent, transcript is child.
        let payload_sid = "parent-1";
        let head = std::fs::read_to_string(dir.path().join("rollout-child.jsonl")).unwrap();
        let meta = extract_session_meta_ids(&head);
        let canonical_sid = meta.session_id.as_deref().unwrap_or(payload_sid);
        assert_ne!(canonical_sid, payload_sid, "mismatch must be detected");
        assert_eq!(canonical_sid, "child-1");

        let parent_tid = meta
            .parent_thread_id
            .as_deref()
            .expect("child must have parent");
        assert!(
            is_child_sync_in_parent(
                dir.path(),
                parent_tid,
                canonical_sid,
                meta.agent_path.as_deref(),
            ),
            "parent has wait_agent for child -> sync"
        );
    }

    #[test]
    fn test_build_postable_turns_holds_final_answer_until_task_complete() {
        // Simple "hello" turn: no tool_calls, final agent_message present
        // (Codex emits `phase: "final_answer"` on the closing assistant message).
        // It must remain pending until an authoritative terminal marker arrives.
        let hello: Vec<serde_json::Value> = vec![
            serde_json::json!({"type":"session_meta","payload":{"id":"sess-1"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"user_message","message":"hello"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"hi there"}}),
            serde_json::json!({"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"hi there"}]}}),
        ];
        let (turns, pending) = build_postable_turns(&[], &hello, &[]);
        assert!(turns.is_empty(), "open final-answer turn must not export");
        assert!(!pending.is_empty(), "open turn must remain pending");
    }

    #[test]
    fn test_retry_cache_rebuilds_only_when_complete_messages_change() {
        let messages = vec![
            serde_json::json!({"type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"user_message","message":"hello"}}),
        ];
        let mut cache = None;

        assert!(refresh_postable_turn_cache(
            &mut cache,
            false,
            &[],
            &messages,
            &[],
            None,
            false,
        ));
        assert!(!refresh_postable_turn_cache(
            &mut cache,
            false,
            &[],
            &messages,
            &[],
            None,
            false,
        ));
        assert!(refresh_postable_turn_cache(
            &mut cache,
            true,
            &[],
            &messages,
            &[],
            None,
            false,
        ));
    }

    #[test]
    fn test_real_subagent_fixture_without_task_complete_stays_pending() {
        let messages: Vec<serde_json::Value> =
            include_str!("../../../tests/fixtures/codex_multi_agent_v2_child.jsonl")
                .lines()
                .filter_map(|line| serde_json::from_str(line).ok())
                .filter(|entry: &serde_json::Value| {
                    entry
                        .pointer("/payload/type")
                        .and_then(|value| value.as_str())
                        != Some("task_complete")
                })
                .collect();

        let (turns, pending) = build_postable_turns(&[], &messages, &[]);
        assert!(
            turns.is_empty(),
            "fixture must not export before task_complete"
        );
        assert!(!pending.is_empty(), "fixture must retain the open turn");
    }

    #[test]
    fn test_build_postable_turns_holds_subagent_turn_while_wait_pending() {
        // Parent mid-task: spawn_agent returned, wait_agent issued but not yet
        // returned. Without an authoritative terminal marker the turn stays
        // pending until a later Stop or SessionEnd.
        let mid: Vec<serde_json::Value> = vec![
            serde_json::json!({"type":"session_meta","payload":{"id":"sess-p"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"task_started","turn_id":"turn-p"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"user_message","message":"spawn one"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"ok, spawning"}}),
            serde_json::json!({"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok, spawning"}]}}),
            serde_json::json!({"type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{}","call_id":"c1"}}),
            serde_json::json!({"type":"response_item","payload":{"type":"function_call_output","call_id":"c1","output":"{\"agent_id\":\"a1\"}"}}),
            serde_json::json!({"type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"targets\":[\"a1\"]}","call_id":"c2"}}),
            // NOTE: no function_call_output for c2 — wait_agent still pending.
        ];
        let (turns, pending) = build_postable_turns(&[], &mid, &[]);
        assert!(
            turns.is_empty(),
            "must not flush while wait_agent is pending"
        );
        assert!(!pending.is_empty(), "pending must retain the open turn");
    }

    #[test]
    fn test_build_postable_turns_completed_branch_still_works_with_task_complete() {
        // Backwards-compat: explicit task_complete still flushes via the
        // `completed_messages` branch.
        let full: Vec<serde_json::Value> = vec![
            serde_json::json!({"type":"session_meta","payload":{"id":"sess-1"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"user_message","message":"hello"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"agent_message","message":"hi"}}),
            serde_json::json!({"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"hi"}]}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"hi"}}),
        ];
        let (turns, pending) = build_postable_turns(&[], &full, &[]);
        assert_eq!(
            turns.len(),
            1,
            "task_complete path must still produce a turn"
        );
        assert_eq!(
            turns[0].terminal_state,
            crate::common::transcript::TurnTerminalState::Completed
        );
        assert!(pending.is_empty());
    }

    #[test]
    fn test_failed_terminal_turn_without_assistant_output_is_postable() {
        let messages = include_str!("../../../tests/fixtures/invalid_model_terminal_error.jsonl")
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect::<Vec<serde_json::Value>>();

        let outcome = build_postable_turn_outcome(&[], &messages, &[]);
        assert_eq!(outcome.turns.len(), 1);
        assert_eq!(outcome.turns[0].terminal_state, TurnTerminalState::Failed);
        assert!(outcome.turns[0].assistants.is_empty());
        assert!(outcome.turns[0].terminal_error.is_some());
        assert!(outcome.pending_messages.is_empty());
        assert!(outcome.diagnostics.is_empty());
    }

    #[test]
    fn test_compaction_only_turn_posts_without_reposting_previous_turn() {
        let messages = include_str!("../../../tests/fixtures/compaction_manual_two_turns.jsonl")
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect::<Vec<serde_json::Value>>();

        let outcome = build_postable_turn_outcome(&[], &messages, &["ordinary-turn".to_string()]);
        assert_eq!(outcome.turns.len(), 1);
        assert_eq!(outcome.turns[0].turn_id, "compaction-turn");
        assert_eq!(outcome.turns[0].compactions[0].post_tokens, Some(5552));
        assert!(outcome.pending_messages.is_empty());
        assert!(outcome.diagnostics.is_empty());
    }

    #[test]
    fn incremental_compaction_backfills_pre_tokens_from_checkpointed_history() {
        let messages = include_str!("../../../tests/fixtures/compaction_manual_two_turns.jsonl")
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect::<Vec<serde_json::Value>>();
        let second_turn_start = messages
            .iter()
            .position(|entry| {
                entry
                    .pointer("/payload/turn_id")
                    .and_then(serde_json::Value::as_str)
                    == Some("compaction-turn")
            })
            .unwrap();
        let outcome = build_postable_turn_outcome(
            &[],
            &messages[second_turn_start..],
            &["ordinary-turn".to_string()],
        );
        assert_eq!(outcome.turns[0].compactions[0].pre_tokens, None);

        let outcome = build_postable_turn_outcome_with_initial_input(
            &[],
            &messages[second_turn_start..],
            &["ordinary-turn".to_string()],
            Some(36941),
            false,
        );

        assert_eq!(outcome.turns[0].compactions[0].pre_tokens, Some(36941));
        assert_eq!(outcome.turns[0].compactions[0].post_tokens, Some(5552));
    }

    #[test]
    fn seeded_incremental_builder_maps_each_compaction_in_stream_order() {
        let messages = vec![
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"two-compactions"}}),
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"compact twice"}}),
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.200Z","type":"compacted","payload":{"replacement_history":[]}}),
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.300Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":0,"total_tokens":50}}}}),
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.400Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"continued"}}),
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.500Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":200,"output_tokens":5,"total_tokens":205}}}}),
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.600Z","type":"compacted","payload":{"replacement_history":[]}}),
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.700Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":0,"total_tokens":80}}}}),
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.800Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"two-compactions","last_agent_message":"done"}}),
        ];

        let turns =
            crate::common::transcript::build_turns_with_initial_input_tokens(&messages, Some(100));

        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].compactions.len(), 2);
        assert_eq!(turns[0].compactions[0].pre_tokens, Some(100));
        assert_eq!(turns[0].compactions[0].post_tokens, Some(50));
        assert_eq!(turns[0].compactions[1].pre_tokens, Some(200));
        assert_eq!(turns[0].compactions[1].post_tokens, Some(80));
    }

    #[test]
    fn compaction_cursor_initializes_once_and_round_trips_at_exact_offset() {
        let directory = tempfile::tempdir().unwrap();
        let transcript = directory.path().join("session.jsonl");
        let prefix = [
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":321,"output_tokens":1,"total_tokens":322}}}}),
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"pending-turn"}}),
        ];
        let content = prefix
            .iter()
            .map(serde_json::Value::to_string)
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        std::fs::write(&transcript, &content).unwrap();
        let pending = vec![prefix[1].clone()];
        let offset = content.len() as i64;

        let initialized = initialize_compaction_cursor(&transcript, offset, "", &pending);
        assert_eq!(initialized.last_trusted_input_tokens, Some(321));
        assert_eq!(initialized.pending_seed_tokens, Some(321));

        let session = serde_json::json!({"compaction_token_cursor": initialized.to_value()});
        assert_eq!(
            CompactionTokenCursor::from_session(Some(&session), offset),
            Some(initialized)
        );
        assert_eq!(
            CompactionTokenCursor::from_session(Some(&session), offset + 1),
            None,
            "a cursor from another transcript offset must never be reused"
        );
    }

    #[test]
    fn compaction_cursor_keeps_seed_before_pending_suffix() {
        let previous = CompactionTokenCursor {
            offset: 10,
            last_trusted_input_tokens: Some(100),
            pending_seed_tokens: None,
            native_usage_mode: false,
            pending_native_usage_mode: false,
        };
        let previous_pending = vec![
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"pending"}}),
        ];
        let new_messages = vec![
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.100Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":240,"total_tokens":245}}}}),
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.200Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"pending","last_agent_message":"done"}}),
            serde_json::json!({"timestamp":"2026-08-23T04:00:01.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"next-pending"}}),
        ];
        let pending = vec![new_messages[2].clone()];

        let advanced = advance_compaction_cursor(
            previous,
            20,
            Some(100),
            &previous_pending,
            &new_messages,
            &pending,
        );

        assert_eq!(advanced.last_trusted_input_tokens, Some(240));
        assert_eq!(advanced.pending_seed_tokens, Some(240));
        assert_eq!(advanced.offset, 20);
    }

    #[test]
    fn native_usage_mode_survives_committed_cursor_and_pending_suffix() {
        let next_start = serde_json::json!({"timestamp":"2026-09-05T00:00:02Z","type":"event_msg","payload":{"type":"task_started","turn_id":"no-call"}});
        let previous_messages = vec![
            serde_json::json!({"type":"token_usage_record","payload":{"response_id":"resp-prior","usage":{"input_tokens":111000,"output_tokens":437,"total_tokens":111437}}}),
            next_start.clone(),
        ];
        let cursor = advance_compaction_cursor(
            CompactionTokenCursor::fresh(0),
            100,
            None,
            &[],
            &previous_messages,
            &[next_start.clone()],
        );
        let state = serde_json::json!({"compaction_token_cursor":cursor.to_value()});
        let restored = CompactionTokenCursor::from_session(Some(&state), 100).unwrap();
        assert!(restored.native_usage_mode);
        assert!(restored.pending_native_usage_mode);
        let suffix = vec![
            serde_json::json!({"timestamp":"2026-09-05T00:00:03Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":111000,"output_tokens":437,"total_tokens":111437}}}}),
            serde_json::json!({"timestamp":"2026-09-05T00:00:04Z","type":"event_msg","payload":{"type":"turn_aborted","turn_id":"no-call"}}),
        ];
        let outcome = build_postable_turn_outcome_with_initial_input(
            &[next_start],
            &suffix,
            &[],
            None,
            restored.pending_native_usage_mode,
        );
        assert_eq!(outcome.turns.len(), 1);
        assert!(outcome.turns[0]
            .assistants
            .iter()
            .all(|assistant| !assistant.usage.has_any()));
        assert!(outcome.pending_messages.is_empty());
    }

    #[test]
    fn legacy_cursor_bootstrap_recovers_native_usage_mode() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("transcript.jsonl");
        let record = serde_json::json!({"type":"token_usage_record","payload":{"response_id":"resp-prior","usage":{"input_tokens":10,"output_tokens":2}}});
        let data = format!("{record}\n");
        std::fs::write(&path, &data).unwrap();
        let legacy = serde_json::json!({"compaction_token_cursor":{"version":1,"initialized":true,"offset":data.len()}});
        assert!(CompactionTokenCursor::from_session(Some(&legacy), data.len() as i64).is_none());
        let initialized = initialize_compaction_cursor(&path, data.len() as i64, "", &[]);
        assert!(initialized.native_usage_mode);
    }

    #[test]
    fn completed_window_advances_cursor_without_retaining_pending_seed() {
        let previous = CompactionTokenCursor {
            offset: 10,
            last_trusted_input_tokens: Some(100),
            pending_seed_tokens: Some(80),
            native_usage_mode: false,
            pending_native_usage_mode: false,
        };
        let previous_pending = vec![
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"completed"}}),
        ];
        let new_messages = vec![
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.100Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":240,"total_tokens":245}}}}),
            serde_json::json!({"timestamp":"2026-08-23T04:00:00.200Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"completed","last_agent_message":"done"}}),
        ];

        let advanced = advance_compaction_cursor(
            previous,
            20,
            Some(80),
            &previous_pending,
            &new_messages,
            &[],
        );

        assert_eq!(advanced.offset, 20);
        assert_eq!(advanced.last_trusted_input_tokens, Some(240));
        assert_eq!(advanced.pending_seed_tokens, None);
    }

    #[test]
    fn test_terminal_but_unpostable_turn_retains_window_for_retry() {
        let messages = vec![
            serde_json::json!({"timestamp":"2026-08-15T07:58:20.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-missing-input"}}),
            serde_json::json!({"timestamp":"2026-08-15T07:58:35.000Z","type":"response_item","payload":{"type":"message","role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"done"}]}}),
            serde_json::json!({"timestamp":"2026-08-15T07:58:35.100Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-missing-input","last_agent_message":"done"}}),
        ];

        let (turns, pending) = build_postable_turns(&[], &messages, &[]);
        assert!(turns.is_empty());
        assert_eq!(pending, messages, "cursor window must remain retryable");

        let outcome = build_postable_turn_outcome(&[], &messages, &[]);
        assert!(outcome.terminal_seen, "terminal visibility is independent");
        assert!(outcome
            .diagnostics
            .iter()
            .any(|reason| reason == "terminal_missing_input:turn-missing-input"));
    }

    #[test]
    fn test_usage_only_terminal_turn_retains_window_for_missing_root_output() {
        let messages = vec![
            serde_json::json!({"timestamp":"2026-08-15T07:58:20.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-missing-output"}}),
            serde_json::json!({"timestamp":"2026-08-15T07:58:20.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}),
            serde_json::json!({"timestamp":"2026-08-15T07:58:35.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":10,"output_tokens":2}}}}),
            serde_json::json!({"timestamp":"2026-08-15T07:58:35.100Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-missing-output"}}),
        ];

        let outcome = build_postable_turn_outcome(&[], &messages, &[]);
        assert!(outcome.turns.is_empty());
        assert_eq!(
            outcome.pending_messages, messages,
            "usage metadata is not a user-facing root output"
        );
        assert!(outcome.terminal_seen, "terminal visibility is independent");
        assert!(outcome
            .diagnostics
            .iter()
            .any(|reason| reason == "terminal_missing_output:turn-missing-output"));
    }

    #[test]
    fn test_unknown_window_is_retained_but_internal_bootstrap_is_safe_to_skip() {
        let unknown = vec![serde_json::json!({
            "timestamp":"2026-08-15T07:58:20.000Z",
            "type":"event_msg",
            "payload":{"type":"future_turn_terminal","turn_id":"future-turn"}
        })];
        let outcome = build_postable_turn_outcome(&[], &unknown, &[]);
        assert_eq!(outcome.pending_messages, unknown);
        assert!(outcome
            .diagnostics
            .iter()
            .any(|reason| reason == "unknown_or_incomplete_transcript_window"));

        let bootstrap = vec![
            serde_json::json!({"type":"session_meta","payload":{"id":"session"}}),
            serde_json::json!({"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"# AGENTS.md instructions for /workspace\n<environment_context>bootstrap</environment_context>"}]}}),
        ];
        let outcome = build_postable_turn_outcome(&[], &bootstrap, &[]);
        assert!(outcome.pending_messages.is_empty());
        assert!(outcome.turns.is_empty());
        assert!(!outcome.terminal_seen);
        assert!(outcome.diagnostics.is_empty());
    }

    #[test]
    fn test_open_latest_turn_does_not_report_terminal_from_completed_prefix() {
        let messages = vec![
            serde_json::json!({"type":"event_msg","payload":{"type":"task_started","turn_id":"done"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"user_message","message":"one"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"task_complete","turn_id":"done","last_agent_message":"done"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"task_started","turn_id":"open"}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"user_message","message":"two"}}),
        ];
        let outcome = build_postable_turn_outcome(&[], &messages, &[]);
        assert!(!outcome.terminal_seen);
        assert!(!outcome.pending_messages.is_empty());
    }

    #[test]
    fn superseded_turn_exports_while_successor_remains_pending() {
        let messages = vec![
            serde_json::json!({"timestamp":"2026-09-04T02:26:52Z","type":"event_msg","payload":{"type":"task_started","turn_id":"old"}}),
            serde_json::json!({"timestamp":"2026-09-04T02:26:53Z","type":"event_msg","payload":{"type":"user_message","message":"review"}}),
            serde_json::json!({"timestamp":"2026-09-04T02:27:00Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"working"}]}}),
            serde_json::json!({"timestamp":"2026-09-04T02:27:01Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"output_tokens":2}}}}),
            serde_json::json!({"timestamp":"2026-09-04T05:23:46Z","type":"event_msg","payload":{"type":"task_started","turn_id":"next"}}),
            serde_json::json!({"timestamp":"2026-09-04T05:23:47Z","type":"event_msg","payload":{"type":"user_message","message":"continue"}}),
        ];
        let outcome = build_postable_turn_outcome(&[], &messages, &[]);
        assert_eq!(outcome.turns.len(), 1);
        assert_eq!(outcome.turns[0].turn_id, "old");
        assert_eq!(
            outcome.turns[0].terminal_state,
            TurnTerminalState::Superseded
        );
        assert_eq!(outcome.pending_messages, messages[4..]);
        let retry = build_postable_turn_outcome(&[], &messages, &["old".to_string()]);
        assert!(retry.turns.is_empty());
        assert_eq!(retry.pending_messages, messages[4..]);
    }

    #[test]
    fn native_pending_superseded_turn_waits_for_late_usage_before_checkpoint() {
        for completed_prefix in [false, true] {
            let mut pending_messages = vec![
                serde_json::json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"old"}}),
                serde_json::json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"old prompt"}}),
                serde_json::json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"old output"}]}}),
                serde_json::json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"next"}}),
                serde_json::json!({"timestamp":"2026-09-05T00:00:02.010Z","type":"event_msg","payload":{"type":"user_message","message":"next prompt"}}),
            ];
            if completed_prefix {
                pending_messages.insert(2, serde_json::json!({"timestamp":"2026-09-05T00:00:00.500Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"completed output"}]}}));
                pending_messages.insert(3, serde_json::json!({"timestamp":"2026-09-05T00:00:00.600Z","type":"token_usage_record","payload":{"turn_id":"old","response_id":"resp-first","usage":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}}));
            }
            let first = build_postable_turn_outcome_with_initial_input(
                &[],
                &pending_messages,
                &[],
                None,
                true,
            );
            assert!(first.turns.is_empty());
            assert_eq!(first.pending_messages, pending_messages);
            assert!(first
                .diagnostics
                .iter()
                .any(|reason| reason == "unposted_turn_pending:old"));

            let late_usage = vec![
                serde_json::json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"token_usage_record","payload":{"turn_id":"old","response_id":"resp-old-late","usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120}}}),
            ];
            let second = build_postable_turn_outcome_with_initial_input(
                &first.pending_messages,
                &late_usage,
                &[],
                None,
                true,
            );

            assert_eq!(second.turns.len(), 1);
            assert_eq!(second.turns[0].turn_id, "old");
            assert_eq!(
                second.turns[0].assistants[usize::from(completed_prefix)].text,
                "old output"
            );
            assert_eq!(
                second.turns[0].assistants[usize::from(completed_prefix)]
                    .usage
                    .response_id
                    .as_deref(),
                Some("resp-old-late")
            );
            assert_eq!(
                second.pending_messages,
                pending_messages[pending_messages.len() - 2..]
            );
        }
    }

    #[test]
    fn posted_last_turn_does_not_discard_an_earlier_unpostable_turn() {
        let messages = vec![
            serde_json::json!({"timestamp":"2026-09-05T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"unresolved"}}),
            serde_json::json!({"timestamp":"2026-09-05T00:00:02Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"missing input"}]}}),
            serde_json::json!({"timestamp":"2026-09-05T00:00:03Z","type":"event_msg","payload":{"type":"task_started","turn_id":"posted"}}),
            serde_json::json!({"timestamp":"2026-09-05T00:00:04Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}),
            serde_json::json!({"timestamp":"2026-09-05T00:00:05Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"posted","last_agent_message":"done"}}),
        ];
        let outcome = build_postable_turn_outcome(&[], &messages, &["posted".to_string()]);
        assert!(outcome.turns.is_empty());
        assert_eq!(outcome.pending_messages, messages);
        assert!(outcome
            .diagnostics
            .iter()
            .any(|reason| reason == "unposted_turn_pending:unresolved"));
    }

    #[test]
    fn test_build_postable_turns_defers_spawn_turn_until_cross_turn_terminal() {
        let spawn_turn = vec![
            serde_json::json!({"timestamp":"2026-07-16T09:46:30Z","type":"event_msg","payload":{"type":"task_started","turn_id":"parent-spawn"}}),
            serde_json::json!({"timestamp":"2026-07-16T09:46:31Z","type":"event_msg","payload":{"type":"user_message","message":"review"}}),
            serde_json::json!({"timestamp":"2026-07-16T09:46:31.1Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{\"task_name\":\"reviewer\"}","call_id":"call-spawn"}}),
            serde_json::json!({"timestamp":"2026-07-16T09:46:31.2Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-spawn","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"started"}}),
            serde_json::json!({"timestamp":"2026-07-16T09:46:31.3Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-spawn","output":"{\"task_name\":\"/root/reviewer\"}"}}),
            serde_json::json!({"timestamp":"2026-07-16T09:46:32Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"timeout_ms\":1000}","call_id":"wait-spawn"}}),
            serde_json::json!({"timestamp":"2026-07-16T09:46:33Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait-spawn","output":"{\"timed_out\":true}"}}),
            serde_json::json!({"timestamp":"2026-07-16T09:58:35Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"blocked"}]}}),
            serde_json::json!({"timestamp":"2026-07-16T09:58:36Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"parent-spawn","last_agent_message":"blocked"}}),
        ];
        let (turns, pending) = build_postable_turns(&[], &spawn_turn, &[]);
        assert!(turns.is_empty());
        assert_eq!(
            pending
                .first()
                .and_then(|entry| entry.pointer("/payload/turn_id"))
                .and_then(serde_json::Value::as_str),
            Some("parent-spawn")
        );

        let followup_turn = vec![
            serde_json::json!({"timestamp":"2026-07-16T09:58:53Z","type":"event_msg","payload":{"type":"task_started","turn_id":"parent-followup"}}),
            serde_json::json!({"timestamp":"2026-07-16T09:58:53.1Z","type":"event_msg","payload":{"type":"user_message","message":"continue"}}),
            serde_json::json!({"timestamp":"2026-07-16T10:07:44Z","type":"response_item","payload":{"type":"function_call","name":"followup_task","arguments":"{\"target\":\"/root/reviewer\",\"message\":\"recheck\"}","call_id":"call-followup"}}),
            serde_json::json!({"timestamp":"2026-07-16T10:07:44.1Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-followup","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"interacted"}}),
            serde_json::json!({"timestamp":"2026-07-16T10:07:44.2Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-followup","output":"{\"delivered\":true}"}}),
            serde_json::json!({"timestamp":"2026-07-16T10:07:45Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"timeout_ms\":1000}","call_id":"wait-followup"}}),
            serde_json::json!({"timestamp":"2026-07-16T10:07:46Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait-followup","output":"{\"timed_out\":false}"}}),
            serde_json::json!({"timestamp":"2026-07-16T10:07:47Z","type":"response_item","payload":{"type":"agent_message","author":"/root/reviewer","recipient":"/root","content":[{"type":"input_text","text":"Message Type: FINAL_ANSWER\nPayload: approved"}]}}),
            serde_json::json!({"timestamp":"2026-07-16T10:07:47.5Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}]}}),
            serde_json::json!({"timestamp":"2026-07-16T10:07:48Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"parent-followup","last_agent_message":"done"}}),
        ];
        let (turns, pending) = build_postable_turns(&pending, &followup_turn, &[]);
        assert_eq!(
            turns
                .iter()
                .map(|turn| turn.turn_id.as_str())
                .collect::<Vec<_>>(),
            vec!["parent-spawn", "parent-followup"]
        );
        assert!(pending.is_empty());
    }

    #[test]
    fn test_merge_child_sessions_finds_child_across_date_dirs() {
        let dir = tempfile::tempdir().unwrap();
        let sessions_dir = dir.path().join("sessions");
        let parent_dir = sessions_dir.join("2026/06/18");
        let child_dir = sessions_dir.join("2026/06/30");
        std::fs::create_dir_all(&parent_dir).unwrap();
        std::fs::create_dir_all(&child_dir).unwrap();

        let parent_path = parent_dir.join("rollout-parent-1.jsonl");
        let child_path = child_dir.join("rollout-child-1.jsonl");
        std::fs::write(
            &parent_path,
            r#"{"timestamp":"2026-06-30T06:47:00.000Z","type":"session_meta","payload":{"id":"parent-1","thread_source":"user"}}
{"timestamp":"2026-06-30T06:47:01.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"parent-turn"}}
{"timestamp":"2026-06-30T06:47:01.100Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{\"message\":\"run sleep\"}","call_id":"call-spawn"}}
{"timestamp":"2026-06-30T06:47:01.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-spawn","output":"{\"agent_id\":\"child-1\"}"}}
{"timestamp":"2026-06-30T06:47:02.000Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"targets\":[\"child-1\"]}","call_id":"call-wait"}}
{"timestamp":"2026-06-30T06:47:30.000Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-wait","output":"done"}}
{"timestamp":"2026-06-30T06:47:31.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"parent-turn","last_agent_message":"done"}}
"#,
        )
        .unwrap();
        std::fs::write(
            &child_path,
            r#"{"timestamp":"2026-06-30T06:47:11.973Z","type":"session_meta","payload":{"session_id":"parent-1","id":"child-1","parent_thread_id":"parent-1","source":{"subagent":{"thread_spawn":{"parent_thread_id":"parent-1","depth":1,"agent_nickname":"Ramanujan","agent_role":null}}},"thread_source":"subagent","agent_nickname":"Ramanujan"}}
{"timestamp":"2026-06-30T06:47:19.233Z","type":"event_msg","payload":{"type":"task_started","turn_id":"child-turn"}}
{"timestamp":"2026-06-30T06:47:19.233Z","type":"event_msg","payload":{"type":"user_message","message":"run sleep"}}
{"timestamp":"2026-06-30T06:47:29.982Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"done"}}
{"timestamp":"2026-06-30T06:47:30.021Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"child-turn","last_agent_message":"done"}}
"#,
        )
        .unwrap();

        let mut body = serde_json::json!({
            "resourceSpans": [{
                "scopeSpans": [{
                    "spans": [
                        {
                            "traceId": "11111111111111111111111111111111",
                            "spanId": "0000000000000001",
                            "parentSpanId": "",
                            "name": "Turn 1",
                            "startTimeUnixNano": "1782802020000000000",
                            "endTimeUnixNano": "1782802055000000000",
                            "attributes": [],
                            "status": {"code": 1}
                        },
                        {
                            "traceId": "11111111111111111111111111111111",
                            "spanId": "0000000000000002",
                            "parentSpanId": "0000000000000001",
                            "name": "subagent spawn_agent",
                            "startTimeUnixNano": "1782802031617000000",
                            "endTimeUnixNano": "1782802032218000000",
                            "attributes": [
                                {"key": "agentic.tool.type", "value": {"stringValue": "subagent"}},
                                {"key": "agentic.tool.raw_name", "value": {"stringValue": "spawn_agent"}},
                                {"key": "gen_ai.tool.call.id", "value": {"stringValue": "call-spawn"}},
                                {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "{\"message\":\"run sleep\"}"}},
                                {"key": "gen_ai.tool.call.result", "value": {"stringValue": "{\"agent_id\":\"child-1\",\"nickname\":\"Ramanujan\"}"}}
                            ],
                            "status": {"code": 1}
                        },
                        {
                            "traceId": "11111111111111111111111111111111",
                            "spanId": "0000000000000003",
                            "parentSpanId": "0000000000000001",
                            "name": "subagent wait_agent",
                            "startTimeUnixNano": "1782802039624000000",
                            "endTimeUnixNano": "1782802050209000000",
                            "attributes": [
                                {"key": "agentic.tool.type", "value": {"stringValue": "subagent"}},
                                {"key": "agentic.tool.raw_name", "value": {"stringValue": "wait_agent"}},
                                {"key": "gen_ai.tool.call.id", "value": {"stringValue": "call-wait"}},
                                {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "{\"targets\":[\"child-1\"],\"timeout_ms\":15000}"}},
                                {"key": "gen_ai.tool.call.result", "value": {"stringValue": "{\"status\":{\"child-1\":{\"completed\":\"done\"}},\"timed_out\":false}"}}
                            ],
                            "status": {"code": 1}
                        }
                    ]
                }]
            }]
        });

        let logger = crate::common::logger::Logger::new(dir.path().join("test.log"), true);
        let parent_messages: Vec<serde_json::Value> = std::fs::read_to_string(&parent_path)
            .unwrap()
            .lines()
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect();
        let parent_turns = build_turns(&parent_messages);
        let _fixture_polling = child_fixture_polling(None);
        let result = merge_child_sessions_into_body(
            &mut body,
            &parent_turns,
            &parent_turns,
            parent_turns.len() - 1,
            "parent-1",
            &parent_dir,
            parent_path.to_str().unwrap(),
            Some(sessions_dir.to_str().unwrap()),
            "tester",
            "test-version",
            &logger,
            Some(ample_budget()),
        );
        let ChildMergeOutcome::Completed(result) = result else {
            panic!("ample test budget must complete child merge");
        };

        assert_eq!(result.merged_sync_ids, vec!["child-1".to_string()]);
        let spans = all_otel_spans(&body);
        assert!(
            spans
                .iter()
                .any(|span| span.get("name").and_then(|v| v.as_str()) == Some("agent Ramanujan")),
            "cross-date child transcript must be folded into a nickname-based AGENT span"
        );
    }

    #[test]
    fn test_live_wrapper_discovers_native_collab_child_from_real_fixture() {
        let dir = tempfile::tempdir().unwrap();
        let parent_path = dir.path().join("rollout-parent-collab.jsonl");
        let child_path = dir.path().join("rollout-child-collab.jsonl");
        let parent_jsonl =
            include_str!("../../../tests/fixtures/codex_collab_tool_call_same_turn_parent.jsonl");
        std::fs::write(&parent_path, parent_jsonl).unwrap();
        std::fs::write(
            &child_path,
            include_str!("../../../tests/fixtures/codex_collab_tool_call_same_turn_child.jsonl"),
        )
        .unwrap();
        let messages = parent_jsonl
            .lines()
            .map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap())
            .collect::<Vec<_>>();
        let turns = build_turns(&messages);
        let mut body = build_otel_export_request_for_turn(
            &turns[0],
            1,
            "parent-collab",
            "tester",
            "test-version",
            None,
        );
        let logger = crate::common::logger::Logger::new(dir.path().join("native.log"), true);
        let _fixture_polling = child_fixture_polling(None);

        let outcome = merge_child_sessions_into_body(
            &mut body,
            &turns,
            &turns,
            0,
            "parent-collab",
            dir.path(),
            parent_path.to_str().unwrap(),
            None,
            "tester",
            "test-version",
            &logger,
            Some(ample_budget()),
        );
        let ChildMergeOutcome::Completed(result) = outcome else {
            panic!("native fixture merge should complete");
        };

        assert_eq!(result.merged_sync_ids, vec!["child-collab"]);
        assert_eq!(
            result.merged_subagent_invocation_ids,
            vec!["child-collab:collab-spawn-1"]
        );
        assert!(result.deferred_sync_ids.is_empty());
        assert!(all_otel_spans(&body).iter().any(|span| {
            span.get("name").and_then(serde_json::Value::as_str) == Some("agent Archimedes")
        }));

        let child_messages =
            include_str!("../../../tests/fixtures/codex_collab_tool_call_same_turn_child.jsonl")
                .lines()
                .map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap())
                .collect::<Vec<_>>();
        let child_turns = build_turns(&child_messages);
        let (nickname, role, agent_path) = child_turns[0]
            .subagent_source
            .as_ref()
            .map(|source| {
                (
                    source.agent_nickname.clone().unwrap_or_default(),
                    source.agent_role.clone().unwrap_or_default(),
                    source.agent_path.clone(),
                )
            })
            .unwrap();
        let replay_child = crate::common::otel::SubagentChild {
            session_id: "child-collab".to_string(),
            turns: child_turns,
            nickname,
            role,
            agent_path,
        };
        let mut replay_body = build_otel_export_request_for_turn(
            &turns[0],
            1,
            "parent-collab",
            "tester",
            "test-version",
            None,
        );
        crate::common::otel::merge_subagent_sessions_into_otel_across_turns(
            &mut replay_body,
            &turns,
            0,
            &[replay_child],
            "tester",
            "test-version",
        );
        assert_eq!(
            semantic_span_signature(&body),
            semantic_span_signature(&replay_body),
            "live wrapper and replay merger must produce the same native semantic topology"
        );
    }

    #[test]
    fn test_live_wrapper_builds_native_error_shell_without_child_transcript() {
        let dir = tempfile::tempdir().unwrap();
        let parent_path = dir.path().join("parent-error.jsonl");
        std::fs::write(&parent_path, "{}\n").unwrap();
        let messages = vec![
            serde_json::json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
            serde_json::json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"collab"}}),
            serde_json::json!({"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-error","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-error"],"receiver_agents":[{"thread_id":"child-error","agent_nickname":"Hume","agent_role":"debugger"}],"prompt":"diagnose","agents_states":{"child-error":"pending_init"}},"started_at_ms":1000,"completed_at_ms":1100}}),
            serde_json::json!({"timestamp":"2026-08-17T00:00:02Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"wait-error","tool":"wait","sender_thread_id":"parent","receiver_thread_ids":["child-error"],"agents_states":{"child-error":{"error":"boom"}}},"started_at_ms":1500,"completed_at_ms":2000}}),
            serde_json::json!({"timestamp":"2026-08-17T00:00:03Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"failed"}}),
        ];
        let turns = build_turns(&messages);
        let mut body = build_otel_export_request_for_turn(
            &turns[0],
            1,
            "parent",
            "tester",
            "test-version",
            None,
        );
        let logger = crate::common::logger::Logger::new(dir.path().join("error.log"), true);

        let outcome = merge_child_sessions_into_body(
            &mut body,
            &turns,
            &turns,
            0,
            "parent",
            dir.path(),
            parent_path.to_str().unwrap(),
            None,
            "tester",
            "test-version",
            &logger,
            Some(ample_budget()),
        );
        let ChildMergeOutcome::Completed(result) = outcome else {
            panic!("native error shell merge should complete");
        };

        assert_eq!(result.merged_sync_ids, vec!["child-error"]);
        assert_eq!(
            result.merged_subagent_invocation_ids,
            vec!["child-error:spawn-error"]
        );
        assert!(result.deferred_sync_ids.is_empty());
        let agent = all_otel_spans(&body)
            .into_iter()
            .find(|span| span.get("name").and_then(serde_json::Value::as_str) == Some("agent Hume"))
            .expect("error activation must produce an exact-metadata AGENT span");
        assert_eq!(agent["status"]["code"], 2);
    }

    #[test]
    fn test_live_wrapper_exports_ready_native_sibling_while_other_child_pending() {
        let dir = tempfile::tempdir().unwrap();
        let parent_path = dir.path().join("parent-mixed.jsonl");
        std::fs::write(&parent_path, "{}\n").unwrap();
        let messages = vec![
            serde_json::json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
            serde_json::json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"collab"}}),
            serde_json::json!({"timestamp":"2026-08-17T00:00:00.200Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-a","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-a"],"agents_states":{"child-a":"pending_init"}},"started_at_ms":200,"completed_at_ms":300}}),
            serde_json::json!({"timestamp":"2026-08-17T00:00:00.400Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-b","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-b"],"agents_states":{"child-b":"pending_init"}},"started_at_ms":400,"completed_at_ms":500}}),
            serde_json::json!({"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"wait-many","tool":"wait","sender_thread_id":"parent","receiver_thread_ids":["child-a","child-b"],"agents_states":{"child-a":{"error":"failed"},"child-b":{"timed_out":true}}},"started_at_ms":1000,"completed_at_ms":2000}}),
            serde_json::json!({"timestamp":"2026-08-17T00:00:03Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"done"}}),
        ];
        let turns = build_turns(&messages);
        let mut body = build_otel_export_request_for_turn(
            &turns[0],
            1,
            "parent",
            "tester",
            "test-version",
            None,
        );
        let logger = crate::common::logger::Logger::new(dir.path().join("mixed.log"), true);

        let outcome = merge_child_sessions_into_body(
            &mut body,
            &turns,
            &turns,
            0,
            "parent",
            dir.path(),
            parent_path.to_str().unwrap(),
            None,
            "tester",
            "test-version",
            &logger,
            Some(ample_budget()),
        );
        let ChildMergeOutcome::Completed(result) = outcome else {
            panic!("semantic deferral must not exhaust the budget");
        };

        assert_eq!(result.deferred_sync_ids, vec!["child-b"]);
        assert!(result
            .correlation_reasons
            .iter()
            .any(|(id, reason)| id == "child-b" && reason == "native_terminal_pending"));
        assert_eq!(result.merged_sync_ids, vec!["child-a"]);
        assert!(all_otel_spans(&body).iter().any(|span| span
            .get("status")
            .and_then(|status| status.get("code"))
            .and_then(serde_json::Value::as_i64)
            == Some(2)));
    }

    #[test]
    fn test_live_child_merge_recovers_prior_spawn_for_followup_turn() {
        let dir = tempfile::tempdir().unwrap();
        let parent_path = dir.path().join("parent.jsonl");
        let child_path = dir.path().join("child.jsonl");
        let parent_jsonl = r#"{"timestamp":"2026-07-16T09:00:00Z","type":"session_meta","payload":{"id":"parent","thread_source":"user"}}
{"timestamp":"2026-07-16T09:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"spawn-turn"}}
{"timestamp":"2026-07-16T09:00:01.1Z","type":"event_msg","payload":{"type":"user_message","message":"review"}}
{"timestamp":"2026-07-16T09:00:02Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{\"task_name\":\"reviewer\",\"message\":\"review\"}","call_id":"call-spawn"}}
{"timestamp":"2026-07-16T09:00:02.1Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-spawn","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"started"}}
{"timestamp":"2026-07-16T09:00:02.2Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-spawn","output":"{\"task_name\":\"/root/reviewer\"}"}}
{"timestamp":"2026-07-16T09:00:03Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"timeout_ms\":1000}","call_id":"wait-spawn"}}
{"timestamp":"2026-07-16T09:00:04Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait-spawn","output":"{\"timed_out\":false}"}}
{"timestamp":"2026-07-16T09:00:05Z","type":"response_item","payload":{"type":"agent_message","author":"/root/reviewer","recipient":"/root","content":[{"type":"input_text","text":"Message Type: FINAL_ANSWER\nPayload: initial"}]}}
{"timestamp":"2026-07-16T09:00:06Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"spawn-turn","last_agent_message":"initial"}}
{"timestamp":"2026-07-16T10:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"followup-turn"}}
{"timestamp":"2026-07-16T10:00:00.1Z","type":"event_msg","payload":{"type":"user_message","message":"recheck"}}
{"timestamp":"2026-07-16T10:00:01Z","type":"response_item","payload":{"type":"function_call","name":"followup_task","arguments":"{\"target\":\"/root/reviewer\",\"message\":\"recheck\"}","call_id":"call-followup"}}
{"timestamp":"2026-07-16T10:00:01.1Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-followup","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"interacted"}}
{"timestamp":"2026-07-16T10:00:01.2Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-followup","output":"{\"delivered\":true}"}}
{"timestamp":"2026-07-16T10:00:02Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"timeout_ms\":1000}","call_id":"wait-followup"}}
{"timestamp":"2026-07-16T10:00:03Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait-followup","output":"{\"timed_out\":false}"}}
{"timestamp":"2026-07-16T10:00:04Z","type":"response_item","payload":{"type":"agent_message","author":"/root/reviewer","recipient":"/root","content":[{"type":"input_text","text":"Message Type: FINAL_ANSWER\nPayload: approved"}]}}
{"timestamp":"2026-07-16T10:00:05Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}]}}
{"timestamp":"2026-07-16T10:00:06Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"followup-turn","last_agent_message":"done"}}
"#;
        std::fs::write(
            &parent_path,
            format!("{parent_jsonl}{{\"timestamp\":\"2026-07-16T10:00:07Z\""),
        )
        .unwrap();
        let child_jsonl = r#"{"timestamp":"2026-07-16T09:00:02Z","type":"session_meta","payload":{"id":"child-reviewer","parent_thread_id":"parent","source":{"subagent":{"thread_spawn":{"parent_thread_id":"parent","depth":1,"agent_path":"/root/reviewer","agent_nickname":"Reviewer"}}},"multi_agent_version":"v2"}}
{"timestamp":"2026-07-16T09:00:02.3Z","type":"event_msg","payload":{"type":"task_started","turn_id":"child-initial"}}
{"timestamp":"2026-07-16T09:00:02.4Z","type":"event_msg","payload":{"type":"user_message","message":"review"}}
{"timestamp":"2026-07-16T09:00:04.8Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"initial"}}
{"timestamp":"2026-07-16T09:00:05Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"child-initial","last_agent_message":"initial"}}
{"timestamp":"2026-07-16T10:00:01.1Z","type":"event_msg","payload":{"type":"task_started","turn_id":"child-followup"}}
{"timestamp":"2026-07-16T10:00:01.2Z","type":"event_msg","payload":{"type":"user_message","message":"recheck"}}
{"timestamp":"2026-07-16T10:00:03.8Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"approved"}}
{"timestamp":"2026-07-16T10:00:04Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"child-followup","last_agent_message":"approved"}}
"#;
        std::fs::write(&child_path, child_jsonl).unwrap();

        let all_parent_messages: Vec<serde_json::Value> = parent_jsonl
            .lines()
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect();
        let all_parent_turns = build_turns(&all_parent_messages);
        let current_turn = all_parent_turns.last().unwrap().clone();
        let live_correlation_turns = load_parent_correlation_turns(
            parent_path.to_str().unwrap(),
            std::slice::from_ref(&current_turn),
        );
        assert_eq!(
            live_correlation_turns.len(),
            2,
            "complete parent prefix must survive an incomplete trailing JSONL line"
        );

        let child_messages: Vec<serde_json::Value> = child_jsonl
            .lines()
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect();
        let child_turns = build_turns(&child_messages);
        let child_source = child_turns
            .first()
            .and_then(|turn| turn.subagent_source.as_ref())
            .unwrap();
        let child_nickname = child_source.agent_nickname.clone().unwrap_or_default();
        let child_role = child_source.agent_role.clone().unwrap_or_default();
        let child_agent_path = child_source.agent_path.clone();
        let replay_child = crate::common::otel::SubagentChild {
            session_id: "child-reviewer".to_string(),
            turns: child_turns,
            nickname: child_nickname,
            role: child_role,
            agent_path: child_agent_path,
        };
        let mut replay_body = build_otel_export_request_for_turn(
            &current_turn,
            2,
            "parent",
            "tester",
            "test-version",
            None,
        );
        crate::common::otel::merge_subagent_sessions_into_otel_across_turns(
            &mut replay_body,
            &all_parent_turns,
            all_parent_turns.len() - 1,
            &[replay_child],
            "tester",
            "test-version",
        );

        let mut body = build_otel_export_request_for_turn(
            &current_turn,
            2,
            "parent",
            "tester",
            "test-version",
            None,
        );
        let logger = crate::common::logger::Logger::new(dir.path().join("followup.log"), true);
        let _fixture_polling = child_fixture_polling(None);
        let result = merge_child_sessions_into_body(
            &mut body,
            std::slice::from_ref(&current_turn),
            &live_correlation_turns,
            0,
            "parent",
            dir.path(),
            parent_path.to_str().unwrap(),
            None,
            "tester",
            "test-version",
            &logger,
            Some(ample_budget()),
        );
        let ChildMergeOutcome::Completed(result) = result else {
            panic!("followup merge should complete");
        };
        assert_eq!(result.merged_sync_ids, vec!["child-reviewer"]);

        let spans = all_otel_spans(&body);
        let followup_agent = spans
            .iter()
            .find(|span| {
                span.get("name").and_then(serde_json::Value::as_str) == Some("agent Reviewer")
                    && span["attributes"].as_array().unwrap().iter().any(|attr| {
                        attr["key"] == "agentic.subagent.parent_tool_call_id"
                            && attr["value"]["stringValue"] == "call-followup"
                    })
            })
            .expect("current followup turn must receive its own nickname-based AGENT span");
        assert!(spans
            .iter()
            .any(|span| span["parentSpanId"] == followup_agent["spanId"]));
        assert_eq!(
            semantic_span_signature(&body),
            semantic_span_signature(&replay_body),
            "live incremental parsing and replay full-transcript parsing must produce the same semantic span topology",
        );
    }

    #[test]
    fn test_parent_correlation_ignores_trailing_pending_turn() {
        let _fixture_polling = child_fixture_polling(None);
        let dir = tempfile::tempdir().unwrap();
        let parent_path = dir.path().join("parent.jsonl");
        let child_path = dir.path().join("child.jsonl");
        let completed_parent_jsonl = r#"{"timestamp":"2026-07-16T09:00:00Z","type":"session_meta","payload":{"id":"parent","thread_source":"user"}}
{"timestamp":"2026-07-16T09:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"spawn-turn"}}
{"timestamp":"2026-07-16T09:00:01.1Z","type":"event_msg","payload":{"type":"user_message","message":"review"}}
{"timestamp":"2026-07-16T09:00:02Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","arguments":"{\"task_name\":\"reviewer\",\"message\":\"review\"}","call_id":"call-spawn"}}
{"timestamp":"2026-07-16T09:00:02.1Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-spawn","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"started"}}
{"timestamp":"2026-07-16T09:00:02.2Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-spawn","output":"{\"task_name\":\"/root/reviewer\"}"}}
{"timestamp":"2026-07-16T09:00:03Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"timeout_ms\":1000}","call_id":"wait-spawn"}}
{"timestamp":"2026-07-16T09:00:04Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait-spawn","output":"{\"timed_out\":false}"}}
{"timestamp":"2026-07-16T09:00:05Z","type":"response_item","payload":{"type":"agent_message","author":"/root/reviewer","recipient":"/root","content":[{"type":"input_text","text":"Message Type: FINAL_ANSWER\nPayload: initial"}]}}
{"timestamp":"2026-07-16T09:00:06Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"spawn-turn","last_agent_message":"initial"}}
{"timestamp":"2026-07-16T10:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"followup-turn"}}
{"timestamp":"2026-07-16T10:00:00.1Z","type":"event_msg","payload":{"type":"user_message","message":"recheck"}}
{"timestamp":"2026-07-16T10:00:01Z","type":"response_item","payload":{"type":"function_call","name":"followup_task","arguments":"{\"target\":\"/root/reviewer\",\"message\":\"recheck\"}","call_id":"call-followup"}}
{"timestamp":"2026-07-16T10:00:01.1Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-followup","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"interacted"}}
{"timestamp":"2026-07-16T10:00:01.2Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-followup","output":"{\"delivered\":true}"}}
{"timestamp":"2026-07-16T10:00:02Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","arguments":"{\"timeout_ms\":1000}","call_id":"wait-followup"}}
{"timestamp":"2026-07-16T10:00:03Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait-followup","output":"{\"timed_out\":false}"}}
{"timestamp":"2026-07-16T10:00:04Z","type":"response_item","payload":{"type":"agent_message","author":"/root/reviewer","recipient":"/root","content":[{"type":"input_text","text":"Message Type: FINAL_ANSWER\nPayload: approved"}]}}
{"timestamp":"2026-07-16T10:00:05Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}]}}
{"timestamp":"2026-07-16T10:00:06Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"followup-turn","last_agent_message":"done"}}
"#;
        let pending_parent_jsonl = r#"{"timestamp":"2026-07-16T11:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"pending-turn"}}
{"timestamp":"2026-07-16T11:00:00.1Z","type":"event_msg","payload":{"type":"user_message","message":"another recheck"}}
{"timestamp":"2026-07-16T11:00:01Z","type":"response_item","payload":{"type":"function_call","name":"followup_task","arguments":"{\"target\":\"/root/reviewer\",\"message\":\"again\"}","call_id":"call-pending-followup"}}
{"timestamp":"2026-07-16T11:00:01.1Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-pending-followup","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"interacted"}}
{"timestamp":"2026-07-16T11:00:01.2Z","type":"response_item","payload":{"type":"function_call_output","call_id":"call-pending-followup","output":"{\"delivered\":true}"}}
"#;
        std::fs::write(
            &parent_path,
            format!("{completed_parent_jsonl}{pending_parent_jsonl}"),
        )
        .unwrap();
        let child_jsonl = r#"{"timestamp":"2026-07-16T09:00:02Z","type":"session_meta","payload":{"id":"child-reviewer","parent_thread_id":"parent","source":{"subagent":{"thread_spawn":{"parent_thread_id":"parent","depth":1,"agent_path":"/root/reviewer","agent_nickname":"Reviewer"}}},"multi_agent_version":"v2"}}
{"timestamp":"2026-07-16T09:00:02.3Z","type":"event_msg","payload":{"type":"task_started","turn_id":"child-initial"}}
{"timestamp":"2026-07-16T09:00:02.4Z","type":"event_msg","payload":{"type":"user_message","message":"review"}}
{"timestamp":"2026-07-16T09:00:04.8Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"initial"}}
{"timestamp":"2026-07-16T09:00:05Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"child-initial","last_agent_message":"initial"}}
{"timestamp":"2026-07-16T10:00:01.1Z","type":"event_msg","payload":{"type":"task_started","turn_id":"child-followup"}}
{"timestamp":"2026-07-16T10:00:01.2Z","type":"event_msg","payload":{"type":"user_message","message":"recheck"}}
{"timestamp":"2026-07-16T10:00:03.8Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"approved"}}
{"timestamp":"2026-07-16T10:00:04Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"child-followup","last_agent_message":"approved"}}
"#;
        std::fs::write(&child_path, child_jsonl).unwrap();

        let completed_parent_messages: Vec<serde_json::Value> = completed_parent_jsonl
            .lines()
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect();
        let completed_parent_turns = build_turns(&completed_parent_messages);
        let current_turn = completed_parent_turns.last().unwrap().clone();
        let live_correlation_turns = load_parent_correlation_turns(
            parent_path.to_str().unwrap(),
            std::slice::from_ref(&current_turn),
        );

        assert_eq!(
            live_correlation_turns
                .iter()
                .map(|turn| turn.turn_id.as_str())
                .collect::<Vec<_>>(),
            vec!["spawn-turn", "followup-turn"],
            "pending parent turn must not enter the parent correlation snapshot"
        );

        let mut body = build_otel_export_request_for_turn(
            &current_turn,
            2,
            "parent",
            "tester",
            "test-version",
            None,
        );
        let logger = crate::common::logger::Logger::new(dir.path().join("pending.log"), true);
        let result = merge_child_sessions_into_body(
            &mut body,
            std::slice::from_ref(&current_turn),
            &live_correlation_turns,
            0,
            "parent",
            dir.path(),
            parent_path.to_str().unwrap(),
            None,
            "tester",
            "test-version",
            &logger,
            Some(ample_budget()),
        );
        let ChildMergeOutcome::Completed(result) = result else {
            panic!("pending parent turn must not exhaust merge budget");
        };
        assert_eq!(result.deferred_sync_ids, Vec::<String>::new());
        assert_eq!(result.merged_sync_ids, vec!["child-reviewer"]);

        let spans = all_otel_spans(&body);
        assert!(spans.iter().any(|span| {
            span.get("name").and_then(serde_json::Value::as_str) == Some("agent Reviewer")
                && span["attributes"].as_array().unwrap().iter().any(|attr| {
                    attr["key"] == "agentic.subagent.parent_tool_call_id"
                        && attr["value"]["stringValue"] == "call-followup"
                })
        }));
        assert!(!spans.iter().any(|span| {
            span["attributes"].as_array().unwrap().iter().any(|attr| {
                attr["key"] == "agentic.subagent.parent_tool_call_id"
                    && attr["value"]["stringValue"] == "call-pending-followup"
            })
        }));
    }

    #[test]
    fn test_followup_without_spawn_snapshot_is_deferred_instead_of_silently_dropped() {
        let dir = tempfile::tempdir().unwrap();
        let parent_path = dir.path().join("parent.jsonl");
        std::fs::write(&parent_path, "{}\n").unwrap();
        let messages = vec![
            serde_json::json!({"timestamp":"2026-07-16T10:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"followup-turn"}}),
            serde_json::json!({"timestamp":"2026-07-16T10:00:01Z","type":"response_item","payload":{"type":"function_call","name":"followup_task","arguments":"{\"target\":\"/root/reviewer\",\"message\":\"recheck\"}","call_id":"call-followup"}}),
            serde_json::json!({"timestamp":"2026-07-16T10:00:01.1Z","type":"event_msg","payload":{"type":"sub_agent_activity","event_id":"call-followup","agent_thread_id":"child-reviewer","agent_path":"/root/reviewer","kind":"interacted"}}),
            serde_json::json!({"timestamp":"2026-07-16T10:00:02Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"followup-turn","last_agent_message":"done"}}),
        ];
        let turn = build_turns(&messages).remove(0);
        let mut body =
            build_otel_export_request_for_turn(&turn, 1, "parent", "tester", "test-version", None);
        let logger = crate::common::logger::Logger::new(dir.path().join("missing.log"), true);

        let outcome = merge_child_sessions_into_body(
            &mut body,
            std::slice::from_ref(&turn),
            std::slice::from_ref(&turn),
            0,
            "parent",
            dir.path(),
            parent_path.to_str().unwrap(),
            None,
            "tester",
            "test-version",
            &logger,
            Some(ample_budget()),
        );
        let ChildMergeOutcome::Completed(result) = outcome else {
            panic!("missing parent snapshot should be retryable, not budget-exhausted");
        };

        assert_eq!(result.deferred_sync_ids, vec!["child-reviewer"]);
        assert_eq!(
            result.correlation_reasons,
            vec![(
                "child-reviewer".to_string(),
                "parent_spawn_anchor_not_ready".to_string()
            )]
        );
    }

    #[test]
    fn test_find_session_transcript_finds_root_parent_across_date_dirs() {
        let dir = tempfile::tempdir().unwrap();
        let sessions_dir = dir.path().join("sessions");
        let child_dir = sessions_dir.join("2026/07/10");
        let parent_dir = sessions_dir.join("2026/07/09");
        std::fs::create_dir_all(&child_dir).unwrap();
        std::fs::create_dir_all(&parent_dir).unwrap();
        let child_path = child_dir.join("child.jsonl");
        let parent_path = parent_dir.join("parent.jsonl");
        std::fs::write(
            &child_path,
            r#"{"type":"session_meta","payload":{"id":"child","parent_thread_id":"parent"}}
"#,
        )
        .unwrap();
        std::fs::write(
            &parent_path,
            r#"{"type":"session_meta","payload":{"id":"parent","thread_source":"user"}}
"#,
        )
        .unwrap();

        let found =
            find_session_transcript(&child_dir, Some(sessions_dir.to_str().unwrap()), "parent");
        assert_eq!(found.as_deref(), Some(parent_path.as_path()));
    }

    #[test]
    fn test_merge_retry_rescans_configured_sessions_root_for_late_child() {
        let dir = tempfile::tempdir().unwrap();
        let sessions_dir = dir.path().join("sessions");
        let parent_dir = sessions_dir.join("2026/07/09");
        let child_dir = sessions_dir.join("2026/07/10");
        std::fs::create_dir_all(&parent_dir).unwrap();
        let parent_path = parent_dir.join("parent.jsonl");
        std::fs::write(&parent_path, "{}\n").unwrap();

        let parent_messages = vec![
            serde_json::json!({"timestamp":"2026-07-10T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"parent-turn"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:00.100Z","type":"response_item","payload":{"type":"function_call","name":"spawn_agent","call_id":"spawn","arguments":"{\"message\":\"work\"}"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:00.200Z","type":"response_item","payload":{"type":"function_call_output","call_id":"spawn","output":"{\"agent_id\":\"late-child\"}"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:00.300Z","type":"response_item","payload":{"type":"function_call","name":"wait_agent","call_id":"wait","arguments":"{\"targets\":[\"late-child\"]}"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:00.400Z","type":"response_item","payload":{"type":"function_call_output","call_id":"wait","output":"{\"timed_out\":false}"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:00.500Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"parent-turn","last_agent_message":"done"}}),
        ];
        let parent_turn = build_turns(&parent_messages).remove(0);
        let _fixture_polling = child_fixture_polling(Some(Box::new(move || {
            std::fs::create_dir_all(&child_dir).unwrap();
            std::fs::write(
                child_dir.join("child.jsonl"),
                r#"{"timestamp":"2026-07-10T00:00:00Z","type":"session_meta","payload":{"id":"late-child","parent_thread_id":"parent","source":{"subagent":{"thread_spawn":{"parent_thread_id":"parent"}}}}}
{"timestamp":"2026-07-10T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"child-turn"}}
{"timestamp":"2026-07-10T00:00:02Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"done"}}
{"timestamp":"2026-07-10T00:00:03Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"child-turn","last_agent_message":"done"}}
"#,
            )
            .unwrap();
        })));
        let mut body = build_otel_export_request_for_turn(
            &parent_turn,
            1,
            "parent",
            "tester",
            "test-version",
            None,
        );
        let logger = crate::common::logger::Logger::new(dir.path().join("retry.log"), true);

        let result = merge_child_sessions_into_body(
            &mut body,
            std::slice::from_ref(&parent_turn),
            std::slice::from_ref(&parent_turn),
            0,
            "parent",
            &parent_dir,
            parent_path.to_str().unwrap(),
            Some(sessions_dir.to_str().unwrap()),
            "tester",
            "test-version",
            &logger,
            Some(ample_budget()),
        );
        let ChildMergeOutcome::Completed(result) = result else {
            panic!("ample test budget must complete child merge");
        };
        let log = std::fs::read_to_string(dir.path().join("retry.log")).unwrap();
        let (_, retries) = log
            .split_once("Child transcript discovery attempt=0: requested=1 found=0")
            .expect("the first scan must miss the late child fixture");
        assert!(retries.lines().any(|line| {
            line.contains("Child transcript discovery attempt=")
                && line.contains("requested=1 found=1 exact=0")
        }));

        assert_eq!(result.merged_sync_ids, vec!["late-child"]);
        assert!(result.deferred_sync_ids.is_empty());
    }

    #[test]
    fn test_single_turn_builder_preserves_trace_content() {
        let dir = tempfile::tempdir().unwrap();
        let parent_path = dir.path().join("parent.jsonl");
        std::fs::write(&parent_path, "{}\n").unwrap();
        let messages = vec![
            serde_json::json!({"timestamp":"2026-07-10T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"first"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:00.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"one"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:00.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"one"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-2"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"second"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"two"}}),
            serde_json::json!({"timestamp":"2026-07-10T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-2","last_agent_message":"two"}}),
        ];
        let turns = build_turns(&messages);
        assert_eq!(turns.len(), 2);
        let bodies: Vec<serde_json::Value> = turns
            .iter()
            .enumerate()
            .map(|(index, turn)| {
                build_otel_export_request_for_turn(
                    turn,
                    index + 1,
                    "parent",
                    "tester",
                    "test-version",
                    None,
                )
            })
            .collect();

        assert_eq!(bodies.len(), 2);
        for (index, body) in bodies.iter().enumerate() {
            let resource_spans = body["resourceSpans"].as_array().unwrap();
            assert_eq!(resource_spans.len(), 1, "one POST body contains one turn");
            let root_name = resource_spans[0]["scopeSpans"][0]["spans"]
                .as_array()
                .unwrap()
                .iter()
                .find_map(|span| {
                    let name = span.get("name")?.as_str()?;
                    name.starts_with("Turn ").then_some(name)
                })
                .unwrap();
            assert_eq!(root_name, format!("Turn {}", index + 1));
        }
    }
}
