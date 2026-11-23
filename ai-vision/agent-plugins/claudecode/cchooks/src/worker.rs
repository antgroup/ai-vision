use std::collections::BTreeMap;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::common::dirs;
use crate::common::hook::{extract_session_id, HookEvent};
use crate::common::logger::Logger;
use crate::common::transcript::transcript_ends_with_session_exit_control;
use crate::hooks::langfuse::domain_state::{DomainStateStore, StateDomain};
use crate::hooks::langfuse::state::{
    get_int_field, get_session_state, SessionStateStore, StateTxnError,
};
use crate::hooks::tool_event::{
    now_millis, ClaimOutcome, ReconcileOutcome, RecordOutcome, ToolEventStateStore,
    MAX_STOP_DRAIN_EVENTS, TOOL_ORPHAN_TTL_MS,
};
use crate::hooks::turn_seed::TurnSeed;
use crate::hooks::StopOutcome;

pub const WORKER_COMMAND: &str = "__worker";
pub const WORKER_MODE_ENV: &str = "CCHOOKS_WORKER_MODE";
pub const WORKER_SESSION_ENV: &str = "CCHOOKS_WORKER_SESSION_ID";
pub const WORKER_START_TOKEN_ENV: &str = "CCHOOKS_WORKER_START_TOKEN";
pub const WORKER_QUEUE_MAX_BYTES_ENV: &str = "CCHOOKS_WORKER_QUEUE_MAX_BYTES";
pub const WORKER_QUEUE_MAX_ITEMS_ENV: &str = "CCHOOKS_WORKER_QUEUE_MAX_ITEMS";
pub const WORKER_MAX_RUNTIME_MS_ENV: &str = "CCHOOKS_WORKER_MAX_RUNTIME_MS";
pub const WORKER_MAX_DELIVERY_ATTEMPTS_ENV: &str = "CCHOOKS_WORKER_MAX_DELIVERY_ATTEMPTS";
pub const WAIT_FOR_TRANSCRIPT_INGEST_ENV: &str = "CCHOOKS_WAIT_FOR_TRANSCRIPT_INGEST";
pub const TRANSCRIPT_INGEST_WAIT_MS_ENV: &str = "CCHOOKS_TRANSCRIPT_INGEST_WAIT_MS";
pub const STOP_RECOVERY_MAX_ATTEMPTS_ENV: &str = "CCHOOKS_STOP_RECOVERY_MAX_ATTEMPTS";
pub const TOOL_POST_EXPORT_LEASE_MS: u64 = 120_000;
pub const STOP_RECOVERY_LEASE_MS: u64 = 120_000;
pub const STOP_INTENT_LOCK_TIMEOUT_MS: u64 = 500;
pub const MAX_WORKER_ENVELOPE_BYTES: usize = 64 * 1024 * 1024;
pub const INTERNAL_STOP_INTENT_ID: &str = "cchooks_stop_intent_id";
pub const INTERNAL_STOP_OWNER_TOKEN: &str = "cchooks_stop_owner_token";
pub const INTERNAL_STOP_REVISION: &str = "cchooks_stop_revision";
pub const INTERNAL_STOP_PHASE: &str = "cchooks_stop_phase";
pub const INTERNAL_STRUCTURED_OUTPUT: &str = "cchooks_structured_output";
pub const DEFAULT_QUEUE_MAX_BYTES: u64 = 256 * 1024 * 1024;
pub const DEFAULT_QUEUE_MAX_ITEMS: usize = 4096;
pub const DEFAULT_WORKER_MAX_RUNTIME_MS: u64 = 10 * 60 * 1000;
const QUEUE_COMPACT_MIN_BYTES: u64 = 8 * 1024 * 1024;
const DEFAULT_MAX_DELIVERY_ATTEMPTS: u32 = 3;
const DEFAULT_STOP_RECOVERY_MAX_ATTEMPTS: u64 = 3;
const STOP_RECOVERY_STATE_VERSION: u64 = 1;
const STOP_RECOVERY_MAX_NO_PROGRESS_ATTEMPTS: u64 = 3;
const STOP_RECOVERY_BACKOFF_BASE_MS: u64 = 500;
const STOP_RECOVERY_BACKOFF_MAX_MS: u64 = 8_000;
const MAX_FAILED_LOG_BYTES: u64 = 128 * 1024 * 1024;
const START_MARKER_TTL_NS: u128 = 30_000_000_000;
const WORKER_START_WAIT: std::time::Duration = std::time::Duration::from_secs(30);
const WORKER_START_POLL: std::time::Duration = std::time::Duration::from_millis(2);
const WORKER_HANDOFF_ATTEMPTS: usize = 3;
const QUEUE_LOCK_WAIT: std::time::Duration = std::time::Duration::from_secs(2);
const SESSION_IDLE_POLL: std::time::Duration = std::time::Duration::from_millis(25);
const TRANSCRIPT_INGEST_POLL: std::time::Duration = std::time::Duration::from_millis(25);
const DEFAULT_TRANSCRIPT_INGEST_WAIT_MS: u64 = 3_000;
const OFFSET_WRITE_RETRY_DELAYS_MS: [u64; 10] = [0, 1, 2, 4, 8, 16, 32, 64, 128, 250];
const HOOK_BATCH_MAX_ITEMS: usize = 32;
const HOOK_BATCH_MAX_WALL: Duration = Duration::from_millis(100);
const MAINTENANCE_WAIT_MAX: Duration = Duration::from_millis(500);
const SESSION_END_EXIT_RECOVERY_CHECKED_LEN_FIELD: &str =
    "session_end_exit_recovery_checked_file_len";
const PHASE_AWAITING_CONTINUATION: &str = "awaiting_continuation";
const PHASE_FINALIZING: &str = "finalizing";
const PHASE_FALLBACK_FINALIZING: &str = "fallback_finalizing";
const PHASE_FINALIZER_IN_FLIGHT: &str = "finalizer_in_flight";

#[derive(Debug, Clone)]
pub struct StopIntentHandoff {
    intent_id: Option<String>,
    owner_token: Option<String>,
    revision: Option<u64>,
    phase: &'static str,
    structured_output: bool,
    finalizer_authorized: bool,
}

impl StopIntentHandoff {
    fn unpersisted(event: &HookEvent, payload: &Value, structured_output: bool) -> Self {
        let active = stop_hook_active(payload);
        // A provisional Stop without durable coordinator state cannot be
        // completed safely by the later active Stop. Preserve the legacy
        // immediate path instead of losing the whole turn.
        let fail_open_legacy = structured_output && matches!(event, HookEvent::Stop) && !active;
        Self {
            intent_id: None,
            owner_token: None,
            revision: None,
            phase: PHASE_FINALIZING,
            structured_output: structured_output && !fail_open_legacy,
            finalizer_authorized: false,
        }
    }

    fn finalizer_in_flight(structured_output: bool) -> Self {
        Self {
            intent_id: None,
            owner_token: None,
            revision: None,
            phase: PHASE_FINALIZER_IN_FLIGHT,
            structured_output,
            finalizer_authorized: false,
        }
    }

    pub fn apply_to_payload(&self, payload: &mut Value) {
        if let Some(object) = payload.as_object_mut() {
            for field in [
                INTERNAL_STOP_INTENT_ID,
                INTERNAL_STOP_OWNER_TOKEN,
                INTERNAL_STOP_REVISION,
                INTERNAL_STOP_PHASE,
                INTERNAL_STRUCTURED_OUTPUT,
                crate::hooks::INTERNAL_FINALIZER_AUTHORIZED,
            ] {
                object.remove(field);
            }
        }
        payload[INTERNAL_STOP_PHASE] = json!(self.phase);
        payload[INTERNAL_STRUCTURED_OUTPUT] = json!(self.structured_output);
        payload[crate::hooks::INTERNAL_FINALIZER_AUTHORIZED] = json!(self.finalizer_authorized);
        if let Some(intent_id) = self.intent_id.as_deref() {
            payload[INTERNAL_STOP_INTENT_ID] = json!(intent_id);
        }
        if let Some(owner_token) = self.owner_token.as_deref() {
            payload[INTERNAL_STOP_OWNER_TOKEN] = json!(owner_token);
        }
        if let Some(revision) = self.revision {
            payload[INTERNAL_STOP_REVISION] = json!(revision);
        }
    }
}

#[derive(Debug, Clone)]
pub struct StopIntentOwner {
    intent_id: String,
    owner_token: String,
    revision: u64,
}

pub enum StopWorkerDecision {
    Run { owner: Option<StopIntentOwner> },
    AwaitingContinuation,
    Stale,
}

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

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionIdleWaitOutcome {
    Drained,
    TimedOut,
    WakeFailed(String),
    StateFailed(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TranscriptIngestWaitOutcome {
    Acked,
    TimedOut,
    StateFailed(String),
}

#[derive(Debug)]
pub struct QueueItem {
    pub payload: Value,
    pub queued_at_ns: u128,
    pub env: BTreeMap<String, String>,
    pub turn_seed: Option<TurnSeed>,
    pub transcript_end_offset: Option<u64>,
    pub transcript_ingest_receipt: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum QueueDisposition {
    Ack,
    Retry(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MaintenanceDisposition {
    Idle,
    Waiting(Duration),
    DidWork,
}

#[derive(Debug, Serialize, Deserialize)]
struct QueueEnvelope {
    queued_at_ns: String,
    #[serde(default)]
    env: BTreeMap<String, String>,
    #[serde(default)]
    turn_seed: Option<TurnSeed>,
    #[serde(default)]
    transcript_end_offset: Option<u64>,
    #[serde(default)]
    transcript_ingest_receipt: Option<String>,
    payload: Value,
}

#[derive(Deserialize)]
struct QueueSummaryEnvelope {
    #[serde(rename = "queued_at_ns")]
    _queued_at_ns: String,
    #[serde(default)]
    turn_seed: Option<TurnSeed>,
    #[serde(default)]
    transcript_end_offset: Option<u64>,
    #[serde(default)]
    transcript_ingest_receipt: Option<String>,
    payload: QueueSummaryPayload,
}

#[derive(Deserialize)]
struct QueueSummaryPayload {
    hook_event_name: Option<String>,
    transcript_path: Option<String>,
    tool_use_id: Option<String>,
}

impl QueueSummaryPayload {
    fn from_value(value: &Value) -> Self {
        Self {
            hook_event_name: value
                .get("hook_event_name")
                .and_then(Value::as_str)
                .map(str::to_owned),
            transcript_path: value
                .get("transcript_path")
                .and_then(Value::as_str)
                .map(str::to_owned),
            tool_use_id: value
                .get("tool_use_id")
                .and_then(Value::as_str)
                .map(str::to_owned),
        }
    }

    fn coalescing_key(&self) -> Option<String> {
        let event = self.hook_event_name.as_deref()?;
        match event {
            "Stop" | "StopFailure" | "SessionEnd" => Some(format!(
                "{event}:{}",
                self.transcript_path.as_deref().unwrap_or_default()
            )),
            "PreToolUse" | "PostToolUse" | "PostToolUseFailure" => self
                .tool_use_id
                .as_deref()
                .filter(|value| !value.is_empty())
                .map(|tool_use_id| format!("{event}:{tool_use_id}")),
            _ => None,
        }
    }

    fn is_seeded_polling_stop(&self, turn_seed: Option<&TurnSeed>) -> bool {
        turn_seed.is_some()
            && matches!(
                self.hook_event_name.as_deref(),
                Some("Stop" | "StopFailure")
            )
    }
}

struct QueueRecord {
    item: QueueItem,
    raw: Vec<u8>,
    offset: u64,
    next_offset: u64,
}

enum QueueRead {
    Item(Box<QueueRecord>),
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

#[derive(Debug, Default, Serialize, PartialEq, Eq)]
pub struct WorkerStatus {
    pub sessions: usize,
    pub pending_queue_items: usize,
    pub dropped_queue_items: u64,
    pub workers_active: usize,
    pub worker_starting: usize,
    pub worker_spawn_failures: usize,
    pub pending_stops: usize,
}

#[derive(Debug, Serialize, Deserialize)]
struct RetryState {
    offset: u64,
    attempts: u32,
    last_error: String,
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

#[derive(Debug, Clone, Serialize, Deserialize)]
struct WorkerStartMarker {
    schema_version: u32,
    token: String,
    created_at_ns: String,
    expires_at_ns: String,
    #[serde(default = "starting_marker_status")]
    status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    last_error: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pid: Option<u32>,
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

pub struct SessionOwner {
    _lock: crate::common::file_lock::ExclusiveFileLock,
}

impl SessionOwner {
    pub fn acquire(session_id: &str, timeout: std::time::Duration) -> Result<Self, String> {
        let path = dirs::session_worker_lock_file(session_id)
            .ok_or_else(|| "missing session id".to_string())?;
        let lock = crate::common::file_lock::ExclusiveFileLock::acquire(&path, timeout)?;
        Ok(Self { _lock: lock })
    }

    pub fn try_acquire(session_id: &str) -> Result<Option<Self>, String> {
        let path = dirs::session_worker_lock_file(session_id)
            .ok_or_else(|| "missing session id".to_string())?;
        Ok(
            crate::common::file_lock::ExclusiveFileLock::try_acquire(&path)?
                .map(|lock| Self { _lock: lock }),
        )
    }
}

pub fn is_worker_mode() -> bool {
    crate::common::hook::env_is_truthy(WORKER_MODE_ENV)
}

pub fn inherited_session_id() -> Option<String> {
    crate::common::env::var(WORKER_SESSION_ENV)
        .ok()
        .filter(|session_id| !session_id.trim().is_empty())
}

pub fn transcript_end_offset(payload: &Value) -> Option<u64> {
    let path = crate::common::hook::extract_transcript_path(payload);
    (!path.trim().is_empty())
        .then(|| std::fs::metadata(path).ok().map(|metadata| metadata.len()))
        .flatten()
}

#[cfg_attr(test, allow(dead_code))]
pub fn spawn_detached(payload: &Value, logger: &Logger) {
    spawn_detached_with_turn_seed(payload, None, logger);
}

pub fn spawn_detached_with_turn_seed(
    payload: &Value,
    turn_seed: Option<TurnSeed>,
    logger: &Logger,
) {
    spawn_detached_with_stop_snapshot(payload, turn_seed, transcript_end_offset(payload), logger);
}

pub fn spawn_detached_with_stop_snapshot(
    payload: &Value,
    turn_seed: Option<TurnSeed>,
    transcript_end_offset: Option<u64>,
    logger: &Logger,
) {
    let _ = spawn_detached_with_stop_snapshot_and_receipt(
        payload,
        turn_seed,
        transcript_end_offset,
        None,
        logger,
    );
}

pub fn spawn_detached_with_stop_snapshot_and_receipt(
    payload: &Value,
    turn_seed: Option<TurnSeed>,
    transcript_end_offset: Option<u64>,
    transcript_ingest_receipt: Option<String>,
    logger: &Logger,
) -> bool {
    let original_bytes = match serde_json::to_vec(payload) {
        Ok(bytes) => bytes.len(),
        Err(error) => {
            logger.warn(&format!(
                "worker spawn skipped: payload serialization failed: {error}"
            ));
            return false;
        }
    };
    spawn_detached_checked(
        payload,
        original_bytes,
        turn_seed,
        transcript_end_offset,
        transcript_ingest_receipt,
        logger,
    )
}

pub fn spawn_detached_parsed(payload: &Value, original_bytes: usize, logger: &Logger) {
    let _ = spawn_detached_checked(payload, original_bytes, None, None, None, logger);
}

fn spawn_detached_checked(
    payload: &Value,
    original_bytes: usize,
    turn_seed: Option<TurnSeed>,
    transcript_end_offset: Option<u64>,
    transcript_ingest_receipt: Option<String>,
    logger: &Logger,
) -> bool {
    if original_bytes == 0 {
        logger.warn("worker spawn skipped: empty payload envelope");
        return false;
    }
    if original_bytes > MAX_WORKER_ENVELOPE_BYTES {
        logger.warn(&format!(
            "worker spawn skipped: payload envelope exceeds {} bytes",
            MAX_WORKER_ENVELOPE_BYTES
        ));
        return false;
    }

    let session_id = extract_session_id(payload);
    match enqueue_and_wake_with_stop_snapshot(
        &session_id,
        payload,
        original_bytes,
        turn_seed,
        transcript_end_offset,
        transcript_ingest_receipt,
        logger,
    ) {
        Ok(EnqueueOutcome::Spawned) => logger.debug(&format!(
            "worker queue handoff accepted: consumer=spawned envelope_bytes={}",
            original_bytes
        )),
        Ok(EnqueueOutcome::Appended) => logger.debug(&format!(
            "worker queue handoff accepted: consumer=existing envelope_bytes={}",
            original_bytes
        )),
        Ok(EnqueueOutcome::Coalesced) => logger.debug(&format!(
            "worker queue handoff coalesced: envelope_bytes={}",
            original_bytes
        )),
        Err(error) => {
            logger.warn(&format!("worker enqueue failed open: {error}"));
            return false;
        }
    }
    true
}

#[cfg_attr(not(test), allow(dead_code))]
pub fn enqueue_and_wake(
    session_id: &str,
    payload: &Value,
    original_bytes: usize,
    logger: &Logger,
) -> Result<EnqueueOutcome, String> {
    enqueue_and_wake_with_turn_seed(session_id, payload, original_bytes, None, logger)
}

pub fn enqueue_and_wake_with_turn_seed(
    session_id: &str,
    payload: &Value,
    original_bytes: usize,
    turn_seed: Option<TurnSeed>,
    logger: &Logger,
) -> Result<EnqueueOutcome, String> {
    enqueue_and_wake_with_stop_snapshot(
        session_id,
        payload,
        original_bytes,
        turn_seed,
        transcript_end_offset(payload),
        None,
        logger,
    )
}

fn enqueue_and_wake_with_stop_snapshot(
    session_id: &str,
    payload: &Value,
    original_bytes: usize,
    turn_seed: Option<TurnSeed>,
    transcript_end_offset: Option<u64>,
    transcript_ingest_receipt: Option<String>,
    logger: &Logger,
) -> Result<EnqueueOutcome, String> {
    if session_id.trim().is_empty() {
        return Err("missing session id".to_string());
    }
    if original_bytes == 0 {
        return Err("empty worker envelope".to_string());
    }
    if original_bytes > MAX_WORKER_ENVELOPE_BYTES {
        return Err(format!(
            "worker envelope exceeds {} bytes",
            MAX_WORKER_ENVELOPE_BYTES
        ));
    }

    let append_outcome = match append_queue_with_stop_snapshot(
        session_id,
        payload,
        turn_seed,
        transcript_end_offset,
        transcript_ingest_receipt,
    ) {
        Ok(outcome) => outcome,
        Err(AppendFailure::Full(error)) => {
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

pub fn record_session_identity(session_id: &str) -> Result<(), String> {
    let path =
        dirs::session_identity_file(session_id).ok_or_else(|| "missing session id".to_string())?;
    if path.is_file() {
        return Ok(());
    }
    write_json_atomically(&path, &json!({"session_id": session_id}))
}

fn state_session_dirs(filter: Option<&str>) -> Result<Vec<PathBuf>, String> {
    if let Some(session_id) = filter.map(str::trim).filter(|value| !value.is_empty()) {
        return dirs::session_state_dir(session_id)
            .map(|path| vec![path])
            .ok_or_else(|| "missing session id".to_string());
    }
    let entries = match std::fs::read_dir(dirs::state_dir()) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(format!("scan state root: {error}")),
    };
    let mut session_dirs = Vec::new();
    for entry in entries {
        let entry = entry.map_err(|error| format!("read state entry: {error}"))?;
        if !entry.file_type().map(|kind| kind.is_dir()).unwrap_or(false)
            || entry.file_name() == "sessions"
        {
            continue;
        }
        let path = entry.path();
        if [
            "worker.lock",
            "worker.starting.json",
            "worker.queue.jsonl",
            "state_stop.json",
            "state_identity.json",
        ]
        .iter()
        .any(|name| path.join(name).exists())
        {
            session_dirs.push(path);
        }
    }
    session_dirs.sort();
    session_dirs.dedup();
    Ok(session_dirs)
}

fn pending_stop_count_in_dir(session_dir: &Path) -> usize {
    let path = session_dir.join(StateDomain::Stop.file_name());
    std::fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .and_then(|state| state.get("pending_final_export").cloned())
        .filter(|intent| {
            intent.is_object()
                && intent.get("status").and_then(Value::as_str) != Some("failed_permanent")
        })
        .is_some() as usize
}

fn legacy_pending_stop_files(filter: Option<&str>) -> Vec<PathBuf> {
    if let Some(session_id) = filter.map(str::trim).filter(|value| !value.is_empty()) {
        return dirs::session_state_file(session_id)
            .filter(|path| path.is_file())
            .into_iter()
            .collect();
    }
    let root = dirs::state_dir().join("sessions");
    let Ok(entries) = std::fs::read_dir(root) else {
        return Vec::new();
    };
    entries
        .filter_map(Result::ok)
        .map(|entry| entry.path().join("state.json"))
        .filter(|path| path.is_file())
        .collect()
}

fn pending_stop_count_in_legacy_file(path: &Path) -> usize {
    std::fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .and_then(|state| state.as_object().cloned())
        .map(|sessions| {
            sessions
                .values()
                .filter(|session| {
                    session.get("pending_final_export").is_some_and(|intent| {
                        intent.is_object()
                            && intent.get("status").and_then(Value::as_str)
                                != Some("failed_permanent")
                    })
                })
                .count()
        })
        .unwrap_or(0)
}

fn pending_count_in_dir(session_dir: &Path) -> Result<usize, String> {
    // Status does not take QueueLock and may observe a checkpoint mid-write.
    let offset = read_offset_file(&session_dir.join("worker.queue.offset.json"))?;
    let queue_path = session_dir.join("worker.queue.jsonl");
    let mut file = match File::open(&queue_path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(0),
        Err(error) => return Err(format!("{}: {error}", queue_path.display())),
    };
    let length = file
        .metadata()
        .map_err(|error| format!("{}: {error}", queue_path.display()))?
        .len();
    file.seek(SeekFrom::Start(if offset <= length { offset } else { 0 }))
        .map_err(|error| format!("{}: {error}", queue_path.display()))?;
    let mut count = 0usize;
    for line in BufReader::new(file).split(b'\n') {
        if !line
            .map_err(|error| format!("{}: {error}", queue_path.display()))?
            .is_empty()
        {
            count = count.saturating_add(1);
        }
    }
    Ok(count)
}

fn overflow_count_in_dir(session_dir: &Path) -> Result<u64, String> {
    let path = session_dir.join("worker.queue.overflow.json");
    match std::fs::read(&path) {
        Ok(bytes) => serde_json::from_slice::<OverflowState>(&bytes)
            .map(|state| state.dropped_items)
            .map_err(|error| format!("{}: {error}", path.display())),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(0),
        Err(error) => Err(format!("{}: {error}", path.display())),
    }
}

fn start_marker_status_in_dir(session_dir: &Path) -> Option<String> {
    std::fs::read(session_dir.join("worker.starting.json"))
        .ok()
        .and_then(|bytes| serde_json::from_slice::<WorkerStartMarker>(&bytes).ok())
        .map(|marker| {
            if marker.status != "starting" {
                return marker.status;
            }
            let expires = marker.expires_at_ns.parse::<u128>().unwrap_or(0);
            if expires > unix_nanos() && marker.pid.is_some_and(start_marker_process_is_alive) {
                "starting".to_string()
            } else {
                "stale".to_string()
            }
        })
        .or_else(|| {
            session_dir
                .join("worker.starting.json")
                .exists()
                .then(|| "invalid".to_string())
        })
}

fn active_worker_in_dir(session_dir: &Path) -> Result<usize, String> {
    let lock_path = session_dir.join("worker.lock");
    if !lock_path.exists() {
        return Ok(0);
    }
    Ok(crate::common::file_lock::ExclusiveFileLock::try_acquire(&lock_path)?.is_none() as usize)
}

pub fn worker_status(filter: Option<&str>) -> Result<WorkerStatus, String> {
    let mut status = WorkerStatus::default();
    let mut visible_session_keys = std::collections::HashSet::new();
    let mut new_pending_stop_keys = std::collections::HashSet::new();
    for session_dir in state_session_dirs(filter)? {
        let pending = pending_count_in_dir(&session_dir)?;
        let dropped = overflow_count_in_dir(&session_dir)?;
        let marker = start_marker_status_in_dir(&session_dir);
        let starting = (marker.as_deref() == Some("starting")) as usize;
        let spawn_failure = marker
            .as_deref()
            .is_some_and(|value| value == "spawn_failed" || value == "stale" || value == "invalid")
            as usize;
        let active_worker = active_worker_in_dir(&session_dir)?;
        let pending_stops = pending_stop_count_in_dir(&session_dir);
        let session_key = session_dir
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("")
            .to_string();
        if pending + starting + spawn_failure + active_worker + pending_stops > 0 {
            status.sessions = status.sessions.saturating_add(1);
            visible_session_keys.insert(session_key.clone());
        }
        if pending_stops > 0 {
            new_pending_stop_keys.insert(session_key);
        }
        status.pending_queue_items = status.pending_queue_items.saturating_add(pending);
        status.dropped_queue_items = status.dropped_queue_items.saturating_add(dropped);
        status.workers_active = status.workers_active.saturating_add(active_worker);
        status.worker_starting = status.worker_starting.saturating_add(starting);
        status.worker_spawn_failures = status.worker_spawn_failures.saturating_add(spawn_failure);
        status.pending_stops = status.pending_stops.saturating_add(pending_stops);
    }
    for path in legacy_pending_stop_files(filter) {
        let pending_stops = pending_stop_count_in_legacy_file(&path);
        let session_key = path
            .parent()
            .and_then(Path::file_name)
            .and_then(|value| value.to_str())
            .unwrap_or("")
            .to_string();
        if pending_stops == 0 || new_pending_stop_keys.contains(&session_key) {
            continue;
        }
        status.pending_stops = status.pending_stops.saturating_add(pending_stops);
        if visible_session_keys.insert(session_key) {
            status.sessions = status.sessions.saturating_add(1);
        }
    }
    Ok(status)
}

pub fn wake_session(session_id: &str, logger: &Logger) -> Result<bool, String> {
    let Some(owner) = SessionOwner::try_acquire(session_id)? else {
        logger.debug("worker queue wake coalesced: consumer=existing");
        return Ok(false);
    };
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let token = match claim_start_marker(session_id)? {
        StartClaim::Existing => {
            logger.debug("worker queue wake coalesced: consumer=starting");
            return Ok(false);
        }
        StartClaim::Claimed(token) => token,
    };
    match spawn_worker_process(&executable, session_id, &token) {
        Ok(mut child) => {
            if let Err(error) = publish_start_marker_pid(session_id, &token, child.id()) {
                let _ = child.kill();
                let _ = child.wait();
                record_start_failure(session_id, &token, &error)?;
                return Err(error);
            }
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

/// Wait until the durable queue is empty and no session worker owns it.
///
/// The queue lock is retained while probing `worker.lock`. This pairs with
/// `drain_queue` releasing its session owner before the queue lock and closes
/// the gap where an item has been read but not checkpointed yet. If a worker
/// dies with an unacknowledged item, the released owner makes that item
/// eligible for a replacement wake.
pub fn wait_for_session_idle(
    session_id: &str,
    timeout: std::time::Duration,
    logger: &Logger,
) -> SessionIdleWaitOutcome {
    wait_for_session_idle_with_wake(session_id, timeout, logger, wake_session)
}

pub fn transcript_ingest_wait_enabled() -> bool {
    crate::common::hook::env_is_truthy(WAIT_FOR_TRANSCRIPT_INGEST_ENV)
}

pub fn transcript_ingest_wait_timeout() -> Duration {
    Duration::from_millis(
        crate::common::env::var(TRANSCRIPT_INGEST_WAIT_MS_ENV)
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(DEFAULT_TRANSCRIPT_INGEST_WAIT_MS),
    )
}

pub fn prepare_transcript_ingest_receipt(session_id: &str) -> Result<String, String> {
    let receipt = format!("{}-{}", std::process::id(), unix_nanos());
    let request = transcript_ingest_receipt_path(session_id, &receipt, "request")?;
    write_json_atomically(
        &request,
        &json!({"schema_version": 1, "receipt": receipt, "status": "waiting"}),
    )?;
    Ok(receipt)
}

pub fn acknowledge_transcript_ingest(
    session_id: &str,
    receipt: Option<&str>,
) -> Result<(), String> {
    crate::hooks::acknowledge_transcript_ingest(session_id, receipt)
}

pub fn wait_for_transcript_ingest(
    session_id: &str,
    receipt: &str,
    timeout: Duration,
) -> TranscriptIngestWaitOutcome {
    let request = match transcript_ingest_receipt_path(session_id, receipt, "request") {
        Ok(path) => path,
        Err(error) => return TranscriptIngestWaitOutcome::StateFailed(error),
    };
    let ack = match transcript_ingest_receipt_path(session_id, receipt, "ack") {
        Ok(path) => path,
        Err(error) => return TranscriptIngestWaitOutcome::StateFailed(error),
    };
    let started = std::time::Instant::now();
    let outcome = loop {
        if ack.is_file() {
            break TranscriptIngestWaitOutcome::Acked;
        }
        if started.elapsed() >= timeout {
            break TranscriptIngestWaitOutcome::TimedOut;
        }
        std::thread::sleep(TRANSCRIPT_INGEST_POLL.min(timeout.saturating_sub(started.elapsed())));
    };
    let _ = std::fs::remove_file(&request);
    let _ = std::fs::remove_file(&ack);
    outcome
}

fn transcript_ingest_receipt_path(
    session_id: &str,
    receipt: &str,
    suffix: &str,
) -> Result<PathBuf, String> {
    dirs::session_transcript_ingest_receipt_file(session_id, receipt, suffix)
        .ok_or_else(|| "invalid transcript ingest receipt".to_string())
}

fn wait_for_session_idle_with_wake<F>(
    session_id: &str,
    timeout: std::time::Duration,
    logger: &Logger,
    mut wake: F,
) -> SessionIdleWaitOutcome
where
    F: FnMut(&str, &Logger) -> Result<bool, String>,
{
    let started = std::time::Instant::now();
    loop {
        if started.elapsed() >= timeout {
            return SessionIdleWaitOutcome::TimedOut;
        }

        let queue_lock = match QueueLock::try_acquire(session_id) {
            Ok(Some(lock)) => lock,
            Ok(None) => {
                std::thread::sleep(
                    SESSION_IDLE_POLL.min(timeout.saturating_sub(started.elapsed())),
                );
                continue;
            }
            Err(error) => return SessionIdleWaitOutcome::StateFailed(error),
        };
        if let Err(error) = repair_partial_tail_locked(session_id) {
            return SessionIdleWaitOutcome::StateFailed(error);
        }
        let queue_empty = match read_next_locked(session_id) {
            Ok(QueueRead::Empty) => true,
            Ok(QueueRead::Item(_) | QueueRead::Corrupt { .. }) => false,
            Err(error) => return SessionIdleWaitOutcome::StateFailed(error),
        };
        let owner = match SessionOwner::try_acquire(session_id) {
            Ok(owner) => owner,
            Err(error) => return SessionIdleWaitOutcome::StateFailed(error),
        };

        if queue_empty && owner.is_some() {
            drop(owner);
            drop(queue_lock);
            return SessionIdleWaitOutcome::Drained;
        }

        let should_wake = !queue_empty && owner.is_some();
        drop(owner);
        drop(queue_lock);
        if should_wake {
            if let Err(error) = wake(session_id, logger) {
                return SessionIdleWaitOutcome::WakeFailed(error);
            }
        }

        std::thread::sleep(SESSION_IDLE_POLL.min(timeout.saturating_sub(started.elapsed())));
    }
}

/// Runtime handoffs confirm that the replacement acquired session ownership.
/// Parent hooks keep using `wake_session` so their latency is not coupled to
/// process startup, while an expiring worker can retry an early child death.
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
    startup_wait: std::time::Duration,
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
        if let Err(error) = publish_start_marker_pid(session_id, &token, child.id()) {
            let _ = child.kill();
            let _ = child.wait();
            record_start_failure(session_id, &token, &error)?;
            last_error = Some(error);
            continue;
        }
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

fn spawn_worker_process(executable: &Path, session_id: &str, token: &str) -> Result<Child, String> {
    if crate::common::hook::env_is_truthy("CCHOOKS_TEST_WORKER_SPAWN_FAILURE") {
        return Err("test-injected worker spawn failure".to_string());
    }

    #[cfg(windows)]
    clear_inheritable_hook_stdio()?;

    let mut command = Command::new(executable);
    command
        .arg(WORKER_COMMAND)
        .env(WORKER_MODE_ENV, "true")
        .env(WORKER_SESSION_ENV, session_id)
        .env(WORKER_START_TOKEN_ENV, token)
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

    // This mutates process-global handle metadata. The hook parent is
    // intentionally single-threaded; if concurrent child spawning is added,
    // replace this with an explicit inherited-handle allowlist.
    // Rust's stable Windows Command implementation enables bInheritHandles by
    // default. Synchronous hook hosts launch cchooks with inheritable standard
    // pipe handles, so merely assigning NUL as the worker's stdio can still
    // copy the old pipes as unrelated inherited handles. Clearing inheritance
    // leaves the handles usable by this parent while preventing them from
    // accompanying the fresh NUL handles created for the detached worker.
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

fn wait_for_existing_worker_start(
    session_id: &str,
    timeout: std::time::Duration,
) -> Result<bool, String> {
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
    timeout: std::time::Duration,
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

pub fn acquire_queued_worker(session_id: &str) -> Result<SessionOwner, String> {
    if let Ok(delay_ms) = crate::common::env::var("CCHOOKS_TEST_WORKER_START_DELAY_MS") {
        if let Ok(delay_ms) = delay_ms.parse::<u64>() {
            std::thread::sleep(std::time::Duration::from_millis(delay_ms));
        }
    }
    let owner = SessionOwner::acquire(session_id, WORKER_START_WAIT)?;
    if let Some(token) = worker_start_token() {
        consume_start_marker(session_id, &token)?;
    }
    Ok(owner)
}

fn worker_start_token() -> Option<String> {
    crate::common::env::var(WORKER_START_TOKEN_ENV)
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
                // claim_start_marker is called only while this process owns
                // worker.lock. A viable earlier starter therefore has to be a
                // still-live process waiting to acquire that lock. Missing or
                // dead PIDs are stale pre-ownership starts and can be reclaimed
                // immediately instead of stranding the queue until another hook.
                if marker.pid.is_some_and(start_marker_process_is_alive) {
                    return Ok(StartClaim::Existing);
                }
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
        pid: None,
    };
    write_start_marker_atomically(
        &path,
        &serde_json::to_value(marker).map_err(|error| error.to_string())?,
    )?;
    Ok(StartClaim::Claimed(token))
}

fn publish_start_marker_pid(session_id: &str, token: &str, pid: u32) -> Result<(), String> {
    let path = dirs::session_worker_start_file(session_id)
        .ok_or_else(|| "missing session id".to_string())?;
    let Some(mut marker) = std::fs::read(&path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<WorkerStartMarker>(&bytes).ok())
        .filter(|marker| marker.token == token && marker.status == "starting")
    else {
        return Ok(());
    };
    marker.pid = Some(pid);
    write_start_marker_atomically(
        &path,
        &serde_json::to_value(marker).map_err(|error| error.to_string())?,
    )
}

#[cfg(unix)]
fn start_marker_process_is_alive(pid: u32) -> bool {
    let Ok(pid) = i32::try_from(pid) else {
        return false;
    };
    if unsafe { libc::kill(pid, 0) } == 0 {
        return true;
    }
    std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[cfg(windows)]
fn start_marker_process_is_alive(pid: u32) -> bool {
    use std::ffi::c_void;

    type Handle = *mut c_void;
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    const STILL_ACTIVE: u32 = 259;
    const ERROR_ACCESS_DENIED: i32 = 5;

    #[link(name = "kernel32")]
    extern "system" {
        fn OpenProcess(desired_access: u32, inherit_handle: i32, process_id: u32) -> Handle;
        fn GetExitCodeProcess(process: Handle, exit_code: *mut u32) -> i32;
        fn CloseHandle(object: Handle) -> i32;
    }

    let process = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if process.is_null() {
        return std::io::Error::last_os_error().raw_os_error() == Some(ERROR_ACCESS_DENIED);
    }
    let mut exit_code = 0_u32;
    let live =
        unsafe { GetExitCodeProcess(process, &mut exit_code) } != 0 && exit_code == STILL_ACTIVE;
    unsafe {
        CloseHandle(process);
    }
    live
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

#[cfg_attr(not(test), allow(dead_code))]
pub fn write_stop_intent(
    event: &HookEvent,
    payload: &Value,
    structured_output: bool,
    logger: &Logger,
) -> StopIntentHandoff {
    write_stop_intent_with_turn_seed(
        event,
        payload,
        structured_output,
        None,
        transcript_end_offset(payload),
        logger,
    )
}

pub fn write_stop_intent_with_turn_seed(
    event: &HookEvent,
    payload: &Value,
    structured_output: bool,
    turn_seed: Option<&TurnSeed>,
    transcript_end_offset: Option<u64>,
    logger: &Logger,
) -> StopIntentHandoff {
    let session_id = extract_session_id(payload);
    if session_id.trim().is_empty() {
        return unpersisted_stop_handoff(event, payload, structured_output, logger);
    }
    let store = match DomainStateStore::for_session(
        &session_id,
        StateDomain::Stop,
        STOP_INTENT_LOCK_TIMEOUT_MS,
    ) {
        Ok(store) => store,
        Err(error) => {
            logger.warn(&format!("stop intent skipped: {error}"));
            return unpersisted_stop_handoff(event, payload, structured_output, logger);
        }
    };
    let transcript_path = crate::common::hook::extract_transcript_path(payload);
    let active = stop_hook_active(payload);
    let persisted = store.update(|state| {
        state["schema_version"] = json!(3);
        let current = state.get("pending_final_export").cloned();
        let current_is_structured = current
            .as_ref()
            .and_then(|intent| intent.get("structured_output"))
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let current_finalizer_in_flight = current_is_structured
            && current.as_ref().is_some_and(|intent| {
                matches!(
                    intent.get("phase").and_then(Value::as_str),
                    Some(PHASE_FINALIZING | PHASE_FALLBACK_FINALIZING)
                ) && intent
                    .get("recovery_lease_until_ms")
                    .and_then(Value::as_u64)
                    .is_some_and(|lease_until_ms| lease_until_ms > now_millis())
            });
        if current_finalizer_in_flight {
            if structured_output && matches!(event, HookEvent::SessionEnd) {
                let Some(current_intent) = current.as_ref() else {
                    return Ok(StopIntentHandoff::finalizer_in_flight(true));
                };
                let intent_id = current_intent
                    .get("intent_id")
                    .and_then(Value::as_str)
                    .map(str::to_string);
                let owner_token = current_intent
                    .get("owner_token")
                    .and_then(Value::as_str)
                    .map(str::to_string);
                let revision = current_intent.get("revision").and_then(Value::as_u64);
                if intent_id.is_some() && owner_token.is_some() && revision.is_some() {
                    let phase = if current_intent.get("phase").and_then(Value::as_str)
                        == Some(PHASE_FALLBACK_FINALIZING)
                    {
                        PHASE_FALLBACK_FINALIZING
                    } else {
                        PHASE_FINALIZING
                    };
                    // The per-session FIFO ensures this SessionEnd runs after
                    // the active Stop. If that Stop completed, this owner is
                    // stale and skips; if it deferred, SessionEnd gets one
                    // authoritative lifecycle finalization opportunity.
                    return Ok(StopIntentHandoff {
                        intent_id,
                        owner_token,
                        revision,
                        phase,
                        structured_output: true,
                        finalizer_authorized: true,
                    });
                }
            }
            return Ok(StopIntentHandoff::finalizer_in_flight(true));
        }
        let current_is_structured_awaiting = current.as_ref().is_some_and(|intent| {
            intent.get("structured_output").and_then(Value::as_bool) == Some(true)
                && intent.get("phase").and_then(Value::as_str) == Some(PHASE_AWAITING_CONTINUATION)
        });

        // Claude Code's active Stop is only an authorized finalizer when it
        // follows the persisted provisional Stop from this same session.
        if structured_output
            && matches!(event, HookEvent::Stop)
            && active
            && !current_is_structured_awaiting
        {
            return Ok(StopIntentHandoff {
                intent_id: None,
                owner_token: None,
                revision: None,
                phase: PHASE_FINALIZING,
                structured_output: true,
                finalizer_authorized: false,
            });
        }

        let reuse_current = current_is_structured_awaiting
            && (structured_output
                || matches!(event, HookEvent::StopFailure | HookEvent::SessionEnd)
                || matches!(event, HookEvent::Stop) && active);
        let intent_id = if reuse_current {
            current
                .as_ref()
                .and_then(|intent| intent.get("intent_id"))
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(new_stop_intent_id)
        } else {
            new_stop_intent_id()
        };
        let revision = if reuse_current {
            current
                .as_ref()
                .and_then(|intent| intent.get("revision"))
                .and_then(Value::as_u64)
                .unwrap_or(0)
                .saturating_add(1)
        } else {
            1
        };
        let owner_token = worker_claim_id();
        let awaiting = structured_output && matches!(event, HookEvent::Stop) && !active;
        let phase = if awaiting {
            PHASE_AWAITING_CONTINUATION
        } else if reuse_current && matches!(event, HookEvent::StopFailure | HookEvent::SessionEnd) {
            PHASE_FALLBACK_FINALIZING
        } else {
            PHASE_FINALIZING
        };
        let persisted_structured_output = structured_output || reuse_current;
        let created_at_unix_nanos = current
            .as_ref()
            .filter(|_| reuse_current)
            .and_then(|intent| intent.get("created_at_unix_nanos"))
            .cloned()
            .unwrap_or_else(|| json!(unix_nanos()));
        let persisted_turn_seed = if turn_seed.is_some() {
            json!(turn_seed)
        } else if reuse_current {
            current
                .as_ref()
                .and_then(|intent| intent.get("turn_seed"))
                .cloned()
                .unwrap_or(Value::Null)
        } else {
            Value::Null
        };
        let persisted_transcript_end_offset = if transcript_end_offset.is_some() {
            json!(transcript_end_offset)
        } else if reuse_current {
            current
                .as_ref()
                .and_then(|intent| intent.get("transcript_end_offset"))
                .cloned()
                .unwrap_or(Value::Null)
        } else {
            Value::Null
        };
        let recovery_lease_until_ms = if awaiting {
            Value::Null
        } else {
            json!(now_millis().saturating_add(STOP_RECOVERY_LEASE_MS))
        };
        let status = if awaiting { "pending" } else { "queued" };
        state["pending_final_export"] = json!({
            "intent_id": intent_id.clone(),
            "hook_event_name": hook_event_name(event),
            "transcript_path": transcript_path,
            "created_at_unix_nanos": created_at_unix_nanos,
            "turn_seed": persisted_turn_seed,
            "transcript_end_offset": persisted_transcript_end_offset,
            "status": status,
            "revision": revision,
            "owner_token": owner_token,
            "phase": phase,
            "structured_output": persisted_structured_output,
            "stop_hook_active": active,
            "recovery_lease_until_ms": recovery_lease_until_ms,
            "env": capture_worker_env(),
        });
        state["stop_recovery"] = json!({
            "schema_version": STOP_RECOVERY_STATE_VERSION,
            "intent_id": intent_id.clone(),
            "phase": "pending",
            "progress_fingerprint": stop_recovery_progress_fingerprint(state),
            "no_progress_attempts": 0,
            "next_eligible_at_ms": 0,
            "last_reason": null,
            "updated_at_ms": now_millis(),
        });
        Ok(StopIntentHandoff {
            intent_id: Some(intent_id),
            owner_token: Some(owner_token),
            revision: Some(revision),
            phase,
            structured_output: persisted_structured_output,
            finalizer_authorized: (persisted_structured_output && active)
                || matches!(phase, PHASE_FALLBACK_FINALIZING),
        })
    });
    let handoff = match persisted {
        Ok(handoff) => handoff,
        Err(error) => {
            log_state_error(logger, "stop intent", error);
            return unpersisted_stop_handoff(event, payload, structured_output, logger);
        }
    };
    if let Err(error) = sync_file_and_parent(store.state_file()) {
        logger.warn(&format!("stop intent durability sync failed: {error}"));
    }
    handoff
}

pub fn prepare_stop_worker(payload: &Value, logger: &Logger) -> StopWorkerDecision {
    let phase = payload
        .get(INTERNAL_STOP_PHASE)
        .and_then(Value::as_str)
        .unwrap_or(PHASE_FINALIZING);
    if phase == PHASE_FINALIZER_IN_FLIGHT {
        logger.debug("Stop finalizer already owns the active lease; skipping duplicate worker");
        return StopWorkerDecision::Stale;
    }
    if phase == PHASE_AWAITING_CONTINUATION {
        logger.debug("structured-output Stop is provisional; awaiting continuation");
        return StopWorkerDecision::AwaitingContinuation;
    }

    let structured_output = payload
        .get(INTERNAL_STRUCTURED_OUTPUT)
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let authorized = payload
        .get(crate::hooks::INTERNAL_FINALIZER_AUTHORIZED)
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if structured_output && stop_hook_active(payload) && !authorized {
        logger.warn(
            "active structured-output Stop has no matching provisional intent; preserving legacy skip",
        );
        return StopWorkerDecision::Stale;
    }

    let Some(intent_id) = payload
        .get(INTERNAL_STOP_INTENT_ID)
        .and_then(Value::as_str)
        .map(str::to_string)
    else {
        return missing_stop_owner_decision(structured_output);
    };
    let Some(owner_token) = payload
        .get(INTERNAL_STOP_OWNER_TOKEN)
        .and_then(Value::as_str)
        .map(str::to_string)
    else {
        return missing_stop_owner_decision(structured_output);
    };
    let Some(revision) = payload.get(INTERNAL_STOP_REVISION).and_then(Value::as_u64) else {
        return missing_stop_owner_decision(structured_output);
    };
    let owner = StopIntentOwner {
        intent_id,
        owner_token,
        revision,
    };
    let session_id = extract_session_id(payload);
    let Ok(store) = DomainStateStore::for_session(&session_id, StateDomain::Stop, 50) else {
        return if structured_output {
            StopWorkerDecision::Stale
        } else {
            StopWorkerDecision::Run { owner: Some(owner) }
        };
    };
    match store.read(|state| stop_intent_matches(state, &owner)) {
        Ok(true) => StopWorkerDecision::Run { owner: Some(owner) },
        Ok(false) => {
            if structured_output {
                logger.debug(
                    "structured-output Stop worker ownership is stale; skipping finalization",
                );
                StopWorkerDecision::Stale
            } else {
                // The durable per-session queue preserves ordering. A newer
                // ordinary Stop may replace the single recovery intent before
                // this queued worker runs; the older event must still execute,
                // but its stale owner cannot mutate the newer intent.
                StopWorkerDecision::Run { owner: Some(owner) }
            }
        }
        Err(error) => {
            log_state_error(logger, "stop ownership check", error);
            if structured_output {
                StopWorkerDecision::Stale
            } else {
                StopWorkerDecision::Run { owner: Some(owner) }
            }
        }
    }
}

/// A retry is progress only when durable delivery/parse state advances. Live
/// transcript length and transient error text are deliberately excluded so an
/// active session cannot keep an old failed Stop intent alive forever.
fn stop_recovery_progress_fingerprint(state: &Value) -> String {
    let buffer_len = state
        .get("buffer")
        .and_then(Value::as_str)
        .map(str::len)
        .unwrap_or(0);
    let posted_turn_count = state
        .get("posted_turn_ids")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0);
    let posted_compaction_count = state
        .get("posted_compaction_ids")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or(0);
    serde_json::to_string(&json!({
        "offset": get_int_field(state, "offset"),
        "buffer_len": buffer_len,
        "turn_count": get_int_field(state, "turn_count"),
        "posted_turn_count": posted_turn_count,
        "posted_compaction_count": posted_compaction_count,
        "compaction_count": get_int_field(state, "compaction_count"),
        "last_exported_turn_anchor": state.get("last_exported_turn_anchor"),
        "pending_window_end_offset": state.get("pending_window_end_offset"),
        "scan_offset": state.pointer("/stop_recovery/scan_offset"),
        "scan_record_boundary": state.pointer("/stop_recovery/scan_record_boundary"),
        "turn_cursor": state.pointer("/stop_recovery/turn_cursor"),
        "build_cursor": state.pointer("/stop_recovery/build_cursor"),
    }))
    .expect("Stop recovery progress fingerprint must serialize")
}

fn stop_recovery_backoff_ms(no_progress_attempts: u64) -> u64 {
    STOP_RECOVERY_BACKOFF_BASE_MS
        .saturating_mul(1u64 << no_progress_attempts.min(4))
        .min(STOP_RECOVERY_BACKOFF_MAX_MS)
}

fn record_stop_recovery_deferred_in_state(
    state: &mut Value,
    payload: &Value,
    reason: &str,
    now_ms: u64,
) {
    let intent_id = state
        .pointer("/pending_final_export/intent_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    if intent_id.is_empty() {
        return;
    }
    let fingerprint = stop_recovery_progress_fingerprint(state);
    let recovered = payload
        .get("cchooks_recovered_stop_intent")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let existing = state
        .get("stop_recovery")
        .filter(|record| record.get("intent_id").and_then(Value::as_str) == Some(&intent_id))
        .cloned();
    let no_progress_attempts = if recovered
        && existing
            .as_ref()
            .and_then(|record| record.get("progress_fingerprint"))
            .and_then(Value::as_str)
            == Some(fingerprint.as_str())
    {
        existing
            .as_ref()
            .and_then(|record| record.get("no_progress_attempts"))
            .and_then(Value::as_u64)
            .unwrap_or(0)
            .saturating_add(1)
    } else {
        0
    };
    let waiting_terminal = matches!(
        reason,
        "incomplete_tail" | "incomplete_utf8" | "Stop handler did not reach a terminal outcome"
    );
    let terminal_reason = matches!(
        reason,
        "record_too_large" | "invalid_utf8" | "invalid_jsonl"
    );
    let quarantined =
        terminal_reason || no_progress_attempts >= STOP_RECOVERY_MAX_NO_PROGRESS_ATTEMPTS;
    let phase = if quarantined {
        "terminal_quarantined"
    } else if waiting_terminal {
        "waiting_terminal"
    } else {
        "cooldown"
    };
    let failure_class = if terminal_reason {
        Some(reason)
    } else if quarantined {
        Some("turn_work_unit_no_progress")
    } else {
        None
    };
    let mut recovery = json!({
        "schema_version": STOP_RECOVERY_STATE_VERSION,
        "intent_id": intent_id,
        "phase": phase,
        "failure_class": failure_class,
        "progress_fingerprint": fingerprint,
        "no_progress_attempts": no_progress_attempts,
        "next_eligible_at_ms": if quarantined {
            0
        } else {
            now_ms.saturating_add(stop_recovery_backoff_ms(no_progress_attempts))
        },
        "last_reason": reason,
        "updated_at_ms": now_ms,
    });
    for field in [
        "scan_offset",
        "scan_record_boundary",
        "scan_buffer",
        "pending_messages",
        "turn_cursor",
        "build_cursor",
    ] {
        if let Some(value) = existing.as_ref().and_then(|record| record.get(field)) {
            recovery[field] = value.clone();
        }
    }
    state["stop_recovery"] = recovery;
    if quarantined {
        if let Some(intent) = state
            .get_mut("pending_final_export")
            .and_then(Value::as_object_mut)
        {
            intent.insert("status".to_string(), json!("terminal_quarantined"));
            intent.insert("failure_reason".to_string(), json!(failure_class));
            intent.insert("recovery_lease_until_ms".to_string(), Value::Null);
        }
    }
}

pub fn finish_stop_worker(
    session_id: &str,
    owner: Option<&StopIntentOwner>,
    payload: &Value,
    outcome: &StopOutcome,
    queued_at_ns: u128,
    logger: &Logger,
) {
    let Some(owner) = owner else {
        logger.debug("unowned Stop worker cannot mutate a persisted finalization intent");
        return;
    };
    let store = match DomainStateStore::for_session(session_id, StateDomain::Stop, 50) {
        Ok(store) => store,
        Err(error) => {
            log_state_error(logger, "stop intent cleanup", error);
            return;
        }
    };
    let observed_len = payload
        .get("transcript_path")
        .and_then(Value::as_str)
        .and_then(|path| std::fs::metadata(path).ok())
        .map(|metadata| metadata.len());
    let is_session_end = payload
        .get("hook_event_name")
        .and_then(Value::as_str)
        .is_some_and(|event| event == "SessionEnd");
    if let Err(error) = store.update(|state| {
        if !stop_intent_matches(state, owner) {
            return Ok(());
        }
        let intent_created_at = state
            .get("pending_final_export")
            .and_then(|intent| intent.get("created_at_unix_nanos"))
            .and_then(parse_json_u128)
            .unwrap_or(0);
        // A newer Stop may have replaced the intent while this worker was
        // exporting. Never clear or rewrite that newer revision.
        if intent_created_at > queued_at_ns {
            return Ok(());
        }
        match outcome {
            StopOutcome::Completed | StopOutcome::IntentionallySkipped => {
                if let Some(object) = state.as_object_mut() {
                    object.remove("pending_final_export");
                    object.remove("stop_recovery");
                }
            }
            StopOutcome::Deferred { reason } => {
                let Some(intent) = state
                    .get_mut("pending_final_export")
                    .and_then(Value::as_object_mut)
                else {
                    return Ok(());
                };
                intent.insert("status".to_string(), json!("pending"));
                intent.insert("last_outcome".to_string(), json!(reason));
                intent.insert("last_attempted_at_ms".to_string(), json!(now_millis()));
                intent.insert("recovery_lease_until_ms".to_string(), Value::Null);
                intent.insert(
                    "wait_for_transcript_growth".to_string(),
                    json!(matches!(
                        reason.as_str(),
                        "incomplete_tail" | "incomplete_utf8"
                    )),
                );
                if let Some(observed_len) = observed_len {
                    intent.insert("last_observed_file_len".to_string(), json!(observed_len));
                    if is_session_end {
                        intent.insert(
                            SESSION_END_EXIT_RECOVERY_CHECKED_LEN_FIELD.to_string(),
                            json!(observed_len),
                        );
                    }
                }
                record_stop_recovery_deferred_in_state(state, payload, reason, now_millis());
            }
        }
        Ok(())
    }) {
        log_state_error(logger, "stop intent cleanup", error);
    }
}

#[derive(Debug)]
pub struct RecoveredStop {
    pub payload: Value,
    pub env: BTreeMap<String, String>,
    pub turn_seed: Option<TurnSeed>,
    pub transcript_end_offset: Option<u64>,
    pub queued_at_ns: u128,
}

#[derive(Debug)]
pub enum PendingStopRecovery {
    Idle,
    Waiting(Duration),
    Ready(Box<RecoveredStop>),
}

/// Prepare one pending Stop-family intent for execution by the session
/// worker's maintenance lane. This never spawns a second process.
pub fn scheduled_stop_recovery(
    session_id: &str,
    logger: &Logger,
) -> Result<PendingStopRecovery, String> {
    let store = match DomainStateStore::for_session(session_id, StateDomain::Stop, 50) {
        Ok(store) => store,
        Err(error) => {
            return Err(format!("open stop intent recovery state: {error}"));
        }
    };
    let intent_snapshot = match store.read(|state| state.get("pending_final_export").cloned()) {
        Ok(Some(intent)) => intent,
        Ok(None) => return Ok(PendingStopRecovery::Idle),
        Err(error) => {
            return Err(format!("read stop intent recovery state: {error}"));
        }
    };
    // A provisional structured-output Stop is not a failed export. Only the
    // later active Stop, StopFailure, or SessionEnd may authorize finalization.
    if intent_snapshot.get("phase").and_then(Value::as_str) == Some(PHASE_AWAITING_CONTINUATION) {
        return Ok(PendingStopRecovery::Idle);
    }
    if intent_snapshot.get("status").and_then(Value::as_str) == Some("queued") {
        return Ok(PendingStopRecovery::Idle);
    }
    let recovery_schedule = store
        .read(|state| state.get("stop_recovery").cloned())
        .map_err(|error| format!("read stop recovery schedule: {error}"))?;
    if recovery_schedule
        .as_ref()
        .and_then(|record| record.get("phase"))
        .and_then(Value::as_str)
        == Some("terminal_quarantined")
    {
        return Ok(PendingStopRecovery::Idle);
    }
    if intent_snapshot.get("status").and_then(Value::as_str) == Some("failed_permanent") {
        let intent_id = intent_snapshot
            .get("intent_id")
            .and_then(Value::as_str)
            .unwrap_or("legacy")
            .to_string();
        store
            .update(|state| {
                state["stop_recovery"] = json!({
                    "schema_version": STOP_RECOVERY_STATE_VERSION,
                    "intent_id": intent_id,
                    "phase": "terminal_quarantined",
                    "failure_class": "legacy_failed_permanent",
                    "progress_fingerprint": stop_recovery_progress_fingerprint(state),
                    "no_progress_attempts": STOP_RECOVERY_MAX_NO_PROGRESS_ATTEMPTS,
                    "next_eligible_at_ms": 0,
                    "last_reason": "migrated legacy failed_permanent intent",
                    "updated_at_ms": now_millis(),
                });
                Ok(())
            })
            .map_err(|error| format!("migrate legacy stop recovery: {error}"))?;
        return Ok(PendingStopRecovery::Idle);
    }
    if let Some(next_eligible_at_ms) = recovery_schedule
        .as_ref()
        .filter(|record| {
            record
                .get("phase")
                .and_then(Value::as_str)
                .is_some_and(|phase| matches!(phase, "cooldown" | "waiting_terminal"))
        })
        .and_then(|record| record.get("next_eligible_at_ms"))
        .and_then(Value::as_u64)
    {
        let now_ms = now_millis();
        if next_eligible_at_ms > now_ms {
            return Ok(PendingStopRecovery::Waiting(Duration::from_millis(
                next_eligible_at_ms.saturating_sub(now_ms).min(500),
            )));
        }
    }
    match stop_recovery_liveness(session_id, &intent_snapshot) {
        Ok(StopRecoveryLiveness::ClearAccounted) => {
            if let Err(error) = store.update(|state| {
                if stop_intent_same_revision(state, &intent_snapshot) {
                    if let Some(object) = state.as_object_mut() {
                        object.remove("pending_final_export");
                        object.remove("stop_recovery");
                    }
                }
                Ok(())
            }) {
                log_state_error(logger, "stop intent accounted cleanup", error);
            }
            return Ok(PendingStopRecovery::Idle);
        }
        Ok(StopRecoveryLiveness::Wait) => return Ok(PendingStopRecovery::Idle),
        Ok(StopRecoveryLiveness::Recover) => {}
        Err(error) => {
            logger.warn(&format!("stop intent liveness check failed: {error}"));
        }
    }
    let now_ms = now_millis();
    let expected_intent_id = intent_snapshot.get("intent_id").and_then(Value::as_str);
    let recovery = match claim_pending_stop_intent(&store, now_ms, expected_intent_id) {
        Ok(recovery) => recovery,
        Err(error) => {
            return Err(format!("claim stop intent recovery: {error}"));
        }
    };
    let Some(intent) = recovery else {
        return Ok(PendingStopRecovery::Waiting(Duration::from_millis(500)));
    };
    let Some(hook_event_name) = intent.get("hook_event_name").and_then(Value::as_str) else {
        return Ok(PendingStopRecovery::Idle);
    };
    let structured_output = intent
        .get("structured_output")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let mut payload = json!({
        "hook_event_name": hook_event_name,
        "session_id": session_id,
        "stop_hook_active": false,
        "cchooks_recovered_stop_intent": true,
        INTERNAL_STRUCTURED_OUTPUT: structured_output,
        crate::hooks::INTERNAL_FINALIZER_AUTHORIZED: true,
        INTERNAL_STOP_PHASE: intent
            .get("phase")
            .and_then(Value::as_str)
            .unwrap_or(PHASE_FINALIZING),
    });
    if let Some(intent_id) = intent.get("intent_id").and_then(Value::as_str) {
        payload[INTERNAL_STOP_INTENT_ID] = json!(intent_id);
    }
    if let Some(owner_token) = intent.get("owner_token").and_then(Value::as_str) {
        payload[INTERNAL_STOP_OWNER_TOKEN] = json!(owner_token);
    }
    if let Some(revision) = intent.get("revision").and_then(Value::as_u64) {
        payload[INTERNAL_STOP_REVISION] = json!(revision);
    }
    if let Some(transcript_path) = intent.get("transcript_path").and_then(Value::as_str) {
        payload["transcript_path"] = json!(transcript_path);
    }
    let turn_seed = intent
        .get("turn_seed")
        .cloned()
        .and_then(|value| serde_json::from_value(value).ok());
    let transcript_end_offset = intent.get("transcript_end_offset").and_then(Value::as_u64);
    let env = intent
        .get("env")
        .cloned()
        .and_then(|value| serde_json::from_value(value).ok())
        .unwrap_or_default();
    let queued_at_ns = intent
        .get("created_at_unix_nanos")
        .and_then(parse_json_u128)
        .unwrap_or_else(unix_nanos);
    Ok(PendingStopRecovery::Ready(Box::new(RecoveredStop {
        payload,
        env,
        turn_seed,
        transcript_end_offset,
        queued_at_ns,
    })))
}

/// Legacy/admin recovery entry retained for compatibility. Normal workers use
/// `scheduled_stop_recovery` and execute the returned payload in-process.
#[cfg_attr(not(test), allow(dead_code))]
pub fn recover_pending_stop_intent(session_id: &str, logger: &Logger) {
    let recovery = match scheduled_stop_recovery(session_id, logger) {
        Ok(PendingStopRecovery::Ready(recovery)) => recovery,
        Ok(PendingStopRecovery::Idle | PendingStopRecovery::Waiting(_)) => return,
        Err(error) => {
            logger.warn(&format!("stop intent recovery failed: {error}"));
            return;
        }
    };
    let RecoveredStop {
        payload,
        env,
        turn_seed,
        transcript_end_offset,
        ..
    } = *recovery;
    logger.warn("recovering pending Stop-family export intent in detached worker");
    with_worker_env(&env, || {
        spawn_detached_with_stop_snapshot(&payload, turn_seed, transcript_end_offset, logger);
    });
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum StopRecoveryLiveness {
    Recover,
    Wait,
    ClearAccounted,
}

fn stop_recovery_liveness(
    session_id: &str,
    intent: &Value,
) -> Result<StopRecoveryLiveness, String> {
    let Some(path) = intent.get("transcript_path").and_then(Value::as_str) else {
        return Ok(StopRecoveryLiveness::Recover);
    };
    let transcript_path = Path::new(path);
    let file_len = match std::fs::metadata(transcript_path) {
        Ok(metadata) => metadata.len(),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(StopRecoveryLiveness::Recover)
        }
        Err(error) => return Err(format!("stat Stop transcript '{}': {error}", path)),
    };

    let state_store = SessionStateStore::for_session(session_id, 50)
        .map_err(|error| format!("open Stop state: {error}"))?;
    let state = state_store
        .read(Clone::clone)
        .map_err(|error| format!("read Stop state: {error}"))?;
    let session = get_session_state(&state, session_id);
    if let Some(session) = session {
        if session
            .get("transcript_path")
            .and_then(Value::as_str)
            .is_some_and(|recorded| !recorded.is_empty() && Path::new(recorded) != transcript_path)
        {
            return Ok(StopRecoveryLiveness::Recover);
        }
        let committed_offset = get_int_field(session, "offset").max(0) as u64;
        if file_len < committed_offset {
            return Ok(StopRecoveryLiveness::Recover);
        }
        let has_pending_state = session
            .get("buffer")
            .and_then(Value::as_str)
            .is_some_and(|buffer| !buffer.is_empty())
            || session
                .get("pending_export_reason")
                .is_some_and(|reason| !reason.is_null())
            || session
                .get("pending_window_end_offset")
                .is_some_and(|offset| !offset.is_null());
        if !has_pending_state && file_len <= committed_offset {
            return Ok(StopRecoveryLiveness::ClearAccounted);
        }
    }

    let waits_for_growth = intent
        .get("wait_for_transcript_growth")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let last_observed_len = intent.get("last_observed_file_len").and_then(Value::as_u64);
    if waits_for_growth && last_observed_len.is_some_and(|observed| file_len <= observed) {
        let is_session_end = intent
            .get("hook_event_name")
            .and_then(Value::as_str)
            .is_some_and(|event| event == "SessionEnd");
        let exit_recovery_checked_len = intent
            .get(SESSION_END_EXIT_RECOVERY_CHECKED_LEN_FIELD)
            .and_then(Value::as_u64);
        if is_session_end
            && exit_recovery_checked_len != Some(file_len)
            && transcript_ends_with_session_exit_control(transcript_path)?
        {
            return Ok(StopRecoveryLiveness::Recover);
        }
        return Ok(StopRecoveryLiveness::Wait);
    }
    Ok(StopRecoveryLiveness::Recover)
}

fn parse_json_u128(value: &Value) -> Option<u128> {
    value
        .as_u64()
        .map(u128::from)
        .or_else(|| value.as_str().and_then(|value| value.parse().ok()))
}

fn claim_pending_stop_intent(
    store: &DomainStateStore,
    now_ms: u64,
    expected_intent_id: Option<&str>,
) -> Result<Option<Value>, StateTxnError> {
    store.update(|state| {
        let has_recovery_state = state.get("stop_recovery").is_some();
        let terminal_quarantined = state
            .pointer("/stop_recovery/phase")
            .and_then(Value::as_str)
            == Some("terminal_quarantined");
        let Some(intent) = state
            .get_mut("pending_final_export")
            .and_then(Value::as_object_mut)
        else {
            return Ok(None);
        };
        if intent.get("phase").and_then(Value::as_str) == Some(PHASE_AWAITING_CONTINUATION)
            || intent.get("intent_id").and_then(Value::as_str) != expected_intent_id
            || intent.get("status").and_then(Value::as_str) == Some("queued")
            || terminal_quarantined
        {
            return Ok(None);
        }
        if intent
            .get("status")
            .and_then(Value::as_str)
            .is_some_and(|status| matches!(status, "failed_permanent" | "terminal_quarantined"))
        {
            return Ok(None);
        }
        if intent
            .get("recovery_lease_until_ms")
            .and_then(Value::as_u64)
            .is_some_and(|lease_until_ms| lease_until_ms > now_ms)
        {
            return Ok(None);
        }
        let completed_attempts = intent.get("attempt").and_then(Value::as_u64).unwrap_or(0);
        if !has_recovery_state && completed_attempts >= stop_recovery_max_attempts() {
            intent.insert("status".to_string(), json!("failed_permanent"));
            intent.insert("failed_at_ms".to_string(), json!(now_ms));
            intent.insert(
                "failure_reason".to_string(),
                json!("stop recovery attempt limit exhausted"),
            );
            intent.insert("recovery_lease_until_ms".to_string(), Value::Null);
            return Ok(None);
        }
        let attempt = if has_recovery_state {
            completed_attempts
        } else {
            completed_attempts + 1
        };
        let revision = intent
            .get("revision")
            .and_then(Value::as_u64)
            .unwrap_or(0)
            .saturating_add(1);
        let intent_id = intent
            .get("intent_id")
            .and_then(Value::as_str)
            .map(str::to_string)
            .unwrap_or_else(new_stop_intent_id);
        let owner_token = worker_claim_id();
        intent.insert("status".to_string(), json!("recovering"));
        intent.insert("attempt".to_string(), json!(attempt));
        intent.insert("revision".to_string(), json!(revision));
        intent.insert("intent_id".to_string(), json!(intent_id));
        intent.insert("owner_token".to_string(), json!(owner_token));
        intent
            .entry("phase".to_string())
            .or_insert_with(|| json!(PHASE_FINALIZING));
        intent.insert(
            "recovery_lease_until_ms".to_string(),
            json!(now_ms.saturating_add(STOP_RECOVERY_LEASE_MS)),
        );
        Ok(Some(Value::Object(intent.clone())))
    })
}

fn stop_recovery_max_attempts() -> u64 {
    crate::common::env::var(STOP_RECOVERY_MAX_ATTEMPTS_ENV)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_STOP_RECOVERY_MAX_ATTEMPTS)
}

pub fn drain_tool_events(session_id: &str, logger: &Logger) {
    let Some(session_dir) = crate::common::dirs::session_state_dir(session_id) else {
        return;
    };
    let Ok(entries) = std::fs::read_dir(session_dir) else {
        return;
    };
    let now = now_millis();
    let mut retriggered = 0_usize;
    for entry in entries.flatten() {
        if retriggered >= MAX_STOP_DRAIN_EVENTS {
            break;
        }
        let path = entry.path();
        if !path.is_dir() || !entry.file_name().to_string_lossy().starts_with("tool_") {
            continue;
        }
        let post = read_json_value(&path.join("post.json"));
        let pre = read_json_value(&path.join("pre.json"));
        let tool_id = post
            .as_ref()
            .or(pre.as_ref())
            .and_then(|value| value.get("tool_id"))
            .and_then(Value::as_str);
        let Some(tool_id) = tool_id else {
            continue;
        };
        let Ok(store) = ToolEventStateStore::for_tool(session_id, tool_id, 50) else {
            continue;
        };
        match store.expire_orphan_if_safe(now, TOOL_ORPHAN_TTL_MS) {
            Ok(true) => {
                logger.debug("tool Stop drain removed an expired orphan");
                continue;
            }
            Ok(false) => {}
            Err(error) => {
                logger.warn(&format!("tool Stop drain TTL check failed: {error}"));
                continue;
            }
        }
        match store.pending_post_payload_if_ready(now) {
            Ok(Some(payload)) => {
                spawn_detached(&payload, logger);
                retriggered += 1;
            }
            Ok(None) => {}
            Err(error) => logger.warn(&format!("tool Stop drain reconcile failed: {error}")),
        }
    }
    logger.debug(&format!(
        "tool Stop drain complete: retriggered={retriggered} limit={MAX_STOP_DRAIN_EVENTS}"
    ));
}

pub fn prepare_pre_worker(payload: &Value, logger: &Logger) {
    let Some(store) = tool_store_from_payload(payload, logger) else {
        return;
    };
    match store.record_pre_event(payload, now_millis()) {
        Ok(RecordOutcome::Recorded { event_id }) => {
            logger.debug(&format!("tool pre event recorded: {event_id}"));
        }
        Ok(RecordOutcome::Duplicate { event_id }) => {
            logger.debug(&format!("tool pre event duplicate: {event_id}"));
        }
        Ok(RecordOutcome::AlreadyExported { event_id }) => {
            logger.debug(&format!("tool pre event already exported: {event_id:?}"));
        }
        Err(error) => logger.warn(&format!("tool pre event record failed open: {error}")),
    }
    match store.pending_post_payload_if_ready(now_millis()) {
        Ok(Some(post_payload)) => {
            logger.debug("tool pre event made pending post ready; retriggering post worker");
            retrigger_post_worker(&post_payload, logger);
        }
        Ok(None) => {}
        Err(error) => logger.warn(&format!("tool pre post-retrigger check failed: {error}")),
    }
}

fn retrigger_post_worker(payload: &Value, logger: &Logger) {
    spawn_detached(payload, logger);
}

pub enum PostWorkerDecision {
    Run {
        store: Box<ToolEventStateStore>,
        claim_id: String,
    },
    Skip,
}

pub fn prepare_post_worker(payload: &Value, logger: &Logger) -> PostWorkerDecision {
    let Some(store) = tool_store_from_payload(payload, logger) else {
        return PostWorkerDecision::Skip;
    };
    let now = now_millis();
    match store.record_post_event(payload, now) {
        Ok(RecordOutcome::Recorded { event_id }) => {
            logger.debug(&format!("tool post event recorded: {event_id}"));
        }
        Ok(RecordOutcome::Duplicate { event_id }) => {
            logger.debug(&format!("tool post event duplicate: {event_id}"));
        }
        Ok(RecordOutcome::AlreadyExported { event_id }) => {
            logger.debug(&format!("tool post event already exported: {event_id:?}"));
            return PostWorkerDecision::Skip;
        }
        Err(error) => {
            logger.warn(&format!("tool post event record failed open: {error}"));
            return PostWorkerDecision::Skip;
        }
    }

    match store.reconcile(now_millis()) {
        Ok(ReconcileOutcome::ReadyToClaim { post_event_id }) => {
            logger.debug(&format!("tool post ready to claim: {post_event_id}"));
        }
        Ok(ReconcileOutcome::WaitingForPre { post_event_id }) => {
            logger.debug(&format!("tool post waiting for pre event: {post_event_id}"));
            return PostWorkerDecision::Skip;
        }
        Ok(ReconcileOutcome::Exporting {
            claim_id,
            lease_until_ms,
        }) => {
            logger.debug(&format!(
                "tool post already exporting claim_id={claim_id} lease_until_ms={lease_until_ms}"
            ));
            return PostWorkerDecision::Skip;
        }
        Ok(ReconcileOutcome::AlreadyExported { event_id }) => {
            logger.debug(&format!("tool post already exported: {event_id:?}"));
            return PostWorkerDecision::Skip;
        }
        Ok(ReconcileOutcome::FailedPermanent { event_id }) => {
            logger.debug(&format!("tool post failed permanent: {event_id:?}"));
            return PostWorkerDecision::Skip;
        }
        Ok(ReconcileOutcome::MissingPost) => {
            logger.debug("tool post missing after record; skipping worker handlers");
            return PostWorkerDecision::Skip;
        }
        Err(error) => {
            logger.warn(&format!("tool post reconcile failed open: {error}"));
            return PostWorkerDecision::Skip;
        }
    }

    let claim_id = worker_claim_id();
    match store.claim_post(&claim_id, now_millis(), TOOL_POST_EXPORT_LEASE_MS) {
        Ok(ClaimOutcome::Claimed { attempt, .. }) => {
            logger.debug(&format!("tool post claimed by worker attempt={attempt}"));
            PostWorkerDecision::Run {
                store: Box::new(store),
                claim_id,
            }
        }
        Ok(ClaimOutcome::Leased {
            claim_id,
            lease_until_ms,
        }) => {
            logger.debug(&format!(
                "tool post leased by another worker claim_id={claim_id} lease_until_ms={lease_until_ms}"
            ));
            PostWorkerDecision::Skip
        }
        Ok(ClaimOutcome::AlreadyExported { event_id }) => {
            logger.debug(&format!("tool post already exported: {event_id:?}"));
            PostWorkerDecision::Skip
        }
        Ok(ClaimOutcome::FailedPermanent { event_id }) => {
            logger.debug(&format!("tool post failed permanent: {event_id:?}"));
            PostWorkerDecision::Skip
        }
        Ok(ClaimOutcome::MissingPost) => {
            logger.debug("tool post missing after record; skipping worker handlers");
            PostWorkerDecision::Skip
        }
        Err(error) => {
            logger.warn(&format!("tool post claim failed open: {error}"));
            PostWorkerDecision::Skip
        }
    }
}

pub fn finish_post_worker(
    store: Box<ToolEventStateStore>,
    claim_id: &str,
    success: bool,
    logger: &Logger,
) {
    if !success {
        logger.debug("tool post export failed; leaving export lease for retry");
        return;
    }
    match store.mark_exported(claim_id, now_millis()) {
        Ok(true) => match store.cleanup_if_exported(now_millis()) {
            Ok(outcome) => logger.debug(&format!("tool post exported cleanup: {outcome:?}")),
            Err(error) => logger.warn(&format!("tool post cleanup failed: {error}")),
        },
        Ok(false) => logger.warn("tool post mark_exported skipped: claim no longer owns state"),
        Err(error) => logger.warn(&format!("tool post mark_exported failed: {error}")),
    }
}

fn tool_store_from_payload(payload: &Value, logger: &Logger) -> Option<ToolEventStateStore> {
    let session_id = extract_session_id(payload);
    let tool_id = payload
        .get("tool_use_id")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())?;
    match ToolEventStateStore::for_tool(&session_id, tool_id, 50) {
        Ok(store) => Some(store),
        Err(error) => {
            logger.warn(&format!("tool event store unavailable: {error}"));
            None
        }
    }
}

fn worker_claim_id() -> String {
    format!("worker-{}-{}", std::process::id(), unix_nanos())
}

fn new_stop_intent_id() -> String {
    format!("stop-{}-{}", std::process::id(), unix_nanos())
}

fn stop_hook_active(payload: &Value) -> bool {
    payload
        .get("stop_hook_active")
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

fn unpersisted_stop_handoff(
    event: &HookEvent,
    payload: &Value,
    structured_output: bool,
    logger: &Logger,
) -> StopIntentHandoff {
    if structured_output && matches!(event, HookEvent::Stop) && !stop_hook_active(payload) {
        logger.warn(
            "structured-output Stop coordinator unavailable; using legacy immediate finalization",
        );
    }
    StopIntentHandoff::unpersisted(event, payload, structured_output)
}

fn missing_stop_owner_decision(structured_output: bool) -> StopWorkerDecision {
    if structured_output {
        StopWorkerDecision::Stale
    } else {
        StopWorkerDecision::Run { owner: None }
    }
}

fn stop_intent_matches(state: &Value, owner: &StopIntentOwner) -> bool {
    state.get("pending_final_export").is_some_and(|intent| {
        let phase = intent.get("phase").and_then(Value::as_str);
        intent.get("intent_id").and_then(Value::as_str) == Some(owner.intent_id.as_str())
            && intent.get("owner_token").and_then(Value::as_str) == Some(owner.owner_token.as_str())
            && intent.get("revision").and_then(Value::as_u64) == Some(owner.revision)
            && matches!(phase, Some(PHASE_FINALIZING | PHASE_FALLBACK_FINALIZING))
    })
}

fn stop_intent_same_revision(state: &Value, snapshot: &Value) -> bool {
    let Some(current) = state.get("pending_final_export") else {
        return false;
    };
    match (
        current.get("intent_id").and_then(Value::as_str),
        snapshot.get("intent_id").and_then(Value::as_str),
        current.get("revision").and_then(Value::as_u64),
        snapshot.get("revision").and_then(Value::as_u64),
    ) {
        (Some(current_id), Some(snapshot_id), Some(current_revision), Some(snapshot_revision)) => {
            current_id == snapshot_id && current_revision == snapshot_revision
        }
        _ => current.get("created_at_unix_nanos") == snapshot.get("created_at_unix_nanos"),
    }
}

fn read_json_value(path: &std::path::Path) -> Option<Value> {
    serde_json::from_slice(&std::fs::read(path).ok()?).ok()
}

/// Drain all currently queued items while retaining sole session ownership.
///
/// The empty check and owner release are serialized with queue append. This is
/// the lost-wakeup barrier: an appender either becomes visible to this worker,
/// or observes the released owner lock and starts the next worker.
#[cfg_attr(not(test), allow(dead_code))]
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
            drop(owner.take());
            return Ok(stats);
        }
        let queue_lock = QueueLock::acquire(session_id)?;
        repair_partial_tail_locked(session_id)?;
        match read_next_locked(session_id)? {
            QueueRead::Item(record) => {
                let QueueRecord {
                    item,
                    raw,
                    offset,
                    next_offset,
                } = *record;
                drop(queue_lock);
                let disposition = process(item);
                let queue_lock = QueueLock::acquire(session_id)?;
                match disposition {
                    QueueDisposition::Ack => {
                        clear_retry_state(session_id)?;
                        acknowledge_locked(session_id, next_offset)?;
                        stats.processed = stats.processed.saturating_add(1);
                    }
                    QueueDisposition::Retry(reason) => {
                        let attempts = record_retry_locked(session_id, offset, &reason)?;
                        stats.retried = stats.retried.saturating_add(1);
                        if attempts >= max_delivery_attempts() {
                            append_failed_locked(session_id, offset, attempts, &reason, &raw)?;
                            clear_retry_state(session_id)?;
                            acknowledge_locked(session_id, next_offset)?;
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
                        // Lost-wakeup barrier: recheck FIFO while QueueLock is
                        // held before releasing the sole SessionOwner.
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

#[cfg_attr(not(test), allow(dead_code))]
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

#[cfg(test)]
fn append_queue(session_id: &str, payload: &Value) -> Result<AppendOutcome, AppendFailure> {
    append_queue_with_turn_seed(session_id, payload, None)
}

#[cfg(test)]
fn append_queue_with_turn_seed(
    session_id: &str,
    payload: &Value,
    turn_seed: Option<TurnSeed>,
) -> Result<AppendOutcome, AppendFailure> {
    append_queue_with_stop_snapshot(
        session_id,
        payload,
        turn_seed,
        transcript_end_offset(payload),
        None,
    )
}

fn append_queue_with_stop_snapshot(
    session_id: &str,
    payload: &Value,
    turn_seed: Option<TurnSeed>,
    transcript_end_offset: Option<u64>,
    transcript_ingest_receipt: Option<String>,
) -> Result<AppendOutcome, AppendFailure> {
    let _queue_lock = QueueLock::acquire(session_id).map_err(AppendFailure::Other)?;
    let (length, offset) = repair_partial_tail_locked(session_id).map_err(AppendFailure::Other)?;
    let path = queue_path(session_id).map_err(AppendFailure::Other)?;
    let envelope = QueueEnvelope {
        queued_at_ns: unix_nanos().to_string(),
        env: capture_worker_env(),
        turn_seed: turn_seed.clone(),
        transcript_end_offset,
        transcript_ingest_receipt: transcript_ingest_receipt.clone(),
        payload: payload.clone(),
    };
    let mut record =
        serde_json::to_vec(&envelope).map_err(|error| AppendFailure::Other(error.to_string()))?;
    record.push(b'\n');
    let max_bytes = queue_max_bytes();
    let pending_bytes = length.saturating_sub(offset);
    let record_bytes = record.len() as u64;
    let (pending_items, duplicate) = if pending_bytes == 0 {
        (0, false)
    } else {
        pending_queue_summary_locked(
            &path,
            offset,
            payload,
            turn_seed.as_ref(),
            transcript_end_offset,
            transcript_ingest_receipt.as_deref(),
        )
        .map_err(AppendFailure::Other)?
    };
    if duplicate {
        return Ok(AppendOutcome::Coalesced);
    }
    let max_items = queue_max_items();
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
    path: &std::path::Path,
    offset: u64,
    candidate: &Value,
    candidate_turn_seed: Option<&TurnSeed>,
    candidate_transcript_end_offset: Option<u64>,
    candidate_transcript_ingest_receipt: Option<&str>,
) -> Result<(usize, bool), String> {
    let mut file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok((0, false)),
        Err(error) => return Err(format!("{}: {error}", path.display())),
    };
    file.seek(SeekFrom::Start(offset))
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let candidate_summary = QueueSummaryPayload::from_value(candidate);
    let candidate_key = candidate_summary.coalescing_key();
    let candidate_is_seeded_polling_stop =
        candidate_summary.is_seeded_polling_stop(candidate_turn_seed);
    let mut pending_items = 0usize;
    for line in BufReader::new(file).split(b'\n') {
        let line = line.map_err(|error| format!("{}: {error}", path.display()))?;
        if line.is_empty() {
            continue;
        }
        pending_items = pending_items.saturating_add(1);
        if let Some(candidate_key) = candidate_key.as_ref() {
            // Stop-family snapshots are part of event identity. If one enqueue
            // captured a seed and another did not (or captured a different
            // seed), coalescing could discard the only correctly bound Turn.
            // Prefer a duplicate attempt; posted-turn checkpoints make the
            // downstream export path idempotent.
            // Deserialize only the fields needed for queue accounting. Serde
            // skips arbitrary payload bodies without allocating their nested
            // JSON trees, which keeps duplicate scans bounded for large hooks.
            let matches = serde_json::from_slice::<QueueSummaryEnvelope>(&line)
                .ok()
                .is_some_and(|envelope| {
                    envelope.payload.coalescing_key().as_ref() == Some(candidate_key)
                        && envelope.turn_seed.as_ref() == candidate_turn_seed
                        && (candidate_is_seeded_polling_stop
                            || envelope.transcript_end_offset == candidate_transcript_end_offset)
                        && envelope.transcript_ingest_receipt.as_deref()
                            == candidate_transcript_ingest_receipt
                });
            if matches {
                return Ok((pending_items, true));
            }
        }
    }
    Ok((pending_items, false))
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
    Ok(QueueRead::Item(Box::new(QueueRecord {
        item: QueueItem {
            payload: envelope.payload,
            queued_at_ns,
            env: envelope.env,
            turn_seed: envelope.turn_seed,
            transcript_end_offset: envelope.transcript_end_offset,
            transcript_ingest_receipt: envelope.transcript_ingest_receipt,
        },
        raw: record,
        offset,
        next_offset,
    })))
}

fn compact_empty_queue_locked(session_id: &str) -> Result<(), String> {
    let path = queue_path(session_id)?;
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
        .map_err(|error| format!("{}: {error}", path.display()))?;
    write_offset(session_id, 0)
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
    let length = std::fs::metadata(&path)
        .map_err(|error| format!("{}: {error}", path.display()))?
        .len();
    if offset > length {
        write_offset(session_id, 0)?;
        return Ok(());
    }
    replace_queue_with_unconsumed_suffix_locked(&path, offset)?;
    // Crash-safe order: after replacement, the old offset is beyond the new
    // compacted file and the next reader repairs it to zero. Resetting the
    // offset before replacement would expose the old acknowledged prefix again.
    write_offset(session_id, 0)
}

fn replace_queue_with_unconsumed_suffix_locked(path: &Path, offset: u64) -> Result<(), String> {
    let mut source = File::open(path).map_err(|error| format!("{}: {error}", path.display()))?;
    let length = source
        .metadata()
        .map_err(|error| format!("{}: {error}", path.display()))?
        .len();
    if offset > length {
        return Err(format!(
            "{}: compaction offset {offset} exceeds queue length {length}",
            path.display()
        ));
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
    persist_named_temp(replacement, path)
}

fn record_retry_locked(session_id: &str, offset: u64, reason: &str) -> Result<u32, String> {
    let path = retry_path(session_id)?;
    let previous = std::fs::read(&path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<RetryState>(&bytes).ok())
        .filter(|state| state.offset == offset);
    let attempts = previous
        .map(|state| state.attempts)
        .unwrap_or(0)
        .saturating_add(1);
    let state = RetryState {
        offset,
        attempts,
        last_error: reason.chars().take(2048).collect(),
    };
    write_json_atomically(
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
    let mut record = serde_json::to_vec(&json!({
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
    write_json_atomically(
        &path,
        &serde_json::to_value(state).map_err(|error| error.to_string())?,
    )
}

#[allow(dead_code)]
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
    crate::common::env::var(WORKER_QUEUE_MAX_BYTES_ENV)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_QUEUE_MAX_BYTES)
}

fn queue_max_items() -> usize {
    crate::common::env::var(WORKER_QUEUE_MAX_ITEMS_ENV)
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_QUEUE_MAX_ITEMS)
}

fn worker_max_runtime() -> std::time::Duration {
    std::time::Duration::from_millis(
        crate::common::env::var(WORKER_MAX_RUNTIME_MS_ENV)
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(DEFAULT_WORKER_MAX_RUNTIME_MS),
    )
}

fn max_delivery_attempts() -> u32 {
    crate::common::env::var(WORKER_MAX_DELIVERY_ATTEMPTS_ENV)
        .ok()
        .and_then(|value| value.parse::<u32>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_MAX_DELIVERY_ATTEMPTS)
}

pub fn with_worker_env<R>(env: &BTreeMap<String, String>, run: impl FnOnce() -> R) -> R {
    crate::common::env::with_worker_env(env, run)
}

fn capture_worker_env() -> BTreeMap<String, String> {
    crate::common::env::capture_worker_env()
}

#[cfg(test)]
fn should_capture_worker_env(key: &str) -> bool {
    crate::common::env::should_capture_worker_env(key)
}

/// Return the repaired length and checkpoint under the same queue lock.
fn repair_partial_tail_locked(session_id: &str) -> Result<(u64, u64), String> {
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
            return Ok((0, 0));
        }
        Err(error) => return Err(format!("{}: {error}", path.display())),
    };
    let length = file
        .metadata()
        .map_err(|error| format!("{}: {error}", path.display()))?
        .len();
    let mut offset = read_offset(session_id)?;
    if offset > length {
        write_offset(session_id, 0)?;
        offset = 0;
    }
    if length == 0 {
        return Ok((0, 0));
    }
    file.seek(SeekFrom::End(-1))
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let mut last = [0u8; 1];
    file.read_exact(&mut last)
        .map_err(|error| format!("{}: {error}", path.display()))?;
    if last[0] == b'\n' {
        return Ok((length, offset));
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
    if offset > repaired_length {
        write_offset(session_id, repaired_length)?;
        offset = repaired_length;
    }
    file.set_len(repaired_length)
        .and_then(|_| file.sync_data())
        .map_err(|error| format!("{}: {error}", path.display()))?;
    Ok((repaired_length, offset))
}

fn read_offset(session_id: &str) -> Result<u64, String> {
    read_offset_file(&offset_path(session_id)?)
}

fn read_offset_file(path: &Path) -> Result<u64, String> {
    match std::fs::read(path) {
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

    fn try_acquire(session_id: &str) -> Result<Option<Self>, String> {
        let path = dirs::session_worker_queue_lock_file(session_id)
            .ok_or_else(|| "missing session id".to_string())?;
        Ok(
            crate::common::file_lock::ExclusiveFileLock::try_acquire(&path)?
                .map(|lock| Self { _lock: lock }),
        )
    }
}

fn write_json_atomically(path: &Path, value: &Value) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("{} has no parent", path.display()))?;
    dirs::ensure_session_state_parent(path).map_err(|error| error.to_string())?;
    let mut replacement =
        tempfile::NamedTempFile::new_in(parent).map_err(|error| error.to_string())?;
    serde_json::to_writer(replacement.as_file_mut(), value).map_err(|error| error.to_string())?;
    replacement
        .flush()
        .and_then(|_| replacement.as_file().sync_data())
        .map_err(|error| format!("{}: {error}", path.display()))?;
    persist_named_temp(replacement, path)
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
    dirs::ensure_session_state_parent(path).map_err(|error| error.to_string())?;
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

pub fn current_worker_id() -> String {
    format!("worker-{}", std::process::id())
}

fn hook_event_name(event: &HookEvent) -> &'static str {
    match event {
        HookEvent::Stop => "Stop",
        HookEvent::StopFailure => "StopFailure",
        HookEvent::SessionEnd => "SessionEnd",
        _ => "Unknown",
    }
}

fn unix_nanos() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default()
}

fn sync_file_and_parent(path: &std::path::Path) -> Result<(), String> {
    std::fs::File::open(path)
        .and_then(|file| file.sync_all())
        .map_err(|error| format!("{}: {error}", path.display()))?;
    #[cfg(unix)]
    if let Some(parent) = path.parent() {
        std::fs::File::open(parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|error| format!("{}: {error}", parent.display()))?;
    }
    Ok(())
}

fn log_state_error(logger: &Logger, operation: &str, error: StateTxnError) {
    match error {
        StateTxnError::Busy { .. } => logger.warn(&format!("{operation}: state lock unavailable")),
        other => logger.warn(&format!("{operation}: {other}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hooks::tool_event::ReconcileOutcome;
    use serde_json::json;

    fn logger(dir: &tempfile::TempDir) -> Logger {
        Logger::new(dir.path().join("worker.log"), true)
    }

    fn write_owned_stop(
        event: &HookEvent,
        payload: &mut Value,
        structured_output: bool,
        logger: &Logger,
    ) -> StopIntentOwner {
        let handoff = write_stop_intent(event, payload, structured_output, logger);
        handoff.apply_to_payload(payload);
        match prepare_stop_worker(payload, logger) {
            StopWorkerDecision::Run { owner: Some(owner) } => owner,
            _ => panic!("persisted Stop should have an owner"),
        }
    }

    fn set_state_dir(path: &std::path::Path) -> Option<String> {
        let previous = crate::common::env::var("CCHOOKS_STATE_DIR").ok();
        std::env::set_var("CCHOOKS_STATE_DIR", path);
        previous
    }

    fn restore_state_dir(previous: Option<String>) {
        match previous {
            Some(value) => std::env::set_var("CCHOOKS_STATE_DIR", value),
            None => std::env::remove_var("CCHOOKS_STATE_DIR"),
        }
    }

    fn set_env(key: &str, value: &str) -> Option<String> {
        let previous = crate::common::env::var(key).ok();
        std::env::set_var(key, value);
        previous
    }

    fn restore_env(key: &str, previous: Option<String>) {
        match previous {
            Some(value) => std::env::set_var(key, value),
            None => std::env::remove_var(key),
        }
    }

    #[test]
    fn transcript_ingest_receipt_acknowledges_local_read_and_cleans_up() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-transcript-ingest-ack";
        let receipt = prepare_transcript_ingest_receipt(session_id).unwrap();

        acknowledge_transcript_ingest(session_id, Some(&receipt)).unwrap();
        let outcome =
            wait_for_transcript_ingest(session_id, &receipt, std::time::Duration::from_millis(100));

        assert_eq!(outcome, TranscriptIngestWaitOutcome::Acked);
        assert!(
            !transcript_ingest_receipt_path(session_id, &receipt, "request")
                .unwrap()
                .exists()
        );
        assert!(!transcript_ingest_receipt_path(session_id, &receipt, "ack")
            .unwrap()
            .exists());
        restore_state_dir(previous_state);
    }

    #[test]
    fn transcript_ingest_timeout_is_fail_open_and_rejects_late_ack() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-transcript-ingest-timeout";
        let receipt = prepare_transcript_ingest_receipt(session_id).unwrap();

        let outcome = wait_for_transcript_ingest(session_id, &receipt, Duration::ZERO);
        acknowledge_transcript_ingest(session_id, Some(&receipt)).unwrap();

        assert_eq!(outcome, TranscriptIngestWaitOutcome::TimedOut);
        assert!(!transcript_ingest_receipt_path(session_id, &receipt, "ack")
            .unwrap()
            .exists());
        restore_state_dir(previous_state);
    }

    fn tool_payload(event: &str, session_id: &str, tool_id: &str) -> Value {
        json!({
            "hook_event_name": event,
            "session_id": session_id,
            "tool_use_id": tool_id,
            "tool_name": "Read",
            "tool_input": {"file_path": "Cargo.toml"},
            "tool_response": {"content": "ok"}
        })
    }

    fn test_turn_seed(turn_id: &str, trace_id: &str) -> TurnSeed {
        TurnSeed {
            schema_version: crate::hooks::turn_seed::TURN_SEED_SCHEMA_VERSION,
            turn_id: turn_id.to_string(),
            trace_id: trace_id.to_string(),
            upstream_traceparent: Some(format!("00-{trace_id}-61c42769ba435f72-01")),
            prompt_timestamp: Some("2026-08-24T00:00:00Z".to_string()),
            prompt_digest: Some(crate::hooks::turn_seed::prompt_digest("hello")),
            transcript_boundary: None,
            created_at: "2026-08-24T00:00:00Z".to_string(),
        }
    }

    #[test]
    fn legacy_queue_envelope_without_turn_seed_remains_readable() {
        let envelope: QueueEnvelope = serde_json::from_value(json!({
            "queued_at_ns": "1",
            "env": {},
            "payload": {"hook_event_name": "Stop", "session_id": "session"}
        }))
        .unwrap();
        assert_eq!(envelope.turn_seed, None);
        assert_eq!(envelope.transcript_end_offset, None);
    }

    #[test]
    fn queue_item_preserves_stop_turn_seed_snapshot() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-queue-turn-seed";
        let seed = test_turn_seed("turn-1", "41049c989ea3510bafe6d446672d7b1c");
        let transcript = dir.path().join("session.jsonl");
        std::fs::write(&transcript, b"snapshot").unwrap();
        let payload = json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript
        });
        append_queue_with_turn_seed(session_id, &payload, Some(seed.clone())).unwrap();
        let owner =
            SessionOwner::acquire(session_id, std::time::Duration::from_millis(100)).unwrap();
        let mut observed = None;
        drain_queue(session_id, Some(owner), |item| {
            observed = Some((item.turn_seed, item.transcript_end_offset));
            QueueDisposition::Ack
        })
        .unwrap();
        assert_eq!(observed, Some((Some(seed), Some(8))));
        restore_state_dir(previous_state);
    }

    #[test]
    fn stop_intent_v3_preserves_snapshot_after_current_seed_changes() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-intent-turn-seed";
        let original = test_turn_seed("turn-original", "41049c989ea3510bafe6d446672d7b1c");
        let payload = json!({"hook_event_name": "Stop", "session_id": session_id});
        write_stop_intent_with_turn_seed(
            &HookEvent::Stop,
            &payload,
            false,
            Some(&original),
            Some(123),
            &logger,
        );

        let turn_store = DomainStateStore::for_session(session_id, StateDomain::Turn, 100).unwrap();
        let replacement = test_turn_seed("turn-new", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        turn_store
            .update(|state| {
                *state = serde_json::to_value(replacement).unwrap();
                Ok(())
            })
            .unwrap();

        let stop_store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        let state = stop_store.read(Clone::clone).unwrap();
        assert_eq!(state["schema_version"], 3);
        let recovered: TurnSeed =
            serde_json::from_value(state["pending_final_export"]["turn_seed"].clone()).unwrap();
        assert_eq!(recovered, original);
        assert_eq!(state["pending_final_export"]["transcript_end_offset"], 123);
        restore_state_dir(previous_state);
    }

    #[test]
    fn stop_intent_recovery_enqueues_the_saved_seed_snapshot() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let previous_failure = set_env("CCHOOKS_TEST_WORKER_SPAWN_FAILURE", "true");
        let logger = logger(&dir);
        let session_id = "session-intent-recovery-turn-seed";
        let original = test_turn_seed("turn-original", "41049c989ea3510bafe6d446672d7b1c");
        let mut payload = json!({"hook_event_name": "Stop", "session_id": session_id});
        let handoff = write_stop_intent_with_turn_seed(
            &HookEvent::Stop,
            &payload,
            false,
            Some(&original),
            Some(456),
            &logger,
        );
        handoff.apply_to_payload(&mut payload);
        let owner = match prepare_stop_worker(&payload, &logger) {
            StopWorkerDecision::Run { owner } => owner,
            _ => panic!("persisted Stop intent should be owned by its queued payload"),
        };
        finish_stop_worker(
            session_id,
            owner.as_ref(),
            &payload,
            &StopOutcome::deferred("hook_budget_exhausted"),
            u128::MAX,
            &logger,
        );
        let stop_store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        stop_store
            .update(|state| {
                state["stop_recovery"]["next_eligible_at_ms"] = json!(0);
                Ok(())
            })
            .unwrap();

        recover_pending_stop_intent(session_id, &logger);

        let queue = std::fs::read_to_string(queue_path(session_id).unwrap()).unwrap();
        let envelope: QueueEnvelope = serde_json::from_str(queue.lines().next().unwrap()).unwrap();
        assert_eq!(envelope.turn_seed, Some(original));
        assert_eq!(envelope.transcript_end_offset, Some(456));
        assert_eq!(envelope.payload["cchooks_recovered_stop_intent"], true);

        restore_env("CCHOOKS_TEST_WORKER_SPAWN_FAILURE", previous_failure);
        restore_state_dir(previous_state);
    }

    #[test]
    fn post_before_pre_waits_then_pre_retriggers_and_allows_claim() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-worker-reorder";
        let tool_id = "tool-worker-reorder";
        let post = tool_payload("PostToolUse", session_id, tool_id);
        let pre = tool_payload("PreToolUse", session_id, tool_id);

        assert!(matches!(
            prepare_post_worker(&post, &logger),
            PostWorkerDecision::Skip
        ));
        let store = ToolEventStateStore::for_tool(session_id, tool_id, 100).unwrap();
        assert!(matches!(
            store.reconcile(now_millis()).unwrap(),
            ReconcileOutcome::WaitingForPre { .. }
        ));

        let owner = SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))
            .expect("hold worker owner so retrigger only appends");
        prepare_pre_worker(&pre, &logger);
        assert_eq!(pending_count(session_id).unwrap(), 1);
        let mut queued = Vec::new();
        drain_queue(session_id, Some(owner), |item| {
            queued.push(item.payload);
            QueueDisposition::Ack
        })
        .unwrap();
        assert_eq!(queued, vec![post.clone()]);

        assert!(matches!(
            prepare_post_worker(&post, &logger),
            PostWorkerDecision::Run { .. }
        ));
        restore_state_dir(previous_state);
    }

    #[test]
    fn active_post_lease_skips_duplicate_worker_claims() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-worker-lease";
        let tool_id = "tool-worker-lease";
        let pre = tool_payload("PreToolUse", session_id, tool_id);
        let post = tool_payload("PostToolUse", session_id, tool_id);

        prepare_pre_worker(&pre, &logger);
        let first = prepare_post_worker(&post, &logger);
        assert!(matches!(first, PostWorkerDecision::Run { .. }));
        let second = prepare_post_worker(&post, &logger);
        assert!(matches!(second, PostWorkerDecision::Skip));

        let store = ToolEventStateStore::for_tool(session_id, tool_id, 100).unwrap();
        match store.reconcile(now_millis()).unwrap() {
            ReconcileOutcome::Exporting { lease_until_ms, .. } => {
                assert!(lease_until_ms > now_millis() + 100_000);
            }
            other => panic!("expected active export lease, got {other:?}"),
        }
        restore_state_dir(previous_state);
    }

    #[test]
    fn pending_stop_recovery_has_single_owner_until_lease_expires() {
        let dir = tempfile::tempdir().unwrap();
        let store = DomainStateStore::new(
            dir.path().join("state_stop.json"),
            None,
            "session-stop-recovery",
            StateDomain::Stop,
            100,
        );
        store
            .update(|state| {
                state["pending_final_export"] = json!({
                    "hook_event_name": "Stop",
                    "transcript_path": "/tmp/session.jsonl",
                    "status": "pending"
                });
                Ok(())
            })
            .unwrap();
        let first = claim_pending_stop_intent(&store, 100, None)
            .unwrap()
            .unwrap();
        assert_eq!(first["attempt"], 1);
        let intent_id = first["intent_id"].as_str().unwrap();
        assert!(claim_pending_stop_intent(&store, 101, Some(intent_id))
            .unwrap()
            .is_none());
        let second = claim_pending_stop_intent(&store, 120_101, Some(intent_id))
            .unwrap()
            .unwrap();
        assert_eq!(second["attempt"], 2);
    }

    #[test]
    fn pending_stop_recovery_reaches_configured_terminal_ceiling() {
        let _guard = crate::test_sync::env_lock();
        let previous_limit = set_env(STOP_RECOVERY_MAX_ATTEMPTS_ENV, "2");
        let dir = tempfile::tempdir().unwrap();
        let store = DomainStateStore::new(
            dir.path().join("state_stop.json"),
            None,
            "session-stop-recovery-limit",
            StateDomain::Stop,
            100,
        );
        store
            .update(|state| {
                state["pending_final_export"] = json!({
                    "hook_event_name": "Stop",
                    "status": "pending"
                });
                Ok(())
            })
            .unwrap();

        let first = claim_pending_stop_intent(&store, 100, None)
            .unwrap()
            .unwrap();
        assert_eq!(first["attempt"], 1);
        let intent_id = first["intent_id"].as_str().unwrap();
        assert_eq!(
            claim_pending_stop_intent(&store, 120_101, Some(intent_id))
                .unwrap()
                .unwrap()["attempt"],
            2
        );
        assert!(claim_pending_stop_intent(&store, 240_102, Some(intent_id))
            .unwrap()
            .is_none());
        let state = store.read(Clone::clone).unwrap();
        let intent = &state["pending_final_export"];
        assert_eq!(intent["status"], "failed_permanent");
        assert_eq!(
            intent["failure_reason"],
            "stop recovery attempt limit exhausted"
        );
        assert!(claim_pending_stop_intent(&store, 999_999, Some(intent_id))
            .unwrap()
            .is_none());
        restore_env(STOP_RECOVERY_MAX_ATTEMPTS_ENV, previous_limit);
    }

    #[test]
    fn deferred_stop_waits_for_growth_and_completed_stop_clears_intent() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-stop-growth";
        let transcript = dir.path().join("session.jsonl");
        std::fs::write(&transcript, b"{\"type\":\"user\"}\n").unwrap();
        let mut payload = json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript.to_string_lossy()
        });
        let owner = write_owned_stop(&HookEvent::Stop, &mut payload, false, &logger);
        finish_stop_worker(
            session_id,
            Some(&owner),
            &payload,
            &StopOutcome::deferred("incomplete_tail"),
            u128::MAX,
            &logger,
        );

        let stop_store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        let intent = stop_store
            .read(|state| state["pending_final_export"].clone())
            .unwrap();
        assert_eq!(intent["wait_for_transcript_growth"], true);
        assert_eq!(
            stop_recovery_liveness(session_id, &intent).unwrap(),
            StopRecoveryLiveness::Wait
        );

        use std::io::Write;
        std::fs::OpenOptions::new()
            .append(true)
            .open(&transcript)
            .unwrap()
            .write_all(b"{\"type\":\"assistant\"}\n")
            .unwrap();
        assert_eq!(
            stop_recovery_liveness(session_id, &intent).unwrap(),
            StopRecoveryLiveness::Recover
        );

        finish_stop_worker(
            session_id,
            Some(&owner),
            &payload,
            &StopOutcome::Completed,
            u128::MAX,
            &logger,
        );
        assert!(stop_store
            .read(|state| state.get("pending_final_export").cloned())
            .unwrap()
            .is_none());
        restore_state_dir(previous_state);
    }

    #[test]
    fn legacy_session_end_exit_intent_recovers_once_then_waits_if_still_incomplete() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-end-exit-recovery";
        let transcript = dir.path().join("session.jsonl");
        let records = [
            json!({"type":"user","message":{"role":"user","content":"<local-command-caveat>Caveat: local command records follow.</local-command-caveat>"},"isMeta":true}),
            json!({"type":"user","message":{"role":"user","content":"<command-name>/exit</command-name>\n<command-message>exit</command-message>\n<command-args></command-args>"}}),
            json!({"type":"user","message":{"role":"user","content":"<local-command-stdout>Bye!</local-command-stdout>"}}),
        ];
        let content = records
            .iter()
            .map(Value::to_string)
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        std::fs::write(&transcript, content).unwrap();
        let file_len = std::fs::metadata(&transcript).unwrap().len();
        let mut payload = json!({
            "hook_event_name": "SessionEnd",
            "session_id": session_id,
            "transcript_path": transcript.to_string_lossy()
        });
        let owner = write_owned_stop(&HookEvent::SessionEnd, &mut payload, false, &logger);

        let stop_store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        stop_store
            .update(|state| {
                let intent = state["pending_final_export"].as_object_mut().unwrap();
                intent.insert("wait_for_transcript_growth".to_string(), json!(true));
                intent.insert("last_observed_file_len".to_string(), json!(file_len));
                Ok(())
            })
            .unwrap();
        let legacy_intent = stop_store
            .read(|state| state["pending_final_export"].clone())
            .unwrap();
        assert_eq!(
            stop_recovery_liveness(session_id, &legacy_intent).unwrap(),
            StopRecoveryLiveness::Recover
        );

        finish_stop_worker(
            session_id,
            Some(&owner),
            &payload,
            &StopOutcome::deferred("incomplete_tail"),
            u128::MAX,
            &logger,
        );
        let checked_intent = stop_store
            .read(|state| state["pending_final_export"].clone())
            .unwrap();
        assert_eq!(
            checked_intent[SESSION_END_EXIT_RECOVERY_CHECKED_LEN_FIELD],
            file_len
        );
        assert_eq!(
            stop_recovery_liveness(session_id, &checked_intent).unwrap(),
            StopRecoveryLiveness::Wait
        );

        finish_stop_worker(
            session_id,
            Some(&owner),
            &payload,
            &StopOutcome::Completed,
            u128::MAX,
            &logger,
        );
        assert!(stop_store
            .read(|state| state.get("pending_final_export").cloned())
            .unwrap()
            .is_none());
        restore_state_dir(previous_state);
    }

    #[test]
    fn accounted_stop_intent_is_cleared_without_replaying_transcript() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-stop-accounted";
        let transcript = dir.path().join("session.jsonl");
        std::fs::write(&transcript, b"{\"type\":\"user\"}\n").unwrap();
        let file_len = std::fs::metadata(&transcript).unwrap().len();

        let state_store = SessionStateStore::for_session(session_id, 100).unwrap();
        state_store
            .update(|state| {
                state[session_id] = json!({
                    "offset": file_len,
                    "buffer": "",
                    "posted_turn_ids": ["already-accounted-turn"]
                });
                Ok(())
            })
            .unwrap();
        let intent = json!({
            "hook_event_name": "Stop",
            "transcript_path": transcript.to_string_lossy(),
            "status": "pending"
        });
        assert_eq!(
            stop_recovery_liveness(session_id, &intent).unwrap(),
            StopRecoveryLiveness::ClearAccounted
        );
        restore_state_dir(previous_state);
    }

    #[cfg(unix)]
    #[test]
    fn stop_intent_waits_through_short_lock_contention_and_persists() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-stop-intent-contention";
        let store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        let held_lock = crate::hooks::langfuse::state::StateLock::acquire(store.state_file(), 100);
        assert!(held_lock.is_acquired());
        let release = std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(100));
            drop(held_lock);
        });

        write_stop_intent(
            &HookEvent::Stop,
            &json!({
                "hook_event_name": "Stop",
                "session_id": session_id,
                "transcript_path": "/tmp/session.jsonl"
            }),
            false,
            &logger,
        );
        release.join().unwrap();

        let intent = store
            .read(|state| state.get("pending_final_export").cloned())
            .unwrap()
            .expect("durable stop intent after transient lock contention");
        assert_eq!(intent["hook_event_name"], "Stop");
        assert_eq!(intent["status"], "queued");
        restore_state_dir(previous_state);
    }

    #[test]
    fn concurrent_same_session_enqueue_is_complete_and_coalesces_worker() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-concurrent-queue";
        let owner = SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))
            .expect("hold owner to prevent worker spawn during append storm");
        let logger = std::sync::Arc::new(logger);
        let handles = (0..16)
            .map(|index| {
                let logger = logger.clone();
                std::thread::spawn(move || {
                    let payload = json!({
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": session_id,
                        "uuid": format!("event-{index}")
                    });
                    enqueue_and_wake(session_id, &payload, payload.to_string().len(), &logger)
                        .expect("enqueue")
                })
            })
            .collect::<Vec<_>>();
        let outcomes = handles
            .into_iter()
            .map(|handle| handle.join().unwrap())
            .collect::<Vec<_>>();
        assert!(outcomes
            .iter()
            .all(|outcome| *outcome == EnqueueOutcome::Appended));
        assert_eq!(pending_count(session_id).unwrap(), 16);

        let mut seen = Vec::new();
        drain_queue(session_id, Some(owner), |item| {
            seen.push(
                item.payload
                    .get("uuid")
                    .and_then(Value::as_str)
                    .unwrap()
                    .to_string(),
            );
            QueueDisposition::Ack
        })
        .unwrap();
        seen.sort();
        assert_eq!(seen.len(), 16);
        assert_eq!(pending_count(session_id).unwrap(), 0);
        restore_state_dir(previous_state);
    }

    #[test]
    fn session_idle_wait_rewakes_after_worker_owner_disappears() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-idle-rewake";
        append_queue(
            session_id,
            &json!({
                "hook_event_name": "SessionEnd",
                "session_id": session_id,
                "uuid": "event-session-end"
            }),
        )
        .unwrap();
        let (owner_ready_tx, owner_ready_rx) = std::sync::mpsc::sync_channel(0);
        let (owner_observed_tx, owner_observed_rx) = std::sync::mpsc::sync_channel(0);
        let owner_thread = std::thread::spawn(move || {
            let owner = SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))
                .expect("simulate active worker ownership");
            owner_ready_tx.send(()).unwrap();
            owner_observed_rx.recv().unwrap();
            std::thread::sleep(std::time::Duration::from_millis(40));
            drop(owner);
        });
        owner_ready_rx.recv().unwrap();
        assert!(SessionOwner::try_acquire(session_id).unwrap().is_none());
        owner_observed_tx.send(()).unwrap();

        let mut wake_count = 0usize;
        let outcome = wait_for_session_idle_with_wake(
            session_id,
            std::time::Duration::from_secs(1),
            &logger,
            |session_id, _logger| {
                wake_count = wake_count.saturating_add(1);
                let owner =
                    SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))?;
                drain_queue(session_id, Some(owner), |_item| QueueDisposition::Ack)?;
                Ok(true)
            },
        );

        owner_thread.join().unwrap();
        assert_eq!(outcome, SessionIdleWaitOutcome::Drained);
        assert_eq!(wake_count, 1);
        assert_eq!(pending_count(session_id).unwrap(), 0);
        restore_state_dir(previous_state);
    }

    #[test]
    fn session_idle_wait_timeout_preserves_queue_and_stop_intent() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-idle-timeout";
        let mut payload = json!({
            "hook_event_name": "SessionEnd",
            "session_id": session_id,
            "transcript_path": dir.path().join("session.jsonl")
        });
        let handoff = write_stop_intent(&HookEvent::SessionEnd, &payload, true, &logger);
        handoff.apply_to_payload(&mut payload);
        append_queue(session_id, &payload).unwrap();
        let owner = SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))
            .expect("simulate worker that exceeds the barrier budget");

        let outcome = wait_for_session_idle_with_wake(
            session_id,
            std::time::Duration::from_millis(40),
            &logger,
            |_session_id, _logger| panic!("owned queue must not be rewoken"),
        );

        assert_eq!(outcome, SessionIdleWaitOutcome::TimedOut);
        assert_eq!(pending_count(session_id).unwrap(), 1);
        let store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        assert!(store
            .read(|state| state
                .get("pending_final_export")
                .is_some_and(Value::is_object))
            .unwrap());
        drop(owner);
        restore_state_dir(previous_state);
    }

    #[test]
    fn session_idle_wait_wake_failure_preserves_pending_queue() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-idle-wake-failed";
        append_queue(
            session_id,
            &json!({
                "hook_event_name": "SessionEnd",
                "session_id": session_id,
                "uuid": "event-session-end"
            }),
        )
        .unwrap();

        let outcome = wait_for_session_idle_with_wake(
            session_id,
            std::time::Duration::from_secs(1),
            &logger,
            |_session_id, _logger| Err("injected wake failure".to_string()),
        );

        assert_eq!(
            outcome,
            SessionIdleWaitOutcome::WakeFailed("injected wake failure".to_string())
        );
        assert_eq!(pending_count(session_id).unwrap(), 1);
        restore_state_dir(previous_state);
    }

    #[test]
    fn worker_status_summarizes_queue_owner_and_identity() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-worker-status";
        let owner = SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))
            .expect("hold worker owner");
        append_queue(
            session_id,
            &json!({
                "hook_event_name": "UserPromptSubmit",
                "session_id": session_id,
                "uuid": "event-status"
            }),
        )
        .unwrap();
        let status = worker_status(None).unwrap();
        assert_eq!(status.sessions, 1);
        assert_eq!(status.pending_queue_items, 1);
        assert_eq!(status.workers_active, 1);
        assert_eq!(status.worker_spawn_failures, 0);

        drop(owner);
        let filtered = worker_status(Some(session_id)).unwrap();
        assert_eq!(filtered.sessions, 1);
        assert_eq!(filtered.pending_queue_items, 1);
        assert_eq!(filtered.workers_active, 0);
        restore_state_dir(previous_state);
    }

    #[test]
    fn worker_status_keeps_queue_visible_with_an_incomplete_offset() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-worker-status-incomplete-offset";
        append_queue(
            session_id,
            &json!({
                "hook_event_name": "UserPromptSubmit",
                "session_id": session_id,
                "uuid": "event-status-incomplete-offset"
            }),
        )
        .unwrap();

        let _queue_lock = QueueLock::acquire(session_id).unwrap();
        let offset = std::fs::metadata(queue_path(session_id).unwrap())
            .unwrap()
            .len();
        write_offset(session_id, offset).unwrap();
        assert_eq!(
            worker_status(Some(session_id)).unwrap().pending_queue_items,
            0
        );

        // A diagnostic read can arrive between truncating and writing the offset.
        let path = offset_path(session_id).unwrap();
        std::fs::write(&path, []).unwrap();
        assert_eq!(
            worker_status(Some(session_id)).unwrap().pending_queue_items,
            1
        );
        assert!(std::fs::read(&path).unwrap().is_empty());
        restore_state_dir(previous_state);
    }

    #[test]
    fn worker_status_reports_legacy_pending_stop_without_identity_artifacts() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-worker-status-legacy";
        let path = dirs::session_state_file(session_id).unwrap();
        crate::common::dirs::ensure_private_dir(path.parent().unwrap()).unwrap();
        std::fs::write(
            &path,
            serde_json::to_vec(&json!({
                session_id: {
                    "pending_final_export": {
                        "hook_event_name": "Stop",
                        "status": "pending"
                    }
                }
            }))
            .unwrap(),
        )
        .unwrap();

        let status = worker_status(Some(session_id)).unwrap();
        assert_eq!(status.sessions, 1);
        assert_eq!(status.pending_stops, 1);
        assert_eq!(status.pending_queue_items, 0);
        restore_state_dir(previous_state);
    }

    #[test]
    fn worker_status_classifies_dead_start_marker_as_failure() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-worker-status-dead-start";
        let path = dirs::session_worker_start_file(session_id).unwrap();
        write_start_marker_atomically(
            &path,
            &serde_json::to_value(WorkerStartMarker {
                schema_version: 1,
                token: "dead-start".to_string(),
                created_at_ns: unix_nanos().to_string(),
                expires_at_ns: u128::MAX.to_string(),
                status: "starting".to_string(),
                last_error: None,
                pid: Some(u32::MAX),
            })
            .unwrap(),
        )
        .unwrap();

        let status = worker_status(Some(session_id)).unwrap();
        assert_eq!(status.sessions, 1);
        assert_eq!(status.worker_starting, 0);
        assert_eq!(status.worker_spawn_failures, 1);
        restore_state_dir(previous_state);
    }

    #[test]
    fn missing_queue_is_created_only_by_the_durable_append_path() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-first-durable-append";
        let path = queue_path(session_id).unwrap();
        let queue_lock = QueueLock::acquire(session_id).unwrap();

        write_offset(session_id, 17).unwrap();
        assert_eq!(repair_partial_tail_locked(session_id).unwrap(), (0, 0));
        assert!(!path.exists());
        assert_eq!(read_offset(session_id).unwrap(), 0);
        drop(queue_lock);

        assert!(matches!(
            append_queue(
                session_id,
                &json!({
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
        restore_state_dir(previous_state);
    }

    #[test]
    fn offset_write_truncates_old_content_and_recovers_invalid_content() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-offset-in-place";
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
        restore_state_dir(previous_state);
    }

    #[cfg(windows)]
    #[test]
    fn offset_write_does_not_require_delete_sharing() {
        use std::os::windows::fs::OpenOptionsExt;

        const FILE_SHARE_READ: u32 = 0x0000_0001;
        const FILE_SHARE_WRITE: u32 = 0x0000_0002;

        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-offset-delete-sharing";
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
        restore_state_dir(previous_state);
    }

    #[cfg(windows)]
    #[test]
    fn offset_write_retries_transient_sharing_violation() {
        use std::os::windows::fs::OpenOptionsExt;

        const FILE_SHARE_READ: u32 = 0x0000_0001;

        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-offset-sharing-retry";
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
        restore_state_dir(previous_state);
    }

    #[cfg(windows)]
    #[test]
    fn offset_write_reports_persistent_sharing_violation_after_bounded_retry() {
        use std::os::windows::fs::OpenOptionsExt;

        const FILE_SHARE_READ: u32 = 0x0000_0001;

        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-offset-sharing-timeout";
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
        restore_state_dir(previous_state);
    }

    #[test]
    fn queue_lock_timeout_rejects_new_record_without_corrupting_existing_queue() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-queue-lock-timeout";
        append_queue(
            session_id,
            &json!({
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
            &json!({
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
        restore_state_dir(previous_state);
    }

    #[test]
    fn queue_item_cap_rejects_overflow_and_stable_tool_key_coalesces() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let previous_items = set_env(WORKER_QUEUE_MAX_ITEMS_ENV, "1");
        let session_id = "session-item-cap";

        assert!(matches!(
            append_queue(
                session_id,
                &json!({
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": session_id,
                    "uuid": "first"
                }),
            )
            .unwrap(),
            AppendOutcome::Appended
        ));
        let overflow = append_queue(
            session_id,
            &json!({
                "hook_event_name": "UserPromptSubmit",
                "session_id": session_id,
                "uuid": "second"
            }),
        )
        .unwrap_err();
        assert!(matches!(overflow, AppendFailure::Full(_)));
        assert_eq!(overflow_count(session_id).unwrap(), 1);

        restore_env(WORKER_QUEUE_MAX_ITEMS_ENV, previous_items);

        let dedup_session = "session-tool-coalesce";
        let tool = tool_payload("PostToolUse", dedup_session, "tool-stable");
        assert!(matches!(
            append_queue(dedup_session, &tool).unwrap(),
            AppendOutcome::Appended
        ));
        assert!(matches!(
            append_queue(dedup_session, &tool).unwrap(),
            AppendOutcome::Coalesced
        ));
        assert_eq!(pending_count(dedup_session).unwrap(), 1);
        restore_state_dir(previous_state);
    }

    #[test]
    fn queue_summary_projection_uses_canonical_coalescing_keys() {
        let stop = json!({
            "hook_event_name": "Stop",
            "transcript_path": "/tmp/session.jsonl",
            "tool_use_id": "ignored"
        });
        assert_eq!(
            QueueSummaryPayload::from_value(&stop).coalescing_key(),
            Some("Stop:/tmp/session.jsonl".to_string())
        );

        let tool = tool_payload("PostToolUse", "session-summary", "tool-summary");
        assert_eq!(
            QueueSummaryPayload::from_value(&tool).coalescing_key(),
            Some("PostToolUse:tool-summary".to_string())
        );

        let unknown = json!({
            "hook_event_name": "UserPromptSubmit",
            "transcript_path": "/tmp/session.jsonl",
            "tool_use_id": "tool-summary"
        });
        assert_eq!(
            QueueSummaryPayload::from_value(&unknown).coalescing_key(),
            None
        );
    }

    #[test]
    fn polling_stop_coalescing_ignores_offset_with_the_same_turn_seed() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let first = test_turn_seed("turn-1", "41049c989ea3510bafe6d446672d7b1c");
        let second = test_turn_seed("turn-2", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");

        for event in ["Stop", "StopFailure"] {
            let session_id = format!("session-{event}-seed-coalescing");
            let payload = json!({
                "hook_event_name": event,
                "session_id": session_id,
                "transcript_path": "/tmp/session.jsonl"
            });

            assert!(matches!(
                append_queue_with_stop_snapshot(
                    &session_id,
                    &payload,
                    Some(first.clone()),
                    Some(100),
                    None,
                )
                .unwrap(),
                AppendOutcome::Appended
            ));
            assert!(matches!(
                append_queue_with_stop_snapshot(
                    &session_id,
                    &payload,
                    Some(first.clone()),
                    Some(200),
                    None,
                )
                .unwrap(),
                AppendOutcome::Coalesced
            ));
            assert!(matches!(
                append_queue_with_stop_snapshot(
                    &session_id,
                    &payload,
                    Some(first.clone()),
                    None,
                    None,
                )
                .unwrap(),
                AppendOutcome::Coalesced
            ));
            assert_eq!(pending_count(&session_id).unwrap(), 1);
        }

        let legacy_session = "session-stop-legacy-offset";
        let legacy_payload = json!({
            "hook_event_name": "Stop",
            "session_id": legacy_session,
            "transcript_path": "/tmp/session.jsonl"
        });
        assert!(matches!(
            append_queue_with_stop_snapshot(legacy_session, &legacy_payload, None, Some(100), None)
                .unwrap(),
            AppendOutcome::Appended
        ));
        assert!(matches!(
            append_queue_with_stop_snapshot(legacy_session, &legacy_payload, None, Some(100), None)
                .unwrap(),
            AppendOutcome::Coalesced
        ));
        assert!(matches!(
            append_queue_with_stop_snapshot(legacy_session, &legacy_payload, None, Some(200), None)
                .unwrap(),
            AppendOutcome::Appended
        ));

        let mismatch_session = "session-stop-seed-mismatch";
        let mismatch_payload = json!({
            "hook_event_name": "Stop",
            "session_id": mismatch_session,
            "transcript_path": "/tmp/session.jsonl"
        });
        assert!(matches!(
            append_queue_with_stop_snapshot(
                mismatch_session,
                &mismatch_payload,
                Some(first.clone()),
                Some(100),
                None,
            )
            .unwrap(),
            AppendOutcome::Appended
        ));
        assert!(matches!(
            append_queue_with_stop_snapshot(
                mismatch_session,
                &mismatch_payload,
                None,
                Some(200),
                None,
            )
            .unwrap(),
            AppendOutcome::Appended
        ));
        assert!(matches!(
            append_queue_with_stop_snapshot(
                mismatch_session,
                &mismatch_payload,
                Some(second.clone()),
                Some(300),
                None,
            )
            .unwrap(),
            AppendOutcome::Appended
        ));

        let terminal_session = "session-session-end-offset";
        let terminal_payload = json!({
            "hook_event_name": "SessionEnd",
            "session_id": terminal_session,
            "transcript_path": "/tmp/session.jsonl"
        });
        assert!(matches!(
            append_queue_with_stop_snapshot(
                terminal_session,
                &terminal_payload,
                Some(first.clone()),
                Some(100),
                None,
            )
            .unwrap(),
            AppendOutcome::Appended
        ));
        assert!(matches!(
            append_queue_with_stop_snapshot(
                terminal_session,
                &terminal_payload,
                Some(first.clone()),
                Some(200),
                None,
            )
            .unwrap(),
            AppendOutcome::Appended
        ));

        let path_session = "session-stop-path-boundary";
        let first_path_payload = json!({
            "hook_event_name": "Stop",
            "session_id": path_session,
            "transcript_path": "/tmp/session-a.jsonl"
        });
        let second_path_payload = json!({
            "hook_event_name": "Stop",
            "session_id": path_session,
            "transcript_path": "/tmp/session-b.jsonl"
        });
        assert!(matches!(
            append_queue_with_stop_snapshot(
                path_session,
                &first_path_payload,
                Some(first.clone()),
                Some(100),
                None,
            )
            .unwrap(),
            AppendOutcome::Appended
        ));
        assert!(matches!(
            append_queue_with_stop_snapshot(
                path_session,
                &second_path_payload,
                Some(first),
                Some(200),
                None,
            )
            .unwrap(),
            AppendOutcome::Appended
        ));

        assert_eq!(pending_count(legacy_session).unwrap(), 2);
        assert_eq!(pending_count(mismatch_session).unwrap(), 3);
        assert_eq!(pending_count(terminal_session).unwrap(), 2);
        assert_eq!(pending_count(path_session).unwrap(), 2);
        restore_state_dir(previous_state);
    }

    #[test]
    fn runtime_cap_releases_owner_and_leaves_successor_work_pending() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let previous_runtime = set_env(WORKER_MAX_RUNTIME_MS_ENV, "10");
        let session_id = "session-runtime-cap";
        for uuid in ["first", "second"] {
            append_queue(
                session_id,
                &json!({
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": session_id,
                    "uuid": uuid
                }),
            )
            .unwrap();
        }
        let owner = SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))
            .expect("owner for drain");
        let stats = drain_queue(session_id, Some(owner), |item| {
            assert_eq!(item.payload["uuid"], "first");
            std::thread::sleep(std::time::Duration::from_millis(20));
            QueueDisposition::Ack
        })
        .unwrap();
        assert!(stats.runtime_exhausted);
        assert_eq!(stats.processed, 1);
        assert_eq!(pending_count(session_id).unwrap(), 1);
        assert!(SessionOwner::try_acquire(session_id).unwrap().is_some());
        restore_env(WORKER_MAX_RUNTIME_MS_ENV, previous_runtime);
        restore_state_dir(previous_state);
    }

    #[test]
    fn delivery_attempt_env_controls_quarantine_threshold() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let previous_attempts = set_env(WORKER_MAX_DELIVERY_ATTEMPTS_ENV, "1");
        let session_id = "session-delivery-attempts";
        append_queue(
            session_id,
            &json!({
                "hook_event_name": "UserPromptSubmit",
                "session_id": session_id,
                "uuid": "retry-once"
            }),
        )
        .unwrap();
        let owner = SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))
            .expect("owner for drain");
        let stats = drain_queue(session_id, Some(owner), |_| {
            QueueDisposition::Retry("test retry".to_string())
        })
        .unwrap();
        assert_eq!(stats.retried, 1);
        assert_eq!(stats.quarantined, 1);
        assert_eq!(pending_count(session_id).unwrap(), 0);
        restore_env(WORKER_MAX_DELIVERY_ATTEMPTS_ENV, previous_attempts);
        restore_state_dir(previous_state);
    }

    #[test]
    fn complete_invalid_jsonl_line_is_dead_lettered_once_and_offset_advances() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-corrupt-line";
        let path = queue_path(session_id).unwrap();
        crate::common::dirs::ensure_private_dir(path.parent().unwrap()).unwrap();
        std::fs::write(&path, b"{not-json}\n").unwrap();
        {
            let _queue_lock = QueueLock::acquire(session_id).unwrap();
            write_offset(session_id, 0).unwrap();
        }
        let owner = SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))
            .expect("owner for drain");
        let mut processed = 0usize;
        let stats = drain_queue(session_id, Some(owner), |_| {
            processed += 1;
            QueueDisposition::Ack
        })
        .unwrap();
        assert_eq!(processed, 0);
        assert_eq!(stats.quarantined, 1);
        assert_eq!(pending_count(session_id).unwrap(), 0);
        let failed = std::fs::read_to_string(failed_path(session_id).unwrap()).unwrap();
        assert!(failed.contains("invalid queue JSON"));
        restore_state_dir(previous_state);
    }

    #[test]
    fn spawn_failure_marker_is_persisted_and_next_claim_replaces_it() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let previous_failure = set_env("CCHOOKS_TEST_WORKER_SPAWN_FAILURE", "true");
        let logger = logger(&dir);
        let session_id = "session-spawn-failure-marker";
        let error = wake_session(session_id, &logger).unwrap_err();
        assert!(error.contains("test-injected worker spawn failure"));
        assert_eq!(
            start_marker_status(session_id).unwrap(),
            Some("spawn_failed".to_string())
        );
        assert!(SessionOwner::try_acquire(session_id).unwrap().is_some());
        let replacement = match claim_start_marker(session_id).unwrap() {
            StartClaim::Claimed(token) => token,
            StartClaim::Existing => panic!("spawn_failed marker must be replaceable immediately"),
        };
        clear_start_marker(session_id, &replacement).unwrap();
        restore_env("CCHOOKS_TEST_WORKER_SPAWN_FAILURE", previous_failure);
        restore_state_dir(previous_state);
    }

    #[test]
    fn live_start_marker_coalesces_but_dead_starter_is_reclaimed() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-start-marker-liveness";
        let _owner = SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))
            .expect("owner for marker liveness test");

        let token = match claim_start_marker(session_id).unwrap() {
            StartClaim::Claimed(token) => token,
            StartClaim::Existing => panic!("first marker claim must succeed"),
        };
        publish_start_marker_pid(session_id, &token, std::process::id()).unwrap();
        assert!(matches!(
            claim_start_marker(session_id).unwrap(),
            StartClaim::Existing
        ));

        let path = dirs::session_worker_start_file(session_id).unwrap();
        let mut marker: WorkerStartMarker =
            serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        marker.pid = Some(u32::MAX);
        write_start_marker_atomically(&path, &serde_json::to_value(marker).unwrap()).unwrap();
        let replacement = match claim_start_marker(session_id).unwrap() {
            StartClaim::Claimed(token) => token,
            StartClaim::Existing => panic!("dead starter must be reclaimed immediately"),
        };
        clear_start_marker(session_id, &replacement).unwrap();
        restore_state_dir(previous_state);
    }

    #[cfg(unix)]
    #[test]
    fn spawned_worker_exit_before_ownership_is_detected() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-worker-early-exit";
        let owner = SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))
            .expect("owner before child start");
        let token = match claim_start_marker(session_id).unwrap() {
            StartClaim::Claimed(token) => token,
            StartClaim::Existing => panic!("new marker unexpectedly existed"),
        };
        let mut child = Command::new("/usr/bin/false").spawn().unwrap();
        publish_start_marker_pid(session_id, &token, child.id()).unwrap();
        drop(owner);
        let error = wait_for_spawned_worker_start(
            session_id,
            &token,
            &mut child,
            std::time::Duration::from_secs(1),
        )
        .unwrap_err();
        assert!(
            error.contains("exited before ownership acknowledgement"),
            "{error}"
        );
        restore_state_dir(previous_state);
    }

    #[cfg(unix)]
    #[test]
    fn consumed_start_marker_without_owner_is_not_acknowledged() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-worker-marker-without-owner";
        let owner = SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))
            .expect("owner before child start");
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
        restore_state_dir(previous_state);
    }

    #[test]
    fn confirmed_handoff_retries_spawn_failure_and_leaves_recoverable_marker() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let previous_failure = set_env("CCHOOKS_TEST_WORKER_SPAWN_FAILURE", "true");
        let logger = logger(&dir);
        let session_id = "session-confirmed-handoff-failure";
        let error = wake_session_confirmed(session_id, &logger).unwrap_err();
        assert!(error.contains("failed after 3 attempts"));
        assert_eq!(
            start_marker_status(session_id).unwrap().as_deref(),
            Some("spawn_failed")
        );
        assert!(SessionOwner::try_acquire(session_id).unwrap().is_some());
        restore_env("CCHOOKS_TEST_WORKER_SPAWN_FAILURE", previous_failure);
        restore_state_dir(previous_state);
    }

    #[test]
    fn repairs_partial_tail_and_out_of_range_offset() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-tail-repair";
        let path = queue_path(session_id).unwrap();
        crate::common::dirs::ensure_private_dir(path.parent().unwrap()).unwrap();
        let good = serde_json::to_vec(&QueueEnvelope {
            queued_at_ns: unix_nanos().to_string(),
            env: BTreeMap::new(),
            turn_seed: None,
            transcript_end_offset: None,
            transcript_ingest_receipt: None,
            payload: json!({"hook_event_name": "UserPromptSubmit", "session_id": session_id, "uuid": "good"}),
        })
        .unwrap();
        let mut bytes = good.clone();
        bytes.push(b'\n');
        bytes.extend_from_slice(b"{\"queued_at_ns\":\"broken\"");
        std::fs::write(&path, bytes).unwrap();
        {
            let _queue_lock = QueueLock::acquire(session_id).unwrap();
            write_offset(session_id, 999_999).unwrap();
        }

        assert_eq!(pending_count(session_id).unwrap(), 1);
        assert_eq!(std::fs::read(&path).unwrap().len(), good.len() + 1);
        assert_eq!(read_offset(session_id).unwrap(), 0);
        restore_state_dir(previous_state);
    }

    #[test]
    fn acknowledged_prefix_compacts_after_threshold_and_preserves_remaining_record() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let previous_bytes = set_env(WORKER_QUEUE_MAX_BYTES_ENV, "1048576");
        let session_id = "session-prefix-compaction";
        for uuid in ["first", "second", "third"] {
            append_queue(
                session_id,
                &json!({
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": session_id,
                    "uuid": uuid,
                    "blob": "x".repeat(300_000)
                }),
            )
            .unwrap();
        }

        let _queue_lock = QueueLock::acquire(session_id).unwrap();
        let first = match read_next_locked(session_id).unwrap() {
            QueueRead::Item(record) => record,
            _ => panic!("first queue record missing"),
        };
        acknowledge_locked(session_id, first.next_offset).unwrap();
        assert!(read_offset(session_id).unwrap() > 0);

        let second = match read_next_locked(session_id).unwrap() {
            QueueRead::Item(record) => record,
            _ => panic!("second queue record missing"),
        };
        acknowledge_locked(session_id, second.next_offset).unwrap();
        assert_eq!(read_offset(session_id).unwrap(), 0);

        let remaining = match read_next_locked(session_id).unwrap() {
            QueueRead::Item(record) => record,
            _ => panic!("remaining queue record missing after compaction"),
        };
        assert_eq!(remaining.item.payload["uuid"], "third");
        restore_env(WORKER_QUEUE_MAX_BYTES_ENV, previous_bytes);
        restore_state_dir(previous_state);
    }

    #[test]
    fn compacted_queue_recovers_if_crash_precedes_offset_reset() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-compaction-crash-order";
        for (uuid, blob_size) in [("acknowledged", 300_000), ("pending", 32)] {
            append_queue(
                session_id,
                &json!({
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": session_id,
                    "uuid": uuid,
                    "blob": "x".repeat(blob_size)
                }),
            )
            .unwrap();
        }

        let _queue_lock = QueueLock::acquire(session_id).unwrap();
        let acknowledged = match read_next_locked(session_id).unwrap() {
            QueueRead::Item(record) => record,
            _ => panic!("acknowledged queue record missing"),
        };
        write_offset(session_id, acknowledged.next_offset).unwrap();
        let path = queue_path(session_id).unwrap();

        // Simulate a crash after the durable queue replacement but before the
        // offset reset. The stale old offset must point beyond the compacted
        // file so the next reader repairs to zero without replaying the prefix.
        replace_queue_with_unconsumed_suffix_locked(&path, acknowledged.next_offset).unwrap();
        assert_eq!(read_offset(session_id).unwrap(), acknowledged.next_offset);
        assert!(std::fs::metadata(&path).unwrap().len() < acknowledged.next_offset);

        let pending = match read_next_locked(session_id).unwrap() {
            QueueRead::Item(record) => record,
            _ => panic!("pending queue record missing after crash-order recovery"),
        };
        assert_eq!(pending.item.payload["uuid"], "pending");
        assert_eq!(read_offset(session_id).unwrap(), 0);
        restore_state_dir(previous_state);
    }

    #[test]
    fn unacknowledged_record_replays_after_simulated_crash() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-replay-unacked";
        append_queue(
            session_id,
            &json!({
                "hook_event_name": "UserPromptSubmit",
                "session_id": session_id,
                "uuid": "replayed"
            }),
        )
        .unwrap();

        {
            let _queue_lock = QueueLock::acquire(session_id).unwrap();
            match read_next_locked(session_id).unwrap() {
                QueueRead::Item(record) => assert_eq!(record.item.payload["uuid"], "replayed"),
                _ => panic!("expected queued item"),
            }
        }
        assert_eq!(read_offset(session_id).unwrap(), 0);

        let owner = SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))
            .expect("owner for drain");
        let mut processed = 0;
        drain_queue(session_id, Some(owner), |item| {
            assert_eq!(item.payload["uuid"], "replayed");
            processed += 1;
            QueueDisposition::Ack
        })
        .unwrap();
        assert_eq!(processed, 1);
        assert_eq!(pending_count(session_id).unwrap(), 0);
        restore_state_dir(previous_state);
    }

    #[test]
    fn queue_env_snapshot_excludes_credentials_and_internal_worker_rendezvous() {
        for key in [
            "LANGFUSE_SECRET_KEY",
            "LANGFUSE_PUBLIC_KEY",
            "AIVISION_API_TOKEN",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "CCHOOKS_WORKER_START_TOKEN",
            "CCHOOKS_WORKER_SESSION_ID",
            "CCHOOKS_WORKER_MODE",
        ] {
            assert!(!should_capture_worker_env(key), "must not persist {key}");
        }
        for key in [
            "TRACEPARENT",
            "SOFA_TRACEID",
            "CCHOOKS_EXPORT_MODE",
            "AIVISION_HOME",
            "AIVISION_AGENT_ENV",
            "LANGFUSE_METADATA",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_RESOURCE_ATTRIBUTES",
            "USERPROFILE",
        ] {
            assert!(should_capture_worker_env(key), "must preserve {key}");
        }
    }

    #[test]
    fn maintenance_fairness_drains_one_thousand_hooks_in_32_item_quanta() {
        use std::cell::{Cell, RefCell};

        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-maintenance-fairness";
        for ordinal in 0..1_000 {
            append_queue(
                session_id,
                &json!({
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": session_id,
                    "ordinal": ordinal,
                }),
            )
            .unwrap();
        }
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(100)).unwrap();
        let processed = Cell::new(0usize);
        let maintenance_at = RefCell::new(Vec::new());
        let stats = drain_queue_with_maintenance(
            session_id,
            Some(owner),
            |_| {
                processed.set(processed.get() + 1);
                QueueDisposition::Ack
            },
            || {
                maintenance_at.borrow_mut().push(processed.get());
                MaintenanceDisposition::Idle
            },
        )
        .unwrap();
        assert_eq!(stats.processed, 1_000);
        let checkpoints = maintenance_at.into_inner();
        assert!(checkpoints.first().is_some_and(|count| *count <= 32));
        assert!(checkpoints
            .windows(2)
            .all(|pair| pair[1].saturating_sub(pair[0]) <= 32));
        assert_eq!(pending_count(session_id).unwrap(), 0);
        restore_state_dir(previous_state);
    }

    #[test]
    fn maintenance_wait_is_capped_at_five_hundred_milliseconds() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-maintenance-wait-cap";
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(100)).unwrap();
        let calls = std::cell::Cell::new(0usize);
        let started = std::time::Instant::now();
        drain_queue_with_maintenance(
            session_id,
            Some(owner),
            |_| QueueDisposition::Ack,
            || {
                let call = calls.get();
                calls.set(call + 1);
                if call == 0 {
                    MaintenanceDisposition::Waiting(Duration::from_secs(5))
                } else {
                    MaintenanceDisposition::Idle
                }
            },
        )
        .unwrap();
        let elapsed = started.elapsed();
        assert!(elapsed >= Duration::from_millis(450), "elapsed={elapsed:?}");
        assert!(elapsed < Duration::from_millis(900), "elapsed={elapsed:?}");
        restore_state_dir(previous_state);
    }

    #[test]
    fn maintenance_did_work_rechecks_fifo_before_running_another_slice() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-maintenance-did-work-fifo";
        let owner = SessionOwner::acquire(session_id, Duration::from_millis(100)).unwrap();
        let maintenance_calls = std::cell::Cell::new(0usize);
        let processed = std::cell::Cell::new(0usize);

        drain_queue_with_maintenance(
            session_id,
            Some(owner),
            |_| {
                processed.set(processed.get() + 1);
                QueueDisposition::Ack
            },
            || {
                let call = maintenance_calls.get();
                maintenance_calls.set(call + 1);
                if call == 0 {
                    append_queue(
                        session_id,
                        &json!({
                            "hook_event_name": "UserPromptSubmit",
                            "session_id": session_id,
                        }),
                    )
                    .unwrap();
                    MaintenanceDisposition::DidWork
                } else {
                    assert_eq!(
                        processed.get(),
                        1,
                        "FIFO must run before maintenance repeats"
                    );
                    MaintenanceDisposition::Idle
                }
            },
        )
        .unwrap();

        assert_eq!(processed.get(), 1);
        assert_eq!(pending_count(session_id).unwrap(), 0);
        restore_state_dir(previous_state);
    }

    #[test]
    fn queued_stop_intent_cannot_race_a_matching_fifo_record() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-stop-queued-barrier";
        let mut payload = json!({"hook_event_name": "Stop", "session_id": session_id});
        let owner = write_owned_stop(&HookEvent::Stop, &mut payload, false, &logger);
        assert!(matches!(
            scheduled_stop_recovery(session_id, &logger).unwrap(),
            PendingStopRecovery::Idle
        ));

        finish_stop_worker(
            session_id,
            Some(&owner),
            &payload,
            &StopOutcome::deferred("hook_budget_exhausted"),
            u128::MAX,
            &logger,
        );
        assert!(matches!(
            scheduled_stop_recovery(session_id, &logger).unwrap(),
            PendingStopRecovery::Waiting(_)
        ));
        restore_state_dir(previous_state);
    }

    #[test]
    fn queued_stop_intent_is_rejected_inside_the_atomic_claim() {
        let dir = tempfile::tempdir().unwrap();
        let store = DomainStateStore::new(
            dir.path().join("state_stop.json"),
            None,
            "session-stop-atomic-queued-barrier",
            StateDomain::Stop,
            100,
        );
        store
            .update(|state| {
                state["pending_final_export"] = json!({
                    "intent_id": "new-queued-intent",
                    "hook_event_name": "Stop",
                    "status": "queued",
                });
                Ok(())
            })
            .unwrap();

        assert!(
            claim_pending_stop_intent(&store, 100, Some("new-queued-intent"))
                .unwrap()
                .is_none()
        );
        let state = store.read(Clone::clone).unwrap();
        assert_eq!(state["pending_final_export"]["status"], "queued");
        assert!(state["pending_final_export"]
            .get("recovery_lease_until_ms")
            .is_none());
    }

    #[test]
    fn atomic_claim_rejects_a_replaced_pending_intent() {
        let dir = tempfile::tempdir().unwrap();
        let store = DomainStateStore::new(
            dir.path().join("state_stop.json"),
            None,
            "session-stop-atomic-revision",
            StateDomain::Stop,
            100,
        );
        store
            .update(|state| {
                state["pending_final_export"] = json!({
                    "intent_id": "new-intent",
                    "hook_event_name": "Stop",
                    "status": "pending",
                });
                Ok(())
            })
            .unwrap();

        assert!(claim_pending_stop_intent(&store, 100, Some("old-intent"))
            .unwrap()
            .is_none());
        let state = store.read(Clone::clone).unwrap();
        assert_eq!(state["pending_final_export"]["intent_id"], "new-intent");
        assert_eq!(state["pending_final_export"]["status"], "pending");
        assert!(state["pending_final_export"]
            .get("recovery_lease_until_ms")
            .is_none());
    }

    #[test]
    fn composite_stop_checkpoint_is_visible_to_domain_fingerprint() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-stop-composite-fingerprint";
        let domain_store =
            DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        domain_store
            .update(|state| {
                state["stop_recovery"] = json!({
                    "intent_id": "intent-composite",
                    "scan_offset": 0,
                });
                Ok(())
            })
            .unwrap();
        let before = domain_store
            .read(stop_recovery_progress_fingerprint)
            .unwrap();

        let composite_store = SessionStateStore::for_session(session_id, 100).unwrap();
        composite_store
            .update_session(|session| {
                session["stop_recovery"]["scan_offset"] = json!(256 * 1024);
                Ok(())
            })
            .unwrap();

        let state = domain_store.read(Clone::clone).unwrap();
        let after = stop_recovery_progress_fingerprint(&state);
        assert_eq!(state["stop_recovery"]["scan_offset"], 256 * 1024);
        assert_ne!(before, after);
        restore_state_dir(previous_state);
    }

    #[test]
    fn waiting_terminal_recovery_has_a_bounded_cooldown() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-stop-waiting-terminal-cooldown";
        let mut payload = json!({"hook_event_name": "Stop", "session_id": session_id});
        let owner = write_owned_stop(&HookEvent::Stop, &mut payload, false, &logger);
        finish_stop_worker(
            session_id,
            Some(&owner),
            &payload,
            &StopOutcome::deferred("Stop handler did not reach a terminal outcome"),
            u128::MAX,
            &logger,
        );

        let store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        let state = store.read(Clone::clone).unwrap();
        assert_eq!(state["stop_recovery"]["phase"], "waiting_terminal");
        assert_eq!(
            state["stop_recovery"]["next_eligible_at_ms"].as_u64(),
            state["stop_recovery"]["updated_at_ms"]
                .as_u64()
                .map(|updated| updated + STOP_RECOVERY_BACKOFF_BASE_MS)
        );
        store
            .update(|state| {
                state["stop_recovery"]["next_eligible_at_ms"] =
                    json!(now_millis().saturating_add(60_000));
                Ok(())
            })
            .unwrap();
        assert!(matches!(
            scheduled_stop_recovery(session_id, &logger).unwrap(),
            PendingStopRecovery::Waiting(delay) if delay <= Duration::from_millis(500)
        ));
        restore_state_dir(previous_state);
    }

    #[test]
    fn unchanged_recovery_quarantines_after_exactly_three_slices() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-stop-no-progress";
        let mut payload = json!({"hook_event_name": "Stop", "session_id": session_id});
        let owner = write_owned_stop(&HookEvent::Stop, &mut payload, false, &logger);
        finish_stop_worker(
            session_id,
            Some(&owner),
            &payload,
            &StopOutcome::deferred("hook_budget_exhausted"),
            u128::MAX,
            &logger,
        );
        let mut recovered = payload.clone();
        recovered["cchooks_recovered_stop_intent"] = json!(true);
        let store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        for expected in 1..=STOP_RECOVERY_MAX_NO_PROGRESS_ATTEMPTS {
            finish_stop_worker(
                session_id,
                Some(&owner),
                &recovered,
                &StopOutcome::deferred("hook_budget_exhausted"),
                u128::MAX,
                &logger,
            );
            let state = store.read(Clone::clone).unwrap();
            assert_eq!(state["stop_recovery"]["no_progress_attempts"], expected);
            if expected < STOP_RECOVERY_MAX_NO_PROGRESS_ATTEMPTS {
                assert_eq!(state["stop_recovery"]["phase"], "cooldown");
            } else {
                assert_eq!(state["stop_recovery"]["phase"], "terminal_quarantined");
                assert_eq!(
                    state["pending_final_export"]["status"],
                    "terminal_quarantined"
                );
            }
        }
        assert!(matches!(
            scheduled_stop_recovery(session_id, &logger).unwrap(),
            PendingStopRecovery::Idle
        ));
        restore_state_dir(previous_state);
    }

    #[cfg(unix)]
    #[test]
    fn queue_activity_preserves_existing_session_directory_permissions() {
        use std::os::unix::fs::PermissionsExt;

        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let session_id = "session-existing-permissions";
        let session_dir = dirs::session_state_dir(session_id).unwrap();
        std::fs::create_dir_all(&session_dir).unwrap();
        std::fs::set_permissions(&session_dir, std::fs::Permissions::from_mode(0o750)).unwrap();

        {
            let _queue_lock = QueueLock::acquire(session_id).unwrap();
            write_offset(session_id, 0).unwrap();
        }
        let _owner = SessionOwner::acquire(session_id, std::time::Duration::from_millis(100))
            .expect("session owner");

        assert_eq!(
            std::fs::metadata(&session_dir)
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o750
        );
        restore_state_dir(previous_state);
    }

    #[test]
    fn structured_output_stop_transitions_from_awaiting_to_owned_finalizer() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-structured-two-phase";
        let base = json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": "/tmp/session.jsonl",
            "stop_hook_active": false
        });

        let first = write_stop_intent(&HookEvent::Stop, &base, true, &logger);
        let mut first_payload = base.clone();
        first.apply_to_payload(&mut first_payload);
        assert!(matches!(
            prepare_stop_worker(&first_payload, &logger),
            StopWorkerDecision::AwaitingContinuation
        ));

        let store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        let provisional = store
            .read(|state| state["pending_final_export"].clone())
            .unwrap();
        assert_eq!(provisional["phase"], PHASE_AWAITING_CONTINUATION);
        assert_eq!(provisional["structured_output"], true);
        let provisional_id = provisional["intent_id"].as_str().unwrap().to_string();
        let provisional_owner = provisional["owner_token"].as_str().unwrap().to_string();

        let mut final_payload = base;
        final_payload["stop_hook_active"] = json!(true);
        let final_handoff = write_stop_intent(&HookEvent::Stop, &final_payload, true, &logger);
        final_handoff.apply_to_payload(&mut final_payload);
        assert_eq!(final_payload[INTERNAL_STOP_INTENT_ID], provisional_id);
        assert_ne!(final_payload[INTERNAL_STOP_OWNER_TOKEN], provisional_owner);
        assert_eq!(
            final_payload[crate::hooks::INTERNAL_FINALIZER_AUTHORIZED],
            true
        );

        let owner = match prepare_stop_worker(&final_payload, &logger) {
            StopWorkerDecision::Run { owner: Some(owner) } => owner,
            _ => panic!("final active Stop must own the provisional intent"),
        };
        finish_stop_worker(
            session_id,
            Some(&owner),
            &final_payload,
            &StopOutcome::Completed,
            u128::MAX,
            &logger,
        );
        assert!(store
            .read(|state| state.get("pending_final_export").is_none())
            .unwrap());
        restore_state_dir(previous_state);
    }

    #[test]
    fn stop_handoff_discards_caller_supplied_internal_fields() {
        let mut payload = json!({
            "hook_event_name": "Stop",
            "session_id": "session-forged-internal-fields",
            "stop_hook_active": false,
            INTERNAL_STOP_INTENT_ID: "forged-intent",
            INTERNAL_STOP_OWNER_TOKEN: "forged-owner",
            INTERNAL_STOP_REVISION: 99,
            INTERNAL_STOP_PHASE: PHASE_AWAITING_CONTINUATION,
            INTERNAL_STRUCTURED_OUTPUT: true,
            crate::hooks::INTERNAL_FINALIZER_AUTHORIZED: true
        });
        let handoff = StopIntentHandoff::unpersisted(&HookEvent::Stop, &payload, true);

        handoff.apply_to_payload(&mut payload);

        assert!(payload.get(INTERNAL_STOP_INTENT_ID).is_none());
        assert!(payload.get(INTERNAL_STOP_OWNER_TOKEN).is_none());
        assert!(payload.get(INTERNAL_STOP_REVISION).is_none());
        assert_eq!(payload[INTERNAL_STOP_PHASE], PHASE_FINALIZING);
        assert_eq!(payload[INTERNAL_STRUCTURED_OUTPUT], false);
        assert_eq!(payload[crate::hooks::INTERNAL_FINALIZER_AUTHORIZED], false);
    }

    #[test]
    fn active_stop_finalizer_lease_rejects_duplicate_terminal_worker() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-active-stop-finalizer";
        let first = json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": "/tmp/session.jsonl",
            "stop_hook_active": false
        });
        write_stop_intent(&HookEvent::Stop, &first, true, &logger);

        let mut final_payload = first;
        final_payload["stop_hook_active"] = json!(true);
        let final_handoff = write_stop_intent(&HookEvent::Stop, &final_payload, true, &logger);
        final_handoff.apply_to_payload(&mut final_payload);
        let owner = match prepare_stop_worker(&final_payload, &logger) {
            StopWorkerDecision::Run { owner: Some(owner) } => owner,
            _ => panic!("first finalizer should own the intent"),
        };

        let mut duplicate_payload = final_payload.clone();
        let duplicate = write_stop_intent(&HookEvent::Stop, &duplicate_payload, true, &logger);
        duplicate.apply_to_payload(&mut duplicate_payload);
        assert!(matches!(
            prepare_stop_worker(&duplicate_payload, &logger),
            StopWorkerDecision::Stale
        ));

        finish_stop_worker(
            session_id,
            Some(&owner),
            &final_payload,
            &StopOutcome::Completed,
            u128::MAX,
            &logger,
        );
        let store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        assert!(store
            .read(|state| state.get("pending_final_export").is_none())
            .unwrap());
        restore_state_dir(previous_state);
    }

    #[test]
    fn session_end_follows_in_flight_structured_stop_only_when_it_defers() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-end-follows-active-stop";
        let provisional = json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": "/tmp/session.jsonl",
            "stop_hook_active": false
        });
        write_stop_intent(&HookEvent::Stop, &provisional, true, &logger);

        let mut active_payload = provisional;
        active_payload["stop_hook_active"] = json!(true);
        let active_handoff = write_stop_intent(&HookEvent::Stop, &active_payload, true, &logger);
        active_handoff.apply_to_payload(&mut active_payload);
        let active_owner = match prepare_stop_worker(&active_payload, &logger) {
            StopWorkerDecision::Run { owner: Some(owner) } => owner,
            _ => panic!("active Stop must own the structured intent"),
        };

        let mut session_end_payload = json!({
            "hook_event_name": "SessionEnd",
            "session_id": session_id,
            "transcript_path": "/tmp/session.jsonl"
        });
        let session_end_handoff =
            write_stop_intent(&HookEvent::SessionEnd, &session_end_payload, true, &logger);
        session_end_handoff.apply_to_payload(&mut session_end_payload);
        assert_eq!(
            session_end_payload[INTERNAL_STOP_OWNER_TOKEN].as_str(),
            Some(active_owner.owner_token.as_str())
        );
        assert_eq!(
            session_end_payload[INTERNAL_STOP_REVISION].as_u64(),
            Some(active_owner.revision)
        );

        finish_stop_worker(
            session_id,
            Some(&active_owner),
            &active_payload,
            &StopOutcome::deferred("incomplete_tail"),
            u128::MAX,
            &logger,
        );
        let session_end_owner = match prepare_stop_worker(&session_end_payload, &logger) {
            StopWorkerDecision::Run { owner: Some(owner) } => owner,
            _ => panic!("SessionEnd must finalize the deferred active Stop intent"),
        };
        finish_stop_worker(
            session_id,
            Some(&session_end_owner),
            &session_end_payload,
            &StopOutcome::Completed,
            u128::MAX,
            &logger,
        );

        let store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        assert!(store
            .read(|state| state.get("pending_final_export").is_none())
            .unwrap());
        assert!(matches!(
            prepare_stop_worker(&active_payload, &logger),
            StopWorkerDecision::Stale
        ));
        restore_state_dir(previous_state);
    }

    #[test]
    fn stale_normal_stop_owner_cannot_clear_newer_intent() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-stale-normal-stop-owner";
        let base = json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": "/tmp/session.jsonl",
            "stop_hook_active": false
        });
        let mut first_payload = base.clone();
        let first_owner = write_owned_stop(&HookEvent::Stop, &mut first_payload, false, &logger);
        let mut second_payload = base;
        let second_owner = write_owned_stop(&HookEvent::Stop, &mut second_payload, false, &logger);

        finish_stop_worker(
            session_id,
            Some(&first_owner),
            &first_payload,
            &StopOutcome::Completed,
            u128::MAX,
            &logger,
        );
        let store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        let persisted = store
            .read(|state| state["pending_final_export"].clone())
            .unwrap();
        assert_eq!(persisted["owner_token"], second_owner.owner_token);
        assert!(matches!(
            prepare_stop_worker(&first_payload, &logger),
            StopWorkerDecision::Run { owner: Some(_) }
        ));
        restore_state_dir(previous_state);
    }

    #[cfg(unix)]
    #[test]
    fn structured_stop_fails_open_when_intent_lock_is_unavailable() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-structured-lock-unavailable";
        let store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        let held_lock = crate::hooks::langfuse::state::StateLock::acquire(store.state_file(), 100);
        assert!(held_lock.is_acquired());

        let mut payload = json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": "/tmp/session.jsonl",
            "stop_hook_active": false
        });
        let handoff = write_stop_intent(&HookEvent::Stop, &payload, true, &logger);
        handoff.apply_to_payload(&mut payload);

        assert_eq!(payload[INTERNAL_STOP_PHASE], PHASE_FINALIZING);
        assert_eq!(payload[INTERNAL_STRUCTURED_OUTPUT], false);
        assert!(matches!(
            prepare_stop_worker(&payload, &logger),
            StopWorkerDecision::Run { owner: None }
        ));
        drop(held_lock);
        restore_state_dir(previous_state);
    }

    #[test]
    fn unowned_stop_worker_cannot_clear_persisted_intent() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-unowned-stop-cleanup";
        let store = DomainStateStore::for_session(session_id, StateDomain::Stop, 100).unwrap();
        store
            .update(|state| {
                state["pending_final_export"] = json!({
                    "hook_event_name": "Stop",
                    "intent_id": "newer-intent",
                    "owner_token": "newer-owner",
                    "revision": 2,
                    "phase": PHASE_AWAITING_CONTINUATION,
                    "structured_output": true,
                    "status": "pending"
                });
                Ok(())
            })
            .unwrap();
        let payload = json!({"hook_event_name": "Stop", "session_id": session_id});

        finish_stop_worker(
            session_id,
            None,
            &payload,
            &StopOutcome::Completed,
            u128::MAX,
            &logger,
        );

        let persisted = store
            .read(|state| state["pending_final_export"].clone())
            .unwrap();
        assert_eq!(persisted["intent_id"], "newer-intent");
        assert_eq!(persisted["owner_token"], "newer-owner");
        assert_eq!(persisted["revision"], 2);
        restore_state_dir(previous_state);
    }

    #[test]
    fn awaiting_intent_is_not_recovered_by_time_alone() {
        let dir = tempfile::tempdir().unwrap();
        let store = DomainStateStore::new(
            dir.path().join("state_stop.json"),
            None,
            "session-awaiting-no-timeout",
            StateDomain::Stop,
            100,
        );
        store
            .update(|state| {
                state["pending_final_export"] = json!({
                    "hook_event_name": "Stop",
                    "phase": PHASE_AWAITING_CONTINUATION,
                    "structured_output": true,
                    "status": "pending"
                });
                Ok(())
            })
            .unwrap();
        assert!(claim_pending_stop_intent(&store, u64::MAX / 2, None)
            .unwrap()
            .is_none());
        let phase = store
            .read(|state| state["pending_final_export"]["phase"].clone())
            .unwrap();
        assert_eq!(phase, PHASE_AWAITING_CONTINUATION);
    }

    #[test]
    fn terminal_events_explicitly_finalize_abandoned_structured_intent() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        for (event, event_name, session_id) in [
            (
                HookEvent::StopFailure,
                "StopFailure",
                "session-structured-stop-failure",
            ),
            (
                HookEvent::SessionEnd,
                "SessionEnd",
                "session-structured-session-end",
            ),
        ] {
            let stop = json!({
                "hook_event_name": "Stop",
                "session_id": session_id,
                "transcript_path": "/tmp/session.jsonl",
                "stop_hook_active": false
            });
            write_stop_intent(&HookEvent::Stop, &stop, true, &logger);

            let terminal = json!({
                "hook_event_name": event_name,
                "session_id": session_id,
                "transcript_path": "/tmp/session.jsonl"
            });
            let handoff = write_stop_intent(&event, &terminal, false, &logger);
            let mut worker_payload = terminal;
            handoff.apply_to_payload(&mut worker_payload);
            assert_eq!(worker_payload[INTERNAL_STRUCTURED_OUTPUT], true);
            assert_eq!(
                worker_payload[INTERNAL_STOP_PHASE],
                PHASE_FALLBACK_FINALIZING
            );
            assert_eq!(
                worker_payload[crate::hooks::INTERNAL_FINALIZER_AUTHORIZED],
                true
            );
            assert!(matches!(
                prepare_stop_worker(&worker_payload, &logger),
                StopWorkerDecision::Run { owner: Some(_) }
            ));
        }
        restore_state_dir(previous_state);
    }

    #[test]
    fn active_final_stop_uses_persisted_structured_resolution() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let session_id = "session-structured-env-drift";
        let first = json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": "/tmp/session.jsonl",
            "stop_hook_active": false
        });
        write_stop_intent(&HookEvent::Stop, &first, true, &logger);

        let mut final_payload = json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": "/tmp/session.jsonl",
            "stop_hook_active": true
        });
        let handoff = write_stop_intent(&HookEvent::Stop, &final_payload, false, &logger);
        handoff.apply_to_payload(&mut final_payload);
        assert_eq!(final_payload[INTERNAL_STRUCTURED_OUTPUT], true);
        assert_eq!(
            final_payload[crate::hooks::INTERNAL_FINALIZER_AUTHORIZED],
            true
        );
        assert!(matches!(
            prepare_stop_worker(&final_payload, &logger),
            StopWorkerDecision::Run { owner: Some(_) }
        ));
        restore_state_dir(previous_state);
    }

    #[test]
    fn active_structured_stop_without_provisional_intent_is_stale() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state = set_state_dir(&dir.path().join("state"));
        let logger = logger(&dir);
        let mut payload = json!({
            "hook_event_name": "Stop",
            "session_id": "session-active-without-provisional",
            "transcript_path": "/tmp/session.jsonl",
            "stop_hook_active": true
        });
        let handoff = write_stop_intent(&HookEvent::Stop, &payload, true, &logger);
        handoff.apply_to_payload(&mut payload);
        assert!(matches!(
            prepare_stop_worker(&payload, &logger),
            StopWorkerDecision::Stale
        ));
        restore_state_dir(previous_state);
    }
}
