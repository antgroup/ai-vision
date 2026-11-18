//! Concurrent send primitive for short-lived hook processes.
//!
//! A single hook invocation may need to POST auxiliary telemetry to independent
//! endpoints such as dynamic upload and turn-digest upload. These sends are
//! fire-and-forget and target different hosts, so they can run concurrently.
//! Langfuse Stop/SessionEnd exports intentionally bypass this queue because each
//! accepted turn must be checkpointed before the transcript cursor can advance.
//!
//! Design constraints (see `.omc/plans/cchooks-concurrent-io-refactor.md`):
//! - No async runtime (tokio): this process lives tens to hundreds of ms; a
//!   reactor's startup cost would *increase* latency for only ~3-way fan-out.
//! - Each task captures only owned data (`Vec<u8>`, `String`, owned `ureq::Agent`,
//!   a cloned `Logger`). Tasks must be `Send`; they must NOT borrow `&CommonContext`,
//!   whose `RefCell` fields are not safe to share across threads.
//! - Every task body is wrapped in `catch_unwind`. Under `panic = "unwind"`, a
//!   panic inside a scoped thread is re-raised at the scope join, which would
//!   unwind through `run_hook` and skip post-dispatch accounting. Catching here
//!   keeps a panicking send from corrupting the main flow.

/// A unit of fire-and-forget network work, runnable on its own thread.
pub type SendTask = Box<dyn FnOnce() + Send + 'static>;

/// Run a batch of independent send tasks, returning each task's wall-clock
/// duration (in input order) so callers can quantify the concurrency benefit:
/// `saved = sum(durations) - max(durations)` (serial total minus parallel wall).
///
/// - 0 tasks: no-op, empty vec.
/// - 1 task: run inline on the current thread (avoids thread-spawn overhead for
///   the common single-send configuration).
/// - 2+ tasks: run concurrently via `std::thread::scope` and join all of them.
///
/// Each task is individually guarded by `catch_unwind`, so a panic in one send
/// neither aborts the others nor propagates through the scope join.
pub fn run_send_tasks(tasks: Vec<SendTask>) -> Vec<std::time::Duration> {
    if tasks.len() < 2 {
        // 0 or 1: run inline, no thread spawn.
        return tasks.into_iter().map(run_one).collect();
    }

    std::thread::scope(|scope| {
        let handles: Vec<_> = tasks
            .into_iter()
            .map(|task| scope.spawn(move || run_one(task)))
            .collect();
        // Join in spawn order to preserve input-order durations. A panic in the
        // worker is already caught by run_one, so join() won't observe one;
        // fall back to zero if it somehow does.
        handles
            .into_iter()
            .map(|h| h.join().unwrap_or_default())
            .collect()
    })
}

/// Execute a single task, swallowing any panic so it cannot escape the thread.
/// Returns the task's wall-clock duration.
fn run_one(task: SendTask) -> std::time::Duration {
    let started = std::time::Instant::now();
    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(task));
    started.elapsed()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    #[test]
    fn test_zero_tasks_is_noop() {
        // Should not panic or hang.
        run_send_tasks(Vec::new());
    }

    #[test]
    fn test_single_task_runs_inline() {
        let counter = Arc::new(AtomicUsize::new(0));
        let c = Arc::clone(&counter);
        let tasks: Vec<SendTask> = vec![Box::new(move || {
            c.fetch_add(1, Ordering::SeqCst);
        })];
        run_send_tasks(tasks);
        assert_eq!(counter.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn test_multiple_tasks_all_run() {
        let counter = Arc::new(AtomicUsize::new(0));
        let mut tasks: Vec<SendTask> = Vec::new();
        for _ in 0..5 {
            let c = Arc::clone(&counter);
            tasks.push(Box::new(move || {
                c.fetch_add(1, Ordering::SeqCst);
            }));
        }
        run_send_tasks(tasks);
        assert_eq!(counter.load(Ordering::SeqCst), 5);
    }

    #[test]
    fn test_panicking_task_does_not_abort_others() {
        let counter = Arc::new(AtomicUsize::new(0));
        let mut tasks: Vec<SendTask> = Vec::new();

        // First task panics.
        tasks.push(Box::new(|| panic!("send failure")));

        // Remaining tasks must still run.
        for _ in 0..3 {
            let c = Arc::clone(&counter);
            tasks.push(Box::new(move || {
                c.fetch_add(1, Ordering::SeqCst);
            }));
        }

        // Must not propagate the panic.
        run_send_tasks(tasks);
        assert_eq!(counter.load(Ordering::SeqCst), 3);
    }

    /// Auxiliary fire-and-forget failures remain isolated within this queue.
    /// Langfuse cursor semantics are covered by the progressive export tests.
    #[test]
    fn test_failed_auxiliary_send_does_not_block_other_tasks() {
        let errors = Arc::new(std::sync::Mutex::new(Vec::<String>::new()));
        let completions = Arc::new(AtomicUsize::new(0));
        let e = Arc::clone(&errors);
        let c = Arc::clone(&completions);
        let tasks: Vec<SendTask> = vec![
            Box::new(move || {
                e.lock().unwrap().push("export failed".to_string());
            }),
            Box::new(move || {
                c.fetch_add(1, Ordering::SeqCst);
            }),
        ];

        run_send_tasks(tasks);

        assert_eq!(completions.load(Ordering::SeqCst), 1);
        assert_eq!(
            errors.lock().unwrap().len(),
            1,
            "failure should be recorded for later metric emission"
        );
    }
}
