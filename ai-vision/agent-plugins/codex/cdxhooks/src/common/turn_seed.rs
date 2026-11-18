use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::common::dirs;
use crate::common::runtime_state::{write_json_atomically, DomainStateStore, StateDomain};

pub const TURN_SEED_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TurnSeed {
    pub schema_version: u32,
    pub turn_id: String,
    pub trace_id: String,
    pub upstream_traceparent: Option<String>,
    #[serde(default)]
    pub sofa_traceid: Option<String>,
    #[serde(default)]
    pub sofa_rpcid: Option<String>,
    pub created_at: String,
}

pub fn replace_for_prompt(
    session_id: &str,
    payload: &Value,
    upstream_traceparent: Option<String>,
) -> Result<TurnSeed, String> {
    let created_at = agentic_otel_common::now_iso8601_pub();
    let turn_id = prompt_turn_id(payload)
        .unwrap_or_else(|| stable_prompt_turn_id(session_id, payload, &created_at));
    let trace_id = upstream_traceparent
        .as_deref()
        .and_then(trace_id_from_traceparent)
        .unwrap_or_else(agentic_otel_common::otel_trace_id);
    let seed = TurnSeed {
        schema_version: TURN_SEED_SCHEMA_VERSION,
        turn_id,
        trace_id,
        upstream_traceparent,
        sofa_traceid: non_empty_effective_env("SOFA_TRACEID"),
        sofa_rpcid: non_empty_effective_env("SOFA_RPCID"),
        created_at,
    };
    let path = dirs::session_turn_seed_file(session_id)
        .ok_or_else(|| "missing session id for turn seed".to_string())?;
    write_json_atomically(
        &path,
        &serde_json::to_value(&seed).map_err(|error| error.to_string())?,
    )?;
    Ok(seed)
}

pub fn trace_id_for_tool(
    session_id: &str,
    upstream_traceparent: Option<&str>,
) -> Result<String, String> {
    if let Some(path) = dirs::session_turn_seed_file(session_id) {
        if let Ok(contents) = std::fs::read_to_string(path) {
            if let Ok(seed) = serde_json::from_str::<TurnSeed>(&contents) {
                if seed.schema_version == TURN_SEED_SCHEMA_VERSION
                    && is_valid_trace_id(&seed.trace_id)
                {
                    return Ok(seed.trace_id);
                }
            }
        }
    }
    let legacy = DomainStateStore::for_session(session_id, StateDomain::Turn)?.load()?;
    if let Some(trace_id) = legacy
        .get("current_trace_context")
        .and_then(|value| value.get("trace_id"))
        .and_then(Value::as_str)
        .filter(|value| is_valid_trace_id(value))
    {
        return Ok(trace_id.to_string());
    }
    Ok(upstream_traceparent
        .and_then(trace_id_from_traceparent)
        .unwrap_or_else(agentic_otel_common::otel_trace_id))
}

pub(crate) fn current_seed_for_turn(session_id: &str, turn_id: &str) -> Option<TurnSeed> {
    let turn_id = turn_id.trim();
    if turn_id.is_empty() {
        return None;
    }
    current_turn_seed(session_id).filter(|seed| {
        seed.turn_id == turn_id
            && seed.schema_version == TURN_SEED_SCHEMA_VERSION
            && is_valid_trace_id(&seed.trace_id)
    })
}

pub fn stop_intent(event: &str, payload: &Value) -> Value {
    let session_id = crate::common::hook::extract_session_id(payload);
    let transcript_path = crate::common::hook::extract_transcript_path(payload);
    let created_at = agentic_otel_common::now_iso8601_pub();
    let target_turn_id = payload
        .get("turn_id")
        .or_else(|| payload.get("turnId"))
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .map(str::to_string)
        .or_else(|| current_turn_seed(&session_id).map(|seed| seed.turn_id));
    let observed_file_len = std::fs::metadata(&transcript_path)
        .ok()
        .map(|metadata| metadata.len());
    let intent_id = stable_stop_intent_id(
        &session_id,
        event,
        target_turn_id.as_deref(),
        observed_file_len,
        &created_at,
    );
    let mut intent = json!({
        "schema_version": 2,
        "intent_id": intent_id,
        "hook_event_name": event,
        "session_id": session_id,
        "transcript_path": transcript_path,
        "created_at": created_at,
        "status": "pending"
    });
    if let Some(turn_id) = target_turn_id {
        intent["target_turn_id"] = json!(turn_id);
    }
    if let Some(file_len) = observed_file_len {
        intent["observed_file_len"] = json!(file_len);
    }
    intent
}

fn current_turn_seed(session_id: &str) -> Option<TurnSeed> {
    let path = dirs::session_turn_seed_file(session_id)?;
    let contents = std::fs::read_to_string(path).ok()?;
    serde_json::from_str::<TurnSeed>(&contents)
        .ok()
        .filter(|seed| seed.schema_version == TURN_SEED_SCHEMA_VERSION)
}

fn stable_stop_intent_id(
    session_id: &str,
    event: &str,
    target_turn_id: Option<&str>,
    observed_file_len: Option<u64>,
    created_at: &str,
) -> String {
    let mut hasher = Sha256::new();
    for value in [
        "cdxhooks-stop-intent-v2",
        session_id,
        event,
        target_turn_id.unwrap_or_default(),
        created_at,
    ] {
        hasher.update(value.len().to_string().as_bytes());
        hasher.update(b":");
        hasher.update(value.as_bytes());
        hasher.update(b"|");
    }
    if let Some(file_len) = observed_file_len {
        hasher.update(file_len.to_string().as_bytes());
    }
    let suffix = hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    format!("stop-{suffix}")
}

fn prompt_turn_id(payload: &Value) -> Option<String> {
    ["turn_id", "turnId", "uuid", "id", "requestId", "request_id"]
        .iter()
        .find_map(|key| {
            payload
                .get(*key)
                .and_then(Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .map(str::to_string)
        })
}

fn stable_prompt_turn_id(session_id: &str, payload: &Value, created_at: &str) -> String {
    let mut hasher = Sha256::new();
    for value in [
        "cdxhooks-turn-seed-v1",
        session_id,
        payload
            .get("timestamp")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        created_at,
    ] {
        hasher.update(value.len().to_string().as_bytes());
        hasher.update(b":");
        hasher.update(value.as_bytes());
        hasher.update(b"|");
    }
    let suffix = hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    format!("seed-{suffix}")
}

fn trace_id_from_traceparent(value: &str) -> Option<String> {
    agentic_otel_common::parse_traceparent(value).and_then(|traceparent| {
        is_valid_trace_id(&traceparent.trace_id).then_some(traceparent.trace_id)
    })
}

fn non_empty_effective_env(key: &str) -> Option<String> {
    crate::common::env::var(key)
        .ok()
        .filter(|value| !value.trim().is_empty())
}

fn is_valid_trace_id(value: &str) -> bool {
    value.len() == 32
        && value.bytes().all(|byte| byte.is_ascii_hexdigit())
        && !value.bytes().all(|byte| byte == b'0')
}

#[cfg(test)]
mod tests {
    use super::stable_stop_intent_id;

    #[test]
    fn stop_intent_id_advances_with_observed_transcript_boundary() {
        let first = stable_stop_intent_id(
            "session",
            "Stop",
            Some("turn-1"),
            Some(100),
            "2026-08-27T00:00:00Z",
        );
        let second = stable_stop_intent_id(
            "session",
            "Stop",
            Some("turn-1"),
            Some(101),
            "2026-08-27T00:00:00Z",
        );

        assert_ne!(first, second);
    }
}
