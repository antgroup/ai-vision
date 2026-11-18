use cchooks::hooks::langfuse::domain_state::{DomainStateStore, StateDomain};
use cchooks::hooks::langfuse::state::{save_state, SessionStateStore, StateTxnError};
use serde_json::{json, Value};

fn runtime_paths(
    dir: &tempfile::TempDir,
    session_id: &str,
) -> (std::path::PathBuf, std::path::PathBuf) {
    let key = cchooks::common::dirs::session_state_key(session_id).unwrap();
    let state_root = dir.path().join("state");
    (
        state_root.join("sessions").join(&key).join("state.json"),
        state_root.join(key),
    )
}

fn read_json(path: &std::path::Path) -> Value {
    serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap()
}

#[test]
fn for_session_initializes_new_state_dir_without_creating_legacy_sessions_dir() {
    let dir = tempfile::tempdir().unwrap();
    let previous = std::env::var("CCHOOKS_STATE_DIR").ok();
    std::env::set_var("CCHOOKS_STATE_DIR", dir.path().join("state"));

    let store = SessionStateStore::for_session("session-1", 1_000).unwrap();
    let key = cchooks::common::dirs::session_state_key("session-1").unwrap();

    assert!(dir.path().join("state").join(&key).is_dir());
    assert!(!dir.path().join("state").join("sessions").exists());
    assert_eq!(
        store.state_file(),
        dir.path()
            .join("state")
            .join("sessions")
            .join(key)
            .join("state.json")
    );

    match previous {
        Some(value) => std::env::set_var("CCHOOKS_STATE_DIR", value),
        None => std::env::remove_var("CCHOOKS_STATE_DIR"),
    }
}

#[test]
fn first_runtime_update_materializes_only_changed_domain_without_rewriting_legacy_file() {
    let dir = tempfile::tempdir().unwrap();
    let session_id = "session-1";
    let (legacy_file, domain_dir) = runtime_paths(&dir, session_id);
    let legacy_state = json!({session_id: {
        "offset": 10,
        "buffer": "legacy",
        "turn_count": 1,
        "pending_final_export": {"status": "pending"},
        "turn_id": "turn-1",
        "trace_id": "trace-1",
        "upstream_traceparent": "00-trace-parent",
        "prompt_timestamp": "2026-08-12T00:00:00Z",
        "prompt_digest": "digest-1",
        "created_at": "2026-08-12T00:00:00Z",
        "vcs": {"remote_url": "ssh://legacy", "branch": "main", "cwd": "/repo"},
        "timing": {"tool-a": {"start_ns": "1"}},
        "extension": {"keep": true}
    }});
    agentic_otel_common::state::save_state(&legacy_file, &legacy_state).unwrap();
    let legacy_before = std::fs::read(&legacy_file).unwrap();

    let store = SessionStateStore::new(legacy_file.clone(), session_id, 1_000);
    store
        .update_session(|session| {
            session["offset"] = json!(20);
            session["buffer"] = json!("new-buffer");
            Ok(())
        })
        .unwrap();

    assert_eq!(std::fs::read(&legacy_file).unwrap(), legacy_before);
    assert_eq!(
        read_json(&domain_dir.join(StateDomain::Stop.file_name()))["offset"],
        20
    );
    assert_eq!(
        read_json(&domain_dir.join(StateDomain::Stop.file_name()))["pending_final_export"]
            ["status"],
        "pending"
    );
    for domain in [
        StateDomain::Turn,
        StateDomain::Resource,
        StateDomain::Tools,
        StateDomain::Attribution,
    ] {
        assert!(!domain_dir.join(domain.file_name()).exists());
    }

    let observed = store.read(Clone::clone).unwrap();
    assert_eq!(observed[session_id]["turn_id"], "turn-1");
    assert_eq!(observed[session_id]["trace_id"], "trace-1");
    assert_eq!(
        observed[session_id]["prompt_timestamp"],
        "2026-08-12T00:00:00Z"
    );
    assert_eq!(observed[session_id]["prompt_digest"], "digest-1");
    assert_eq!(observed[session_id]["vcs"]["remote_url"], "ssh://legacy");
    assert_eq!(observed[session_id]["timing"]["tool-a"]["start_ns"], "1");
    assert_eq!(observed[session_id]["extension"]["keep"], true);
}

#[test]
fn new_domain_file_suppresses_legacy_resurrection_for_that_domain() {
    let dir = tempfile::tempdir().unwrap();
    let session_id = "session-1";
    let (legacy_file, domain_dir) = runtime_paths(&dir, session_id);
    agentic_otel_common::state::save_state(
        &legacy_file,
        &json!({session_id: {"timing": {"legacy-tool": {"start_ns": "1"}}}}),
    )
    .unwrap();
    save_state(&domain_dir.join(StateDomain::Tools.file_name()), &json!({})).unwrap();

    let observed = SessionStateStore::new(legacy_file, session_id, 1_000)
        .read(Clone::clone)
        .unwrap();

    assert!(observed[session_id].get("timing").is_none());
}

#[test]
fn empty_domain_file_is_authoritative_in_session_store_reads() {
    let dir = tempfile::tempdir().unwrap();
    let session_id = "session-1";
    let (legacy_file, domain_dir) = runtime_paths(&dir, session_id);
    agentic_otel_common::state::save_state(
        &legacy_file,
        &json!({session_id: {
            "current_trace_context": {"trace_id": "legacy"},
            "offset": 1
        }}),
    )
    .unwrap();
    std::fs::create_dir_all(&domain_dir).unwrap();
    std::fs::write(domain_dir.join(StateDomain::Turn.file_name()), "").unwrap();

    let observed = SessionStateStore::new(legacy_file, session_id, 1_000)
        .read(Clone::clone)
        .unwrap();

    assert!(observed[session_id].get("current_trace_context").is_none());
    assert_eq!(observed[session_id]["offset"], 1);
}

#[test]
fn runtime_save_state_wrapper_splits_domains_instead_of_writing_state_json() {
    let dir = tempfile::tempdir().unwrap();
    let session_id = "session-1";
    let (legacy_file, domain_dir) = runtime_paths(&dir, session_id);
    let state = json!({session_id: {
        "realtime_export": {"current": {"context_id": "ctx-1"}},
        "custom": "keep"
    }});

    save_state(&legacy_file, &state).unwrap();

    assert!(!legacy_file.exists());
    assert_eq!(
        read_json(&domain_dir.join(StateDomain::Generation.file_name()))["realtime_export"]
            ["current"]["context_id"],
        "ctx-1"
    );
    assert_eq!(
        read_json(&domain_dir.join(StateDomain::Attribution.file_name()))["custom"],
        "keep"
    );
}

#[test]
fn composite_update_preserves_tools_tombstones_and_cutover_marker() {
    let dir = tempfile::tempdir().unwrap();
    let session_id = "session-1";
    let (legacy_file, domain_dir) = runtime_paths(&dir, session_id);
    save_state(
        &domain_dir.join(StateDomain::Tools.file_name()),
        &json!({
            "schema_version": 1,
            "tool_state_cutover": {
                "layout": "state/<session>/tool_<tool>/pre|post.json",
                "updated_at_ms": 100,
                "legacy_state_json_writes": false
            },
            "tools": {
                "tool-key": {
                    "status": "exported",
                    "event_id": "tool-event-1"
                }
            }
        }),
    )
    .unwrap();

    SessionStateStore::new(legacy_file.clone(), session_id, 1_000)
        .update_session(|session| {
            session["timing"]["tool-a"] = json!({"start_ns": "1"});
            Ok(())
        })
        .unwrap();

    let tools = read_json(&domain_dir.join(StateDomain::Tools.file_name()));
    assert_eq!(tools["timing"]["tool-a"]["start_ns"], "1");
    assert_eq!(tools["tools"]["tool-key"]["status"], "exported");
    assert_eq!(tools["tool_state_cutover"]["updated_at_ms"], 100);
}

#[test]
fn composite_update_preserves_concurrent_domain_fields_absent_from_loaded_snapshot() {
    let dir = tempfile::tempdir().unwrap();
    let session_id = "session-1";
    let (legacy_file, domain_dir) = runtime_paths(&dir, session_id);
    save_state(
        &domain_dir.join(StateDomain::Stop.file_name()),
        &json!({
            "schema_version": 1,
            "offset": 10,
            "buffer": "",
            "turn_count": 1
        }),
    )
    .unwrap();

    let stop_store = DomainStateStore::new(
        domain_dir.join(StateDomain::Stop.file_name()),
        Some(legacy_file.clone()),
        session_id,
        StateDomain::Stop,
        1_000,
    );
    SessionStateStore::new(legacy_file, session_id, 1_000)
        .update_session(|session| {
            stop_store
                .update(|state| {
                    state["pending_final_export"] = json!({"status": "pending"});
                    Ok(())
                })
                .unwrap();
            session["offset"] = json!(20);
            Ok(())
        })
        .unwrap();

    let stop = read_json(&domain_dir.join(StateDomain::Stop.file_name()));
    assert_eq!(stop["offset"], 20);
    assert_eq!(stop["pending_final_export"]["status"], "pending");
}

#[test]
fn composite_update_rejects_same_field_conflict_without_clobbering_domain_writer() {
    let dir = tempfile::tempdir().unwrap();
    let session_id = "session-1";
    let (legacy_file, domain_dir) = runtime_paths(&dir, session_id);
    save_state(
        &domain_dir.join(StateDomain::Stop.file_name()),
        &json!({
            "schema_version": 1,
            "offset": 10,
            "buffer": "",
            "turn_count": 1
        }),
    )
    .unwrap();

    let stop_store = DomainStateStore::new(
        domain_dir.join(StateDomain::Stop.file_name()),
        Some(legacy_file.clone()),
        session_id,
        StateDomain::Stop,
        1_000,
    );
    let result = SessionStateStore::new(legacy_file, session_id, 1_000).update_session(|session| {
        stop_store
            .update(|state| {
                state["offset"] = json!(30);
                Ok(())
            })
            .unwrap();
        session["offset"] = json!(20);
        Ok(())
    });

    assert!(matches!(result, Err(StateTxnError::Conflict(_))));
    assert_eq!(
        read_json(&domain_dir.join(StateDomain::Stop.file_name()))["offset"],
        30
    );
}

#[test]
fn composite_update_writes_only_domains_changed_by_callback() {
    let dir = tempfile::tempdir().unwrap();
    let session_id = "session-1";
    let (legacy_file, domain_dir) = runtime_paths(&dir, session_id);
    agentic_otel_common::state::save_state(
        &legacy_file,
        &json!({session_id: {
            "offset": 10,
            "turn_id": "legacy-turn"
        }}),
    )
    .unwrap();

    SessionStateStore::new(legacy_file, session_id, 1_000)
        .update_session(|session| {
            std::fs::create_dir_all(&domain_dir).unwrap();
            std::fs::create_dir(domain_dir.join(StateDomain::Turn.file_name())).unwrap();
            session["offset"] = json!(20);
            Ok(())
        })
        .unwrap();

    assert_eq!(
        read_json(&domain_dir.join(StateDomain::Stop.file_name()))["offset"],
        20
    );
    assert!(domain_dir.join(StateDomain::Turn.file_name()).is_dir());
}

#[test]
fn failed_multi_domain_commit_is_resumed_from_transaction_journal() {
    let dir = tempfile::tempdir().unwrap();
    let session_id = "session-1";
    let (legacy_file, domain_dir) = runtime_paths(&dir, session_id);
    agentic_otel_common::state::save_state(
        &legacy_file,
        &json!({session_id: {
            "offset": 10,
            "turn_id": "turn-1"
        }}),
    )
    .unwrap();

    let store = SessionStateStore::new(legacy_file, session_id, 1_000);
    let result = store.update_session(|session| {
        std::fs::create_dir_all(&domain_dir).unwrap();
        std::fs::create_dir(domain_dir.join(StateDomain::Turn.file_name())).unwrap();
        session["offset"] = json!(20);
        session["turn_id"] = json!("turn-2");
        Ok(())
    });
    assert!(matches!(
        result,
        Err(StateTxnError::Read(_) | StateTxnError::Write(_))
    ));
    assert_eq!(
        read_json(&domain_dir.join(StateDomain::Stop.file_name()))["offset"],
        20
    );
    assert!(domain_dir.join("state_transaction.json").exists());

    std::fs::remove_dir(domain_dir.join(StateDomain::Turn.file_name())).unwrap();
    let observed = store.read(Clone::clone).unwrap();
    assert_eq!(observed[session_id]["offset"], 20);
    assert_eq!(observed[session_id]["turn_id"], "turn-2");
    assert!(!domain_dir.join("state_transaction.json").exists());
}

#[cfg(unix)]
#[test]
fn concurrent_different_domain_updates_preserve_both_fields() {
    use std::sync::{Arc, Barrier};
    use std::thread;

    let dir = tempfile::tempdir().unwrap();
    let session_id = "session-1";
    let (legacy_file, _) = runtime_paths(&dir, session_id);
    let barrier = Arc::new(Barrier::new(3));

    let timing_file = legacy_file.clone();
    let timing_barrier = Arc::clone(&barrier);
    let timing = thread::spawn(move || {
        let store = SessionStateStore::new(timing_file, "session-1", 2_000);
        timing_barrier.wait();
        store
            .update_session(|session| {
                session["timing"]["tool-a"] = json!({"start_ns": "1"});
                Ok(())
            })
            .unwrap();
    });

    let subagent_file = legacy_file.clone();
    let subagent_barrier = Arc::clone(&barrier);
    let subagent = thread::spawn(move || {
        let store = SessionStateStore::new(subagent_file, "session-1", 2_000);
        subagent_barrier.wait();
        store
            .update_session(|session| {
                session["subagent_timing"]["agent-a"]["timing"]["tool-b"] =
                    json!({"start_ns": "2"});
                Ok(())
            })
            .unwrap();
    });

    barrier.wait();
    timing.join().unwrap();
    subagent.join().unwrap();

    let observed = SessionStateStore::new(legacy_file, session_id, 1_000)
        .read(Clone::clone)
        .unwrap();
    assert_eq!(observed[session_id]["timing"]["tool-a"]["start_ns"], "1");
    assert_eq!(
        observed[session_id]["subagent_timing"]["agent-a"]["timing"]["tool-b"]["start_ns"],
        "2"
    );
}
