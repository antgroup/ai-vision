use cdxhooks::common::spool::{
    self, RealtimeSpoolKind, SpoolJob, SpoolJobKind, CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
    REALTIME_SCHEMA_VERSION,
};
use serde_json::{json, Value};

#[test]
fn state_native_work_identity_is_deterministic_and_payload_bound() {
    let payload = json!({"resourceSpans": [{"scopeSpans": []}], "z": 1});
    let digest = spool::payload_digest(&payload).unwrap();
    let first = spool::deterministic_realtime_job_id_for_session_schema(
        RealtimeSpoolKind::Batch,
        CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
        "session-r",
        "context-1",
        "completed-1",
        &digest,
    );
    let second = spool::deterministic_realtime_job_id_for_session_schema(
        RealtimeSpoolKind::Batch,
        CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
        "session-r",
        "context-1",
        "completed-1",
        &spool::payload_digest(&payload).unwrap(),
    );

    assert_eq!(first, second);
    assert_ne!(
        first,
        spool::deterministic_realtime_job_id_for_session_schema(
            RealtimeSpoolKind::Batch,
            CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
            "session-r",
            "context-1",
            "completed-1",
            &spool::payload_digest(&json!({"resourceSpans": []})).unwrap(),
        )
    );
}

#[test]
fn imported_realtime_identity_rejects_payload_tampering() {
    let payload = json!({"resourceSpans": [{"scopeSpans": []}]});
    let digest = spool::payload_digest(&payload).unwrap();
    let job_id = spool::deterministic_realtime_job_id_for_session_schema(
        RealtimeSpoolKind::Tool,
        CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
        "session-r",
        "context-1",
        "tool-1",
        &digest,
    );
    let mut job = SpoolJob {
        schema_version: REALTIME_SCHEMA_VERSION,
        session_schema_version: CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
        job_kind: SpoolJobKind::Realtime(RealtimeSpoolKind::Tool),
        job_id,
        session_id: "session-r".to_string(),
        turn_id: None,
        created_at: "2026-08-13T00:00:00Z".to_string(),
        claimed_at: None,
        attempts: 0,
        last_error: None,
        otel: payload,
        transcript_path: None,
        cwd: None,
        username: None,
        hook_version: None,
        subject_id: Some("tool-1".to_string()),
        context_id: Some("context-1".to_string()),
        payload_digest: Some(digest),
        batch_manifest: None,
        flush_manifest: None,
    };

    assert!(spool::realtime_job_identity_matches(&job).unwrap());
    job.otel = Value::Null;
    assert!(!spool::realtime_job_identity_matches(&job).unwrap());
}

#[test]
fn legacy_spool_producers_are_test_only_compatibility_fixtures() {
    let source = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/common/spool.rs"),
    )
    .unwrap();
    for producer in [
        "pub fn enqueue(",
        "pub fn enqueue_transcript(",
        "pub fn publish_realtime_job(",
        "pub fn publish_realtime_flush_job(",
        "pub fn publish_realtime_batch_job(",
    ] {
        let prefix = source.split_once(producer).unwrap().0;
        assert!(
            prefix.ends_with("#[cfg(test)]\n"),
            "legacy producer must not compile into production: {producer}"
        );
    }
}
