use serde_json::json;

#[test]
fn test_state_roundtrip() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state_roundtrip.json");

    // Create initial state
    let original_state = json!({
        "session1": {
            "offset": 100,
            "buffer": "test content",
            "turn_count": 5,
            "updated": "2026-03-28T10:00:00Z"
        },
        "session2": {
            "offset": 200,
            "buffer": "another session",
            "turn_count": 3,
            "updated": "2026-03-28T11:00:00Z"
        }
    });

    // Save state to file
    cchooks::hooks::langfuse::state::save_state(&path, &original_state)
        .expect("Failed to save state");

    // Load state back
    let loaded_state = agentic_otel_common::state::load_state(&path);

    // Verify consistency
    assert_eq!(
        original_state, loaded_state,
        "Loaded state should match original state"
    );

    // Verify file exists and contains valid JSON
    assert!(path.exists(), "State file should exist");
    let file_content = std::fs::read_to_string(&path).expect("Failed to read state file");
    let parsed: serde_json::Value =
        serde_json::from_str(&file_content).expect("File should contain valid JSON");
    assert_eq!(parsed, original_state);
}

#[test]
fn test_state_update_and_save() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state_update.json");

    // Start with empty state
    let mut state = json!({});

    // Update session state
    cchooks::hooks::langfuse::state::update_state_and_save(
        &path,
        &mut state,
        "session-abc123",
        500,
        "buffer content here",
        10,
        "2026-03-28T12:00:00Z",
    );

    // Verify in-memory state was updated
    let session = state
        .get("session-abc123")
        .expect("Session should exist in memory");
    assert_eq!(session["offset"], 500);
    assert_eq!(session["buffer"], "buffer content here");
    assert_eq!(session["turn_count"], 10);
    assert_eq!(session["updated"], "2026-03-28T12:00:00Z");

    // Verify persisted to disk
    let loaded_state = agentic_otel_common::state::load_state(&path);
    assert_eq!(
        loaded_state, state,
        "Persisted state should match in-memory state"
    );

    // Update another session
    cchooks::hooks::langfuse::state::update_state_and_save(
        &path,
        &mut state,
        "session-xyz789",
        1000,
        "another buffer",
        20,
        "2026-03-28T13:00:00Z",
    );

    // Verify both sessions exist in persisted state
    let final_state = agentic_otel_common::state::load_state(&path);
    assert_eq!(final_state["session-abc123"]["offset"], 500);
    assert_eq!(final_state["session-xyz789"]["offset"], 1000);
}

#[test]
fn test_session_state_isolation() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state_isolation.json");

    let mut state = json!({});

    // Create multiple sessions
    cchooks::hooks::langfuse::state::update_state_and_save(
        &path,
        &mut state,
        "session-1",
        100,
        "buffer-1",
        5,
        "2026-03-28T10:00:00Z",
    );

    cchooks::hooks::langfuse::state::update_state_and_save(
        &path,
        &mut state,
        "session-2",
        200,
        "buffer-2",
        10,
        "2026-03-28T11:00:00Z",
    );

    cchooks::hooks::langfuse::state::update_state_and_save(
        &path,
        &mut state,
        "session-3",
        300,
        "buffer-3",
        15,
        "2026-03-28T12:00:00Z",
    );

    // Load state and verify isolation
    let loaded_state = agentic_otel_common::state::load_state(&path);

    // Verify each session has its own independent state
    let sess1 = cchooks::hooks::langfuse::state::get_session_state(&loaded_state, "session-1")
        .expect("Session-1 should exist");
    assert_eq!(sess1["offset"], 100);
    assert_eq!(sess1["buffer"], "buffer-1");
    assert_eq!(sess1["turn_count"], 5);

    let sess2 = cchooks::hooks::langfuse::state::get_session_state(&loaded_state, "session-2")
        .expect("Session-2 should exist");
    assert_eq!(sess2["offset"], 200);
    assert_eq!(sess2["buffer"], "buffer-2");
    assert_eq!(sess2["turn_count"], 10);

    let sess3 = cchooks::hooks::langfuse::state::get_session_state(&loaded_state, "session-3")
        .expect("Session-3 should exist");
    assert_eq!(sess3["offset"], 300);
    assert_eq!(sess3["buffer"], "buffer-3");
    assert_eq!(sess3["turn_count"], 15);

    // Verify non-existent session returns None
    let sess4 = cchooks::hooks::langfuse::state::get_session_state(&loaded_state, "session-4");
    assert!(sess4.is_none(), "Non-existent session should return None");
}

#[test]
fn test_vcs_snapshots_are_isolated_by_session_shard_and_cwd() {
    use cchooks::hooks::langfuse::state::{
        get_vcs_for_cwd_from_state, replace_vcs_snapshot, SessionStateStore, VcsInfo,
    };

    let dir = tempfile::tempdir().unwrap();
    let fixtures = [
        (
            "session-a",
            "/repo-a",
            "git@git.example.com:team/a.git",
            "branch-a",
        ),
        (
            "session-b",
            "/repo-b",
            "git@git.example.com:team/b.git",
            "branch-b",
        ),
    ];

    for (session_id, cwd, remote_url, branch) in fixtures {
        let state_file = dir.path().join(format!("{session_id}.json"));
        SessionStateStore::new(state_file.clone(), session_id, 100)
            .update_session(|session| {
                replace_vcs_snapshot(
                    session,
                    &VcsInfo {
                        remote_url: remote_url.to_string(),
                        branch: branch.to_string(),
                        cwd: cwd.to_string(),
                    },
                );
                Ok(())
            })
            .unwrap();

        let state = agentic_otel_common::state::load_state(&state_file);
        let vcs = get_vcs_for_cwd_from_state(&state, session_id, cwd).unwrap();
        assert_eq!(vcs.remote_url, remote_url);
        assert_eq!(vcs.branch, branch);
        assert!(get_vcs_for_cwd_from_state(&state, session_id, "/other").is_none());
        let other_session = if session_id == "session-a" {
            "session-b"
        } else {
            "session-a"
        };
        assert!(get_vcs_for_cwd_from_state(&state, other_session, cwd).is_none());
    }
}

#[cfg(unix)]
#[test]
fn test_state_lock_timeout_is_explicit_and_does_not_mutate() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state_concurrent.json");

    // Create initial state
    let initial_state = json!({
        "session1": {"offset": 100, "buffer": "test", "turn_count": 1, "updated": "2026-03-28T10:00:00Z"}
    });
    cchooks::hooks::langfuse::state::save_state(&path, &initial_state)
        .expect("Failed to save initial state");

    // Acquire first lock
    let lock1 = cchooks::hooks::langfuse::state::StateLock::acquire(&path, 1000);
    assert!(lock1.is_acquired(), "first lock must be acquired");

    // Try to acquire second lock (must time out while the first is held).
    let lock2 = cchooks::hooks::langfuse::state::StateLock::acquire(&path, 100);
    assert!(
        !lock2.is_acquired(),
        "second lock must report that ownership was not acquired"
    );

    // A caller that failed to acquire ownership must leave the file unchanged.
    let final_state = agentic_otel_common::state::load_state(&path);
    assert_eq!(final_state, initial_state);

    drop(lock2);
    drop(lock1);
}

#[cfg(unix)]
#[test]
fn test_lock_guarded_concurrent_updates_preserve_both_writes() {
    use std::sync::{Arc, Barrier};
    use std::thread;

    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state_no_lost_updates.json");
    cchooks::hooks::langfuse::state::save_state(&path, &json!({}))
        .expect("Failed to save initial state");

    let barrier = Arc::new(Barrier::new(3));
    let mut handles = Vec::new();
    for (session_id, offset) in [("session-a", 100), ("session-b", 200)] {
        let path = path.clone();
        let barrier = Arc::clone(&barrier);
        handles.push(thread::spawn(move || {
            barrier.wait();
            let state_lock = cchooks::hooks::langfuse::state::StateLock::acquire(&path, 2_000);
            assert!(state_lock.is_acquired(), "{session_id} must acquire lock");
            let mut state = agentic_otel_common::state::load_state(&path);
            state[session_id] = json!({"offset": offset});
            cchooks::hooks::langfuse::state::save_state(&path, &state)
                .expect("lock owner must save state");
        }));
    }

    barrier.wait();
    for handle in handles {
        handle.join().expect("state writer thread must finish");
    }

    let final_state = agentic_otel_common::state::load_state(&path);
    assert_eq!(final_state["session-a"]["offset"], 100);
    assert_eq!(final_state["session-b"]["offset"], 200);
}

#[cfg(unix)]
#[test]
fn test_parent_and_subagent_timing_survive_same_session_concurrency() {
    use std::sync::{Arc, Barrier};
    use std::thread;

    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("same_session_state.json");
    let barrier = Arc::new(Barrier::new(3));

    let parent_path = path.clone();
    let parent_barrier = Arc::clone(&barrier);
    let parent = thread::spawn(move || {
        let store = cchooks::hooks::langfuse::state::SessionStateStore::new(
            parent_path,
            "session-1",
            2_000,
        );
        parent_barrier.wait();
        store
            .update_session(|session| {
                session["timing"]["parent-tool"] =
                    json!({"start_ns": "1", "end_ns": "2", "ok": true});
                Ok(())
            })
            .unwrap();
    });

    let subagent_path = path.clone();
    let subagent_barrier = Arc::clone(&barrier);
    let subagent = thread::spawn(move || {
        let store = cchooks::hooks::langfuse::state::SessionStateStore::new(
            subagent_path,
            "session-1",
            2_000,
        );
        subagent_barrier.wait();
        store
            .update_session(|session| {
                session["subagent_timing"]["agent-1"]["timing"]["child-tool"] =
                    json!({"start_ns": "3", "end_ns": "4", "ok": true});
                Ok(())
            })
            .unwrap();
    });

    barrier.wait();
    parent.join().unwrap();
    subagent.join().unwrap();

    let state = agentic_otel_common::state::load_state(&path);
    assert_eq!(state["session-1"]["timing"]["parent-tool"]["start_ns"], "1");
    assert_eq!(
        state["session-1"]["subagent_timing"]["agent-1"]["timing"]["child-tool"]["start_ns"],
        "3"
    );
}

#[cfg(unix)]
#[test]
fn test_one_session_lock_does_not_block_another_session_shard() {
    let dir = tempfile::tempdir().unwrap();
    let key_a = cchooks::common::dirs::session_state_key("session-a").unwrap();
    let key_b = cchooks::common::dirs::session_state_key("session-b").unwrap();
    let state_a = dir.path().join("sessions").join(key_a).join("state.json");
    let state_b = dir.path().join("sessions").join(key_b).join("state.json");

    let lock_a = cchooks::hooks::langfuse::state::StateLock::acquire(&state_a, 100);
    assert!(lock_a.is_acquired());
    let lock_b = cchooks::hooks::langfuse::state::StateLock::acquire(&state_b, 100);
    assert!(
        lock_b.is_acquired(),
        "session B must not wait on session A's lock"
    );
    assert_ne!(state_a, state_b);
}

#[test]
fn test_session_store_rejects_corrupt_state_without_overwriting_it() {
    use cchooks::hooks::langfuse::state::{SessionStateStore, StateTxnError};

    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state_corrupted_transaction.json");
    let original = b"{ definitely-not-json";
    std::fs::write(&path, original).expect("write corrupt state");

    let store = SessionStateStore::new(path.clone(), "session-1", 100);
    let error = store
        .update_session(|session| {
            session["offset"] = json!(42);
            Ok(())
        })
        .expect_err("corrupt state must fail closed");

    assert!(matches!(error, StateTxnError::Schema(_)));
    assert_eq!(
        std::fs::read(&path).expect("read original state"),
        original,
        "failed transaction must preserve corrupt bytes for diagnosis"
    );
}

#[test]
fn test_state_corrupted_file() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state_corrupted.json");

    // Write non-JSON content to file
    std::fs::write(&path, "this is not valid JSON at all! {broken")
        .expect("Failed to write corrupted data");

    // Load state should return empty object instead of crashing
    let loaded_state = agentic_otel_common::state::load_state(&path);

    // Verify it returns empty object
    assert_eq!(
        loaded_state,
        json!({}),
        "Corrupted file should return empty object"
    );

    // Write another type of invalid JSON
    std::fs::write(&path, "{ incomplete: json").expect("Failed to write incomplete JSON");
    let loaded_state2 = agentic_otel_common::state::load_state(&path);
    assert_eq!(
        loaded_state2,
        json!({}),
        "Incomplete JSON should return empty object"
    );

    // Write binary data
    std::fs::write(&path, b"\x00\x01\x02\x03\x04\x05").expect("Failed to write binary data");
    let loaded_state3 = agentic_otel_common::state::load_state(&path);
    assert_eq!(
        loaded_state3,
        json!({}),
        "Binary data should return empty object"
    );
}
