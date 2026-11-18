use std::collections::{HashMap, HashSet};
use std::fmt;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::common::otel::deterministic_tool_span_id;
use crate::common::spool::SpoolJob;
use crate::hooks::langfuse::realtime_generation::TranscriptSourceEpoch;
use crate::hooks::RetryPolicy;

pub const REALTIME_STATE_SCHEMA_VERSION: u32 = 1;
pub const DEFAULT_REALTIME_LLM_BATCH_SIZE: usize =
    crate::hooks::DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE;

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeTraceSnapshot {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub traceparent: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub upstream_parent_span_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sofa_traceid: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sofa_rpcid: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeContext {
    pub schema_version: u32,
    pub context_id: String,
    #[serde(default)]
    pub context_sequence: u64,
    pub trace_id: String,
    pub root_span_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub turn_id: Option<String>,
    pub created_at: String,
    pub generation_batch_size: usize,
    pub retry_policy: String,
    #[serde(default)]
    pub trace: RealtimeTraceSnapshot,
    #[serde(default)]
    pub tool_use_ids: Vec<String>,
    #[serde(default)]
    pub tool_span_ids: HashMap<String, String>,
    #[serde(default)]
    pub tool_jobs: HashMap<String, RealtimeJobSnapshot>,
    #[serde(default)]
    pub tool_timings: HashMap<String, RealtimeToolTiming>,
    #[serde(default)]
    pub generation_span_ids: HashMap<String, String>,
    #[serde(default)]
    pub accounted_generation_ids: Vec<String>,
}

impl Default for RealtimeContext {
    fn default() -> Self {
        Self {
            schema_version: REALTIME_STATE_SCHEMA_VERSION,
            context_id: String::new(),
            context_sequence: 0,
            trace_id: String::new(),
            root_span_id: String::new(),
            turn_id: None,
            created_at: String::new(),
            generation_batch_size: DEFAULT_REALTIME_LLM_BATCH_SIZE,
            retry_policy: RetryPolicy::AtLeastOnce.as_str().to_string(),
            trace: RealtimeTraceSnapshot::default(),
            tool_use_ids: Vec::new(),
            tool_span_ids: HashMap::new(),
            tool_jobs: HashMap::new(),
            tool_timings: HashMap::new(),
            generation_span_ids: HashMap::new(),
            accounted_generation_ids: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CanonicalizationOutcome {
    Unique,
    Canonicalized,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RealtimeClaimAction {
    ReusedExactCurrent,
    ReusedExactPending,
    CanonicalizedPublishedOwner,
    CanonicalizedUnpublishedSequence,
    BoundUnboundCurrent,
    CreatedNewTurn,
    CurrentOnlyFallback,
}

impl RealtimeClaimAction {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::ReusedExactCurrent => "reused_exact_current",
            Self::ReusedExactPending => "reused_exact_pending",
            Self::CanonicalizedPublishedOwner => "canonicalized_published_owner",
            Self::CanonicalizedUnpublishedSequence => "canonicalized_unpublished_sequence",
            Self::BoundUnboundCurrent => "bound_unbound_current",
            Self::CreatedNewTurn => "created_new_turn",
            Self::CurrentOnlyFallback => "current_only_fallback",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CanonicalContext {
    pub context: RealtimeContext,
    pub outcome: CanonicalizationOutcome,
    pub action: RealtimeClaimAction,
    pub aliases: Vec<(String, String)>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RealtimeClaimError {
    MultiplePublishedRoots {
        turn_id: String,
        context_ids: Vec<String>,
    },
    FactConflict {
        turn_id: String,
        field: String,
        context_ids: Vec<String>,
    },
    AliasCycle {
        context_id: String,
    },
    MissingAliasTarget {
        context_id: String,
        target_id: String,
    },
}

impl fmt::Display for RealtimeClaimError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MultiplePublishedRoots {
                turn_id,
                context_ids,
            } => write!(
                formatter,
                "multiple_published_realtime_roots: turn_id={turn_id} contexts={}",
                context_ids.join(",")
            ),
            Self::FactConflict {
                turn_id,
                field,
                context_ids,
            } => write!(
                formatter,
                "realtime_context_fact_conflict: turn_id={turn_id} field={field} contexts={}",
                context_ids.join(",")
            ),
            Self::AliasCycle { context_id } => {
                write!(formatter, "realtime_context_alias_cycle: context_id={context_id}")
            }
            Self::MissingAliasTarget {
                context_id,
                target_id,
            } => write!(
                formatter,
                "realtime_context_alias_missing_target: context_id={context_id} target_id={target_id}"
            ),
        }
    }
}

impl std::error::Error for RealtimeClaimError {}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ContextPublicationEvidence {
    pub child_ids: HashSet<String>,
    pub generation_ids: HashSet<String>,
}

impl ContextPublicationEvidence {
    pub fn is_published(&self) -> bool {
        !self.child_ids.is_empty() || !self.generation_ids.is_empty()
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeToolTiming {
    pub started_at_unix_nano: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub completed_at_unix_nano: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeJobStatus {
    #[default]
    Discovered,
    Queued,
    Inflight,
    Accounted,
    Deferred,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeJobSnapshot {
    #[serde(default)]
    pub status: RealtimeJobStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub job_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub payload_digest: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub claim_id: Option<String>,
    #[serde(default)]
    pub lease_until_ms: u64,
    #[serde(default)]
    pub attempts: usize,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeRegisteredJobStatus {
    #[default]
    Queued,
    Inflight,
    Accounted,
    Failed,
    PermanentFailed,
    PolicyTerminal,
    Compensated,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeRegisteredJob {
    pub job_id: String,
    pub payload_digest: String,
    pub context_id: String,
    pub kind: String,
    pub sequence: u64,
    #[serde(default)]
    pub status: RealtimeRegisteredJobStatus,
    #[serde(default)]
    pub attempts: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub claim_id: Option<String>,
    #[serde(default)]
    pub lease_until_ms: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeGenerationState {
    #[serde(default)]
    pub scan_anchor: i64,
    #[serde(default)]
    pub committed_offset: i64,
    #[serde(default)]
    pub discovery_offset: i64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_epoch: Option<TranscriptSourceEpoch>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sticky_terminal: Option<GenerationScanTerminal>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_scan_outcome: Option<String>,
    #[serde(default)]
    pub scan_base_ordinal: u64,
    #[serde(default)]
    pub window_consumed: usize,
    #[serde(default)]
    pub native_usage_mode: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub native_usage_mode_effective_offset: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub native_usage_prefix_checked_offset: Option<u64>,
    pub batch_size: usize,
    #[serde(default)]
    pub rounds: Vec<GenerationRound>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub finalizer_only_ordinals: Vec<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GenerationScanTerminal {
    pub source_identity_digest: String,
    pub record_start: u64,
    pub reason: String,
    pub observed_bytes: u64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GenerationRoundStatus {
    #[default]
    Pending,
    Inflight,
    Accounted,
    Deferred,
    PolicyTerminal,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct GenerationRound {
    pub generation_id: String,
    pub span_id: String,
    pub context_id: String,
    pub ordinal: u64,
    #[serde(default)]
    pub status: GenerationRoundStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub job_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub payload_digest: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub claim_id: Option<String>,
    #[serde(default)]
    pub lease_until_ms: u64,
    #[serde(default)]
    pub attempts: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub terminal_reason: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeBatchStatus {
    #[default]
    Prepared,
    Published,
    Success,
    Failure,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeBatchChildLedger {
    pub child_id: String,
    pub ordinal: u64,
    #[serde(default)]
    pub status: RealtimeBatchStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub claim_id: Option<String>,
    #[serde(default)]
    pub post_compensation_used: bool,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeBatchLedger {
    pub batch_id: String,
    pub context_id: String,
    pub kind: String,
    pub payload_digest: String,
    #[serde(default)]
    pub child_ids: Vec<String>,
    #[serde(default)]
    pub children: HashMap<String, RealtimeBatchChildLedger>,
    #[serde(default)]
    pub status: RealtimeBatchStatus,
    #[serde(default)]
    pub post_compensation_round: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compensates_batch_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compensated_by_batch_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
}

pub const REALTIME_DELIVERY_PLAN_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeDeliveryChunkStatus {
    #[default]
    Queued,
    Inflight,
    PolicyTerminal,
    Accounted,
    FailedRetryable,
    PermanentFailed,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeDeliveryChunkPlanEntry {
    pub cursor: u64,
    pub batch_id: String,
    pub payload_digest: String,
    pub body_len: usize,
    #[serde(default)]
    pub child_ids: Vec<String>,
    #[serde(default)]
    pub span_ids: Vec<String>,
    #[serde(default)]
    pub digests: Vec<String>,
    /// Durable payload for recovery plans that must resume without rebuilding
    /// transcript semantics. Realtime spool-backed plans leave this empty.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub body: Option<Value>,
    #[serde(default)]
    pub status: RealtimeDeliveryChunkStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeDeliveryPlan {
    pub schema_version: u32,
    pub plan_id: String,
    pub context_id: String,
    pub trigger_id: String,
    #[serde(default)]
    pub chunks: Vec<RealtimeDeliveryChunkPlanEntry>,
    #[serde(default)]
    pub oversized_child_ids: Vec<String>,
    #[serde(default)]
    pub next_cursor: u64,
    #[serde(default)]
    pub complete: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub terminal_reason: Option<String>,
}

pub fn stable_tool_span_id(tool_use_id: &str) -> String {
    deterministic_tool_span_id(tool_use_id)
}

pub fn stable_generation_span_id(generation_id: &str) -> String {
    stable_hex("cdxhooks.realtime.generation-span.v1", generation_id, 8)
}

pub fn stable_fallback_generation_id(
    session_id: &str,
    context_id: &str,
    assistant_ordinal: usize,
) -> String {
    format!(
        "fallback:{}",
        stable_hex(
            "cdxhooks.realtime.generation-fallback-id.v1",
            &format!("{session_id}:{context_id}:{assistant_ordinal}"),
            16,
        )
    )
}

pub fn stable_context_id(trace_id: &str, root_span_id: &str) -> String {
    stable_hex(
        "cdxhooks.realtime.context.v1",
        &format!("{trace_id}:{root_span_id}"),
        16,
    )
}

pub fn stable_realtime_batch_id(
    session_id: &str,
    context_id: &str,
    kind: &str,
    payload_digest: &str,
    child_ids: &[String],
) -> String {
    let child_ids = canonical_batch_child_ids(child_ids);
    stable_hex(
        "cdxhooks.realtime.batch.v2",
        &format!(
            "{}\n{}\n{}\n{}\n{}",
            session_id,
            context_id,
            kind,
            payload_digest,
            child_ids.join("\n")
        ),
        16,
    )
}

pub fn contexts(state: &Value, session_id: &str) -> Vec<RealtimeContext> {
    let realtime = session_realtime(state, session_id);
    let mut result: Vec<RealtimeContext> = realtime
        .get("pending")
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(read_context).collect())
        .unwrap_or_default();
    if let Some(current) = realtime.get("current").and_then(read_context) {
        result.push(current);
    }
    result
}

pub fn published_root_identities_for_turn(
    state: &Value,
    session_id: &str,
    native_turn_id: &str,
) -> Vec<String> {
    let mut identities = contexts(state, session_id)
        .into_iter()
        .filter(|context| context.turn_id.as_deref() == Some(native_turn_id))
        .filter(|context| {
            context_publication_evidence(state, session_id, &context.context_id).is_published()
        })
        .map(|context| format!("{}/{}", context.trace_id, context.root_span_id))
        .collect::<Vec<_>>();
    identities.sort();
    identities.dedup();
    identities
}

pub fn context_publication_evidence(
    state: &Value,
    session_id: &str,
    context_id: &str,
) -> ContextPublicationEvidence {
    let realtime = session_realtime(state, session_id);
    let mut evidence = ContextPublicationEvidence::default();

    if let Some(context) = contexts(state, session_id)
        .into_iter()
        .find(|context| context.context_id == context_id)
    {
        evidence
            .generation_ids
            .extend(context.accounted_generation_ids);
    }

    if let Some(batches) = realtime.get("batches").and_then(Value::as_object) {
        for batch in batches.values().filter_map(read_realtime_batch) {
            if batch.context_id == context_id && batch.status == RealtimeBatchStatus::Success {
                evidence.child_ids.extend(batch.child_ids);
            }
        }
    }

    if let Some(rounds) = realtime
        .get("generation")
        .and_then(|generation| generation.get("rounds"))
        .and_then(Value::as_array)
    {
        for round in rounds.iter().filter_map(read_generation_round) {
            if round.context_id == context_id && round.status == GenerationRoundStatus::Accounted {
                evidence.generation_ids.insert(round.generation_id);
            }
        }
    }

    evidence
}

pub fn resolve_context_alias(
    state: &Value,
    session_id: &str,
    context_id: &str,
) -> Result<String, RealtimeClaimError> {
    let realtime = session_realtime(state, session_id);
    let aliases = realtime.get("context_aliases").and_then(Value::as_object);
    let live_context_ids: HashSet<String> = contexts(state, session_id)
        .into_iter()
        .map(|context| context.context_id)
        .collect();
    let mut current = context_id.to_string();
    let mut visited = HashSet::new();

    loop {
        if !visited.insert(current.clone()) {
            return Err(RealtimeClaimError::AliasCycle {
                context_id: current,
            });
        }
        let Some(target) = aliases
            .and_then(|aliases| aliases.get(&current))
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|target| !target.is_empty())
        else {
            if live_context_ids.contains(&current) {
                return Ok(current);
            }
            return Err(RealtimeClaimError::MissingAliasTarget {
                context_id: context_id.to_string(),
                target_id: current,
            });
        };
        current = target.to_string();
    }
}

pub fn context_alias_exists(state: &Value, session_id: &str, context_id: &str) -> bool {
    session_realtime(state, session_id)
        .get("context_aliases")
        .and_then(Value::as_object)
        .is_some_and(|aliases| aliases.contains_key(context_id))
}

pub fn canonicalize_turn_contexts_in_state(
    state: &mut Value,
    session_id: &str,
    native_turn_id: &str,
) -> Result<Option<CanonicalContext>, RealtimeClaimError> {
    let native_turn_id = native_turn_id.trim();
    if native_turn_id.is_empty() {
        return Ok(None);
    }
    validate_context_aliases(state, session_id)?;
    let mut candidate = state.clone();
    let outcome = canonicalize_turn_contexts_inner(&mut candidate, session_id, native_turn_id)?;
    if outcome.is_some() {
        *state = candidate;
    }
    Ok(outcome)
}

#[allow(clippy::too_many_arguments)]
pub fn claim_tool_for_native_turn_in_state(
    state: &mut Value,
    session_id: &str,
    tool_use_id: &str,
    native_turn_id: Option<&str>,
    now: &str,
    trace_id: &str,
    upstream_traceparent: Option<&str>,
    upstream_parent_span_id: Option<&str>,
    sofa_traceid: Option<&str>,
    sofa_rpcid: Option<&str>,
    generation_batch_size: usize,
    retry_policy: RetryPolicy,
) -> Result<CanonicalContext, RealtimeClaimError> {
    let native_turn_id = native_turn_id
        .map(str::trim)
        .filter(|value| !value.is_empty());
    let Some(native_turn_id) = native_turn_id else {
        let context = claim_tool_in_state(
            state,
            session_id,
            tool_use_id,
            now,
            trace_id,
            upstream_traceparent,
            upstream_parent_span_id,
            sofa_traceid,
            sofa_rpcid,
            generation_batch_size,
            retry_policy,
        );
        return Ok(CanonicalContext {
            context,
            outcome: CanonicalizationOutcome::Unique,
            action: RealtimeClaimAction::CurrentOnlyFallback,
            aliases: Vec::new(),
        });
    };

    if let Some(mut canonical) =
        canonicalize_turn_contexts_in_state(state, session_id, native_turn_id)?
    {
        add_tool_to_context_in_state(
            state,
            session_id,
            &canonical.context.context_id,
            tool_use_id,
        )
        .map_err(|field| RealtimeClaimError::FactConflict {
            turn_id: native_turn_id.to_string(),
            field,
            context_ids: vec![canonical.context.context_id.clone()],
        })?;
        canonical.context = contexts(state, session_id)
            .into_iter()
            .find(|context| context.context_id == canonical.context.context_id)
            .expect("canonical realtime context remains present after tool claim");
        return Ok(canonical);
    }

    let current = session_realtime(state, session_id)
        .get("current")
        .and_then(read_context);
    if current
        .as_ref()
        .is_some_and(|context| context.turn_id.is_none())
    {
        let mut current = current.clone().expect("unbound current context exists");
        current.turn_id = Some(native_turn_id.to_string());
        add_tool_identity(&mut current, tool_use_id);
        persist_current(state, session_id, &current);
        return Ok(CanonicalContext {
            context: current,
            outcome: CanonicalizationOutcome::Unique,
            action: RealtimeClaimAction::BoundUnboundCurrent,
            aliases: Vec::new(),
        });
    }

    if current.is_some() {
        rotate_current_in_state(state, session_id);
    }
    let context = claim_tool_in_state(
        state,
        session_id,
        tool_use_id,
        now,
        trace_id,
        upstream_traceparent,
        upstream_parent_span_id,
        sofa_traceid,
        sofa_rpcid,
        generation_batch_size,
        retry_policy,
    );
    let context =
        bind_turn_id_in_state(state, session_id, &context.context_id, Some(native_turn_id))
            .expect("new realtime context accepts its native turn identity");
    Ok(CanonicalContext {
        context,
        outcome: CanonicalizationOutcome::Unique,
        action: RealtimeClaimAction::CreatedNewTurn,
        aliases: Vec::new(),
    })
}

fn canonicalize_turn_contexts_inner(
    state: &mut Value,
    session_id: &str,
    native_turn_id: &str,
) -> Result<Option<CanonicalContext>, RealtimeClaimError> {
    let all_contexts = contexts(state, session_id);
    let current_context_id = session_realtime(state, session_id)
        .get("current")
        .and_then(read_context)
        .map(|context| context.context_id);
    let matches: Vec<(usize, RealtimeContext)> = all_contexts
        .into_iter()
        .enumerate()
        .filter(|(_, context)| context.turn_id.as_deref() == Some(native_turn_id))
        .collect();
    if matches.is_empty() {
        return Ok(None);
    }
    if matches.len() == 1 {
        let context = matches[0].1.clone();
        let action = if current_context_id.as_deref() == Some(context.context_id.as_str()) {
            RealtimeClaimAction::ReusedExactCurrent
        } else {
            RealtimeClaimAction::ReusedExactPending
        };
        return Ok(Some(CanonicalContext {
            context,
            outcome: CanonicalizationOutcome::Unique,
            action,
            aliases: Vec::new(),
        }));
    }

    let published: Vec<&(usize, RealtimeContext)> = matches
        .iter()
        .filter(|(_, context)| {
            context_publication_evidence(state, session_id, &context.context_id).is_published()
        })
        .collect();
    let published_roots: HashSet<(String, String)> = published
        .iter()
        .map(|(_, context)| (context.trace_id.clone(), context.root_span_id.clone()))
        .collect();
    if published_roots.len() > 1 {
        let mut context_ids: Vec<String> = published
            .iter()
            .map(|(_, context)| context.context_id.clone())
            .collect();
        context_ids.sort();
        context_ids.dedup();
        return Err(RealtimeClaimError::MultiplePublishedRoots {
            turn_id: native_turn_id.to_string(),
            context_ids,
        });
    }

    let candidates: Vec<&(usize, RealtimeContext)> = if published.is_empty() {
        matches.iter().collect()
    } else {
        let published_root = published_roots.iter().next().cloned();
        matches
            .iter()
            .filter(|(_, context)| {
                published_root.as_ref()
                    == Some(&(context.trace_id.clone(), context.root_span_id.clone()))
                    && context_publication_evidence(state, session_id, &context.context_id)
                        .is_published()
            })
            .collect()
    };
    let winner = candidates
        .into_iter()
        .min_by(stable_context_order)
        .expect("multiple exact matches provide a canonicalization candidate");
    let mut merged = winner.1.clone();
    let mut aliases = Vec::new();
    for (_, context) in &matches {
        if std::ptr::eq(context, &winner.1) {
            continue;
        }
        merge_context_facts(&mut merged, context).map_err(|field| {
            let mut context_ids = vec![winner.1.context_id.clone(), context.context_id.clone()];
            context_ids.sort();
            context_ids.dedup();
            RealtimeClaimError::FactConflict {
                turn_id: native_turn_id.to_string(),
                field,
                context_ids,
            }
        })?;
        if context.context_id != merged.context_id
            && !aliases
                .iter()
                .any(|(loser, _): &(String, String)| loser == &context.context_id)
        {
            aliases.push((context.context_id.clone(), merged.context_id.clone()));
        }
    }
    if merged.context_sequence == 0 {
        merged.context_sequence = allocate_context_sequence_in_state(state, session_id);
    }
    replace_canonicalized_contexts(
        state,
        session_id,
        native_turn_id,
        &merged,
        &aliases,
        current_context_id.as_deref(),
    )?;
    let action = if published.is_empty() {
        RealtimeClaimAction::CanonicalizedUnpublishedSequence
    } else {
        RealtimeClaimAction::CanonicalizedPublishedOwner
    };
    Ok(Some(CanonicalContext {
        context: merged,
        outcome: CanonicalizationOutcome::Canonicalized,
        action,
        aliases,
    }))
}

fn stable_context_order(
    left: &&(usize, RealtimeContext),
    right: &&(usize, RealtimeContext),
) -> std::cmp::Ordering {
    let left_sequence = (left.1.context_sequence == 0, left.1.context_sequence);
    let right_sequence = (right.1.context_sequence == 0, right.1.context_sequence);
    left_sequence
        .cmp(&right_sequence)
        .then_with(|| left.1.created_at.cmp(&right.1.created_at))
        .then_with(|| left.0.cmp(&right.0))
        .then_with(|| left.1.context_id.cmp(&right.1.context_id))
}

fn merge_context_facts(
    winner: &mut RealtimeContext,
    loser: &RealtimeContext,
) -> Result<(), String> {
    if winner.generation_batch_size != loser.generation_batch_size {
        return Err("generation_batch_size".to_string());
    }
    if winner.retry_policy != loser.retry_policy {
        return Err("retry_policy".to_string());
    }
    merge_optional_fact(
        &mut winner.trace.traceparent,
        &loser.trace.traceparent,
        "trace.traceparent",
    )?;
    merge_optional_fact(
        &mut winner.trace.upstream_parent_span_id,
        &loser.trace.upstream_parent_span_id,
        "trace.upstream_parent_span_id",
    )?;
    merge_optional_fact(
        &mut winner.trace.sofa_traceid,
        &loser.trace.sofa_traceid,
        "trace.sofa_traceid",
    )?;
    merge_optional_fact(
        &mut winner.trace.sofa_rpcid,
        &loser.trace.sofa_rpcid,
        "trace.sofa_rpcid",
    )?;
    merge_exact_map(
        &mut winner.tool_span_ids,
        &loser.tool_span_ids,
        "tool_span_ids",
    )?;
    merge_exact_map(&mut winner.tool_jobs, &loser.tool_jobs, "tool_jobs")?;
    merge_exact_map(
        &mut winner.generation_span_ids,
        &loser.generation_span_ids,
        "generation_span_ids",
    )?;
    for (tool_use_id, timing) in &loser.tool_timings {
        match winner.tool_timings.get_mut(tool_use_id) {
            None => {
                winner
                    .tool_timings
                    .insert(tool_use_id.clone(), timing.clone());
            }
            Some(existing) => {
                if existing.started_at_unix_nano != timing.started_at_unix_nano {
                    return Err(format!("tool_timings.{tool_use_id}.started_at"));
                }
                merge_optional_fact(
                    &mut existing.completed_at_unix_nano,
                    &timing.completed_at_unix_nano,
                    &format!("tool_timings.{tool_use_id}.completed_at"),
                )?;
                merge_optional_fact(
                    &mut existing.last_error,
                    &timing.last_error,
                    &format!("tool_timings.{tool_use_id}.last_error"),
                )?;
            }
        }
    }
    for tool_use_id in &loser.tool_use_ids {
        if !winner.tool_use_ids.contains(tool_use_id) {
            winner.tool_use_ids.push(tool_use_id.clone());
        }
    }
    for generation_id in &loser.accounted_generation_ids {
        if !winner.accounted_generation_ids.contains(generation_id) {
            winner.accounted_generation_ids.push(generation_id.clone());
        }
    }
    Ok(())
}

fn merge_optional_fact<T: Clone + PartialEq>(
    winner: &mut Option<T>,
    loser: &Option<T>,
    field: &str,
) -> Result<(), String> {
    match (winner.as_ref(), loser.as_ref()) {
        (Some(left), Some(right)) if left != right => Err(field.to_string()),
        (None, Some(value)) => {
            *winner = Some(value.clone());
            Ok(())
        }
        _ => Ok(()),
    }
}

fn merge_exact_map<T: Clone + PartialEq>(
    winner: &mut HashMap<String, T>,
    loser: &HashMap<String, T>,
    field: &str,
) -> Result<(), String> {
    for (key, value) in loser {
        match winner.get(key) {
            Some(existing) if existing != value => return Err(format!("{field}.{key}")),
            Some(_) => {}
            None => {
                winner.insert(key.clone(), value.clone());
            }
        }
    }
    Ok(())
}

pub(crate) fn add_tool_to_context_in_state(
    state: &mut Value,
    session_id: &str,
    context_id: &str,
    tool_use_id: &str,
) -> Result<(), String> {
    let realtime = ensure_realtime_object(state, session_id);
    let mut found = false;
    update_context(realtime, context_id, |value| {
        let Some(mut context) = read_context(&Value::Object(value.clone())) else {
            return;
        };
        add_tool_identity(&mut context, tool_use_id);
        if let Ok(Value::Object(updated)) = serde_json::to_value(context) {
            *value = updated;
            found = true;
        }
    });
    found
        .then_some(())
        .ok_or_else(|| format!("context.{context_id}"))
}

fn validate_context_aliases(state: &Value, session_id: &str) -> Result<(), RealtimeClaimError> {
    let alias_ids: Vec<String> = session_realtime(state, session_id)
        .get("context_aliases")
        .and_then(Value::as_object)
        .map(|aliases| aliases.keys().cloned().collect())
        .unwrap_or_default();
    for alias_id in alias_ids {
        resolve_context_alias(state, session_id, &alias_id)?;
    }
    Ok(())
}

fn replace_canonicalized_contexts(
    state: &mut Value,
    session_id: &str,
    native_turn_id: &str,
    winner: &RealtimeContext,
    aliases: &[(String, String)],
    current_context_id: Option<&str>,
) -> Result<(), RealtimeClaimError> {
    let matched_current = current_context_id.is_some_and(|current_id| {
        contexts(state, session_id).into_iter().any(|context| {
            context.context_id == current_id && context.turn_id.as_deref() == Some(native_turn_id)
        })
    });
    let winner_value = serde_json::to_value(winner).expect("realtime context serializes");
    let realtime = ensure_realtime_object(state, session_id);
    let pending = realtime
        .entry("pending".to_string())
        .or_insert_with(|| json!([]));
    if let Some(items) = pending.as_array_mut() {
        items.retain(|item| {
            read_context(item)
                .is_none_or(|context| context.turn_id.as_deref() != Some(native_turn_id))
        });
        if !matched_current {
            items.push(winner_value.clone());
        }
    }
    if matched_current {
        realtime.insert("current".to_string(), winner_value);
    }

    let alias_map = realtime
        .entry("context_aliases".to_string())
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .expect("context_aliases initialized as object");
    let loser_ids: HashSet<&str> = aliases.iter().map(|(loser, _)| loser.as_str()).collect();
    for target in alias_map.values_mut() {
        if target
            .as_str()
            .is_some_and(|target| loser_ids.contains(target))
        {
            *target = json!(winner.context_id.clone());
        }
    }
    alias_map.remove(&winner.context_id);
    for (loser, _) in aliases {
        alias_map.insert(loser.clone(), json!(winner.context_id.clone()));
    }

    rewrite_context_references(realtime, &loser_ids, &winner.context_id);
    Ok(())
}

fn rewrite_context_references(
    realtime: &mut serde_json::Map<String, Value>,
    loser_ids: &HashSet<&str>,
    winner_id: &str,
) {
    for collection in ["registered_jobs", "batches", "pending_flushes"] {
        if let Some(values) = realtime.get_mut(collection).and_then(Value::as_object_mut) {
            for value in values.values_mut() {
                if value
                    .get("context_id")
                    .and_then(Value::as_str)
                    .is_some_and(|context_id| loser_ids.contains(context_id))
                {
                    value["context_id"] = json!(winner_id);
                }
            }
        }
    }
    if let Some(rounds) = realtime
        .get_mut("generation")
        .and_then(|generation| generation.get_mut("rounds"))
        .and_then(Value::as_array_mut)
    {
        for round in rounds {
            if round
                .get("context_id")
                .and_then(Value::as_str)
                .is_some_and(|context_id| loser_ids.contains(context_id))
            {
                round["context_id"] = json!(winner_id);
            }
        }
    }
}

/// Ensure an exact realtime context reconstructed from a session-local durable
/// artifact exists in state. Existing contexts are merged without replacing
/// delivery/accounting fields; a new context becomes current only when there is
/// no current context, otherwise it is appended to the session-local pending
/// list.
pub fn ensure_context_in_state(
    state: &mut Value,
    session_id: &str,
    context: &RealtimeContext,
) -> Result<RealtimeContext, String> {
    if context.context_id.is_empty()
        || context.trace_id.is_empty()
        || context.root_span_id.is_empty()
    {
        return Err("cannot reconcile incomplete realtime context".to_string());
    }

    if contexts(state, session_id)
        .iter()
        .any(|candidate| candidate.context_id == context.context_id)
    {
        let realtime = ensure_realtime_object(state, session_id);
        update_context(realtime, &context.context_id, |value| {
            for tool_use_id in &context.tool_use_ids {
                if !value
                    .get("tool_use_ids")
                    .and_then(Value::as_array)
                    .is_some_and(|ids| ids.iter().any(|id| id.as_str() == Some(tool_use_id)))
                {
                    value
                        .entry("tool_use_ids".to_string())
                        .or_insert_with(|| json!([]))
                        .as_array_mut()
                        .expect("tool_use_ids initialized as array")
                        .push(json!(tool_use_id));
                }
            }
            let span_ids = value
                .entry("tool_span_ids".to_string())
                .or_insert_with(|| json!({}));
            if let Some(span_ids) = span_ids.as_object_mut() {
                for (tool_use_id, span_id) in &context.tool_span_ids {
                    span_ids
                        .entry(tool_use_id.clone())
                        .or_insert_with(|| json!(span_id));
                }
            }
            if value
                .get("turn_id")
                .and_then(Value::as_str)
                .is_none_or(str::is_empty)
            {
                if let Some(turn_id) = context.turn_id.as_deref() {
                    value.insert("turn_id".to_string(), json!(turn_id));
                }
            }
        });
        return contexts(state, session_id)
            .into_iter()
            .find(|candidate| candidate.context_id == context.context_id)
            .ok_or_else(|| "reconciled realtime context disappeared".to_string());
    }

    let mut context = context.clone();
    if context.context_sequence == 0 {
        context.context_sequence = allocate_context_sequence_in_state(state, session_id);
    }
    let realtime = ensure_realtime_object(state, session_id);
    let encoded = serde_json::to_value(&context)
        .map_err(|error| format!("encode reconciled realtime context: {error}"))?;
    if realtime.get("current").is_none_or(Value::is_null) {
        realtime.insert("current".to_string(), encoded);
    } else {
        let pending = realtime
            .entry("pending".to_string())
            .or_insert_with(|| json!([]));
        let Some(pending) = pending.as_array_mut() else {
            return Err("invalid realtime pending context list".to_string());
        };
        pending.push(encoded);
    }
    Ok(context)
}

pub fn rotate_current_in_state(state: &mut Value, session_id: &str) {
    let Some(current) = session_realtime(state, session_id)
        .get("current")
        .and_then(read_context)
    else {
        ensure_realtime_object(state, session_id);
        return;
    };
    if current.tool_use_ids.is_empty() {
        ensure_realtime_object(state, session_id);
        remove_current(state, session_id);
        return;
    }
    let Ok(current_value) = serde_json::to_value(&current) else {
        return;
    };

    let realtime = ensure_realtime_object(state, session_id);
    let pending = realtime
        .entry("pending".to_string())
        .or_insert_with(|| json!([]));
    if let Some(items) = pending.as_array_mut() {
        if !items.iter().any(|item| {
            item.get("context_id").and_then(Value::as_str) == Some(current.context_id.as_str())
        }) {
            items.push(current_value);
        }
    }
    realtime.remove("current");
}

#[allow(clippy::too_many_arguments)]
pub fn claim_tool_in_state(
    state: &mut Value,
    session_id: &str,
    tool_use_id: &str,
    now: &str,
    trace_id: &str,
    upstream_traceparent: Option<&str>,
    upstream_parent_span_id: Option<&str>,
    sofa_traceid: Option<&str>,
    sofa_rpcid: Option<&str>,
    generation_batch_size: usize,
    retry_policy: RetryPolicy,
) -> RealtimeContext {
    if let Some(mut current) = session_realtime(state, session_id)
        .get("current")
        .and_then(read_context)
    {
        add_tool_identity(&mut current, tool_use_id);
        persist_current(state, session_id, &current);
        return current;
    }

    let root_span_id = agentic_otel_common::otel_span_id();
    let context_sequence = allocate_context_sequence_in_state(state, session_id);
    let mut context = RealtimeContext {
        schema_version: REALTIME_STATE_SCHEMA_VERSION,
        context_id: stable_context_id(trace_id, &root_span_id),
        context_sequence,
        trace_id: trace_id.to_string(),
        root_span_id,
        created_at: now.to_string(),
        generation_batch_size: generation_batch_size.max(1),
        retry_policy: retry_policy.as_str().to_string(),
        trace: RealtimeTraceSnapshot {
            traceparent: upstream_traceparent.map(str::to_string),
            upstream_parent_span_id: upstream_parent_span_id.map(str::to_string),
            sofa_traceid: sofa_traceid.map(str::to_string),
            sofa_rpcid: sofa_rpcid.map(str::to_string),
        },
        ..RealtimeContext::default()
    };
    add_tool_identity(&mut context, tool_use_id);
    persist_current(state, session_id, &context);
    context
}

pub fn ensure_generation_state_in_state(
    state: &mut Value,
    session_id: &str,
    configured_batch_size: usize,
) -> RealtimeGenerationState {
    let realtime = ensure_realtime_object(state, session_id);
    if let Some(generation) = realtime.get("generation").and_then(read_generation_state) {
        return generation;
    }
    let generation = RealtimeGenerationState {
        scan_anchor: 0,
        committed_offset: 0,
        discovery_offset: 0,
        source_epoch: None,
        sticky_terminal: None,
        last_scan_outcome: None,
        scan_base_ordinal: 0,
        window_consumed: 0,
        native_usage_mode: false,
        native_usage_mode_effective_offset: None,
        native_usage_prefix_checked_offset: None,
        batch_size: configured_batch_size.max(1),
        rounds: Vec::new(),
        finalizer_only_ordinals: Vec::new(),
    };
    realtime.insert(
        "generation".to_string(),
        serde_json::to_value(&generation).unwrap_or_else(|_| json!({})),
    );
    generation
}

pub fn prepare_realtime_batch_in_state(
    state: &mut Value,
    session_id: &str,
    context_id: &str,
    kind: &str,
    child_ids: &[String],
    payload_digest: &str,
) -> Result<RealtimeBatchLedger, String> {
    let batch_id =
        stable_realtime_batch_id(session_id, context_id, kind, payload_digest, child_ids);
    prepare_realtime_batch_with_id_in_state(
        state,
        session_id,
        context_id,
        kind,
        &batch_id,
        child_ids,
        payload_digest,
    )
}

pub fn stable_realtime_delivery_plan_id(
    session_id: &str,
    context_id: &str,
    trigger_id: &str,
    child_ids: &[String],
    payload_digests: &[String],
) -> String {
    stable_hex(
        "cdxhooks.realtime.delivery-plan.v1",
        &format!(
            "{session_id}:{context_id}:{trigger_id}:{}:{}",
            child_ids.join(","),
            payload_digests.join(",")
        ),
        16,
    )
}

pub fn prepare_realtime_delivery_plan_in_state(
    state: &mut Value,
    session_id: &str,
    plan: &RealtimeDeliveryPlan,
) -> Result<RealtimeDeliveryPlan, String> {
    if plan.schema_version != REALTIME_DELIVERY_PLAN_SCHEMA_VERSION {
        return Err(format!(
            "unsupported realtime delivery plan schema: {}",
            plan.schema_version
        ));
    }
    if plan.plan_id.trim().is_empty()
        || plan.context_id.trim().is_empty()
        || plan.trigger_id.trim().is_empty()
    {
        return Err("realtime delivery plan identity is incomplete".to_string());
    }
    if plan.chunks.iter().enumerate().any(|(index, chunk)| {
        chunk.cursor != index as u64
            || chunk.batch_id.trim().is_empty()
            || chunk.payload_digest.trim().is_empty()
            || chunk.child_ids.is_empty()
            || chunk.child_ids.len() != chunk.span_ids.len()
            || chunk.child_ids.len() != chunk.digests.len()
    }) {
        return Err("realtime delivery plan chunks are invalid".to_string());
    }
    let realtime = ensure_realtime_object(state, session_id);
    let plans = realtime
        .entry("delivery_plans".to_string())
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or("realtime delivery_plans is not an object")?;
    if let Some(existing) = plans
        .get(&plan.plan_id)
        .and_then(read_realtime_delivery_plan)
    {
        let mut expected = plan.clone();
        expected.next_cursor = existing.next_cursor;
        expected.complete = existing.complete;
        expected.terminal_reason = existing.terminal_reason.clone();
        for (expected_chunk, existing_chunk) in
            expected.chunks.iter_mut().zip(existing.chunks.iter())
        {
            expected_chunk.status = existing_chunk.status.clone();
            expected_chunk.last_error = existing_chunk.last_error.clone();
        }
        let mut resumed = existing.clone();
        for (resumed_chunk, proposed_chunk) in resumed.chunks.iter_mut().zip(plan.chunks.iter()) {
            if resumed_chunk.body.is_none() {
                resumed_chunk.body = proposed_chunk.body.clone();
            }
        }
        if resumed != expected {
            return Err(format!("realtime delivery plan conflict: {}", plan.plan_id));
        }
        if resumed != existing {
            plans.insert(
                resumed.plan_id.clone(),
                serde_json::to_value(&resumed).map_err(|error| error.to_string())?,
            );
        }
        return Ok(resumed);
    }
    let mut prepared = plan.clone();
    prepared.next_cursor = 0;
    prepared.complete = prepared.chunks.is_empty();
    if !prepared.oversized_child_ids.is_empty() {
        prepared.terminal_reason = Some("realtime_child_oversize".to_string());
    }
    plans.insert(
        prepared.plan_id.clone(),
        serde_json::to_value(&prepared).map_err(|error| error.to_string())?,
    );
    Ok(prepared)
}

pub fn realtime_delivery_plan(
    state: &Value,
    session_id: &str,
    plan_id: &str,
) -> Option<RealtimeDeliveryPlan> {
    session_realtime(state, session_id)
        .get("delivery_plans")
        .and_then(Value::as_object)
        .and_then(|plans| plans.get(plan_id))
        .and_then(read_realtime_delivery_plan)
}

pub fn begin_realtime_delivery_plan_chunks_in_state(
    state: &mut Value,
    session_id: &str,
    plan_id: &str,
    cursors: &[u64],
    retry_policy: RetryPolicy,
) -> Result<RealtimeDeliveryPlan, String> {
    update_realtime_delivery_plan(state, session_id, plan_id, |plan| {
        for cursor in cursors {
            let chunk = plan
                .chunks
                .get_mut(*cursor as usize)
                .ok_or_else(|| format!("realtime delivery chunk missing: {cursor}"))?;
            if !matches!(
                chunk.status,
                RealtimeDeliveryChunkStatus::Queued
                    | RealtimeDeliveryChunkStatus::FailedRetryable
                    | RealtimeDeliveryChunkStatus::Inflight
            ) {
                return Err(format!(
                    "realtime delivery chunk is not claimable: cursor={cursor} status={:?}",
                    chunk.status
                ));
            }
            chunk.status = match retry_policy {
                RetryPolicy::AtMostOnce => RealtimeDeliveryChunkStatus::PolicyTerminal,
                RetryPolicy::AtLeastOnce => RealtimeDeliveryChunkStatus::Inflight,
            };
            chunk.last_error = None;
        }
        Ok(())
    })
}

pub fn finish_realtime_delivery_plan_chunk_in_state(
    state: &mut Value,
    session_id: &str,
    plan_id: &str,
    cursor: u64,
    retry_policy: RetryPolicy,
    accepted: bool,
    permanent: bool,
    error: Option<&str>,
) -> Result<RealtimeDeliveryPlan, String> {
    update_realtime_delivery_plan(state, session_id, plan_id, |plan| {
        let chunk = plan
            .chunks
            .get_mut(cursor as usize)
            .ok_or_else(|| format!("realtime delivery chunk missing: {cursor}"))?;
        if accepted {
            chunk.status = RealtimeDeliveryChunkStatus::Accounted;
            chunk.last_error = None;
        } else {
            chunk.last_error = error.map(str::to_string);
            chunk.status = if permanent {
                RealtimeDeliveryChunkStatus::PermanentFailed
            } else {
                match retry_policy {
                    RetryPolicy::AtMostOnce => RealtimeDeliveryChunkStatus::PolicyTerminal,
                    RetryPolicy::AtLeastOnce => RealtimeDeliveryChunkStatus::FailedRetryable,
                }
            };
        }
        if accepted || permanent || retry_policy == RetryPolicy::AtMostOnce {
            advance_realtime_delivery_plan_cursor(plan);
        }
        Ok(())
    })
}

pub fn settle_interrupted_policy_terminal_plan_in_state(
    state: &mut Value,
    session_id: &str,
    plan_id: &str,
) -> Result<RealtimeDeliveryPlan, String> {
    update_realtime_delivery_plan(state, session_id, plan_id, |plan| {
        advance_realtime_delivery_plan_cursor(plan);
        if plan.complete && plan.terminal_reason.is_none() {
            plan.terminal_reason = Some("at_most_once_interrupted_after_checkpoint".to_string());
        }
        Ok(())
    })
}

fn advance_realtime_delivery_plan_cursor(plan: &mut RealtimeDeliveryPlan) {
    while let Some(chunk) = plan.chunks.get(plan.next_cursor as usize) {
        if !matches!(
            chunk.status,
            RealtimeDeliveryChunkStatus::Accounted
                | RealtimeDeliveryChunkStatus::PolicyTerminal
                | RealtimeDeliveryChunkStatus::PermanentFailed
        ) {
            break;
        }
        plan.next_cursor = plan.next_cursor.saturating_add(1);
    }
    plan.complete = plan.next_cursor as usize >= plan.chunks.len();
}

fn update_realtime_delivery_plan<F>(
    state: &mut Value,
    session_id: &str,
    plan_id: &str,
    update: F,
) -> Result<RealtimeDeliveryPlan, String>
where
    F: FnOnce(&mut RealtimeDeliveryPlan) -> Result<(), String>,
{
    let realtime = ensure_realtime_object(state, session_id);
    let plans = realtime
        .get_mut("delivery_plans")
        .and_then(Value::as_object_mut)
        .ok_or_else(|| format!("realtime delivery plan is not prepared: {plan_id}"))?;
    let value = plans
        .get_mut(plan_id)
        .ok_or_else(|| format!("realtime delivery plan is not prepared: {plan_id}"))?;
    let mut plan = read_realtime_delivery_plan(value)
        .ok_or_else(|| format!("invalid realtime delivery plan: {plan_id}"))?;
    update(&mut plan)?;
    *value = serde_json::to_value(&plan).map_err(|error| error.to_string())?;
    Ok(plan)
}

#[allow(clippy::too_many_arguments)]
pub fn prepare_realtime_batch_with_id_in_state(
    state: &mut Value,
    session_id: &str,
    context_id: &str,
    kind: &str,
    batch_id: &str,
    child_ids: &[String],
    payload_digest: &str,
) -> Result<RealtimeBatchLedger, String> {
    if context_id.trim().is_empty() {
        return Err("realtime batch context_id is required".to_string());
    }
    if kind.trim().is_empty() {
        return Err("realtime batch kind is required".to_string());
    }
    if payload_digest.trim().is_empty() {
        return Err("realtime batch payload_digest is required".to_string());
    }
    let child_ids = canonical_batch_child_ids(child_ids);
    if child_ids.is_empty() {
        return Err("realtime batch requires at least one child".to_string());
    }
    if batch_id.trim().is_empty() {
        return Err("realtime batch batch_id is required".to_string());
    }
    let batch_id = batch_id.to_string();
    let realtime = ensure_realtime_object(state, session_id);

    if let Some(existing) = realtime
        .get("batches")
        .and_then(Value::as_object)
        .and_then(|batches| batches.get(&batch_id))
        .and_then(read_realtime_batch)
    {
        if existing.context_id != context_id
            || existing.kind != kind
            || existing.payload_digest != payload_digest
            || existing.child_ids != child_ids
        {
            return Err(format!("realtime batch prepare conflict: {batch_id}"));
        }
        ensure_realtime_child_compensation_entries(realtime, &existing.child_ids)?;
        return Ok(existing);
    }

    let children = child_ids
        .iter()
        .enumerate()
        .map(|(index, child_id)| {
            (
                child_id.clone(),
                RealtimeBatchChildLedger {
                    child_id: child_id.clone(),
                    ordinal: index as u64,
                    status: RealtimeBatchStatus::Prepared,
                    last_error: None,
                    post_compensation_used: false,
                    claim_id: None,
                },
            )
        })
        .collect();
    let batch = RealtimeBatchLedger {
        batch_id: batch_id.clone(),
        context_id: context_id.to_string(),
        kind: kind.to_string(),
        payload_digest: payload_digest.to_string(),
        child_ids,
        children,
        status: RealtimeBatchStatus::Prepared,
        post_compensation_round: 0,
        compensates_batch_id: None,
        compensated_by_batch_id: None,
        last_error: None,
    };
    ensure_realtime_child_compensation_entries(realtime, &batch.child_ids)?;
    let batches = realtime
        .entry("batches".to_string())
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or("realtime batches is not an object")?;
    batches.insert(
        batch_id,
        serde_json::to_value(&batch).map_err(|error| error.to_string())?,
    );
    Ok(batch)
}

pub fn realtime_batch(
    state: &Value,
    session_id: &str,
    batch_id: &str,
) -> Option<RealtimeBatchLedger> {
    session_realtime(state, session_id)
        .get("batches")
        .and_then(Value::as_object)
        .and_then(|batches| batches.get(batch_id))
        .and_then(read_realtime_batch)
}

pub fn publish_realtime_batch_in_state(
    state: &mut Value,
    session_id: &str,
    batch_id: &str,
) -> Result<RealtimeBatchLedger, String> {
    update_realtime_batch(state, session_id, batch_id, |batch| match batch.status {
        RealtimeBatchStatus::Prepared | RealtimeBatchStatus::Published => {
            batch.status = RealtimeBatchStatus::Published;
            batch.last_error = None;
            for child in batch.children.values_mut() {
                child.status = RealtimeBatchStatus::Published;
                child.last_error = None;
            }
            Ok(())
        }
        RealtimeBatchStatus::Success | RealtimeBatchStatus::Failure => Err(format!(
            "realtime batch cannot be published: {batch_id} status={:?}",
            batch.status
        )),
    })
}

pub fn succeed_realtime_batch_in_state(
    state: &mut Value,
    session_id: &str,
    batch_id: &str,
) -> Result<RealtimeBatchLedger, String> {
    update_realtime_batch(state, session_id, batch_id, |batch| match batch.status {
        RealtimeBatchStatus::Published | RealtimeBatchStatus::Success => {
            batch.status = RealtimeBatchStatus::Success;
            batch.last_error = None;
            for child in batch.children.values_mut() {
                child.status = RealtimeBatchStatus::Success;
                child.last_error = None;
            }
            Ok(())
        }
        RealtimeBatchStatus::Prepared | RealtimeBatchStatus::Failure => Err(format!(
            "realtime batch cannot succeed: {batch_id} status={:?}",
            batch.status
        )),
    })
}

pub fn complete_realtime_batch_compensation_in_state(
    state: &mut Value,
    session_id: &str,
    compensation_batch_id: &str,
) -> Result<(), String> {
    let compensation = realtime_batch(state, session_id, compensation_batch_id)
        .ok_or_else(|| format!("realtime batch is not prepared: {compensation_batch_id}"))?;
    let Some(original_batch_id) = compensation.compensates_batch_id.as_deref() else {
        return Ok(());
    };
    let original = registered_realtime_job(state, session_id, original_batch_id)
        .ok_or_else(|| format!("realtime job is not registered: {original_batch_id}"))?;
    compensate_realtime_job_in_state(
        state,
        session_id,
        original_batch_id,
        &original.payload_digest,
    )
}

pub fn fail_realtime_batch_in_state(
    state: &mut Value,
    session_id: &str,
    batch_id: &str,
    error: &str,
) -> Result<RealtimeBatchLedger, String> {
    update_realtime_batch(state, session_id, batch_id, |batch| match batch.status {
        RealtimeBatchStatus::Published | RealtimeBatchStatus::Failure => {
            batch.status = RealtimeBatchStatus::Failure;
            batch.last_error = Some(error.to_string());
            for child in batch.children.values_mut() {
                child.status = RealtimeBatchStatus::Failure;
                child.last_error = Some(error.to_string());
            }
            Ok(())
        }
        RealtimeBatchStatus::Prepared | RealtimeBatchStatus::Success => Err(format!(
            "realtime batch cannot fail: {batch_id} status={:?}",
            batch.status
        )),
    })
}

/// Re-open a failed Batch only for the state-native direct-delivery retry path.
/// The registered job must already be queued by an at-least-once failure
/// checkpoint; terminal and legacy-compensation failures remain immutable.
pub fn prepare_failed_realtime_batch_retry_in_state(
    state: &mut Value,
    session_id: &str,
    batch_id: &str,
) -> Result<RealtimeBatchLedger, String> {
    let registration = registered_realtime_job(state, session_id, batch_id)
        .ok_or_else(|| format!("realtime job is not registered: {batch_id}"))?;
    if registration.status != RealtimeRegisteredJobStatus::Queued {
        return Err(format!(
            "realtime batch retry is not queued: {batch_id} status={:?}",
            registration.status
        ));
    }
    update_realtime_batch(state, session_id, batch_id, |batch| match batch.status {
        RealtimeBatchStatus::Failure => {
            batch.status = RealtimeBatchStatus::Prepared;
            batch.last_error = None;
            for child in batch.children.values_mut() {
                child.status = RealtimeBatchStatus::Prepared;
                child.last_error = None;
            }
            Ok(())
        }
        RealtimeBatchStatus::Prepared | RealtimeBatchStatus::Published => Ok(()),
        RealtimeBatchStatus::Success => Err(format!(
            "successful realtime batch cannot be retried: {batch_id}"
        )),
    })
}

pub fn prepare_realtime_batch_compensation_in_state(
    state: &mut Value,
    session_id: &str,
    original_batch_id: &str,
    compensation_batch_id: &str,
    original_payload_digest: &str,
    compensation_child_ids: &[String],
    compensation_payload_digest: &str,
) -> Result<RealtimeBatchLedger, String> {
    let original = realtime_batch(state, session_id, original_batch_id)
        .ok_or_else(|| format!("realtime batch is not prepared: {original_batch_id}"))?;
    if original.payload_digest != original_payload_digest {
        return Err(format!(
            "realtime batch compensation digest mismatch: {original_batch_id}"
        ));
    }
    let compensation_child_ids = canonical_batch_child_ids(compensation_child_ids);
    if !original
        .child_ids
        .iter()
        .all(|child_id| compensation_child_ids.contains(child_id))
    {
        return Err(format!(
            "realtime compensation batch omits original children: {original_batch_id}"
        ));
    }
    if let Some(existing) = realtime_batch(state, session_id, compensation_batch_id) {
        if existing.compensates_batch_id.as_deref() != Some(original_batch_id)
            || existing.payload_digest != compensation_payload_digest
            || existing.child_ids != compensation_child_ids
        {
            return Err(format!(
                "realtime compensation batch conflict: {compensation_batch_id}"
            ));
        }
        return Ok(existing);
    }
    if original.status != RealtimeBatchStatus::Failure
        || original.post_compensation_round != 0
        || original.compensated_by_batch_id.is_some()
    {
        return Err(format!(
            "realtime batch is not eligible for PostToolUse compensation: {original_batch_id}"
        ));
    }

    prepare_realtime_batch_with_id_in_state(
        state,
        session_id,
        &original.context_id,
        "compensation",
        compensation_batch_id,
        &compensation_child_ids,
        compensation_payload_digest,
    )?;
    register_realtime_job_in_state(
        state,
        session_id,
        &original.context_id,
        "batch",
        compensation_batch_id,
        compensation_payload_digest,
    )?;
    for child_id in &original.child_ids {
        mark_realtime_batch_child_post_compensation_used_in_state(
            state,
            session_id,
            original_batch_id,
            child_id,
        )?;
        mark_realtime_batch_child_post_compensation_used_in_state(
            state,
            session_id,
            compensation_batch_id,
            child_id,
        )?;
    }
    let compensation = update_realtime_batch(state, session_id, compensation_batch_id, |batch| {
        batch.post_compensation_round = 1;
        batch.compensates_batch_id = Some(original_batch_id.to_string());
        Ok(())
    })?;
    update_realtime_batch(state, session_id, original_batch_id, |batch| {
        batch.post_compensation_round = 1;
        batch.compensated_by_batch_id = Some(compensation_batch_id.to_string());
        Ok(())
    })?;
    Ok(compensation)
}

pub fn claim_realtime_batch_child_in_state(
    state: &mut Value,
    session_id: &str,
    batch_id: &str,
    child_id: &str,
    claim_id: &str,
) -> Result<RealtimeBatchChildLedger, String> {
    if claim_id.trim().is_empty() {
        return Err("realtime batch child claim_id is required".to_string());
    }
    update_realtime_batch_child(state, session_id, batch_id, child_id, |child| {
        if let Some(existing) = child.claim_id.as_deref() {
            if existing != claim_id {
                return Err(format!(
                    "realtime batch child already claimed: batch_id={batch_id} child_id={child_id}"
                ));
            }
        } else {
            child.claim_id = Some(claim_id.to_string());
        }
        Ok(())
    })
}

pub fn mark_realtime_batch_child_post_compensation_used_in_state(
    state: &mut Value,
    session_id: &str,
    batch_id: &str,
    child_id: &str,
) -> Result<RealtimeBatchChildLedger, String> {
    let batch = realtime_batch(state, session_id, batch_id)
        .ok_or_else(|| format!("realtime batch is not prepared: {batch_id}"))?;
    if !batch.children.contains_key(child_id) {
        return Err(format!(
            "realtime batch child not found: batch_id={batch_id} child_id={child_id}"
        ));
    }
    mark_realtime_child_post_compensation_used(state, session_id, child_id)?;
    let child = update_realtime_batch_child(state, session_id, batch_id, child_id, |child| {
        child.post_compensation_used = true;
        Ok(())
    })?;
    Ok(child)
}

pub fn bind_turn_id_in_state(
    state: &mut Value,
    session_id: &str,
    context_id: &str,
    turn_id: Option<&str>,
) -> Result<RealtimeContext, String> {
    let Some(turn_id) = turn_id.map(str::trim).filter(|value| !value.is_empty()) else {
        return contexts(state, session_id)
            .into_iter()
            .find(|context| context.context_id == context_id)
            .ok_or_else(|| format!("realtime context not found: {context_id}"));
    };
    let mut outcome = None;
    let realtime = ensure_realtime_object(state, session_id);
    update_context(realtime, context_id, |value| {
        let existing = value
            .get("turn_id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty());
        if existing.is_some_and(|existing| existing != turn_id) {
            outcome = Some(Err(format!(
                "realtime context turn_id conflict: context_id={context_id}"
            )));
            return;
        }
        value.insert("turn_id".to_string(), json!(turn_id));
        outcome = serde_json::from_value::<RealtimeContext>(Value::Object(value.clone()))
            .map_err(|error| format!("invalid realtime context: {error}"))
            .into();
    });
    outcome.unwrap_or_else(|| Err(format!("realtime context not found: {context_id}")))
}

pub fn generation_scan_anchor(state: &Value, session_id: &str) -> u64 {
    session_realtime(state, session_id)
        .get("generation")
        .and_then(|generation| generation.get("scan_anchor"))
        .and_then(Value::as_u64)
        .unwrap_or(0)
}

pub fn generation_scan_base_ordinal(state: &Value, session_id: &str) -> u64 {
    session_realtime(state, session_id)
        .get("generation")
        .and_then(|generation| generation.get("scan_base_ordinal"))
        .and_then(Value::as_u64)
        .unwrap_or(0)
}

pub fn generation_discovery_offset(state: &Value, session_id: &str) -> u64 {
    let realtime = session_realtime(state, session_id);
    let generation = realtime.get("generation");
    let committed = generation
        .and_then(|value| value.get("scan_anchor"))
        .and_then(Value::as_u64)
        .unwrap_or(0);
    generation
        .and_then(|value| value.get("discovery_offset"))
        .and_then(Value::as_u64)
        .unwrap_or(committed)
        .max(committed)
}

pub fn generation_native_usage_mode_at(state: &Value, session_id: &str, offset: u64) -> bool {
    let generation = session_realtime(state, session_id);
    let generation = generation.get("generation");
    let native_usage_mode = generation
        .and_then(|generation| generation.get("native_usage_mode"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if !native_usage_mode {
        return false;
    }
    generation
        .and_then(|generation| generation.get("native_usage_mode_effective_offset"))
        .and_then(Value::as_u64)
        .is_none_or(|effective_offset| effective_offset <= offset)
}

pub fn generation_native_usage_mode(state: &Value, session_id: &str) -> bool {
    generation_native_usage_mode_at(state, session_id, u64::MAX)
}

pub fn generation_native_usage_mode_effective_offset(
    state: &Value,
    session_id: &str,
) -> Option<u64> {
    session_realtime(state, session_id)
        .get("generation")
        .and_then(|generation| generation.get("native_usage_mode_effective_offset"))
        .and_then(Value::as_u64)
}

pub fn generation_native_usage_prefix_checked_offset(
    state: &Value,
    session_id: &str,
) -> Option<u64> {
    session_realtime(state, session_id)
        .get("generation")
        .and_then(|generation| generation.get("native_usage_prefix_checked_offset"))
        .and_then(Value::as_u64)
}

pub fn generation_source_epoch(state: &Value, session_id: &str) -> Option<TranscriptSourceEpoch> {
    session_realtime(state, session_id)
        .get("generation")
        .and_then(|generation| generation.get("source_epoch"))
        .and_then(|value| serde_json::from_value(value.clone()).ok())
}

pub fn generation_sticky_terminal(
    state: &Value,
    session_id: &str,
) -> Option<GenerationScanTerminal> {
    session_realtime(state, session_id)
        .get("generation")
        .and_then(|generation| generation.get("sticky_terminal"))
        .and_then(|value| serde_json::from_value(value.clone()).ok())
}

pub fn mark_generation_native_usage_mode_in_state(
    state: &mut Value,
    session_id: &str,
    effective_offset: u64,
) -> Result<(), String> {
    let realtime = ensure_realtime_object(state, session_id);
    let generation = realtime
        .get_mut("generation")
        .and_then(Value::as_object_mut)
        .ok_or("realtime generation state is missing")?;
    generation.insert("native_usage_mode".to_string(), json!(true));
    let current = generation
        .get("native_usage_mode_effective_offset")
        .and_then(Value::as_u64);
    if current.is_none_or(|current| effective_offset < current) {
        generation.insert(
            "native_usage_mode_effective_offset".to_string(),
            json!(effective_offset),
        );
    }
    Ok(())
}

pub fn mark_generation_native_usage_prefix_checked_in_state(
    state: &mut Value,
    session_id: &str,
    checked_offset: u64,
) -> Result<(), String> {
    let realtime = ensure_realtime_object(state, session_id);
    let generation = realtime
        .get_mut("generation")
        .and_then(Value::as_object_mut)
        .ok_or("realtime generation state is missing")?;
    let current = generation
        .get("native_usage_prefix_checked_offset")
        .and_then(Value::as_u64);
    if current.is_none_or(|current| checked_offset > current) {
        generation.insert(
            "native_usage_prefix_checked_offset".to_string(),
            json!(checked_offset),
        );
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
pub fn checkpoint_generation_discovery_in_state(
    state: &mut Value,
    session_id: &str,
    expected_committed: u64,
    discovery_offset: u64,
    observed_file_len: u64,
    source_epoch: &TranscriptSourceEpoch,
    outcome: &str,
) -> Result<(), String> {
    if discovery_offset < expected_committed || discovery_offset > observed_file_len {
        return Err("generation discovery checkpoint violates offset bounds".to_string());
    }
    let realtime = ensure_realtime_object(state, session_id);
    let generation = realtime
        .get_mut("generation")
        .and_then(Value::as_object_mut)
        .ok_or("realtime generation state is missing")?;
    let committed = generation
        .get("scan_anchor")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    if committed != expected_committed {
        return Err("generation committed cursor changed during discovery".to_string());
    }
    let previous_discovery = generation
        .get("discovery_offset")
        .and_then(Value::as_u64)
        .unwrap_or(committed);
    if discovery_offset < previous_discovery {
        return Err("generation discovery cursor cannot move backwards".to_string());
    }
    if let Some(existing) = generation
        .get("source_epoch")
        .and_then(|value| serde_json::from_value::<TranscriptSourceEpoch>(value.clone()).ok())
    {
        if existing != *source_epoch {
            return Err("generation transcript source epoch changed".to_string());
        }
    }
    generation.insert("discovery_offset".to_string(), json!(discovery_offset));
    generation.insert("source_epoch".to_string(), json!(source_epoch));
    generation.insert("last_scan_outcome".to_string(), json!(outcome));
    Ok(())
}

pub fn mark_generation_scan_terminal_in_state(
    state: &mut Value,
    session_id: &str,
    source_epoch: &TranscriptSourceEpoch,
    record_start: u64,
    reason: &str,
    observed_bytes: u64,
) -> Result<(), String> {
    let realtime = ensure_realtime_object(state, session_id);
    let generation = realtime
        .get_mut("generation")
        .and_then(Value::as_object_mut)
        .ok_or("realtime generation state is missing")?;
    generation.insert("source_epoch".to_string(), json!(source_epoch));
    generation.insert(
        "sticky_terminal".to_string(),
        json!(GenerationScanTerminal {
            source_identity_digest: source_epoch.identity_digest.clone(),
            record_start,
            reason: reason.to_string(),
            observed_bytes,
        }),
    );
    generation.insert("last_scan_outcome".to_string(), json!("finalizer_only"));
    Ok(())
}

pub fn register_generation_identity_in_state(
    state: &mut Value,
    session_id: &str,
    context_id: &str,
    generation_id: &str,
) {
    let span_id = stable_generation_span_id(generation_id);
    let realtime = ensure_realtime_object(state, session_id);
    update_context(realtime, context_id, |context| {
        context
            .entry("generation_span_ids".to_string())
            .or_insert_with(|| json!({}))[generation_id] = json!(span_id);
    });
}

pub fn record_tool_start_in_state(
    state: &mut Value,
    session_id: &str,
    context_id: &str,
    tool_use_id: &str,
    started_at_unix_nano: &str,
) -> Result<(), String> {
    let started_at = parse_unix_nanos(started_at_unix_nano)
        .ok_or_else(|| "invalid realtime Tool start timestamp".to_string())?;
    let realtime = ensure_realtime_object(state, session_id);
    let mut found = false;
    update_context(realtime, context_id, |context| {
        found = true;
        let timings = context
            .entry("tool_timings".to_string())
            .or_insert_with(|| json!({}));
        let Some(timings) = timings.as_object_mut() else {
            return;
        };
        let existing = timings
            .get(tool_use_id)
            .and_then(|value| serde_json::from_value::<RealtimeToolTiming>(value.clone()).ok());
        let should_replace = existing
            .as_ref()
            .and_then(|timing| parse_unix_nanos(&timing.started_at_unix_nano))
            .map(|existing_start| started_at < existing_start)
            .unwrap_or(true);
        if should_replace {
            let mut timing = existing.unwrap_or_default();
            timing.started_at_unix_nano = started_at_unix_nano.to_string();
            timing.last_error = None;
            timings.insert(
                tool_use_id.to_string(),
                serde_json::to_value(timing).unwrap_or_else(|_| json!({})),
            );
        }
    });
    found
        .then_some(())
        .ok_or_else(|| format!("realtime context not found: {context_id}"))
}

pub fn complete_tool_timing_in_state(
    state: &mut Value,
    session_id: &str,
    tool_use_id: &str,
    completed_at_unix_nano: &str,
) -> Result<(RealtimeContext, RealtimeToolTiming), String> {
    let completed_at = parse_unix_nanos(completed_at_unix_nano)
        .ok_or_else(|| "invalid realtime Tool completion timestamp".to_string())?;
    let mut context = contexts(state, session_id)
        .into_iter()
        .find(|candidate| candidate.tool_use_ids.iter().any(|id| id == tool_use_id))
        .ok_or_else(|| format!("realtime context missing for tool_use_id={tool_use_id}"))?;
    let mut timing = context
        .tool_timings
        .get(tool_use_id)
        .cloned()
        .ok_or_else(|| format!("realtime Tool start missing for tool_use_id={tool_use_id}"))?;
    let started_at = parse_unix_nanos(&timing.started_at_unix_nano)
        .ok_or_else(|| format!("invalid realtime Tool start for tool_use_id={tool_use_id}"))?;

    let persisted_completed_at = timing
        .completed_at_unix_nano
        .as_deref()
        .and_then(parse_unix_nanos);
    let effective_completed_at = persisted_completed_at.unwrap_or(completed_at);
    if effective_completed_at <= started_at {
        timing.last_error = Some("invalid_or_nonpositive_interval".to_string());
        context
            .tool_timings
            .insert(tool_use_id.to_string(), timing.clone());
        persist_context_timing(state, session_id, &context, tool_use_id, &timing);
        return Err(format!(
            "realtime Tool interval is not positive for tool_use_id={tool_use_id}"
        ));
    }

    if timing.completed_at_unix_nano.is_none() {
        timing.completed_at_unix_nano = Some(completed_at_unix_nano.to_string());
    }
    timing.last_error = None;
    context
        .tool_timings
        .insert(tool_use_id.to_string(), timing.clone());
    persist_context_timing(state, session_id, &context, tool_use_id, &timing);
    Ok((context, timing))
}

pub fn register_realtime_job_in_state(
    state: &mut Value,
    session_id: &str,
    context_id: &str,
    kind: &str,
    job_id: &str,
    payload_digest: &str,
) -> Result<u64, String> {
    let realtime = ensure_realtime_object(state, session_id);
    if let Some(existing) = realtime
        .get("registered_jobs")
        .and_then(Value::as_object)
        .and_then(|registrations| registrations.get(job_id))
        .and_then(read_registered_job)
    {
        if existing.payload_digest != payload_digest
            || existing.context_id != context_id
            || existing.kind != kind
        {
            return Err(format!(
                "realtime job registration conflict for job_id={job_id}"
            ));
        }
        return Ok(existing.sequence);
    }

    let next_sequence = realtime
        .get("next_sequence")
        .and_then(Value::as_u64)
        .unwrap_or(1)
        .max(1);
    let registrations = realtime
        .entry("registered_jobs".to_string())
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or("realtime registered_jobs is not an object")?;
    let registration = RealtimeRegisteredJob {
        job_id: job_id.to_string(),
        payload_digest: payload_digest.to_string(),
        context_id: context_id.to_string(),
        kind: kind.to_string(),
        sequence: next_sequence,
        ..RealtimeRegisteredJob::default()
    };
    registrations.insert(
        job_id.to_string(),
        serde_json::to_value(registration).map_err(|err| err.to_string())?,
    );
    realtime.insert(
        "next_sequence".to_string(),
        json!(next_sequence.saturating_add(1)),
    );
    Ok(next_sequence)
}

pub fn persist_tool_job_registration_in_state(
    state: &mut Value,
    session_id: &str,
    context_id: &str,
    tool_use_id: &str,
    job_id: &str,
    payload_digest: &str,
) {
    let realtime = ensure_realtime_object(state, session_id);
    update_context(realtime, context_id, |context| {
        let tool_use_ids = context
            .entry("tool_use_ids".to_string())
            .or_insert_with(|| json!([]))
            .as_array_mut()
            .expect("tool_use_ids initialized as array");
        if !tool_use_ids
            .iter()
            .any(|candidate| candidate.as_str() == Some(tool_use_id))
        {
            tool_use_ids.push(json!(tool_use_id));
        }
        context
            .entry("tool_span_ids".to_string())
            .or_insert_with(|| json!({}))[tool_use_id] =
            json!(deterministic_tool_span_id(tool_use_id));
        context
            .entry("tool_jobs".to_string())
            .or_insert_with(|| json!({}))[tool_use_id] = json!({
            "status": "queued",
            "job_id": job_id,
            "payload_digest": payload_digest,
            "claim_id": null,
            "lease_until_ms": 0,
            "attempts": 0,
        });
    });
}

pub fn registered_realtime_job_matches(
    state: &Value,
    session_id: &str,
    job_id: &str,
    payload_digest: &str,
) -> bool {
    registered_realtime_job(state, session_id, job_id)
        .is_some_and(|job| job.payload_digest == payload_digest)
}

pub fn registered_realtime_job_claimable(
    state: &Value,
    session_id: &str,
    job_id: &str,
    payload_digest: &str,
    now_ms: u64,
) -> bool {
    registered_realtime_job(state, session_id, job_id).is_some_and(|job| {
        job.payload_digest == payload_digest
            && (job.status == RealtimeRegisteredJobStatus::Queued
                || (job.status == RealtimeRegisteredJobStatus::Inflight
                    && job.lease_until_ms <= now_ms))
    })
}

pub fn registered_realtime_job(
    state: &Value,
    session_id: &str,
    job_id: &str,
) -> Option<RealtimeRegisteredJob> {
    session_realtime(state, session_id)
        .get("registered_jobs")
        .and_then(Value::as_object)
        .and_then(|jobs| jobs.get(job_id))
        .and_then(read_registered_job)
}

pub fn retry_policy_for_context(
    state: &Value,
    session_id: &str,
    context_id: &str,
) -> Option<RetryPolicy> {
    contexts(state, session_id)
        .into_iter()
        .find(|context| context.context_id == context_id)
        .and_then(|context| RetryPolicy::parse(&context.retry_policy))
}

pub fn finalizer_high_water(state: &Value, session_id: &str) -> u64 {
    session_realtime(state, session_id)
        .get("registered_jobs")
        .and_then(Value::as_object)
        .map(|jobs| {
            jobs.values()
                .filter_map(read_registered_job)
                .filter(|job| matches!(job.kind.as_str(), "tool" | "generation" | "batch"))
                .map(|job| job.sequence)
                .max()
                .unwrap_or(0)
        })
        .unwrap_or(0)
}

pub fn record_finalizer_high_water_in_state(state: &mut Value, session_id: &str) -> u64 {
    let high_water = finalizer_high_water(state, session_id);
    let realtime = ensure_realtime_object(state, session_id);
    realtime.insert("finalizer_high_water".to_string(), json!(high_water));
    high_water
}

pub fn begin_realtime_delivery_in_state(
    state: &mut Value,
    session_id: &str,
    job_id: &str,
    payload_digest: &str,
    retry_policy: RetryPolicy,
    claim_id: &str,
    lease_until_ms: u64,
) -> Result<(), String> {
    let existing = registered_realtime_job(state, session_id, job_id)
        .ok_or_else(|| format!("realtime job is not registered: {job_id}"))?;
    if existing.payload_digest != payload_digest {
        return Err(format!("realtime job digest mismatch: {job_id}"));
    }
    if !matches!(
        existing.status,
        RealtimeRegisteredJobStatus::Queued | RealtimeRegisteredJobStatus::Inflight
    ) {
        return Err(format!(
            "realtime job is not claimable: {job_id} status={:?}",
            existing.status
        ));
    }
    update_registered_job(state, session_id, job_id, payload_digest, |job| {
        job.attempts = job.attempts.saturating_add(1);
        job.claim_id = Some(claim_id.to_string());
        job.lease_until_ms = lease_until_ms;
        job.last_error = None;
        job.status = match retry_policy {
            RetryPolicy::AtMostOnce => RealtimeRegisteredJobStatus::PolicyTerminal,
            RetryPolicy::AtLeastOnce => RealtimeRegisteredJobStatus::Inflight,
        };
    })
}

pub fn begin_direct_realtime_delivery_in_state(
    state: &mut Value,
    session_id: &str,
    job_id: &str,
    payload_digest: &str,
    retry_policy: RetryPolicy,
) -> Result<(), String> {
    let existing = registered_realtime_job(state, session_id, job_id)
        .ok_or_else(|| format!("realtime job is not registered: {job_id}"))?;
    if existing.payload_digest != payload_digest {
        return Err(format!("realtime job digest mismatch: {job_id}"));
    }
    if !matches!(
        existing.status,
        RealtimeRegisteredJobStatus::Queued | RealtimeRegisteredJobStatus::Inflight
    ) {
        return Err(format!(
            "realtime direct job is not runnable: {job_id} status={:?}",
            existing.status
        ));
    }
    update_registered_job(state, session_id, job_id, payload_digest, |job| {
        job.attempts = job.attempts.saturating_add(1);
        job.claim_id = None;
        job.lease_until_ms = 0;
        job.last_error = None;
        job.status = match retry_policy {
            RetryPolicy::AtMostOnce => RealtimeRegisteredJobStatus::PolicyTerminal,
            RetryPolicy::AtLeastOnce => RealtimeRegisteredJobStatus::Inflight,
        };
    })
}

pub fn complete_realtime_delivery_in_state(
    state: &mut Value,
    session_id: &str,
    job_id: &str,
    payload_digest: &str,
) -> Result<(), String> {
    let existing = registered_realtime_job(state, session_id, job_id)
        .ok_or_else(|| format!("realtime job is not registered: {job_id}"))?;
    if !matches!(
        existing.status,
        RealtimeRegisteredJobStatus::Inflight | RealtimeRegisteredJobStatus::PolicyTerminal
    ) {
        return Err(format!(
            "realtime job cannot be completed: {job_id} status={:?}",
            existing.status
        ));
    }
    update_registered_job(state, session_id, job_id, payload_digest, |job| {
        job.status = RealtimeRegisteredJobStatus::Accounted;
        job.claim_id = None;
        job.lease_until_ms = 0;
        job.last_error = None;
    })
}

pub fn fail_realtime_delivery_in_state(
    state: &mut Value,
    session_id: &str,
    job_id: &str,
    payload_digest: &str,
    retry_policy: RetryPolicy,
    error: &str,
    retry: bool,
    permanent: bool,
) -> Result<(), String> {
    let existing = registered_realtime_job(state, session_id, job_id)
        .ok_or_else(|| format!("realtime job is not registered: {job_id}"))?;
    if !matches!(
        existing.status,
        RealtimeRegisteredJobStatus::Inflight | RealtimeRegisteredJobStatus::PolicyTerminal
    ) {
        return Err(format!(
            "realtime job cannot fail delivery: {job_id} status={:?}",
            existing.status
        ));
    }
    update_registered_job(state, session_id, job_id, payload_digest, |job| {
        job.claim_id = None;
        job.lease_until_ms = 0;
        job.last_error = Some(error.to_string());
        job.status = if permanent {
            RealtimeRegisteredJobStatus::PermanentFailed
        } else {
            match retry_policy {
                RetryPolicy::AtMostOnce => RealtimeRegisteredJobStatus::PolicyTerminal,
                RetryPolicy::AtLeastOnce if retry => RealtimeRegisteredJobStatus::Queued,
                RetryPolicy::AtLeastOnce => RealtimeRegisteredJobStatus::Failed,
            }
        };
    })
}

pub fn failed_realtime_tool_job_ids(state: &Value, session_id: &str) -> HashSet<String> {
    session_realtime(state, session_id)
        .get("registered_jobs")
        .and_then(Value::as_object)
        .map(|jobs| {
            jobs.values()
                .filter_map(read_registered_job)
                .filter(|job| {
                    job.kind == "tool" && job.status == RealtimeRegisteredJobStatus::Failed
                })
                .map(|job| job.job_id)
                .collect()
        })
        .unwrap_or_default()
}

pub fn requeue_failed_realtime_job_in_state(
    state: &mut Value,
    session_id: &str,
    job_id: &str,
    payload_digest: &str,
) -> Result<(), String> {
    let existing = registered_realtime_job(state, session_id, job_id)
        .ok_or_else(|| format!("realtime job is not registered: {job_id}"))?;
    if existing.status != RealtimeRegisteredJobStatus::Failed {
        return Ok(());
    }
    update_registered_job(state, session_id, job_id, payload_digest, |job| {
        job.status = RealtimeRegisteredJobStatus::Queued;
        job.claim_id = None;
        job.lease_until_ms = 0;
    })
}

pub fn requeue_interrupted_realtime_job_in_state(
    state: &mut Value,
    session_id: &str,
    job_id: &str,
    payload_digest: &str,
) -> Result<(), String> {
    let existing = registered_realtime_job(state, session_id, job_id)
        .ok_or_else(|| format!("realtime job is not registered: {job_id}"))?;
    if existing.status != RealtimeRegisteredJobStatus::Inflight {
        return Ok(());
    }
    update_registered_job(state, session_id, job_id, payload_digest, |job| {
        job.status = RealtimeRegisteredJobStatus::Queued;
        job.claim_id = None;
        job.lease_until_ms = 0;
        job.last_error = Some("recovered interrupted delivery".to_string());
    })
}

pub fn compensate_realtime_job_in_state(
    state: &mut Value,
    session_id: &str,
    job_id: &str,
    payload_digest: &str,
) -> Result<(), String> {
    update_registered_job(state, session_id, job_id, payload_digest, |job| {
        job.status = RealtimeRegisteredJobStatus::Compensated;
        job.claim_id = None;
        job.lease_until_ms = 0;
    })
}

pub fn finalizer_barrier_satisfied(state: &Value, session_id: &str, high_water: u64) -> bool {
    session_realtime(state, session_id)
        .get("registered_jobs")
        .and_then(Value::as_object)
        .map(|jobs| {
            jobs.values().filter_map(read_registered_job).all(|job| {
                job.sequence > high_water
                    || matches!(
                        job.status,
                        RealtimeRegisteredJobStatus::Accounted
                            | RealtimeRegisteredJobStatus::Failed
                            | RealtimeRegisteredJobStatus::PermanentFailed
                            | RealtimeRegisteredJobStatus::PolicyTerminal
                            | RealtimeRegisteredJobStatus::Compensated
                    )
            })
        })
        .unwrap_or(true)
}

fn update_registered_job<F>(
    state: &mut Value,
    session_id: &str,
    job_id: &str,
    payload_digest: &str,
    update: F,
) -> Result<(), String>
where
    F: FnOnce(&mut RealtimeRegisteredJob),
{
    let realtime = ensure_realtime_object(state, session_id);
    let jobs = realtime
        .get_mut("registered_jobs")
        .and_then(Value::as_object_mut)
        .ok_or_else(|| format!("realtime job is not registered: {job_id}"))?;
    let value = jobs
        .get_mut(job_id)
        .ok_or_else(|| format!("realtime job is not registered: {job_id}"))?;
    let mut job = read_registered_job(value)
        .ok_or_else(|| format!("invalid realtime job registration: {job_id}"))?;
    if job.payload_digest != payload_digest {
        return Err(format!("realtime job digest mismatch: {job_id}"));
    }
    update(&mut job);
    *value = serde_json::to_value(job).map_err(|err| err.to_string())?;
    Ok(())
}

fn read_registered_job(value: &Value) -> Option<RealtimeRegisteredJob> {
    serde_json::from_value(value.clone()).ok()
}

fn read_realtime_batch(value: &Value) -> Option<RealtimeBatchLedger> {
    serde_json::from_value(value.clone()).ok()
}

fn read_realtime_delivery_plan(value: &Value) -> Option<RealtimeDeliveryPlan> {
    serde_json::from_value(value.clone()).ok()
}

fn canonical_batch_child_ids(child_ids: &[String]) -> Vec<String> {
    let mut child_ids: Vec<String> = child_ids
        .iter()
        .map(|child_id| child_id.trim())
        .filter(|child_id| !child_id.is_empty())
        .map(str::to_string)
        .collect();
    child_ids.sort();
    child_ids.dedup();
    child_ids
}

fn ensure_realtime_child_compensation_entries(
    realtime: &mut serde_json::Map<String, Value>,
    child_ids: &[String],
) -> Result<(), String> {
    let children = realtime
        .entry("children".to_string())
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or("realtime children is not an object")?;
    for child_id in child_ids {
        let child = children.entry(child_id.clone()).or_insert_with(|| {
            json!({
                "child_id": child_id,
                "post_compensation_used": false,
            })
        });
        let Some(child) = child.as_object_mut() else {
            return Err(format!(
                "realtime child ledger is not an object: {child_id}"
            ));
        };
        child
            .entry("child_id".to_string())
            .or_insert_with(|| json!(child_id));
        child
            .entry("post_compensation_used".to_string())
            .or_insert_with(|| json!(false));
    }
    Ok(())
}

fn mark_realtime_child_post_compensation_used(
    state: &mut Value,
    session_id: &str,
    child_id: &str,
) -> Result<(), String> {
    let realtime = ensure_realtime_object(state, session_id);
    ensure_realtime_child_compensation_entries(realtime, &[child_id.to_string()])?;
    let children = realtime
        .get_mut("children")
        .and_then(Value::as_object_mut)
        .ok_or("realtime children is not an object")?;
    let child = children
        .get_mut(child_id)
        .and_then(Value::as_object_mut)
        .ok_or_else(|| format!("realtime child ledger is not an object: {child_id}"))?;
    child.insert("post_compensation_used".to_string(), json!(true));
    Ok(())
}

fn update_realtime_batch<F>(
    state: &mut Value,
    session_id: &str,
    batch_id: &str,
    update: F,
) -> Result<RealtimeBatchLedger, String>
where
    F: FnOnce(&mut RealtimeBatchLedger) -> Result<(), String>,
{
    let realtime = ensure_realtime_object(state, session_id);
    let batches = realtime
        .get_mut("batches")
        .and_then(Value::as_object_mut)
        .ok_or_else(|| format!("realtime batch is not prepared: {batch_id}"))?;
    let value = batches
        .get_mut(batch_id)
        .ok_or_else(|| format!("realtime batch is not prepared: {batch_id}"))?;
    let mut batch = read_realtime_batch(value)
        .ok_or_else(|| format!("invalid realtime batch ledger: {batch_id}"))?;
    update(&mut batch)?;
    *value = serde_json::to_value(&batch).map_err(|error| error.to_string())?;
    Ok(batch)
}

fn update_realtime_batch_child<F>(
    state: &mut Value,
    session_id: &str,
    batch_id: &str,
    child_id: &str,
    update: F,
) -> Result<RealtimeBatchChildLedger, String>
where
    F: FnOnce(&mut RealtimeBatchChildLedger) -> Result<(), String>,
{
    let mut updated_child = None;
    update_realtime_batch(state, session_id, batch_id, |batch| {
        let child = batch.children.get_mut(child_id).ok_or_else(|| {
            format!("realtime batch child not found: batch_id={batch_id} child_id={child_id}")
        })?;
        update(child)?;
        updated_child = Some(child.clone());
        Ok(())
    })?;
    updated_child.ok_or_else(|| {
        format!("realtime batch child not found: batch_id={batch_id} child_id={child_id}")
    })
}

fn add_tool_identity(context: &mut RealtimeContext, tool_use_id: &str) {
    if !context.tool_use_ids.iter().any(|id| id == tool_use_id) {
        context.tool_use_ids.push(tool_use_id.to_string());
    }
    context
        .tool_span_ids
        .entry(tool_use_id.to_string())
        .or_insert_with(|| stable_tool_span_id(tool_use_id));
    context
        .tool_jobs
        .entry(tool_use_id.to_string())
        .or_insert_with(|| RealtimeJobSnapshot {
            status: RealtimeJobStatus::Discovered,
            ..RealtimeJobSnapshot::default()
        });
}

fn persist_current(state: &mut Value, session_id: &str, context: &RealtimeContext) {
    let Ok(value) = serde_json::to_value(context) else {
        return;
    };
    let realtime = ensure_realtime_object(state, session_id);
    realtime.insert(
        "schema_version".to_string(),
        json!(REALTIME_STATE_SCHEMA_VERSION),
    );
    realtime.insert("current".to_string(), value);
    realtime
        .entry("pending".to_string())
        .or_insert_with(|| json!([]));
}

fn remove_current(state: &mut Value, session_id: &str) {
    if let Some(realtime) = state
        .get_mut(session_id)
        .and_then(Value::as_object_mut)
        .and_then(|session| session.get_mut("realtime_export"))
        .and_then(Value::as_object_mut)
    {
        realtime.remove("current");
    }
}

fn read_context(value: &Value) -> Option<RealtimeContext> {
    let mut context: RealtimeContext = serde_json::from_value(value.clone()).ok()?;
    if context.schema_version == 0 {
        context.schema_version = REALTIME_STATE_SCHEMA_VERSION;
    }
    if context.generation_batch_size == 0 {
        context.generation_batch_size = DEFAULT_REALTIME_LLM_BATCH_SIZE;
    }
    if context.retry_policy.is_empty() {
        context.retry_policy = RetryPolicy::AtLeastOnce.as_str().to_string();
    }
    Some(context)
}

fn read_generation_state(value: &Value) -> Option<RealtimeGenerationState> {
    let mut generation: RealtimeGenerationState = serde_json::from_value(value.clone()).ok()?;
    if generation.batch_size == 0 {
        generation.batch_size = DEFAULT_REALTIME_LLM_BATCH_SIZE;
    }
    if generation.discovery_offset < generation.scan_anchor {
        generation.discovery_offset = generation.scan_anchor;
    }
    Some(generation)
}

fn read_generation_round(value: &Value) -> Option<GenerationRound> {
    serde_json::from_value(value.clone()).ok()
}

fn session_realtime(state: &Value, session_id: &str) -> Value {
    state
        .get(session_id)
        .and_then(|session| session.get("realtime_export"))
        .cloned()
        .filter(Value::is_object)
        .unwrap_or_else(|| json!({}))
}

fn ensure_realtime_object<'a>(
    state: &'a mut Value,
    session_id: &str,
) -> &'a mut serde_json::Map<String, Value> {
    if !state.is_object() {
        *state = json!({});
    }
    if !state.get(session_id).map(Value::is_object).unwrap_or(false) {
        state[session_id] = json!({});
    }
    if state[session_id].get("realtime_export").is_none()
        || !state[session_id]["realtime_export"].is_object()
    {
        state[session_id]["realtime_export"] = json!({});
    }
    let realtime = state[session_id]["realtime_export"]
        .as_object_mut()
        .expect("realtime_export initialized as object");
    realtime
        .entry("schema_version".to_string())
        .or_insert_with(|| json!(REALTIME_STATE_SCHEMA_VERSION));
    realtime
}

fn allocate_context_sequence_in_state(state: &mut Value, session_id: &str) -> u64 {
    let observed_next = contexts(state, session_id)
        .into_iter()
        .map(|context| context.context_sequence)
        .max()
        .unwrap_or(0)
        .saturating_add(1)
        .max(1);
    let realtime = ensure_realtime_object(state, session_id);
    let sequence = realtime
        .get("next_context_sequence")
        .and_then(Value::as_u64)
        .unwrap_or(observed_next)
        .max(observed_next)
        .max(1);
    realtime.insert(
        "next_context_sequence".to_string(),
        json!(sequence.saturating_add(1)),
    );
    sequence
}

pub fn register_pending_flush_in_state(
    state: &mut Value,
    session_id: &str,
    job: &SpoolJob,
) -> Result<(), String> {
    let encoded = serde_json::to_value(job).map_err(|error| error.to_string())?;
    let pending = ensure_realtime_object(state, session_id)
        .entry("pending_flushes".to_string())
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or("realtime pending_flushes is not an object")?;
    match pending.get(&job.job_id) {
        Some(existing) if existing == &encoded => Ok(()),
        Some(_) => Err(format!("realtime pending Flush conflict: {}", job.job_id)),
        None => {
            pending.insert(job.job_id.clone(), encoded);
            Ok(())
        }
    }
}

pub fn pending_flush_jobs(state: &Value, session_id: &str) -> Vec<SpoolJob> {
    let mut jobs = session_realtime(state, session_id)
        .get("pending_flushes")
        .and_then(Value::as_object)
        .into_iter()
        .flat_map(|pending| pending.values())
        .filter_map(|value| serde_json::from_value::<SpoolJob>(value.clone()).ok())
        .collect::<Vec<_>>();
    jobs.sort_by(|left, right| {
        let left_anchor = left
            .flush_manifest
            .as_ref()
            .map(|manifest| manifest.scan_anchor)
            .unwrap_or(u64::MAX);
        let right_anchor = right
            .flush_manifest
            .as_ref()
            .map(|manifest| manifest.scan_anchor)
            .unwrap_or(u64::MAX);
        left_anchor
            .cmp(&right_anchor)
            .then_with(|| left.created_at.cmp(&right.created_at))
            .then_with(|| left.job_id.cmp(&right.job_id))
    });
    jobs
}

pub fn remove_pending_flush_in_state(
    state: &mut Value,
    session_id: &str,
    job_id: &str,
) -> Result<(), String> {
    let realtime = ensure_realtime_object(state, session_id);
    if let Some(pending) = realtime
        .get_mut("pending_flushes")
        .and_then(Value::as_object_mut)
    {
        pending.remove(job_id);
    }
    Ok(())
}

pub fn finalize_pending_flushes_in_state(
    state: &mut Value,
    session_id: &str,
    finalized_boundary: u64,
) -> usize {
    let realtime = ensure_realtime_object(state, session_id);
    let mut removed = 0usize;
    if let Some(pending) = realtime
        .get_mut("pending_flushes")
        .and_then(Value::as_object_mut)
    {
        let before = pending.len();
        pending.retain(|_, value| {
            value
                .get("flush_manifest")
                .and_then(|manifest| manifest.get("scan_anchor"))
                .and_then(Value::as_u64)
                .map(|scan_anchor| scan_anchor >= finalized_boundary)
                .unwrap_or(true)
        });
        removed = before.saturating_sub(pending.len());
    }
    if let Some(generation) = realtime
        .get_mut("generation")
        .and_then(Value::as_object_mut)
    {
        let terminal_is_covered = generation
            .get("sticky_terminal")
            .and_then(|terminal| terminal.get("record_start"))
            .and_then(Value::as_u64)
            .map(|record_start| record_start < finalized_boundary)
            .unwrap_or(false);
        if terminal_is_covered {
            generation.remove("sticky_terminal");
            generation.insert("last_scan_outcome".to_string(), json!("finalized"));
        }
    }
    removed
}

fn update_context<F>(realtime: &mut serde_json::Map<String, Value>, context_id: &str, update: F)
where
    F: FnOnce(&mut serde_json::Map<String, Value>),
{
    let mut update = Some(update);
    if let Some(current) = realtime.get_mut("current").and_then(Value::as_object_mut) {
        if current.get("context_id").and_then(Value::as_str) == Some(context_id) {
            if let Some(update) = update.take() {
                update(current);
            }
            return;
        }
    }
    if let Some(pending) = realtime.get_mut("pending").and_then(Value::as_array_mut) {
        for item in pending {
            let Some(object) = item.as_object_mut() else {
                continue;
            };
            if object.get("context_id").and_then(Value::as_str) == Some(context_id) {
                if let Some(update) = update.take() {
                    update(object);
                }
                return;
            }
        }
    }
}

fn persist_context_timing(
    state: &mut Value,
    session_id: &str,
    context: &RealtimeContext,
    tool_use_id: &str,
    timing: &RealtimeToolTiming,
) {
    let realtime = ensure_realtime_object(state, session_id);
    update_context(realtime, &context.context_id, |value| {
        value
            .entry("tool_timings".to_string())
            .or_insert_with(|| json!({}))[tool_use_id] =
            serde_json::to_value(timing).unwrap_or_else(|_| json!({}));
    });
}

fn parse_unix_nanos(value: &str) -> Option<u128> {
    value.parse::<u128>().ok().filter(|value| *value > 0)
}

fn stable_hex(domain: &str, value: &str, bytes: usize) -> String {
    let mut digest = Sha256::new();
    digest.update(domain.as_bytes());
    digest.update([0]);
    digest.update(value.as_bytes());
    let hex: String = digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    hex[..bytes * 2].to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn claim_for_turn(
        state: &mut Value,
        session_id: &str,
        tool_use_id: &str,
        turn_id: &str,
        created_at: &str,
        trace_id: &str,
    ) -> RealtimeContext {
        let context = claim_tool_in_state(
            state,
            session_id,
            tool_use_id,
            created_at,
            trace_id,
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        bind_turn_id_in_state(state, session_id, &context.context_id, Some(turn_id)).unwrap()
    }

    fn split_same_turn_state() -> (Value, RealtimeContext, RealtimeContext) {
        let mut state = json!({});
        let first = claim_for_turn(
            &mut state,
            "s",
            "exec-1",
            "turn-a",
            "2026-08-17T00:00:00Z",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        );
        rotate_current_in_state(&mut state, "s");
        let second = claim_for_turn(
            &mut state,
            "s",
            "exec-2",
            "turn-a",
            "2026-08-17T00:00:01Z",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        );
        (state, first, second)
    }

    fn publish_child_for_context(state: &mut Value, context: &RealtimeContext, child_id: &str) {
        let batch = prepare_realtime_batch_in_state(
            state,
            "s",
            &context.context_id,
            "tool",
            &[child_id.to_string()],
            &format!("digest-{child_id}"),
        )
        .unwrap();
        publish_realtime_batch_in_state(state, "s", &batch.batch_id).unwrap();
        succeed_realtime_batch_in_state(state, "s", &batch.batch_id).unwrap();
    }

    fn source_epoch(id: &str) -> TranscriptSourceEpoch {
        TranscriptSourceEpoch {
            identity_digest: id.to_string(),
            prefix_len: 4,
        }
    }

    #[test]
    fn legacy_generation_state_initializes_discovery_from_committed_anchor() {
        let generation = read_generation_state(&json!({
            "scan_anchor": 42,
            "committed_offset": 42,
            "scan_base_ordinal": 3,
            "window_consumed": 0,
            "batch_size": 1,
            "rounds": []
        }))
        .unwrap();
        assert_eq!(generation.discovery_offset, 42);
        assert!(generation.source_epoch.is_none());
        assert!(generation.sticky_terminal.is_none());
    }

    #[test]
    fn realtime_generation_state_round_trips_source_epoch_and_offsets() {
        let generation = RealtimeGenerationState {
            scan_anchor: 42,
            committed_offset: 42,
            discovery_offset: 128,
            source_epoch: Some(source_epoch("epoch-a")),
            sticky_terminal: None,
            last_scan_outcome: Some("progressed".to_string()),
            scan_base_ordinal: 3,
            window_consumed: 2,
            native_usage_mode: false,
            native_usage_mode_effective_offset: None,
            native_usage_prefix_checked_offset: None,
            batch_size: 4,
            rounds: Vec::new(),
            finalizer_only_ordinals: Vec::new(),
        };
        let encoded = serde_json::to_value(&generation).unwrap();
        assert_eq!(
            encoded["source_epoch"],
            json!({
                "identity_digest": "epoch-a",
                "prefix_len": 4
            })
        );
        for field in ["scan_anchor", "committed_offset", "discovery_offset"] {
            assert!(
                encoded[field].as_i64().is_some(),
                "{field} must stay an integer"
            );
        }
        let decoded: RealtimeGenerationState = serde_json::from_value(encoded.clone()).unwrap();
        assert_eq!(decoded, generation);
        assert_eq!(serde_json::to_value(decoded).unwrap(), encoded);
    }

    #[test]
    fn discovery_checkpoint_is_monotonic_and_does_not_commit() {
        let mut state = json!({});
        ensure_generation_state_in_state(&mut state, "s", 1);
        checkpoint_generation_discovery_in_state(
            &mut state,
            "s",
            0,
            128,
            256,
            &source_epoch("epoch-a"),
            "progressed",
        )
        .unwrap();
        assert_eq!(generation_discovery_offset(&state, "s"), 128);
        assert_eq!(generation_scan_anchor(&state, "s"), 0);
        assert!(checkpoint_generation_discovery_in_state(
            &mut state,
            "s",
            0,
            64,
            256,
            &source_epoch("epoch-a"),
            "progressed",
        )
        .unwrap_err()
        .contains("backwards"));
        assert!(checkpoint_generation_discovery_in_state(
            &mut state,
            "s",
            0,
            129,
            256,
            &source_epoch("epoch-b"),
            "progressed",
        )
        .unwrap_err()
        .contains("source epoch"));
    }

    #[test]
    fn successful_finalizer_prunes_pending_flushes_and_sticky_terminal() {
        let mut state = json!({});
        ensure_generation_state_in_state(&mut state, "s", 1);
        let realtime = ensure_realtime_object(&mut state, "s");
        realtime.insert(
            "pending_flushes".to_string(),
            json!({
                "job-covered": {"flush_manifest": {"scan_anchor": 40}},
                "job-at-boundary": {"flush_manifest": {"scan_anchor": 100}},
                "job-future": {"flush_manifest": {"scan_anchor": 120}},
                "job-unknown": {}
            }),
        );
        mark_generation_scan_terminal_in_state(
            &mut state,
            "s",
            &source_epoch("epoch-a"),
            7,
            "record_too_large",
            65,
        )
        .unwrap();
        assert_eq!(finalize_pending_flushes_in_state(&mut state, "s", 100), 1);
        let pending = state["s"]["realtime_export"]["pending_flushes"]
            .as_object()
            .unwrap();
        assert!(!pending.contains_key("job-covered"));
        assert!(pending.contains_key("job-at-boundary"));
        assert!(pending.contains_key("job-future"));
        assert!(pending.contains_key("job-unknown"));
        assert!(generation_sticky_terminal(&state, "s").is_none());
        assert_eq!(
            state["s"]["realtime_export"]["generation"]["last_scan_outcome"],
            "finalized"
        );
    }

    #[test]
    fn successful_finalizer_retains_sticky_terminal_beyond_boundary() {
        let mut state = json!({});
        ensure_generation_state_in_state(&mut state, "s", 1);
        mark_generation_scan_terminal_in_state(
            &mut state,
            "s",
            &source_epoch("epoch-a"),
            100,
            "record_too_large",
            65,
        )
        .unwrap();

        assert_eq!(finalize_pending_flushes_in_state(&mut state, "s", 100), 0);
        assert!(generation_sticky_terminal(&state, "s").is_some());
    }

    #[test]
    fn claim_tool_persists_versioned_context_and_snapshots_policy() {
        let mut state = json!({"s": {"offset": 12, "buffer": "tail"}});
        let context = claim_tool_in_state(
            &mut state,
            "s",
            "call-1",
            "now",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            Some("00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"),
            Some("bbbbbbbbbbbbbbbb"),
            Some("sofa-trace"),
            Some("0.1"),
            5,
            RetryPolicy::AtMostOnce,
        );

        assert_eq!(context.schema_version, REALTIME_STATE_SCHEMA_VERSION);
        assert_eq!(context.trace_id, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        assert_eq!(context.generation_batch_size, 5);
        assert_eq!(context.retry_policy, "at_most_once");
        assert_eq!(
            context.tool_span_ids["call-1"],
            stable_tool_span_id("call-1")
        );
        assert_eq!(
            context.tool_jobs["call-1"].status,
            RealtimeJobStatus::Discovered
        );
        assert_eq!(state["s"]["offset"], 12);
        assert_eq!(state["s"]["realtime_export"]["schema_version"], 1);
    }

    #[test]
    fn duplicate_tool_claim_and_prompt_rotation_are_idempotent() {
        let mut state = json!({});
        let first = claim_tool_in_state(
            &mut state,
            "s",
            "call-1",
            "first",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        let repeated = claim_tool_in_state(
            &mut state,
            "s",
            "call-1",
            "later",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            9,
            RetryPolicy::AtMostOnce,
        );
        assert_eq!(first, repeated);

        rotate_current_in_state(&mut state, "s");
        rotate_current_in_state(&mut state, "s");
        let contexts = contexts(&state, "s");
        assert_eq!(contexts.len(), 1);
        assert_eq!(contexts[0].context_id, first.context_id);
        assert_eq!(contexts[0].tool_use_ids, vec!["call-1"]);
    }

    #[test]
    fn new_realtime_contexts_receive_monotonic_context_sequences() {
        let mut state = json!({});
        let first = claim_tool_in_state(
            &mut state,
            "s",
            "call-1",
            "2026-08-17T00:00:00Z",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        rotate_current_in_state(&mut state, "s");
        let second = claim_tool_in_state(
            &mut state,
            "s",
            "call-2",
            "2026-08-17T00:00:01Z",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );

        assert_eq!(first.context_sequence, 1);
        assert_eq!(second.context_sequence, 2);
        assert_eq!(state["s"]["realtime_export"]["next_context_sequence"], 3);
    }

    #[test]
    fn publication_evidence_requires_successful_non_empty_children() {
        let mut state = json!({});
        let context = claim_tool_in_state(
            &mut state,
            "s",
            "call-1",
            "now",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        register_realtime_job_in_state(
            &mut state,
            "s",
            &context.context_id,
            "batch",
            "job-1",
            "digest-1",
        )
        .unwrap();
        assert!(!context_publication_evidence(&state, "s", &context.context_id).is_published());

        let batch = prepare_realtime_batch_in_state(
            &mut state,
            "s",
            &context.context_id,
            "tool",
            &["child-1".to_string()],
            "digest-1",
        )
        .unwrap();
        publish_realtime_batch_in_state(&mut state, "s", &batch.batch_id).unwrap();
        assert!(!context_publication_evidence(&state, "s", &context.context_id).is_published());

        succeed_realtime_batch_in_state(&mut state, "s", &batch.batch_id).unwrap();
        let evidence = context_publication_evidence(&state, "s", &context.context_id);
        assert_eq!(evidence.child_ids, HashSet::from(["child-1".to_string()]));
        assert!(evidence.is_published());
    }

    #[test]
    fn context_alias_resolution_is_transitive_and_fail_closed() {
        let mut state = json!({});
        let first = claim_tool_in_state(
            &mut state,
            "s",
            "call-1",
            "first",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        rotate_current_in_state(&mut state, "s");
        let second = claim_tool_in_state(
            &mut state,
            "s",
            "call-2",
            "second",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        rotate_current_in_state(&mut state, "s");
        let third = claim_tool_in_state(
            &mut state,
            "s",
            "call-3",
            "third",
            "cccccccccccccccccccccccccccccccc",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        state["s"]["realtime_export"]["context_aliases"] = json!({
            (first.context_id.clone()): second.context_id.clone(),
            (second.context_id.clone()): third.context_id.clone(),
        });
        assert_eq!(
            resolve_context_alias(&state, "s", &first.context_id).unwrap(),
            third.context_id
        );

        state["s"]["realtime_export"]["context_aliases"][third.context_id.as_str()] =
            json!(first.context_id);
        assert!(matches!(
            resolve_context_alias(&state, "s", &second.context_id),
            Err(RealtimeClaimError::AliasCycle { .. })
        ));

        state["s"]["realtime_export"]["context_aliases"] = json!({
            (second.context_id.clone()): "missing-context",
        });
        assert!(matches!(
            resolve_context_alias(&state, "s", &second.context_id),
            Err(RealtimeClaimError::MissingAliasTarget { .. })
        ));
    }

    #[test]
    fn claim_native_turn_reuses_pending_context_after_prompt_rotation() {
        let mut state = json!({});
        let first = claim_for_turn(
            &mut state,
            "s",
            "exec-1",
            "turn-a",
            "first",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        );
        rotate_current_in_state(&mut state, "s");

        let claimed = claim_tool_for_native_turn_in_state(
            &mut state,
            "s",
            "exec-2",
            Some("turn-a"),
            "second",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        )
        .unwrap();

        assert_eq!(claimed.action, RealtimeClaimAction::ReusedExactPending);
        assert_eq!(claimed.context.context_id, first.context_id);
        assert_eq!(claimed.context.trace_id, first.trace_id);
        assert_eq!(claimed.context.root_span_id, first.root_span_id);
        assert_eq!(contexts(&state, "s").len(), 1);
        assert_eq!(claimed.context.tool_use_ids, vec!["exec-1", "exec-2"]);
    }

    #[test]
    fn claim_native_turn_creates_new_context_for_real_next_turn() {
        let mut state = json!({});
        let first = claim_for_turn(
            &mut state,
            "s",
            "exec-1",
            "turn-a",
            "first",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        );
        let second = claim_tool_for_native_turn_in_state(
            &mut state,
            "s",
            "exec-2",
            Some("turn-b"),
            "second",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        )
        .unwrap();

        assert_eq!(second.action, RealtimeClaimAction::CreatedNewTurn);
        assert_ne!(second.context.context_id, first.context_id);
        let contexts = contexts(&state, "s");
        assert_eq!(contexts.len(), 2);
        assert!(contexts
            .iter()
            .any(|context| context.turn_id.as_deref() == Some("turn-a")));
        assert!(contexts
            .iter()
            .any(|context| context.turn_id.as_deref() == Some("turn-b")));
    }

    #[test]
    fn claim_native_turn_binds_unbound_current_without_extra_context() {
        let mut state = json!({});
        let first = claim_tool_in_state(
            &mut state,
            "s",
            "exec-1",
            "first",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        let claimed = claim_tool_for_native_turn_in_state(
            &mut state,
            "s",
            "exec-2",
            Some("turn-a"),
            "second",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        )
        .unwrap();

        assert_eq!(claimed.action, RealtimeClaimAction::BoundUnboundCurrent);
        assert_eq!(claimed.context.context_id, first.context_id);
        assert_eq!(claimed.context.turn_id.as_deref(), Some("turn-a"));
        assert_eq!(contexts(&state, "s").len(), 1);
    }

    #[test]
    fn canonicalize_same_turn_split_prefers_single_published_owner() {
        let (mut state, first, second) = split_same_turn_state();
        publish_child_for_context(&mut state, &first, "child-1");
        register_realtime_job_in_state(
            &mut state,
            "s",
            &second.context_id,
            "tool",
            "loser-job",
            "loser-digest",
        )
        .unwrap();

        let canonical = canonicalize_turn_contexts_in_state(&mut state, "s", "turn-a")
            .unwrap()
            .unwrap();

        assert_eq!(
            canonical.action,
            RealtimeClaimAction::CanonicalizedPublishedOwner
        );
        assert_eq!(canonical.context.context_id, first.context_id);
        assert_eq!(contexts(&state, "s").len(), 1);
        assert_eq!(
            resolve_context_alias(&state, "s", &second.context_id).unwrap(),
            first.context_id
        );
        assert_eq!(
            registered_realtime_job(&state, "s", "loser-job")
                .unwrap()
                .context_id,
            first.context_id
        );
        assert_eq!(canonical.context.tool_use_ids, vec!["exec-1", "exec-2"]);
    }

    #[test]
    fn canonicalize_unpublished_split_uses_sequence_idempotently() {
        let (mut state, first, second) = split_same_turn_state();
        let canonical = canonicalize_turn_contexts_in_state(&mut state, "s", "turn-a")
            .unwrap()
            .unwrap();
        let once = state.clone();
        let repeated = canonicalize_turn_contexts_in_state(&mut state, "s", "turn-a")
            .unwrap()
            .unwrap();

        assert_eq!(
            canonical.action,
            RealtimeClaimAction::CanonicalizedUnpublishedSequence
        );
        assert_eq!(canonical.context.context_id, first.context_id);
        assert_eq!(repeated.context.context_id, first.context_id);
        assert_eq!(state, once);
        assert_eq!(
            resolve_context_alias(&state, "s", &second.context_id).unwrap(),
            first.context_id
        );
    }

    #[test]
    fn canonicalize_multiple_published_roots_is_side_effect_free() {
        let (mut state, first, second) = split_same_turn_state();
        publish_child_for_context(&mut state, &first, "child-1");
        publish_child_for_context(&mut state, &second, "child-2");
        assert_eq!(
            published_root_identities_for_turn(&state, "s", "turn-a"),
            vec![
                format!("{}/{}", first.trace_id, first.root_span_id),
                format!("{}/{}", second.trace_id, second.root_span_id),
            ]
        );
        let before = state.clone();

        let error = canonicalize_turn_contexts_in_state(&mut state, "s", "turn-a").unwrap_err();

        assert!(matches!(
            error,
            RealtimeClaimError::MultiplePublishedRoots { .. }
        ));
        assert_eq!(state, before);
    }

    #[test]
    fn canonicalize_conflicting_tool_facts_is_side_effect_free() {
        let (mut state, first, second) = split_same_turn_state();
        state["s"]["realtime_export"]["pending"][0]["tool_use_ids"] = json!(["shared"]);
        state["s"]["realtime_export"]["pending"][0]["tool_span_ids"] =
            json!({"shared": "1111111111111111"});
        state["s"]["realtime_export"]["current"]["tool_use_ids"] = json!(["shared"]);
        state["s"]["realtime_export"]["current"]["tool_span_ids"] =
            json!({"shared": "2222222222222222"});
        let before = state.clone();

        let error = canonicalize_turn_contexts_in_state(&mut state, "s", "turn-a").unwrap_err();

        assert!(matches!(error, RealtimeClaimError::FactConflict { .. }));
        assert_eq!(state, before);
        assert_ne!(first.context_id, second.context_id);
    }

    #[test]
    fn canonicalize_duplicate_serialized_context_is_not_multiple_published_roots() {
        let mut state = json!({});
        let context = claim_for_turn(
            &mut state,
            "s",
            "exec-1",
            "turn-a",
            "first",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        );
        publish_child_for_context(&mut state, &context, "child-1");
        let duplicate = state["s"]["realtime_export"]["current"].clone();
        state["s"]["realtime_export"]["pending"] = json!([duplicate]);

        let canonical = canonicalize_turn_contexts_in_state(&mut state, "s", "turn-a")
            .unwrap()
            .unwrap();

        assert_eq!(canonical.context.context_id, context.context_id);
        assert_eq!(contexts(&state, "s").len(), 1);
    }

    #[test]
    fn canonicalize_legacy_contexts_uses_created_at_then_slot_then_id() {
        let (mut state, first, second) = split_same_turn_state();
        state["s"]["realtime_export"]["pending"][0]["context_sequence"] = json!(0);
        state["s"]["realtime_export"]["current"]["context_sequence"] = json!(0);
        state["s"]["realtime_export"]["pending"][0]["created_at"] = json!("2026-01-01");
        state["s"]["realtime_export"]["current"]["created_at"] = json!("2026-01-02");

        let canonical = canonicalize_turn_contexts_in_state(&mut state, "s", "turn-a")
            .unwrap()
            .unwrap();

        assert_eq!(canonical.context.context_id, first.context_id);
        assert_ne!(canonical.context.context_id, second.context_id);
    }

    #[test]
    fn native_turn_binding_is_idempotent_strict_and_backward_compatible() {
        let mut state = json!({});
        let context = claim_tool_in_state(
            &mut state,
            "s",
            "exec-1",
            "now",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        assert_eq!(context.turn_id, None);

        let bound =
            bind_turn_id_in_state(&mut state, "s", &context.context_id, Some("turn-1")).unwrap();
        assert_eq!(bound.turn_id.as_deref(), Some("turn-1"));
        assert!(
            bind_turn_id_in_state(&mut state, "s", &context.context_id, Some("turn-1")).is_ok()
        );
        assert!(
            bind_turn_id_in_state(&mut state, "s", &context.context_id, Some("turn-2"))
                .unwrap_err()
                .contains("conflict")
        );
        assert_eq!(
            contexts(&state, "s")[0].turn_id.as_deref(),
            Some("turn-1"),
            "a conflicting hook must not replace the native turn identity"
        );
    }

    #[test]
    fn tool_timing_keeps_earliest_pre_and_first_valid_post() {
        let mut state = json!({});
        let context = claim_tool_in_state(
            &mut state,
            "s",
            "call-1",
            "now",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        record_tool_start_in_state(&mut state, "s", &context.context_id, "call-1", "1000000000")
            .unwrap();
        record_tool_start_in_state(&mut state, "s", &context.context_id, "call-1", "900000000")
            .unwrap();

        let (_, first) =
            complete_tool_timing_in_state(&mut state, "s", "call-1", "6150000000").unwrap();
        let (_, duplicate) =
            complete_tool_timing_in_state(&mut state, "s", "call-1", "7000000000").unwrap();

        assert_eq!(first.started_at_unix_nano, "900000000");
        assert_eq!(first.completed_at_unix_nano.as_deref(), Some("6150000000"));
        assert_eq!(duplicate, first);
        rotate_current_in_state(&mut state, "s");
        assert_eq!(
            contexts(&state, "s")[0].tool_timings["call-1"],
            first,
            "timing must rotate and clean up with its owning realtime context"
        );
    }

    #[test]
    fn invalid_tool_interval_defers_without_consuming_first_valid_post() {
        let mut state = json!({});
        let context = claim_tool_in_state(
            &mut state,
            "s",
            "call-1",
            "now",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        record_tool_start_in_state(&mut state, "s", &context.context_id, "call-1", "2000000000")
            .unwrap();

        assert!(complete_tool_timing_in_state(&mut state, "s", "call-1", "1000000000").is_err());
        let after_invalid = contexts(&state, "s").remove(0);
        assert_eq!(
            after_invalid.tool_timings["call-1"].completed_at_unix_nano,
            None
        );
        assert_eq!(
            after_invalid.tool_timings["call-1"].last_error.as_deref(),
            Some("invalid_or_nonpositive_interval")
        );

        let (_, recovered) =
            complete_tool_timing_in_state(&mut state, "s", "call-1", "3000000000").unwrap();
        assert_eq!(
            recovered.completed_at_unix_nano.as_deref(),
            Some("3000000000")
        );
        assert_eq!(recovered.last_error, None);
    }

    #[test]
    fn legacy_context_without_tool_timings_deserializes_with_empty_map() {
        let mut state = json!({});
        let context = claim_tool_in_state(
            &mut state,
            "s",
            "call-1",
            "now",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        state["s"]["realtime_export"]["current"]
            .as_object_mut()
            .unwrap()
            .remove("tool_timings");

        let restored = contexts(&state, "s").remove(0);
        assert_eq!(restored.context_id, context.context_id);
        assert!(restored.tool_timings.is_empty());
    }

    #[test]
    fn fallback_generation_identity_is_session_turn_scoped() {
        let id = stable_fallback_generation_id("s", "ctx-1", 2);
        assert_eq!(id, stable_fallback_generation_id("s", "ctx-1", 2));
        assert_ne!(id, stable_fallback_generation_id("s", "ctx-2", 2));
        assert_ne!(id, stable_fallback_generation_id("other", "ctx-1", 2));
        assert_eq!(stable_generation_span_id(&id).len(), 16);
    }

    #[test]
    fn generation_state_and_identity_are_initialized_without_replacing_legacy_state() {
        let mut state = json!({"s": {"turn_count": 7}});
        let context = claim_tool_in_state(
            &mut state,
            "s",
            "call-1",
            "now",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            4,
            RetryPolicy::AtLeastOnce,
        );
        let generation = ensure_generation_state_in_state(&mut state, "s", 4);
        register_generation_identity_in_state(&mut state, "s", &context.context_id, "msg-1");

        assert_eq!(generation.batch_size, 4);
        assert_eq!(state["s"]["turn_count"], 7);
        assert_eq!(
            state["s"]["realtime_export"]["current"]["generation_span_ids"]["msg-1"],
            stable_generation_span_id("msg-1")
        );
    }

    #[test]
    fn realtime_batch_id_and_membership_are_canonical() {
        let mut state = json!({});
        let unordered = vec![
            "child-b".to_string(),
            "child-a".to_string(),
            "child-b".to_string(),
        ];
        let ordered = vec!["child-a".to_string(), "child-b".to_string()];

        let batch =
            prepare_realtime_batch_in_state(&mut state, "s", "ctx", "generation", &unordered, "d")
                .unwrap();

        assert_eq!(batch.child_ids, ordered);
        assert_eq!(
            batch.batch_id,
            stable_realtime_batch_id("s", "ctx", "generation", "d", &ordered)
        );
        assert_ne!(
            batch.batch_id,
            stable_realtime_batch_id("s", "ctx", "generation", "changed", &ordered)
        );
        assert_eq!(batch.children["child-a"].ordinal, 0);
        assert_eq!(batch.children["child-b"].ordinal, 1);
        assert_eq!(
            state["s"]["realtime_export"]["batches"][&batch.batch_id]["children"]["child-a"]
                ["post_compensation_used"],
            false
        );
        assert_eq!(
            state["s"]["realtime_export"]["children"]["child-a"]["post_compensation_used"],
            false
        );
    }

    #[test]
    fn realtime_batch_prepare_is_idempotent_and_payload_bound() {
        let mut state = json!({});
        let child_ids = vec!["child-2".to_string(), "child-1".to_string()];
        let first =
            prepare_realtime_batch_in_state(&mut state, "s", "ctx", "tool", &child_ids, "digest")
                .unwrap();
        let repeated =
            prepare_realtime_batch_in_state(&mut state, "s", "ctx", "tool", &child_ids, "digest")
                .unwrap();

        assert_eq!(repeated, first);
        assert_eq!(
            state["s"]["realtime_export"]["batches"]
                .as_object()
                .unwrap()
                .len(),
            1
        );
        let changed_payload = prepare_realtime_batch_in_state(
            &mut state,
            "s",
            "ctx",
            "tool",
            &child_ids,
            "different",
        )
        .unwrap();
        assert_ne!(changed_payload.batch_id, first.batch_id);
        assert_eq!(
            state["s"]["realtime_export"]["batches"]
                .as_object()
                .unwrap()
                .len(),
            2
        );
    }

    #[test]
    fn realtime_batch_child_claims_and_post_compensation_are_atomic() {
        let mut state = json!({});
        let child_ids = vec!["child-1".to_string(), "child-2".to_string()];
        let batch =
            prepare_realtime_batch_in_state(&mut state, "s", "ctx", "tool", &child_ids, "digest")
                .unwrap();

        let claimed = claim_realtime_batch_child_in_state(
            &mut state,
            "s",
            &batch.batch_id,
            "child-1",
            "claim-a",
        )
        .unwrap();
        assert_eq!(claimed.claim_id.as_deref(), Some("claim-a"));
        assert_eq!(
            claim_realtime_batch_child_in_state(
                &mut state,
                "s",
                &batch.batch_id,
                "child-1",
                "claim-a",
            )
            .unwrap(),
            claimed
        );
        assert!(claim_realtime_batch_child_in_state(
            &mut state,
            "s",
            &batch.batch_id,
            "child-1",
            "claim-b",
        )
        .unwrap_err()
        .contains("already claimed"));
        assert_eq!(
            realtime_batch(&state, "s", &batch.batch_id)
                .unwrap()
                .children["child-1"]
                .claim_id
                .as_deref(),
            Some("claim-a"),
            "conflicting child claim must not replace the original claim"
        );

        let compensated = mark_realtime_batch_child_post_compensation_used_in_state(
            &mut state,
            "s",
            &batch.batch_id,
            "child-1",
        )
        .unwrap();
        assert!(compensated.post_compensation_used);
        assert!(
            mark_realtime_batch_child_post_compensation_used_in_state(
                &mut state,
                "s",
                &batch.batch_id,
                "child-1",
            )
            .unwrap()
            .post_compensation_used
        );
        assert_eq!(
            state["s"]["realtime_export"]["children"]["child-1"]["post_compensation_used"],
            true
        );
        assert_eq!(
            state["s"]["realtime_export"]["children"]["child-2"]["post_compensation_used"],
            false
        );
        assert!(
            !realtime_batch(&state, "s", &batch.batch_id)
                .unwrap()
                .children["child-2"]
                .post_compensation_used
        );
    }

    #[test]
    fn realtime_batch_status_transitions_are_ordered_and_idempotent() {
        let mut state = json!({});
        let child_ids = vec!["child-1".to_string()];
        let batch =
            prepare_realtime_batch_in_state(&mut state, "s", "ctx", "tool", &child_ids, "digest")
                .unwrap();

        assert!(succeed_realtime_batch_in_state(&mut state, "s", &batch.batch_id).is_err());
        assert_eq!(
            realtime_batch(&state, "s", &batch.batch_id).unwrap().status,
            RealtimeBatchStatus::Prepared
        );

        let published = publish_realtime_batch_in_state(&mut state, "s", &batch.batch_id).unwrap();
        assert_eq!(published.status, RealtimeBatchStatus::Published);
        assert_eq!(
            publish_realtime_batch_in_state(&mut state, "s", &batch.batch_id)
                .unwrap()
                .status,
            RealtimeBatchStatus::Published
        );

        let failed =
            fail_realtime_batch_in_state(&mut state, "s", &batch.batch_id, "http 503").unwrap();
        assert_eq!(failed.status, RealtimeBatchStatus::Failure);
        assert_eq!(failed.last_error.as_deref(), Some("http 503"));
        assert!(succeed_realtime_batch_in_state(&mut state, "s", &batch.batch_id).is_err());
        assert_eq!(
            realtime_batch(&state, "s", &batch.batch_id).unwrap().status,
            RealtimeBatchStatus::Failure
        );

        let success_batch = prepare_realtime_batch_in_state(
            &mut state,
            "s",
            "ctx",
            "generation",
            &child_ids,
            "digest-2",
        )
        .unwrap();
        publish_realtime_batch_in_state(&mut state, "s", &success_batch.batch_id).unwrap();
        assert_eq!(
            succeed_realtime_batch_in_state(&mut state, "s", &success_batch.batch_id)
                .unwrap()
                .status,
            RealtimeBatchStatus::Success
        );
        assert!(fail_realtime_batch_in_state(
            &mut state,
            "s",
            &success_batch.batch_id,
            "late failure",
        )
        .is_err());
    }

    fn delivery_plan_fixture() -> RealtimeDeliveryPlan {
        RealtimeDeliveryPlan {
            schema_version: REALTIME_DELIVERY_PLAN_SCHEMA_VERSION,
            plan_id: "plan-1".to_string(),
            context_id: "ctx".to_string(),
            trigger_id: "flush-1".to_string(),
            chunks: (0..2)
                .map(|cursor| RealtimeDeliveryChunkPlanEntry {
                    cursor,
                    batch_id: format!("batch-{cursor}"),
                    payload_digest: format!("digest-{cursor}"),
                    body_len: 512,
                    child_ids: vec![format!("generation:g-{cursor}")],
                    span_ids: vec![format!("{cursor:016x}")],
                    digests: vec![format!("child-digest-{cursor}")],
                    body: None,
                    status: RealtimeDeliveryChunkStatus::Queued,
                    last_error: None,
                })
                .collect(),
            oversized_child_ids: vec![],
            next_cursor: 0,
            complete: false,
            terminal_reason: None,
        }
    }

    #[test]
    fn delivery_plan_persists_complete_suffix_and_advances_cursor_monotonically() {
        let mut state = json!({});
        let plan = delivery_plan_fixture();
        assert_eq!(
            prepare_realtime_delivery_plan_in_state(&mut state, "s", &plan).unwrap(),
            plan
        );
        begin_realtime_delivery_plan_chunks_in_state(
            &mut state,
            "s",
            "plan-1",
            &[0],
            RetryPolicy::AtLeastOnce,
        )
        .unwrap();
        let failed = finish_realtime_delivery_plan_chunk_in_state(
            &mut state,
            "s",
            "plan-1",
            0,
            RetryPolicy::AtLeastOnce,
            false,
            false,
            Some("timeout"),
        )
        .unwrap();
        assert_eq!(failed.next_cursor, 0);
        assert_eq!(
            failed.chunks[0].status,
            RealtimeDeliveryChunkStatus::FailedRetryable
        );
        assert_eq!(
            failed.chunks[1].status,
            RealtimeDeliveryChunkStatus::Queued,
            "the complete safe suffix remains durably queued"
        );

        begin_realtime_delivery_plan_chunks_in_state(
            &mut state,
            "s",
            "plan-1",
            &[0],
            RetryPolicy::AtLeastOnce,
        )
        .unwrap();
        let first = finish_realtime_delivery_plan_chunk_in_state(
            &mut state,
            "s",
            "plan-1",
            0,
            RetryPolicy::AtLeastOnce,
            true,
            false,
            None,
        )
        .unwrap();
        assert_eq!(first.next_cursor, 1);
        let repeated = prepare_realtime_delivery_plan_in_state(&mut state, "s", &plan).unwrap();
        assert_eq!(repeated.next_cursor, 1);
        assert_eq!(
            repeated.chunks[0].status,
            RealtimeDeliveryChunkStatus::Accounted
        );
    }

    #[test]
    fn interrupted_at_most_once_chunk_becomes_terminal_without_repost() {
        let mut state = json!({});
        let plan = delivery_plan_fixture();
        prepare_realtime_delivery_plan_in_state(&mut state, "s", &plan).unwrap();
        begin_realtime_delivery_plan_chunks_in_state(
            &mut state,
            "s",
            "plan-1",
            &[0],
            RetryPolicy::AtMostOnce,
        )
        .unwrap();

        let settled =
            settle_interrupted_policy_terminal_plan_in_state(&mut state, "s", "plan-1").unwrap();

        assert_eq!(settled.next_cursor, 1);
        assert!(!settled.complete);
        assert_eq!(
            settled.chunks[0].status,
            RealtimeDeliveryChunkStatus::PolicyTerminal
        );
        assert_eq!(
            settled.chunks[1].status,
            RealtimeDeliveryChunkStatus::Queued
        );
    }

    #[test]
    fn delivery_plan_records_oversized_children_as_terminal_reason() {
        let mut state = json!({});
        let mut plan = delivery_plan_fixture();
        plan.chunks.clear();
        plan.oversized_child_ids = vec!["generation:huge".to_string()];
        let prepared = prepare_realtime_delivery_plan_in_state(&mut state, "s", &plan).unwrap();
        assert!(prepared.complete);
        assert_eq!(
            prepared.terminal_reason.as_deref(),
            Some("realtime_child_oversize")
        );
    }

    #[test]
    fn delivery_plan_keeps_the_fifth_chunk_queued_for_the_next_claim() {
        let mut state = json!({});
        let mut plan = delivery_plan_fixture();
        plan.chunks = (0..5)
            .map(|cursor| RealtimeDeliveryChunkPlanEntry {
                cursor,
                batch_id: format!("batch-{cursor}"),
                payload_digest: format!("digest-{cursor}"),
                body_len: 512,
                child_ids: vec![format!("generation:g-{cursor}")],
                span_ids: vec![format!("{cursor:016x}")],
                digests: vec![format!("child-digest-{cursor}")],
                body: None,
                status: RealtimeDeliveryChunkStatus::Queued,
                last_error: None,
            })
            .collect();
        prepare_realtime_delivery_plan_in_state(&mut state, "s", &plan).unwrap();

        for cursor in 0..4 {
            begin_realtime_delivery_plan_chunks_in_state(
                &mut state,
                "s",
                "plan-1",
                &[cursor],
                RetryPolicy::AtLeastOnce,
            )
            .unwrap();
            finish_realtime_delivery_plan_chunk_in_state(
                &mut state,
                "s",
                "plan-1",
                cursor,
                RetryPolicy::AtLeastOnce,
                true,
                false,
                None,
            )
            .unwrap();
        }

        let queued_suffix = realtime_delivery_plan(&state, "s", "plan-1").unwrap();
        assert_eq!(queued_suffix.next_cursor, 4);
        assert!(!queued_suffix.complete);
        assert_eq!(
            queued_suffix.chunks[4].status,
            RealtimeDeliveryChunkStatus::Queued
        );

        begin_realtime_delivery_plan_chunks_in_state(
            &mut state,
            "s",
            "plan-1",
            &[4],
            RetryPolicy::AtLeastOnce,
        )
        .unwrap();
        let complete = finish_realtime_delivery_plan_chunk_in_state(
            &mut state,
            "s",
            "plan-1",
            4,
            RetryPolicy::AtLeastOnce,
            true,
            false,
            None,
        )
        .unwrap();
        assert_eq!(complete.next_cursor, 5);
        assert!(complete.complete);
    }

    #[test]
    fn realtime_batch_allows_exactly_one_later_post_compensation() {
        let mut state = json!({});
        let context = claim_tool_in_state(
            &mut state,
            "s",
            "tool-1",
            "now",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        let children = vec!["generation:g-1".to_string(), "tool:tool-1".to_string()];
        let batch = prepare_realtime_batch_in_state(
            &mut state,
            "s",
            &context.context_id,
            "completed",
            &children,
            "digest",
        )
        .unwrap();
        register_realtime_job_in_state(
            &mut state,
            "s",
            &context.context_id,
            "batch",
            &batch.batch_id,
            "digest",
        )
        .unwrap();
        publish_realtime_batch_in_state(&mut state, "s", &batch.batch_id).unwrap();
        begin_realtime_delivery_in_state(
            &mut state,
            "s",
            &batch.batch_id,
            "digest",
            RetryPolicy::AtLeastOnce,
            "claim-1",
            100,
        )
        .unwrap();
        fail_realtime_delivery_in_state(
            &mut state,
            "s",
            &batch.batch_id,
            "digest",
            RetryPolicy::AtLeastOnce,
            "http 500",
            false,
            false,
        )
        .unwrap();
        fail_realtime_batch_in_state(&mut state, "s", &batch.batch_id, "http 500").unwrap();

        let compensation = prepare_realtime_batch_compensation_in_state(
            &mut state,
            "s",
            &batch.batch_id,
            "compensation-1",
            "digest",
            &children,
            "digest",
        )
        .unwrap();
        assert_eq!(compensation.status, RealtimeBatchStatus::Prepared);
        assert_eq!(compensation.post_compensation_round, 1);
        assert!(compensation
            .children
            .values()
            .all(|child| child.post_compensation_used));
        assert_eq!(
            compensation.compensates_batch_id.as_deref(),
            Some(batch.batch_id.as_str())
        );
        let original = realtime_batch(&state, "s", &batch.batch_id).unwrap();
        assert_eq!(original.status, RealtimeBatchStatus::Failure);
        assert_eq!(original.post_compensation_round, 1);
        assert_eq!(
            original.compensated_by_batch_id.as_deref(),
            Some("compensation-1")
        );
        assert!(original
            .children
            .values()
            .all(|child| child.post_compensation_used));

        publish_realtime_batch_in_state(&mut state, "s", "compensation-1").unwrap();
        begin_realtime_delivery_in_state(
            &mut state,
            "s",
            "compensation-1",
            "digest",
            RetryPolicy::AtLeastOnce,
            "claim-2",
            200,
        )
        .unwrap();
        fail_realtime_delivery_in_state(
            &mut state,
            "s",
            "compensation-1",
            "digest",
            RetryPolicy::AtLeastOnce,
            "http 500 again",
            false,
            false,
        )
        .unwrap();
        fail_realtime_batch_in_state(&mut state, "s", "compensation-1", "http 500 again").unwrap();
        assert!(prepare_realtime_batch_compensation_in_state(
            &mut state,
            "s",
            &batch.batch_id,
            "compensation-2",
            "digest",
            &children,
            "digest",
        )
        .is_err());
        assert_eq!(
            registered_realtime_job(&state, "s", "compensation-1")
                .unwrap()
                .status,
            RealtimeRegisteredJobStatus::Failed
        );
    }

    #[test]
    fn registered_jobs_are_idempotent_digest_bound_and_monotonic() {
        let mut state = json!({});
        let first =
            register_realtime_job_in_state(&mut state, "s", "ctx", "tool", "job-1", "digest-1")
                .unwrap();
        let repeated =
            register_realtime_job_in_state(&mut state, "s", "ctx", "tool", "job-1", "digest-1")
                .unwrap();
        let second = register_realtime_job_in_state(
            &mut state,
            "s",
            "ctx",
            "generation",
            "job-2",
            "digest-2",
        )
        .unwrap();

        assert_eq!(first, 1);
        assert_eq!(repeated, first);
        assert_eq!(second, 2);
        assert!(registered_realtime_job_matches(
            &state, "s", "job-1", "digest-1"
        ));
        assert!(!registered_realtime_job_matches(
            &state, "s", "job-1", "wrong"
        ));
        assert!(register_realtime_job_in_state(
            &mut state,
            "s",
            "ctx",
            "tool",
            "job-1",
            "different"
        )
        .is_err());
    }

    #[test]
    fn finalizer_barrier_uses_snapshot_high_water_only() {
        let mut state = json!({});
        register_realtime_job_in_state(&mut state, "s", "ctx", "tool", "job-1", "digest-1")
            .unwrap();
        let high_water = record_finalizer_high_water_in_state(&mut state, "s");
        register_realtime_job_in_state(&mut state, "s", "next", "tool", "job-2", "digest-2")
            .unwrap();

        assert_eq!(high_water, 1);
        assert!(!finalizer_barrier_satisfied(&state, "s", high_water));
        begin_realtime_delivery_in_state(
            &mut state,
            "s",
            "job-1",
            "digest-1",
            RetryPolicy::AtLeastOnce,
            "claim-1",
            100,
        )
        .unwrap();
        complete_realtime_delivery_in_state(&mut state, "s", "job-1", "digest-1").unwrap();
        assert!(finalizer_barrier_satisfied(&state, "s", high_water));
        assert!(!finalizer_barrier_satisfied(&state, "s", 2));
    }

    #[test]
    fn retry_policy_controls_durable_checkpoint_order() {
        let mut state = json!({});
        register_realtime_job_in_state(&mut state, "s", "ctx", "tool", "amo", "digest-1").unwrap();
        register_realtime_job_in_state(&mut state, "s", "ctx", "tool", "alo", "digest-2").unwrap();

        begin_realtime_delivery_in_state(
            &mut state,
            "s",
            "amo",
            "digest-1",
            RetryPolicy::AtMostOnce,
            "claim-1",
            100,
        )
        .unwrap();
        begin_realtime_delivery_in_state(
            &mut state,
            "s",
            "alo",
            "digest-2",
            RetryPolicy::AtLeastOnce,
            "claim-2",
            100,
        )
        .unwrap();

        assert_eq!(
            registered_realtime_job(&state, "s", "amo").unwrap().status,
            RealtimeRegisteredJobStatus::PolicyTerminal
        );
        assert!(begin_realtime_delivery_in_state(
            &mut state,
            "s",
            "amo",
            "digest-1",
            RetryPolicy::AtMostOnce,
            "claim-race",
            200,
        )
        .is_err());
        assert_eq!(
            registered_realtime_job(&state, "s", "alo").unwrap().status,
            RealtimeRegisteredJobStatus::Inflight
        );

        fail_realtime_delivery_in_state(
            &mut state,
            "s",
            "alo",
            "digest-2",
            RetryPolicy::AtLeastOnce,
            "network",
            true,
            false,
        )
        .unwrap();
        assert_eq!(
            registered_realtime_job(&state, "s", "alo").unwrap().status,
            RealtimeRegisteredJobStatus::Queued
        );
        begin_realtime_delivery_in_state(
            &mut state,
            "s",
            "alo",
            "digest-2",
            RetryPolicy::AtLeastOnce,
            "claim-3",
            200,
        )
        .unwrap();
        complete_realtime_delivery_in_state(&mut state, "s", "alo", "digest-2").unwrap();
        assert_eq!(
            registered_realtime_job(&state, "s", "alo").unwrap().status,
            RealtimeRegisteredJobStatus::Accounted
        );

        register_realtime_job_in_state(&mut state, "s", "ctx", "tool", "alo-failed", "digest-3")
            .unwrap();
        begin_realtime_delivery_in_state(
            &mut state,
            "s",
            "alo-failed",
            "digest-3",
            RetryPolicy::AtLeastOnce,
            "claim-4",
            300,
        )
        .unwrap();
        fail_realtime_delivery_in_state(
            &mut state,
            "s",
            "alo-failed",
            "digest-3",
            RetryPolicy::AtLeastOnce,
            "timeout",
            false,
            false,
        )
        .unwrap();
        assert_eq!(
            registered_realtime_job(&state, "s", "alo-failed")
                .unwrap()
                .status,
            RealtimeRegisteredJobStatus::Failed
        );
        assert!(failed_realtime_tool_job_ids(&state, "s").contains("alo-failed"));
        assert!(finalizer_barrier_satisfied(&state, "s", 3));
        requeue_failed_realtime_job_in_state(&mut state, "s", "alo-failed", "digest-3").unwrap();
        assert_eq!(
            registered_realtime_job(&state, "s", "alo-failed")
                .unwrap()
                .status,
            RealtimeRegisteredJobStatus::Queued
        );

        register_realtime_job_in_state(&mut state, "s", "ctx", "tool", "alo-permanent", "digest-4")
            .unwrap();
        begin_realtime_delivery_in_state(
            &mut state,
            "s",
            "alo-permanent",
            "digest-4",
            RetryPolicy::AtLeastOnce,
            "claim-5",
            400,
        )
        .unwrap();
        fail_realtime_delivery_in_state(
            &mut state,
            "s",
            "alo-permanent",
            "digest-4",
            RetryPolicy::AtLeastOnce,
            "http 400",
            false,
            true,
        )
        .unwrap();
        assert_eq!(
            registered_realtime_job(&state, "s", "alo-permanent")
                .unwrap()
                .status,
            RealtimeRegisteredJobStatus::PermanentFailed
        );
        assert!(!failed_realtime_tool_job_ids(&state, "s").contains("alo-permanent"));
        requeue_failed_realtime_job_in_state(&mut state, "s", "alo-permanent", "digest-4").unwrap();
        assert_eq!(
            registered_realtime_job(&state, "s", "alo-permanent")
                .unwrap()
                .status,
            RealtimeRegisteredJobStatus::PermanentFailed
        );

        register_realtime_job_in_state(
            &mut state,
            "s",
            "ctx",
            "finalizer",
            "interrupted-finalizer",
            "digest-5",
        )
        .unwrap();
        begin_realtime_delivery_in_state(
            &mut state,
            "s",
            "interrupted-finalizer",
            "digest-5",
            RetryPolicy::AtLeastOnce,
            "claim-6",
            500,
        )
        .unwrap();
        requeue_interrupted_realtime_job_in_state(
            &mut state,
            "s",
            "interrupted-finalizer",
            "digest-5",
        )
        .unwrap();
        let recovered = registered_realtime_job(&state, "s", "interrupted-finalizer").unwrap();
        assert_eq!(recovered.status, RealtimeRegisteredJobStatus::Queued);
        assert_eq!(
            recovered.last_error.as_deref(),
            Some("recovered interrupted delivery")
        );
    }
}
