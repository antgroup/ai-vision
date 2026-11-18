use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

/// Root directory for dynamic sessions: {state}/dynamic_sessions
fn data_root() -> PathBuf {
    crate::common::dirs::dynamic_sessions_dir()
}

/// Session directory: DATA_ROOT/{session_id}
pub fn session_dir(session_id: &str) -> PathBuf {
    let session_key = crate::common::dirs::session_state_key(session_id)
        .expect("dynamic session state requires a non-empty session_id");
    data_root().join(session_key)
}

/// Turn file path: DATA_ROOT/{session_id}/dynamic_turns/dynamic_turn_{turn_id}.json
fn turn_path(session_id: &str, turn_id: i64) -> PathBuf {
    session_dir(session_id)
        .join("dynamic_turns")
        .join(format!("dynamic_turn_{}.json", turn_id))
}

/// Initial pre-state file path
fn initial_pre_state_path(session_id: &str) -> PathBuf {
    session_dir(session_id).join("dynamic_initial_pre_state.txt")
}

/// Meta file path
fn meta_path(session_id: &str) -> PathBuf {
    session_dir(session_id).join("dynamic_meta.json")
}

/// State file path (per-session dynamic_state.json, not the global state)
fn session_state_path(session_id: &str) -> PathBuf {
    session_dir(session_id).join("dynamic_state.json")
}

// --- JSON helpers ---

fn read_json(path: &Path) -> Option<Value> {
    let data = fs::read_to_string(path).ok()?;
    serde_json::from_str(&data).ok()
}

fn write_json(path: &Path, data: &Value) {
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let serialized = serde_json::to_string_pretty(data).unwrap_or_default();
    let _ = fs::write(path, serialized);
}

// --- Session state (per-session state.json) ---

pub fn read_session_state(session_id: &str) -> Option<Value> {
    read_json(&session_state_path(session_id))
}

pub fn write_session_state(session_id: &str, data: &Value) {
    write_json(&session_state_path(session_id), data);
}

// --- Meta ---

pub fn read_meta(session_id: &str) -> Option<Value> {
    read_json(&meta_path(session_id))
}

pub fn write_meta(session_id: &str, data: &Value) {
    write_json(&meta_path(session_id), data);
}

// --- Initial pre-state ---

pub fn write_initial_pre_state(session_id: &str, state: &str) {
    let path = initial_pre_state_path(session_id);
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let _ = fs::write(&path, state);
}

pub fn read_initial_pre_state(session_id: &str) -> Option<String> {
    fs::read_to_string(initial_pre_state_path(session_id)).ok()
}

// --- Turn files ---

pub fn read_turn(session_id: &str, turn_id: i64) -> Option<Value> {
    read_json(&turn_path(session_id, turn_id))
}

pub fn write_turn(session_id: &str, turn_id: i64, data: &Value) {
    write_json(&turn_path(session_id, turn_id), data);
}

/// Delete a turn file after successful upload (cleanup intermediate data).
pub fn delete_turn(session_id: &str, turn_id: i64) {
    let _ = fs::remove_file(turn_path(session_id, turn_id));
}

/// Initialize a new session directory with meta.json and state.json
pub fn init_session(
    session_id: &str,
    cwd: &str,
    transcript_path: Option<&str>,
    remote_url: &str,
    branch: &str,
    base_commit: &str,
) {
    let dir = session_dir(session_id);
    let _ = fs::create_dir_all(dir.join("dynamic_turns"));

    let now = crate::common::logger::now_iso8601_pub();
    let meta = json!({
        "session_id": session_id,
        "started_at": now,
        "cwd": cwd,
        "transcript_path": transcript_path,
        "git": {
            "remote_url": remote_url,
            "branch": branch,
            "base_commit": base_commit,
        },
    });
    write_meta(session_id, &meta);
    write_session_state(session_id, &json!({ "current_turn": 0 }));
}

/// Create a new turn record (called on UserPromptSubmit)
pub fn create_turn(
    session_id: &str,
    turn_id: i64,
    query: &str,
    pre_state: Option<&str>,
    skip_reason: Option<&str>,
) {
    let now = crate::common::logger::now_iso8601_pub();
    let turn = json!({
        "turn_id": turn_id,
        "started_at": now,
        "ended_at": null,
        "query": query,
        "pre_state": pre_state,
        "post_state": null,
        "skip_reason": skip_reason,
    });
    write_turn(session_id, turn_id, &turn);
    write_session_state(session_id, &json!({ "current_turn": turn_id }));
}

/// Update an existing turn with post_state (called on Stop)
pub fn update_turn_post_state(
    session_id: &str,
    turn_id: i64,
    post_state: Option<&str>,
    post_skip_reason: Option<&str>,
) {
    let tp = turn_path(session_id, turn_id);
    if !tp.exists() {
        return;
    }
    let Some(mut turn) = read_json(&tp) else {
        return;
    };

    let now = crate::common::logger::now_iso8601_pub();
    turn["ended_at"] = json!(now);
    turn["post_state"] = match post_state {
        Some(s) => json!(s),
        None => json!(null),
    };

    if post_state.is_none() && turn.get("skip_reason").and_then(|v| v.as_str()).is_none() {
        turn["skip_reason"] = json!(post_skip_reason.unwrap_or("post_capture_failed"));
    }

    write_json(&tp, &turn);
}

/// Create a synthetic turn for Plan Mode (when current_turn == 0 at Stop)
pub fn create_synthetic_turn(
    session_id: &str,
    started_at: &str,
    query: &str,
    pre_state: &str,
    post_state: Option<&str>,
    post_skip_reason: Option<&str>,
) {
    let now = crate::common::logger::now_iso8601_pub();
    let turn = json!({
        "turn_id": 1,
        "started_at": started_at,
        "ended_at": now,
        "query": query,
        "pre_state": pre_state,
        "post_state": post_state,
        "skip_reason": if post_state.is_none() {
            Some(post_skip_reason.unwrap_or("post_capture_failed"))
        } else {
            None
        },
        "synthetic": true,
    });
    write_turn(session_id, 1, &turn);
    write_session_state(session_id, &json!({ "current_turn": 1 }));
}
