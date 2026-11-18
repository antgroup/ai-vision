use std::fs;
use std::path::Path;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::common::dirs;
use crate::common::runtime_state::{
    publish_immutable_json, DomainStateStore, ImmutablePublish, StateDomain,
};

const SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ToolPhase {
    Pre,
    Post,
}

impl ToolPhase {
    fn from_event(name: &str) -> Option<Self> {
        match name {
            "PreToolUse" => Some(Self::Pre),
            "PostToolUse" => Some(Self::Post),
            _ => None,
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::Pre => "pre",
            Self::Post => "post",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ToolFact {
    pub schema_version: u32,
    pub event_id: String,
    pub event_name: String,
    pub session_id: String,
    pub tool_use_id: String,
    pub observed_at_unix_nano: String,
    pub payload: Value,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ReadyTool {
    pub tool_use_id: String,
    pub post_event_id: String,
    pub payload: Value,
}

enum ReadyDecision {
    Ready(ReadyTool),
    Waiting,
    Terminal,
}

/// Idempotent immutable publication derived from a durable queue record. The
/// session worker normally calls this while it owns the session; tests may
/// call it directly to exercise reconciliation. It never mutates an existing
/// fact.
pub fn publish_ingress_fact(payload: &Value) -> Result<Option<ImmutablePublish>, String> {
    let event_name = payload
        .get("hook_event_name")
        .and_then(Value::as_str)
        .unwrap_or("");
    let Some(phase) = ToolPhase::from_event(event_name) else {
        return Ok(None);
    };
    let session_id = crate::common::hook::extract_session_id(payload);
    let tool_use_id = extract_tool_use_id(payload);
    if session_id.is_empty() || tool_use_id.is_empty() {
        return Err("tool fact requires session_id and tool_use_id".to_string());
    }
    let event_id = stable_event_id(&session_id, &tool_use_id, phase, payload)?;
    let fact = ToolFact {
        schema_version: SCHEMA_VERSION,
        event_id,
        event_name: event_name.to_string(),
        session_id: session_id.clone(),
        tool_use_id: tool_use_id.clone(),
        observed_at_unix_nano: agentic_otel_common::now_nanos(),
        payload: payload.clone(),
    };
    let path = dirs::tool_event_file(&session_id, &tool_use_id, phase.as_str())
        .ok_or_else(|| "invalid tool fact path".to_string())?;
    let value = serde_json::to_value(&fact).map_err(|error| error.to_string())?;
    match publish_immutable_json(&path, &value) {
        Ok(result) => Ok(Some(result)),
        Err(error) => {
            let existing = read_fact_path(&path)?;
            if existing
                .as_ref()
                .is_some_and(|existing| existing.event_id == fact.event_id)
            {
                Ok(Some(ImmutablePublish::ExistingIdentical))
            } else {
                Err(error)
            }
        }
    }
}

/// Reconcile one tool while the caller owns the session worker lock.
///
/// A non-terminal `ready` record is deliberately retryable. If the worker is
/// killed after remote acceptance but before `mark_terminal`, the next session
/// worker sends it again (the documented at-least-once duplicate window).
pub fn begin_ready(session_id: &str, tool_use_id: &str) -> Result<Option<ReadyTool>, String> {
    let pre = read_fact(session_id, tool_use_id, ToolPhase::Pre)?;
    let post = read_fact(session_id, tool_use_id, ToolPhase::Post)?;
    let store = DomainStateStore::for_session(session_id, StateDomain::Tools)?;
    let (decision, _changed) = store.update(|state| {
        let entry = tool_entry_mut(state, tool_use_id);
        if entry
            .get("status")
            .and_then(Value::as_str)
            .is_some_and(is_terminal_status)
        {
            return Ok(ReadyDecision::Terminal);
        }
        let Some(post) = post.as_ref() else {
            return Ok(ReadyDecision::Waiting);
        };
        if pre.is_none() {
            entry["status"] = Value::String("waiting_for_pre".to_string());
            entry["post_event_id"] = Value::String(post.event_id.clone());
            return Ok(ReadyDecision::Waiting);
        }
        let pre = pre.expect("checked above");
        entry["status"] = Value::String("ready".to_string());
        entry["pre_event_id"] = Value::String(pre.event_id);
        entry["post_event_id"] = Value::String(post.event_id.clone());
        entry["attempts"] = json!(entry
            .get("attempts")
            .and_then(Value::as_u64)
            .unwrap_or(0)
            .saturating_add(1));
        entry["last_attempt_at_unix_nano"] = Value::String(agentic_otel_common::now_nanos());
        Ok(ReadyDecision::Ready(ReadyTool {
            tool_use_id: tool_use_id.to_string(),
            post_event_id: post.event_id.clone(),
            payload: post.payload.clone(),
        }))
    })?;
    match decision {
        ReadyDecision::Ready(ready) => Ok(Some(ready)),
        ReadyDecision::Waiting => Ok(None),
        ReadyDecision::Terminal => {
            remove_fact(session_id, tool_use_id, ToolPhase::Pre);
            remove_fact(session_id, tool_use_id, ToolPhase::Post);
            Ok(None)
        }
    }
}

/// Commit the terminal tombstone and remove immutable ingress facts. The
/// domain tombstone remains authoritative for duplicate suppression.
pub fn mark_terminal(session_id: &str, ready: &ReadyTool) -> Result<(), String> {
    let store = DomainStateStore::for_session(session_id, StateDomain::Tools)?;
    store.update(|state| {
        let entry = tool_entry_mut(state, &ready.tool_use_id);
        entry["status"] = Value::String("terminal".to_string());
        entry["terminal_event_id"] = Value::String(ready.post_event_id.clone());
        entry["completed_at_unix_nano"] = Value::String(agentic_otel_common::now_nanos());
        entry
            .as_object_mut()
            .expect("tool entry object")
            .remove("last_error");
        Ok(())
    })?;
    remove_fact(session_id, &ready.tool_use_id, ToolPhase::Pre);
    remove_fact(session_id, &ready.tool_use_id, ToolPhase::Post);
    Ok(())
}

pub fn mark_retryable_error(
    session_id: &str,
    ready: &ReadyTool,
    error: &str,
) -> Result<(), String> {
    let store = DomainStateStore::for_session(session_id, StateDomain::Tools)?;
    store
        .update(|state| {
            let entry = tool_entry_mut(state, &ready.tool_use_id);
            entry["status"] = Value::String("ready".to_string());
            entry["last_error"] = Value::String(error.to_string());
            Ok(())
        })
        .map(|_| ())
}

/// Scan immutable tool directories for Stop/lost-wakeup recovery. The caller
/// decides the processing cap; this function has no claims or leases.
pub fn pending_tool_ids(session_id: &str, limit: usize) -> Result<Vec<String>, String> {
    let mut ready = Vec::new();
    let mut incomplete = Vec::new();
    for facts in scan_fact_sets(session_id)? {
        let target = if facts.pre.is_some() && facts.post.is_some() {
            &mut ready
        } else {
            &mut incomplete
        };
        if let Some(fact) = facts.post.or(facts.pre) {
            target.push(fact.tool_use_id);
        }
    }
    ready.sort();
    ready.dedup();
    incomplete.sort();
    incomplete.dedup();
    ready.extend(incomplete);
    ready.truncate(limit);
    Ok(ready)
}

/// Return one durable ready Post payload without mutating reconciliation
/// state. Operator recovery uses this only to wake the normal detached worker;
/// the worker remains the sole owner of state convergence.
pub fn first_ready_payload(session_id: &str) -> Result<Option<Value>, String> {
    for facts in scan_fact_sets(session_id)? {
        if facts.pre.is_some() {
            if let Some(post) = facts.post {
                return Ok(Some(post.payload));
            }
        }
    }
    Ok(None)
}

/// Remove ingress payloads which have not converged for `max_age`. A compact
/// tombstone remains in the Tools domain so a late duplicate cannot revive an
/// expired event. The caller owns the session worker lock.
pub fn expire_orphan_facts(session_id: &str, max_age: Duration) -> Result<usize, String> {
    let now = agentic_otel_common::now_nanos()
        .parse::<u128>()
        .map_err(|error| format!("invalid current timestamp: {error}"))?;
    let cutoff = now.saturating_sub(max_age.as_nanos());
    let expired = scan_fact_sets(session_id)?
        .into_iter()
        .filter_map(|facts| {
            if facts.pre.is_some() && facts.post.is_some() {
                return None;
            }
            let observed = facts
                .pre
                .as_ref()
                .into_iter()
                .chain(facts.post.as_ref())
                .filter_map(|fact| fact.observed_at_unix_nano.parse::<u128>().ok())
                .max()?;
            (observed <= cutoff).then_some(facts)
        })
        .collect::<Vec<_>>();
    settle_fact_sets(session_id, expired, "expired", "expired_at_unix_nano")
}

/// A completed Stop finalizer has made the transcript authoritative. Remove
/// all remaining ingress payloads, including incomplete Post-before-Pre
/// orphans, while retaining payload-free tombstones.
pub fn finalize_session_facts(session_id: &str) -> Result<usize, String> {
    settle_fact_sets(
        session_id,
        scan_fact_sets(session_id)?,
        "finalizer_accounted",
        "completed_at_unix_nano",
    )
}

#[derive(Debug)]
struct FactSet {
    dir: std::path::PathBuf,
    pre: Option<ToolFact>,
    post: Option<ToolFact>,
}

fn scan_fact_sets(session_id: &str) -> Result<Vec<FactSet>, String> {
    let root =
        dirs::session_state_dir(session_id).ok_or_else(|| "invalid session id".to_string())?;
    let entries = match fs::read_dir(root) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error.to_string()),
    };
    let mut dirs = entries
        .flatten()
        .filter(|entry| entry.file_type().ok().is_some_and(|kind| kind.is_dir()))
        .filter(|entry| entry.file_name().to_string_lossy().starts_with("tool_"))
        .map(|entry| entry.path())
        .collect::<Vec<_>>();
    dirs.sort();
    dirs.into_iter()
        .map(|dir| {
            Ok(FactSet {
                pre: read_fact_path(&dir.join("pre.json"))?,
                post: read_fact_path(&dir.join("post.json"))?,
                dir,
            })
        })
        .collect()
}

fn settle_fact_sets(
    session_id: &str,
    facts: Vec<FactSet>,
    status: &str,
    timestamp_field: &str,
) -> Result<usize, String> {
    if facts.is_empty() {
        return Ok(0);
    }
    let mut settled = facts
        .into_iter()
        .filter_map(|facts| {
            let tool_use_id = facts
                .post
                .as_ref()
                .or(facts.pre.as_ref())?
                .tool_use_id
                .clone();
            Some((tool_use_id, facts.dir))
        })
        .collect::<Vec<_>>();
    settled.sort_by(|left, right| left.0.cmp(&right.0));
    settled.dedup_by(|left, right| left.0 == right.0);
    if settled.is_empty() {
        return Ok(0);
    }
    let completed_at = agentic_otel_common::now_nanos();
    let store = DomainStateStore::for_session(session_id, StateDomain::Tools)?;
    store.update(|state| {
        for (tool_use_id, _) in &settled {
            let entry = tool_entry_mut(state, tool_use_id);
            entry["status"] = Value::String(status.to_string());
            entry[timestamp_field] = Value::String(completed_at.clone());
            entry
                .as_object_mut()
                .expect("tool entry object")
                .remove("last_error");
        }
        Ok(())
    })?;
    for (_, dir) in &settled {
        let _ = fs::remove_file(dir.join("pre.json"));
        let _ = fs::remove_file(dir.join("post.json"));
        let _ = fs::remove_dir(dir);
    }
    Ok(settled.len())
}

fn is_terminal_status(status: &str) -> bool {
    matches!(status, "terminal" | "expired" | "finalizer_accounted")
}

fn stable_event_id(
    session_id: &str,
    tool_use_id: &str,
    phase: ToolPhase,
    payload: &Value,
) -> Result<String, String> {
    let bytes = serde_json::to_vec(payload).map_err(|error| error.to_string())?;
    let mut hash = Sha256::new();
    hash.update(b"cdxhooks-tool-event-v1\0");
    hash.update(session_id.as_bytes());
    hash.update(b"\0");
    hash.update(tool_use_id.as_bytes());
    hash.update(b"\0");
    hash.update(phase.as_str().as_bytes());
    hash.update(b"\0");
    hash.update(bytes);
    Ok(hash
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

fn extract_tool_use_id(payload: &Value) -> String {
    payload
        .get("tool_use_id")
        .or_else(|| payload.get("toolUseId"))
        .or_else(|| payload.get("call_id"))
        .or_else(|| payload.get("callId"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string()
}

fn read_fact(
    session_id: &str,
    tool_use_id: &str,
    phase: ToolPhase,
) -> Result<Option<ToolFact>, String> {
    let path = dirs::tool_event_file(session_id, tool_use_id, phase.as_str())
        .ok_or_else(|| "invalid tool fact path".to_string())?;
    read_fact_path(&path)
}

fn read_fact_path(path: &Path) -> Result<Option<ToolFact>, String> {
    let text = match fs::read_to_string(path) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("{}: {error}", path.display())),
    };
    serde_json::from_str(&text)
        .map(Some)
        .map_err(|error| format!("{}: {error}", path.display()))
}

fn remove_fact(session_id: &str, tool_use_id: &str, phase: ToolPhase) {
    if let Some(path) = dirs::tool_event_file(session_id, tool_use_id, phase.as_str()) {
        let _ = fs::remove_file(path);
    }
    if let Some(dir) = dirs::tool_event_dir(session_id, tool_use_id) {
        let _ = fs::remove_dir(dir);
    }
}

fn tool_entry_mut<'a>(state: &'a mut Value, tool_use_id: &str) -> &'a mut Value {
    if !state.is_object() {
        *state = json!({});
    }
    if !state.get("events").is_some_and(Value::is_object) {
        state["events"] = json!({});
    }
    if !state["events"]
        .get(tool_use_id)
        .is_some_and(Value::is_object)
    {
        state["events"][tool_use_id] = json!({"tool_use_id": tool_use_id});
    }
    &mut state["events"][tool_use_id]
}
