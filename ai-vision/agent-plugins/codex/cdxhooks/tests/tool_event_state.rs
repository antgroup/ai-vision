use serde_json::json;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use cdxhooks::common::dirs;
use cdxhooks::common::runtime_state::{DomainStateStore, StateDomain};
use cdxhooks::common::tool_event;

static ENV_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

fn with_state_root(test: impl FnOnce(&std::path::Path)) {
    let _guard = ENV_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let root = tempfile::tempdir().unwrap();
    let previous = std::env::var("CDXHOOKS_STATE_DIR").ok();
    std::env::set_var("CDXHOOKS_STATE_DIR", root.path());
    test(root.path());
    match previous {
        Some(value) => std::env::set_var("CDXHOOKS_STATE_DIR", value),
        None => std::env::remove_var("CDXHOOKS_STATE_DIR"),
    }
}

fn fact(event: &str, session: &str, tool: &str) -> serde_json::Value {
    json!({
        "hook_event_name": event,
        "session_id": session,
        "tool_use_id": tool,
        "tool_name": "Bash",
        "tool_input": {"command": "true"},
        "tool_response": {"output": "ok"}
    })
}

#[test]
fn post_before_pre_becomes_ready_and_terminal_without_per_tool_lock() {
    with_state_root(|_| {
        let session = "post-before-pre";
        let tool = "tool/1";

        tool_event::publish_ingress_fact(&fact("PostToolUse", session, tool)).unwrap();
        assert!(tool_event::begin_ready(session, tool).unwrap().is_none());
        let state = DomainStateStore::for_session(session, StateDomain::Tools)
            .unwrap()
            .load()
            .unwrap();
        assert_eq!(state["events"][tool]["status"], "waiting_for_pre");

        tool_event::publish_ingress_fact(&fact("PreToolUse", session, tool)).unwrap();
        let ready = tool_event::begin_ready(session, tool).unwrap().unwrap();
        assert_eq!(ready.tool_use_id, tool);
        tool_event::mark_terminal(session, &ready).unwrap();

        let state = DomainStateStore::for_session(session, StateDomain::Tools)
            .unwrap()
            .load()
            .unwrap();
        assert_eq!(state["events"][tool]["status"], "terminal");
        assert!(tool_event::begin_ready(session, tool).unwrap().is_none());
        assert!(!dirs::tool_event_dir(session, tool).unwrap().exists());

        tool_event::publish_ingress_fact(&fact("PreToolUse", session, tool)).unwrap();
        tool_event::publish_ingress_fact(&fact("PostToolUse", session, tool)).unwrap();
        assert!(tool_event::begin_ready(session, tool).unwrap().is_none());
        assert!(!dirs::tool_event_dir(session, tool).unwrap().exists());
    });
}

#[test]
fn duplicate_fact_is_idempotent_and_ready_retries_until_terminal() {
    with_state_root(|_| {
        let session = "duplicate";
        let tool = "tool-1";
        let pre = fact("PreToolUse", session, tool);
        let post = fact("PostToolUse", session, tool);
        tool_event::publish_ingress_fact(&pre).unwrap();
        tool_event::publish_ingress_fact(&pre).unwrap();
        tool_event::publish_ingress_fact(&post).unwrap();

        let first = tool_event::begin_ready(session, tool).unwrap().unwrap();
        let second = tool_event::begin_ready(session, tool).unwrap().unwrap();
        assert_eq!(first.post_event_id, second.post_event_id);
        let state = DomainStateStore::for_session(session, StateDomain::Tools)
            .unwrap()
            .load()
            .unwrap();
        assert_eq!(state["events"][tool]["attempts"], 2);
    });
}

#[test]
fn pending_scan_has_no_claim_or_lease_lifecycle() {
    with_state_root(|_| {
        tool_event::publish_ingress_fact(&fact("PostToolUse", "session", "b")).unwrap();
        tool_event::publish_ingress_fact(&fact("PreToolUse", "session", "a")).unwrap();
        assert_eq!(
            tool_event::pending_tool_ids("session", 16).unwrap(),
            vec!["a".to_string(), "b".to_string()]
        );

        let source = std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/common/tool_event.rs"),
        )
        .unwrap();
        for forbidden in [
            "StateLock::",
            "SessionLock",
            "lease_duration",
            "common::spool",
        ] {
            assert!(!source.contains(forbidden), "unexpected {forbidden}");
        }
    });
}

#[test]
fn pending_scan_prioritizes_ready_pairs_before_incomplete_orphans() {
    with_state_root(|_| {
        for index in 0..20 {
            let tool = format!("a-orphan-{index:02}");
            tool_event::publish_ingress_fact(&fact("PostToolUse", "session", &tool)).unwrap();
        }
        tool_event::publish_ingress_fact(&fact("PreToolUse", "session", "z-ready")).unwrap();
        tool_event::publish_ingress_fact(&fact("PostToolUse", "session", "z-ready")).unwrap();

        let pending = tool_event::pending_tool_ids("session", 16).unwrap();
        assert_eq!(pending.len(), 16);
        assert_eq!(pending[0], "z-ready");
    });
}

#[test]
fn old_orphan_facts_expire_with_payload_free_tombstone() {
    with_state_root(|_| {
        let session = "expiry";
        let old_tool = "old-post-only";
        let old_ready_tool = "old-ready";
        let recent_tool = "recent-post-only";
        tool_event::publish_ingress_fact(&fact("PostToolUse", session, old_tool)).unwrap();
        tool_event::publish_ingress_fact(&fact("PreToolUse", session, old_ready_tool)).unwrap();
        tool_event::publish_ingress_fact(&fact("PostToolUse", session, old_ready_tool)).unwrap();
        tool_event::publish_ingress_fact(&fact("PostToolUse", session, recent_tool)).unwrap();

        let old_path = dirs::tool_event_file(session, old_tool, "post").unwrap();
        let mut old_fact: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&old_path).unwrap()).unwrap();
        old_fact["observed_at_unix_nano"] = json!("0");
        std::fs::write(&old_path, serde_json::to_vec(&old_fact).unwrap()).unwrap();
        for phase in ["pre", "post"] {
            let path = dirs::tool_event_file(session, old_ready_tool, phase).unwrap();
            let mut fact: serde_json::Value =
                serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
            fact["observed_at_unix_nano"] = json!("0");
            std::fs::write(&path, serde_json::to_vec(&fact).unwrap()).unwrap();
        }

        assert_eq!(
            tool_event::expire_orphan_facts(session, Duration::from_secs(24 * 60 * 60)).unwrap(),
            1
        );
        assert!(!dirs::tool_event_dir(session, old_tool).unwrap().exists());
        assert!(dirs::tool_event_dir(session, old_ready_tool)
            .unwrap()
            .exists());
        assert!(dirs::tool_event_dir(session, recent_tool).unwrap().exists());
        let state = DomainStateStore::for_session(session, StateDomain::Tools)
            .unwrap()
            .load()
            .unwrap();
        assert_eq!(state["events"][old_tool]["status"], "expired");
        assert!(state["events"][old_tool].get("payload").is_none());
    });
}

#[test]
fn completed_finalizer_cleans_all_remaining_fact_payloads_beyond_recovery_cap() {
    with_state_root(|_| {
        let session = "finalizer-cleanup";
        for index in 0..20 {
            let tool = format!("tool-{index:02}");
            tool_event::publish_ingress_fact(&fact("PreToolUse", session, &tool)).unwrap();
            tool_event::publish_ingress_fact(&fact("PostToolUse", session, &tool)).unwrap();
        }

        assert_eq!(
            tool_event::pending_tool_ids(session, usize::MAX)
                .unwrap()
                .len(),
            20
        );
        assert_eq!(tool_event::finalize_session_facts(session).unwrap(), 20);
        assert!(tool_event::pending_tool_ids(session, 16)
            .unwrap()
            .is_empty());

        let state = DomainStateStore::for_session(session, StateDomain::Tools)
            .unwrap()
            .load()
            .unwrap();
        assert_eq!(state["events"].as_object().unwrap().len(), 20);
        assert!(state["events"]
            .as_object()
            .unwrap()
            .values()
            .all(|entry| entry["status"] == "finalizer_accounted"));
    });
}
