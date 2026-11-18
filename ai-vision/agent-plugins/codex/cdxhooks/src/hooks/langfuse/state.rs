use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::HashSet;
use std::path::Path;

pub const POSTED_TURN_ID_RETENTION: usize = 200;
pub const PARTIAL_EXPORT_REASON: &str = "partial_export_in_progress";
pub const REALTIME_TOPOLOGY_AMBIGUOUS_REASON: &str = "realtime_topology_ambiguous";
pub const FINALIZER_EVIDENCE_DEFERRED_REASON: &str = "finalizer_evidence_deferred";
pub const DEFERRED_EVIDENCE_EXPIRED_REASON: &str = "deferred_evidence_expired";
pub const HOOK_BUDGET_EXHAUSTED_REASON: &str = "hook_budget_exhausted";
pub const MULTIPLE_PUBLISHED_ROOTS_QUARANTINE_REASON: &str = "multiple_published_realtime_roots";
pub const FACT_CONFLICT_QUARANTINE_REASON: &str = "realtime_context_fact_conflict";
pub const ALIAS_CYCLE_QUARANTINE_REASON: &str = "realtime_context_alias_cycle";
pub const MISSING_ALIAS_TARGET_QUARANTINE_REASON: &str = "realtime_context_alias_missing_target";
pub const QUARANTINE_STATE_VERSION: u64 = 1;
pub const EXPORT_ACCOUNTING_STATE_VERSION: u64 = 1;
pub const LEGACY_FINALIZER_RECOVERY_STATE_VERSION: u64 = 1;
pub const STOP_RECOVERY_STATE_VERSION: u64 = 2;
pub const STOP_RECOVERY_MAX_NO_PROGRESS_ATTEMPTS: u32 = 3;
pub const STATE_LOCK_TIMEOUT_MS: u64 = 2_000;
/// Maximum time a synchronous foreground hook may wait for shared state.
/// Foreground callers must fail open after this budget so Stop workers cannot
/// delay later lifecycle or tool hooks.
pub const FOREGROUND_STATE_LOCK_TIMEOUT_MS: u64 = 25;

pub fn structural_topology_quarantine(
    error: &super::realtime_state::RealtimeClaimError,
    state: &Value,
    session_id: &str,
    turn_id: &str,
) -> (&'static str, Vec<String>) {
    match error {
        super::realtime_state::RealtimeClaimError::MultiplePublishedRoots { .. } => (
            MULTIPLE_PUBLISHED_ROOTS_QUARANTINE_REASON,
            super::realtime_state::published_root_identities_for_turn(state, session_id, turn_id),
        ),
        super::realtime_state::RealtimeClaimError::FactConflict { context_ids, .. } => {
            (FACT_CONFLICT_QUARANTINE_REASON, context_ids.clone())
        }
        super::realtime_state::RealtimeClaimError::AliasCycle { context_id } => {
            (ALIAS_CYCLE_QUARANTINE_REASON, vec![context_id.clone()])
        }
        super::realtime_state::RealtimeClaimError::MissingAliasTarget {
            context_id,
            target_id,
        } => (
            MISSING_ALIAS_TARGET_QUARANTINE_REASON,
            vec![context_id.clone(), target_id.clone()],
        ),
    }
}

// Re-export shared state utilities from agentic-otel-common
pub use agentic_otel_common::state::{
    get_array_field, get_int_field, get_session_state, get_str_field, get_string_array_field,
    StateLock,
};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TerminalQuarantineEntry {
    pub turn_id: String,
    pub reason: String,
    pub original_root_identities: Vec<String>,
    pub timestamp: String,
    pub attempt: u64,
    pub state_version: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recovery_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub terminal_high_water_offset: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TerminalQuarantineTransition {
    pub appended: bool,
    pub terminal_high_water_offset: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LegacyFinalizerRecoveryTransition {
    pub recovery_key: String,
    pub attempt: u64,
    pub status: String,
    pub armed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FiniteFinalizerDeferredTransition {
    pub attempt: u64,
    pub repeated_boundary: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StopRecoveryPhase {
    WaitingTerminal,
    BuildPending,
    DeliveryPending,
    Cooldown,
    TerminalQuarantined,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StopRecoveryTopologyKind {
    Exact,
    LegacyFallback,
    TerminalQuarantined,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StopRecoveryTopologyCheckpoint {
    pub turn_id: String,
    pub kind: StopRecoveryTopologyKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trace_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub root_span_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StopRecoveryDeliveryCheckpoint {
    pub plan_id: String,
    pub chunk_cursor: u64,
    pub payload_digest: String,
    pub turn_id: String,
    pub root_span_id: String,
    pub window_end_offset: i64,
    pub finalizer_high_water: u64,
    pub generation_count: u64,
    pub merged_sync_ids: Vec<String>,
    pub merged_subagent_invocation_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StopRecoveryRecord {
    pub schema_version: u64,
    pub intent_id: String,
    pub phase: StopRecoveryPhase,
    pub attempt: u32,
    pub no_progress_attempts: u32,
    pub observed_file_len: u64,
    #[serde(default)]
    /// Count of whole turns durably exported by this intent. This is a
    /// completed-turn progress marker, not an intra-turn builder cursor.
    pub build_turn_cursor: u64,
    #[serde(default)]
    pub topology_cursor: u64,
    #[serde(default)]
    pub topology_checkpoints: Vec<StopRecoveryTopologyCheckpoint>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delivery_plan_id: Option<String>,
    #[serde(default)]
    pub delivery_chunk_cursor: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delivery_payload_digest: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delivery_turn_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delivery_root_span_id: Option<String>,
    #[serde(default)]
    pub delivery_window_end_offset: i64,
    #[serde(default)]
    pub delivery_finalizer_high_water: u64,
    #[serde(default)]
    pub delivery_generation_count: u64,
    #[serde(default)]
    pub delivery_merged_sync_ids: Vec<String>,
    #[serde(default)]
    pub delivery_merged_subagent_invocation_ids: Vec<String>,
    pub progress_fingerprint: String,
    pub next_eligible_at_ms: u64,
    pub last_reason: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StopRecoveryEligibility {
    Idle,
    Waiting(std::time::Duration),
    Eligible,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StopRecoveryDeliveryAdoption {
    Adopted,
    AlreadyOwned,
    Cleared,
    Changed,
}

pub fn stop_recovery_intent_id(payload: &Value) -> String {
    payload
        .get("intent_id")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| {
            format!(
                "legacy:{}:{}",
                payload
                    .get("session_id")
                    .and_then(Value::as_str)
                    .unwrap_or_default(),
                payload
                    .get("created_at")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
            )
        })
}

fn stop_recovery_progress_fingerprint(session: &Value) -> String {
    let recovery = session
        .get("stop_recovery")
        .and_then(|record| serde_json::from_value::<StopRecoveryRecord>(record.clone()).ok());
    let persisted_delivery_cursor = recovery
        .as_ref()
        .and_then(|record| record.delivery_plan_id.as_deref())
        .and_then(|plan_id| {
            session
                .pointer("/realtime_export/delivery_plans")
                .and_then(Value::as_object)
                .and_then(|plans| plans.get(plan_id))
                .and_then(|plan| plan.get("next_cursor"))
                .and_then(Value::as_u64)
        })
        .unwrap_or_else(|| {
            recovery
                .as_ref()
                .map(|record| record.delivery_chunk_cursor)
                .unwrap_or(0)
        });
    let fingerprint = json!({
        "offset": get_int_field(session, "offset"),
        "turn_count": get_int_field(session, "turn_count"),
        "terminal_high_water": session
            .pointer("/export_accounting/terminal_high_water_offset")
            .and_then(Value::as_i64)
            .unwrap_or(0),
        "generation_committed_offset": session
            .pointer("/realtime_export/generation/committed_offset")
            .and_then(Value::as_u64)
            .unwrap_or(0),
        "build_turn_cursor": recovery
            .as_ref()
            .map(|record| record.build_turn_cursor)
            .unwrap_or(0),
        "topology_cursor": recovery
            .as_ref()
            .map(|record| record.topology_cursor)
            .unwrap_or(0),
        "delivery_plan_id": recovery
            .as_ref()
            .and_then(|record| record.delivery_plan_id.as_deref())
            .unwrap_or_default(),
        "delivery_chunk_cursor": persisted_delivery_cursor,
    });
    serde_json::to_string(&fingerprint).expect("Stop recovery fingerprint must serialize")
}

fn stop_recovery_now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(u64::MAX as u128) as u64
}

pub fn stop_recovery_record(state: &Value, session_id: &str) -> Option<StopRecoveryRecord> {
    get_session_state(state, session_id)
        .and_then(|session| session.get("stop_recovery"))
        .and_then(|record| serde_json::from_value(record.clone()).ok())
}

pub fn stop_recovery_eligibility(state: &Value, session_id: &str) -> StopRecoveryEligibility {
    let Some(record) = stop_recovery_record(state, session_id) else {
        return StopRecoveryEligibility::Idle;
    };
    if matches!(
        record.phase,
        StopRecoveryPhase::WaitingTerminal | StopRecoveryPhase::TerminalQuarantined
    ) {
        return StopRecoveryEligibility::Idle;
    }
    let now = stop_recovery_now_ms();
    if now >= record.next_eligible_at_ms {
        StopRecoveryEligibility::Eligible
    } else {
        StopRecoveryEligibility::Waiting(std::time::Duration::from_millis(
            record.next_eligible_at_ms.saturating_sub(now).min(500),
        ))
    }
}

pub fn record_stop_recovery_deferred(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    payload: &Value,
    reason: &str,
    updated_at: &str,
) -> Result<StopRecoveryRecord, String> {
    let intent_id = stop_recovery_intent_id(payload);
    let observed_file_len = payload
        .get("transcript_path")
        .and_then(Value::as_str)
        .and_then(|path| std::fs::metadata(path).ok())
        .map(|metadata| metadata.len())
        .or_else(|| payload.get("observed_file_len").and_then(Value::as_u64))
        .unwrap_or(0);
    let now_ms = stop_recovery_now_ms();
    let waiting_terminal = reason.contains("awaiting terminal")
        || reason.contains("pending messages")
        || reason.contains("terminal marker");
    let mut transition = None;
    update_state_transaction(state_file, state, |candidate| {
        let session = candidate
            .as_object_mut()
            .expect("state root must be an object")
            .entry(session_id.to_string())
            .or_insert_with(|| json!({}));
        // Transcript growth is new input, not proof that this recovery intent
        // advanced. Only durable accounting/build/topology/delivery state may
        // reset the no-progress fuse; otherwise an active session can keep a
        // failed old Stop alive forever merely by appending new records.
        let fingerprint = stop_recovery_progress_fingerprint(session);
        let current = session
            .get("stop_recovery")
            .and_then(|record| serde_json::from_value::<StopRecoveryRecord>(record.clone()).ok());
        // A durable delivery plan is the authoritative replay identity after
        // POST begins. A newer Stop may report Deferred while trying to adopt
        // it, but must never replace the only record that still references the
        // prepared body. Leave ownership unchanged so a later Stop can retry
        // the atomic adoption instead of rebuilding and re-POSTing the turn.
        if let Some(active_delivery) = current
            .as_ref()
            .filter(|record| record.intent_id != intent_id && record.delivery_plan_id.is_some())
        {
            transition = Some(active_delivery.clone());
            return;
        }
        let existing = current.filter(|record| record.intent_id == intent_id);
        let no_progress_attempts = if waiting_terminal {
            0
        } else if existing
            .as_ref()
            .is_some_and(|record| record.progress_fingerprint == fingerprint)
        {
            existing
                .as_ref()
                .map(|record| record.no_progress_attempts.saturating_add(1))
                .unwrap_or(0)
        } else {
            0
        };
        let phase = if waiting_terminal {
            StopRecoveryPhase::WaitingTerminal
        } else if no_progress_attempts >= STOP_RECOVERY_MAX_NO_PROGRESS_ATTEMPTS {
            StopRecoveryPhase::TerminalQuarantined
        } else {
            StopRecoveryPhase::Cooldown
        };
        let backoff_ms = 500u64
            .saturating_mul(1u64 << no_progress_attempts.min(4))
            .min(8_000);
        let record = StopRecoveryRecord {
            schema_version: STOP_RECOVERY_STATE_VERSION,
            intent_id: intent_id.clone(),
            phase,
            attempt: existing
                .as_ref()
                .map(|record| record.attempt.saturating_add(1))
                .unwrap_or(1),
            no_progress_attempts,
            observed_file_len,
            build_turn_cursor: existing
                .as_ref()
                .map(|record| record.build_turn_cursor)
                .unwrap_or(0),
            topology_cursor: existing
                .as_ref()
                .map(|record| record.topology_cursor)
                .unwrap_or(0),
            topology_checkpoints: existing
                .as_ref()
                .map(|record| record.topology_checkpoints.clone())
                .unwrap_or_default(),
            delivery_plan_id: existing
                .as_ref()
                .and_then(|record| record.delivery_plan_id.clone()),
            delivery_chunk_cursor: existing
                .as_ref()
                .map(|record| record.delivery_chunk_cursor)
                .unwrap_or(0),
            delivery_payload_digest: existing
                .as_ref()
                .and_then(|record| record.delivery_payload_digest.clone()),
            delivery_turn_id: existing
                .as_ref()
                .and_then(|record| record.delivery_turn_id.clone()),
            delivery_root_span_id: existing
                .as_ref()
                .and_then(|record| record.delivery_root_span_id.clone()),
            delivery_window_end_offset: existing
                .as_ref()
                .map(|record| record.delivery_window_end_offset)
                .unwrap_or_default(),
            delivery_finalizer_high_water: existing
                .as_ref()
                .map(|record| record.delivery_finalizer_high_water)
                .unwrap_or_default(),
            delivery_generation_count: existing
                .as_ref()
                .map(|record| record.delivery_generation_count)
                .unwrap_or_default(),
            delivery_merged_sync_ids: existing
                .as_ref()
                .map(|record| record.delivery_merged_sync_ids.clone())
                .unwrap_or_default(),
            delivery_merged_subagent_invocation_ids: existing
                .as_ref()
                .map(|record| record.delivery_merged_subagent_invocation_ids.clone())
                .unwrap_or_default(),
            progress_fingerprint: fingerprint,
            next_eligible_at_ms: if waiting_terminal {
                u64::MAX
            } else {
                now_ms.saturating_add(backoff_ms)
            },
            last_reason: reason.chars().take(2048).collect(),
            updated_at: updated_at.to_string(),
        };
        session["stop_recovery"] =
            serde_json::to_value(&record).expect("Stop recovery record must serialize");
        transition = Some(record);
    })?;
    transition.ok_or_else(|| "Stop recovery transition was not produced".to_string())
}

fn new_stop_recovery_record(
    intent_id: &str,
    observed_file_len: u64,
    updated_at: &str,
) -> StopRecoveryRecord {
    StopRecoveryRecord {
        schema_version: STOP_RECOVERY_STATE_VERSION,
        intent_id: intent_id.to_string(),
        phase: StopRecoveryPhase::BuildPending,
        attempt: 0,
        no_progress_attempts: 0,
        observed_file_len,
        build_turn_cursor: 0,
        topology_cursor: 0,
        topology_checkpoints: Vec::new(),
        delivery_plan_id: None,
        delivery_chunk_cursor: 0,
        delivery_payload_digest: None,
        delivery_turn_id: None,
        delivery_root_span_id: None,
        delivery_window_end_offset: 0,
        delivery_finalizer_high_water: 0,
        delivery_generation_count: 0,
        delivery_merged_sync_ids: Vec::new(),
        delivery_merged_subagent_invocation_ids: Vec::new(),
        progress_fingerprint: String::new(),
        next_eligible_at_ms: 0,
        last_reason: "build_pending".to_string(),
        updated_at: updated_at.to_string(),
    }
}

pub fn checkpoint_stop_recovery_topology(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    intent_id: &str,
    observed_file_len: u64,
    cursor: u64,
    checkpoint: StopRecoveryTopologyCheckpoint,
    updated_at: &str,
) -> Result<StopRecoveryRecord, String> {
    let mut transition = None;
    update_state_transaction(state_file, state, |candidate| {
        let session = candidate
            .as_object_mut()
            .expect("state root must be an object")
            .entry(session_id.to_string())
            .or_insert_with(|| json!({}));
        let mut record = session
            .get("stop_recovery")
            .and_then(|value| serde_json::from_value::<StopRecoveryRecord>(value.clone()).ok())
            .filter(|record| record.intent_id == intent_id)
            .unwrap_or_else(|| new_stop_recovery_record(intent_id, observed_file_len, updated_at));
        if let Some(existing) = record
            .topology_checkpoints
            .iter_mut()
            .find(|existing| existing.turn_id == checkpoint.turn_id)
        {
            *existing = checkpoint;
        } else {
            record.topology_checkpoints.push(checkpoint);
        }
        record.phase = StopRecoveryPhase::BuildPending;
        record.observed_file_len = observed_file_len;
        record.topology_cursor = record.topology_cursor.max(cursor);
        record.next_eligible_at_ms = 0;
        record.updated_at = updated_at.to_string();
        session["stop_recovery"] =
            serde_json::to_value(&record).expect("Stop recovery record must serialize");
        transition = Some(record);
    })?;
    transition.ok_or_else(|| "Stop recovery topology checkpoint was not produced".to_string())
}

pub fn checkpoint_stop_recovery_build_cursor(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    intent_id: &str,
    observed_file_len: u64,
    cursor: u64,
    updated_at: &str,
) -> Result<StopRecoveryRecord, String> {
    let mut transition = None;
    update_state_transaction(state_file, state, |candidate| {
        let session = candidate
            .as_object_mut()
            .expect("state root must be an object")
            .entry(session_id.to_string())
            .or_insert_with(|| json!({}));
        let mut record = session
            .get("stop_recovery")
            .and_then(|value| serde_json::from_value::<StopRecoveryRecord>(value.clone()).ok())
            .filter(|record| record.intent_id == intent_id)
            .unwrap_or_else(|| new_stop_recovery_record(intent_id, observed_file_len, updated_at));
        record.phase = StopRecoveryPhase::BuildPending;
        record.observed_file_len = observed_file_len;
        record.build_turn_cursor = record.build_turn_cursor.max(cursor);
        record.next_eligible_at_ms = 0;
        record.updated_at = updated_at.to_string();
        session["stop_recovery"] =
            serde_json::to_value(&record).expect("Stop recovery record must serialize");
        transition = Some(record);
    })?;
    transition.ok_or_else(|| "Stop recovery build checkpoint was not produced".to_string())
}

pub fn checkpoint_stop_recovery_delivery(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    intent_id: &str,
    observed_file_len: u64,
    checkpoint: &StopRecoveryDeliveryCheckpoint,
    updated_at: &str,
) -> Result<StopRecoveryRecord, String> {
    let mut transition = None;
    update_state_transaction(state_file, state, |candidate| {
        let session = candidate
            .as_object_mut()
            .expect("state root must be an object")
            .entry(session_id.to_string())
            .or_insert_with(|| json!({}));
        let mut record = session
            .get("stop_recovery")
            .and_then(|value| serde_json::from_value::<StopRecoveryRecord>(value.clone()).ok())
            .filter(|record| record.intent_id == intent_id)
            .unwrap_or_else(|| new_stop_recovery_record(intent_id, observed_file_len, updated_at));
        record.phase = StopRecoveryPhase::DeliveryPending;
        record.observed_file_len = observed_file_len;
        record.delivery_plan_id = Some(checkpoint.plan_id.clone());
        record.delivery_chunk_cursor = checkpoint.chunk_cursor;
        record.delivery_payload_digest = Some(checkpoint.payload_digest.clone());
        record.delivery_turn_id = Some(checkpoint.turn_id.clone());
        record.delivery_root_span_id = Some(checkpoint.root_span_id.clone());
        record.delivery_window_end_offset = checkpoint.window_end_offset;
        record.delivery_finalizer_high_water = checkpoint.finalizer_high_water;
        record.delivery_generation_count = checkpoint.generation_count;
        record.delivery_merged_sync_ids = checkpoint.merged_sync_ids.clone();
        record.delivery_merged_subagent_invocation_ids =
            checkpoint.merged_subagent_invocation_ids.clone();
        record.next_eligible_at_ms = 0;
        record.updated_at = updated_at.to_string();
        session["stop_recovery"] =
            serde_json::to_value(&record).expect("Stop recovery record must serialize");
        transition = Some(record);
    })?;
    transition.ok_or_else(|| "Stop recovery delivery checkpoint was not produced".to_string())
}

pub fn adopt_stop_recovery_delivery_intent(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    expected_intent_id: &str,
    expected_plan_id: &str,
    next_intent_id: &str,
    updated_at: &str,
) -> Result<StopRecoveryDeliveryAdoption, String> {
    let mut outcome = StopRecoveryDeliveryAdoption::Cleared;
    update_state_transaction(state_file, state, |candidate| {
        let Some(session) = candidate.get_mut(session_id) else {
            return;
        };
        let Some(mut record) = session
            .get("stop_recovery")
            .and_then(|value| serde_json::from_value::<StopRecoveryRecord>(value.clone()).ok())
        else {
            return;
        };
        let Some(plan_id) = record.delivery_plan_id.as_deref() else {
            return;
        };
        if record.intent_id == next_intent_id && plan_id == expected_plan_id {
            outcome = StopRecoveryDeliveryAdoption::AlreadyOwned;
            return;
        }
        if record.intent_id != expected_intent_id || plan_id != expected_plan_id {
            outcome = StopRecoveryDeliveryAdoption::Changed;
            return;
        }
        record.intent_id = next_intent_id.to_string();
        record.phase = StopRecoveryPhase::DeliveryPending;
        record.next_eligible_at_ms = 0;
        record.updated_at = updated_at.to_string();
        session["stop_recovery"] =
            serde_json::to_value(&record).expect("Stop recovery record must serialize");
        outcome = StopRecoveryDeliveryAdoption::Adopted;
    })?;
    Ok(outcome)
}

pub fn clear_stop_recovery_delivery(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    intent_id: &str,
    updated_at: &str,
) -> Result<bool, String> {
    let mut cleared = false;
    update_state_transaction(state_file, state, |candidate| {
        let Some(session) = candidate.get_mut(session_id) else {
            return;
        };
        let Some(mut record) = session
            .get("stop_recovery")
            .and_then(|value| serde_json::from_value::<StopRecoveryRecord>(value.clone()).ok())
            .filter(|record| record.intent_id == intent_id)
        else {
            return;
        };
        record.phase = StopRecoveryPhase::BuildPending;
        record.delivery_plan_id = None;
        record.delivery_chunk_cursor = 0;
        record.delivery_payload_digest = None;
        record.delivery_turn_id = None;
        record.delivery_root_span_id = None;
        record.delivery_window_end_offset = 0;
        record.delivery_finalizer_high_water = 0;
        record.delivery_generation_count = 0;
        record.delivery_merged_sync_ids.clear();
        record.delivery_merged_subagent_invocation_ids.clear();
        record.updated_at = updated_at.to_string();
        session["stop_recovery"] =
            serde_json::to_value(&record).expect("Stop recovery record must serialize");
        cleared = true;
    })?;
    Ok(cleared)
}

pub fn clear_stop_recovery(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    expected_intent_id: Option<&str>,
) -> Result<bool, String> {
    let mut cleared = false;
    update_state_transaction(state_file, state, |candidate| {
        let Some(session) = candidate.get_mut(session_id).and_then(Value::as_object_mut) else {
            return;
        };
        let matches = session
            .get("stop_recovery")
            .and_then(|record| record.get("intent_id"))
            .and_then(Value::as_str)
            .is_some_and(|intent_id| {
                expected_intent_id.is_none_or(|expected| expected == intent_id)
            });
        if matches {
            session.remove("stop_recovery");
            cleared = true;
        }
    })?;
    Ok(cleared)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FinalizerFutureSource {
    TranscriptGrowth,
    GenerationCursorProgress,
    ChildJobStatusTransition,
    SubagentTerminalEvidence,
    HookBudgetAvailable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct FinalizerObservedBoundary {
    finalizer_high_water: u64,
    terminal_high_water_offset: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct FiniteFinalizerDeferredRecord {
    schema_version: u64,
    reason: String,
    future_source: FinalizerFutureSource,
    retry_boundary: String,
    recovery_key: String,
    attempt: u64,
    observed_boundary: FinalizerObservedBoundary,
    updated: String,
}

pub fn finalizer_waits_for_explicit_terminal(state: &Value, session_id: &str) -> bool {
    let Some(session) = get_session_state(state, session_id) else {
        return false;
    };
    match session.get("pending_export_reason").and_then(Value::as_str) {
        Some(REALTIME_TOPOLOGY_AMBIGUOUS_REASON) => {
            session
                .get("legacy_finalizer_recovery")
                .and_then(|recovery| recovery.get("status"))
                .and_then(Value::as_str)
                == Some("awaiting_explicit_terminal")
        }
        Some(FINALIZER_EVIDENCE_DEFERRED_REASON) => session
            .get("finalizer_deferred")
            .and_then(|record| {
                serde_json::from_value::<FiniteFinalizerDeferredRecord>(record.clone()).ok()
            })
            .is_some_and(|record| record.retry_boundary == "explicit_terminal"),
        // Budget exhaustion means compute/delivery work remains. It must be
        // governed by the bounded recovery cooldown, not mistaken for missing
        // transcript evidence that only a future explicit terminal can fix.
        Some(HOOK_BUDGET_EXHAUSTED_REASON) => false,
        _ => false,
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RealtimeFinalizerTransition {
    pub high_water: u64,
    pub compensated: usize,
    pub removed_jobs: usize,
}

const COMPAT_STATE_FILE: &str = "state_compat.json";

/// Load the legacy composite view. New flat-domain files are authoritative;
/// when none exist, this imports the legacy nested state as a read fallback.
pub fn load_state(state_file: &Path) -> Value {
    if state_file.file_name().and_then(|name| name.to_str()) != Some(COMPAT_STATE_FILE) {
        return agentic_otel_common::state::load_state(state_file);
    }
    let Some(session_dir) = state_file.parent() else {
        return json!({});
    };
    let session_id = std::fs::read_to_string(session_dir.join("state_identity.json"))
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .and_then(|value| {
            value
                .get("session_id")
                .and_then(Value::as_str)
                .map(str::to_string)
        });
    let Some(session_id) = session_id else {
        return json!({});
    };
    let mut session = Map::new();
    for domain in crate::common::runtime_state::StateDomain::ALL {
        let domain_value =
            crate::common::runtime_state::DomainStateStore::for_session(&session_id, domain)
                .and_then(|store| store.load())
                .unwrap_or_else(|_| json!({}));
        if let Some(fields) = domain_value.as_object() {
            for (field, value) in fields {
                session.insert(field.clone(), value.clone());
            }
        }
    }
    let mut composite = Map::new();
    composite.insert(session_id, Value::Object(session));
    Value::Object(composite)
}

/// Persist a composite candidate as changed domain files only. The compatibility
/// path itself is a lock identity and is never written.
pub fn save_state(state_file: &Path, state: &Value) -> Result<(), String> {
    if state_file.file_name().and_then(|name| name.to_str()) != Some(COMPAT_STATE_FILE) {
        return agentic_otel_common::state::save_state(state_file, state);
    }
    let session_dir = state_file
        .parent()
        .ok_or_else(|| "compat state path has no parent".to_string())?;
    let (session_id, session) = state
        .as_object()
        .and_then(|sessions| sessions.iter().next())
        .ok_or_else(|| "compat state has no session".to_string())?;
    let fields = session
        .as_object()
        .ok_or_else(|| "compat session state is not an object".to_string())?;
    let identity_path = session_dir.join("state_identity.json");
    if !identity_path.exists() {
        crate::common::runtime_state::publish_immutable_json(
            &identity_path,
            &json!({"session_id": session_id}),
        )?;
    }
    let mut domains = crate::common::runtime_state::StateDomain::ALL
        .into_iter()
        .map(|domain| (domain, Map::new()))
        .collect::<Vec<_>>();
    for (field, value) in fields {
        let owner = crate::common::runtime_state::StateDomain::for_field(field);
        if let Some((_, domain)) = domains
            .iter_mut()
            .find(|(candidate, _)| *candidate == owner)
        {
            domain.insert(field.clone(), value.clone());
        }
    }
    for (domain, fields) in domains {
        let path = session_dir.join(domain.file_name());
        let value = Value::Object(fields);
        let existing = agentic_otel_common::state::load_state(&path);
        if value != existing {
            crate::common::runtime_state::write_json_atomically(&path, &value)?;
        }
    }
    Ok(())
}

/// Cached VCS information for a session.
#[derive(Debug, Clone)]
pub struct VcsInfo {
    pub remote_url: String,
    pub branch: String,
    pub cwd: String,
}

/// Trace context captured during PreToolUse for downstream tool propagation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolTraceContext {
    pub trace_id: String,
    pub traceparent: String,
    pub upstream_parent_span_id: Option<String>,
    pub sofa_traceid: Option<String>,
    pub sofa_rpcid: Option<String>,
}

impl ToolTraceContext {
    pub fn to_value(&self) -> Value {
        let mut value = json!({
            "trace_id": self.trace_id,
            "traceparent": self.traceparent,
        });
        if let Some(ref upstream_parent_span_id) = self.upstream_parent_span_id {
            value["upstream_parent_span_id"] = json!(upstream_parent_span_id);
        }
        if let Some(ref sofa_traceid) = self.sofa_traceid {
            value["sofa_traceid"] = json!(sofa_traceid);
        }
        if let Some(ref sofa_rpcid) = self.sofa_rpcid {
            value["sofa_rpcid"] = json!(sofa_rpcid);
        }
        value
    }

    pub fn from_value(value: &Value) -> Option<Self> {
        Some(Self {
            trace_id: value.get("trace_id")?.as_str()?.to_string(),
            traceparent: value.get("traceparent")?.as_str()?.to_string(),
            upstream_parent_span_id: value
                .get("upstream_parent_span_id")
                .and_then(Value::as_str)
                .map(String::from),
            sofa_traceid: value
                .get("sofa_traceid")
                .and_then(Value::as_str)
                .map(String::from),
            sofa_rpcid: value
                .get("sofa_rpcid")
                .and_then(Value::as_str)
                .map(String::from),
        })
    }
}

/// Read cached VCS info from session state.
pub fn get_vcs_from_state(state: &Value, session_id: &str) -> Option<VcsInfo> {
    let session = state.get(session_id)?;
    let vcs = session.get("vcs")?;
    let remote_url = vcs.get("remote_url")?.as_str()?.to_string();
    let branch = vcs.get("branch")?.as_str()?.to_string();
    let cwd = vcs.get("cwd")?.as_str()?.to_string();
    if remote_url.is_empty() && branch.is_empty() {
        return None;
    }
    Some(VcsInfo {
        remote_url,
        branch,
        cwd,
    })
}

pub fn get_vcs_for_cwd(state: &Value, session_id: &str, cwd: Option<&str>) -> Option<VcsInfo> {
    let vcs = get_vcs_from_state(state, session_id)?;
    if cwd
        .map(str::trim)
        .filter(|cwd| !cwd.is_empty())
        .is_some_and(|cwd| cwd != vcs.cwd)
    {
        return None;
    }
    Some(vcs)
}

pub fn set_vcs_in_state(
    state: &mut Value,
    session_id: &str,
    vcs: &VcsInfo,
    remote_name: Option<&str>,
    selected_remote_match: Option<&str>,
    captured_at: &str,
) {
    ensure_session_object(state, session_id);
    let mut value = json!({
        "remote_url": vcs.remote_url,
        "branch": vcs.branch,
        "cwd": vcs.cwd,
        "captured_at": captured_at,
    });
    if let Some(remote_name) = remote_name.filter(|value| !value.is_empty()) {
        value["remote_name"] = json!(remote_name);
    }
    if let Some(selected_remote_match) = selected_remote_match.filter(|value| !value.is_empty()) {
        value["selected_remote_match"] = json!(selected_remote_match);
    }
    state[session_id]["vcs"] = value;
}

pub fn clear_vcs_in_state(state: &mut Value, session_id: &str) {
    if let Some(session) = state.get_mut(session_id).and_then(Value::as_object_mut) {
        session.remove("vcs");
    }
}

pub fn get_current_trace_context(state: &Value, session_id: &str) -> Option<ToolTraceContext> {
    state
        .get(session_id)?
        .get("current_trace_context")
        .and_then(ToolTraceContext::from_value)
}

pub fn get_trace_context_history(state: &Value, session_id: &str) -> Vec<ToolTraceContext> {
    state
        .get(session_id)
        .and_then(|session| session.get("trace_context_history"))
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(ToolTraceContext::from_value)
                .collect()
        })
        .unwrap_or_default()
}

pub fn rotate_current_trace_context(state: &mut Value, session_id: &str) {
    let Some(current) = state
        .get(session_id)
        .and_then(|session| session.get("current_trace_context"))
        .cloned()
    else {
        ensure_session_object(state, session_id);
        return;
    };

    ensure_session_object(state, session_id);
    if let Some(session) = state.get_mut(session_id).and_then(Value::as_object_mut) {
        let history = session
            .entry("trace_context_history".to_string())
            .or_insert_with(|| json!([]));
        if let Some(items) = history.as_array_mut() {
            items.push(current);
            if items.len() > POSTED_TURN_ID_RETENTION {
                items.drain(0..items.len() - POSTED_TURN_ID_RETENTION);
            }
        }
        session.remove("current_trace_context");
    }
}

pub fn set_current_trace_context(state: &mut Value, session_id: &str, ctx: &ToolTraceContext) {
    ensure_session_object(state, session_id);
    state[session_id]["current_trace_context"] = ctx.to_value();
}

fn known_billing_mode(value: &str) -> Option<&str> {
    match value {
        "subscription" | "pay_as_you_go" => Some(value),
        _ => None,
    }
}

pub(crate) fn record_turn_billing_mode(
    state: &mut Value,
    session_id: &str,
    turn_id: &str,
    mode: &str,
) {
    if turn_id.trim().is_empty() || known_billing_mode(mode).is_none() {
        return;
    }
    ensure_session_object(state, session_id);
    if !state[session_id]["turn_billing_modes"].is_array() {
        state[session_id]["turn_billing_modes"] = json!([]);
    }
    let snapshots = state[session_id]["turn_billing_modes"]
        .as_array_mut()
        .unwrap();
    // The first accepted prompt snapshot owns the turn, including during retries.
    if snapshots
        .iter()
        .any(|item| item["turn_id"].as_str() == Some(turn_id))
    {
        return;
    }
    snapshots.push(json!({"turn_id": turn_id, "mode": mode}));
    if snapshots.len() > POSTED_TURN_ID_RETENTION {
        snapshots.drain(..snapshots.len() - POSTED_TURN_ID_RETENTION);
    }
}

pub(crate) fn turn_billing_mode<'a>(
    state: &'a Value,
    session_id: &str,
    turn_id: &str,
) -> Option<&'a str> {
    state
        .get(session_id)?
        .get("turn_billing_modes")?
        .as_array()?
        .iter()
        .find(|item| item["turn_id"].as_str() == Some(turn_id))?
        .get("mode")?
        .as_str()
        .and_then(known_billing_mode)
}

fn ensure_session_object(state: &mut Value, session_id: &str) {
    if !state.is_object() {
        *state = json!({});
    }
    if !state.get(session_id).map(Value::is_object).unwrap_or(false) {
        state[session_id] = json!({});
    }
}

/// Update session state in memory while preserving fields owned by newer or
/// optional code paths.
fn update_session_state(
    state: &mut Value,
    session_id: &str,
    offset: i64,
    buffer: &str,
    pending_messages: &[Value],
    posted_turn_ids: &[String],
    turn_count: usize,
    now: &str,
    transcript_path: Option<&str>,
) {
    let mut session = state
        .get(session_id)
        .cloned()
        .filter(Value::is_object)
        .unwrap_or_else(|| json!({}));
    session["offset"] = json!(offset);
    session["buffer"] = json!(buffer);
    session["pending_messages"] = json!(pending_messages);
    session["posted_turn_ids"] = json!(posted_turn_ids);
    session["turn_count"] = json!(turn_count);
    session["updated"] = json!(now);
    // Persist transcript_path so the salvage pass at the next Stop event can
    // re-process this session if its task_complete arrived after the hook
    // returned (Codex Desktop can delay task_complete by 5+ seconds).
    if let Some(path) = transcript_path {
        session["transcript_path"] = json!(path);
    }
    state[session_id] = session;
}

pub fn update_state_transaction<F>(
    state_file: &Path,
    state: &mut Value,
    update: F,
) -> Result<(), String>
where
    F: FnOnce(&mut Value),
{
    update_state_transaction_fallible_with_timeout(
        state_file,
        state,
        STATE_LOCK_TIMEOUT_MS,
        |candidate| {
            update(candidate);
            Ok(())
        },
    )
}

pub fn update_state_transaction_with_timeout<F>(
    state_file: &Path,
    state: &mut Value,
    timeout_ms: u64,
    update: F,
) -> Result<(), String>
where
    F: FnOnce(&mut Value),
{
    update_state_transaction_fallible_with_timeout(state_file, state, timeout_ms, |candidate| {
        update(candidate);
        Ok(())
    })
}

pub fn update_state_transaction_fallible<F>(
    state_file: &Path,
    state: &mut Value,
    update: F,
) -> Result<(), String>
where
    F: FnOnce(&mut Value) -> Result<(), String>,
{
    update_state_transaction_fallible_with_timeout(state_file, state, STATE_LOCK_TIMEOUT_MS, update)
}

pub fn update_state_transaction_fallible_with_timeout<F>(
    state_file: &Path,
    state: &mut Value,
    timeout_ms: u64,
    update: F,
) -> Result<(), String>
where
    F: FnOnce(&mut Value) -> Result<(), String>,
{
    let state_lock = StateLock::acquire(state_file, timeout_ms);
    if !state_lock.is_acquired() {
        return Err(format!("state lock unavailable after {}ms", timeout_ms));
    }

    let loaded = load_state(state_file);
    let base = if loaded.as_object().is_some_and(|value| value.is_empty()) {
        state.clone()
    } else {
        loaded
    };
    let mut candidate = base;
    update(&mut candidate)?;
    save_state(state_file, &candidate)?;
    *state = candidate;
    Ok(())
}

pub fn mark_export_terminal_failure(
    state_file: &Path,
    session_id: &str,
    error: &str,
) -> Result<(), String> {
    let mut state = json!({});
    update_state_transaction(state_file, &mut state, |candidate| {
        let mut session = candidate
            .get(session_id)
            .cloned()
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({}));
        session.as_object_mut().map(|object| {
            object.remove("pending_export_reason");
            object.remove("pending_subagent_ids");
        });
        session["terminal_export_error"] = json!(error);
        candidate[session_id] = session;
    })
}

fn normalized_root_identities(original_root_identities: &[String]) -> Vec<String> {
    let mut roots = Vec::new();
    for root in original_root_identities {
        let root = root.trim();
        if !root.is_empty() && !roots.iter().any(|existing| existing == root) {
            roots.push(root.to_string());
        }
    }
    roots
}

fn entry_matches_terminal_turn(entry: &Value, turn_id: &str, recovery_key: Option<&str>) -> bool {
    if entry.get("turn_id").and_then(Value::as_str) == Some(turn_id) {
        return true;
    }
    recovery_key.is_some_and(|key| {
        !key.is_empty() && entry.get("recovery_key").and_then(Value::as_str) == Some(key)
    })
}

fn matching_terminal_turn_id(
    entries: &[Value],
    turn_id: &str,
    recovery_key: Option<&str>,
) -> Option<String> {
    entries
        .iter()
        .find(|entry| entry_matches_terminal_turn(entry, turn_id, recovery_key))
        .and_then(|entry| entry.get("turn_id").and_then(Value::as_str))
        .map(str::to_string)
}

fn export_accounting_high_water(session: &Value) -> Option<i64> {
    session
        .get("export_accounting")
        .and_then(|accounting| accounting.get("terminal_high_water_offset"))
        .and_then(Value::as_i64)
}

pub fn terminal_accounted_turn_ids(session: &Value) -> Vec<String> {
    session
        .get("export_accounting")
        .map(|accounting| get_string_array_field(accounting, "terminal_turn_ids"))
        .unwrap_or_default()
}

pub fn is_turn_terminally_accounted(session: &Value, turn_id: &str) -> bool {
    terminal_accounted_turn_ids(session)
        .iter()
        .any(|accounted| accounted == turn_id)
}

/// Resolve durable accounting against a complete, ordered session history.
/// `turn_count` counts successful non-terminal turns even after their ids age
/// out of the bounded `posted_turn_ids` diagnostic window.
pub fn durably_accounted_turn_ids_for_history<'a>(
    session: &Value,
    ordered_turn_ids: impl IntoIterator<Item = &'a str>,
) -> HashSet<String> {
    let terminal = terminal_accounted_turn_ids(session)
        .into_iter()
        .collect::<HashSet<_>>();
    let mut accounted = terminal.clone();
    accounted.extend(get_string_array_field(session, "posted_turn_ids"));
    let successful_count = get_int_field(session, "turn_count").max(0) as usize;
    let mut successful_ordinal = 0usize;
    for turn_id in ordered_turn_ids {
        if terminal.contains(turn_id) {
            continue;
        }
        if successful_ordinal < successful_count {
            accounted.insert(turn_id.to_string());
            successful_ordinal = successful_ordinal.saturating_add(1);
        }
    }
    accounted
}

pub fn terminal_quarantine_count(session: &Value) -> usize {
    get_array_field(session, "quarantined_turns").len()
}

fn merge_terminal_export_accounting(
    session: &mut Value,
    turn_id: &str,
    terminal_high_water_offset: Option<i64>,
    now: &str,
) -> Option<i64> {
    let mut accounting = session
        .get("export_accounting")
        .cloned()
        .filter(Value::is_object)
        .unwrap_or_else(|| json!({}));
    let mut terminal_turn_ids = get_string_array_field(&accounting, "terminal_turn_ids");
    if !terminal_turn_ids.iter().any(|existing| existing == turn_id) {
        terminal_turn_ids.push(turn_id.to_string());
    }

    let merged_high_water = match (
        export_accounting_high_water(session),
        terminal_high_water_offset,
    ) {
        (Some(current), Some(incoming)) => Some(current.max(incoming)),
        (Some(current), None) => Some(current),
        (None, Some(incoming)) => Some(incoming),
        (None, None) => None,
    };

    accounting["state_version"] = json!(EXPORT_ACCOUNTING_STATE_VERSION);
    accounting["terminal_turn_ids"] = json!(terminal_turn_ids);
    accounting["updated"] = json!(now);
    if let Some(offset) = merged_high_water {
        accounting["terminal_high_water_offset"] = json!(offset);
    }
    session["export_accounting"] = accounting;
    merged_high_water
}

/// Record a structural terminal turn exactly once and advance terminal export
/// accounting without marking the turn as successfully posted.
///
/// This is the durable state transition for fail-closed topology ambiguity
/// such as MultiplePublishedRoots. It deliberately keeps `posted_turn_ids`
/// and `turn_count` untouched, while clearing retry markers that would
/// otherwise keep later hooks replaying the same poisoned turn.
#[allow(clippy::too_many_arguments)]
pub fn record_terminal_quarantined_turn(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    turn_id: &str,
    reason: &str,
    original_root_identities: &[String],
    now: &str,
    attempt: u64,
    recovery_key: Option<&str>,
    terminal_high_water_offset: Option<i64>,
) -> Result<TerminalQuarantineTransition, String> {
    let recovery_key = recovery_key
        .map(str::trim)
        .filter(|key| !key.is_empty())
        .map(str::to_string);
    let entry = TerminalQuarantineEntry {
        turn_id: turn_id.to_string(),
        reason: reason.to_string(),
        original_root_identities: normalized_root_identities(original_root_identities),
        timestamp: now.to_string(),
        attempt,
        state_version: QUARANTINE_STATE_VERSION,
        recovery_key: recovery_key.clone(),
        terminal_high_water_offset,
    };
    let entry_value = serde_json::to_value(entry).map_err(|error| error.to_string())?;
    let mut transition = TerminalQuarantineTransition {
        appended: false,
        terminal_high_water_offset: None,
    };

    save_candidate_state(state_file, state, |candidate| {
        let mut session = candidate
            .get(session_id)
            .cloned()
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({}));
        let mut quarantined_turns = get_array_field(&session, "quarantined_turns");
        let matching_turn_id =
            matching_terminal_turn_id(&quarantined_turns, turn_id, recovery_key.as_deref());
        let already_quarantined = matching_turn_id.is_some();
        if !already_quarantined {
            quarantined_turns.push(entry_value);
            transition.appended = true;
        }
        session["quarantined_turns"] = json!(quarantined_turns);
        let accounting_turn_id = matching_turn_id.as_deref().unwrap_or(turn_id);
        transition.terminal_high_water_offset = merge_terminal_export_accounting(
            &mut session,
            accounting_turn_id,
            terminal_high_water_offset,
            now,
        );
        session["updated"] = json!(now);
        if let Some(object) = session.as_object_mut() {
            object.remove("pending_export_reason");
            object.remove("pending_subagent_ids");
            object.remove("pending_final_export");
            object.remove("pending_window_end_offset");
            object.remove("legacy_finalizer_recovery");
            object.remove("finalizer_deferred");
        }
        candidate[session_id] = session;
    })?;

    Ok(transition)
}

fn save_candidate_state<F>(state_file: &Path, state: &mut Value, update: F) -> Result<(), String>
where
    F: FnOnce(&mut Value),
{
    update_state_transaction(state_file, state, update)
}

pub fn legacy_finalizer_recovery_key(session_id: &str, transcript_path: &str) -> String {
    format!("legacy_finalizer_recovery:{session_id}:{transcript_path}")
}

pub fn finalizer_recovery_key(
    transcript_path: &str,
    turn_id: &str,
    terminal_high_water_offset: i64,
) -> String {
    format!("realtime_finalizer:{transcript_path}:{turn_id}:{terminal_high_water_offset}")
}

/// Claim the logical attempt for one explicit finalizer boundary.
///
/// An `armed` marker is crash-resumable and therefore keeps its attempt. Only
/// a subsequent explicit terminal trigger after `awaiting_explicit_terminal`
/// advances the durable attempt counter.
pub fn claim_finalizer_recovery_attempt(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    now: &str,
    transcript_path: &str,
    recovery_key: &str,
) -> Result<LegacyFinalizerRecoveryTransition, String> {
    let mut transition = None;
    save_candidate_state(state_file, state, |candidate| {
        let mut session = candidate
            .get(session_id)
            .cloned()
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({}));
        let existing = session
            .get("legacy_finalizer_recovery")
            .cloned()
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({}));
        let same_recovery =
            existing.get("recovery_key").and_then(Value::as_str) == Some(recovery_key);
        let existing_attempt = existing
            .get("attempt")
            .and_then(Value::as_u64)
            .filter(|attempt| *attempt > 0)
            .unwrap_or(1);
        let awaiting =
            existing.get("status").and_then(Value::as_str) == Some("awaiting_explicit_terminal");
        let (attempt, armed) = if same_recovery && awaiting {
            (existing_attempt.saturating_add(1), true)
        } else if same_recovery {
            (existing_attempt, false)
        } else {
            (1, true)
        };
        session["legacy_finalizer_recovery"] = json!({
            "state_version": LEGACY_FINALIZER_RECOVERY_STATE_VERSION,
            "recovery_key": recovery_key,
            "attempt": attempt,
            "status": "armed",
            "updated": now,
            "transcript_path": transcript_path,
        });
        session["updated"] = json!(now);
        transition = Some(LegacyFinalizerRecoveryTransition {
            recovery_key: recovery_key.to_string(),
            attempt,
            status: "armed".to_string(),
            armed,
        });
        candidate[session_id] = session;
    })?;
    transition.ok_or_else(|| "finalizer recovery claim did not produce a transition".to_string())
}

#[allow(clippy::too_many_arguments)]
pub fn record_finite_finalizer_deferred(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    reason: &str,
    now: &str,
    transcript_path: &str,
    recovery_key: &str,
    attempt: u64,
    finalizer_high_water: u64,
    terminal_high_water_offset: i64,
) -> Result<FiniteFinalizerDeferredTransition, String> {
    let deferred_record = FiniteFinalizerDeferredRecord {
        schema_version: 1,
        reason: reason.to_string(),
        future_source: FinalizerFutureSource::TranscriptGrowth,
        retry_boundary: "explicit_terminal".to_string(),
        recovery_key: recovery_key.to_string(),
        attempt: attempt.max(1),
        observed_boundary: FinalizerObservedBoundary {
            finalizer_high_water,
            terminal_high_water_offset,
        },
        updated: now.to_string(),
    };
    let deferred_record_value =
        serde_json::to_value(&deferred_record).map_err(|error| error.to_string())?;
    let mut transition = None;
    save_candidate_state(state_file, state, |candidate| {
        let mut session = candidate
            .get(session_id)
            .cloned()
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({}));
        let repeated_boundary = session
            .get("finalizer_deferred")
            .and_then(|record| {
                serde_json::from_value::<FiniteFinalizerDeferredRecord>(record.clone()).ok()
            })
            .is_some_and(|record| {
                record.recovery_key == recovery_key
                    && record.observed_boundary.finalizer_high_water == finalizer_high_water
                    && record.observed_boundary.terminal_high_water_offset
                        == terminal_high_water_offset
            });
        if !repeated_boundary {
            session["pending_export_reason"] = json!(FINALIZER_EVIDENCE_DEFERRED_REASON);
            session["pending_subagent_ids"] = json!([]);
            session["transcript_path"] = json!(transcript_path);
            session["finalizer_deferred"] = deferred_record_value;
            session["legacy_finalizer_recovery"] = json!({
                "state_version": LEGACY_FINALIZER_RECOVERY_STATE_VERSION,
                "recovery_key": recovery_key,
                "attempt": attempt.max(1),
                "status": "awaiting_explicit_terminal",
                "updated": now,
                "transcript_path": transcript_path,
            });
            session["updated"] = json!(now);
            candidate[session_id] = session;
        }
        transition = Some(FiniteFinalizerDeferredTransition {
            attempt: attempt.max(1),
            repeated_boundary,
        });
    })?;
    transition.ok_or_else(|| "finite finalizer deferred transition was not produced".to_string())
}

pub fn finalize_realtime_high_water(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    high_water: u64,
    transcript_offset: u64,
    generation_count: u64,
) -> Result<RealtimeFinalizerTransition, String> {
    let mut transition = RealtimeFinalizerTransition {
        high_water,
        compensated: 0,
        removed_jobs: 0,
    };
    update_state_transaction_fallible(state_file, state, |candidate| {
        let jobs = candidate
            .get(session_id)
            .and_then(|session| session.get("realtime_export"))
            .and_then(|realtime| realtime.get("registered_jobs"))
            .and_then(Value::as_object)
            .map(|jobs| {
                jobs.values()
                    .filter_map(|value| {
                        serde_json::from_value::<super::realtime_state::RealtimeRegisteredJob>(
                            value.clone(),
                        )
                        .ok()
                    })
                    .filter(|job| {
                        job.sequence <= high_water
                            && matches!(job.kind.as_str(), "tool" | "generation" | "batch")
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        for job in jobs {
            let needs_compensation = match job.status {
                super::realtime_state::RealtimeRegisteredJobStatus::Accounted
                | super::realtime_state::RealtimeRegisteredJobStatus::Compensated => false,
                super::realtime_state::RealtimeRegisteredJobStatus::PolicyTerminal => {
                    super::realtime_state::retry_policy_for_context(
                        candidate,
                        session_id,
                        &job.context_id,
                    ) != Some(crate::hooks::RetryPolicy::AtMostOnce)
                }
                super::realtime_state::RealtimeRegisteredJobStatus::Failed
                | super::realtime_state::RealtimeRegisteredJobStatus::PermanentFailed
                | super::realtime_state::RealtimeRegisteredJobStatus::Queued
                | super::realtime_state::RealtimeRegisteredJobStatus::Inflight => true,
            };
            if needs_compensation {
                super::realtime_state::compensate_realtime_job_in_state(
                    candidate,
                    session_id,
                    &job.job_id,
                    &job.payload_digest,
                )?;
                transition.compensated = transition.compensated.saturating_add(1);
            }
        }

        let Some(realtime) = candidate
            .get_mut(session_id)
            .and_then(Value::as_object_mut)
            .and_then(|session| session.get_mut("realtime_export"))
            .and_then(Value::as_object_mut)
        else {
            return Ok(());
        };
        let generation = realtime.entry("generation".to_string()).or_insert_with(|| {
            json!({
                "batch_size": crate::hooks::DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
                "rounds": [],
            })
        });
        if let Some(generation) = generation.as_object_mut() {
            generation.insert("scan_anchor".to_string(), json!(transcript_offset));
            generation.insert("committed_offset".to_string(), json!(transcript_offset));
            generation.insert("scan_base_ordinal".to_string(), json!(generation_count));
            generation.insert("finalizer_only_ordinals".to_string(), json!([]));
            generation.insert("window_consumed".to_string(), json!(0));
        }

        let mut removed_job_ids = HashSet::new();
        let mut live_context_ids = HashSet::new();
        if let Some(jobs) = realtime
            .get_mut("registered_jobs")
            .and_then(Value::as_object_mut)
        {
            let job_ids = jobs.keys().cloned().collect::<Vec<_>>();
            for job_id in job_ids {
                let Some(job) = jobs.get(&job_id).and_then(|value| {
                    serde_json::from_value::<super::realtime_state::RealtimeRegisteredJob>(
                        value.clone(),
                    )
                    .ok()
                }) else {
                    continue;
                };
                if matches!(job.kind.as_str(), "tool" | "generation" | "batch")
                    && job.sequence > high_water
                {
                    live_context_ids.insert(job.context_id.clone());
                }
                if matches!(job.kind.as_str(), "tool" | "generation" | "batch")
                    && job.sequence <= high_water
                    && matches!(
                        job.status,
                        super::realtime_state::RealtimeRegisteredJobStatus::Accounted
                            | super::realtime_state::RealtimeRegisteredJobStatus::PolicyTerminal
                            | super::realtime_state::RealtimeRegisteredJobStatus::Compensated
                    )
                {
                    jobs.remove(&job_id);
                    removed_job_ids.insert(job_id);
                }
            }
        }
        transition.removed_jobs = removed_job_ids.len();
        if let Some(rounds) = realtime
            .get_mut("generation")
            .and_then(Value::as_object_mut)
            .and_then(|generation| generation.get_mut("rounds"))
            .and_then(Value::as_array_mut)
        {
            rounds.retain(|round| {
                round
                    .get("job_id")
                    .and_then(Value::as_str)
                    .is_none_or(|job_id| !removed_job_ids.contains(job_id))
            });
        }
        if let Some(pending) = realtime.get_mut("pending").and_then(Value::as_array_mut) {
            pending.retain(|context| {
                context
                    .get("context_id")
                    .and_then(Value::as_str)
                    .is_some_and(|context_id| live_context_ids.contains(context_id))
            });
        }
        let remove_current = realtime
            .get("current")
            .and_then(|context| context.get("context_id"))
            .and_then(Value::as_str)
            .is_some_and(|context_id| !live_context_ids.contains(context_id));
        if remove_current {
            realtime.remove("current");
        }
        Ok(())
    })?;
    Ok(transition)
}

pub fn arm_legacy_finalizer_recovery(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    now: &str,
    transcript_path: &str,
) -> Result<Option<LegacyFinalizerRecoveryTransition>, String> {
    let fallback_recovery_key = legacy_finalizer_recovery_key(session_id, transcript_path);
    let mut transition = None;
    save_candidate_state(state_file, state, |candidate| {
        let mut session = candidate
            .get(session_id)
            .cloned()
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({}));
        let pending_reason = session
            .get("pending_export_reason")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if pending_reason != REALTIME_TOPOLOGY_AMBIGUOUS_REASON {
            candidate[session_id] = session;
            return;
        }
        let existing = session
            .get("legacy_finalizer_recovery")
            .cloned()
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({}));
        let existing_recovery_key = existing
            .get("recovery_key")
            .and_then(Value::as_str)
            .filter(|key| !key.is_empty())
            .map(str::to_string);
        let recovery_key = existing_recovery_key
            .clone()
            .unwrap_or_else(|| fallback_recovery_key.clone());
        let existing_status = existing
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let attempt = existing
            .get("attempt")
            .and_then(Value::as_u64)
            .filter(|attempt| *attempt > 0)
            .unwrap_or(1);
        let status = if existing_status == "awaiting_explicit_terminal" {
            "awaiting_explicit_terminal"
        } else {
            "armed"
        };
        let armed = existing_recovery_key.is_none();
        session["legacy_finalizer_recovery"] = json!({
            "state_version": LEGACY_FINALIZER_RECOVERY_STATE_VERSION,
            "recovery_key": recovery_key,
            "attempt": attempt,
            "status": status,
            "updated": now,
            "transcript_path": transcript_path,
        });
        session["updated"] = json!(now);
        transition = Some(LegacyFinalizerRecoveryTransition {
            recovery_key: recovery_key.clone(),
            attempt,
            status: status.to_string(),
            armed,
        });
        candidate[session_id] = session;
    })?;
    Ok(transition)
}

fn merge_string_array_field(session: &mut Value, field: &str, incoming: &[String]) {
    let mut merged = get_string_array_field(session, field);
    for item in incoming {
        if !merged.contains(item) {
            merged.push(item.clone());
        }
    }
    session[field] = json!(merged);
}

fn merge_subagent_ids(session: &mut Value, merged_subagent_ids: &[String]) {
    merge_string_array_field(session, "merged_subagent_ids", merged_subagent_ids);
}

fn merge_subagent_invocation_ids(session: &mut Value, merged_subagent_invocation_ids: &[String]) {
    merge_string_array_field(
        session,
        "merged_subagent_invocation_ids",
        merged_subagent_invocation_ids,
    );
}

fn has_unmerged_items(session: &Value, field: &str, incoming: &[String]) -> bool {
    let existing = get_string_array_field(session, field);
    incoming.iter().any(|item| !existing.contains(item))
}

fn prune_posted_turn_ids(posted_turn_ids: &[String]) -> Vec<String> {
    let keep_from = posted_turn_ids
        .len()
        .saturating_sub(POSTED_TURN_ID_RETENTION);
    posted_turn_ids[keep_from..].to_vec()
}

/// Update session state and persist to disk atomically.
pub fn update_state_and_save(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    offset: i64,
    buffer: &str,
    pending_messages: &[Value],
    posted_turn_ids: &[String],
    turn_count: usize,
    now: &str,
    transcript_path: Option<&str>,
) -> Result<(), String> {
    save_candidate_state(state_file, state, |candidate| {
        update_session_state(
            candidate,
            session_id,
            offset,
            buffer,
            pending_messages,
            posted_turn_ids,
            turn_count,
            now,
            transcript_path,
        );
    })
}

#[allow(clippy::too_many_arguments)]
pub fn update_state_and_save_with_compaction_cursor(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    offset: i64,
    buffer: &str,
    pending_messages: &[Value],
    posted_turn_ids: &[String],
    turn_count: usize,
    now: &str,
    transcript_path: Option<&str>,
    compaction_cursor: &Value,
) -> Result<(), String> {
    save_candidate_state(state_file, state, |candidate| {
        update_session_state(
            candidate,
            session_id,
            offset,
            buffer,
            pending_messages,
            posted_turn_ids,
            turn_count,
            now,
            transcript_path,
        );
        candidate[session_id]["compaction_token_cursor"] = compaction_cursor.clone();
    })
}

pub fn persist_compaction_cursor_for_offset(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    expected_offset: i64,
    compaction_cursor: &Value,
) -> Result<(), String> {
    update_state_transaction_fallible(state_file, state, |candidate| {
        let actual_offset = candidate
            .get(session_id)
            .map(|session| get_int_field(session, "offset"))
            .unwrap_or(0);
        if actual_offset != expected_offset {
            return Err(format!(
                "compaction cursor offset changed: expected {expected_offset}, actual {actual_offset}"
            ));
        }
        ensure_session_object(candidate, session_id);
        candidate[session_id]["compaction_token_cursor"] = compaction_cursor.clone();
        Ok(())
    })
}

/// Persist a non-sensitive retry reason without advancing transcript offsets,
/// posted turn ids, merge history, or parent completion state.
pub fn record_pending_export(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    reason: &str,
    pending_subagent_ids: &[String],
    now: &str,
    transcript_path: &str,
) -> Result<(), String> {
    record_pending_export_with_recovery(
        state_file,
        state,
        session_id,
        reason,
        pending_subagent_ids,
        now,
        transcript_path,
        None,
    )
}

pub fn record_pending_export_awaiting_explicit_terminal(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    now: &str,
    transcript_path: &str,
    recovery_key: &str,
    attempt: u64,
) -> Result<(), String> {
    record_pending_export_with_recovery(
        state_file,
        state,
        session_id,
        REALTIME_TOPOLOGY_AMBIGUOUS_REASON,
        &[],
        now,
        transcript_path,
        Some((recovery_key, attempt)),
    )
}

#[allow(clippy::too_many_arguments)]
fn record_pending_export_with_recovery(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    reason: &str,
    pending_subagent_ids: &[String],
    now: &str,
    transcript_path: &str,
    recovery: Option<(&str, u64)>,
) -> Result<(), String> {
    save_candidate_state(state_file, state, |candidate| {
        let mut session = candidate
            .get(session_id)
            .cloned()
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({}));
        session["pending_export_reason"] = json!(reason);
        session["pending_subagent_ids"] = json!(pending_subagent_ids);
        session["updated"] = json!(now);
        session["transcript_path"] = json!(transcript_path);
        if let Some((recovery_key, attempt)) = recovery {
            session["legacy_finalizer_recovery"] = json!({
                "state_version": LEGACY_FINALIZER_RECOVERY_STATE_VERSION,
                "recovery_key": recovery_key,
                "attempt": attempt.max(1),
                "status": "awaiting_explicit_terminal",
                "updated": now,
                "transcript_path": transcript_path,
            });
        }
        candidate[session_id] = session;
    })
}

/// Persist one remotely accepted turn without advancing the transcript cursor.
///
/// This checkpoint is idempotent by turn id. It preserves the current
/// offset/buffer/pending window, keeps every posted id needed to suppress a
/// reread from that cursor, and leaves a marker that makes the session
/// salvage-eligible until the window is finalized.
#[allow(clippy::too_many_arguments)]
pub fn checkpoint_turn_export_success(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    turn_id: &str,
    now: &str,
    transcript_path: Option<&str>,
    merged_subagent_ids: &[String],
    merged_subagent_invocation_ids: &[String],
) -> Result<usize, String> {
    checkpoint_turn_accounted(
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

/// Persist a turn before its single at-most-once transport attempt.
///
/// If this checkpoint fails, the caller must not send the turn. Once it
/// succeeds, the turn remains accounted for even if the HTTP request fails or
/// the hook exits before receiving a response.
#[allow(clippy::too_many_arguments)]
pub fn checkpoint_turn_export_attempt(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    turn_id: &str,
    now: &str,
    transcript_path: Option<&str>,
    merged_subagent_ids: &[String],
    merged_subagent_invocation_ids: &[String],
) -> Result<usize, String> {
    checkpoint_turn_accounted(
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

#[allow(clippy::too_many_arguments)]
fn checkpoint_turn_accounted(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    turn_id: &str,
    now: &str,
    transcript_path: Option<&str>,
    merged_subagent_ids: &[String],
    merged_subagent_invocation_ids: &[String],
) -> Result<usize, String> {
    let existing = get_session_state(state, session_id);
    let posted_turn_ids = existing
        .map(|session| get_string_array_field(session, "posted_turn_ids"))
        .unwrap_or_default();
    let turn_count = existing
        .map(|session| get_int_field(session, "turn_count") as usize)
        .unwrap_or(0);

    let has_existing_turn = posted_turn_ids.iter().any(|posted| posted == turn_id);
    let merge_history_is_current = existing
        .map(|session| {
            !has_unmerged_items(session, "merged_subagent_ids", merged_subagent_ids)
                && !has_unmerged_items(
                    session,
                    "merged_subagent_invocation_ids",
                    merged_subagent_invocation_ids,
                )
        })
        .unwrap_or(false);
    if has_existing_turn && merge_history_is_current {
        return Ok(turn_count);
    }

    let mut committed_turn_count = turn_count;
    save_candidate_state(state_file, state, |candidate| {
        let mut session = candidate
            .get(session_id)
            .cloned()
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({}));

        if session.get("offset").is_none() {
            session["offset"] = json!(0);
        }
        if session.get("buffer").is_none() {
            session["buffer"] = json!("");
        }
        if session.get("pending_messages").is_none() {
            session["pending_messages"] = json!([]);
        }
        let mut posted_turn_ids = get_string_array_field(&session, "posted_turn_ids");
        let current_turn_count = get_int_field(&session, "turn_count").max(0) as usize;
        if posted_turn_ids.iter().any(|posted| posted == turn_id) {
            committed_turn_count = current_turn_count;
            merge_subagent_ids(&mut session, merged_subagent_ids);
            merge_subagent_invocation_ids(&mut session, merged_subagent_invocation_ids);
            candidate[session_id] = session;
            return;
        }
        posted_turn_ids.push(turn_id.to_string());
        committed_turn_count = current_turn_count + 1;
        session["posted_turn_ids"] = json!(posted_turn_ids);
        session["turn_count"] = json!(committed_turn_count);
        session["updated"] = json!(now);
        session["parent_stopped"] = json!(false);
        session["pending_export_reason"] = json!(PARTIAL_EXPORT_REASON);
        session["pending_subagent_ids"] = json!([]);
        if let Some(path) = transcript_path {
            session["transcript_path"] = json!(path);
        }
        merge_subagent_ids(&mut session, merged_subagent_ids);
        merge_subagent_invocation_ids(&mut session, merged_subagent_invocation_ids);
        candidate[session_id] = session;
    })?;

    Ok(committed_turn_count)
}

/// Commit a successful export and its subagent bookkeeping in one atomic
/// state write. Existing/unknown session fields are retained and merged child
/// ids are unioned so later turns cannot erase prior correlation history.
#[allow(clippy::too_many_arguments)]
pub fn commit_export_success(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    offset: i64,
    buffer: &str,
    pending_messages: &[Value],
    posted_turn_ids: &[String],
    turn_count: usize,
    now: &str,
    transcript_path: Option<&str>,
    merged_subagent_ids: &[String],
    merged_subagent_invocation_ids: &[String],
) -> Result<(), String> {
    let posted_turn_ids = prune_posted_turn_ids(posted_turn_ids);
    save_candidate_state(state_file, state, |candidate| {
        update_session_state(
            candidate,
            session_id,
            offset,
            buffer,
            pending_messages,
            &posted_turn_ids,
            turn_count,
            now,
            transcript_path,
        );

        merge_subagent_ids(&mut candidate[session_id], merged_subagent_ids);
        merge_subagent_invocation_ids(&mut candidate[session_id], merged_subagent_invocation_ids);
        candidate[session_id]["parent_stopped"] = json!(true);
        if let Some(session) = candidate[session_id].as_object_mut() {
            session.remove("pending_export_reason");
            session.remove("pending_subagent_ids");
            session.remove("current_trace_context");
            session.remove("trace_context_history");
            session.remove("legacy_finalizer_recovery");
            session.remove("finalizer_deferred");
        }
    })
}

#[allow(clippy::too_many_arguments)]
pub fn commit_export_success_with_compaction_cursor(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    offset: i64,
    buffer: &str,
    pending_messages: &[Value],
    posted_turn_ids: &[String],
    turn_count: usize,
    now: &str,
    transcript_path: Option<&str>,
    merged_subagent_ids: &[String],
    merged_subagent_invocation_ids: &[String],
    compaction_cursor: &Value,
) -> Result<(), String> {
    let posted_turn_ids = prune_posted_turn_ids(posted_turn_ids);
    save_candidate_state(state_file, state, |candidate| {
        update_session_state(
            candidate,
            session_id,
            offset,
            buffer,
            pending_messages,
            &posted_turn_ids,
            turn_count,
            now,
            transcript_path,
        );

        merge_subagent_ids(&mut candidate[session_id], merged_subagent_ids);
        merge_subagent_invocation_ids(&mut candidate[session_id], merged_subagent_invocation_ids);
        candidate[session_id]["parent_stopped"] = json!(true);
        candidate[session_id]["compaction_token_cursor"] = compaction_cursor.clone();
        if let Some(session) = candidate[session_id].as_object_mut() {
            session.remove("pending_export_reason");
            session.remove("pending_subagent_ids");
            session.remove("current_trace_context");
            session.remove("trace_context_history");
            session.remove("legacy_finalizer_recovery");
            session.remove("finalizer_deferred");
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn hook_budget_exhaustion_remains_compute_recoverable() {
        let state = json!({
            "sess": {
                "pending_export_reason": HOOK_BUDGET_EXHAUSTED_REASON
            }
        });
        assert!(!finalizer_waits_for_explicit_terminal(&state, "sess"));
        assert!(!finalizer_waits_for_explicit_terminal(&state, "other"));
    }

    #[test]
    fn stop_recovery_cooldown_is_bounded_and_terminalizes_no_progress() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript = dir.path().join("session.jsonl");
        std::fs::write(&transcript, b"stable").unwrap();
        let payload = json!({
            "session_id": "sess",
            "intent_id": "intent-1",
            "transcript_path": transcript,
        });
        let mut state = json!({
            "sess": {
                "offset": 0,
                "turn_count": 0,
                "pending_export_reason": HOOK_BUDGET_EXHAUSTED_REASON,
            }
        });

        let mut record = record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "sess",
            &payload,
            "turn export incomplete: BudgetExhausted",
            "2026-08-28T00:00:00Z",
        )
        .unwrap();
        assert_eq!(record.phase, StopRecoveryPhase::Cooldown);
        assert_eq!(record.no_progress_attempts, 0);

        for expected in 1..=STOP_RECOVERY_MAX_NO_PROGRESS_ATTEMPTS {
            record = record_stop_recovery_deferred(
                &state_file,
                &mut state,
                "sess",
                &payload,
                "turn export incomplete: BudgetExhausted",
                "2026-08-28T00:00:01Z",
            )
            .unwrap();
            assert_eq!(record.no_progress_attempts, expected);
        }
        assert_eq!(record.phase, StopRecoveryPhase::TerminalQuarantined);
        assert_eq!(
            stop_recovery_eligibility(&state, "sess"),
            StopRecoveryEligibility::Idle
        );
    }

    #[test]
    fn transcript_growth_does_not_reset_stop_recovery_no_progress() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript = dir.path().join("session.jsonl");
        std::fs::write(&transcript, b"one").unwrap();
        let payload = json!({
            "session_id": "sess",
            "intent_id": "intent-1",
            "transcript_path": transcript,
        });
        let mut state = json!({"sess": {"pending_export_reason": HOOK_BUDGET_EXHAUSTED_REASON}});
        record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "sess",
            &payload,
            "budget exhausted",
            "2026-08-28T00:00:00Z",
        )
        .unwrap();
        let repeated = record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "sess",
            &payload,
            "budget exhausted",
            "2026-08-28T00:00:01Z",
        )
        .unwrap();
        assert_eq!(repeated.no_progress_attempts, 1);

        std::fs::write(&transcript, b"one-two").unwrap();
        let still_stalled = record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "sess",
            &payload,
            "budget exhausted",
            "2026-08-28T00:00:02Z",
        )
        .unwrap();
        assert_eq!(still_stalled.no_progress_attempts, 2);
        assert_eq!(still_stalled.observed_file_len, 7);

        std::fs::write(&transcript, b"one-two-three").unwrap();
        let quarantined = record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "sess",
            &payload,
            "budget exhausted",
            "2026-08-28T00:00:03Z",
        )
        .unwrap();
        assert_eq!(quarantined.no_progress_attempts, 3);
        assert_eq!(quarantined.phase, StopRecoveryPhase::TerminalQuarantined);
    }

    #[test]
    fn failure_reason_changes_do_not_reset_stop_recovery_no_progress() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript = dir.path().join("session.jsonl");
        std::fs::write(&transcript, b"stable").unwrap();
        let payload = json!({
            "session_id": "sess",
            "intent_id": "intent-1",
            "transcript_path": transcript,
        });
        let mut state = json!({"sess": {"pending_export_reason": HOOK_BUDGET_EXHAUSTED_REASON}});

        record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "sess",
            &payload,
            "budget exhausted",
            "2026-08-28T00:00:00Z",
        )
        .unwrap();

        for (expected, reason) in [
            (1, "otel_export_failed"),
            (2, HOOK_BUDGET_EXHAUSTED_REASON),
            (3, "otel_export_failed"),
        ] {
            state["sess"]["pending_export_reason"] = json!(reason);
            let record = record_stop_recovery_deferred(
                &state_file,
                &mut state,
                "sess",
                &payload,
                reason,
                "2026-08-28T00:00:01Z",
            )
            .unwrap();
            assert_eq!(record.no_progress_attempts, expected);
            if expected == STOP_RECOVERY_MAX_NO_PROGRESS_ATTEMPTS {
                assert_eq!(record.phase, StopRecoveryPhase::TerminalQuarantined);
            }
        }
    }

    #[test]
    fn stop_recovery_cursors_are_durable_progress_boundaries() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript = dir.path().join("session.jsonl");
        std::fs::write(&transcript, b"stable").unwrap();
        let payload = json!({
            "session_id": "sess",
            "intent_id": "intent-1",
            "transcript_path": transcript,
        });
        let mut state = json!({"sess": {"pending_export_reason": HOOK_BUDGET_EXHAUSTED_REASON}});
        record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "sess",
            &payload,
            "budget exhausted",
            "2026-08-28T00:00:00Z",
        )
        .unwrap();
        let topology = StopRecoveryTopologyCheckpoint {
            turn_id: "turn-1".to_string(),
            kind: StopRecoveryTopologyKind::Exact,
            trace_id: Some("11111111111111111111111111111111".to_string()),
            root_span_id: Some("aaaaaaaaaaaaaaaa".to_string()),
        };
        checkpoint_stop_recovery_topology(
            &state_file,
            &mut state,
            "sess",
            "intent-1",
            6,
            1,
            topology,
            "2026-08-28T00:00:01Z",
        )
        .unwrap();
        checkpoint_stop_recovery_build_cursor(
            &state_file,
            &mut state,
            "sess",
            "intent-1",
            6,
            1,
            "2026-08-28T00:00:02Z",
        )
        .unwrap();
        let after_compute = record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "sess",
            &payload,
            "budget exhausted",
            "2026-08-28T00:00:03Z",
        )
        .unwrap();
        assert_eq!(after_compute.no_progress_attempts, 0);
        assert_eq!(after_compute.topology_cursor, 1);
        assert_eq!(after_compute.build_turn_cursor, 1);
        assert_eq!(after_compute.topology_checkpoints.len(), 1);

        checkpoint_stop_recovery_delivery(
            &state_file,
            &mut state,
            "sess",
            "intent-1",
            6,
            &StopRecoveryDeliveryCheckpoint {
                plan_id: "plan-1".to_string(),
                chunk_cursor: 1,
                payload_digest: "digest-1".to_string(),
                turn_id: "turn-1".to_string(),
                root_span_id: "aaaaaaaaaaaaaaaa".to_string(),
                window_end_offset: 6,
                finalizer_high_water: 3,
                generation_count: 2,
                merged_sync_ids: vec!["child-1".to_string()],
                merged_subagent_invocation_ids: vec!["call-1".to_string()],
            },
            "2026-08-28T00:00:04Z",
        )
        .unwrap();
        let after_delivery = record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "sess",
            &payload,
            "delivery failed",
            "2026-08-28T00:00:05Z",
        )
        .unwrap();
        assert_eq!(after_delivery.no_progress_attempts, 0);
        assert_eq!(after_delivery.delivery_plan_id.as_deref(), Some("plan-1"));
        assert_eq!(after_delivery.delivery_chunk_cursor, 1);
    }

    #[test]
    fn stop_recovery_delivery_intent_adoption_preserves_durable_plan() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript = dir.path().join("session.jsonl");
        std::fs::write(&transcript, b"stable").unwrap();
        let payload = json!({
            "session_id": "sess",
            "intent_id": "intent-old",
            "transcript_path": transcript,
        });
        let mut state = json!({"sess": {"pending_export_reason": HOOK_BUDGET_EXHAUSTED_REASON}});
        record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "sess",
            &payload,
            "delivery failed",
            "2026-08-28T00:00:00Z",
        )
        .unwrap();
        checkpoint_stop_recovery_delivery(
            &state_file,
            &mut state,
            "sess",
            "intent-old",
            6,
            &StopRecoveryDeliveryCheckpoint {
                plan_id: "plan-1".to_string(),
                chunk_cursor: 1,
                payload_digest: "digest-1".to_string(),
                turn_id: "turn-1".to_string(),
                root_span_id: "aaaaaaaaaaaaaaaa".to_string(),
                window_end_offset: 6,
                finalizer_high_water: 3,
                generation_count: 2,
                merged_sync_ids: vec!["child-1".to_string()],
                merged_subagent_invocation_ids: vec!["call-1".to_string()],
            },
            "2026-08-28T00:00:01Z",
        )
        .unwrap();

        let state_backup = dir.path().join("state.backup.json");
        std::fs::rename(&state_file, &state_backup).unwrap();
        std::fs::create_dir(&state_file).unwrap();
        assert!(
            adopt_stop_recovery_delivery_intent(
                &state_file,
                &mut state,
                "sess",
                "intent-old",
                "plan-1",
                "intent-new",
                "2026-08-28T00:00:02Z",
            )
            .is_err(),
            "an adoption persistence failure must be surfaced"
        );
        std::fs::remove_dir(&state_file).unwrap();
        std::fs::rename(&state_backup, &state_file).unwrap();

        let newer_payload = json!({
            "session_id": "sess",
            "intent_id": "intent-new",
            "transcript_path": transcript,
        });
        let preserved = record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "sess",
            &newer_payload,
            "failed to adopt pending finalizer delivery",
            "2026-08-28T00:00:03Z",
        )
        .unwrap();
        assert_eq!(preserved.intent_id, "intent-old");
        assert_eq!(preserved.delivery_plan_id.as_deref(), Some("plan-1"));
        assert_eq!(
            preserved.delivery_payload_digest.as_deref(),
            Some("digest-1")
        );

        assert_eq!(
            adopt_stop_recovery_delivery_intent(
                &state_file,
                &mut state,
                "sess",
                "intent-old",
                "plan-1",
                "intent-new",
                "2026-08-28T00:00:04Z",
            )
            .unwrap(),
            StopRecoveryDeliveryAdoption::Adopted
        );
        let adopted = stop_recovery_record(&state, "sess").unwrap();
        assert_eq!(adopted.intent_id, "intent-new");
        assert_eq!(adopted.phase, StopRecoveryPhase::DeliveryPending);
        assert_eq!(adopted.delivery_plan_id.as_deref(), Some("plan-1"));
        assert_eq!(adopted.delivery_turn_id.as_deref(), Some("turn-1"));
        assert_eq!(adopted.delivery_chunk_cursor, 1);
        assert_eq!(adopted.delivery_payload_digest.as_deref(), Some("digest-1"));
        assert_eq!(adopted.delivery_merged_sync_ids, vec!["child-1"]);
        assert_eq!(
            adopted.delivery_merged_subagent_invocation_ids,
            vec!["call-1"]
        );

        assert_eq!(
            adopt_stop_recovery_delivery_intent(
                &state_file,
                &mut state,
                "sess",
                "intent-old",
                "plan-1",
                "intent-new",
                "2026-08-28T00:00:05Z",
            )
            .unwrap(),
            StopRecoveryDeliveryAdoption::AlreadyOwned
        );
        assert_eq!(
            adopt_stop_recovery_delivery_intent(
                &state_file,
                &mut state,
                "sess",
                "intent-new",
                "plan-1",
                "intent-newer",
                "2026-08-28T00:00:06Z",
            )
            .unwrap(),
            StopRecoveryDeliveryAdoption::Adopted
        );
        assert_eq!(
            stop_recovery_record(&state, "sess").unwrap().intent_id,
            "intent-newer"
        );
        assert_eq!(
            adopt_stop_recovery_delivery_intent(
                &state_file,
                &mut state,
                "sess",
                "intent-old",
                "plan-1",
                "intent-stale",
                "2026-08-28T00:00:07Z",
            )
            .unwrap(),
            StopRecoveryDeliveryAdoption::Changed,
            "stale adopters must not overwrite the current recovery owner"
        );

        clear_stop_recovery_delivery(
            &state_file,
            &mut state,
            "sess",
            "intent-newer",
            "2026-08-28T00:00:08Z",
        )
        .unwrap();
        assert_eq!(
            adopt_stop_recovery_delivery_intent(
                &state_file,
                &mut state,
                "sess",
                "intent-newer",
                "plan-1",
                "intent-after-clear",
                "2026-08-28T00:00:09Z",
            )
            .unwrap(),
            StopRecoveryDeliveryAdoption::Cleared
        );
    }

    #[test]
    fn missing_terminal_does_not_arm_compute_recovery() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript = dir.path().join("session.jsonl");
        std::fs::write(&transcript, b"pending").unwrap();
        let payload = json!({
            "session_id": "sess",
            "intent_id": "intent-1",
            "transcript_path": transcript,
        });
        let mut state = json!({});
        let record = record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "sess",
            &payload,
            "target turn awaiting terminal: turn-1",
            "2026-08-28T00:00:00Z",
        )
        .unwrap();
        assert_eq!(record.phase, StopRecoveryPhase::WaitingTerminal);
        assert_eq!(
            stop_recovery_eligibility(&state, "sess"),
            StopRecoveryEligibility::Idle
        );
    }

    #[test]
    fn finalizer_future_source_rejects_unknown_values() {
        for source in [
            "transcript_growth",
            "generation_cursor_progress",
            "child_job_status_transition",
            "subagent_terminal_evidence",
            "hook_budget_available",
        ] {
            assert!(serde_json::from_value::<FinalizerFutureSource>(json!(source)).is_ok());
        }
        assert!(
            serde_json::from_value::<FinalizerFutureSource>(json!("transcript_append")).is_err()
        );
    }

    #[test]
    fn invalid_deferred_future_source_is_replaced_not_accepted_as_same_boundary() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({
            "sess": {
                "finalizer_deferred": {
                    "schema_version": 1,
                    "reason": "realtime_evidence_without_unique_match",
                    "future_source": "transcript_append",
                    "retry_boundary": "explicit_terminal",
                    "recovery_key": "recovery-1",
                    "attempt": 1,
                    "observed_boundary": {
                        "finalizer_high_water": 7,
                        "terminal_high_water_offset": 123
                    },
                    "updated": "2026-08-22T00:00:00Z"
                }
            }
        });

        let transition = record_finite_finalizer_deferred(
            &path,
            &mut state,
            "sess",
            "realtime_evidence_without_unique_match",
            "2026-08-22T00:00:01Z",
            "/tmp/session.jsonl",
            "recovery-1",
            1,
            7,
            123,
        )
        .unwrap();

        assert!(!transition.repeated_boundary);
        assert_eq!(
            state["sess"]["finalizer_deferred"]["future_source"],
            "transcript_growth"
        );
    }

    #[test]
    fn vcs_helpers_preserve_legacy_reading_and_validate_cwd() {
        let mut state = json!({});
        let vcs = VcsInfo {
            remote_url: "ssh://example/repo.git".to_string(),
            branch: "main".to_string(),
            cwd: "/repo".to_string(),
        };
        set_vcs_in_state(
            &mut state,
            "session",
            &vcs,
            Some("upstream"),
            Some("fallback"),
            "2026-07-30T00:00:00Z",
        );

        assert_eq!(
            get_vcs_for_cwd(&state, "session", Some("/repo"))
                .unwrap()
                .remote_url,
            "ssh://example/repo.git"
        );
        assert!(get_vcs_for_cwd(&state, "session", Some("/other")).is_none());
        assert_eq!(state["session"]["vcs"]["remote_name"], "upstream");
        assert_eq!(state["session"]["vcs"]["selected_remote_match"], "fallback");

        state["legacy"] = json!({
            "vcs": {
                "remote_url": "ssh://legacy/repo.git",
                "branch": "legacy",
                "cwd": "/legacy"
            }
        });
        assert_eq!(
            get_vcs_from_state(&state, "legacy").unwrap().branch,
            "legacy"
        );

        clear_vcs_in_state(&mut state, "session");
        assert!(get_vcs_from_state(&state, "session").is_none());
    }

    #[test]
    fn test_update_state_and_save() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("update.json");
        let mut state = json!({
            "sess-1": {
                "vcs": {
                    "remote_url": "ssh://example/repo.git",
                    "branch": "main",
                    "cwd": "/repo",
                    "remote_name": "upstream",
                    "selected_remote_match": "fallback",
                    "captured_at": "2026-03-27T09:00:00Z"
                }
            }
        });
        update_state_and_save(
            &path,
            &mut state,
            "sess-1",
            500,
            "leftover",
            &[],
            &[],
            10,
            "2026-03-27T10:00:00Z",
            Some("/tmp/sess-1.jsonl"),
        )
        .unwrap();
        let session = state.get("sess-1").unwrap();
        assert_eq!(session["pending_messages"], json!([]));
        assert_eq!(session["posted_turn_ids"], json!([]));
        assert_eq!(session["offset"], 500);
        assert_eq!(session["buffer"], "leftover");
        assert_eq!(session["turn_count"], 10);
        assert_eq!(session["updated"], "2026-03-27T10:00:00Z");
        assert_eq!(session["vcs"]["remote_name"], "upstream");
        assert_eq!(
            session["vcs"]["captured_at"], "2026-03-27T09:00:00Z",
            "transcript checkpoints must preserve UserPromptSubmit-owned VCS state"
        );

        // Verify persisted to disk
        let loaded = load_state(&path);
        assert_eq!(loaded, state);
    }

    #[test]
    fn compaction_cursor_is_committed_atomically_with_transcript_checkpoint() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("compaction-cursor.json");
        let mut state = json!({});
        let cursor = json!({
            "version": 1,
            "initialized": true,
            "offset": 500,
            "last_trusted_input_tokens": 321,
            "pending_seed_tokens": 123
        });

        update_state_and_save_with_compaction_cursor(
            &path,
            &mut state,
            "sess-1",
            500,
            "leftover",
            &[json!({"type":"event_msg"})],
            &[],
            10,
            "2026-08-23T04:00:00Z",
            Some("/tmp/sess-1.jsonl"),
            &cursor,
        )
        .unwrap();

        let loaded = load_state(&path);
        assert_eq!(loaded["sess-1"]["offset"], 500);
        assert_eq!(loaded["sess-1"]["compaction_token_cursor"], cursor);

        let stale_cursor = json!({"initialized": true, "offset": 499});
        assert!(persist_compaction_cursor_for_offset(
            &path,
            &mut state,
            "sess-1",
            499,
            &stale_cursor,
        )
        .is_err());
        assert_eq!(load_state(&path), loaded);
    }

    #[test]
    fn test_commit_export_preserves_extensions_and_unions_children() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("commit.json");
        let mut state = json!({
            "sess-1": {
                "custom_extension": {"keep": true},
                "merged_subagent_ids": ["child-old"],
                "merged_subagent_invocation_ids": ["inv-old"],
                "current_trace_context": {"trace_id": "trace-current", "traceparent": "tp"},
                "trace_context_history": [{"trace_id": "trace-old", "traceparent": "old-tp"}],
                "vcs": {
                    "remote_url": "ssh://example/repo.git",
                    "branch": "main",
                    "cwd": "/repo",
                    "remote_name": "upstream",
                    "selected_remote_match": "fallback"
                }
            }
        });

        commit_export_success(
            &path,
            &mut state,
            "sess-1",
            42,
            "",
            &[],
            &["turn-1".to_string()],
            1,
            "2026-07-10T00:00:00Z",
            Some("/tmp/transcript.jsonl"),
            &["child-old".to_string(), "child-new".to_string()],
            &["inv-old".to_string(), "inv-new".to_string()],
        )
        .unwrap();

        assert_eq!(state["sess-1"]["custom_extension"]["keep"], true);
        assert_eq!(
            state["sess-1"]["merged_subagent_ids"],
            json!(["child-old", "child-new"])
        );
        assert_eq!(
            state["sess-1"]["merged_subagent_invocation_ids"],
            json!(["inv-old", "inv-new"])
        );
        assert_eq!(state["sess-1"]["parent_stopped"], true);
        assert!(state["sess-1"].get("current_trace_context").is_none());
        assert!(state["sess-1"].get("trace_context_history").is_none());
        assert_eq!(state["sess-1"]["vcs"]["remote_name"], "upstream");
        assert_eq!(load_state(&path), state);
    }

    #[test]
    fn test_failed_save_does_not_install_candidate_state() {
        let dir = tempfile::tempdir().unwrap();
        let blocking_parent = dir.path().join("not-a-directory");
        std::fs::write(&blocking_parent, "file").unwrap();
        let path = blocking_parent.join("state.json");
        let original = json!({"sess-1": {"turn_count": 0, "custom": true}});
        let mut state = original.clone();

        let result = commit_export_success(
            &path,
            &mut state,
            "sess-1",
            42,
            "",
            &[],
            &["turn-1".to_string()],
            1,
            "2026-07-10T00:00:00Z",
            Some("/tmp/transcript.jsonl"),
            &["child-1".to_string()],
            &["inv-1".to_string()],
        );

        assert!(result.is_err());
        assert_eq!(state, original);
    }

    #[test]
    fn test_pending_reason_is_non_committing_and_cleared_on_success() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("pending.json");
        let mut state = json!({});
        record_pending_export(
            &path,
            &mut state,
            "sess-1",
            "subagent_transcript_not_ready",
            &["child-1".to_string()],
            "2026-07-10T00:00:00Z",
            "/tmp/transcript.jsonl",
        )
        .unwrap();
        assert_eq!(state["sess-1"]["turn_count"], Value::Null);
        assert_eq!(
            state["sess-1"]["pending_export_reason"],
            "subagent_transcript_not_ready"
        );

        commit_export_success(
            &path,
            &mut state,
            "sess-1",
            42,
            "",
            &[],
            &["turn-1".to_string()],
            1,
            "2026-07-10T00:00:01Z",
            Some("/tmp/transcript.jsonl"),
            &["child-1".to_string()],
            &["inv-1".to_string()],
        )
        .unwrap();
        assert!(state["sess-1"].get("pending_export_reason").is_none());
        assert!(state["sess-1"].get("pending_subagent_ids").is_none());
    }

    #[test]
    fn test_terminal_quarantine_is_separate_from_success_accounting() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("quarantine.json");
        let mut state = json!({
            "sess-1": {
                "offset": 123,
                "buffer": "tail",
                "pending_messages": [{"type": "pending"}],
                "posted_turn_ids": ["turn-ok"],
                "turn_count": 1,
                "pending_export_reason": PARTIAL_EXPORT_REASON,
                "pending_subagent_ids": ["child-1"],
                "pending_final_export": true,
                "pending_window_end_offset": 456
            }
        });

        let transition = record_terminal_quarantined_turn(
            &path,
            &mut state,
            "sess-1",
            "turn-bad",
            "multiple_published_roots",
            &[
                "trace-a/root-a".to_string(),
                "trace-b/root-b".to_string(),
                "trace-a/root-a".to_string(),
            ],
            "2026-08-17T00:00:00Z",
            2,
            Some("turn-bad:roots"),
            Some(456),
        )
        .unwrap();

        assert!(transition.appended);
        assert_eq!(transition.terminal_high_water_offset, Some(456));
        assert_eq!(state["sess-1"]["posted_turn_ids"], json!(["turn-ok"]));
        assert_eq!(state["sess-1"]["turn_count"], 1);
        assert_eq!(state["sess-1"]["offset"], 123);
        assert!(is_turn_terminally_accounted(&state["sess-1"], "turn-bad"));
        assert_eq!(terminal_quarantine_count(&state["sess-1"]), 1);
        assert_eq!(
            state["sess-1"]["pending_messages"],
            json!([{"type": "pending"}])
        );
        assert!(state["sess-1"].get("pending_export_reason").is_none());
        assert!(state["sess-1"].get("pending_subagent_ids").is_none());
        assert!(state["sess-1"].get("pending_final_export").is_none());
        assert!(state["sess-1"].get("pending_window_end_offset").is_none());

        let quarantine = state["sess-1"]["quarantined_turns"].as_array().unwrap();
        assert_eq!(quarantine.len(), 1);
        assert_eq!(quarantine[0]["turn_id"], "turn-bad");
        assert_eq!(quarantine[0]["reason"], "multiple_published_roots");
        assert_eq!(
            quarantine[0]["original_root_identities"],
            json!(["trace-a/root-a", "trace-b/root-b"])
        );
        assert_eq!(quarantine[0]["attempt"], 2);
        assert_eq!(quarantine[0]["state_version"], QUARANTINE_STATE_VERSION);
        assert_eq!(
            state["sess-1"]["export_accounting"],
            json!({
                "state_version": EXPORT_ACCOUNTING_STATE_VERSION,
                "terminal_turn_ids": ["turn-bad"],
                "updated": "2026-08-17T00:00:00Z",
                "terminal_high_water_offset": 456
            })
        );
        assert_eq!(load_state(&path), state);
    }

    #[test]
    fn test_terminal_quarantine_is_idempotent_by_turn_id_and_recovery_key() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("quarantine.json");
        let mut state = json!({});

        let first = record_terminal_quarantined_turn(
            &path,
            &mut state,
            "sess-1",
            "turn-bad",
            "multiple_published_roots",
            &["trace-a/root-a".to_string()],
            "2026-08-17T00:00:00Z",
            1,
            Some("stable-key"),
            Some(100),
        )
        .unwrap();
        let duplicate_turn = record_terminal_quarantined_turn(
            &path,
            &mut state,
            "sess-1",
            "turn-bad",
            "multiple_published_roots",
            &["trace-b/root-b".to_string()],
            "2026-08-17T00:00:01Z",
            2,
            Some("other-key"),
            Some(110),
        )
        .unwrap();
        let duplicate_key = record_terminal_quarantined_turn(
            &path,
            &mut state,
            "sess-1",
            "turn-later-name",
            "multiple_published_roots",
            &["trace-c/root-c".to_string()],
            "2026-08-17T00:00:02Z",
            3,
            Some("stable-key"),
            Some(90),
        )
        .unwrap();

        assert!(first.appended);
        assert!(!duplicate_turn.appended);
        assert!(!duplicate_key.appended);
        assert_eq!(
            state["sess-1"]["quarantined_turns"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            state["sess-1"]["export_accounting"]["terminal_turn_ids"],
            json!(["turn-bad"])
        );
        assert_eq!(
            state["sess-1"]["export_accounting"]["terminal_high_water_offset"],
            110
        );
        assert!(state["sess-1"].get("posted_turn_ids").is_none());
        assert!(state["sess-1"].get("turn_count").is_none());
    }

    #[test]
    fn test_terminal_quarantine_lazily_initializes_legacy_state() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("quarantine.json");
        let mut state = json!({
            "sess-1": {
                "posted_turn_ids": ["turn-ok"],
                "turn_count": 1,
                "custom": true
            }
        });

        record_terminal_quarantined_turn(
            &path,
            &mut state,
            "sess-1",
            "turn-bad",
            "multiple_published_roots",
            &[],
            "2026-08-17T00:00:00Z",
            1,
            None,
            None,
        )
        .unwrap();

        assert_eq!(state["sess-1"]["custom"], true);
        assert_eq!(state["sess-1"]["posted_turn_ids"], json!(["turn-ok"]));
        assert_eq!(
            state["sess-1"]["quarantined_turns"],
            json!([{
                "turn_id": "turn-bad",
                "reason": "multiple_published_roots",
                "original_root_identities": [],
                "timestamp": "2026-08-17T00:00:00Z",
                "attempt": 1,
                "state_version": QUARANTINE_STATE_VERSION
            }])
        );
        assert_eq!(
            state["sess-1"]["export_accounting"],
            json!({
                "state_version": EXPORT_ACCOUNTING_STATE_VERSION,
                "terminal_turn_ids": ["turn-bad"],
                "updated": "2026-08-17T00:00:00Z"
            })
        );
    }

    #[test]
    fn test_terminal_export_accounting_cursor_is_monotonic() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("quarantine.json");
        let mut state = json!({});

        record_terminal_quarantined_turn(
            &path,
            &mut state,
            "sess-1",
            "turn-2",
            "multiple_published_roots",
            &[],
            "2026-08-17T00:00:00Z",
            1,
            None,
            Some(200),
        )
        .unwrap();
        record_terminal_quarantined_turn(
            &path,
            &mut state,
            "sess-1",
            "turn-1",
            "multiple_published_roots",
            &[],
            "2026-08-17T00:00:01Z",
            1,
            None,
            Some(100),
        )
        .unwrap();
        record_terminal_quarantined_turn(
            &path,
            &mut state,
            "sess-1",
            "turn-3",
            "multiple_published_roots",
            &[],
            "2026-08-17T00:00:02Z",
            1,
            None,
            Some(300),
        )
        .unwrap();

        assert_eq!(
            state["sess-1"]["export_accounting"]["terminal_turn_ids"],
            json!(["turn-2", "turn-1", "turn-3"])
        );
        assert_eq!(
            state["sess-1"]["export_accounting"]["terminal_high_water_offset"],
            300
        );
        assert!(state["sess-1"].get("posted_turn_ids").is_none());
    }

    #[test]
    fn durable_history_accounting_keeps_success_ordinals_separate_from_terminal_turns() {
        let session = json!({
            "turn_count": 2,
            "posted_turn_ids": ["turn-2"],
            "export_accounting": {
                "terminal_turn_ids": ["turn-bad"]
            }
        });

        let accounted = durably_accounted_turn_ids_for_history(
            &session,
            ["turn-bad", "turn-1", "turn-2", "turn-3"],
        );

        assert_eq!(
            accounted,
            ["turn-bad", "turn-1", "turn-2"]
                .into_iter()
                .map(str::to_string)
                .collect()
        );
    }

    #[test]
    fn legacy_finalizer_recovery_arms_once_and_terminal_commit_clears_marker() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("legacy-recovery.json");
        let transcript_path = "/tmp/session.jsonl";
        let mut state = json!({
            "sess-1": {
                "pending_export_reason": REALTIME_TOPOLOGY_AMBIGUOUS_REASON,
                "transcript_path": transcript_path
            }
        });

        let first = arm_legacy_finalizer_recovery(
            &path,
            &mut state,
            "sess-1",
            "2026-08-22T00:00:00Z",
            transcript_path,
        )
        .unwrap()
        .unwrap();
        let second = arm_legacy_finalizer_recovery(
            &path,
            &mut state,
            "sess-1",
            "2026-08-22T00:00:01Z",
            transcript_path,
        )
        .unwrap()
        .unwrap();

        assert!(first.armed);
        assert!(!second.armed);
        assert_eq!(first.recovery_key, second.recovery_key);
        assert_eq!(second.attempt, 1);
        assert_eq!(
            state["sess-1"]["legacy_finalizer_recovery"]["state_version"],
            json!(LEGACY_FINALIZER_RECOVERY_STATE_VERSION)
        );
        assert_eq!(
            state["sess-1"]["legacy_finalizer_recovery"]["status"],
            json!("armed")
        );

        record_terminal_quarantined_turn(
            &path,
            &mut state,
            "sess-1",
            "turn-bad",
            "multiple_realtime_matches",
            &["root-a".to_string(), "root-b".to_string()],
            "2026-08-22T00:00:02Z",
            1,
            Some(&first.recovery_key),
            Some(123),
        )
        .unwrap();

        assert!(state["sess-1"].get("pending_export_reason").is_none());
        assert!(state["sess-1"].get("legacy_finalizer_recovery").is_none());
    }

    #[test]
    fn retryable_legacy_finalizer_recovery_transitions_to_awaiting_explicit_terminal() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("legacy-recovery-await.json");
        let transcript_path = "/tmp/session.jsonl";
        let mut state = json!({
            "sess-1": {
                "pending_export_reason": REALTIME_TOPOLOGY_AMBIGUOUS_REASON,
                "transcript_path": transcript_path
            }
        });
        let armed = arm_legacy_finalizer_recovery(
            &path,
            &mut state,
            "sess-1",
            "2026-08-22T00:00:00Z",
            transcript_path,
        )
        .unwrap()
        .unwrap();

        record_pending_export_awaiting_explicit_terminal(
            &path,
            &mut state,
            "sess-1",
            "2026-08-22T00:00:01Z",
            transcript_path,
            &armed.recovery_key,
            armed.attempt,
        )
        .unwrap();
        let resumed = arm_legacy_finalizer_recovery(
            &path,
            &mut state,
            "sess-1",
            "2026-08-22T00:00:02Z",
            transcript_path,
        )
        .unwrap()
        .unwrap();

        assert_eq!(resumed.status, "awaiting_explicit_terminal");
        assert_eq!(resumed.attempt, 1);
        assert_eq!(
            state["sess-1"]["pending_export_reason"],
            json!(REALTIME_TOPOLOGY_AMBIGUOUS_REASON)
        );
        assert_eq!(
            state["sess-1"]["legacy_finalizer_recovery"]["status"],
            json!("awaiting_explicit_terminal")
        );
    }

    #[test]
    fn explicit_terminal_recovery_claim_increments_after_awaiting_terminal() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("finalizer-attempt.json");
        let transcript_path = "/tmp/session.jsonl";
        let recovery_key = finalizer_recovery_key(transcript_path, "turn-1", 123);
        let mut state = json!({});

        let first = claim_finalizer_recovery_attempt(
            &path,
            &mut state,
            "sess-1",
            "2026-08-22T00:00:00Z",
            transcript_path,
            &recovery_key,
        )
        .unwrap();
        let crash_resume = claim_finalizer_recovery_attempt(
            &path,
            &mut state,
            "sess-1",
            "2026-08-22T00:00:01Z",
            transcript_path,
            &recovery_key,
        )
        .unwrap();

        assert_eq!(first.attempt, 1);
        assert!(first.armed);
        assert_eq!(crash_resume.attempt, 1);
        assert!(!crash_resume.armed);

        record_pending_export_awaiting_explicit_terminal(
            &path,
            &mut state,
            "sess-1",
            "2026-08-22T00:00:02Z",
            transcript_path,
            &recovery_key,
            crash_resume.attempt,
        )
        .unwrap();
        let legacy_arm = arm_legacy_finalizer_recovery(
            &path,
            &mut state,
            "sess-1",
            "2026-08-22T00:00:03Z",
            transcript_path,
        )
        .unwrap()
        .unwrap();

        assert_eq!(legacy_arm.recovery_key, recovery_key);
        assert_eq!(legacy_arm.attempt, 1);
        assert_eq!(legacy_arm.status, "awaiting_explicit_terminal");

        let second = claim_finalizer_recovery_attempt(
            &path,
            &mut state,
            "sess-1",
            "2026-08-22T00:00:04Z",
            transcript_path,
            &recovery_key,
        )
        .unwrap();
        let second_crash_resume = claim_finalizer_recovery_attempt(
            &path,
            &mut state,
            "sess-1",
            "2026-08-22T00:00:05Z",
            transcript_path,
            &recovery_key,
        )
        .unwrap();

        assert_eq!(second.attempt, 2);
        assert!(second.armed);
        assert_eq!(second_crash_resume.attempt, 2);
        assert!(!second_crash_resume.armed);
        assert_eq!(
            state["sess-1"]["legacy_finalizer_recovery"]["attempt"],
            json!(2)
        );
    }

    #[test]
    fn retryable_pending_reason_and_recovery_marker_commit_together() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("legacy-recovery-atomic.json");
        let transcript_path = "/tmp/session.jsonl";
        let mut state = json!({});

        record_pending_export_awaiting_explicit_terminal(
            &path,
            &mut state,
            "sess-1",
            "2026-08-22T00:00:01Z",
            transcript_path,
            "recovery-key-1",
            3,
        )
        .unwrap();

        let persisted: Value =
            serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(
            persisted["sess-1"]["pending_export_reason"],
            json!(REALTIME_TOPOLOGY_AMBIGUOUS_REASON)
        );
        assert_eq!(
            persisted["sess-1"]["legacy_finalizer_recovery"],
            json!({
                "state_version": LEGACY_FINALIZER_RECOVERY_STATE_VERSION,
                "recovery_key": "recovery-key-1",
                "attempt": 3,
                "status": "awaiting_explicit_terminal",
                "updated": "2026-08-22T00:00:01Z",
                "transcript_path": transcript_path,
            })
        );
        assert_eq!(persisted, state);
    }

    #[test]
    fn test_turn_checkpoint_preserves_cursor_and_marks_partial() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("checkpoint.json");
        let mut state = json!({
            "sess-1": {
                "offset": 123,
                "buffer": "tail",
                "pending_messages": [{"type": "pending"}],
                "posted_turn_ids": ["turn-old"],
                "turn_count": 7,
                "merged_subagent_ids": ["child-old"],
                "merged_subagent_invocation_ids": ["inv-old"],
                "parent_stopped": true,
                "custom_extension": {"keep": true}
            }
        });

        let count = checkpoint_turn_export_success(
            &path,
            &mut state,
            "sess-1",
            "turn-new",
            "2026-07-13T00:00:00Z",
            Some("/tmp/transcript.jsonl"),
            &["child-old".to_string(), "child-new".to_string()],
            &["inv-old".to_string(), "inv-new".to_string()],
        )
        .unwrap();

        assert_eq!(count, 8);
        assert_eq!(state["sess-1"]["offset"], 123);
        assert_eq!(state["sess-1"]["buffer"], "tail");
        assert_eq!(
            state["sess-1"]["pending_messages"],
            json!([{"type": "pending"}])
        );
        assert_eq!(
            state["sess-1"]["posted_turn_ids"],
            json!(["turn-old", "turn-new"])
        );
        assert_eq!(state["sess-1"]["turn_count"], 8);
        assert_eq!(
            state["sess-1"]["merged_subagent_ids"],
            json!(["child-old", "child-new"])
        );
        assert_eq!(
            state["sess-1"]["merged_subagent_invocation_ids"],
            json!(["inv-old", "inv-new"])
        );
        assert_eq!(state["sess-1"]["parent_stopped"], false);
        assert_eq!(
            state["sess-1"]["pending_export_reason"],
            PARTIAL_EXPORT_REASON
        );
        assert_eq!(state["sess-1"]["custom_extension"]["keep"], true);
        assert_eq!(load_state(&path), state);
    }

    #[test]
    fn test_turn_checkpoint_is_idempotent_by_turn_id() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("checkpoint.json");
        let mut state = json!({});

        for _ in 0..2 {
            checkpoint_turn_export_success(
                &path,
                &mut state,
                "sess-1",
                "turn-1",
                "2026-07-13T00:00:00Z",
                Some("/tmp/transcript.jsonl"),
                &[],
                &[],
            )
            .unwrap();
        }

        assert_eq!(state["sess-1"]["posted_turn_ids"], json!(["turn-1"]));
        assert_eq!(state["sess-1"]["turn_count"], 1);
    }

    #[test]
    fn test_legacy_state_defaults_invocation_merge_history_to_empty() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("checkpoint.json");
        let mut state = json!({
            "sess-1": {
                "posted_turn_ids": ["turn-1"],
                "turn_count": 1,
                "merged_subagent_ids": ["child-old"]
            }
        });

        let count = checkpoint_turn_export_attempt(
            &path,
            &mut state,
            "sess-1",
            "turn-1",
            "2026-07-13T00:00:00Z",
            Some("/tmp/transcript.jsonl"),
            &["child-old".to_string(), "child-new".to_string()],
            &["inv-new".to_string()],
        )
        .unwrap();

        assert_eq!(count, 1);
        assert_eq!(state["sess-1"]["posted_turn_ids"], json!(["turn-1"]));
        assert_eq!(state["sess-1"]["turn_count"], 1);
        assert_eq!(
            state["sess-1"]["merged_subagent_ids"],
            json!(["child-old", "child-new"])
        );
        assert_eq!(
            state["sess-1"]["merged_subagent_invocation_ids"],
            json!(["inv-new"])
        );
        assert_eq!(load_state(&path), state);
    }

    #[test]
    fn test_failed_checkpoint_save_does_not_install_candidate_state() {
        let dir = tempfile::tempdir().unwrap();
        let blocking_parent = dir.path().join("not-a-directory");
        std::fs::write(&blocking_parent, "file").unwrap();
        let path = blocking_parent.join("state.json");
        let original = json!({"sess-1": {"turn_count": 0, "custom": true}});
        let mut state = original.clone();

        let result = checkpoint_turn_export_success(
            &path,
            &mut state,
            "sess-1",
            "turn-1",
            "2026-07-13T00:00:00Z",
            Some("/tmp/transcript.jsonl"),
            &[],
            &[],
        );

        assert!(result.is_err());
        assert_eq!(state, original);
    }

    #[test]
    fn test_posted_ids_prune_only_on_final_commit() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("checkpoint.json");
        let ids: Vec<String> = (0..POSTED_TURN_ID_RETENTION)
            .map(|index| format!("turn-{index}"))
            .collect();
        let mut state = json!({
            "sess-1": {
                "offset": 0,
                "buffer": "",
                "pending_messages": [],
                "posted_turn_ids": ids,
                "turn_count": POSTED_TURN_ID_RETENTION
            }
        });

        checkpoint_turn_export_success(
            &path,
            &mut state,
            "sess-1",
            "turn-extra",
            "2026-07-13T00:00:00Z",
            Some("/tmp/transcript.jsonl"),
            &[],
            &[],
        )
        .unwrap();
        assert_eq!(
            state["sess-1"]["posted_turn_ids"].as_array().unwrap().len(),
            POSTED_TURN_ID_RETENTION + 1
        );

        let posted = get_string_array_field(&state["sess-1"], "posted_turn_ids");
        commit_export_success(
            &path,
            &mut state,
            "sess-1",
            999,
            "",
            &[],
            &posted,
            POSTED_TURN_ID_RETENTION + 1,
            "2026-07-13T00:00:01Z",
            Some("/tmp/transcript.jsonl"),
            &[],
            &[],
        )
        .unwrap();

        let final_ids = state["sess-1"]["posted_turn_ids"].as_array().unwrap();
        assert_eq!(final_ids.len(), POSTED_TURN_ID_RETENTION);
        assert_eq!(final_ids.last().unwrap(), "turn-extra");
        assert_eq!(state["sess-1"]["offset"], 999);
        assert!(state["sess-1"].get("pending_export_reason").is_none());
    }

    #[test]
    fn test_finalization_failure_keeps_durable_partial_checkpoint_retryable() {
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        std::fs::create_dir(&state_dir).unwrap();
        let state_path = state_dir.join("state.json");
        let mut state = json!({});
        checkpoint_turn_export_success(
            &state_path,
            &mut state,
            "sess-1",
            "turn-1",
            "2026-07-13T00:00:00Z",
            Some("/tmp/transcript.jsonl"),
            &[],
            &[],
        )
        .unwrap();

        let backup_dir = dir.path().join("state-backup");
        std::fs::rename(&state_dir, &backup_dir).unwrap();
        std::fs::write(&state_dir, "blocks directory recreation").unwrap();
        let posted = get_string_array_field(&state["sess-1"], "posted_turn_ids");
        let result = commit_export_success(
            &state_path,
            &mut state,
            "sess-1",
            999,
            "",
            &[],
            &posted,
            1,
            "2026-07-13T00:00:01Z",
            Some("/tmp/transcript.jsonl"),
            &[],
            &[],
        );

        assert!(result.is_err());
        assert_eq!(state["sess-1"]["offset"], 0);
        assert_eq!(
            state["sess-1"]["pending_export_reason"],
            PARTIAL_EXPORT_REASON
        );
        let durable_partial = load_state(&backup_dir.join("state.json"));
        assert_eq!(
            durable_partial["sess-1"]["posted_turn_ids"],
            json!(["turn-1"])
        );
        assert_eq!(durable_partial["sess-1"]["offset"], 0);
    }
}
