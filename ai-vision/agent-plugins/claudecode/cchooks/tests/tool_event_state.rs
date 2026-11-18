use cchooks::hooks::langfuse::domain_state::{
    DomainStateStore, StateDomain, ToolHookPhase, ToolHookStateStore,
};
use cchooks::hooks::langfuse::state::{save_state, StateLock};
use cchooks::hooks::tool_event::{
    ClaimOutcome, CleanupOutcome, ReconcileOutcome, RecordOutcome, ToolEventStateStore,
    MAX_TOOL_EXPORT_ATTEMPTS,
};
use serde_json::{json, Value};

fn store(
    dir: &tempfile::TempDir,
    session_id: &str,
    tool_id: &str,
) -> (
    ToolEventStateStore,
    std::path::PathBuf,
    std::path::PathBuf,
    std::path::PathBuf,
) {
    let safe_tool = tool_id.replace(['/', '\\', ':'], "_");
    let pre_file = dir
        .path()
        .join(format!("tool_{safe_tool}"))
        .join("pre.json");
    let post_file = dir
        .path()
        .join(format!("tool_{safe_tool}"))
        .join("post.json");
    let tools_file = dir.path().join(StateDomain::Tools.file_name());
    std::fs::create_dir_all(pre_file.parent().unwrap()).unwrap();
    let store = ToolEventStateStore::new(
        session_id,
        tool_id,
        ToolHookStateStore::new(pre_file.clone(), 100),
        ToolHookStateStore::new(post_file.clone(), 100),
        DomainStateStore::new(
            tools_file.clone(),
            None,
            session_id,
            StateDomain::Tools,
            100,
        ),
    )
    .unwrap();
    (store, pre_file, post_file, tools_file)
}

fn read_json(path: &std::path::Path) -> Value {
    serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap()
}

#[test]
fn pre_event_is_written_atomically_and_tools_cutover_marker_is_durable() {
    let dir = tempfile::tempdir().unwrap();
    let (store, pre_file, _post_file, tools_file) = store(&dir, "session-1", "tool-1");

    let outcome = store
        .record_pre_event(
            &json!({"tool_name": "Read", "tool_input": {"file_path": "a"}}),
            10,
        )
        .unwrap();

    assert!(matches!(outcome, RecordOutcome::Recorded { .. }));
    let pre = read_json(&pre_file);
    assert_eq!(pre["schema_version"], 1);
    assert_eq!(pre["session_id"], "session-1");
    assert_eq!(pre["tool_id"], "tool-1");
    assert_eq!(pre["phase"], "pre");
    assert!(pre["event_id"].as_str().unwrap().starts_with("tool-event-"));
    let tools = read_json(&tools_file);
    assert_eq!(tools["schema_version"], 1);
    assert_eq!(
        tools["tool_state_cutover"]["layout"],
        "state/<session>/tool_<tool>/pre|post.json"
    );
    assert_eq!(
        tools["tool_state_cutover"]["legacy_state_json_writes"],
        false
    );
}

#[test]
fn duplicate_pre_and_post_events_preserve_original_identity() {
    let dir = tempfile::tempdir().unwrap();
    let (store, pre_file, post_file, _tools_file) = store(&dir, "session-1", "tool-1");

    let first_pre = store.record_pre_event(&json!({"a": 1}), 10).unwrap();
    let second_pre = store.record_pre_event(&json!({"a": 2}), 11).unwrap();
    assert_eq!(
        match first_pre {
            RecordOutcome::Recorded { event_id } => event_id,
            other => panic!("unexpected {other:?}"),
        },
        match second_pre {
            RecordOutcome::Duplicate { event_id } => event_id,
            other => panic!("unexpected {other:?}"),
        }
    );
    assert_eq!(read_json(&pre_file)["payload"], json!({"a": 1}));

    let first_post = store.record_post_event(&json!({"b": 1}), 20).unwrap();
    let second_post = store.record_post_event(&json!({"b": 2}), 21).unwrap();
    assert_eq!(
        match first_post {
            RecordOutcome::Recorded { event_id } => event_id,
            other => panic!("unexpected {other:?}"),
        },
        match second_post {
            RecordOutcome::Duplicate { event_id } => event_id,
            other => panic!("unexpected {other:?}"),
        }
    );
    assert_eq!(read_json(&post_file)["payload"], json!({"b": 1}));
}

#[test]
fn post_event_persists_explicit_success_or_failure_outcome() {
    let dir = tempfile::tempdir().unwrap();
    let (success_store, _pre_file, success_post, _tools_file) =
        store(&dir, "session-1", "tool-success");
    success_store
        .record_post_event(
            &json!({"hook_event_name": "PostToolUse", "tool_response": {"content": "ok"}}),
            10,
        )
        .unwrap();
    assert_eq!(read_json(&success_post)["outcome"], "success");

    let (failure_store, _pre_file, failure_post, _tools_file) =
        store(&dir, "session-1", "tool-failure");
    failure_store
        .record_post_event(
            &json!({"hook_event_name": "PostToolUseFailure", "error": "failed"}),
            11,
        )
        .unwrap();
    assert_eq!(read_json(&failure_post)["outcome"], "failure");
}

#[test]
fn post_before_pre_reconciles_to_waiting_then_ready() {
    let dir = tempfile::tempdir().unwrap();
    let (store, _pre_file, _post_file, _tools_file) = store(&dir, "session-1", "tool-1");

    store
        .record_post_event(&json!({"result": "ok"}), 20)
        .unwrap();
    assert!(matches!(
        store.reconcile(21).unwrap(),
        ReconcileOutcome::WaitingForPre { .. }
    ));

    store.record_pre_event(&json!({"input": "ok"}), 22).unwrap();
    assert!(matches!(
        store.reconcile(23).unwrap(),
        ReconcileOutcome::ReadyToClaim { .. }
    ));
    assert_eq!(
        store.pending_post_payload_if_ready(24).unwrap(),
        Some(json!({"result": "ok"})),
        "late PreToolUse must make the stored PostToolUse payload retriggerable"
    );
}

#[test]
fn legacy_timing_cutover_makes_new_post_ready_without_rewriting_legacy() {
    let dir = tempfile::tempdir().unwrap();
    let (store, _pre_file, _post_file, tools_file) = store(&dir, "session-1", "legacy-tool");
    save_state(
        &tools_file,
        &json!({"timing": {"legacy-tool": {"start_ns": "1", "name": "Read"}}}),
    )
    .unwrap();
    store
        .record_post_event(
            &json!({"hook_event_name": "PostToolUse", "tool_response": {"content": "ok"}}),
            20,
        )
        .unwrap();
    assert!(matches!(
        store.reconcile(21).unwrap(),
        ReconcileOutcome::ReadyToClaim { .. }
    ));
    assert_eq!(
        read_json(&tools_file)["tool_state_cutover"]["legacy_timing_import"],
        "complete"
    );
}

#[test]
fn claim_has_single_owner_until_lease_expires_then_can_be_reclaimed() {
    let dir = tempfile::tempdir().unwrap();
    let (store, _pre_file, post_file, _tools_file) = store(&dir, "session-1", "tool-1");
    store
        .record_post_event(&json!({"result": "ok"}), 10)
        .unwrap();

    assert_eq!(
        store.claim_post("worker-a", 100, 50).unwrap(),
        ClaimOutcome::Claimed {
            claim_id: "worker-a".to_string(),
            attempt: 1
        }
    );
    assert_eq!(
        store.claim_post("worker-b", 120, 50).unwrap(),
        ClaimOutcome::Leased {
            claim_id: "worker-a".to_string(),
            lease_until_ms: 150
        }
    );
    assert_eq!(
        store.claim_post("worker-b", 151, 50).unwrap(),
        ClaimOutcome::Claimed {
            claim_id: "worker-b".to_string(),
            attempt: 2
        }
    );

    let post = read_json(&post_file);
    assert_eq!(post["status"], "exporting");
    assert_eq!(post["claim_id"], "worker-b");
    assert_eq!(post["attempt"], 2);
}

#[test]
fn expired_tool_export_claims_reach_a_terminal_attempt_ceiling() {
    let dir = tempfile::tempdir().unwrap();
    let (store, _pre_file, post_file, _tools_file) = store(&dir, "session-1", "tool-limit");
    store
        .record_post_event(&json!({"result": "ok"}), 10)
        .unwrap();

    for attempt in 1..=MAX_TOOL_EXPORT_ATTEMPTS {
        let now = attempt * 100;
        assert_eq!(
            store
                .claim_post(&format!("worker-{attempt}"), now, 1)
                .unwrap(),
            ClaimOutcome::Claimed {
                claim_id: format!("worker-{attempt}"),
                attempt,
            }
        );
    }
    assert!(matches!(
        store.claim_post("worker-terminal", 1_000, 1).unwrap(),
        ClaimOutcome::FailedPermanent { .. }
    ));
    let post = read_json(&post_file);
    assert_eq!(post["status"], "failed_permanent");
    assert_eq!(
        post["failure_reason"],
        "tool export attempt limit exhausted"
    );
    assert!(matches!(
        store.claim_post("worker-late", 2_000, 1).unwrap(),
        ClaimOutcome::FailedPermanent { .. }
    ));
}

#[test]
fn only_claim_owner_can_mark_terminal_state() {
    let dir = tempfile::tempdir().unwrap();
    let (tool_store, _pre_file, post_file, _tools_file) = store(&dir, "session-1", "tool-1");
    tool_store
        .record_post_event(&json!({"result": "ok"}), 10)
        .unwrap();
    tool_store.claim_post("worker-a", 100, 50).unwrap();

    assert!(!tool_store.mark_exported("worker-b", 120).unwrap());
    assert_eq!(read_json(&post_file)["status"], "exporting");
    assert!(tool_store.mark_exported("worker-a", 121).unwrap());
    assert_eq!(read_json(&post_file)["status"], "exported");

    let (failed_store, _pre, failed_post, _tools) = store(&dir, "session-1", "tool-2");
    failed_store
        .record_post_event(&json!({"result": "bad"}), 10)
        .unwrap();
    failed_store.claim_post("worker-a", 100, 50).unwrap();
    assert!(!failed_store
        .mark_failed_permanent("worker-b", "400", 120)
        .unwrap());
    assert!(failed_store
        .mark_failed_permanent("worker-a", "400", 121)
        .unwrap());
    assert_eq!(read_json(&failed_post)["status"], "failed_permanent");
}

#[test]
fn durable_exported_cleanup_deletes_pre_post_and_prevents_resend() {
    let dir = tempfile::tempdir().unwrap();
    let (store, pre_file, post_file, tools_file) = store(&dir, "session-1", "tool-1");
    store.record_pre_event(&json!({"input": "ok"}), 10).unwrap();
    store
        .record_post_event(&json!({"result": "ok"}), 20)
        .unwrap();
    store.claim_post("worker-a", 100, 50).unwrap();
    store.mark_exported("worker-a", 120).unwrap();

    assert!(matches!(
        store.cleanup_if_exported(130).unwrap(),
        CleanupOutcome::Cleaned { .. }
    ));
    assert!(!pre_file.exists());
    assert!(!post_file.exists());
    let tools = read_json(&tools_file);
    let tool = tools["tools"].as_object().unwrap().values().next().unwrap();
    assert_eq!(tool["status"], "exported");

    assert!(matches!(
        store
            .record_post_event(&json!({"result": "ok"}), 200)
            .unwrap(),
        RecordOutcome::AlreadyExported { .. }
    ));
    assert!(matches!(
        store.reconcile(201).unwrap(),
        ReconcileOutcome::AlreadyExported { .. }
    ));
    assert!(!post_file.exists());
}

#[test]
fn ttl_preserves_active_lease_and_removes_expired_orphan() {
    let dir = tempfile::tempdir().unwrap();
    let (store, pre_file, post_file, tools_file) = store(&dir, "session-1", "tool-ttl");
    store.record_pre_event(&json!({"input": "ok"}), 1).unwrap();
    store
        .record_post_event(
            &json!({"hook_event_name": "PostToolUse", "result": "ok"}),
            2,
        )
        .unwrap();
    store.claim_post("worker-a", 100, 1_000).unwrap();
    assert!(!store.expire_orphan_if_safe(200, 10).unwrap());
    assert!(post_file.exists());
    assert!(store.expire_orphan_if_safe(1_101, 10).unwrap());
    assert!(!pre_file.exists());
    assert!(!post_file.exists());
    let tool = read_json(&tools_file)["tools"]
        .as_object()
        .unwrap()
        .values()
        .next()
        .unwrap()
        .clone();
    assert_eq!(tool["status"], "expired");
}

#[cfg(unix)]
#[test]
fn pre_post_and_tools_state_use_independent_locks() {
    let dir = tempfile::tempdir().unwrap();
    let (store, pre_file, post_file, tools_file) = store(&dir, "session-1", "tool-1");
    save_state(&pre_file, &json!({"event_id": "held"})).unwrap();
    let held_pre_lock = StateLock::acquire(&pre_file, 100);
    assert!(held_pre_lock.is_acquired());

    store
        .record_post_event(&json!({"result": "ok"}), 20)
        .unwrap();
    assert!(post_file.exists());
    assert!(tools_file.exists());
}

#[test]
fn explicit_hook_phase_paths_are_valid_for_new_store() {
    let dir = tempfile::tempdir().unwrap();
    let pre = ToolHookStateStore::new(dir.path().join("pre.json"), 100);
    let post = ToolHookStateStore::new(dir.path().join("post.json"), 100);
    let tools = DomainStateStore::new(
        dir.path().join("state_tools.json"),
        None,
        "session-1",
        StateDomain::Tools,
        100,
    );
    let store = ToolEventStateStore::new("session-1", "tool-1", pre, post, tools).unwrap();
    store
        .record_pre_event(&json!({"phase": ToolHookPhase::Pre.as_str()}), 1)
        .unwrap();
    store
        .record_post_event(&json!({"phase": ToolHookPhase::Post.as_str()}), 2)
        .unwrap();
}
