use crate::cli::config::Config;
use crate::common::dirs;
use crate::common::logger::Logger;
use crate::common::otel::{
    build_otel_export_request_for_turn, deterministic_tool_span_id, inject_vcs_resource_attrs,
    post_otel_to_langfuse_once, post_otel_to_langfuse_once_classified, suppress_child_spans_by_id,
    TurnReplayOpts,
};
use crate::common::spool;
use crate::common::transcript::{
    build_turns, build_turns_with_usage_context, has_native_usage_record, read_new_jsonl,
    split_completed_messages, Turn,
};
use crate::hooks::langfuse::langfuse_handler::{
    build_turn_trace_opts, merge_child_sessions_into_body, ChildMergeOutcome,
};
use crate::hooks::langfuse::realtime::{
    build_realtime_generation_payload_for_turns, build_realtime_tool_payload,
    extract_realtime_semantic_children, plan_realtime_payload_chunks, RealtimeToolCompletion,
    REALTIME_MAX_CHUNKS_PER_CLAIM, REALTIME_TARGET_SERIALIZED_BODY_BYTES,
};
use crate::hooks::langfuse::realtime_generation::{
    inspect_transcript_source, read_bounded_transcript_window, read_complete_transcript_range,
    BoundedScanOutcome, RealtimeGenerationCorrelation, RealtimeGenerationTrigger,
    TEMPORARY_TRANSCRIPT_IO_ERROR,
};
use crate::hooks::langfuse::realtime_state;
use crate::hooks::langfuse::state::{
    arm_legacy_finalizer_recovery, checkpoint_turn_export_attempt, checkpoint_turn_export_success,
    claim_finalizer_recovery_attempt, commit_export_success,
    durably_accounted_turn_ids_for_history, finalize_realtime_high_water, finalizer_recovery_key,
    finalizer_waits_for_explicit_terminal, get_int_field, get_session_state,
    get_string_array_field, get_vcs_for_cwd, load_state, mark_export_terminal_failure,
    record_terminal_quarantined_turn, stop_recovery_eligibility, stop_recovery_record,
    structural_topology_quarantine, terminal_quarantine_count, update_state_transaction,
    StopRecoveryEligibility, REALTIME_TOPOLOGY_AMBIGUOUS_REASON,
};
#[cfg(test)]
use crate::hooks::langfuse::state::{
    record_finite_finalizer_deferred, ALIAS_CYCLE_QUARANTINE_REASON,
    FACT_CONFLICT_QUARANTINE_REASON, MISSING_ALIAS_TARGET_QUARANTINE_REASON,
    MULTIPLE_PUBLISHED_ROOTS_QUARANTINE_REASON,
};
use crate::hooks::langfuse::subagent_progress::{
    account, account_chunk, config_matches_delivery_context, due,
    records as subagent_recovery_records, suppress_accounted, wake as wake_subagent_recovery,
    RecoveryRecord, RecoverySchedule,
};
#[cfg(test)]
use crate::hooks::langfuse::turn_topology::finalizer_trace_root_for_turn_with_scan_base;
use crate::hooks::langfuse::turn_topology::{
    accounted_realtime_child_span_ids, finalizer_generation_span_ids_for_turn_with_scan_base,
    persist_finalizer_topology_decision, resolve_finalizer_turn_topology_with_scan_base,
    FinalizerDecision,
};
use crate::hooks::langfuse::LangfuseConfig;
use crate::hooks::tool_context::tool_records;
use crate::hooks::{RetryPolicy, StopBudget};
use pico_args::Arguments;
use serde::Serialize;
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::io::{BufRead, Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::time::Duration;

// Must exceed the terminal wait budget plus export/checkpoint time so recovery
// cannot reclaim a live session while its worker still owns the lease.
const DEFAULT_STALE_AFTER_MS: u64 = 600_000;
const DEFAULT_HTTP_TIMEOUT_MS: u64 = 20_000;
const REALTIME_TOOL_HTTP_TIMEOUT_MS: u64 = 5_000;
const REALTIME_CHUNK_SAFE_START_BUDGET_MS: u64 = REALTIME_TOOL_HTTP_TIMEOUT_MS;
const REALTIME_CLAIM_HARD_CAP_MS: u64 = 20_000;
const DEFAULT_MAX_ATTEMPTS: u32 = 3;
const DEFAULT_TRANSCRIPT_TERMINAL_TIMEOUT_SECS: u64 = 30;
const REALTIME_GENERATION_DEFERRED: &str = "realtime_generation_deferred:";
const REALTIME_FLUSH_DEFERRED: &str = "realtime_flush_deferred:";
const REALTIME_FINALIZER_TOPOLOGY_DEFERRED: &str = "realtime_finalizer_topology_deferred:";
const REALTIME_TOOL_CORRELATION_SKEW_NANOS: u128 = 1_000_000_000;
const NATIVE_USAGE_PREFIX_REWIND_BLOCK_BYTES: u64 = 8 * 1024;
type MultiTurnGenerationSelection = (usize, usize, String);
type GenerationRoundRecord = (u64, String, String);

#[derive(Clone, Copy)]
struct GenerationScanCommit {
    scan_anchor: u64,
    next_offset: u64,
    scan_base_ordinal: u64,
    window_candidate_count: u64,
    window_terminal: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FlushReconcileOutcome {
    Completed,
    Progressed,
    Waiting,
    FinalizerOnly,
}

impl FlushReconcileOutcome {
    fn is_completed(self) -> bool {
        self == Self::Completed
    }

    fn deferred_reason(self) -> &'static str {
        match self {
            Self::Completed => "completed",
            Self::Progressed => "scan_progressed",
            Self::Waiting => "waiting_for_more_data",
            Self::FinalizerOnly => "finalizer_only",
        }
    }
}

struct PreparedGenerationWindow {
    entries: Vec<Value>,
    next_offset: u64,
    window_terminal: bool,
    discovery_progressed: bool,
}

enum GenerationWindowPreparation {
    Semantic(PreparedGenerationWindow),
    Waiting,
    FinalizerOnly(&'static str),
}

fn transcript_terminal_timeout() -> Duration {
    if cfg!(debug_assertions) {
        if let Ok(value) = std::env::var("CDXHOOKS_TEST_TRANSCRIPT_TERMINAL_TIMEOUT_MS") {
            if let Ok(milliseconds) = value.parse::<u64>() {
                return Duration::from_millis(milliseconds);
            }
        }
    }
    Duration::from_secs(DEFAULT_TRANSCRIPT_TERMINAL_TIMEOUT_SECS)
}

pub enum WorkerCommand {
    Once(StateWorkerOptions),
    Run(StateWorkerOptions),
    RecoverStale(StateWorkerOptions),
    Status(StateWorkerOptions),
}

struct WorkerOptions {
    spool_dir: PathBuf,
    profile: Option<String>,
    config_path: Option<String>,
    stale_after: Duration,
    http_timeout: Duration,
    max_attempts: u32,
    debug: bool,
}

pub struct StateWorkerOptions {
    pub session_id: Option<String>,
    pub limit: usize,
}

#[derive(Clone, Copy)]
enum StateWakeMode {
    Ordinary,
    RecoverStale,
}

const USAGE: &str = "Usage: cdxhooks worker (once|run|recover-stale|status) [OPTIONS]";

pub fn parse_args(mut pargs: Arguments) -> WorkerCommand {
    match pargs.subcommand().ok().flatten().as_deref() {
        Some("once") => WorkerCommand::Once(parse_state_worker_options(&mut pargs, 1)),
        Some("run") => WorkerCommand::Run(parse_state_worker_options(&mut pargs, usize::MAX)),
        Some("recover-stale") => {
            WorkerCommand::RecoverStale(parse_state_worker_options(&mut pargs, usize::MAX))
        }
        Some("status") => WorkerCommand::Status(parse_state_worker_options(&mut pargs, usize::MAX)),
        Some(unknown) => {
            eprintln!("Unknown worker command: {unknown}");
            eprintln!("{USAGE}");
            std::process::exit(1);
        }
        None => WorkerCommand::Once(parse_state_worker_options(&mut pargs, 1)),
    }
}

pub fn run(command: WorkerCommand) -> Result<(), String> {
    match command {
        WorkerCommand::Once(options) => {
            let scheduled = wake_pending_state(&options, StateWakeMode::Ordinary)?;
            println!("{}", serde_json::json!({ "scheduled": scheduled }));
            Ok(())
        }
        WorkerCommand::Run(options) => {
            let scheduled = wake_pending_state(&options, StateWakeMode::Ordinary)?;
            println!("{}", serde_json::json!({ "scheduled": scheduled }));
            Ok(())
        }
        WorkerCommand::RecoverStale(options) => {
            // State-native ownership has no lease to recover. Re-wake durable
            // facts/intents; the blocking session owner serializes delivery.
            let scheduled = wake_pending_state(&options, StateWakeMode::RecoverStale)?;
            println!(
                "{}",
                serde_json::json!({ "scheduled": scheduled, "stale_claims": 0 })
            );
            Ok(())
        }
        WorkerCommand::Status(options) => {
            let status = state_worker_status(options.session_id.as_deref())?;
            println!("{}", serde_json::json!({"status": status}));
            Ok(())
        }
    }
}

#[derive(Debug, Default, Serialize)]
struct StateWorkerStatus {
    sessions: usize,
    pending_queue_items: usize,
    dropped_queue_items: u64,
    worker_starting: usize,
    worker_spawn_failures: usize,
    pending_tool_facts: usize,
    pending_flushes: usize,
    pending_stops: usize,
    pending_subagent_recoveries: usize,
    terminal_quarantines: usize,
}

fn parse_state_worker_options(pargs: &mut Arguments, default_limit: usize) -> StateWorkerOptions {
    let session_id = pargs.opt_value_from_str("--session-id").ok().flatten();
    let limit = pargs
        .opt_value_from_str("--max-jobs")
        .ok()
        .flatten()
        .or_else(|| pargs.opt_value_from_str("--limit").ok().flatten())
        .unwrap_or(default_limit)
        .max(1);
    StateWorkerOptions { session_id, limit }
}

fn state_session_ids(filter: Option<&str>) -> Result<Vec<String>, String> {
    if let Some(session_id) = filter.map(str::trim).filter(|value| !value.is_empty()) {
        return Ok(vec![session_id.to_string()]);
    }
    let mut session_ids = Vec::new();
    let entries = match std::fs::read_dir(dirs::state_dir()) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(session_ids),
        Err(error) => return Err(format!("failed to scan state root: {error}")),
    };
    for entry in entries {
        let entry = entry.map_err(|error| format!("failed to read state entry: {error}"))?;
        if !entry.file_type().map(|kind| kind.is_dir()).unwrap_or(false)
            || entry.file_name() == "sessions"
        {
            continue;
        }
        let identity_path = entry.path().join("state_identity.json");
        let Some(session_id) = std::fs::read_to_string(identity_path)
            .ok()
            .and_then(|content| serde_json::from_str::<Value>(&content).ok())
            .and_then(|value| {
                value
                    .get("session_id")
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
        else {
            continue;
        };
        if !session_id.trim().is_empty() {
            session_ids.push(session_id);
        }
    }
    session_ids.sort();
    session_ids.dedup();
    Ok(session_ids)
}

fn session_pending_counts(
    session_id: &str,
) -> Result<(usize, u64, usize, usize, usize, usize, usize, usize, usize), String> {
    let queue_count = crate::worker::pending_count(session_id)?;
    let overflow_count = crate::worker::overflow_count(session_id)?;
    let wake_status = crate::worker::start_marker_status(session_id)
        .unwrap_or_else(|_| Some("invalid".to_string()));
    let starting_count = (wake_status.as_deref() == Some("starting")) as usize;
    let spawn_failure_count = wake_status
        .as_deref()
        .is_some_and(|status| status == "spawn_failed" || status == "invalid")
        as usize;
    let tool_count = crate::common::tool_event::pending_tool_ids(session_id, usize::MAX)?.len();
    let state = dirs::session_state_file(session_id)
        .map(|path| load_state(&path))
        .unwrap_or(Value::Null);
    let stop_count = live_stop_intent_payload(session_id, &state, false)?.is_some() as usize;
    let flush_count = realtime_state::pending_flush_jobs(&state, session_id).len();
    let subagent_count = subagent_recovery_records(&state, session_id)
        .into_iter()
        .filter(|record| record.has_ready_recovery_work())
        .count();
    let quarantine_count = get_session_state(&state, session_id)
        .map(terminal_quarantine_count)
        .unwrap_or(0);
    Ok((
        queue_count,
        overflow_count,
        starting_count,
        spawn_failure_count,
        tool_count,
        flush_count,
        stop_count,
        subagent_count,
        quarantine_count,
    ))
}

fn state_worker_status(filter: Option<&str>) -> Result<StateWorkerStatus, String> {
    let mut status = StateWorkerStatus::default();
    for session_id in state_session_ids(filter)? {
        let (
            queue,
            dropped,
            starting,
            spawn_failures,
            tools,
            flushes,
            stops,
            subagents,
            quarantines,
        ) = session_pending_counts(&session_id)?;
        let active =
            queue + starting + spawn_failures + tools + flushes + stops + subagents + quarantines
                > 0;
        if active {
            status.sessions = status.sessions.saturating_add(1);
            status.pending_queue_items = status.pending_queue_items.saturating_add(queue);
            status.worker_starting = status.worker_starting.saturating_add(starting);
            status.worker_spawn_failures =
                status.worker_spawn_failures.saturating_add(spawn_failures);
            status.pending_tool_facts = status.pending_tool_facts.saturating_add(tools);
            status.pending_flushes = status.pending_flushes.saturating_add(flushes);
            status.pending_stops = status.pending_stops.saturating_add(stops);
            status.pending_subagent_recoveries =
                status.pending_subagent_recoveries.saturating_add(subagents);
            status.terminal_quarantines = status.terminal_quarantines.saturating_add(quarantines);
        }
        status.dropped_queue_items = status.dropped_queue_items.saturating_add(dropped);
    }
    Ok(status)
}

fn live_stop_intent_payload(
    session_id: &str,
    state: &Value,
    clear_accounted: bool,
) -> Result<Option<Value>, String> {
    if let Some(path) = dirs::session_stop_intent_file(session_id) {
        if let Some(payload) = std::fs::read_to_string(&path)
            .ok()
            .and_then(|content| serde_json::from_str::<Value>(&content).ok())
            .filter(Value::is_object)
        {
            let event_is_stop = payload
                .get("hook_event_name")
                .and_then(Value::as_str)
                .is_some_and(|event| matches!(event, "Stop" | "SessionEnd"));
            let has_unaccounted_turn =
                !event_is_stop || stop_intent_has_unaccounted_turn(&payload, state, session_id)?;
            if has_unaccounted_turn {
                // A completed Stop already proved this topology cannot be
                // resolved from the current durable state. Replaying it ahead
                // of every ordinary hook only rescans the same transcript and
                // cannot add evidence. Keep the intent for the next explicit
                // Stop/SessionEnd, when later realtime facts may have arrived.
                let waits_for_explicit_stop =
                    clear_accounted && finalizer_waits_for_explicit_terminal(state, session_id);
                if waits_for_explicit_stop {
                    return Ok(None);
                }
                return Ok(Some(payload));
            }
            if clear_accounted {
                std::fs::remove_file(&path)
                    .map_err(|error| format!("remove terminally accounted Stop intent: {error}"))?;
            }
        }
    }
    Ok(None)
}

fn stop_intent_has_unaccounted_turn(
    payload: &Value,
    state: &Value,
    session_id: &str,
) -> Result<bool, String> {
    let Some(path) = payload.get("transcript_path").and_then(Value::as_str) else {
        return Ok(true);
    };
    let Some(session) = get_session_state(state, session_id) else {
        return Ok(true);
    };
    let transcript_path = Path::new(path);
    if !transcript_path.is_file() {
        return Ok(true);
    }

    // Recovery eligibility is a hot-path coordination check, not a semantic
    // replay. The Stop handler owns incremental parsing from `offset`; doing a
    // second parse from byte zero here made every normal hook O(transcript).
    // Treat the durable cursor/accounting boundary as authoritative and only
    // stat the source file to detect new bytes.
    if session
        .get("transcript_path")
        .and_then(Value::as_str)
        .is_some_and(|recorded| Path::new(recorded) != transcript_path)
    {
        return Ok(true);
    }

    let has_pending_state = session
        .get("buffer")
        .and_then(Value::as_str)
        .is_some_and(|buffer| !buffer.is_empty())
        || session
            .get("pending_messages")
            .and_then(Value::as_array)
            .is_some_and(|messages| !messages.is_empty())
        || session
            .get("pending_export_reason")
            .is_some_and(|value| !value.is_null())
        || session
            .get("pending_final_export")
            .is_some_and(|value| !value.is_null())
        || session
            .get("pending_window_end_offset")
            .is_some_and(|value| !value.is_null())
        || session
            .get("pending_subagent_ids")
            .and_then(Value::as_array)
            .is_some_and(|ids| !ids.is_empty());
    if has_pending_state {
        return Ok(true);
    }

    let committed_offset = get_int_field(session, "offset").max(0);
    let terminal_high_water = session
        .get("export_accounting")
        .and_then(|accounting| accounting.get("terminal_high_water_offset"))
        .and_then(Value::as_i64)
        .unwrap_or(0)
        .max(0);
    let durable_boundary = committed_offset.max(terminal_high_water) as u64;
    let file_len = std::fs::metadata(transcript_path)
        .map_err(|error| format!("stat Stop transcript '{}': {error}", path))?
        .len();

    // A shorter file means replacement/truncation. Do not clear the intent
    // based on offsets that belong to a different source epoch.
    if file_len < durable_boundary {
        return Ok(true);
    }

    // No-growth wait: skip if transcript unchanged since last recovery.
    let last_recovery_len = session
        .get("last_recovery_file_len")
        .and_then(Value::as_u64);
    if let Some(last_len) = last_recovery_len {
        if file_len <= last_len {
            return Ok(false);
        }
    }
    Ok(file_len > durable_boundary)
}

pub fn recovery_stop_intent_payload(session_id: &str) -> Result<Option<Value>, String> {
    let Some(state_file) = dirs::session_state_file(session_id) else {
        return Ok(None);
    };
    let mut state = load_state(&state_file);
    if stop_recovery_record(&state, session_id).is_some() {
        return match scheduled_stop_recovery_payload_from_state(session_id, &state)? {
            ScheduledStopRecovery::Eligible(payload) => Ok(Some(payload)),
            ScheduledStopRecovery::Idle | ScheduledStopRecovery::Waiting(_) => Ok(None),
        };
    }
    if finalizer_waits_for_explicit_terminal(&state, session_id) {
        return Ok(None);
    }
    let legacy_recovery_status = get_session_state(&state, session_id)
        .filter(|session| {
            session.get("pending_export_reason").and_then(Value::as_str)
                == Some(REALTIME_TOPOLOGY_AMBIGUOUS_REASON)
        })
        .and_then(|session| session.get("legacy_finalizer_recovery"))
        .and_then(|recovery| recovery.get("status"))
        .and_then(Value::as_str);

    if legacy_recovery_status != Some("awaiting_explicit_terminal") {
        if let Some(payload) = live_stop_intent_payload(session_id, &state, false)? {
            let transcript_path = payload
                .get("transcript_path")
                .and_then(Value::as_str)
                .unwrap_or_default();
            if !transcript_path.is_empty()
                && get_session_state(&state, session_id)
                    .and_then(|session| session.get("pending_export_reason"))
                    .and_then(Value::as_str)
                    == Some(REALTIME_TOPOLOGY_AMBIGUOUS_REASON)
            {
                let now = crate::common::logger::now_iso8601_pub();
                arm_legacy_finalizer_recovery(
                    &state_file,
                    &mut state,
                    session_id,
                    &now,
                    transcript_path,
                )?;
                return Ok(Some(payload));
            }
        }
    }

    live_stop_intent_payload(session_id, &state, true)
}

#[derive(Debug)]
pub enum ScheduledStopRecovery {
    Idle,
    Waiting(Duration),
    Eligible(Value),
}

fn scheduled_stop_recovery_payload_from_state(
    session_id: &str,
    state: &Value,
) -> Result<ScheduledStopRecovery, String> {
    match stop_recovery_eligibility(state, session_id) {
        StopRecoveryEligibility::Idle => Ok(ScheduledStopRecovery::Idle),
        StopRecoveryEligibility::Waiting(delay) => Ok(ScheduledStopRecovery::Waiting(delay)),
        StopRecoveryEligibility::Eligible => {
            let Some(record) = stop_recovery_record(state, session_id) else {
                return Ok(ScheduledStopRecovery::Idle);
            };
            let Some(payload) = live_stop_intent_payload(session_id, state, false)? else {
                return Ok(ScheduledStopRecovery::Idle);
            };
            let current_intent_id = payload.get("intent_id").and_then(Value::as_str);
            // A new Stop may replace the live intent after an earlier delivery
            // completed its POST but before its success checkpoint committed.
            // Keep ordinary recovery intent-scoped, but let a durable delivery
            // plan reach the handler, which atomically adopts it before replay.
            if current_intent_id != Some(record.intent_id.as_str())
                && record.delivery_plan_id.is_none()
            {
                return Ok(ScheduledStopRecovery::Idle);
            }
            Ok(ScheduledStopRecovery::Eligible(payload))
        }
    }
}

pub fn scheduled_stop_recovery_payload(session_id: &str) -> Result<ScheduledStopRecovery, String> {
    let Some(state_file) = dirs::session_state_file(session_id) else {
        return Ok(ScheduledStopRecovery::Idle);
    };
    let state = load_state(&state_file);
    scheduled_stop_recovery_payload_from_state(session_id, &state)
}

#[derive(Debug)]
pub enum ScheduledSubagentRecovery {
    Idle,
    Waiting(Duration),
    Eligible(RecoveryRecord),
}

pub fn scheduled_subagent_recovery(session_id: &str) -> Result<ScheduledSubagentRecovery, String> {
    let Some(state_file) = dirs::session_state_file(session_id) else {
        return Ok(ScheduledSubagentRecovery::Idle);
    };
    let mut state = load_state(&state_file);
    Ok(match due(&state_file, &mut state, session_id)? {
        RecoverySchedule::Idle => ScheduledSubagentRecovery::Idle,
        RecoverySchedule::Waiting(delay) => ScheduledSubagentRecovery::Waiting(delay),
        RecoverySchedule::Ready(record) => ScheduledSubagentRecovery::Eligible(record),
    })
}

fn recovery_payload(session_id: &str) -> Result<Option<Value>, String> {
    if crate::worker::handoff_intent_exists(session_id)? {
        let _ = crate::worker::clear_handoff_intent(session_id);
        return Ok(Some(serde_json::json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "recovery_source": "handoff_intent",
        })));
    }
    if let Some(payload) = recovery_stop_intent_payload(session_id)? {
        record_recovery_file_len(session_id, &payload);
        return Ok(Some(payload));
    }
    if let Some(payload) = crate::common::tool_event::first_ready_payload(session_id)? {
        return Ok(Some(payload));
    }
    let (_, _, _, _, _, pending_flushes, _, _, _) = session_pending_counts(session_id)?;
    Ok((pending_flushes > 0).then(|| {
        serde_json::json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "recovery_source": "state_worker",
        })
    }))
}

pub fn run_subagent_recovery(session_id: &str, record: &RecoveryRecord) -> Result<bool, String> {
    let state_file = session_state_file(session_id)?;
    let log_file = dirs::session_log_file(session_id)
        .ok_or_else(|| "subagent recovery requires session log path".to_string())?;
    if let Some(parent) = log_file.parent() {
        dirs::ensure_private_dir(parent)
            .map_err(|error| format!("failed to create session log directory: {error}"))?;
    }
    let logger = Logger::new(
        log_file,
        crate::common::hook::env_is_truthy("CDXHOOKS_DEBUG"),
    )
    .with_event("subagent-recovery");
    let delivery_context = record.delivery_context.as_ref().ok_or_else(|| {
        format!(
            "subagent recovery missing delivery context session={session_id} turn_id={}",
            record.parent_turn_id
        )
    })?;
    let retry_policy = RetryPolicy::parse(&delivery_context.retry_policy).ok_or_else(|| {
        format!(
            "invalid subagent recovery retry policy: {}",
            delivery_context.retry_policy
        )
    })?;
    crate::common::env::with_worker_env(&delivery_context.worker_env, || {
        let config = resolve_langfuse_config_for_recovery(delivery_context)?;
        if !config_matches_delivery_context(&config, delivery_context) {
            return Err(format!(
                "subagent recovery delivery context changed session={session_id} turn_id={} source={}",
                record.parent_turn_id, delivery_context.config_source
            ));
        }
        run_subagent_recovery_with_context(
            session_id,
            record,
            &state_file,
            &config,
            &logger,
            retry_policy,
        )
    })
}

pub(crate) fn run_subagent_recovery_with_context(
    session_id: &str,
    record: &RecoveryRecord,
    state_file: &Path,
    config: &LangfuseConfig,
    logger: &Logger,
    retry_policy: RetryPolicy,
) -> Result<bool, String> {
    let transcript_path = Path::new(&record.parent_transcript_path);
    let (messages, _, _) = read_new_jsonl(transcript_path, 0, "");
    if messages.is_empty() {
        logger.debug(&format!(
            "Subagent recovery parent transcript is empty session={session_id} turn_id={}",
            record.parent_turn_id
        ));
        return Ok(false);
    }
    let turns = build_turns(&messages);
    let Some(turn_index) = turns
        .iter()
        .position(|turn| turn.turn_id == record.parent_turn_id)
    else {
        logger.debug(&format!(
            "Subagent recovery parent turn missing session={session_id} turn_id={}",
            record.parent_turn_id
        ));
        return Ok(false);
    };
    let turn = &turns[turn_index];
    let state = load_state(state_file);
    let mut trace_opts = build_turn_trace_opts(&state, session_id, &turn.turn_id);
    {
        let opts = trace_opts.get_or_insert_with(TurnReplayOpts::default);
        opts.trace_id = Some(record.trace_id.clone());
        opts.root_span_id = Some(record.root_span_id.clone());
    }
    let mut body = build_otel_export_request_for_turn(
        turn,
        turn_index + 1,
        session_id,
        &record.username,
        &record.version,
        trace_opts.as_ref(),
    );
    let vcs_info = get_vcs_for_cwd(
        &state,
        session_id,
        (!turn.cwd.trim().is_empty()).then_some(turn.cwd.as_str()),
    );
    if let Some(vcs) = vcs_info.as_ref() {
        inject_vcs_resource_attrs(&mut body, &vcs.remote_url, &vcs.branch);
    }
    let transcript_dir = transcript_path.parent().unwrap_or_else(|| Path::new("."));
    let sessions_dir = dirs::sessions_dir();
    let sessions_dir_text = sessions_dir.to_string_lossy();
    let merge_result = match merge_child_sessions_into_body(
        &mut body,
        &turns,
        &turns,
        turn_index,
        session_id,
        transcript_dir,
        &record.parent_transcript_path,
        Some(sessions_dir_text.as_ref()),
        &record.username,
        &record.version,
        logger,
        None,
    ) {
        ChildMergeOutcome::Completed(result) => result,
        ChildMergeOutcome::BudgetExhausted => {
            return Err("subagent recovery merge budget exhausted".to_string())
        }
    };
    let parent_suppressed = suppress_parent_conversation_spans(&mut body, session_id);
    if parent_suppressed > 0 {
        logger.info(&format!(
            "Subagent recovery removed {parent_suppressed} parent span(s) session={session_id} turn_id={}",
            record.parent_turn_id
        ));
    }
    // Re-read once after the merge, before suppressing already-accounted spans.
    let mut state = load_state(state_file);
    let latest_record = crate::hooks::langfuse::subagent_progress::record(
        &state,
        session_id,
        &record.parent_turn_id,
    )
    .unwrap_or_else(|| record.clone());
    let suppressed = suppress_accounted(&mut body, &latest_record);
    if suppressed > 0 {
        logger.info(&format!(
            "Subagent recovery suppressed {suppressed} accounted span(s) session={session_id} turn_id={}",
            record.parent_turn_id
        ));
    }
    let span_count = count_export_spans(&body);
    let delivered = if span_count == 0 {
        account(
            state_file,
            &mut state,
            session_id,
            &record.parent_turn_id,
            &body,
            &merge_result.deferred_sync_ids,
        )
        .map_err(|error| format!("account empty subagent recovery: {error}"))?;
        true
    } else {
        deliver_subagent_delta_chunks(
            config,
            logger,
            retry_policy,
            state_file,
            &mut state,
            session_id,
            record,
            &body,
            &merge_result.deferred_sync_ids,
        )?
    };
    if delivered && merge_result.deferred_sync_ids.is_empty() {
        checkpoint_posted_parent_subagent_history(
            state_file,
            &mut state,
            session_id,
            &record.parent_turn_id,
            &record.parent_transcript_path,
            &merge_result.merged_sync_ids,
            &merge_result.merged_subagent_invocation_ids,
        )?;
    }
    logger.info(&format!(
        "Subagent recovery completed slice session={session_id} turn_id={} spans={} pending={}",
        record.parent_turn_id,
        span_count,
        merge_result.deferred_sync_ids.len()
    ));
    Ok(delivered && (span_count > 0 || merge_result.deferred_sync_ids.is_empty()))
}

#[allow(clippy::too_many_arguments)]
fn deliver_subagent_delta_chunks(
    config: &LangfuseConfig,
    logger: &Logger,
    retry_policy: RetryPolicy,
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    record: &RecoveryRecord,
    body: &Value,
    pending_child_ids: &[String],
) -> Result<bool, String> {
    let identities = span_identities(body);
    let children = extract_realtime_semantic_children(body, &identities)?;
    let plan = plan_realtime_payload_chunks(children, REALTIME_TARGET_SERIALIZED_BODY_BYTES)?;
    if let Some(oversized) = plan.oversized_children.first() {
        return Err(format!(
            "subagent_recovery_span_oversize: span_id={} child_id={}",
            oversized.span_id, oversized.child_id
        ));
    }
    let chunks = plan.chunks;
    for (index, chunk) in chunks.iter().enumerate() {
        let is_last = index + 1 == chunks.len();
        if retry_policy == RetryPolicy::AtMostOnce {
            account_chunk(
                state_file,
                state,
                session_id,
                &record.parent_turn_id,
                &chunk.body,
                pending_child_ids,
                !is_last,
            )
            .map_err(|error| format!("account at-most-once subagent recovery chunk: {error}"))?;
        }
        match post_otel_to_langfuse_once(
            config,
            &chunk.body,
            logger,
            Duration::from_millis(DEFAULT_HTTP_TIMEOUT_MS),
        ) {
            Ok(()) => {}
            Err(error) if retry_policy == RetryPolicy::AtMostOnce => {
                logger.warn(&format!(
                    "AtMostOnce subagent recovery chunk dropped after account session={session_id} turn_id={} cursor={} error={error}",
                    record.parent_turn_id,
                    chunk.cursor
                ));
                return Ok(false);
            }
            Err(error) => return Err(error),
        }
        if retry_policy == RetryPolicy::AtLeastOnce {
            account_chunk(
                state_file,
                state,
                session_id,
                &record.parent_turn_id,
                &chunk.body,
                pending_child_ids,
                !is_last,
            )
            .map_err(|error| format!("account subagent recovery chunk success: {error}"))?;
        }
    }
    Ok(true)
}

fn checkpoint_posted_parent_subagent_history(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    turn_id: &str,
    transcript_path: &str,
    merged_subagent_ids: &[String],
    merged_subagent_invocation_ids: &[String],
) -> Result<(), String> {
    let session = state.get(session_id).cloned().unwrap_or(Value::Null);
    let posted_turn_ids = get_string_array_field(&session, "posted_turn_ids");
    if !posted_turn_ids.iter().any(|posted| posted == turn_id) {
        return Ok(());
    }
    let turn_count = get_int_field(&session, "turn_count").max(0) as usize;
    let now = crate::common::logger::now_iso8601_pub();
    checkpoint_turn_export_success(
        state_file,
        state,
        session_id,
        turn_id,
        &now,
        Some(transcript_path),
        merged_subagent_ids,
        merged_subagent_invocation_ids,
    )
    .map(|_| ())
    .or_else(|error| {
        let refreshed = load_state(state_file);
        let refreshed_session = refreshed.get(session_id).cloned().unwrap_or(Value::Null);
        let still_posted = get_string_array_field(&refreshed_session, "posted_turn_ids")
            .iter()
            .any(|posted| posted == turn_id);
        if !still_posted {
            Ok(())
        } else {
            Err(format!(
                "checkpoint posted parent subagent history turn_count={turn_count}: {error}"
            ))
        }
    })
}

fn span_identities(body: &Value) -> Vec<(String, String)> {
    body.get("resourceSpans")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .flat_map(|resource| {
            resource
                .get("scopeSpans")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .flat_map(|scope| {
            scope
                .get("spans")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .filter_map(|span| {
            let span_id = span.get("spanId").and_then(Value::as_str)?;
            Some((format!("subagent:{span_id}"), span_id.to_string()))
        })
        .collect()
}

fn suppress_parent_conversation_spans(body: &mut Value, parent_session_id: &str) -> usize {
    let mut removed = 0usize;
    for resource in body
        .get_mut("resourceSpans")
        .and_then(Value::as_array_mut)
        .into_iter()
        .flatten()
    {
        for scope in resource
            .get_mut("scopeSpans")
            .and_then(Value::as_array_mut)
            .into_iter()
            .flatten()
        {
            let Some(spans) = scope.get_mut("spans").and_then(Value::as_array_mut) else {
                continue;
            };
            let before = spans.len();
            spans.retain(|span| {
                !span
                    .get("attributes")
                    .and_then(Value::as_array)
                    .is_some_and(|attrs| {
                        otel_attr_string(attrs, "gen_ai.conversation.id").as_deref()
                            == Some(parent_session_id)
                    })
            });
            removed = removed.saturating_add(before.saturating_sub(spans.len()));
        }
    }
    removed
}

fn otel_attr_string(attrs: &[Value], key: &str) -> Option<String> {
    attrs.iter().find_map(|attr| {
        if attr.get("key").and_then(Value::as_str) == Some(key) {
            attr.get("value")
                .and_then(|value| value.get("stringValue"))
                .and_then(Value::as_str)
                .map(str::to_string)
        } else {
            None
        }
    })
}

fn count_export_spans(body: &Value) -> usize {
    body.get("resourceSpans")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .flat_map(|resource| {
            resource
                .get("scopeSpans")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .map(|scope| {
            scope
                .get("spans")
                .and_then(Value::as_array)
                .map(Vec::len)
                .unwrap_or(0)
        })
        .sum()
}

fn wake_pending_state(options: &StateWorkerOptions, mode: StateWakeMode) -> Result<usize, String> {
    let mut scheduled = 0usize;
    for session_id in state_session_ids(options.session_id.as_deref())? {
        if scheduled >= options.limit {
            break;
        }
        let queued = crate::worker::pending_count(&session_id)?;
        let payload = if queued == 0 {
            recovery_payload(&session_id)?
        } else {
            None
        };
        let subagent_state_file = dirs::session_state_file(&session_id);
        let has_subagent_recovery = if queued == 0 && payload.is_none() {
            let state = subagent_state_file
                .as_ref()
                .map(|path| load_state(path))
                .unwrap_or(Value::Null);
            subagent_recovery_records(&state, &session_id)
                .iter()
                .any(|record| record.has_recovery_work())
        } else {
            false
        };
        if queued == 0 && payload.is_none() && !has_subagent_recovery {
            continue;
        }
        let log_file = dirs::session_log_file(&session_id)
            .ok_or_else(|| format!("invalid state session id: {session_id}"))?;
        if let Some(parent) = log_file.parent() {
            dirs::ensure_private_dir(parent)
                .map_err(|error| format!("failed to create state worker log dir: {error}"))?;
        }
        let logger = Logger::new(
            log_file,
            crate::common::hook::env_is_truthy("CDXHOOKS_DEBUG"),
        )
        .with_event("state-worker");
        if has_subagent_recovery && matches!(mode, StateWakeMode::RecoverStale) {
            if let Some(state_file) = subagent_state_file.as_ref() {
                let mut state = load_state(state_file);
                wake_subagent_recovery(state_file, &mut state, &session_id)?;
            }
        }
        if queued > 0 {
            crate::worker::wake_session(&session_id, &logger)?;
        } else if let Some(payload) = payload {
            let envelope = serde_json::to_vec(&payload).map_err(|error| error.to_string())?;
            crate::worker::spawn_detached_raw(&envelope, &logger)?;
        } else {
            crate::worker::wake_session(&session_id, &logger)?;
        }
        scheduled = scheduled.saturating_add(1);
    }
    Ok(scheduled)
}

/// Consume upgrade leftovers while the detached worker already owns the
/// session. New hook paths never publish here. A completed marker is a hint,
/// not a blind skip: late jobs from an older binary are still detected.
pub fn import_legacy_spool_once(session_id: &str, max_jobs: usize) -> Result<usize, String> {
    if session_id.trim().is_empty() {
        return Err("legacy spool import requires session id".to_string());
    }
    let session_spool = session_spool_root(&spool::default_root(), session_id)?;
    if !session_spool.is_dir() {
        if !migration_complete(session_id) {
            write_migration_marker(session_id, 0, 0)?;
        }
        return Ok(0);
    }
    spool::recover_stale(
        &session_spool,
        Duration::from_millis(DEFAULT_STALE_AFTER_MS),
    )?;
    let before = spool::status(&session_spool)?;
    if before.pending == 0 && before.claimed == 0 {
        write_migration_marker(session_id, before.done, before.failed)?;
        cleanup_imported_spool_root(&session_spool)?;
        return Ok(0);
    }
    let options = WorkerOptions {
        spool_dir: spool::default_root(),
        profile: None,
        config_path: None,
        stale_after: Duration::from_millis(DEFAULT_STALE_AFTER_MS),
        http_timeout: Duration::from_millis(DEFAULT_HTTP_TIMEOUT_MS),
        max_attempts: DEFAULT_MAX_ATTEMPTS,
        debug: crate::common::hook::env_is_truthy("CDXHOOKS_DEBUG"),
    };
    let mut deferred = HashSet::new();
    let mut processed = 0usize;
    while processed < max_jobs && process_once_session(&options, session_id, &mut deferred)? {
        processed = processed.saturating_add(1);
    }
    let after = spool::status(&session_spool)?;
    if after.pending == 0 && after.claimed == 0 {
        write_migration_marker(session_id, after.done, after.failed)?;
        cleanup_imported_spool_root(&session_spool)?;
    }
    Ok(processed)
}

fn migration_complete(session_id: &str) -> bool {
    dirs::session_migration_file(session_id).is_some_and(|path| {
        std::fs::read_to_string(path)
            .ok()
            .and_then(|content| serde_json::from_str::<Value>(&content).ok())
            .and_then(|value| {
                value
                    .get("legacy_spool_import")
                    .and_then(Value::as_str)
                    .map(|status| status == "complete")
            })
            .unwrap_or(false)
    })
}

fn cleanup_imported_spool_root(session_spool: &Path) -> Result<(), String> {
    match std::fs::remove_dir_all(session_spool) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!(
            "failed to remove imported legacy spool '{}': {error}",
            session_spool.display()
        )),
    }
}

fn write_migration_marker(session_id: &str, done: usize, failed: usize) -> Result<(), String> {
    let path = dirs::session_migration_file(session_id)
        .ok_or("legacy spool import requires a valid session id")?;
    crate::common::runtime_state::write_json_atomically(
        &path,
        &serde_json::json!({
            "schema_version": 1,
            "legacy_spool_import": "complete",
            "done_jobs": done,
            "failed_jobs": failed,
            "completed_at_unix_nano": agentic_otel_common::now_nanos(),
        }),
    )
}

/// Reconcile one in-memory Flush trigger while the detached worker owns the
/// session. Neither the trigger nor the resulting Batch is written into the
/// spool; the Batch ledger and retry trigger stay in domain state.
fn process_realtime_flush_inline_with_outcome(
    job: &spool::SpoolJob,
    state_file: &Path,
    logger: &Logger,
    config: &LangfuseConfig,
    budget: Option<StopBudget>,
) -> Result<FlushReconcileOutcome, String> {
    if job.job_kind != spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Flush) {
        return Err("state-native realtime reconciliation requires a Flush trigger".to_string());
    }
    let outcome = process_realtime_flush(job, state_file, logger, config, budget)?;
    if !outcome.is_completed() {
        logger.debug(&format!(
            "state-native realtime Flush retained session={} job={} outcome={}",
            job.session_id,
            job.job_id,
            outcome.deferred_reason()
        ));
        return Ok(outcome);
    }
    timed_checkpoint(logger, "flush_success", || {
        checkpoint_realtime_delivery_success(state_file, job)
    })?;
    timed_checkpoint(logger, "flush_cleanup", || {
        update_realtime_state(state_file, |state| {
            realtime_state::remove_pending_flush_in_state(state, &job.session_id, &job.job_id)
        })
    })?;
    Ok(outcome)
}

#[cfg(test)]
pub fn process_pending_realtime_flushes(
    session_id: &str,
    state_file: &Path,
    logger: &Logger,
    config: Option<&LangfuseConfig>,
    limit: usize,
) -> Result<usize, String> {
    process_pending_realtime_flushes_with_budget(
        session_id, state_file, logger, config, limit, None,
    )
}

pub fn process_pending_realtime_flushes_with_budget(
    session_id: &str,
    state_file: &Path,
    logger: &Logger,
    config: Option<&LangfuseConfig>,
    limit: usize,
    budget: Option<StopBudget>,
) -> Result<usize, String> {
    let jobs = realtime_state::pending_flush_jobs(&load_state(state_file), session_id);
    if jobs.is_empty() {
        return Ok(0);
    }
    let config = config.ok_or("state-native realtime Flush requires Langfuse configuration")?;
    let mut processed = 0usize;
    for job in jobs.into_iter().take(limit) {
        let outcome =
            process_realtime_flush_inline_with_outcome(&job, state_file, logger, config, budget)?;
        if outcome.is_completed() {
            processed = processed.saturating_add(1);
        } else {
            break;
        }
    }
    Ok(processed)
}

pub fn finalize_pending_realtime_flushes(
    session_id: &str,
    state_file: &Path,
) -> Result<usize, String> {
    let mut removed = 0usize;
    update_realtime_state(state_file, |state| {
        let finalized_boundary = finalized_transcript_boundary(state, session_id);
        removed = realtime_state::finalize_pending_flushes_in_state(
            state,
            session_id,
            finalized_boundary,
        );
        Ok(())
    })?;
    Ok(removed)
}

fn finalized_transcript_boundary(state: &Value, session_id: &str) -> u64 {
    state
        .get(session_id)
        .and_then(Value::as_object)
        .map(|session| {
            let offset = session
                .get("offset")
                .and_then(Value::as_i64)
                .unwrap_or_default()
                .max(0) as u64;
            let buffered_bytes = session
                .get("buffer")
                .and_then(Value::as_str)
                .map(|buffer| buffer.len() as u64)
                .unwrap_or_default();
            offset.saturating_sub(buffered_bytes)
        })
        .unwrap_or_default()
}

fn session_spool_root(spool_dir: &Path, session_id: &str) -> Result<PathBuf, String> {
    spool::session_root(spool_dir, session_id).ok_or_else(|| {
        "worker session id is empty; refusing cross-session spool access".to_string()
    })
}

fn session_state_file(session_id: &str) -> Result<PathBuf, String> {
    dirs::session_state_file(session_id).ok_or_else(|| {
        "worker session id is empty; refusing cross-session state access".to_string()
    })
}

fn prepare_generation_window(
    session_id: &str,
    state_file: &Path,
    transcript_path: &Path,
    committed_offset: u64,
    logger: &Logger,
) -> Result<GenerationWindowPreparation, String> {
    let state = load_state(state_file);
    let discovery_offset =
        realtime_state::generation_discovery_offset(&state, session_id).max(committed_offset);
    let existing_epoch = realtime_state::generation_source_epoch(&state, session_id);
    if realtime_state::generation_sticky_terminal(&state, session_id).is_some() {
        logger.debug(&format!(
            "generation scan sticky terminal suppressed session={session_id} committed={committed_offset} discovery={discovery_offset}"
        ));
        return Ok(GenerationWindowPreparation::FinalizerOnly(
            "sticky_terminal",
        ));
    }
    let prefix_len_hint = existing_epoch.as_ref().map(|epoch| epoch.prefix_len);
    let snapshot = match inspect_transcript_source(transcript_path, prefix_len_hint) {
        Ok(snapshot) => snapshot,
        Err(error) if error == "transcript_replaced_or_truncated" => {
            let Some(epoch) = existing_epoch.as_ref() else {
                return Err(error);
            };
            update_realtime_state(state_file, |state| {
                realtime_state::mark_generation_scan_terminal_in_state(
                    state,
                    session_id,
                    epoch,
                    discovery_offset,
                    "transcript_replaced_or_truncated",
                    std::fs::metadata(transcript_path)
                        .map(|metadata| metadata.len())
                        .unwrap_or(0),
                )
            })?;
            return Ok(GenerationWindowPreparation::FinalizerOnly(
                "transcript_replaced_or_truncated",
            ));
        }
        Err(error) => return Err(error),
    };

    if existing_epoch
        .as_ref()
        .is_some_and(|epoch| epoch != &snapshot.epoch)
        || snapshot.file_len < discovery_offset
    {
        let terminal_epoch = existing_epoch.as_ref().unwrap_or(&snapshot.epoch);
        update_realtime_state(state_file, |state| {
            realtime_state::mark_generation_scan_terminal_in_state(
                state,
                session_id,
                terminal_epoch,
                discovery_offset,
                "transcript_replaced_or_truncated",
                snapshot.file_len,
            )
        })?;
        return Ok(GenerationWindowPreparation::FinalizerOnly(
            "transcript_replaced_or_truncated",
        ));
    }
    if existing_epoch.is_none() {
        update_realtime_state(state_file, |state| {
            realtime_state::checkpoint_generation_discovery_in_state(
                state,
                session_id,
                committed_offset,
                discovery_offset,
                snapshot.file_len,
                &snapshot.epoch,
                "initialized",
            )
        })?;
    }

    let scan_started = std::time::Instant::now();
    let scan = read_bounded_transcript_window(transcript_path, discovery_offset)?;
    let (next_offset, scan_outcome, discovery_progressed, scanned_records) = match scan {
        BoundedScanOutcome::Ready {
            entries,
            next_offset,
        } => (
            next_offset,
            "progressed",
            next_offset > discovery_offset,
            entries.len(),
        ),
        BoundedScanOutcome::CaughtUp { next_offset } => (next_offset, "caught_up", false, 0),
        BoundedScanOutcome::WaitingForMoreData { record_start } => {
            (record_start, "waiting_for_more_data", false, 0)
        }
        BoundedScanOutcome::FinalizerOnly {
            reason,
            record_start,
            observed_bytes,
        } => {
            update_realtime_state(state_file, |state| {
                realtime_state::mark_generation_scan_terminal_in_state(
                    state,
                    session_id,
                    &snapshot.epoch,
                    record_start,
                    reason,
                    observed_bytes,
                )
            })?;
            logger.warn(
                &serde_json::json!({
                    "kind": "cdxhooks.generation_scan",
                    "outcome": "finalizer_only",
                    "session": dirs::session_key(session_id).unwrap_or_else(|| "invalid-session".to_string()),
                    "reason": reason,
                    "record_start": record_start,
                    "observed_bytes": observed_bytes,
                })
                .to_string(),
            );
            return Ok(GenerationWindowPreparation::FinalizerOnly(reason));
        }
        BoundedScanOutcome::Deferred { reason } => {
            logger.debug(&format!(
                "generation scan local yield session={session_id} reason={reason} committed={committed_offset} discovery={discovery_offset}"
            ));
            return Ok(GenerationWindowPreparation::Waiting);
        }
    };

    let verified = inspect_transcript_source(transcript_path, Some(snapshot.epoch.prefix_len))?;
    let observed_file_len = verified.file_len;
    if verified.epoch != snapshot.epoch || observed_file_len < next_offset {
        update_realtime_state(state_file, |state| {
            realtime_state::mark_generation_scan_terminal_in_state(
                state,
                session_id,
                &snapshot.epoch,
                discovery_offset,
                "transcript_replaced_or_truncated",
                observed_file_len,
            )
        })?;
        return Ok(GenerationWindowPreparation::FinalizerOnly(
            "transcript_replaced_or_truncated",
        ));
    }
    update_realtime_state(state_file, |state| {
        realtime_state::checkpoint_generation_discovery_in_state(
            state,
            session_id,
            committed_offset,
            next_offset,
            observed_file_len,
            &snapshot.epoch,
            scan_outcome,
        )
    })?;
    logger.info(
        &serde_json::json!({
            "kind": "cdxhooks.generation_scan",
            "outcome": scan_outcome,
            "session": dirs::session_key(session_id).unwrap_or_else(|| "invalid-session".to_string()),
            "committed_offset": committed_offset,
            "discovery_start": discovery_offset,
            "discovery_next": next_offset,
            "file_len": observed_file_len,
            "bytes": next_offset.saturating_sub(discovery_offset),
            "records": scanned_records,
            "backlog_bytes": observed_file_len.saturating_sub(next_offset),
            "elapsed_ms": scan_started.elapsed().as_millis().min(u64::MAX as u128) as u64,
            "execution": "async",
        })
        .to_string(),
    );

    if next_offset <= committed_offset {
        return Ok(GenerationWindowPreparation::Waiting);
    }
    let entries =
        match read_complete_transcript_range(transcript_path, committed_offset, next_offset) {
            Ok(entries) => entries,
            Err(error) if error == "semantic_window_too_large" => {
                update_realtime_state(state_file, |state| {
                    realtime_state::mark_generation_scan_terminal_in_state(
                        state,
                        session_id,
                        &snapshot.epoch,
                        committed_offset,
                        "semantic_window_too_large",
                        next_offset.saturating_sub(committed_offset),
                    )
                })?;
                return Ok(GenerationWindowPreparation::FinalizerOnly(
                    "semantic_window_too_large",
                ));
            }
            Err(error) => return Err(error),
        };
    let (_, pending) = split_completed_messages(&entries);
    Ok(GenerationWindowPreparation::Semantic(
        PreparedGenerationWindow {
            entries,
            next_offset,
            window_terminal: next_offset == observed_file_len && pending.is_empty(),
            discovery_progressed,
        },
    ))
}

fn record_recovery_file_len(session_id: &str, payload: &Value) {
    let transcript_path = payload
        .get("transcript_path")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if transcript_path.is_empty() {
        return;
    }
    let file_len = match std::fs::metadata(transcript_path) {
        Ok(metadata) => metadata.len(),
        Err(_) => return,
    };
    let Some(state_file) = dirs::session_state_file(session_id) else {
        return;
    };
    let mut state = load_state(&state_file);
    let _ = update_state_transaction(&state_file, &mut state, |candidate| {
        if let Some(session) = candidate.get_mut(session_id).and_then(Value::as_object_mut) {
            session.insert(
                "last_recovery_file_len".to_string(),
                serde_json::json!(file_len),
            );
        }
    });
}

pub fn clear_recovery_file_len(session_id: &str) {
    let Some(state_file) = dirs::session_state_file(session_id) else {
        return;
    };
    let mut state = load_state(&state_file);
    let _ = update_state_transaction(&state_file, &mut state, |candidate| {
        if let Some(session) = candidate.get_mut(session_id).and_then(Value::as_object_mut) {
            session.remove("last_recovery_file_len");
        }
    });
}

// Production reaches `process_once_session` exclusively through the bounded
// `import_legacy_spool_once` bridge above.
#[cfg(test)]
fn worker_session_ids(spool_dir: &Path) -> Result<Vec<String>, String> {
    let mut ids = HashSet::new();
    for session_root in spool::session_roots(spool_dir)? {
        for state_dir in ["pending", "claimed", "failed"] {
            for job_path in read_sorted_json_files(&session_root.join(state_dir))? {
                let Ok(content) = std::fs::read_to_string(&job_path) else {
                    continue;
                };
                let Ok(job) = serde_json::from_str::<spool::SpoolJob>(&content) else {
                    continue;
                };
                if spool::session_root(spool_dir, &job.session_id).as_deref()
                    == Some(session_root.as_path())
                {
                    ids.insert(job.session_id);
                } else if state_dir == "pending" {
                    let failed_dir = session_root.join("failed");
                    dirs::ensure_private_dir(&failed_dir).map_err(|error| error.to_string())?;
                    let file_name = job_path
                        .file_name()
                        .ok_or("mismatched pending job has no filename")?;
                    std::fs::rename(&job_path, failed_dir.join(file_name))
                        .map_err(|error| error.to_string())?;
                }
            }
        }
    }
    let mut ids = ids.into_iter().collect::<Vec<_>>();
    ids.sort();
    Ok(ids)
}

#[cfg(test)]
fn read_sorted_json_files(path: &Path) -> Result<Vec<PathBuf>, String> {
    let entries = match std::fs::read_dir(path) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error.to_string()),
    };
    let mut files = entries
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_file()))
        .map(|entry| entry.path())
        .filter(|path| path.extension().and_then(std::ffi::OsStr::to_str) == Some("json"))
        .collect::<Vec<_>>();
    files.sort();
    Ok(files)
}

#[cfg(test)]
fn process_once(options: &WorkerOptions) -> Result<bool, String> {
    process_once_excluding_deferred(options, &mut HashSet::new())
}

#[cfg(test)]
fn process_once_excluding_deferred(
    options: &WorkerOptions,
    deferred_jobs: &mut HashSet<String>,
) -> Result<bool, String> {
    if let Ok(session_id) = std::env::var("CDXHOOKS_WORKER_SESSION_ID") {
        return process_once_session(options, &session_id, deferred_jobs);
    }
    for session_id in worker_session_ids(&options.spool_dir)? {
        if process_once_session(options, &session_id, deferred_jobs)? {
            return Ok(true);
        }
    }
    Ok(false)
}

fn process_once_session(
    options: &WorkerOptions,
    session_id: &str,
    deferred_jobs: &mut HashSet<String>,
) -> Result<bool, String> {
    let state_file = session_state_file(session_id)?;
    let session_spool = session_spool_root(&options.spool_dir, session_id)?;
    let state_snapshot = load_state(&state_file);
    let now_ms = unix_millis();
    spool::settle_pending_jobs(
        &session_spool,
        options.stale_after,
        "unregistered realtime orphan",
        |job, stale| {
            if job.session_id != session_id {
                spool::PendingDisposition::Failed
            } else {
                realtime_pending_disposition(&state_snapshot, job, stale)
            }
        },
    )?;
    reconcile_pending_realtime_jobs(&state_file, &session_spool, options.stale_after)?;
    let state_snapshot = load_state(&state_file);
    let Some(claimed) = spool::claim_next_eligible(&session_spool, options.stale_after, |job| {
        if job.session_id != session_id {
            return false;
        }
        match job.job_kind {
            spool::SpoolJobKind::Legacy => true,
            spool::SpoolJobKind::Realtime(_) => {
                !deferred_jobs.contains(&job.job_id)
                    && realtime_job_schema_identity_matches(&state_snapshot, job)
                    && job.payload_digest.as_deref().is_some_and(|digest| {
                        realtime_state::registered_realtime_job_claimable(
                            &state_snapshot,
                            &job.session_id,
                            &job.job_id,
                            digest,
                            now_ms,
                        )
                    })
            }
        }
    })?
    else {
        return Ok(false);
    };
    if claimed.job.session_id != session_id {
        let actual = claimed.job.session_id.clone();
        spool::fail(claimed, "cross-session spool job rejected", false)?;
        return Err(format!(
            "claimed job session mismatch: expected {session_id}, got {actual}"
        ));
    }
    let log_file = dirs::session_log_file(&claimed.job.session_id)
        .ok_or_else(|| "worker claimed job without session log path".to_string())?;
    if let Some(parent) = log_file.parent() {
        dirs::ensure_private_dir(parent)
            .map_err(|error| format!("failed to create session log directory: {error}"))?;
    }
    let logger = Logger::new(log_file, options.debug).with_event("worker");
    let langfuse_config =
        resolve_langfuse_config(options.config_path.as_deref(), options.profile.as_deref())?;

    let retry_policy = realtime_job_retry_policy(&state_snapshot, &claimed.job);
    let realtime_tool_job =
        claimed.job.job_kind == spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Tool);
    let realtime_batch_job =
        claimed.job.job_kind == spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Batch);
    let realtime_flush_job =
        claimed.job.job_kind == spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Flush);
    let delivery_policy = if realtime_tool_job {
        RetryPolicy::AtLeastOnce
    } else {
        retry_policy
    };
    let begin_before_process = !matches!(
        claimed.job.job_kind,
        spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Finalizer)
            | spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Generation)
            | spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Flush)
    ) && claimed.job.job_kind != spool::SpoolJobKind::Legacy;
    if begin_before_process {
        if let Err(error) = checkpoint_realtime_delivery_begin(
            &state_file,
            &claimed.job,
            delivery_policy,
            options.stale_after,
        ) {
            let current_state = load_state(&state_file);
            if realtime_pending_disposition(&current_state, &claimed.job, true)
                == spool::PendingDisposition::Done
            {
                spool::complete(claimed)?;
                return Ok(true);
            }
            spool::fail(claimed, &error, false)?;
            return Err(format!(
                "realtime worker claim checkpoint failed; job moved to failed: {error}"
            ));
        }
    }
    let mut non_retryable_realtime_tool_http = false;
    let post_result = if claimed.job.job_kind
        == spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Finalizer)
    {
        process_realtime_finalizer(
            &claimed.job,
            &state_file,
            &langfuse_config,
            &logger,
            options.http_timeout,
            retry_policy,
            options.stale_after,
        )
    } else if claimed.job.job_kind
        == spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Generation)
    {
        process_realtime_generation(
            &claimed.job,
            &state_file,
            &langfuse_config,
            &logger,
            options.http_timeout,
            retry_policy,
            options.stale_after,
        )
    } else if realtime_flush_job {
        process_realtime_flush(&claimed.job, &state_file, &logger, &langfuse_config, None).and_then(
            |outcome| {
                if outcome.is_completed() {
                    Ok(())
                } else {
                    Err(format!(
                        "{REALTIME_FLUSH_DEFERRED}{}",
                        outcome.deferred_reason()
                    ))
                }
            },
        )
    } else if claimed.job.transcript_path.is_some() {
        process_transcript(
            &claimed.job,
            &langfuse_config,
            &logger,
            options.http_timeout,
            None,
            options.config_path.as_deref(),
            options.profile.as_deref(),
        )
    } else {
        if realtime_tool_job || realtime_batch_job {
            post_otel_to_langfuse_once_classified(
                &langfuse_config,
                &claimed.job.otel,
                &logger,
                std::cmp::min(
                    options.http_timeout,
                    Duration::from_millis(REALTIME_TOOL_HTTP_TIMEOUT_MS),
                ),
            )
            .map_err(|error| {
                non_retryable_realtime_tool_http = error.is_non_retryable_http_status();
                error.to_string()
            })
        } else {
            // Same body as the classified branch: emit companion event logs so
            // durable-retry turns also populate the dashboard. Job-level dedupe
            // guarantees a turn is posted once, so no duplicate logs.
            post_otel_to_langfuse_once_classified(
                &langfuse_config,
                &claimed.job.otel,
                &logger,
                options.http_timeout,
            )
            .map_err(|error| error.to_string())
        }
    };
    match post_result {
        Ok(()) => {
            let session_id = claimed.job.session_id.clone();
            let turn_id = claimed.job.turn_id.clone();
            if claimed.job.job_kind != spool::SpoolJobKind::Legacy {
                let current_state = load_state(&state_file);
                if realtime_delivery_needs_success_checkpoint(&current_state, &claimed.job) {
                    checkpoint_realtime_delivery_success(&state_file, &claimed.job)?;
                    if claimed.job.job_kind
                        == spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Generation)
                        || realtime_batch_job
                    {
                        mark_realtime_generation_rounds_accounted(&state_file, &claimed.job)?;
                    }
                }
            }
            spool::complete(claimed)?;
            logger.info(&format!(
                "Worker exported queued job session={} turn_id={}",
                session_id,
                turn_id.as_deref().unwrap_or("<none>")
            ));
            Ok(true)
        }
        Err(err) => {
            if claimed.job.job_kind
                == spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Finalizer)
                && err.starts_with(REALTIME_FINALIZER_TOPOLOGY_DEFERRED)
            {
                deferred_jobs.insert(claimed.job.job_id.clone());
                spool::defer_claimed(claimed, &err)?;
                logger.info(&format!(
                    "worker deferred realtime finalizer with ambiguous topology: {err}"
                ));
                return Ok(true);
            }
            if realtime_flush_job && err.starts_with(REALTIME_FLUSH_DEFERRED) {
                deferred_jobs.insert(claimed.job.job_id.clone());
                spool::defer_claimed(claimed, &err)?;
                logger.info(&format!("worker deferred realtime Flush: {err}"));
                return Ok(true);
            }
            if claimed.job.job_kind
                == spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Generation)
            {
                if err.starts_with(REALTIME_GENERATION_DEFERRED) {
                    deferred_jobs.insert(claimed.job.job_id.clone());
                    spool::defer_claimed(claimed, &err)?;
                    logger.info(&format!("worker deferred realtime generation scan: {err}"));
                    return Ok(true);
                }
                if !realtime_delivery_started(&state_file, &claimed.job) {
                    spool::fail(claimed, &err, false)?;
                    return Err(format!(
                        "worker realtime generation preparation failed before delivery begin: {err}"
                    ));
                }
            }
            let retry = !realtime_tool_job
                && !realtime_batch_job
                && retry_policy == RetryPolicy::AtLeastOnce
                && should_retry(claimed.job.attempts, options.max_attempts);
            let session_id = claimed.job.session_id.clone();
            let realtime = claimed.job.job_kind != spool::SpoolJobKind::Legacy;
            if realtime {
                let delivery_started = realtime_delivery_started(&state_file, &claimed.job);
                if delivery_started {
                    checkpoint_realtime_delivery_failure(
                        &state_file,
                        &claimed.job,
                        delivery_policy,
                        &err,
                        retry,
                        non_retryable_realtime_tool_http && !realtime_batch_job,
                    )?;
                }
            }
            spool::fail(claimed, &err, retry)?;
            if retry {
                logger.warn(&format!(
                    "worker export failed; job returned to pending: {err}"
                ));
                Ok(true)
            } else {
                if !realtime {
                    mark_export_terminal_failure(&state_file, &session_id, &err)?;
                }
                if realtime_tool_job || realtime_batch_job {
                    if non_retryable_realtime_tool_http {
                        logger.warn(&format!(
                            "realtime export received a non-retryable HTTP status; job will fall back to delayed PostToolUse/Stop compensation: {err}"
                        ));
                    } else {
                        logger.warn(&format!(
                            "realtime export failed once; job marked failed for delayed PostToolUse/Stop compensation: {err}"
                        ));
                    }
                    Ok(true)
                } else {
                    Err(format!(
                        "worker export failed after {} attempts; job moved to failed: {err}",
                        options.max_attempts
                    ))
                }
            }
        }
    }
}

fn reconcile_pending_realtime_jobs(
    state_file: &Path,
    spool_dir: &Path,
    stale_after: Duration,
) -> Result<usize, String> {
    let pending = spool::pending_realtime_jobs(spool_dir, stale_after)?;
    if pending.is_empty() {
        return Ok(0);
    }
    let mut reconciled = 0usize;
    update_realtime_state(state_file, |state| {
        for job in &pending {
            if !spool::realtime_job_identity_matches(job)? {
                continue;
            }
            let (
                spool::SpoolJobKind::Realtime(kind),
                Some(context_id),
                Some(subject_id),
                Some(payload_digest),
            ) = (
                job.job_kind.clone(),
                job.context_id.as_deref(),
                job.subject_id.as_deref(),
                job.payload_digest.as_deref(),
            )
            else {
                continue;
            };
            if let Some(existing) =
                realtime_state::registered_realtime_job(state, &job.session_id, &job.job_id)
            {
                if kind == spool::RealtimeSpoolKind::Finalizer
                    && existing.status == realtime_state::RealtimeRegisteredJobStatus::Inflight
                    && job.last_error.as_deref() == Some("recovered stale claim")
                {
                    realtime_state::requeue_interrupted_realtime_job_in_state(
                        state,
                        &job.session_id,
                        &job.job_id,
                        payload_digest,
                    )?;
                    reconciled = reconciled.saturating_add(1);
                    continue;
                }
                if kind == spool::RealtimeSpoolKind::Tool
                    && existing.status == realtime_state::RealtimeRegisteredJobStatus::Failed
                {
                    realtime_state::requeue_failed_realtime_job_in_state(
                        state,
                        &job.session_id,
                        &job.job_id,
                        payload_digest,
                    )?;
                    reconciled = reconciled.saturating_add(1);
                }
                continue;
            }
            let Some(context) = realtime_state::contexts(state, &job.session_id)
                .iter()
                .find(|context| context.context_id == context_id)
                .cloned()
            else {
                continue;
            };
            let subject_matches_context = match kind {
                spool::RealtimeSpoolKind::Tool => {
                    context.tool_use_ids.iter().any(|tool| tool == subject_id)
                }
                spool::RealtimeSpoolKind::Generation => {
                    generation_trigger_for_job(job).is_ok_and(|trigger| {
                        trigger.identity_matches(
                            &job.session_id,
                            job.context_id.as_deref(),
                            job.subject_id.as_deref(),
                        ) && trigger
                            .matches_context(context.turn_id.as_deref(), &context.tool_use_ids)
                    })
                }
                spool::RealtimeSpoolKind::Finalizer => job
                    .otel
                    .get("transcript_size")
                    .and_then(Value::as_u64)
                    .zip(job.otel.get("high_water").and_then(Value::as_u64))
                    .is_some_and(|(transcript_size, high_water)| {
                        subject_id == format!("finalizer:{transcript_size}:{high_water}")
                    }),
                spool::RealtimeSpoolKind::Flush => {
                    job.flush_manifest.as_ref().is_some_and(|manifest| {
                        manifest.trigger_id == subject_id
                            && context
                                .tool_use_ids
                                .iter()
                                .any(|tool_id| tool_id == &manifest.subject_id)
                            && context.turn_id.as_deref() == Some(manifest.turn_id.as_str())
                    })
                }
                spool::RealtimeSpoolKind::Batch => job
                    .batch_manifest
                    .as_ref()
                    .is_some_and(|manifest| manifest.batch_id == job.job_id),
            };
            if !subject_matches_context {
                continue;
            }
            realtime_state::register_realtime_job_in_state(
                state,
                &job.session_id,
                context_id,
                kind.as_str(),
                &job.job_id,
                payload_digest,
            )?;
            if kind == spool::RealtimeSpoolKind::Tool {
                realtime_state::persist_tool_job_registration_in_state(
                    state,
                    &job.session_id,
                    context_id,
                    subject_id,
                    &job.job_id,
                    payload_digest,
                );
            }
            reconciled = reconciled.saturating_add(1);
        }
        Ok(())
    })?;
    Ok(reconciled)
}

fn generation_trigger_for_job(job: &spool::SpoolJob) -> Result<RealtimeGenerationTrigger, String> {
    RealtimeGenerationTrigger::parse(&job.otel, Some(&job.session_id), job.context_id.as_deref())
}

fn realtime_job_schema_identity_matches(state: &Value, job: &spool::SpoolJob) -> bool {
    if job.job_kind != spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Generation) {
        return true;
    }
    let Ok(trigger) = generation_trigger_for_job(job) else {
        return false;
    };
    if !trigger.identity_matches(
        &job.session_id,
        job.context_id.as_deref(),
        job.subject_id.as_deref(),
    ) {
        return false;
    }
    realtime_state::contexts(state, &job.session_id)
        .into_iter()
        .find(|context| context.context_id == trigger.context_id)
        .is_some_and(|context| {
            trigger.matches_context(context.turn_id.as_deref(), &context.tool_use_ids)
        })
}

fn realtime_pending_disposition(
    state: &Value,
    job: &spool::SpoolJob,
    stale: bool,
) -> spool::PendingDisposition {
    if job.job_kind == spool::SpoolJobKind::Legacy {
        return spool::PendingDisposition::Keep;
    }
    if !realtime_job_schema_identity_matches(state, job) {
        return if stale {
            spool::PendingDisposition::Failed
        } else {
            spool::PendingDisposition::Keep
        };
    }
    let Some(digest) = job.payload_digest.as_deref() else {
        return if stale {
            spool::PendingDisposition::Failed
        } else {
            spool::PendingDisposition::Keep
        };
    };
    let Some(registration) =
        realtime_state::registered_realtime_job(state, &job.session_id, &job.job_id)
    else {
        return if stale {
            spool::PendingDisposition::Failed
        } else {
            spool::PendingDisposition::Keep
        };
    };
    if registration.payload_digest != digest {
        return if stale {
            spool::PendingDisposition::Failed
        } else {
            spool::PendingDisposition::Keep
        };
    }
    match registration.status {
        realtime_state::RealtimeRegisteredJobStatus::Accounted
        | realtime_state::RealtimeRegisteredJobStatus::Failed
        | realtime_state::RealtimeRegisteredJobStatus::PermanentFailed
        | realtime_state::RealtimeRegisteredJobStatus::PolicyTerminal
        | realtime_state::RealtimeRegisteredJobStatus::Compensated => {
            spool::PendingDisposition::Done
        }
        realtime_state::RealtimeRegisteredJobStatus::Queued
        | realtime_state::RealtimeRegisteredJobStatus::Inflight => spool::PendingDisposition::Keep,
    }
}

fn realtime_delivery_needs_success_checkpoint(state: &Value, job: &spool::SpoolJob) -> bool {
    let Some(registered) =
        realtime_state::registered_realtime_job(state, &job.session_id, &job.job_id)
    else {
        return true;
    };
    !matches!(
        registered.status,
        realtime_state::RealtimeRegisteredJobStatus::Accounted
            | realtime_state::RealtimeRegisteredJobStatus::Compensated
    )
}

fn realtime_job_retry_policy(state: &Value, job: &spool::SpoolJob) -> RetryPolicy {
    job.otel
        .get("retry_policy")
        .and_then(Value::as_str)
        .and_then(RetryPolicy::parse)
        .or_else(|| {
            job.context_id.as_deref().and_then(|context_id| {
                realtime_state::retry_policy_for_context(state, &job.session_id, context_id)
            })
        })
        .unwrap_or(RetryPolicy::AtLeastOnce)
}

fn checkpoint_realtime_delivery_begin(
    state_file: &Path,
    job: &spool::SpoolJob,
    retry_policy: RetryPolicy,
    lease_duration: Duration,
) -> Result<(), String> {
    let digest = job
        .payload_digest
        .as_deref()
        .ok_or("realtime job missing payload_digest")?;
    let claim_id = format!("{}:{}", job.job_id, job.attempts);
    let lease_ms = lease_duration.as_millis().try_into().unwrap_or(u64::MAX);
    let lease_until_ms = unix_millis().saturating_add(lease_ms);
    update_realtime_state(state_file, |state| {
        realtime_state::begin_realtime_delivery_in_state(
            state,
            &job.session_id,
            &job.job_id,
            digest,
            retry_policy,
            &claim_id,
            lease_until_ms,
        )
    })
}

fn timed_checkpoint<T, F>(logger: &Logger, operation: &str, checkpoint: F) -> Result<T, String>
where
    F: FnOnce() -> Result<T, String>,
{
    let started = std::time::Instant::now();
    let result = checkpoint();
    logger.log_raw(
        &serde_json::json!({
            "kind": "cdxhooks.worker_timing",
            "phase": "checkpoint",
            "operation": operation,
            "elapsed_ms": started.elapsed().as_millis(),
            "execution": "async",
            "outcome": if result.is_ok() { "ok" } else { "error" },
        })
        .to_string(),
    );
    result
}

fn checkpoint_realtime_delivery_begin_direct(
    state_file: &Path,
    job: &spool::SpoolJob,
    retry_policy: RetryPolicy,
) -> Result<(), String> {
    let digest = job
        .payload_digest
        .as_deref()
        .ok_or("realtime job missing payload_digest")?;
    update_realtime_state(state_file, |state| {
        realtime_state::begin_direct_realtime_delivery_in_state(
            state,
            &job.session_id,
            &job.job_id,
            digest,
            retry_policy,
        )
    })
}

fn checkpoint_realtime_delivery_success(
    state_file: &Path,
    job: &spool::SpoolJob,
) -> Result<(), String> {
    let digest = job
        .payload_digest
        .as_deref()
        .ok_or("realtime job missing payload_digest")?;
    update_realtime_state(state_file, |state| {
        realtime_state::complete_realtime_delivery_in_state(
            state,
            &job.session_id,
            &job.job_id,
            digest,
        )?;
        if job.job_kind == spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Batch) {
            realtime_state::succeed_realtime_batch_in_state(state, &job.session_id, &job.job_id)?;
            realtime_state::complete_realtime_batch_compensation_in_state(
                state,
                &job.session_id,
                &job.job_id,
            )?;
        }
        Ok(())
    })
}

fn checkpoint_realtime_delivery_failure(
    state_file: &Path,
    job: &spool::SpoolJob,
    retry_policy: RetryPolicy,
    error: &str,
    retry: bool,
    permanent: bool,
) -> Result<(), String> {
    let digest = job
        .payload_digest
        .as_deref()
        .ok_or("realtime job missing payload_digest")?;
    update_realtime_state(state_file, |state| {
        realtime_state::fail_realtime_delivery_in_state(
            state,
            &job.session_id,
            &job.job_id,
            digest,
            retry_policy,
            error,
            retry,
            permanent,
        )?;
        if job.job_kind == spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Batch) {
            realtime_state::fail_realtime_batch_in_state(
                state,
                &job.session_id,
                &job.job_id,
                error,
            )?;
        }
        Ok(())
    })
}

fn realtime_delivery_started(state_file: &Path, job: &spool::SpoolJob) -> bool {
    let state = load_state(state_file);
    realtime_state::registered_realtime_job(&state, &job.session_id, &job.job_id).is_some_and(
        |registration| {
            matches!(
                registration.status,
                realtime_state::RealtimeRegisteredJobStatus::Inflight
                    | realtime_state::RealtimeRegisteredJobStatus::PolicyTerminal
            )
        },
    )
}

fn update_realtime_state<F>(state_file: &Path, update: F) -> Result<(), String>
where
    F: FnOnce(&mut Value) -> Result<(), String>,
{
    let mut state = load_state(state_file);
    let mut result = Ok(());
    update_state_transaction(state_file, &mut state, |candidate| {
        result = update(candidate);
    })?;
    result
}

fn unix_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn should_retry(attempts: u32, max_attempts: u32) -> bool {
    attempts < max_attempts
}

#[derive(Debug, Clone, Copy)]
struct FinalizerReconcile {
    high_water: u64,
    retry_policy: RetryPolicy,
    lease_duration: Duration,
}

fn process_realtime_finalizer(
    job: &spool::SpoolJob,
    state_file: &Path,
    config: &LangfuseConfig,
    logger: &Logger,
    timeout: Duration,
    retry_policy: RetryPolicy,
    lease_duration: Duration,
) -> Result<(), String> {
    let state = load_state(state_file);
    let high_water = finalizer_high_water_for_job(&state, job)?;
    if !realtime_state::finalizer_barrier_satisfied(&state, &job.session_id, high_water) {
        logger.info(&format!(
            "Realtime finalizer will compensate missing children through high_water={high_water} session={}",
            job.session_id
        ));
    }
    process_transcript(
        job,
        config,
        logger,
        timeout,
        Some(FinalizerReconcile {
            high_water,
            retry_policy,
            lease_duration,
        }),
        None,
        None,
    )
}

fn finalizer_high_water_for_job(state: &Value, job: &spool::SpoolJob) -> Result<u64, String> {
    if let Some(high_water) = job.otel.get("high_water").and_then(Value::as_u64) {
        return Ok(high_water);
    }
    let registration = realtime_state::registered_realtime_job(state, &job.session_id, &job.job_id)
        .ok_or_else(|| format!("realtime finalizer is not registered: {}", job.job_id))?;
    Ok(registration.sequence.saturating_sub(1))
}

fn process_realtime_flush(
    job: &spool::SpoolJob,
    state_file: &Path,
    logger: &Logger,
    config: &LangfuseConfig,
    budget: Option<StopBudget>,
) -> Result<FlushReconcileOutcome, String> {
    let effective_claim_budget = effective_realtime_claim_budget(budget);
    if effective_claim_budget.is_zero() {
        return Ok(FlushReconcileOutcome::Waiting);
    }
    let claim_deadline = std::time::Instant::now() + effective_claim_budget;
    let manifest = job
        .flush_manifest
        .as_ref()
        .ok_or_else(|| format!("{REALTIME_FLUSH_DEFERRED}missing_manifest"))?;
    let state = load_state(state_file);
    let current_anchor = realtime_state::generation_scan_anchor(&state, &job.session_id);
    if current_anchor != manifest.scan_anchor {
        checkpoint_realtime_delivery_begin_direct(state_file, job, RetryPolicy::AtLeastOnce)?;
        logger.info(&format!(
            "Realtime Flush already superseded session={} job={} scan_anchor={} current_anchor={}",
            job.session_id, job.job_id, manifest.scan_anchor, current_anchor
        ));
        return Ok(FlushReconcileOutcome::Completed);
    }
    let Some(context) = realtime_state::contexts(&state, &job.session_id)
        .into_iter()
        .find(|candidate| candidate.context_id == job.context_id.as_deref().unwrap_or_default())
    else {
        return Err(format!("{REALTIME_FLUSH_DEFERRED}missing_context"));
    };
    if context.turn_id.as_deref() != Some(manifest.turn_id.as_str())
        || !context
            .tool_use_ids
            .iter()
            .any(|tool_id| tool_id == &manifest.subject_id)
    {
        return Err(format!("{REALTIME_FLUSH_DEFERRED}context_mismatch"));
    }

    let prepared = prepare_generation_window(
        &job.session_id,
        state_file,
        Path::new(&manifest.transcript_path),
        manifest.scan_anchor,
        logger,
    )
    .map_err(|error| classify_realtime_preparation_error(error, REALTIME_FLUSH_DEFERRED))?;
    let PreparedGenerationWindow {
        entries,
        next_offset,
        window_terminal,
        discovery_progressed,
    } = match prepared {
        GenerationWindowPreparation::Semantic(window) => window,
        GenerationWindowPreparation::Waiting => {
            return Ok(FlushReconcileOutcome::Waiting);
        }
        GenerationWindowPreparation::FinalizerOnly(reason) => {
            logger.debug(&format!(
                "realtime Flush deferred to finalizer session={} job={} reason={reason}",
                job.session_id, job.job_id
            ));
            return Ok(FlushReconcileOutcome::FinalizerOnly);
        }
    };
    let turns = build_turns_for_generation_window(
        state_file,
        &state,
        &job.session_id,
        Path::new(&manifest.transcript_path),
        &entries,
        manifest.scan_anchor,
        next_offset,
    )?;
    let trigger = match RealtimeGenerationTrigger::v2(
        &job.session_id,
        &context.context_id,
        &manifest.turn_id,
        &manifest.subject_id,
        &manifest.transcript_path,
        manifest.scan_anchor,
        manifest.generation_batch_size,
    ) {
        Ok(trigger) => trigger,
        Err(_) => {
            return Err(format!("{REALTIME_FLUSH_DEFERRED}invalid_trigger"));
        }
    };
    let (turn_index, trigger_assistant_index) = match resolve_realtime_generation_boundary(
        &trigger, &turns,
    ) {
        Ok(boundary) => boundary,
        Err(reason) => {
            logger.debug(&format!(
                    "realtime Flush boundary not discovered session={} job={} reason={reason} next_offset={next_offset}",
                    job.session_id, job.job_id
                ));
            return Ok(if discovery_progressed {
                FlushReconcileOutcome::Progressed
            } else {
                FlushReconcileOutcome::Waiting
            });
        }
    };
    let scan_base_ordinal = realtime_state::generation_scan_base_ordinal(&state, &job.session_id);
    let exported_ordinals = realtime_generation_rounds(&state, &job.session_id)
        .into_iter()
        .map(|round| round.ordinal)
        .collect::<HashSet<_>>();
    let mut turn_topologies = (0..=turn_index)
        .filter_map(|index| {
            resolve_finalizer_turn_topology_with_scan_base(
                &state,
                &job.session_id,
                &turns,
                index,
                scan_base_ordinal,
            )
            .into_exact_ids()
            .map(|topology| (index, topology))
        })
        .collect::<HashMap<_, _>>();
    turn_topologies
        .entry(turn_index)
        .or_insert((context.trace_id.clone(), context.root_span_id.clone()));
    let eligible_turns = turn_topologies.keys().copied().collect::<HashSet<_>>();
    let selection = select_realtime_generation_batch_from_turns(
        &turns,
        &job.session_id,
        &context.context_id,
        manifest.generation_batch_size,
        scan_base_ordinal,
        &exported_ordinals,
        &eligible_turns,
        (turn_index, trigger_assistant_index),
        false,
    );
    let Some((selections, rounds)) = selection else {
        if semantic_generation_ordinal(
            &turns,
            turn_index,
            trigger_assistant_index,
            scan_base_ordinal,
        )
        .is_some_and(|ordinal| exported_ordinals.contains(&ordinal))
        {
            checkpoint_realtime_delivery_begin_direct(state_file, job, RetryPolicy::AtLeastOnce)?;
            return Ok(FlushReconcileOutcome::Completed);
        }
        return Ok(if discovery_progressed {
            FlushReconcileOutcome::Progressed
        } else {
            FlushReconcileOutcome::Waiting
        });
    };

    let vcs_info = get_vcs_for_cwd(&state, &job.session_id, manifest.cwd.as_deref());
    let mut generation_payload = build_realtime_generation_payload_for_turns(
        &turns,
        &selections,
        &turn_topologies,
        manifest.generation_batch_size,
        false,
        manifest.username.as_deref().unwrap_or("unknown"),
        &job.session_id,
        manifest
            .hook_version
            .as_deref()
            .unwrap_or(env!("CARGO_PKG_VERSION")),
        &state,
    )?;
    if let Some(vcs) = vcs_info.as_ref() {
        inject_vcs_resource_attrs(&mut generation_payload, &vcs.remote_url, &vcs.branch);
    }
    let generation_identities = rounds
        .iter()
        .map(|(_, generation_id, span_id)| (format!("generation:{generation_id}"), span_id.clone()))
        .collect::<Vec<_>>();
    let mut semantic_children =
        extract_realtime_semantic_children(&generation_payload, &generation_identities)?;
    let selected_ordinals = selections
        .iter()
        .zip(rounds.iter())
        .map(|((turn_index, assistant_index, _), (ordinal, _, _))| {
            ((*turn_index, *assistant_index), *ordinal)
        })
        .collect::<HashMap<_, _>>();
    let mut selected_tools = Vec::<(String, String, String)>::new();
    for (selected_turn_index, turn) in turns.iter().enumerate() {
        let Some((trace_id, root_span_id)) = turn_topologies.get(&selected_turn_index) else {
            continue;
        };
        let turn_records =
            tool_records::completed_for_turn(state_file, &job.session_id, &turn.turn_id);
        let transcript_call_ids = turn
            .tool_calls
            .iter()
            .map(|tool| tool.call_id.as_str())
            .collect::<HashSet<_>>();
        let mut used_record_ids = HashSet::new();
        for tool in &turn.tool_calls {
            let Some(semantic_ordinal) = selected_ordinals
                .get(&(selected_turn_index, tool.assistant_index))
                .copied()
            else {
                continue;
            };
            if tool.ended_timestamp.is_empty() {
                continue;
            }
            let exact_record = turn_records
                .iter()
                .find(|record| record.tool_use_id == tool.call_id)
                .cloned();
            let record = if let Some(record) = exact_record {
                record
            } else {
                let Some(transcript_started_at) =
                    agentic_otel_common::iso_to_nanos(&tool.started_timestamp)
                        .parse::<u128>()
                        .ok()
                        .filter(|value| *value > 0)
                else {
                    continue;
                };
                let Some(transcript_completed_at) =
                    agentic_otel_common::iso_to_nanos(&tool.ended_timestamp)
                        .parse::<u128>()
                        .ok()
                        .filter(|value| *value >= transcript_started_at)
                else {
                    continue;
                };
                let candidates = turn_records
                    .iter()
                    .filter(|candidate| candidate.tool_use_id.starts_with("exec-"))
                    .filter(|candidate| {
                        !transcript_call_ids.contains(candidate.tool_use_id.as_str())
                            && !used_record_ids.contains(&candidate.tool_use_id)
                    })
                    .filter(|candidate| {
                        candidate
                            .assistant_index
                            .is_none_or(|index| index == tool.assistant_index)
                            && candidate
                                .semantic_generation_ordinal
                                .is_none_or(|ordinal| ordinal == semantic_ordinal)
                    })
                    .filter(|candidate| {
                        candidate.completed_timing().ok().is_some_and(|timing| {
                            let started_at =
                                timing.started_at_unix_nano.parse::<u128>().unwrap_or(0);
                            let completed_at =
                                timing.completed_at_unix_nano.parse::<u128>().unwrap_or(0);
                            started_at
                                >= transcript_started_at
                                    .saturating_sub(REALTIME_TOOL_CORRELATION_SKEW_NANOS)
                                && completed_at
                                    <= transcript_completed_at
                                        .saturating_add(REALTIME_TOOL_CORRELATION_SKEW_NANOS)
                        })
                    })
                    .collect::<Vec<_>>();
                let [candidate] = candidates.as_slice() else {
                    logger.debug(&format!(
                        "realtime Tool correlation unavailable; deferring to Stop: session={} turn={} call_id={} candidates={} completed_records={}",
                        job.session_id,
                        turn.turn_id,
                        tool.call_id,
                        candidates.len(),
                        turn_records.len()
                    ));
                    continue;
                };
                (*candidate).clone()
            };
            used_record_ids.insert(record.tool_use_id.clone());
            let timing = match record.completed_timing() {
                Ok(timing) => timing,
                Err(_) => continue,
            };
            tool_records::persist_semantic_mapping(
                state_file,
                &job.session_id,
                &record.tool_use_id,
                tool.assistant_index,
                semantic_ordinal,
            )
            .map_err(|error| format!("{REALTIME_FLUSH_DEFERRED}{error}"))?;
            let mut tool_payload = build_realtime_tool_payload(
                &RealtimeToolCompletion {
                    tool_use_id: tool.call_id.clone(),
                    tool_name: tool.name.clone(),
                    tool_input: tool.input.clone(),
                    tool_output: tool.output.clone(),
                    started_at: timing.started_at_unix_nano,
                    completed_at: timing.completed_at_unix_nano,
                    success: tool.success,
                    chat_id: turn.chat_id.clone().or_else(|| Some(turn.turn_id.clone())),
                    cwd: manifest.cwd.clone().unwrap_or_else(|| turn.cwd.clone()),
                },
                trace_id,
                root_span_id,
                manifest.username.as_deref().unwrap_or("unknown"),
                &job.session_id,
                manifest
                    .hook_version
                    .as_deref()
                    .unwrap_or(env!("CARGO_PKG_VERSION")),
            )?;
            if let Some(vcs) = vcs_info.as_ref() {
                inject_vcs_resource_attrs(&mut tool_payload, &vcs.remote_url, &vcs.branch);
            }
            let canonical_tool_span_id = deterministic_tool_span_id(&tool.call_id);
            semantic_children.extend(extract_realtime_semantic_children(
                &tool_payload,
                &[(
                    format!("tool:{}", tool.call_id),
                    canonical_tool_span_id.clone(),
                )],
            )?);
            let tool_context_id = realtime_state::contexts(&state, &job.session_id)
                .into_iter()
                .find(|candidate| {
                    candidate
                        .tool_use_ids
                        .iter()
                        .any(|tool_id| tool_id == &record.tool_use_id)
                })
                .map(|candidate| candidate.context_id)
                .unwrap_or_else(|| context.context_id.clone());
            selected_tools.push((
                tool.call_id.clone(),
                tool_context_id,
                canonical_tool_span_id,
            ));
        }
    }

    let semantic_boundary = rounds.iter().map(|round| round.0).max().unwrap_or(0);
    let batch_context_id = context.context_id.as_str();
    let delivery_policy = context.retry_policy.as_str();
    let all_child_ids = semantic_children
        .iter()
        .map(|child| child.child_id.clone())
        .collect::<Vec<_>>();
    let all_child_digests = semantic_children
        .iter()
        .map(|child| child.payload_digest.clone())
        .collect::<Vec<_>>();
    let plan_id = realtime_state::stable_realtime_delivery_plan_id(
        &job.session_id,
        batch_context_id,
        &job.job_id,
        &all_child_ids,
        &all_child_digests,
    );
    let payload_plan =
        plan_realtime_payload_chunks(semantic_children, REALTIME_TARGET_SERIALIZED_BODY_BYTES)?;
    let oversized_child_ids = payload_plan
        .oversized_children
        .iter()
        .map(|child| child.child_id.clone())
        .collect::<Vec<_>>();
    let mut batch_jobs = Vec::new();
    let mut plan_chunks = Vec::new();
    for chunk in payload_plan.chunks {
        let item_count = chunk.child_ids.len();
        let batch_subject = format!("completed:{plan_id}:{}", chunk.cursor);
        let batch_id = spool::deterministic_realtime_job_id_for_session_schema(
            spool::RealtimeSpoolKind::Batch,
            spool::CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
            &job.session_id,
            batch_context_id,
            &batch_subject,
            &chunk.payload_digest,
        );
        plan_chunks.push(realtime_state::RealtimeDeliveryChunkPlanEntry {
            cursor: chunk.cursor,
            batch_id: batch_id.clone(),
            payload_digest: chunk.payload_digest.clone(),
            body_len: chunk.body_len,
            child_ids: chunk.child_ids.clone(),
            span_ids: chunk.span_ids.clone(),
            digests: chunk.digests.clone(),
            body: None,
            status: realtime_state::RealtimeDeliveryChunkStatus::Queued,
            last_error: None,
        });
        batch_jobs.push(spool::SpoolJob {
            schema_version: spool::REALTIME_SCHEMA_VERSION,
            session_schema_version: spool::CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
            job_kind: spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Batch),
            job_id: batch_id.clone(),
            session_id: job.session_id.clone(),
            turn_id: Some(manifest.turn_id.clone()),
            created_at: crate::common::logger::now_iso8601_pub(),
            claimed_at: None,
            attempts: 1,
            last_error: None,
            otel: chunk.body,
            transcript_path: None,
            cwd: manifest.cwd.clone(),
            username: manifest.username.clone(),
            hook_version: manifest.hook_version.clone(),
            subject_id: Some(batch_subject.clone()),
            context_id: Some(batch_context_id.to_string()),
            payload_digest: Some(chunk.payload_digest.clone()),
            batch_manifest: Some(spool::RealtimeBatchManifest {
                schema_version: spool::REALTIME_BATCH_MANIFEST_SCHEMA_VERSION,
                batch_id,
                subject_id: batch_subject,
                session_schema_version: spool::CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
                semantic_generation_boundary: Some(serde_json::json!(semantic_boundary)),
                member_ids: chunk.child_ids,
                span_ids: chunk.span_ids,
                digests: chunk.digests,
                compensation_round: 0,
                delivery_policy: delivery_policy.to_string(),
                item_count,
                otlp_digest: chunk.payload_digest,
            }),
            flush_manifest: None,
        });
    }
    let delivery_plan = realtime_state::RealtimeDeliveryPlan {
        schema_version: realtime_state::REALTIME_DELIVERY_PLAN_SCHEMA_VERSION,
        plan_id: plan_id.clone(),
        context_id: batch_context_id.to_string(),
        trigger_id: job.job_id.clone(),
        chunks: plan_chunks,
        oversized_child_ids: oversized_child_ids.clone(),
        next_cursor: 0,
        complete: false,
        terminal_reason: None,
    };
    let prior_plan = realtime_state::realtime_delivery_plan(&state, &job.session_id, &plan_id);
    update_realtime_state(state_file, |candidate| {
        realtime_state::prepare_realtime_delivery_plan_in_state(
            candidate,
            &job.session_id,
            &delivery_plan,
        )?;
        for batch_job in &batch_jobs {
            let manifest = batch_job
                .batch_manifest
                .as_ref()
                .ok_or("planned realtime Batch is missing its manifest")?;
            let payload_digest = batch_job
                .payload_digest
                .as_deref()
                .ok_or("planned realtime Batch is missing its payload digest")?;
            realtime_state::prepare_realtime_batch_with_id_in_state(
                candidate,
                &job.session_id,
                &context.context_id,
                "completed_chunk",
                &batch_job.job_id,
                &manifest.member_ids,
                payload_digest,
            )?;
            realtime_state::register_realtime_job_in_state(
                candidate,
                &job.session_id,
                &context.context_id,
                spool::RealtimeSpoolKind::Batch.as_str(),
                &batch_job.job_id,
                payload_digest,
            )?;
            for (tool_use_id, tool_context_id, _) in &selected_tools {
                if manifest.member_ids.contains(&format!("tool:{tool_use_id}")) {
                    realtime_state::persist_tool_job_registration_in_state(
                        candidate,
                        &job.session_id,
                        tool_context_id,
                        tool_use_id,
                        &batch_job.job_id,
                        payload_digest,
                    );
                }
            }
        }
        Ok(())
    })?;
    let window_candidate_count = turns
        .iter()
        .map(Turn::semantic_assistant_count)
        .sum::<usize>() as u64;
    let selected_set = rounds.iter().map(|round| round.0).collect::<HashSet<_>>();
    let finalizer_only_ordinals = if window_terminal {
        (0..window_candidate_count)
            .map(|relative| scan_base_ordinal.saturating_add(relative))
            .filter(|ordinal| {
                !exported_ordinals.contains(ordinal) && !selected_set.contains(ordinal)
            })
            .collect::<Vec<_>>()
    } else {
        Vec::new()
    };
    let scan_commit = GenerationScanCommit {
        scan_anchor: manifest.scan_anchor,
        next_offset,
        scan_base_ordinal,
        window_candidate_count,
        window_terminal,
    };
    let delivery_policy = RetryPolicy::parse(delivery_policy).unwrap_or(RetryPolicy::AtLeastOnce);
    if prior_plan.is_some() && delivery_policy == RetryPolicy::AtMostOnce {
        update_realtime_state(state_file, |candidate| {
            realtime_state::settle_interrupted_policy_terminal_plan_in_state(
                candidate,
                &job.session_id,
                &plan_id,
            )
            .map(|_| ())
        })?;
    }
    let mut current_plan =
        realtime_state::realtime_delivery_plan(&load_state(state_file), &job.session_id, &plan_id)
            .ok_or_else(|| format!("realtime delivery plan disappeared: {plan_id}"))?;
    if !oversized_child_ids.is_empty() {
        logger.warn(&format!(
            "event=realtime_delivery_decision operation=realtime_batch outcome=terminal reason=realtime_child_oversize plan_id={} child_count={}",
            plan_id,
            oversized_child_ids.len()
        ));
    }
    let claim_end = (current_plan.next_cursor as usize)
        .saturating_add(REALTIME_MAX_CHUNKS_PER_CLAIM)
        .min(batch_jobs.len());
    for (cursor, batch_job) in batch_jobs
        .iter()
        .enumerate()
        .take(claim_end)
        .skip(current_plan.next_cursor as usize)
    {
        let remaining = claim_deadline.saturating_duration_since(std::time::Instant::now());
        let Some(timeout) = realtime_chunk_timeout(remaining) else {
            logger.info(&format!(
                "event=realtime_delivery_decision operation=realtime_batch outcome=queued reason=insufficient_claim_budget plan_id={} next_cursor={cursor} remaining_ms={} required_ms={}",
                plan_id,
                remaining.as_millis(),
                REALTIME_CHUNK_SAFE_START_BUDGET_MS
            ));
            return Ok(FlushReconcileOutcome::Progressed);
        };
        timed_checkpoint(logger, "batch_begin", || {
            checkpoint_realtime_delivery_plan_chunk_begin(
                state_file,
                &plan_id,
                cursor as u64,
                batch_job,
                delivery_policy,
            )
        })?;
        let result =
            post_otel_to_langfuse_once_classified(config, &batch_job.otel, logger, timeout);
        match result {
            Ok(()) => {
                timed_checkpoint(logger, "batch_success", || {
                    checkpoint_realtime_delivery_plan_chunk_finish(
                        state_file,
                        &plan_id,
                        cursor as u64,
                        batch_job,
                        delivery_policy,
                        true,
                        false,
                        None,
                    )
                })?;
            }
            Err(error) => {
                let permanent = error.is_non_retryable_http_status();
                let message = error.to_string();
                timed_checkpoint(logger, "batch_failure", || {
                    checkpoint_realtime_delivery_plan_chunk_finish(
                        state_file,
                        &plan_id,
                        cursor as u64,
                        batch_job,
                        delivery_policy,
                        false,
                        permanent,
                        Some(&message),
                    )
                })?;
                if delivery_policy == RetryPolicy::AtLeastOnce && !permanent {
                    return Err(message);
                }
                logger.warn(&format!(
                    "event=realtime_delivery_decision operation=realtime_batch outcome=terminal reason={} plan_id={} chunk_cursor={} error={}",
                    if permanent { "http_permanent_failure" } else { "at_most_once_transport_failure" },
                    plan_id,
                    cursor,
                    message
                ));
            }
        }
    }
    current_plan =
        realtime_state::realtime_delivery_plan(&load_state(state_file), &job.session_id, &plan_id)
            .ok_or_else(|| format!("realtime delivery plan disappeared: {plan_id}"))?;
    if !current_plan.complete {
        logger.info(&format!(
            "event=realtime_delivery_decision operation=realtime_batch outcome=queued reason=chunk_claim_limit plan_id={} next_cursor={} total_chunks={}",
            plan_id,
            current_plan.next_cursor,
            current_plan.chunks.len()
        ));
        return Ok(FlushReconcileOutcome::Progressed);
    }
    timed_checkpoint(logger, "generation_accounted", || {
        finalize_realtime_delivery_plan_rounds(
            state_file,
            job,
            batch_context_id,
            &current_plan,
            &batch_jobs,
            scan_commit,
            &rounds,
            &finalizer_only_ordinals,
        )
    })?;
    timed_checkpoint(logger, "flush_begin", || {
        checkpoint_realtime_delivery_begin_direct(state_file, job, RetryPolicy::AtLeastOnce)
    })?;
    Ok(FlushReconcileOutcome::Completed)
}

fn classify_realtime_preparation_error(error: String, deferred_prefix: &str) -> String {
    if error.starts_with(TEMPORARY_TRANSCRIPT_IO_ERROR) {
        format!("{deferred_prefix}{error}")
    } else {
        error
    }
}

fn effective_realtime_claim_budget(budget: Option<StopBudget>) -> Duration {
    let hard_cap = Duration::from_millis(REALTIME_CLAIM_HARD_CAP_MS);
    match budget {
        Some(budget) => std::cmp::min(
            hard_cap,
            budget.processing_time_remaining().unwrap_or_default(),
        ),
        None => hard_cap,
    }
}

fn realtime_chunk_timeout(remaining: Duration) -> Option<Duration> {
    let safe_start_budget = Duration::from_millis(REALTIME_CHUNK_SAFE_START_BUDGET_MS);
    if remaining < safe_start_budget {
        return None;
    }
    Some(std::cmp::min(
        Duration::from_millis(REALTIME_TOOL_HTTP_TIMEOUT_MS),
        remaining,
    ))
}

fn checkpoint_realtime_delivery_plan_chunk_begin(
    state_file: &Path,
    plan_id: &str,
    cursor: u64,
    job: &spool::SpoolJob,
    retry_policy: RetryPolicy,
) -> Result<(), String> {
    let payload_digest = job
        .payload_digest
        .as_deref()
        .ok_or("planned realtime Batch is missing its payload digest")?;
    update_realtime_state(state_file, |state| {
        if realtime_state::realtime_batch(state, &job.session_id, &job.job_id)
            .is_some_and(|batch| batch.status == realtime_state::RealtimeBatchStatus::Failure)
        {
            realtime_state::prepare_failed_realtime_batch_retry_in_state(
                state,
                &job.session_id,
                &job.job_id,
            )?;
        }
        realtime_state::publish_realtime_batch_in_state(state, &job.session_id, &job.job_id)?;
        realtime_state::begin_direct_realtime_delivery_in_state(
            state,
            &job.session_id,
            &job.job_id,
            payload_digest,
            retry_policy,
        )?;
        realtime_state::begin_realtime_delivery_plan_chunks_in_state(
            state,
            &job.session_id,
            plan_id,
            &[cursor],
            retry_policy,
        )?;
        Ok(())
    })
}

#[allow(clippy::too_many_arguments)]
fn checkpoint_realtime_delivery_plan_chunk_finish(
    state_file: &Path,
    plan_id: &str,
    cursor: u64,
    job: &spool::SpoolJob,
    retry_policy: RetryPolicy,
    accepted: bool,
    permanent: bool,
    error: Option<&str>,
) -> Result<(), String> {
    let payload_digest = job
        .payload_digest
        .as_deref()
        .ok_or("planned realtime Batch is missing its payload digest")?;
    update_realtime_state(state_file, |state| {
        if accepted {
            realtime_state::complete_realtime_delivery_in_state(
                state,
                &job.session_id,
                &job.job_id,
                payload_digest,
            )?;
            realtime_state::succeed_realtime_batch_in_state(state, &job.session_id, &job.job_id)?;
            realtime_state::complete_realtime_batch_compensation_in_state(
                state,
                &job.session_id,
                &job.job_id,
            )?;
        } else {
            let message = error.unwrap_or("realtime delivery failed");
            realtime_state::fail_realtime_delivery_in_state(
                state,
                &job.session_id,
                &job.job_id,
                payload_digest,
                retry_policy,
                message,
                retry_policy == RetryPolicy::AtLeastOnce && !permanent,
                permanent,
            )?;
            realtime_state::fail_realtime_batch_in_state(
                state,
                &job.session_id,
                &job.job_id,
                message,
            )?;
        }
        realtime_state::finish_realtime_delivery_plan_chunk_in_state(
            state,
            &job.session_id,
            plan_id,
            cursor,
            retry_policy,
            accepted,
            permanent,
            error,
        )?;
        Ok(())
    })
}

#[allow(clippy::too_many_arguments)]
fn finalize_realtime_delivery_plan_rounds(
    state_file: &Path,
    flush_job: &spool::SpoolJob,
    context_id: &str,
    plan: &realtime_state::RealtimeDeliveryPlan,
    batch_jobs: &[spool::SpoolJob],
    scan: GenerationScanCommit,
    rounds: &[GenerationRoundRecord],
    finalizer_only_ordinals: &[u64],
) -> Result<(), String> {
    for (chunk, batch_job) in plan.chunks.iter().zip(batch_jobs) {
        let (round_status, terminal_reason) = generation_round_outcome_for_delivery_chunk(chunk)?;
        let chunk_rounds = rounds
            .iter()
            .filter(|(_, generation_id, _)| {
                chunk
                    .child_ids
                    .contains(&format!("generation:{generation_id}"))
            })
            .cloned()
            .collect::<Vec<_>>();
        if chunk_rounds.is_empty() {
            continue;
        }
        record_realtime_generation_rounds_with_status(
            state_file,
            &flush_job.session_id,
            context_id,
            batch_job,
            scan,
            &chunk_rounds,
            &[],
            round_status,
            terminal_reason,
        )?;
    }
    let oversized_rounds = rounds
        .iter()
        .filter(|(_, generation_id, _)| {
            plan.oversized_child_ids
                .contains(&format!("generation:{generation_id}"))
        })
        .cloned()
        .collect::<Vec<_>>();
    if !oversized_rounds.is_empty() {
        record_realtime_generation_rounds_with_status(
            state_file,
            &flush_job.session_id,
            context_id,
            flush_job,
            scan,
            &oversized_rounds,
            &[],
            "policy_terminal",
            Some("realtime_child_oversize"),
        )?;
    }
    record_realtime_generation_rounds_with_status(
        state_file,
        &flush_job.session_id,
        context_id,
        flush_job,
        scan,
        &[],
        finalizer_only_ordinals,
        "deferred",
        None,
    )
}

fn generation_round_outcome_for_delivery_chunk(
    chunk: &realtime_state::RealtimeDeliveryChunkPlanEntry,
) -> Result<(&'static str, Option<&'static str>), String> {
    use realtime_state::RealtimeDeliveryChunkStatus;

    match (&chunk.status, chunk.last_error.as_deref()) {
        (RealtimeDeliveryChunkStatus::Accounted, _) => Ok(("accounted", None)),
        (RealtimeDeliveryChunkStatus::PolicyTerminal, Some(_)) => {
            Ok(("policy_terminal", Some("at_most_once_transport_failure")))
        }
        (RealtimeDeliveryChunkStatus::PolicyTerminal, None) => Ok((
            "policy_terminal",
            Some("at_most_once_interrupted_after_checkpoint"),
        )),
        (RealtimeDeliveryChunkStatus::PermanentFailed, _) => {
            Ok(("policy_terminal", Some("http_permanent_failure")))
        }
        (status, _) => Err(format!(
            "cannot finalize nonterminal realtime delivery chunk: cursor={} status={status:?}",
            chunk.cursor
        )),
    }
}

fn process_realtime_generation(
    job: &spool::SpoolJob,
    state_file: &Path,
    config: &LangfuseConfig,
    logger: &Logger,
    timeout: Duration,
    retry_policy: RetryPolicy,
    lease_duration: Duration,
) -> Result<(), String> {
    let state = load_state(state_file);
    let trigger = generation_trigger_for_job(job)
        .map_err(|_| format!("{REALTIME_GENERATION_DEFERRED}invalid_trigger"))?;
    if !realtime_job_schema_identity_matches(&state, job) {
        return Err(format!(
            "{REALTIME_GENERATION_DEFERRED}invalid_trigger_identity"
        ));
    }
    let transcript_path = trigger.transcript_path.as_str();
    let scan_anchor = trigger.scan_anchor;
    let batch_size = trigger.generation_batch_size;
    let context_id = trigger.context_id.as_str();
    let current_anchor = realtime_state::generation_scan_anchor(&state, &job.session_id);
    if current_anchor != scan_anchor {
        logger.info(&format!(
            "Realtime generation scan already reconciled session={} job={} scan_anchor={} current_anchor={}",
            job.session_id, job.job_id, scan_anchor, current_anchor
        ));
        let payload_digest = job
            .payload_digest
            .as_deref()
            .ok_or("realtime generation job missing payload_digest")?;
        update_realtime_state(state_file, |state| {
            realtime_state::compensate_realtime_job_in_state(
                state,
                &job.session_id,
                &job.job_id,
                payload_digest,
            )
        })?;
        return Ok(());
    }

    let prepared = prepare_generation_window(
        &job.session_id,
        state_file,
        Path::new(transcript_path),
        scan_anchor,
        logger,
    )
    .map_err(|error| classify_realtime_preparation_error(error, REALTIME_GENERATION_DEFERRED))?;
    let PreparedGenerationWindow {
        entries,
        next_offset,
        window_terminal,
        discovery_progressed,
    } = match prepared {
        GenerationWindowPreparation::Semantic(window) => window,
        GenerationWindowPreparation::Waiting => {
            return Err(format!(
                "{REALTIME_GENERATION_DEFERRED}waiting_for_more_data"
            ));
        }
        GenerationWindowPreparation::FinalizerOnly(_) => {
            let payload_digest = job
                .payload_digest
                .as_deref()
                .ok_or("realtime generation job missing payload_digest")?;
            update_realtime_state(state_file, |state| {
                realtime_state::compensate_realtime_job_in_state(
                    state,
                    &job.session_id,
                    &job.job_id,
                    payload_digest,
                )
            })?;
            return Ok(());
        }
    };
    let turns = build_turns_for_generation_window(
        state_file,
        &state,
        &job.session_id,
        Path::new(transcript_path),
        &entries,
        scan_anchor,
        next_offset,
    )?;
    let (turn_index, trigger_assistant_index) =
        resolve_realtime_generation_boundary(&trigger, &turns).map_err(|reason| {
            format!(
                "{REALTIME_GENERATION_DEFERRED}{}:{reason}",
                if discovery_progressed {
                    "scan_progressed"
                } else {
                    "waiting_for_more_data"
                }
            )
        })?;
    let scan_base_ordinal = realtime_state::generation_scan_base_ordinal(&state, &job.session_id);
    let window_candidate_count = turns
        .iter()
        .map(Turn::semantic_assistant_count)
        .sum::<usize>() as u64;
    let exported_ordinals = realtime_generation_rounds(&state, &job.session_id)
        .into_iter()
        .map(|round| round.ordinal)
        .collect::<HashSet<_>>();
    let context_snapshot = realtime_state::contexts(&state, &job.session_id)
        .into_iter()
        .find(|candidate| candidate.context_id == context_id);
    let trace_id = job
        .otel
        .get("trace_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| {
            context_snapshot
                .as_ref()
                .map(|context| context.trace_id.clone())
        })
        .ok_or("realtime generation job missing trace_id")?;
    let root_span_id = job
        .otel
        .get("root_span_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| context_snapshot.map(|context| context.root_span_id))
        .ok_or("realtime generation job missing root_span_id")?;
    let mut turn_topologies = (0..=turn_index)
        .filter_map(|index| {
            resolve_finalizer_turn_topology_with_scan_base(
                &state,
                &job.session_id,
                &turns,
                index,
                scan_base_ordinal,
            )
            .into_exact_ids()
            .map(|topology| (index, topology))
        })
        .collect::<HashMap<_, _>>();
    turn_topologies
        .entry(turn_index)
        .or_insert((trace_id, root_span_id));
    let eligible_turns = turn_topologies.keys().copied().collect::<HashSet<_>>();
    let Some((selections, rounds)) = select_realtime_generation_batch_from_turns(
        &turns,
        &job.session_id,
        context_id,
        batch_size,
        scan_base_ordinal,
        &exported_ordinals,
        &eligible_turns,
        (turn_index, trigger_assistant_index),
        window_terminal,
    ) else {
        return Err(format!("{REALTIME_GENERATION_DEFERRED}batch_not_ready"));
    };
    let selected_ordinals = rounds
        .iter()
        .map(|(ordinal, _, _)| *ordinal)
        .collect::<HashSet<_>>();
    let finalizer_only_ordinals = if window_terminal {
        (0..window_candidate_count)
            .map(|relative| scan_base_ordinal.saturating_add(relative))
            .filter(|ordinal| {
                !exported_ordinals.contains(ordinal) && !selected_ordinals.contains(ordinal)
            })
            .collect::<Vec<_>>()
    } else {
        Vec::new()
    };
    let mut body = build_realtime_generation_payload_for_turns(
        &turns,
        &selections,
        &turn_topologies,
        batch_size,
        window_terminal,
        job_username(job),
        &job.session_id,
        job_hook_version(job),
        &state,
    )?;
    let turn_cwd = turns
        .get(turn_index)
        .map(|turn| turn.cwd.trim())
        .filter(|cwd| !cwd.is_empty());
    if let Some(vcs) = get_vcs_for_cwd(&state, &job.session_id, turn_cwd) {
        inject_vcs_resource_attrs(&mut body, &vcs.remote_url, &vcs.branch);
    }
    let scan_commit = GenerationScanCommit {
        scan_anchor,
        next_offset,
        scan_base_ordinal,
        window_candidate_count,
        window_terminal,
    };

    checkpoint_realtime_delivery_begin(state_file, job, retry_policy, lease_duration)
        .map_err(|e| format!("checkpoint realtime generation delivery begin: {e}"))?;
    if retry_policy == RetryPolicy::AtMostOnce {
        record_realtime_generation_rounds_for_job(
            state_file,
            &job.session_id,
            context_id,
            job,
            scan_commit,
            &rounds,
            &finalizer_only_ordinals,
        )?;
    }
    post_otel_to_langfuse_once(config, &body, logger, timeout)?;
    if retry_policy == RetryPolicy::AtLeastOnce {
        record_realtime_generation_rounds_for_job(
            state_file,
            &job.session_id,
            context_id,
            job,
            scan_commit,
            &rounds,
            &finalizer_only_ordinals,
        )?;
    }
    Ok(())
}

fn resolve_realtime_generation_boundary(
    trigger: &RealtimeGenerationTrigger,
    turns: &[Turn],
) -> Result<(usize, usize), &'static str> {
    match &trigger.correlation {
        RealtimeGenerationCorrelation::NativeTurn { turn_id, .. } => {
            let matching_turns = turns
                .iter()
                .enumerate()
                .filter(|(_, turn)| turn.turn_id == *turn_id)
                .collect::<Vec<_>>();
            let [(turn_index, turn)] = matching_turns.as_slice() else {
                return Err("ambiguous_trigger_turn");
            };
            let Some(boundary) = turn
                .tool_calls
                .iter()
                .filter(|tool| !tool.ended_timestamp.is_empty())
                .map(|tool| tool.assistant_index)
                .max()
            else {
                return Err("no_completed_tool_boundary");
            };
            Ok((*turn_index, boundary))
        }
        RealtimeGenerationCorrelation::LegacyTool { tool_use_id } => {
            let matches = turns
                .iter()
                .enumerate()
                .flat_map(|(turn_index, turn)| {
                    turn.tool_calls
                        .iter()
                        .filter(move |tool| tool.call_id == *tool_use_id)
                        .map(move |tool| (turn_index, tool))
                })
                .collect::<Vec<_>>();
            let [(turn_index, tool)] = matches.as_slice() else {
                return Err("ambiguous_trigger_turn");
            };
            if tool.ended_timestamp.is_empty() {
                return Err("no_completed_tool_boundary");
            }
            Ok((*turn_index, tool.assistant_index))
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn select_realtime_generation_batch_from_turns(
    turns: &[Turn],
    session_id: &str,
    context_id: &str,
    batch_size: usize,
    scan_base_ordinal: u64,
    exported_ordinals: &HashSet<u64>,
    eligible_turns: &HashSet<usize>,
    through: (usize, usize),
    flush_partial: bool,
) -> Option<(
    Vec<MultiTurnGenerationSelection>,
    Vec<GenerationRoundRecord>,
)> {
    let batch_size = batch_size.max(1);
    let mut candidates = Vec::new();
    let mut window_ordinal = 0_u64;
    for (turn_index, turn) in turns.iter().enumerate() {
        if turn_index > through.0 {
            break;
        }
        for (assistant_index, _) in turn.assistants.iter().enumerate() {
            if turn_index == through.0 && assistant_index > through.1 {
                break;
            }
            if !turn.has_semantic_assistant_at(assistant_index) {
                continue;
            }
            let global_ordinal = scan_base_ordinal.saturating_add(window_ordinal);
            window_ordinal = window_ordinal.saturating_add(1);
            if exported_ordinals.contains(&global_ordinal) || !eligible_turns.contains(&turn_index)
            {
                continue;
            }
            candidates.push((turn_index, assistant_index, global_ordinal));
        }
    }

    let selected_count = if flush_partial {
        candidates.len()
    } else {
        candidates.len() / batch_size * batch_size
    };
    if selected_count == 0 {
        return None;
    }

    let mut selections = Vec::with_capacity(selected_count);
    let mut rounds = Vec::with_capacity(selected_count);
    for (turn_index, assistant_index, global_ordinal) in candidates.into_iter().take(selected_count)
    {
        let generation_id = realtime_state::stable_fallback_generation_id(
            session_id,
            context_id,
            global_ordinal as usize,
        );
        let span_id = realtime_state::stable_generation_span_id(&generation_id);
        selections.push((turn_index, assistant_index, span_id.clone()));
        rounds.push((global_ordinal, generation_id, span_id));
    }
    Some((selections, rounds))
}

fn record_realtime_generation_rounds_for_job(
    state_file: &Path,
    session_id: &str,
    context_id: &str,
    job: &spool::SpoolJob,
    scan: GenerationScanCommit,
    rounds: &[GenerationRoundRecord],
    finalizer_only_ordinals: &[u64],
) -> Result<(), String> {
    record_realtime_generation_rounds_with_status(
        state_file,
        session_id,
        context_id,
        job,
        scan,
        rounds,
        finalizer_only_ordinals,
        "inflight",
        None,
    )
}

#[allow(clippy::too_many_arguments)]
fn record_realtime_generation_rounds_with_status(
    state_file: &Path,
    session_id: &str,
    context_id: &str,
    job: &spool::SpoolJob,
    scan: GenerationScanCommit,
    rounds: &[GenerationRoundRecord],
    finalizer_only_ordinals: &[u64],
    status: &str,
    terminal_reason: Option<&str>,
) -> Result<(), String> {
    let payload_digest = job
        .payload_digest
        .as_deref()
        .ok_or("realtime generation job missing payload_digest")?;
    update_realtime_state(state_file, |state| {
        let generation = state
            .get_mut(session_id)
            .and_then(Value::as_object_mut)
            .and_then(|session| session.get_mut("realtime_export"))
            .and_then(Value::as_object_mut)
            .and_then(|realtime| realtime.get_mut("generation"))
            .and_then(Value::as_object_mut)
            .ok_or("realtime generation state is missing")?;
        let round_values = generation
            .entry("rounds".to_string())
            .or_insert_with(|| serde_json::json!([]))
            .as_array_mut()
            .ok_or("realtime generation rounds is not an array")?;
        for (ordinal, generation_id, span_id) in rounds {
            if let Some(existing) = round_values.iter_mut().find(|value| {
                value.get("generation_id").and_then(Value::as_str) == Some(generation_id)
            }) {
                if existing.get("job_id").and_then(Value::as_str) == Some(job.job_id.as_str())
                    && existing.get("payload_digest").and_then(Value::as_str)
                        == Some(payload_digest)
                {
                    match status {
                        "accounted" => {
                            existing["status"] = serde_json::json!("accounted");
                            if let Some(object) = existing.as_object_mut() {
                                object.remove("terminal_reason");
                            }
                        }
                        "policy_terminal" => {
                            existing["status"] = serde_json::json!("policy_terminal");
                            if let Some(reason) = terminal_reason {
                                existing["terminal_reason"] = serde_json::json!(reason);
                            }
                        }
                        _ => {}
                    }
                }
                continue;
            }
            let mut round = serde_json::json!({
                "generation_id": generation_id,
                "span_id": span_id,
                "context_id": context_id,
                "ordinal": ordinal,
                "status": status,
                "job_id": job.job_id,
                "payload_digest": payload_digest,
                "claim_id": null,
                "lease_until_ms": 0,
                "attempts": 0,
            });
            if let Some(reason) = terminal_reason {
                round["terminal_reason"] = serde_json::json!(reason);
            }
            round_values.push(round);
        }
        let mut discovered_ordinals = round_values
            .iter()
            .filter_map(|round| round.get("ordinal").and_then(Value::as_u64))
            .collect::<HashSet<_>>();
        let finalizer_only_values = generation
            .entry("finalizer_only_ordinals".to_string())
            .or_insert_with(|| serde_json::json!([]))
            .as_array_mut()
            .ok_or("realtime generation finalizer-only ordinals is not an array")?;
        for ordinal in finalizer_only_ordinals {
            if !finalizer_only_values
                .iter()
                .any(|value| value.as_u64() == Some(*ordinal))
            {
                finalizer_only_values.push(serde_json::json!(ordinal));
            }
        }
        discovered_ordinals.extend(finalizer_only_values.iter().filter_map(Value::as_u64));
        let window_fully_discovered = scan.window_terminal
            && (0..scan.window_candidate_count).all(|relative| {
                discovered_ordinals.contains(&scan.scan_base_ordinal.saturating_add(relative))
            });
        let committed_offset = if window_fully_discovered {
            scan.next_offset
        } else {
            scan.scan_anchor
        };
        let next_base_ordinal = if window_fully_discovered {
            scan.scan_base_ordinal
                .saturating_add(scan.window_candidate_count)
        } else {
            scan.scan_base_ordinal
        };
        generation.insert(
            "scan_anchor".to_string(),
            serde_json::json!(committed_offset),
        );
        generation.insert(
            "committed_offset".to_string(),
            serde_json::json!(committed_offset),
        );
        generation.insert(
            "scan_base_ordinal".to_string(),
            serde_json::json!(next_base_ordinal),
        );
        generation.insert("window_consumed".to_string(), serde_json::json!(0));
        Ok(())
    })
}

fn mark_realtime_generation_rounds_accounted(
    state_file: &Path,
    job: &spool::SpoolJob,
) -> Result<(), String> {
    let payload_digest = job
        .payload_digest
        .as_deref()
        .ok_or("realtime generation job missing payload_digest")?;
    update_realtime_state(state_file, |state| {
        let Some(rounds) = state
            .get_mut(&job.session_id)
            .and_then(Value::as_object_mut)
            .and_then(|session| session.get_mut("realtime_export"))
            .and_then(Value::as_object_mut)
            .and_then(|realtime| realtime.get_mut("generation"))
            .and_then(Value::as_object_mut)
            .and_then(|generation| generation.get_mut("rounds"))
            .and_then(Value::as_array_mut)
        else {
            return Ok(());
        };
        for value in rounds {
            if value.get("job_id").and_then(Value::as_str) == Some(job.job_id.as_str())
                && value.get("payload_digest").and_then(Value::as_str) == Some(payload_digest)
            {
                value["status"] = serde_json::json!("accounted");
            }
        }
        Ok(())
    })
}

fn unique_unconsumed_tool_record<'a, F>(
    records: &'a [tool_records::ToolRecord],
    used_record_ids: &HashSet<String>,
    predicate: F,
) -> Option<&'a tool_records::ToolRecord>
where
    F: Fn(&tool_records::ToolRecord) -> bool,
{
    let mut candidates = records
        .iter()
        .filter(|record| !used_record_ids.contains(&record.tool_use_id))
        .filter(|record| predicate(record));
    let candidate = candidates.next()?;
    candidates.next().is_none().then_some(candidate)
}

fn finalizer_tool_record_for_call<'a>(
    tool: &crate::common::transcript::ToolCall,
    records: &'a [tool_records::ToolRecord],
    used_record_ids: &HashSet<String>,
) -> Option<&'a tool_records::ToolRecord> {
    if let Some(record) = records.iter().find(|record| {
        !used_record_ids.contains(&record.tool_use_id) && record.tool_use_id == tool.call_id
    }) {
        return Some(record);
    }

    if let Some(record) = unique_unconsumed_tool_record(records, used_record_ids, |record| {
        record.assistant_index == Some(tool.assistant_index)
    }) {
        return Some(record);
    }

    let transcript_started_at = agentic_otel_common::iso_to_nanos(&tool.started_timestamp)
        .parse::<u128>()
        .ok()
        .filter(|value| *value > 0)?;
    let transcript_completed_at = agentic_otel_common::iso_to_nanos(&tool.ended_timestamp)
        .parse::<u128>()
        .ok()
        .filter(|value| *value >= transcript_started_at)?;
    unique_unconsumed_tool_record(records, used_record_ids, |record| {
        if !record.tool_use_id.starts_with("exec-") {
            return false;
        }
        record.completed_timing().ok().is_some_and(|timing| {
            let started_at = timing.started_at_unix_nano.parse::<u128>().unwrap_or(0);
            let completed_at = timing.completed_at_unix_nano.parse::<u128>().unwrap_or(0);
            started_at >= transcript_started_at.saturating_sub(REALTIME_TOOL_CORRELATION_SKEW_NANOS)
                && completed_at
                    <= transcript_completed_at.saturating_add(REALTIME_TOOL_CORRELATION_SKEW_NANOS)
        })
    })
}

fn overlay_finalizer_tool_timings(
    body: &mut Value,
    turn: &Turn,
    records: &[tool_records::ToolRecord],
) -> usize {
    if records.is_empty() {
        return 0;
    }

    let mut used_record_ids = HashSet::new();
    let mut timings_by_span_id = HashMap::new();
    for tool in &turn.tool_calls {
        let Some(record) = finalizer_tool_record_for_call(tool, records, &used_record_ids) else {
            continue;
        };
        let Ok(timing) = record.completed_timing() else {
            continue;
        };
        used_record_ids.insert(record.tool_use_id.clone());
        timings_by_span_id.insert(deterministic_tool_span_id(&tool.call_id), timing);
    }
    if timings_by_span_id.is_empty() {
        return 0;
    }

    let Some(resource_spans) = body.get_mut("resourceSpans").and_then(Value::as_array_mut) else {
        return 0;
    };
    let mut overlaid = 0;
    for resource in resource_spans {
        let Some(scope_spans) = resource.get_mut("scopeSpans").and_then(Value::as_array_mut) else {
            continue;
        };
        for scope in scope_spans {
            let Some(spans) = scope.get_mut("spans").and_then(Value::as_array_mut) else {
                continue;
            };
            for span in spans {
                let Some(span_id) = span.get("spanId").and_then(Value::as_str) else {
                    continue;
                };
                let Some(timing) = timings_by_span_id.get(span_id) else {
                    continue;
                };
                span["startTimeUnixNano"] = Value::String(timing.started_at_unix_nano.clone());
                span["endTimeUnixNano"] = Value::String(timing.completed_at_unix_nano.clone());
                let duration_ms =
                    timing.duration_unix_nano.parse::<u128>().unwrap_or(0) / 1_000_000;
                if let Some(attributes) = span.get_mut("attributes").and_then(Value::as_array_mut) {
                    if let Some(attribute) = attributes
                        .iter_mut()
                        .find(|attribute| attribute["key"] == "agentic.tool.duration_ms")
                    {
                        attribute["value"] =
                            serde_json::json!({"intValue": duration_ms.to_string()});
                    } else {
                        attributes.push(serde_json::json!({
                            "key": "agentic.tool.duration_ms",
                            "value": {"intValue": duration_ms.to_string()}
                        }));
                    }
                }
                overlaid += 1;
            }
        }
    }
    overlaid
}

fn realtime_generation_rounds(
    state: &Value,
    session_id: &str,
) -> Vec<realtime_state::GenerationRound> {
    state
        .get(session_id)
        .and_then(|session| session.get("realtime_export"))
        .and_then(|realtime| realtime.get("generation"))
        .and_then(|generation| generation.get("rounds"))
        .and_then(Value::as_array)
        .map(|rounds| {
            rounds
                .iter()
                .filter_map(|round| serde_json::from_value(round.clone()).ok())
                .collect()
        })
        .unwrap_or_default()
}

fn job_string_field<'a>(job: &'a spool::SpoolJob, field: &str) -> Option<&'a str> {
    job.otel.get(field).and_then(Value::as_str)
}

fn job_username(job: &spool::SpoolJob) -> &str {
    job.username
        .as_deref()
        .or_else(|| job_string_field(job, "username"))
        .unwrap_or("unknown")
}

fn job_hook_version(job: &spool::SpoolJob) -> &str {
    job.hook_version
        .as_deref()
        .or_else(|| job_string_field(job, "hook_version"))
        .unwrap_or(env!("CARGO_PKG_VERSION"))
}

fn job_cwd(job: &spool::SpoolJob) -> Option<&str> {
    job.cwd.as_deref().or_else(|| job_string_field(job, "cwd"))
}

fn semantic_generation_ordinal(
    turns: &[Turn],
    target_turn_index: usize,
    target_assistant_index: usize,
    scan_base_ordinal: u64,
) -> Option<u64> {
    let mut ordinal = scan_base_ordinal;
    for (turn_index, turn) in turns.iter().enumerate() {
        for (assistant_index, _) in turn.assistants.iter().enumerate() {
            if !turn.has_semantic_assistant_at(assistant_index) {
                continue;
            }
            if turn_index == target_turn_index && assistant_index == target_assistant_index {
                return Some(ordinal);
            }
            ordinal = ordinal.saturating_add(1);
        }
    }
    None
}

fn entries_contain_native_usage(records: &[Value]) -> bool {
    records.iter().any(has_native_usage_record)
}

fn transcript_first_native_usage_offset(
    transcript_path: &Path,
    start_offset: u64,
    end_offset: u64,
) -> Result<Option<u64>, String> {
    if end_offset <= start_offset {
        return Ok(None);
    }
    let mut file = std::fs::File::open(transcript_path)
        .map_err(|error| format!("open transcript for native usage offset scan: {error}"))?;
    file.seek(SeekFrom::Start(start_offset))
        .map_err(|error| format!("seek transcript for native usage offset scan: {error}"))?;
    let mut reader = std::io::BufReader::new(file);
    let mut offset = start_offset;
    loop {
        let mut line = Vec::new();
        let read = reader
            .read_until(b'\n', &mut line)
            .map_err(|error| format!("read transcript for native usage offset scan: {error}"))?;
        if read == 0 {
            return Ok(None);
        }
        offset = offset.saturating_add(read as u64);
        if offset > end_offset {
            return Ok(None);
        }
        let Ok(text) = std::str::from_utf8(&line) else {
            continue;
        };
        let trimmed = text.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Ok(entry) = serde_json::from_str::<Value>(trimmed) {
            if has_native_usage_record(&entry) {
                return Ok(Some(offset));
            }
        }
    }
}

fn transcript_native_usage_prefix_scan_start(
    transcript_path: &Path,
    checked_offset: u64,
) -> Result<u64, String> {
    if checked_offset == 0 {
        return Ok(0);
    }
    let mut file = std::fs::File::open(transcript_path)
        .map_err(|error| format!("open transcript for native usage prefix scan start: {error}"))?;
    let file_len = file
        .metadata()
        .map_err(|error| format!("stat transcript for native usage prefix scan start: {error}"))?
        .len();
    let checked_offset = checked_offset.min(file_len);
    if checked_offset == 0 {
        return Ok(0);
    }
    file.seek(SeekFrom::Start(checked_offset.saturating_sub(1)))
        .map_err(|error| format!("seek transcript for native usage prefix scan start: {error}"))?;
    let mut byte = [0_u8; 1];
    file.read_exact(&mut byte)
        .map_err(|error| format!("read transcript for native usage prefix scan start: {error}"))?;
    if byte[0] == b'\n' {
        return Ok(checked_offset);
    }

    let mut block_end = checked_offset.saturating_sub(1);
    while block_end > 0 {
        let block_start = block_end.saturating_sub(NATIVE_USAGE_PREFIX_REWIND_BLOCK_BYTES);
        let block_len = block_end.saturating_sub(block_start) as usize;
        let mut block = vec![0_u8; block_len];
        file.seek(SeekFrom::Start(block_start)).map_err(|error| {
            format!("seek transcript for native usage prefix scan start: {error}")
        })?;
        file.read_exact(&mut block).map_err(|error| {
            format!("read transcript for native usage prefix scan start: {error}")
        })?;
        if let Some(index) = block.iter().rposition(|candidate| *candidate == b'\n') {
            return Ok(block_start.saturating_add(index as u64).saturating_add(1));
        }
        block_end = block_start;
    }
    Ok(0)
}

fn build_turns_for_generation_window(
    state_file: &Path,
    state: &Value,
    session_id: &str,
    transcript_path: &Path,
    entries: &[Value],
    window_start_offset: u64,
    window_next_offset: u64,
) -> Result<Vec<Turn>, String> {
    let mut initial_native_mode =
        realtime_state::generation_native_usage_mode_at(state, session_id, window_start_offset);
    let mut effective_offset =
        realtime_state::generation_native_usage_mode_effective_offset(state, session_id);
    let checked_offset = if realtime_state::generation_source_epoch(state, session_id).is_some() {
        realtime_state::generation_native_usage_prefix_checked_offset(state, session_id)
    } else {
        None
    };
    if !initial_native_mode
        && window_start_offset > 0
        && effective_offset.is_none()
        && checked_offset.unwrap_or(0) < window_start_offset
    {
        let scan_start = transcript_native_usage_prefix_scan_start(
            transcript_path,
            checked_offset.unwrap_or(0).min(window_start_offset),
        )?;
        if let Some(native_offset) =
            transcript_first_native_usage_offset(transcript_path, scan_start, window_start_offset)?
        {
            update_realtime_state(state_file, |state| {
                realtime_state::mark_generation_native_usage_mode_in_state(
                    state,
                    session_id,
                    native_offset,
                )
            })?;
            initial_native_mode = true;
            effective_offset = Some(native_offset);
        } else {
            update_realtime_state(state_file, |state| {
                realtime_state::mark_generation_native_usage_prefix_checked_in_state(
                    state,
                    session_id,
                    window_start_offset,
                )
            })?;
        }
    }
    if entries_contain_native_usage(entries)
        && effective_offset.is_none_or(|current| current > window_next_offset)
    {
        let native_offset = transcript_first_native_usage_offset(
            transcript_path,
            window_start_offset,
            window_next_offset,
        )?
        .ok_or_else(|| {
            "native usage record observed in generation window but transcript offset was not resolved"
                .to_string()
        })?;
        update_realtime_state(state_file, |state| {
            realtime_state::mark_generation_native_usage_mode_in_state(
                state,
                session_id,
                native_offset,
            )
        })?;
    }
    Ok(build_turns_with_usage_context(
        entries,
        None,
        initial_native_mode,
    ))
}

fn completed_prefix_offset_for_jsonl(
    transcript_path: &Path,
    completed_record_count: usize,
) -> Result<i64, String> {
    if completed_record_count == 0 {
        return Ok(0);
    }
    let file = std::fs::File::open(transcript_path)
        .map_err(|error| format!("open transcript for completed prefix offset: {error}"))?;
    let mut reader = std::io::BufReader::new(file);
    let mut offset = 0_u64;
    let mut parsed_records = 0_usize;
    loop {
        let mut line = Vec::new();
        let read = reader
            .read_until(b'\n', &mut line)
            .map_err(|error| format!("read transcript for completed prefix offset: {error}"))?;
        if read == 0 {
            break;
        }
        offset = offset.saturating_add(read as u64);
        let Ok(text) = std::str::from_utf8(&line) else {
            continue;
        };
        let trimmed = text.trim();
        if trimmed.is_empty() {
            continue;
        }
        if serde_json::from_str::<Value>(trimmed).is_ok() {
            parsed_records = parsed_records.saturating_add(1);
            if parsed_records == completed_record_count {
                return i64::try_from(offset)
                    .map_err(|_| "completed prefix offset is out of range".to_string());
            }
        }
    }
    Err("completed prefix offset could not be resolved".to_string())
}

fn process_transcript(
    job: &spool::SpoolJob,
    config: &LangfuseConfig,
    logger: &Logger,
    timeout: Duration,
    finalizer: Option<FinalizerReconcile>,
    recovery_config_path: Option<&str>,
    recovery_profile: Option<&str>,
) -> Result<(), String> {
    let path = job
        .transcript_path
        .as_deref()
        .or_else(|| job.otel.get("transcript_path").and_then(Value::as_str))
        .ok_or("transcript job missing path")?;
    let session_id = &job.session_id;
    let deadline = std::time::Instant::now() + transcript_terminal_timeout();
    let mut last_len = None;
    let (parsed, completed, transcript_offset) = loop {
        let file_len = std::fs::metadata(path)
            .map_err(|e| format!("stat transcript: {e}"))?
            .len();
        if last_len == Some(file_len) && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(100));
            continue;
        }
        last_len = Some(file_len);
        let (parsed, parsed_offset, partial) = read_new_jsonl(Path::new(path), 0, "");
        if !partial.is_empty() && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(100));
            continue;
        }
        if !partial.is_empty() {
            return Err("transcript still has incomplete JSONL tail".to_string());
        }
        let (completed, pending) = split_completed_messages(&parsed);
        let allow_pending_successor = finalizer.is_some() && !completed.is_empty();
        if !pending.is_empty() && !allow_pending_successor && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(100));
            continue;
        }
        if !pending.is_empty() && !allow_pending_successor {
            return Err("transcript still has no authoritative terminal marker".to_string());
        }
        let completed_offset = if pending.is_empty() {
            parsed_offset
        } else {
            completed_prefix_offset_for_jsonl(Path::new(path), completed.len())?
        };
        break (parsed, completed, completed_offset);
    };
    let transcript_offset = transcript_offset.max(0);
    let transcript_offset_u64 = transcript_offset as u64;
    let turns = if completed.len() == parsed.len() {
        build_turns(&completed)
    } else {
        let completed_ids = build_turns(&completed)
            .into_iter()
            .map(|turn| turn.turn_id)
            .collect::<HashSet<_>>();
        build_turns(&parsed)
            .into_iter()
            .filter(|turn| completed_ids.contains(&turn.turn_id))
            .collect::<Vec<_>>()
    };
    if turns.is_empty() {
        return Err("transcript has no turns".to_string());
    }
    let state_file = session_state_file(session_id)?;
    let mut state = load_state(&state_file);
    let now = crate::common::logger::now_iso8601_pub();
    if let Some(recovery) =
        arm_legacy_finalizer_recovery(&state_file, &mut state, session_id, &now, path)?
    {
        logger.info(&format!(
            "Worker armed legacy finalizer recovery session={} recovery_key={} attempt={} status={} newly_armed={}",
            session_id, recovery.recovery_key, recovery.attempt, recovery.status, recovery.armed
        ));
    }
    let exported_count = get_session_state(&state, session_id)
        .map(|s| get_int_field(s, "turn_count").max(0) as usize)
        .unwrap_or(0);
    let durably_accounted_turn_ids = get_session_state(&state, session_id)
        .map(|session| {
            durably_accounted_turn_ids_for_history(
                session,
                turns.iter().map(|turn| turn.turn_id.as_str()),
            )
        })
        .unwrap_or_default();
    let mut successful_turn_count = exported_count;
    let retry_policy = finalizer
        .map(|finalizer| finalizer.retry_policy)
        .unwrap_or_else(|| {
            std::env::var("CDXHOOKS_RETRY_POLICY")
                .ok()
                .and_then(|value| RetryPolicy::parse(&value))
                .unwrap_or(RetryPolicy::AtLeastOnce)
        });
    let mut finalizer_begin_done = false;
    let finalizer_accounted_children = finalizer
        .map(|finalizer| {
            accounted_realtime_child_span_ids(&state, session_id, finalizer.high_water)
        })
        .unwrap_or_default();
    for (idx, turn) in turns.iter().enumerate() {
        if durably_accounted_turn_ids.contains(&turn.turn_id) {
            continue;
        }
        let posted = get_session_state(&state, session_id)
            .map(|s| get_string_array_field(s, "posted_turn_ids"))
            .unwrap_or_default();
        if posted.iter().any(|id| id == &turn.turn_id) {
            continue;
        }
        let mut trace_opts = build_turn_trace_opts(&state, session_id, &turn.turn_id);
        if let Some(finalizer) = finalizer {
            match realtime_state::canonicalize_turn_contexts_in_state(
                &mut state,
                session_id,
                &turn.turn_id,
            ) {
                Ok(Some(canonical)) => logger.debug(&format!(
                    "Realtime worker finalizer canonicalization action={} session={} turn_id={} context_id={} aliases={}",
                    canonical.action.as_str(),
                    session_id,
                    turn.turn_id,
                    canonical.context.context_id,
                    canonical.aliases.len()
                )),
                Ok(None) => {}
                Err(error) => {
                    let now = crate::common::logger::now_iso8601_pub();
                    let terminal_high_water = transcript_offset;
                    let (reason, root_identities) = structural_topology_quarantine(
                        &error,
                        &state,
                        session_id,
                        &turn.turn_id,
                    );
                    let recovery_key =
                        finalizer_recovery_key(path, &turn.turn_id, terminal_high_water);
                    let attempt = claim_finalizer_recovery_attempt(
                        &state_file,
                        &mut state,
                        session_id,
                        &now,
                        path,
                        &recovery_key,
                    )
                    .map_err(|persist_error| {
                        format!(
                            "claim terminal realtime quarantine attempt for turn {}: {persist_error}",
                            turn.turn_id
                        )
                    })?
                    .attempt;
                    let transition = record_terminal_quarantined_turn(
                        &state_file,
                        &mut state,
                        session_id,
                        &turn.turn_id,
                        reason,
                        &root_identities,
                        &now,
                        attempt,
                        Some(&recovery_key),
                        Some(terminal_high_water),
                    )
                    .map_err(|persist_error| {
                        format!(
                            "persist terminal realtime quarantine for turn {}: {persist_error}",
                            turn.turn_id
                        )
                    })?;
                    logger.warn(&format!(
                        "Realtime worker terminally quarantined turn with structural topology error session={} turn_id={} reason={} evidence={} appended={} high_water={} error={}",
                        session_id,
                        turn.turn_id,
                        reason,
                        root_identities.len(),
                        transition.appended,
                        terminal_high_water,
                        error
                    ));
                    continue;
                }
            }
            let scan_base_ordinal =
                realtime_state::generation_scan_base_ordinal(&state, session_id);
            let topology = resolve_finalizer_turn_topology_with_scan_base(
                &state,
                session_id,
                &turns,
                idx,
                scan_base_ordinal,
            );
            let recovery_key = finalizer_recovery_key(path, &turn.turn_id, transcript_offset);
            let now = crate::common::logger::now_iso8601_pub();
            let decision = persist_finalizer_topology_decision(
                &state_file,
                &mut state,
                session_id,
                &turn.turn_id,
                topology,
                &now,
                Some(&recovery_key),
                finalizer.high_water,
                Some(transcript_offset),
                path,
            )
            .map_err(|error| {
                format!(
                    "persist realtime finalizer decision for turn {}: {error}",
                    turn.turn_id
                )
            })?;
            let trace_root = match decision {
                FinalizerDecision::Exact {
                    trace_id,
                    root_span_id,
                } => Some((trace_id, root_span_id)),
                FinalizerDecision::LegacyFallback => {
                    logger.info(&format!(
                        "Realtime worker finalizer using LegacyFallback session={} turn_id={}",
                        session_id, turn.turn_id
                    ));
                    None
                }
                FinalizerDecision::TerminalQuarantined { reason, appended } => {
                    logger.warn(&format!(
                        "Realtime worker terminally quarantined finalizer topology session={} turn_id={} reason={} appended={}",
                        session_id, turn.turn_id, reason, appended
                    ));
                    continue;
                }
                FinalizerDecision::Deferred { reason } => {
                    return Err(format!(
                        "{REALTIME_FINALIZER_TOPOLOGY_DEFERRED}turn_id={}:{}",
                        turn.turn_id, reason
                    ));
                }
            };
            if let Some((trace_id, root_span_id)) = trace_root {
                crate::hooks::langfuse::turn_trace_identity::apply_exact_trace_root_metadata(
                    &state,
                    session_id,
                    &mut trace_opts,
                    trace_id,
                    root_span_id,
                );
            }
            let generation_span_ids = finalizer_generation_span_ids_for_turn_with_scan_base(
                &state,
                session_id,
                &turns,
                idx,
                finalizer.high_water,
                scan_base_ordinal,
            );
            if !generation_span_ids.is_empty() {
                trace_opts
                    .get_or_insert_with(TurnReplayOpts::default)
                    .generation_span_ids
                    .extend(generation_span_ids);
            }
        }
        let mut body = build_otel_export_request_for_turn(
            turn,
            idx + 1,
            session_id,
            job_username(job),
            job_hook_version(job),
            trace_opts.as_ref(),
        );
        let vcs_info = get_vcs_for_cwd(&state, session_id, job_cwd(job));
        if let Some(vcs) = vcs_info.as_ref() {
            inject_vcs_resource_attrs(&mut body, &vcs.remote_url, &vcs.branch);
        }
        let transcript_dir = Path::new(path).parent().unwrap_or_else(|| Path::new("."));
        let sessions_dir = dirs::sessions_dir();
        let sessions_dir_text = sessions_dir.to_string_lossy();
        let merge_result = match merge_child_sessions_into_body(
            &mut body,
            &turns,
            &turns,
            idx,
            session_id,
            transcript_dir,
            path,
            Some(sessions_dir_text.as_ref()),
            job_username(job),
            job_hook_version(job),
            logger,
            None,
        ) {
            ChildMergeOutcome::Completed(result) => result,
            ChildMergeOutcome::BudgetExhausted => {
                return Err("subagent merge budget exhausted".to_string());
            }
        };
        let track_subagents = !merge_result.deferred_sync_ids.is_empty()
            || !merge_result.merged_sync_ids.is_empty()
            || subagent_recovery_records(&state, session_id)
                .iter()
                .any(|r| r.parent_turn_id == turn.turn_id);
        if track_subagents {
            crate::hooks::langfuse::subagent_progress::register(
                &state_file,
                &mut state,
                session_id,
                &turn.turn_id,
                path,
                &body,
                &merge_result.deferred_sync_ids,
                job_username(job),
                job_hook_version(job),
                Some(recovery_delivery_context_for_worker(
                    config,
                    retry_policy,
                    recovery_config_path,
                    recovery_profile,
                )),
            )?;
            if let Some(record) = subagent_recovery_records(&state, session_id)
                .into_iter()
                .find(|r| r.parent_turn_id == turn.turn_id)
            {
                crate::hooks::langfuse::subagent_progress::suppress_accounted_children(
                    &mut body, &record, session_id,
                );
            }
        }
        let merged_subagent_ids = merge_result.merged_sync_ids;
        let merged_subagent_invocation_ids = merge_result.merged_subagent_invocation_ids;
        if finalizer.is_some() {
            let completed_tool_records =
                tool_records::completed_for_turn(&state_file, session_id, &turn.turn_id);
            let overlaid = overlay_finalizer_tool_timings(&mut body, turn, &completed_tool_records);
            if overlaid > 0 {
                logger.info(&format!(
                    "Realtime finalizer overlaid hook-pair timing on {overlaid} Tool span(s) session={session_id} turn={}",
                    turn.turn_id
                ));
            }
            let removed = suppress_child_spans_by_id(&mut body, &finalizer_accounted_children);
            if removed > 0 {
                logger.info(&format!(
                    "Realtime finalizer suppressed {removed} accounted child span(s) session={session_id}"
                ));
            }
        }
        let now = crate::common::logger::now_iso8601_pub();
        if retry_policy == RetryPolicy::AtMostOnce {
            checkpoint_turn_export_attempt(
                &state_file,
                &mut state,
                session_id,
                &turn.turn_id,
                &now,
                Some(path),
                &merged_subagent_ids,
                &merged_subagent_invocation_ids,
            )
            .map_err(|e| format!("checkpoint export attempt: {e}"))?;
        }
        if let Some(finalizer) = finalizer {
            if !finalizer_begin_done {
                checkpoint_realtime_delivery_begin(
                    &state_file,
                    job,
                    finalizer.retry_policy,
                    finalizer.lease_duration,
                )
                .map_err(|e| format!("checkpoint realtime finalizer delivery begin: {e}"))?;
                finalizer_begin_done = true;
            }
        }
        if track_subagents && retry_policy == RetryPolicy::AtMostOnce {
            account(
                &state_file,
                &mut state,
                session_id,
                &turn.turn_id,
                &body,
                &merge_result.deferred_sync_ids,
            )?;
        }
        post_otel_to_langfuse_once(config, &body, logger, timeout)?;
        if track_subagents && retry_policy == RetryPolicy::AtLeastOnce {
            account(
                &state_file,
                &mut state,
                session_id,
                &turn.turn_id,
                &body,
                &merge_result.deferred_sync_ids,
            )?;
        }

        if retry_policy == RetryPolicy::AtLeastOnce {
            checkpoint_turn_export_success(
                &state_file,
                &mut state,
                session_id,
                &turn.turn_id,
                &now,
                Some(path),
                &merged_subagent_ids,
                &merged_subagent_invocation_ids,
            )
            .map_err(|e| format!("checkpoint export success: {e}"))?;
        }
        let mut committed_ids = posted;
        committed_ids.push(turn.turn_id.clone());
        commit_export_success(
            &state_file,
            &mut state,
            session_id,
            transcript_offset,
            "",
            &[],
            &committed_ids,
            successful_turn_count.saturating_add(1),
            &now,
            Some(path),
            &merged_subagent_ids,
            &merged_subagent_invocation_ids,
        )
        .map_err(|e| format!("commit export success: {e}"))?;
        successful_turn_count = successful_turn_count.saturating_add(1);
    }
    if let Some(finalizer) = finalizer {
        if !finalizer_begin_done {
            checkpoint_realtime_delivery_begin(
                &state_file,
                job,
                finalizer.retry_policy,
                finalizer.lease_duration,
            )
            .map_err(|e| format!("checkpoint realtime finalizer delivery begin: {e}"))?;
        }
        let generation_count = turns
            .iter()
            .map(Turn::semantic_assistant_count)
            .sum::<usize>() as u64;
        finalize_realtime_high_water(
            &state_file,
            &mut state,
            session_id,
            finalizer.high_water,
            transcript_offset_u64,
            generation_count,
        )?;
        state = load_state(&state_file);
    }
    let final_state = get_session_state(&state, session_id)
        .cloned()
        .unwrap_or_default();
    let final_ids = get_string_array_field(&final_state, "posted_turn_ids");
    let final_count = get_int_field(&final_state, "turn_count").max(0) as usize;
    let now = crate::common::logger::now_iso8601_pub();
    commit_export_success(
        &state_file,
        &mut state,
        session_id,
        transcript_offset,
        "",
        &[],
        &final_ids,
        final_count,
        &now,
        Some(path),
        &[],
        &[],
    )
    .map_err(|e| format!("finalize transcript cursor: {e}"))?;
    Ok(())
}

fn resolve_langfuse_config(
    config_path: Option<&str>,
    profile: Option<&str>,
) -> Result<LangfuseConfig, String> {
    if let Some(path) = config_path {
        let config = Config::load(Path::new(path))?;
        return config.resolve_profile(profile).map(Into::into);
    }

    let default_config = Path::new("report.config.toml");
    if default_config.exists() {
        let config = Config::load(default_config)?;
        return config
            .resolve_profile(profile.or(config.default_profile.as_deref()))
            .map(Into::into);
    }

    resolve_cli_env_langfuse_config()
}

fn recovery_delivery_context_for_worker(
    config: &LangfuseConfig,
    retry_policy: RetryPolicy,
    config_path: Option<&str>,
    profile: Option<&str>,
) -> crate::hooks::langfuse::subagent_progress::RecoveryDeliveryContext {
    let explicit_path = config_path.and_then(|path| absolute_config_path(path));
    if let Some(path) = explicit_path.as_deref() {
        return crate::hooks::langfuse::subagent_progress::delivery_context_with_source(
            config,
            retry_policy,
            "cli_config",
            Some(path),
            profile,
        );
    }

    let default_config = Path::new("report.config.toml");
    if default_config.exists() {
        if let Some(path) = absolute_config_path("report.config.toml") {
            return crate::hooks::langfuse::subagent_progress::delivery_context_with_source(
                config,
                retry_policy,
                "cli_default_config",
                Some(&path),
                profile,
            );
        }
    }

    crate::hooks::langfuse::subagent_progress::delivery_context_with_source(
        config,
        retry_policy,
        "cli_env",
        None,
        profile,
    )
}

fn absolute_config_path(path: &str) -> Option<String> {
    let path = Path::new(path);
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().ok()?.join(path)
    };
    Some(absolute.to_string_lossy().into_owned())
}

fn resolve_langfuse_config_for_recovery(
    context: &crate::hooks::langfuse::subagent_progress::RecoveryDeliveryContext,
) -> Result<LangfuseConfig, String> {
    match context.config_source.as_str() {
        "hook" => resolve_hook_langfuse_config(),
        "cli_config" | "cli_default_config" => {
            let path = context
                .config_path
                .as_deref()
                .ok_or("subagent recovery config source missing config_path")?;
            resolve_langfuse_config(Some(path), context.profile.as_deref())
        }
        "cli_env" => resolve_cli_env_langfuse_config(),
        other => Err(format!("unknown subagent recovery config source: {other}")),
    }
}

fn resolve_hook_langfuse_config() -> Result<LangfuseConfig, String> {
    crate::hooks::langfuse::resolve_hook_export_config()
        .ok_or_else(|| "missing hook export config for subagent recovery".to_string())
}

fn resolve_cli_env_langfuse_config() -> Result<LangfuseConfig, String> {
    crate::hooks::langfuse::resolve_env_export_config().ok_or_else(|| {
        "missing export config; provide --config or exporter environment".to_string()
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::transcript::{
        AssistantMessage, TokenUsage, TokenUsageSource, ToolCall, TurnTerminalState,
    };
    use crate::hooks::tool_context::ToolContextHandler;
    use crate::hooks::{CommonContext, HookHandler, RealtimeExportMode};
    use serde_json::json;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};

    #[test]
    fn finalizer_boundary_excludes_incomplete_trailing_buffer() {
        let state = json!({"session": {"offset": 128, "buffer": "partial"}});
        assert_eq!(finalized_transcript_boundary(&state, "session"), 121);
        assert_eq!(finalized_transcript_boundary(&state, "missing"), 0);
    }

    #[test]
    fn worker_retry_and_terminal_wait_defaults_are_bounded() {
        assert_eq!(DEFAULT_MAX_ATTEMPTS, 3);
        assert_eq!(REALTIME_TOOL_HTTP_TIMEOUT_MS, 5_000);
        assert_eq!(REALTIME_CHUNK_SAFE_START_BUDGET_MS, 5_000);
        assert_eq!(REALTIME_CLAIM_HARD_CAP_MS, 20_000);
        assert_eq!(DEFAULT_TRANSCRIPT_TERMINAL_TIMEOUT_SECS, 30);
        assert!(should_retry(1, DEFAULT_MAX_ATTEMPTS));
        assert!(should_retry(2, DEFAULT_MAX_ATTEMPTS));
        assert!(!should_retry(3, DEFAULT_MAX_ATTEMPTS));
    }

    #[test]
    fn realtime_chunk_requires_a_full_safe_start_budget() {
        let safe_start = Duration::from_millis(REALTIME_CHUNK_SAFE_START_BUDGET_MS);
        assert_eq!(realtime_chunk_timeout(Duration::ZERO), None);
        assert_eq!(
            realtime_chunk_timeout(safe_start - Duration::from_millis(1)),
            None,
            "a non-zero but insufficient budget must queue the suffix without POST"
        );
        assert_eq!(realtime_chunk_timeout(safe_start), Some(safe_start));
        assert_eq!(
            realtime_chunk_timeout(safe_start + Duration::from_secs(1)),
            Some(Duration::from_millis(REALTIME_TOOL_HTTP_TIMEOUT_MS))
        );
    }

    #[test]
    fn realtime_claim_budget_distinguishes_worker_default_from_exhausted_hook_budget() {
        let hard_cap = Duration::from_millis(REALTIME_CLAIM_HARD_CAP_MS);
        assert_eq!(effective_realtime_claim_budget(None), hard_cap);

        let long_hook_budget = StopBudget::new(
            std::time::Instant::now(),
            Duration::from_secs(110),
            Duration::from_secs(5),
            Duration::from_secs(1),
        );
        assert_eq!(
            effective_realtime_claim_budget(Some(long_hook_budget)),
            hard_cap
        );

        let stop_budget = StopBudget::new(
            std::time::Instant::now(),
            Duration::from_secs(14),
            Duration::from_secs(5),
            Duration::from_secs(1),
        );
        let stop_effective = effective_realtime_claim_budget(Some(stop_budget));
        assert!(stop_effective <= Duration::from_secs(13));
        assert!(stop_effective > Duration::from_secs(12));

        let exhausted = StopBudget::new(
            std::time::Instant::now()
                .checked_sub(Duration::from_secs(1))
                .unwrap(),
            Duration::from_millis(1),
            Duration::from_secs(5),
            Duration::ZERO,
        );
        assert_eq!(
            effective_realtime_claim_budget(Some(exhausted)),
            Duration::ZERO,
            "an exhausted hook budget must not fall back to the worker's 20s default"
        );
    }

    #[test]
    fn repeated_generation_scan_invocations_reach_trigger_beyond_soft_budget() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript_path = dir.path().join("session.jsonl");
        let mut transcript = String::new();
        for index in 0..24 {
            transcript.push_str(
                &serde_json::to_string(&json!({
                    "type": "scan_padding",
                    "payload": {
                        "index": index,
                        "value": format!("SENSITIVE_SCAN_SENTINEL{}", "x".repeat(64 * 1024)),
                    }
                }))
                .unwrap(),
            );
            transcript.push('\n');
        }
        for entry in [
            json!({
                "timestamp": "2026-08-13T00:00:00Z",
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "turn-target"}
            }),
            json!({
                "timestamp": "2026-08-13T00:00:00.100Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "target"}
            }),
            json!({
                "timestamp": "2026-08-13T00:00:00.200Z",
                "type": "event_msg",
                "payload": {"type": "agent_message", "phase": "commentary", "message": "calling"}
            }),
            json!({
                "timestamp": "2026-08-13T00:00:00.300Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec",
                    "arguments": "{}",
                    "call_id": "call-target"
                }
            }),
            json!({
                "timestamp": "2026-08-13T00:00:00.400Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-target",
                    "output": "ok"
                }
            }),
        ] {
            transcript.push_str(&serde_json::to_string(&entry).unwrap());
            transcript.push('\n');
        }
        std::fs::write(&transcript_path, transcript).unwrap();
        update_realtime_state(&state_file, |state| {
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            Ok(())
        })
        .unwrap();
        let trigger = RealtimeGenerationTrigger::v2(
            "session",
            "context",
            "turn-target",
            "exec-target",
            transcript_path.to_str().unwrap(),
            0,
            1,
        )
        .unwrap();
        let logger = Logger::new(dir.path().join("worker.log"), true);
        let mut previous_discovery = 0;
        let mut resolved = false;
        for _ in 0..4 {
            let preparation =
                prepare_generation_window("session", &state_file, &transcript_path, 0, &logger)
                    .unwrap();
            let state = load_state(&state_file);
            let discovery = realtime_state::generation_discovery_offset(&state, "session");
            assert!(discovery >= previous_discovery);
            assert_eq!(realtime_state::generation_scan_anchor(&state, "session"), 0);
            previous_discovery = discovery;
            if let GenerationWindowPreparation::Semantic(window) = preparation {
                let turns = build_turns(&window.entries);
                if resolve_realtime_generation_boundary(&trigger, &turns).is_ok() {
                    resolved = true;
                    break;
                }
            }
        }
        assert!(
            resolved,
            "the trigger must become visible within bounded retries"
        );
        assert!(
            previous_discovery
                > crate::hooks::langfuse::realtime_generation::DEFAULT_GENERATION_SCAN_MAX_BYTES
                    as u64
        );
        let encoded_state = serde_json::to_string(&load_state(&state_file)).unwrap();
        assert!(!encoded_state.contains("SENSITIVE_SCAN_SENTINEL"));
        let log = std::fs::read_to_string(dir.path().join("worker.log")).unwrap();
        assert!(log.contains("cdxhooks.generation_scan"));
        assert!(log.contains("backlog_bytes"));
        assert!(!log.contains("SENSITIVE_SCAN_SENTINEL"));
    }

    #[test]
    fn realtime_flush_defers_only_temporary_transcript_io() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let missing_transcript = dir.path().join("missing.jsonl");
        let context = CommonContext {
            logger: Logger::new(dir.path().join("hook.log"), true),
            state_file: state_file.clone(),
            langfuse_config: None,
            username: "tester".to_string(),
            sessions_dir: dir.path().to_string_lossy().to_string(),
            hook_version: "test-version".to_string(),
            stop_budget: None,
            retry_policy: RetryPolicy::AtLeastOnce,
            export_mode: RealtimeExportMode::RealtimeWithStopFinalizer,
            realtime_llm_batch_size: 1,
            metrics: None,
        };
        let handler = ToolContextHandler::new();
        handler
            .handle(
                &crate::common::hook::HookEvent::PreToolUse,
                &json!({
                    "session_id": "session",
                    "turn_id": "turn-1",
                    "tool_name": "Bash",
                    "tool_use_id": "exec-1",
                    "cwd": "/repo",
                    "tool_input": {"cmd": "true"}
                }),
                &context,
            )
            .unwrap();
        handler
            .handle_post_tool_use(
                &json!({
                    "session_id": "session",
                    "turn_id": "turn-1",
                    "tool_name": "Bash",
                    "tool_use_id": "exec-1",
                    "cwd": "/repo",
                    "transcript_path": missing_transcript,
                    "tool_input": {"cmd": "true"},
                    "tool_response": {"stdout": "", "stderr": "", "exit_code": 0}
                }),
                &context,
                false,
            )
            .unwrap();
        let job = realtime_state::pending_flush_jobs(&load_state(&state_file), "session")
            .into_iter()
            .next()
            .expect("PostToolUse should retain one realtime Flush trigger");
        let config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host: "http://127.0.0.1".to_string(),
            headers: None,
        };
        let temporary =
            process_realtime_flush(&job, &state_file, &context.logger, &config, None).unwrap_err();
        assert!(temporary.starts_with(REALTIME_FLUSH_DEFERRED));
        assert!(temporary.contains(TEMPORARY_TRANSCRIPT_IO_ERROR));

        let permanent = "open transcript 'session.jsonl': permission denied".to_string();
        assert_eq!(
            classify_realtime_preparation_error(permanent.clone(), REALTIME_FLUSH_DEFERRED),
            permanent
        );
    }

    #[test]
    fn realtime_generation_defers_temporary_transcript_io() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let missing_transcript = dir.path().join("missing.jsonl");
        let mut context_id = String::new();
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-1",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            context_id = context.context_id;
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            Ok(())
        })
        .unwrap();
        let published = spool::publish_realtime_job(
            dir.path(),
            spool::RealtimeSpoolKind::Generation,
            "session",
            &context_id,
            "scan:tool-1:0",
            json!({
                "trigger_tool_use_id": "tool-1",
                "transcript_path": missing_transcript,
                "scan_anchor": 0,
                "generation_batch_size": 1,
                "retry_policy": "at_least_once",
            }),
        )
        .unwrap();
        let job = spool::pending_realtime_jobs(dir.path(), Duration::from_secs(60))
            .unwrap()
            .into_iter()
            .find(|pending| pending.job_id == published.job_id)
            .unwrap();
        let config = LangfuseConfig {
            host: "http://127.0.0.1:9".to_string(),
            public_key: Some("pk".to_string()),
            secret_key: Some("sk".to_string()),
            headers: None,
        };
        let logger = Logger::new(dir.path().join("worker.log"), true);

        let error = process_realtime_generation(
            &job,
            &state_file,
            &config,
            &logger,
            Duration::from_millis(50),
            RetryPolicy::AtLeastOnce,
            Duration::from_secs(60),
        )
        .unwrap_err();
        assert!(error.starts_with(REALTIME_GENERATION_DEFERRED));
        assert!(error.contains(TEMPORARY_TRANSCRIPT_IO_ERROR));
    }

    #[test]
    fn replaced_transcript_sets_one_sticky_finalizer_only_checkpoint() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript_path = dir.path().join("session.jsonl");
        let mut initial = String::new();
        for index in 0..24 {
            initial.push_str(
                &serde_json::to_string(&json!({
                    "type": "padding",
                    "payload": {"index": index, "value": "x".repeat(64 * 1024)}
                }))
                .unwrap(),
            );
            initial.push('\n');
        }
        std::fs::write(&transcript_path, initial).unwrap();
        update_realtime_state(&state_file, |state| {
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            Ok(())
        })
        .unwrap();
        let logger = Logger::new(dir.path().join("worker.log"), true);
        assert!(matches!(
            prepare_generation_window("session", &state_file, &transcript_path, 0, &logger)
                .unwrap(),
            GenerationWindowPreparation::Semantic(_)
        ));
        std::fs::write(&transcript_path, "{\"type\":\"replacement\"}\n").unwrap();
        assert!(matches!(
            prepare_generation_window("session", &state_file, &transcript_path, 0, &logger)
                .unwrap(),
            GenerationWindowPreparation::FinalizerOnly("transcript_replaced_or_truncated")
        ));
        let checkpoint = serde_json::to_string(&load_state(&state_file)).unwrap();
        assert!(checkpoint.contains("transcript_replaced_or_truncated"));
        assert!(matches!(
            prepare_generation_window("session", &state_file, &transcript_path, 0, &logger)
                .unwrap(),
            GenerationWindowPreparation::FinalizerOnly("sticky_terminal")
        ));
        assert_eq!(
            serde_json::to_string(&load_state(&state_file)).unwrap(),
            checkpoint,
            "sticky suppression must not rewrite or rescan state"
        );
    }

    #[test]
    fn public_worker_commands_do_not_create_spool_for_new_session() {
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir.clone(), spool_dir.clone());

        for command in [
            WorkerCommand::Once(StateWorkerOptions {
                session_id: Some("new/session".to_string()),
                limit: 1,
            }),
            WorkerCommand::Run(StateWorkerOptions {
                session_id: Some("new/session".to_string()),
                limit: 10,
            }),
            WorkerCommand::RecoverStale(StateWorkerOptions {
                session_id: Some("new/session".to_string()),
                limit: 10,
            }),
        ] {
            run(command).unwrap();
        }
        let status = state_worker_status(Some("new/session")).unwrap();
        assert_eq!(status.sessions, 0);
        assert_eq!(status.pending_tool_facts, 0);
        assert_eq!(status.pending_flushes, 0);
        assert_eq!(status.pending_stops, 0);
        assert!(
            !spool_dir.exists(),
            "state-native worker commands must not create the legacy spool root"
        );
    }

    #[test]
    fn ordinary_state_wake_ignores_completed_subagent_ledger() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let state_file = dirs::session_state_file("session").unwrap();
        update_state_transaction(&state_file, &mut json!({}), |state| {
            *state = json!({
                "session": {
                    "subagent_progress": {
                        "version": 1,
                        "turns": {
                            "turn-done": {
                                "parent_turn_id": "turn-done",
                                "parent_transcript_path": "missing",
                                "trace_id": "trace",
                                "root_span_id": "root",
                                "username": "u",
                                "version": "v",
                                "pending_child_ids": [],
                                "accounted_span_ids": ["done"],
                                "dormant": true,
                                "needs_delivery": false
                            }
                        }
                    }
                }
            });
        })
        .unwrap();

        let scheduled = wake_pending_state(
            &StateWorkerOptions {
                session_id: Some("session".to_string()),
                limit: 1,
            },
            StateWakeMode::Ordinary,
        )
        .unwrap();

        assert_eq!(scheduled, 0);
        assert_eq!(
            state_worker_status(Some("session"))
                .unwrap()
                .pending_subagent_recoveries,
            0
        );
    }

    #[test]
    fn ordinary_state_wake_does_not_reset_dormant_subagent_backoff() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let _owner = crate::common::runtime_state::SessionOwner::acquire(
            "session",
            Duration::from_millis(100),
        )
        .unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        update_state_transaction(&state_file, &mut json!({}), |state| {
            *state = json!({
                "session": {
                    "subagent_progress": {
                        "version": 1,
                        "turns": {
                            "turn-pending": {
                                "parent_turn_id": "turn-pending",
                                "parent_transcript_path": "missing",
                                "trace_id": "trace",
                                "root_span_id": "root",
                                "username": "u",
                                "version": "v",
                                "pending_child_ids": ["child"],
                                "accounted_span_ids": ["already-posted"],
                                "dormant": true,
                                "needs_delivery": false,
                                "attempt": 5,
                                "next_at_ms": 123456,
                                "unchanged_since_ms": 654321
                            }
                        }
                    }
                }
            });
        })
        .unwrap();

        let scheduled = wake_pending_state(
            &StateWorkerOptions {
                session_id: Some("session".to_string()),
                limit: 1,
            },
            StateWakeMode::Ordinary,
        )
        .unwrap();

        assert_eq!(scheduled, 1);
        let state = load_state(&state_file);
        let record = &state["session"]["subagent_progress"]["turns"]["turn-pending"];
        assert_eq!(record["dormant"], json!(true));
        assert_eq!(record["attempt"], json!(5));
        assert_eq!(record["next_at_ms"], json!(123456));
        assert_eq!(record["unchanged_since_ms"], json!(654321));
    }

    #[test]
    fn ordinary_state_wake_keeps_delivery_only_subagent_eligible() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let _owner = crate::common::runtime_state::SessionOwner::acquire(
            "session",
            Duration::from_millis(100),
        )
        .unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        update_state_transaction(&state_file, &mut json!({}), |state| {
            *state = json!({
                "session": {
                    "subagent_progress": {
                        "version": 1,
                        "turns": {
                            "turn-delivery": {
                                "parent_turn_id": "turn-delivery",
                                "parent_transcript_path": "missing",
                                "trace_id": "trace",
                                "root_span_id": "root",
                                "username": "u",
                                "version": "v",
                                "pending_child_ids": [],
                                "accounted_span_ids": ["first-chunk"],
                                "dormant": false,
                                "needs_delivery": true,
                                "attempt": 2,
                                "next_at_ms": 777,
                                "unchanged_since_ms": 555
                            }
                        }
                    }
                }
            });
        })
        .unwrap();

        let scheduled = wake_pending_state(
            &StateWorkerOptions {
                session_id: Some("session".to_string()),
                limit: 1,
            },
            StateWakeMode::Ordinary,
        )
        .unwrap();

        assert_eq!(scheduled, 1);
        assert_eq!(
            state_worker_status(Some("session"))
                .unwrap()
                .pending_subagent_recoveries,
            1
        );
        let state = load_state(&state_file);
        let record = &state["session"]["subagent_progress"]["turns"]["turn-delivery"];
        assert_eq!(record["needs_delivery"], json!(true));
        assert_eq!(record["attempt"], json!(2));
        assert_eq!(record["next_at_ms"], json!(777));
        assert_eq!(record["unchanged_since_ms"], json!(555));
    }

    #[test]
    fn recover_stale_state_wake_retries_dormant_delivery_only_subagent() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let _owner = crate::common::runtime_state::SessionOwner::acquire(
            "session",
            Duration::from_millis(100),
        )
        .unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        update_state_transaction(&state_file, &mut json!({}), |state| {
            *state = json!({
                "session": {
                    "subagent_progress": {
                        "version": 1,
                        "turns": {
                            "turn-delivery": {
                                "parent_turn_id": "turn-delivery",
                                "parent_transcript_path": "missing",
                                "trace_id": "trace",
                                "root_span_id": "root",
                                "username": "u",
                                "version": "v",
                                "pending_child_ids": [],
                                "accounted_span_ids": ["first-chunk"],
                                "dormant": true,
                                "needs_delivery": true,
                                "attempt": 5,
                                "next_at_ms": 123456,
                                "unchanged_since_ms": 654321
                            }
                        }
                    }
                }
            });
        })
        .unwrap();

        let scheduled = wake_pending_state(
            &StateWorkerOptions {
                session_id: Some("session".to_string()),
                limit: 1,
            },
            StateWakeMode::RecoverStale,
        )
        .unwrap();

        assert_eq!(scheduled, 1);
        let state = load_state(&state_file);
        let record = &state["session"]["subagent_progress"]["turns"]["turn-delivery"];
        assert_eq!(record["dormant"], json!(false));
        assert_eq!(record["attempt"], json!(0));
        assert_ne!(record["next_at_ms"], json!(123456));
        assert_ne!(record["unchanged_since_ms"], json!(654321));
    }

    #[test]
    fn worker_status_reports_observable_spawn_failure_with_pending_queue() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let logger = Logger::new(dir.path().join("worker.log"), true);
        std::env::set_var("CDXHOOKS_TEST_WORKER_SPAWN_FAILURE", "true");
        let result = crate::worker::enqueue_and_wake(
            "spawn-failure-status",
            &json!({
                "hook_event_name": "PreCompact",
                "session_id": "spawn-failure-status"
            }),
            1,
            &logger,
        );
        std::env::remove_var("CDXHOOKS_TEST_WORKER_SPAWN_FAILURE");
        assert!(result.is_err());

        let status = state_worker_status(Some("spawn-failure-status")).unwrap();
        assert_eq!(status.sessions, 1);
        assert_eq!(status.pending_queue_items, 1);
        assert_eq!(status.worker_starting, 0);
        assert_eq!(status.worker_spawn_failures, 1);
    }

    // Platform editions intentionally use embedded endpoints instead of the
    // environment-only test sink. The non-platform build covers this import
    // path with an injectable endpoint.
    #[cfg(not(feature = "aivision"))]
    #[test]
    fn legacy_spool_import_marks_empty_then_consumes_late_upgrade_job() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir.clone(), spool_dir.clone());

        assert_eq!(import_legacy_spool_once("session", 10).unwrap(), 0);
        assert!(
            !spool_dir.exists(),
            "an empty one-time import must not create the legacy spool root"
        );
        let marker_path = dirs::session_migration_file("session").unwrap();
        let initial: Value = serde_json::from_slice(&std::fs::read(&marker_path).unwrap()).unwrap();
        assert_eq!(initial["legacy_spool_import"], "complete");
        assert_eq!(initial["done_jobs"], 0);
        let initial_bytes = std::fs::read(&marker_path).unwrap();
        assert_eq!(import_legacy_spool_once("session", 10).unwrap(), 0);
        assert_eq!(
            std::fs::read(&marker_path).unwrap(),
            initial_bytes,
            "completed empty import must not rewrite its one-time marker"
        );

        let session_spool = spool::session_root(&spool_dir, "session").unwrap();
        spool::enqueue(
            &session_spool,
            "session",
            Some("legacy-turn"),
            json!({"resourceSpans": []}),
        )
        .unwrap();
        let (host, sink) = spawn_status_sink(200);
        let saved_host = std::env::var("CDXHOOKS_LANGFUSE_BASE_URL").ok();
        std::env::set_var("CDXHOOKS_LANGFUSE_BASE_URL", host);

        assert_eq!(import_legacy_spool_once("session", 10).unwrap(), 1);
        assert_eq!(sink.join().unwrap().len(), 1);
        assert!(
            !session_spool.exists(),
            "a fully imported legacy session root must be removed"
        );
        let completed: Value =
            serde_json::from_slice(&std::fs::read(marker_path).unwrap()).unwrap();
        assert_eq!(completed["done_jobs"], 1);

        match saved_host {
            Some(value) => std::env::set_var("CDXHOOKS_LANGFUSE_BASE_URL", value),
            None => std::env::remove_var("CDXHOOKS_LANGFUSE_BASE_URL"),
        }
    }

    #[test]
    fn worker_quarantines_job_stored_under_another_session_root() {
        let _guard = crate::test_sync::env_lock();
        let saved_state_dir = std::env::var("CDXHOOKS_STATE_DIR").ok();
        let state_dir = tempfile::tempdir().unwrap();
        let spool_dir = tempfile::tempdir().unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", state_dir.path());

        let owner_root = spool::session_root(spool_dir.path(), "owner").unwrap();
        let pending = spool::enqueue(
            &owner_root,
            "foreign",
            Some("turn-1"),
            json!({"resourceSpans": []}),
        )
        .unwrap();
        assert!(pending.exists());

        assert!(worker_session_ids(spool_dir.path()).unwrap().is_empty());
        assert!(!pending.exists());
        assert_eq!(spool::status(&owner_root).unwrap().failed, 1);
        for root_name in ["pending", "claimed", "done", "failed", "locks"] {
            assert!(
                !spool_dir.path().join(root_name).exists(),
                "mutable spool state escaped the sessions container: {root_name}"
            );
        }

        match saved_state_dir {
            Some(value) => std::env::set_var("CDXHOOKS_STATE_DIR", value),
            None => std::env::remove_var("CDXHOOKS_STATE_DIR"),
        }
    }

    #[test]
    fn realtime_worker_checkpoints_follow_retry_policy_order() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        update_realtime_state(&state_file, |state| {
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                "context",
                "tool",
                "job-amo",
                "digest-amo",
            )
            .map(|_| ())
        })
        .unwrap();
        update_realtime_state(&state_file, |state| {
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                "context",
                "tool",
                "job-alo",
                "digest-alo",
            )
            .map(|_| ())
        })
        .unwrap();

        let at_most_once = realtime_test_job("job-amo", "digest-amo");
        checkpoint_realtime_delivery_begin(
            &state_file,
            &at_most_once,
            RetryPolicy::AtMostOnce,
            Duration::from_secs(60),
        )
        .unwrap();
        let state = load_state(&state_file);
        assert_eq!(
            realtime_state::registered_realtime_job(&state, "session", "job-amo")
                .unwrap()
                .status,
            realtime_state::RealtimeRegisteredJobStatus::PolicyTerminal
        );

        let at_least_once = realtime_test_job("job-alo", "digest-alo");
        checkpoint_realtime_delivery_begin(
            &state_file,
            &at_least_once,
            RetryPolicy::AtLeastOnce,
            Duration::from_secs(60),
        )
        .unwrap();
        let state = load_state(&state_file);
        assert_eq!(
            realtime_state::registered_realtime_job(&state, "session", "job-alo")
                .unwrap()
                .status,
            realtime_state::RealtimeRegisteredJobStatus::Inflight
        );
        checkpoint_realtime_delivery_success(&state_file, &at_least_once).unwrap();
        let state = load_state(&state_file);
        assert_eq!(
            realtime_state::registered_realtime_job(&state, "session", "job-alo")
                .unwrap()
                .status,
            realtime_state::RealtimeRegisteredJobStatus::Accounted
        );
        assert_eq!(
            realtime_pending_disposition(&state, &at_least_once, false),
            spool::PendingDisposition::Done
        );
        assert_eq!(
            realtime_pending_disposition(&json!({}), &at_least_once, false),
            spool::PendingDisposition::Keep
        );
        assert_eq!(
            realtime_pending_disposition(&json!({}), &at_least_once, true),
            spool::PendingDisposition::Failed
        );
    }

    fn realtime_test_job(job_id: &str, digest: &str) -> spool::SpoolJob {
        spool::SpoolJob {
            schema_version: spool::REALTIME_SCHEMA_VERSION,
            session_schema_version: spool::LEGACY_REALTIME_SESSION_SCHEMA_VERSION,
            job_kind: spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Tool),
            job_id: job_id.to_string(),
            session_id: "session".to_string(),
            turn_id: None,
            created_at: "now".to_string(),
            claimed_at: Some("now".to_string()),
            attempts: 1,
            last_error: None,
            otel: json!({"resourceSpans": []}),
            transcript_path: None,
            cwd: None,
            username: None,
            hook_version: None,
            subject_id: Some("tool".to_string()),
            context_id: Some("context".to_string()),
            payload_digest: Some(digest.to_string()),
            batch_manifest: None,
            flush_manifest: None,
        }
    }

    fn realtime_test_delivery_chunk(
        status: realtime_state::RealtimeDeliveryChunkStatus,
        last_error: Option<&str>,
    ) -> realtime_state::RealtimeDeliveryChunkPlanEntry {
        realtime_state::RealtimeDeliveryChunkPlanEntry {
            cursor: 0,
            batch_id: "batch-terminal".to_string(),
            payload_digest: "batch-digest".to_string(),
            body_len: 1,
            child_ids: vec!["generation:g-terminal".to_string()],
            span_ids: vec!["5555555555555555".to_string()],
            digests: vec!["generation-digest".to_string()],
            body: None,
            status,
            last_error: last_error.map(str::to_string),
        }
    }

    #[test]
    fn delivery_chunk_outcomes_preserve_terminal_semantics() {
        use realtime_state::RealtimeDeliveryChunkStatus;

        let cases = [
            (
                realtime_test_delivery_chunk(RealtimeDeliveryChunkStatus::Accounted, None),
                ("accounted", None),
            ),
            (
                realtime_test_delivery_chunk(
                    RealtimeDeliveryChunkStatus::PolicyTerminal,
                    Some("timeout"),
                ),
                ("policy_terminal", Some("at_most_once_transport_failure")),
            ),
            (
                realtime_test_delivery_chunk(RealtimeDeliveryChunkStatus::PolicyTerminal, None),
                (
                    "policy_terminal",
                    Some("at_most_once_interrupted_after_checkpoint"),
                ),
            ),
            (
                realtime_test_delivery_chunk(
                    RealtimeDeliveryChunkStatus::PermanentFailed,
                    Some("HTTP 400"),
                ),
                ("policy_terminal", Some("http_permanent_failure")),
            ),
        ];
        for (chunk, expected) in cases {
            assert_eq!(
                generation_round_outcome_for_delivery_chunk(&chunk).unwrap(),
                expected
            );
        }

        for status in [
            RealtimeDeliveryChunkStatus::Queued,
            RealtimeDeliveryChunkStatus::Inflight,
            RealtimeDeliveryChunkStatus::FailedRetryable,
        ] {
            let chunk = realtime_test_delivery_chunk(status, None);
            assert!(generation_round_outcome_for_delivery_chunk(&chunk)
                .unwrap_err()
                .contains("cannot finalize nonterminal realtime delivery chunk"));
        }
    }

    #[test]
    fn policy_terminal_delivery_chunk_is_persisted_as_terminal_generation_round() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        update_realtime_state(&state_file, |state| {
            state["session"]["realtime_export"]["generation"] = json!({
                "batch_size": 3,
                "rounds": []
            });
            Ok(())
        })
        .unwrap();
        let plan = realtime_state::RealtimeDeliveryPlan {
            schema_version: realtime_state::REALTIME_DELIVERY_PLAN_SCHEMA_VERSION,
            plan_id: "plan-terminal".to_string(),
            context_id: "context".to_string(),
            trigger_id: "flush-job".to_string(),
            chunks: vec![realtime_test_delivery_chunk(
                realtime_state::RealtimeDeliveryChunkStatus::PolicyTerminal,
                Some("timeout"),
            )],
            oversized_child_ids: vec![],
            next_cursor: 1,
            complete: true,
            terminal_reason: None,
        };
        let flush_job = realtime_test_job("flush-job", "flush-digest");
        let batch_job = realtime_test_job("batch-terminal", "batch-digest");
        let scan = GenerationScanCommit {
            scan_anchor: 10,
            next_offset: 20,
            scan_base_ordinal: 0,
            window_candidate_count: 1,
            window_terminal: true,
        };

        finalize_realtime_delivery_plan_rounds(
            &state_file,
            &flush_job,
            "context",
            &plan,
            &[batch_job],
            scan,
            &[(0, "g-terminal".to_string(), "5555555555555555".to_string())],
            &[],
        )
        .unwrap();

        let state = load_state(&state_file);
        let round = &state["session"]["realtime_export"]["generation"]["rounds"][0];
        assert_eq!(round["status"], json!("policy_terminal"));
        assert_eq!(
            round["terminal_reason"],
            json!("at_most_once_transport_failure")
        );
        assert_eq!(round["generation_id"], json!("g-terminal"));
        assert_eq!(round["job_id"], json!("batch-terminal"));
        assert_eq!(round["payload_digest"], json!("batch-digest"));
    }

    #[test]
    fn oversized_generation_rounds_are_persisted_as_policy_terminal() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        update_realtime_state(&state_file, |state| {
            state["session"]["realtime_export"]["generation"] = json!({
                "batch_size": 3,
                "rounds": []
            });
            Ok(())
        })
        .unwrap();
        let mut plan = realtime_state::RealtimeDeliveryPlan {
            schema_version: realtime_state::REALTIME_DELIVERY_PLAN_SCHEMA_VERSION,
            plan_id: "plan-oversize".to_string(),
            context_id: "context".to_string(),
            trigger_id: "flush-job".to_string(),
            chunks: vec![],
            oversized_child_ids: vec!["generation:g-oversize".to_string()],
            next_cursor: 0,
            complete: true,
            terminal_reason: Some("realtime_child_oversize".to_string()),
        };
        let flush_job = realtime_test_job("flush-job", "flush-digest");
        let scan = GenerationScanCommit {
            scan_anchor: 10,
            next_offset: 20,
            scan_base_ordinal: 0,
            window_candidate_count: 1,
            window_terminal: true,
        };

        finalize_realtime_delivery_plan_rounds(
            &state_file,
            &flush_job,
            "context",
            &plan,
            &[],
            scan,
            &[(0, "g-oversize".to_string(), "5555555555555555".to_string())],
            &[],
        )
        .unwrap();

        let state = load_state(&state_file);
        let round = &state["session"]["realtime_export"]["generation"]["rounds"][0];
        assert_eq!(round["status"], json!("policy_terminal"));
        assert_eq!(round["terminal_reason"], json!("realtime_child_oversize"));
        assert_eq!(round["generation_id"], json!("g-oversize"));
        assert_eq!(round["job_id"], json!("flush-job"));
        assert_eq!(round["payload_digest"], json!("flush-digest"));
        assert_eq!(
            state["session"]["realtime_export"]["generation"]["committed_offset"],
            json!(20)
        );

        plan.terminal_reason = None;
        finalize_realtime_delivery_plan_rounds(
            &state_file,
            &flush_job,
            "context",
            &plan,
            &[],
            scan,
            &[(0, "g-oversize".to_string(), "5555555555555555".to_string())],
            &[],
        )
        .unwrap();
        let state = load_state(&state_file);
        let round = &state["session"]["realtime_export"]["generation"]["rounds"][0];
        assert_eq!(round["status"], json!("policy_terminal"));
        assert_eq!(round["terminal_reason"], json!("realtime_child_oversize"));
        assert_eq!(
            state["session"]["realtime_export"]["generation"]["rounds"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }

    fn read_test_http_body(stream: &mut TcpStream) -> serde_json::Value {
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let mut bytes = Vec::new();
        let mut buffer = [0_u8; 4096];
        let header_end = loop {
            let read = stream.read(&mut buffer).unwrap();
            assert!(read > 0, "request closed before headers completed");
            bytes.extend_from_slice(&buffer[..read]);
            if let Some(index) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
                break index + 4;
            }
        };
        let headers = std::str::from_utf8(&bytes[..header_end]).unwrap();
        let content_length: usize = headers
            .lines()
            .find_map(|line| {
                let (name, value) = line.split_once(':')?;
                name.eq_ignore_ascii_case("content-length")
                    .then(|| value.trim().parse().unwrap())
            })
            .unwrap();
        while bytes.len() - header_end < content_length {
            let read = stream.read(&mut buffer).unwrap();
            assert!(read > 0, "request closed before body completed");
            bytes.extend_from_slice(&buffer[..read]);
        }
        serde_json::from_slice(&bytes[header_end..header_end + content_length]).unwrap()
    }

    fn spawn_status_sink(status: u16) -> (String, std::thread::JoinHandle<Vec<serde_json::Value>>) {
        spawn_status_sink_n(status, 1)
    }

    fn spawn_status_sink_n(
        status: u16,
        expected_requests: usize,
    ) -> (String, std::thread::JoinHandle<Vec<serde_json::Value>>) {
        spawn_status_sequence_sink(vec![status; expected_requests])
    }

    fn spawn_status_sequence_sink(
        statuses: Vec<u16>,
    ) -> (String, std::thread::JoinHandle<Vec<serde_json::Value>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        listener.set_nonblocking(true).unwrap();
        let handle = std::thread::spawn(move || {
            let mut bodies = Vec::with_capacity(statuses.len());
            for status in statuses {
                let deadline = std::time::Instant::now() + Duration::from_secs(5);
                let mut stream = loop {
                    match listener.accept() {
                        Ok((stream, _)) => break stream,
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            assert!(
                                std::time::Instant::now() < deadline,
                                "test sink timed out waiting for request"
                            );
                            std::thread::sleep(Duration::from_millis(5));
                        }
                        Err(error) => panic!("test sink accept failed: {error}"),
                    }
                };
                stream.set_nonblocking(false).unwrap();
                bodies.push(read_test_http_body(&mut stream));
                let reason = if (200..300).contains(&status) {
                    "OK"
                } else {
                    "Internal Server Error"
                };
                let response = format!(
                    "HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                );
                stream.write_all(response.as_bytes()).unwrap();
                stream.flush().unwrap();
            }
            bodies
        });
        (format!("http://{address}"), handle)
    }

    fn subagent_recovery_test_body(span_ids: &[&str], payload_len: usize) -> Value {
        let spans = span_ids
            .iter()
            .map(|span_id| {
                json!({
                    "traceId": "11111111111111111111111111111111",
                    "spanId": span_id,
                    "parentSpanId": "root000000000000",
                    "name": format!("child-{span_id}"),
                    "attributes": [
                        {
                            "key": "gen_ai.conversation.id",
                            "value": {"stringValue": "child-session"}
                        },
                        {
                            "key": "test.payload",
                            "value": {"stringValue": "x".repeat(payload_len)}
                        }
                    ]
                })
            })
            .collect::<Vec<_>>();
        json!({
            "resourceSpans": [{
                "resource": {"attributes": []},
                "scopeSpans": [{"scope": {"name": "test"}, "spans": spans}]
            }]
        })
    }

    fn subagent_recovery_parent_body() -> Value {
        json!({
            "resourceSpans": [{
                "resource": {"attributes": []},
                "scopeSpans": [{
                    "scope": {"name": "test"},
                    "spans": [{
                        "traceId": "11111111111111111111111111111111",
                        "spanId": "root000000000000",
                        "name": "parent",
                        "attributes": [{
                            "key": "gen_ai.conversation.id",
                            "value": {"stringValue": "parent-session"}
                        }]
                    }]
                }]
            }]
        })
    }

    #[test]
    fn subagent_recovery_multichunk_failure_keeps_continuation_state() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let logger = Logger::new(dir.path().join("worker.log"), true);
        let mut state = json!({});
        crate::hooks::langfuse::subagent_progress::register(
            &state_file,
            &mut state,
            "parent-session",
            "turn-1",
            "parent.jsonl",
            &subagent_recovery_parent_body(),
            &["child-session".to_string()],
            "tester",
            "test-version",
            None,
        )
        .unwrap();
        let mut record = crate::hooks::langfuse::subagent_progress::records(
            &load_state(&state_file),
            "parent-session",
        )
        .remove(0);
        let body = subagent_recovery_test_body(&["child0000000001", "child0000000002"], 4_193_600);
        let (host, sink) = spawn_status_sequence_sink(vec![200, 500]);
        let config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host,
            headers: None,
        };

        let first = deliver_subagent_delta_chunks(
            &config,
            &logger,
            RetryPolicy::AtLeastOnce,
            &state_file,
            &mut state,
            "parent-session",
            &record,
            &body,
            &[],
        );
        assert!(
            first.is_err(),
            "second chunk failure must surface for retry"
        );
        let first_bodies = sink.join().unwrap();
        assert_eq!(first_bodies.len(), 2);
        let failed_state = load_state(&state_file);
        assert_eq!(
            crate::hooks::langfuse::subagent_progress::pending_recovery_count(
                &failed_state,
                "parent-session"
            ),
            1,
            "a committed non-final chunk must keep the recovery lane eligible"
        );
        record =
            crate::hooks::langfuse::subagent_progress::records(&failed_state, "parent-session")
                .remove(0);
        assert!(record
            .accounted_span_ids
            .contains(&"child0000000001".to_string()));
        assert!(!record
            .accounted_span_ids
            .contains(&"child0000000002".to_string()));

        let mut remaining = body.clone();
        suppress_accounted(&mut remaining, &record);
        let (host, sink) = spawn_status_sink(200);
        let config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host,
            headers: None,
        };
        deliver_subagent_delta_chunks(
            &config,
            &logger,
            RetryPolicy::AtLeastOnce,
            &state_file,
            &mut state,
            "parent-session",
            &record,
            &remaining,
            &[],
        )
        .unwrap();
        let second_bodies = sink.join().unwrap();
        assert_eq!(second_bodies.len(), 1);
        let completed_state = load_state(&state_file);
        assert_eq!(
            crate::hooks::langfuse::subagent_progress::pending_recovery_count(
                &completed_state,
                "parent-session"
            ),
            0
        );
        let completed_record =
            crate::hooks::langfuse::subagent_progress::records(&completed_state, "parent-session")
                .remove(0);
        assert!(completed_record
            .accounted_span_ids
            .contains(&"child0000000001".to_string()));
        assert!(completed_record
            .accounted_span_ids
            .contains(&"child0000000002".to_string()));
    }

    fn spawn_blocking_status_sink(
        status: u16,
    ) -> (
        String,
        std::sync::mpsc::Receiver<()>,
        std::sync::mpsc::Sender<()>,
        std::thread::JoinHandle<serde_json::Value>,
    ) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let (accepted_tx, accepted_rx) = std::sync::mpsc::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let handle = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream.set_nonblocking(false).unwrap();
            accepted_tx.send(()).unwrap();
            let body = read_test_http_body(&mut stream);
            release_rx.recv_timeout(Duration::from_secs(5)).unwrap();
            let reason = if (200..300).contains(&status) {
                "OK"
            } else {
                "Internal Server Error"
            };
            let response = format!(
                "HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            );
            stream.write_all(response.as_bytes()).unwrap();
            stream.flush().unwrap();
            body
        });
        (format!("http://{address}"), accepted_rx, release_tx, handle)
    }

    struct CurrentDirGuard(std::path::PathBuf);

    impl CurrentDirGuard {
        fn enter(path: &Path) -> Self {
            let previous = std::env::current_dir().unwrap();
            std::env::set_current_dir(path).unwrap();
            Self(previous)
        }
    }

    impl Drop for CurrentDirGuard {
        fn drop(&mut self) {
            std::env::set_current_dir(&self.0).unwrap();
        }
    }

    fn write_collab_recovery_transcripts(dir: &Path) -> (PathBuf, PathBuf) {
        let parent = dir.join("rollout-parent-collab.jsonl");
        let child = dir.join("rollout-child-collab.jsonl");
        std::fs::write(
            &parent,
            include_str!("../../tests/fixtures/codex_collab_tool_call_same_turn_parent.jsonl"),
        )
        .unwrap();
        std::fs::write(
            &child,
            include_str!("../../tests/fixtures/codex_collab_tool_call_same_turn_child.jsonl"),
        )
        .unwrap();
        (parent, child)
    }

    fn write_public_subagent_recovery_record(
        state_file: &Path,
        transcript_path: &Path,
        context: Option<crate::hooks::langfuse::subagent_progress::RecoveryDeliveryContext>,
    ) -> RecoveryRecord {
        if let Some(parent) = state_file.parent() {
            dirs::ensure_private_dir(parent).unwrap();
        }
        let record = json!({
            "parent_turn_id": "turn-collab-parent",
            "parent_transcript_path": transcript_path.to_string_lossy(),
            "trace_id": "trace000000000000000000000000000",
            "root_span_id": "root000000000000",
            "username": "tester",
            "version": "test-version",
            "pending_child_ids": ["child-collab"],
            "accounted_span_ids": [],
            "dormant": false,
            "needs_delivery": true,
            "delivery_context": context,
            "attempt": 0,
            "next_at_ms": 0,
            "unchanged_since_ms": 0,
            "child_paths": {},
            "snapshots": {}
        });
        let parsed_record: RecoveryRecord =
            serde_json::from_value(record.clone()).expect("test recovery record shape");
        let next_state = json!({
            "parent-collab": {
                "subagent_progress": {
                    "version": 1,
                    "turns": {
                        "turn-collab-parent": record
                    }
                }
            }
        });
        let mut state = json!({});
        update_state_transaction(state_file, &mut state, |candidate| {
            *candidate = next_state;
        })
        .unwrap();
        let records = subagent_recovery_records(&load_state(state_file), "parent-collab");
        assert_eq!(records.len(), 1);
        parsed_record
    }

    #[test]
    fn worker_transcript_path_posts_parent_then_recovers_child_without_stop() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let transcript_path = dir.path().join("rollout-parent-collab.jsonl");
        let child_path = dir.path().join("rollout-child-collab.jsonl");
        std::fs::write(
            &transcript_path,
            include_str!("../../tests/fixtures/codex_collab_tool_call_same_turn_parent.jsonl"),
        )
        .unwrap();
        let (host, sink) = spawn_status_sink_n(200, 2);
        let config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host,
            headers: None,
        };
        let logger = Logger::new(dir.path().join("worker-native.log"), true);
        let job = spool::SpoolJob {
            schema_version: 1,
            session_schema_version: spool::LEGACY_REALTIME_SESSION_SCHEMA_VERSION,
            job_kind: spool::SpoolJobKind::Legacy,
            job_id: "native-finalizer".to_string(),
            session_id: "parent-collab".to_string(),
            turn_id: None,
            created_at: "now".to_string(),
            claimed_at: Some("now".to_string()),
            attempts: 1,
            last_error: None,
            otel: json!({}),
            transcript_path: Some(transcript_path.to_string_lossy().to_string()),
            cwd: None,
            username: Some("tester".to_string()),
            hook_version: Some("test-version".to_string()),
            subject_id: None,
            context_id: None,
            payload_digest: None,
            batch_manifest: None,
            flush_manifest: None,
        };

        process_transcript(
            &job,
            &config,
            &logger,
            Duration::from_secs(2),
            None,
            None,
            None,
        )
        .unwrap();
        let state_file = dirs::session_state_file("parent-collab").unwrap();
        let parent_state = load_state(&state_file);
        assert_eq!(
            parent_state["parent-collab"]["posted_turn_ids"],
            json!(["turn-collab-parent"])
        );
        let record = subagent_recovery_records(&parent_state, "parent-collab").remove(0);
        assert_eq!(record.pending_child_ids, vec!["child-collab"]);

        std::fs::write(
            &child_path,
            include_str!("../../tests/fixtures/codex_collab_tool_call_same_turn_child.jsonl"),
        )
        .unwrap();
        run_subagent_recovery_with_context(
            "parent-collab",
            &record,
            &state_file,
            &config,
            &logger,
            RetryPolicy::AtLeastOnce,
        )
        .unwrap();
        run_subagent_recovery_with_context(
            "parent-collab",
            &record,
            &state_file,
            &config,
            &logger,
            RetryPolicy::AtLeastOnce,
        )
        .unwrap();
        process_transcript(
            &job,
            &config,
            &logger,
            Duration::from_secs(2),
            None,
            None,
            None,
        )
        .unwrap();

        let bodies = sink.join().unwrap();
        assert_eq!(bodies.len(), 2);
        let span_names = bodies[1]["resourceSpans"]
            .as_array()
            .into_iter()
            .flatten()
            .flat_map(|resource| resource["scopeSpans"].as_array().into_iter().flatten())
            .flat_map(|scope| scope["spans"].as_array().into_iter().flatten())
            .filter_map(|span| span["name"].as_str())
            .collect::<Vec<_>>();
        let worker_log = std::fs::read_to_string(dir.path().join("worker-native.log")).unwrap();
        assert!(
            span_names.contains(&"agent Archimedes"),
            "worker export spans: {span_names:?}\n{worker_log}"
        );
        let state = load_state(&state_file);
        assert_eq!(
            state["parent-collab"]["posted_turn_ids"],
            json!(["turn-collab-parent"])
        );
        assert_eq!(
            state["parent-collab"]["merged_subagent_ids"],
            json!(["child-collab"])
        );
        assert_eq!(
            state["parent-collab"]["merged_subagent_invocation_ids"],
            json!(["child-collab:collab-spawn-1"])
        );
    }

    #[test]
    fn public_subagent_recovery_uses_original_env_context_after_reload() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let (parent_path, _child_path) = write_collab_recovery_transcripts(dir.path());
        let (host, sink) = spawn_status_sink(200);
        let config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host: host.clone(),
            headers: None,
        };
        let original_env = std::collections::BTreeMap::from([
            (
                "CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT".to_string(),
                format!("{host}/v1/traces"),
            ),
            ("CDXHOOKS_LANGFUSE_BASE_URL".to_string(), host),
            (
                "CDXHOOKS_TEST_ALLOW_ENV_EXPORTER".to_string(),
                "true".to_string(),
            ),
            ("ANT_GRAY".to_string(), "true".to_string()),
        ]);
        let delivery_context = crate::common::env::with_worker_env(&original_env, || {
            crate::hooks::langfuse::subagent_progress::delivery_context_with_source(
                &config,
                RetryPolicy::AtMostOnce,
                "hook",
                None,
                Some("original-profile"),
            )
        });
        let state_file = dirs::session_state_file("parent-collab").unwrap();
        let _record = write_public_subagent_recovery_record(
            &state_file,
            &parent_path,
            Some(delivery_context),
        );
        let reloaded =
            subagent_recovery_records(&load_state(&state_file), "parent-collab").remove(0);
        let reloaded_context = reloaded.delivery_context.as_ref().unwrap();
        assert_eq!(reloaded_context.retry_policy, "at_most_once");
        assert_eq!(
            reloaded_context.profile.as_deref(),
            Some("original-profile")
        );

        std::env::set_var("CDXHOOKS_LANGFUSE_BASE_URL", "http://127.0.0.1:9");
        std::env::set_var(
            "CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            "http://127.0.0.1:9/v1/traces",
        );
        std::env::set_var("ANT_GRAY", "false");
        std::env::set_var("CDXHOOKS_RETRY_POLICY", "at_least_once");
        run_subagent_recovery("parent-collab", &reloaded).unwrap();
        std::env::remove_var("CDXHOOKS_LANGFUSE_BASE_URL");
        std::env::remove_var("CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT");
        std::env::remove_var("ANT_GRAY");
        std::env::remove_var("CDXHOOKS_RETRY_POLICY");

        assert_eq!(sink.join().unwrap().len(), 1);
    }

    #[test]
    fn public_subagent_recovery_uses_explicit_config_path_over_cwd_default() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let (parent_path, _child_path) = write_collab_recovery_transcripts(dir.path());
        let (host, sink) = spawn_status_sink(200);
        let config_path = dir.path().join("original.config.toml");
        std::fs::write(
            &config_path,
            format!(
                r#"version = 1
default_profile = "wrong"

[profiles.original]
host = "{}"
public_key = "pk-original"
secret_key = "sk-original"

[profiles.wrong]
host = "http://127.0.0.1:9"
public_key = "pk-wrong"
secret_key = "sk-wrong"
"#,
                host
            ),
        )
        .unwrap();
        let config =
            resolve_langfuse_config(Some(config_path.to_str().unwrap()), Some("original")).unwrap();
        let cwd = dir.path().join("cwd");
        std::fs::create_dir(&cwd).unwrap();
        std::fs::write(
            cwd.join("report.config.toml"),
            r#"version = 1
default_profile = "local"

[profiles.local]
host = "http://127.0.0.1:9"
public_key = "pk-local"
secret_key = "sk-local"
"#,
        )
        .unwrap();
        let delivery_context = recovery_delivery_context_for_worker(
            &config,
            RetryPolicy::AtLeastOnce,
            Some(config_path.to_str().unwrap()),
            Some("original"),
        );
        let state_file = dirs::session_state_file("parent-collab").unwrap();
        let record = write_public_subagent_recovery_record(
            &state_file,
            &parent_path,
            Some(delivery_context),
        );

        let _cwd = CurrentDirGuard::enter(&cwd);
        run_subagent_recovery("parent-collab", &record).unwrap();

        assert_eq!(sink.join().unwrap().len(), 1);
    }

    #[test]
    fn public_subagent_recovery_rejects_changed_context_or_missing_context_without_mutating_ledger()
    {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let (parent_path, _child_path) = write_collab_recovery_transcripts(dir.path());
        let state_file = dirs::session_state_file("parent-collab").unwrap();

        let config_path = dir.path().join("secrets.config.toml");
        std::fs::write(
            &config_path,
            r#"version = 1
default_profile = "original"

[profiles.original]
host = "http://127.0.0.1:9"
public_key = "pk"
secret_key_env = "CDXHOOKS_TEST_SUBAGENT_RECOVERY_SECRET"
"#,
        )
        .unwrap();
        std::env::set_var("CDXHOOKS_TEST_SUBAGENT_RECOVERY_SECRET", "old-secret");
        let original_config =
            resolve_langfuse_config(Some(config_path.to_str().unwrap()), Some("original")).unwrap();
        let credential_context = recovery_delivery_context_for_worker(
            &original_config,
            RetryPolicy::AtLeastOnce,
            Some(config_path.to_str().unwrap()),
            Some("original"),
        );
        let credential_record = write_public_subagent_recovery_record(
            &state_file,
            &parent_path,
            Some(credential_context),
        );
        let before_credential = load_state(&state_file);
        std::env::set_var("CDXHOOKS_TEST_SUBAGENT_RECOVERY_SECRET", "new-secret");
        let err = run_subagent_recovery("parent-collab", &credential_record).unwrap_err();
        assert!(
            err.contains("delivery context changed"),
            "recovery should reject changed credentials"
        );
        assert_eq!(load_state(&state_file), before_credential);
        std::env::remove_var("CDXHOOKS_TEST_SUBAGENT_RECOVERY_SECRET");

        let header_env = std::collections::BTreeMap::from([
            (
                "CDXHOOKS_LANGFUSE_BASE_URL".to_string(),
                "http://127.0.0.1:9".to_string(),
            ),
            (
                "CDXHOOKS_TEST_ALLOW_ENV_EXPORTER".to_string(),
                "true".to_string(),
            ),
            ("LANGFUSE_HEADERS".to_string(), "x-route=old".to_string()),
        ]);
        let header_config =
            crate::common::env::with_worker_env(&header_env, || resolve_hook_langfuse_config())
                .unwrap();
        let header_context = crate::common::env::with_worker_env(&header_env, || {
            crate::hooks::langfuse::subagent_progress::delivery_context_with_source(
                &header_config,
                RetryPolicy::AtLeastOnce,
                "hook",
                None,
                None,
            )
        });
        let header_record =
            write_public_subagent_recovery_record(&state_file, &parent_path, Some(header_context));
        let before_header = load_state(&state_file);
        std::env::set_var("LANGFUSE_HEADERS", "x-route=new");
        let err = run_subagent_recovery("parent-collab", &header_record).unwrap_err();
        assert!(
            err.contains("delivery context changed"),
            "recovery should reject changed exporter headers"
        );
        assert_eq!(load_state(&state_file), before_header);
        std::env::remove_var("LANGFUSE_HEADERS");

        let missing_context_record =
            write_public_subagent_recovery_record(&state_file, &parent_path, None);
        let before_missing = load_state(&state_file);
        let err = run_subagent_recovery("parent-collab", &missing_context_record).unwrap_err();
        assert!(
            err.contains("missing delivery context"),
            "recovery should reject a missing delivery context"
        );
        assert_eq!(load_state(&state_file), before_missing);
    }

    #[test]
    fn registering_context_for_legacy_record_preserves_accounted_span_ids() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let mut state = json!({});
        crate::hooks::langfuse::subagent_progress::register(
            &state_file,
            &mut state,
            "parent-session",
            "turn-1",
            "parent.jsonl",
            &subagent_recovery_parent_body(),
            &["child-session".to_string()],
            "tester",
            "test-version",
            None,
        )
        .unwrap();
        crate::hooks::langfuse::subagent_progress::account(
            &state_file,
            &mut state,
            "parent-session",
            "turn-1",
            &subagent_recovery_test_body(&["child0000000001"], 8),
            &["child-session".to_string()],
        )
        .unwrap();
        let config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host: "http://127.0.0.1:9".to_string(),
            headers: None,
        };
        let context = crate::hooks::langfuse::subagent_progress::delivery_context_with_source(
            &config,
            RetryPolicy::AtLeastOnce,
            "cli_env",
            None,
            None,
        );
        crate::hooks::langfuse::subagent_progress::register(
            &state_file,
            &mut state,
            "parent-session",
            "turn-1",
            "parent.jsonl",
            &subagent_recovery_parent_body(),
            &["child-session".to_string()],
            "tester",
            "test-version",
            Some(context),
        )
        .unwrap();
        let record =
            subagent_recovery_records(&load_state(&state_file), "parent-session").remove(0);
        assert!(record.delivery_context.is_some());
        assert_eq!(
            record.accounted_span_ids,
            vec!["child0000000001".to_string()]
        );
        let before_mismatch = load_state(&state_file);
        let changed_config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host: "http://127.0.0.1:10".to_string(),
            headers: None,
        };
        let changed_context =
            crate::hooks::langfuse::subagent_progress::delivery_context_with_source(
                &changed_config,
                RetryPolicy::AtMostOnce,
                "cli_env",
                None,
                None,
            );
        let err = crate::hooks::langfuse::subagent_progress::register(
            &state_file,
            &mut state,
            "parent-session",
            "turn-1",
            "parent.jsonl",
            &subagent_recovery_parent_body(),
            &["child-session".to_string()],
            "tester",
            "test-version",
            Some(changed_context),
        )
        .unwrap_err();
        assert!(err.contains("delivery context changed"), "{err}");
        assert_eq!(load_state(&state_file), before_mismatch);
    }

    #[test]
    fn finalizer_compensates_permanent_failure_and_suppresses_at_most_policy_terminal() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        update_realtime_state(&state_file, |state| {
            let at_least_context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-alo",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &at_least_context.context_id,
                "tool",
                "job-alo",
                "digest-alo",
            )?;
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-alo"]["job_id"] =
                json!("job-alo");
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-alo"]
                ["payload_digest"] = json!("digest-alo");
            realtime_state::begin_realtime_delivery_in_state(
                state,
                "session",
                "job-alo",
                "digest-alo",
                RetryPolicy::AtLeastOnce,
                "claim-alo",
                1,
            )?;
            realtime_state::fail_realtime_delivery_in_state(
                state,
                "session",
                "job-alo",
                "digest-alo",
                RetryPolicy::AtLeastOnce,
                "failed",
                false,
                true,
            )?;
            realtime_state::rotate_current_in_state(state, "session");

            let at_most_context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-amo",
                "now",
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtMostOnce,
            );
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &at_most_context.context_id,
                "tool",
                "job-amo",
                "digest-amo",
            )?;
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-amo"]["job_id"] =
                json!("job-amo");
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-amo"]
                ["payload_digest"] = json!("digest-amo");
            realtime_state::begin_realtime_delivery_in_state(
                state,
                "session",
                "job-amo",
                "digest-amo",
                RetryPolicy::AtMostOnce,
                "claim-amo",
                1,
            )?;
            realtime_state::rotate_current_in_state(state, "session");

            let future_context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-future",
                "now",
                "cccccccccccccccccccccccccccccccc",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &future_context.context_id,
                "tool",
                "job-future",
                "digest-future",
            )?;
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-future"]["job_id"] =
                json!("job-future");
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-future"]
                ["payload_digest"] = json!("digest-future");
            Ok(())
        })
        .unwrap();

        let mut state = load_state(&state_file);
        let suppressed = accounted_realtime_child_span_ids(&state, "session", 2);
        assert!(suppressed.contains(&realtime_state::stable_tool_span_id("tool-amo")));
        assert!(!suppressed.contains(&realtime_state::stable_tool_span_id("tool-alo")));
        assert!(suppressed.contains(&realtime_state::stable_tool_span_id("tool-future")));

        let transition =
            finalize_realtime_high_water(&state_file, &mut state, "session", 2, 100, 4).unwrap();
        assert_eq!(transition.compensated, 1);
        let state = load_state(&state_file);
        assert!(realtime_state::registered_realtime_job(&state, "session", "job-alo").is_none());
        assert!(realtime_state::registered_realtime_job(&state, "session", "job-amo").is_none());
        assert!(realtime_state::registered_realtime_job(&state, "session", "job-future").is_some());
    }

    #[test]
    fn failed_batch_is_inside_stop_finalizer_high_water_and_is_compensated() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-batch",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                3,
                RetryPolicy::AtLeastOnce,
            );
            let children = vec!["generation:g-1".to_string(), "tool:tool-batch".to_string()];
            realtime_state::prepare_realtime_batch_with_id_in_state(
                state,
                "session",
                &context.context_id,
                "completed",
                "batch-1",
                &children,
                "digest-batch",
            )?;
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &context.context_id,
                "batch",
                "batch-1",
                "digest-batch",
            )?;
            realtime_state::persist_tool_job_registration_in_state(
                state,
                "session",
                &context.context_id,
                "tool-batch",
                "batch-1",
                "digest-batch",
            );
            realtime_state::publish_realtime_batch_in_state(state, "session", "batch-1")?;
            realtime_state::begin_realtime_delivery_in_state(
                state,
                "session",
                "batch-1",
                "digest-batch",
                RetryPolicy::AtLeastOnce,
                "claim-batch",
                1,
            )?;
            realtime_state::fail_realtime_delivery_in_state(
                state,
                "session",
                "batch-1",
                "digest-batch",
                RetryPolicy::AtLeastOnce,
                "http 500",
                false,
                false,
            )?;
            realtime_state::fail_realtime_batch_in_state(state, "session", "batch-1", "http 500")?;
            Ok(())
        })
        .unwrap();

        let mut state = load_state(&state_file);
        let high_water =
            realtime_state::record_finalizer_high_water_in_state(&mut state, "session");
        assert_eq!(
            high_water, 1,
            "Batch registrations belong to the Stop barrier"
        );
        assert!(
            !accounted_realtime_child_span_ids(&state, "session", high_water)
                .contains(&realtime_state::stable_tool_span_id("tool-batch"))
        );

        let transition =
            finalize_realtime_high_water(&state_file, &mut state, "session", high_water, 100, 2)
                .unwrap();
        assert_eq!(transition.compensated, 1);
        assert!(realtime_state::registered_realtime_job(
            &load_state(&state_file),
            "session",
            "batch-1"
        )
        .is_none());
    }

    #[test]
    fn finalizer_cleanup_prunes_only_terminal_children_at_or_below_high_water() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        update_realtime_state(&state_file, |state| {
            let old_context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-old",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &old_context.context_id,
                "tool",
                "job-old",
                "digest-old",
            )?;
            realtime_state::begin_realtime_delivery_in_state(
                state,
                "session",
                "job-old",
                "digest-old",
                RetryPolicy::AtLeastOnce,
                "claim-old",
                1,
            )?;
            realtime_state::complete_realtime_delivery_in_state(
                state,
                "session",
                "job-old",
                "digest-old",
            )?;
            realtime_state::rotate_current_in_state(state, "session");

            let live_context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-live",
                "now",
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &live_context.context_id,
                "generation",
                "job-live",
                "digest-live",
            )?;
            state["session"]["realtime_export"]["generation"] = json!({
                "scan_anchor": 0,
                "committed_offset": 0,
                "window_consumed": 0,
                "batch_size": 1,
                "rounds": [
                    {
                        "generation_id": "old-generation",
                        "span_id": "1111111111111111",
                        "context_id": old_context.context_id,
                        "ordinal": 0,
                        "status": "accounted",
                        "job_id": "job-old",
                        "payload_digest": "digest-old"
                    },
                    {
                        "generation_id": "live-generation",
                        "span_id": "2222222222222222",
                        "context_id": live_context.context_id,
                        "ordinal": 1,
                        "status": "pending",
                        "job_id": "job-live",
                        "payload_digest": "digest-live"
                    }
                ]
            });
            Ok(())
        })
        .unwrap();

        let mut state = load_state(&state_file);
        finalize_realtime_high_water(&state_file, &mut state, "session", 1, 321, 9).unwrap();
        let state = load_state(&state_file);
        assert!(realtime_state::registered_realtime_job(&state, "session", "job-old").is_none());
        assert!(realtime_state::registered_realtime_job(&state, "session", "job-live").is_some());
        let rounds = state["session"]["realtime_export"]["generation"]["rounds"]
            .as_array()
            .unwrap();
        assert_eq!(rounds.len(), 1);
        assert_eq!(rounds[0]["generation_id"], json!("live-generation"));
        let contexts = realtime_state::contexts(&state, "session");
        assert_eq!(contexts.len(), 1);
        assert_eq!(contexts[0].tool_use_ids, vec!["tool-live".to_string()]);
        assert_eq!(
            state["session"]["realtime_export"]["generation"]["scan_anchor"],
            json!(321)
        );
        assert_eq!(
            state["session"]["realtime_export"]["generation"]["scan_base_ordinal"],
            json!(9)
        );
    }

    #[test]
    fn generation_defer_requeues_without_consuming_spool_attempt() {
        let dir = tempfile::tempdir().unwrap();
        let payload = json!({
            "transcript_path": "/tmp/missing.jsonl",
            "scan_anchor": 0,
        });
        spool::publish_realtime_job(
            dir.path(),
            spool::RealtimeSpoolKind::Generation,
            "session",
            "context",
            "scan:tool:0",
            payload,
        )
        .unwrap();
        let claimed = spool::claim_next_eligible(dir.path(), Duration::from_secs(60), |_| true)
            .unwrap()
            .unwrap();
        assert_eq!(claimed.job.attempts, 1);
        let pending = spool::defer_claimed(claimed, "scan_byte_cap").unwrap();
        let job: spool::SpoolJob =
            serde_json::from_slice(&std::fs::read(&pending).unwrap()).unwrap();
        assert_eq!(job.attempts, 0);
        assert_eq!(job.last_error.as_deref(), Some("scan_byte_cap"));
    }

    #[test]
    fn open_turn_generation_exports_before_task_complete_without_advancing_anchor() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        std::fs::create_dir_all(&state_dir).unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", &state_dir);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"timestamp":"2026-07-13T00:00:00Z","type":"session_meta","payload":{"id":"session","thread_source":"user"}}
{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-13T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}
{"timestamp":"2026-07-13T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"one"}}
{"timestamp":"2026-07-13T00:00:01.220Z","type":"response_item","payload":{"type":"function_call","name":"Bash","arguments":"{\"cmd\":\"true\"}","call_id":"tool-1"}}
{"timestamp":"2026-07-13T00:00:01.250Z","type":"response_item","payload":{"type":"function_call_output","call_id":"tool-1","output":"{\"stdout\":\"\",\"stderr\":\"\",\"exit_code\":0}"}}
"#,
        )
        .unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(dir.path(), "session").unwrap();
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-1",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            let payload = json!({
                "kind": "realtime_generation_scan",
                "schema_version": 1,
                "session_id": "session",
                "context_id": context.context_id,
                "trigger_tool_use_id": "tool-1",
                "transcript_path": transcript_path,
                "scan_anchor": 0,
                "generation_batch_size": 1,
                "retry_policy": "at_least_once",
                "trace_id": context.trace_id,
                "root_span_id": context.root_span_id,
            });
            let published = spool::publish_realtime_job(
                &session_spool,
                spool::RealtimeSpoolKind::Generation,
                "session",
                &context.context_id,
                "scan:tool-1:0",
                payload,
            )?;
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &context.context_id,
                "generation",
                &published.job_id,
                &published.payload_digest,
            )
            .map(|_| ())
        })
        .unwrap();

        let (host, sink) = spawn_status_sink(200);
        let config_path = dir.path().join("report.config.toml");
        std::fs::write(
            &config_path,
            format!(
                r#"version = 1
default_profile = "local"

[profiles.local]
host = "{}"
public_key = "pk"
secret_key = "sk"
"#,
                host
            ),
        )
        .unwrap();
        let options = WorkerOptions {
            spool_dir: dir.path().to_path_buf(),
            profile: None,
            config_path: Some(config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_secs(2),
            max_attempts: 3,
            debug: true,
        };

        assert!(process_once(&options).unwrap());
        assert_eq!(sink.join().unwrap().len(), 1);
        let state = load_state(&state_file);
        assert_eq!(
            state["session"]["realtime_export"]["generation"]["scan_anchor"],
            json!(0)
        );
        assert_eq!(
            state["session"]["realtime_export"]["generation"]["rounds"][0]["status"],
            json!("accounted")
        );
        std::env::remove_var("CDXHOOKS_STATE_DIR");
    }

    #[test]
    fn realtime_finalizer_exports_usage_only_completed_prefix_without_consuming_pending_successor()
    {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        std::fs::create_dir_all(&state_dir).unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", &state_dir);
        let transcript_path = dir.path().join("session.jsonl");
        let completed_lines = [
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"session_meta","payload":{"id":"session","thread_source":"user","model":"gpt-5"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-usage"}}),
            json!({"timestamp":"2026-09-05T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"run"}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120}}}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-usage","last_agent_message":""}}),
        ];
        let pending_lines = [
            json!({"timestamp":"2026-09-05T00:00:04.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-pending"}}),
            json!({"timestamp":"2026-09-05T00:00:04.100Z","type":"event_msg","payload":{"type":"user_message","message":"next"}}),
        ];
        let completed_text = completed_lines
            .iter()
            .map(|entry| serde_json::to_string(entry).unwrap())
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        let full_text = format!(
            "{}{}",
            completed_text,
            pending_lines
                .iter()
                .map(|entry| serde_json::to_string(entry).unwrap())
                .collect::<Vec<_>>()
                .join("\n")
        ) + "\n";
        std::fs::write(&transcript_path, full_text).unwrap();
        let completed_offset = completed_text.len() as i64;
        assert_eq!(
            completed_prefix_offset_for_jsonl(&transcript_path, completed_lines.len()).unwrap(),
            completed_offset
        );

        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(dir.path(), "session").unwrap();
        let mut captured_context = None;
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-usage",
                "2026-09-05T00:00:01.500Z",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::bind_turn_id_in_state(
                state,
                "session",
                &context.context_id,
                Some("turn-usage"),
            )?;
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            captured_context = Some(context);
            Ok(())
        })
        .unwrap();
        let context = captured_context.expect("realtime context");
        let finalizer_payload = json!({
            "kind": "realtime_finalizer",
            "session_id": "session",
            "context_id": context.context_id,
            "transcript_path": transcript_path,
            "high_water": 1,
            "retry_policy": "at_least_once",
            "trace_id": context.trace_id,
            "root_span_id": context.root_span_id,
        });
        let published = spool::publish_realtime_job(
            &session_spool,
            spool::RealtimeSpoolKind::Finalizer,
            "session",
            &context.context_id,
            "finalizer:1",
            finalizer_payload,
        )
        .unwrap();
        update_realtime_state(&state_file, |state| {
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &context.context_id,
                "finalizer",
                &published.job_id,
                &published.payload_digest,
            )
            .map(|_| ())
        })
        .unwrap();
        let finalizer_job = spool::pending_realtime_jobs(&session_spool, Duration::from_secs(60))
            .unwrap()
            .into_iter()
            .find(|job| job.job_id == published.job_id)
            .expect("published finalizer job");

        let (host, sink) = spawn_status_sink(200);
        let logger = Logger::new(dir.path().join("worker.log"), true);
        let config = LangfuseConfig {
            public_key: Some("pk".to_string()),
            secret_key: Some("sk".to_string()),
            host,
            headers: None,
        };
        process_transcript(
            &finalizer_job,
            &config,
            &logger,
            Duration::from_secs(2),
            Some(FinalizerReconcile {
                high_water: 1,
                retry_policy: RetryPolicy::AtLeastOnce,
                lease_duration: Duration::from_secs(60),
            }),
            None,
            None,
        )
        .unwrap();

        let bodies = sink.join().unwrap();
        assert_eq!(bodies.len(), 1);
        let all_spans = bodies[0]
            .get("resourceSpans")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .flat_map(|resource| {
                resource
                    .get("scopeSpans")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
            })
            .flat_map(|scope| {
                scope
                    .get("spans")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
            })
            .collect::<Vec<_>>();
        let attr_string = |span: &Value, key: &str| {
            span.get("attributes")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .find(|attribute| attribute.get("key").and_then(Value::as_str) == Some(key))
                .and_then(|attribute| attribute.pointer("/value/stringValue"))
                .and_then(Value::as_str)
                .map(str::to_string)
        };
        let llm_spans = all_spans
            .into_iter()
            .filter(|span| attr_string(span, "gen_ai.span.kind").as_deref() == Some("LLM"))
            .collect::<Vec<_>>();
        assert_eq!(llm_spans.len(), 1);
        let attr_int = |span: &Value, key: &str| {
            span.get("attributes")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .find(|attribute| attribute.get("key").and_then(Value::as_str) == Some(key))
                .and_then(|attribute| {
                    attribute
                        .pointer("/value/intValue")
                        .or_else(|| attribute.pointer("/value/stringValue"))
                })
                .and_then(|value| {
                    value
                        .as_str()
                        .map(str::to_string)
                        .or_else(|| value.as_i64().map(|number| number.to_string()))
                })
        };
        assert_eq!(
            attr_int(llm_spans[0], "gen_ai.usage.input_tokens").as_deref(),
            Some("100")
        );
        assert_eq!(
            attr_int(llm_spans[0], "gen_ai.usage.output_tokens").as_deref(),
            Some("20")
        );

        let state = load_state(&state_file);
        assert_eq!(state["session"]["offset"], json!(completed_offset));
        assert_eq!(state["session"]["posted_turn_ids"], json!(["turn-usage"]));
        assert_ne!(
            state["session"]["offset"],
            json!(std::fs::metadata(&transcript_path).unwrap().len() as i64)
        );
        std::env::remove_var("CDXHOOKS_STATE_DIR");
    }

    #[test]
    fn codex_exec_hook_correlates_native_turn_to_call_id_in_open_turn() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        std::fs::create_dir_all(&state_dir).unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", &state_dir);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            include_str!("../../tests/fixtures/codex_realtime_open_turn_exec_call.jsonl"),
        )
        .unwrap();
        let state_file = dirs::session_state_file("session-redacted").unwrap();
        let session_spool = spool::session_root(dir.path(), "session-redacted").unwrap();
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session-redacted",
                "exec-redacted",
                "2026-07-29T01:00:01.500Z",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::bind_turn_id_in_state(
                state,
                "session-redacted",
                &context.context_id,
                Some("turn-redacted"),
            )?;
            realtime_state::ensure_generation_state_in_state(state, "session-redacted", 1);
            let payload = json!({
                "kind": "realtime_generation_scan",
                "schema_version": 2,
                "session_id": "session-redacted",
                "context_id": context.context_id,
                "trigger_turn_id": "turn-redacted",
                "trigger_tool_use_id": "exec-redacted",
                "transcript_path": transcript_path,
                "scan_anchor": 0,
                "cursor_bytes": 0,
                "generation_batch_size": 1,
                "retry_policy": "at_least_once",
                "trace_id": context.trace_id,
                "root_span_id": context.root_span_id,
            });
            let published = spool::publish_realtime_job(
                &session_spool,
                spool::RealtimeSpoolKind::Generation,
                "session-redacted",
                &context.context_id,
                "scan:v2:turn-redacted:exec-redacted:0",
                payload,
            )?;
            realtime_state::register_realtime_job_in_state(
                state,
                "session-redacted",
                &context.context_id,
                "generation",
                &published.job_id,
                &published.payload_digest,
            )
            .map(|_| ())
        })
        .unwrap();

        let (host, sink) = spawn_status_sink(200);
        let config_path = dir.path().join("report.config.toml");
        std::fs::write(
            &config_path,
            format!(
                r#"version = 1
default_profile = "local"

[profiles.local]
host = "{}"
public_key = "pk"
secret_key = "sk"
"#,
                host
            ),
        )
        .unwrap();
        let options = WorkerOptions {
            spool_dir: dir.path().to_path_buf(),
            profile: None,
            config_path: Some(config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_secs(2),
            max_attempts: 3,
            debug: true,
        };

        assert!(process_once(&options).unwrap());
        assert_eq!(
            spool::status(&session_spool).unwrap().done,
            1,
            "schema-v2 turn_id must correlate exec-* hook IDs with call_* transcript IDs"
        );
        assert_eq!(sink.join().unwrap().len(), 1);
        let state = load_state(&state_file);
        assert_eq!(
            state["session-redacted"]["realtime_export"]["generation"]["rounds"][0]["status"],
            json!("accounted")
        );
        assert_eq!(
            state["session-redacted"]["realtime_export"]["generation"]["scan_anchor"],
            json!(0),
            "an open turn must not advance the durable scan anchor"
        );
        std::env::remove_var("CDXHOOKS_STATE_DIR");
    }

    #[test]
    fn schema_v2_generation_orphan_is_reconciled_and_claimed_once_after_restart() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        std::fs::create_dir_all(&state_dir).unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", &state_dir);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            include_str!("../../tests/fixtures/codex_realtime_open_turn_exec_call.jsonl"),
        )
        .unwrap();
        let state_file = dirs::session_state_file("session-redacted").unwrap();
        let session_spool = spool::session_root(dir.path(), "session-redacted").unwrap();
        let mut context_id = String::new();
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session-redacted",
                "exec-redacted",
                "2026-07-29T01:00:01.500Z",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::bind_turn_id_in_state(
                state,
                "session-redacted",
                &context.context_id,
                Some("turn-redacted"),
            )?;
            realtime_state::ensure_generation_state_in_state(state, "session-redacted", 1);
            context_id = context.context_id;
            Ok(())
        })
        .unwrap();
        let payload = json!({
            "kind": "realtime_generation_scan",
            "schema_version": 2,
            "session_id": "session-redacted",
            "context_id": context_id,
            "trigger_turn_id": "turn-redacted",
            "trigger_tool_use_id": "exec-redacted",
            "transcript_path": transcript_path,
            "scan_anchor": 0,
            "cursor_bytes": 0,
            "generation_batch_size": 1,
            "retry_policy": "at_least_once",
            "trace_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "root_span_id": "bbbbbbbbbbbbbbbb",
        });
        let published = spool::publish_realtime_job(
            &session_spool,
            spool::RealtimeSpoolKind::Generation,
            "session-redacted",
            &context_id,
            "scan:v2:turn-redacted:exec-redacted:0",
            payload,
        )
        .unwrap();
        assert!(
            realtime_state::registered_realtime_job(
                &load_state(&state_file),
                "session-redacted",
                &published.job_id
            )
            .is_none(),
            "fixture must model publish-before-register crash"
        );

        let (host, sink) = spawn_status_sink(200);
        let config_path = dir.path().join("report.config.toml");
        std::fs::write(
            &config_path,
            format!(
                r#"version = 1
default_profile = "local"

[profiles.local]
host = "{}"
public_key = "pk"
secret_key = "sk"
"#,
                host
            ),
        )
        .unwrap();
        let options = WorkerOptions {
            spool_dir: dir.path().to_path_buf(),
            profile: None,
            config_path: Some(config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_secs(2),
            max_attempts: 3,
            debug: true,
        };

        assert!(process_once(&options).unwrap());
        assert!(!process_once(&options).unwrap());
        assert_eq!(sink.join().unwrap().len(), 1);
        assert_eq!(spool::status(&session_spool).unwrap().done, 1);
        let registration = realtime_state::registered_realtime_job(
            &load_state(&state_file),
            "session-redacted",
            &published.job_id,
        )
        .unwrap();
        assert_eq!(
            registration.status,
            realtime_state::RealtimeRegisteredJobStatus::Accounted
        );
        std::env::remove_var("CDXHOOKS_STATE_DIR");
    }

    #[test]
    fn schema_v2_generation_with_mismatched_turn_is_not_reconciled_or_claimable() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let mut context_id = String::new();
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "exec-1",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                3,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::bind_turn_id_in_state(
                state,
                "session",
                &context.context_id,
                Some("turn-a"),
            )?;
            context_id = context.context_id;
            Ok(())
        })
        .unwrap();
        let published = spool::publish_realtime_job(
            dir.path(),
            spool::RealtimeSpoolKind::Generation,
            "session",
            &context_id,
            "scan:v2:turn-b:exec-1:0",
            json!({
                "schema_version": 2,
                "session_id": "session",
                "context_id": context_id,
                "trigger_turn_id": "turn-b",
                "trigger_tool_use_id": "exec-1",
                "transcript_path": "/tmp/session.jsonl",
                "scan_anchor": 0,
                "generation_batch_size": 3
            }),
        )
        .unwrap();

        assert_eq!(
            reconcile_pending_realtime_jobs(&state_file, dir.path(), Duration::from_secs(60))
                .unwrap(),
            0
        );
        let state = load_state(&state_file);
        let job = spool::pending_realtime_jobs(dir.path(), Duration::from_secs(60))
            .unwrap()
            .into_iter()
            .find(|job| job.job_id == published.job_id)
            .unwrap();
        assert!(!realtime_job_schema_identity_matches(&state, &job));
        assert!(
            realtime_state::registered_realtime_job(&state, "session", &published.job_id).is_none()
        );
    }

    #[test]
    fn terminal_generation_scan_advances_past_finalizer_only_prior_turn() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        std::fs::create_dir_all(&state_dir).unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", &state_dir);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"timestamp":"2026-07-13T00:00:00Z","type":"session_meta","payload":{"id":"session","thread_source":"user"}}
{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-0"}}
{"timestamp":"2026-07-13T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"first"}}
{"timestamp":"2026-07-13T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"prior"}}
{"timestamp":"2026-07-13T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-0","last_agent_message":"prior"}}
{"timestamp":"2026-07-13T00:00:02Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-13T00:00:02.100Z","type":"event_msg","payload":{"type":"user_message","message":"second"}}
{"timestamp":"2026-07-13T00:00:02.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"trigger"}}
{"timestamp":"2026-07-13T00:00:02.220Z","type":"response_item","payload":{"type":"function_call","name":"Bash","arguments":"{\"cmd\":\"true\"}","call_id":"tool-2"}}
{"timestamp":"2026-07-13T00:00:02.250Z","type":"response_item","payload":{"type":"function_call_output","call_id":"tool-2","output":"{\"exit_code\":0}"}}
{"timestamp":"2026-07-13T00:00:02.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"trigger"}}
"#,
        )
        .unwrap();
        let transcript_len = std::fs::metadata(&transcript_path).unwrap().len();
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(dir.path(), "session").unwrap();
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-2",
                "now",
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            let payload = json!({
                "kind": "realtime_generation_scan",
                "schema_version": 1,
                "session_id": "session",
                "context_id": context.context_id,
                "trigger_tool_use_id": "tool-2",
                "transcript_path": transcript_path,
                "scan_anchor": 0,
                "generation_batch_size": 1,
                "retry_policy": "at_least_once",
                "trace_id": context.trace_id,
                "root_span_id": context.root_span_id,
            });
            let published = spool::publish_realtime_job(
                &session_spool,
                spool::RealtimeSpoolKind::Generation,
                "session",
                &context.context_id,
                "scan:tool-2:0",
                payload,
            )?;
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &context.context_id,
                "generation",
                &published.job_id,
                &published.payload_digest,
            )
            .map(|_| ())
        })
        .unwrap();

        let (host, sink) = spawn_status_sink(200);
        let config_path = dir.path().join("report.config.toml");
        std::fs::write(
            &config_path,
            format!(
                r#"version = 1
default_profile = "local"

[profiles.local]
host = "{}"
public_key = "pk"
secret_key = "sk"
"#,
                host
            ),
        )
        .unwrap();
        let options = WorkerOptions {
            spool_dir: dir.path().to_path_buf(),
            profile: None,
            config_path: Some(config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_secs(2),
            max_attempts: 3,
            debug: true,
        };

        assert!(process_once(&options).unwrap());
        assert_eq!(sink.join().unwrap().len(), 1);
        let state = load_state(&state_file);
        let generation = &state["session"]["realtime_export"]["generation"];
        assert_eq!(generation["scan_anchor"], json!(transcript_len));
        assert_eq!(generation["committed_offset"], json!(transcript_len));
        assert_eq!(generation["scan_base_ordinal"], json!(2));
        assert_eq!(generation["finalizer_only_ordinals"], json!([0]));
        assert_eq!(generation["rounds"][0]["ordinal"], json!(1));
        assert_eq!(generation["rounds"][0]["status"], json!("accounted"));
        std::env::remove_var("CDXHOOKS_STATE_DIR");
    }

    #[test]
    fn post_tool_use_open_turn_retries_state_native_batch_without_spool() {
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        std::fs::create_dir_all(&state_dir).unwrap();
        let _dir_override =
            dirs::override_runtime_dirs_for_test(state_dir.clone(), spool_dir.clone());
        let transcript_path = dir.path().join("session.jsonl");
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(&spool_dir, "session").unwrap();
        let context = CommonContext {
            logger: Logger::new(dir.path().join("hook.log"), true),
            state_file: state_file.clone(),
            langfuse_config: None,
            username: "tester".to_string(),
            sessions_dir: dir.path().to_string_lossy().to_string(),
            hook_version: "test-version".to_string(),
            stop_budget: None,
            retry_policy: RetryPolicy::AtLeastOnce,
            export_mode: RealtimeExportMode::RealtimeWithStopFinalizer,
            realtime_llm_batch_size: 1,
            metrics: None,
        };
        let pre = json!({
            "session_id": "session",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_use_id": "exec-1",
            "cwd": "/repo",
            "tool_input": {"cmd": "true"}
        });
        ToolContextHandler::new()
            .handle(&crate::common::hook::HookEvent::PreToolUse, &pre, &context)
            .unwrap();
        std::fs::write(&transcript_path, "").unwrap();
        let post = json!({
            "session_id": "session",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_use_id": "exec-1",
            "cwd": "/repo",
            "transcript_path": transcript_path,
            "tool_input": {"cmd": "true"},
            "tool_response": "{\"stdout\":\"\",\"stderr\":\"\",\"exit_code\":0}"
        });
        ToolContextHandler::new()
            .handle_post_tool_use(&post, &context, false)
            .unwrap();
        let hook_timing = tool_records::read_for_state_file(&state_file, "session", "exec-1")
            .expect("completed exec-* hook record")
            .completed_timing()
            .unwrap();
        let transcript_started_at = crate::common::logger::now_iso8601_pub();
        let transcript_completed_at = transcript_started_at.clone();
        let transcript = vec![
            json!({
                "timestamp": transcript_started_at,
                "type": "session_meta",
                "payload": {"id": "session", "thread_source": "user"}
            }),
            json!({
                "timestamp": transcript_started_at,
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "turn-1"}
            }),
            json!({
                "timestamp": transcript_started_at,
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "hello"}
            }),
            json!({
                "timestamp": transcript_started_at,
                "type": "event_msg",
                "payload": {
                    "type": "agent_message",
                    "phase": "commentary",
                    "message": "one"
                }
            }),
            json!({
                "timestamp": transcript_started_at,
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "input": "const r = await tools.exec_command({\"cmd\":\"true\"}); text(r.output);",
                    "call_id": "call-1"
                }
            }),
            json!({
                "timestamp": transcript_completed_at,
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "call-1",
                    "output": [{"type": "input_text", "text": "Script completed"}]
                }
            }),
        ]
        .into_iter()
        .map(|entry| serde_json::to_string(&entry).unwrap())
        .collect::<Vec<_>>()
        .join("\n");
        std::fs::write(&transcript_path, format!("{transcript}\n")).unwrap();
        let completed_records = tool_records::completed_for_turn(&state_file, "session", "turn-1");
        assert_eq!(completed_records.len(), 1);
        let transcript_entries = std::fs::read_to_string(&transcript_path)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str::<Value>(line).unwrap())
            .collect::<Vec<_>>();
        let parsed_turns = build_turns(&transcript_entries);
        assert_eq!(parsed_turns.len(), 1);
        assert_eq!(parsed_turns[0].turn_id, "turn-1");
        assert_eq!(parsed_turns[0].tool_calls.len(), 1);
        assert_eq!(parsed_turns[0].tool_calls[0].call_id, "call-1");
        assert_eq!(parsed_turns[0].tool_calls[0].assistant_index, 0);
        let parsed_tool = &parsed_turns[0].tool_calls[0];
        let transcript_started_nanos =
            agentic_otel_common::iso_to_nanos(&parsed_tool.started_timestamp)
                .parse::<u128>()
                .unwrap();
        let transcript_completed_nanos =
            agentic_otel_common::iso_to_nanos(&parsed_tool.ended_timestamp)
                .parse::<u128>()
                .unwrap();
        assert!(completed_records[0].tool_use_id.starts_with("exec-"));
        assert!(completed_records[0].assistant_index.is_none());
        assert!(completed_records[0].semantic_generation_ordinal.is_none());
        assert!(
            hook_timing.started_at_unix_nano.parse::<u128>().unwrap()
                >= transcript_started_nanos.saturating_sub(REALTIME_TOOL_CORRELATION_SKEW_NANOS)
        );
        assert!(
            hook_timing.completed_at_unix_nano.parse::<u128>().unwrap()
                <= transcript_completed_nanos.saturating_add(REALTIME_TOOL_CORRELATION_SKEW_NANOS)
        );
        let pending_jobs = realtime_state::pending_flush_jobs(&load_state(&state_file), "session");
        assert_eq!(pending_jobs.len(), 1);
        assert_eq!(
            pending_jobs[0].job_kind,
            spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Flush)
        );
        assert!(pending_jobs[0].otel.is_null());
        assert!(!session_spool.exists());
        let (host, sink) = spawn_status_sequence_sink(vec![503, 200]);
        let config = LangfuseConfig {
            public_key: Some("pk".to_string()),
            secret_key: Some("sk".to_string()),
            host,
            headers: None,
        };
        let first_error = process_pending_realtime_flushes(
            "session",
            &state_file,
            &context.logger,
            Some(&config),
            10,
        )
        .unwrap_err();
        assert!(first_error.contains("503"));
        let failed_state = load_state(&state_file);
        assert_eq!(
            realtime_state::pending_flush_jobs(&failed_state, "session").len(),
            1,
            "retryable delivery retains the immutable state trigger"
        );
        assert!(
            failed_state["session"]["realtime_export"]["generation"]["rounds"]
                .as_array()
                .is_none_or(Vec::is_empty)
        );
        let failed_batch = failed_state["session"]["realtime_export"]["batches"]
            .as_object()
            .unwrap()
            .values()
            .next()
            .unwrap();
        assert_eq!(failed_batch["status"], "failure");
        let failed_batch_id = failed_batch["batch_id"].as_str().unwrap();
        assert_eq!(
            realtime_state::registered_realtime_job(&failed_state, "session", failed_batch_id,)
                .unwrap()
                .status,
            realtime_state::RealtimeRegisteredJobStatus::Queued
        );
        assert!(!session_spool.exists());

        assert_eq!(
            process_pending_realtime_flushes(
                "session",
                &state_file,
                &context.logger,
                Some(&config),
                10,
            )
            .unwrap(),
            1
        );
        let bodies = sink.join().unwrap();
        assert_eq!(bodies.len(), 2);
        assert_eq!(
            bodies[0], bodies[1],
            "retry must rebuild the same OTLP batch"
        );
        let spans = bodies
            .last()
            .into_iter()
            .flat_map(|body| {
                body["resourceSpans"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .flat_map(|resource| {
                        resource["scopeSpans"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .flat_map(|scope| scope["spans"].as_array().unwrap())
                    })
            })
            .collect::<Vec<_>>();
        let span_names = spans
            .iter()
            .filter_map(|span| span["name"].as_str())
            .collect::<Vec<_>>();
        assert_eq!(spans.len(), 2, "span names={span_names:?}");
        let tool_span = spans
            .iter()
            .find(|span| span["name"] == "tool exec")
            .expect("realtime Tool span");
        assert!(
            spans.iter().any(|span| span["name"]
                .as_str()
                .is_some_and(|name| name.starts_with("chat "))),
            "open-turn PostToolUse must export a realtime chat span"
        );
        let tool_started_at = tool_span["startTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse::<u128>()
            .unwrap();
        let tool_completed_at = tool_span["endTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse::<u128>()
            .unwrap();
        assert_eq!(
            tool_started_at,
            hook_timing.started_at_unix_nano.parse::<u128>().unwrap()
        );
        assert_eq!(
            tool_completed_at,
            hook_timing.completed_at_unix_nano.parse::<u128>().unwrap(),
            "realtime Tool duration must come from the PreToolUse/PostToolUse hook interval"
        );
        assert_eq!(
            tool_span["spanId"],
            json!(deterministic_tool_span_id("call-1")),
            "the exported span identity remains canonical to the transcript call id"
        );
        assert!(!session_spool.exists());
        let state = load_state(&state_file);
        assert!(realtime_state::pending_flush_jobs(&state, "session").is_empty());
        let successful_batch = state["session"]["realtime_export"]["batches"]
            .as_object()
            .unwrap()
            .values()
            .find(|batch| batch["status"] == "success")
            .expect("successful realtime batch");
        assert!(successful_batch["child_ids"]
            .as_array()
            .unwrap()
            .iter()
            .any(|child| child == "tool:call-1"));
        let realtime_context = realtime_state::contexts(&state, "session")
            .into_iter()
            .next()
            .expect("active realtime context");
        assert!(realtime_context
            .tool_use_ids
            .iter()
            .any(|tool_use_id| tool_use_id == "call-1"));
        assert_eq!(
            realtime_context.tool_span_ids["call-1"],
            deterministic_tool_span_id("call-1"),
            "the canonical transcript identity must be persisted for Stop suppression"
        );
        assert!(
            accounted_realtime_child_span_ids(&state, "session", u64::MAX)
                .contains(&deterministic_tool_span_id("call-1")),
            "Stop must suppress the Tool span already exported by the realtime batch"
        );
        assert!(
            spans
                .iter()
                .all(|span| span["spanId"] != realtime_context.root_span_id),
            "the turn root span remains Stop-only"
        );
        assert!(spans
            .iter()
            .all(|span| span["parentSpanId"] == realtime_context.root_span_id));
        let registrations = state["session"]["realtime_export"]["registered_jobs"]
            .as_object()
            .unwrap();
        assert_eq!(registrations.len(), 2);
        assert!(registrations
            .values()
            .all(|job| job["status"] == "accounted"));
        assert!(state["session"]["realtime_export"]["batches"]
            .as_object()
            .unwrap()
            .values()
            .any(|batch| batch["child_ids"]
                .as_array()
                .is_some_and(|ids| ids.iter().any(|id| id == "tool:call-1"))));
        let timing_log = std::fs::read_to_string(dir.path().join("hook.log")).unwrap();
        for operation in [
            "batch_begin",
            "batch_failure",
            "batch_success",
            "generation_accounted",
            "flush_begin",
            "flush_success",
            "flush_cleanup",
        ] {
            assert!(
                timing_log.contains(&format!("\"operation\":\"{operation}\"")),
                "missing checkpoint timing operation={operation}"
            );
        }
        assert!(timing_log.contains("\"phase\":\"checkpoint\""));
    }

    #[test]
    fn code_mode_children_use_exact_hook_records_in_realtime_and_finalizer() {
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        std::fs::create_dir_all(&state_dir).unwrap();
        let _dir_override =
            dirs::override_runtime_dirs_for_test(state_dir.clone(), spool_dir.clone());
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(&transcript_path, "").unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let context = CommonContext {
            logger: Logger::new(dir.path().join("hook.log"), true),
            state_file: state_file.clone(),
            langfuse_config: None,
            username: "tester".to_string(),
            sessions_dir: dir.path().to_string_lossy().to_string(),
            hook_version: "test-version".to_string(),
            stop_budget: None,
            retry_policy: RetryPolicy::AtLeastOnce,
            export_mode: RealtimeExportMode::RealtimeWithStopFinalizer,
            realtime_llm_batch_size: 1,
            metrics: None,
        };
        let handler = ToolContextHandler::new();
        let tool_ids = (1..=5)
            .map(|index| format!("exec-{index}"))
            .collect::<Vec<_>>();
        let mut hook_timings = HashMap::new();
        for tool_id in &tool_ids {
            let pre = json!({
                "session_id": "session",
                "turn_id": "turn-1",
                "tool_name": "Bash",
                "tool_use_id": tool_id,
                "cwd": "/repo",
                "tool_input": {"cmd": format!("printf {tool_id}")}
            });
            handler
                .handle(&crate::common::hook::HookEvent::PreToolUse, &pre, &context)
                .unwrap();
            let post = json!({
                "session_id": "session",
                "turn_id": "turn-1",
                "tool_name": "Bash",
                "tool_use_id": tool_id,
                "cwd": "/repo",
                "transcript_path": transcript_path,
                "tool_input": {"cmd": format!("printf {tool_id}")},
                "tool_response": {"stdout": tool_id, "stderr": "", "exit_code": 0}
            });
            handler
                .handle_post_tool_use(&post, &context, false)
                .unwrap();
            let timing = tool_records::read_for_state_file(&state_file, "session", tool_id)
                .expect("completed Code Mode hook record")
                .completed_timing()
                .unwrap();
            hook_timings.insert(tool_id.clone(), timing);
        }

        let mut transcript = vec![
            json!({
                "timestamp": "2020-01-01T00:00:00.000Z",
                "type": "session_meta",
                "payload": {"id": "session", "thread_source": "user"}
            }),
            json!({
                "timestamp": "2020-01-01T00:00:00.010Z",
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "turn-1"}
            }),
            json!({
                "timestamp": "2020-01-01T00:00:00.020Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "run five commands"}
            }),
            json!({
                "timestamp": "2020-01-01T00:00:00.030Z",
                "type": "event_msg",
                "payload": {"type": "agent_message", "phase": "commentary", "message": "running"}
            }),
            json!({
                "timestamp": "2020-01-01T00:00:00.040Z",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "input": "Promise.all([1,2,3,4,5].map(run))",
                    "call_id": "call-wrapper"
                }
            }),
        ];
        for (index, tool_id) in tool_ids.iter().enumerate() {
            let started_at_ms = 1_577_836_800_100_u64 + index as u64;
            transcript.push(json!({
                "timestamp": format!("2020-01-01T00:00:00.1{index:02}Z"),
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "turn_id": "turn-1",
                    "item": {
                        "type": "CommandExecution",
                        "id": tool_id,
                        "command": ["/bin/sh", "-lc", format!("printf {tool_id}")],
                        "status": "completed",
                        "exit_code": 0,
                        "aggregated_output": tool_id
                    },
                    "started_at_ms": started_at_ms,
                    "completed_at_ms": started_at_ms + 50
                }
            }));
        }
        transcript.push(json!({
            "timestamp": "2020-01-01T00:00:00.200Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call-wrapper",
                "output": [{"type": "input_text", "text": "Script completed"}]
            }
        }));
        let transcript_text = transcript
            .iter()
            .map(|entry| serde_json::to_string(entry).unwrap())
            .collect::<Vec<_>>()
            .join("\n");
        std::fs::write(&transcript_path, format!("{transcript_text}\n")).unwrap();

        let parsed_turns = build_turns(&transcript);
        assert_eq!(parsed_turns.len(), 1);
        assert_eq!(
            parsed_turns[0]
                .tool_calls
                .iter()
                .map(|tool| tool.call_id.as_str())
                .collect::<Vec<_>>(),
            tool_ids.iter().map(String::as_str).collect::<Vec<_>>()
        );
        assert!(parsed_turns[0]
            .tool_calls
            .iter()
            .all(|tool| tool.ended_timestamp != tool.started_timestamp));

        let (host, sink) = spawn_status_sink(200);
        let config = LangfuseConfig {
            public_key: Some("pk".to_string()),
            secret_key: Some("sk".to_string()),
            host,
            headers: None,
        };
        assert!(
            process_pending_realtime_flushes(
                "session",
                &state_file,
                &context.logger,
                Some(&config),
                10,
            )
            .unwrap()
                >= 1
        );
        let bodies = sink.join().unwrap();
        assert_eq!(bodies.len(), 1);
        let spans = bodies[0]["resourceSpans"]
            .as_array()
            .into_iter()
            .flatten()
            .flat_map(|resource| resource["scopeSpans"].as_array().into_iter().flatten())
            .flat_map(|scope| scope["spans"].as_array().into_iter().flatten())
            .collect::<Vec<_>>();
        for tool_id in &tool_ids {
            let tool_span = spans
                .iter()
                .find(|span| {
                    span["attributes"].as_array().is_some_and(|attributes| {
                        attributes.iter().any(|attribute| {
                            attribute["key"] == "gen_ai.tool.call.id"
                                && attribute["value"]["stringValue"] == tool_id.as_str()
                        })
                    })
                })
                .unwrap_or_else(|| panic!("missing realtime span for {tool_id}; spans={spans:#?}"));
            let timing = &hook_timings[tool_id];
            assert_eq!(
                tool_span["startTimeUnixNano"],
                json!(timing.started_at_unix_nano)
            );
            assert_eq!(
                tool_span["endTimeUnixNano"],
                json!(timing.completed_at_unix_nano)
            );
            let record = tool_records::read_for_state_file(&state_file, "session", tool_id)
                .expect("mapped Code Mode hook record");
            assert_eq!(record.assistant_index, Some(0));
            assert_eq!(record.semantic_generation_ordinal, Some(0));
        }
        let state = load_state(&state_file);
        let successful_batch = state["session"]["realtime_export"]["batches"]
            .as_object()
            .unwrap()
            .values()
            .find(|batch| batch["status"] == "success")
            .expect("successful Code Mode realtime batch");
        for tool_id in &tool_ids {
            assert!(successful_batch["child_ids"]
                .as_array()
                .unwrap()
                .iter()
                .any(|child| child == &format!("tool:{tool_id}")));
        }

        let records = tool_records::completed_for_turn(&state_file, "session", "turn-1");
        let mut finalizer_body = build_otel_export_request_for_turn(
            &parsed_turns[0],
            1,
            "session",
            "tester",
            "test-version",
            None,
        );
        assert_eq!(
            overlay_finalizer_tool_timings(&mut finalizer_body, &parsed_turns[0], &records),
            5
        );
        for tool_id in &tool_ids {
            assert!(
                accounted_realtime_child_span_ids(&state, "session", u64::MAX)
                    .contains(&deterministic_tool_span_id(tool_id))
            );
        }
    }

    #[test]
    fn permanent_realtime_failure_is_terminal_without_blocking_later_flush() {
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        std::fs::create_dir_all(&state_dir).unwrap();
        let _dir_override =
            dirs::override_runtime_dirs_for_test(state_dir.clone(), spool_dir.clone());
        let transcript_path = dir.path().join("session.jsonl");
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(&spool_dir, "session").unwrap();
        let context = CommonContext {
            logger: Logger::new(dir.path().join("hook.log"), true),
            state_file: state_file.clone(),
            langfuse_config: None,
            username: "tester".to_string(),
            sessions_dir: dir.path().to_string_lossy().to_string(),
            hook_version: "test-version".to_string(),
            stop_budget: None,
            retry_policy: RetryPolicy::AtLeastOnce,
            export_mode: RealtimeExportMode::RealtimeWithStopFinalizer,
            realtime_llm_batch_size: 1,
            metrics: None,
        };
        let handler = ToolContextHandler::new();
        let first_pre = json!({
            "session_id": "session",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_use_id": "exec-1",
            "cwd": "/repo",
            "tool_input": {"cmd": "true"}
        });
        handler
            .handle(
                &crate::common::hook::HookEvent::PreToolUse,
                &first_pre,
                &context,
            )
            .unwrap();
        std::fs::write(&transcript_path, "").unwrap();
        let first_post = json!({
            "session_id": "session",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_use_id": "exec-1",
            "cwd": "/repo",
            "transcript_path": transcript_path,
            "tool_input": {"cmd": "true"},
            "tool_response": "{\"stdout\":\"\",\"stderr\":\"\",\"exit_code\":0}"
        });
        handler
            .handle_post_tool_use(&first_post, &context, false)
            .unwrap();

        let first_timestamp = crate::common::logger::now_iso8601_pub();
        let first_entries = [
            json!({
                "timestamp": first_timestamp,
                "type": "session_meta",
                "payload": {"id": "session", "thread_source": "user"}
            }),
            json!({
                "timestamp": first_timestamp,
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "turn-1"}
            }),
            json!({
                "timestamp": first_timestamp,
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "hello"}
            }),
            json!({
                "timestamp": first_timestamp,
                "type": "event_msg",
                "payload": {"type": "agent_message", "phase": "commentary", "message": "one"}
            }),
            json!({
                "timestamp": first_timestamp,
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "input": "const r = await tools.exec_command({\"cmd\":\"true\"}); text(r.output);",
                    "call_id": "call-1"
                }
            }),
            json!({
                "timestamp": first_timestamp,
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "call-1",
                    "output": [{"type": "input_text", "text": "Script completed"}]
                }
            }),
        ];
        let first_transcript = first_entries
            .iter()
            .map(|entry| serde_json::to_string(entry).unwrap())
            .collect::<Vec<_>>()
            .join("\n");
        std::fs::write(&transcript_path, format!("{first_transcript}\n")).unwrap();

        let (failed_host, failed_sink) = spawn_status_sink(400);
        let failed_config = LangfuseConfig {
            public_key: Some("pk".to_string()),
            secret_key: Some("sk".to_string()),
            host: failed_host,
            headers: None,
        };
        assert_eq!(
            process_pending_realtime_flushes(
                "session",
                &state_file,
                &context.logger,
                Some(&failed_config),
                10,
            )
            .unwrap(),
            1
        );
        assert_eq!(failed_sink.join().unwrap().len(), 1);
        let failed_state = load_state(&state_file);
        assert!(realtime_state::pending_flush_jobs(&failed_state, "session").is_empty());
        let failed_batch = failed_state["session"]["realtime_export"]["batches"]
            .as_object()
            .unwrap()
            .values()
            .next()
            .unwrap();
        assert_eq!(failed_batch["status"], "failure");
        let failed_batch_id = failed_batch["batch_id"].as_str().unwrap();
        assert_eq!(
            realtime_state::registered_realtime_job(&failed_state, "session", failed_batch_id)
                .unwrap()
                .status,
            realtime_state::RealtimeRegisteredJobStatus::PermanentFailed
        );
        assert!(
            failed_state["session"]["realtime_export"]["generation"]["rounds"]
                .as_array()
                .unwrap()
                .iter()
                .all(|round| {
                    round["status"] == "policy_terminal"
                        && round["terminal_reason"] == "http_permanent_failure"
                })
        );
        assert!(!session_spool.exists());

        let second_pre = json!({
            "session_id": "session",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_use_id": "exec-2",
            "cwd": "/repo",
            "tool_input": {"cmd": "printf two"}
        });
        handler
            .handle(
                &crate::common::hook::HookEvent::PreToolUse,
                &second_pre,
                &context,
            )
            .unwrap();
        let second_post = json!({
            "session_id": "session",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_use_id": "exec-2",
            "cwd": "/repo",
            "transcript_path": transcript_path,
            "tool_input": {"cmd": "printf two"},
            "tool_response": "{\"stdout\":\"two\",\"stderr\":\"\",\"exit_code\":0}"
        });
        handler
            .handle_post_tool_use(&second_post, &context, false)
            .unwrap();
        let second_timestamp = crate::common::logger::now_iso8601_pub();
        let second_entries = [
            json!({
                "timestamp": second_timestamp,
                "type": "event_msg",
                "payload": {"type": "agent_message", "phase": "commentary", "message": "two"}
            }),
            json!({
                "timestamp": second_timestamp,
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "input": "const r = await tools.exec_command({\"cmd\":\"printf two\"}); text(r.output);",
                    "call_id": "call-2"
                }
            }),
            json!({
                "timestamp": second_timestamp,
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "call-2",
                    "output": [{"type": "input_text", "text": "two"}]
                }
            }),
        ];
        let mut transcript = std::fs::OpenOptions::new()
            .append(true)
            .open(&transcript_path)
            .unwrap();
        for entry in second_entries {
            writeln!(transcript, "{}", serde_json::to_string(&entry).unwrap()).unwrap();
        }
        drop(transcript);

        let (success_host, success_sink) = spawn_status_sink(200);
        let success_config = LangfuseConfig {
            public_key: Some("pk".to_string()),
            secret_key: Some("sk".to_string()),
            host: success_host,
            headers: None,
        };
        assert_eq!(
            process_pending_realtime_flushes(
                "session",
                &state_file,
                &context.logger,
                Some(&success_config),
                10,
            )
            .unwrap(),
            1
        );
        let success_bodies = success_sink.join().unwrap();
        assert_eq!(success_bodies.len(), 1);
        let success_state = load_state(&state_file);
        assert!(realtime_state::pending_flush_jobs(&success_state, "session").is_empty());
        assert!(success_state["session"]["realtime_export"]["batches"]
            .as_object()
            .unwrap()
            .values()
            .any(|batch| {
                batch["status"] == "success"
                    && batch["child_ids"]
                        .as_array()
                        .is_some_and(|ids| ids.iter().any(|id| id == "tool:call-2"))
            }));
        assert!(!session_spool.exists());
    }

    #[test]
    fn prepared_batch_is_republished_deterministically_after_crash_before_spool_write() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let body = json!({"resourceSpans": []});
        let payload_digest = spool::payload_digest(&body).unwrap();
        let member_ids = vec!["generation:g-1".to_string(), "tool:t-1".to_string()];
        let subject = "completed:stable";
        let expected_id = spool::deterministic_realtime_job_id_for_session_schema(
            spool::RealtimeSpoolKind::Batch,
            spool::CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
            "session",
            "context",
            subject,
            &payload_digest,
        );
        update_realtime_state(&state_file, |state| {
            realtime_state::prepare_realtime_batch_with_id_in_state(
                state,
                "session",
                "context",
                "completed",
                &expected_id,
                &member_ids,
                &payload_digest,
            )?;
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                "context",
                spool::RealtimeSpoolKind::Batch.as_str(),
                &expected_id,
                &payload_digest,
            )
            .map(|_| ())
        })
        .unwrap();
        assert!(
            spool::pending_realtime_jobs(dir.path(), Duration::from_secs(60))
                .unwrap()
                .is_empty(),
            "fixture models a crash after durable prepare and before spool publish"
        );

        let first = spool::publish_realtime_batch_job(
            dir.path(),
            "session",
            "context",
            subject,
            body.clone(),
            member_ids.clone(),
            vec![
                "1111111111111111".to_string(),
                "2222222222222222".to_string(),
            ],
            vec!["generation-digest".to_string(), "tool-digest".to_string()],
            Some(json!(1)),
            0,
            RetryPolicy::AtLeastOnce.as_str(),
        )
        .unwrap();
        let second = spool::publish_realtime_batch_job(
            dir.path(),
            "session",
            "context",
            subject,
            body,
            member_ids.clone(),
            vec![
                "1111111111111111".to_string(),
                "2222222222222222".to_string(),
            ],
            vec!["generation-digest".to_string(), "tool-digest".to_string()],
            Some(json!(1)),
            0,
            RetryPolicy::AtLeastOnce.as_str(),
        )
        .unwrap();
        assert_eq!(first.job_id, expected_id);
        assert_eq!(second.job_id, expected_id);
        assert_eq!(first.payload_digest, payload_digest);
        assert_eq!(second.path, first.path);
        let state = load_state(&state_file);
        let batch = realtime_state::realtime_batch(&state, "session", &expected_id).unwrap();
        assert_eq!(batch.child_ids, member_ids);
        assert_eq!(batch.payload_digest, payload_digest);
    }

    #[test]
    fn batch_success_is_accounted_before_claimed_spool_cleanup() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let member_ids = vec!["generation:g-1".to_string()];
        let published = spool::publish_realtime_batch_job(
            dir.path(),
            "session",
            "context",
            "completed:account-first",
            json!({"resourceSpans": []}),
            member_ids.clone(),
            vec!["1111111111111111".to_string()],
            vec!["member-digest".to_string()],
            Some(json!(0)),
            0,
            RetryPolicy::AtLeastOnce.as_str(),
        )
        .unwrap();
        update_realtime_state(&state_file, |state| {
            realtime_state::prepare_realtime_batch_with_id_in_state(
                state,
                "session",
                "context",
                "completed",
                &published.job_id,
                &member_ids,
                &published.payload_digest,
            )?;
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                "context",
                spool::RealtimeSpoolKind::Batch.as_str(),
                &published.job_id,
                &published.payload_digest,
            )?;
            realtime_state::publish_realtime_batch_in_state(state, "session", &published.job_id)
                .map(|_| ())
        })
        .unwrap();
        let claimed = spool::claim_next(dir.path(), Duration::from_secs(60))
            .unwrap()
            .unwrap();
        checkpoint_realtime_delivery_begin(
            &state_file,
            &claimed.job,
            RetryPolicy::AtLeastOnce,
            Duration::from_secs(60),
        )
        .unwrap();
        checkpoint_realtime_delivery_success(&state_file, &claimed.job).unwrap();

        let state = load_state(&state_file);
        assert_eq!(
            realtime_state::registered_realtime_job(&state, "session", &published.job_id)
                .unwrap()
                .status,
            realtime_state::RealtimeRegisteredJobStatus::Accounted
        );
        assert_eq!(
            realtime_state::realtime_batch(&state, "session", &published.job_id)
                .unwrap()
                .status,
            realtime_state::RealtimeBatchStatus::Success
        );
        assert!(
            claimed.path.exists(),
            "durable accounting must complete while the claimed spool file still exists"
        );
        spool::complete(claimed).unwrap();
        assert_eq!(spool::status(dir.path()).unwrap().done, 1);
    }

    #[test]
    fn deferred_generation_does_not_starve_ready_tool_job_in_same_worker_run() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        std::fs::create_dir_all(&state_dir).unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", &state_dir);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-13T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}
{"timestamp":"2026-07-13T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"one"}}
{"timestamp":"2026-07-13T00:00:01.220Z","type":"response_item","payload":{"type":"function_call","name":"Bash","arguments":"{\"cmd\":\"true\"}","call_id":"tool-1"}}
{"timestamp":"2026-07-13T00:00:01.250Z","type":"response_item","payload":{"type":"function_call_output","call_id":"tool-1","output":"{\"exit_code\":0}"}}
"#,
        )
        .unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(dir.path(), "session").unwrap();
        let mut generation_job_id = String::new();
        let mut tool_job_id = String::new();
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-1",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                3,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::ensure_generation_state_in_state(state, "session", 3);
            let generation = spool::publish_realtime_job(
                &session_spool,
                spool::RealtimeSpoolKind::Generation,
                "session",
                &context.context_id,
                "scan:tool-1:0",
                json!({
                    "trigger_tool_use_id": "tool-1",
                    "transcript_path": transcript_path,
                    "scan_anchor": 0,
                    "generation_batch_size": 3,
                    "retry_policy": "at_least_once",
                    "trace_id": context.trace_id,
                    "root_span_id": context.root_span_id,
                }),
            )?;
            generation_job_id = generation.job_id.clone();
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &context.context_id,
                "generation",
                &generation.job_id,
                &generation.payload_digest,
            )?;
            let tool = spool::publish_realtime_job(
                &session_spool,
                spool::RealtimeSpoolKind::Tool,
                "session",
                &context.context_id,
                "tool-1",
                json!({"resourceSpans": []}),
            )?;
            tool_job_id = tool.job_id.clone();
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &context.context_id,
                "tool",
                &tool.job_id,
                &tool.payload_digest,
            )
            .map(|_| ())
        })
        .unwrap();

        let (host, sink) = spawn_status_sink(200);
        let config_path = dir.path().join("report.config.toml");
        std::fs::write(
            &config_path,
            format!(
                r#"version = 1
default_profile = "local"

[profiles.local]
host = "{}"
public_key = "pk"
secret_key = "sk"
"#,
                host
            ),
        )
        .unwrap();
        let options = WorkerOptions {
            spool_dir: dir.path().to_path_buf(),
            profile: None,
            config_path: Some(config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_secs(2),
            max_attempts: 3,
            debug: true,
        };
        let mut deferred_jobs = HashSet::new();

        assert!(process_once_excluding_deferred(&options, &mut deferred_jobs).unwrap());
        assert!(deferred_jobs.contains(&generation_job_id));
        assert!(process_once_excluding_deferred(&options, &mut deferred_jobs).unwrap());
        assert_eq!(sink.join().unwrap().len(), 1);
        let state = load_state(&state_file);
        assert_eq!(
            realtime_state::registered_realtime_job(&state, "session", &tool_job_id)
                .unwrap()
                .status,
            realtime_state::RealtimeRegisteredJobStatus::Accounted
        );
        assert_eq!(spool::status(&session_spool).unwrap().pending, 1);
        assert_eq!(spool::status(&session_spool).unwrap().done, 1);
        std::env::remove_var("CDXHOOKS_STATE_DIR");
    }

    #[test]
    fn at_most_once_tool_http_success_is_accounted() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        std::fs::create_dir_all(&state_dir).unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", &state_dir);
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(dir.path(), "session").unwrap();
        let mut published_job_id = String::new();
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-1",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                3,
                RetryPolicy::AtMostOnce,
            );
            let published = spool::publish_realtime_job(
                &session_spool,
                spool::RealtimeSpoolKind::Tool,
                "session",
                &context.context_id,
                "tool-1",
                json!({"resourceSpans": []}),
            )?;
            published_job_id = published.job_id.clone();
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &context.context_id,
                "tool",
                &published.job_id,
                &published.payload_digest,
            )
            .map(|_| ())
        })
        .unwrap();

        let (host, sink) = spawn_status_sink(200);
        let config_path = dir.path().join("report.config.toml");
        std::fs::write(
            &config_path,
            format!(
                r#"version = 1
default_profile = "local"

[profiles.local]
host = "{}"
public_key = "pk"
secret_key = "sk"
"#,
                host
            ),
        )
        .unwrap();
        let options = WorkerOptions {
            spool_dir: dir.path().to_path_buf(),
            profile: None,
            config_path: Some(config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_secs(2),
            max_attempts: 3,
            debug: true,
        };

        assert!(process_once(&options).unwrap());
        assert_eq!(sink.join().unwrap().len(), 1);
        let state = load_state(&state_file);
        let registered =
            realtime_state::registered_realtime_job(&state, "session", &published_job_id).unwrap();
        assert_eq!(
            registered.status,
            realtime_state::RealtimeRegisteredJobStatus::Accounted
        );
        assert!(registered.claim_id.is_none());
        assert_eq!(registered.lease_until_ms, 0);
        assert_eq!(spool::status(&session_spool).unwrap().done, 1);
        std::env::remove_var("CDXHOOKS_STATE_DIR");
    }

    #[test]
    fn realtime_tool_http_failure_is_classified_without_immediate_retry() {
        let _guard = crate::test_sync::env_lock();
        for (status, expected_status) in [
            (503, realtime_state::RealtimeRegisteredJobStatus::Failed),
            (
                400,
                realtime_state::RealtimeRegisteredJobStatus::PermanentFailed,
            ),
        ] {
            let dir = tempfile::tempdir().unwrap();
            let state_dir = dir.path().join("state");
            std::fs::create_dir_all(&state_dir).unwrap();
            std::env::set_var("CDXHOOKS_STATE_DIR", &state_dir);
            let state_file = dirs::session_state_file("session").unwrap();
            let session_spool = spool::session_root(dir.path(), "session").unwrap();
            let mut published_job_id = String::new();
            update_realtime_state(&state_file, |state| {
                let context = realtime_state::claim_tool_in_state(
                    state,
                    "session",
                    "tool-1",
                    "now",
                    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    None,
                    None,
                    None,
                    None,
                    3,
                    RetryPolicy::AtLeastOnce,
                );
                let published = spool::publish_realtime_job(
                    &session_spool,
                    spool::RealtimeSpoolKind::Tool,
                    "session",
                    &context.context_id,
                    "tool-1",
                    json!({"resourceSpans": []}),
                )?;
                published_job_id = published.job_id.clone();
                realtime_state::register_realtime_job_in_state(
                    state,
                    "session",
                    &context.context_id,
                    "tool",
                    &published.job_id,
                    &published.payload_digest,
                )
                .map(|_| ())
            })
            .unwrap();

            let (host, sink) = spawn_status_sink(status);
            let config_path = dir.path().join("report.config.toml");
            std::fs::write(
                &config_path,
                format!(
                    r#"version = 1
default_profile = "local"

[profiles.local]
host = "{}"
public_key = "pk"
secret_key = "sk"
"#,
                    host
                ),
            )
            .unwrap();
            let options = WorkerOptions {
                spool_dir: dir.path().to_path_buf(),
                profile: None,
                config_path: Some(config_path.to_string_lossy().to_string()),
                stale_after: Duration::from_secs(60),
                http_timeout: Duration::from_secs(20),
                max_attempts: 3,
                debug: true,
            };

            assert!(process_once(&options).unwrap());
            assert_eq!(sink.join().unwrap().len(), 1);
            let state = load_state(&state_file);
            let registered =
                realtime_state::registered_realtime_job(&state, "session", &published_job_id)
                    .unwrap();
            assert_eq!(registered.status, expected_status, "http status={status}");
            assert_eq!(registered.attempts, 1);
            assert_eq!(spool::status(&session_spool).unwrap().failed, 1);
            assert_eq!(spool::status(&session_spool).unwrap().pending, 0);
        }
        std::env::remove_var("CDXHOOKS_STATE_DIR");
    }

    #[test]
    fn generation_at_most_once_begin_failure_does_not_advance_scan_cursor() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-13T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}
{"timestamp":"2026-07-13T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"one"}}
{"timestamp":"2026-07-13T00:00:01.220Z","type":"response_item","payload":{"type":"function_call","name":"Bash","arguments":"{\"cmd\":\"true\"}","call_id":"tool-1"}}
{"timestamp":"2026-07-13T00:00:01.250Z","type":"response_item","payload":{"type":"function_call_output","call_id":"tool-1","output":"{\"exit_code\":0}"}}
"#,
        )
        .unwrap();
        let state_file = dir.path().join("state.json");
        let mut context_id = String::new();
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-1",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtMostOnce,
            );
            context_id = context.context_id;
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            Ok(())
        })
        .unwrap();
        let published = spool::publish_realtime_job(
            dir.path(),
            spool::RealtimeSpoolKind::Generation,
            "session",
            &context_id,
            "scan:tool-1:0",
            json!({
                "trigger_tool_use_id": "tool-1",
                "transcript_path": transcript_path,
                "scan_anchor": 0,
                "generation_batch_size": 1,
                "retry_policy": "at_most_once",
            }),
        )
        .unwrap();
        let job = spool::pending_realtime_jobs(dir.path(), Duration::from_secs(60))
            .unwrap()
            .into_iter()
            .find(|pending| pending.job_id == published.job_id)
            .unwrap();
        let config = LangfuseConfig {
            host: "http://127.0.0.1:9".to_string(),
            public_key: Some("pk".to_string()),
            secret_key: Some("sk".to_string()),
            headers: None,
        };
        let logger = Logger::new(dir.path().join("worker.log"), true);

        let error = process_realtime_generation(
            &job,
            &state_file,
            &config,
            &logger,
            Duration::from_millis(50),
            RetryPolicy::AtMostOnce,
            Duration::from_secs(60),
        )
        .unwrap_err();
        assert!(error.contains("delivery begin"));
        let state = load_state(&state_file);
        assert_eq!(
            state["session"]["realtime_export"]["generation"]["scan_anchor"],
            json!(0)
        );
        assert!(state["session"]["realtime_export"]["generation"]["rounds"]
            .as_array()
            .is_none_or(Vec::is_empty));
    }

    #[test]
    fn corrupt_pending_job_does_not_block_exact_realtime_orphan_reconciliation() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        std::fs::create_dir_all(&state_dir).unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", &state_dir);
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(dir.path(), "session").unwrap();
        let mut context_id = String::new();
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-1",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                3,
                RetryPolicy::AtLeastOnce,
            );
            context_id = context.context_id;
            Ok(())
        })
        .unwrap();
        let published = spool::publish_realtime_job(
            &session_spool,
            spool::RealtimeSpoolKind::Tool,
            "session",
            &context_id,
            "tool-1",
            json!({"resourceSpans": []}),
        )
        .unwrap();
        let corrupt_path = published.path.parent().unwrap().join("000-corrupt.json");
        std::fs::write(&corrupt_path, b"{not-json").unwrap();

        let (host, sink) = spawn_status_sink(200);
        let config_path = dir.path().join("report.config.toml");
        std::fs::write(
            &config_path,
            format!(
                r#"version = 1
default_profile = "local"

[profiles.local]
host = "{}"
public_key = "pk"
secret_key = "sk"
"#,
                host
            ),
        )
        .unwrap();
        let options = WorkerOptions {
            spool_dir: dir.path().to_path_buf(),
            profile: None,
            config_path: Some(config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_secs(2),
            max_attempts: 3,
            debug: true,
        };

        assert!(process_once(&options).unwrap());
        assert_eq!(sink.join().unwrap().len(), 1);
        let state = load_state(&state_file);
        assert_eq!(
            realtime_state::registered_realtime_job(&state, "session", &published.job_id)
                .unwrap()
                .status,
            realtime_state::RealtimeRegisteredJobStatus::Accounted
        );
        let context = realtime_state::contexts(&state, "session")
            .into_iter()
            .find(|context| context.context_id == context_id)
            .unwrap();
        assert_eq!(
            context.tool_jobs["tool-1"].job_id.as_deref(),
            Some(published.job_id.as_str())
        );
        assert_eq!(
            context.tool_jobs["tool-1"].payload_digest.as_deref(),
            Some(published.payload_digest.as_str())
        );
        let spool_status = spool::status(&session_spool).unwrap();
        assert_eq!(spool_status.done, 1);
        assert_eq!(spool_status.failed, 1);
        assert!(!corrupt_path.exists());
        std::env::remove_var("CDXHOOKS_STATE_DIR");
    }

    #[test]
    fn generation_http_failure_uses_at_least_retry_after_delivery_begin() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        std::fs::create_dir_all(&state_dir).unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", &state_dir);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"timestamp":"2026-07-13T00:00:00Z","type":"session_meta","payload":{"id":"session","thread_source":"user"}}
{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-13T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}
{"timestamp":"2026-07-13T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"one"}}
{"timestamp":"2026-07-13T00:00:01.220Z","type":"response_item","payload":{"type":"function_call","name":"Bash","arguments":"{\"cmd\":\"true\"}","call_id":"tool-1"}}
{"timestamp":"2026-07-13T00:00:01.250Z","type":"response_item","payload":{"type":"function_call_output","call_id":"tool-1","output":"ok"}}
{"timestamp":"2026-07-13T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"one"}}
"#,
        )
        .unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(dir.path(), "session").unwrap();
        update_realtime_state(&state_file, |state| {
            let realtime_context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-1",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            let payload = json!({
                "kind": "realtime_generation_scan",
                "schema_version": 1,
                "session_id": "session",
                "context_id": realtime_context.context_id,
                "trigger_tool_use_id": "tool-1",
                "transcript_path": transcript_path,
                "scan_anchor": 0,
                "cursor_bytes": 0,
                "generation_batch_size": 1,
                "retry_policy": "at_least_once",
                "trace_id": realtime_context.trace_id,
                "root_span_id": realtime_context.root_span_id,
            });
            let published = spool::publish_realtime_job(
                &session_spool,
                spool::RealtimeSpoolKind::Generation,
                "session",
                &realtime_context.context_id,
                "scan:tool-1:0",
                payload,
            )?;
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &realtime_context.context_id,
                "generation",
                &published.job_id,
                &published.payload_digest,
            )
            .map(|_| ())
        })
        .unwrap();

        let (host, sink) = spawn_status_sink(500);
        let config_path = dir.path().join("report.config.toml");
        std::fs::write(
            &config_path,
            format!(
                r#"version = 1
default_profile = "local"

[profiles.local]
host = "{}"
public_key = "pk"
secret_key = "sk"
"#,
                host
            ),
        )
        .unwrap();
        let options = WorkerOptions {
            spool_dir: dir.path().to_path_buf(),
            profile: None,
            config_path: Some(config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_secs(2),
            max_attempts: 3,
            debug: true,
        };
        assert!(process_once(&options).unwrap());
        assert_eq!(sink.join().unwrap().len(), 1);
        let state = load_state(&state_file);
        let registration = state["session"]["realtime_export"]["registered_jobs"]
            .as_object()
            .unwrap()
            .values()
            .find_map(|value| {
                let job: realtime_state::RealtimeRegisteredJob =
                    serde_json::from_value(value.clone()).ok()?;
                (job.kind == "generation").then_some(job)
            })
            .unwrap();
        assert_eq!(
            registration.status,
            realtime_state::RealtimeRegisteredJobStatus::Queued
        );
        assert_eq!(registration.attempts, 1);
        assert_eq!(spool::status(&session_spool).unwrap().pending, 1);
        assert_eq!(
            state["session"]["realtime_export"]["generation"]["scan_anchor"],
            json!(0)
        );
        assert!(state["session"]["realtime_export"]["generation"]["rounds"]
            .as_array()
            .is_none_or(Vec::is_empty));

        let (host, sink) = spawn_status_sink(200);
        std::fs::write(
            &config_path,
            format!(
                r#"version = 1
default_profile = "local"

[profiles.local]
host = "{}"
public_key = "pk"
secret_key = "sk"
"#,
                host
            ),
        )
        .unwrap();
        assert!(process_once(&options).unwrap());
        assert_eq!(sink.join().unwrap().len(), 1);
        let state = load_state(&state_file);
        let registration = state["session"]["realtime_export"]["registered_jobs"]
            .as_object()
            .unwrap()
            .values()
            .find_map(|value| {
                let job: realtime_state::RealtimeRegisteredJob =
                    serde_json::from_value(value.clone()).ok()?;
                (job.kind == "generation").then_some(job)
            })
            .unwrap();
        assert_eq!(
            registration.status,
            realtime_state::RealtimeRegisteredJobStatus::Accounted
        );
        assert_eq!(
            state["session"]["realtime_export"]["generation"]["rounds"][0]["status"],
            json!("accounted")
        );
        assert_eq!(spool::status(&session_spool).unwrap().done, 1);
        std::env::remove_var("CDXHOOKS_STATE_DIR");
    }

    #[test]
    fn finalizer_e2e_emits_stable_root_and_only_missing_children() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        std::fs::create_dir_all(&state_dir).unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", &state_dir);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"timestamp":"2026-07-13T00:00:00Z","type":"session_meta","payload":{"id":"session","thread_source":"user"}}
{"timestamp":"2026-07-13T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-07-13T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}
{"timestamp":"2026-07-13T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"working"}}
{"timestamp":"2026-07-13T00:00:01.220Z","type":"response_item","payload":{"type":"function_call","name":"Bash","arguments":"{\"cmd\":\"one\"}","call_id":"tool-accounted"}}
{"timestamp":"2026-07-13T00:00:01.250Z","type":"response_item","payload":{"type":"function_call_output","call_id":"tool-accounted","output":"one"}}
{"timestamp":"2026-07-13T00:00:01.270Z","type":"response_item","payload":{"type":"function_call","name":"Bash","arguments":"{\"cmd\":\"two\"}","call_id":"tool-missing"}}
{"timestamp":"2026-07-13T00:00:01.290Z","type":"response_item","payload":{"type":"function_call_output","call_id":"tool-missing","output":"two"}}
{"timestamp":"2026-07-13T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"working"}}
"#,
        )
        .unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(dir.path(), "session").unwrap();
        let mut stable_trace_id = String::new();
        let mut stable_root_span_id = String::new();
        let mut finalizer_context_id = String::new();
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-accounted",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                3,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-missing",
                "now",
                &context.trace_id,
                None,
                None,
                None,
                None,
                3,
                RetryPolicy::AtLeastOnce,
            );
            stable_trace_id = context.trace_id.clone();
            stable_root_span_id = context.root_span_id.clone();
            finalizer_context_id = context.context_id.clone();
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &context.context_id,
                "tool",
                "job-accounted",
                "digest-accounted",
            )?;
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &context.context_id,
                "tool",
                "job-missing",
                "digest-missing",
            )?;
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-accounted"]
                ["job_id"] = json!("job-accounted");
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-accounted"]
                ["payload_digest"] = json!("digest-accounted");
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-missing"]["job_id"] =
                json!("job-missing");
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-missing"]
                ["payload_digest"] = json!("digest-missing");
            realtime_state::begin_realtime_delivery_in_state(
                state,
                "session",
                "job-accounted",
                "digest-accounted",
                RetryPolicy::AtLeastOnce,
                "claim-accounted",
                1,
            )?;
            realtime_state::complete_realtime_delivery_in_state(
                state,
                "session",
                "job-accounted",
                "digest-accounted",
            )
        })
        .unwrap();

        let hook_context = CommonContext {
            logger: Logger::new(dir.path().join("hook.log"), true),
            state_file: state_file.clone(),
            langfuse_config: None,
            username: "tester".to_string(),
            sessions_dir: dir.path().to_string_lossy().to_string(),
            hook_version: "test-version".to_string(),
            stop_budget: None,
            retry_policy: RetryPolicy::AtLeastOnce,
            export_mode: RealtimeExportMode::RealtimeWithStopFinalizer,
            realtime_llm_batch_size: 3,
            metrics: None,
        };
        let hook_started_at = agentic_otel_common::iso_to_nanos("2026-07-13T00:00:01.271Z");
        let hook_completed_at = agentic_otel_common::iso_to_nanos("2026-07-13T00:00:01.286Z");
        tool_records::record_pre(
            &hook_context,
            "session",
            "tool-missing",
            &deterministic_tool_span_id("tool-missing"),
            Some(&finalizer_context_id),
            &stable_trace_id,
            &stable_root_span_id,
            Some("turn-1"),
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
            &hook_started_at,
        )
        .unwrap();
        tool_records::record_post(&hook_context, "session", "tool-missing", &hook_completed_at)
            .unwrap();

        let finalizer_payload = json!({
            "transcript_path": transcript_path,
            "high_water": 2,
            "retry_policy": "at_least_once",
            "username": "tester",
            "hook_version": "test-version",
            "cwd": "/repo",
        });
        let published = spool::publish_realtime_job(
            &session_spool,
            spool::RealtimeSpoolKind::Finalizer,
            "session",
            &finalizer_context_id,
            "finalizer:round-1",
            finalizer_payload,
        )
        .unwrap();
        update_realtime_state(&state_file, |state| {
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &finalizer_context_id,
                "finalizer",
                &published.job_id,
                &published.payload_digest,
            )
            .map(|_| ())
        })
        .unwrap();

        let (host, sink) = spawn_status_sink(200);
        let config_path = dir.path().join("report.config.toml");
        std::fs::write(
            &config_path,
            format!(
                r#"version = 1
default_profile = "local"

[profiles.local]
host = "{}"
public_key = "pk"
secret_key = "sk"
"#,
                host
            ),
        )
        .unwrap();
        let options = WorkerOptions {
            spool_dir: dir.path().to_path_buf(),
            profile: None,
            config_path: Some(config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_secs(2),
            max_attempts: 3,
            debug: true,
        };

        assert!(process_once(&options).unwrap());
        let bodies = sink.join().unwrap();
        let spans = bodies[0]["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .unwrap();
        let accounted_span_id = realtime_state::stable_tool_span_id("tool-accounted");
        let missing_span_id = realtime_state::stable_tool_span_id("tool-missing");
        assert!(spans
            .iter()
            .any(|span| span["spanId"] == stable_root_span_id));
        assert!(!spans.iter().any(|span| span["spanId"] == accounted_span_id));
        let missing_span = spans
            .iter()
            .find(|span| span["spanId"] == missing_span_id)
            .expect("missing realtime Tool is compensated by Stop");
        assert_eq!(
            missing_span["startTimeUnixNano"],
            json!(hook_started_at),
            "Stop compensation must preserve the PreToolUse timestamp"
        );
        assert_eq!(
            missing_span["endTimeUnixNano"],
            json!(hook_completed_at),
            "Stop compensation must preserve the PostToolUse timestamp"
        );
        let duration = missing_span["attributes"]
            .as_array()
            .unwrap()
            .iter()
            .find(|attribute| attribute["key"] == "agentic.tool.duration_ms")
            .expect("Tool duration attribute");
        assert_eq!(duration["value"]["intValue"], json!("15"));
        assert!(spans
            .iter()
            .filter(|span| span["spanId"] != stable_root_span_id)
            .all(|span| span["parentSpanId"] == stable_root_span_id));

        let state = load_state(&state_file);
        assert!(
            realtime_state::registered_realtime_job(&state, "session", "job-accounted").is_none()
        );
        assert!(
            realtime_state::registered_realtime_job(&state, "session", "job-missing").is_none()
        );
        assert!(realtime_state::contexts(&state, "session").is_empty());
        std::env::remove_var("CDXHOOKS_STATE_DIR");
    }

    #[test]
    fn multi_write_stdin_accounted_span_is_stop_stable() {
        let parsed = include_str!("../../tests/fixtures/codex_realtime_multi_write_stdin.jsonl")
            .lines()
            .map(|line| serde_json::from_str::<Value>(line).unwrap())
            .collect::<Vec<_>>();
        let prefix_turns = build_turns(&parsed[..12]);
        let full_turns = build_turns(&parsed);
        let tool_span = |turn: &Turn| {
            let body = build_otel_export_request_for_turn(
                turn,
                1,
                "session-redacted",
                "tester",
                "test",
                None,
            );
            body.pointer("/resourceSpans/0/scopeSpans/0/spans")
                .and_then(Value::as_array)
                .and_then(|spans| spans.iter().find(|span| span["name"] == "tool Bash"))
                .cloned()
                .expect("Bash ToolSpan")
        };
        let prefix_span = tool_span(&prefix_turns[0]);
        let full_span = tool_span(&full_turns[0]);

        assert_eq!(
            prefix_span["spanId"], full_span["spanId"],
            "the ledger sees the same canonical tool identity"
        );
        assert_eq!(
            prefix_span["endTimeUnixNano"], full_span["endTimeUnixNano"],
            "the accounted tool callback must remain immutable at Stop"
        );

        let mut state = json!({});
        let context = realtime_state::claim_tool_in_state(
            &mut state,
            "session-redacted",
            "call-pty-1",
            "now",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::register_realtime_job_in_state(
            &mut state,
            "session-redacted",
            &context.context_id,
            "tool",
            "job-prefix",
            "digest-prefix",
        )
        .unwrap();
        realtime_state::persist_tool_job_registration_in_state(
            &mut state,
            "session-redacted",
            &context.context_id,
            "call-pty-1",
            "job-prefix",
            "digest-prefix",
        );
        realtime_state::begin_realtime_delivery_in_state(
            &mut state,
            "session-redacted",
            "job-prefix",
            "digest-prefix",
            RetryPolicy::AtLeastOnce,
            "claim-prefix",
            1,
        )
        .unwrap();
        realtime_state::complete_realtime_delivery_in_state(
            &mut state,
            "session-redacted",
            "job-prefix",
            "digest-prefix",
        )
        .unwrap();

        assert!(
            accounted_realtime_child_span_ids(&state, "session-redacted", u64::MAX)
                .contains(prefix_span["spanId"].as_str().unwrap()),
            "Stop suppresses the semantically identical accounted callback by stable ID"
        );
    }

    #[test]
    fn generation_batch_selection_uses_stable_contiguous_round_ids() {
        let turn = Turn {
            chat_id: Some("chat".to_string()),
            turn_id: "turn".to_string(),
            user_timestamp: "2026-03-28T08:00:00.000Z".to_string(),
            user_text: "hello".to_string(),
            user_content: serde_json::Value::Null,
            assistants: vec![
                AssistantMessage {
                    timestamp: "2026-03-28T08:00:01.000Z".to_string(),
                    text: "one".to_string(),
                    media_content: serde_json::Value::Null,
                    phase: "commentary".to_string(),
                    usage: TokenUsage {
                        input: Some(1),
                        output: Some(1),
                        total: Some(2),
                        ..TokenUsage::default()
                    },
                    token_count_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
                AssistantMessage {
                    timestamp: "2026-03-28T08:00:03.000Z".to_string(),
                    text: "two".to_string(),
                    media_content: serde_json::Value::Null,
                    phase: "final_answer".to_string(),
                    usage: TokenUsage {
                        input: Some(2),
                        output: Some(2),
                        total: Some(4),
                        ..TokenUsage::default()
                    },
                    token_count_timestamp: "2026-03-28T08:00:04.000Z".to_string(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
            ],
            tool_calls: vec![],
            model: "gpt-5".to_string(),
            cwd: "/repo".to_string(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: "2026-03-28T08:00:05.000Z".to_string(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events: vec![],
            subagent_source: None,
        };
        let turns = vec![turn];
        let (selections, rounds) = select_realtime_generation_batch_from_turns(
            &turns,
            "session",
            "context",
            2,
            5,
            &HashSet::new(),
            &HashSet::from([0]),
            (0, usize::MAX),
            false,
        )
        .unwrap();
        assert_eq!(selections.len(), 2);
        assert_eq!(rounds[0].0, 5);
        assert_eq!(rounds[1].0, 6);
        assert_eq!(selections[0].2, rounds[0].2);
        assert_eq!(
            rounds[0].2,
            realtime_state::stable_generation_span_id(&rounds[0].1)
        );
    }

    #[test]
    fn generation_batch_selection_counts_usage_only_invocations_and_skips_accounted_rounds() {
        let mut turn = test_turn(
            "turn-usage",
            vec![
                AssistantMessage {
                    timestamp: "2026-03-28T08:00:01.000Z".to_string(),
                    text: String::new(),
                    media_content: serde_json::Value::Null,
                    phase: "generation_0".to_string(),
                    usage: TokenUsage {
                        input: Some(100),
                        output: Some(20),
                        total: Some(120),
                        response_id: Some("resp-usage-only".to_string()),
                        ..TokenUsage::default()
                    },
                    token_count_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
                    invocation_started_timestamp: "2026-03-28T08:00:00.500Z".to_string(),
                    invocation_end_source: None,
                },
                test_assistant("visible"),
            ],
            vec![],
        );
        turn.assistants[1].usage.response_id = Some("resp-visible".to_string());
        let turns = vec![turn];

        let (selections, rounds) = select_realtime_generation_batch_from_turns(
            &turns,
            "session",
            "context",
            2,
            7,
            &HashSet::new(),
            &HashSet::from([0]),
            (0, usize::MAX),
            false,
        )
        .expect("usage-only invocation participates in full realtime batch");
        assert_eq!(
            selections
                .iter()
                .map(|(_, assistant_index, _)| *assistant_index)
                .collect::<Vec<_>>(),
            vec![0, 1]
        );
        assert_eq!(
            rounds.iter().map(|round| round.0).collect::<Vec<_>>(),
            vec![7, 8]
        );

        let exported = HashSet::from([7]);
        let (remaining, remaining_rounds) = select_realtime_generation_batch_from_turns(
            &turns,
            "session",
            "context",
            1,
            7,
            &exported,
            &HashSet::from([0]),
            (0, usize::MAX),
            false,
        )
        .expect("accounted usage-only round must not be selected twice");
        assert_eq!(remaining.len(), 1);
        assert_eq!(remaining[0].1, 1);
        assert_eq!(remaining_rounds[0].0, 8);
    }

    fn write_jsonl(path: &Path, entries: &[Value]) -> u64 {
        let text = entries
            .iter()
            .map(|entry| serde_json::to_string(entry).unwrap())
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        std::fs::write(path, text.as_bytes()).unwrap();
        text.len() as u64
    }

    fn append_jsonl(path: &Path, entries: &[Value]) -> (u64, u64) {
        let start = std::fs::metadata(path)
            .map(|metadata| metadata.len())
            .unwrap_or(0);
        let text = entries
            .iter()
            .map(|entry| serde_json::to_string(entry).unwrap())
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .unwrap()
            .write_all(text.as_bytes())
            .unwrap();
        (start, start + text.len() as u64)
    }

    #[test]
    fn generation_window_replay_keeps_mixed_legacy_prefix_until_native_boundary() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript_path = dir.path().join("session.jsonl");
        update_realtime_state(&state_file, |state| {
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            Ok(())
        })
        .unwrap();

        let mixed_entries = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-legacy-prefix"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"legacy"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"legacy output"}]}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":7,"output_tokens":3,"total_tokens":10}}}}),
            json!({"timestamp":"2026-09-05T00:00:02.500Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-legacy-prefix","last_agent_message":""}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-native"}}),
            json!({"timestamp":"2026-09-05T00:00:03.010Z","type":"event_msg","payload":{"type":"user_message","message":"native"}}),
            json!({"timestamp":"2026-09-05T00:00:04.000Z","type":"token_usage_record","payload":{"turn_id":"turn-native","response_id":"resp-native","usage":{"input_tokens":20,"output_tokens":4,"total_tokens":24}}}),
            json!({"timestamp":"2026-09-05T00:00:05.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-native","last_agent_message":""}}),
        ];
        let next_offset = write_jsonl(&transcript_path, &mixed_entries);
        let state = load_state(&state_file);
        let turns = build_turns_for_generation_window(
            &state_file,
            &state,
            "session",
            &transcript_path,
            &mixed_entries,
            0,
            next_offset,
        )
        .unwrap();
        assert_eq!(turns.len(), 2);
        assert_eq!(turns[0].semantic_assistant_count(), 1);
        assert_eq!(turns[0].assistants[0].usage.total, Some(10));
        assert_eq!(
            turns[0].assistants[0].usage.source,
            Some(TokenUsageSource::LegacyTokenCount)
        );
        assert_eq!(turns[1].semantic_assistant_count(), 1);
        assert_eq!(
            turns[1].assistants[0].usage.response_id.as_deref(),
            Some("resp-native")
        );

        let state = load_state(&state_file);
        let native_effective_offset =
            transcript_first_native_usage_offset(&transcript_path, 0, next_offset)
                .unwrap()
                .unwrap();
        assert!(native_effective_offset < next_offset);
        assert_eq!(
            realtime_state::generation_native_usage_mode_effective_offset(&state, "session"),
            Some(native_effective_offset)
        );
        assert!(!realtime_state::generation_native_usage_mode_at(
            &state,
            "session",
            native_effective_offset.saturating_sub(1)
        ));
        assert!(realtime_state::generation_native_usage_mode_at(
            &state,
            "session",
            native_effective_offset
        ));

        let repeated = build_turns_for_generation_window(
            &state_file,
            &state,
            "session",
            &transcript_path,
            &mixed_entries,
            0,
            next_offset,
        )
        .unwrap();
        assert_eq!(repeated[0].semantic_assistant_count(), 1);
        assert_eq!(repeated[0].assistants[0].usage.total, Some(10));
        assert_eq!(
            repeated[0].assistants[0].usage.source,
            Some(TokenUsageSource::LegacyTokenCount)
        );

        let stale_legacy_entries = vec![
            json!({"timestamp":"2026-09-05T00:01:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-late"}}),
            json!({"timestamp":"2026-09-05T00:01:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"continue"}}),
            json!({"timestamp":"2026-09-05T00:01:01.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":7,"output_tokens":3,"total_tokens":10}}}}),
            json!({"timestamp":"2026-09-05T00:01:02.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-late","last_agent_message":""}}),
        ];
        let stale_turns = build_turns_for_generation_window(
            &state_file,
            &state,
            "session",
            &transcript_path,
            &stale_legacy_entries,
            native_effective_offset,
            next_offset + 400,
        )
        .unwrap();
        assert_eq!(stale_turns[0].semantic_assistant_count(), 0);
    }

    #[test]
    fn generation_window_preserves_compaction_token_context_before_native_boundary() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript_path = dir.path().join("session.jsonl");
        update_realtime_state(&state_file, |state| {
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            Ok(())
        })
        .unwrap();
        let entries = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-compact"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"compact"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":200,"output_tokens":5,"total_tokens":205}}}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"compacted","payload":{"replacement_history":[]}}),
            json!({"timestamp":"2026-09-05T00:00:02.010Z","type":"event_msg","payload":{"type":"context_compacted"}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":80,"output_tokens":0,"total_tokens":80}}}}),
            json!({"timestamp":"2026-09-05T00:00:04.000Z","type":"token_usage_record","payload":{"turn_id":"turn-compact","response_id":"resp-after-compact","usage":{"input_tokens":300,"output_tokens":10,"total_tokens":310}}}),
            json!({"timestamp":"2026-09-05T00:00:05.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-compact","last_agent_message":""}}),
        ];
        let next_offset = write_jsonl(&transcript_path, &entries);
        let turns = build_turns_for_generation_window(
            &state_file,
            &load_state(&state_file),
            "session",
            &transcript_path,
            &entries,
            0,
            next_offset,
        )
        .unwrap();

        assert_eq!(turns[0].compactions.len(), 1);
        assert_eq!(turns[0].compactions[0].pre_tokens, Some(200));
        assert_eq!(turns[0].compactions[0].post_tokens, Some(80));
        assert_eq!(turns[0].semantic_assistant_count(), 1);
        let native_effective_offset =
            transcript_first_native_usage_offset(&transcript_path, 0, next_offset)
                .unwrap()
                .unwrap();
        assert_eq!(
            realtime_state::generation_native_usage_mode_effective_offset(
                &load_state(&state_file),
                "session"
            ),
            Some(native_effective_offset)
        );
    }

    #[test]
    fn generation_window_bootstraps_native_mode_for_old_state_with_prior_native_prefix() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript_path = dir.path().join("session.jsonl");
        update_realtime_state(&state_file, |state| {
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            Ok(())
        })
        .unwrap();
        let prefix_entries = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-native-prefix"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"token_usage_record","payload":{"turn_id":"turn-native-prefix","response_id":"resp-prefix","usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120}}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-native-prefix","last_agent_message":""}}),
        ];
        let prefix_offset = write_jsonl(&transcript_path, &prefix_entries);
        let stale_legacy_entries = vec![
            json!({"timestamp":"2026-09-05T00:01:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-stale"}}),
            json!({"timestamp":"2026-09-05T00:01:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"continue"}}),
            json!({"timestamp":"2026-09-05T00:01:01.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120}}}}),
            json!({"timestamp":"2026-09-05T00:01:02.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-stale","last_agent_message":""}}),
        ];
        std::fs::OpenOptions::new()
            .append(true)
            .open(&transcript_path)
            .unwrap()
            .write_all(
                (stale_legacy_entries
                    .iter()
                    .map(|entry| serde_json::to_string(entry).unwrap())
                    .collect::<Vec<_>>()
                    .join("\n")
                    + "\n")
                    .as_bytes(),
            )
            .unwrap();

        let turns = build_turns_for_generation_window(
            &state_file,
            &load_state(&state_file),
            "session",
            &transcript_path,
            &stale_legacy_entries,
            prefix_offset,
            std::fs::metadata(&transcript_path).unwrap().len(),
        )
        .unwrap();
        assert_eq!(turns[0].semantic_assistant_count(), 0);
        let state = load_state(&state_file);
        let native_effective_offset =
            transcript_first_native_usage_offset(&transcript_path, 0, prefix_offset)
                .unwrap()
                .unwrap();
        assert_eq!(
            realtime_state::generation_native_usage_mode_effective_offset(&state, "session"),
            Some(native_effective_offset)
        );
        assert!(realtime_state::generation_native_usage_mode_at(
            &state,
            "session",
            prefix_offset
        ));
    }

    #[test]
    fn generation_window_caches_negative_native_prefix_scan() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript_path = dir.path().join("session.jsonl");
        update_realtime_state(&state_file, |state| {
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            Ok(())
        })
        .unwrap();
        let prefix_entries = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-legacy-prefix"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":30,"output_tokens":5,"total_tokens":35}}}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-legacy-prefix","last_agent_message":""}}),
        ];
        let prefix_offset = write_jsonl(&transcript_path, &prefix_entries);
        let current_entries = vec![
            json!({"timestamp":"2026-09-05T00:01:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-current"}}),
            json!({"timestamp":"2026-09-05T00:01:01.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":40,"output_tokens":6,"total_tokens":46}}}}),
            json!({"timestamp":"2026-09-05T00:01:02.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-current","last_agent_message":""}}),
        ];
        std::fs::OpenOptions::new()
            .append(true)
            .open(&transcript_path)
            .unwrap()
            .write_all(
                (current_entries
                    .iter()
                    .map(|entry| serde_json::to_string(entry).unwrap())
                    .collect::<Vec<_>>()
                    .join(
                        "
",
                    )
                    + "
")
                .as_bytes(),
            )
            .unwrap();

        let turns = build_turns_for_generation_window(
            &state_file,
            &load_state(&state_file),
            "session",
            &transcript_path,
            &current_entries,
            prefix_offset,
            std::fs::metadata(&transcript_path).unwrap().len(),
        )
        .unwrap();
        assert_eq!(turns[0].assistants[0].usage.total, Some(46));
        let state = load_state(&state_file);
        assert_eq!(
            realtime_state::generation_native_usage_mode_effective_offset(&state, "session"),
            None
        );
        assert_eq!(
            realtime_state::generation_native_usage_prefix_checked_offset(&state, "session"),
            Some(prefix_offset)
        );
    }

    #[test]
    fn generation_window_scans_native_prefix_growth_from_cached_offset() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript_path = dir.path().join("session.jsonl");
        update_realtime_state(&state_file, |state| {
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            Ok(())
        })
        .unwrap();
        let legacy_prefix = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-legacy-prefix"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-legacy-prefix","last_agent_message":""}}),
        ];
        let checked_offset = write_jsonl(&transcript_path, &legacy_prefix);
        let native_entries = vec![
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-native-growth"}}),
            json!({"timestamp":"2026-09-05T00:00:04.000Z","type":"token_usage_record","payload":{"turn_id":"turn-native-growth","response_id":"resp-growth","usage":{"input_tokens":50,"output_tokens":5,"total_tokens":55}}}),
            json!({"timestamp":"2026-09-05T00:00:05.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-native-growth","last_agent_message":""}}),
        ];
        let (_, window_start) = append_jsonl(&transcript_path, &native_entries);
        let current_entries = vec![
            json!({"timestamp":"2026-09-05T00:01:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-current"}}),
            json!({"timestamp":"2026-09-05T00:01:01.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":50,"output_tokens":5,"total_tokens":55}}}}),
            json!({"timestamp":"2026-09-05T00:01:02.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-current","last_agent_message":""}}),
        ];
        let (_, next_offset) = append_jsonl(&transcript_path, &current_entries);
        let snapshot = inspect_transcript_source(&transcript_path, None).unwrap();
        update_realtime_state(&state_file, |state| {
            realtime_state::checkpoint_generation_discovery_in_state(
                state,
                "session",
                0,
                checked_offset,
                next_offset,
                &snapshot.epoch,
                "cached_prefix",
            )?;
            realtime_state::mark_generation_native_usage_prefix_checked_in_state(
                state,
                "session",
                checked_offset,
            )
        })
        .unwrap();

        let turns = build_turns_for_generation_window(
            &state_file,
            &load_state(&state_file),
            "session",
            &transcript_path,
            &current_entries,
            window_start,
            next_offset,
        )
        .unwrap();

        assert_eq!(turns[0].semantic_assistant_count(), 0);
        let state = load_state(&state_file);
        assert_eq!(
            realtime_state::generation_native_usage_mode_effective_offset(&state, "session"),
            transcript_first_native_usage_offset(&transcript_path, checked_offset, window_start)
                .unwrap()
        );
    }

    #[test]
    fn generation_window_rewinds_cached_prefix_offset_inside_native_record() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let transcript_path = dir.path().join("session.jsonl");
        update_realtime_state(&state_file, |state| {
            realtime_state::ensure_generation_state_in_state(state, "session", 1);
            Ok(())
        })
        .unwrap();
        let legacy_prefix = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-legacy-prefix"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-legacy-prefix","last_agent_message":""}}),
        ];
        write_jsonl(&transcript_path, &legacy_prefix);
        let native_entries = vec![
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"token_usage_record","payload":{"turn_id":"turn-native","response_id":"resp-native","message":"x".repeat(16 * 1024),"usage":{"input_tokens":90,"output_tokens":9,"total_tokens":99}}}),
        ];
        let (native_start, window_start) = append_jsonl(&transcript_path, &native_entries);
        let current_entries = vec![
            json!({"timestamp":"2026-09-05T00:01:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-current"}}),
            json!({"timestamp":"2026-09-05T00:01:01.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":90,"output_tokens":9,"total_tokens":99}}}}),
            json!({"timestamp":"2026-09-05T00:01:02.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-current","last_agent_message":""}}),
        ];
        let (_, next_offset) = append_jsonl(&transcript_path, &current_entries);
        let snapshot = inspect_transcript_source(&transcript_path, None).unwrap();
        update_realtime_state(&state_file, |state| {
            realtime_state::checkpoint_generation_discovery_in_state(
                state,
                "session",
                0,
                native_start,
                next_offset,
                &snapshot.epoch,
                "cached_prefix",
            )?;
            realtime_state::mark_generation_native_usage_prefix_checked_in_state(
                state,
                "session",
                native_start + (12 * 1024),
            )
        })
        .unwrap();

        let turns = build_turns_for_generation_window(
            &state_file,
            &load_state(&state_file),
            "session",
            &transcript_path,
            &current_entries,
            window_start,
            next_offset,
        )
        .unwrap();

        assert_eq!(turns[0].semantic_assistant_count(), 0);
        assert_eq!(
            realtime_state::generation_native_usage_mode_effective_offset(
                &load_state(&state_file),
                "session"
            ),
            Some(window_start)
        );
    }

    #[test]
    fn generation_batch_selection_claims_largest_contiguous_full_batch_set() {
        let mut turn = Turn {
            chat_id: Some("chat".to_string()),
            turn_id: "turn".to_string(),
            user_timestamp: "2026-03-28T08:00:00.000Z".to_string(),
            user_text: "hello".to_string(),
            user_content: serde_json::Value::Null,
            assistants: Vec::new(),
            tool_calls: vec![],
            model: "gpt-5".to_string(),
            cwd: "/repo".to_string(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: "2026-03-28T08:00:05.000Z".to_string(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events: vec![],
            subagent_source: None,
        };
        for index in 0..6 {
            turn.assistants.push(AssistantMessage {
                timestamp: format!("2026-03-28T08:00:0{index}.000Z"),
                text: format!("assistant {index}"),
                media_content: serde_json::Value::Null,
                phase: "commentary".to_string(),
                usage: TokenUsage {
                    input: Some(1),
                    output: Some(1),
                    total: Some(2),
                    ..TokenUsage::default()
                },
                token_count_timestamp: format!("2026-03-28T08:00:1{index}.000Z"),
                invocation_started_timestamp: String::new(),
                invocation_end_source: None,
            });
        }

        let turns = vec![turn];
        let (first, first_rounds) = select_realtime_generation_batch_from_turns(
            &turns,
            "session",
            "context",
            3,
            0,
            &HashSet::new(),
            &HashSet::from([0]),
            (0, usize::MAX),
            false,
        )
        .unwrap();
        assert_eq!(first.len(), 6);
        assert_eq!(first_rounds[0].0, 0);
        assert_eq!(first_rounds[5].0, 5);

        let exported = first_rounds
            .iter()
            .map(|(ordinal, _, _)| *ordinal)
            .collect::<HashSet<_>>();
        assert!(select_realtime_generation_batch_from_turns(
            &turns,
            "session",
            "context",
            3,
            0,
            &exported,
            &HashSet::from([0]),
            (0, usize::MAX),
            false,
        )
        .is_none());

        let mut four = turns.into_iter().next().unwrap();
        four.assistants.truncate(4);
        let four_turns = vec![four];
        let (full_batch, rounds) = select_realtime_generation_batch_from_turns(
            &four_turns,
            "session",
            "context",
            3,
            0,
            &HashSet::new(),
            &HashSet::from([0]),
            (0, usize::MAX),
            false,
        )
        .unwrap();
        assert_eq!(full_batch.len(), 3);
        assert_eq!(rounds.last().unwrap().0, 2);
    }

    #[test]
    fn generation_batch_selection_spans_prior_turns_and_flushes_terminal_tail() {
        let make_turn = |turn_id: &str, assistant_count: usize| {
            let mut turn = Turn {
                chat_id: Some(turn_id.to_string()),
                turn_id: turn_id.to_string(),
                user_timestamp: "2026-03-28T08:00:00.000Z".to_string(),
                user_text: "hello".to_string(),
                user_content: serde_json::Value::Null,
                assistants: Vec::new(),
                tool_calls: vec![],
                model: "gpt-5".to_string(),
                cwd: "/repo".to_string(),
                system_instructions: String::new(),
                completed: true,
                completion_timestamp: "2026-03-28T08:00:05.000Z".to_string(),
                terminal_state: TurnTerminalState::Completed,
                terminal_error: None,
                trigger: Default::default(),
                skill_invocations: vec![],
                skill_name: None,
                compactions: vec![],
                collab_agent_events: vec![],
                subagent_source: None,
            };
            for index in 0..assistant_count {
                turn.assistants.push(AssistantMessage {
                    timestamp: format!("2026-03-28T08:00:0{index}.000Z"),
                    text: format!("{turn_id} assistant {index}"),
                    media_content: serde_json::Value::Null,
                    phase: "commentary".to_string(),
                    usage: TokenUsage {
                        input: Some(1),
                        output: Some(1),
                        total: Some(2),
                        ..TokenUsage::default()
                    },
                    token_count_timestamp: format!("2026-03-28T08:00:1{index}.000Z"),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                });
            }
            turn
        };
        let turns = vec![make_turn("turn-0", 2), make_turn("turn-1", 3)];

        let (selections, rounds) = select_realtime_generation_batch_from_turns(
            &turns,
            "session",
            "context",
            3,
            0,
            &HashSet::new(),
            &HashSet::from([0, 1]),
            (1, 2),
            true,
        )
        .unwrap();

        assert_eq!(selections.len(), 5);
        assert_eq!(
            selections
                .iter()
                .map(|(turn_index, assistant_index, _)| (*turn_index, *assistant_index))
                .collect::<Vec<_>>(),
            vec![(0, 0), (0, 1), (1, 0), (1, 1), (1, 2)]
        );
        assert_eq!(
            rounds
                .iter()
                .map(|(ordinal, _, _)| *ordinal)
                .collect::<Vec<_>>(),
            vec![0, 1, 2, 3, 4]
        );

        let (_, current_turn_rounds) = select_realtime_generation_batch_from_turns(
            &turns,
            "session",
            "context",
            3,
            0,
            &HashSet::new(),
            &HashSet::from([1]),
            (1, 2),
            true,
        )
        .unwrap();
        assert_eq!(
            current_turn_rounds
                .iter()
                .map(|(ordinal, _, _)| *ordinal)
                .collect::<Vec<_>>(),
            vec![2, 3, 4]
        );
    }

    #[test]
    fn finalizer_trace_root_matches_turn_content_not_context_position() {
        let mut state = json!({});
        let generation_context = realtime_state::claim_tool_in_state(
            &mut state,
            "session",
            "setup-generation",
            "now",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::rotate_current_in_state(&mut state, "session");
        let tool_context = realtime_state::claim_tool_in_state(
            &mut state,
            "session",
            "tool-call",
            "now",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        state["session"]["realtime_export"]["generation"] = json!({
            "scan_anchor": 0,
            "committed_offset": 0,
            "window_consumed": 0,
            "batch_size": 1,
            "rounds": [{
                "generation_id": "generation-0",
                "span_id": "1111111111111111",
                "context_id": generation_context.context_id,
                "ordinal": 0,
                "status": "accounted",
                "job_id": "generation-job",
                "payload_digest": "generation-digest"
            }]
        });
        let generation_turn = test_turn("turn-generation", vec![test_assistant("hello")], vec![]);
        let tool_turn = test_turn(
            "turn-tool",
            vec![test_assistant("tool")],
            vec![ToolCall {
                call_id: "tool-call".to_string(),
                name: "Bash".to_string(),
                input: json!({}),
                output: json!("ok"),
                started_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
                ended_timestamp: "2026-03-28T08:00:03.000Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            }],
        );
        let turns = vec![generation_turn, tool_turn];

        assert_eq!(
            finalizer_trace_root_for_turn_with_scan_base(&state, "session", &turns, 0, 0)
                .unwrap()
                .unwrap(),
            (
                generation_context.trace_id.clone(),
                generation_context.root_span_id.clone()
            )
        );
        assert_eq!(
            finalizer_trace_root_for_turn_with_scan_base(&state, "session", &turns, 1, 0)
                .unwrap()
                .unwrap(),
            (
                tool_context.trace_id.clone(),
                tool_context.root_span_id.clone()
            )
        );
    }

    #[test]
    fn finalizer_trace_root_rejects_ambiguous_exact_turn_matches() {
        let mut state = json!({});
        let first = realtime_state::claim_tool_in_state(
            &mut state,
            "session",
            "tool-a",
            "now",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            "session",
            &first.context_id,
            Some("turn-1"),
        )
        .unwrap();
        realtime_state::rotate_current_in_state(&mut state, "session");
        let second = realtime_state::claim_tool_in_state(
            &mut state,
            "session",
            "tool-b",
            "now",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            "session",
            &second.context_id,
            Some("turn-1"),
        )
        .unwrap();
        let turns = vec![test_turn("turn-1", vec![test_assistant("done")], vec![])];

        assert_eq!(
            finalizer_trace_root_for_turn_with_scan_base(&state, "session", &turns, 0, 0),
            Err("multiple_realtime_matches".to_string()),
            "multiple exact turn contexts must defer instead of choosing or creating a root"
        );
    }

    #[test]
    fn ambiguous_finalizer_topology_is_terminally_quarantined_without_turn_success_accounting() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir.clone(), spool_dir.clone());
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"timestamp":"2026-08-16T00:00:00Z","type":"session_meta","payload":{"id":"session","thread_source":"user"}}
{"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-08-16T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}
{"timestamp":"2026-08-16T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"commentary","message":"working"}}
{"timestamp":"2026-08-16T00:00:01.220Z","type":"response_item","payload":{"type":"function_call","name":"Bash","arguments":"{\"cmd\":\"one\"}","call_id":"tool-1"}}
{"timestamp":"2026-08-16T00:00:01.250Z","type":"response_item","payload":{"type":"function_call_output","call_id":"tool-1","output":"one"}}
{"timestamp":"2026-08-16T00:00:01.270Z","type":"response_item","payload":{"type":"function_call","name":"Bash","arguments":"{\"cmd\":\"two\"}","call_id":"tool-2"}}
{"timestamp":"2026-08-16T00:00:01.290Z","type":"response_item","payload":{"type":"function_call_output","call_id":"tool-2","output":"two"}}
{"timestamp":"2026-08-16T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"working"}}
"#,
        )
        .unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(&spool_dir, "session").unwrap();
        let mut finalizer_job_id = String::new();
        update_realtime_state(&state_file, |state| {
            let first = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-a",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::bind_turn_id_in_state(
                state,
                "session",
                &first.context_id,
                Some("turn-1"),
            )?;
            realtime_state::rotate_current_in_state(state, "session");
            let second = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-b",
                "now",
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::bind_turn_id_in_state(
                state,
                "session",
                &second.context_id,
                Some("turn-1"),
            )?;
            for (context_id, child_id) in [
                (first.context_id.as_str(), "published-child-a"),
                (second.context_id.as_str(), "published-child-b"),
            ] {
                let batch = realtime_state::prepare_realtime_batch_in_state(
                    state,
                    "session",
                    context_id,
                    "tool",
                    &[child_id.to_string()],
                    child_id,
                )?;
                realtime_state::publish_realtime_batch_in_state(state, "session", &batch.batch_id)?;
                realtime_state::succeed_realtime_batch_in_state(state, "session", &batch.batch_id)?;
            }
            let published = spool::publish_realtime_job(
                &session_spool,
                spool::RealtimeSpoolKind::Finalizer,
                "session",
                &second.context_id,
                "finalizer:ambiguous",
                json!({
                    "transcript_path": transcript_path,
                    "high_water": 0,
                    "retry_policy": "at_least_once",
                    "username": "tester",
                    "hook_version": "test-version",
                    "cwd": "/repo"
                }),
            )?;
            finalizer_job_id = published.job_id.clone();
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &second.context_id,
                "finalizer",
                &published.job_id,
                &published.payload_digest,
            )
            .map(|_| ())
        })
        .unwrap();

        let config_path = dir.path().join("report.config.toml");
        std::fs::write(
            &config_path,
            r#"version = 1
default_profile = "local"

[profiles.local]
host = "http://127.0.0.1:9"
public_key = "pk"
secret_key = "sk"
"#,
        )
        .unwrap();
        let options = WorkerOptions {
            spool_dir,
            profile: None,
            config_path: Some(config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_millis(100),
            max_attempts: 3,
            debug: true,
        };
        let mut deferred_jobs = HashSet::new();

        assert!(process_once_session(&options, "session", &mut deferred_jobs).unwrap());
        assert!(!process_once_session(&options, "session", &mut deferred_jobs).unwrap());
        assert!(!deferred_jobs.contains(&finalizer_job_id));

        let status = spool::status(&session_spool).unwrap();
        assert_eq!(status.pending, 0);
        assert_eq!(status.failed, 0);
        assert_eq!(status.done, 1);
        let state = load_state(&state_file);
        assert_eq!(state["session"]["posted_turn_ids"], json!([]));
        assert_eq!(state["session"]["turn_count"], json!(0));
        assert!(crate::hooks::langfuse::state::is_turn_terminally_accounted(
            &state["session"],
            "turn-1"
        ));
        assert_eq!(
            state["session"]["quarantined_turns"][0]["reason"],
            json!(MULTIPLE_PUBLISHED_ROOTS_QUARANTINE_REASON)
        );
        assert_eq!(
            state["session"]["quarantined_turns"][0]["attempt"],
            json!(1)
        );
        assert_eq!(
            state["session"]["quarantined_turns"][0]["original_root_identities"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
        assert!(state["session"]["pending_export_reason"].is_null());
    }

    fn write_single_turn_transcript(path: &Path, turn_id: &str) {
        std::fs::write(
            path,
            format!(
                r#"{{"timestamp":"2026-08-16T00:00:00Z","type":"session_meta","payload":{{"id":"session","thread_source":"user"}}}}
{{"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{{"type":"task_started","turn_id":"{turn_id}"}}}}
{{"timestamp":"2026-08-16T00:00:01.100Z","type":"event_msg","payload":{{"type":"user_message","message":"hello"}}}}
{{"timestamp":"2026-08-16T00:00:01.200Z","type":"event_msg","payload":{{"type":"agent_message","phase":"final_answer","message":"done"}}}}
{{"timestamp":"2026-08-16T00:00:01.300Z","type":"event_msg","payload":{{"type":"task_complete","turn_id":"{turn_id}","last_agent_message":"done"}}}}
"#,
            ),
        )
        .unwrap();
    }

    fn worker_options_with_dead_config(dir: &Path, spool_dir: PathBuf) -> WorkerOptions {
        let config_path = dir.join("report.config.toml");
        std::fs::write(
            &config_path,
            r#"version = 1
default_profile = "local"

[profiles.local]
host = "http://127.0.0.1:9"
public_key = "pk"
secret_key = "sk"
"#,
        )
        .unwrap();
        WorkerOptions {
            spool_dir,
            profile: None,
            config_path: Some(config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_millis(100),
            max_attempts: 3,
            debug: true,
        }
    }

    fn publish_structural_finalizer_job(
        state: &mut Value,
        session_spool: &Path,
        context_id: &str,
        subject_id: &str,
        transcript_path: &Path,
    ) -> Result<String, String> {
        let published = spool::publish_realtime_job(
            session_spool,
            spool::RealtimeSpoolKind::Finalizer,
            "session",
            context_id,
            subject_id,
            json!({
                "transcript_path": transcript_path,
                "high_water": 0,
                "retry_policy": "at_least_once",
                "username": "tester",
                "hook_version": "test-version",
                "cwd": "/repo"
            }),
        )?;
        realtime_state::register_realtime_job_in_state(
            state,
            "session",
            context_id,
            "finalizer",
            &published.job_id,
            &published.payload_digest,
        )?;
        Ok(published.job_id)
    }

    fn assert_structural_finalizer_is_terminal_after_two_worker_rounds(
        dir: &Path,
        spool_dir: PathBuf,
        session_spool: &Path,
        state_file: &Path,
        finalizer_job_id: &str,
        expected_reason: &str,
    ) {
        let options = worker_options_with_dead_config(dir, spool_dir);
        let mut deferred_jobs = HashSet::new();

        assert!(process_once_session(&options, "session", &mut deferred_jobs).unwrap());
        assert!(!process_once_session(&options, "session", &mut deferred_jobs).unwrap());
        assert!(!deferred_jobs.contains(finalizer_job_id));

        let status = spool::status(session_spool).unwrap();
        assert_eq!(status.pending, 0);
        assert_eq!(status.failed, 0);
        assert_eq!(status.done, 1);
        let done_paths = read_sorted_json_files(&session_spool.join("done")).unwrap();
        assert_eq!(done_paths.len(), 1);
        let done_job = spool::read_job(&done_paths[0]).unwrap();
        assert_eq!(done_job.job_id, finalizer_job_id);
        assert_eq!(done_job.attempts, 1);

        let state = load_state(state_file);
        assert_eq!(state["session"]["posted_turn_ids"], json!([]));
        assert_eq!(state["session"]["turn_count"], json!(0));
        assert_eq!(
            state["session"]["quarantined_turns"][0]["reason"],
            json!(expected_reason)
        );
        assert_eq!(
            state["session"]["quarantined_turns"][0]["attempt"],
            json!(1)
        );
        assert!(crate::hooks::langfuse::state::is_turn_terminally_accounted(
            &state["session"],
            "turn-1"
        ));
        assert!(state["session"]["pending_export_reason"].is_null());
    }

    #[test]
    fn fact_conflict_finalizer_topology_is_terminal_not_deferred() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir.clone(), spool_dir.clone());
        let transcript_path = dir.path().join("session.jsonl");
        write_single_turn_transcript(&transcript_path, "turn-1");
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(&spool_dir, "session").unwrap();
        let mut finalizer_job_id = String::new();

        update_realtime_state(&state_file, |state| {
            let first = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-a",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::bind_turn_id_in_state(
                state,
                "session",
                &first.context_id,
                Some("turn-1"),
            )?;
            realtime_state::rotate_current_in_state(state, "session");
            let second = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-b",
                "now",
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::bind_turn_id_in_state(
                state,
                "session",
                &second.context_id,
                Some("turn-1"),
            )?;
            state["session"]["realtime_export"]["pending"][0]["tool_use_ids"] = json!(["shared"]);
            state["session"]["realtime_export"]["pending"][0]["tool_span_ids"] =
                json!({"shared": "1111111111111111"});
            state["session"]["realtime_export"]["current"]["tool_use_ids"] = json!(["shared"]);
            state["session"]["realtime_export"]["current"]["tool_span_ids"] =
                json!({"shared": "2222222222222222"});
            finalizer_job_id = publish_structural_finalizer_job(
                state,
                &session_spool,
                &second.context_id,
                "finalizer:fact-conflict",
                &transcript_path,
            )?;
            Ok(())
        })
        .unwrap();

        assert_structural_finalizer_is_terminal_after_two_worker_rounds(
            dir.path(),
            spool_dir,
            &session_spool,
            &state_file,
            &finalizer_job_id,
            FACT_CONFLICT_QUARANTINE_REASON,
        );
    }

    #[test]
    fn alias_cycle_finalizer_topology_is_terminal_not_deferred() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir.clone(), spool_dir.clone());
        let transcript_path = dir.path().join("session.jsonl");
        write_single_turn_transcript(&transcript_path, "turn-1");
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(&spool_dir, "session").unwrap();
        let mut finalizer_job_id = String::new();

        update_realtime_state(&state_file, |state| {
            let first = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-a",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::bind_turn_id_in_state(
                state,
                "session",
                &first.context_id,
                Some("turn-1"),
            )?;
            realtime_state::rotate_current_in_state(state, "session");
            let second = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-b",
                "now",
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            state["session"]["realtime_export"]["context_aliases"] = json!({
                (first.context_id.clone()): second.context_id.clone(),
                (second.context_id.clone()): first.context_id.clone(),
            });
            finalizer_job_id = publish_structural_finalizer_job(
                state,
                &session_spool,
                &first.context_id,
                "finalizer:alias-cycle",
                &transcript_path,
            )?;
            Ok(())
        })
        .unwrap();

        assert_structural_finalizer_is_terminal_after_two_worker_rounds(
            dir.path(),
            spool_dir,
            &session_spool,
            &state_file,
            &finalizer_job_id,
            ALIAS_CYCLE_QUARANTINE_REASON,
        );
    }

    #[test]
    fn missing_alias_target_finalizer_topology_is_terminal_not_deferred() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir.clone(), spool_dir.clone());
        let transcript_path = dir.path().join("session.jsonl");
        write_single_turn_transcript(&transcript_path, "turn-1");
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(&spool_dir, "session").unwrap();
        let mut finalizer_job_id = String::new();

        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-a",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::bind_turn_id_in_state(
                state,
                "session",
                &context.context_id,
                Some("turn-1"),
            )?;
            state["session"]["realtime_export"]["context_aliases"] = json!({
                (context.context_id.clone()): "missing-context",
            });
            finalizer_job_id = publish_structural_finalizer_job(
                state,
                &session_spool,
                &context.context_id,
                "finalizer:missing-alias-target",
                &transcript_path,
            )?;
            Ok(())
        })
        .unwrap();

        assert_structural_finalizer_is_terminal_after_two_worker_rounds(
            dir.path(),
            spool_dir,
            &session_spool,
            &state_file,
            &finalizer_job_id,
            MISSING_ALIAS_TARGET_QUARANTINE_REASON,
        );
    }

    #[test]
    fn worker_finalizer_quarantines_poison_turn_and_exports_later_clean_turn_once() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir.clone(), spool_dir.clone());
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"timestamp":"2026-08-16T00:00:00Z","type":"session_meta","payload":{"id":"session","thread_source":"user"}}
{"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-bad"}}
{"timestamp":"2026-08-16T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"bad"}}
{"timestamp":"2026-08-16T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"bad done"}}
{"timestamp":"2026-08-16T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-bad","last_agent_message":"bad done"}}
{"timestamp":"2026-08-16T00:00:02Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-clean"}}
{"timestamp":"2026-08-16T00:00:02.100Z","type":"event_msg","payload":{"type":"user_message","message":"clean"}}
{"timestamp":"2026-08-16T00:00:02.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"clean done"}}
{"timestamp":"2026-08-16T00:00:02.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-clean","last_agent_message":"clean done"}}
"#,
        )
        .unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(&spool_dir, "session").unwrap();
        let mut finalizer_context_id = String::new();
        for subject in ["finalizer:poison-clean", "finalizer:duplicate"] {
            update_realtime_state(&state_file, |state| {
                if finalizer_context_id.is_empty() {
                    let first = realtime_state::claim_tool_in_state(
                        state,
                        "session",
                        "tool-a",
                        "now",
                        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        None,
                        None,
                        None,
                        None,
                        1,
                        RetryPolicy::AtLeastOnce,
                    );
                    realtime_state::bind_turn_id_in_state(
                        state,
                        "session",
                        &first.context_id,
                        Some("turn-bad"),
                    )?;
                    realtime_state::rotate_current_in_state(state, "session");
                    let second = realtime_state::claim_tool_in_state(
                        state,
                        "session",
                        "tool-b",
                        "now",
                        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        None,
                        None,
                        None,
                        None,
                        1,
                        RetryPolicy::AtLeastOnce,
                    );
                    realtime_state::bind_turn_id_in_state(
                        state,
                        "session",
                        &second.context_id,
                        Some("turn-bad"),
                    )?;
                    finalizer_context_id = second.context_id.clone();
                    for (context_id, child_id) in [
                        (first.context_id.as_str(), "published-child-a"),
                        (second.context_id.as_str(), "published-child-b"),
                    ] {
                        let batch = realtime_state::prepare_realtime_batch_in_state(
                            state,
                            "session",
                            context_id,
                            "tool",
                            &[child_id.to_string()],
                            child_id,
                        )?;
                        realtime_state::publish_realtime_batch_in_state(
                            state,
                            "session",
                            &batch.batch_id,
                        )?;
                        realtime_state::succeed_realtime_batch_in_state(
                            state,
                            "session",
                            &batch.batch_id,
                        )?;
                    }
                    realtime_state::rotate_current_in_state(state, "session");
                    let clean = realtime_state::claim_tool_in_state(
                        state,
                        "session",
                        "tool-clean",
                        "now",
                        "cccccccccccccccccccccccccccccccc",
                        None,
                        None,
                        None,
                        None,
                        1,
                        RetryPolicy::AtLeastOnce,
                    );
                    realtime_state::bind_turn_id_in_state(
                        state,
                        "session",
                        &clean.context_id,
                        Some("turn-clean"),
                    )?;
                }
                let published = spool::publish_realtime_job(
                    &session_spool,
                    spool::RealtimeSpoolKind::Finalizer,
                    "session",
                    &finalizer_context_id,
                    subject,
                    json!({
                        "transcript_path": transcript_path,
                        "high_water": 0,
                        "retry_policy": "at_least_once",
                        "username": "tester",
                        "hook_version": "test-version",
                        "cwd": "/repo"
                    }),
                )?;
                realtime_state::register_realtime_job_in_state(
                    state,
                    "session",
                    &finalizer_context_id,
                    "finalizer",
                    &published.job_id,
                    &published.payload_digest,
                )
                .map(|_| ())
            })
            .unwrap();
        }

        let (host, sink) = spawn_status_sink(200);
        let config_path = dir.path().join("report.config.toml");
        std::fs::write(
            &config_path,
            format!(
                r#"version = 1
default_profile = "local"

[profiles.local]
host = "{}"
public_key = "pk"
secret_key = "sk"
"#,
                host
            ),
        )
        .unwrap();
        let options = WorkerOptions {
            spool_dir: spool_dir.clone(),
            profile: None,
            config_path: Some(config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_secs(2),
            max_attempts: 3,
            debug: true,
        };
        let mut deferred_jobs = HashSet::new();

        assert!(process_once_session(&options, "session", &mut deferred_jobs).unwrap());
        let bodies = sink.join().unwrap();
        assert_eq!(bodies.len(), 1);
        let spans = bodies[0]
            .pointer("/resourceSpans/0/scopeSpans/0/spans")
            .and_then(Value::as_array)
            .unwrap();
        assert!(spans.iter().any(|span| {
            span.get("name")
                .and_then(Value::as_str)
                .is_some_and(|name| name.contains("Turn"))
        }));
        let state = load_state(&state_file);
        assert_eq!(state["session"]["posted_turn_ids"], json!(["turn-clean"]));
        assert_eq!(state["session"]["turn_count"], json!(1));
        assert_eq!(
            state["session"]["quarantined_turns"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            state["session"]["quarantined_turns"][0]["turn_id"],
            json!("turn-bad")
        );
        assert!(crate::hooks::langfuse::state::is_turn_terminally_accounted(
            &state["session"],
            "turn-bad"
        ));

        let no_server_config_path = dir.path().join("report-no-server.config.toml");
        std::fs::write(
            &no_server_config_path,
            r#"version = 1
default_profile = "local"

[profiles.local]
host = "http://127.0.0.1:9"
public_key = "pk"
secret_key = "sk"
"#,
        )
        .unwrap();
        let duplicate_options = WorkerOptions {
            spool_dir,
            profile: None,
            config_path: Some(no_server_config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_millis(100),
            max_attempts: 3,
            debug: true,
        };
        assert!(process_once_session(&duplicate_options, "session", &mut deferred_jobs).unwrap());
        assert!(!process_once_session(&duplicate_options, "session", &mut deferred_jobs).unwrap());
        let duplicate_state = load_state(&state_file);
        assert_eq!(
            duplicate_state["session"]["quarantined_turns"]
                .as_array()
                .unwrap()
                .len(),
            1,
            "duplicate finalizer must skip the already terminal turn"
        );
        assert_eq!(
            duplicate_state["session"]["posted_turn_ids"],
            json!(["turn-clean"])
        );
        assert_eq!(duplicate_state["session"]["turn_count"], json!(1));
        let status = spool::status(&session_spool).unwrap();
        assert_eq!(status.pending, 0);
        assert_eq!(status.failed, 0);
        assert_eq!(status.done, 2);
    }

    #[test]
    fn finalizer_cursor_uses_parsed_offset_when_transcript_grows_during_post() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir.clone());
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"timestamp":"2026-08-16T00:00:00Z","type":"session_meta","payload":{"id":"session","thread_source":"user"}}
{"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-08-16T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}
{"timestamp":"2026-08-16T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"done"}}
{"timestamp":"2026-08-16T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}
"#,
        )
        .unwrap();
        let parsed_prefix_len = std::fs::metadata(&transcript_path).unwrap().len() as i64;
        let state_file = dirs::session_state_file("session").unwrap();
        let mut initial_state = json!({});
        commit_export_success(
            &state_file,
            &mut initial_state,
            "session",
            0,
            "",
            &[],
            &[],
            0,
            "2026-08-16T00:00:00Z",
            Some(transcript_path.to_string_lossy().as_ref()),
            &[],
            &[],
        )
        .unwrap();
        let mut finalizer_context_id = String::new();
        update_realtime_state(&state_file, |state| {
            let context = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-1",
                "now",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-2",
                "now",
                &context.trace_id,
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::bind_turn_id_in_state(
                state,
                "session",
                &context.context_id,
                Some("turn-1"),
            )?;
            finalizer_context_id = context.context_id.clone();
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &context.context_id,
                "tool",
                "job-tool-1",
                "digest-tool-1",
            )?;
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &context.context_id,
                "tool",
                "job-tool-2",
                "digest-tool-2",
            )?;
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-1"]["job_id"] =
                json!("job-tool-1");
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-1"]
                ["payload_digest"] = json!("digest-tool-1");
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-2"]["job_id"] =
                json!("job-tool-2");
            state["session"]["realtime_export"]["current"]["tool_jobs"]["tool-2"]
                ["payload_digest"] = json!("digest-tool-2");
            realtime_state::begin_realtime_delivery_in_state(
                state,
                "session",
                "job-tool-1",
                "digest-tool-1",
                RetryPolicy::AtLeastOnce,
                "claim-tool-1",
                1,
            )?;
            realtime_state::complete_realtime_delivery_in_state(
                state,
                "session",
                "job-tool-1",
                "digest-tool-1",
            )?;
            Ok(())
        })
        .unwrap();
        let session_spool = spool::session_root(&spool_dir, "session").unwrap();
        let published = spool::publish_realtime_job(
            &session_spool,
            spool::RealtimeSpoolKind::Finalizer,
            "session",
            &finalizer_context_id,
            "finalizer:blocking-cursor",
            json!({
                "transcript_path": transcript_path,
                "high_water": 2,
                "retry_policy": "at_least_once",
                "username": "tester",
                "hook_version": "test-version",
                "cwd": "/repo"
            }),
        )
        .unwrap();
        update_realtime_state(&state_file, |state| {
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &finalizer_context_id,
                "finalizer",
                &published.job_id,
                &published.payload_digest,
            )
            .map(|_| ())
        })
        .unwrap();

        let (host, accepted_rx, release_tx, sink_handle) = spawn_blocking_status_sink(200);
        let config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host,
            headers: None,
        };
        let logger = Logger::new(dir.path().join("blocking-finalizer.log"), true);
        let job = spool::SpoolJob {
            schema_version: 1,
            session_schema_version: spool::LEGACY_REALTIME_SESSION_SCHEMA_VERSION,
            job_kind: spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Finalizer),
            job_id: published.job_id.clone(),
            session_id: "session".to_string(),
            turn_id: None,
            created_at: "now".to_string(),
            claimed_at: Some("now".to_string()),
            attempts: 1,
            last_error: None,
            otel: json!({"high_water": 2}),
            transcript_path: Some(transcript_path.to_string_lossy().to_string()),
            cwd: Some("/repo".to_string()),
            username: Some("tester".to_string()),
            hook_version: Some("test-version".to_string()),
            subject_id: Some("finalizer:blocking-cursor".to_string()),
            context_id: Some(finalizer_context_id),
            payload_digest: Some(published.payload_digest.clone()),
            batch_manifest: None,
            flush_manifest: None,
        };

        let appender_transcript_path = transcript_path.clone();
        let appender = std::thread::spawn(move || {
            accepted_rx.recv_timeout(Duration::from_secs(5)).unwrap();
            let mut transcript = std::fs::OpenOptions::new()
                .append(true)
                .open(&appender_transcript_path)
                .unwrap();
            transcript
                .write_all(
                    br#"{"timestamp":"2026-08-16T00:00:02Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-2"}}
{"timestamp":"2026-08-16T00:00:02.100Z","type":"event_msg","payload":{"type":"user_message","message":"next"}}
{"timestamp":"2026-08-16T00:00:02.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"next done"}}
{"timestamp":"2026-08-16T00:00:02.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-2","last_agent_message":"next done"}}
"#,
                )
                .unwrap();
            let later_len = std::fs::metadata(&appender_transcript_path).unwrap().len() as i64;
            assert!(later_len > parsed_prefix_len);
            release_tx.send(()).unwrap();
            later_len
        });

        process_transcript(
            &job,
            &config,
            &logger,
            Duration::from_secs(5),
            Some(FinalizerReconcile {
                high_water: 2,
                retry_policy: RetryPolicy::AtLeastOnce,
                lease_duration: Duration::from_secs(60),
            }),
            None,
            None,
        )
        .unwrap();
        let later_len = appender.join().unwrap();
        let body = sink_handle.join().unwrap();
        let spans = body
            .pointer("/resourceSpans/0/scopeSpans/0/spans")
            .and_then(Value::as_array)
            .unwrap();
        assert!(!spans.is_empty());

        let state = load_state(&state_file);
        assert_eq!(
            get_int_field(&state["session"], "offset"),
            parsed_prefix_len,
            "finalizer must not advance session cursor past parsed prefix"
        );
        assert_ne!(
            get_int_field(&state["session"], "offset"),
            later_len,
            "finalizer cursor overran to later file length"
        );
        assert_eq!(state["session"]["posted_turn_ids"], json!(["turn-1"]));
        assert_eq!(state["session"]["turn_count"], json!(1));
        assert_eq!(
            state["session"]["realtime_export"]["generation"]["scan_anchor"],
            json!(parsed_prefix_len as u64)
        );
        assert_eq!(
            state["session"]["realtime_export"]["generation"]["committed_offset"],
            json!(parsed_prefix_len as u64)
        );
    }

    fn seed_quarantine_retention_boundary(
        transcript_path: &Path,
        state_file: &Path,
    ) -> (usize, Vec<String>) {
        use std::fmt::Write as _;

        let successful_turn_count = crate::hooks::langfuse::state::POSTED_TURN_ID_RETENTION + 1;
        let mut transcript = String::from(
            r#"{"timestamp":"2026-08-16T00:00:00Z","type":"session_meta","payload":{"id":"session","thread_source":"user"}}
{"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-bad"}}
{"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"user_message","message":"bad"}}
{"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"bad done"}}
{"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-bad","last_agent_message":"bad done"}}
"#,
        );
        let successful_turn_ids = (0..successful_turn_count)
            .map(|index| format!("turn-clean-{index}"))
            .collect::<Vec<_>>();
        for turn_id in &successful_turn_ids {
            writeln!(
                transcript,
                r#"{{"timestamp":"2026-08-16T00:00:02Z","type":"event_msg","payload":{{"type":"task_started","turn_id":"{turn_id}"}}}}"#
            )
            .unwrap();
            writeln!(
                transcript,
                r#"{{"timestamp":"2026-08-16T00:00:02Z","type":"event_msg","payload":{{"type":"user_message","message":"clean"}}}}"#
            )
            .unwrap();
            writeln!(
                transcript,
                r#"{{"timestamp":"2026-08-16T00:00:02Z","type":"event_msg","payload":{{"type":"agent_message","phase":"final_answer","message":"clean done"}}}}"#
            )
            .unwrap();
            writeln!(
                transcript,
                r#"{{"timestamp":"2026-08-16T00:00:02Z","type":"event_msg","payload":{{"type":"task_complete","turn_id":"{turn_id}","last_agent_message":"clean done"}}}}"#
            )
            .unwrap();
        }
        std::fs::write(transcript_path, transcript).unwrap();

        let mut state = json!({});
        record_terminal_quarantined_turn(
            state_file,
            &mut state,
            "session",
            "turn-bad",
            MULTIPLE_PUBLISHED_ROOTS_QUARANTINE_REASON,
            &["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/1111111111111111".to_string()],
            "2026-08-16T00:00:03Z",
            1,
            Some("retention-boundary-recovery-key"),
            Some(std::fs::metadata(transcript_path).unwrap().len() as i64),
        )
        .unwrap();
        commit_export_success(
            state_file,
            &mut state,
            "session",
            std::fs::metadata(transcript_path).unwrap().len() as i64,
            "",
            &[],
            &successful_turn_ids,
            successful_turn_count,
            "2026-08-16T00:00:04Z",
            Some(transcript_path.to_string_lossy().as_ref()),
            &[],
            &[],
        )
        .unwrap();
        assert_eq!(
            state["session"]["posted_turn_ids"]
                .as_array()
                .unwrap()
                .len(),
            crate::hooks::langfuse::state::POSTED_TURN_ID_RETENTION
        );
        assert!(
            !get_string_array_field(&state["session"], "posted_turn_ids")
                .contains(&successful_turn_ids[0])
        );
        (successful_turn_count, successful_turn_ids)
    }

    #[test]
    fn duplicate_finalizer_skips_successes_pruned_from_posted_turn_ids_after_quarantine() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let transcript_path = dir.path().join("session.jsonl");
        let state_file = dirs::session_state_file("session").unwrap();
        let (successful_turn_count, _) =
            seed_quarantine_retention_boundary(&transcript_path, &state_file);

        let job_id = "duplicate-retention-finalizer";
        let payload_digest = "duplicate-retention-digest";
        update_realtime_state(&state_file, |state| {
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                "retention-context",
                "finalizer",
                job_id,
                payload_digest,
            )
            .map(|_| ())
        })
        .unwrap();
        let job = spool::SpoolJob {
            schema_version: 1,
            session_schema_version: spool::LEGACY_REALTIME_SESSION_SCHEMA_VERSION,
            job_kind: spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Finalizer),
            job_id: job_id.to_string(),
            session_id: "session".to_string(),
            turn_id: None,
            created_at: "now".to_string(),
            claimed_at: Some("now".to_string()),
            attempts: 1,
            last_error: None,
            otel: json!({"high_water": 0}),
            transcript_path: Some(transcript_path.to_string_lossy().to_string()),
            cwd: None,
            username: Some("tester".to_string()),
            hook_version: Some("test-version".to_string()),
            subject_id: Some("duplicate-retention".to_string()),
            context_id: Some("retention-context".to_string()),
            payload_digest: Some(payload_digest.to_string()),
            batch_manifest: None,
            flush_manifest: None,
        };
        let config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host: "http://127.0.0.1:9".to_string(),
            headers: None,
        };
        let logger = Logger::new(dir.path().join("retention-finalizer.log"), true);

        process_transcript(
            &job,
            &config,
            &logger,
            Duration::from_millis(100),
            Some(FinalizerReconcile {
                high_water: 0,
                retry_policy: RetryPolicy::AtLeastOnce,
                lease_duration: Duration::from_secs(60),
            }),
            None,
            None,
        )
        .unwrap();

        let final_state = load_state(&state_file);
        assert_eq!(
            get_int_field(&final_state["session"], "turn_count") as usize,
            successful_turn_count,
            "duplicate finalizer must not advance the successful export count"
        );
        assert_eq!(
            final_state["session"]["quarantined_turns"]
                .as_array()
                .unwrap()
                .len(),
            1,
            "duplicate finalizer must not append another quarantine entry"
        );
        assert_eq!(
            final_state["session"]["posted_turn_ids"]
                .as_array()
                .unwrap()
                .len(),
            crate::hooks::langfuse::state::POSTED_TURN_ID_RETENTION
        );
    }

    #[test]
    fn duplicate_finalizer_defers_partial_tail_after_accounted_prefix() {
        let _guard = crate::test_sync::env_lock();
        std::env::set_var("CDXHOOKS_TEST_TRANSCRIPT_TERMINAL_TIMEOUT_MS", "0");
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let transcript_path = dir.path().join("session.jsonl");
        let state_file = dirs::session_state_file("session").unwrap();
        let (successful_turn_count, _) =
            seed_quarantine_retention_boundary(&transcript_path, &state_file);
        let completed_prefix_len = std::fs::metadata(&transcript_path).unwrap().len() as i64;
        let job_id = "duplicate-retention-partial-finalizer";
        let payload_digest = "duplicate-retention-partial-digest";
        update_realtime_state(&state_file, |state| {
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                "retention-context",
                "finalizer",
                job_id,
                payload_digest,
            )
            .map(|_| ())
        })
        .unwrap();
        let state_before = load_state(&state_file);
        let mut transcript = std::fs::read_to_string(&transcript_path).unwrap();
        transcript.push_str(
            r#"{"timestamp":"2026-08-16T00:00:05Z","type":"event_msg","payload":{"type":"task_started""#,
        );
        std::fs::write(&transcript_path, transcript).unwrap();
        let full_len = std::fs::metadata(&transcript_path).unwrap().len() as i64;
        assert!(full_len > completed_prefix_len);

        let job = spool::SpoolJob {
            schema_version: 1,
            session_schema_version: spool::LEGACY_REALTIME_SESSION_SCHEMA_VERSION,
            job_kind: spool::SpoolJobKind::Realtime(spool::RealtimeSpoolKind::Finalizer),
            job_id: job_id.to_string(),
            session_id: "session".to_string(),
            turn_id: None,
            created_at: "now".to_string(),
            claimed_at: Some("now".to_string()),
            attempts: 1,
            last_error: None,
            otel: json!({"high_water": 0}),
            transcript_path: Some(transcript_path.to_string_lossy().to_string()),
            cwd: None,
            username: Some("tester".to_string()),
            hook_version: Some("test-version".to_string()),
            subject_id: Some("duplicate-retention-partial".to_string()),
            context_id: Some("retention-context".to_string()),
            payload_digest: Some(payload_digest.to_string()),
            batch_manifest: None,
            flush_manifest: None,
        };
        let config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host: "http://127.0.0.1:9".to_string(),
            headers: None,
        };
        let logger = Logger::new(dir.path().join("retention-partial-finalizer.log"), true);

        let err = process_transcript(
            &job,
            &config,
            &logger,
            Duration::from_millis(100),
            Some(FinalizerReconcile {
                high_water: 0,
                retry_policy: RetryPolicy::AtLeastOnce,
                lease_duration: Duration::from_secs(60),
            }),
            None,
            None,
        )
        .unwrap_err();
        std::env::remove_var("CDXHOOKS_TEST_TRANSCRIPT_TERMINAL_TIMEOUT_MS");
        assert!(
            err.contains("incomplete JSONL tail"),
            "the finalizer should defer an incomplete JSONL tail"
        );

        let final_state = load_state(&state_file);
        assert_eq!(
            final_state, state_before,
            "partial tail must defer before any state/export mutation"
        );
        assert_eq!(
            get_int_field(&final_state["session"], "turn_count") as usize,
            successful_turn_count,
            "partial tail must not advance successful export accounting"
        );
        assert_eq!(
            get_int_field(&final_state["session"], "offset"),
            completed_prefix_len,
            "partial tail must not advance cursor to full file length"
        );
        assert_ne!(
            get_int_field(&final_state["session"], "offset"),
            full_len,
            "partial tail advanced the transcript cursor"
        );
        assert_eq!(
            final_state["session"]["quarantined_turns"]
                .as_array()
                .unwrap()
                .len(),
            1,
            "partial tail must not append another quarantine entry"
        );
    }

    #[test]
    fn stop_intent_recovery_uses_durable_boundary_without_transcript_reparse() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(&transcript_path, b"not-json-but-already-accounted\n").unwrap();
        let file_len = std::fs::metadata(&transcript_path).unwrap().len() as i64;
        let payload = json!({
            "hook_event_name": "Stop",
            "session_id": "session",
            "transcript_path": transcript_path,
            "status": "pending"
        });
        let mut state = json!({
            "session": {
                "offset": file_len,
                "buffer": "",
                "pending_messages": [],
                "transcript_path": transcript_path
            }
        });

        assert!(
            !stop_intent_has_unaccounted_turn(&payload, &state, "session").unwrap(),
            "durably consumed bytes must not be reparsed to decide Stop liveness"
        );

        std::fs::OpenOptions::new()
            .append(true)
            .open(&transcript_path)
            .unwrap()
            .write_all(b"new-byte")
            .unwrap();
        assert!(
            stop_intent_has_unaccounted_turn(&payload, &state, "session").unwrap(),
            "bytes beyond the durable cursor must keep the Stop intent live"
        );

        state["session"]["offset"] =
            json!(std::fs::metadata(&transcript_path).unwrap().len() as i64);
        state["session"]["buffer"] = json!("partial-record");
        assert!(
            stop_intent_has_unaccounted_turn(&payload, &state, "session").unwrap(),
            "a buffered partial record must remain retryable at end-of-file"
        );
    }

    #[test]
    fn stale_stop_intent_uses_success_ordinals_beyond_posted_id_retention() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let transcript_path = dir.path().join("session.jsonl");
        let state_file = dirs::session_state_file("session").unwrap();
        let (successful_turn_count, _) =
            seed_quarantine_retention_boundary(&transcript_path, &state_file);
        let stop_path = dirs::session_stop_intent_file("session").unwrap();
        dirs::ensure_private_dir(stop_path.parent().unwrap()).unwrap();
        std::fs::write(
            &stop_path,
            json!({
                "schema_version": 1,
                "hook_event_name": "Stop",
                "session_id": "session",
                "transcript_path": transcript_path,
                "status": "pending"
            })
            .to_string(),
        )
        .unwrap();

        let status = state_worker_status(Some("session")).unwrap();
        assert_eq!(status.pending_stops, 0);
        assert_eq!(status.terminal_quarantines, 1);
        assert!(recovery_stop_intent_payload("session").unwrap().is_none());
        assert!(
            !stop_path.exists(),
            "fully accounted Stop intent remained live"
        );
        assert_eq!(
            get_int_field(&load_state(&state_file)["session"], "turn_count") as usize,
            successful_turn_count
        );
    }

    #[test]
    fn state_worker_status_separates_live_stop_from_terminal_quarantine() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir.clone(), spool_dir);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-bad"}}
{"timestamp":"2026-08-16T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"bad"}}
{"timestamp":"2026-08-16T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"bad done"}}
{"timestamp":"2026-08-16T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-bad","last_agent_message":"bad done"}}
"#,
        )
        .unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let stop_path = dirs::session_stop_intent_file("session").unwrap();
        dirs::ensure_private_dir(stop_path.parent().unwrap()).unwrap();
        std::fs::write(
            &stop_path,
            json!({
                "schema_version": 1,
                "hook_event_name": "Stop",
                "session_id": "session",
                "transcript_path": transcript_path,
                "status": "pending"
            })
            .to_string(),
        )
        .unwrap();
        let mut state = json!({});
        record_terminal_quarantined_turn(
            &state_file,
            &mut state,
            "session",
            "turn-bad",
            MULTIPLE_PUBLISHED_ROOTS_QUARANTINE_REASON,
            &["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/1111111111111111".to_string()],
            "2026-08-16T00:00:02Z",
            1,
            Some("recovery-key"),
            Some(std::fs::metadata(&transcript_path).unwrap().len() as i64),
        )
        .unwrap();

        let status = state_worker_status(Some("session")).unwrap();
        assert_eq!(status.sessions, 1);
        assert_eq!(status.pending_stops, 0);
        assert_eq!(status.terminal_quarantines, 1);
        assert!(recovery_payload("session").unwrap().is_none());
        assert!(!stop_path.exists(), "accounted Stop intent was not cleared");
    }

    #[test]
    fn legacy_ambiguous_stop_is_armed_once_then_waits_after_retryable_result() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(&transcript_path, b"new-unaccounted-data\n").unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let stop_path = dirs::session_stop_intent_file("session").unwrap();
        dirs::ensure_private_dir(stop_path.parent().unwrap()).unwrap();
        std::fs::write(
            &stop_path,
            json!({
                "schema_version": 1,
                "hook_event_name": "Stop",
                "session_id": "session",
                "transcript_path": transcript_path,
                "status": "pending"
            })
            .to_string(),
        )
        .unwrap();
        let state = json!({
            "session": {
                "offset": 0,
                "buffer": "",
                "pending_messages": [],
                "pending_export_reason": REALTIME_TOPOLOGY_AMBIGUOUS_REASON,
                "transcript_path": transcript_path
            }
        });
        crate::hooks::langfuse::state::save_state(&state_file, &state).unwrap();

        let status = state_worker_status(Some("session")).unwrap();
        assert_eq!(status.pending_stops, 1);
        let first_payload = recovery_stop_intent_payload("session")
            .unwrap()
            .expect("first post-upgrade scan must arm the legacy Stop recovery");
        assert_eq!(first_payload["hook_event_name"], json!("Stop"));
        let armed_state = load_state(&state_file);
        let recovery = &armed_state["session"]["legacy_finalizer_recovery"];
        assert_eq!(recovery["state_version"], json!(1));
        assert_eq!(recovery["attempt"], json!(1));
        assert_eq!(recovery["status"], json!("armed"));
        let recovery_key = recovery["recovery_key"].as_str().unwrap().to_string();

        assert!(
            recovery_stop_intent_payload("session").unwrap().is_some(),
            "a crash before outcome must resume the same armed logical attempt"
        );
        let resumed_state = load_state(&state_file);
        assert_eq!(
            resumed_state["session"]["legacy_finalizer_recovery"]["attempt"],
            json!(1)
        );

        let mut retryable_state = resumed_state;
        crate::hooks::langfuse::state::record_pending_export_awaiting_explicit_terminal(
            &state_file,
            &mut retryable_state,
            "session",
            "2026-08-22T00:00:01Z",
            transcript_path.to_str().unwrap(),
            &recovery_key,
            1,
        )
        .unwrap();
        assert!(
            recovery_stop_intent_payload("session").unwrap().is_none(),
            "retryable recovery must restore ordinary-hook suppression"
        );
        assert!(
            stop_path.exists(),
            "suppression must preserve the intent for an explicit Stop/SessionEnd"
        );
    }

    #[test]
    fn finite_finalizer_deferred_waits_for_explicit_terminal_recovery() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(&transcript_path, b"new-unaccounted-data\n").unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let stop_path = dirs::session_stop_intent_file("session").unwrap();
        dirs::ensure_private_dir(stop_path.parent().unwrap()).unwrap();
        std::fs::write(
            &stop_path,
            json!({
                "schema_version": 1,
                "hook_event_name": "Stop",
                "session_id": "session",
                "transcript_path": transcript_path,
                "status": "pending"
            })
            .to_string(),
        )
        .unwrap();
        let mut state = json!({});
        record_finite_finalizer_deferred(
            &state_file,
            &mut state,
            "session",
            "realtime_evidence_without_unique_match",
            "2026-08-23T00:00:00Z",
            transcript_path.to_str().unwrap(),
            "recovery-key",
            1,
            7,
            42,
        )
        .unwrap();

        assert!(
            recovery_stop_intent_payload("session").unwrap().is_none(),
            "ordinary recovery must not cross an explicit-terminal retry boundary"
        );
        assert!(
            recovery_payload("session").unwrap().is_none(),
            "the state worker must not synthesize a Stop for finite Deferred state"
        );
        assert!(
            stop_path.exists(),
            "suppression must preserve the intent for the next explicit Stop/SessionEnd"
        );
    }

    #[test]
    fn waiting_terminal_stop_recovery_is_not_scheduled_or_consumed() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(&transcript_path, b"open-turn\n").unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let stop_path = dirs::session_stop_intent_file("session").unwrap();
        dirs::ensure_private_dir(stop_path.parent().unwrap()).unwrap();
        let payload = json!({
            "schema_version": 2,
            "intent_id": "intent-waiting",
            "hook_event_name": "Stop",
            "session_id": "session",
            "target_turn_id": "turn-open",
            "transcript_path": transcript_path,
            "status": "pending"
        });
        std::fs::write(&stop_path, payload.to_string()).unwrap();
        let mut state = json!({});
        crate::hooks::langfuse::state::record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "session",
            &payload,
            "target turn awaiting terminal: turn-open",
            "2026-08-28T00:00:00Z",
        )
        .unwrap();

        assert!(matches!(
            scheduled_stop_recovery_payload("session").unwrap(),
            ScheduledStopRecovery::Idle
        ));
        assert!(recovery_payload("session").unwrap().is_none());
        assert!(stop_path.exists());
    }

    #[test]
    fn scheduled_recovery_keeps_cross_intent_durable_delivery_plan_eligible() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(&transcript_path, b"pending\n").unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let stop_path = dirs::session_stop_intent_file("session").unwrap();
        dirs::ensure_private_dir(stop_path.parent().unwrap()).unwrap();
        let old_payload = json!({
            "schema_version": 2,
            "intent_id": "intent-old",
            "hook_event_name": "Stop",
            "session_id": "session",
            "transcript_path": transcript_path,
            "status": "pending"
        });
        let new_payload = json!({
            "schema_version": 2,
            "intent_id": "intent-new",
            "hook_event_name": "Stop",
            "session_id": "session",
            "transcript_path": transcript_path,
            "status": "pending"
        });
        std::fs::write(&stop_path, new_payload.to_string()).unwrap();
        let mut state = json!({});
        crate::hooks::langfuse::state::record_stop_recovery_deferred(
            &state_file,
            &mut state,
            "session",
            &old_payload,
            "turn export incomplete",
            "2026-08-28T00:00:00Z",
        )
        .unwrap();
        state["session"]["stop_recovery"]["next_eligible_at_ms"] = json!(0);
        crate::hooks::langfuse::state::save_state(&state_file, &state).unwrap();
        assert!(matches!(
            scheduled_stop_recovery_payload("session").unwrap(),
            ScheduledStopRecovery::Idle
        ));
        crate::hooks::langfuse::state::checkpoint_stop_recovery_delivery(
            &state_file,
            &mut state,
            "session",
            "intent-old",
            8,
            &crate::hooks::langfuse::state::StopRecoveryDeliveryCheckpoint {
                plan_id: "plan-1".to_string(),
                chunk_cursor: 1,
                payload_digest: "digest-1".to_string(),
                turn_id: "turn-1".to_string(),
                root_span_id: "aaaaaaaaaaaaaaaa".to_string(),
                window_end_offset: 8,
                finalizer_high_water: 1,
                generation_count: 0,
                merged_sync_ids: Vec::new(),
                merged_subagent_invocation_ids: Vec::new(),
            },
            "2026-08-28T00:00:01Z",
        )
        .unwrap();

        let scheduled = scheduled_stop_recovery_payload("session").unwrap();
        let ScheduledStopRecovery::Eligible(payload) = scheduled else {
            panic!("cross-intent durable delivery plan must remain schedulable");
        };
        assert_eq!(payload["intent_id"], json!("intent-new"));
        assert_eq!(
            stop_recovery_record(&load_state(&state_file), "session")
                .unwrap()
                .intent_id,
            "intent-old",
            "the scheduler must leave atomic ownership transfer to the Stop handler"
        );
    }

    #[test]
    fn partial_transcript_tail_keeps_accounted_stop_intent_live() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir, spool_dir);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-bad"}}
{"timestamp":"2026-08-16T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"bad"}}
{"timestamp":"2026-08-16T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"bad done"}}
{"timestamp":"2026-08-16T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-bad","last_agent_message":"bad done"}}
{"timestamp":"2026-08-16T00:00:02Z","type":"event_msg","payload":{"type":"task_started""#,
        )
        .unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let stop_path = dirs::session_stop_intent_file("session").unwrap();
        dirs::ensure_private_dir(stop_path.parent().unwrap()).unwrap();
        std::fs::write(
            &stop_path,
            json!({
                "schema_version": 1,
                "hook_event_name": "Stop",
                "session_id": "session",
                "transcript_path": transcript_path,
                "status": "pending"
            })
            .to_string(),
        )
        .unwrap();
        let mut state = json!({});
        record_terminal_quarantined_turn(
            &state_file,
            &mut state,
            "session",
            "turn-bad",
            MULTIPLE_PUBLISHED_ROOTS_QUARANTINE_REASON,
            &["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/1111111111111111".to_string()],
            "2026-08-16T00:00:02Z",
            1,
            Some("recovery-key"),
            None,
        )
        .unwrap();

        let status = state_worker_status(Some("session")).unwrap();
        assert_eq!(status.pending_stops, 1);
        assert_eq!(status.terminal_quarantines, 1);
        assert!(recovery_payload("session").unwrap().is_some());
        assert!(
            stop_path.exists(),
            "partial transcript tail lost Stop intent"
        );
    }

    #[test]
    fn worker_finalizer_recovers_split_turn_with_single_published_owner() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime = dirs::override_runtime_dirs_for_test(state_dir.clone(), spool_dir.clone());
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript_path,
            r#"{"timestamp":"2026-08-16T00:00:00Z","type":"session_meta","payload":{"id":"session","thread_source":"user"}}
{"timestamp":"2026-08-16T00:00:01Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-1"}}
{"timestamp":"2026-08-16T00:00:01.100Z","type":"event_msg","payload":{"type":"user_message","message":"hello"}}
{"timestamp":"2026-08-16T00:00:01.200Z","type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"done"}}
{"timestamp":"2026-08-16T00:00:01.300Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1","last_agent_message":"done"}}
"#,
        )
        .unwrap();
        let state_file = dirs::session_state_file("session").unwrap();
        let session_spool = spool::session_root(&spool_dir, "session").unwrap();
        let published_trace_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        let stale_history_trace_id = "cccccccccccccccccccccccccccccccc";
        let mut published_root_span_id = String::new();
        let mut loser_context_id = String::new();
        let mut finalizer_job_id = String::new();
        update_realtime_state(&state_file, |state| {
            let owner = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-owner",
                "first",
                published_trace_id,
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            published_root_span_id = owner.root_span_id.clone();
            realtime_state::bind_turn_id_in_state(
                state,
                "session",
                &owner.context_id,
                Some("turn-1"),
            )?;
            let owner_batch = realtime_state::prepare_realtime_batch_in_state(
                state,
                "session",
                &owner.context_id,
                "tool",
                &["published-child".to_string()],
                "published-digest",
            )?;
            realtime_state::publish_realtime_batch_in_state(
                state,
                "session",
                &owner_batch.batch_id,
            )?;
            realtime_state::succeed_realtime_batch_in_state(
                state,
                "session",
                &owner_batch.batch_id,
            )?;

            realtime_state::rotate_current_in_state(state, "session");
            let loser = realtime_state::claim_tool_in_state(
                state,
                "session",
                "tool-loser",
                "second",
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                None,
                None,
                None,
                None,
                1,
                RetryPolicy::AtLeastOnce,
            );
            realtime_state::bind_turn_id_in_state(
                state,
                "session",
                &loser.context_id,
                Some("turn-1"),
            )?;
            loser_context_id = loser.context_id.clone();
            let published = spool::publish_realtime_job(
                &session_spool,
                spool::RealtimeSpoolKind::Finalizer,
                "session",
                &loser.context_id,
                "finalizer:recover-split",
                json!({
                    "transcript_path": transcript_path,
                    "high_water": 0,
                    "retry_policy": "at_least_once",
                    "username": "tester",
                    "hook_version": "test-version",
                    "cwd": "/repo"
                }),
            )?;
            finalizer_job_id = published.job_id.clone();
            realtime_state::register_realtime_job_in_state(
                state,
                "session",
                &loser.context_id,
                "finalizer",
                &published.job_id,
                &published.payload_digest,
            )?;
            state["session"]["trace_context_history"] = json!([{
                "trace_id": stale_history_trace_id,
                "traceparent": format!(
                    "00-{stale_history_trace_id}-1111111111111111-01"
                )
            }]);
            Ok(())
        })
        .unwrap();

        let (host, sink) = spawn_status_sink(200);
        let config_path = dir.path().join("report.config.toml");
        std::fs::write(
            &config_path,
            format!(
                r#"version = 1
default_profile = "local"

[profiles.local]
host = "{}"
public_key = "pk"
secret_key = "sk"
"#,
                host
            ),
        )
        .unwrap();
        let options = WorkerOptions {
            spool_dir,
            profile: None,
            config_path: Some(config_path.to_string_lossy().to_string()),
            stale_after: Duration::from_secs(60),
            http_timeout: Duration::from_secs(2),
            max_attempts: 3,
            debug: true,
        };
        let mut deferred_jobs = HashSet::new();

        assert!(process_once_session(&options, "session", &mut deferred_jobs).unwrap());
        assert!(!deferred_jobs.contains(&finalizer_job_id));
        let bodies = sink.join().unwrap();
        assert_eq!(bodies.len(), 1);
        let spans = bodies[0]
            .pointer("/resourceSpans/0/scopeSpans/0/spans")
            .and_then(Value::as_array)
            .unwrap();
        assert!(!spans.is_empty());
        assert!(spans
            .iter()
            .all(|span| span["traceId"] == json!(published_trace_id)));
        assert!(spans
            .iter()
            .all(|span| span["traceId"] != json!(stale_history_trace_id)));
        assert!(spans
            .iter()
            .any(|span| span["spanId"] == json!(published_root_span_id)));
        let status = spool::status(&session_spool).unwrap();
        assert_eq!(status.pending, 0);
        assert_eq!(status.failed, 0);
        assert_eq!(status.done, 1);
        let state = load_state(&state_file);
        assert_ne!(
            state["session"]["pending_export_reason"],
            json!("realtime_topology_ambiguous")
        );
        assert!(
            !realtime_state::context_alias_exists(&state, "session", &loser_context_id)
                || realtime_state::resolve_context_alias(&state, "session", &loser_context_id)
                    .is_ok(),
            "cleanup may prune the completed alias, but must not leave a dangling live alias"
        );
    }

    #[test]
    fn native_turn_identity_preserves_stop_topology_when_hook_and_call_ids_differ() {
        let mut state = json!({});
        let context = realtime_state::claim_tool_in_state(
            &mut state,
            "session",
            "exec-1",
            "now",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            "session",
            &context.context_id,
            Some("turn-1"),
        )
        .unwrap();
        let turns = vec![test_turn(
            "turn-1",
            vec![test_assistant("tool")],
            vec![ToolCall {
                call_id: "call_1".to_string(),
                name: "Bash".to_string(),
                input: json!({}),
                output: json!("ok"),
                started_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
                ended_timestamp: "2026-03-28T08:00:03.000Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            }],
        )];

        assert_eq!(
            finalizer_trace_root_for_turn_with_scan_base(&state, "session", &turns, 0, 0)
                .unwrap()
                .unwrap(),
            (context.trace_id, context.root_span_id)
        );
    }

    #[test]
    fn native_turn_generation_boundary_uses_greatest_completed_tool_assistant() {
        let trigger = RealtimeGenerationTrigger::v2(
            "session",
            "context",
            "turn-1",
            "exec-1",
            "/tmp/session.jsonl",
            0,
            3,
        )
        .unwrap();
        let turn = test_turn(
            "turn-1",
            vec![
                test_assistant("one"),
                test_assistant("two"),
                test_assistant("three"),
            ],
            vec![
                ToolCall {
                    call_id: "call_1".to_string(),
                    name: "Bash".to_string(),
                    input: json!({}),
                    output: json!("ok"),
                    started_timestamp: "1".to_string(),
                    ended_timestamp: "2".to_string(),
                    assistant_index: 0,
                    success: Some(true),
                },
                ToolCall {
                    call_id: "call_2".to_string(),
                    name: "Bash".to_string(),
                    input: json!({}),
                    output: Value::Null,
                    started_timestamp: "3".to_string(),
                    ended_timestamp: String::new(),
                    assistant_index: 1,
                    success: None,
                },
                ToolCall {
                    call_id: "call_3".to_string(),
                    name: "Bash".to_string(),
                    input: json!({}),
                    output: json!("ok"),
                    started_timestamp: "4".to_string(),
                    ended_timestamp: "5".to_string(),
                    assistant_index: 2,
                    success: Some(true),
                },
            ],
        );
        assert_eq!(
            resolve_realtime_generation_boundary(&trigger, &[turn]),
            Ok((0, 2))
        );
    }

    fn test_assistant(text: &str) -> AssistantMessage {
        AssistantMessage {
            timestamp: "2026-03-28T08:00:01.000Z".to_string(),
            text: text.to_string(),
            media_content: serde_json::Value::Null,
            phase: "final_answer".to_string(),
            usage: TokenUsage {
                input: Some(1),
                output: Some(1),
                total: Some(2),
                ..TokenUsage::default()
            },
            token_count_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
            invocation_started_timestamp: String::new(),
            invocation_end_source: None,
        }
    }

    fn test_turn(id: &str, assistants: Vec<AssistantMessage>, tool_calls: Vec<ToolCall>) -> Turn {
        Turn {
            chat_id: Some(id.to_string()),
            turn_id: id.to_string(),
            user_timestamp: "2026-03-28T08:00:00.000Z".to_string(),
            user_text: "hello".to_string(),
            user_content: serde_json::Value::Null,
            assistants,
            tool_calls,
            model: "gpt-5".to_string(),
            cwd: "/repo".to_string(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: "2026-03-28T08:00:05.000Z".to_string(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events: vec![],
            subagent_source: None,
        }
    }
}
