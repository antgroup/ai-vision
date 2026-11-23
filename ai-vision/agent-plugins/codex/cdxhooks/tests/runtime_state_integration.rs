use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use cdxhooks::common::dirs;
use cdxhooks::common::logger::Logger;
use cdxhooks::common::runtime_state::{
    publish_immutable_json, DomainStateStore, ImmutablePublish, SessionOwner, StateDomain,
};
use cdxhooks::common::timing::PhaseRecorder;
use cdxhooks::hooks::langfuse::state::{load_state, save_state};
use cdxhooks::worker::{self, EnqueueOutcome, QueueDisposition};
use serde_json::json;

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

#[test]
fn flat_new_root_and_nested_legacy_are_distinct() {
    with_state_root(|root| {
        let key = dirs::session_key("session/one").unwrap();
        assert_eq!(
            dirs::session_state_dir("session/one").unwrap(),
            root.join(&key)
        );
        assert_eq!(
            dirs::legacy_session_state_file("session/one").unwrap(),
            root.join("sessions").join(key).join("state.json")
        );
        assert_eq!(
            dirs::session_domain_state_file("session/one", "state_turn.json").unwrap(),
            dirs::session_state_dir("session/one")
                .unwrap()
                .join("state_turn.json")
        );
    });
}

#[test]
fn domain_store_falls_back_once_and_never_writes_legacy() {
    with_state_root(|_| {
        let legacy = dirs::legacy_session_state_file("session").unwrap();
        dirs::ensure_private_dir(legacy.parent().unwrap()).unwrap();
        std::fs::write(
            &legacy,
            serde_json::to_vec(&json!({
                "session": {
                    "offset": 41,
                    "turn_count": 2,
                    "current_trace_context": {"trace_id": "abc"},
                    "vcs": {"branch": "main"},
                    "unowned_extension": true
                }
            }))
            .unwrap(),
        )
        .unwrap();
        let legacy_before = std::fs::read(&legacy).unwrap();

        let stop = DomainStateStore::for_session("session", StateDomain::Stop).unwrap();
        assert_eq!(stop.load().unwrap()["offset"], 41);
        let (_, changed) = stop
            .update(|state| {
                state["offset"] = json!(42);
                Ok(())
            })
            .unwrap();
        assert!(changed);
        assert_eq!(std::fs::read(&legacy).unwrap(), legacy_before);
        assert_eq!(stop.load().unwrap()["offset"], 42);

        std::fs::write(stop.state_file(), b"").unwrap();
        assert_eq!(stop.load().unwrap(), json!({}));
        assert_eq!(std::fs::read(&legacy).unwrap(), legacy_before);
    });
}

#[test]
fn parent_stopped_belongs_to_stop_domain() {
    assert_eq!(StateDomain::for_field("parent_stopped"), StateDomain::Stop);
}

#[test]
fn composite_adapter_reads_legacy_and_writes_only_flat_domains() {
    with_state_root(|_| {
        let session = "compat/session";
        let legacy = dirs::legacy_session_state_file(session).unwrap();
        dirs::ensure_private_dir(legacy.parent().unwrap()).unwrap();
        std::fs::write(
            &legacy,
            serde_json::to_vec(&json!({session: {
                "offset": 7,
                "current_trace_context": {"trace_id": "abc"},
                "vcs": {"branch": "main"},
                "custom": "kept"
            }}))
            .unwrap(),
        )
        .unwrap();
        let identity = dirs::session_identity_file(session).unwrap();
        publish_immutable_json(&identity, &json!({"session_id": session})).unwrap();
        let compat = dirs::session_state_file(session).unwrap();
        let loaded = load_state(&compat);
        assert_eq!(loaded[session]["offset"], 7);
        std::fs::write(
            dirs::session_domain_state_file(session, "state_tools.json").unwrap(),
            b"{\"events\":{}}",
        )
        .unwrap();
        assert_eq!(
            load_state(&compat)[session]["offset"],
            7,
            "a new Tool domain must not suppress legacy Stop fallback"
        );
        save_state(&compat, &loaded).unwrap();

        assert!(!compat.exists());
        assert_eq!(
            std::fs::read_to_string(
                dirs::session_domain_state_file(session, "state_stop.json").unwrap()
            )
            .ok()
            .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
            .unwrap()["offset"],
            7
        );
        assert_eq!(
            load_state(&compat)[session]["custom"],
            "kept",
            "unknown fields must survive in attribution domain"
        );
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&std::fs::read_to_string(&legacy).unwrap())
                .unwrap()[session]["offset"],
            7,
            "legacy file is read-only"
        );
    });
}

#[test]
fn changed_domain_only_write_and_immutable_publish_are_idempotent() {
    with_state_root(|_| {
        let store = DomainStateStore::for_session("session", StateDomain::Turn).unwrap();
        let (_, changed) = store.update(|_| Ok(())).unwrap();
        assert!(!changed);
        assert!(!store.state_file().exists());

        let fact = dirs::tool_event_file("session", "tool/1", "pre").unwrap();
        let payload = json!({"schema_version": 1, "tool_use_id": "tool/1"});
        assert_eq!(
            publish_immutable_json(&fact, &payload).unwrap(),
            ImmutablePublish::Created
        );
        assert_eq!(
            publish_immutable_json(&fact, &payload).unwrap(),
            ImmutablePublish::ExistingIdentical
        );
        assert!(publish_immutable_json(&fact, &json!({"different": true})).is_err());
    });
}

#[cfg(unix)]
#[test]
fn session_owner_is_process_lifetime_and_has_no_lease() {
    with_state_root(|_| {
        let first = SessionOwner::acquire("session", Duration::from_millis(50)).unwrap();
        assert!(SessionOwner::try_acquire("session").unwrap().is_none());
        let busy = SessionOwner::acquire("session", Duration::from_millis(25));
        assert!(busy.is_err());
        drop(first);
        assert!(SessionOwner::try_acquire("session").unwrap().is_some());
        SessionOwner::acquire("session", Duration::from_millis(50)).unwrap();
    });
}

#[cfg(unix)]
#[test]
fn explicit_unlock_releases_lock_even_with_forked_child() {
    with_state_root(|_| {
        let owner =
            SessionOwner::acquire("fork-handoff-session", Duration::from_millis(50)).unwrap();
        let child = unsafe { libc::fork() };
        assert!(
            child >= 0,
            "fork failed: {}",
            std::io::Error::last_os_error()
        );
        if child == 0 {
            unsafe {
                libc::sleep(1);
                libc::_exit(0);
            }
        }

        drop(owner);
        // With explicit LOCK_UN, parent drop releases the lock immediately
        // even though the child still has an inherited fd.
        assert!(
            SessionOwner::try_acquire("fork-handoff-session")
                .unwrap()
                .is_some(),
            "explicit unlock must release lock regardless of forked child"
        );
        let mut status = 0;
        assert_eq!(unsafe { libc::waitpid(child, &mut status, 0) }, child);
    });
}

#[cfg(unix)]
#[test]
fn session_owner_releases_lock_on_drop_even_with_forked_subprocess() {
    with_state_root(|_| {
        let session_id = "owner-drop-release-session";
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let mut child = std::process::Command::new("sleep")
            .arg("2")
            .spawn()
            .unwrap();
        drop(owner);
        assert!(
            SessionOwner::try_acquire(session_id).unwrap().is_some(),
            "lock must be released after drop even with live child process"
        );
        let _ = child.kill();
        let _ = child.wait();
    });
}

#[cfg(unix)]
#[test]
fn blocking_session_owner_waits_until_current_worker_exits() {
    with_state_root(|_| {
        let first = SessionOwner::acquire("blocking-session", Duration::from_millis(50)).unwrap();
        let (sender, receiver) = std::sync::mpsc::channel();
        let waiter = std::thread::spawn(move || {
            sender.send(SessionOwner::acquire_blocking("blocking-session").is_ok())
        });

        assert!(receiver.recv_timeout(Duration::from_millis(50)).is_err());
        drop(first);
        assert!(receiver.recv_timeout(Duration::from_secs(1)).unwrap());
        waiter.join().unwrap().unwrap();
    });
}

#[cfg(unix)]
#[test]
fn unacknowledged_queue_item_is_replayed_after_worker_panic() {
    with_state_root(|root| {
        let session_id = "queue-crash-session";
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let logger = Logger::new(root.join("queue.log"), true);
        let payload = json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "sequence": 7
        });
        assert_eq!(
            worker::enqueue_and_wake(session_id, &payload, 1, &logger).unwrap(),
            EnqueueOutcome::Appended
        );

        let panicked = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _ = worker::drain_queue(session_id, Some(owner), |_| {
                panic!("simulated worker crash before queue acknowledgement")
            });
        }));
        assert!(panicked.is_err());

        let owner = SessionOwner::try_acquire(session_id).unwrap().unwrap();
        let mut replayed = Vec::new();
        let stats = worker::drain_queue(session_id, Some(owner), |item| {
            replayed.push(item.payload);
            QueueDisposition::Ack
        })
        .unwrap();
        assert_eq!(stats.processed, 1);
        assert_eq!(replayed, vec![payload]);
        assert_eq!(
            std::fs::metadata(dirs::session_worker_queue_file(session_id).unwrap())
                .unwrap()
                .len(),
            0
        );
        assert_eq!(
            std::fs::read_to_string(dirs::session_worker_queue_offset_file(session_id).unwrap())
                .unwrap(),
            "0"
        );
    });
}

#[cfg(unix)]
#[test]
fn queue_capacity_is_bounded_and_overflow_is_accounted() {
    with_state_root(|root| {
        let previous_limit = std::env::var(worker::QUEUE_MAX_BYTES_ENV).ok();
        std::env::set_var(worker::QUEUE_MAX_BYTES_ENV, "6144");
        let session_id = "bounded-queue-session";
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let logger = Logger::new(root.join("bounded.log"), true);
        let payload = json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "blob": "x".repeat(5000)
        });
        assert_eq!(
            worker::enqueue_and_wake(session_id, &payload, 700, &logger).unwrap(),
            EnqueueOutcome::Appended
        );
        let overflow = worker::enqueue_and_wake(session_id, &payload, 700, &logger).unwrap_err();
        assert!(
            overflow.contains("queue capacity exceeded"),
            "queue should reject an item above the byte limit"
        );
        assert_eq!(worker::pending_count(session_id).unwrap(), 1);
        assert_eq!(worker::overflow_count(session_id).unwrap(), 1);
        assert!(
            std::fs::metadata(dirs::session_worker_queue_file(session_id).unwrap())
                .unwrap()
                .len()
                <= 6144
        );
        drop(owner);
        match previous_limit {
            Some(value) => std::env::set_var(worker::QUEUE_MAX_BYTES_ENV, value),
            None => std::env::remove_var(worker::QUEUE_MAX_BYTES_ENV),
        }
    });
}

#[cfg(unix)]
#[test]
fn queue_item_cap_rejects_new_observation_but_coalesces_duplicate_lifecycle() {
    with_state_root(|root| {
        let previous_limit = std::env::var(worker::QUEUE_MAX_ITEMS_ENV).ok();
        std::env::set_var(worker::QUEUE_MAX_ITEMS_ENV, "2");
        let session_id = "bounded-item-queue-session";
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let logger = Logger::new(root.join("bounded-items.log"), true);
        let stop = json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": "/tmp/session.jsonl"
        });
        let observation = json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "sequence": 1
        });
        assert_eq!(
            worker::enqueue_and_wake(session_id, &stop, 1, &logger).unwrap(),
            EnqueueOutcome::Appended
        );
        assert_eq!(
            worker::enqueue_and_wake(session_id, &observation, 1, &logger).unwrap(),
            EnqueueOutcome::Appended
        );
        assert_eq!(
            worker::enqueue_and_wake(session_id, &stop, 1, &logger).unwrap(),
            EnqueueOutcome::Coalesced
        );
        let rejected = json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "sequence": 2
        });
        let error = worker::enqueue_and_wake(session_id, &rejected, 1, &logger).unwrap_err();
        assert!(
            error.contains("pending_items=2"),
            "overflow should report the number of pending items"
        );
        assert_eq!(worker::pending_count(session_id).unwrap(), 2);
        assert_eq!(worker::overflow_count(session_id).unwrap(), 1);
        drop(owner);
        match previous_limit {
            Some(value) => std::env::set_var(worker::QUEUE_MAX_ITEMS_ENV, value),
            None => std::env::remove_var(worker::QUEUE_MAX_ITEMS_ENV),
        }
    });
}

#[cfg(unix)]
#[test]
fn appender_never_compacts_beneath_an_inflight_worker_record() {
    with_state_root(|root| {
        let previous_limit = std::env::var(worker::QUEUE_MAX_BYTES_ENV).ok();
        let session_id = "inflight-compaction-session";
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let logger = Logger::new(root.join("inflight.log"), true);
        let prefix = json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "sequence": 1,
            "blob": "p".repeat(64)
        });
        let inflight = json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "sequence": 2,
            "blob": "a".repeat(512)
        });
        for payload in [&prefix, &inflight] {
            assert_eq!(
                worker::enqueue_and_wake(session_id, payload, 1, &logger).unwrap(),
                EnqueueOutcome::Appended
            );
        }
        let queue_path = dirs::session_worker_queue_file(session_id).unwrap();
        let before = std::fs::read(&queue_path).unwrap();
        let prefix_len = before.iter().position(|byte| *byte == b'\n').unwrap() + 1;
        let rejected = json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "sequence": 3,
            "blob": "b".repeat(768)
        });
        let estimated_record_len = serde_json::to_vec(&json!({
            "queued_at_ns": "0000000000000000000",
            "payload": rejected.clone()
        }))
        .unwrap()
        .len()
            + 1;
        let max_bytes = before.len() + estimated_record_len - 1;
        assert!(prefix_len < max_bytes / 2);
        std::env::set_var(worker::QUEUE_MAX_BYTES_ENV, max_bytes.to_string());

        let (inflight_tx, inflight_rx) = std::sync::mpsc::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let worker_thread = std::thread::spawn(move || {
            let mut seen = Vec::new();
            let stats = worker::drain_queue(session_id, Some(owner), |item| {
                let sequence = item.payload["sequence"].as_u64().unwrap();
                seen.push(sequence);
                if sequence == 2 {
                    inflight_tx.send(()).unwrap();
                    release_rx.recv().unwrap();
                }
                QueueDisposition::Ack
            })
            .unwrap();
            (stats, seen)
        });
        inflight_rx.recv_timeout(Duration::from_secs(2)).unwrap();

        let overflow = worker::enqueue_and_wake(session_id, &rejected, 1, &logger).unwrap_err();
        assert!(
            overflow.contains("queue capacity exceeded"),
            "queue should count in-flight items toward the item limit"
        );
        assert_eq!(std::fs::read(&queue_path).unwrap(), before);
        release_tx.send(()).unwrap();
        let (stats, seen) = worker_thread.join().unwrap();
        assert_eq!(stats.processed, 2);
        assert_eq!(seen, vec![1, 2]);
        assert_eq!(worker::pending_count(session_id).unwrap(), 0);
        assert_eq!(worker::overflow_count(session_id).unwrap(), 1);

        match previous_limit {
            Some(value) => std::env::set_var(worker::QUEUE_MAX_BYTES_ENV, value),
            None => std::env::remove_var(worker::QUEUE_MAX_BYTES_ENV),
        }
    });
}

#[cfg(unix)]
#[test]
fn deterministic_poison_is_retried_then_quarantined_without_blocking_queue() {
    with_state_root(|root| {
        let session_id = "poison-queue-session";
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let logger = Logger::new(root.join("poison.log"), true);
        let payload = json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "sequence": 9
        });
        assert_eq!(
            worker::enqueue_and_wake(session_id, &payload, 1, &logger).unwrap(),
            EnqueueOutcome::Appended
        );
        let mut attempts = 0usize;
        let stats = worker::drain_queue(session_id, Some(owner), |_| {
            attempts += 1;
            QueueDisposition::Retry("deterministic poison".to_string())
        })
        .unwrap();
        assert_eq!(attempts, 3);
        assert_eq!(stats.retried, 3);
        assert_eq!(stats.quarantined, 1);
        assert_eq!(worker::pending_count(session_id).unwrap(), 0);
        let failed = dirs::session_state_dir(session_id)
            .unwrap()
            .join("worker.queue.failed.jsonl");
        let record: serde_json::Value =
            serde_json::from_str(std::fs::read_to_string(failed).unwrap().trim()).unwrap();
        assert_eq!(record["attempts"], 3);
        assert_eq!(record["envelope"]["payload"]["sequence"], 9);
    });
}

#[cfg(unix)]
#[test]
fn transient_retry_remains_queued_past_poison_limit_until_it_succeeds() {
    with_state_root(|root| {
        let session_id = "transient-retry-queue-session";
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let logger = Logger::new(root.join("transient-retry.log"), true);
        let payload = json!({
            "hook_event_name": "PostToolUse",
            "session_id": session_id,
            "sequence": 10
        });
        assert_eq!(
            worker::enqueue_and_wake(session_id, &payload, 1, &logger).unwrap(),
            EnqueueOutcome::Appended
        );

        let mut attempts = 0usize;
        let stats = worker::drain_queue(session_id, Some(owner), |_| {
            attempts += 1;
            if attempts <= 3 {
                QueueDisposition::RetryDurable("state lock unavailable".to_string())
            } else {
                QueueDisposition::Ack
            }
        })
        .unwrap();

        assert_eq!(attempts, 4);
        assert_eq!(stats.retried, 3);
        assert_eq!(stats.processed, 1);
        assert_eq!(stats.quarantined, 0);
        assert_eq!(worker::pending_count(session_id).unwrap(), 0);
        assert!(!dirs::session_state_dir(session_id)
            .unwrap()
            .join("worker.queue.failed.jsonl")
            .exists());
    });
}

#[test]
fn durable_retry_has_an_independent_terminal_ceiling() {
    with_state_root(|root| {
        let previous = std::env::var(worker::MAX_DURABLE_ATTEMPTS_ENV).ok();
        std::env::set_var(worker::MAX_DURABLE_ATTEMPTS_ENV, "2");
        let session_id = "durable-retry-ceiling-session";
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let logger = Logger::new(root.join("durable-retry-ceiling.log"), true);
        let payload = json!({
            "hook_event_name": "PostToolUse",
            "session_id": session_id,
            "tool_use_id": "tool-durable-ceiling"
        });
        assert_eq!(
            worker::enqueue_and_wake(session_id, &payload, 1, &logger).unwrap(),
            EnqueueOutcome::Appended
        );

        let stats = worker::drain_queue(session_id, Some(owner), |_| {
            QueueDisposition::RetryDurable("persistent state lock contention".to_string())
        })
        .unwrap();
        assert_eq!(stats.retried, 2);
        assert_eq!(stats.quarantined, 1);
        assert_eq!(worker::pending_count(session_id).unwrap(), 0);
        let failed = std::fs::read_to_string(
            dirs::session_state_dir(session_id)
                .unwrap()
                .join("worker.queue.failed.jsonl"),
        )
        .unwrap();
        assert!(failed.contains("durable retry limit exhausted"));

        match previous {
            Some(value) => std::env::set_var(worker::MAX_DURABLE_ATTEMPTS_ENV, value),
            None => std::env::remove_var(worker::MAX_DURABLE_ATTEMPTS_ENV),
        }
    });
}

#[cfg(unix)]
#[test]
fn transient_retries_do_not_consume_the_standard_poison_budget() {
    with_state_root(|root| {
        let session_id = "transient-then-poison-session";
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let logger = Logger::new(root.join("transient-then-poison.log"), true);
        let payload = json!({
            "hook_event_name": "PostToolUse",
            "session_id": session_id,
            "sequence": 12
        });
        assert_eq!(
            worker::enqueue_and_wake(session_id, &payload, 1, &logger).unwrap(),
            EnqueueOutcome::Appended
        );

        let mut durable_attempts = 0usize;
        let mut standard_attempts = 0usize;
        let stats = worker::drain_queue(session_id, Some(owner), |_| {
            if durable_attempts < 3 {
                durable_attempts += 1;
                QueueDisposition::RetryDurable("state lock unavailable".to_string())
            } else {
                standard_attempts += 1;
                QueueDisposition::Retry("deterministic poison".to_string())
            }
        })
        .unwrap();

        assert_eq!(durable_attempts, 3);
        assert_eq!(standard_attempts, 3);
        assert_eq!(stats.retried, 6);
        assert_eq!(stats.quarantined, 1);
        assert_eq!(worker::pending_count(session_id).unwrap(), 0);
        let failed = dirs::session_state_dir(session_id)
            .unwrap()
            .join("worker.queue.failed.jsonl");
        let record: serde_json::Value =
            serde_json::from_str(std::fs::read_to_string(failed).unwrap().trim()).unwrap();
        assert_eq!(record["attempts"], 3);
    });
}

#[cfg(unix)]
#[test]
fn transient_retry_runtime_cap_preserves_record_for_worker_handoff() {
    with_state_root(|root| {
        let previous = std::env::var(worker::WORKER_MAX_RUNTIME_MS_ENV).ok();
        std::env::set_var(worker::WORKER_MAX_RUNTIME_MS_ENV, "1");
        let session_id = "transient-retry-handoff-session";
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let logger = Logger::new(root.join("transient-handoff.log"), true);
        let payload = json!({
            "hook_event_name": "PostToolUse",
            "session_id": session_id,
            "sequence": 11
        });
        assert_eq!(
            worker::enqueue_and_wake(session_id, &payload, 1, &logger).unwrap(),
            EnqueueOutcome::Appended
        );

        let first = worker::drain_queue(session_id, Some(owner), |_| {
            QueueDisposition::RetryDurable("state lock unavailable".to_string())
        })
        .unwrap();
        assert_eq!(first.retried, 1);
        assert!(first.runtime_exhausted);
        assert_eq!(worker::pending_count(session_id).unwrap(), 1);

        let replacement = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let second =
            worker::drain_queue(session_id, Some(replacement), |_| QueueDisposition::Ack).unwrap();
        assert_eq!(second.processed, 1);
        assert_eq!(worker::pending_count(session_id).unwrap(), 0);

        match previous {
            Some(value) => std::env::set_var(worker::WORKER_MAX_RUNTIME_MS_ENV, value),
            None => std::env::remove_var(worker::WORKER_MAX_RUNTIME_MS_ENV),
        }
    });
}

#[cfg(unix)]
#[test]
fn worker_runtime_cap_releases_owner_with_remaining_queue_for_handoff() {
    with_state_root(|root| {
        let previous = std::env::var(worker::WORKER_MAX_RUNTIME_MS_ENV).ok();
        std::env::set_var(worker::WORKER_MAX_RUNTIME_MS_ENV, "1");
        let session_id = "runtime-capped-queue-session";
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let logger = Logger::new(root.join("runtime-cap.log"), true);
        for sequence in [1, 2] {
            let payload = json!({
                "hook_event_name": "PreCompact",
                "session_id": session_id,
                "sequence": sequence
            });
            assert_eq!(
                worker::enqueue_and_wake(session_id, &payload, 1, &logger).unwrap(),
                EnqueueOutcome::Appended
            );
        }
        let mut seen = Vec::new();
        let stats = worker::drain_queue(session_id, Some(owner), |item| {
            seen.push(item.payload["sequence"].as_u64().unwrap());
            std::thread::sleep(Duration::from_millis(5));
            QueueDisposition::Ack
        })
        .unwrap();
        assert_eq!(seen, vec![1]);
        assert_eq!(stats.processed, 1);
        assert!(stats.runtime_exhausted);
        assert_eq!(worker::pending_count(session_id).unwrap(), 1);
        assert!(SessionOwner::try_acquire(session_id).unwrap().is_some());

        match previous {
            Some(value) => std::env::set_var(worker::WORKER_MAX_RUNTIME_MS_ENV, value),
            None => std::env::remove_var(worker::WORKER_MAX_RUNTIME_MS_ENV),
        }
    });
}

#[cfg(unix)]
#[test]
fn partial_tail_and_stale_offset_are_repaired_before_recovery_or_append() {
    with_state_root(|root| {
        let session_id = "partial-queue-session";
        let runtime_dir = dirs::session_state_dir(session_id).unwrap();
        std::fs::create_dir_all(&runtime_dir).unwrap();
        std::fs::write(runtime_dir.join("worker.queue.jsonl"), b"{partial").unwrap();
        std::fs::write(runtime_dir.join("worker.queue.offset.json"), b"9999").unwrap();
        assert_eq!(worker::pending_count(session_id).unwrap(), 0);
        assert!(std::fs::read(runtime_dir.join("worker.queue.jsonl"))
            .unwrap()
            .is_empty());
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let logger = Logger::new(root.join("partial.log"), true);
        let payload = json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "sequence": 11
        });
        assert_eq!(
            worker::enqueue_and_wake(session_id, &payload, 1, &logger).unwrap(),
            EnqueueOutcome::Appended
        );
        let queue = std::fs::read_to_string(runtime_dir.join("worker.queue.jsonl")).unwrap();
        assert_eq!(queue.lines().count(), 1);
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(queue.trim()).unwrap()["payload"]["sequence"],
            11
        );
        let mut seen = Vec::new();
        worker::drain_queue(session_id, Some(owner), |item| {
            seen.push(item.payload);
            QueueDisposition::Ack
        })
        .unwrap();
        assert_eq!(seen, vec![payload]);
    });
}

#[cfg(unix)]
#[test]
fn corrupt_complete_record_is_dead_lettered_once_and_drain_advances() {
    with_state_root(|root| {
        let session_id = "corrupt-queue-session";
        let runtime_dir = dirs::session_state_dir(session_id).unwrap();
        std::fs::create_dir_all(&runtime_dir).unwrap();
        std::fs::write(
            runtime_dir.join("worker.queue.jsonl"),
            b"{not-json}\n{\"queued_at_ns\":\"1\",\"payload\":{\"sequence\":2}}\n",
        )
        .unwrap();
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        let mut seen = Vec::new();
        let stats = worker::drain_queue(session_id, Some(owner), |item| {
            seen.push(item.payload["sequence"].as_u64().unwrap());
            QueueDisposition::Ack
        })
        .unwrap();
        assert_eq!(seen, vec![2]);
        assert_eq!(stats.processed, 1);
        assert_eq!(stats.quarantined, 1);
        assert_eq!(worker::pending_count(session_id).unwrap(), 0);

        let failed = runtime_dir.join("worker.queue.failed.jsonl");
        let records = std::fs::read_to_string(failed).unwrap();
        assert_eq!(records.lines().count(), 1);
        let record: serde_json::Value = serde_json::from_str(records.trim()).unwrap();
        assert_eq!(record["attempts"], 1);
        assert!(record["reason"]
            .as_str()
            .unwrap()
            .contains("invalid queue JSON"));
        assert_eq!(record["envelope"], "{not-json}\n");
        assert!(std::fs::read(runtime_dir.join("worker.queue.jsonl"))
            .unwrap()
            .is_empty());

        let logger = Logger::new(root.join("corrupt.log"), true);
        let next = json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "sequence": 3
        });
        let held = SessionOwner::acquire(session_id, Duration::from_millis(50)).unwrap();
        assert_eq!(
            worker::enqueue_and_wake(session_id, &next, 1, &logger).unwrap(),
            EnqueueOutcome::Appended
        );
        drop(held);
    });
}

#[test]
fn phase_recorder_emits_one_payload_free_record() {
    let root = tempfile::tempdir().unwrap();
    let log = root.path().join("logs/cdxhooks_key.log");
    let logger = Logger::new(log.clone(), true);
    let mut timing = PhaseRecorder::new("parent", "PreToolUse", "opaque-session-key");
    timing.mark("validate");
    timing.mark("state_publish");
    timing.record(&logger, "ok");

    let contents = std::fs::read_to_string(log).unwrap();
    assert_eq!(contents.lines().count(), 1);
    assert!(contents.contains("cdxhooks.phase_timing"));
    assert!(contents.contains("\"execution\":\"sync\""));
    assert!(contents.contains("\"elapsed_ms\""));
    assert!(contents.contains("\"total\""));
    assert!(!contents.contains("elapsed_us"));
    assert!(!contents.contains("prompt"));
    assert!(!contents.contains("tool_input"));
}
