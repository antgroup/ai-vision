use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use agentic_otel_common::otlp::OtelAttribute;
use serde_json::Value;

use crate::common::hook::{
    extract_session_id, extract_transcript_path, is_subagent_orchestration_tool, HookEvent,
};
use crate::common::logger::now_iso8601_pub;
use crate::common::otel::{
    build_realtime_generation_export_request, inject_vcs_resource_attrs,
    post_otel_to_langfuse_once_with_context, LangfuseAttemptContext, RealtimeGenerationInput,
};
use crate::common::transcript::{
    build_turns, get_role, inspect_transcript_source, is_realtime_terminal_generation,
    is_tool_result, read_bounded_jsonl_records, BoundedJsonlScan, Turn,
};
use crate::hooks::langfuse::realtime_state::{
    checkpoint_generation_discovery, child_delivery_is_terminal, claim_generation_prefix, contexts,
    ensure_generation_state, finish_child_deliveries, finish_generation_claim,
    generation_child_key, mark_generation_scan_terminal, otlp_payload_digest,
    otlp_span_end_time_unix_nano, prepare_child_delivery, ChildDeliverySpec, GenerationCandidate,
    GenerationClaimUpdate, RealtimeContext,
};
use crate::hooks::langfuse::state::{
    get_vcs_for_cwd_from_state, SessionStateStore, StateTxnError, VcsInfo,
};
use crate::hooks::timing::load_timing_from_state;
use crate::hooks::{
    CommonContext, HandlerError, HookHandler, RetryPolicy, DEFAULT_REALTIME_LLM_CLAIM_LEASE_MS,
    DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE, DEFAULT_REALTIME_LLM_SCAN_BUDGET_MS,
    DEFAULT_REALTIME_LLM_SCAN_MAX_BYTES,
};

const REALTIME_GENERATION_LOCK_TIMEOUT_MS: u64 = 500;
const REALTIME_GENERATION_HTTP_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_REALTIME_GENERATION_REQUEST_CHUNKS: usize = 4;

#[derive(Debug, Clone, Copy)]
struct ScanPolicy {
    max_bytes: usize,
    budget: Duration,
    lease_ms: u64,
}

impl Default for ScanPolicy {
    fn default() -> Self {
        Self {
            max_bytes: DEFAULT_REALTIME_LLM_SCAN_MAX_BYTES,
            budget: Duration::from_millis(DEFAULT_REALTIME_LLM_SCAN_BUDGET_MS),
            lease_ms: DEFAULT_REALTIME_LLM_CLAIM_LEASE_MS,
        }
    }
}

#[derive(Default)]
pub struct RealtimeGenerationHandler {
    policy: ScanPolicy,
}

impl RealtimeGenerationHandler {
    pub fn new() -> Self {
        Self::default()
    }
}

impl HookHandler for RealtimeGenerationHandler {
    fn name(&self) -> &str {
        "langfuse_realtime_generation"
    }

    fn supported_events(&self) -> &[HookEvent] {
        &[HookEvent::PostToolUse, HookEvent::PostToolUseFailure]
    }

    fn handle(
        &self,
        event: &HookEvent,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        if !context.export_mode.realtime_enabled() {
            return Ok(());
        }
        if !matches!(
            event,
            HookEvent::PostToolUse | HookEvent::PostToolUseFailure
        ) {
            return Ok(());
        }
        if is_deferred_tool_topology(payload) {
            context
                .logger
                .debug("realtime generation: deferring subagent topology to Stop");
            return Ok(());
        }

        let session_id = extract_session_id(payload);
        let tool_use_id = payload
            .get("tool_use_id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or("");
        let transcript_path = extract_transcript_path(payload);
        if session_id.is_empty() || tool_use_id.is_empty() || transcript_path.is_empty() {
            context
                .logger
                .debug("realtime generation: missing session_id, tool_use_id, or transcript_path");
            return Ok(());
        }
        let cwd = payload.get("cwd").and_then(Value::as_str).unwrap_or("");
        let config = context
            .langfuse_config
            .as_ref()
            .ok_or_else(|| HandlerError::from("Missing Langfuse configuration".to_string()))?;

        let state_store = SessionStateStore::new(
            context.state_file.clone(),
            &session_id,
            REALTIME_GENERATION_LOCK_TIMEOUT_MS,
        );
        let (mut generation_state, known_contexts) = match state_store.update(|state| {
            let generation_state = ensure_generation_state(
                &context.state_file,
                state,
                &session_id,
                context.realtime_llm_batch_size,
                DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
            )
            .map_err(StateTxnError::Schema)?;
            Ok((generation_state, contexts(state, &session_id)))
        }) {
            Ok(result) => result,
            Err(StateTxnError::Busy { .. }) => {
                context.logger.warn(
                    "realtime generation: state lock unavailable; leaving Stop to compensate",
                );
                return Ok(());
            }
            Err(error) => return Err(HandlerError::from(error.to_string())),
        };

        if known_contexts.is_empty() {
            return Ok(());
        }
        let transcript = Path::new(&transcript_path);
        let prefix_hint = generation_state
            .source_epoch
            .as_ref()
            .map(|epoch| epoch.prefix_len);
        let snapshot = inspect_transcript_source(transcript, prefix_hint)
            .or_else(|_| inspect_transcript_source(transcript, None))
            .map_err(HandlerError::from)?;
        if generation_state
            .source_epoch
            .as_ref()
            .is_some_and(|known| known != &snapshot.epoch)
        {
            let reset = state_store.update(|state| {
                checkpoint_generation_discovery(
                    state,
                    &session_id,
                    generation_state.discovery_offset,
                    0,
                    snapshot.file_len,
                    &snapshot.epoch,
                )
                .map_err(StateTxnError::Schema)
            });
            if reset.is_err() {
                context.logger.warn(
                    "realtime generation: source epoch reset lost a state race; retrying later",
                );
                return Ok(());
            }
            generation_state = state_store
                .read(|state| {
                    let mut state = state.clone();
                    ensure_generation_state(
                        &context.state_file,
                        &mut state,
                        &session_id,
                        context.realtime_llm_batch_size,
                        DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
                    )
                })
                .map_err(|error| HandlerError::from(error.to_string()))?
                .map_err(HandlerError::from)?;
        }
        if let Some(reason) = generation_state.scan_terminal_reason.as_deref() {
            context.logger.debug(&format!(
                "realtime generation: source quarantined reason={reason}; deferring to Stop"
            ));
            return Ok(());
        }

        let discovery_start = generation_state.discovery_offset;
        let scan = read_bounded_jsonl_records(
            transcript,
            discovery_start,
            self.policy.max_bytes,
            self.policy.budget,
        )
        .map_err(HandlerError::from)?;
        let (next_discovery, scanned_records) = match scan {
            BoundedJsonlScan::Ready {
                records,
                next_offset,
            } => {
                if let Some(last) = records.last() {
                    debug_assert_eq!(last.end_offset, next_offset);
                }
                (next_offset, records.len())
            }
            BoundedJsonlScan::CaughtUp { next_offset } => (next_offset, 0),
            BoundedJsonlScan::WaitingForMoreData { record_start } => (record_start, 0),
            BoundedJsonlScan::Terminal {
                reason,
                record_start,
                observed_bytes,
            } => {
                state_store
                    .update(|state| {
                        mark_generation_scan_terminal(
                            state,
                            &session_id,
                            snapshot.file_len,
                            &snapshot.epoch,
                            reason,
                        )
                        .map_err(StateTxnError::Schema)
                    })
                    .map_err(|error| HandlerError::from(error.to_string()))?;
                context.logger.warn(&format!(
                    "realtime generation: terminal transcript scan reason={reason} record_start={record_start} observed_bytes={observed_bytes}; deferring to Stop"
                ));
                return Ok(());
            }
        };
        let checkpointed = state_store
            .update(|state| {
                checkpoint_generation_discovery(
                    state,
                    &session_id,
                    discovery_start,
                    next_discovery,
                    snapshot.file_len,
                    &snapshot.epoch,
                )
                .map_err(StateTxnError::Schema)
            })
            .map_err(|error| HandlerError::from(error.to_string()))?;
        if !checkpointed {
            context.logger.debug(
                "realtime generation: discovery cursor changed concurrently; retrying later",
            );
            return Ok(());
        }
        context.logger.info(&format!(
            "realtime generation scan: discovery_start={discovery_start} discovery_next={next_discovery} scanned_records={scanned_records} file_len={} backlog_bytes={}",
            snapshot.file_len,
            snapshot.file_len.saturating_sub(next_discovery.max(0) as u64)
        ));
        let verified = inspect_transcript_source(transcript, Some(snapshot.epoch.prefix_len))
            .map_err(HandlerError::from)?;
        if verified.epoch != snapshot.epoch || verified.file_len < next_discovery as u64 {
            state_store
                .update(|state| {
                    mark_generation_scan_terminal(
                        state,
                        &session_id,
                        verified.file_len,
                        &snapshot.epoch,
                        "transcript_replaced_or_truncated",
                    )
                    .map_err(StateTxnError::Schema)
                })
                .map_err(|error| HandlerError::from(error.to_string()))?;
            return Ok(());
        }
        let semantic_scan = read_bounded_jsonl_records(
            transcript,
            generation_state.scan_anchor,
            self.policy.max_bytes,
            self.policy.budget,
        )
        .map_err(HandlerError::from)?;
        let (mut semantic_records, semantic_scan_end) = match semantic_scan {
            BoundedJsonlScan::Ready {
                records,
                next_offset,
            } => (records, next_offset),
            BoundedJsonlScan::CaughtUp { next_offset } => (Vec::new(), next_offset),
            BoundedJsonlScan::WaitingForMoreData { record_start } => (Vec::new(), record_start),
            BoundedJsonlScan::Terminal {
                reason,
                record_start,
                observed_bytes,
            } => {
                state_store
                    .update(|state| {
                        mark_generation_scan_terminal(
                            state,
                            &session_id,
                            verified.file_len,
                            &snapshot.epoch,
                            reason,
                        )
                        .map_err(StateTxnError::Schema)
                    })
                    .map_err(|state_error| HandlerError::from(state_error.to_string()))?;
                context.logger.warn(&format!(
                    "realtime generation: terminal semantic record reason={reason} record_start={record_start} observed_bytes={observed_bytes}; deferring to Stop"
                ));
                return Ok(());
            }
        };
        semantic_records.retain(|record| record.end_offset <= next_discovery);
        let semantic_next = semantic_records
            .last()
            .map(|record| record.end_offset)
            .unwrap_or_else(|| semantic_scan_end.min(next_discovery));
        if semantic_records.is_empty() {
            return Ok(());
        }
        let messages: Vec<Value> = semantic_records
            .iter()
            .map(|record| record.value.clone())
            .collect();
        let turns = build_turns(&messages, None);
        let (candidates, locations, current_seen) =
            discover_candidates(&turns, &known_contexts, tool_use_id);
        let last_user_anchor = semantic_records
            .iter()
            .rev()
            .find(|record| {
                get_role(&record.value) == "user"
                    && !is_tool_result(&record.value)
                    && !record
                        .value
                        .get("isMeta")
                        .and_then(Value::as_bool)
                        .unwrap_or(false)
            })
            .map(|record| record.start_offset);
        if !current_seen || candidates.is_empty() {
            if !current_seen {
                let next_anchor = last_user_anchor.unwrap_or(semantic_next);
                if next_anchor > generation_state.scan_anchor {
                    state_store
                        .update(|latest_state| {
                            ensure_generation_state(
                                &context.state_file,
                                latest_state,
                                &session_id,
                                context.realtime_llm_batch_size,
                                DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
                            )
                            .map_err(StateTxnError::Schema)?;
                            claim_generation_prefix(
                                &context.state_file,
                                latest_state,
                                &session_id,
                                generation_state.scan_anchor,
                                next_anchor,
                                semantic_next,
                                &[],
                                now_millis(),
                                self.policy.lease_ms,
                                false,
                            )
                            .map(|_| ())
                            .map_err(StateTxnError::Schema)
                        })
                        .map_err(|error| HandlerError::from(error.to_string()))?;
                }
            }
            context.logger.debug(
                "realtime generation: current tool generation is not terminal in transcript",
            );
            return Ok(());
        }
        let next_anchor = last_user_anchor.unwrap_or(generation_state.scan_anchor);

        let (claim, timing, vcs_info) = match state_store.update(|latest_state| {
            ensure_generation_state(
                &context.state_file,
                latest_state,
                &session_id,
                context.realtime_llm_batch_size,
                DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
            )
            .map_err(StateTxnError::Schema)?;
            let claim = claim_generation_prefix(
                &context.state_file,
                latest_state,
                &session_id,
                generation_state.scan_anchor,
                next_anchor,
                semantic_next,
                &candidates,
                now_millis(),
                self.policy.lease_ms,
                false,
            )
            .map_err(StateTxnError::Schema)?;
            Ok((
                claim,
                load_timing_from_state(latest_state, &session_id),
                get_vcs_for_cwd_from_state(latest_state, &session_id, cwd),
            ))
        }) {
            Ok(result) => result,
            Err(StateTxnError::Busy { .. }) => {
                context.logger.warn(
                    "realtime generation: claim lock unavailable; leaving generations pending",
                );
                return Ok(());
            }
            Err(error) => return Err(HandlerError::from(error.to_string())),
        };

        let Some(claim) = claim else {
            return Ok(());
        };
        let mut inputs = Vec::with_capacity(claim.generations.len());
        for generation in &claim.generations {
            let Some((turn_index, assistant_index)) = locations.get(&generation.message_id) else {
                release_claim_after_local_failure(context, &session_id, &claim.claim_id);
                return Ok(());
            };
            inputs.push(RealtimeGenerationInput {
                turn: &turns[*turn_index],
                turn_index: *turn_index,
                assistant_index: *assistant_index,
                trace_id: &generation.trace_id,
                root_span_id: &generation.root_span_id,
                span_id: &generation.span_id,
            });
        }
        let bodies = match build_generation_request_chunks(
            &inputs,
            claim.batch_size,
            &session_id,
            &context.username,
            &context.hook_version,
            &timing,
            vcs_info.as_ref(),
            self.policy.max_bytes,
            MAX_REALTIME_GENERATION_REQUEST_CHUNKS,
        ) {
            Ok(bodies) => bodies,
            Err(error) => {
                release_claim_after_local_failure(context, &session_id, &claim.claim_id);
                context
                    .logger
                    .error(&format!("Realtime generation build failed: {error}"));
                return Ok(());
            }
        };
        for chunk in &bodies {
            let payload_digest = match otlp_payload_digest(&chunk.body) {
                Ok(digest) => digest,
                Err(error) => {
                    release_claim_after_local_failure(context, &session_id, &claim.claim_id);
                    context.logger.error(&format!(
                        "Realtime generation payload digest failed before POST: {error}"
                    ));
                    return Ok(());
                }
            };
            let child_keys = chunk
                .message_ids
                .iter()
                .filter_map(|message_id| {
                    claim
                        .generations
                        .iter()
                        .find(|generation| generation.message_id == *message_id)
                        .map(|generation| generation_child_key(&generation.context_id, message_id))
                })
                .collect::<Vec<_>>();
            let prepared = state_store.update(|latest_state| {
                let terminal_message_ids = chunk
                    .message_ids
                    .iter()
                    .filter_map(|message_id| {
                        claim
                            .generations
                            .iter()
                            .find(|generation| generation.message_id == *message_id)
                            .map(|generation| {
                                (
                                    message_id,
                                    generation_child_key(&generation.context_id, message_id),
                                )
                            })
                    })
                    .filter(|(_, child_key)| {
                        child_delivery_is_terminal(latest_state, &session_id, child_key)
                    })
                    .map(|(message_id, _)| message_id.clone())
                    .collect::<Vec<_>>();
                if !terminal_message_ids.is_empty() {
                    return Ok(Some(terminal_message_ids));
                }
                for message_id in &chunk.message_ids {
                    let generation = claim
                        .generations
                        .iter()
                        .find(|generation| generation.message_id == *message_id)
                        .ok_or_else(|| {
                            StateTxnError::Schema(format!(
                                "claimed generation is missing from chunk: {message_id}"
                            ))
                        })?;
                    let child_key = generation_child_key(&generation.context_id, message_id);
                    if !prepare_child_delivery(
                        latest_state,
                        &session_id,
                        ChildDeliverySpec {
                            child_key: &child_key,
                            kind: "generation",
                            context_id: &generation.context_id,
                            turn_evidence: serde_json::json!({
                                "message_id": message_id,
                                "ordinal": generation.ordinal,
                            }),
                            trace_id: &generation.trace_id,
                            root_span_id: &generation.root_span_id,
                            span_id: &generation.span_id,
                            end_time_unix_nano: otlp_span_end_time_unix_nano(
                                &chunk.body,
                                &generation.span_id,
                            ),
                        },
                        context.retry_policy,
                        &claim.claim_id,
                        &payload_digest,
                        &now_iso8601_pub(),
                    )
                    .map_err(StateTxnError::Schema)?
                    {
                        return Err(StateTxnError::Schema(format!(
                            "generation child became terminal during one state transaction: {child_key}"
                        )));
                    }
                }
                Ok(None)
            });
            match prepared {
                Ok(None) => {}
                Ok(Some(terminal_message_ids)) => {
                    if let Err(error) = checkpoint_generation_chunk(
                        &context.state_file,
                        &session_id,
                        &claim.claim_id,
                        &terminal_message_ids,
                    ) {
                        release_claim_after_local_failure(context, &session_id, &claim.claim_id);
                        context.logger.error(&format!(
                            "Realtime generation terminal delivery checkpoint failed: {error}"
                        ));
                        return Ok(());
                    }
                    release_claim_after_local_failure(context, &session_id, &claim.claim_id);
                    context.logger.debug(&format!(
                        "Realtime generation delivery already terminal; accounted terminal subset without POST claim_id={} message_ids={terminal_message_ids:?}",
                        claim.claim_id
                    ));
                    return Ok(());
                }
                Err(error) => {
                    release_claim_after_local_failure(context, &session_id, &claim.claim_id);
                    context.logger.error(&format!(
                        "Realtime generation delivery checkpoint failed before POST: {error}"
                    ));
                    return Ok(());
                }
            }
            if context.retry_policy == RetryPolicy::AtMostOnce {
                if let Err(error) = checkpoint_generation_chunk(
                    &context.state_file,
                    &session_id,
                    &claim.claim_id,
                    &chunk.message_ids,
                ) {
                    release_claim_after_local_failure(context, &session_id, &claim.claim_id);
                    context.logger.error(&format!(
                        "Realtime generation at-most-once checkpoint failed before POST: {error}"
                    ));
                    return Ok(());
                }
            }
            let result = post_otel_to_langfuse_once_with_context(
                config,
                &chunk.body,
                &context.logger,
                false,
                &session_id,
                &transcript_path,
                REALTIME_GENERATION_HTTP_TIMEOUT,
                LangfuseAttemptContext::realtime_generation(&claim.claim_id, &chunk.message_ids),
            );
            if let Err(error) = result {
                let error = error.to_string();
                let _ = state_store.update(|latest_state| {
                    finish_child_deliveries(
                        latest_state,
                        &session_id,
                        &child_keys,
                        context.retry_policy,
                        false,
                        &now_iso8601_pub(),
                        Some(&error),
                    )
                    .map_err(StateTxnError::Schema)
                });
                release_claim_after_local_failure(context, &session_id, &claim.claim_id);
                context
                    .logger
                    .error(&format!("Realtime generation export failed: {error}"));
                if let Some(metrics) = context.metrics.as_ref() {
                    metrics.borrow_mut().inc(
                        "aivision.agentic.export_error",
                        "Export error",
                        "{count}",
                        vec![OtelAttribute::string("error.type", &error)],
                    );
                }
                return Ok(());
            }

            if context.retry_policy == RetryPolicy::AtLeastOnce {
                if let Err(error) = checkpoint_generation_chunk(
                    &context.state_file,
                    &session_id,
                    &claim.claim_id,
                    &chunk.message_ids,
                ) {
                    release_claim_after_local_failure(context, &session_id, &claim.claim_id);
                    context.logger.error(&format!(
                        "Realtime generation chunk accepted but checkpoint failed; Stop may compensate: {error}"
                    ));
                    return Ok(());
                }
            }
            if let Err(error) = state_store.update(|latest_state| {
                finish_child_deliveries(
                    latest_state,
                    &session_id,
                    &child_keys,
                    context.retry_policy,
                    true,
                    &now_iso8601_pub(),
                    None,
                )
                .map_err(StateTxnError::Schema)
            }) {
                context.logger.error(&format!(
                    "Realtime generation ledger checkpoint failed after accepted POST: {error}"
                ));
                return Ok(());
            }
        }
        context.logger.info(&format!(
            "Realtime generation export accepted rounds={} batch_size={} requests={}",
            claim.generations.len(),
            claim.batch_size,
            bodies.len()
        ));
        Ok(())
    }
}

struct GenerationRequestChunk {
    body: Value,
    body_len: usize,
    message_ids: Vec<String>,
}

#[derive(Default)]
struct ByteCounter(usize);

impl std::io::Write for ByteCounter {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0 = self.0.saturating_add(bytes.len());
        Ok(bytes.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

fn serialized_json_len(value: &Value) -> Result<usize, String> {
    let mut counter = ByteCounter::default();
    serde_json::to_writer(&mut counter, value)
        .map_err(|error| format!("measure realtime generation request: {error}"))?;
    Ok(counter.0)
}

fn build_generation_request_chunk(
    inputs: &[RealtimeGenerationInput<'_>],
    session_id: &str,
    username: &str,
    hook_version: &str,
    timing: &crate::hooks::timing::TimingData,
    vcs_info: Option<&VcsInfo>,
) -> Result<GenerationRequestChunk, String> {
    let mut body = build_realtime_generation_export_request(
        inputs,
        session_id,
        username,
        hook_version,
        timing,
    )?;
    if let Some(vcs) = vcs_info {
        inject_vcs_resource_attrs(&mut body, &vcs.remote_url, &vcs.branch);
    }
    let message_ids = inputs
        .iter()
        .map(|input| {
            input
                .turn
                .assistants
                .get(input.assistant_index)
                .map(|assistant| assistant.message_id.clone())
                .ok_or_else(|| "realtime generation assistant index is out of bounds".to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;
    let body_len = serialized_json_len(&body)?;
    Ok(GenerationRequestChunk {
        body,
        body_len,
        message_ids,
    })
}

#[allow(clippy::too_many_arguments)]
fn build_generation_request_chunks(
    inputs: &[RealtimeGenerationInput<'_>],
    batch_size: usize,
    session_id: &str,
    username: &str,
    hook_version: &str,
    timing: &crate::hooks::timing::TimingData,
    vcs_info: Option<&VcsInfo>,
    max_body_bytes: usize,
    max_chunks: usize,
) -> Result<Vec<GenerationRequestChunk>, String> {
    let full = build_generation_request_chunk(
        inputs,
        session_id,
        username,
        hook_version,
        timing,
        vcs_info,
    )?;
    if full.body_len <= max_body_bytes {
        return Ok(vec![full]);
    }
    if batch_size == 0 || !inputs.len().is_multiple_of(batch_size) || max_chunks == 0 {
        return Err("realtime generation claim cannot be split into full batches".to_string());
    }

    let logical_batches = inputs.len() / batch_size;
    for chunk_count in 2..=logical_batches.min(max_chunks) {
        let batches_per_chunk = logical_batches.div_ceil(chunk_count);
        let inputs_per_chunk = batches_per_chunk * batch_size;
        let mut bodies = Vec::new();
        let mut fits = true;
        for chunk in inputs.chunks(inputs_per_chunk) {
            let body = build_generation_request_chunk(
                chunk,
                session_id,
                username,
                hook_version,
                timing,
                vcs_info,
            )?;
            if body.body_len > max_body_bytes {
                fits = false;
                break;
            }
            bodies.push(body);
        }
        if fits && bodies.len() <= max_chunks {
            return Ok(bodies);
        }
    }
    Err(format!(
        "realtime generation payload exceeds {max_body_bytes} bytes and cannot be split into at most {max_chunks} full-batch requests"
    ))
}

fn discover_candidates(
    turns: &[Turn],
    contexts: &[RealtimeContext],
    current_tool_use_id: &str,
) -> (
    Vec<GenerationCandidate>,
    HashMap<String, (usize, usize)>,
    bool,
) {
    let mut candidates = Vec::new();
    let mut locations = HashMap::new();
    let mut seen = HashSet::new();
    let mut current_seen = false;
    for (turn_index, turn) in turns.iter().enumerate() {
        for (assistant_index, assistant) in turn.assistants.iter().enumerate() {
            if !is_realtime_terminal_generation(assistant)
                || !seen.insert(assistant.message_id.clone())
            {
                continue;
            }
            let tool_ids: Vec<String> = assistant
                .tool_uses
                .iter()
                .map(|tool| tool.id.clone())
                .filter(|id| !id.is_empty())
                .collect();
            let Some(context) = contexts.iter().find(|context| {
                tool_ids
                    .iter()
                    .any(|id| context.tool_use_ids.iter().any(|known| known == id))
            }) else {
                continue;
            };
            if tool_ids.iter().any(|id| id == current_tool_use_id) {
                current_seen = true;
            }
            locations.insert(assistant.message_id.clone(), (turn_index, assistant_index));
            candidates.push(GenerationCandidate {
                message_id: assistant.message_id.clone(),
                context_id: context.context_id.clone(),
                correlated_tool_use_ids: tool_ids,
            });
        }
    }
    (candidates, locations, current_seen)
}

fn is_deferred_tool_topology(payload: &Value) -> bool {
    payload
        .get("agent_id")
        .and_then(Value::as_str)
        .map(|value| !value.is_empty())
        .unwrap_or(false)
        || payload
            .get("tool_name")
            .and_then(Value::as_str)
            .is_some_and(is_subagent_orchestration_tool)
}

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn checkpoint_generation_chunk(
    state_file: &Path,
    session_id: &str,
    claim_id: &str,
    message_ids: &[String],
) -> Result<(), String> {
    let store = SessionStateStore::new(
        state_file.to_path_buf(),
        session_id,
        REALTIME_GENERATION_LOCK_TIMEOUT_MS,
    );
    store
        .update(|checkpoint_state| {
            finish_generation_claim(
                state_file,
                checkpoint_state,
                session_id,
                claim_id,
                GenerationClaimUpdate::Account(message_ids),
            )
            .map_err(StateTxnError::Schema)
        })
        .map_err(|error| error.to_string())
}

fn release_claim_after_local_failure(context: &CommonContext, session_id: &str, claim_id: &str) {
    let store = SessionStateStore::new(
        context.state_file.clone(),
        session_id,
        REALTIME_GENERATION_LOCK_TIMEOUT_MS,
    );
    let _ = store.update(|state| {
        finish_generation_claim(
            &context.state_file,
            state,
            session_id,
            claim_id,
            GenerationClaimUpdate::Release,
        )
        .map_err(StateTxnError::Schema)
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use agentic_otel_common::state::{load_state, save_state, StateLock};
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};

    use crate::common::logger::Logger;
    use crate::common::transcript::{AssistantMessage, TokenUsage, ToolUse};
    use crate::hooks::langfuse::realtime_state::{claim_tool, stable_generation_span_id};
    use crate::hooks::langfuse::LangfuseConfig;
    use crate::hooks::ExportMode;
    use serde_json::json;

    fn assistant(message_id: &str, tool_ids: &[&str]) -> AssistantMessage {
        AssistantMessage {
            timestamp: "2026-01-01T00:00:00Z".to_string(),
            end_timestamp: "2026-01-01T00:00:01Z".to_string(),
            text: String::new(),
            media_content: serde_json::Value::Null,
            model: "claude".to_string(),
            usage: TokenUsage {
                input: Some(1),
                output: Some(1),
                cache_creation_input_tokens: None,
                cache_read_input_tokens: None,
            },
            tool_uses: tool_ids
                .iter()
                .map(|id| ToolUse {
                    name: "Read".to_string(),
                    id: (*id).to_string(),
                    input: json!({}),
                })
                .collect(),
            message_id: message_id.to_string(),
            stop_reason: "tool_use".to_string(),
            thinking_blocks: Vec::new(),
            is_synthetic: false,
        }
    }

    #[test]
    fn parallel_tool_hooks_count_as_one_message_round() {
        let turn = Turn {
            chat_id: None,
            trigger: Default::default(),
            user_timestamp: String::new(),
            user_text: "test".to_string(),
            user_content: serde_json::Value::Null,
            assistants: vec![assistant("msg-1", &["tool-1", "tool-2"])],
            tool_results: Vec::new(),
            skill_name: None,
            api_errors: Vec::new(),
            terminal_llm_error: None,
            compactions: Vec::new(),
            cancelled: false,
            task_notifications: Vec::new(),
        };
        let context = RealtimeContext {
            context_id: "ctx".to_string(),
            trace_id: "a".repeat(32),
            root_span_id: "b".repeat(16),
            traceparent: None,
            sofa_traceid: None,
            sofa_rpcid: None,
            tool_use_ids: vec!["tool-1".to_string(), "tool-2".to_string()],
            tool_span_ids: HashMap::new(),
            emitted_tool_use_ids: Vec::new(),
            failed_tool_exports: HashMap::new(),
            generation_span_ids: HashMap::new(),
            accounted_generation_ids: Vec::new(),
            created_at: "now".to_string(),
        };
        let (candidates, locations, current_seen) =
            discover_candidates(&[turn], &[context], "tool-2");
        assert!(current_seen);
        assert_eq!(candidates.len(), 1);
        assert_eq!(locations.len(), 1);
        assert_eq!(
            stable_generation_span_id("msg-1"),
            stable_generation_span_id(&candidates[0].message_id)
        );
    }

    #[test]
    fn internal_policy_defaults_are_bounded_and_lease_outlives_http() {
        let policy = ScanPolicy::default();
        assert_eq!(policy.max_bytes, 1024 * 1024);
        assert_eq!(policy.budget, Duration::from_millis(100));
        assert!(policy.lease_ms > REALTIME_GENERATION_HTTP_TIMEOUT.as_millis() as u64);
    }

    fn read_request(stream: &mut TcpStream) -> Value {
        // Accepted sockets may inherit the listener's nonblocking mode.
        stream.set_nonblocking(false).expect("set blocking test stream");
        stream
            .set_read_timeout(Some(Duration::from_secs(1)))
            .expect("set read timeout");
        let mut bytes = Vec::new();
        let mut buffer = [0_u8; 4096];
        let header_end = loop {
            let read = stream.read(&mut buffer).unwrap();
            assert!(read > 0);
            bytes.extend_from_slice(&buffer[..read]);
            if let Some(position) = bytes.windows(4).position(|part| part == b"\r\n\r\n") {
                break position + 4;
            }
        };
        let headers = String::from_utf8_lossy(&bytes[..header_end]);
        let content_length = headers
            .lines()
            .find_map(|line| {
                line.split_once(':').and_then(|(name, value)| {
                    name.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().unwrap())
                })
            })
            .unwrap();
        while bytes.len() < header_end + content_length {
            let read = stream.read(&mut buffer).unwrap();
            assert!(read > 0);
            bytes.extend_from_slice(&buffer[..read]);
        }
        serde_json::from_slice(&bytes[header_end..header_end + content_length]).unwrap()
    }

    fn spawn_sink() -> (std::net::SocketAddr, std::thread::JoinHandle<Value>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let request = read_request(&mut stream);
            stream
                .write_all(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
                .unwrap();
            request
        });
        (address, server)
    }

    fn spawn_probe_sink() -> (std::net::SocketAddr, std::thread::JoinHandle<Option<Value>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let deadline = std::time::Instant::now() + Duration::from_millis(200);
            loop {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let request = read_request(&mut stream);
                        stream
                            .write_all(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
                            .unwrap();
                        return Some(request);
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        if std::time::Instant::now() >= deadline {
                            return None;
                        }
                        std::thread::sleep(Duration::from_millis(10));
                    }
                    Err(error) => panic!("probe sink accept failed: {error}"),
                }
            }
        });
        (address, server)
    }

    fn test_context(
        dir: &tempfile::TempDir,
        address: std::net::SocketAddr,
        batch_size: usize,
    ) -> CommonContext {
        CommonContext {
            logger: Logger::new(dir.path().join("generation.log"), true),
            state_file: dir.path().join("state.json"),
            langfuse_config: Some(LangfuseConfig {
                public_key: None,
                secret_key: None,
                host: format!("http://{address}"),
                headers: None,
            }),
            username: "user".to_string(),
            projects_dir: String::new(),
            debug_enabled: false,
            hook_version: "test".to_string(),
            turn_seed: None,
            stop_transcript_end_offset: None,
            transcript_ingest_receipt: None,
            stop_budget: None,
            retry_policy: RetryPolicy::AtLeastOnce,
            export_mode: ExportMode::RealtimeWithStopFinalizer,
            realtime_llm_batch_size: batch_size,
            metrics: None,
            send_queue: std::cell::RefCell::new(Vec::new()),
            send_errors: std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
            stop_outcome: std::cell::RefCell::new(crate::hooks::StopOutcome::IntentionallySkipped),
        }
    }

    fn transcript(rounds: usize) -> String {
        let mut records = vec![json!({
            "type": "user",
            "uuid": "chat-1",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "user", "content": "run tools"}
        })];
        for index in 1..=rounds {
            records.push(json!({
                "type": "assistant",
                "timestamp": format!("2026-01-01T00:00:0{}Z", index),
                "message": {
                    "id": format!("msg-{index}"),
                    "role": "assistant",
                    "model": "claude-sonnet-4",
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": index, "output_tokens": 1},
                    "content": [{
                        "type": "tool_use",
                        "id": format!("tool-{index}"),
                        "name": "Read",
                        "input": {"file_path": format!("/tmp/{index}")}
                    }]
                }
            }));
            records.push(json!({
                "type": "user",
                "timestamp": format!("2026-01-01T00:00:1{}Z", index),
                "message": {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": format!("tool-{index}"),
                        "content": "ok"
                    }]
                }
            }));
        }
        records
            .into_iter()
            .map(|record| record.to_string())
            .collect::<Vec<_>>()
            .join("\n")
            + "\n"
    }

    fn multi_turn_transcript(rounds: usize) -> String {
        let mut records = Vec::new();
        for index in 1..=rounds {
            records.push(json!({
                "type": "user",
                "uuid": format!("chat-{index}"),
                "timestamp": format!("2026-01-01T00:{index:02}:00Z"),
                "message": {"role": "user", "content": "x".repeat(512)}
            }));
            records.push(json!({
                "type": "assistant",
                "timestamp": format!("2026-01-01T00:{index:02}:01Z"),
                "message": {
                    "id": format!("msg-{index}"),
                    "role": "assistant",
                    "model": "claude-sonnet-4",
                    "stop_reason": "tool_use",
                    "content": [{
                        "type": "tool_use",
                        "id": format!("tool-{index}"),
                        "name": "Read",
                        "input": {"file_path": format!("/tmp/{index}")}
                    }],
                    "usage": {"input_tokens": 10, "output_tokens": 5}
                }
            }));
            records.push(json!({
                "type": "user",
                "timestamp": format!("2026-01-01T00:{index:02}:02Z"),
                "message": {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": format!("tool-{index}"),
                        "content": "ok"
                    }]
                }
            }));
        }
        records
            .into_iter()
            .map(|record| record.to_string())
            .collect::<Vec<_>>()
            .join("\n")
            + "\n"
    }

    fn payload(path: &Path, index: usize) -> Value {
        json!({
            "session_id": "session-1",
            "hook_event_name": "PostToolUse",
            "transcript_path": path,
            "tool_name": "Read",
            "tool_use_id": format!("tool-{index}"),
            "tool_input": {"file_path": format!("/tmp/{index}")},
            "tool_response": {"content": "ok"}
        })
    }

    #[test]
    fn third_distinct_round_posts_one_request_with_three_generation_spans() {
        let (address, server) = spawn_sink();
        let dir = tempfile::tempdir().unwrap();
        let context = test_context(&dir, address, 3);
        let transcript_path = dir.path().join("session.jsonl");
        let handler = RealtimeGenerationHandler::new();

        for index in 1..=3 {
            let lock = StateLock::acquire(&context.state_file, 100);
            assert!(lock.is_acquired());
            let mut state = load_state(&context.state_file);
            claim_tool(
                &context.state_file,
                &mut state,
                "session-1",
                &format!("tool-{index}"),
                "now",
                None,
            )
            .unwrap();
            save_state(&context.state_file, &state).unwrap();
            drop(lock);
            std::fs::write(&transcript_path, transcript(index)).unwrap();
            handler
                .handle(
                    &HookEvent::PostToolUse,
                    &payload(&transcript_path, index),
                    &context,
                )
                .unwrap();
        }

        let request = server.join().unwrap();
        let spans = request["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .unwrap();
        assert_eq!(spans.len(), 3);
        assert!(spans.iter().all(|span| {
            span["attributes"]
                .as_array()
                .unwrap()
                .iter()
                .any(|attribute| {
                    attribute["key"] == "gen_ai.span.kind"
                        && attribute["value"]["stringValue"] == "LLM"
                })
        }));
        let state = load_state(&context.state_file);
        assert_eq!(
            state["session-1"]["realtime_export"]["current"]["accounted_generation_ids"],
            json!(["msg-1", "msg-2", "msg-3"])
        );
    }

    #[test]
    fn long_session_advances_semantic_anchor_in_bounded_windows_without_quarantine() {
        let (address, server) = spawn_sink();
        let dir = tempfile::tempdir().unwrap();
        let context = test_context(&dir, address, 1);
        let transcript_path = dir.path().join("long-session.jsonl");
        let transcript = multi_turn_transcript(40);
        assert!(transcript.len() > 20_000);
        std::fs::write(&transcript_path, &transcript).unwrap();

        let lock = StateLock::acquire(&context.state_file, 100);
        assert!(lock.is_acquired());
        let mut state = load_state(&context.state_file);
        claim_tool(
            &context.state_file,
            &mut state,
            "session-1",
            "tool-40",
            "now",
            None,
        )
        .unwrap();
        save_state(&context.state_file, &state).unwrap();
        drop(lock);

        let handler = RealtimeGenerationHandler {
            policy: ScanPolicy {
                max_bytes: 16 * 1024,
                budget: Duration::from_millis(100),
                lease_ms: DEFAULT_REALTIME_LLM_CLAIM_LEASE_MS,
            },
        };
        let mut accounted = false;
        for _ in 0..100 {
            handler
                .handle(
                    &HookEvent::PostToolUse,
                    &payload(&transcript_path, 40),
                    &context,
                )
                .unwrap();
            let state = load_state(&context.state_file);
            if contexts(&state, "session-1")[0]
                .accounted_generation_ids
                .contains(&"msg-40".to_string())
            {
                accounted = true;
                break;
            }
        }

        let pre_request_state = load_state(&context.state_file);
        let diagnostic_log =
            std::fs::read_to_string(dir.path().join("generation.log")).unwrap_or_default();
        assert!(
            accounted,
            "long-session generation was not accounted: {}\n{}",
            pre_request_state["session-1"]["realtime_export"]["generation"], diagnostic_log,
        );
        let request = server.join().unwrap();
        let spans = request["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .unwrap();
        assert_eq!(spans.len(), 1);
        let state = load_state(&context.state_file);
        let generation = &state["session-1"]["realtime_export"]["generation"];
        assert!(generation["scan_anchor"].as_i64().unwrap() > 16 * 1024);
        assert!(generation["scan_terminal_reason"].is_null());
        assert_eq!(
            contexts(&state, "session-1")[0].accounted_generation_ids,
            vec!["msg-40".to_string()]
        );
    }

    #[test]
    fn oversized_claim_splits_only_on_full_batch_boundaries() {
        let turn = Turn {
            chat_id: Some("chat-1".to_string()),
            trigger: Default::default(),
            user_timestamp: "2026-01-01T00:00:00Z".to_string(),
            user_text: "test".repeat(64),
            user_content: serde_json::Value::Null,
            assistants: (1..=6)
                .map(|index| assistant(&format!("msg-{index}"), &[&format!("tool-{index}")]))
                .collect(),
            tool_results: Vec::new(),
            skill_name: None,
            api_errors: Vec::new(),
            terminal_llm_error: None,
            compactions: Vec::new(),
            cancelled: false,
            task_notifications: Vec::new(),
        };
        let trace_id = "a".repeat(32);
        let root_span_id = "b".repeat(16);
        let span_ids: Vec<String> = (1..=6).map(|index| format!("{index:016x}")).collect();
        let inputs: Vec<RealtimeGenerationInput<'_>> = (0..6)
            .map(|assistant_index| RealtimeGenerationInput {
                turn: &turn,
                turn_index: 0,
                assistant_index,
                trace_id: &trace_id,
                root_span_id: &root_span_id,
                span_id: &span_ids[assistant_index],
            })
            .collect();
        let timing = crate::hooks::timing::TimingData::new();
        let first_size = build_realtime_generation_export_request(
            &inputs[..3],
            "session-1",
            "user",
            "test",
            &timing,
        )
        .unwrap()
        .to_string()
        .len();
        let second_size = build_realtime_generation_export_request(
            &inputs[3..],
            "session-1",
            "user",
            "test",
            &timing,
        )
        .unwrap()
        .to_string()
        .len();
        let chunks = build_generation_request_chunks(
            &inputs,
            3,
            "session-1",
            "user",
            "test",
            &timing,
            None,
            first_size.max(second_size),
            4,
        )
        .unwrap();
        assert_eq!(chunks.len(), 2);
        assert!(chunks
            .iter()
            .all(|chunk| { chunk.body_len == serde_json::to_vec(&chunk.body).unwrap().len() }));
        assert!(chunks.iter().all(|chunk| {
            chunk.body["resourceSpans"][0]["scopeSpans"][0]["spans"]
                .as_array()
                .unwrap()
                .len()
                == 3
        }));
        assert_eq!(
            chunks
                .iter()
                .map(|chunk| chunk.message_ids.clone())
                .collect::<Vec<_>>(),
            vec![
                vec![
                    "msg-1".to_string(),
                    "msg-2".to_string(),
                    "msg-3".to_string()
                ],
                vec![
                    "msg-4".to_string(),
                    "msg-5".to_string(),
                    "msg-6".to_string()
                ],
            ]
        );
    }

    #[test]
    fn generation_chunk_injects_persisted_vcs_before_measuring_body() {
        let turn = Turn {
            chat_id: Some("chat-1".to_string()),
            trigger: Default::default(),
            user_timestamp: "2026-01-01T00:00:00Z".to_string(),
            user_text: "test".to_string(),
            user_content: serde_json::Value::Null,
            assistants: vec![assistant("msg-1", &["tool-1"])],
            tool_results: Vec::new(),
            skill_name: None,
            api_errors: Vec::new(),
            terminal_llm_error: None,
            compactions: Vec::new(),
            cancelled: false,
            task_notifications: Vec::new(),
        };
        let trace_id = "a".repeat(32);
        let root_span_id = "b".repeat(16);
        let span_id = "c".repeat(16);
        let input = RealtimeGenerationInput {
            turn: &turn,
            turn_index: 0,
            assistant_index: 0,
            trace_id: &trace_id,
            root_span_id: &root_span_id,
            span_id: &span_id,
        };
        let vcs = VcsInfo {
            remote_url: "git@git.example.com:team/repo.git".to_string(),
            branch: "realtime".to_string(),
            cwd: "/repo".to_string(),
        };
        let chunk = build_generation_request_chunk(
            &[input],
            "session-1",
            "user",
            "test",
            &crate::hooks::timing::TimingData::new(),
            Some(&vcs),
        )
        .unwrap();

        assert_eq!(
            chunk.body_len,
            serde_json::to_vec(&chunk.body).unwrap().len()
        );
        let attributes = chunk.body["resourceSpans"][0]["resource"]["attributes"]
            .as_array()
            .unwrap();
        assert!(attributes.iter().any(|attribute| {
            attribute["key"] == "vcs.repository.url.full"
                && attribute["value"]["stringValue"] == "git@git.example.com:team/repo.git"
        }));
        assert!(attributes.iter().any(|attribute| {
            attribute["key"] == "vcs.ref.head.name"
                && attribute["value"]["stringValue"] == "realtime"
        }));
    }

    #[test]
    fn checkpoint_failure_releases_claim_for_immediate_retry() {
        let dir = tempfile::tempdir().unwrap();
        let context = test_context(&dir, "127.0.0.1:1".parse().unwrap(), 1);
        let mut state = json!({});
        let realtime = claim_tool(
            &context.state_file,
            &mut state,
            "session-1",
            "tool-1",
            "now",
            None,
        )
        .unwrap();
        ensure_generation_state(&context.state_file, &mut state, "session-1", 1, 3).unwrap();
        let claim = claim_generation_prefix(
            &context.state_file,
            &mut state,
            "session-1",
            0,
            0,
            10,
            &[GenerationCandidate {
                message_id: "msg-1".to_string(),
                context_id: realtime.context_id,
                correlated_tool_use_ids: vec!["tool-1".to_string()],
            }],
            1,
            5_000,
            false,
        )
        .unwrap()
        .unwrap();
        save_state(&context.state_file, &state).unwrap();

        release_claim_after_local_failure(&context, "session-1", &claim.claim_id);

        let mut retry_state = load_state(&context.state_file);
        let retry = claim_generation_prefix(
            &context.state_file,
            &mut retry_state,
            "session-1",
            0,
            0,
            11,
            &[GenerationCandidate {
                message_id: "msg-1".to_string(),
                context_id: claim.generations[0].context_id.clone(),
                correlated_tool_use_ids: vec!["tool-1".to_string()],
            }],
            2,
            5_000,
            false,
        )
        .unwrap();
        assert!(retry.is_some());
    }

    #[test]
    fn missing_config_does_not_account_generation_before_any_post_attempt() {
        let dir = tempfile::tempdir().unwrap();
        let mut context = test_context(&dir, "127.0.0.1:1".parse().unwrap(), 1);
        context.langfuse_config = None;
        context.retry_policy = RetryPolicy::AtMostOnce;
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(&transcript_path, transcript(1)).unwrap();

        let lock = StateLock::acquire(&context.state_file, 100);
        assert!(lock.is_acquired());
        let mut state = load_state(&context.state_file);
        claim_tool(
            &context.state_file,
            &mut state,
            "session-1",
            "tool-1",
            "now",
            None,
        )
        .unwrap();
        save_state(&context.state_file, &state).unwrap();
        drop(lock);

        let result = RealtimeGenerationHandler::new().handle(
            &HookEvent::PostToolUse,
            &payload(&transcript_path, 1),
            &context,
        );
        assert!(result.is_err());

        let state = load_state(&context.state_file);
        assert!(contexts(&state, "session-1")[0]
            .accounted_generation_ids
            .is_empty());
    }

    #[test]
    fn oversized_at_most_once_preflight_releases_unattempted_generation() {
        let dir = tempfile::tempdir().unwrap();
        let mut context = test_context(&dir, "127.0.0.1:1".parse().unwrap(), 1);
        context.retry_policy = RetryPolicy::AtMostOnce;
        let transcript_path = dir.path().join("session.jsonl");
        let transcript = transcript(1);
        std::fs::write(&transcript_path, &transcript).unwrap();

        let lock = StateLock::acquire(&context.state_file, 100);
        assert!(lock.is_acquired());
        let mut state = load_state(&context.state_file);
        claim_tool(
            &context.state_file,
            &mut state,
            "session-1",
            "tool-1",
            "now",
            None,
        )
        .unwrap();
        save_state(&context.state_file, &state).unwrap();
        drop(lock);

        let handler = RealtimeGenerationHandler {
            policy: ScanPolicy {
                max_bytes: transcript.len(),
                budget: Duration::from_millis(100),
                lease_ms: DEFAULT_REALTIME_LLM_CLAIM_LEASE_MS,
            },
        };
        handler
            .handle(
                &HookEvent::PostToolUse,
                &payload(&transcript_path, 1),
                &context,
            )
            .unwrap();

        let state = load_state(&context.state_file);
        assert!(contexts(&state, "session-1")[0]
            .accounted_generation_ids
            .is_empty());
        assert_eq!(
            state["session-1"]["realtime_export"]["generation"]["rounds"][0]["status"],
            "pending"
        );
    }

    #[test]
    fn terminal_generation_ledger_resume_accounts_without_post_or_lease_wait() {
        let (address, server) = spawn_probe_sink();
        let dir = tempfile::tempdir().unwrap();
        let mut context = test_context(&dir, address, 2);
        context.retry_policy = RetryPolicy::AtMostOnce;
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(&transcript_path, transcript(2)).unwrap();

        let lock = StateLock::acquire(&context.state_file, 100);
        assert!(lock.is_acquired());
        let mut state = load_state(&context.state_file);
        let realtime = claim_tool(
            &context.state_file,
            &mut state,
            "session-1",
            "tool-1",
            "now",
            None,
        )
        .unwrap();
        claim_tool(
            &context.state_file,
            &mut state,
            "session-1",
            "tool-2",
            "later",
            None,
        )
        .unwrap();
        ensure_generation_state(&context.state_file, &mut state, "session-1", 2, 2).unwrap();
        let claim = claim_generation_prefix(
            &context.state_file,
            &mut state,
            "session-1",
            0,
            0,
            0,
            &[
                GenerationCandidate {
                    message_id: "msg-1".to_string(),
                    context_id: realtime.context_id.clone(),
                    correlated_tool_use_ids: vec!["tool-1".to_string()],
                },
                GenerationCandidate {
                    message_id: "msg-2".to_string(),
                    context_id: realtime.context_id.clone(),
                    correlated_tool_use_ids: vec!["tool-2".to_string()],
                },
            ],
            2,
            5_000,
            false,
        )
        .unwrap()
        .unwrap();
        let child_key = generation_child_key(&realtime.context_id, "msg-1");
        prepare_child_delivery(
            &mut state,
            "session-1",
            ChildDeliverySpec {
                child_key: &child_key,
                kind: "generation",
                context_id: &realtime.context_id,
                turn_evidence: serde_json::json!({"message_id": "msg-1", "ordinal": 1}),
                trace_id: &realtime.trace_id,
                root_span_id: &realtime.root_span_id,
                span_id: &claim.generations[0].span_id,
                end_time_unix_nano: None,
            },
            RetryPolicy::AtMostOnce,
            &claim.claim_id,
            "digest-before-crash",
            "before-crash",
        )
        .unwrap();
        state["session-1"]["realtime_export"]["generation"]["rounds"][0]["lease_until_ms"] =
            json!(0);
        state["session-1"]["realtime_export"]["generation"]["rounds"][1]["lease_until_ms"] =
            json!(0);
        save_state(&context.state_file, &state).unwrap();
        drop(lock);

        RealtimeGenerationHandler::new()
            .handle(
                &HookEvent::PostToolUse,
                &payload(&transcript_path, 2),
                &context,
            )
            .unwrap();

        assert!(
            server.join().unwrap().is_none(),
            "terminal ledger resume must not send another generation POST"
        );
        let state = load_state(&context.state_file);
        let rounds = state["session-1"]["realtime_export"]["generation"]["rounds"]
            .as_array()
            .unwrap();
        let round = &rounds[0];
        assert_eq!(
            round["status"],
            "accounted",
            "{}",
            serde_json::to_string_pretty(&state).unwrap()
        );
        assert!(round["claim_id"].is_null());
        assert_eq!(round["lease_until_ms"], 0);
        assert_eq!(rounds[1]["status"], "pending");
        assert!(rounds[1]["claim_id"].is_null());
        assert_eq!(rounds[1]["lease_until_ms"], 0);
        assert_eq!(
            state["session-1"]["realtime_export"]["current"]["accounted_generation_ids"],
            json!(["msg-1"])
        );
        assert_eq!(
            state["session-1"]["realtime_export"]["child_ledger"][child_key]["status"],
            "policy_terminal"
        );
        assert!(state["session-1"]["realtime_export"]["child_ledger"]
            .get(generation_child_key(&realtime.context_id, "msg-2"))
            .is_none());
    }
}
