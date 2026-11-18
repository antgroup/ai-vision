//! Session and turn lifecycle management for dynamic data collection.
//!
//! Provides agent-agnostic session directory management, turn CRUD operations,
//! and state persistence. All path functions accept a `base_dir` parameter
//! so each agent hook can supply its own root directory.

use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

// ── Directory paths ────────────────────────────────────────────────

/// Session directory: `{base_dir}/{session_id}`
pub fn session_dir(base_dir: &Path, session_id: &str) -> PathBuf {
    base_dir.join(session_id)
}

/// Turn file path: `{base_dir}/{session_id}/dynamic_turns/dynamic_turn_{turn_id}.json`
pub fn turn_path(base_dir: &Path, session_id: &str, turn_id: i64) -> PathBuf {
    session_dir(base_dir, session_id)
        .join("dynamic_turns")
        .join(format!("dynamic_turn_{}.json", turn_id))
}

/// Initial pre-state file path
pub fn initial_pre_state_path(base_dir: &Path, session_id: &str) -> PathBuf {
    session_dir(base_dir, session_id).join("dynamic_initial_pre_state.txt")
}

/// Meta file path
pub fn meta_path(base_dir: &Path, session_id: &str) -> PathBuf {
    session_dir(base_dir, session_id).join("dynamic_meta.json")
}

/// Session state file path (per-session dynamic_state.json)
pub fn session_state_path(base_dir: &Path, session_id: &str) -> PathBuf {
    session_dir(base_dir, session_id).join("dynamic_state.json")
}

// ── JSON helpers ───────────────────────────────────────────────────

/// Read a JSON file, returning None on any error.
pub fn read_json(path: &Path) -> Option<Value> {
    let data = fs::read_to_string(path).ok()?;
    serde_json::from_str(&data).ok()
}

/// Write a JSON value to a file (pretty-printed), creating parent dirs as needed.
pub fn write_json(path: &Path, data: &Value) {
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let serialized = serde_json::to_string_pretty(data).unwrap_or_default();
    let _ = fs::write(path, serialized);
}

// ── Session state (per-session state.json) ─────────────────────────

/// Read session state JSON.
pub fn read_session_state(base_dir: &Path, session_id: &str) -> Option<Value> {
    read_json(&session_state_path(base_dir, session_id))
}

/// Write session state JSON.
pub fn write_session_state(base_dir: &Path, session_id: &str, data: &Value) {
    write_json(&session_state_path(base_dir, session_id), data);
}

// ── Meta ───────────────────────────────────────────────────────────

/// Read session meta JSON.
pub fn read_meta(base_dir: &Path, session_id: &str) -> Option<Value> {
    read_json(&meta_path(base_dir, session_id))
}

/// Write session meta JSON.
pub fn write_meta(base_dir: &Path, session_id: &str, data: &Value) {
    write_json(&meta_path(base_dir, session_id), data);
}

// ── Initial pre-state ──────────────────────────────────────────────

/// Write initial pre-state as plain text (for Plan Mode fallback).
pub fn write_initial_pre_state(base_dir: &Path, session_id: &str, state: &str) {
    let path = initial_pre_state_path(base_dir, session_id);
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let _ = fs::write(&path, state);
}

/// Read initial pre-state plain text.
pub fn read_initial_pre_state(base_dir: &Path, session_id: &str) -> Option<String> {
    fs::read_to_string(initial_pre_state_path(base_dir, session_id)).ok()
}

// ── Turn files ─────────────────────────────────────────────────────

/// Read a turn record.
pub fn read_turn(base_dir: &Path, session_id: &str, turn_id: i64) -> Option<Value> {
    read_json(&turn_path(base_dir, session_id, turn_id))
}

/// Write a turn record.
pub fn write_turn(base_dir: &Path, session_id: &str, turn_id: i64, data: &Value) {
    write_json(&turn_path(base_dir, session_id, turn_id), data);
}

/// Delete a turn file after successful upload (cleanup intermediate data).
pub fn delete_turn(base_dir: &Path, session_id: &str, turn_id: i64) {
    let _ = fs::remove_file(turn_path(base_dir, session_id, turn_id));
}

// ── Session lifecycle ──────────────────────────────────────────────

/// Initialize a new session directory with meta.json and state.json.
///
/// `now_fn` is a callback to get the current ISO 8601 timestamp,
/// allowing the caller to supply its own timestamp implementation.
pub fn init_session(
    base_dir: &Path,
    session_id: &str,
    cwd: &str,
    transcript_path: Option<&str>,
    remote_url: &str,
    branch: &str,
    base_commit: &str,
    now: &str,
) {
    let dir = session_dir(base_dir, session_id);
    let _ = fs::create_dir_all(dir.join("dynamic_turns"));

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
    write_meta(base_dir, session_id, &meta);
    write_session_state(base_dir, session_id, &json!({ "current_turn": 0 }));
}

/// Create a new turn record (called on UserPromptSubmit).
pub fn create_turn(
    base_dir: &Path,
    session_id: &str,
    turn_id: i64,
    query: &str,
    pre_state: Option<&str>,
    skip_reason: Option<&str>,
    now: &str,
) {
    let turn = json!({
        "turn_id": turn_id,
        "started_at": now,
        "ended_at": null,
        "query": query,
        "pre_state": pre_state,
        "post_state": null,
        "skip_reason": skip_reason,
    });
    write_turn(base_dir, session_id, turn_id, &turn);
    write_session_state(base_dir, session_id, &json!({ "current_turn": turn_id }));
}

/// Update an existing turn with post_state (called on Stop).
pub fn update_turn_post_state(
    base_dir: &Path,
    session_id: &str,
    turn_id: i64,
    post_state: Option<&str>,
    post_skip_reason: Option<&str>,
    now: &str,
) {
    let tp = turn_path(base_dir, session_id, turn_id);
    if !tp.exists() {
        return;
    }
    let Some(mut turn) = read_json(&tp) else {
        return;
    };

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

/// Create a synthetic turn for Plan Mode (when current_turn == 0 at Stop).
pub fn create_synthetic_turn(
    base_dir: &Path,
    session_id: &str,
    started_at: &str,
    query: &str,
    pre_state: &str,
    post_state: Option<&str>,
    post_skip_reason: Option<&str>,
    now: &str,
) {
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
    write_turn(base_dir, session_id, 1, &turn);
    write_session_state(base_dir, session_id, &json!({ "current_turn": 1 }));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_session_dir_structure() {
        let base = Path::new("/tmp/test");
        assert_eq!(session_dir(base, "s1"), PathBuf::from("/tmp/test/s1"));
        assert_eq!(
            turn_path(base, "s1", 3),
            PathBuf::from("/tmp/test/s1/dynamic_turns/dynamic_turn_3.json")
        );
        assert_eq!(
            meta_path(base, "s1"),
            PathBuf::from("/tmp/test/s1/dynamic_meta.json")
        );
    }

    #[test]
    fn test_init_session_and_read() {
        let tmp = tempfile::tempdir().unwrap();
        let base = tmp.path();
        init_session(
            base,
            "sess-1",
            "/cwd",
            Some("/t.jsonl"),
            "url",
            "main",
            "abc",
            "2026-01-01T00:00:00Z",
        );

        let meta = read_meta(base, "sess-1").unwrap();
        assert_eq!(meta["session_id"], "sess-1");
        assert_eq!(meta["git"]["branch"], "main");

        let state = read_session_state(base, "sess-1").unwrap();
        assert_eq!(state["current_turn"], 0);
    }

    #[test]
    fn test_create_and_read_turn() {
        let tmp = tempfile::tempdir().unwrap();
        let base = tmp.path();
        init_session(base, "s1", "/cwd", None, "", "", "", "now");
        create_turn(base, "s1", 1, "hello", Some("diff"), None, "now");

        let turn = read_turn(base, "s1", 1).unwrap();
        assert_eq!(turn["query"], "hello");
        assert_eq!(turn["pre_state"], "diff");
        assert!(turn["post_state"].is_null());

        let state = read_session_state(base, "s1").unwrap();
        assert_eq!(state["current_turn"], 1);
    }

    #[test]
    fn test_update_turn_post_state() {
        let tmp = tempfile::tempdir().unwrap();
        let base = tmp.path();
        init_session(base, "s1", "/cwd", None, "", "", "", "now");
        create_turn(base, "s1", 1, "q", Some("pre"), None, "t1");
        update_turn_post_state(base, "s1", 1, Some("post"), None, "t2");

        let turn = read_turn(base, "s1", 1).unwrap();
        assert_eq!(turn["post_state"], "post");
        assert_eq!(turn["ended_at"], "t2");
    }

    #[test]
    fn test_synthetic_turn() {
        let tmp = tempfile::tempdir().unwrap();
        let base = tmp.path();
        init_session(base, "s1", "/cwd", None, "", "", "", "now");
        create_synthetic_turn(
            base,
            "s1",
            "start",
            "query",
            "pre",
            Some("post"),
            None,
            "end",
        );

        let turn = read_turn(base, "s1", 1).unwrap();
        assert_eq!(turn["synthetic"], true);
        assert_eq!(turn["query"], "query");
    }

    #[test]
    fn test_delete_turn() {
        let tmp = tempfile::tempdir().unwrap();
        let base = tmp.path();
        init_session(base, "s1", "/cwd", None, "", "", "", "now");
        create_turn(base, "s1", 1, "q", None, None, "now");
        assert!(read_turn(base, "s1", 1).is_some());
        delete_turn(base, "s1", 1);
        assert!(read_turn(base, "s1", 1).is_none());
    }

    #[test]
    fn test_initial_pre_state() {
        let tmp = tempfile::tempdir().unwrap();
        let base = tmp.path();
        let dir = session_dir(base, "s1");
        let _ = fs::create_dir_all(&dir);
        write_initial_pre_state(base, "s1", "some diff");
        assert_eq!(read_initial_pre_state(base, "s1").unwrap(), "some diff");
    }
}
