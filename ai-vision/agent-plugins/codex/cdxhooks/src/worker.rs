use std::collections::BTreeMap;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::common::dirs;
use crate::common::hook::env_is_truthy;
use crate::common::logger::Logger;
use crate::common::runtime_state::{self, SessionOwner};

pub const COMMAND: &str = "__worker";
pub const MODE_ENV: &str = "CDXHOOKS_INTERNAL_WORKER";
pub const SESSION_ENV: &str = "CDXHOOKS_WORKER_SESSION_ID";
pub const START_TOKEN_ENV: &str = "CDXHOOKS_WORKER_START_TOKEN";
/// Kept for old direct/admin invocations. New workers never inherit an fd.
pub const QUEUE_MAX_BYTES_ENV: &str = "CDXHOOKS_WORKER_QUEUE_MAX_BYTES";
pub const QUEUE_MAX_ITEMS_ENV: &str = "CDXHOOKS_WORKER_QUEUE_MAX_ITEMS";
pub const WORKER_MAX_RUNTIME_MS_ENV: &str = "CDXHOOKS_WORKER_MAX_RUNTIME_MS";
pub const MAX_DELIVERY_ATTEMPTS_ENV: &str = "CDXHOOKS_WORKER_MAX_DELIVERY_ATTEMPTS";
pub const MAX_DURABLE_ATTEMPTS_ENV: &str = "CDXHOOKS_WORKER_MAX_DURABLE_ATTEMPTS";
pub const MAX_ENVELOPE_BYTES: usize = 64 * 1024 * 1024;
pub const DEFAULT_QUEUE_MAX_BYTES: u64 = 256 * 1024 * 1024;
pub const DEFAULT_QUEUE_MAX_ITEMS: usize = 4096;
pub const DEFAULT_WORKER_MAX_RUNTIME_MS: u64 = 10 * 60 * 1000;
const QUEUE_COMPACT_MIN_BYTES: u64 = 8 * 1024 * 1024;
const DEFAULT_MAX_DELIVERY_ATTEMPTS: u32 = 3;
const DEFAULT_MAX_DURABLE_ATTEMPTS: u32 = 600;
const MAX_FAILED_LOG_BYTES: u64 = 128 * 1024 * 1024;
const START_MARKER_TTL_NS: u128 = 30_000_000_000;
const WORKER_START_WAIT: std::time::Duration = std::time::Duration::from_secs(30);
const WORKER_START_POLL: std::time::Duration = std::time::Duration::from_millis(2);
const WORKER_HANDOFF_ATTEMPTS: usize = 3;
const QUEUE_LOCK_WAIT: std::time::Duration = std::time::Duration::from_secs(2);
const OFFSET_WRITE_RETRY_DELAYS_MS: [u64; 10] = [0, 1, 2, 4, 8, 16, 32, 64, 128, 250];
const HOOK_BATCH_MAX_ITEMS: usize = 32;
const HOOK_BATCH_MAX_WALL: Duration = Duration::from_millis(100);
const MAINTENANCE_WAIT_MAX: Duration = Duration::from_millis(500);

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn GetStdHandle(std_handle: u32) -> *mut std::ffi::c_void;
    fn SetHandleInformation(handle: *mut std::ffi::c_void, mask: u32, flags: u32) -> i32;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EnqueueOutcome {
    Spawned,
    Appended,
    Coalesced,
}

#[derive(Debug)]
pub struct QueueItem {
    pub env: BTreeMap<String, String>,
    pub payload: Value,
    pub queued_at_ns: u128,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum QueueDisposition {
    Ack,
    Retry(String),
    /// Retry a failure that is known to be transient without dead-lettering
    /// the durable queue record. The per-process runtime cap still forces a
    /// handoff, so one worker cannot live forever while waiting for recovery.
    RetryDurable(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MaintenanceDisposition {
    Idle,
    Waiting(Duration),
    DidWork,
}

#[derive(Debug, Serialize, Deserialize)]
struct QueueEnvelope {
    #[serde(default)]
    env: BTreeMap<String, String>,
    queued_at_ns: String,
    payload: Value,
}

struct QueueRecord {
    item: QueueItem,
    raw: Vec<u8>,
    offset: u64,
    next_offset: u64,
}

enum QueueRead {
    Item(QueueRecord),
    Corrupt {
        raw: Vec<u8>,
        offset: u64,
        next_offset: u64,
        reason: String,
    },
    Empty,
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct DrainStats {
    pub processed: usize,
    pub retried: usize,
    pub quarantined: usize,
    pub runtime_exhausted: bool,
    pub maintenance_slices: usize,
}

#[derive(Debug, Serialize, Deserialize)]
struct RetryState {
    offset: u64,
    attempts: u32,
    last_error: String,
    #[serde(default)]
    kind: RetryKind,
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum RetryKind {
    #[default]
    Standard,
    Durable,
}

#[derive(Debug, Serialize, Deserialize)]
struct OverflowState {
    schema_version: u32,
    dropped_items: u64,
    first_dropped_at_ns: String,
    last_dropped_at_ns: String,
    last_event: String,
    pending_bytes: u64,
    max_bytes: u64,
    #[serde(default)]
    pending_items: usize,
    #[serde(default)]
    max_items: usize,
}

#[derive(Debug)]
enum AppendOutcome {
    Appended,
    Coalesced,
}

#[derive(Debug, Serialize, Deserialize)]
struct WorkerStartMarker {
    schema_version: u32,
    token: String,
    created_at_ns: String,
    expires_at_ns: String,
    #[serde(default = "starting_marker_status")]
    status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    last_error: Option<String>,
}

enum StartClaim {
    Existing,
    Claimed(String),
}

#[derive(Debug)]
enum AppendFailure {
    Full(String),
    Other(String),
}

pub fn is_worker_mode() -> bool {
    env_is_truthy(MODE_ENV)
}

pub fn inherited_session_id() -> Option<String> {
    std::env::var(SESSION_ENV)
        .ok()
        .filter(|session_id| !session_id.trim().is_empty())
}

/// Durably append one hook payload, then start a consumer only when this
/// parent wins the per-session ownership lock. Busy sessions only append.
pub fn enqueue_and_wake(
    session_id: &str,
    payload: &Value,
    original_bytes: usize,
    logger: &Logger,
) -> Result<EnqueueOutcome, String> {
    if session_id.trim().is_empty() {
        return Err("missing session id".to_string());
    }
    if original_bytes == 0 {
        return Err("empty worker envelope".to_string());
    }
    if original_bytes > MAX_ENVELOPE_BYTES {
        return Err(format!(
            "worker envelope exceeds {} bytes",
            MAX_ENVELOPE_BYTES
        ));
    }

    let append_outcome = match append_queue(session_id, payload) {
        Ok(outcome) => outcome,
        Err(AppendFailure::Full(error)) => {
            // A full queue already contains recoverable work. Ensure a dead
            // consumer is restarted even though this newest trigger is
            // rejected to preserve the fail-open hook latency contract.
            let wake_result = wake_session(session_id, logger);
            return match wake_result {
                Ok(_) => Err(error),
                Err(wake_error) => Err(format!("{error}; worker wake failed: {wake_error}")),
            };
        }
        Err(AppendFailure::Other(error)) => return Err(error),
    };

    let spawned = wake_session(session_id, logger)?;
    Ok(if matches!(append_outcome, AppendOutcome::Coalesced) {
        EnqueueOutcome::Coalesced
    } else if spawned {
        EnqueueOutcome::Spawned
    } else {
        EnqueueOutcome::Appended
    })
}

/// Start the sole consumer for an already-persisted queue, if none is live.
pub fn wake_session(session_id: &str, logger: &Logger) -> Result<bool, String> {
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let Some(owner) = SessionOwner::try_acquire(session_id)? else {
        logger.debug("worker queue wake coalesced: consumer=existing");
        return Ok(false);
    };
    let token = match claim_start_marker(session_id)? {
        StartClaim::Existing => {
            logger.debug("worker queue wake coalesced: consumer=starting");
            return Ok(false);
        }
        StartClaim::Claimed(token) => token,
    };
    match spawn_worker_process(&executable, session_id, &token) {
        Ok(_child) => {
            // Dropping Child does not terminate it. Release ownership only
            // after CreateProcess/posix_spawn succeeded; the child then takes
            // the normal cross-platform file lock and consumes its token.
            drop(owner);
            logger.debug("worker queue wake scheduled");
            Ok(true)
        }
        Err(error) => {
            record_start_failure(session_id, &token, &error)?;
            Err(error)
        }
    }
}

/// Runtime handoffs wait for the replacement to acquire session ownership.
/// Unlike a latency-sensitive parent hook, an expiring worker can afford this
/// acknowledgement barrier and retry an early child death without requiring a
/// future hook to wake the durable queue again.
pub fn wake_session_confirmed(session_id: &str, logger: &Logger) -> Result<bool, String> {
    wake_session_with_confirmation(
        session_id,
        logger,
        WORKER_HANDOFF_ATTEMPTS,
        WORKER_START_WAIT,
        "worker runtime handoff",
    )
}

fn wake_session_with_confirmation(
    session_id: &str,
    logger: &Logger,
    attempts: usize,
    startup_wait: Duration,
    operation: &str,
) -> Result<bool, String> {
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let mut last_error = None;
    for attempt in 1..=attempts {
        let Some(owner) = SessionOwner::try_acquire(session_id)? else {
            logger.debug(&format!("{operation} coalesced: consumer=existing"));
            return Ok(false);
        };
        let token = match claim_start_marker(session_id)? {
            StartClaim::Existing => {
                drop(owner);
                if wait_for_existing_worker_start(session_id, startup_wait)? {
                    return Ok(false);
                }
                let error = "existing worker start marker timed out without ownership";
                fail_unowned_start_marker(session_id, error)?;
                last_error = Some(error.to_string());
                continue;
            }
            StartClaim::Claimed(token) => token,
        };
        let mut child = match spawn_worker_process(&executable, session_id, &token) {
            Ok(child) => child,
            Err(error) => {
                record_start_failure(session_id, &token, &error)?;
                last_error = Some(error);
                continue;
            }
        };
        drop(owner);
        match wait_for_spawned_worker_start(session_id, &token, &mut child, startup_wait) {
            Ok(()) => {
                logger.debug(&format!("{operation} acknowledged: attempt={attempt}"));
                return Ok(true);
            }
            Err(error) => {
                record_start_failure(session_id, &token, &error)?;
                last_error = Some(error);
            }
        }
    }
    Err(format!(
        "{operation} failed after {attempts} attempts: {}",
        last_error.unwrap_or_else(|| "unknown startup failure".to_string())
    ))
}

fn fail_unowned_start_marker(session_id: &str, error: &str) -> Result<(), String> {
    let Some(_owner) = SessionOwner::try_acquire(session_id)? else {
        return Ok(());
    };
    let path = dirs::session_worker_start_file(session_id)
        .ok_or_else(|| "missing session id".to_string())?;
    if let Ok(bytes) = std::fs::read(path) {
        if let Ok(marker) = serde_json::from_slice::<WorkerStartMarker>(&bytes) {
            if marker.status == "starting" {
                record_start_failure(session_id, &marker.token, error)?;
            }
        }
    }
    Ok(())
}

/// Compatibility entry used by the state-worker CLI. New callers should pass
/// the parsed payload so it can be persisted as one compact JSONL record.
pub fn spawn_detached_raw(payload: &[u8], logger: &Logger) -> Result<(), String> {
    if payload.is_empty() {
        return Err("empty worker envelope".to_string());
    }
    if payload.len() > MAX_ENVELOPE_BYTES {
        return Err(format!(
            "worker envelope exceeds {} bytes",
            MAX_ENVELOPE_BYTES
        ));
    }
    let value: Value = serde_json::from_slice(payload).map_err(|error| error.to_string())?;
    let session_id = crate::common::hook::extract_session_id(&value);
    enqueue_and_wake(&session_id, &value, payload.len(), logger).map(|_| ())
}

fn spawn_worker_process(executable: &Path, session_id: &str, token: &str) -> Result<Child, String> {
    if env_is_truthy("CDXHOOKS_TEST_WORKER_SPAWN_FAILURE") {
        return Err("test-injected worker spawn failure".to_string());
    }

    #[cfg(windows)]
    clear_inheritable_hook_stdio()?;

    let mut command = Command::new(executable);
    command
        .arg(COMMAND)
        .env(MODE_ENV, "true")
        .env(SESSION_ENV, session_id)
        .env(START_TOKEN_ENV, token)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        command.creation_flags(CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | DETACHED_PROCESS);
    }

    command
        .spawn()
        .map_err(|error| format!("spawn session worker: {error}"))
}

#[cfg(windows)]
fn clear_inheritable_hook_stdio() -> Result<(), String> {
    const STD_INPUT_HANDLE: u32 = (-10_i32) as u32;
    const STD_OUTPUT_HANDLE: u32 = (-11_i32) as u32;
    const STD_ERROR_HANDLE: u32 = (-12_i32) as u32;
    const HANDLE_FLAG_INHERIT: u32 = 0x0000_0001;
    const INVALID_HANDLE_VALUE: *mut std::ffi::c_void = (-1_isize) as *mut std::ffi::c_void;

    // Rust's stable Windows Command implementation enables bInheritHandles by
    // default. Codex launches synchronous hooks with inheritable stdout/stderr
    // pipe handles, so merely assigning NUL as the worker's stdio can still
    // copy the old pipe handles as unrelated inherited handles. That keeps
    // Codex's wait_with_output readers open until the detached worker exits and
    // prevents Codex from appending task_complete while the worker is polling.
    // Clearing inheritance on the current standard handles before spawning is
    // process-local, leaves the handles usable by this parent, and prevents the
    // old hook pipes from accompanying the fresh NUL handles created for the
    // worker.
    for (name, std_handle) in [
        ("stdin", STD_INPUT_HANDLE),
        ("stdout", STD_OUTPUT_HANDLE),
        ("stderr", STD_ERROR_HANDLE),
    ] {
        let handle = unsafe { GetStdHandle(std_handle) };
        if handle.is_null() || handle == INVALID_HANDLE_VALUE {
            continue;
        }
        let cleared = unsafe { SetHandleInformation(handle, HANDLE_FLAG_INHERIT, 0) };
        if cleared == 0 {
            return Err(format!(
                "clear inherited hook {name} handle before worker spawn: {}",
                std::io::Error::last_os_error()
            ));
        }
    }
    Ok(())
}

fn wait_for_existing_worker_start(session_id: &str, timeout: Duration) -> Result<bool, String> {
    let deadline = std::time::Instant::now() + timeout;
    while std::time::Instant::now() < deadline {
        let owner_is_live = SessionOwner::try_acquire(session_id)?.is_none();
        let marker_status = start_marker_status(session_id)?;
        if owner_is_live && marker_status.is_none() {
            return Ok(true);
        }
        if marker_status.as_deref() != Some("starting") {
            return Ok(false);
        }
        std::thread::sleep(WORKER_START_POLL);
    }
    Ok(false)
}

fn wait_for_spawned_worker_start(
    session_id: &str,
    token: &str,
    child: &mut Child,
    timeout: Duration,
) -> Result<(), String> {
    let deadline = std::time::Instant::now() + timeout;
    while std::time::Instant::now() < deadline {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| format!("poll session worker startup: {error}"))?
        {
            return Err(format!(
                "session worker exited before ownership acknowledgement: {status}"
            ));
        }
        let marker_matches = start_marker_matches(session_id, token)?;
        let owner_is_live = SessionOwner::try_acquire(session_id)?.is_none();
        if !marker_matches && owner_is_live {
            return Ok(());
        }
        if !marker_matches {
            let _ = child.kill();
            let _ = child.wait();
            return Err(
                "session worker consumed its marker without retaining ownership".to_string(),
            );
        }
        std::thread::sleep(WORKER_START_POLL);
    }
    let _ = child.kill();
    let _ = child.wait();
    Err("session worker startup acknowledgement timed out".to_string())
}

fn start_marker_matches(session_id: &str, token: &str) -> Result<bool, String> {
    let path = dirs::session_worker_start_file(session_id)
        .ok_or_else(|| "missing session id".to_string())?;
    match std::fs::read(&path) {
        Ok(bytes) => Ok(serde_json::from_slice::<WorkerStartMarker>(&bytes)
            .ok()
            .is_some_and(|marker| marker.token == token && marker.status == "starting")),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(format!("{}: {error}", path.display())),
    }
}

/// Acquire the session lock from a fresh process and consume only its own
/// startup marker. The lock is opened independently on every platform.
pub fn acquire_queued_worker(session_id: &str) -> Result<SessionOwner, String> {
    if let Ok(delay_ms) = std::env::var("CDXHOOKS_TEST_WORKER_START_DELAY_MS") {
        if let Ok(delay_ms) = delay_ms.parse::<u64>() {
            std::thread::sleep(Duration::from_millis(delay_ms));
        }
    }
    let owner = SessionOwner::acquire(session_id, WORKER_START_WAIT)?;
    if let Some(token) = worker_start_token() {
        consume_start_marker(session_id, &token)?;
    }
    Ok(owner)
}

fn worker_start_token() -> Option<String> {
    std::env::var(START_TOKEN_ENV)
        .ok()
        .filter(|token| !token.trim().is_empty())
}

fn claim_start_marker(session_id: &str) -> Result<StartClaim, String> {
    let path = dirs::session_worker_start_file(session_id)
        .ok_or_else(|| "missing session id".to_string())?;
    let now = unix_nanos();
    if let Ok(bytes) = std::fs::read(&path) {
        if let Ok(marker) = serde_json::from_slice::<WorkerStartMarker>(&bytes) {
            let expires = marker.expires_at_ns.parse::<u128>().unwrap_or(0);
            if marker.schema_version == 1 && marker.status == "starting" && expires > now {
                return Ok(StartClaim::Existing);
            }
        }
        let _ = std::fs::remove_file(&path);
    }
    let token = format!("{}-{}", std::process::id(), now);
    let marker = WorkerStartMarker {
        schema_version: 1,
        token: token.clone(),
        created_at_ns: now.to_string(),
        expires_at_ns: now.saturating_add(START_MARKER_TTL_NS).to_string(),
        status: starting_marker_status(),
        last_error: None,
    };
    write_start_marker_atomically(
        &path,
        &serde_json::to_value(marker).map_err(|error| error.to_string())?,
    )?;
    Ok(StartClaim::Claimed(token))
}

fn starting_marker_status() -> String {
    "starting".to_string()
}

fn record_start_failure(session_id: &str, token: &str, error: &str) -> Result<(), String> {
    let path = dirs::session_worker_start_file(session_id)
        .ok_or_else(|| "missing session id".to_string())?;
    let Some(mut marker) = std::fs::read(&path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<WorkerStartMarker>(&bytes).ok())
        .filter(|marker| marker.token == token)
    else {
        return Ok(());
    };
    marker.status = "spawn_failed".to_string();
    marker.expires_at_ns = unix_nanos().to_string();
    marker.last_error = Some(error.chars().take(2048).collect());
    write_start_marker_atomically(
        &path,
        &serde_json::to_value(marker).map_err(|error| error.to_string())?,
    )
}

pub fn start_marker_status(session_id: &str) -> Result<Option<String>, String> {
    let path = dirs::session_worker_start_file(session_id)
        .ok_or_else(|| "missing session id".to_string())?;
    match std::fs::read(&path) {
        Ok(bytes) => serde_json::from_slice::<WorkerStartMarker>(&bytes)
            .map(|marker| Some(marker.status))
            .map_err(|error| format!("{}: {error}", path.display())),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(format!("{}: {error}", path.display())),
    }
}

#[cfg(test)]
fn clear_start_marker(session_id: &str, token: &str) -> Result<(), String> {
    let path = dirs::session_worker_start_file(session_id)
        .ok_or_else(|| "missing session id".to_string())?;
    let matches = std::fs::read(&path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<WorkerStartMarker>(&bytes).ok())
        .is_some_and(|marker| marker.token == token);
    if matches {
        match std::fs::remove_file(&path) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(format!("{}: {error}", path.display())),
        }
    }
    Ok(())
}

fn consume_start_marker(session_id: &str, token: &str) -> Result<(), String> {
    let path = dirs::session_worker_start_file(session_id)
        .ok_or_else(|| "missing session id".to_string())?;
    let marker = match std::fs::read(&path) {
        Ok(bytes) => serde_json::from_slice::<WorkerStartMarker>(&bytes)
            .map_err(|error| format!("{}: {error}", path.display()))?,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Err("session worker start marker is missing".to_string())
        }
        Err(error) => return Err(format!("{}: {error}", path.display())),
    };
    if marker.token != token || marker.status != "starting" {
        return Err("session worker start marker is stale or owned by another start".to_string());
    }
    match std::fs::remove_file(&path) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Err(
                "session worker start marker disappeared before acknowledgement".to_string(),
            )
        }
        Err(error) => return Err(format!("{}: {error}", path.display())),
    }
    Ok(())
}

pub fn acquire_session(session_id: &str) -> Result<SessionOwner, String> {
    SessionOwner::acquire_blocking(session_id)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn with_state_root(test: impl FnOnce(&std::path::Path)) {
        let _guard = crate::test_sync::env_lock();
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
    fn stale_start_marker_is_replaced_and_live_marker_is_coalesced() {
        with_state_root(|_| {
            let session_id = "marker-lifecycle-session";
            let owner =
                SessionOwner::acquire(session_id, std::time::Duration::from_millis(100)).unwrap();
            let path = dirs::session_worker_start_file(session_id).unwrap();
            runtime_state::write_json_atomically(
                &path,
                &serde_json::json!({
                    "schema_version": 1,
                    "token": "expired",
                    "created_at_ns": "1",
                    "expires_at_ns": "1"
                }),
            )
            .unwrap();

            let token = match claim_start_marker(session_id).unwrap() {
                StartClaim::Claimed(token) => token,
                StartClaim::Existing => panic!("expired marker must not coalesce wake"),
            };
            assert_ne!(token, "expired");
            assert!(matches!(
                claim_start_marker(session_id).unwrap(),
                StartClaim::Existing
            ));
            clear_start_marker(session_id, &token).unwrap();
            assert!(!path.exists());
            drop(owner);
        });
    }

    #[test]
    fn queued_worker_consumes_only_its_start_token() {
        with_state_root(|_| {
            let session_id = "marker-consume-session";
            let owner =
                SessionOwner::acquire(session_id, std::time::Duration::from_millis(100)).unwrap();
            let token = match claim_start_marker(session_id).unwrap() {
                StartClaim::Claimed(token) => token,
                StartClaim::Existing => panic!("new marker unexpectedly existed"),
            };
            drop(owner);
            std::env::set_var(START_TOKEN_ENV, &token);
            let owner = acquire_queued_worker(session_id).unwrap();
            assert!(!dirs::session_worker_start_file(session_id)
                .unwrap()
                .exists());
            drop(owner);
            std::env::remove_var(START_TOKEN_ENV);
        });
    }

    #[test]
    fn spawn_failure_persists_observable_marker_and_releases_owner() {
        with_state_root(|root| {
            let previous = std::env::var("CDXHOOKS_TEST_WORKER_SPAWN_FAILURE").ok();
            std::env::set_var("CDXHOOKS_TEST_WORKER_SPAWN_FAILURE", "true");
            let logger = Logger::new(root.join("worker.log"), true);
            let error = wake_session("spawn-failure-session", &logger).unwrap_err();
            assert!(error.contains("test-injected worker spawn failure"));
            assert_eq!(
                start_marker_status("spawn-failure-session").unwrap(),
                Some("spawn_failed".to_string())
            );
            assert!(SessionOwner::try_acquire("spawn-failure-session")
                .unwrap()
                .is_some());
            let replacement = match claim_start_marker("spawn-failure-session").unwrap() {
                StartClaim::Claimed(token) => token,
                StartClaim::Existing => {
                    panic!("failed start marker must be immediately recoverable")
                }
            };
            clear_start_marker("spawn-failure-session", &replacement).unwrap();
            match previous {
                Some(value) => std::env::set_var("CDXHOOKS_TEST_WORKER_SPAWN_FAILURE", value),
                None => std::env::remove_var("CDXHOOKS_TEST_WORKER_SPAWN_FAILURE"),
            }
        });
    }

    #[cfg(unix)]
    #[test]
    fn spawned_worker_exit_before_ownership_is_detected() {
        with_state_root(|_| {
            let session_id = "worker-early-exit-session";
            let owner =
                SessionOwner::acquire(session_id, std::time::Duration::from_millis(100)).unwrap();
            let token = match claim_start_marker(session_id).unwrap() {
                StartClaim::Claimed(token) => token,
                StartClaim::Existing => panic!("new marker unexpectedly existed"),
            };
            let mut child = Command::new("/usr/bin/false").spawn().unwrap();
            drop(owner);

            let error = wait_for_spawned_worker_start(
                session_id,
                &token,
                &mut child,
                std::time::Duration::from_secs(1),
            )
            .unwrap_err();
            assert!(error.contains("exited before ownership acknowledgement"));
            record_start_failure(session_id, &token, &error).unwrap();
            assert_eq!(
                start_marker_status(session_id).unwrap().as_deref(),
                Some("spawn_failed")
            );
        });
    }

    #[cfg(unix)]
    #[test]
    fn consumed_start_marker_without_owner_is_not_acknowledged() {
        with_state_root(|_| {
            let session_id = "worker-marker-without-owner-session";
            let owner =
                SessionOwner::acquire(session_id, std::time::Duration::from_millis(100)).unwrap();
            let token = match claim_start_marker(session_id).unwrap() {
                StartClaim::Claimed(token) => token,
                StartClaim::Existing => panic!("new marker unexpectedly existed"),
            };
            let mut child = Command::new("/bin/sleep").arg("2").spawn().unwrap();
            clear_start_marker(session_id, &token).unwrap();
            drop(owner);

            let error = wait_for_spawned_worker_start(
                session_id,
                &token,
                &mut child,
                std::time::Duration::from_secs(1),
            )
            .unwrap_err();
            assert!(error.contains("without retaining ownership"));
        });
    }

    #[test]
    fn confirmed_handoff_retries_spawn_failure_and_leaves_recoverable_marker() {
        with_state_root(|root| {
            let previous = std::env::var("CDXHOOKS_TEST_WORKER_SPAWN_FAILURE").ok();
            std::env::set_var("CDXHOOKS_TEST_WORKER_SPAWN_FAILURE", "true");
            let logger = Logger::new(root.join("confirmed-handoff.log"), true);

            let error = wake_session_confirmed("confirmed-handoff-session", &logger).unwrap_err();
            assert!(error.contains("failed after 3 attempts"));
            assert_eq!(
                start_marker_status("confirmed-handoff-session")
                    .unwrap()
                    .as_deref(),
                Some("spawn_failed")
            );
            assert!(SessionOwner::try_acquire("confirmed-handoff-session")
                .unwrap()
                .is_some());

            match previous {
                Some(value) => std::env::set_var("CDXHOOKS_TEST_WORKER_SPAWN_FAILURE", value),
                None => std::env::remove_var("CDXHOOKS_TEST_WORKER_SPAWN_FAILURE"),
            }
        });
    }

    #[test]
    fn missing_queue_is_created_only_by_the_durable_append_path() {
        with_state_root(|_| {
            let session_id = "first-durable-append-session";
            let path = queue_path(session_id).unwrap();
            let queue_lock = QueueLock::acquire(session_id).unwrap();

            write_offset(session_id, 17).unwrap();
            repair_partial_tail_locked(session_id).unwrap();
            assert!(!path.exists());
            assert_eq!(read_offset(session_id).unwrap(), 0);
            drop(queue_lock);

            assert!(matches!(
                append_queue(
                    session_id,
                    &serde_json::json!({
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": session_id,
                        "uuid": "first"
                    }),
                )
                .unwrap(),
                AppendOutcome::Appended
            ));
            assert!(path.exists());
            assert_eq!(pending_count(session_id).unwrap(), 1);
        });
    }

    #[test]
    fn offset_write_truncates_old_content_and_recovers_invalid_content() {
        with_state_root(|_| {
            let session_id = "offset-in-place-session";
            let _queue_lock = QueueLock::acquire(session_id).unwrap();
            let path = offset_path(session_id).unwrap();

            write_offset(session_id, 123_456).unwrap();
            write_offset(session_id, 7).unwrap();
            assert_eq!(std::fs::read(&path).unwrap(), b"7");
            assert_eq!(read_offset(session_id).unwrap(), 7);

            std::fs::write(&path, []).unwrap();
            assert_eq!(read_offset(session_id).unwrap(), 0);
            std::fs::write(&path, b"invalid").unwrap();
            assert_eq!(read_offset(session_id).unwrap(), 0);
        });
    }

    #[cfg(windows)]
    #[test]
    fn offset_write_does_not_require_delete_sharing() {
        use std::os::windows::fs::OpenOptionsExt;

        const FILE_SHARE_READ: u32 = 0x0000_0001;
        const FILE_SHARE_WRITE: u32 = 0x0000_0002;

        with_state_root(|_| {
            let session_id = "offset-delete-sharing-session";
            let _queue_lock = QueueLock::acquire(session_id).unwrap();
            let path = offset_path(session_id).unwrap();
            write_offset(session_id, 1).unwrap();

            let held = OpenOptions::new()
                .read(true)
                .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
                .open(&path)
                .unwrap();
            write_offset(session_id, 2).unwrap();
            drop(held);

            assert_eq!(read_offset(session_id).unwrap(), 2);
        });
    }

    #[cfg(windows)]
    #[test]
    fn offset_write_retries_transient_sharing_violation() {
        use std::os::windows::fs::OpenOptionsExt;

        const FILE_SHARE_READ: u32 = 0x0000_0001;

        with_state_root(|_| {
            let session_id = "offset-sharing-retry-session";
            let _queue_lock = QueueLock::acquire(session_id).unwrap();
            let path = offset_path(session_id).unwrap();
            write_offset(session_id, 1).unwrap();

            let held = OpenOptions::new()
                .read(true)
                .share_mode(FILE_SHARE_READ)
                .open(&path)
                .unwrap();
            let releaser = std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(20));
                drop(held);
            });

            write_offset(session_id, 2).unwrap();
            releaser.join().unwrap();
            assert_eq!(read_offset(session_id).unwrap(), 2);
        });
    }

    #[cfg(windows)]
    #[test]
    fn offset_write_reports_persistent_sharing_violation_after_bounded_retry() {
        use std::os::windows::fs::OpenOptionsExt;

        const FILE_SHARE_READ: u32 = 0x0000_0001;

        with_state_root(|_| {
            let session_id = "offset-sharing-timeout-session";
            let _queue_lock = QueueLock::acquire(session_id).unwrap();
            let path = offset_path(session_id).unwrap();
            write_offset(session_id, 1).unwrap();

            let held = OpenOptions::new()
                .read(true)
                .share_mode(FILE_SHARE_READ)
                .open(&path)
                .unwrap();
            let started = std::time::Instant::now();
            let error = write_offset(session_id, 2).unwrap_err();
            let elapsed = started.elapsed();
            drop(held);

            assert!(error.contains("failed to open queue offset in place"));
            assert!(elapsed >= Duration::from_millis(450));
            assert!(elapsed < Duration::from_secs(2));
        });
    }

    #[test]
    fn queue_lock_timeout_rejects_new_record_without_corrupting_existing_queue() {
        with_state_root(|_| {
            let session_id = "queue-lock-timeout-session";
            append_queue(
                session_id,
                &serde_json::json!({
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": session_id,
                    "uuid": "existing"
                }),
            )
            .unwrap();
            let queue_lock = QueueLock::acquire(session_id).unwrap();
            let started = std::time::Instant::now();
            let result = append_queue(
                session_id,
                &serde_json::json!({
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": session_id,
                    "uuid": "rejected"
                }),
            );
            assert!(
                matches!(result, Err(AppendFailure::Other(ref error)) if error.contains("file lock busy after 2000ms"))
            );
            assert!(started.elapsed() >= QUEUE_LOCK_WAIT);
            drop(queue_lock);
            assert_eq!(pending_count(session_id).unwrap(), 1);
        });
    }

    #[test]
    fn drain_queue_processes_bounded_fifo_batch_before_maintenance() {
        with_state_root(|_| {
            let session_id = "fifo-maintenance-quantum-session";
            for seq in 0..33u64 {
                append_queue(
                    session_id,
                    &serde_json::json!({
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": session_id,
                        "uuid": format!("item-{seq}"),
                        "seq": seq
                    }),
                )
                .unwrap();
            }
            let processed = std::rc::Rc::new(std::cell::RefCell::new(Vec::new()));
            let maintenance_snapshots = std::rc::Rc::new(std::cell::RefCell::new(Vec::new()));
            let process_seen = processed.clone();
            let maintenance_seen = processed.clone();
            let maintenance_log = maintenance_snapshots.clone();
            let mut maintenance_calls = 0usize;
            let stats = drain_queue_with_maintenance(
                session_id,
                None,
                move |item| {
                    process_seen
                        .borrow_mut()
                        .push(item.payload["seq"].as_u64().unwrap());
                    QueueDisposition::Ack
                },
                move || {
                    maintenance_calls += 1;
                    maintenance_log
                        .borrow_mut()
                        .push(maintenance_seen.borrow().len());
                    if maintenance_calls == 1 {
                        MaintenanceDisposition::DidWork
                    } else {
                        MaintenanceDisposition::Idle
                    }
                },
            )
            .unwrap();

            assert_eq!(*processed.borrow(), (0..33u64).collect::<Vec<_>>());
            let first_batch = maintenance_snapshots.borrow().first().copied().unwrap();
            assert!((1..=HOOK_BATCH_MAX_ITEMS).contains(&first_batch));
            assert_eq!(HOOK_BATCH_MAX_ITEMS, 32);
            assert_eq!(stats.processed, 33);
            assert_eq!(stats.maintenance_slices, 1);
        });
    }

    #[test]
    fn maintenance_waiting_releases_queue_lock_and_rechecks_fifo() {
        with_state_root(|_| {
            let session_id = "maintenance-wait-recheck-session";
            let mut maintenance_calls = 0usize;
            let mut processed = Vec::new();
            let stats = drain_queue_with_maintenance(
                session_id,
                None,
                |item| {
                    processed.push(item.payload["uuid"].as_str().unwrap().to_string());
                    QueueDisposition::Ack
                },
                || {
                    maintenance_calls += 1;
                    if maintenance_calls == 1 {
                        let started = std::time::Instant::now();
                        append_queue(
                            session_id,
                            &serde_json::json!({
                                "hook_event_name": "UserPromptSubmit",
                                "session_id": session_id,
                                "uuid": "arrived-during-maintenance"
                            }),
                        )
                        .unwrap();
                        assert!(started.elapsed() < QUEUE_LOCK_WAIT);
                        MaintenanceDisposition::Waiting(Duration::from_millis(1))
                    } else {
                        MaintenanceDisposition::Idle
                    }
                },
            )
            .unwrap();

            assert_eq!(processed, vec!["arrived-during-maintenance"]);
            assert_eq!(stats.processed, 1);
        });
    }
}

/// Drain all currently queued items while retaining sole session ownership.
///
/// The empty check and owner release are serialized with queue append. This is
/// the lost-wakeup barrier: an appender either becomes visible to this worker,
/// or observes the released owner lock and starts the next worker.
pub fn drain_queue<F>(
    session_id: &str,
    owner: Option<SessionOwner>,
    process: F,
) -> Result<DrainStats, String>
where
    F: FnMut(QueueItem) -> QueueDisposition,
{
    drain_queue_with_maintenance(session_id, owner, process, || MaintenanceDisposition::Idle)
}

/// Drain the FIFO hook lane while giving due session maintenance a bounded
/// fairness point after each hook quantum and whenever the FIFO becomes empty.
/// Maintenance runs without QueueLock but under the same SessionOwner.
pub fn drain_queue_with_maintenance<F, M>(
    session_id: &str,
    mut owner: Option<SessionOwner>,
    mut process: F,
    mut maintenance: M,
) -> Result<DrainStats, String>
where
    F: FnMut(QueueItem) -> QueueDisposition,
    M: FnMut() -> MaintenanceDisposition,
{
    let mut stats = DrainStats::default();
    let started = std::time::Instant::now();
    let max_runtime = worker_max_runtime();
    let mut batch_items = 0usize;
    let mut batch_started = std::time::Instant::now();
    loop {
        if started.elapsed() >= max_runtime {
            stats.runtime_exhausted = true;
            let _ = persist_handoff_intent(session_id);
            drop(owner.take());
            return Ok(stats);
        }
        let queue_lock = QueueLock::acquire(session_id)?;
        repair_partial_tail_locked(session_id)?;
        match read_next_locked(session_id)? {
            QueueRead::Item(record) => {
                drop(queue_lock);
                let disposition = process(record.item);
                let queue_lock = QueueLock::acquire(session_id)?;
                match disposition {
                    QueueDisposition::Ack => {
                        clear_retry_state(session_id)?;
                        acknowledge_locked(session_id, record.next_offset)?;
                        stats.processed = stats.processed.saturating_add(1);
                    }
                    QueueDisposition::Retry(reason) => {
                        let attempts = record_retry_locked(
                            session_id,
                            record.offset,
                            &reason,
                            RetryKind::Standard,
                        )?;
                        stats.retried = stats.retried.saturating_add(1);
                        if attempts >= max_delivery_attempts() {
                            append_failed_locked(
                                session_id,
                                record.offset,
                                attempts,
                                &reason,
                                &record.raw,
                            )?;
                            clear_retry_state(session_id)?;
                            acknowledge_locked(session_id, record.next_offset)?;
                            stats.quarantined = stats.quarantined.saturating_add(1);
                        } else {
                            drop(queue_lock);
                            std::thread::sleep(retry_backoff(attempts));
                            continue;
                        }
                    }
                    QueueDisposition::RetryDurable(reason) => {
                        let attempts = record_retry_locked(
                            session_id,
                            record.offset,
                            &reason,
                            RetryKind::Durable,
                        )?;
                        stats.retried = stats.retried.saturating_add(1);
                        if attempts >= max_durable_attempts() {
                            let terminal_reason =
                                format!("durable retry limit exhausted: {reason}");
                            append_failed_locked(
                                session_id,
                                record.offset,
                                attempts,
                                &terminal_reason,
                                &record.raw,
                            )?;
                            clear_retry_state(session_id)?;
                            acknowledge_locked(session_id, record.next_offset)?;
                            stats.quarantined = stats.quarantined.saturating_add(1);
                        } else {
                            drop(queue_lock);
                            std::thread::sleep(retry_backoff(attempts));
                            continue;
                        }
                    }
                }
                drop(queue_lock);
                batch_items = batch_items.saturating_add(1);
                if batch_items >= HOOK_BATCH_MAX_ITEMS
                    || batch_started.elapsed() >= HOOK_BATCH_MAX_WALL
                {
                    if matches!(maintenance(), MaintenanceDisposition::DidWork) {
                        stats.maintenance_slices = stats.maintenance_slices.saturating_add(1);
                    }
                    batch_items = 0;
                    batch_started = std::time::Instant::now();
                }
            }
            QueueRead::Corrupt {
                raw,
                offset,
                next_offset,
                reason,
            } => {
                append_failed_locked(session_id, offset, 1, &reason, &raw)?;
                clear_retry_state(session_id)?;
                acknowledge_locked(session_id, next_offset)?;
                stats.quarantined = stats.quarantined.saturating_add(1);
                drop(queue_lock);
            }
            QueueRead::Empty => {
                drop(queue_lock);
                match maintenance() {
                    MaintenanceDisposition::DidWork => {
                        stats.maintenance_slices = stats.maintenance_slices.saturating_add(1);
                        batch_items = 0;
                        batch_started = std::time::Instant::now();
                        continue;
                    }
                    MaintenanceDisposition::Waiting(delay) => {
                        std::thread::sleep(delay.min(MAINTENANCE_WAIT_MAX));
                        continue;
                    }
                    MaintenanceDisposition::Idle => {
                        // Lost-wakeup barrier: recheck FIFO under QueueLock
                        // after maintenance before releasing SessionOwner.
                        let queue_lock = QueueLock::acquire(session_id)?;
                        repair_partial_tail_locked(session_id)?;
                        if !matches!(read_next_locked(session_id)?, QueueRead::Empty) {
                            drop(queue_lock);
                            continue;
                        }
                        clear_retry_state(session_id)?;
                        compact_empty_queue_locked(session_id)?;
                        drop(owner.take());
                        drop(queue_lock);
                        return Ok(stats);
                    }
                }
            }
        }
    }
}

pub fn queued_elapsed_ms(queued_at_ns: u128) -> u128 {
    unix_nanos().saturating_sub(queued_at_ns) / 1_000_000
}

pub fn pending_count(session_id: &str) -> Result<usize, String> {
    let _queue_lock = QueueLock::acquire(session_id)?;
    repair_partial_tail_locked(session_id)?;
    let path = queue_path(session_id)?;
    let mut offset = read_offset(session_id)?;
    let mut file = match File::open(&path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(0),
        Err(error) => return Err(format!("{}: {error}", path.display())),
    };
    let length = file
        .metadata()
        .map_err(|error| format!("{}: {error}", path.display()))?
        .len();
    if offset > length {
        offset = 0;
        write_offset(session_id, 0)?;
    }
    file.seek(SeekFrom::Start(offset))
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let reader = BufReader::new(file);
    let mut count = 0usize;
    for line in reader.split(b'\n') {
        if !line
            .map_err(|error| format!("{}: {error}", path.display()))?
            .is_empty()
        {
            count = count.saturating_add(1);
        }
    }
    Ok(count)
}

fn append_queue(session_id: &str, payload: &Value) -> Result<AppendOutcome, AppendFailure> {
    let _queue_lock = QueueLock::acquire(session_id).map_err(AppendFailure::Other)?;
    repair_partial_tail_locked(session_id).map_err(AppendFailure::Other)?;
    let path = queue_path(session_id).map_err(AppendFailure::Other)?;
    let envelope = QueueEnvelope {
        queued_at_ns: unix_nanos().to_string(),
        env: crate::common::env::capture_worker_env(),
        payload: payload.clone(),
    };

    let mut record =
        serde_json::to_vec(&envelope).map_err(|error| AppendFailure::Other(error.to_string()))?;
    record.push(b'\n');
    let max_bytes = queue_max_bytes();
    let length = std::fs::metadata(&path)
        .map(|value| value.len())
        .unwrap_or(0);
    let mut offset = read_offset(session_id).map_err(AppendFailure::Other)?;
    if offset > length {
        offset = 0;
        write_offset(session_id, 0).map_err(AppendFailure::Other)?;
    }
    let pending_bytes = length.saturating_sub(offset);
    let record_bytes = record.len() as u64;
    let (pending_items, duplicate) =
        pending_queue_summary_locked(session_id, payload).map_err(AppendFailure::Other)?;
    if duplicate {
        return Ok(AppendOutcome::Coalesced);
    }
    let max_items = queue_max_items();
    // Only the consumer may change the queue's offset coordinate system.
    // Compacting here while a worker processes an unlocked record could make
    // its later acknowledgement skip data in the replacement file.
    if length.saturating_add(record_bytes) > max_bytes
        || pending_bytes.saturating_add(record_bytes) > max_bytes
        || pending_items >= max_items
    {
        record_overflow_locked(
            session_id,
            payload,
            pending_bytes,
            max_bytes,
            pending_items,
            max_items,
        )
        .map_err(AppendFailure::Other)?;
        return Err(AppendFailure::Full(format!(
            "worker queue capacity exceeded: pending_items={pending_items} max_items={max_items} pending_bytes={pending_bytes} record_bytes={} max_bytes={max_bytes}",
            record.len()
        )));
    }
    append_private(&path, &record).map_err(AppendFailure::Other)?;
    Ok(AppendOutcome::Appended)
}

fn pending_queue_summary_locked(
    session_id: &str,
    candidate: &Value,
) -> Result<(usize, bool), String> {
    let path = queue_path(session_id)?;
    let mut file = match File::open(&path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok((0, false)),
        Err(error) => return Err(format!("{}: {error}", path.display())),
    };
    let offset = read_offset(session_id)?;
    file.seek(SeekFrom::Start(offset))
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let candidate_key = coalescing_key(candidate);
    let mut pending_items = 0usize;
    for line in BufReader::new(file).split(b'\n') {
        let line = line.map_err(|error| format!("{}: {error}", path.display()))?;
        if line.is_empty() {
            continue;
        }
        pending_items = pending_items.saturating_add(1);
        if let Some(candidate_key) = candidate_key.as_ref() {
            let matches = serde_json::from_slice::<QueueEnvelope>(&line)
                .ok()
                .and_then(|envelope| coalescing_key(&envelope.payload))
                .is_some_and(|key| &key == candidate_key);
            if matches {
                return Ok((pending_items, true));
            }
        }
    }
    Ok((pending_items, false))
}

fn coalescing_key(payload: &Value) -> Option<String> {
    let event = payload.get("hook_event_name")?.as_str()?;
    match event {
        "Stop" | "SessionEnd" => Some(format!(
            "{event}:{}",
            payload
                .get("transcript_path")
                .and_then(Value::as_str)
                .unwrap_or_default()
        )),
        "PreToolUse" | "PostToolUse" => payload
            .get("tool_use_id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(|tool_use_id| format!("{event}:{tool_use_id}")),
        _ => None,
    }
}

fn read_next_locked(session_id: &str) -> Result<QueueRead, String> {
    let path = queue_path(session_id)?;
    let mut offset = read_offset(session_id)?;
    let mut file = match File::open(&path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(QueueRead::Empty),
        Err(error) => return Err(format!("{}: {error}", path.display())),
    };
    let length = file
        .metadata()
        .map_err(|error| format!("{}: {error}", path.display()))?
        .len();
    if offset > length {
        offset = 0;
        write_offset(session_id, 0)?;
    }
    if offset == length {
        return Ok(QueueRead::Empty);
    }
    file.seek(SeekFrom::Start(offset))
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let mut reader = BufReader::new(file);
    let mut record = Vec::new();
    let bytes = reader
        .read_until(b'\n', &mut record)
        .map_err(|error| format!("{}: {error}", path.display()))?;
    if bytes == 0 || record.last() != Some(&b'\n') {
        return Err(format!(
            "{}: incomplete queue record at {offset}",
            path.display()
        ));
    }
    let next_offset = offset.saturating_add(bytes as u64);
    let envelope: QueueEnvelope = match serde_json::from_slice(&record) {
        Ok(envelope) => envelope,
        Err(error) => {
            return Ok(QueueRead::Corrupt {
                raw: record,
                offset,
                next_offset,
                reason: format!("invalid queue JSON: {error}"),
            });
        }
    };
    let queued_at_ns = match envelope.queued_at_ns.parse::<u128>() {
        Ok(value) => value,
        Err(error) => {
            return Ok(QueueRead::Corrupt {
                raw: record,
                offset,
                next_offset,
                reason: format!("invalid queued timestamp: {error}"),
            });
        }
    };
    Ok(QueueRead::Item(QueueRecord {
        item: QueueItem {
            env: envelope.env,
            payload: envelope.payload,
            queued_at_ns,
        },
        raw: record,
        offset,
        next_offset,
    }))
}

fn compact_empty_queue_locked(session_id: &str) -> Result<(), String> {
    let path = queue_path(session_id)?;
    // Reset the checkpoint first. A crash between these operations may replay
    // acknowledged entries, but cannot hide a later append behind an old
    // offset. Reversed ordering could silently drop that later append.
    write_offset(session_id, 0)?;
    let mut options = OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options
        .open(&path)
        .and_then(|file| file.sync_data())
        .map_err(|error| format!("{}: {error}", path.display()))
}

fn acknowledge_locked(session_id: &str, next_offset: u64) -> Result<(), String> {
    write_offset(session_id, next_offset)?;
    let path = queue_path(session_id)?;
    let length = std::fs::metadata(&path)
        .map(|value| value.len())
        .unwrap_or(0);
    let compact_threshold = QUEUE_COMPACT_MIN_BYTES.min((queue_max_bytes() / 2).max(1));
    if next_offset >= compact_threshold && next_offset >= length.saturating_sub(next_offset) {
        compact_prefix_locked(session_id, next_offset)?;
    }
    Ok(())
}

fn compact_prefix_locked(session_id: &str, offset: u64) -> Result<(), String> {
    if offset == 0 {
        return Ok(());
    }
    let path = queue_path(session_id)?;
    let mut source = File::open(&path).map_err(|error| format!("{}: {error}", path.display()))?;
    let length = source
        .metadata()
        .map_err(|error| format!("{}: {error}", path.display()))?
        .len();
    if offset > length {
        write_offset(session_id, 0)?;
        return Ok(());
    }
    source
        .seek(SeekFrom::Start(offset))
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let parent = path
        .parent()
        .ok_or_else(|| format!("{} has no parent", path.display()))?;
    let mut replacement =
        tempfile::NamedTempFile::new_in(parent).map_err(|error| error.to_string())?;
    std::io::copy(&mut source, &mut replacement)
        .and_then(|_| replacement.flush())
        .and_then(|_| replacement.as_file().sync_data())
        .map_err(|error| format!("{}: {error}", path.display()))?;
    drop(source);

    // Offset-first makes a crash replay acknowledged work instead of hiding
    // pending work. The subsequent atomic rename establishes the new base.
    write_offset(session_id, 0)?;
    persist_named_temp(replacement, &path)?;
    Ok(())
}

fn record_retry_locked(
    session_id: &str,
    offset: u64,
    reason: &str,
    kind: RetryKind,
) -> Result<u32, String> {
    let path = retry_path(session_id)?;
    let previous = std::fs::read(&path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<RetryState>(&bytes).ok())
        .filter(|state| state.offset == offset);
    let attempts = previous
        .filter(|state| state.kind == kind)
        .map(|state| state.attempts)
        .unwrap_or(0)
        .saturating_add(1);
    let state = RetryState {
        offset,
        attempts,
        last_error: reason.chars().take(2048).collect(),
        kind,
    };
    runtime_state::write_json_atomically(
        &path,
        &serde_json::to_value(state).map_err(|error| error.to_string())?,
    )?;
    Ok(attempts)
}

fn clear_retry_state(session_id: &str) -> Result<(), String> {
    let path = retry_path(session_id)?;
    match std::fs::remove_file(&path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("{}: {error}", path.display())),
    }
}

fn append_failed_locked(
    session_id: &str,
    offset: u64,
    attempts: u32,
    reason: &str,
    raw_envelope: &[u8],
) -> Result<(), String> {
    let path = failed_path(session_id)?;
    let backup = failed_backup_path(session_id)?;
    let envelope = serde_json::from_slice::<Value>(raw_envelope)
        .unwrap_or_else(|_| Value::String(String::from_utf8_lossy(raw_envelope).into_owned()));
    let mut record = serde_json::to_vec(&serde_json::json!({
        "failed_at_ns": unix_nanos().to_string(),
        "offset": offset,
        "attempts": attempts,
        "reason": reason.chars().take(2048).collect::<String>(),
        "envelope": envelope,
    }))
    .map_err(|error| error.to_string())?;
    record.push(b'\n');
    let length = std::fs::metadata(&path)
        .map(|value| value.len())
        .unwrap_or(0);
    if length.saturating_add(record.len() as u64) > MAX_FAILED_LOG_BYTES {
        let _ = std::fs::remove_file(&backup);
        if let Err(error) = std::fs::rename(&path, &backup) {
            if error.kind() != std::io::ErrorKind::NotFound {
                return Err(format!("{}: {error}", path.display()));
            }
        }
    }
    append_private(&path, &record)
}

fn record_overflow_locked(
    session_id: &str,
    payload: &Value,
    pending_bytes: u64,
    max_bytes: u64,
    pending_items: usize,
    max_items: usize,
) -> Result<(), String> {
    let path = overflow_path(session_id)?;
    let now = unix_nanos().to_string();
    let previous = std::fs::read(&path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<OverflowState>(&bytes).ok());
    let state = OverflowState {
        schema_version: 1,
        dropped_items: previous
            .as_ref()
            .map(|value| value.dropped_items)
            .unwrap_or(0)
            .saturating_add(1),
        first_dropped_at_ns: previous
            .map(|value| value.first_dropped_at_ns)
            .unwrap_or_else(|| now.clone()),
        last_dropped_at_ns: now,
        last_event: payload
            .get("hook_event_name")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string(),
        pending_bytes,
        max_bytes,
        pending_items,
        max_items,
    };
    runtime_state::write_json_atomically(
        &path,
        &serde_json::to_value(state).map_err(|error| error.to_string())?,
    )
}

pub fn overflow_count(session_id: &str) -> Result<u64, String> {
    let _queue_lock = QueueLock::acquire(session_id)?;
    let path = overflow_path(session_id)?;
    match std::fs::read(&path) {
        Ok(bytes) => serde_json::from_slice::<OverflowState>(&bytes)
            .map(|state| state.dropped_items)
            .map_err(|error| format!("{}: {error}", path.display())),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(0),
        Err(error) => Err(format!("{}: {error}", path.display())),
    }
}

fn append_private(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let created = !path.exists();
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(path)
        .map_err(|error| format!("{}: {error}", path.display()))?;
    file.write_all(bytes)
        .and_then(|_| file.sync_data())
        .map_err(|error| format!("{}: {error}", path.display()))?;
    if created {
        agentic_otel_common::state::sync_parent_directory(path)
            .map_err(|error| format!("{}: {error}", path.display()))?;
    }
    Ok(())
}

fn retry_backoff(attempts: u32) -> std::time::Duration {
    std::time::Duration::from_millis(50u64.saturating_mul(1u64 << attempts.min(4)))
}

fn queue_max_bytes() -> u64 {
    std::env::var(QUEUE_MAX_BYTES_ENV)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_QUEUE_MAX_BYTES)
}

fn queue_max_items() -> usize {
    std::env::var(QUEUE_MAX_ITEMS_ENV)
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_QUEUE_MAX_ITEMS)
}

fn worker_max_runtime() -> std::time::Duration {
    std::time::Duration::from_millis(
        std::env::var(WORKER_MAX_RUNTIME_MS_ENV)
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(DEFAULT_WORKER_MAX_RUNTIME_MS),
    )
}

fn max_delivery_attempts() -> u32 {
    std::env::var(MAX_DELIVERY_ATTEMPTS_ENV)
        .ok()
        .and_then(|value| value.parse::<u32>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_MAX_DELIVERY_ATTEMPTS)
}

fn max_durable_attempts() -> u32 {
    std::env::var(MAX_DURABLE_ATTEMPTS_ENV)
        .ok()
        .and_then(|value| value.parse::<u32>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_MAX_DURABLE_ATTEMPTS)
}

fn repair_partial_tail_locked(session_id: &str) -> Result<(), String> {
    const CHUNK: u64 = 8 * 1024;
    let path = queue_path(session_id)?;
    let mut options = OpenOptions::new();
    options.read(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = match options.open(&path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            if read_offset(session_id)? != 0 {
                write_offset(session_id, 0)?;
            }
            return Ok(());
        }
        Err(error) => return Err(format!("{}: {error}", path.display())),
    };
    let length = file
        .metadata()
        .map_err(|error| format!("{}: {error}", path.display()))?
        .len();
    if length == 0 {
        if read_offset(session_id)? != 0 {
            write_offset(session_id, 0)?;
        }
        return Ok(());
    }
    let offset = read_offset(session_id)?;
    if offset > length {
        write_offset(session_id, 0)?;
    }
    file.seek(SeekFrom::End(-1))
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let mut last = [0u8; 1];
    file.read_exact(&mut last)
        .map_err(|error| format!("{}: {error}", path.display()))?;
    if last[0] == b'\n' {
        return Ok(());
    }

    let mut end = length;
    let mut repaired_length = 0u64;
    while end > 0 {
        let start = end.saturating_sub(CHUNK);
        let size = (end - start) as usize;
        let mut chunk = vec![0u8; size];
        file.seek(SeekFrom::Start(start))
            .and_then(|_| file.read_exact(&mut chunk))
            .map_err(|error| format!("{}: {error}", path.display()))?;
        if let Some(index) = chunk.iter().rposition(|byte| *byte == b'\n') {
            repaired_length = start.saturating_add(index as u64).saturating_add(1);
            break;
        }
        end = start;
    }
    let offset = read_offset(session_id)?;
    if offset > repaired_length {
        write_offset(session_id, repaired_length)?;
    }
    file.set_len(repaired_length)
        .and_then(|_| file.sync_data())
        .map_err(|error| format!("{}: {error}", path.display()))
}

fn read_offset(session_id: &str) -> Result<u64, String> {
    let path = offset_path(session_id)?;
    match std::fs::read(&path) {
        // A crash during the lock-protected truncate/write sequence can leave
        // an empty, invalid, or decimal-prefix checkpoint. Empty/invalid data
        // falls back to zero; a numeric prefix is no greater than the intended
        // offset. Both preserve at-least-once delivery: acknowledged records
        // may replay, but pending records are never hidden.
        Ok(bytes) => Ok(serde_json::from_slice::<u64>(&bytes).unwrap_or(0)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(0),
        Err(error) => Err(format!("{}: {error}", path.display())),
    }
}

fn write_offset(session_id: &str, offset: u64) -> Result<(), String> {
    let path = offset_path(session_id)?;
    let bytes = offset.to_string();
    // Every production caller holds QueueLock, so in-place truncate/write is
    // serialized across hook parents and the detached worker. Remember first
    // creation across retries so its directory entry is persisted once.
    let sync_parent = !path.exists();
    let mut retry_index = 0usize;
    loop {
        match write_offset_once(&path, bytes.as_bytes(), sync_parent) {
            Ok(()) => return Ok(()),
            Err((_operation, error))
                if is_retryable_offset_write_error(&error)
                    && retry_index < OFFSET_WRITE_RETRY_DELAYS_MS.len() =>
            {
                let delay_ms = OFFSET_WRITE_RETRY_DELAYS_MS[retry_index];
                retry_index += 1;
                if delay_ms == 0 {
                    std::thread::yield_now();
                } else {
                    std::thread::sleep(Duration::from_millis(delay_ms));
                }
            }
            Err((operation, error)) => {
                return Err(format!(
                    "{}: failed to {operation} queue offset in place: {error}",
                    path.display()
                ));
            }
        }
    }
}

fn write_offset_once(
    path: &Path,
    bytes: &[u8],
    sync_parent: bool,
) -> Result<(), (&'static str, std::io::Error)> {
    let mut options = OpenOptions::new();
    options.create(true).write(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path).map_err(|error| ("open", error))?;
    file.write_all(bytes).map_err(|error| ("write", error))?;
    file.flush().map_err(|error| ("flush", error))?;
    file.sync_data().map_err(|error| ("sync", error))?;
    drop(file);
    if sync_parent {
        agentic_otel_common::state::sync_parent_directory(path)
            .map_err(|error| ("sync parent directory for", error))?;
    }
    Ok(())
}

#[cfg(windows)]
fn is_retryable_offset_write_error(error: &std::io::Error) -> bool {
    matches!(error.raw_os_error(), Some(5 | 32 | 33))
}

#[cfg(not(windows))]
fn is_retryable_offset_write_error(_error: &std::io::Error) -> bool {
    false
}

fn queue_path(session_id: &str) -> Result<PathBuf, String> {
    dirs::session_worker_queue_file(session_id).ok_or_else(|| "missing session id".to_string())
}

fn offset_path(session_id: &str) -> Result<PathBuf, String> {
    dirs::session_worker_queue_offset_file(session_id)
        .ok_or_else(|| "missing session id".to_string())
}

fn retry_path(session_id: &str) -> Result<PathBuf, String> {
    session_queue_artifact(session_id, "worker.queue.retry.json")
}

fn overflow_path(session_id: &str) -> Result<PathBuf, String> {
    session_queue_artifact(session_id, "worker.queue.overflow.json")
}

fn failed_path(session_id: &str) -> Result<PathBuf, String> {
    session_queue_artifact(session_id, "worker.queue.failed.jsonl")
}

fn failed_backup_path(session_id: &str) -> Result<PathBuf, String> {
    session_queue_artifact(session_id, "worker.queue.failed.1.jsonl")
}

fn session_queue_artifact(session_id: &str, name: &str) -> Result<PathBuf, String> {
    dirs::session_state_dir(session_id)
        .map(|path| path.join(name))
        .ok_or_else(|| "missing session id".to_string())
}

fn persist_handoff_intent(session_id: &str) -> Result<(), String> {
    let path = session_queue_artifact(session_id, "worker.handoff.json")?;
    runtime_state::write_json_atomically(
        &path,
        &serde_json::json!({
            "schema_version": 1,
            "created_at_ns": unix_nanos().to_string(),
            "pid": std::process::id(),
        }),
    )
}

pub fn clear_handoff_intent(session_id: &str) -> Result<(), String> {
    let path = session_queue_artifact(session_id, "worker.handoff.json")?;
    match std::fs::remove_file(&path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("{}: {error}", path.display())),
    }
}

pub fn handoff_intent_exists(session_id: &str) -> Result<bool, String> {
    let path = session_queue_artifact(session_id, "worker.handoff.json")?;
    Ok(path.is_file())
}

fn persist_named_temp(
    replacement: tempfile::NamedTempFile,
    destination: &Path,
) -> Result<(), String> {
    let (file, temporary) = replacement
        .keep()
        .map_err(|error| format!("{}: {}", destination.display(), error.error))?;
    drop(file);
    if let Err(error) = agentic_otel_common::state::replace_file_durable(&temporary, destination) {
        let _ = std::fs::remove_file(&temporary);
        return Err(format!("{}: {error}", destination.display()));
    }
    Ok(())
}

fn write_start_marker_atomically(path: &Path, value: &Value) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("{} has no parent", path.display()))?;
    dirs::ensure_private_dir(parent).map_err(|error| error.to_string())?;
    let mut replacement =
        tempfile::NamedTempFile::new_in(parent).map_err(|error| error.to_string())?;
    serde_json::to_writer(replacement.as_file_mut(), value).map_err(|error| error.to_string())?;
    replacement
        .flush()
        .and_then(|_| replacement.as_file().sync_data())
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let (file, temporary) = replacement
        .keep()
        .map_err(|error| format!("{}: {}", path.display(), error.error))?;
    drop(file);
    if let Err(error) = agentic_otel_common::state::replace_file(&temporary, path) {
        let _ = std::fs::remove_file(&temporary);
        return Err(format!("{}: {error}", path.display()));
    }
    Ok(())
}

struct QueueLock {
    _lock: crate::common::file_lock::ExclusiveFileLock,
}

impl QueueLock {
    fn acquire(session_id: &str) -> Result<Self, String> {
        let path = dirs::session_worker_queue_lock_file(session_id)
            .ok_or_else(|| "missing session id".to_string())?;
        let lock = crate::common::file_lock::ExclusiveFileLock::acquire(&path, QUEUE_LOCK_WAIT)?;
        Ok(Self { _lock: lock })
    }
}

fn unix_nanos() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
}
