use cchooks::hooks::langfuse::domain_state::{DomainStateStore, StateDomain};
#[cfg(all(feature = "tool_context", not(windows)))]
use cchooks::hooks::langfuse::state::save_state;
#[cfg(all(feature = "tool_context", not(windows)))]
use cchooks::hooks::turn_seed::{read_trace_context_from_store, TurnTraceContext};
use cchooks::hooks::turn_seed::{replace_turn_seed_in_store, TURN_SEED_SCHEMA_VERSION};
use serde_json::{json, Value};

fn turn_store(
    dir: &tempfile::TempDir,
    session_id: &str,
) -> (DomainStateStore, std::path::PathBuf, std::path::PathBuf) {
    let new_file = dir.path().join(StateDomain::Turn.file_name());
    let legacy_file = dir.path().join("legacy").join("state.json");
    (
        DomainStateStore::new(
            new_file.clone(),
            Some(legacy_file.clone()),
            session_id,
            StateDomain::Turn,
            100,
        ),
        new_file,
        legacy_file,
    )
}

#[test]
fn user_prompt_replace_writes_minimal_turn_seed() {
    let dir = tempfile::tempdir().unwrap();
    let (store, new_file, _legacy_file) = turn_store(&dir, "session-1");
    let traceparent = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01";

    let seed = replace_turn_seed_in_store(
        &store,
        "session-1",
        &json!({"uuid": "prompt-1", "prompt": "hello"}),
        Some(traceparent.to_string()),
        "2026-08-12T00:00:00Z".to_string(),
    )
    .unwrap();

    assert_eq!(seed.schema_version, TURN_SEED_SCHEMA_VERSION);
    assert_eq!(seed.turn_id, "prompt-1");
    assert_eq!(seed.trace_id, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    assert_eq!(seed.upstream_traceparent.as_deref(), Some(traceparent));
    assert!(seed.prompt_digest.is_some());
    let stored: Value = serde_json::from_slice(&std::fs::read(new_file).unwrap()).unwrap();
    assert_eq!(stored["turn_id"], "prompt-1");
    assert!(stored.get("prompt_digest").is_some());
    assert!(stored.get("prompt").is_none());
    assert!(stored.get("current_trace_context").is_none());
}

#[test]
fn user_prompt_seed_records_current_turn_boundary_without_copying_transcript() {
    let dir = tempfile::tempdir().unwrap();
    let transcript = dir.path().join("session.jsonl");
    let history = json!({
        "type": "user",
        "uuid": "history-user",
        "message": {"role": "user", "content": "old prompt"}
    });
    let current = json!({
        "type": "user",
        "uuid": "prompt-current",
        "message": {"role": "user", "content": "current prompt"}
    });
    let history_line = format!("{history}\n");
    std::fs::write(&transcript, format!("{history_line}{current}\n")).unwrap();
    let (store, _new_file, _legacy_file) = turn_store(&dir, "session-1");

    let seed = replace_turn_seed_in_store(
        &store,
        "session-1",
        &json!({
            "uuid": "prompt-current",
            "prompt": "current prompt",
            "transcript_path": transcript,
        }),
        None,
        "2026-08-12T00:00:00Z".to_string(),
    )
    .unwrap();

    let boundary = seed.transcript_boundary.expect("turn boundary");
    assert_eq!(boundary.start_offset, history_line.len() as i64);
    assert_eq!(
        boundary.transcript_path,
        transcript.canonicalize().unwrap().to_string_lossy()
    );
}

#[test]
fn user_prompt_seed_uses_current_eof_when_host_has_not_appended_prompt_yet() {
    let dir = tempfile::tempdir().unwrap();
    let transcript = dir.path().join("session.jsonl");
    std::fs::write(
        &transcript,
        format!(
            "{}\n",
            json!({
                "type": "user",
                "uuid": "history-user",
                "message": {"role": "user", "content": "old prompt"}
            })
        ),
    )
    .unwrap();
    let expected_eof = std::fs::metadata(&transcript).unwrap().len() as i64;
    let (store, _new_file, _legacy_file) = turn_store(&dir, "session-1");

    let seed = replace_turn_seed_in_store(
        &store,
        "session-1",
        &json!({
            "uuid": "prompt-current",
            "prompt": "current prompt",
            "transcript_path": transcript,
        }),
        None,
        "2026-08-12T00:00:00Z".to_string(),
    )
    .unwrap();

    assert_eq!(
        seed.transcript_boundary
            .expect("turn boundary")
            .start_offset,
        expected_eof
    );
}

#[test]
#[cfg(all(feature = "tool_context", not(windows)))]
fn two_pre_reads_reuse_same_seed_trace() {
    let dir = tempfile::tempdir().unwrap();
    let (store, _new_file, _legacy_file) = turn_store(&dir, "session-1");
    replace_turn_seed_in_store(
        &store,
        "session-1",
        &json!({"uuid": "prompt-1"}),
        Some("00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01".to_string()),
        "2026-08-12T00:00:00Z".to_string(),
    )
    .unwrap();

    let first = read_trace_context_from_store(&store, None).unwrap();
    let second = read_trace_context_from_store(&store, None).unwrap();

    assert_eq!(first.trace_id(), Some("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
    assert_eq!(second.trace_id(), Some("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
}

#[test]
#[cfg(all(feature = "tool_context", not(windows)))]
fn empty_new_turn_file_is_authoritative_and_missing_seed_fail_opens() {
    let dir = tempfile::tempdir().unwrap();
    let (store, new_file, legacy_file) = turn_store(&dir, "session-1");
    save_state(
        &legacy_file,
        &json!({"session-1": {"current_trace_context": {"trace_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}}),
    )
    .unwrap();
    std::fs::write(new_file, "").unwrap();

    let observed = read_trace_context_from_store(&store, None).unwrap();

    assert_eq!(observed, TurnTraceContext::Missing);
}

#[test]
#[cfg(all(feature = "tool_context", not(windows)))]
fn missing_new_turn_file_falls_back_to_legacy_current_trace_context() {
    let dir = tempfile::tempdir().unwrap();
    let (store, _new_file, legacy_file) = turn_store(&dir, "session-1");
    save_state(
        &legacy_file,
        &json!({"session-1": {"current_trace_context": {"trace_id": "cccccccccccccccccccccccccccccccc"}}}),
    )
    .unwrap();

    let observed = read_trace_context_from_store(&store, None).unwrap();

    assert_eq!(
        observed,
        TurnTraceContext::Legacy {
            trace_id: "cccccccccccccccccccccccccccccccc".to_string()
        }
    );
}

#[test]
#[cfg(all(feature = "tool_context", not(windows)))]
fn upstream_traceparent_is_used_only_when_seed_and_legacy_are_missing() {
    let dir = tempfile::tempdir().unwrap();
    let (store, _new_file, _legacy_file) = turn_store(&dir, "session-1");

    let observed = read_trace_context_from_store(
        &store,
        Some("00-dddddddddddddddddddddddddddddddd-bbbbbbbbbbbbbbbb-01"),
    )
    .unwrap();

    assert_eq!(
        observed,
        TurnTraceContext::Upstream {
            trace_id: "dddddddddddddddddddddddddddddddd".to_string()
        }
    );
}

#[test]
fn replacing_prompt_seed_overwrites_previous_turn() {
    let dir = tempfile::tempdir().unwrap();
    let (store, _new_file, _legacy_file) = turn_store(&dir, "session-1");
    replace_turn_seed_in_store(
        &store,
        "session-1",
        &json!({"uuid": "prompt-old"}),
        Some("00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01".to_string()),
        "2026-08-12T00:00:00Z".to_string(),
    )
    .unwrap();

    replace_turn_seed_in_store(
        &store,
        "session-1",
        &json!({"message": {"id": "prompt-new", "content": "hello"}}),
        Some("00-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-bbbbbbbbbbbbbbbb-01".to_string()),
        "2026-08-12T00:00:01Z".to_string(),
    )
    .unwrap();

    let stored = store.read(Clone::clone).unwrap();
    assert_eq!(stored["turn_id"], "prompt-new");
    assert_eq!(stored["trace_id"], "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee");
}

#[test]
fn fallback_turn_id_changes_when_created_at_changes() {
    let dir = tempfile::tempdir().unwrap();
    let (store, _new_file, _legacy_file) = turn_store(&dir, "session-1");
    let first = replace_turn_seed_in_store(
        &store,
        "session-1",
        &json!({"prompt": "same prompt"}),
        None,
        "2026-08-12T00:00:00Z".to_string(),
    )
    .unwrap();
    let second = replace_turn_seed_in_store(
        &store,
        "session-1",
        &json!({"prompt": "same prompt"}),
        None,
        "2026-08-12T00:00:01Z".to_string(),
    )
    .unwrap();

    assert_ne!(first.turn_id, second.turn_id);
}
