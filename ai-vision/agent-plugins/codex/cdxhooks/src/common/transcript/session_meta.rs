//! session meta ownership module.

use super::{BufRead, BufReader, File, Path, TurnTrigger, Value};

/// Sub-agent origin info parsed from `session_meta`.
///
/// Codex format: `source.subagent.thread_spawn` (key `"subagent"`, no underscore).
/// Legacy/compat: `source.sub_agent.thread_spawn` (key `"sub_agent"`).
/// Fallback: `thread_source == "subagent"` at payload level with top-level `agent_nickname`/`agent_role`.
///
/// When a Codex session is itself a sub-agent (spawned by a parent thread),
/// the session metadata records the parent relationship.
#[derive(Debug, Clone, PartialEq)]
pub struct SubagentSourceInfo {
    pub parent_thread_id: String,
    pub depth: i32,
    pub agent_path: Option<String>,
    pub agent_nickname: Option<String>,
    pub agent_role: Option<String>,
    pub protocol_version: Option<String>,
}

/// Identity fields parsed from a transcript's `session_meta` line.
///
/// Both replay and hook paths use this to detect whether a transcript belongs
/// to a sub-agent (`parent_thread_id.is_some()`) and to reconcile cases where
/// the hook payload's `session_id` disagrees with the transcript's own id.
#[derive(Debug, Clone, Default)]
pub struct SessionMetaIds {
    /// Legacy `payload.thread_id` field; preserved for callers that need it.
    #[allow(dead_code)]
    pub thread_id: Option<String>,
    pub session_id: Option<String>,
    /// If this session is a subagent, the parent's session/thread ID.
    /// From `source.subagent.thread_spawn.parent_thread_id` (codex format)
    /// or `source.sub_agent.thread_spawn.parent_thread_id` (legacy format).
    pub parent_thread_id: Option<String>,
    pub agent_path: Option<String>,
}

/// Parse `thread_id`, `session_id`, and `parent_thread_id` from a transcript's
/// first `session_meta` line. Returns an all-`None` `SessionMetaIds` if the
/// content has no `session_meta` entry.
pub fn extract_session_meta_ids(content: &str) -> SessionMetaIds {
    for line in content.lines() {
        let Ok(v) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if let Some(meta) = extract_session_meta_ids_from_value(&v) {
            return meta;
        }
    }
    SessionMetaIds::default()
}

/// Parse identity fields from the first `session_meta` entry in a transcript
/// file without reading the whole JSONL. Codex transcripts put `session_meta`
/// at the top, but this scans a small prefix defensively.
pub fn extract_session_meta_ids_from_path(path: &Path) -> SessionMetaIds {
    try_extract_session_meta_ids_from_path(path).unwrap_or_default()
}

/// Fallible variant for callers where an unavailable transcript must remain
/// retryable instead of being treated like a readable transcript without
/// `session_meta`.
pub fn try_extract_session_meta_ids_from_path(path: &Path) -> std::io::Result<SessionMetaIds> {
    let file = File::open(path)?;
    let reader = BufReader::new(file);
    for line in reader.lines().take(32) {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let Ok(v) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        if let Some(meta) = extract_session_meta_ids_from_value(&v) {
            return Ok(meta);
        }
    }
    Ok(SessionMetaIds::default())
}

pub fn resolve_turn_trigger(
    per_turn_direct: Option<TurnTrigger>,
    session_snapshot: TurnTrigger,
) -> TurnTrigger {
    per_turn_direct.unwrap_or(session_snapshot)
}

pub(super) fn resolve_session_origin_turn_trigger(payload: &Value) -> TurnTrigger {
    if has_structured_subagent_thread_spawn(payload) {
        return TurnTrigger::Subagent;
    }

    match payload.get("thread_source").and_then(Value::as_str) {
        Some("subagent") => TurnTrigger::Subagent,
        Some("user") => resolve_user_thread_source(payload),
        Some(_) | None => TurnTrigger::Unknown,
    }
}

fn resolve_user_thread_source(payload: &Value) -> TurnTrigger {
    match payload.get("source") {
        None | Some(Value::Null) => TurnTrigger::Interactive,
        Some(Value::String(source)) if is_non_conflicting_user_source(source) => {
            TurnTrigger::Interactive
        }
        Some(Value::String(source)) if source == "exec" => TurnTrigger::Unknown,
        Some(_) => TurnTrigger::Unknown,
    }
}

fn is_non_conflicting_user_source(source: &str) -> bool {
    matches!(source, "" | "cli" | "vscode" | "codex-tui" | "codex-cli")
}

fn subagent_thread_spawn(source: &Value) -> Option<&Value> {
    let source = source.as_object()?;
    source
        .get("subagent")
        .and_then(|subagent| subagent.get("thread_spawn"))
        .filter(|thread_spawn| thread_spawn.is_object())
        .or_else(|| {
            source
                .get("sub_agent")
                .and_then(|subagent| subagent.get("thread_spawn"))
                .filter(|thread_spawn| thread_spawn.is_object())
        })
}

fn has_structured_subagent_thread_spawn(payload: &Value) -> bool {
    payload
        .get("source")
        .and_then(subagent_thread_spawn)
        .is_some()
}

pub(super) fn extract_session_meta_ids_from_value(v: &Value) -> Option<SessionMetaIds> {
    if v.get("type").and_then(|t| t.as_str()) != Some("session_meta") {
        return None;
    }
    let payload = &v["payload"];
    let thread_id = payload["thread_id"].as_str().map(String::from);
    // `payload.id` is the transcript/thread's own id in current Codex Desktop.
    // Some child transcripts also carry `payload.session_id` with the *parent*
    // session id for compatibility, so prefer `id` and only fall back to
    // `session_id` for older transcripts that lacked `id`.
    let session_id = payload["id"]
        .as_str()
        .or_else(|| payload["session_id"].as_str())
        .map(String::from);
    let parent_thread_id = payload
        .get("source")
        .and_then(subagent_thread_spawn)
        .and_then(|ts| ts.get("parent_thread_id"))
        .and_then(|v| v.as_str())
        .or_else(|| payload.get("parent_thread_id").and_then(|v| v.as_str()))
        .map(String::from);
    let agent_path = payload
        .get("source")
        .and_then(subagent_thread_spawn)
        .and_then(|ts| ts.get("agent_path"))
        .and_then(Value::as_str)
        .or_else(|| payload.get("agent_path").and_then(Value::as_str))
        .map(str::to_string);
    Some(SessionMetaIds {
        thread_id,
        session_id,
        parent_thread_id,
        agent_path,
    })
}
