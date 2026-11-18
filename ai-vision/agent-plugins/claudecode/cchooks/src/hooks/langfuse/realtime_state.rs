use std::collections::HashMap;
use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::common::transcript::TranscriptSourceEpoch;
use crate::hooks::RetryPolicy;

pub const CHILD_DELIVERY_SCHEMA_VERSION: u64 = 1;

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeContext {
    pub context_id: String,
    pub trace_id: String,
    pub root_span_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub traceparent: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sofa_traceid: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sofa_rpcid: Option<String>,
    #[serde(default)]
    pub tool_use_ids: Vec<String>,
    #[serde(default)]
    pub tool_span_ids: HashMap<String, String>,
    #[serde(default)]
    pub emitted_tool_use_ids: Vec<String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub failed_tool_exports: HashMap<String, FailedToolExport>,
    #[serde(default)]
    pub generation_span_ids: HashMap<String, String>,
    #[serde(default)]
    pub accounted_generation_ids: Vec<String>,
    pub created_at: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FailedToolExportStatus {
    #[default]
    Failed,
    Inflight,
    NonRetryable,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct FailedToolExport {
    pub body: Value,
    #[serde(default)]
    pub status: FailedToolExportStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub claim_id: Option<String>,
    #[serde(default)]
    pub lease_until_ms: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClaimedFailedToolExport {
    pub context_id: String,
    pub tool_use_id: String,
    pub body: Value,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FailedToolExportClaim {
    pub claim_id: String,
    pub exports: Vec<ClaimedFailedToolExport>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GenerationRoundStatus {
    #[default]
    Pending,
    Inflight,
    Accounted,
    Deferred,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct GenerationRound {
    pub message_id: String,
    pub span_id: String,
    pub context_id: String,
    pub ordinal: u64,
    #[serde(default)]
    pub correlated_tool_use_ids: Vec<String>,
    #[serde(default)]
    pub status: GenerationRoundStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub claim_id: Option<String>,
    #[serde(default)]
    pub lease_until_ms: u64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeGenerationState {
    #[serde(default)]
    pub scan_anchor: i64,
    #[serde(default)]
    pub committed_offset: i64,
    #[serde(default)]
    pub discovery_offset: i64,
    #[serde(default)]
    pub observed_file_len: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_epoch: Option<TranscriptSourceEpoch>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scan_terminal_reason: Option<String>,
    pub batch_size: usize,
    #[serde(default)]
    pub rounds: Vec<GenerationRound>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GenerationCandidate {
    pub message_id: String,
    pub context_id: String,
    pub correlated_tool_use_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClaimedGeneration {
    pub message_id: String,
    pub span_id: String,
    pub context_id: String,
    pub trace_id: String,
    pub root_span_id: String,
    pub ordinal: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GenerationClaim {
    pub claim_id: String,
    pub batch_size: usize,
    pub generations: Vec<ClaimedGeneration>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChildDeliveryStatus {
    Pending,
    Claimed,
    Accepted,
    Accounted,
    FailedRetryable,
    PolicyTerminal,
    Compensated,
    TerminalQuarantined,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChildDeliveryRecord {
    pub schema_version: u64,
    pub child_key: String,
    pub kind: String,
    pub context_id: String,
    pub turn_evidence: Value,
    pub trace_id: String,
    pub root_span_id: String,
    pub span_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub end_time_unix_nano: Option<String>,
    pub retry_policy: String,
    pub sequence: u64,
    pub high_water: u64,
    pub transport_unit_id: String,
    pub payload_digest: String,
    pub status: ChildDeliveryStatus,
    pub attempt_count: u64,
    pub first_attempt_at: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub accepted_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub terminal_reason: Option<String>,
}

pub struct ChildDeliverySpec<'a> {
    pub child_key: &'a str,
    pub kind: &'a str,
    pub context_id: &'a str,
    pub turn_evidence: Value,
    pub trace_id: &'a str,
    pub root_span_id: &'a str,
    pub span_id: &'a str,
    pub end_time_unix_nano: Option<String>,
}

fn child_delivery_status_is_terminal(status: &ChildDeliveryStatus) -> bool {
    matches!(
        status,
        ChildDeliveryStatus::Accepted
            | ChildDeliveryStatus::Accounted
            | ChildDeliveryStatus::PolicyTerminal
            | ChildDeliveryStatus::Compensated
            | ChildDeliveryStatus::TerminalQuarantined
    )
}

pub fn child_delivery_is_terminal(state: &Value, session_id: &str, child_key: &str) -> bool {
    session_realtime(state, session_id)
        .get("child_ledger")
        .and_then(|ledger| ledger.get(child_key))
        .and_then(|value| serde_json::from_value::<ChildDeliveryRecord>(value.clone()).ok())
        .is_some_and(|record| child_delivery_status_is_terminal(&record.status))
}

pub fn otlp_payload_digest(body: &Value) -> Result<String, String> {
    let bytes = serde_json::to_vec(body).map_err(|error| error.to_string())?;
    let mut digest = Sha256::new();
    digest.update(bytes);
    Ok(digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

pub fn otlp_span_end_time_unix_nano(body: &Value, span_id: &str) -> Option<String> {
    for resource_span in body.get("resourceSpans")?.as_array()? {
        for scope_span in resource_span.get("scopeSpans")?.as_array()? {
            for span in scope_span.get("spans")?.as_array()? {
                if span.get("spanId").and_then(Value::as_str) == Some(span_id) {
                    return span
                        .get("endTimeUnixNano")
                        .and_then(Value::as_str)
                        .map(str::to_string);
                }
            }
        }
    }
    None
}

pub fn accounted_child_max_end_times(state: &Value, session_id: &str) -> HashMap<String, String> {
    let realtime = session_realtime(state, session_id);
    let Some(ledger) = realtime.get("child_ledger").and_then(Value::as_object) else {
        return HashMap::new();
    };
    let mut max_by_context = HashMap::new();
    for value in ledger.values() {
        let Ok(record) = serde_json::from_value::<ChildDeliveryRecord>(value.clone()) else {
            continue;
        };
        if record.status != ChildDeliveryStatus::Accounted {
            continue;
        }
        let Some(end_time) = record.end_time_unix_nano else {
            continue;
        };
        let Ok(end_nanos) = end_time.parse::<u128>() else {
            continue;
        };
        let entry = max_by_context
            .entry(record.context_id)
            .or_insert_with(|| end_time.clone());
        if entry
            .parse::<u128>()
            .is_ok_and(|current| end_nanos > current)
        {
            *entry = end_time;
        }
    }
    max_by_context
}

pub fn prepare_child_delivery(
    state: &mut Value,
    session_id: &str,
    spec: ChildDeliverySpec<'_>,
    retry_policy: RetryPolicy,
    transport_unit_id: &str,
    payload_digest: &str,
    now: &str,
) -> Result<bool, String> {
    let realtime = ensure_realtime_object(state, session_id)?;
    let next_sequence = realtime
        .get("next_child_sequence")
        .and_then(Value::as_u64)
        .unwrap_or(1)
        .max(1);
    let ledger = realtime
        .entry("child_ledger".to_string())
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or("realtime child_ledger is not an object")?;
    let existing = ledger
        .get(spec.child_key)
        .and_then(|value| serde_json::from_value::<ChildDeliveryRecord>(value.clone()).ok());
    if existing
        .as_ref()
        .is_some_and(|record| child_delivery_status_is_terminal(&record.status))
    {
        return Ok(false);
    }
    let sequence = existing
        .as_ref()
        .map(|record| record.sequence)
        .unwrap_or(next_sequence);
    let attempt_count = existing
        .as_ref()
        .map(|record| record.attempt_count.saturating_add(1))
        .unwrap_or(1);
    let first_attempt_at = existing
        .as_ref()
        .map(|record| record.first_attempt_at.clone())
        .unwrap_or_else(|| now.to_string());
    let record = ChildDeliveryRecord {
        schema_version: CHILD_DELIVERY_SCHEMA_VERSION,
        child_key: spec.child_key.to_string(),
        kind: spec.kind.to_string(),
        context_id: spec.context_id.to_string(),
        turn_evidence: spec.turn_evidence,
        trace_id: spec.trace_id.to_string(),
        root_span_id: spec.root_span_id.to_string(),
        span_id: spec.span_id.to_string(),
        end_time_unix_nano: spec.end_time_unix_nano,
        retry_policy: retry_policy.as_str().to_string(),
        sequence,
        high_water: sequence,
        transport_unit_id: transport_unit_id.to_string(),
        payload_digest: payload_digest.to_string(),
        status: if retry_policy == RetryPolicy::AtMostOnce {
            ChildDeliveryStatus::PolicyTerminal
        } else {
            ChildDeliveryStatus::Claimed
        },
        attempt_count,
        first_attempt_at,
        accepted_at: None,
        last_error: None,
        terminal_reason: (retry_policy == RetryPolicy::AtMostOnce)
            .then(|| "checkpoint_before_post".to_string()),
    };
    ledger.insert(
        spec.child_key.to_string(),
        serde_json::to_value(record).map_err(|error| error.to_string())?,
    );
    if existing.is_none() {
        realtime.insert(
            "next_child_sequence".to_string(),
            json!(next_sequence.saturating_add(1)),
        );
    }
    Ok(true)
}

pub fn finish_child_deliveries(
    state: &mut Value,
    session_id: &str,
    child_keys: &[String],
    retry_policy: RetryPolicy,
    accepted: bool,
    now: &str,
    error: Option<&str>,
) -> Result<(), String> {
    let realtime = ensure_realtime_object(state, session_id)?;
    let Some(ledger) = realtime
        .get_mut("child_ledger")
        .and_then(Value::as_object_mut)
    else {
        return Ok(());
    };
    for child_key in child_keys {
        let Some(value) = ledger.get_mut(child_key) else {
            continue;
        };
        let Some(mut record) = serde_json::from_value::<ChildDeliveryRecord>(value.clone()).ok()
        else {
            continue;
        };
        if accepted {
            record.status = ChildDeliveryStatus::Accounted;
            record.accepted_at = Some(now.to_string());
            record.last_error = None;
            if retry_policy == RetryPolicy::AtMostOnce {
                record.terminal_reason = Some("accepted_after_checkpoint".to_string());
            }
        } else {
            record.last_error = error.map(str::to_string);
            if retry_policy == RetryPolicy::AtMostOnce {
                record.status = ChildDeliveryStatus::PolicyTerminal;
                record.terminal_reason = Some("post_claim_transport_failure".to_string());
            } else {
                record.status = ChildDeliveryStatus::FailedRetryable;
            }
        }
        *value = serde_json::to_value(record).map_err(|error| error.to_string())?;
    }
    Ok(())
}

pub fn reclaim_child_deliveries(
    state: &mut Value,
    session_id: &str,
    child_keys: &[String],
    transport_unit_id: &str,
    payload_digest: &str,
) -> Result<(), String> {
    let realtime = ensure_realtime_object(state, session_id)?;
    let Some(ledger) = realtime
        .get_mut("child_ledger")
        .and_then(Value::as_object_mut)
    else {
        return Ok(());
    };
    for child_key in child_keys {
        let Some(value) = ledger.get_mut(child_key) else {
            continue;
        };
        let Some(mut record) = serde_json::from_value::<ChildDeliveryRecord>(value.clone()).ok()
        else {
            continue;
        };
        if record.status != ChildDeliveryStatus::FailedRetryable {
            continue;
        }
        record.status = ChildDeliveryStatus::Claimed;
        record.attempt_count = record.attempt_count.saturating_add(1);
        record.transport_unit_id = transport_unit_id.to_string();
        record.payload_digest = payload_digest.to_string();
        record.last_error = None;
        *value = serde_json::to_value(record).map_err(|error| error.to_string())?;
    }
    Ok(())
}

pub enum GenerationClaimUpdate<'a> {
    Account(&'a [String]),
    Release,
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

pub fn stable_tool_span_id(tool_use_id: &str) -> String {
    stable_hex("cchooks.realtime.tool-span.v1", tool_use_id, 8)
}

pub fn stable_generation_span_id(message_id: &str) -> String {
    stable_hex("cchooks.realtime.generation-span.v1", message_id, 8)
}

pub fn tool_child_key(context_id: &str, tool_use_id: &str) -> String {
    format!("tool:{context_id}:{tool_use_id}")
}

pub fn generation_child_key(context_id: &str, message_id: &str) -> String {
    format!("generation:{context_id}:{message_id}")
}

fn context_id(trace_id: &str, root_span_id: &str) -> String {
    stable_hex(
        "cchooks.realtime.context.v1",
        &format!("{trace_id}:{root_span_id}"),
        16,
    )
}

fn read_context(value: &Value) -> Option<RealtimeContext> {
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

fn update_realtime<F>(state: &mut Value, session_id: &str, update: F)
where
    F: FnOnce(&mut Value),
{
    if state.get(session_id).is_none() {
        state[session_id] = json!({});
    }
    if state[session_id].get("realtime_export").is_none() {
        state[session_id]["realtime_export"] = json!({});
    }
    update(&mut state[session_id]["realtime_export"]);
}

fn ensure_realtime_object<'a>(
    state: &'a mut Value,
    session_id: &str,
) -> Result<&'a mut serde_json::Map<String, Value>, String> {
    update_realtime(state, session_id, |_| {});
    state[session_id]["realtime_export"]
        .as_object_mut()
        .ok_or_else(|| "realtime_export is not an object".to_string())
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

pub fn rotate_current(
    _state_file: &Path,
    state: &mut Value,
    session_id: &str,
) -> Result<(), String> {
    let Some(current) = session_realtime(state, session_id)
        .get("current")
        .and_then(read_context)
    else {
        return Ok(());
    };
    if current.tool_use_ids.is_empty() {
        return Ok(());
    }
    let current_value = serde_json::to_value(&current)
        .map_err(|error| format!("serialize realtime context: {error}"))?;

    update_realtime(state, session_id, |realtime| {
        let pending = realtime.get_mut("pending").and_then(Value::as_array_mut);
        if let Some(pending) = pending {
            if !pending.iter().any(|item| {
                item.get("context_id").and_then(Value::as_str) == Some(current.context_id.as_str())
            }) {
                pending.push(current_value);
            }
        } else {
            realtime["pending"] = Value::Array(vec![current_value]);
        }
        realtime
            .as_object_mut()
            .map(|object| object.remove("current"));
    });
    Ok(())
}

#[cfg(test)]
pub fn claim_tool(
    _state_file: &Path,
    state: &mut Value,
    session_id: &str,
    tool_use_id: &str,
    now: &str,
    upstream_traceparent: Option<&str>,
) -> Result<RealtimeContext, String> {
    claim_tool_with_trace_snapshot(
        _state_file,
        state,
        session_id,
        tool_use_id,
        now,
        upstream_traceparent,
        None,
        None,
    )
}

#[allow(clippy::too_many_arguments)]
pub fn claim_tool_with_trace_snapshot(
    _state_file: &Path,
    state: &mut Value,
    session_id: &str,
    tool_use_id: &str,
    now: &str,
    upstream_traceparent: Option<&str>,
    sofa_traceid: Option<&str>,
    sofa_rpcid: Option<&str>,
) -> Result<RealtimeContext, String> {
    if let Some(mut current) = session_realtime(state, session_id)
        .get("current")
        .and_then(read_context)
    {
        let mut changed = false;
        if !current.tool_use_ids.iter().any(|id| id == tool_use_id) {
            current.tool_use_ids.push(tool_use_id.to_string());
            changed = true;
        }
        if !current.tool_span_ids.contains_key(tool_use_id) {
            current
                .tool_span_ids
                .insert(tool_use_id.to_string(), stable_tool_span_id(tool_use_id));
            changed = true;
        }
        if current.traceparent.is_none() && upstream_traceparent.is_some() {
            current.traceparent = upstream_traceparent.map(str::to_string);
            changed = true;
        }
        if current.sofa_traceid.is_none() && sofa_traceid.is_some() {
            current.sofa_traceid = sofa_traceid.map(str::to_string);
            changed = true;
        }
        if current.sofa_rpcid.is_none() && sofa_rpcid.is_some() {
            current.sofa_rpcid = sofa_rpcid.map(str::to_string);
            changed = true;
        }
        if changed {
            let current_value = serde_json::to_value(&current)
                .map_err(|error| format!("serialize realtime context: {error}"))?;
            update_realtime(state, session_id, |realtime| {
                realtime["current"] = current_value;
            });
        }
        return Ok(current);
    }

    let trace_id = upstream_traceparent
        .and_then(agentic_otel_common::parse_traceparent)
        .map(|parent| parent.trace_id)
        .unwrap_or_else(agentic_otel_common::otel_trace_id);
    let root_span_id = agentic_otel_common::otel_span_id();
    let context = RealtimeContext {
        context_id: context_id(&trace_id, &root_span_id),
        trace_id,
        root_span_id,
        traceparent: upstream_traceparent.map(str::to_string),
        sofa_traceid: sofa_traceid.map(str::to_string),
        sofa_rpcid: sofa_rpcid.map(str::to_string),
        tool_use_ids: vec![tool_use_id.to_string()],
        tool_span_ids: HashMap::from([(tool_use_id.to_string(), stable_tool_span_id(tool_use_id))]),
        emitted_tool_use_ids: Vec::new(),
        failed_tool_exports: HashMap::new(),
        generation_span_ids: HashMap::new(),
        accounted_generation_ids: Vec::new(),
        created_at: now.to_string(),
    };
    let context_value = serde_json::to_value(&context)
        .map_err(|error| format!("serialize realtime context: {error}"))?;
    update_realtime(state, session_id, |realtime| {
        realtime["current"] = context_value;
        if realtime.get("pending").is_none() {
            realtime["pending"] = json!([]);
        }
    });
    Ok(context)
}

pub fn claim_failed_tool_exports(
    _state_file: &Path,
    state: &mut Value,
    session_id: &str,
    now_ms: u64,
    lease_ms: u64,
) -> Result<Option<FailedToolExportClaim>, String> {
    let has_claimable = contexts(state, session_id).iter().any(|context| {
        context.failed_tool_exports.values().any(|export| {
            export.status == FailedToolExportStatus::Failed
                || (export.status == FailedToolExportStatus::Inflight
                    && export.lease_until_ms <= now_ms)
        })
    });
    if !has_claimable {
        return Ok(None);
    }

    let claim_id = agentic_otel_common::otel_span_id();
    let lease_until_ms = now_ms.saturating_add(lease_ms);
    let mut exports = Vec::new();
    update_realtime(state, session_id, |realtime| {
        for_each_context_object_mut(realtime, |context| {
            let context_id = context
                .get("context_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let Some(failed) = context
                .get_mut("failed_tool_exports")
                .and_then(Value::as_object_mut)
            else {
                return;
            };
            for (tool_use_id, value) in failed {
                let Ok(mut export) = serde_json::from_value::<FailedToolExport>(value.clone())
                else {
                    continue;
                };
                if export.status == FailedToolExportStatus::NonRetryable
                    || (export.status == FailedToolExportStatus::Inflight
                        && export.lease_until_ms > now_ms)
                {
                    continue;
                }
                export.status = FailedToolExportStatus::Inflight;
                export.claim_id = Some(claim_id.clone());
                export.lease_until_ms = lease_until_ms;
                exports.push(ClaimedFailedToolExport {
                    context_id: context_id.clone(),
                    tool_use_id: tool_use_id.clone(),
                    body: export.body.clone(),
                });
                if let Ok(serialized) = serde_json::to_value(export) {
                    *value = serialized;
                }
            }
        });
    });
    Ok((!exports.is_empty()).then_some(FailedToolExportClaim { claim_id, exports }))
}

pub fn mark_tool_export_batch_emitted(
    _state_file: &Path,
    state: &mut Value,
    session_id: &str,
    exported: &[(String, String)],
) -> Result<(), String> {
    update_realtime(state, session_id, |realtime| {
        for (context_id, tool_use_id) in exported {
            update_context_object(realtime, context_id, |context| {
                push_unique_string(context, "emitted_tool_use_ids", tool_use_id);
                if let Some(failed) = context
                    .get_mut("failed_tool_exports")
                    .and_then(Value::as_object_mut)
                {
                    failed.remove(tool_use_id);
                }
            });
        }
    });
    Ok(())
}

#[allow(clippy::too_many_arguments)]
pub fn mark_tool_export_batch_failed(
    _state_file: &Path,
    state: &mut Value,
    session_id: &str,
    claimed: &[(String, String)],
    current_context_id: &str,
    current_tool_use_id: &str,
    current_body: &Value,
    error: &str,
    non_retryable: bool,
) -> Result<(), String> {
    let failed_status = if non_retryable {
        FailedToolExportStatus::NonRetryable
    } else {
        FailedToolExportStatus::Failed
    };
    update_realtime(state, session_id, |realtime| {
        for (context_id, tool_use_id) in claimed {
            update_context_object(realtime, context_id, |context| {
                let Some(value) = context
                    .get_mut("failed_tool_exports")
                    .and_then(Value::as_object_mut)
                    .and_then(|failed| failed.get_mut(tool_use_id))
                else {
                    return;
                };
                let Ok(mut export) = serde_json::from_value::<FailedToolExport>(value.clone())
                else {
                    return;
                };
                export.status = failed_status.clone();
                export.claim_id = None;
                export.lease_until_ms = 0;
                export.last_error = Some(error.to_string());
                if let Ok(serialized) = serde_json::to_value(export) {
                    *value = serialized;
                }
            });
        }
        update_context_object(realtime, current_context_id, |context| {
            let failed = context
                .entry("failed_tool_exports".to_string())
                .or_insert_with(|| json!({}));
            if let Some(failed) = failed.as_object_mut() {
                failed.insert(
                    current_tool_use_id.to_string(),
                    serde_json::to_value(FailedToolExport {
                        body: current_body.clone(),
                        status: failed_status.clone(),
                        claim_id: None,
                        lease_until_ms: 0,
                        last_error: Some(error.to_string()),
                    })
                    .unwrap_or_else(|_| json!({})),
                );
            }
        });
    });
    Ok(())
}

fn for_each_context_object_mut(
    realtime: &mut Value,
    mut update: impl FnMut(&mut serde_json::Map<String, Value>),
) {
    if let Some(current) = realtime.get_mut("current").and_then(Value::as_object_mut) {
        update(current);
    }
    if let Some(pending) = realtime.get_mut("pending").and_then(Value::as_array_mut) {
        for context in pending.iter_mut().filter_map(Value::as_object_mut) {
            update(context);
        }
    }
}

fn update_context_object(
    realtime: &mut Value,
    context_id: &str,
    update: impl FnOnce(&mut serde_json::Map<String, Value>),
) {
    let mut update = Some(update);
    for_each_context_object_mut(realtime, |context| {
        if update.is_some() && context.get("context_id").and_then(Value::as_str) == Some(context_id)
        {
            if let Some(update) = update.take() {
                update(context);
            }
        }
    });
}

fn read_generation_state(realtime: &Value) -> Option<RealtimeGenerationState> {
    let mut value = realtime.get("generation")?.clone();
    if value.get("batch_size").is_none() {
        value["batch_size"] = json!(0);
    }
    serde_json::from_value(value).ok()
}

pub fn ensure_generation_state(
    _state_file: &Path,
    state: &mut Value,
    session_id: &str,
    configured_batch_size: usize,
    legacy_default_batch_size: usize,
) -> Result<RealtimeGenerationState, String> {
    let realtime = session_realtime(state, session_id);
    if let Some(mut generation) = read_generation_state(&realtime) {
        if generation.discovery_offset < generation.committed_offset {
            generation.discovery_offset = generation.committed_offset;
        }
        if generation.batch_size == 0 {
            generation.batch_size = legacy_default_batch_size;
        }
        let value = serde_json::to_value(&generation)
            .map_err(|error| format!("serialize realtime generation state: {error}"))?;
        update_realtime(state, session_id, |realtime| {
            realtime["generation"] = value;
        });
        return Ok(generation);
    }

    let generation = RealtimeGenerationState {
        scan_anchor: 0,
        committed_offset: 0,
        discovery_offset: 0,
        observed_file_len: 0,
        source_epoch: None,
        scan_terminal_reason: None,
        batch_size: configured_batch_size,
        rounds: Vec::new(),
    };
    let value = serde_json::to_value(&generation)
        .map_err(|error| format!("serialize realtime generation state: {error}"))?;
    update_realtime(state, session_id, |realtime| {
        realtime["generation"] = value;
    });
    Ok(generation)
}

pub fn checkpoint_generation_discovery(
    state: &mut Value,
    session_id: &str,
    expected_discovery_offset: i64,
    next_discovery_offset: i64,
    observed_file_len: u64,
    source_epoch: &TranscriptSourceEpoch,
) -> Result<bool, String> {
    let realtime = session_realtime(state, session_id);
    let Some(mut generation) = read_generation_state(&realtime) else {
        return Err("realtime generation state is missing".to_string());
    };
    if generation.discovery_offset != expected_discovery_offset {
        return Ok(false);
    }
    if generation
        .source_epoch
        .as_ref()
        .is_some_and(|known| known != source_epoch)
    {
        generation.scan_anchor = 0;
        generation.committed_offset = 0;
        generation.discovery_offset = 0;
        generation.rounds.clear();
    }
    generation.discovery_offset = next_discovery_offset;
    generation.observed_file_len = observed_file_len;
    generation.source_epoch = Some(source_epoch.clone());
    generation.scan_terminal_reason = None;
    let value = serde_json::to_value(&generation)
        .map_err(|error| format!("serialize realtime generation state: {error}"))?;
    update_realtime(state, session_id, |realtime| {
        realtime["generation"] = value;
    });
    Ok(true)
}

pub fn mark_generation_scan_terminal(
    state: &mut Value,
    session_id: &str,
    observed_file_len: u64,
    source_epoch: &TranscriptSourceEpoch,
    reason: &str,
) -> Result<(), String> {
    let realtime = session_realtime(state, session_id);
    let Some(mut generation) = read_generation_state(&realtime) else {
        return Err("realtime generation state is missing".to_string());
    };
    generation.observed_file_len = observed_file_len;
    generation.source_epoch = Some(source_epoch.clone());
    generation.scan_terminal_reason = Some(reason.to_string());
    let value = serde_json::to_value(&generation)
        .map_err(|error| format!("serialize realtime generation state: {error}"))?;
    update_realtime(state, session_id, |realtime| {
        realtime["generation"] = value;
    });
    Ok(())
}

#[allow(clippy::too_many_arguments)]
pub fn claim_generation_prefix(
    _state_file: &Path,
    state: &mut Value,
    session_id: &str,
    expected_scan_anchor: i64,
    next_scan_anchor: i64,
    committed_offset: i64,
    candidates: &[GenerationCandidate],
    now_ms: u64,
    lease_ms: u64,
    account_before_post: bool,
) -> Result<Option<GenerationClaim>, String> {
    let realtime = session_realtime(state, session_id);
    let Some(mut generation) = read_generation_state(&realtime) else {
        return Err("realtime generation state is missing".to_string());
    };
    if generation.scan_anchor != expected_scan_anchor {
        return Ok(None);
    }

    for round in &mut generation.rounds {
        if round.status == GenerationRoundStatus::Inflight && round.lease_until_ms <= now_ms {
            round.status = GenerationRoundStatus::Pending;
            round.claim_id = None;
            round.lease_until_ms = 0;
        }
    }
    if next_scan_anchor != expected_scan_anchor {
        let visible_ids: std::collections::HashSet<&str> = candidates
            .iter()
            .map(|candidate| candidate.message_id.as_str())
            .collect();
        for round in &mut generation.rounds {
            if round.status == GenerationRoundStatus::Pending
                && !visible_ids.contains(round.message_id.as_str())
            {
                round.status = GenerationRoundStatus::Deferred;
            }
        }
    }

    let context_map: HashMap<String, RealtimeContext> = contexts(state, session_id)
        .into_iter()
        .map(|context| (context.context_id.clone(), context))
        .collect();
    let mut next_ordinal = generation
        .rounds
        .iter()
        .map(|round| round.ordinal)
        .max()
        .unwrap_or(0)
        .saturating_add(1);
    for candidate in candidates {
        if generation
            .rounds
            .iter()
            .any(|round| round.message_id == candidate.message_id)
        {
            continue;
        }
        if !context_map.contains_key(&candidate.context_id) {
            continue;
        }
        generation.rounds.push(GenerationRound {
            message_id: candidate.message_id.clone(),
            span_id: stable_generation_span_id(&candidate.message_id),
            context_id: candidate.context_id.clone(),
            ordinal: next_ordinal,
            correlated_tool_use_ids: candidate.correlated_tool_use_ids.clone(),
            status: GenerationRoundStatus::Pending,
            claim_id: None,
            lease_until_ms: 0,
        });
        next_ordinal = next_ordinal.saturating_add(1);
    }
    generation.rounds.sort_by_key(|round| round.ordinal);
    generation.scan_anchor = next_scan_anchor;
    generation.committed_offset = committed_offset;

    let available: Vec<usize> = generation
        .rounds
        .iter()
        .enumerate()
        .skip_while(|(_, round)| {
            matches!(
                round.status,
                GenerationRoundStatus::Accounted | GenerationRoundStatus::Deferred
            )
        })
        .take_while(|(_, round)| round.status == GenerationRoundStatus::Pending)
        .map(|(index, _)| index)
        .collect();
    let claim_count = (available.len() / generation.batch_size) * generation.batch_size;
    let selected = &available[..claim_count];
    let claim_id = if selected.is_empty() {
        String::new()
    } else {
        stable_hex(
            "cchooks.realtime.generation-claim.v1",
            &format!(
                "{session_id}:{now_ms}:{}",
                selected
                    .iter()
                    .map(|index| generation.rounds[*index].message_id.as_str())
                    .collect::<Vec<_>>()
                    .join(",")
            ),
            16,
        )
    };

    let mut claimed = Vec::new();
    for index in selected {
        let round = &mut generation.rounds[*index];
        round.claim_id = Some(claim_id.clone());
        if account_before_post {
            round.status = GenerationRoundStatus::Accounted;
            round.lease_until_ms = 0;
        } else {
            round.status = GenerationRoundStatus::Inflight;
            round.lease_until_ms = now_ms.saturating_add(lease_ms);
        }
        let context = &context_map[&round.context_id];
        claimed.push(ClaimedGeneration {
            message_id: round.message_id.clone(),
            span_id: round.span_id.clone(),
            context_id: round.context_id.clone(),
            trace_id: context.trace_id.clone(),
            root_span_id: context.root_span_id.clone(),
            ordinal: round.ordinal,
        });
    }

    let generation_value = serde_json::to_value(&generation)
        .map_err(|error| format!("serialize realtime generation state: {error}"))?;
    update_realtime(state, session_id, |realtime| {
        realtime["generation"] = generation_value;
        for candidate in candidates {
            update_context_generation_identity(
                realtime,
                &candidate.context_id,
                &candidate.message_id,
                account_before_post
                    && claimed
                        .iter()
                        .any(|generation| generation.message_id == candidate.message_id),
            );
        }
    });

    Ok((!claimed.is_empty()).then_some(GenerationClaim {
        claim_id,
        batch_size: generation.batch_size,
        generations: claimed,
    }))
}

fn update_context_generation_identity(
    realtime: &mut Value,
    context_id: &str,
    message_id: &str,
    accounted: bool,
) {
    let update = |object: &mut serde_json::Map<String, Value>| {
        let span_ids = object
            .entry("generation_span_ids".to_string())
            .or_insert_with(|| json!({}));
        if let Some(span_ids) = span_ids.as_object_mut() {
            span_ids.insert(
                message_id.to_string(),
                Value::String(stable_generation_span_id(message_id)),
            );
        }
        if accounted {
            push_unique_string(object, "accounted_generation_ids", message_id);
        }
    };
    if let Some(current) = realtime.get_mut("current").and_then(Value::as_object_mut) {
        if current.get("context_id").and_then(Value::as_str) == Some(context_id) {
            update(current);
            return;
        }
    }
    if let Some(pending) = realtime.get_mut("pending").and_then(Value::as_array_mut) {
        for item in pending {
            let Some(object) = item.as_object_mut() else {
                continue;
            };
            if object.get("context_id").and_then(Value::as_str) == Some(context_id) {
                update(object);
                return;
            }
        }
    }
}

pub fn finish_generation_claim(
    _state_file: &Path,
    state: &mut Value,
    session_id: &str,
    claim_id: &str,
    update: GenerationClaimUpdate<'_>,
) -> Result<(), String> {
    let realtime = session_realtime(state, session_id);
    let Some(mut generation) = read_generation_state(&realtime) else {
        return Ok(());
    };
    let mut accounted = Vec::new();
    for round in &mut generation.rounds {
        if round.claim_id.as_deref() != Some(claim_id)
            || round.status != GenerationRoundStatus::Inflight
        {
            continue;
        }
        match update {
            GenerationClaimUpdate::Account(message_ids)
                if message_ids.iter().any(|id| id == &round.message_id) =>
            {
                round.status = GenerationRoundStatus::Accounted;
                accounted.push((round.context_id.clone(), round.message_id.clone()));
            }
            GenerationClaimUpdate::Account(_) => continue,
            GenerationClaimUpdate::Release => {
                round.status = GenerationRoundStatus::Pending;
            }
        }
        round.claim_id = None;
        round.lease_until_ms = 0;
    }
    let generation_value = serde_json::to_value(&generation)
        .map_err(|error| format!("serialize realtime generation state: {error}"))?;
    update_realtime(state, session_id, |realtime| {
        realtime["generation"] = generation_value;
        for (context_id, message_id) in &accounted {
            update_context_generation_identity(realtime, context_id, message_id, true);
        }
    });
    Ok(())
}

fn push_unique_string(object: &mut serde_json::Map<String, Value>, field: &str, value: &str) {
    let values = object.entry(field.to_string()).or_insert_with(|| json!([]));
    let Some(values) = values.as_array_mut() else {
        return;
    };
    if !values.iter().any(|item| item.as_str() == Some(value)) {
        values.push(Value::String(value.to_string()));
    }
}

pub fn remove_contexts_from_realtime(realtime: &mut Value, matched_context_ids: &[String]) {
    if matched_context_ids.is_empty() {
        return;
    }
    if let Some(ledger) = realtime
        .get_mut("child_ledger")
        .and_then(Value::as_object_mut)
    {
        for value in ledger.values_mut() {
            let Some(mut record) =
                serde_json::from_value::<ChildDeliveryRecord>(value.clone()).ok()
            else {
                continue;
            };
            if matched_context_ids.contains(&record.context_id)
                && matches!(
                    record.status,
                    ChildDeliveryStatus::Pending
                        | ChildDeliveryStatus::Claimed
                        | ChildDeliveryStatus::FailedRetryable
                )
            {
                record.status = ChildDeliveryStatus::Compensated;
                record.terminal_reason = Some("stop_finalizer_compensation".to_string());
                if let Ok(serialized) = serde_json::to_value(record) {
                    *value = serialized;
                }
            }
        }
    }
    if realtime
        .get("current")
        .and_then(|current| current.get("context_id"))
        .and_then(Value::as_str)
        .map(|id| matched_context_ids.iter().any(|matched| matched == id))
        .unwrap_or(false)
    {
        realtime
            .as_object_mut()
            .map(|object| object.remove("current"));
    }
    if let Some(pending) = realtime.get_mut("pending").and_then(Value::as_array_mut) {
        pending.retain(|context| {
            context
                .get("context_id")
                .and_then(Value::as_str)
                .map(|id| !matched_context_ids.iter().any(|matched| matched == id))
                .unwrap_or(true)
        });
    }
    if let Some(rounds) = realtime
        .get_mut("generation")
        .and_then(|generation| generation.get_mut("rounds"))
        .and_then(Value::as_array_mut)
    {
        rounds.retain(|round| {
            round
                .get("context_id")
                .and_then(Value::as_str)
                .map(|id| !matched_context_ids.iter().any(|matched| matched == id))
                .unwrap_or(true)
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_exact_end_time_for_a_persisted_otlp_span() {
        let body = json!({
            "resourceSpans": [{
                "scopeSpans": [{
                    "spans": [
                        {"spanId": "first", "endTimeUnixNano": "100"},
                        {"spanId": "target", "endTimeUnixNano": "200"}
                    ]
                }]
            }]
        });

        assert_eq!(
            otlp_span_end_time_unix_nano(&body, "target").as_deref(),
            Some("200")
        );
        assert_eq!(otlp_span_end_time_unix_nano(&body, "missing"), None);
    }

    #[test]
    fn context_lifecycle_is_stable_and_isolated() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({"s": {
            "offset": 12,
            "buffer": "tail",
            "turn_count": 3,
            "posted_turn_ids": ["turn-1"]
        }});
        let stop_fields = state["s"].clone();

        let first = claim_tool(&path, &mut state, "s", "tool-1", "now", None).unwrap();
        let repeated = claim_tool(&path, &mut state, "s", "tool-1", "later", None).unwrap();
        assert_eq!(first, repeated);
        assert_eq!(first.tool_span_ids["tool-1"], stable_tool_span_id("tool-1"));
        mark_tool_export_batch_emitted(
            &path,
            &mut state,
            "s",
            &[(first.context_id.clone(), "tool-1".to_string())],
        )
        .unwrap();
        rotate_current(&path, &mut state, "s").unwrap();

        for field in ["offset", "buffer", "turn_count", "posted_turn_ids"] {
            assert_eq!(state["s"][field], stop_fields[field]);
        }
        let loaded = contexts(&state, "s");
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].emitted_tool_use_ids, vec!["tool-1"]);
    }

    #[test]
    fn trace_snapshot_is_captured_once_for_stop_recovery() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        let first_traceparent = "00-11111111111111111111111111111111-2222222222222222-01";
        let context = claim_tool_with_trace_snapshot(
            &path,
            &mut state,
            "s",
            "tool-1",
            "first",
            Some(first_traceparent),
            Some("sofa-trace-first"),
            Some("0.1"),
        )
        .unwrap();
        let repeated = claim_tool_with_trace_snapshot(
            &path,
            &mut state,
            "s",
            "tool-2",
            "later",
            Some("00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"),
            Some("sofa-trace-later"),
            Some("0.2"),
        )
        .unwrap();

        assert_eq!(context.traceparent.as_deref(), Some(first_traceparent));
        assert_eq!(repeated.traceparent, context.traceparent);
        assert_eq!(repeated.sofa_traceid.as_deref(), Some("sofa-trace-first"));
        assert_eq!(repeated.sofa_rpcid.as_deref(), Some("0.1"));
        assert_eq!(contexts(&state, "s"), vec![repeated]);
    }

    fn child_record(state: &Value, session_id: &str, child_key: &str) -> ChildDeliveryRecord {
        serde_json::from_value(
            state[session_id]["realtime_export"]["child_ledger"][child_key].clone(),
        )
        .unwrap()
    }

    #[test]
    fn child_ledger_enforces_retry_policy_state_machines() {
        let mut state = json!({});
        let at_most_once_key = tool_child_key("ctx", "tool-1");
        let spec = || ChildDeliverySpec {
            child_key: &at_most_once_key,
            kind: "tool",
            context_id: "ctx",
            turn_evidence: json!({"tool_use_id": "tool-1"}),
            trace_id: "trace",
            root_span_id: "root",
            span_id: "span",
            end_time_unix_nano: Some("100".to_string()),
        };
        assert!(prepare_child_delivery(
            &mut state,
            "s",
            spec(),
            RetryPolicy::AtMostOnce,
            "transport-1",
            "digest-1",
            "first",
        )
        .unwrap());
        let checkpointed = child_record(&state, "s", &at_most_once_key);
        assert_eq!(checkpointed.status, ChildDeliveryStatus::PolicyTerminal);
        assert_eq!(
            checkpointed.terminal_reason.as_deref(),
            Some("checkpoint_before_post")
        );
        assert!(!prepare_child_delivery(
            &mut state,
            "s",
            spec(),
            RetryPolicy::AtMostOnce,
            "transport-2",
            "digest-1",
            "later",
        )
        .unwrap());

        let at_least_once_key = generation_child_key("ctx", "message-1");
        let generation_spec = || ChildDeliverySpec {
            child_key: &at_least_once_key,
            kind: "generation",
            context_id: "ctx",
            turn_evidence: json!({"message_id": "message-1", "ordinal": 1}),
            trace_id: "trace",
            root_span_id: "root",
            span_id: "generation-span",
            end_time_unix_nano: Some("200".to_string()),
        };
        assert!(prepare_child_delivery(
            &mut state,
            "s",
            generation_spec(),
            RetryPolicy::AtLeastOnce,
            "transport-3",
            "digest-2",
            "first",
        )
        .unwrap());
        finish_child_deliveries(
            &mut state,
            "s",
            std::slice::from_ref(&at_least_once_key),
            RetryPolicy::AtLeastOnce,
            false,
            "failed",
            Some("timeout"),
        )
        .unwrap();
        assert_eq!(
            child_record(&state, "s", &at_least_once_key).status,
            ChildDeliveryStatus::FailedRetryable
        );
        reclaim_child_deliveries(
            &mut state,
            "s",
            std::slice::from_ref(&at_least_once_key),
            "transport-4",
            "digest-2",
        )
        .unwrap();
        assert_eq!(
            child_record(&state, "s", &at_least_once_key).status,
            ChildDeliveryStatus::Claimed
        );
        finish_child_deliveries(
            &mut state,
            "s",
            std::slice::from_ref(&at_least_once_key),
            RetryPolicy::AtLeastOnce,
            true,
            "accepted",
            None,
        )
        .unwrap();
        let accounted = child_record(&state, "s", &at_least_once_key);
        assert_eq!(accounted.status, ChildDeliveryStatus::Accounted);
        assert_eq!(accounted.attempt_count, 2);
        assert_eq!(accounted.accepted_at.as_deref(), Some("accepted"));
        assert_eq!(accounted.end_time_unix_nano.as_deref(), Some("200"));
        assert_eq!(
            accounted_child_max_end_times(&state, "s").get("ctx"),
            Some(&"200".to_string())
        );
    }

    #[test]
    fn stop_removal_compensates_unfinished_children_without_erasing_audit_rows() {
        let mut state = json!({});
        let child_key = tool_child_key("ctx", "tool-1");
        prepare_child_delivery(
            &mut state,
            "s",
            ChildDeliverySpec {
                child_key: &child_key,
                kind: "tool",
                context_id: "ctx",
                turn_evidence: json!({"tool_use_id": "tool-1"}),
                trace_id: "trace",
                root_span_id: "root",
                span_id: "span",
                end_time_unix_nano: None,
            },
            RetryPolicy::AtLeastOnce,
            "transport-1",
            "digest-1",
            "first",
        )
        .unwrap();
        state["s"]["realtime_export"]["current"] = json!({"context_id": "ctx"});

        remove_contexts_from_realtime(&mut state["s"]["realtime_export"], &["ctx".to_string()]);

        let compensated = child_record(&state, "s", &child_key);
        assert_eq!(compensated.status, ChildDeliveryStatus::Compensated);
        assert_eq!(
            compensated.terminal_reason.as_deref(),
            Some("stop_finalizer_compensation")
        );
        assert!(state["s"]["realtime_export"].get("current").is_none());
    }

    #[test]
    fn stable_tool_id_is_domain_separated_and_sixteen_hex_chars() {
        let id = stable_tool_span_id("tool-1");
        assert_eq!(id.len(), 16);
        assert_eq!(id, stable_tool_span_id("tool-1"));
        assert_ne!(id, stable_tool_span_id("tool-2"));
    }

    #[test]
    fn prompt_rotation_keeps_turn_contexts_separate_and_ordered() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        let first = claim_tool(&path, &mut state, "s", "tool-1", "first", None).unwrap();
        rotate_current(&path, &mut state, "s").unwrap();
        let second = claim_tool(&path, &mut state, "s", "tool-2", "second", None).unwrap();

        let loaded = contexts(&state, "s");
        assert_eq!(loaded.len(), 2);
        assert_eq!(loaded[0], first);
        assert_eq!(loaded[1], second);
        assert_ne!(loaded[0].trace_id, loaded[1].trace_id);
        assert_eq!(loaded[0].tool_use_ids, vec!["tool-1"]);
        assert_eq!(loaded[1].tool_use_ids, vec!["tool-2"]);
    }

    fn candidates(context_id: &str, count: usize) -> Vec<GenerationCandidate> {
        (1..=count)
            .map(|index| GenerationCandidate {
                message_id: format!("msg-{index}"),
                context_id: context_id.to_string(),
                correlated_tool_use_ids: vec![format!("tool-{index}")],
            })
            .collect()
    }

    #[test]
    fn generation_batch_claims_largest_full_prefix_for_generic_n() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        let context = claim_tool(&path, &mut state, "s", "tool-1", "now", None).unwrap();
        for index in 2..=6 {
            claim_tool(
                &path,
                &mut state,
                "s",
                &format!("tool-{index}"),
                "now",
                None,
            )
            .unwrap();
        }
        ensure_generation_state(&path, &mut state, "s", 3, 3).unwrap();

        let none = claim_generation_prefix(
            &path,
            &mut state,
            "s",
            0,
            0,
            10,
            &candidates(&context.context_id, 2),
            1,
            5_000,
            false,
        )
        .unwrap();
        assert!(none.is_none());

        let first = claim_generation_prefix(
            &path,
            &mut state,
            "s",
            0,
            0,
            20,
            &candidates(&context.context_id, 3),
            2,
            5_000,
            false,
        )
        .unwrap()
        .unwrap();
        assert_eq!(first.generations.len(), 3);
        let first_ids: Vec<String> = first
            .generations
            .iter()
            .map(|generation| generation.message_id.clone())
            .collect();
        finish_generation_claim(
            &path,
            &mut state,
            "s",
            &first.claim_id,
            GenerationClaimUpdate::Account(&first_ids),
        )
        .unwrap();

        let second = claim_generation_prefix(
            &path,
            &mut state,
            "s",
            0,
            0,
            30,
            &candidates(&context.context_id, 6),
            3,
            5_000,
            false,
        )
        .unwrap()
        .unwrap();
        assert_eq!(second.generations.len(), 3);
    }

    #[test]
    fn delayed_scan_can_claim_six_rounds_in_one_request() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        let context = claim_tool(&path, &mut state, "s", "tool-1", "now", None).unwrap();
        for index in 2..=6 {
            claim_tool(
                &path,
                &mut state,
                "s",
                &format!("tool-{index}"),
                "now",
                None,
            )
            .unwrap();
        }
        ensure_generation_state(&path, &mut state, "s", 3, 3).unwrap();
        let claim = claim_generation_prefix(
            &path,
            &mut state,
            "s",
            0,
            0,
            30,
            &candidates(&context.context_id, 6),
            1,
            5_000,
            false,
        )
        .unwrap()
        .unwrap();
        assert_eq!(claim.generations.len(), 6);
    }

    #[test]
    fn batch_size_is_snapshotted_and_legacy_missing_value_migrates_to_three() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        let first = ensure_generation_state(&path, &mut state, "s", 5, 3).unwrap();
        let unchanged = ensure_generation_state(&path, &mut state, "s", 9, 3).unwrap();
        assert_eq!(first.batch_size, 5);
        assert_eq!(unchanged.batch_size, 5);

        let mut legacy = json!({"old": {
            "realtime_export": {
                "generation": {"scan_anchor": 7, "committed_offset": 9, "rounds": []}
            }
        }});
        let migrated = ensure_generation_state(&path, &mut legacy, "old", 8, 3).unwrap();
        assert_eq!(migrated.batch_size, 3);
        assert_eq!(
            legacy["old"]["realtime_export"]["generation"]["batch_size"],
            3
        );
        assert_eq!(migrated.discovery_offset, 9);
    }

    #[test]
    fn discovery_checkpoint_is_monotonic_and_source_epoch_change_resets_semantic_state() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        ensure_generation_state(&path, &mut state, "s", 3, 3).unwrap();
        let first_epoch = TranscriptSourceEpoch {
            identity_digest: "first".to_string(),
            prefix_len: 8,
        };
        assert!(
            checkpoint_generation_discovery(&mut state, "s", 0, 128, 256, &first_epoch,).unwrap()
        );
        assert!(
            !checkpoint_generation_discovery(&mut state, "s", 0, 200, 256, &first_epoch,).unwrap()
        );

        let generation = read_generation_state(&session_realtime(&state, "s")).unwrap();
        assert_eq!(generation.discovery_offset, 128);
        assert_eq!(generation.observed_file_len, 256);

        let second_epoch = TranscriptSourceEpoch {
            identity_digest: "second".to_string(),
            prefix_len: 8,
        };
        assert!(
            checkpoint_generation_discovery(&mut state, "s", 128, 0, 16, &second_epoch,).unwrap()
        );
        let reset = read_generation_state(&session_realtime(&state, "s")).unwrap();
        assert_eq!(reset.scan_anchor, 0);
        assert_eq!(reset.committed_offset, 0);
        assert_eq!(reset.discovery_offset, 0);
        assert!(reset.rounds.is_empty());
        assert_eq!(reset.source_epoch, Some(second_epoch));
    }

    #[test]
    fn at_most_once_accounts_before_post_while_inflight_is_not_accounted() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        let context = claim_tool(&path, &mut state, "s", "tool-1", "now", None).unwrap();
        ensure_generation_state(&path, &mut state, "s", 1, 3).unwrap();
        let claim = claim_generation_prefix(
            &path,
            &mut state,
            "s",
            0,
            0,
            10,
            &candidates(&context.context_id, 1),
            1,
            5_000,
            true,
        )
        .unwrap()
        .unwrap();
        assert_eq!(claim.generations.len(), 1);
        let loaded = contexts(&state, "s");
        assert_eq!(loaded[0].accounted_generation_ids, vec!["msg-1"]);
    }

    #[test]
    fn successful_chunk_is_accounted_before_releasing_failed_tail() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        let context = claim_tool(&path, &mut state, "s", "tool-1", "now", None).unwrap();
        for index in 2..=6 {
            claim_tool(
                &path,
                &mut state,
                "s",
                &format!("tool-{index}"),
                "now",
                None,
            )
            .unwrap();
        }
        ensure_generation_state(&path, &mut state, "s", 3, 3).unwrap();
        let claim = claim_generation_prefix(
            &path,
            &mut state,
            "s",
            0,
            0,
            60,
            &candidates(&context.context_id, 6),
            10,
            5_000,
            false,
        )
        .unwrap()
        .unwrap();
        let first_chunk = claim.generations[..3]
            .iter()
            .map(|generation| generation.message_id.clone())
            .collect::<Vec<_>>();
        finish_generation_claim(
            &path,
            &mut state,
            "s",
            &claim.claim_id,
            GenerationClaimUpdate::Account(&first_chunk),
        )
        .unwrap();
        finish_generation_claim(
            &path,
            &mut state,
            "s",
            &claim.claim_id,
            GenerationClaimUpdate::Release,
        )
        .unwrap();

        let generation = read_generation_state(&session_realtime(&state, "s")).unwrap();
        assert!(generation.rounds[..3]
            .iter()
            .all(|round| round.status == GenerationRoundStatus::Accounted));
        assert!(generation.rounds[3..]
            .iter()
            .all(|round| round.status == GenerationRoundStatus::Pending));
        assert_eq!(
            contexts(&state, "s")[0].accounted_generation_ids,
            first_chunk
        );
    }

    #[test]
    fn active_inflight_claim_blocks_concurrent_sender_and_expired_lease_retries() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        let context = claim_tool(&path, &mut state, "s", "tool-1", "now", None).unwrap();
        ensure_generation_state(&path, &mut state, "s", 1, 3).unwrap();
        let first = claim_generation_prefix(
            &path,
            &mut state,
            "s",
            0,
            0,
            10,
            &candidates(&context.context_id, 1),
            100,
            5_000,
            false,
        )
        .unwrap()
        .unwrap();
        let concurrent = claim_generation_prefix(
            &path,
            &mut state,
            "s",
            0,
            0,
            10,
            &candidates(&context.context_id, 1),
            101,
            5_000,
            false,
        )
        .unwrap();
        assert!(concurrent.is_none());

        let retry = claim_generation_prefix(
            &path,
            &mut state,
            "s",
            0,
            0,
            10,
            &candidates(&context.context_id, 1),
            5_101,
            5_000,
            false,
        )
        .unwrap()
        .unwrap();
        assert_ne!(retry.claim_id, first.claim_id);
    }
}
