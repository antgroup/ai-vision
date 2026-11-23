use serde_json::json;

#[test]
fn test_state_roundtrip() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state_roundtrip.json");

    let original_state = json!({
        "session1": {
            "offset": 100,
            "buffer": "test content",
            "turn_count": 5,
            "updated": "2026-03-28T10:00:00Z"
        }
    });

    cdxhooks::hooks::langfuse::state::save_state(&path, &original_state)
        .expect("Failed to save state");
    let loaded_state = cdxhooks::hooks::langfuse::state::load_state(&path);
    assert_eq!(original_state, loaded_state);
}

#[test]
fn test_state_update_and_load() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state_update.json");
    let mut state = json!({});

    cdxhooks::hooks::langfuse::state::update_state_and_save(
        &path,
        &mut state,
        "session-abc123",
        500,
        "buffer content here",
        &[],
        &[],
        10,
        "2026-03-28T12:00:00Z",
        Some("/tmp/session-abc123.jsonl"),
    )
    .unwrap();

    let loaded_state = cdxhooks::hooks::langfuse::state::load_state(&path);
    assert_eq!(loaded_state["session-abc123"]["offset"], 500);
    assert_eq!(
        loaded_state["session-abc123"]["pending_messages"],
        json!([])
    );
    assert_eq!(loaded_state["session-abc123"]["posted_turn_ids"], json!([]));
    assert_eq!(loaded_state["session-abc123"]["turn_count"], 10);
}

#[test]
fn test_state_transaction_preserves_other_session_file_updates() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state_transaction.json");
    let mut stale_state = json!({
        "session-a": {
            "offset": 1
        }
    });
    let disk_state = json!({
        "session-a": {
            "offset": 1
        },
        "session-b": {
            "offset": 99
        }
    });
    cdxhooks::hooks::langfuse::state::save_state(&path, &disk_state).expect("Failed to save state");

    cdxhooks::hooks::langfuse::state::update_state_transaction(
        &path,
        &mut stale_state,
        |candidate| {
            candidate["session-a"]["offset"] = json!(2);
        },
    )
    .expect("Failed to update state transaction");

    let loaded_state = cdxhooks::hooks::langfuse::state::load_state(&path);
    assert_eq!(loaded_state["session-a"]["offset"], 2);
    assert_eq!(loaded_state["session-b"]["offset"], 99);
    assert_eq!(stale_state, loaded_state);
}

#[test]
fn fallible_state_transaction_does_not_save_rejected_candidate() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state_transaction_rejected.json");
    let original = json!({
        "session-a": {
            "offset": 1,
            "aliases": {}
        }
    });
    cdxhooks::hooks::langfuse::state::save_state(&path, &original).expect("Failed to save state");
    let mut state = json!({});

    let result = cdxhooks::hooks::langfuse::state::update_state_transaction_fallible(
        &path,
        &mut state,
        |candidate| {
            candidate["session-a"]["offset"] = json!(2);
            candidate["session-a"]["aliases"]["candidate"] = json!("canonical");
            Err("reject staged candidate".to_string())
        },
    );

    assert_eq!(result.unwrap_err(), "reject staged candidate");
    assert_eq!(
        cdxhooks::hooks::langfuse::state::load_state(&path),
        original
    );
    assert_eq!(state, json!({}));
}

#[test]
fn test_state_lock_and_corrupted_file() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state_corrupted.json");
    std::fs::write(&path, "{ broken json").unwrap();
    let loaded_state = cdxhooks::hooks::langfuse::state::load_state(&path);
    assert_eq!(loaded_state, json!({}));

    let _lock = cdxhooks::hooks::langfuse::state::StateLock::acquire(&path, 100);
    let reloaded = cdxhooks::hooks::langfuse::state::load_state(&path);
    assert_eq!(reloaded, json!({}));
}

#[test]
fn short_state_transaction_fails_fast_under_contention() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state_busy.json");
    let lock = cdxhooks::hooks::langfuse::state::StateLock::acquire(&path, 100);
    assert!(lock.is_acquired(), "fixture must hold the state lock");
    let started = std::time::Instant::now();
    let mut state = json!({});
    let result = cdxhooks::hooks::langfuse::state::update_state_transaction_with_timeout(
        &path,
        &mut state,
        25,
        |_| panic!("contended state transaction must not run its update"),
    );
    assert_eq!(result.unwrap_err(), "state lock unavailable after 25ms");
    let elapsed = started.elapsed();
    assert!(
        elapsed < std::time::Duration::from_millis(100),
        "contended state transaction exceeded its timing bound: {elapsed:?}"
    );
}

#[test]
fn checkpoint_rechecks_turn_id_inside_locked_transaction() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("checkpoint_race.json");
    let disk = json!({
        "session": {"posted_turn_ids": ["turn-1"], "turn_count": 1}
    });
    cdxhooks::hooks::langfuse::state::save_state(&path, &disk).unwrap();
    let mut stale = json!({"session": {"posted_turn_ids": [], "turn_count": 0}});
    let count = cdxhooks::hooks::langfuse::state::checkpoint_turn_export_success(
        &path,
        &mut stale,
        "session",
        "turn-1",
        "2026-07-22T00:00:00Z",
        None,
        &[],
        &[],
    )
    .unwrap();
    assert_eq!(count, 1);
    assert_eq!(stale["session"]["posted_turn_ids"], json!(["turn-1"]));
}

#[test]
fn terminal_failure_clears_automatic_retry_marker() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("terminal_failure.json");
    cdxhooks::hooks::langfuse::state::save_state(
        &path,
        &json!({"session": {"pending_export_reason": "otel_export_failed", "pending_subagent_ids": ["child"]}}),
    )
    .unwrap();
    cdxhooks::hooks::langfuse::state::mark_export_terminal_failure(
        &path,
        "session",
        "permanent failure",
    )
    .unwrap();
    let state = cdxhooks::hooks::langfuse::state::load_state(&path);
    assert!(state["session"].get("pending_export_reason").is_none());
    assert!(state["session"].get("pending_subagent_ids").is_none());
    assert_eq!(
        state["session"]["terminal_export_error"],
        "permanent failure"
    );
}
