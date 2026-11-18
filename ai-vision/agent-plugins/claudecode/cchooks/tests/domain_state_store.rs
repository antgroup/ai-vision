use cchooks::hooks::langfuse::domain_state::{DomainStateStore, StateDomain};
use cchooks::hooks::langfuse::state::{save_state, StateLock};
use serde_json::{json, Value};

fn store(
    dir: &tempfile::TempDir,
    session_id: &str,
    domain: StateDomain,
) -> (DomainStateStore, std::path::PathBuf, std::path::PathBuf) {
    let new_file = dir.path().join(domain.file_name());
    let legacy_file = dir
        .path()
        .join("state")
        .join("sessions")
        .join("legacy")
        .join("state.json");
    (
        DomainStateStore::new(
            new_file.clone(),
            Some(legacy_file.clone()),
            session_id,
            domain,
            100,
        ),
        new_file,
        legacy_file,
    )
}

#[test]
fn domain_store_reads_new_file_before_legacy() {
    let dir = tempfile::tempdir().unwrap();
    let (store, new_file, legacy_file) = store(&dir, "session-1", StateDomain::Turn);
    save_state(
        &legacy_file,
        &json!({"session-1": {"current_trace_context": {"trace_id": "legacy"}}}),
    )
    .unwrap();
    save_state(
        &new_file,
        &json!({"current_trace_context": {"trace_id": "new"}}),
    )
    .unwrap();

    let observed = store.read(Clone::clone).unwrap();

    assert_eq!(observed["current_trace_context"]["trace_id"], "new");
}

#[test]
fn empty_new_domain_file_is_authoritative_and_does_not_fallback() {
    let dir = tempfile::tempdir().unwrap();
    let (store, new_file, legacy_file) = store(&dir, "session-1", StateDomain::Turn);
    save_state(
        &legacy_file,
        &json!({"session-1": {"offset": 10, "buffer": "legacy", "turn_count": 1}}),
    )
    .unwrap();
    std::fs::create_dir_all(new_file.parent().unwrap()).unwrap();
    std::fs::write(&new_file, "").unwrap();

    let observed = store.read(Clone::clone).unwrap();

    assert_eq!(observed, json!({}));
}

#[test]
fn missing_new_domain_file_falls_back_to_matching_legacy_fields() {
    let dir = tempfile::tempdir().unwrap();
    let (store, new_file, legacy_file) = store(&dir, "session-1", StateDomain::Stop);
    save_state(
        &legacy_file,
        &json!({
            "session-1": {
                "offset": 10,
                "buffer": "legacy",
                "turn_count": 1,
                "vcs": {"remote_url": "ssh://repo"},
                "unknown": true
            }
        }),
    )
    .unwrap();

    let observed = store.read(Clone::clone).unwrap();

    assert!(
        !new_file.exists(),
        "read fallback must not create a new domain file"
    );
    assert_eq!(observed["offset"], 10);
    assert_eq!(observed["buffer"], "legacy");
    assert_eq!(observed["turn_count"], 1);
    assert!(observed.get("vcs").is_none());
    assert!(observed.get("unknown").is_none());
}

#[test]
fn domain_store_writes_only_new_file_and_preserves_legacy_bytes() {
    let dir = tempfile::tempdir().unwrap();
    let (store, new_file, legacy_file) = store(&dir, "session-1", StateDomain::Resource);
    let legacy = json!({"session-1": {"vcs": {"remote_url": "ssh://legacy"}}});
    save_state(&legacy_file, &legacy).unwrap();
    let legacy_before = std::fs::read(&legacy_file).unwrap();

    store
        .update(|state| {
            state["vcs"] = json!({"remote_url": "ssh://new", "branch": "main", "cwd": "/repo"});
            Ok(())
        })
        .unwrap();

    assert_eq!(std::fs::read(&legacy_file).unwrap(), legacy_before);
    let new_state: Value = serde_json::from_slice(&std::fs::read(&new_file).unwrap()).unwrap();
    assert_eq!(new_state["vcs"]["remote_url"], "ssh://new");
}

#[cfg(unix)]
#[test]
fn independent_domains_do_not_compete_for_the_same_lock() {
    let dir = tempfile::tempdir().unwrap();
    let turn_file = dir.path().join(StateDomain::Turn.file_name());
    let stop_store = DomainStateStore::new(
        dir.path().join(StateDomain::Stop.file_name()),
        None,
        "session-1",
        StateDomain::Stop,
        10,
    );
    let held_turn_lock = StateLock::acquire(&turn_file, 100);
    assert!(held_turn_lock.is_acquired());

    stop_store
        .update(|state| {
            state["pending_export_reason"] = json!("test");
            Ok(())
        })
        .unwrap();

    assert_eq!(
        stop_store
            .read(|state| state["pending_export_reason"].clone())
            .unwrap(),
        json!("test")
    );
}
