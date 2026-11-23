use agentic_otel_common::otlp::OtelAttribute;
use serde_json::Value;

use crate::common::hook::{extract_session_id, extract_transcript_path, HookEvent};
use crate::common::logger::now_iso8601_pub;
use crate::common::otel::{
    apply_async_subagent_semantics, build_otel_export_request_for_turn, inject_vcs_resource_attrs,
    post_otel_to_langfuse, stabilize_turn_export_identities,
};
use crate::common::transcript::{
    build_turns, inspect_transcript_source, read_bounded_jsonl_records, read_new_jsonl,
    stable_turn_id, tool_result_marks_async, BoundedJsonlScan, Turn,
};
use crate::hooks::langfuse::state::{
    get_session_state, get_vcs_for_cwd_from_session, SessionStateStore, StateTxnError, VcsInfo,
};
use crate::hooks::timing::{
    cleanup_subagent_timing_from_session, companion_timing_path,
    load_subagent_timing_for_agent_from_session, TimingData,
};
use crate::hooks::{CommonContext, HandlerError, HookHandler};

use super::subagent_state::{
    begin_parent_turn, mark_agent_processed, prune_terminal_agents_after_stop, read_subagent_meta,
    record_agent_export_progress, register_agent_stop, registry_from_session, registry_to_session,
    set_agent_background, SubagentRecord, SubagentRegistry,
};

const HOOK_VERSION: &str = env!("HOOK_VERSION");
const ASYNC_SUBAGENT_MAX_BODY_BYTES: usize = 1024 * 1024;
const FINALIZER_MAX_ATTEMPTS: usize = 3;
const FINALIZER_RETRY_BACKOFF_MS: u64 = 50;
const PARENT_DISCOVERY_MAX_BYTES: usize = 4 * 1024 * 1024;
const PARENT_DISCOVERY_BUDGET: std::time::Duration = std::time::Duration::from_millis(100);

/// Handler for subagent lifecycle events.
///
/// - At SubagentStop: records agent completion to registry. If parent already stopped
///   and agent is background, builds Turn(AsyncSubAgent) immediately.
/// - At parent Stop: scans parent transcript for Agent tool_uses, records is_background
///   in registry. Processes any already-arrived background SubagentStop events.
pub struct SubagentHandler;

struct AsyncExportSnapshot {
    record: SubagentRecord,
    timing: TimingData,
    vcs_info: Option<VcsInfo>,
}

struct AgentExportProgress {
    agent_id: String,
    accepted_turn_ids: Vec<String>,
    complete: bool,
}

struct AsyncExportAttempt {
    progress: AgentExportProgress,
    error: Option<HandlerError>,
}

struct AsyncExportFragment {
    body: Value,
    completed_turn_id: Option<String>,
}

struct AsyncExportChunk {
    body: Value,
    completed_turn_ids: Vec<String>,
}

impl Default for SubagentHandler {
    fn default() -> Self {
        Self::new()
    }
}

impl SubagentHandler {
    pub fn new() -> Self {
        Self
    }

    fn handle_user_prompt_submit(
        &self,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        let session_id = extract_session_id(payload);
        if session_id.is_empty() {
            return Ok(());
        }

        let store = SessionStateStore::new(context.state_file.clone(), &session_id, 500);
        let (removed, archive_snapshot) = match store.update_session(|session| {
            let mut registry = registry_from_session(session);
            let removed = begin_parent_turn(&mut registry);
            let archive_snapshot = context.debug_enabled.then(|| session.clone());
            for record in &removed {
                cleanup_subagent_timing_from_session(session, &record.agent_id, None);
            }
            registry_to_session(session, &registry);
            Ok((removed, archive_snapshot))
        }) {
            Ok(result) => result,
            Err(StateTxnError::Busy { .. }) => {
                context
                    .logger
                    .warn("[subagent] State lock unavailable; skipping parent turn reset");
                return Ok(());
            }
            Err(error) => {
                return Err(HandlerError {
                    message: format!("subagent: failed to reset parent turn: {error}"),
                });
            }
        };

        self.archive_agent_timings(archive_snapshot, &removed, context);
        context.logger.debug(&format!(
            "[subagent] Parent turn started; removed_processed={}",
            removed.len()
        ));
        Ok(())
    }

    /// Handle SubagentStop: record agent, check if background, optionally build Turn.
    fn handle_subagent_stop(
        &self,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        let session_id = extract_session_id(payload);
        if session_id.is_empty() {
            return Ok(());
        }

        let agent_id = payload
            .get("agent_id")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let agent_type = payload
            .get("agent_type")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let transcript_path = payload
            .get("agent_transcript_path")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let last_msg = payload
            .get("last_assistant_message")
            .and_then(|v| v.as_str())
            .unwrap_or("");

        if agent_id.is_empty() {
            return Ok(());
        }

        let transcript_path = agentic_otel_common::hook::expand_home_path(transcript_path);
        let (meta_type, description) = read_subagent_meta(&transcript_path);
        let agent_type = if agent_type.is_empty() {
            &meta_type
        } else {
            agent_type
        };
        let cwd = payload.get("cwd").and_then(Value::as_str).unwrap_or("");

        let store = SessionStateStore::new(context.state_file.clone(), &session_id, 500);
        let now = now_iso8601_pub();
        let upload = match store.update_session(|session| {
            let mut registry = registry_from_session(session);
            let is_new = register_agent_stop(
                &mut registry,
                agent_id,
                agent_type,
                &transcript_path,
                &now,
                last_msg,
                &description,
            );

            if !is_new {
                if let Some(record) = registry.agents.get_mut(agent_id) {
                    if record.stopped_at.is_empty() {
                        record.stopped_at = now.clone();
                        record.transcript_path = transcript_path.to_string();
                        record.last_assistant_message = last_msg.to_string();
                        if record.description.is_empty() {
                            record.description = description.clone();
                        }
                        context.logger.info(&format!(
                            "[subagent] SubagentStop: updated pre-registered agent_id={} parent_stopped={}",
                            agent_id, registry.parent_stopped
                        ));
                    } else {
                        context.logger.debug(&format!(
                            "[subagent] SubagentStop: agent {} already processed, skipping",
                            agent_id
                        ));
                        registry_to_session(session, &registry);
                        return Ok(None);
                    }
                }
            } else {
                context.logger.info(&format!(
                    "[subagent] SubagentStop: registered agent_id={} type={} parent_stopped={}",
                    agent_id, agent_type, registry.parent_stopped
                ));
            }

            let upload_record = if registry.parent_stopped {
                let record = registry.agents.get(agent_id).cloned();
                let is_bg = record
                    .as_ref()
                    .and_then(|r| r.is_background)
                    .unwrap_or(false);

                if is_bg {
                    context.logger.info(&format!(
                        "[subagent] Background agent {} -> building Turn(AsyncSubAgent)",
                        agent_id
                    ));
                    record
                } else {
                    context.logger.info(&format!(
                        "[subagent] Sync agent {} -> deferring to next parent Stop",
                        agent_id
                    ));
                    None
                }
            } else {
                None
            };

            let upload = upload_record.map(|record| AsyncExportSnapshot {
                timing: load_subagent_timing_for_agent_from_session(session, &record.agent_id),
                vcs_info: get_vcs_for_cwd_from_session(session, cwd),
                record,
            });

            registry_to_session(session, &registry);
            Ok(upload)
        }) {
            Ok(upload) => upload,
            Err(StateTxnError::Busy { .. }) => {
                context
                    .logger
                    .warn("[subagent] State lock unavailable; skipping registry update");
                return Ok(());
            }
            Err(error) => {
                return Err(HandlerError {
                    message: format!("subagent: failed to update registry: {error}"),
                });
            }
        };

        if let Some(upload) = upload {
            let attempt = self.build_and_upload_async_turn(&session_id, &upload, context);
            self.finalize_agent_export(&session_id, &attempt.progress, context)?;
            if let Some(error) = attempt.error {
                return Err(error);
            }
        }
        Ok(())
    }

    /// Handle parent Stop: scan transcript for Agent tool_uses, write is_background to registry.
    /// Then process any already-arrived background SubagentStop events.
    fn handle_parent_stop(
        &self,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        if crate::hooks::active_stop_should_skip(payload) {
            return Ok(());
        }

        let session_id = extract_session_id(payload);
        if session_id.is_empty() {
            return Ok(());
        }

        let transcript_path = extract_transcript_path(payload);
        let store = SessionStateStore::new(context.state_file.clone(), &session_id, 500);
        let mut discovery_registry = store
            .read(|state| {
                get_session_state(state, &session_id)
                    .map(registry_from_session)
                    .unwrap_or_default()
            })
            .unwrap_or_default();
        let discovered_agents = if transcript_path.is_empty() {
            Vec::new()
        } else {
            let (agent_bg_map, _total_lines, _assistant_count, _agent_tu_count) =
                scan_transcript_for_agent_background(&transcript_path, &mut discovery_registry);
            if !agent_bg_map.is_empty() {
                context.logger.info(&format!(
                    "[subagent] Parent Stop: found {} Agent calls ({} background)",
                    agent_bg_map.len(),
                    agent_bg_map.values().filter(|&&v| v).count()
                ));
            }
            let subagent_dir = transcript_path.replace(".jsonl", "");
            agent_bg_map
                .into_iter()
                .map(|(agent_id, is_background)| {
                    let subagent_transcript =
                        format!("{subagent_dir}/subagents/agent-{agent_id}.jsonl");
                    let (agent_type, description) = read_subagent_meta(&subagent_transcript);
                    (
                        agent_id,
                        is_background,
                        subagent_transcript,
                        agent_type,
                        description,
                    )
                })
                .collect()
        };

        let cwd = payload.get("cwd").and_then(Value::as_str).unwrap_or("");
        let (bg_exports, cleanup_candidates) = match store.update_session(|session| {
            let mut registry = registry_from_session(session);
            registry.parent_discovery_offset = discovery_registry.parent_discovery_offset;
            registry.parent_observed_file_len = discovery_registry.parent_observed_file_len;
            registry.parent_source_epoch = discovery_registry.parent_source_epoch.clone();
            registry.pending_agent_tool_uses = discovery_registry.pending_agent_tool_uses.clone();
            registry.parent_scan_terminal_reason =
                discovery_registry.parent_scan_terminal_reason.clone();

            for (agent_id, is_background, subagent_transcript, agent_type, description) in
                &discovered_agents
            {
                set_agent_background(&mut registry, agent_id, *is_background);
                if !registry.agents.contains_key(agent_id.as_str()) {
                    register_agent_stop(
                        &mut registry,
                        agent_id,
                        agent_type,
                        subagent_transcript,
                        "",
                        "",
                        description,
                    );
                    set_agent_background(&mut registry, agent_id, *is_background);
                }
            }

            let sync_done: Vec<SubagentRecord> = registry
                .agents
                .values()
                .filter(|r| {
                    !r.processed && r.is_background == Some(false) && !r.stopped_at.is_empty()
                })
                .cloned()
                .collect();
            for record in &sync_done {
                mark_agent_processed(&mut registry, &record.agent_id);
            }

            registry.parent_stopped = true;
            registry.parent_stop_time = Some(now_iso8601_pub());

            let bg_exports: Vec<AsyncExportSnapshot> = registry
                .agents
                .values()
                .filter(|r| {
                    !r.processed && r.is_background == Some(true) && !r.stopped_at.is_empty()
                })
                .cloned()
                .map(|record| AsyncExportSnapshot {
                    timing: load_subagent_timing_for_agent_from_session(session, &record.agent_id),
                    vcs_info: get_vcs_for_cwd_from_session(session, cwd),
                    record,
                })
                .collect();
            // Only records that were already terminal in this snapshot may be
            // pruned by the later finalizer. An active agent can stop while the
            // HTTP exports run outside the lock; including it here would let the
            // finalizer delete that newly-arrived stop before it is classified.
            let cleanup_candidates: Vec<String> = registry
                .agents
                .values()
                .filter(|record| !record.stopped_at.is_empty())
                .map(|record| record.agent_id.clone())
                .collect();

            registry_to_session(session, &registry);
            Ok((bg_exports, cleanup_candidates))
        }) {
            Ok(records) => records,
            Err(StateTxnError::Busy { .. }) => {
                context
                    .logger
                    .warn("[subagent] State lock unavailable; skipping registry update");
                return Ok(());
            }
            Err(error) => {
                return Err(HandlerError {
                    message: format!("subagent: failed to update registry: {error}"),
                });
            }
        };

        let mut export_progress = Vec::new();
        if !bg_exports.is_empty() {
            context.logger.info(&format!(
                "[subagent] Parent Stop: {} background agents already arrived, uploading",
                bg_exports.len()
            ));
            for upload in &bg_exports {
                let attempt = self.build_and_upload_async_turn(&session_id, upload, context);
                if let Some(error) = attempt.error.as_ref() {
                    context.logger.error(&format!(
                        "[subagent] Failed to upload {} : {}",
                        upload.record.agent_id, error
                    ));
                } else {
                    context.logger.info(&format!(
                        "[subagent] Uploaded Turn(AsyncSubAgent) for {}",
                        upload.record.agent_id
                    ));
                }
                export_progress.push(attempt.progress);
            }
        }
        self.finalize_parent_stop(&session_id, &export_progress, &cleanup_candidates, context)?;
        Ok(())
    }

    fn finalize_parent_stop(
        &self,
        session_id: &str,
        export_progress: &[AgentExportProgress],
        cleanup_candidates: &[String],
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        let store = SessionStateStore::new(context.state_file.clone(), session_id, 500);
        let (removed, archive_snapshot) = match self.update_session_with_retry(
            &store,
            context,
            "parent Stop finalization",
            |session| {
                let mut registry = registry_from_session(session);
                for progress in export_progress {
                    record_agent_export_progress(
                        &mut registry,
                        &progress.agent_id,
                        &progress.accepted_turn_ids,
                        progress.complete,
                    );
                }
                let removed = prune_terminal_agents_after_stop(&mut registry, cleanup_candidates);
                let archive_snapshot = context.debug_enabled.then(|| session.clone());
                for record in &removed {
                    cleanup_subagent_timing_from_session(session, &record.agent_id, None);
                }
                registry_to_session(session, &registry);
                Ok((removed, archive_snapshot))
            },
        ) {
            Ok(result) => result,
            Err(StateTxnError::Busy { .. }) => {
                return Err(HandlerError {
                    message: "subagent: state lock unavailable; deferred parent Stop finalization"
                        .to_string(),
                });
            }
            Err(error) => {
                return Err(HandlerError {
                    message: format!("subagent: failed to finalize parent Stop: {error}"),
                });
            }
        };

        self.archive_agent_timings(archive_snapshot, &removed, context);
        if !removed.is_empty() {
            context.logger.info(&format!(
                "[subagent] Cleaned {} terminal registry records",
                removed.len()
            ));
        }
        Ok(())
    }

    fn finalize_agent_export(
        &self,
        session_id: &str,
        progress: &AgentExportProgress,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        let store = SessionStateStore::new(context.state_file.clone(), session_id, 500);
        let (record, archive_snapshot) = self
            .update_session_with_retry(&store, context, "subagent export finalization", |session| {
                let mut registry = registry_from_session(session);
                record_agent_export_progress(
                    &mut registry,
                    &progress.agent_id,
                    &progress.accepted_turn_ids,
                    progress.complete,
                );
                let record = registry.agents.get(&progress.agent_id).cloned();
                let archive_snapshot = context.debug_enabled.then(|| session.clone());
                if progress.complete {
                    cleanup_subagent_timing_from_session(session, &progress.agent_id, None);
                }
                registry_to_session(session, &registry);
                Ok((record, archive_snapshot))
            })
            .map_err(|error| HandlerError {
                message: format!(
                    "subagent: export progress was not saved for {}: {error}",
                    progress.agent_id
                ),
            })?;

        if progress.complete {
            if let Some(record) = record {
                self.archive_agent_timings(archive_snapshot, &[record], context);
            }
        }
        Ok(())
    }

    fn update_session_with_retry<R>(
        &self,
        store: &SessionStateStore,
        context: &CommonContext,
        operation: &str,
        mut update: impl FnMut(&mut Value) -> Result<R, StateTxnError>,
    ) -> Result<R, StateTxnError> {
        for attempt in 1..=FINALIZER_MAX_ATTEMPTS {
            match store.update_session(|session| update(session)) {
                Err(StateTxnError::Busy { .. }) if attempt < FINALIZER_MAX_ATTEMPTS => {
                    let backoff_ms = FINALIZER_RETRY_BACKOFF_MS * attempt as u64;
                    context.logger.warn(&format!(
                        "[subagent] {operation} lock busy; retrying attempt={}/{} backoff_ms={backoff_ms}",
                        attempt + 1,
                        FINALIZER_MAX_ATTEMPTS
                    ));
                    std::thread::sleep(std::time::Duration::from_millis(backoff_ms));
                }
                result => return result,
            }
        }
        unreachable!("bounded finalizer retry loop must return")
    }

    fn archive_agent_timings(
        &self,
        mut session_snapshot: Option<Value>,
        records: &[SubagentRecord],
        context: &CommonContext,
    ) {
        let Some(session) = session_snapshot.as_mut() else {
            return;
        };
        for record in records {
            if record.transcript_path.is_empty() {
                continue;
            }
            let archive_path = companion_timing_path(&record.transcript_path);
            cleanup_subagent_timing_from_session(session, &record.agent_id, Some(&archive_path));
        }
        context.logger.debug(&format!(
            "[subagent] Archived timing for {} terminal agents",
            records.len()
        ));
    }

    /// Build and upload an independent Turn(AsyncSubAgent) trace for a background agent.
    fn build_and_upload_async_turn(
        &self,
        session_id: &str,
        upload: &AsyncExportSnapshot,
        context: &CommonContext,
    ) -> AsyncExportAttempt {
        let record = &upload.record;
        let langfuse_config = match &context.langfuse_config {
            Some(config) => config,
            None => {
                return AsyncExportAttempt {
                    progress: AgentExportProgress {
                        agent_id: record.agent_id.clone(),
                        accepted_turn_ids: Vec::new(),
                        complete: true,
                    },
                    error: None,
                };
            }
        };

        let transcript_path = std::path::Path::new(&record.transcript_path);
        if !transcript_path.exists() {
            return failed_async_export(
                record,
                Vec::new(),
                format!("Subagent transcript not found: {}", record.transcript_path),
            );
        }

        let (messages, _offset, _buffer) = read_new_jsonl(transcript_path, 0, "");
        if messages.is_empty() {
            return completed_async_export(record, Vec::new());
        }

        let turns = build_turns(&messages, None);
        if turns.is_empty() {
            return completed_async_export(record, Vec::new());
        }

        context.logger.info(&format!(
            "[subagent] Parsed async agent {}: {} messages, {} turns",
            record.agent_id,
            messages.len(),
            turns.len()
        ));

        let chunks = match build_async_export_chunks(
            &turns,
            session_id,
            record,
            &context.username,
            &upload.timing,
            upload.vcs_info.as_ref(),
            ASYNC_SUBAGENT_MAX_BODY_BYTES,
        ) {
            Ok(chunks) => chunks,
            Err(error) => return failed_async_export(record, Vec::new(), error),
        };
        if chunks.is_empty() {
            return completed_async_export(record, Vec::new());
        }

        let mut accepted_turn_ids = Vec::new();
        for (index, chunk) in chunks.iter().enumerate() {
            match post_otel_to_langfuse(
                langfuse_config,
                &chunk.body,
                &context.logger,
                index == 0,
                session_id,
                &record.transcript_path,
            ) {
                Ok(()) => accepted_turn_ids.extend(chunk.completed_turn_ids.iter().cloned()),
                Err(err) => {
                    context.logger.error(&format!(
                        "[subagent] Failed to post async chunk {}/{} for agent {}: {}",
                        index + 1,
                        chunks.len(),
                        record.agent_id,
                        err
                    ));
                    if let Some(ref metrics_cell) = context.metrics {
                        metrics_cell.borrow_mut().inc(
                            "aivision.agentic.export_error",
                            "Export error",
                            "{count}",
                            vec![OtelAttribute::string("error.type", &err)],
                        );
                    }
                    return failed_async_export(record, accepted_turn_ids, err);
                }
            }
        }

        context.logger.debug(&format!(
            "[subagent] Posted {} Turn(AsyncSubAgent) spans for agent {} in {} bounded chunks",
            turns.len(),
            record.agent_id,
            chunks.len()
        ));
        AsyncExportAttempt {
            progress: AgentExportProgress {
                agent_id: record.agent_id.clone(),
                accepted_turn_ids,
                complete: true,
            },
            error: None,
        }
    }
}

fn completed_async_export(
    record: &SubagentRecord,
    accepted_turn_ids: Vec<String>,
) -> AsyncExportAttempt {
    AsyncExportAttempt {
        progress: AgentExportProgress {
            agent_id: record.agent_id.clone(),
            accepted_turn_ids,
            complete: true,
        },
        error: None,
    }
}

fn failed_async_export(
    record: &SubagentRecord,
    accepted_turn_ids: Vec<String>,
    message: impl Into<String>,
) -> AsyncExportAttempt {
    AsyncExportAttempt {
        progress: AgentExportProgress {
            agent_id: record.agent_id.clone(),
            accepted_turn_ids,
            complete: false,
        },
        error: Some(HandlerError {
            message: format!(
                "subagent: export failed for {}: {}",
                record.agent_id,
                message.into()
            ),
        }),
    }
}

#[allow(clippy::too_many_arguments)]
fn build_async_export_chunks(
    turns: &[Turn],
    session_id: &str,
    record: &SubagentRecord,
    username: &str,
    timing: &TimingData,
    vcs_info: Option<&VcsInfo>,
    max_body_bytes: usize,
) -> Result<Vec<AsyncExportChunk>, String> {
    let mut fragments = Vec::new();
    for (index, turn) in turns.iter().enumerate() {
        let native_turn_id = stable_turn_id(turn, session_id, index);
        let turn_id = format!(
            "cchooks-async-subagent-v1:{}:{native_turn_id}",
            record.agent_id
        );
        if record
            .exported_turn_ids
            .iter()
            .any(|exported| exported == &turn_id)
        {
            continue;
        }

        let mut body = build_otel_export_request_for_turn(
            turn,
            index + 1,
            session_id,
            username,
            HOOK_VERSION,
            &record.transcript_path,
            timing,
        );
        stabilize_turn_export_identities(&mut body, &turn_id, &[]);
        if let Some(vcs) = vcs_info {
            inject_vcs_resource_attrs(&mut body, &vcs.remote_url, &vcs.branch);
        }
        apply_async_subagent_semantics(
            &mut body,
            session_id,
            &record.agent_id,
            &record.agent_type,
            &record.description,
        );
        fragments.extend(split_async_turn_body(body, turn_id, max_body_bytes)?);
    }
    pack_async_fragments(fragments, max_body_bytes)
}

fn split_async_turn_body(
    body: Value,
    turn_id: String,
    max_body_bytes: usize,
) -> Result<Vec<AsyncExportFragment>, String> {
    if serialized_body_len(&body)? <= max_body_bytes {
        return Ok(vec![AsyncExportFragment {
            body,
            completed_turn_id: Some(turn_id),
        }]);
    }

    let resource_spans = body
        .get("resourceSpans")
        .and_then(Value::as_array)
        .ok_or_else(|| "async subagent OTLP body is missing resourceSpans".to_string())?;
    let mut fragments = Vec::new();
    for resource_span in resource_spans {
        let scope_spans = resource_span
            .get("scopeSpans")
            .and_then(Value::as_array)
            .ok_or_else(|| "async subagent resource span is missing scopeSpans".to_string())?;
        for scope_span in scope_spans {
            let spans = scope_span
                .get("spans")
                .and_then(Value::as_array)
                .ok_or_else(|| "async subagent scope span is missing spans".to_string())?;
            let mut current_spans = Vec::new();
            for span in spans {
                let mut candidate_spans = current_spans.clone();
                candidate_spans.push(span.clone());
                let candidate = async_span_fragment(resource_span, scope_span, candidate_spans);
                if serialized_body_len(&candidate)? <= max_body_bytes {
                    current_spans.push(span.clone());
                    continue;
                }
                if current_spans.is_empty() {
                    return Err(format!(
                        "one async subagent span exceeds max_body_bytes={max_body_bytes}"
                    ));
                }
                fragments.push(AsyncExportFragment {
                    body: async_span_fragment(resource_span, scope_span, current_spans),
                    completed_turn_id: None,
                });
                current_spans = vec![span.clone()];
                let single = async_span_fragment(resource_span, scope_span, current_spans.clone());
                if serialized_body_len(&single)? > max_body_bytes {
                    return Err(format!(
                        "one async subagent span exceeds max_body_bytes={max_body_bytes}"
                    ));
                }
            }
            if !current_spans.is_empty() {
                fragments.push(AsyncExportFragment {
                    body: async_span_fragment(resource_span, scope_span, current_spans),
                    completed_turn_id: None,
                });
            }
        }
    }
    let Some(last) = fragments.last_mut() else {
        return Err("oversized async subagent body contained no spans".to_string());
    };
    last.completed_turn_id = Some(turn_id);
    Ok(fragments)
}

fn async_span_fragment(resource_span: &Value, scope_span: &Value, spans: Vec<Value>) -> Value {
    let mut scope = scope_span.clone();
    scope["spans"] = Value::Array(spans);
    let mut resource = resource_span.clone();
    resource["scopeSpans"] = Value::Array(vec![scope]);
    serde_json::json!({"resourceSpans": [resource]})
}

fn pack_async_fragments(
    fragments: Vec<AsyncExportFragment>,
    max_body_bytes: usize,
) -> Result<Vec<AsyncExportChunk>, String> {
    let mut chunks = Vec::new();
    let mut current_resource_spans = Vec::new();
    let mut current_turn_ids = Vec::new();
    for fragment in fragments {
        let fragment_resource_spans = fragment
            .body
            .get("resourceSpans")
            .and_then(Value::as_array)
            .ok_or_else(|| "async subagent fragment is missing resourceSpans".to_string())?;
        let mut candidate_resource_spans = current_resource_spans.clone();
        candidate_resource_spans.extend(fragment_resource_spans.iter().cloned());
        let candidate = serde_json::json!({"resourceSpans": candidate_resource_spans});
        if !current_resource_spans.is_empty() && serialized_body_len(&candidate)? > max_body_bytes {
            chunks.push(AsyncExportChunk {
                body: serde_json::json!({"resourceSpans": current_resource_spans}),
                completed_turn_ids: current_turn_ids,
            });
            current_resource_spans = fragment_resource_spans.to_vec();
            current_turn_ids = Vec::new();
        } else {
            current_resource_spans = candidate
                .get("resourceSpans")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
        }
        if let Some(turn_id) = fragment.completed_turn_id {
            current_turn_ids.push(turn_id);
        }
    }
    if !current_resource_spans.is_empty() {
        chunks.push(AsyncExportChunk {
            body: serde_json::json!({"resourceSpans": current_resource_spans}),
            completed_turn_ids: current_turn_ids,
        });
    }
    Ok(chunks)
}

fn serialized_body_len(body: &Value) -> Result<usize, String> {
    serde_json::to_vec(body)
        .map(|bytes| bytes.len())
        .map_err(|error| format!("failed to measure async subagent OTLP body: {error}"))
}

impl HookHandler for SubagentHandler {
    fn name(&self) -> &str {
        "subagent"
    }

    fn supported_events(&self) -> &[HookEvent] {
        &[
            HookEvent::UserPromptSubmit,
            HookEvent::SubagentStop,
            HookEvent::Stop,
        ]
    }

    fn handle(
        &self,
        event: &HookEvent,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        match event {
            HookEvent::UserPromptSubmit => self.handle_user_prompt_submit(payload, context),
            HookEvent::SubagentStop => self.handle_subagent_stop(payload, context),
            HookEvent::Stop => self.handle_parent_stop(payload, context),
            _ => Ok(()),
        }
    }
}

// --- Helpers ---

/// Scan the parent transcript for Agent/Task subagent tool_use calls.
/// Returns (agent_id → is_background, total_lines, assistant_count, agent_tool_use_count).
/// Uses BufReader for reliable line-by-line reading (avoids read_new_jsonl buffering edge cases).
fn scan_transcript_for_agent_background(
    transcript_path: &str,
    registry: &mut SubagentRegistry,
) -> (std::collections::HashMap<String, bool>, usize, usize, usize) {
    use crate::common::otel::extract_agent_id_from_result;
    use std::collections::HashMap;

    let mut result = HashMap::new();
    let path = std::path::Path::new(transcript_path);
    let prefix_hint = registry
        .parent_source_epoch
        .as_ref()
        .map(|epoch| epoch.prefix_len);
    let snapshot = match inspect_transcript_source(path, prefix_hint)
        .or_else(|_| inspect_transcript_source(path, None))
    {
        Ok(snapshot) => snapshot,
        Err(_) => return (result, 0, 0, 0),
    };
    let source_changed = registry
        .parent_source_epoch
        .as_ref()
        .is_some_and(|known| known != &snapshot.epoch);
    if !source_changed && registry.parent_scan_terminal_reason.is_some() {
        return (result, 0, 0, 0);
    }
    let mut next_offset = if source_changed {
        0
    } else {
        registry.parent_discovery_offset.max(0)
    };
    if next_offset as u64 > snapshot.file_len {
        next_offset = 0;
    }
    let mut pending_tool_uses = if source_changed || next_offset == 0 {
        HashMap::new()
    } else {
        registry.pending_agent_tool_uses.clone()
    };

    let mut total_lines = 0usize;
    let mut assistant_count = 0usize;
    let mut agent_tu_count = 0usize;
    let mut terminal_reason = None;
    let records = match read_bounded_jsonl_records(
        path,
        next_offset,
        PARENT_DISCOVERY_MAX_BYTES,
        PARENT_DISCOVERY_BUDGET,
    ) {
        Ok(BoundedJsonlScan::Ready {
            records,
            next_offset: scanned_offset,
        }) => {
            next_offset = scanned_offset;
            records
        }
        Ok(BoundedJsonlScan::CaughtUp {
            next_offset: scanned_offset,
        }) => {
            next_offset = scanned_offset;
            Vec::new()
        }
        Ok(BoundedJsonlScan::WaitingForMoreData { record_start }) => {
            next_offset = record_start;
            Vec::new()
        }
        Ok(BoundedJsonlScan::Terminal {
            reason,
            record_start,
            ..
        }) => {
            next_offset = record_start;
            terminal_reason = Some(reason.to_string());
            Vec::new()
        }
        Err(_) => return (result, 0, 0, 0),
    };

    for record in records {
        let msg = record.value;
        total_lines += 1;

        let msg_type = msg.get("type").and_then(|v| v.as_str()).unwrap_or("");

        if msg_type == "assistant" {
            assistant_count += 1;
            let content = msg
                .get("message")
                .and_then(|m| m.get("content"))
                .and_then(|c| c.as_array());
            if let Some(items) = content {
                for item in items {
                    if item.get("type").and_then(|v| v.as_str()) == Some("tool_use")
                        && item
                            .get("name")
                            .and_then(|v| v.as_str())
                            .is_some_and(crate::common::hook::is_subagent_orchestration_tool)
                    {
                        let tool_id = item.get("id").and_then(|v| v.as_str()).unwrap_or("");
                        let input = item.get("input");
                        let is_bg = input
                            .and_then(|i| i.get("run_in_background"))
                            .and_then(|v| v.as_bool())
                            .unwrap_or(false);
                        if !tool_id.is_empty() {
                            pending_tool_uses.insert(tool_id.to_string(), is_bg);
                            agent_tu_count += 1;
                        }
                    }
                }
            }
        }

        if msg_type == "user" {
            let result_marks_async = tool_result_marks_async(&msg);
            let structured_agent_id = msg
                .pointer("/toolUseResult/agentId")
                .and_then(Value::as_str);
            let content = msg
                .get("message")
                .and_then(|m| m.get("content"))
                .and_then(|c| c.as_array());
            if let Some(items) = content {
                for item in items {
                    if item.get("type").and_then(|v| v.as_str()) != Some("tool_result") {
                        continue;
                    }
                    let tool_use_id = item
                        .get("tool_use_id")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    // tool_result content can be string or array
                    let content_text = match item.get("content") {
                        Some(serde_json::Value::String(s)) => s.clone(),
                        Some(serde_json::Value::Array(arr)) => {
                            // Array of content blocks — extract text from each
                            arr.iter()
                                .filter_map(|c| c.get("text").and_then(|t| t.as_str()))
                                .collect::<Vec<_>>()
                                .join("\n")
                        }
                        _ => String::new(),
                    };
                    if !tool_use_id.is_empty() {
                        if let Some(launch_marks_async) = pending_tool_uses.remove(&tool_use_id) {
                            let is_background = launch_marks_async || result_marks_async;
                            if let Some(agent_id) = extract_agent_id_from_result(&content_text)
                                .or_else(|| structured_agent_id.map(str::to_string))
                            {
                                result.insert(agent_id, is_background);
                            }
                        }
                    }
                }
            }
        }
    }

    let verified = match inspect_transcript_source(path, Some(snapshot.epoch.prefix_len)) {
        Ok(verified)
            if verified.epoch == snapshot.epoch && verified.file_len >= next_offset as u64 =>
        {
            verified
        }
        _ => return (HashMap::new(), 0, 0, 0),
    };
    registry.parent_discovery_offset = next_offset;
    registry.parent_observed_file_len = verified.file_len;
    registry.parent_source_epoch = Some(snapshot.epoch);
    registry.pending_agent_tool_uses = pending_tool_uses;
    registry.parent_scan_terminal_reason = terminal_reason;

    for (agent_id, is_background) in &result {
        if let Some(record) = registry.agents.get_mut(agent_id) {
            record.is_background = Some(*is_background);
        }
    }

    (result, total_lines, assistant_count, agent_tu_count)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::otel::{consume_test_http_request, derive_subagent_conversation_id};
    use serde_json::json;
    use std::sync::mpsc::{self, RecvTimeoutError};
    use std::thread::JoinHandle;

    fn failing_http_endpoint(requests: usize) -> String {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind test server");
        let address = listener.local_addr().expect("test server address");
        std::thread::spawn(move || {
            for _ in 0..requests {
                let (mut stream, _) = listener.accept().expect("accept request");
                consume_test_http_request(&mut stream);
                let response =
                    b"HTTP/1.1 500 Test Failure\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
                let _ = std::io::Write::write_all(&mut stream, response);
            }
        });
        format!("http://{address}/v1/traces")
    }

    fn counting_success_endpoint() -> (String, mpsc::Sender<()>, JoinHandle<usize>) {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind test server");
        listener
            .set_nonblocking(true)
            .expect("set nonblocking test server");
        let address = listener.local_addr().expect("test server address");
        let (finished, client_finished) = mpsc::channel();
        let server = std::thread::spawn(move || {
            let mut requests = 0;
            loop {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        consume_test_http_request(&mut stream);
                        let response =
                            b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
                        let _ = std::io::Write::write_all(&mut stream, response);
                        requests += 1;
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        match client_finished.recv_timeout(std::time::Duration::from_millis(5)) {
                            Ok(()) | Err(RecvTimeoutError::Disconnected) => break,
                            Err(RecvTimeoutError::Timeout) => {}
                        }
                    }
                    Err(error) => panic!("accept test request: {error}"),
                }
            }
            requests
        });
        (format!("http://{address}/v1/traces"), finished, server)
    }

    fn test_context(dir: &tempfile::TempDir, endpoint: String) -> CommonContext {
        CommonContext {
            logger: crate::common::logger::Logger::new(dir.path().join("subagent.log"), true),
            state_file: dir.path().join("state.json"),
            langfuse_config: Some(crate::hooks::langfuse::LangfuseConfig {
                public_key: None,
                secret_key: None,
                host: endpoint,
                headers: None,
            }),
            username: "test-user".to_string(),
            projects_dir: String::new(),
            debug_enabled: false,
            hook_version: "test".to_string(),
            turn_seed: None,
            stop_transcript_end_offset: None,
            transcript_ingest_receipt: None,
            stop_budget: None,
            retry_policy: crate::hooks::RetryPolicy::AtLeastOnce,
            export_mode: crate::hooks::ExportMode::StopOnly,
            realtime_llm_batch_size: crate::hooks::DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
            metrics: None,
            send_queue: std::cell::RefCell::new(Vec::new()),
            send_errors: std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
            stop_outcome: std::cell::RefCell::new(crate::hooks::StopOutcome::IntentionallySkipped),
        }
    }

    fn write_test_turns(
        transcript: &std::path::Path,
        turn_count: usize,
        text_bytes: usize,
    ) -> Vec<Turn> {
        let mut records = Vec::new();
        for index in 0..turn_count {
            records.push(json!({
                "type": "user",
                "message": {"role": "user", "content": format!("prompt-{index}")},
                "timestamp": format!("2026-08-14T08:00:{:02}.000Z", index * 2)
            }));
            records.push(json!({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": format!("answer-{index}-{}", "x".repeat(text_bytes))}],
                    "id": format!("msg-{index}"),
                    "model": "test-model",
                    "usage": {"input_tokens": 1, "output_tokens": 1}
                },
                "timestamp": format!("2026-08-14T08:00:{:02}.500Z", index * 2 + 1)
            }));
        }
        std::fs::write(
            transcript,
            records
                .iter()
                .map(Value::to_string)
                .collect::<Vec<_>>()
                .join("\n"),
        )
        .expect("write test turns");
        let (messages, _, _) = read_new_jsonl(transcript, 0, "");
        build_turns(&messages, None)
    }

    fn test_subagent_record(transcript: &std::path::Path, agent_id: &str) -> SubagentRecord {
        SubagentRecord {
            agent_id: agent_id.to_string(),
            agent_type: "Explore".to_string(),
            transcript_path: transcript.to_string_lossy().to_string(),
            stopped_at: "2026-08-14T08:10:00.000Z".to_string(),
            processed: false,
            last_assistant_message: "done".to_string(),
            description: "test async export".to_string(),
            is_background: Some(true),
            exported_turn_ids: Vec::new(),
        }
    }

    fn export_span_identities(body: &Value) -> Vec<(String, String, String)> {
        body.get("resourceSpans")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .flat_map(|resource| {
                resource
                    .get("scopeSpans")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
            })
            .flat_map(|scope| {
                scope
                    .get("spans")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
            })
            .map(|span| {
                (
                    span.get("traceId")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                    span.get("spanId")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                    span.get("parentSpanId")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                )
            })
            .collect()
    }

    fn export_span_string_attrs(body: &Value, key: &str) -> Vec<String> {
        body.get("resourceSpans")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .flat_map(|resource| {
                resource
                    .get("scopeSpans")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
            })
            .flat_map(|scope| {
                scope
                    .get("spans")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
            })
            .filter_map(|span| {
                span.get("attributes")
                    .and_then(Value::as_array)
                    .and_then(|attributes| {
                        attributes.iter().find_map(|attribute| {
                            if attribute.get("key").and_then(Value::as_str) == Some(key) {
                                attribute
                                    .pointer("/value/stringValue")
                                    .and_then(Value::as_str)
                                    .map(str::to_string)
                            } else {
                                None
                            }
                        })
                    })
            })
            .collect()
    }

    #[test]
    fn subagent_handler_tracks_parent_turn_boundaries() {
        let handler = SubagentHandler::new();
        assert_eq!(
            handler.supported_events(),
            &[
                HookEvent::UserPromptSubmit,
                HookEvent::SubagentStop,
                HookEvent::Stop,
            ]
        );
    }

    #[test]
    fn async_export_propagates_final_http_failure() {
        let dir = tempfile::tempdir().expect("tempdir");
        let transcript = dir.path().join("agent-failed.jsonl");
        let records = [
            json!({
                "type": "user",
                "message": {"role": "user", "content": "hello"},
                "timestamp": "2026-08-14T08:00:00.000Z"
            }),
            json!({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                    "id": "msg-1",
                    "model": "test-model",
                    "usage": {"input_tokens": 1, "output_tokens": 1}
                },
                "timestamp": "2026-08-14T08:00:01.000Z"
            }),
        ];
        std::fs::write(
            &transcript,
            records
                .iter()
                .map(serde_json::Value::to_string)
                .collect::<Vec<_>>()
                .join("\n"),
        )
        .expect("write transcript");

        let upload = AsyncExportSnapshot {
            record: SubagentRecord {
                agent_id: "agent-failed".to_string(),
                agent_type: "Explore".to_string(),
                transcript_path: transcript.to_string_lossy().to_string(),
                stopped_at: "2026-08-14T08:00:02.000Z".to_string(),
                processed: false,
                last_assistant_message: "done".to_string(),
                description: "test failure propagation".to_string(),
                is_background: Some(true),
                exported_turn_ids: Vec::new(),
            },
            timing: TimingData::default(),
            vcs_info: None,
        };
        let context = test_context(&dir, failing_http_endpoint(2));

        let result =
            SubagentHandler::new().build_and_upload_async_turn("session-failed", &upload, &context);

        assert!(
            result.error.is_some(),
            "final HTTP failure must remain retryable"
        );
        assert!(!result.progress.complete);
        assert!(result.progress.accepted_turn_ids.is_empty());
        assert!(result.error.unwrap().message.contains("export failed"));
    }

    #[test]
    fn multi_turn_async_export_stays_in_one_chunk_when_under_limit() {
        let dir = tempfile::tempdir().expect("tempdir");
        let transcript = dir.path().join("agent-multi-turn.jsonl");
        let records = [
            json!({
                "type": "user",
                "message": {"role": "user", "content": "first"},
                "timestamp": "2026-08-14T08:00:00.000Z"
            }),
            json!({
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "one"}], "id": "msg-1", "model": "test-model", "usage": {"input_tokens": 1, "output_tokens": 1}},
                "timestamp": "2026-08-14T08:00:01.000Z"
            }),
            json!({
                "type": "user",
                "message": {"role": "user", "content": "second"},
                "timestamp": "2026-08-14T08:00:02.000Z"
            }),
            json!({
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "two"}], "id": "msg-2", "model": "test-model", "usage": {"input_tokens": 1, "output_tokens": 1}},
                "timestamp": "2026-08-14T08:00:03.000Z"
            }),
        ];
        std::fs::write(
            &transcript,
            records
                .iter()
                .map(serde_json::Value::to_string)
                .collect::<Vec<_>>()
                .join("\n"),
        )
        .expect("write transcript");
        let upload = AsyncExportSnapshot {
            record: SubagentRecord {
                agent_id: "agent-multi-turn".to_string(),
                agent_type: "Explore".to_string(),
                transcript_path: transcript.to_string_lossy().to_string(),
                stopped_at: "2026-08-14T08:00:04.000Z".to_string(),
                processed: false,
                last_assistant_message: "two".to_string(),
                description: "test batch delivery".to_string(),
                is_background: Some(true),
                exported_turn_ids: Vec::new(),
            },
            timing: TimingData::default(),
            vcs_info: None,
        };
        let (endpoint, finished, server) = counting_success_endpoint();
        let context = test_context(&dir, endpoint);

        let result = SubagentHandler::new().build_and_upload_async_turn(
            "session-multi-turn",
            &upload,
            &context,
        );
        finished.send(()).expect("notify fixture of client completion");
        let request_count = server.join().expect("request count");

        assert!(result.error.is_none());
        assert!(result.progress.complete);
        assert_eq!(result.progress.accepted_turn_ids.len(), 2);
        assert_eq!(
            request_count, 1,
            "under-limit subagent turns should share one bounded HTTP chunk"
        );
    }

    #[test]
    fn async_export_ids_are_stable_and_acknowledged_turns_are_skipped() {
        let dir = tempfile::tempdir().expect("tempdir");
        let transcript = dir.path().join("agent-stable.jsonl");
        let turns = write_test_turns(&transcript, 3, 128);
        let record = test_subagent_record(&transcript, "agent-stable");

        let first = build_async_export_chunks(
            &turns,
            "session-stable",
            &record,
            "test-user",
            &TimingData::default(),
            None,
            usize::MAX,
        )
        .expect("first build");
        let second = build_async_export_chunks(
            &turns,
            "session-stable",
            &record,
            "test-user",
            &TimingData::default(),
            None,
            usize::MAX,
        )
        .expect("second build");

        assert_eq!(first.len(), 1);
        assert_eq!(
            export_span_identities(&first[0].body),
            export_span_identities(&second[0].body)
        );
        assert_eq!(first[0].completed_turn_ids, second[0].completed_turn_ids);
        assert_eq!(first[0].completed_turn_ids.len(), 3);

        let acknowledged = first[0].completed_turn_ids[0].clone();
        let mut resumed_record = record.clone();
        resumed_record.exported_turn_ids.push(acknowledged.clone());
        let resumed = build_async_export_chunks(
            &turns,
            "session-stable",
            &resumed_record,
            "test-user",
            &TimingData::default(),
            None,
            usize::MAX,
        )
        .expect("resume build");
        let remaining_ids = resumed
            .iter()
            .flat_map(|chunk| chunk.completed_turn_ids.iter())
            .collect::<Vec<_>>();

        assert_eq!(remaining_ids.len(), 2);
        assert!(!remaining_ids.contains(&&acknowledged));
    }

    #[test]
    fn async_subagent_turns_share_stable_scoped_conversation_id() {
        let dir = tempfile::tempdir().expect("tempdir");
        let transcript = dir.path().join("agent-conversation.jsonl");
        let turns = write_test_turns(&transcript, 3, 128);
        let parent_session_id = "session-parent";
        let record = test_subagent_record(&transcript, "agent-a");

        let chunks = build_async_export_chunks(
            &turns,
            parent_session_id,
            &record,
            "test-user",
            &TimingData::default(),
            None,
            usize::MAX,
        )
        .expect("build async export");
        let expected = derive_subagent_conversation_id(parent_session_id, "agent-a");
        let conversations = chunks
            .iter()
            .flat_map(|chunk| export_span_string_attrs(&chunk.body, "gen_ai.conversation.id"))
            .collect::<Vec<_>>();

        assert!(!conversations.is_empty());
        assert!(conversations.iter().all(|value| value == &expected));
        assert_ne!(expected, parent_session_id);

        let other_record = test_subagent_record(&transcript, "agent-b");
        let other_chunks = build_async_export_chunks(
            &turns,
            parent_session_id,
            &other_record,
            "test-user",
            &TimingData::default(),
            None,
            usize::MAX,
        )
        .expect("build second async export");
        let other_expected = derive_subagent_conversation_id(parent_session_id, "agent-b");
        let other_conversations = other_chunks
            .iter()
            .flat_map(|chunk| export_span_string_attrs(&chunk.body, "gen_ai.conversation.id"))
            .collect::<Vec<_>>();
        assert!(other_conversations
            .iter()
            .all(|value| value == &other_expected));
        assert_ne!(conversations[0], other_conversations[0]);
    }

    #[test]
    fn async_export_chunks_respect_the_serialized_byte_limit() {
        let dir = tempfile::tempdir().expect("tempdir");
        let transcript = dir.path().join("agent-bounded.jsonl");
        let turns = write_test_turns(&transcript, 6, 4096);
        let record = test_subagent_record(&transcript, "agent-bounded");
        let timing = TimingData::default();
        let largest_single = turns
            .iter()
            .map(|turn| {
                let chunks = build_async_export_chunks(
                    std::slice::from_ref(turn),
                    "session-bounded",
                    &record,
                    "test-user",
                    &timing,
                    None,
                    usize::MAX,
                )
                .expect("single-turn build");
                serialized_body_len(&chunks[0].body).expect("measure single turn")
            })
            .max()
            .expect("at least one turn");
        let max_body_bytes = largest_single + 64;

        let chunks = build_async_export_chunks(
            &turns,
            "session-bounded",
            &record,
            "test-user",
            &timing,
            None,
            max_body_bytes,
        )
        .expect("bounded build");

        assert!(chunks.len() > 1);
        assert!(chunks.iter().all(|chunk| {
            serialized_body_len(&chunk.body).expect("measure chunk") <= max_body_bytes
        }));
        assert_eq!(
            chunks
                .iter()
                .map(|chunk| chunk.completed_turn_ids.len())
                .sum::<usize>(),
            turns.len()
        );
    }

    #[cfg(unix)]
    #[test]
    fn export_finalizer_retries_lock_contention_and_saves_progress() {
        let dir = tempfile::tempdir().expect("tempdir");
        let state_file = dir.path().join("state.json");
        let session_id = "session-finalizer-retry";
        let transcript = dir.path().join("agent-retry.jsonl");
        let record = test_subagent_record(&transcript, "agent-retry");
        let mut registry = super::super::subagent_state::SubagentRegistry::default();
        registry.agents.insert(record.agent_id.clone(), record);
        let mut session = json!({});
        registry_to_session(&mut session, &registry);
        let mut root = serde_json::Map::new();
        root.insert(session_id.to_string(), session);
        std::fs::write(
            &state_file,
            serde_json::to_vec(&Value::Object(root)).expect("serialize state"),
        )
        .expect("write state");

        let held_lock = agentic_otel_common::state::StateLock::acquire(&state_file, 100);
        assert!(held_lock.is_acquired());
        let release = std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(600));
            drop(held_lock);
        });
        let mut context = test_context(&dir, "http://127.0.0.1:9/v1/traces".to_string());
        context.state_file = state_file.clone();
        let progress = AgentExportProgress {
            agent_id: "agent-retry".to_string(),
            accepted_turn_ids: vec!["turn-accepted".to_string()],
            complete: true,
        };

        SubagentHandler::new()
            .finalize_agent_export(session_id, &progress, &context)
            .expect("finalizer should retry after lock release");
        release.join().expect("release lock");

        let saved: Value =
            serde_json::from_slice(&std::fs::read(&state_file).expect("read finalized state"))
                .expect("parse finalized state");
        let saved_registry = registry_from_session(&saved[session_id]);
        let saved_record = &saved_registry.agents["agent-retry"];
        assert!(saved_record.processed);
        assert_eq!(saved_record.exported_turn_ids, vec!["turn-accepted"]);
        assert!(std::fs::read_to_string(dir.path().join("subagent.log"))
            .expect("read log")
            .contains("lock busy; retrying"));
    }

    #[test]
    fn test_apply_async_subagent_semantics() {
        let mut request = json!({
            "resourceSpans": [{
                "resource": { "attributes": [] },
                "scopeSpans": [{
                    "spans": [{
                        "traceId": "abc",
                        "spanId": "def",
                        "parentSpanId": "",
                        "name": "Turn 1",
                        "attributes": [
                            {"key": "gen_ai.span.kind", "value": {"stringValue": "CHAT"}},
                            {"key": "gen_ai.conversation.id", "value": {"stringValue": "parent-session"}}
                        ]
                    }, {
                        "traceId": "abc",
                        "spanId": "ghi",
                        "parentSpanId": "def",
                        "name": "chat GLM",
                        "attributes": [
                            {"key": "gen_ai.conversation.id", "value": {"stringValue": "parent-session"}}
                        ]
                    }, {
                        "traceId": "abc",
                        "spanId": "jkl",
                        "parentSpanId": "def",
                        "name": "tool Read",
                        "attributes": [
                            {"key": "gen_ai.conversation.id", "value": {"stringValue": "parent-session"}}
                        ]
                    }]
                }]
            }]
        });

        let conversation_id = derive_subagent_conversation_id("parent-session", "agent-abc");
        apply_async_subagent_semantics(
            &mut request,
            "parent-session",
            "agent-abc",
            "general-purpose",
            "test task",
        );

        // Root span renamed
        let root_name = request
            .pointer("/resourceSpans/0/scopeSpans/0/spans/0/name")
            .unwrap()
            .as_str()
            .unwrap();
        assert_eq!(root_name, "Turn AsyncAgent");

        // Child span NOT renamed
        let child_name = request
            .pointer("/resourceSpans/0/scopeSpans/0/spans/1/name")
            .unwrap()
            .as_str()
            .unwrap();
        assert_eq!(child_name, "chat GLM");

        // Child spans retain subagent attrs.
        let child_attrs = request
            .pointer("/resourceSpans/0/scopeSpans/0/spans/1/attributes")
            .unwrap()
            .as_array()
            .unwrap();
        assert!(child_attrs
            .iter()
            .any(|a| a["key"] == "agentic.subagent.mode" && a["value"]["stringValue"] == "async"));

        let spans = request
            .pointer("/resourceSpans/0/scopeSpans/0/spans")
            .and_then(Value::as_array)
            .unwrap();
        for span in spans {
            let attrs = span["attributes"].as_array().unwrap();
            let conversation_attrs = attrs
                .iter()
                .filter(|attribute| attribute["key"] == "gen_ai.conversation.id")
                .collect::<Vec<_>>();
            assert_eq!(conversation_attrs.len(), 1);
            assert_eq!(
                conversation_attrs[0]["value"]["stringValue"],
                conversation_id
            );
        }
    }

    #[test]
    fn test_scan_transcript_empty() {
        // Non-existent file returns empty map
        let mut registry = SubagentRegistry::default();
        let (result, total, asst, agent) = scan_transcript_for_agent_background(
            "/tmp/nonexistent-transcript.jsonl",
            &mut registry,
        );
        assert!(result.is_empty());
        assert_eq!(total, 0);
        assert_eq!(asst, 0);
        assert_eq!(agent, 0);
    }

    #[test]
    fn parent_stop_scanner_recovers_both_agent_and_task_subagents() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("session.jsonl");
        let records = [
            json!({
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "id": "tool-agent", "name": "Agent",
                     "input": {"run_in_background": false}},
                    {"type": "tool_use", "id": "tool-task", "name": "Task",
                     "input": {"run_in_background": true}}
                ]}
            }),
            json!({
                "type": "user",
                "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "tool-agent",
                     "content": "agentId: sync123 completed"},
                    {"type": "tool_result", "tool_use_id": "tool-task",
                     "content": "agentId: async456 started"}
                ]}
            }),
        ];
        std::fs::write(
            &path,
            records
                .iter()
                .map(serde_json::Value::to_string)
                .collect::<Vec<_>>()
                .join("\n"),
        )
        .unwrap();

        let mut registry = SubagentRegistry::default();
        let (subagents, total, assistants, tool_uses) =
            scan_transcript_for_agent_background(path.to_str().unwrap(), &mut registry);

        assert_eq!(total, 2);
        assert_eq!(assistants, 1);
        assert_eq!(tool_uses, 2);
        assert_eq!(subagents.get("sync123"), Some(&false));
        assert_eq!(subagents.get("async456"), Some(&true));

        let first_offset = registry.parent_discovery_offset;
        let (unchanged, total, assistants, tool_uses) =
            scan_transcript_for_agent_background(path.to_str().unwrap(), &mut registry);
        assert!(unchanged.is_empty());
        assert_eq!((total, assistants, tool_uses), (0, 0, 0));
        assert_eq!(registry.parent_discovery_offset, first_offset);
    }

    #[test]
    fn parent_stop_scanner_recognizes_result_only_async_markers() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("session.jsonl");
        let records = [
            json!({
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "id": "tool-is-async", "name": "Agent", "input": {}},
                    {"type": "tool_use", "id": "tool-status", "name": "Agent", "input": {}}
                ]}
            }),
            json!({
                "type": "user",
                "message": {"content": [{
                    "type": "tool_result", "tool_use_id": "tool-is-async", "content": "launched"
                }]},
                "toolUseResult": {"agentId": "agent-is-async", "isAsync": true}
            }),
            json!({
                "type": "user",
                "message": {"content": [{
                    "type": "tool_result", "tool_use_id": "tool-status", "content": "launched"
                }]},
                "toolUseResult": {"agentId": "agent-status", "status": "async_launched"}
            }),
        ];
        std::fs::write(
            &path,
            records
                .iter()
                .map(Value::to_string)
                .collect::<Vec<_>>()
                .join("\n"),
        )
        .unwrap();

        let mut registry = SubagentRegistry::default();
        let (subagents, _, _, _) =
            scan_transcript_for_agent_background(path.to_str().unwrap(), &mut registry);

        assert_eq!(subagents.get("agent-is-async"), Some(&true));
        assert_eq!(subagents.get("agent-status"), Some(&true));
        assert!(registry.pending_agent_tool_uses.is_empty());
    }

    #[test]
    fn parent_stop_scanner_preserves_unmatched_agent_tool_use_across_appends() {
        use std::io::Write;

        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("session.jsonl");
        std::fs::write(
            &path,
            format!(
                "{}\n",
                json!({
                    "type": "assistant",
                    "message": {"content": [{
                        "type": "tool_use",
                        "id": "tool-agent",
                        "name": "Agent",
                        "input": {"run_in_background": true}
                    }]}
                })
            ),
        )
        .unwrap();
        let mut registry = SubagentRegistry::default();
        let (first, total, assistants, tool_uses) =
            scan_transcript_for_agent_background(path.to_str().unwrap(), &mut registry);
        assert!(first.is_empty());
        assert_eq!((total, assistants, tool_uses), (1, 1, 1));
        assert_eq!(
            registry.pending_agent_tool_uses.get("tool-agent"),
            Some(&true)
        );

        let mut file = std::fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap();
        writeln!(
            file,
            "{}",
            json!({
                "type": "user",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "tool-agent",
                    "content": "agentId: appended123 started"
                }]}
            })
        )
        .unwrap();

        let (second, total, assistants, tool_uses) =
            scan_transcript_for_agent_background(path.to_str().unwrap(), &mut registry);
        assert_eq!(second.get("appended123"), Some(&true));
        assert_eq!((total, assistants, tool_uses), (1, 0, 0));
        assert!(registry.pending_agent_tool_uses.is_empty());
    }

    #[test]
    fn parent_stop_scanner_advances_large_transcript_in_bounded_passes() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("large-session.jsonl");
        let line = format!(
            "{}\n",
            json!({
                "type": "user",
                "message": {"content": "x".repeat(1024)}
            })
        );
        let transcript = line.repeat(5 * 1024);
        assert!(transcript.len() > PARENT_DISCOVERY_MAX_BYTES);
        std::fs::write(&path, transcript.as_bytes()).unwrap();

        let mut registry = SubagentRegistry::default();
        let (_, first_lines, _, _) =
            scan_transcript_for_agent_background(path.to_str().unwrap(), &mut registry);
        let first_offset = registry.parent_discovery_offset;
        assert!(first_lines > 0);
        assert!(first_offset > 0);
        assert!((first_offset as usize) < transcript.len());
        assert!(registry.parent_scan_terminal_reason.is_none());

        let (_, second_lines, _, _) =
            scan_transcript_for_agent_background(path.to_str().unwrap(), &mut registry);
        assert!(second_lines > 0);
        assert!(registry.parent_discovery_offset > first_offset);
        assert!(registry.parent_scan_terminal_reason.is_none());
    }

    #[test]
    fn parent_stop_scanner_quarantines_terminal_record_until_source_changes() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("session.jsonl");
        std::fs::write(&path, b"not-json\n").unwrap();
        let mut registry = SubagentRegistry::default();

        let (_, total, _, _) =
            scan_transcript_for_agent_background(path.to_str().unwrap(), &mut registry);
        assert_eq!(total, 0);
        assert_eq!(
            registry.parent_scan_terminal_reason.as_deref(),
            Some("invalid_jsonl")
        );
        let epoch = registry.parent_source_epoch.clone();

        let (_, total, _, _) =
            scan_transcript_for_agent_background(path.to_str().unwrap(), &mut registry);
        assert_eq!(total, 0);
        assert_eq!(registry.parent_source_epoch, epoch);

        std::fs::write(
            &path,
            format!("{}\n", json!({"type": "user", "message": {"content": []}})),
        )
        .unwrap();
        let (_, total, _, _) =
            scan_transcript_for_agent_background(path.to_str().unwrap(), &mut registry);
        assert_eq!(total, 1);
        assert!(registry.parent_scan_terminal_reason.is_none());
    }
}
