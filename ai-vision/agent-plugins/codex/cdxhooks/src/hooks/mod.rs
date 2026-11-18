#[cfg(feature = "dynamic")]
pub mod dynamic;
pub mod langfuse;
pub mod resource_context;
#[cfg(feature = "skill")]
pub mod skill_tag_handler;
#[cfg(feature = "tool_context")]
pub mod tool_context;

use std::cell::RefCell;
use std::fmt;

use agentic_otel_common::metrics::MetricsCollector;

use crate::common::hook::HookEvent;
use crate::common::logger::Logger;
use crate::hooks::langfuse::LangfuseConfig;

pub const DEFAULT_STOP_BUDGET_MS: u64 = 30_000;
pub const DEFAULT_STOP_MAX_HTTP_TIMEOUT_MS: u64 = 20_000;
pub const DEFAULT_STOP_CHECKPOINT_RESERVE_MS: u64 = 500;
pub const DEFAULT_SESSION_END_BUDGET_MS: u64 = 110_000;
pub const RETRY_POLICY_ENV: &str = "CDXHOOKS_RETRY_POLICY";
pub const REALTIME_EXPORT_ENV: &str = "CDXHOOKS_REALTIME_EXPORT";
pub const REALTIME_LLM_EXPORT_BATCH_SIZE_ENV: &str = "CDXHOOKS_REALTIME_LLM_EXPORT_BATCH_SIZE";
pub const DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE: usize = 3;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum RealtimeExportMode {
    StopOnly,
    #[default]
    RealtimeWithStopFinalizer,
}

impl RealtimeExportMode {
    pub fn parse(value: impl ToString) -> Option<Self> {
        match setting_value_text(value)
            .trim()
            .to_ascii_lowercase()
            .as_str()
        {
            "true" | "1" => Some(Self::RealtimeWithStopFinalizer),
            "false" | "0" => Some(Self::StopOnly),
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
        let total_budget = duration_from_env("CDXHOOKS_STOP_BUDGET_MS", DEFAULT_STOP_BUDGET_MS);
        let max_http_timeout = duration_from_env(
            "CDXHOOKS_STOP_HTTP_TIMEOUT_MS",
            DEFAULT_STOP_MAX_HTTP_TIMEOUT_MS,
        );
        let checkpoint_reserve = duration_from_env(
            "CDXHOOKS_STOP_CHECKPOINT_RESERVE_MS",
            DEFAULT_STOP_CHECKPOINT_RESERVE_MS,
        );
        Self::new(
            started_at,
            total_budget,
            max_http_timeout,
            checkpoint_reserve,
        )
    }

    pub fn from_session_end_env(started_at: std::time::Instant) -> Self {
        let total_budget = duration_from_env(
            "CDXHOOKS_SESSION_END_BUDGET_MS",
            DEFAULT_SESSION_END_BUDGET_MS,
        );
        let max_http_timeout = duration_from_env(
            "CDXHOOKS_STOP_HTTP_TIMEOUT_MS",
            DEFAULT_STOP_MAX_HTTP_TIMEOUT_MS,
        );
        let checkpoint_reserve = duration_from_env(
            "CDXHOOKS_STOP_CHECKPOINT_RESERVE_MS",
            DEFAULT_STOP_CHECKPOINT_RESERVE_MS,
        );
        Self::new(
            started_at,
            total_budget,
            max_http_timeout,
            checkpoint_reserve,
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

    pub fn can_start_turn(&self) -> bool {
        self.processing_time_remaining().is_some()
    }

    /// Time available for turn construction/merge work while preserving the
    /// configured checkpoint reserve.
    pub fn processing_time_remaining(&self) -> Option<std::time::Duration> {
        let available = self.remaining().checked_sub(self.checkpoint_reserve)?;
        (!available.is_zero()).then_some(available)
    }

    pub fn http_timeout(&self) -> Option<std::time::Duration> {
        let available = self.processing_time_remaining()?;
        Some(std::cmp::min(available, self.max_http_timeout))
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
    let millis = std::env::var(name)
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
    retryable: bool,
}

impl HandlerError {
    pub fn retryable(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            retryable: true,
        }
    }

    pub fn is_retryable(&self) -> bool {
        self.retryable
    }
}

impl fmt::Display for HandlerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl From<String> for HandlerError {
    fn from(message: String) -> Self {
        Self {
            message,
            retryable: false,
        }
    }
}

/// Shared context passed to all hook handlers.
pub struct CommonContext {
    pub logger: Logger,
    pub state_file: std::path::PathBuf,
    pub langfuse_config: Option<LangfuseConfig>,
    pub username: String,
    pub sessions_dir: String,
    pub hook_version: String,
    pub stop_budget: Option<StopBudget>,
    pub retry_policy: RetryPolicy,
    pub export_mode: RealtimeExportMode,
    pub realtime_llm_batch_size: usize,
    /// Optional metrics collector for self-monitoring (aivision.agentic.* metrics).
    pub metrics: Option<RefCell<MetricsCollector>>,
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

#[cfg(test)]
mod tests {
    use super::{
        parse_positive_batch_size, RealtimeExportMode, RetryPolicy,
        DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE, DEFAULT_STOP_MAX_HTTP_TIMEOUT_MS,
    };

    #[test]
    fn realtime_export_mode_accepts_documented_boolean_values() {
        for value in ["true", "1", " TRUE "] {
            assert_eq!(
                RealtimeExportMode::parse(value),
                Some(RealtimeExportMode::RealtimeWithStopFinalizer)
            );
        }
        for value in ["false", "0", " FALSE "] {
            assert_eq!(
                RealtimeExportMode::parse(value),
                Some(RealtimeExportMode::StopOnly)
            );
        }

        assert_eq!(
            RealtimeExportMode::parse(true),
            Some(RealtimeExportMode::RealtimeWithStopFinalizer)
        );
        assert_eq!(
            RealtimeExportMode::parse(1),
            Some(RealtimeExportMode::RealtimeWithStopFinalizer)
        );
        assert_eq!(
            RealtimeExportMode::parse(false),
            Some(RealtimeExportMode::StopOnly)
        );
        assert_eq!(
            RealtimeExportMode::parse(0),
            Some(RealtimeExportMode::StopOnly)
        );
        assert_eq!(
            RealtimeExportMode::parse(serde_json::json!("true")),
            Some(RealtimeExportMode::RealtimeWithStopFinalizer)
        );
        assert_eq!(
            RealtimeExportMode::parse(serde_json::json!(true)),
            Some(RealtimeExportMode::RealtimeWithStopFinalizer)
        );
        assert_eq!(
            RealtimeExportMode::parse(serde_json::json!("false")),
            Some(RealtimeExportMode::StopOnly)
        );
        assert_eq!(
            RealtimeExportMode::parse(serde_json::json!(0)),
            Some(RealtimeExportMode::StopOnly)
        );

        assert_eq!(RealtimeExportMode::parse("yes"), None);
        assert_eq!(RealtimeExportMode::parse("on"), None);
        assert_eq!(RealtimeExportMode::parse("invalid"), None);
        assert_eq!(
            RealtimeExportMode::default(),
            RealtimeExportMode::RealtimeWithStopFinalizer
        );
        assert!(RealtimeExportMode::default().realtime_enabled());
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
}

/// Registry that manages hook handlers and dispatches events.
pub struct HookHandlerRegistry {
    handlers: Vec<Box<dyn HookHandler>>,
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
            let started = std::time::Instant::now();
            context.logger.debug(&format!(
                "Dispatching {:?} to handler '{}'",
                event,
                handler.name()
            ));
            let result = handler.handle(event, payload, context);
            context.logger.info(&format!(
                "handler '{}' {:?}: {}ms execution=async",
                handler.name(),
                event,
                started.elapsed().as_millis()
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
mod stop_budget_tests {
    use super::StopBudget;
    use std::time::{Duration, Instant};

    #[test]
    fn fresh_budget_caps_http_timeout_and_reserves_checkpoint_time() {
        let budget = StopBudget::new(
            Instant::now(),
            Duration::from_secs(2),
            Duration::from_millis(200),
            Duration::from_millis(100),
        );

        assert!(budget.can_start_turn());
        let timeout = budget.http_timeout().unwrap();
        assert!(timeout > Duration::ZERO);
        assert!(timeout <= Duration::from_millis(200));
    }

    #[test]
    fn expired_budget_starts_neither_turn_nor_http_request() {
        let budget = StopBudget::new(
            Instant::now() - Duration::from_secs(2),
            Duration::from_secs(1),
            Duration::from_millis(200),
            Duration::from_millis(100),
        );

        assert!(!budget.can_start_turn());
        assert_eq!(budget.http_timeout(), None);
    }
}
