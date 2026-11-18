use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::common::hook::extract_transcript_path;
use crate::common::transcript::{inspect_turn_transcript, TranscriptSourceEpoch};
use crate::hooks::langfuse::domain_state::{DomainStateStore, StateDomain};
use crate::hooks::langfuse::state::StateTxnError;

pub const TURN_SEED_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TurnTranscriptBoundary {
    pub transcript_path: String,
    pub source_epoch: TranscriptSourceEpoch,
    pub start_offset: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TurnSeed {
    pub schema_version: u32,
    pub turn_id: String,
    pub trace_id: String,
    pub upstream_traceparent: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prompt_timestamp: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prompt_digest: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transcript_boundary: Option<TurnTranscriptBoundary>,
    pub created_at: String,
}

#[cfg(all(feature = "tool_context", not(windows)))]
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TurnTraceContext {
    Seed(TurnSeed),
    Legacy { trace_id: String },
    Upstream { trace_id: String },
    Missing,
}

#[cfg(all(feature = "tool_context", not(windows)))]
impl TurnTraceContext {
    pub fn trace_id(&self) -> Option<&str> {
        match self {
            Self::Seed(seed) => Some(seed.trace_id.as_str()),
            Self::Legacy { trace_id } | Self::Upstream { trace_id } => Some(trace_id.as_str()),
            Self::Missing => None,
        }
    }
}

pub fn replace_turn_seed_for_prompt(
    session_id: &str,
    payload: &Value,
    upstream_traceparent: Option<String>,
) -> Result<TurnSeed, StateTxnError> {
    let store = DomainStateStore::for_session(session_id, StateDomain::Turn, 10)?;
    replace_turn_seed_in_store(
        &store,
        session_id,
        payload,
        upstream_traceparent,
        agentic_otel_common::now_iso8601_pub(),
    )
}

/// Read the latest prompt-bound trace seed for a Stop-family event.
///
/// Missing or legacy turn-domain state fails open as `None`. Invalid current
/// seed data returns a schema error so the parent hook can warn before failing
/// open; in particular, an upstream traceparent must be valid and carry the
/// same trace ID as the seed.
pub fn read_current_turn_seed(session_id: &str) -> Result<Option<TurnSeed>, StateTxnError> {
    // Parent hooks must remain low-latency. A missed snapshot fails open and is
    // recovered through the queue TRACEPARENT/local-trace fallback.
    let store = DomainStateStore::for_session(session_id, StateDomain::Turn, 0)?;
    read_current_turn_seed_from_store(&store)
}

pub(crate) fn read_current_turn_seed_from_store(
    store: &DomainStateStore,
) -> Result<Option<TurnSeed>, StateTxnError> {
    store.read(parse_seed_checked)?
}

pub fn replace_turn_seed_in_store(
    store: &DomainStateStore,
    session_id: &str,
    payload: &Value,
    upstream_traceparent: Option<String>,
    created_at: String,
) -> Result<TurnSeed, StateTxnError> {
    let seed = build_turn_seed(session_id, payload, upstream_traceparent, created_at);
    store.update(|state| {
        *state = serde_json::to_value(&seed)
            .map_err(|error| StateTxnError::Write(format!("serialize turn seed: {error}")))?;
        Ok(())
    })?;
    Ok(seed)
}

#[cfg(all(feature = "tool_context", not(windows)))]
pub fn read_trace_context_for_tool(
    session_id: &str,
    upstream_traceparent: Option<&str>,
) -> Result<TurnTraceContext, StateTxnError> {
    let store = DomainStateStore::for_session(session_id, StateDomain::Turn, 50)?;
    read_trace_context_from_store(&store, upstream_traceparent)
}

#[cfg(all(feature = "tool_context", not(windows)))]
pub fn read_trace_context_from_store(
    store: &DomainStateStore,
    upstream_traceparent: Option<&str>,
) -> Result<TurnTraceContext, StateTxnError> {
    store.read(|state| {
        parse_seed(state)
            .map(TurnTraceContext::Seed)
            .or_else(|| {
                legacy_trace_id(state).map(|trace_id| TurnTraceContext::Legacy { trace_id })
            })
            .or_else(|| {
                upstream_trace_id(upstream_traceparent)
                    .map(|trace_id| TurnTraceContext::Upstream { trace_id })
            })
            .unwrap_or(TurnTraceContext::Missing)
    })
}

fn build_turn_seed(
    session_id: &str,
    payload: &Value,
    upstream_traceparent: Option<String>,
    created_at: String,
) -> TurnSeed {
    let turn_id = prompt_turn_id(payload)
        .unwrap_or_else(|| stable_prompt_turn_id(session_id, payload, &created_at));
    let transcript_boundary = capture_transcript_boundary(payload, &turn_id);
    let trace_id = upstream_traceparent
        .as_deref()
        .and_then(|traceparent| upstream_trace_id(Some(traceparent)))
        .unwrap_or_else(agentic_otel_common::otel_trace_id);
    TurnSeed {
        schema_version: TURN_SEED_SCHEMA_VERSION,
        turn_id,
        trace_id,
        upstream_traceparent,
        prompt_timestamp: payload
            .get("timestamp")
            .and_then(Value::as_str)
            .filter(|timestamp| !timestamp.trim().is_empty())
            .map(str::to_string),
        prompt_digest: prompt_text(payload).map(prompt_digest),
        transcript_boundary,
        created_at,
    }
}

fn capture_transcript_boundary(payload: &Value, turn_id: &str) -> Option<TurnTranscriptBoundary> {
    let transcript_path = extract_transcript_path(payload);
    if transcript_path.is_empty() {
        return None;
    }
    let path = std::path::Path::new(&transcript_path);
    let inspection = inspect_turn_transcript(path, turn_id, prompt_text(payload)).ok()?;
    let start_offset = inspection
        .prompt_start
        .unwrap_or_else(|| i64::try_from(inspection.snapshot.file_len).unwrap_or(i64::MAX));
    Some(TurnTranscriptBoundary {
        transcript_path: inspection.canonical_path,
        source_epoch: inspection.snapshot.epoch,
        start_offset,
    })
}

fn parse_seed(state: &Value) -> Option<TurnSeed> {
    parse_seed_checked(state).ok().flatten()
}

fn parse_seed_checked(state: &Value) -> Result<Option<TurnSeed>, StateTxnError> {
    let seed: TurnSeed = match serde_json::from_value(state.clone()) {
        Ok(seed) => seed,
        Err(error) if state.get("schema_version").is_some() || state.get("turn_id").is_some() => {
            return Err(StateTxnError::Schema(format!(
                "invalid current turn seed: {error}"
            )))
        }
        Err(_) => return Ok(None),
    };
    if seed.schema_version != TURN_SEED_SCHEMA_VERSION
        || seed.turn_id.trim().is_empty()
        || !is_valid_trace_id(&seed.trace_id)
        || seed.created_at.trim().is_empty()
    {
        return Err(StateTxnError::Schema(
            "invalid current turn seed fields".to_string(),
        ));
    }
    if let Some(traceparent) = seed.upstream_traceparent.as_deref() {
        let upstream = agentic_otel_common::parse_traceparent(traceparent).ok_or_else(|| {
            StateTxnError::Schema("invalid current turn seed traceparent".to_string())
        })?;
        if upstream.trace_id != seed.trace_id {
            return Err(StateTxnError::Schema(
                "current turn seed traceparent trace-id mismatch".to_string(),
            ));
        }
    }
    Ok(Some(seed))
}

#[cfg(all(feature = "tool_context", not(windows)))]
fn legacy_trace_id(state: &Value) -> Option<String> {
    state
        .get("current_trace_context")?
        .get("trace_id")?
        .as_str()
        .filter(|trace_id| is_valid_trace_id(trace_id))
        .map(str::to_string)
}

fn upstream_trace_id(upstream_traceparent: Option<&str>) -> Option<String> {
    agentic_otel_common::parse_traceparent(upstream_traceparent?).map(|tp| tp.trace_id)
}

fn is_valid_trace_id(trace_id: &str) -> bool {
    trace_id.len() == 32
        && trace_id.bytes().all(|byte| byte.is_ascii_hexdigit())
        && !trace_id.bytes().all(|byte| byte == b'0')
}

fn prompt_turn_id(payload: &Value) -> Option<String> {
    for key in ["uuid", "id", "requestId", "request_id"] {
        if let Some(value) = payload.get(key).and_then(Value::as_str) {
            if !value.trim().is_empty() {
                return Some(value.to_string());
            }
        }
    }
    if let Some(message) = payload.get("message") {
        for key in ["uuid", "id", "requestId", "request_id"] {
            if let Some(value) = message.get(key).and_then(Value::as_str) {
                if !value.trim().is_empty() {
                    return Some(value.to_string());
                }
            }
        }
    }
    None
}

fn stable_prompt_turn_id(session_id: &str, payload: &Value, created_at: &str) -> String {
    let prompt = prompt_text(payload).unwrap_or_default();
    let timestamp = payload
        .get("timestamp")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let mut hasher = Sha256::new();
    for value in [
        "cchooks-turn-seed-v1",
        session_id,
        prompt,
        timestamp,
        created_at,
    ] {
        hasher.update(value.len().to_string().as_bytes());
        hasher.update(b":");
        hasher.update(value.as_bytes());
        hasher.update(b"|");
    }
    let digest = hasher.finalize();
    let suffix = digest
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    format!("seed-{suffix}")
}

fn prompt_text(payload: &Value) -> Option<&str> {
    payload
        .get("prompt")
        .or_else(|| {
            payload
                .get("message")
                .and_then(|message| message.get("content"))
        })
        .and_then(Value::as_str)
}

pub(crate) fn prompt_digest(prompt: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(b"cchooks-turn-prompt-v1\0");
    hasher.update((prompt.len() as u64).to_be_bytes());
    hasher.update(prompt.as_bytes());
    hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn store(dir: &tempfile::TempDir) -> DomainStateStore {
        DomainStateStore::new(
            dir.path().join("state_turn.json"),
            None,
            "session",
            StateDomain::Turn,
            100,
        )
    }

    fn seed(trace_id: &str, traceparent: Option<&str>) -> Value {
        json!({
            "schema_version": TURN_SEED_SCHEMA_VERSION,
            "turn_id": "turn-1",
            "trace_id": trace_id,
            "upstream_traceparent": traceparent,
            "created_at": "2026-08-24T00:00:00Z"
        })
    }

    #[test]
    fn reads_valid_current_seed() {
        let dir = tempfile::tempdir().unwrap();
        let store = store(&dir);
        let trace_id = "41049c989ea3510bafe6d446672d7b1c";
        let traceparent = format!("00-{trace_id}-61c42769ba435f72-01");
        store
            .update(|state| {
                *state = seed(trace_id, Some(&traceparent));
                Ok(())
            })
            .unwrap();

        let actual = read_current_turn_seed_from_store(&store).unwrap().unwrap();
        assert_eq!(actual.trace_id, trace_id);
        assert_eq!(
            actual.upstream_traceparent.as_deref(),
            Some(traceparent.as_str())
        );
    }

    #[test]
    fn rejects_invalid_or_mismatched_upstream_traceparent() {
        let dir = tempfile::tempdir().unwrap();
        let store = store(&dir);
        for state in [
            seed("41049c989ea3510bafe6d446672d7b1c", Some("invalid")),
            seed(
                "41049c989ea3510bafe6d446672d7b1c",
                Some("00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-61c42769ba435f72-01"),
            ),
        ] {
            store
                .update(|current| {
                    *current = state.clone();
                    Ok(())
                })
                .unwrap();
            assert!(matches!(
                read_current_turn_seed_from_store(&store),
                Err(StateTxnError::Schema(_))
            ));
        }
    }

    #[test]
    fn missing_and_legacy_state_fail_open() {
        let dir = tempfile::tempdir().unwrap();
        let store = store(&dir);
        assert_eq!(read_current_turn_seed_from_store(&store).unwrap(), None);
        store
            .update(|state| {
                *state = json!({"current_trace_context": {"trace_id": "41049c989ea3510bafe6d446672d7b1c"}});
                Ok(())
            })
            .unwrap();
        assert_eq!(read_current_turn_seed_from_store(&store).unwrap(), None);
    }

    #[test]
    fn parent_snapshot_does_not_wait_for_a_contended_turn_lock() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous = crate::common::env::var("CCHOOKS_STATE_DIR").ok();
        std::env::set_var("CCHOOKS_STATE_DIR", dir.path().join("state"));
        let session_id = "session-contended-turn-seed";
        let store = DomainStateStore::for_session(session_id, StateDomain::Turn, 100).unwrap();
        let held = crate::hooks::langfuse::state::StateLock::acquire(store.state_file(), 100);
        assert!(held.is_acquired());

        let result = read_current_turn_seed(session_id);
        assert!(
            matches!(result, Err(StateTxnError::Busy { waited_ms: 0 })),
            "unexpected contended snapshot result: {result:?}"
        );

        drop(held);
        match previous {
            Some(value) => std::env::set_var("CCHOOKS_STATE_DIR", value),
            None => std::env::remove_var("CCHOOKS_STATE_DIR"),
        }
    }
}
