use std::time::{Duration, Instant};

use cchooks::common::transcript::{
    read_bounded_jsonl_records, read_new_jsonl_limited_bounded, BoundedJsonlScan,
    TRANSCRIPT_RECORD_HARD_LIMIT_BYTES,
};
use cchooks::hooks::{
    StopBudget, DEFAULT_SESSION_END_BUDGET_MS, DEFAULT_STOP_BUDGET_MS,
    DEFAULT_STOP_TRANSCRIPT_SETTLE_MS, STOP_RECOVERY_BUDGET_MS,
    STOP_RECOVERY_CHECKPOINT_RESERVE_MS, STOP_RECOVERY_MAX_HTTP_TIMEOUT_MS,
    STOP_RECOVERY_TRANSCRIPT_SCAN_MAX_BYTES, STOP_TERMINAL_POLL_INTERVAL_MS,
};

#[test]
fn declared_stop_and_recovery_budgets_are_bounded_and_independent() {
    assert_eq!(DEFAULT_STOP_BUDGET_MS, 30_000);
    assert_eq!(DEFAULT_STOP_TRANSCRIPT_SETTLE_MS, 10_000);
    assert_eq!(STOP_TERMINAL_POLL_INTERVAL_MS, 500);
    assert_eq!(DEFAULT_SESSION_END_BUDGET_MS, 110_000);
    assert_eq!(STOP_RECOVERY_TRANSCRIPT_SCAN_MAX_BYTES, 256 * 1024);

    let recovery = StopBudget::for_scheduled_recovery(Instant::now());
    assert_eq!(
        recovery.total_budget(),
        Duration::from_millis(STOP_RECOVERY_BUDGET_MS)
    );
    assert_eq!(
        recovery.max_http_timeout(),
        Duration::from_millis(STOP_RECOVERY_MAX_HTTP_TIMEOUT_MS)
    );
    assert_eq!(
        recovery.checkpoint_reserve(),
        Duration::from_millis(STOP_RECOVERY_CHECKPOINT_RESERVE_MS)
    );
    assert!(recovery.processing_time_remaining().unwrap() <= Duration::from_millis(500));
}

#[test]
fn ordinary_hooks_cannot_execute_old_stop_recovery() {
    let main = include_str!("../src/main.rs");
    let payload_start = main.find("fn run_worker_payload(").unwrap();
    let payload_end = main[payload_start..]
        .find("fn run_hook_with_payload(")
        .map(|offset| payload_start + offset)
        .unwrap();
    let payload_path = &main[payload_start..payload_end];
    assert!(!payload_path.contains("recover_pending_stop_intent"));
    assert!(main.contains("drain_queue_with_maintenance"));
    assert!(main.contains("run_scheduled_stop_recovery_slice"));

    let worker = include_str!("../src/worker.rs");
    assert!(worker.contains("HOOK_BATCH_MAX_ITEMS: usize = 32"));
    assert!(worker.contains("HOOK_BATCH_MAX_WALL: Duration = Duration::from_millis(100)"));
    assert!(worker.contains("MAINTENANCE_WAIT_MAX: Duration = Duration::from_millis(500)"));
    assert!(worker.contains("STOP_RECOVERY_MAX_NO_PROGRESS_ATTEMPTS: u64 = 3"));
    assert!(worker.contains("terminal_quarantined"));
    assert!(worker.contains("Some(\"queued\")"));
}

#[test]
fn accounted_generation_and_tool_checks_precede_expensive_builders() {
    let source = include_str!("../src/common/otel.rs");
    let start = source.find("fn collect_turn_spans(").unwrap();
    let end = source[start..]
        .find("fn turn_end_nanos(")
        .map(|offset| start + offset)
        .unwrap();
    let collect = &source[start..end];
    assert!(
        collect.find("stored_generation_is_accounted").unwrap()
            < collect.find("build_generation_span(").unwrap()
    );
    assert!(
        collect.find("stored_tool_is_accounted").unwrap()
            < collect.find("build_tool_spans(").unwrap()
    );
    assert!(collect.contains("if !unaccounted_results.is_empty()"));
}

#[test]
fn limited_scan_resumes_without_rereading_bytes() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("session.jsonl");
    let line = serde_json::json!({"value": "é", "payload": "x".repeat(300_000)});
    let contents = format!("{line}\n{}\n", serde_json::json!({"value": 2}));
    std::fs::write(&path, contents.as_bytes()).unwrap();

    let (first, first_offset, first_buffer, first_more, first_terminal) =
        read_new_jsonl_limited_bounded(&path, 0, "", STOP_RECOVERY_TRANSCRIPT_SCAN_MAX_BYTES, None);
    assert_eq!(first_terminal, None);
    assert!(first.is_empty());
    assert!(first_more);
    assert!(first_offset > 0);
    assert!(!first_buffer.is_empty());
    let (second, second_offset, second_buffer, _, second_terminal) = read_new_jsonl_limited_bounded(
        &path,
        first_offset,
        &first_buffer,
        STOP_RECOVERY_TRANSCRIPT_SCAN_MAX_BYTES,
        None,
    );
    assert_eq!(second_terminal, None);
    assert_eq!(second.len(), 2);
    assert_eq!(second_offset as usize, contents.len());
    assert!(second_buffer.is_empty());
}

#[test]
fn record_hard_limit_and_plus_one_have_distinct_outcomes() {
    let dir = tempfile::tempdir().unwrap();
    let exact = dir.path().join("exact-limit.jsonl");
    std::fs::write(&exact, vec![b'x'; TRANSCRIPT_RECORD_HARD_LIMIT_BYTES]).unwrap();
    assert!(matches!(
        read_bounded_jsonl_records(&exact, 0, 256 * 1024, Duration::from_secs(2)).unwrap(),
        BoundedJsonlScan::WaitingForMoreData { record_start: 0 }
    ));

    let plus_one = dir.path().join("plus-one.jsonl");
    std::fs::write(
        &plus_one,
        vec![b'x'; TRANSCRIPT_RECORD_HARD_LIMIT_BYTES + 1],
    )
    .unwrap();
    assert!(matches!(
        read_bounded_jsonl_records(&plus_one, 0, 256 * 1024, Duration::from_secs(2)).unwrap(),
        BoundedJsonlScan::Terminal {
            reason: "record_too_large",
            record_start: 0,
            ..
        }
    ));
}

#[test]
fn windows_detach_and_offset_regressions_remain_covered() {
    let worker = include_str!("../src/worker.rs");
    for required in [
        "clear_inheritable_hook_stdio",
        "SetHandleInformation",
        "offset_write_does_not_require_delete_sharing",
        "offset_write_retries_transient_sharing_violation",
        "offset_write_reports_persistent_sharing_violation_after_bounded_retry",
    ] {
        assert!(
            worker.contains(required),
            "missing Windows regression contract: {required}"
        );
    }
    let async_contract = include_str!("async_hook_contract.rs");
    assert!(async_contract.contains("detached_worker_does_not_keep_hook_output_pipes_open"));
}
