#[cfg(feature = "dynamic")]
pub mod dynamic;
pub mod langfuse;
pub mod resource_context;
#[cfg(feature = "skill")]
pub mod skill_tag_handler;
pub mod subagent;
pub mod timing;
#[cfg(all(feature = "tool_context", not(windows)))]
pub mod tool_context;
pub mod tool_event;
#[cfg(feature = "turn_digest")]
pub mod turn_digest;
pub mod turn_seed;

use std::cell::RefCell;
use std::fmt;

use agentic_otel_common::metrics::MetricsCollector;

use crate::common::concurrent::SendTask;
use crate::common::hook::HookEvent;
use crate::common::logger::Logger;
use crate::hooks::langfuse::LangfuseConfig;

pub const DEFAULT_STOP_BUDGET_MS: u64 = 30_000;
pub const DEFAULT_STOP_MAX_HTTP_TIMEOUT_MS: u64 = 20_000;
pub const DEFAULT_STOP_CHECKPOINT_RESERVE_MS: u64 = 500;
pub const DEFAULT_STOP_TRANSCRIPT_SETTLE_MS: u64 = 10_000;
pub const STOP_TERMINAL_POLL_INTERVAL_MS: u64 = 500;
pub const STOP_RECOVERY_BUDGET_MS: u64 = 600;
pub const STOP_RECOVERY_MAX_HTTP_TIMEOUT_MS: u64 = 400;
pub const STOP_RECOVERY_CHECKPOINT_RESERVE_MS: u64 = 100;
pub const STOP_RECOVERY_TRANSCRIPT_SCAN_MAX_BYTES: usize = 256 * 1024;
pub const DEFAULT_SESSION_END_BUDGET_MS: u64 = 110_000;
pub const RETRY_POLICY_ENV: &str = "CCHOOKS_RETRY_POLICY";
pub const REALTIME_EXPORT_ENV: &str = "CCHOOKS_REALTIME_EXPORT";
pub const STRUCTURED_OUTPUT_ENV: &str = "CCHOOKS_STRUCTURED_OUTPUT";
pub const INTERNAL_FINALIZER_AUTHORIZED: &str = "cchooks_finalizer_authorized";
pub const REALTIME_LLM_EXPORT_BATCH_SIZE_ENV: &str = "CCHOOKS_REALTIME_LLM_EXPORT_BATCH_SIZE";
pub const DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE: usize = 3;
pub const DEFAULT_REALTIME_LLM_SCAN_MAX_BYTES: usize = 1024 * 1024;
pub const DEFAULT_REALTIME_LLM_SCAN_BUDGET_MS: u64 = 100;
pub const DEFAULT_REALTIME_LLM_CLAIM_LEASE_MS: u64 = 5_000;

pub fn acknowledge_transcript_ingest(
    session_id: &str,
    receipt: Option<&str>,
) -> Result<(), String> {
    let Some(receipt) = receipt.filter(|value| !value.is_empty()) else {
        return Ok(());
    };
    let request =
        crate::common::dirs::session_transcript_ingest_receipt_file(session_id, receipt, "request")
            .ok_or_else(|| "invalid transcript ingest request".to_string())?;
    let ack =
        crate::common::dirs::session_transcript_ingest_receipt_file(session_id, receipt, "ack")
            .ok_or_else(|| "invalid transcript ingest acknowledgement".to_string())?;
    // Atomically consume the parent's request as the acknowledgement. This
    // closes the timeout race: after the parent removes `request`, a late
    // worker can no longer recreate a stale `ack` file.
    match std::fs::rename(&request, &ack) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!(
            "publish transcript ingest acknowledgement: {error}"
        )),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BoolSetting {
    Unset,
    Valid(bool),
    Invalid(String),
}

pub fn parse_bool_setting(value: &str) -> Option<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "true" | "1" => Some(true),
        "false" | "0" => Some(false),
        _ => None,
    }
}

pub fn resolve_bool_setting(value: Option<String>) -> BoolSetting {
    let Some(value) = value else {
        return BoolSetting::Unset;
    };
    if value.trim().is_empty() {
        return BoolSetting::Unset;
    }
    match parse_bool_setting(&value) {
        Some(value) => BoolSetting::Valid(value),
        None => BoolSetting::Invalid(sanitize_setting_value(&value)),
    }
}

fn sanitize_setting_value(value: &str) -> String {
    const MAX_CHARS: usize = 64;
    let mut sanitized = value
        .chars()
        .take(MAX_CHARS)
        .map(|character| {
            if character.is_control() {
                '�'
            } else {
                character
            }
        })
        .collect::<String>();
    if value.chars().count() > MAX_CHARS {
        sanitized.push('…');
    }
    sanitized
}

pub fn active_stop_should_skip(payload: &serde_json::Value) -> bool {
    payload
        .get("stop_hook_active")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false)
        && !payload
            .get(INTERNAL_FINALIZER_AUTHORIZED)
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false)
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum ExportMode {
    StopOnly,
    #[default]
    RealtimeWithStopFinalizer,
}

impl ExportMode {
    pub fn parse(value: impl ToString) -> Option<Self> {
        match setting_value_text(value)
            .trim()
            .to_ascii_lowercase()
            .as_str()
        {
            "true" | "1" | "yes" | "on" => Some(Self::RealtimeWithStopFinalizer),
            "false" | "0" | "no" | "off" => Some(Self::StopOnly),
            _ => None,
        }
    }

    pub fn realtime_enabled(self) -> bool {
        matches!(self, Self::RealtimeWithStopFinalizer)
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::StopOnly => "stop_only",
            Self::RealtimeWithStopFinalizer => "realtime_with_stop_finalizer",
        }
    }
}

fn setting_value_text(value: impl ToString) -> String {
    let text = value.to_string();
    serde_json::from_str::<String>(&text).unwrap_or(text)
}

pub fn parse_positive_batch_size(value: impl ToString) -> Option<usize> {
    setting_value_text(value)
        .trim()
        .parse::<usize>()
        .ok()
        .filter(|value| *value > 0)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RetryPolicy {
    AtLeastOnce,
    AtMostOnce,
}

impl RetryPolicy {
    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "at_least_once" | "at-least-once" | "atleastonce" => Some(Self::AtLeastOnce),
            "at_most_once" | "at-most-once" | "atmostonce" => Some(Self::AtMostOnce),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::AtLeastOnce => "at_least_once",
            Self::AtMostOnce => "at_most_once",
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct StopBudget {
    deadline: std::time::Instant,
    max_http_timeout: std::time::Duration,
    checkpoint_reserve: std::time::Duration,
    total_budget: std::time::Duration,
}

impl StopBudget {
    pub fn from_env(started_at: std::time::Instant) -> Self {
        Self::new(
            started_at,
            duration_from_env("CCHOOKS_STOP_BUDGET_MS", DEFAULT_STOP_BUDGET_MS),
            duration_from_env(
                "CCHOOKS_STOP_HTTP_TIMEOUT_MS",
                DEFAULT_STOP_MAX_HTTP_TIMEOUT_MS,
            ),
            duration_from_env(
                "CCHOOKS_STOP_CHECKPOINT_RESERVE_MS",
                DEFAULT_STOP_CHECKPOINT_RESERVE_MS,
            ),
        )
    }

    pub fn from_session_end_env(started_at: std::time::Instant) -> Self {
        Self::new(
            started_at,
            duration_from_env(
                "CCHOOKS_SESSION_END_BUDGET_MS",
                DEFAULT_SESSION_END_BUDGET_MS,
            ),
            duration_from_env(
                "CCHOOKS_STOP_HTTP_TIMEOUT_MS",
                DEFAULT_STOP_MAX_HTTP_TIMEOUT_MS,
            ),
            duration_from_env(
                "CCHOOKS_STOP_CHECKPOINT_RESERVE_MS",
                DEFAULT_STOP_CHECKPOINT_RESERVE_MS,
            ),
        )
    }

    pub fn for_scheduled_recovery(started_at: std::time::Instant) -> Self {
        Self::new(
            started_at,
            std::time::Duration::from_millis(STOP_RECOVERY_BUDGET_MS),
            std::time::Duration::from_millis(STOP_RECOVERY_MAX_HTTP_TIMEOUT_MS),
            std::time::Duration::from_millis(STOP_RECOVERY_CHECKPOINT_RESERVE_MS),
        )
    }

    pub fn new(
        started_at: std::time::Instant,
        total_budget: std::time::Duration,
        max_http_timeout: std::time::Duration,
        checkpoint_reserve: std::time::Duration,
    ) -> Self {
        Self {
            deadline: started_at + total_budget,
            max_http_timeout,
            checkpoint_reserve,
            total_budget,
        }
    }

    pub fn remaining(&self) -> std::time::Duration {
        self.deadline
            .saturating_duration_since(std::time::Instant::now())
    }

    pub fn processing_time_remaining(&self) -> Option<std::time::Duration> {
        let available = self.remaining().checked_sub(self.checkpoint_reserve)?;
        (!available.is_zero()).then_some(available)
    }

    pub fn can_start_turn(&self) -> bool {
        self.processing_time_remaining().is_some()
    }

    pub fn http_timeout(&self) -> Option<std::time::Duration> {
        self.processing_time_remaining()
            .map(|available| available.min(self.max_http_timeout))
    }

    pub fn total_budget(&self) -> std::time::Duration {
        self.total_budget
    }

    pub fn max_http_timeout(&self) -> std::time::Duration {
        self.max_http_timeout
    }

    pub fn checkpoint_reserve(&self) -> std::time::Duration {
        self.checkpoint_reserve
    }
}

fn duration_from_env(name: &str, default_ms: u64) -> std::time::Duration {
    let millis = crate::common::env::var(name)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(default_ms);
    std::time::Duration::from_millis(millis)
}

/// Error type returned by hook handlers.
#[derive(Debug)]
pub struct HandlerError {
    pub message: String,
}

impl fmt::Display for HandlerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl From<String> for HandlerError {
    fn from(message: String) -> Self {
        Self { message }
    }
}

/// A deferred-send failure recorded by a worker thread, drained and turned into
/// a metric on the MAIN thread after the concurrent flush join. Metrics are kept
/// single-owner on the main thread (simplest correct design; also preserves the
/// panic-path flush in `main`), so worker threads only report failures here.
pub struct SendError {
    /// Metric name to increment (e.g. "aivision.agentic.export_error").
    pub metric: &'static str,
    /// Value for the `error.type` attribute.
    pub error_type: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StopOutcome {
    Completed,
    Deferred { reason: String },
    IntentionallySkipped,
}

impl StopOutcome {
    pub fn deferred(reason: impl Into<String>) -> Self {
        Self::Deferred {
            reason: reason.into(),
        }
    }
}

/// Shared context passed to all hook handlers.
pub struct CommonContext {
    pub logger: Logger,
    pub state_file: std::path::PathBuf,
    pub langfuse_config: Option<LangfuseConfig>,
    pub username: String,
    pub projects_dir: String,
    #[allow(dead_code)]
    pub debug_enabled: bool,
    pub hook_version: String,
    /// Prompt-time trace identity captured when this Stop-family event was
    /// enqueued. Recovery must reuse this snapshot instead of rereading the
    /// mutable current-turn state.
    pub turn_seed: Option<turn_seed::TurnSeed>,
    /// Transcript EOF captured by the parent for this Stop-family event. Old
    /// queue records omit it and retain legacy read-to-current-EOF behavior.
    pub stop_transcript_end_offset: Option<u64>,
    /// Local-only receipt used by embedders whose host transcript disappears
    /// as soon as the hook returns. The Langfuse handler acknowledges it after
    /// reading the bounded transcript window into memory, before network I/O.
    pub transcript_ingest_receipt: Option<String>,
    pub stop_budget: Option<StopBudget>,
    pub retry_policy: RetryPolicy,
    pub export_mode: ExportMode,
    pub realtime_llm_batch_size: usize,
    /// Optional metrics collector for self-monitoring (aivision.agentic.* metrics).
    /// Wrapped in RefCell to allow mutable access from handlers via shared &CommonContext.
    pub metrics: Option<RefCell<MetricsCollector>>,
    /// Deferred sends for auxiliary handlers. Langfuse Stop/SessionEnd export is
    /// synchronous so remote acceptance can be checkpointed before cursor commit.
    /// Wrapped in RefCell for the same shared-mutability reason as `metrics`.
    pub send_queue: RefCell<Vec<SendTask>>,
    /// Thread-safe sink for send failures recorded by worker threads. Drained on
    /// the MAIN thread after the concurrent flush to emit error metrics (the
    /// metrics collector is kept single-owner on the main thread). Cloneable
    /// `Arc` handles are captured by send closures.
    pub send_errors: std::sync::Arc<std::sync::Mutex<Vec<SendError>>>,
    /// A Stop handler may durably defer work while returning `Ok(())`, so its
    /// lifecycle cannot be inferred from generic handler success alone.
    pub stop_outcome: RefCell<StopOutcome>,
}

impl CommonContext {
    pub fn set_stop_outcome(&self, outcome: StopOutcome) {
        *self.stop_outcome.borrow_mut() = outcome;
    }

    pub fn stop_outcome(&self) -> StopOutcome {
        self.stop_outcome.borrow().clone()
    }
}

/// Trait that all hook handlers must implement.
pub trait HookHandler {
    /// Human-readable name for logging and debugging.
    fn name(&self) -> &str;

    /// List of hook events this handler is interested in.
    fn supported_events(&self) -> &[HookEvent];

    /// Process a hook event.
    fn handle(
        &self,
        event: &HookEvent,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError>;
}

/// Registry that manages hook handlers and dispatches events.
pub struct HookHandlerRegistry {
    handlers: Vec<Box<dyn HookHandler>>,
}

impl Default for HookHandlerRegistry {
    fn default() -> Self {
        Self::new()
    }
}

impl HookHandlerRegistry {
    pub fn new() -> Self {
        Self {
            handlers: Vec::new(),
        }
    }

    /// Register a new hook handler.
    pub fn register(&mut self, handler: Box<dyn HookHandler>) {
        self.handlers.push(handler);
    }

    /// Get all handlers that support the given event.
    pub fn get_handlers_for_event(&self, event: &HookEvent) -> Vec<&dyn HookHandler> {
        self.handlers
            .iter()
            .filter(|handler| handler.supported_events().contains(event))
            .map(|handler| handler.as_ref())
            .collect()
    }

    /// Dispatch an event to all matching handlers with error isolation.
    /// Returns a list of errors from failed handlers (if any).
    pub fn dispatch(
        &self,
        event: &HookEvent,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Vec<(String, HandlerError)> {
        let matching_handlers = self.get_handlers_for_event(event);
        let mut errors = Vec::new();

        if matching_handlers.is_empty() {
            context.logger.debug(&format!(
                "No handlers registered for event {:?}, skipping",
                event
            ));
            return errors;
        }

        for handler in matching_handlers {
            let t = std::time::Instant::now();
            let result = handler.handle(event, payload, context);
            let elapsed = t.elapsed().as_millis();
            context.logger.info(&format!(
                "handler '{}' {:?}: {}ms execution=async",
                handler.name(),
                event,
                elapsed
            ));
            if let Err(err) = result {
                context.logger.error(&format!(
                    "Handler '{}' failed for event {:?}: {}",
                    handler.name(),
                    event,
                    err
                ));
                errors.push((handler.name().to_string(), err));
            }
        }

        errors
    }
}

#[cfg(test)]
mod tests {
    use super::{
        active_stop_should_skip, parse_bool_setting, parse_positive_batch_size,
        resolve_bool_setting, BoolSetting, ExportMode, RetryPolicy, StopBudget,
        DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE, DEFAULT_STOP_MAX_HTTP_TIMEOUT_MS,
        DEFAULT_STOP_TRANSCRIPT_SETTLE_MS, INTERNAL_FINALIZER_AUTHORIZED,
    };
    use serde_json::json;

    #[test]
    fn strict_bool_setting_accepts_only_the_four_value_contract() {
        for value in ["true", "TRUE", "TrUe", "1", " true ", "\t1\n"] {
            assert_eq!(parse_bool_setting(value), Some(true), "value={value:?}");
        }
        for value in ["false", "FALSE", "FaLsE", "0", " false ", "\t0\n"] {
            assert_eq!(parse_bool_setting(value), Some(false), "value={value:?}");
        }
        for value in ["", " ", "yes", "no", "on", "off", "01", "2", "invalid"] {
            assert_eq!(parse_bool_setting(value), None, "value={value:?}");
        }
    }

    #[test]
    fn strict_bool_resolution_distinguishes_unset_empty_and_invalid() {
        assert_eq!(resolve_bool_setting(None), BoolSetting::Unset);
        assert_eq!(
            resolve_bool_setting(Some("  ".to_string())),
            BoolSetting::Unset
        );
        assert_eq!(
            resolve_bool_setting(Some(" true ".to_string())),
            BoolSetting::Valid(true)
        );
        assert_eq!(
            resolve_bool_setting(Some(" false ".to_string())),
            BoolSetting::Valid(false)
        );
        assert_eq!(
            resolve_bool_setting(Some("bad\nvalue".to_string())),
            BoolSetting::Invalid("bad�value".to_string())
        );
        let long = "x".repeat(80);
        assert_eq!(
            resolve_bool_setting(Some(long)),
            BoolSetting::Invalid(format!("{}…", "x".repeat(64)))
        );
    }

    #[test]
    fn active_stop_guard_requires_internal_finalizer_authorization() {
        assert!(active_stop_should_skip(&json!({"stop_hook_active": true})));
        assert!(!active_stop_should_skip(&json!({
            "stop_hook_active": true,
            INTERNAL_FINALIZER_AUTHORIZED: true
        })));
        assert!(!active_stop_should_skip(
            &json!({"stop_hook_active": false})
        ));
    }

    #[test]
    fn realtime_export_mode_accepts_truthy_and_falsey_values() {
        for value in ["true", "1", "yes", "on", " TRUE "] {
            assert_eq!(
                ExportMode::parse(value),
                Some(ExportMode::RealtimeWithStopFinalizer)
            );
        }
        for value in ["false", "0", "no", "off", " FALSE "] {
            assert_eq!(ExportMode::parse(value), Some(ExportMode::StopOnly));
        }
        assert_eq!(
            ExportMode::parse(true),
            Some(ExportMode::RealtimeWithStopFinalizer)
        );
        assert_eq!(
            ExportMode::parse(1),
            Some(ExportMode::RealtimeWithStopFinalizer)
        );
        assert_eq!(ExportMode::parse(false), Some(ExportMode::StopOnly));
        assert_eq!(ExportMode::parse(0), Some(ExportMode::StopOnly));
        assert_eq!(
            ExportMode::parse(serde_json::json!("true")),
            Some(ExportMode::RealtimeWithStopFinalizer)
        );
        assert_eq!(
            ExportMode::parse(serde_json::json!(true)),
            Some(ExportMode::RealtimeWithStopFinalizer)
        );
        assert_eq!(
            ExportMode::parse(serde_json::json!("false")),
            Some(ExportMode::StopOnly)
        );
        assert_eq!(
            ExportMode::parse(serde_json::json!(0)),
            Some(ExportMode::StopOnly)
        );
        assert_eq!(ExportMode::parse("invalid"), None);
        assert_eq!(ExportMode::default(), ExportMode::RealtimeWithStopFinalizer);
    }

    #[test]
    fn realtime_llm_batch_size_requires_a_positive_usize() {
        for (value, expected) in [
            ("1", Some(1)),
            (" 3 ", Some(3)),
            ("17", Some(17)),
            ("0", None),
            ("-1", None),
            ("nope", None),
            ("999999999999999999999999999999999999", None),
        ] {
            assert_eq!(parse_positive_batch_size(value), expected);
        }
        assert_eq!(parse_positive_batch_size(3), Some(3));
        assert_eq!(parse_positive_batch_size(0), None);
        assert_eq!(parse_positive_batch_size(serde_json::json!("3")), Some(3));
        assert_eq!(parse_positive_batch_size(serde_json::json!(3)), Some(3));
        assert_eq!(DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE, 3);
    }

    #[test]
    fn retry_policy_accepts_canonical_and_compatible_values() {
        assert_eq!(
            RetryPolicy::parse("at_least_once"),
            Some(RetryPolicy::AtLeastOnce)
        );
        assert_eq!(
            RetryPolicy::parse("at-least-once"),
            Some(RetryPolicy::AtLeastOnce)
        );
        assert_eq!(
            RetryPolicy::parse("atmostonce"),
            Some(RetryPolicy::AtMostOnce)
        );
        assert_eq!(RetryPolicy::parse("invalid"), None);
    }

    #[test]
    fn default_http_timeout_is_twenty_seconds() {
        assert_eq!(DEFAULT_STOP_MAX_HTTP_TIMEOUT_MS, 20_000);
    }

    #[test]
    fn default_stop_transcript_settle_is_ten_seconds() {
        assert_eq!(DEFAULT_STOP_TRANSCRIPT_SETTLE_MS, 10_000);
    }

    #[test]
    fn stop_budget_caps_http_and_preserves_checkpoint_reserve() {
        let budget = StopBudget::new(
            std::time::Instant::now(),
            std::time::Duration::from_secs(2),
            std::time::Duration::from_millis(400),
            std::time::Duration::from_millis(250),
        );
        assert!(budget.can_start_turn());
        assert!(budget.http_timeout().unwrap() <= std::time::Duration::from_millis(400));

        let exhausted = StopBudget::new(
            std::time::Instant::now() - std::time::Duration::from_secs(1),
            std::time::Duration::from_millis(100),
            std::time::Duration::from_secs(5),
            std::time::Duration::from_millis(50),
        );
        assert!(!exhausted.can_start_turn());
        assert!(exhausted.http_timeout().is_none());
    }
}
