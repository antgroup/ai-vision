use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;

use agentic_otel_common::{
    extract_skill_name_from_tool_input, iso_to_nanos,
    message::{serialize_messages_default_with_stats, OpenAIMessage},
    otel_span_id, otel_trace_id,
    otlp::{
        append_agentic_tool_attrs_typed, append_otel_span_attributes, wrap_trace_export,
        OtelAttribute, OtelSpan, OtelStatus, SPAN_KIND_INTERNAL, SPAN_KIND_SERVER,
    },
    parse_agentic_tool as parse_shared_agentic_tool, tool_span_name, truncate_text_with_stats,
    truncate_value_with_stats, AgenticToolInfo, ExportContext, TruncationStats,
    INTERNAL_META_ROOT_KEY, INTERNAL_META_ROOT_VALUE,
};

use crate::common::hook::is_subagent_orchestration_tool;
use crate::common::message_format::{
    build_generation_accumulated_input, build_generation_delta_input, build_input_messages,
    build_output_messages,
};
use crate::common::transcript::{
    TaskNotification, TerminalLlmError, ToolResultItem, ToolUse, Turn,
};
use crate::hooks::langfuse::{
    build_basic_auth_header, build_custom_metadata, resolve_agent_product, resolve_agent_type,
    LangfuseConfig,
};
use crate::hooks::timing::{SubagentTimingData, TimingData};

fn parse_agentic_tool(
    raw_tool_name: &str,
    tool_id: &str,
    skill_name: Option<&str>,
) -> AgenticToolInfo {
    let mut info = parse_shared_agentic_tool(raw_tool_name, tool_id, skill_name);
    if raw_tool_name == "Task" {
        info.tool_type = "subagent";
        info.name = raw_tool_name.to_string();
    }
    info
}

const HTTP_TIMEOUT_SECS: u64 = 10;

/// Default threshold for `auto` mode: switch to delta when a turn is estimated
/// to produce more than this many spans. `build_generation_accumulated_input` is
/// O(n) per generation and serialized per generation, so a turn that emits N spans
/// pays roughly O(N²) work and bytes in the accumulated branch. 100 is the knee
/// observed in real traces (e.g. session 83753157 turn 5: ~1500 spans → 60 s+).
const INPUT_MESSAGES_AUTO_DEFAULT_THRESHOLD: usize = 100;

/// Each inlined subagent call expands into many spans (its own LLM + TOOL chain).
/// Observed turns avg ~30-80 spans per subagent; we pick the lower bound so the
/// estimator stays conservative (slightly underestimates rather than overcounts).
const SUBAGENT_INLINE_SPAN_WEIGHT: usize = 30;

/// Estimate the number of spans this turn will emit, including the spans inlined
/// by `expand_subagent_spans` for any subagent (Agent tool) invocation. Cheap:
/// no JSON building, no subagent transcript reads.
fn estimated_turn_span_count(turn: &Turn) -> usize {
    let llm = turn.assistants.len();
    let mut tools = 0usize;
    let mut subagent_calls = 0usize;
    for a in &turn.assistants {
        for tu in &a.tool_uses {
            tools += 1;
            // Claude Code names the subagent invocation tool "Agent" (and "Task" in
            // some plugin variants). Each one inlines a full subagent transcript.
            if is_subagent_orchestration_tool(&tu.name) {
                subagent_calls += 1;
            }
        }
    }
    llm + tools + subagent_calls * SUBAGENT_INLINE_SPAN_WEIGHT
}

/// How `gen_ai.input.messages` is materialized on each generation span.
///
/// Driven by env `CCHOOKS_INPUT_MESSAGES_MODE`:
/// - unset / `auto` / `auto:N` → `Auto(N)` (default N = 100, counted in
///   estimated spans, not assistants)
/// - `accumulated` → always emit the full prior-message history (legacy behavior;
///   O(n²) build cost across a turn)
/// - `delta` → emit only the messages new since the previous generation
///
/// `Auto` falls back to `Accumulated` for small turns and `Delta` once the turn
/// crosses the estimated-span threshold, capping per-turn body size and CPU.
#[derive(Debug, Clone, Copy)]
enum InputMessagesMode {
    Accumulated,
    Delta,
    Auto(usize),
}

fn input_messages_mode() -> InputMessagesMode {
    match crate::common::env::var("CCHOOKS_INPUT_MESSAGES_MODE")
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "" | "delta" => InputMessagesMode::Delta,
        "accumulated" => InputMessagesMode::Accumulated,
        "auto" => InputMessagesMode::Auto(INPUT_MESSAGES_AUTO_DEFAULT_THRESHOLD),
        other if other.starts_with("auto:") => {
            let n = other["auto:".len()..]
                .parse::<usize>()
                .unwrap_or(INPUT_MESSAGES_AUTO_DEFAULT_THRESHOLD);
            InputMessagesMode::Auto(n)
        }
        _ => InputMessagesMode::Delta,
    }
}

/// Privacy collection mode.
/// Driven by env: AIVISION_COLLECTION_MODE > CCHOOKS_COLLECTION_MODE
/// Values: "full" (default) | "privacy"
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CollectionMode {
    Full,
    Privacy,
}

pub fn collection_mode() -> CollectionMode {
    match agentic_otel_common::privacy::resolve_mode("CCHOOKS_COLLECTION_MODE") {
        Ok("full") => CollectionMode::Full,
        _ => CollectionMode::Privacy,
    }
}

pub fn is_privacy_mode() -> bool {
    collection_mode() == CollectionMode::Privacy
}

// Cascade: when the parent turn is large enough to trigger delta mode, every
// subagent inlined into the same turn must follow suit. Otherwise an 18-subagent
// mega-turn re-bloats because each subagent's own turn is small and individually
// would pick accumulated. Thread-local because each cchooks invocation runs on a
// single thread per Stop event.
std::thread_local! {
    static PARENT_FORCE_DELTA: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

struct ParentForceDeltaGuard;

impl ParentForceDeltaGuard {
    fn set(force: bool) -> Self {
        PARENT_FORCE_DELTA.with(|c| c.set(force));
        Self
    }
}

impl Drop for ParentForceDeltaGuard {
    fn drop(&mut self) {
        PARENT_FORCE_DELTA.with(|c| c.set(false));
    }
}

/// Returns true when this turn should drop the accumulated history and emit
/// delta-only input messages.
fn use_delta_only(turn: &Turn) -> bool {
    if PARENT_FORCE_DELTA.with(|c| c.get()) {
        return true;
    }
    match input_messages_mode() {
        InputMessagesMode::Accumulated => false,
        InputMessagesMode::Delta => true,
        InputMessagesMode::Auto(threshold) => estimated_turn_span_count(turn) > threshold,
    }
}

// --- Rule 7: Error detection helpers ---

/// Detect errors in tool results. Returns (error_type, error_message) if error found.
fn detect_tool_error(tool_result: &Value) -> Option<(String, String)> {
    // Check for is_error flag
    if tool_result
        .get("is_error")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        let msg = tool_result
            .get("error")
            .or_else(|| tool_result.get("message"))
            .and_then(|v| v.as_str())
            .unwrap_or("tool execution error")
            .to_string();
        return Some(("tool_error".to_string(), msg));
    }

    // Check for error field in result
    if let Some(err) = tool_result.get("error").and_then(|v| v.as_str()) {
        return Some(("tool_error".to_string(), err.to_string()));
    }

    // Check string results starting with "Error:"
    if let Some(s) = tool_result.as_str() {
        if s.starts_with("Error:") || s.starts_with("error:") {
            return Some(("tool_error".to_string(), s.to_string()));
        }
    }

    None
}

fn detect_tool_result_item_error(
    result: Option<&ToolResultItem>,
    content: &Value,
) -> Option<(String, String)> {
    if let Some(result) = result {
        if let Some(reason) = result.cancellation_reason.as_deref() {
            return Some(("cancelled".to_string(), reason.to_string()));
        }
        if result.is_error {
            let msg = content
                .get("error")
                .or_else(|| content.get("message"))
                .and_then(|v| v.as_str())
                .or(result.tool_use_result_text.as_deref())
                .unwrap_or("tool execution error")
                .to_string();
            return Some(("tool_error".to_string(), msg));
        }
    }
    detect_tool_error(content)
}

fn neutral_tool_result_item(
    tool_use_id: String,
    content: Value,
    tool_name: Option<String>,
    timestamp: String,
    agent_id: Option<String>,
    agent_type: Option<String>,
    is_async: bool,
) -> ToolResultItem {
    ToolResultItem {
        tool_use_id,
        content,
        is_error: false,
        tool_denial_kind: None,
        tool_use_result_text: None,
        cancellation_reason: None,
        tool_name,
        timestamp,
        agent_id,
        agent_type,
        is_async,
    }
}

/// Detect LLM errors from stop_reason. Returns (error_type, error_message) if error found.
fn detect_llm_error(stop_reason: &str) -> Option<(String, String)> {
    match stop_reason {
        "max_tokens" => Some((
            "max_tokens".to_string(),
            "LLM response truncated: max tokens reached".to_string(),
        )),
        s if s.contains("error") => Some(("server_error".to_string(), format!("LLM error: {}", s))),
        _ => None,
    }
}

// --- Rule 12: Truncation metadata helper ---

/// Append truncation metadata attributes when content was truncated.
fn append_truncation_meta(
    attributes: &mut Vec<OtelAttribute>,
    input_meta: &Value,
    output_meta: &Value,
) {
    if input_meta
        .get("truncated")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        attributes.push(OtelAttribute::string(
            "agentic.tool.input.truncated",
            "true",
        ));
        if let Some(len) = input_meta.get("orig_len").and_then(|v| v.as_i64()) {
            attributes.push(OtelAttribute::int_as_string(
                "agentic.tool.input.original_length",
                len,
            ));
        }
    }
    if output_meta
        .get("truncated")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        attributes.push(OtelAttribute::string(
            "agentic.tool.output.truncated",
            "true",
        ));
        if let Some(len) = output_meta.get("orig_len").and_then(|v| v.as_i64()) {
            attributes.push(OtelAttribute::int_as_string(
                "agentic.tool.output.original_length",
                len,
            ));
        }
    }
}

#[cfg(test)]
fn format_truncation_log_fields(
    stats: &TruncationStats,
    operation: &str,
    hook_event: &str,
    event_id: &str,
    worker_id: &str,
    field_kind: &str,
) -> String {
    stats
        .clone()
        .with_log_context(operation, hook_event, event_id, worker_id, field_kind)
        .to_safe_log_fields()
}

fn log_truncation_stats(
    logger: &crate::common::logger::Logger,
    stats: &TruncationStats,
    operation: &str,
    hook_event: &str,
    event_id: &str,
    worker_id: &str,
    field_kind: &str,
) {
    let enriched = stats
        .clone()
        .with_log_context(operation, hook_event, event_id, worker_id, field_kind);
    let line = format!(
        "truncation execution=async {}",
        enriched.to_safe_log_fields()
    );
    match enriched.log_level() {
        "WARN" => logger.warn(&line),
        "INFO" => logger.info(&line),
        _ => logger.debug(&line),
    }
}

fn terminal_error_for_assistant<'a>(
    turn: &'a Turn,
    assistant: &crate::common::transcript::AssistantMessage,
) -> Option<&'a TerminalLlmError> {
    if assistant.is_synthetic {
        turn.terminal_llm_error.as_ref()
    } else {
        None
    }
}

fn llm_error_type_from_value(error: &Value) -> String {
    let raw = error
        .pointer("/error/type")
        .or_else(|| error.get("type"))
        .and_then(Value::as_str)
        .unwrap_or("api_error");
    let message = llm_error_message_from_value(error);
    let has_invalid_model_evidence = error
        .pointer("/error/model")
        .or_else(|| error.pointer("/error/requested_model"))
        .or_else(|| error.pointer("/model"))
        .or_else(|| error.pointer("/requested_model"))
        .is_some()
        || message.to_ascii_lowercase().contains("invalid model")
        || message.to_ascii_lowercase().contains("model:");
    match raw {
        "invalid_request_error" if has_invalid_model_evidence => "invalid_model".to_string(),
        "invalid_request_error" => "invalid_request".to_string(),
        "authentication_error" => "authentication".to_string(),
        "permission_error" => "permission".to_string(),
        "rate_limit_error" => "rate_limit".to_string(),
        "api_error" | "overloaded_error" | "server_error" => "api_error".to_string(),
        other if other.contains("timeout") => "timeout".to_string(),
        other if other.contains("invalid") && has_invalid_model_evidence => {
            "invalid_model".to_string()
        }
        other if other.contains("invalid") => "invalid_request".to_string(),
        _ => "api_error".to_string(),
    }
}

fn llm_error_message_from_value(error: &Value) -> &str {
    error
        .pointer("/error/message")
        .or_else(|| error.pointer("/error/error"))
        .or_else(|| error.get("message"))
        .or_else(|| error.get("error"))
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or("unknown error")
}

#[derive(Clone)]
struct WorkerLogContext {
    logger: crate::common::logger::Logger,
    hook_event: String,
    event_id: String,
    worker_id: String,
    http_total_ms: u128,
}

thread_local! {
    static WORKER_LOG_CONTEXT: RefCell<Option<WorkerLogContext>> = const { RefCell::new(None) };
}

pub fn install_worker_log_context(
    logger: &crate::common::logger::Logger,
    hook_event: &str,
    event_id: &str,
    worker_id: &str,
) {
    WORKER_LOG_CONTEXT.with(|slot| {
        *slot.borrow_mut() = Some(WorkerLogContext {
            logger: logger.clone(),
            hook_event: hook_event.to_string(),
            event_id: event_id.to_string(),
            worker_id: worker_id.to_string(),
            http_total_ms: 0,
        });
    });
}

pub fn clear_worker_log_context() {
    WORKER_LOG_CONTEXT.with(|slot| *slot.borrow_mut() = None);
}

fn log_hook_phase(
    logger: &crate::common::logger::Logger,
    component: &str,
    execution: &str,
    phase: &str,
    elapsed_ms: u128,
) {
    let line = format!(
        "hook_timing {{\"component\":\"{}\",\"phase\":\"{}\",\"elapsed_ms\":{},\"execution\":\"{}\"}}",
        component, phase, elapsed_ms, execution
    );
    if elapsed_ms > 10 {
        logger.warn(&line);
    } else {
        logger.debug(&line);
    }
}

pub fn log_parent_phase(logger: &crate::common::logger::Logger, phase: &str, elapsed_ms: u128) {
    log_hook_phase(logger, "parent", "sync", phase, elapsed_ms);
}

pub fn log_worker_phase(logger: &crate::common::logger::Logger, phase: &str, elapsed_ms: u128) {
    if matches!(phase, "http" | "metrics_http") {
        WORKER_LOG_CONTEXT.with(|slot| {
            if let Some(context) = slot.borrow_mut().as_mut() {
                context.http_total_ms = context.http_total_ms.saturating_add(elapsed_ms);
            }
        });
    }
    log_hook_phase(logger, "worker", "async", phase, elapsed_ms);
}

pub fn observe_current_worker_phase(phase: &str, elapsed_ms: u128) {
    let logger = WORKER_LOG_CONTEXT
        .with(|slot| slot.borrow().as_ref().map(|context| context.logger.clone()));
    if let Some(logger) = logger {
        log_worker_phase(&logger, phase, elapsed_ms);
    }
}

pub fn log_worker_http_total(logger: &crate::common::logger::Logger) {
    let elapsed_ms = WORKER_LOG_CONTEXT.with(|slot| {
        slot.borrow()
            .as_ref()
            .map(|context| context.http_total_ms)
            .unwrap_or(0)
    });
    log_worker_phase(logger, "http_total", elapsed_ms);
}

struct WorkerPhaseGuard {
    phase: &'static str,
    started: std::time::Instant,
}

impl WorkerPhaseGuard {
    fn new(phase: &'static str) -> Self {
        Self {
            phase,
            started: std::time::Instant::now(),
        }
    }
}

impl Drop for WorkerPhaseGuard {
    fn drop(&mut self) {
        observe_current_worker_phase(self.phase, self.started.elapsed().as_millis());
    }
}

fn observe_truncation(stats: &TruncationStats, field_kind: &str) {
    WORKER_LOG_CONTEXT.with(|slot| {
        let context = slot.borrow();
        if let Some(context) = context.as_ref() {
            log_truncation_stats(
                &context.logger,
                stats,
                &stats.operation,
                &context.hook_event,
                &context.event_id,
                &context.worker_id,
                field_kind,
            );
        }
    });
}

fn truncate_text_observed(text: &str, field_kind: &str) -> (String, Value) {
    let (value, meta, stats) = truncate_text_with_stats(text, field_kind);
    observe_truncation(&stats, field_kind);
    (value, meta)
}

fn truncate_value_observed(value: &Value, field_kind: &str) -> (Value, Value) {
    let (value, meta, stats) = truncate_value_with_stats(value, field_kind);
    observe_truncation(&stats, field_kind);
    (value, meta)
}

fn serialize_messages_observed(
    messages: &[OpenAIMessage],
    protected_tail: usize,
    field_kind: &str,
) -> String {
    let (value, stats) =
        serialize_messages_default_with_stats(messages, protected_tail, field_kind);
    observe_truncation(&stats, field_kind);
    value
}

// --- OTel span builders ---

/// Build a root span representing the trace-level entity.
/// Langfuse maps root span attributes to trace-level fields.
/// Check if tool_context is enabled at runtime.
#[cfg(all(feature = "tool_context", not(windows)))]
pub fn tool_context_enabled() -> bool {
    matches!(
        crate::common::hook::env_or_default(
            "CCHOOKS_TOOL_CONTEXT",
            "CCHOOKS_TOOL_CONTEXT",
            "false"
        )
        .as_str(),
        "true" | "1"
    )
}

/// Trace context stored by ToolContextHandler at PreToolUse time.
/// Carries the full upstream context snapshot for ESC-recovery scenarios.
#[derive(Debug, Clone, Default)]
#[allow(dead_code)]
pub struct StoredTraceContext {
    pub trace_id: String,
    /// Optional persisted root span ID used only by the realtime overlay.
    pub root_span_id: Option<String>,
    /// Original W3C traceparent string (if upstream provided one)
    pub traceparent: Option<String>,
    pub sofa_traceid: Option<String>,
    pub sofa_rpcid: Option<String>,
    /// tool_use_ids that used this context (for matching turns at Stop time)
    pub tool_use_ids: Vec<String>,
    /// Persisted tool span identities keyed by tool_use_id.
    pub tool_span_ids: HashMap<String, String>,
    /// Realtime tool spans already accounted for under the selected retry policy.
    pub emitted_tool_use_ids: Vec<String>,
    /// Persisted generation span identities keyed only by native assistant message.id.
    pub generation_span_ids: HashMap<String, String>,
    /// Realtime generation spans already accounted for under the selected retry policy.
    pub accounted_generation_ids: Vec<String>,
    /// Maximum exact end time among realtime children durably acknowledged by the exporter.
    pub max_accounted_child_end_time_unix_nano: Option<String>,
}

/// Minimal root identity needed to attach late realtime compaction events to
/// the already-exported turn root without replaying the whole turn.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CompactionParentAnchor {
    pub turn_id: String,
    pub turn_number: usize,
    pub trace_id: String,
    pub root_span_id: String,
    pub chat_id: String,
    pub transcript_path: String,
    pub source_end_offset: u64,
}

/// Make every locally-created identity in one Stop export stable across
/// concurrent/retried delivery attempts while preserving identities already
/// claimed by realtime/tool-context state.
pub fn stabilize_turn_export_identities(
    body: &mut Value,
    stable_turn_id: &str,
    trace_context_history: &[StoredTraceContext],
) {
    stabilize_turn_export_identities_with_selected_trace(
        body,
        stable_turn_id,
        trace_context_history,
        None,
    );
}

/// Stabilize locally-created IDs while protecting both historical realtime
/// contexts and the trace identity selected for the current Stop turn.
pub fn stabilize_turn_export_identities_with_selected_trace(
    body: &mut Value,
    stable_turn_id: &str,
    trace_context_history: &[StoredTraceContext],
    selected_trace_id: Option<&str>,
) {
    let mut protected_trace_ids: HashSet<String> = trace_context_history
        .iter()
        .map(|context| context.trace_id.clone())
        .collect();
    protected_trace_ids.extend(selected_trace_id.map(str::to_string));
    let mut local_span_ids = HashSet::<String>::new();
    for_each_export_span(body, |span| {
        if let Some(span_id) = span.get("spanId").and_then(Value::as_str) {
            local_span_ids.insert(span_id.to_string());
        }
    });
    // A non-local parentSpanId is an upstream W3C parent. Its trace identity is
    // externally owned even when no persisted tool context exists (pure-LLM
    // Stop is the common case), so stabilization must never rewrite it.
    for_each_export_span(body, |span| {
        let external_parent = span
            .get("parentSpanId")
            .and_then(Value::as_str)
            .filter(|parent| !parent.is_empty())
            .is_some_and(|parent| !local_span_ids.contains(parent));
        if external_parent {
            if let Some(trace_id) = span.get("traceId").and_then(Value::as_str) {
                protected_trace_ids.insert(trace_id.to_string());
            }
        }
    });
    let mut protected_span_ids: HashSet<&str> = HashSet::new();
    for context in trace_context_history {
        if let Some(root_span_id) = context.root_span_id.as_deref() {
            protected_span_ids.insert(root_span_id);
        }
        protected_span_ids.extend(context.tool_span_ids.values().map(String::as_str));
        protected_span_ids.extend(context.generation_span_ids.values().map(String::as_str));
    }

    let mut trace_id_map = HashMap::<String, String>::new();
    let mut span_id_map = HashMap::<String, String>::new();
    let mut trace_ordinal = 0usize;
    let mut span_ordinal = 0usize;
    for_each_export_span(body, |span| {
        if let Some(trace_id) = span.get("traceId").and_then(Value::as_str) {
            if !protected_trace_ids.contains(trace_id) && !trace_id_map.contains_key(trace_id) {
                trace_id_map.insert(
                    trace_id.to_string(),
                    stable_export_hex(stable_turn_id, "trace", trace_ordinal, 16),
                );
                trace_ordinal += 1;
            }
        }
        if let Some(span_id) = span.get("spanId").and_then(Value::as_str) {
            if !protected_span_ids.contains(span_id) && !span_id_map.contains_key(span_id) {
                span_id_map.insert(
                    span_id.to_string(),
                    stable_export_hex(stable_turn_id, "span", span_ordinal, 8),
                );
                span_ordinal += 1;
            }
        }
    });

    for_each_export_span_mut(body, |span| {
        if let Some(old_trace_id) = span
            .get("traceId")
            .and_then(Value::as_str)
            .map(str::to_string)
        {
            if let Some(stable_trace_id) = trace_id_map.get(&old_trace_id) {
                span["traceId"] = Value::String(stable_trace_id.clone());
            }
        }
        if let Some(old_span_id) = span
            .get("spanId")
            .and_then(Value::as_str)
            .map(str::to_string)
        {
            if let Some(stable_span_id) = span_id_map.get(&old_span_id) {
                span["spanId"] = Value::String(stable_span_id.clone());
            }
        }
        if let Some(old_parent_span_id) = span
            .get("parentSpanId")
            .and_then(Value::as_str)
            .map(str::to_string)
        {
            if let Some(stable_parent_span_id) = span_id_map.get(&old_parent_span_id) {
                span["parentSpanId"] = Value::String(stable_parent_span_id.clone());
            }
        }
    });
}

fn stable_export_hex(seed: &str, kind: &str, ordinal: usize, bytes: usize) -> String {
    let mut digest = Sha256::new();
    digest.update(b"cchooks.stop-export-identity.v1");
    digest.update([0]);
    digest.update(seed.as_bytes());
    digest.update([0]);
    digest.update(kind.as_bytes());
    digest.update([0]);
    digest.update(ordinal.to_be_bytes());
    digest
        .finalize()
        .iter()
        .take(bytes)
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn stable_compaction_span_id(
    anchor: &CompactionParentAnchor,
    compaction: &crate::common::transcript::CompactionEvent,
) -> String {
    let identity = if compaction.identity.is_empty() {
        format!("{}:{}", compaction.sequence, compaction.timestamp)
    } else {
        compaction.identity.clone()
    };
    let seed = format!("{}\0{}", anchor.turn_id, identity);
    stable_export_hex(&seed, "compaction", 0, 8)
}

fn for_each_export_span(body: &Value, mut visit: impl FnMut(&Value)) {
    let Some(resource_spans) = body.get("resourceSpans").and_then(Value::as_array) else {
        return;
    };
    for resource_span in resource_spans {
        let Some(scope_spans) = resource_span.get("scopeSpans").and_then(Value::as_array) else {
            continue;
        };
        for scope_span in scope_spans {
            let Some(spans) = scope_span.get("spans").and_then(Value::as_array) else {
                continue;
            };
            for span in spans {
                visit(span);
            }
        }
    }
}

fn export_span_string_attr(span: &Value, key: &str) -> Option<String> {
    span.get("attributes")
        .and_then(Value::as_array)
        .and_then(|attributes| {
            attributes.iter().find_map(|attribute| {
                if attribute.get("key").and_then(Value::as_str) != Some(key) {
                    return None;
                }
                attribute
                    .get("value")
                    .and_then(|value| value.get("stringValue"))
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
        })
}

fn is_root_chat_export_span(span: &Value) -> bool {
    export_span_string_attr(span, INTERNAL_META_ROOT_KEY).as_deref()
        == Some(INTERNAL_META_ROOT_VALUE)
        || export_span_string_attr(span, "gen_ai.span.kind").as_deref() == Some("CHAT")
}

fn for_each_export_span_mut(body: &mut Value, mut visit: impl FnMut(&mut Value)) {
    let Some(resource_spans) = body.get_mut("resourceSpans").and_then(Value::as_array_mut) else {
        return;
    };
    for resource_span in resource_spans {
        let Some(scope_spans) = resource_span
            .get_mut("scopeSpans")
            .and_then(Value::as_array_mut)
        else {
            continue;
        };
        for scope_span in scope_spans {
            let Some(spans) = scope_span.get_mut("spans").and_then(Value::as_array_mut) else {
                continue;
            };
            for span in spans {
                visit(span);
            }
        }
    }
}

fn apply_stored_tool_identity(
    span: &mut OtelSpan,
    tool_use_id: &str,
    context: Option<&StoredTraceContext>,
) -> bool {
    if tool_use_id.is_empty() {
        return true;
    }
    let Some(context) = context else {
        return true;
    };
    apply_stored_span_identity(
        span,
        tool_use_id,
        &context.emitted_tool_use_ids,
        &context.tool_span_ids,
    )
}

fn apply_stored_generation_identity(
    span: &mut OtelSpan,
    message_id: &str,
    context: Option<&StoredTraceContext>,
) -> bool {
    if message_id.is_empty() {
        return true;
    }
    let Some(context) = context else {
        return true;
    };
    apply_stored_span_identity(
        span,
        message_id,
        &context.accounted_generation_ids,
        &context.generation_span_ids,
    )
}

fn stored_generation_is_accounted(message_id: &str, context: Option<&StoredTraceContext>) -> bool {
    !message_id.is_empty()
        && context.is_some_and(|context| {
            context
                .accounted_generation_ids
                .iter()
                .any(|id| id == message_id)
        })
}

fn stored_tool_is_accounted(tool_use_id: &str, context: Option<&StoredTraceContext>) -> bool {
    !tool_use_id.is_empty()
        && context.is_some_and(|context| {
            context
                .emitted_tool_use_ids
                .iter()
                .any(|id| id == tool_use_id)
        })
}

fn apply_stored_span_identity(
    span: &mut OtelSpan,
    identity: &str,
    accounted_ids: &[String],
    span_ids: &HashMap<String, String>,
) -> bool {
    if accounted_ids.iter().any(|id| id == identity) {
        return false;
    }
    if let Some(span_id) = span_ids.get(identity) {
        span.span_id = span_id.clone();
    }
    true
}

fn resolve_chat_id(turn: &Turn, trace_id: &str) -> String {
    turn.chat_id
        .as_deref()
        .filter(|value| !value.is_empty())
        .unwrap_or(trace_id)
        .to_string()
}

/// Generate a deterministic span_id from tool_use_id using the same hash as
/// ToolContextHandler, so the span_id in the trace matches the parent_span_id
/// injected into tool scripts via TRACEPARENT.
#[cfg(all(feature = "tool_context", not(windows)))]
fn deterministic_tool_span_id(tool_use_id: &str) -> String {
    if !tool_context_enabled() {
        return otel_span_id();
    }
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut h = DefaultHasher::new();
    tool_use_id.hash(&mut h);
    format!("{:016x}", h.finish())
}

#[cfg(not(all(feature = "tool_context", not(windows))))]
fn deterministic_tool_span_id(_tool_use_id: &str) -> String {
    otel_span_id()
}

#[allow(clippy::too_many_arguments)]
fn build_root_span(
    trace_id: &str,
    span_id: &str,
    parent_span_id: &str,
    turn: &Turn,
    session_id: &str,
    chat_id: &str,
    username: &str,
    now_ns: &str,
    turn_number: usize,
    _skill_name: &Option<String>,
    cwd: &str,
) -> OtelSpan {
    let start_ns = if !turn.user_timestamp.is_empty() {
        iso_to_nanos(&turn.user_timestamp)
    } else {
        now_ns.to_string()
    };

    let trace_name = if turn.cancelled {
        format!("Turn {} (Cancelled)", turn_number)
    } else {
        format!("Turn {}", turn_number)
    };

    // Trace input/output in OpenAI API message format (JSON string)
    let (trace_input, trace_output) = if is_privacy_mode() {
        ("[REDACTED]".to_string(), "[REDACTED]".to_string())
    } else {
        let ti =
            serialize_messages_observed(&build_input_messages(turn), 0, "gen_ai.input.messages");
        let to =
            serialize_messages_observed(&build_output_messages(turn), 0, "gen_ai.output.messages");
        (ti, to)
    };

    let mut attributes = vec![
        OtelAttribute::string(INTERNAL_META_ROOT_KEY, INTERNAL_META_ROOT_VALUE),
        OtelAttribute::string("gen_ai.span.kind", "CHAT"),
        OtelAttribute::string("gen_ai.operation.name", "invoke_agent"),
        OtelAttribute::string("gen_ai.conversation.id", session_id),
        OtelAttribute::string("gen_ai.chat.id", chat_id),
        OtelAttribute::string("agentic.runtime.name", "claude-code"),
        OtelAttribute::int_as_string("agentic.turn.number", turn_number as i64),
        OtelAttribute::string("agentic.turn.trigger", turn.trigger.as_str()),
        OtelAttribute::string("ant.username", username),
        OtelAttribute::string("user.id", username),
    ];

    if !cwd.is_empty() {
        attributes.push(OtelAttribute::string("agentic.runtime.cwd", cwd));
    }

    if !trace_input.is_empty() {
        attributes.push(OtelAttribute::string("gen_ai.input.messages", &trace_input));
    }
    if !trace_output.is_empty() {
        attributes.push(OtelAttribute::string(
            "gen_ai.output.messages",
            &trace_output,
        ));
    }

    // Add custom env metadata as plain attributes
    let custom_metadata = build_custom_metadata();
    for (key, value) in &custom_metadata {
        let value_str = match value {
            Value::String(s) => s.clone(),
            other => serde_json::to_string(other).unwrap_or_default(),
        };
        attributes.push(OtelAttribute::string(format!("custom.{}", key), &value_str));
    }

    let root_error = if turn.cancelled {
        Some((
            "cancelled".to_string(),
            "turn cancelled by user".to_string(),
        ))
    } else if let Some(err) = &turn.terminal_llm_error {
        Some((err.error_type.clone(), err.message.clone()))
    } else {
        turn.assistants
            .iter()
            .find_map(|a| detect_llm_error(&a.stop_reason))
    };
    if let Some((ref err_type, _)) = root_error {
        attributes.push(OtelAttribute::string("error.type", err_type));
    }
    let status = if let Some((_, ref err_msg)) = root_error {
        OtelStatus::error(err_msg)
    } else {
        OtelStatus::ok()
    };

    OtelSpan {
        trace_id: trace_id.to_string(),
        span_id: span_id.to_string(),
        parent_span_id: parent_span_id.to_string(),
        name: trace_name,
        kind: SPAN_KIND_SERVER,
        start_time_unix_nano: start_ns,
        end_time_unix_nano: now_ns.to_string(),
        attributes,
        status,
    }
}

/// Build a turn span (child of root).
#[allow(dead_code)]
fn build_turn_span(
    trace_id: &str,
    parent_span_id: &str,
    turn: &Turn,
    turn_index: usize,
    now_ns: &str,
    username: &str,
) -> (String, OtelSpan) {
    let span_id = otel_span_id();
    let start_ns = if !turn.user_timestamp.is_empty() {
        iso_to_nanos(&turn.user_timestamp)
    } else {
        now_ns.to_string()
    };

    let (_user_text_truncated, _) = truncate_text_observed(&turn.user_text, "gen_ai.user.message");

    let attributes = vec![
        OtelAttribute::string("ant.username", username),
        OtelAttribute::string("user.id", username),
        OtelAttribute::int_as_string("agentic.turn.index", turn_index as i64),
    ];

    let span = OtelSpan {
        trace_id: trace_id.to_string(),
        span_id: span_id.clone(),
        parent_span_id: parent_span_id.to_string(),
        name: format!("turn-{}", turn_index + 1),
        kind: SPAN_KIND_INTERNAL,
        start_time_unix_nano: start_ns,
        end_time_unix_nano: now_ns.to_string(),
        attributes,
        status: OtelStatus::ok(),
    };

    (span_id, span)
}

/// Build a generation span (child of turn span).
/// Uses gen_ai.* attributes so Langfuse auto-detects it as a generation.
#[allow(clippy::too_many_arguments)]
fn build_generation_span(
    trace_id: &str,
    parent_span_id: &str,
    turn: &Turn,
    turn_index: usize,
    assistant: &crate::common::transcript::AssistantMessage,
    assistant_index: usize,
    now_ns: &str,
    username: &str,
    session_id: &str,
    chat_id: &str,
    timing: &TimingData,
) -> (String, OtelSpan) {
    let span_id = otel_span_id();
    let terminal_error = terminal_error_for_assistant(turn, assistant);
    let is_terminal_synthetic = terminal_error.is_some();
    let request_model = terminal_error
        .and_then(|err| err.request_model.as_deref())
        .unwrap_or(&assistant.model);

    // Compute precise generation timing from tool boundaries when timing data is available.
    //
    // Timeline:  ... [prev_tool post_ts] → [LLM thinking] → [this_tool pre_ts] ...
    //
    // - start: previous assistant's last tool post_ts, or fallback to assistant.timestamp
    // - end: this assistant's first tool pre_ts, or fallback to assistant.end_timestamp
    let (timing_start, timing_end) = compute_generation_timing(turn, assistant_index, timing);

    let start_ns = timing_start.unwrap_or_else(|| {
        if assistant_index == 0 && !turn.user_timestamp.is_empty() {
            iso_to_nanos(&turn.user_timestamp)
        } else if !assistant.timestamp.is_empty() {
            iso_to_nanos(&assistant.timestamp)
        } else {
            now_ns.to_string()
        }
    });

    // Generation-level input: accumulated context up to this assistant
    // (user message + all prior assistant responses and tool results)
    // Delta is cheap (O(1) per generation); always build it.
    let (gen_input, gen_output, gen_delta) = if is_privacy_mode() {
        (
            "[REDACTED]".to_string(),
            "[REDACTED]".to_string(),
            "[REDACTED]".to_string(),
        )
    } else if is_terminal_synthetic {
        let delta = build_generation_delta_input(turn, assistant_index);
        let gd = serialize_messages_observed(&delta, 0, "gen_ai.input.messages.delta");
        let gi = if use_delta_only(turn) {
            gd.clone()
        } else {
            let accumulated = build_generation_accumulated_input(turn, assistant_index);
            serialize_messages_observed(
                &accumulated,
                delta.len(),
                "gen_ai.input.messages.accumulated",
            )
        };
        (gi, String::new(), gd)
    } else {
        let delta = build_generation_delta_input(turn, assistant_index);
        let delta_count = delta.len();
        let gd = serialize_messages_observed(&delta, 0, "gen_ai.input.messages.delta");
        // Accumulated is O(n) per generation → O(n²) across the turn. Skip it for large
        // turns (auto mode) or when the operator explicitly asked for delta-only.
        let gi = if use_delta_only(turn) {
            gd.clone()
        } else {
            let accumulated = build_generation_accumulated_input(turn, assistant_index);
            serialize_messages_observed(
                &accumulated,
                delta_count,
                "gen_ai.input.messages.accumulated",
            ) // protect delta messages
        };
        let go = {
            let mut msg = crate::common::message_format::assistant_to_openai_message_pub(assistant);
            if !assistant.stop_reason.is_empty() {
                msg.finish_reason = Some(assistant.stop_reason.clone());
            }
            serialize_messages_observed(&[msg], 0, "gen_ai.output.messages")
        };
        (gi, go, gd)
    };

    let mut attributes = vec![
        OtelAttribute::string("gen_ai.span.kind", "LLM"),
        OtelAttribute::string("gen_ai.operation.name", "chat"),
        OtelAttribute::string("gen_ai.request.model", request_model),
        OtelAttribute::string("gen_ai.conversation.id", session_id),
        OtelAttribute::string("gen_ai.chat.id", chat_id),
        OtelAttribute::string("gen_ai.input.messages", &gen_input),
        OtelAttribute::string("ant.username", username),
        OtelAttribute::string("user.id", username),
        OtelAttribute::int_as_string("agentic.turn.index", turn_index as i64),
        OtelAttribute::int_as_string("agentic.response.index", assistant_index as i64),
        OtelAttribute::int_as_string("agentic.tool.count", assistant.tool_uses.len() as i64),
    ];
    if !gen_output.is_empty() {
        attributes.push(OtelAttribute::string("gen_ai.output.messages", &gen_output));
    }
    if !use_delta_only(turn) {
        attributes.push(OtelAttribute::string("agentic.input.delta", &gen_delta));
    }
    if let Some(provider) = agentic_otel_common::extract_provider_name(request_model) {
        attributes.push(OtelAttribute::string("gen_ai.provider.name", provider));
    }

    // response.model = request.model for Claude Code (same model responds)
    if !is_terminal_synthetic && !assistant.model.is_empty() && assistant.model != "claude" {
        attributes.push(OtelAttribute::string(
            "gen_ai.response.model",
            &assistant.model,
        ));
    }
    if !is_terminal_synthetic && !assistant.message_id.is_empty() {
        attributes.push(OtelAttribute::string(
            "gen_ai.response.id",
            &assistant.message_id,
        ));
    }
    if !is_terminal_synthetic && !assistant.stop_reason.is_empty() {
        // OTel semconv uses an array; wrap single value in JSON array format
        attributes.push(OtelAttribute::string(
            "gen_ai.response.finish_reasons",
            format!("[\"{}\"]", assistant.stop_reason),
        ));
    }

    // gen_ai.usage.input_tokens SHOULD include all input token types (semconv [13])
    if let Some(total_input) = assistant.usage.input_total() {
        attributes.push(OtelAttribute::int_as_string(
            "gen_ai.usage.input_tokens",
            total_input,
        ));
    } else if assistant.is_synthetic {
        attributes.push(OtelAttribute::int_as_string("gen_ai.usage.input_tokens", 0));
        attributes.push(OtelAttribute::string(
            "agentic.usage.input_tokens.missing",
            "true",
        ));
    }
    if let Some(output_tokens) = assistant.usage.output {
        attributes.push(OtelAttribute::int_as_string(
            "gen_ai.usage.output_tokens",
            output_tokens,
        ));
    } else if assistant.is_synthetic {
        attributes.push(OtelAttribute::int_as_string(
            "gen_ai.usage.output_tokens",
            0,
        ));
        attributes.push(OtelAttribute::string(
            "agentic.usage.output_tokens.missing",
            "true",
        ));
    }
    // cache_creation_input_tokens / cache_read_input_tokens 不单独上报为 OTel 属性，
    // 已聚合到上方 gen_ai.usage.input_tokens 中（= input + cache_creation + cache_read）。

    // Use precise timing end if available, otherwise fall back to transcript timestamps.
    let end_ns = timing_end.unwrap_or_else(|| {
        if !assistant.end_timestamp.is_empty() {
            iso_to_nanos(&assistant.end_timestamp)
        } else {
            now_ns.to_string()
        }
    });

    // Rule 5: agentic.llm.duration_ms
    let start_u128 = start_ns.parse::<u128>().unwrap_or(0);
    let end_u128 = end_ns.parse::<u128>().unwrap_or(0);
    let llm_duration_ms = if end_u128 > start_u128 {
        (end_u128 - start_u128) / 1_000_000
    } else {
        0
    };
    attributes.push(OtelAttribute::int_as_string(
        "agentic.llm.duration_ms",
        llm_duration_ms as i64,
    ));

    // Rule 7: detect LLM errors from stop_reason
    let llm_error = detect_llm_error(&assistant.stop_reason);
    if let Some((ref err_type, _)) = llm_error {
        attributes.push(OtelAttribute::string("error.type", err_type));
    }

    // Handle synthetic (isApiErrorMessage) assistant — API retries exhausted
    if let Some(err) = terminal_error {
        attributes.push(OtelAttribute::string("agentic.llm.is_synthetic", "true"));
        attributes.push(OtelAttribute::string(
            "agentic.llm.error.type",
            &err.error_type,
        ));
        attributes.push(OtelAttribute::string(
            "agentic.llm.error.message",
            &err.message,
        ));
        if let Some(status_code) = err.status_code {
            attributes.push(OtelAttribute::int_as_string(
                "agentic.llm.error.status_code",
                status_code,
            ));
        }
        attributes.push(OtelAttribute::string("error.type", &err.error_type));
        if !attributes
            .iter()
            .any(|attr| attr.key == "agentic.usage.input_tokens.missing")
        {
            attributes.push(OtelAttribute::string(
                "agentic.usage.input_tokens.missing",
                "true",
            ));
        }
        if !attributes
            .iter()
            .any(|attr| attr.key == "agentic.usage.output_tokens.missing")
        {
            attributes.push(OtelAttribute::string(
                "agentic.usage.output_tokens.missing",
                "true",
            ));
        }
    } else if assistant.is_synthetic {
        attributes.push(OtelAttribute::string("agentic.llm.is_synthetic", "true"));
        attributes.push(OtelAttribute::string("agentic.llm.error.type", "api_error"));
        attributes.push(OtelAttribute::string("error.type", "api_error"));
    }

    let status = if let Some(err) = terminal_error {
        OtelStatus::error(&err.message)
    } else if assistant.is_synthetic {
        OtelStatus::error("API retries exhausted")
    } else if let Some((ref err_type, ref err_msg)) = llm_error {
        OtelStatus::error(format!("{}: {}", err_type, err_msg))
    } else {
        OtelStatus::ok()
    };

    let name = if request_model.is_empty() {
        format!("chat unknown-{}", assistant_index + 1)
    } else {
        format!("chat {}", request_model)
    };

    let span = OtelSpan {
        trace_id: trace_id.to_string(),
        span_id: span_id.clone(),
        parent_span_id: parent_span_id.to_string(),
        name,
        kind: SPAN_KIND_INTERNAL,
        start_time_unix_nano: start_ns,
        end_time_unix_nano: end_ns,
        attributes,
        status,
    };

    (span_id, span)
}

/// Compute precise generation span start/end from tool timing boundaries.
///
/// Returns `(Option<start_ns>, Option<end_ns>)` — None means no timing data available.
///
/// - **start**: previous assistant's last tool `post_ts` (LLM starts thinking after tool finishes)
/// - **end**: this assistant's first tool `pre_ts` (LLM stops thinking when tool starts)
///
/// For the first assistant, start falls back to transcript. For assistants with no tools, end
/// falls back to transcript.
fn compute_generation_timing(
    turn: &Turn,
    assistant_index: usize,
    timing: &TimingData,
) -> (Option<String>, Option<String>) {
    if timing.is_empty() {
        return (None, None);
    }

    // Start: max end_ns across ALL previous assistant's tools (handles concurrent tools)
    // For the first assistant (index 0), use user_timestamp — that's when the LLM
    // started processing, not assistant.timestamp which is when the response arrived.
    let start = if assistant_index > 0 {
        let prev_assistant = &turn.assistants[assistant_index - 1];
        prev_assistant
            .tool_uses
            .iter()
            .filter_map(|tu| timing.get(&tu.id).and_then(|t| t.end_ns.as_deref()))
            .max()
            .map(|s| s.to_string())
    } else if !turn.user_timestamp.is_empty() {
        Some(iso_to_nanos(&turn.user_timestamp))
    } else {
        None
    };

    // End: min start_ns across ALL current assistant's tools (handles concurrent tools)
    let end = {
        let assistant = &turn.assistants[assistant_index];
        assistant
            .tool_uses
            .iter()
            .filter_map(|tu| {
                timing.get(&tu.id).and_then(|t| {
                    if !t.start_ns.is_empty() {
                        Some(t.start_ns.as_str())
                    } else {
                        None
                    }
                })
            })
            .min()
            .map(|s| s.to_string())
    };

    (start, end)
}

/// Build tool call spans (siblings of generation span, children of root).
#[allow(clippy::too_many_arguments, clippy::type_complexity)]
fn build_tool_spans(
    trace_id: &str,
    parent_span_id: &str,
    tool_uses: &[ToolUse],
    tool_results: &[ToolResultItem],
    assistant_ts_ns: &str,
    now_ns: &str,
    username: &str,
    session_id: &str,
    chat_id: &str,
    skill_name: Option<&str>,
    parallel_group: Option<&str>,
    timing: &TimingData,
) -> Vec<OtelSpan> {
    let result_map: HashMap<&str, &ToolResultItem> = tool_results
        .iter()
        .map(|r| (r.tool_use_id.as_str(), r))
        .collect();

    tool_uses
        .iter()
        .map(|tool_use| {
            let span_id = deterministic_tool_span_id(&tool_use.id);
            let result_item = result_map.get(tool_use.id.as_str()).copied();
            let (raw_output, result_ts, result_agent_id, result_agent_type, result_is_async) =
                result_item
                    .map(|r| {
                        (
                            r.content.clone(),
                            r.timestamp.as_str(),
                            r.agent_id.as_deref(),
                            r.agent_type.as_deref(),
                            r.is_async,
                        )
                    })
                    .unwrap_or((Value::Null, "", None, None, false));

            // Use precise timing from hooks if available, otherwise fall back to transcript timestamps
            let (start_ns_final, end_ns) = if let Some(tool_timing) = timing.get(&tool_use.id) {
                let start = if !tool_timing.start_ns.is_empty() {
                    tool_timing.start_ns.clone()
                } else {
                    assistant_ts_ns.to_string()
                };
                let end = tool_timing.end_ns.clone().unwrap_or_else(|| {
                    if !result_ts.is_empty() {
                        iso_to_nanos(result_ts)
                    } else {
                        now_ns.to_string()
                    }
                });
                (start, end)
            } else {
                let end = if !result_ts.is_empty() {
                    iso_to_nanos(result_ts)
                } else {
                    now_ns.to_string()
                };
                (assistant_ts_ns.to_string(), end)
            };

            let (truncated_input, input_meta) =
                truncate_value_observed(&tool_use.input, "agentic.tool.input");
            let (truncated_output, output_meta) =
                truncate_value_observed(&raw_output, "agentic.tool.output");

            let (input_str, output_str) = if is_privacy_mode() {
                ("[REDACTED]".to_string(), "[REDACTED]".to_string())
            } else {
                let is = match &truncated_input {
                    Value::String(s) => s.clone(),
                    other => serde_json::to_string(other).unwrap_or_default(),
                };
                let os = match &truncated_output {
                    Value::String(s) => s.clone(),
                    other => serde_json::to_string(other).unwrap_or_default(),
                };
                (is, os)
            };

            // Rule 5: compute duration_ms
            let start_u128 = start_ns_final.parse::<u128>().unwrap_or(0);
            let end_u128 = end_ns.parse::<u128>().unwrap_or(0);
            let duration_ms = if end_u128 > start_u128 {
                (end_u128 - start_u128) / 1_000_000
            } else {
                0
            };

            // Rule 7: detect tool errors (transcript result + timing hook status)
            let tool_error = detect_tool_result_item_error(result_item, &raw_output);
            let timing_failed = timing
                .get(&tool_use.id)
                .and_then(|t| t.success)
                .is_some_and(|ok| !ok);
            let status = if let Some((ref err_type, ref err_msg)) = tool_error {
                OtelStatus::error(format!("{}: {}", err_type, err_msg))
            } else if timing_failed {
                OtelStatus::error("tool execution failed")
            } else {
                OtelStatus::ok()
            };

            // For Skill tool_uses, create an independent skill span instead of a tool span.
            // Extract skill name from tool_use.input["skill"].
            if tool_use.name == "Skill" {
                let skill_name = extract_skill_name_from_tool_input(&tool_use.input)
                    .unwrap_or_else(|| "unknown".to_string());
                let skill_id = if tool_use.id.is_empty() {
                    format!("skill-{}", skill_name)
                } else {
                    tool_use.id.clone()
                };

                let mut attributes = vec![
                    OtelAttribute::string("gen_ai.span.kind", "TOOL"),
                    OtelAttribute::string("gen_ai.operation.name", "execute_tool"),
                    OtelAttribute::string("gen_ai.conversation.id", session_id),
                    OtelAttribute::string("gen_ai.chat.id", chat_id),
                    OtelAttribute::string("gen_ai.tool.name", format!("skill {}", skill_name)),
                    OtelAttribute::string("gen_ai.tool.call.id", &tool_use.id),
                    OtelAttribute::string("gen_ai.tool.call.arguments", &input_str),
                    OtelAttribute::string("gen_ai.tool.call.result", &output_str),
                    OtelAttribute::string("ant.username", username),
                    OtelAttribute::string("user.id", username),
                    OtelAttribute::string("agentic.tool.type", "skill"),
                    OtelAttribute::string("agentic.tool.name", &skill_name),
                    OtelAttribute::string("agentic.tool.id", &skill_id),
                    OtelAttribute::int_as_string("agentic.tool.duration_ms", duration_ms as i64),
                ];
                // Rule 7: error.type attribute
                if let Some((ref err_type, _)) = tool_error {
                    attributes.push(OtelAttribute::string("error.type", err_type));
                } else if timing_failed {
                    attributes.push(OtelAttribute::string("error.type", "tool_error"));
                }
                // Rule 12: truncation metadata
                append_truncation_meta(&mut attributes, &input_meta, &output_meta);
                // Rule 13: parallel group
                if let Some(group_id) = parallel_group {
                    attributes.push(OtelAttribute::string(
                        "agentic.tool.parallel_group",
                        group_id,
                    ));
                }

                return OtelSpan {
                    trace_id: trace_id.to_string(),
                    span_id,
                    parent_span_id: parent_span_id.to_string(),
                    name: format!("skill {}", skill_name),
                    kind: SPAN_KIND_INTERNAL,
                    start_time_unix_nano: start_ns_final.clone(),
                    end_time_unix_nano: end_ns,
                    attributes,
                    status,
                };
            }

            // For non-Skill tools, create regular tool spans
            // Agent tool_use gets AGENT/invoke_agent; others get TOOL/execute_tool
            let effective_skill = skill_name;
            let info = parse_agentic_tool(&tool_use.name, &tool_use.id, effective_skill);
            let is_agent = info.tool_type == "subagent";
            // Async detection: v2.1.199+ marks async launch in toolUseResult
            // (isAsync / status=async_launched); older versions use tool input
            // run_in_background. Either source marks the agent as background.
            let is_background = is_agent
                && (tool_use
                    .input
                    .get("run_in_background")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false)
                    || result_is_async);
            let display_name = if is_agent {
                if is_background {
                    "agent AsyncAgent".to_string()
                } else {
                    "agent SyncAgent".to_string()
                }
            } else {
                tool_span_name(&info)
            };
            let (span_kind, op_name) = if is_agent {
                ("AGENT", "invoke_agent")
            } else {
                ("TOOL", "execute_tool")
            };

            let mut attributes = vec![
                OtelAttribute::string("gen_ai.span.kind", span_kind),
                OtelAttribute::string("gen_ai.operation.name", op_name),
                OtelAttribute::string("gen_ai.conversation.id", session_id),
                OtelAttribute::string("gen_ai.chat.id", chat_id),
                OtelAttribute::string("gen_ai.tool.name", &display_name),
                OtelAttribute::string("gen_ai.tool.call.id", &tool_use.id),
                OtelAttribute::string("gen_ai.tool.call.arguments", &input_str),
                OtelAttribute::string("gen_ai.tool.call.result", &output_str),
                OtelAttribute::string("ant.username", username),
                OtelAttribute::string("user.id", username),
                // Rule 5: duration_ms
                OtelAttribute::int_as_string("agentic.tool.duration_ms", duration_ms as i64),
            ];
            append_agentic_tool_attrs_typed(&mut attributes, &info);
            // Subagent: store agent_id/agent_type from toolUseResult metadata + mode
            if is_agent {
                let mode = if is_background { "async" } else { "sync" };
                attributes.push(OtelAttribute::string("agentic.subagent.mode", mode));
                if let Some(aid) = result_agent_id {
                    attributes.push(OtelAttribute::string("agentic.subagent.id", aid));
                }
                if let Some(atype) = result_agent_type {
                    attributes.push(OtelAttribute::string("agentic.subagent.type", atype));
                }
            }
            // Rule 7: error.type attribute
            if let Some((ref err_type, _)) = tool_error {
                attributes.push(OtelAttribute::string("error.type", err_type));
            } else if timing_failed {
                attributes.push(OtelAttribute::string("error.type", "tool_error"));
            }
            // Rule 12: truncation metadata
            append_truncation_meta(&mut attributes, &input_meta, &output_meta);
            // Rule 13: parallel group
            if let Some(group_id) = parallel_group {
                attributes.push(OtelAttribute::string(
                    "agentic.tool.parallel_group",
                    group_id,
                ));
            }

            OtelSpan {
                trace_id: trace_id.to_string(),
                span_id,
                parent_span_id: parent_span_id.to_string(),
                name: display_name,
                kind: SPAN_KIND_INTERNAL,
                start_time_unix_nano: start_ns_final,
                end_time_unix_nano: end_ns,
                attributes,
                status,
            }
        })
        .collect()
}

/// Build tool call spans from tool_results only (fallback when tool_uses is empty).
/// Prefers `result.tool_name` (from `toolUseResult.toolName` in newer Claude Code transcripts)
/// over the raw `tool_use_id` UUID, so Langfuse shows a readable tool name.
/// When tool_name is None, falls back to reading from transcript file to find tool name.
#[allow(clippy::too_many_arguments)]
fn build_tool_spans_from_results<F>(
    trace_id: &str,
    parent_span_id: &str,
    tool_results: &[ToolResultItem],
    start_time_ns: &str,
    now_ns: &str,
    username: &str,
    session_id: &str,
    chat_id: &str,
    skill_name: Option<&str>,
    lookup_tool_name: F,
) -> Vec<OtelSpan>
where
    F: Fn(&str) -> Option<String>,
{
    tool_results
        .iter()
        .map(|result| {
            let span_id = deterministic_tool_span_id(&result.tool_use_id);

            let (truncated_output, output_meta) =
                truncate_value_observed(&result.content, "agentic.tool.output");
            let output_str = if is_privacy_mode() {
                "[REDACTED]".to_string()
            } else {
                match &truncated_output {
                    Value::String(s) => s.clone(),
                    other => serde_json::to_string(other).unwrap_or_default(),
                }
            };

            // Prefer the tool name extracted from `toolUseResult.toolName` (newer Claude Code
            // transcript format). If not available, try the lookup function to find tool name
            // from the original transcript. Only fall back to "unknown" if both fail.
            let tool_name = result
                .tool_name
                .as_deref()
                .filter(|s| !s.is_empty())
                .map(|s| s.to_string())
                .or_else(|| lookup_tool_name(&result.tool_use_id))
                .unwrap_or_else(|| "unknown".to_string());

            let info = parse_agentic_tool(&tool_name, &result.tool_use_id, skill_name);
            let is_agent = info.tool_type == "subagent";
            // Results-only path has no tool input; async detection relies on
            // toolUseResult.isAsync / status=async_launched captured at parse time.
            let display_name = if is_agent {
                if result.is_async {
                    "agent AsyncAgent".to_string()
                } else {
                    "agent SyncAgent".to_string()
                }
            } else {
                tool_span_name(&info)
            };

            // Use tool_result timestamp as end time when available
            let end_ns = if !result.timestamp.is_empty() {
                iso_to_nanos(&result.timestamp)
            } else {
                now_ns.to_string()
            };

            // Rule 5: compute duration_ms
            let start_u128 = start_time_ns.parse::<u128>().unwrap_or(0);
            let end_u128 = end_ns.parse::<u128>().unwrap_or(0);
            let duration_ms = if end_u128 > start_u128 {
                (end_u128 - start_u128) / 1_000_000
            } else {
                0
            };

            // Rule 7: detect tool errors
            let tool_error = detect_tool_result_item_error(Some(result), &result.content);
            let status = if let Some((ref err_type, ref err_msg)) = tool_error {
                OtelStatus::error(format!("{}: {}", err_type, err_msg))
            } else {
                OtelStatus::ok()
            };

            let (span_kind, op_name) = if is_agent {
                ("AGENT", "invoke_agent")
            } else {
                ("TOOL", "execute_tool")
            };

            let mut attributes = vec![
                OtelAttribute::string("gen_ai.span.kind", span_kind),
                OtelAttribute::string("gen_ai.operation.name", op_name),
                OtelAttribute::string("gen_ai.conversation.id", session_id),
                OtelAttribute::string("gen_ai.chat.id", chat_id),
                OtelAttribute::string("gen_ai.tool.name", &display_name),
                OtelAttribute::string("gen_ai.tool.call.id", &result.tool_use_id),
                OtelAttribute::string(
                    "gen_ai.tool.call.arguments",
                    if is_privacy_mode() { "[REDACTED]" } else { "" },
                ),
                OtelAttribute::string("gen_ai.tool.call.result", &output_str),
                OtelAttribute::string("ant.username", username),
                OtelAttribute::string("user.id", username),
                // Rule 5: duration_ms
                OtelAttribute::int_as_string("agentic.tool.duration_ms", duration_ms as i64),
            ];
            append_agentic_tool_attrs_typed(&mut attributes, &info);
            // Subagent: store agent_id/agent_type from toolUseResult metadata + mode
            if is_agent {
                let mode = if result.is_async { "async" } else { "sync" };
                attributes.push(OtelAttribute::string("agentic.subagent.mode", mode));
                if let Some(ref aid) = result.agent_id {
                    attributes.push(OtelAttribute::string("agentic.subagent.id", aid));
                }
                if let Some(ref atype) = result.agent_type {
                    attributes.push(OtelAttribute::string("agentic.subagent.type", atype));
                }
            }
            // Rule 7: error.type attribute
            if let Some((ref err_type, _)) = tool_error {
                attributes.push(OtelAttribute::string("error.type", err_type));
            }
            // Rule 12: truncation metadata (no input_meta for results-only path)
            let empty_meta = json!({});
            append_truncation_meta(&mut attributes, &empty_meta, &output_meta);
            // Mark arguments as missing (result-only fallback, no tool_use available)
            attributes.push(OtelAttribute::string("agentic.tool.input.missing", "true"));

            OtelSpan {
                trace_id: trace_id.to_string(),
                span_id,
                parent_span_id: parent_span_id.to_string(),
                name: display_name,
                kind: SPAN_KIND_INTERNAL,
                start_time_unix_nano: start_time_ns.to_string(),
                end_time_unix_nano: end_ns,
                attributes,
                status,
            }
        })
        .collect()
}

/// Build api_error spans as siblings of generation spans (children of turn root).
/// Each span represents a failed LLM call attempt before a successful retry.
#[allow(clippy::too_many_arguments)]
fn build_api_error_spans(
    trace_id: &str,
    parent_span_id: &str,
    api_errors: &[crate::common::transcript::ApiError],
    recovery_timestamp_ns: &str,
    request_model: &str,
    username: &str,
    session_id: &str,
    chat_id: &str,
) -> Vec<OtelSpan> {
    api_errors
        .iter()
        .enumerate()
        .map(|(i, err)| {
            let start_ns = if !err.timestamp.is_empty() {
                iso_to_nanos(&err.timestamp)
            } else {
                recovery_timestamp_ns.to_string()
            };
            // end_time = next api_error's timestamp, or recovery_timestamp_ns for the last one
            let end_ns = api_errors
                .get(i + 1)
                .filter(|next| !next.timestamp.is_empty())
                .map(|next| iso_to_nanos(&next.timestamp))
                .unwrap_or_else(|| recovery_timestamp_ns.to_string());

            let duration_ms = {
                let start = start_ns.parse::<u128>().unwrap_or(0);
                let end = end_ns.parse::<u128>().unwrap_or(0);
                end.saturating_sub(start) / 1_000_000
            };
            let model = if request_model.is_empty() {
                "unknown"
            } else {
                request_model
            };
            let error_type = llm_error_type_from_value(&err.error);

            let mut attributes = vec![
                OtelAttribute::string("gen_ai.span.kind", "LLM"),
                OtelAttribute::string("gen_ai.operation.name", "chat"),
                OtelAttribute::string("gen_ai.request.model", model),
                OtelAttribute::string("gen_ai.conversation.id", session_id),
                OtelAttribute::string("gen_ai.chat.id", chat_id),
                OtelAttribute::string("agentic.llm.error.type", &error_type),
                OtelAttribute::int_as_string("agentic.llm.retry.attempt", err.retry_attempt as i64),
                OtelAttribute::int_as_string("agentic.llm.retry.max", err.max_retries as i64),
                OtelAttribute::int_as_string("agentic.llm.retry.delay_ms", err.retry_in_ms as i64),
                OtelAttribute::string("ant.username", username),
                OtelAttribute::string("user.id", username),
                OtelAttribute::int_as_string("agentic.llm.duration_ms", duration_ms as i64),
                OtelAttribute::int_as_string("gen_ai.usage.input_tokens", 0),
                OtelAttribute::int_as_string("gen_ai.usage.output_tokens", 0),
                OtelAttribute::string("agentic.usage.input_tokens.missing", "true"),
                OtelAttribute::string("agentic.usage.output_tokens.missing", "true"),
                OtelAttribute::string("error.type", &error_type),
            ];
            if let Some(provider) = agentic_otel_common::extract_provider_name(model) {
                attributes.push(OtelAttribute::string("gen_ai.provider.name", provider));
            }

            // Extract status code from error object
            if let Some(status) = err.error.get("status").and_then(|v| v.as_i64()) {
                attributes.push(OtelAttribute::int_as_string(
                    "agentic.llm.error.status_code",
                    status,
                ));
            }
            // Extract error message from nested error object
            let error_msg = if is_privacy_mode() {
                "[REDACTED]"
            } else {
                llm_error_message_from_value(&err.error)
            };
            attributes.push(OtelAttribute::string(
                "agentic.llm.error.message",
                error_msg,
            ));

            OtelSpan {
                trace_id: trace_id.to_string(),
                span_id: otel_span_id(),
                parent_span_id: parent_span_id.to_string(),
                name: "chat api_error".to_string(),
                kind: SPAN_KIND_INTERNAL,
                start_time_unix_nano: start_ns,
                end_time_unix_nano: end_ns,
                attributes,
                status: OtelStatus::error(error_msg),
            }
        })
        .collect()
}

/// Build a compaction span (child of turn root span).
/// Represents a context compression event per Rule 14 of goldenrules.
fn build_compaction_span(
    trace_id: &str,
    parent_span_id: &str,
    compaction: &crate::common::transcript::CompactionEvent,
    now_ns: &str,
    username: &str,
    session_id: &str,
    chat_id: &str,
) -> OtelSpan {
    let start_ns = if !compaction.timestamp.is_empty() {
        iso_to_nanos(&compaction.timestamp)
    } else {
        now_ns.to_string()
    };
    let end_ns = if !compaction.end_timestamp.is_empty() {
        iso_to_nanos(&compaction.end_timestamp)
    } else {
        now_ns.to_string()
    };

    let tokens_saved = compaction.pre_tokens.saturating_sub(compaction.post_tokens);
    let ratio = if compaction.pre_tokens > 0 {
        format!(
            "{:.2}",
            compaction.post_tokens as f64 / compaction.pre_tokens as f64
        )
    } else {
        "0.00".to_string()
    };
    let duration_ms = {
        let s = start_ns.parse::<u64>().unwrap_or(0);
        let e = end_ns.parse::<u64>().unwrap_or(0);
        e.saturating_sub(s) / 1_000_000
    };

    let status = if compaction.post_tokens == 0 || compaction.post_tokens < compaction.pre_tokens {
        OtelStatus::ok()
    } else {
        OtelStatus::error("compaction did not reduce tokens")
    };

    OtelSpan {
        trace_id: trace_id.to_string(),
        span_id: otel_span_id(),
        parent_span_id: parent_span_id.to_string(),
        name: "compaction".to_string(),
        kind: SPAN_KIND_INTERNAL,
        start_time_unix_nano: start_ns,
        end_time_unix_nano: end_ns,
        attributes: vec![
            OtelAttribute::string("gen_ai.span.kind", "COMPACTION"),
            OtelAttribute::string("gen_ai.operation.name", "execute_tool"),
            OtelAttribute::string("gen_ai.conversation.id", session_id),
            OtelAttribute::string("gen_ai.chat.id", chat_id),
            OtelAttribute::string("gen_ai.tool.name", "compaction"),
            OtelAttribute::string(
                "gen_ai.tool.call.id",
                format!("compact-{}", compaction.sequence),
            ),
            OtelAttribute::string(
                "gen_ai.tool.call.arguments",
                if is_privacy_mode() {
                    "[REDACTED]".to_string()
                } else {
                    format!(
                        "{{\"trigger\":\"{}\",\"pre_tokens\":{}}}",
                        compaction.trigger, compaction.pre_tokens
                    )
                },
            ),
            OtelAttribute::string(
                "gen_ai.tool.call.result",
                if is_privacy_mode() {
                    "[REDACTED]".to_string()
                } else {
                    format!(
                        "{{\"post_tokens\":{},\"tokens_saved\":{},\"ratio\":{}}}",
                        compaction.post_tokens, tokens_saved, ratio
                    )
                },
            ),
            OtelAttribute::string("agentic.tool.type", "system"),
            OtelAttribute::int_as_string("agentic.tool.duration_ms", duration_ms as i64),
            OtelAttribute::string("agentic.compaction.trigger", &compaction.trigger),
            OtelAttribute::int_as_string(
                "agentic.compaction.tokens.before",
                compaction.pre_tokens as i64,
            ),
            OtelAttribute::int_as_string(
                "agentic.compaction.tokens.after",
                compaction.post_tokens as i64,
            ),
            OtelAttribute::int_as_string("agentic.compaction.tokens.saved", tokens_saved as i64),
            OtelAttribute::int_as_string("agentic.compaction.sequence", compaction.sequence as i64),
            OtelAttribute::string("agentic.compaction.ratio", &ratio),
            OtelAttribute::string("ant.username", username),
            OtelAttribute::string("user.id", username),
        ],
        status,
    }
}

// --- Subagent child span expansion ---

/// Derive an opaque, replay-stable conversation ID for one Claude Code subagent.
///
/// The parent session scopes agent IDs that may be reused by another session, while
/// hashing prevents either source identifier from being exposed through telemetry.
pub fn derive_subagent_conversation_id(parent_session_id: &str, agent_id: &str) -> String {
    let mut digest = Sha256::new();
    digest.update(b"cchooks.subagent-conversation.v1");
    for value in [parent_session_id, agent_id] {
        digest.update((value.len() as u64).to_be_bytes());
        digest.update(value.as_bytes());
    }
    let hash = digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    format!("cc-subagent-{hash}")
}

/// Apply the shared async-subagent semantic overlay used by live export and replay.
pub fn apply_async_subagent_semantics(
    request: &mut Value,
    parent_session_id: &str,
    agent_id: &str,
    agent_type: &str,
    description: &str,
) {
    let conversation_id = derive_subagent_conversation_id(parent_session_id, agent_id);
    let Some(resource_spans) = request
        .get_mut("resourceSpans")
        .and_then(Value::as_array_mut)
    else {
        return;
    };

    for resource_span in resource_spans {
        if let Some(attributes) = resource_span
            .pointer_mut("/resource/attributes")
            .and_then(Value::as_array_mut)
        {
            upsert_json_string_attr(attributes, "agentic.subagent", "true");
            upsert_json_string_attr(attributes, "agentic.subagent.mode", "async");
            upsert_json_string_attr(attributes, "agentic.subagent.id", agent_id);
            upsert_json_string_attr(attributes, "agentic.subagent.type", agent_type);
            if !description.is_empty() {
                upsert_json_string_attr(attributes, "agentic.subagent.description", description);
            }
        }

        let Some(scope_spans) = resource_span
            .get_mut("scopeSpans")
            .and_then(Value::as_array_mut)
        else {
            continue;
        };
        for scope_span in scope_spans {
            let Some(spans) = scope_span.get_mut("spans").and_then(Value::as_array_mut) else {
                continue;
            };

            // A completed async-agent transcript may itself contain synchronous
            // subagent expansion. Keep the nested invocation metadata and every
            // descendant's already-derived conversation ID intact. Only the
            // outer async agent's own spans receive this overlay.
            let nested_invocation_ids = spans
                .iter()
                .filter(|span| {
                    export_span_string_attr(span, "agentic.tool.type").as_deref()
                        == Some("subagent")
                })
                .filter_map(|span| span.get("spanId").and_then(Value::as_str))
                .map(str::to_string)
                .collect::<HashSet<_>>();
            let mut nested_descendant_ids = HashSet::new();
            loop {
                let before = nested_descendant_ids.len();
                for span in spans.iter() {
                    let Some(parent_id) = span.get("parentSpanId").and_then(Value::as_str) else {
                        continue;
                    };
                    if nested_invocation_ids.contains(parent_id)
                        || nested_descendant_ids.contains(parent_id)
                    {
                        if let Some(span_id) = span.get("spanId").and_then(Value::as_str) {
                            nested_descendant_ids.insert(span_id.to_string());
                        }
                    }
                }
                if nested_descendant_ids.len() == before {
                    break;
                }
            }

            for span in spans {
                let is_root = span
                    .get("parentSpanId")
                    .and_then(Value::as_str)
                    .is_none_or(str::is_empty);
                if is_root {
                    span["name"] = Value::String("Turn AsyncAgent".to_string());
                }
                let is_nested_descendant = span
                    .get("spanId")
                    .and_then(Value::as_str)
                    .is_some_and(|span_id| nested_descendant_ids.contains(span_id));
                if is_nested_descendant {
                    continue;
                }
                let is_nested_invocation = export_span_string_attr(span, "agentic.tool.type")
                    .as_deref()
                    == Some("subagent");
                if let Some(attributes) = span.get_mut("attributes").and_then(Value::as_array_mut) {
                    upsert_json_string_attr(attributes, "gen_ai.conversation.id", &conversation_id);
                    if !is_nested_invocation {
                        upsert_json_string_attr(attributes, "agentic.subagent", "true");
                        upsert_json_string_attr(attributes, "agentic.subagent.mode", "async");
                        upsert_json_string_attr(attributes, "agentic.subagent.id", agent_id);
                        upsert_json_string_attr(attributes, "agentic.subagent.type", agent_type);
                        if !description.is_empty() {
                            upsert_json_string_attr(
                                attributes,
                                "agentic.subagent.description",
                                description,
                            );
                        }
                    }
                }
            }
        }
    }
}

fn upsert_json_string_attr(attributes: &mut Vec<Value>, key: &str, value: &str) {
    if let Some(attribute) = attributes
        .iter_mut()
        .find(|attribute| attribute.get("key").and_then(Value::as_str) == Some(key))
    {
        attribute["value"] = json!({"stringValue": value});
    } else {
        attributes.push(json!({"key": key, "value": {"stringValue": value}}));
    }
}

/// Extract agent_id from a tool result text.
/// Looks for patterns like "agentId: a87d5d293f61d5ecd" in the result string.
pub fn extract_agent_id_from_result(result_text: &str) -> Option<String> {
    // Pattern 1: "agentId: <hex>" (standard Claude Code output)
    if let Some(pos) = result_text.find("agentId: ") {
        let after = &result_text[pos + 9..];
        let id: String = after
            .chars()
            .take_while(|c| c.is_ascii_alphanumeric())
            .collect();
        if !id.is_empty() {
            return Some(id);
        }
    }
    // Pattern 2: "agent-<hex>" (agent ID with prefix)
    if let Some(pos) = result_text.find("agent-") {
        let after = &result_text[pos + 6..];
        let id: String = after
            .chars()
            .take_while(|c| c.is_ascii_alphanumeric())
            .collect();
        if !id.is_empty() {
            return Some(id);
        }
    }
    None
}

/// Derive the subagents directory from the parent transcript path.
/// Parent transcript: .../720028d7-xxx.jsonl → subagent dir: .../720028d7-xxx/subagents/
fn subagent_dir_from_transcript(transcript_path: &str) -> Option<PathBuf> {
    if transcript_path.is_empty() {
        return None;
    }
    let base = transcript_path.strip_suffix(".jsonl")?;
    let dir = PathBuf::from(base).join("subagents");
    if dir.is_dir() {
        Some(dir)
    } else {
        None
    }
}

/// Build child spans for a subagent's turns, all parented under the given parent_span_id.
/// Produces generation and tool spans matching the standard turn structure.
#[allow(clippy::too_many_arguments)]
fn build_subagent_child_spans(
    trace_id: &str,
    parent_span_id: &str,
    subagent_turns: &[Turn],
    username: &str,
    conversation_id: &str,
    parent_start_ns: &str,
    parent_end_ns: &str,
    agent_type: &str,
    description: &str,
    timing: &TimingData,
) -> Vec<OtelSpan> {
    let mut spans = Vec::new();

    for turn in subagent_turns {
        let chat_id = resolve_chat_id(turn, trace_id);
        let all_tool_results: Vec<ToolResultItem> = turn
            .tool_results
            .iter()
            .flat_map(|tr| tr.items.iter().cloned())
            .collect();

        for (ai, assistant) in turn.assistants.iter().enumerate() {
            // Generation span
            let gen_span_id = otel_span_id();
            let (timing_start, timing_end) = compute_generation_timing(turn, ai, timing);
            let gen_start = timing_start.unwrap_or_else(|| {
                if ai == 0 && !turn.user_timestamp.is_empty() {
                    iso_to_nanos(&turn.user_timestamp)
                } else if !assistant.timestamp.is_empty() {
                    iso_to_nanos(&assistant.timestamp)
                } else {
                    parent_start_ns.to_string()
                }
            });
            let gen_end = timing_end.unwrap_or_else(|| {
                if !assistant.end_timestamp.is_empty() {
                    iso_to_nanos(&assistant.end_timestamp)
                } else {
                    parent_end_ns.to_string()
                }
            });

            // Build input/output messages (same logic as build_generation_span).
            // Delta is cheap; accumulated is the O(n²) hot path — skip it under
            // delta/auto mode (see use_delta_only).
            let (gen_input, gen_output, gen_delta) = if is_privacy_mode() {
                (
                    "[REDACTED]".to_string(),
                    "[REDACTED]".to_string(),
                    "[REDACTED]".to_string(),
                )
            } else {
                let delta = build_generation_delta_input(turn, ai);
                let delta_count = delta.len();
                let gd = serialize_messages_observed(&delta, 0, "gen_ai.input.messages.delta");
                let gi = if use_delta_only(turn) {
                    gd.clone()
                } else {
                    let accumulated = build_generation_accumulated_input(turn, ai);
                    serialize_messages_observed(
                        &accumulated,
                        delta_count,
                        "gen_ai.input.messages.accumulated",
                    )
                };
                let go = {
                    let mut msg =
                        crate::common::message_format::assistant_to_openai_message_pub(assistant);
                    if !assistant.stop_reason.is_empty() {
                        msg.finish_reason = Some(assistant.stop_reason.clone());
                    }
                    serialize_messages_observed(&[msg], 0, "gen_ai.output.messages")
                };
                (gi, go, gd)
            };

            let mut gen_attrs = vec![
                OtelAttribute::string("gen_ai.span.kind", "LLM"),
                OtelAttribute::string("gen_ai.operation.name", "chat"),
                OtelAttribute::string("gen_ai.conversation.id", conversation_id),
                OtelAttribute::string("gen_ai.chat.id", &chat_id),
                OtelAttribute::string("gen_ai.request.model", &assistant.model),
                OtelAttribute::string("ant.username", username),
                OtelAttribute::string("user.id", username),
                OtelAttribute::int_as_string("agentic.response.index", ai as i64),
                OtelAttribute::string("agentic.subagent", "true"),
                OtelAttribute::string("agentic.subagent.type", agent_type),
            ];
            if let Some(provider) = agentic_otel_common::extract_provider_name(&assistant.model) {
                gen_attrs.push(OtelAttribute::string("gen_ai.provider.name", provider));
            }

            // Input/output messages
            if !gen_input.is_empty() {
                gen_attrs.push(OtelAttribute::string("gen_ai.input.messages", &gen_input));
            }
            if !gen_output.is_empty() {
                gen_attrs.push(OtelAttribute::string("gen_ai.output.messages", &gen_output));
            }
            if !gen_delta.is_empty() && !use_delta_only(turn) {
                gen_attrs.push(OtelAttribute::string("agentic.input.delta", &gen_delta));
            }

            if !assistant.message_id.is_empty() {
                gen_attrs.push(OtelAttribute::string(
                    "gen_ai.response.id",
                    &assistant.message_id,
                ));
            }
            if !assistant.model.is_empty() && assistant.model != "claude" {
                gen_attrs.push(OtelAttribute::string(
                    "gen_ai.response.model",
                    &assistant.model,
                ));
            }
            if !assistant.stop_reason.is_empty() {
                gen_attrs.push(OtelAttribute::string(
                    "gen_ai.response.finish_reasons",
                    format!("[\"{}\"]", assistant.stop_reason),
                ));
            }
            if let Some(input_tokens) = assistant.usage.input_total() {
                gen_attrs.push(OtelAttribute::int_as_string(
                    "gen_ai.usage.input_tokens",
                    input_tokens,
                ));
            }
            if let Some(output_tokens) = assistant.usage.output {
                gen_attrs.push(OtelAttribute::int_as_string(
                    "gen_ai.usage.output_tokens",
                    output_tokens,
                ));
            }
            // cache_creation_input_tokens / cache_read_input_tokens 不单独上报，
            // 已聚合到 gen_ai.usage.input_tokens 中。

            // Error detection
            let gen_error = detect_llm_error(&assistant.stop_reason);
            if let Some((ref err_type, _)) = gen_error {
                gen_attrs.push(OtelAttribute::string("error.type", err_type));
            }
            let gen_status = if let Some((_, ref msg)) = gen_error {
                OtelStatus::error(msg)
            } else {
                OtelStatus::ok()
            };

            let gen_start_u128 = gen_start.parse::<u128>().unwrap_or(0);
            let gen_end_u128 = gen_end.parse::<u128>().unwrap_or(0);
            let llm_duration_ms = if gen_end_u128 > gen_start_u128 {
                (gen_end_u128 - gen_start_u128) / 1_000_000
            } else {
                0
            };
            gen_attrs.push(OtelAttribute::int_as_string(
                "agentic.llm.duration_ms",
                llm_duration_ms as i64,
            ));

            spans.push(OtelSpan {
                trace_id: trace_id.to_string(),
                span_id: gen_span_id,
                parent_span_id: parent_span_id.to_string(),
                name: format!("chat {}", assistant.model),
                kind: SPAN_KIND_INTERNAL,
                start_time_unix_nano: gen_start.clone(),
                end_time_unix_nano: gen_end,
                attributes: gen_attrs,
                status: gen_status,
            });

            // Tool spans for this assistant's tool_uses
            if !assistant.tool_uses.is_empty() {
                let tool_start_ns =
                    (gen_start.parse::<u128>().unwrap_or(0) + 1_000_000).to_string();
                let owned_tool_uses: Vec<ToolUse> = assistant.tool_uses.to_vec();
                let parallel_group = if owned_tool_uses.len() > 1 {
                    Some(otel_span_id())
                } else {
                    None
                };
                let tool_spans = build_tool_spans(
                    trace_id,
                    parent_span_id,
                    &owned_tool_uses,
                    &all_tool_results,
                    &tool_start_ns,
                    parent_end_ns,
                    username,
                    conversation_id,
                    &chat_id,
                    None,
                    parallel_group.as_deref(),
                    timing,
                );
                // Tag each tool span with subagent info
                for mut ts in tool_spans {
                    ts.attributes
                        .push(OtelAttribute::string("agentic.subagent", "true"));
                    ts.attributes
                        .push(OtelAttribute::string("agentic.subagent.type", agent_type));
                    spans.push(ts);
                }
            }
        }
    }

    // Add subagent metadata to first span if available
    if !spans.is_empty() && !description.is_empty() {
        spans[0].attributes.push(OtelAttribute::string(
            "agentic.subagent.description",
            description,
        ));
    }

    spans
}

/// Expand Agent TOOL spans by reading subagent transcripts and adding child spans.
/// Scans `spans` for spans with `agentic.tool.type == "subagent"`, finds the corresponding
/// subagent transcript, parses it, and appends child generation/tool spans.
/// Read a string attribute from a span.
fn span_str_attr(span: &OtelSpan, key: &str) -> Option<String> {
    span.attributes.iter().find_map(|a| {
        if a.key == key {
            match &a.value {
                agentic_otel_common::otlp::OtelAnyValue::String { string_value } => {
                    Some(string_value.clone())
                }
                _ => None,
            }
        } else {
            None
        }
    })
}

/// Backfill async subagent spans with real completion data from task
/// notifications (`queue-operation` transcript lines, CC v2.1.199+).
///
/// Without this, an async agent span ends at the launch receipt (or a
/// TaskOutput poll) and carries no usage: the real completion time, duration,
/// token usage and final result only exist in the task-notification.
/// Spans are matched by `gen_ai.tool.call.id` == notification `tool-use-id`.
/// Async spans with no notification (session ended first) are marked
/// `agentic.subagent.status="unknown"` — not an error.
fn backfill_task_notifications(spans: &mut [OtelSpan], notifications: &[TaskNotification]) {
    let by_tool_use: HashMap<&str, &TaskNotification> = notifications
        .iter()
        .map(|n| (n.tool_use_id.as_str(), n))
        .collect();

    for span in spans.iter_mut() {
        let is_subagent = span_str_attr(span, "agentic.tool.type")
            .map(|v| v == "subagent")
            .unwrap_or(false);
        if !is_subagent {
            continue;
        }
        let is_async = span_str_attr(span, "agentic.subagent.mode")
            .map(|v| v == "async")
            .unwrap_or(false);
        if !is_async {
            continue;
        }
        let Some(call_id) = span_str_attr(span, "gen_ai.tool.call.id") else {
            continue;
        };
        let Some(notification) = by_tool_use.get(call_id.as_str()) else {
            // Session ended (or transcript trimmed) before the agent finished.
            span.attributes
                .push(OtelAttribute::string("agentic.subagent.status", "unknown"));
            continue;
        };

        // Defensive: when both ids are present they must agree; a mismatch
        // means the notification belongs to a different agent — skip the
        // backfill but still mark the span so it is not left status-less.
        if !notification.task_id.is_empty() {
            if let Some(agent_id) = span_str_attr(span, "agentic.subagent.id") {
                if agent_id != notification.task_id {
                    span.attributes
                        .push(OtelAttribute::string("agentic.subagent.status", "unknown"));
                    continue;
                }
            }
        }

        // Real completion time: notification timestamp ≈ when the agent stopped.
        if !notification.timestamp.is_empty() {
            let new_end = iso_to_nanos(&notification.timestamp);
            let new_end_u = new_end.parse::<u128>().unwrap_or(0);
            let cur_end_u = span.end_time_unix_nano.parse::<u128>().unwrap_or(0);
            if new_end_u > cur_end_u {
                span.end_time_unix_nano = new_end;
                let start_u = span.start_time_unix_nano.parse::<u128>().unwrap_or(0);
                if new_end_u > start_u {
                    let duration_ms = ((new_end_u - start_u) / 1_000_000) as i64;
                    let mut updated = false;
                    for attr in span.attributes.iter_mut() {
                        if attr.key == "agentic.tool.duration_ms" {
                            *attr = OtelAttribute::int_as_string(
                                "agentic.tool.duration_ms",
                                duration_ms,
                            );
                            updated = true;
                        }
                    }
                    if !updated {
                        span.attributes.push(OtelAttribute::int_as_string(
                            "agentic.tool.duration_ms",
                            duration_ms,
                        ));
                    }
                }
            }
        }

        // Malformed notifications may carry an empty status; normalize to
        // "unknown" so the attribute never holds an invisible empty string.
        let status = if notification.status.is_empty() {
            "unknown"
        } else {
            notification.status.as_str()
        };
        span.attributes
            .push(OtelAttribute::string("agentic.subagent.status", status));
        if let Some(tokens) = notification.subagent_tokens {
            span.attributes
                .push(OtelAttribute::int("agentic.subagent.tokens", tokens));
        }
        if let Some(tool_uses) = notification.tool_uses {
            span.attributes
                .push(OtelAttribute::int("agentic.subagent.tool_uses", tool_uses));
        }
        if !notification.result.is_empty() && !is_privacy_mode() {
            let (truncated, _meta) = truncate_value_observed(
                &Value::String(notification.result.clone()),
                "agentic.subagent.result",
            );
            let result_str = match truncated {
                Value::String(s) => s,
                other => serde_json::to_string(&other).unwrap_or_default(),
            };
            span.attributes
                .push(OtelAttribute::string("agentic.subagent.result", result_str));
        }
    }
}

/// Information about a detected Agent TOOL span for subagent expansion.
struct AgentToolSpanInfo {
    span_id: String,
    trace_id: String,
    agent_id: String,
    start_ns: String,
    end_ns: String,
    is_background: bool,
}

fn expand_subagent_spans(
    spans: &mut Vec<OtelSpan>,
    transcript_path: &str,
    username: &str,
    session_id: &str,
    subagent_timing: &SubagentTimingData,
) {
    let subagent_dir = match subagent_dir_from_transcript(transcript_path) {
        Some(dir) => dir,
        None => return,
    };

    // Collect Agent TOOL span info
    let mut agent_spans: Vec<AgentToolSpanInfo> = Vec::new();

    for span in spans.iter() {
        let is_subagent = span.attributes.iter().any(|a| {
            a.key == "agentic.tool.type"
                && matches!(&a.value, agentic_otel_common::otlp::OtelAnyValue::String { string_value } if string_value == "subagent")
        });
        if !is_subagent {
            continue;
        }

        // Extract agent_id: prefer structured attribute, fallback to text extraction
        let agent_id = span_str_attr(span, "agentic.subagent.id").or_else(|| {
            span_str_attr(span, "gen_ai.tool.call.result")
                .as_deref()
                .and_then(extract_agent_id_from_result)
                .map(|s| s.to_string())
        });

        // Detect background mode: prefer the resolved agentic.subagent.mode
        // attribute (already accounts for toolUseResult.isAsync from v2.1.199+),
        // fallback to run_in_background in tool arguments (parsed as JSON so
        // any serialization whitespace is handled).
        let is_background = span_str_attr(span, "agentic.subagent.mode")
            .map(|mode| mode == "async")
            .unwrap_or_else(|| {
                span_str_attr(span, "gen_ai.tool.call.arguments")
                    .and_then(|args| serde_json::from_str::<Value>(&args).ok())
                    .and_then(|v| v.get("run_in_background").and_then(|b| b.as_bool()))
                    .unwrap_or(false)
            });

        if let Some(agent_id) = agent_id {
            agent_spans.push(AgentToolSpanInfo {
                span_id: span.span_id.clone(),
                trace_id: span.trace_id.clone(),
                agent_id,
                start_ns: span.start_time_unix_nano.clone(),
                end_ns: span.end_time_unix_nano.clone(),
                is_background,
            });
        }
    }

    // For each Agent TOOL span, read the subagent transcript and build child spans
    for info in &agent_spans {
        let meta_file = subagent_dir.join(format!("agent-{}.meta.json", info.agent_id));

        // Read meta for agent_type and description (meta file may exist even if transcript doesn't)
        let (agent_type, description) = if let Ok(meta_str) = fs::read_to_string(&meta_file) {
            if let Ok(meta) = serde_json::from_str::<Value>(&meta_str) {
                (
                    meta.get("agentType")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    meta.get("description")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                )
            } else {
                (String::new(), String::new())
            }
        } else {
            (String::new(), String::new())
        };

        // Supplement Agent TOOL span with meta.json data (description, agent_type)
        // Naming and mode are already set correctly in build_tool_spans
        if let Some(agent_span) = spans.iter_mut().find(|s| s.span_id == info.span_id) {
            if !agent_type.is_empty() {
                let has_type = agent_span
                    .attributes
                    .iter()
                    .any(|a| a.key == "agentic.subagent.type");
                if !has_type {
                    agent_span
                        .attributes
                        .push(OtelAttribute::string("agentic.subagent.type", &agent_type));
                }
            }
            if !description.is_empty() {
                agent_span.attributes.push(OtelAttribute::string(
                    "agentic.subagent.description",
                    &description,
                ));
            }
        }

        // Transcript check — async agents may not have a complete transcript yet
        let transcript_file = subagent_dir.join(format!("agent-{}.jsonl", info.agent_id));
        if !transcript_file.exists() {
            continue;
        }

        // Read and parse the subagent transcript
        let (messages, _offset, _buffer) =
            crate::common::transcript::read_new_jsonl(&transcript_file, 0, "");
        if messages.is_empty() {
            continue;
        }

        let turns = crate::common::transcript::build_turns(&messages, None);
        if turns.is_empty() {
            continue;
        }

        // Sync agents: expand child spans under the Agent TOOL span
        // Async agents: skip child expansion (transcript may be incomplete; SubagentHandler handles it)
        if !info.is_background {
            let empty_timing = TimingData::new();
            let timing = subagent_timing.get(&info.agent_id).unwrap_or(&empty_timing);
            let conversation_id = derive_subagent_conversation_id(session_id, &info.agent_id);
            let child_spans = build_subagent_child_spans(
                &info.trace_id,
                &info.span_id,
                &turns,
                username,
                &conversation_id,
                &info.start_ns,
                &info.end_ns,
                &agent_type,
                &description,
                timing,
            );
            spans.extend(child_spans);
        }
    }
}

// --- Main OTel batch builder ---

// --- Shared core: collect spans for a single turn ---
/// Collect all spans (root, skill, generation, tool) for a single turn.
/// This is the single source of truth for span assembly — used by both live and replay paths.
#[allow(clippy::too_many_arguments)]
fn collect_turn_spans(
    turn: &Turn,
    turn_number: usize,
    session_id: &str,
    username: &str,
    now_ns: &str,
    cwd: &str,
    transcript_path: &str,
    timing: &TimingData,
    subagent_timing: &SubagentTimingData,
    stored_trace_context: Option<&StoredTraceContext>,
) -> Vec<OtelSpan> {
    // Decide once whether this turn (parent) goes delta; cascade into any inlined
    // subagent spans so a mega-turn with many small subagents doesn't rebloat.
    // Guard auto-resets when this function returns.
    let _parent_delta_guard = ParentForceDeltaGuard::set(use_delta_only(turn));

    // Trace context propagation: prefer stored context (from PreToolUse state),
    // then fall back to current env TRACEPARENT, then random.
    let (trace_id, upstream_parent_span_id) = if let Some(ctx) = stored_trace_context {
        let parent_span_id = ctx
            .traceparent
            .as_deref()
            .and_then(agentic_otel_common::parse_traceparent)
            .map(|tp| tp.parent_span_id)
            .unwrap_or_default();
        (ctx.trace_id.clone(), parent_span_id)
    } else {
        let traceparent = crate::common::env::var("TRACEPARENT")
            .ok()
            .and_then(|value| agentic_otel_common::parse_traceparent(&value));
        if let Some(ref tp) = traceparent {
            if !turn.cancelled {
                (tp.trace_id.clone(), tp.parent_span_id.clone())
            } else {
                (otel_trace_id(), String::new())
            }
        } else {
            (otel_trace_id(), String::new())
        }
    };

    let chat_id = resolve_chat_id(turn, &trace_id);
    let root_span_id = stored_trace_context
        .and_then(|context| context.root_span_id.clone())
        .unwrap_or_else(otel_span_id);

    let mut spans: Vec<OtelSpan> = Vec::new();
    let mut max_logical_child_end = stored_trace_context
        .and_then(|context| context.max_accounted_child_end_time_unix_nano.as_deref())
        .and_then(|value| value.parse::<u128>().ok());

    // Root span
    let mut root_span = build_root_span(
        &trace_id,
        &root_span_id,
        &upstream_parent_span_id,
        turn,
        session_id,
        &chat_id,
        username,
        now_ns,
        turn_number,
        &turn.skill_name,
        cwd,
    );
    // SOFA trace context attributes (from env vars)
    if let Ok(sofa_traceid) = crate::common::env::var("SOFA_TRACEID") {
        if !sofa_traceid.is_empty() {
            root_span.attributes.push(OtelAttribute::string(
                "_internal.sofa.trace_id",
                &sofa_traceid,
            ));
        }
    }
    if let Ok(sofa_rpcid) = crate::common::env::var("SOFA_RPCID") {
        if !sofa_rpcid.is_empty() {
            root_span
                .attributes
                .push(OtelAttribute::string("_internal.sofa.span_id", &sofa_rpcid));
        }
    }
    spans.push(root_span);

    // Collect all tool results for this turn
    let all_tool_results: Vec<ToolResultItem> = turn
        .tool_results
        .iter()
        .flat_map(|tr| tr.items.iter().cloned())
        .collect();

    // Collect all tool_uses across all assistants in this turn
    let all_tool_uses: Vec<&ToolUse> = turn
        .assistants
        .iter()
        .flat_map(|a| a.tool_uses.iter())
        .collect();

    // Determine the earliest assistant timestamp for tool spans
    let earliest_assistant_ts = turn
        .assistants
        .first()
        .map(|a| iso_to_nanos(&a.timestamp))
        .unwrap_or_else(|| now_ns.to_string());

    // For command-style Skill invocations (skill_name set but no "Skill" in tool_uses),
    // create a Skill span at the same level as other tool spans.
    let has_skill_tool_use = all_tool_uses.iter().any(|tu| tu.name == "Skill");
    if let Some(skill) = turn.skill_name.as_ref().filter(|_| !has_skill_tool_use) {
        let skill_span_id = otel_span_id();
        let skill_start_ns = if !turn.user_timestamp.is_empty() {
            iso_to_nanos(&turn.user_timestamp)
        } else {
            now_ns.to_string()
        };

        let (input_json, output_text) = if is_privacy_mode() {
            ("[REDACTED]".to_string(), "[REDACTED]".to_string())
        } else {
            (
                json!({"skill": skill}).to_string(),
                format!("Launching skill: {}", skill),
            )
        };
        let skill_id = format!("skill-{}", skill);
        let skill_duration_ms = {
            let start = skill_start_ns.parse::<u128>().unwrap_or(0);
            let end = now_ns.parse::<u128>().unwrap_or(0);
            end.saturating_sub(start) / 1_000_000
        };
        let skill_attributes = vec![
            OtelAttribute::string("gen_ai.span.kind", "TOOL"),
            OtelAttribute::string("gen_ai.operation.name", "execute_tool"),
            OtelAttribute::string("gen_ai.conversation.id", session_id),
            OtelAttribute::string("gen_ai.chat.id", &chat_id),
            OtelAttribute::string("gen_ai.tool.name", format!("skill {}", skill)),
            OtelAttribute::string("gen_ai.tool.call.id", &skill_id),
            OtelAttribute::string("gen_ai.tool.call.arguments", &input_json),
            OtelAttribute::string("gen_ai.tool.call.result", &output_text),
            OtelAttribute::string("ant.username", username),
            OtelAttribute::string("user.id", username),
            OtelAttribute::string("agentic.tool.raw_name", "Skill"),
            OtelAttribute::string("agentic.tool.type", "skill"),
            OtelAttribute::string("agentic.tool.name", skill),
            OtelAttribute::string("agentic.tool.id", &skill_id),
            OtelAttribute::int_as_string("agentic.tool.duration_ms", skill_duration_ms as i64),
        ];

        spans.push(OtelSpan {
            trace_id: trace_id.clone(),
            span_id: skill_span_id,
            parent_span_id: root_span_id.clone(),
            name: format!("skill {}", skill),
            kind: SPAN_KIND_INTERNAL,
            start_time_unix_nano: skill_start_ns,
            end_time_unix_nano: now_ns.to_string(),
            attributes: skill_attributes,
            status: OtelStatus::ok(),
        });
    }

    // Generation spans and tool spans: interleave by assistant order
    // Tool spans are children of their corresponding generation span
    for (assistant_index, assistant) in turn.assistants.iter().enumerate() {
        // 1. Generation span first
        if !stored_generation_is_accounted(&assistant.message_id, stored_trace_context) {
            let (_gen_span_id, mut generation_span) = build_generation_span(
                &trace_id,
                &root_span_id,
                turn,
                0,
                assistant,
                assistant_index,
                now_ns,
                username,
                session_id,
                &chat_id,
                timing,
            );

            // Add retry_count attribute if this turn has api_errors
            if !turn.api_errors.is_empty()
                && (assistant.is_synthetic || assistant_index == turn.assistants.len() - 1)
            {
                generation_span
                    .attributes
                    .push(OtelAttribute::int_as_string(
                        "agentic.llm.retry_count",
                        turn.api_errors.len() as i64,
                    ));
            }
            max_logical_child_end =
                max_logical_child_end.max(generation_span.end_time_unix_nano.parse::<u128>().ok());
            if apply_stored_generation_identity(
                &mut generation_span,
                &assistant.message_id,
                stored_trace_context,
            ) {
                spans.push(generation_span);
            }
        } else {
            // An already-accounted generation is omitted from this delta export,
            // but its duration must still bound the turn root.
            let generation_end = if !assistant.end_timestamp.is_empty() {
                iso_to_nanos(&assistant.end_timestamp)
            } else {
                now_ns.to_string()
            };
            max_logical_child_end = max_logical_child_end.max(generation_end.parse::<u128>().ok());
        }

        // 2. Then tool spans as siblings of generation span (children of root)
        //    Rule 3: LLM ↔ Tool are same-level, not parent-child
        //    Rule 2: Use assistant timestamp directly, no artificial offset
        if !assistant.tool_uses.is_empty() {
            let owned_tool_uses: Vec<ToolUse> = assistant
                .tool_uses
                .iter()
                .filter(|tool_use| !stored_tool_is_accounted(&tool_use.id, stored_trace_context))
                .cloned()
                .collect();
            if owned_tool_uses.is_empty() {
                continue;
            }
            // Tool execution starts after the LLM generates the response.
            // Add 1ms ordering offset so tools sort after the generation span in the UI.
            let assistant_ns = iso_to_nanos(&assistant.timestamp);
            let assistant_ts = (assistant_ns.parse::<u128>().unwrap_or(0) + 1_000_000).to_string();
            // Rule 13: detect parallel tool calls (>1 tool_use in same assistant response)
            let parallel_group = if owned_tool_uses.len() > 1 {
                Some(otel_span_id())
            } else {
                None
            };
            let tool_spans = build_tool_spans(
                &trace_id,
                &root_span_id,
                &owned_tool_uses,
                &all_tool_results,
                &assistant_ts,
                now_ns,
                username,
                session_id,
                &chat_id,
                turn.skill_name.as_deref(),
                parallel_group.as_deref(),
                timing,
            );
            for (tool_use, mut tool_span) in owned_tool_uses.iter().zip(tool_spans) {
                max_logical_child_end =
                    max_logical_child_end.max(tool_span.end_time_unix_nano.parse::<u128>().ok());
                if apply_stored_tool_identity(&mut tool_span, &tool_use.id, stored_trace_context) {
                    spans.push(tool_span);
                }
            }
        }
    }

    // Compaction spans: children of root turn span (Rule 14)
    for compaction in &turn.compactions {
        spans.push(build_compaction_span(
            &trace_id,
            &root_span_id,
            compaction,
            now_ns,
            username,
            session_id,
            &chat_id,
        ));
    }

    // api_error spans: siblings of generation spans (children of root turn span)
    if !turn.api_errors.is_empty() {
        // Recovery timestamp: first non-synthetic assistant AFTER the last api_error
        let last_error_ts = turn
            .api_errors
            .last()
            .map(|e| e.timestamp.as_str())
            .unwrap_or("");
        let last_error_ns = if !last_error_ts.is_empty() {
            iso_to_nanos(last_error_ts)
        } else {
            "0".to_string()
        };
        let recovery_assistant = turn.assistants.iter().find(|a| {
            !a.is_synthetic && !a.timestamp.is_empty() && iso_to_nanos(&a.timestamp) > last_error_ns
        });
        let recovery_ns = recovery_assistant
            .map(|a| iso_to_nanos(&a.timestamp))
            .unwrap_or_else(|| now_ns.to_string());
        let api_error_request_model = turn
            .terminal_llm_error
            .as_ref()
            .and_then(|err| err.request_model.as_deref())
            .or_else(|| recovery_assistant.map(|a| a.model.as_str()))
            .or_else(|| {
                turn.assistants
                    .iter()
                    .find(|a| !a.is_synthetic && !a.model.is_empty() && a.model != "<synthetic>")
                    .map(|a| a.model.as_str())
            })
            .unwrap_or("unknown");

        let error_spans = build_api_error_spans(
            &trace_id,
            &root_span_id,
            &turn.api_errors,
            &recovery_ns,
            api_error_request_model,
            username,
            session_id,
            &chat_id,
        );
        spans.extend(error_spans);
    }

    // Fallback: tool spans from results only (when no tool_uses in assistants)
    if all_tool_uses.is_empty() && !all_tool_results.is_empty() {
        let unaccounted_results = all_tool_results
            .iter()
            .filter(|result| !stored_tool_is_accounted(&result.tool_use_id, stored_trace_context))
            .cloned()
            .collect::<Vec<_>>();
        if !unaccounted_results.is_empty() {
            // Create a lookup function to find tool names from the transcript file
            let transcript_path = transcript_path.to_string();
            let lookup_tool_name = move |tool_use_id: &str| -> Option<String> {
                lookup_tool_name_from_transcript(&transcript_path, tool_use_id)
            };
            let tool_spans = build_tool_spans_from_results(
                &trace_id,
                &root_span_id,
                &unaccounted_results,
                &earliest_assistant_ts,
                now_ns,
                username,
                session_id,
                &chat_id,
                turn.skill_name.as_deref(),
                lookup_tool_name,
            );
            for (result, mut tool_span) in unaccounted_results.iter().zip(tool_spans) {
                max_logical_child_end =
                    max_logical_child_end.max(tool_span.end_time_unix_nano.parse::<u128>().ok());
                if apply_stored_tool_identity(
                    &mut tool_span,
                    &result.tool_use_id,
                    stored_trace_context,
                ) {
                    spans.push(tool_span);
                }
            }
        }
    }

    // --- Trace-level tags: collect skill/mcp tool names + aivision tags ---
    {
        use agentic_otel_common::otlp::OtelAnyValue;

        fn attr_str_value(val: &OtelAnyValue) -> Option<&str> {
            match val {
                OtelAnyValue::String { string_value } => Some(string_value.as_str()),
                _ => None,
            }
        }

        fn get_attr<'a>(span: &'a OtelSpan, key: &str) -> Option<&'a str> {
            span.attributes
                .iter()
                .find(|a| a.key == key)
                .and_then(|a| attr_str_value(&a.value))
        }

        let mut trace_tags: Vec<String> = Vec::new();

        // Collect skill:xxx and mcp:xxx tags from tool spans
        for span in spans.iter() {
            let tool_type = get_attr(span, "agentic.tool.type").unwrap_or("");
            let tool_name = get_attr(span, "agentic.tool.name").unwrap_or("");
            if tool_name.is_empty() {
                continue;
            }
            let tag = match tool_type {
                "skill" => format!("skill:{}", tool_name),
                "mcp" => format!("mcp:{}", tool_name),
                _ => continue,
            };
            if !trace_tags.contains(&tag) {
                trace_tags.push(tag);
            }
        }

        // Aivision skill tag annotation (feature-gated)
        #[cfg(feature = "skill")]
        {
            use crate::common::skill_tags;
            let registry = skill_tags::load_registry(&skill_tags::state_file_path());
            if let Some(ref reg) = registry {
                if !reg.is_empty() {
                    for span in spans.iter_mut() {
                        if get_attr(span, "agentic.tool.type") != Some("skill") {
                            continue;
                        }
                        let skill_name = get_attr(span, "agentic.tool.name").unwrap_or("");
                        if let Some(tags) = skill_tags::lookup_tags(reg, skill_name) {
                            span.attributes
                                .push(OtelAttribute::string_array("agentic.skill.tags", tags));
                            for tag in tags {
                                if !trace_tags.contains(tag) {
                                    trace_tags.push(tag.clone());
                                }
                            }
                        }
                    }
                }
            }
        }

        // Set merged tags on root span
        if !trace_tags.is_empty() {
            if let Some(root_span) = spans.first_mut() {
                root_span.attributes.push(OtelAttribute::string_array(
                    "langfuse.trace.tags",
                    &trace_tags,
                ));
            }
        }
    }

    // Sort child spans (skip root at index 0) by startTimeUnixNano for consistent ordering
    if spans.len() > 1 {
        spans[1..].sort_by(|a, b| {
            let a_ns = a.start_time_unix_nano.parse::<u128>().unwrap_or(0);
            let b_ns = b.start_time_unix_nano.parse::<u128>().unwrap_or(0);
            a_ns.cmp(&b_ns)
        });
    }

    // Ensure all child spans start at least 1ms after the root span.
    // When child start times are estimated (no precise timing data), they may
    // coincidentally equal the root start, which confuses timeline UIs.
    if spans.len() > 1 {
        let root_start = spans[0].start_time_unix_nano.parse::<u128>().unwrap_or(0);
        let child_min_start = root_start + 1_000_000; // +1ms
        for span in &mut spans[1..] {
            let child_start = span.start_time_unix_nano.parse::<u128>().unwrap_or(0);
            if child_start <= root_start {
                span.start_time_unix_nano = child_min_start.to_string();
            }
        }
    }

    // --- Backfill async subagent completion data ---
    // Before expansion so backfilled end times/attrs apply to the parent spans.
    backfill_task_notifications(&mut spans, &turn.task_notifications);

    // --- Expand subagent child spans ---
    // Called AFTER sorting so subagent children don't interfere with parent span ordering.
    // Subagent child spans are appended at the end; Langfuse uses parent_span_id for nesting
    // and timestamps for ordering within each level.
    expand_subagent_spans(
        &mut spans,
        transcript_path,
        username,
        session_id,
        subagent_timing,
    );

    let max_descendant_end = spans[1..]
        .iter()
        .filter_map(|span| span.end_time_unix_nano.parse::<u128>().ok())
        .max();
    if let Some(max_end) = max_descendant_end.max(max_logical_child_end) {
        let root_end = spans[0].end_time_unix_nano.parse::<u128>().unwrap_or(0);
        if max_end > root_end {
            spans[0].end_time_unix_nano = max_end.to_string();
        }
    }

    spans
}

fn turn_end_nanos(turn: &Turn) -> String {
    turn.assistants
        .last()
        .map(|assistant| {
            if !assistant.end_timestamp.is_empty() {
                iso_to_nanos(&assistant.end_timestamp)
            } else {
                iso_to_nanos(&assistant.timestamp)
            }
        })
        .unwrap_or_else(|| iso_to_nanos(&turn.user_timestamp))
}

/// Wrap collected spans into an OTLP ExportTraceServiceRequest JSON.
fn cchooks_wrap_export_request(spans: Vec<OtelSpan>, build_version: &str) -> Value {
    let agent_env =
        crate::common::hook::env_or_default("CCHOOKS_AGENT_ENV", "AIVISION_AGENT_ENV", "Default");
    let agent_scene = crate::common::hook::env_or_default(
        "CCHOOKS_AGENT_SCENE",
        "AIVISION_AGENT_SCENE",
        "Default",
    );
    let agent_product = resolve_agent_product();
    let agent_type = resolve_agent_type();
    let collection_mode_str = match collection_mode() {
        CollectionMode::Full => "full",
        CollectionMode::Privacy => "privacy",
    };
    let ctx = ExportContext {
        agent_product: &agent_product,
        agent_type: &agent_type,
        sdk_name: "cchooks",
        build_version,
        agent_env: &agent_env,
        agent_scene: &agent_scene,
        collection_mode: collection_mode_str,
        emit_meta_trigger: true,
    };
    wrap_trace_export(spans, &ctx)
}

/// Idempotently inject VCS attributes into every Resource section of an OTLP
/// export request.
pub fn inject_vcs_resource_attrs(request: &mut Value, remote_url: &str, branch: &str) {
    let Some(resource_spans) = request
        .get_mut("resourceSpans")
        .and_then(Value::as_array_mut)
    else {
        return;
    };

    for resource_span in resource_spans {
        let Some(resource_span) = resource_span.as_object_mut() else {
            continue;
        };
        let resource = resource_span.entry("resource").or_insert_with(|| json!({}));
        let Some(resource) = resource.as_object_mut() else {
            continue;
        };
        let attributes = resource.entry("attributes").or_insert_with(|| json!([]));
        let Some(attributes) = attributes.as_array_mut() else {
            continue;
        };

        upsert_vcs_resource_attr(attributes, "vcs.repository.url.full", remote_url.trim());
        upsert_vcs_resource_attr(attributes, "vcs.ref.head.name", branch.trim());
    }
}

fn upsert_vcs_resource_attr(attributes: &mut Vec<Value>, key: &str, value: &str) {
    if value.is_empty() {
        return;
    }
    attributes.retain(|attribute| attribute.get("key").and_then(Value::as_str) != Some(key));
    if let Ok(attribute) = serde_json::to_value(OtelAttribute::string(key, value)) {
        attributes.push(attribute);
    }
}

// --- Public entry points ---

fn cchooks_otel_span_attr_pairs() -> Vec<(String, String)> {
    agentic_otel_common::otlp::parse_otel_span_attributes("CCHOOKS_OTEL_ATTRIBUTES")
        .into_iter()
        // Logical-root ownership is repository-defined, not operator-overridable.
        .filter(|(key, _)| key != INTERNAL_META_ROOT_KEY)
        .collect()
}

fn apply_cchooks_otel_span_attrs(spans: &mut [OtelSpan]) {
    let extra_attrs = cchooks_otel_span_attr_pairs();
    if extra_attrs.is_empty() {
        return;
    }
    for span in spans {
        append_otel_span_attributes(&mut span.attributes, &extra_attrs);
    }
}

/// Extract the persisted parent anchor for late realtime compactions from a
/// finalized turn export. The caller supplies the stable turn identity and the
/// source cursor associated with that export.
pub fn compaction_parent_anchor_from_turn_export(
    body: &Value,
    turn_id: &str,
    turn_number: usize,
    transcript_path: &str,
    source_end_offset: u64,
) -> Result<CompactionParentAnchor, String> {
    let mut root: Option<(String, String, String)> = None;
    for_each_export_span(body, |span| {
        if root.is_none() && is_root_chat_export_span(span) {
            let trace_id = span
                .get("traceId")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty());
            let span_id = span
                .get("spanId")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty());
            let chat_id =
                export_span_string_attr(span, "gen_ai.chat.id").filter(|value| !value.is_empty());
            if let (Some(trace_id), Some(span_id), Some(chat_id)) = (trace_id, span_id, chat_id) {
                root = Some((trace_id.to_string(), span_id.to_string(), chat_id));
            }
        }
    });
    let (trace_id, root_span_id, chat_id) =
        root.ok_or_else(|| "turn export missing complete root CHAT span".to_string())?;

    Ok(CompactionParentAnchor {
        turn_id: turn_id.to_string(),
        turn_number,
        trace_id,
        root_span_id,
        chat_id,
        transcript_path: transcript_path.to_string(),
        source_end_offset,
    })
}

/// Build a minimal OTLP request containing only one late realtime compaction
/// child span. Used when a compact_boundary arrives in a suffix after the
/// parent turn was already exported.
pub fn build_compaction_export_request(
    anchor: &CompactionParentAnchor,
    compaction: &crate::common::transcript::CompactionEvent,
    session_id: &str,
    username: &str,
    build_version: &str,
) -> Result<Value, String> {
    if anchor.trace_id.is_empty() {
        return Err("compaction parent anchor missing trace_id".to_string());
    }
    if anchor.root_span_id.is_empty() {
        return Err("compaction parent anchor missing root_span_id".to_string());
    }
    if anchor.chat_id.is_empty() {
        return Err("compaction parent anchor missing chat_id".to_string());
    }

    let now_ns = agentic_otel_common::now_nanos();
    let mut span = build_compaction_span(
        &anchor.trace_id,
        &anchor.root_span_id,
        compaction,
        &now_ns,
        username,
        session_id,
        &anchor.chat_id,
    );
    span.span_id = stable_compaction_span_id(anchor, compaction);

    let mut spans = vec![span];
    apply_cchooks_otel_span_attrs(&mut spans);
    Ok(cchooks_wrap_export_request(spans, build_version))
}

/// Build the smallest valid OTLP request for one completed tool hook.
///
/// The caller supplies persisted trace/root/tool identities. Payload fields and
/// already-recorded hook timing are the only semantic inputs; this function
/// never reads a transcript.
#[allow(clippy::too_many_arguments)]
pub fn build_realtime_tool_export_request(
    payload: &Value,
    trace_id: &str,
    root_span_id: &str,
    tool_span_id: &str,
    session_id: &str,
    username: &str,
    build_version: &str,
    timing: &TimingData,
    success: bool,
) -> Result<Value, String> {
    let _build_timing = WorkerPhaseGuard::new("build");
    let tool_use_id = payload
        .get("tool_use_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "realtime tool payload missing tool_use_id".to_string())?;
    let tool_name = payload
        .get("tool_name")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or("unknown");
    let tool_input = payload.get("tool_input").cloned().unwrap_or(Value::Null);
    let tool_output = if success {
        payload.get("tool_response").cloned().unwrap_or(Value::Null)
    } else {
        json!({
            "error": payload.get("error").cloned().unwrap_or(Value::Null),
            "is_interrupt": payload.get("is_interrupt").cloned().unwrap_or(Value::Bool(false)),
        })
    };
    let result_agent_id = payload
        .get("tool_response")
        .and_then(|value| value.get("agentId").or_else(|| value.get("agent_id")))
        .and_then(Value::as_str)
        .map(str::to_string);
    let result_agent_type = payload
        .get("tool_response")
        .and_then(|value| value.get("agentType").or_else(|| value.get("agent_type")))
        .and_then(Value::as_str)
        .map(str::to_string);
    // Async subagent launch marker (v2.1.199+ realtime payload).
    let result_is_async = payload
        .get("tool_response")
        .map(|value| {
            value
                .get("isAsync")
                .and_then(Value::as_bool)
                .unwrap_or(false)
                || value.get("status").and_then(Value::as_str) == Some("async_launched")
        })
        .unwrap_or(false);
    let tool_use = ToolUse {
        name: tool_name.to_string(),
        id: tool_use_id.to_string(),
        input: tool_input,
    };
    let tool_result = neutral_tool_result_item(
        tool_use_id.to_string(),
        tool_output,
        Some(tool_name.to_string()),
        String::new(),
        result_agent_id,
        result_agent_type,
        result_is_async,
    );
    let now_ns = agentic_otel_common::now_nanos();
    let start_ns = timing
        .get(tool_use_id)
        .map(|entry| entry.start_ns.as_str())
        .filter(|value| !value.is_empty())
        .unwrap_or(now_ns.as_str());
    let mut spans = build_tool_spans(
        trace_id,
        root_span_id,
        std::slice::from_ref(&tool_use),
        std::slice::from_ref(&tool_result),
        start_ns,
        &now_ns,
        username,
        session_id,
        trace_id,
        None,
        None,
        timing,
    );
    let span = spans
        .first_mut()
        .ok_or_else(|| "realtime tool semantic builder produced no span".to_string())?;
    span.span_id = tool_span_id.to_string();
    apply_cchooks_otel_span_attrs(&mut spans);
    Ok(cchooks_wrap_export_request(spans, build_version))
}

pub struct RealtimeGenerationInput<'a> {
    pub turn: &'a Turn,
    pub turn_index: usize,
    pub assistant_index: usize,
    pub trace_id: &'a str,
    pub root_span_id: &'a str,
    pub span_id: &'a str,
}

/// Build one OTLP request containing only the claimed generation children.
///
/// The semantic generation builder is shared with Stop/replay; this wrapper
/// supplies the stable trace/root/span identities claimed under the realtime
/// state lock.
pub fn build_realtime_generation_export_request(
    inputs: &[RealtimeGenerationInput<'_>],
    session_id: &str,
    username: &str,
    build_version: &str,
    timing: &TimingData,
) -> Result<Value, String> {
    let _build_timing = WorkerPhaseGuard::new("build");
    let now_ns = agentic_otel_common::now_nanos();
    let mut spans = Vec::with_capacity(inputs.len());
    for input in inputs {
        let assistant = input
            .turn
            .assistants
            .get(input.assistant_index)
            .ok_or_else(|| "realtime generation assistant index is out of bounds".to_string())?;
        if assistant.message_id.is_empty() {
            return Err("realtime generation is missing native message.id".to_string());
        }
        let chat_id = resolve_chat_id(input.turn, input.trace_id);
        let (_, mut span) = build_generation_span(
            input.trace_id,
            input.root_span_id,
            input.turn,
            input.turn_index,
            assistant,
            input.assistant_index,
            &now_ns,
            username,
            session_id,
            &chat_id,
            timing,
        );
        span.span_id = input.span_id.to_string();
        if !input.turn.api_errors.is_empty()
            && (assistant.is_synthetic
                || input.assistant_index == input.turn.assistants.len().saturating_sub(1))
        {
            span.attributes.push(OtelAttribute::int_as_string(
                "agentic.llm.retry_count",
                input.turn.api_errors.len() as i64,
            ));
        }
        spans.push(span);
    }
    apply_cchooks_otel_span_attrs(&mut spans);
    Ok(cchooks_wrap_export_request(spans, build_version))
}

/// Build OTel ExportTraceServiceRequest for a single turn.
/// Used by cchooks-replay binary to process each turn independently.
#[allow(dead_code)]
pub fn build_otel_export_request_for_turn(
    turn: &Turn,
    turn_number: usize,
    session_id: &str,
    username: &str,
    build_version: &str,
    transcript_path: &str,
    timing: &TimingData,
) -> Value {
    let _build_timing = WorkerPhaseGuard::new("build");
    let now_ns = turn_end_nanos(turn);

    let empty_subagent_timing = SubagentTimingData::new();
    let mut spans = collect_turn_spans(
        turn,
        turn_number,
        session_id,
        username,
        &now_ns,
        "",
        transcript_path,
        timing,
        &empty_subagent_timing,
        None::<&StoredTraceContext>,
    );
    apply_cchooks_otel_span_attrs(&mut spans);
    cchooks_wrap_export_request(spans, build_version)
}

/// Build the parent turn plus independent async-subagent turns for offline replay/report.
/// Live parent Stop keeps using the single-request adapter so background agents remain
/// owned by SubagentHandler and cannot be double-exported.
#[allow(dead_code)]
#[allow(clippy::too_many_arguments)]
pub fn build_otel_replay_export_requests_for_turn(
    turn: &Turn,
    turn_number: usize,
    session_id: &str,
    username: &str,
    build_version: &str,
    transcript_path: &str,
    timing: &TimingData,
) -> Vec<Value> {
    let parent = build_otel_export_request_for_turn(
        turn,
        turn_number,
        session_id,
        username,
        build_version,
        transcript_path,
        timing,
    );
    let mut requests = vec![parent];
    let Some(subagent_dir) = subagent_dir_from_transcript(transcript_path) else {
        return requests;
    };

    let mut async_agents = Vec::<(String, String, String)>::new();
    for_each_export_span(&requests[0], |span| {
        if export_span_string_attr(span, "agentic.tool.type").as_deref() != Some("subagent")
            || export_span_string_attr(span, "agentic.subagent.mode").as_deref() != Some("async")
        {
            return;
        }
        let agent_id = export_span_string_attr(span, "agentic.subagent.id").or_else(|| {
            export_span_string_attr(span, "gen_ai.tool.call.result")
                .as_deref()
                .and_then(extract_agent_id_from_result)
        });
        let Some(agent_id) = agent_id else {
            return;
        };
        if async_agents.iter().any(|known| known.0 == agent_id) {
            return;
        }
        async_agents.push((
            agent_id,
            export_span_string_attr(span, "agentic.subagent.type").unwrap_or_default(),
            export_span_string_attr(span, "agentic.subagent.description").unwrap_or_default(),
        ));
    });

    for (agent_id, agent_type, description) in async_agents {
        let subagent_path = subagent_dir.join(format!("agent-{agent_id}.jsonl"));
        let (messages, _, _) = crate::common::transcript::read_new_jsonl(&subagent_path, 0, "");
        let turns = crate::common::transcript::build_turns(&messages, None);
        if turns.is_empty() {
            continue;
        }
        let subagent_path_text = subagent_path.to_string_lossy();
        let subagent_timing_path =
            crate::hooks::timing::companion_timing_path(subagent_path_text.as_ref());
        let subagent_timing = if subagent_timing_path.exists() {
            crate::hooks::timing::load_timing_data_from_path(&subagent_timing_path)
        } else {
            TimingData::new()
        };
        for (index, subagent_turn) in turns.iter().enumerate() {
            let mut request = build_otel_export_request_for_turn(
                subagent_turn,
                index + 1,
                session_id,
                username,
                build_version,
                subagent_path_text.as_ref(),
                &subagent_timing,
            );
            apply_async_subagent_semantics(
                &mut request,
                session_id,
                &agent_id,
                &agent_type,
                &description,
            );
            requests.push(request);
        }
    }

    requests
}

#[allow(dead_code)]
#[allow(clippy::too_many_arguments)]
pub fn build_otel_export_request(
    turns: &[Turn],
    session_id: &str,
    username: &str,
    _config: &LangfuseConfig,
    _now: &str,
    build_version: &str,
    prev_turn_count: usize,
    cwd: &str,
    transcript_path: &str,
    timing: &TimingData,
) -> Value {
    let empty_subagent_timing = SubagentTimingData::new();
    build_otel_export_request_with_subagent_timing(
        turns,
        session_id,
        username,
        _config,
        _now,
        build_version,
        prev_turn_count,
        cwd,
        transcript_path,
        timing,
        &empty_subagent_timing,
        &[] as &[StoredTraceContext],
    )
}

#[allow(clippy::too_many_arguments)]
pub fn build_otel_export_request_with_subagent_timing(
    turns: &[Turn],
    session_id: &str,
    username: &str,
    _config: &LangfuseConfig,
    _now: &str,
    build_version: &str,
    prev_turn_count: usize,
    cwd: &str,
    transcript_path: &str,
    timing: &TimingData,
    subagent_timing: &SubagentTimingData,
    trace_context_history: &[StoredTraceContext],
) -> Value {
    let _build_timing = WorkerPhaseGuard::new("build");
    // Process all turns (not just the last) to handle interrupted + resumed scenarios
    if turns.is_empty() {
        return json!({"resourceSpans": []});
    }

    let mut all_spans = Vec::new();
    for (idx, turn) in turns.iter().enumerate() {
        let turn_number = prev_turn_count + idx + 1;
        let now_ns = turn_end_nanos(turn);
        // Match turns to trace_context_history from the end:
        // last turn → history[-1], second-to-last → history[-2], etc.
        // Match trace context to this turn by checking if any of
        // the turn's tool_use_ids appear in a context's tool_use_ids list.
        let stored_ctx = {
            let turn_tool_ids = turn.tool_use_ids();
            trace_context_history.iter().find(|ctx| {
                ctx.tool_use_ids
                    .iter()
                    .any(|id| turn_tool_ids.contains(&id.as_str()))
            })
        };
        all_spans.extend(collect_turn_spans(
            turn,
            turn_number,
            session_id,
            username,
            &now_ns,
            cwd,
            transcript_path,
            timing,
            subagent_timing,
            stored_ctx,
        ));
    }

    // Debug: dump last turn structure for diagnosis
    if crate::common::hook::env_is_truthy("CCHOOKS_DEBUG") {
        let latest_turn = turns.last().unwrap();
        let log_dir = crate::common::dirs::log_dir().to_string_lossy().to_string();
        let debug_info = format!(
            "turns={} last_turn_number={} assistants={} tool_results={} tool_uses_per_assistant=[{}]",
            turns.len(),
            prev_turn_count + turns.len(),
            latest_turn.assistants.len(),
            latest_turn
                .tool_results
                .iter()
                .flat_map(|tr| tr.items.iter())
                .count(),
            latest_turn
                .assistants
                .iter()
                .map(|a| a.tool_uses.len().to_string())
                .collect::<Vec<_>>()
                .join(","),
        );
        let _ = std::fs::write(format!("{}/otel_turn_debug.txt", log_dir), &debug_info);
    }

    apply_cchooks_otel_span_attrs(&mut all_spans);
    cchooks_wrap_export_request(all_spans, build_version)
}

/// Full-input one-turn adapter for the live progressive exporter. The caller
/// resolves trace context exactly once; live and aggregate replay paths still
/// share `collect_turn_spans` as their semantic assembly core.
#[allow(clippy::too_many_arguments)]
pub fn build_otel_export_request_for_turn_with_full_context(
    turn: &Turn,
    turn_number: usize,
    session_id: &str,
    username: &str,
    _config: &LangfuseConfig,
    _now: &str,
    build_version: &str,
    cwd: &str,
    transcript_path: &str,
    timing: &TimingData,
    subagent_timing: &SubagentTimingData,
    stored_trace_context: Option<&StoredTraceContext>,
) -> Value {
    let _build_timing = WorkerPhaseGuard::new("build");
    let now_ns = turn_end_nanos(turn);
    let mut spans = collect_turn_spans(
        turn,
        turn_number,
        session_id,
        username,
        &now_ns,
        cwd,
        transcript_path,
        timing,
        subagent_timing,
        stored_trace_context,
    );
    apply_cchooks_otel_span_attrs(&mut spans);
    cchooks_wrap_export_request(spans, build_version)
}

// --- HTTP POST for OTel ---

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OtelPostError {
    Preparation(String),
    Transport(String),
    HttpStatus(u16),
}

impl OtelPostError {
    pub fn is_non_retryable_http_status(&self) -> bool {
        matches!(self, Self::HttpStatus(status) if !otlp_http_status_is_retryable(*status))
    }
}

impl std::fmt::Display for OtelPostError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Preparation(error) => formatter.write_str(error),
            Self::Transport(error) => {
                write!(formatter, "Langfuse OTel HTTP request failed: {error}")
            }
            Self::HttpStatus(status) => write!(
                formatter,
                "Langfuse OTel HTTP request returned non-success status {status}"
            ),
        }
    }
}

pub fn otlp_http_status_is_retryable(status: u16) -> bool {
    matches!(status, 429 | 502 | 503 | 504)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum LangfuseOperation {
    Stop,
    RealtimeTool,
    RealtimeGeneration,
    /// Default context for retained legacy/default one-shot entry points.
    /// Only exercised by tests; production callers pass a typed context.
    #[allow(dead_code)]
    Other,
}

impl LangfuseOperation {
    fn as_str(self) -> &'static str {
        match self {
            Self::Stop => "stop",
            Self::RealtimeTool => "realtime_tool",
            Self::RealtimeGeneration => "realtime_generation",
            Self::Other => "other",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct LangfuseAttemptContext {
    operation: LangfuseOperation,
    correlation: String,
}

impl LangfuseAttemptContext {
    pub(crate) fn stop(turn_id: &str, turn_number: usize) -> Self {
        Self::new(
            LangfuseOperation::Stop,
            &[
                ("turn_id", turn_id),
                ("turn_number", &turn_number.to_string()),
            ],
        )
    }

    pub(crate) fn realtime_tool(context_id: &str, tool_use_id: &str) -> Self {
        Self::new(
            LangfuseOperation::RealtimeTool,
            &[("context_id", context_id), ("tool_use_id", tool_use_id)],
        )
    }

    pub(crate) fn realtime_generation(claim_id: &str, message_ids: &[String]) -> Self {
        Self::new(
            LangfuseOperation::RealtimeGeneration,
            &[
                ("claim_id", claim_id),
                ("message_ids", &message_ids.join(",")),
            ],
        )
    }

    #[allow(dead_code)]
    fn other() -> Self {
        Self {
            operation: LangfuseOperation::Other,
            correlation: String::new(),
        }
    }

    fn new(operation: LangfuseOperation, fields: &[(&str, &str)]) -> Self {
        let correlation = fields
            .iter()
            .map(|(key, value)| format!(" {key}={}", bounded_log_value(value, 192)))
            .collect();
        Self {
            operation,
            correlation,
        }
    }
}

fn bounded_log_value(value: &str, max_bytes: usize) -> String {
    let mut escaped = String::new();
    for character in value.chars() {
        match character {
            '\n' => escaped.push_str("\\n"),
            '\r' => escaped.push_str("\\r"),
            '\t' => escaped.push_str("\\t"),
            character if character.is_control() || character.is_whitespace() => {
                escaped.push_str(&format!("\\u{{{:x}}}", character as u32));
            }
            character => escaped.push(character),
        }
    }
    if escaped.len() <= max_bytes {
        return escaped;
    }
    let mut end = max_bytes;
    while !escaped.is_char_boundary(end) {
        end -= 1;
    }
    escaped.truncate(end);
    escaped
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LangfuseErrorKind {
    Preparation,
    Timeout,
    Dns,
    Connect,
    HttpStatus,
    Transport,
}

impl LangfuseErrorKind {
    fn as_str(self) -> &'static str {
        match self {
            Self::Preparation => "preparation",
            Self::Timeout => "timeout",
            Self::Dns => "dns",
            Self::Connect => "connect",
            Self::HttpStatus => "http_status",
            Self::Transport => "transport",
        }
    }
}

fn classify_ureq_error(error: &ureq::Error) -> LangfuseErrorKind {
    match error {
        ureq::Error::StatusCode(_) => LangfuseErrorKind::HttpStatus,
        ureq::Error::Timeout(_) => LangfuseErrorKind::Timeout,
        ureq::Error::HostNotFound => LangfuseErrorKind::Dns,
        ureq::Error::ConnectionFailed => LangfuseErrorKind::Connect,
        ureq::Error::Io(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::ConnectionRefused
                    | std::io::ErrorKind::ConnectionReset
                    | std::io::ErrorKind::ConnectionAborted
                    | std::io::ErrorKind::NotConnected
            ) =>
        {
            LangfuseErrorKind::Connect
        }
        _ => LangfuseErrorKind::Transport,
    }
}

/// Resolve OTLP endpoint URL.
/// If `host` already contains a URI path (e.g. "https://proxy/v1/traces"),
/// use it as-is. Otherwise append `default_path`.
fn resolve_otel_url(host: &str, default_path: &str) -> String {
    let trimmed = host.trim_end_matches('/');
    if let Some(scheme_end) = trimmed.find("://") {
        let after_scheme = &trimmed[scheme_end + 3..];
        if after_scheme.contains('/') {
            return trimmed.to_string();
        }
    }
    format!("{}{}", trimmed, default_path)
}

/// A fully-prepared Langfuse OTLP trace POST, carrying only owned data so it can
/// be moved onto another thread and executed without borrowing any context.
pub struct PreparedLangfuseSend {
    url: String,
    body: Vec<u8>,
    /// Header (name, value) pairs, including Content-Type, ingestion-version,
    /// Authorization, and any custom config headers — resolved up front.
    headers: Vec<(String, String)>,
    agent: ureq::Agent,
    body_len: usize,
    timeout: std::time::Duration,
}

struct LangfuseSendOutcome {
    status: u16,
}

struct LangfuseSendFailure {
    error: OtelPostError,
    kind: LangfuseErrorKind,
    status: Option<u16>,
}

impl PreparedLangfuseSend {
    /// Execute the blocking HTTP POST. Safe to call on a worker thread.
    /// Returns Ok on a completed request, Err(message) on transport failure.
    ///
    /// `logger` is a `Logger` (cheap `Clone`); worker threads pass a clone and
    /// write directly to the log file under `LOG_LOCK`, so send-phase
    /// diagnostics are serialized with all other writers.
    pub fn execute(self, logger: &crate::common::logger::Logger) -> Result<(), String> {
        self.execute_with_policy(logger, true)
    }

    fn execute_with_policy(
        self,
        logger: &crate::common::logger::Logger,
        retry_once: bool,
    ) -> Result<(), String> {
        let context = LangfuseAttemptContext::other();
        let send = || run_prepared_langfuse_attempt(logger, self.timeout, &context, &self);

        let result = if retry_once {
            crate::common::hook::retry_once(send, |err| {
                logger.warn(&format!(
                    "Langfuse OTel HTTP request failed, retrying once: {}",
                    err
                ));
            })
        } else {
            send()
        };
        result.map_err(|error| error.to_string())
    }

    fn send_once(&self) -> Result<LangfuseSendOutcome, LangfuseSendFailure> {
        let mut request = self.agent.post(&self.url);
        for (k, v) in &self.headers {
            request = request.header(k, v);
        }
        let response = request.send(&self.body[..]).map_err(|error| {
            let kind = classify_ureq_error(&error);
            let status = match error {
                ureq::Error::StatusCode(status) => Some(status),
                _ => None,
            };
            let error = match error {
                ureq::Error::StatusCode(status) => OtelPostError::HttpStatus(status),
                error => OtelPostError::Transport(error.to_string()),
            };
            LangfuseSendFailure {
                error,
                kind,
                status,
            }
        })?;
        let status_code = response.status().as_u16();
        let _ = response.into_body().read_to_string().unwrap_or_default();
        if !(200..300).contains(&status_code) {
            return Err(LangfuseSendFailure {
                error: OtelPostError::HttpStatus(status_code),
                kind: LangfuseErrorKind::HttpStatus,
                status: Some(status_code),
            });
        }
        Ok(LangfuseSendOutcome {
            status: status_code,
        })
    }
}

/// Prepare (but do not send) an OTLP trace POST to Langfuse. All work that
/// touches `config`/`body` happens here on the calling thread; the returned
/// value owns everything needed to send later (possibly on another thread).
///
/// Debug-only body dumps and the trace JSONL companion write also happen here,
/// since they read `body` by reference.
pub fn prepare_langfuse_send(
    config: &LangfuseConfig,
    body: &Value,
    logger: &crate::common::logger::Logger,
    is_new_session: bool,
    session_id: &str,
    transcript_path: &str,
) -> Result<PreparedLangfuseSend, String> {
    prepare_langfuse_send_with_policy(
        config,
        body,
        logger,
        is_new_session,
        session_id,
        transcript_path,
        std::time::Duration::from_secs(HTTP_TIMEOUT_SECS),
        true,
    )
}

#[allow(clippy::too_many_arguments)]
fn prepare_langfuse_send_with_policy(
    config: &LangfuseConfig,
    body: &Value,
    logger: &crate::common::logger::Logger,
    is_new_session: bool,
    session_id: &str,
    transcript_path: &str,
    timeout: std::time::Duration,
    follow_redirects: bool,
) -> Result<PreparedLangfuseSend, String> {
    let url =
        crate::common::hook::otlp_traces_endpoint("CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            .unwrap_or_else(|| resolve_otel_url(&config.host, "/api/public/otel/v1/traces"));

    let agent =
        crate::common::hook::build_http_agent_with_timeout_and_redirects(timeout, follow_redirects);

    let body_str = serde_json::to_string(body).map_err(|e| e.to_string())?;

    // Debug: dump OTel body to file for inspection
    if crate::common::hook::env_is_truthy("CCHOOKS_DEBUG") {
        let log_dir = crate::common::dirs::log_dir().to_string_lossy().to_string();
        let dump_path = format!("{}/otel_body_debug.json", log_dir);
        let pretty = serde_json::to_string_pretty(body).unwrap_or_default();
        let _ = std::fs::write(&dump_path, &pretty);
    }
    write_trace_jsonl_if_debug(body, logger, is_new_session, session_id, transcript_path);

    // Resolve all headers up front into owned pairs.
    let mut headers: Vec<(String, String)> = vec![
        ("Content-Type".to_string(), "application/json".to_string()),
        ("x-langfuse-ingestion-version".to_string(), "4".to_string()),
    ];

    // Add Authorization header only when PK/SK are both configured
    if let (Some(pk), Some(sk)) = (&config.public_key, &config.secret_key) {
        headers.push(("Authorization".to_string(), build_basic_auth_header(pk, sk)));
    }

    // Add custom headers from config if present
    if let Some(ref custom) = config.headers {
        logger.debug(&format!("Config has {} custom headers", custom.len()));
        for (key, value) in custom {
            headers.push((key.clone(), value.clone()));
            logger.debug(&format!("Adding custom header name: {}", key));
        }
    } else {
        logger.debug("No custom headers in config");
    }

    let body_len = body_str.len();
    Ok(PreparedLangfuseSend {
        url,
        body: body_str.into_bytes(),
        headers,
        agent,
        body_len,
        timeout,
    })
}

/// Post OTLP JSON to Langfuse OTel endpoint (synchronous: prepare + execute).
///
/// Retained for callers that send inline on the current thread (offline replay
/// CLI, eval, subagent). Live Stop/SessionEnd uses the one-attempt variant below.
pub fn post_otel_to_langfuse(
    config: &LangfuseConfig,
    body: &Value,
    logger: &crate::common::logger::Logger,
    is_new_session: bool,
    session_id: &str,
    transcript_path: &str,
) -> Result<(), String> {
    prepare_langfuse_send(
        config,
        body,
        logger,
        is_new_session,
        session_id,
        transcript_path,
    )
    .and_then(|prepared| prepared.execute(logger))
}

/// Typed-error compat surface; delegates to the contextual lifecycle helper.
/// Exercised by lifecycle timeout/non-2xx tests.
#[allow(clippy::too_many_arguments)]
#[allow(dead_code)]
pub fn post_otel_to_langfuse_once_classified(
    config: &LangfuseConfig,
    body: &Value,
    logger: &crate::common::logger::Logger,
    is_new_session: bool,
    session_id: &str,
    transcript_path: &str,
    timeout: std::time::Duration,
) -> Result<(), OtelPostError> {
    post_otel_to_langfuse_once_with_context(
        config,
        body,
        logger,
        is_new_session,
        session_id,
        transcript_path,
        timeout,
        LangfuseAttemptContext::other(),
    )
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn post_otel_to_langfuse_once_with_context(
    config: &LangfuseConfig,
    body: &Value,
    logger: &crate::common::logger::Logger,
    is_new_session: bool,
    session_id: &str,
    transcript_path: &str,
    timeout: std::time::Duration,
    context: LangfuseAttemptContext,
) -> Result<(), OtelPostError> {
    run_langfuse_attempt(logger, timeout, context, || {
        prepare_langfuse_send_with_policy(
            config,
            body,
            logger,
            is_new_session,
            session_id,
            transcript_path,
            timeout,
            false,
        )
    })
}

fn run_langfuse_attempt(
    logger: &crate::common::logger::Logger,
    timeout: std::time::Duration,
    context: LangfuseAttemptContext,
    prepare: impl FnOnce() -> Result<PreparedLangfuseSend, String>,
) -> Result<(), OtelPostError> {
    let _http_timing = WorkerPhaseGuard::new("http");
    let (attempt_id, started) = begin_langfuse_attempt(logger, timeout, &context);

    let prepared = match prepare() {
        Ok(prepared) => prepared,
        Err(error) => {
            logger.error(&format!(
                "event=langfuse_send_end attempt_id={attempt_id} operation={} outcome=failure error_kind={} elapsed_ms={}{}",
                context.operation.as_str(),
                LangfuseErrorKind::Preparation.as_str(),
                started.elapsed().as_millis(),
                context.correlation
            ));
            return Err(OtelPostError::Preparation(error));
        }
    };
    finish_langfuse_attempt(logger, &attempt_id, started, &context, &prepared)
}

fn run_prepared_langfuse_attempt(
    logger: &crate::common::logger::Logger,
    timeout: std::time::Duration,
    context: &LangfuseAttemptContext,
    prepared: &PreparedLangfuseSend,
) -> Result<(), OtelPostError> {
    let _http_timing = WorkerPhaseGuard::new("http");
    let (attempt_id, started) = begin_langfuse_attempt(logger, timeout, context);
    finish_langfuse_attempt(logger, &attempt_id, started, context, prepared)
}

fn begin_langfuse_attempt(
    logger: &crate::common::logger::Logger,
    timeout: std::time::Duration,
    context: &LangfuseAttemptContext,
) -> (String, std::time::Instant) {
    let attempt_id = otel_span_id();
    let started = std::time::Instant::now();
    logger.info(&format!(
        "event=langfuse_send_start attempt_id={attempt_id} operation={} timeout_ms={}{}",
        context.operation.as_str(),
        timeout.as_millis(),
        context.correlation
    ));
    (attempt_id, started)
}

fn finish_langfuse_attempt(
    logger: &crate::common::logger::Logger,
    attempt_id: &str,
    started: std::time::Instant,
    context: &LangfuseAttemptContext,
    prepared: &PreparedLangfuseSend,
) -> Result<(), OtelPostError> {
    let body_bytes = prepared.body_len;
    match prepared.send_once() {
        Ok(outcome) => {
            logger.info(&format!(
                "event=langfuse_send_end attempt_id={attempt_id} operation={} outcome=success http_status={} elapsed_ms={} body_bytes={}{}",
                context.operation.as_str(),
                outcome.status,
                started.elapsed().as_millis(),
                body_bytes,
                context.correlation
            ));
            Ok(())
        }
        Err(failure) => {
            let status = failure.status.map_or_else(String::new, |status| {
                format!(
                    " http_status={status} http_status_retryable={}",
                    otlp_http_status_is_retryable(status)
                )
            });
            logger.error(&format!(
                "event=langfuse_send_end attempt_id={attempt_id} operation={} outcome=failure error_kind={} elapsed_ms={}{} body_bytes={}{}",
                context.operation.as_str(),
                failure.kind.as_str(),
                started.elapsed().as_millis(),
                status,
                body_bytes,
                context.correlation
            ));
            Err(failure.error)
        }
    }
}

// --- System helpers ---

fn write_trace_jsonl_if_debug(
    body: &Value,
    logger: &crate::common::logger::Logger,
    truncate: bool,
    session_id: &str,
    transcript_path: &str,
) {
    if !crate::common::hook::env_is_truthy("CCHOOKS_DEBUG") {
        return;
    }

    let path = resolve_trace_jsonl_path(session_id, transcript_path);
    if let Some(parent) = path.parent() {
        if let Err(err) = crate::common::dirs::ensure_private_dir(parent) {
            logger.debug(&format!(
                "Failed to create OTel debug log dir {}: {}",
                parent.display(),
                err
            ));
            return;
        }
    }

    let line = match serde_json::to_string(body) {
        Ok(line) => line,
        Err(err) => {
            logger.debug(&format!(
                "Failed to serialize OTel debug JSONL line: {}",
                err
            ));
            return;
        }
    };

    // New session: truncate (overwrite); subsequent turns: append
    let mut file = if truncate {
        match OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&path)
        {
            Ok(file) => file,
            Err(err) => {
                logger.debug(&format!(
                    "Failed to open OTel debug JSONL file {}: {}",
                    path.display(),
                    err
                ));
                return;
            }
        }
    } else {
        match OpenOptions::new().append(true).create(true).open(&path) {
            Ok(file) => file,
            Err(err) => {
                logger.debug(&format!(
                    "Failed to open OTel debug JSONL file {}: {}",
                    path.display(),
                    err
                ));
                return;
            }
        }
    };

    if let Err(err) = writeln!(file, "{}", line) {
        logger.debug(&format!(
            "Failed to write OTel debug JSONL file {}: {}",
            path.display(),
            err
        ));
        return;
    }

    let mode = if truncate {
        "Wrote (new session)"
    } else {
        "Appended"
    };
    logger.debug(&format!("{} OTel trace JSONL to {}", mode, path.display()));
}

fn resolve_trace_jsonl_path(session_id: &str, transcript_path: &str) -> PathBuf {
    let dir = crate::common::dirs::replay_dir();

    // Extract project dir name from transcript_path
    // e.g. "~/.claude/projects/-Users-xxx-yyy/{session}.jsonl" → "-Users-xxx-yyy"
    let project_dir = std::path::Path::new(transcript_path)
        .parent()
        .and_then(|p| p.file_name())
        .and_then(|n| n.to_str())
        .unwrap_or("unknown");

    // Shorten session_id: take first 8 chars
    let short_session = if session_id.len() > 8 {
        &session_id[..8]
    } else {
        session_id
    };

    dir.join(format!("spans_{}_{}.jsonl", project_dir, short_session))
}

/// Look up tool name from transcript file by tool_use_id.
/// First tries to find tool_use in assistant messages, then falls back to
/// inferring from toolUseResult in user messages.
fn lookup_tool_name_from_transcript(transcript_path: &str, tool_use_id: &str) -> Option<String> {
    let path = std::path::Path::new(transcript_path);
    if !path.exists() {
        return None;
    }

    let content = std::fs::read_to_string(path).ok()?;
    for line in content.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(data) = serde_json::from_str::<serde_json::Value>(line) {
            let msg_type = data.get("type").and_then(|v| v.as_str());

            // First: look for assistant messages with tool_use blocks
            if msg_type == Some("assistant") {
                let content_arr = data
                    .get("message")
                    .and_then(|m| m.get("content"))
                    .and_then(|c| c.as_array())?;
                for block in content_arr {
                    if block.get("type").and_then(|v| v.as_str()) == Some("tool_use") {
                        let id = block.get("id").and_then(|v| v.as_str()).unwrap_or("");
                        if id == tool_use_id {
                            return block
                                .get("name")
                                .and_then(|v| v.as_str())
                                .map(|s| s.to_string());
                        }
                    }
                }
            }

            // Second: try to infer from toolUseResult in user messages
            // toolUseResult is at the top level of the message, not inside content blocks
            if msg_type == Some("user") {
                // Check if this message has a tool_result with matching tool_use_id
                let content_arr = data
                    .get("message")
                    .and_then(|m| m.get("content"))
                    .and_then(|c| c.as_array());
                let mut has_matching_result = false;
                if let Some(arr) = content_arr {
                    for block in arr {
                        if block.get("type").and_then(|v| v.as_str()) == Some("tool_result") {
                            let tid = block
                                .get("tool_use_id")
                                .and_then(|v| v.as_str())
                                .unwrap_or("");
                            if tid == tool_use_id {
                                has_matching_result = true;
                                break;
                            }
                        }
                    }
                }

                if has_matching_result {
                    // Try to infer tool name from top-level toolUseResult
                    if let Some(result) = data.get("toolUseResult") {
                        return infer_tool_name_from_result(result);
                    }
                }
            }
        }
    }
    None
}

/// Infer tool name from toolUseResult structure.
fn infer_tool_name_from_result(result: &serde_json::Value) -> Option<String> {
    // Handle string format (error messages like "Error: ...")
    if let Some(s) = result.as_str() {
        if s.starts_with("Error:") {
            return Some("Bash".to_string());
        }
        return None;
    }

    // Check for file operations
    if let Some(file_obj) = result.get("file") {
        if file_obj.get("filePath").is_some() {
            // Check type field or content to determine Read vs Write
            let result_type = result.get("type").and_then(|v| v.as_str());
            if result_type == Some("create") {
                return Some("Write".to_string());
            }
            if result_type == Some("update") || result_type == Some("edit") {
                return Some("Edit".to_string());
            }
            return Some("Read".to_string());
        }
    }

    // Check for command execution (stdout/stderr)
    if result.get("stdout").is_some() || result.get("stderr").is_some() {
        return Some("Bash".to_string());
    }

    // Check for type field
    if let Some(t) = result.get("type").and_then(|v| v.as_str()) {
        match t {
            "text" => return Some("Bash".to_string()), // Default for text output
            "update" | "edit" => return Some("Edit".to_string()),
            "create" => return Some("Write".to_string()),
            "delete" => return Some("Bash".to_string()), // rm command
            "search" | "grep" => return Some("Grep".to_string()),
            "glob" => return Some("Glob".to_string()),
            "list" => return Some("Glob".to_string()),
            _ => {}
        }
    }

    // Check for questions (AskUserQuestion tool)
    if result.get("questions").is_some() {
        return Some("AskUserQuestion".to_string());
    }

    // Check for error field
    if result.get("error").is_some() {
        return Some("Bash".to_string());
    }

    // Infer from keys when type is None
    let keys: Vec<&str> = result.as_object()?.keys().map(|s| s.as_str()).collect();

    // Grep: has numMatches, content (matched lines)
    if keys.contains(&"numMatches") || keys.contains(&"content") && keys.contains(&"numLines") {
        return Some("Grep".to_string());
    }

    // Glob: has filenames, numFiles, (no stdout/stderr)
    if keys.contains(&"filenames")
        && keys.contains(&"numFiles")
        && !keys.contains(&"stdout")
        && !keys.contains(&"content")
    {
        return Some("Glob".to_string());
    }

    // Bash: has stdout/stderr (already handled above, but catch-all for remaining)
    if keys.contains(&"stdout") || keys.contains(&"stderr") {
        return Some("Bash".to_string());
    }

    None
}

#[cfg(test)]
pub(crate) fn consume_test_http_request(stream: &mut std::net::TcpStream) {
    use std::io::{BufRead, BufReader, Read};

    stream.set_nonblocking(false).expect("set blocking test stream");
    stream
        .set_read_timeout(Some(std::time::Duration::from_secs(1)))
        .expect("set read timeout");
    let mut reader = BufReader::new(stream);
    let mut content_length = 0;
    loop {
        let mut line = String::new();
        assert!(reader.read_line(&mut line).expect("read request header") > 0);
        if line == "\r\n" {
            break;
        }
        if let Some((name, value)) = line.split_once(':') {
            if name.eq_ignore_ascii_case("content-length") {
                content_length = value.trim().parse().expect("request Content-Length");
            }
        }
    }
    let consumed = std::io::copy(&mut reader.take(content_length), &mut std::io::sink())
        .expect("read request body");
    assert_eq!(consumed, content_length, "request body must be complete");
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::transcript::*;
    use agentic_otel_common::days_from_epoch;
    use serde_json::{json, Value};
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::thread;

    fn spawn_http_response(status: u16, body: &str, delay: std::time::Duration) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind test server");
        let address = listener.local_addr().expect("test server address");
        let body = body.to_string();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept test request");
            consume_test_http_request(&mut stream);
            thread::sleep(delay);
            let reason = match status {
                200 => "OK",
                204 => "No Content",
                302 => "Found",
                400 => "Bad Request",
                500 => "Internal Server Error",
                _ => "Test",
            };
            let response = format!(
                "HTTP/1.1 {status} {reason}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        format!("http://{address}/v1/traces")
    }

    fn spawn_http_responses(statuses: Vec<u16>) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind test server");
        let address = listener.local_addr().expect("test server address");
        thread::spawn(move || {
            for status in statuses {
                let (mut stream, _) = listener.accept().expect("accept test request");
                consume_test_http_request(&mut stream);
                let reason = if (200..300).contains(&status) {
                    "OK"
                } else {
                    "Test Failure"
                };
                let response = format!(
                    "HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                );
                let _ = stream.write_all(response.as_bytes());
            }
        });
        format!("http://{address}/v1/traces")
    }

    fn test_config(host: String) -> LangfuseConfig {
        LangfuseConfig {
            public_key: None,
            secret_key: None,
            host,
            headers: None,
        }
    }

    #[test]
    fn http_fixtures_consume_requests_larger_than_one_tcp_read() {
        for host in [
            spawn_http_response(200, "ok", std::time::Duration::ZERO),
            spawn_http_responses(vec![200]),
        ] {
            let address = host
                .strip_prefix("http://")
                .unwrap()
                .split('/')
                .next()
                .unwrap();
            let mut stream = TcpStream::connect(address).expect("connect fixture");
            stream
                .set_read_timeout(Some(std::time::Duration::from_secs(1)))
                .unwrap();
            let body = vec![b'x'; 32 * 1024];
            let mut request = format!(
                "POST /v1/traces HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            )
            .into_bytes();
            request.extend(body);
            stream.write_all(&request).expect("write complete request");
            let mut response = String::new();
            stream
                .read_to_string(&mut response)
                .expect("fixture must consume the body before closing without a TCP reset");
            assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
        }
    }

    #[test]
    fn hook_phase_logs_mark_sync_and_async_execution() {
        let dir = tempfile::tempdir().unwrap();
        let log_file = dir.path().join("timing.log");
        let logger = crate::common::logger::Logger::new(log_file.clone(), true);

        log_parent_phase(&logger, "total", 9);
        install_worker_log_context(&logger, "PostToolUse", "toolu-timing", "worker-test");
        log_worker_phase(&logger, "http", 12);
        log_worker_phase(&logger, "metrics_http", 18);
        log_worker_http_total(&logger);
        clear_worker_log_context();

        let log = std::fs::read_to_string(log_file).unwrap();
        assert!(log.contains(
            "hook_timing {\"component\":\"parent\",\"phase\":\"total\",\"elapsed_ms\":9,\"execution\":\"sync\"}"
        ));
        assert!(log.contains(
            "hook_timing {\"component\":\"worker\",\"phase\":\"http\",\"elapsed_ms\":12,\"execution\":\"async\"}"
        ));
        assert!(log.contains(
            "hook_timing {\"component\":\"worker\",\"phase\":\"http_total\",\"elapsed_ms\":30,\"execution\":\"async\"}"
        ));
    }

    #[test]
    fn truncation_log_fields_are_structured_and_payload_safe() {
        let stats = TruncationStats::new(
            "truncate_value",
            "tool.output",
            4096,
            1024,
            2048,
            true,
            "hard_limit",
            2,
            true,
            12,
        );
        let line = format_truncation_log_fields(
            &stats,
            "tool_export",
            "PostToolUse",
            "toolu_safe",
            "worker-1",
            "tool.output",
        );

        for required in [
            "operation",
            "hook_event",
            "event_id",
            "worker_id",
            "field_kind",
            "elapsed_ms",
            "original_bytes",
            "kept_bytes",
            "limit_bytes",
            "truncated",
            "reason",
            "iterations",
            "hash_present",
        ] {
            assert!(line.contains(required), "missing field {required}: {line}");
        }
        for prohibited in [
            "tool_input",
            "tool_output",
            "request_body",
            "Authorization",
            "secret",
            "sha256",
        ] {
            assert!(
                !line.contains(prohibited),
                "truncation log leaked prohibited token {prohibited}: {line}"
            );
        }
    }

    #[test]
    fn observed_truncation_emits_safe_log_from_real_call_path() {
        let dir = tempfile::tempdir().unwrap();
        let log_file = dir.path().join("worker.log");
        let logger = crate::common::logger::Logger::new(log_file.clone(), true);
        install_worker_log_context(&logger, "PostToolUse", "toolu-observed", "worker-test");
        let secret = format!("TOP-SECRET-MARKER{}", "x".repeat(4_194_400));
        let (_value, metadata) =
            truncate_value_observed(&Value::String(secret), "agentic.tool.output");
        clear_worker_log_context();

        assert_eq!(metadata["truncated"], true);
        let log = std::fs::read_to_string(log_file).unwrap();
        assert!(log.contains("truncation"));
        assert!(log.contains("execution=async"));
        assert!(log.contains("\"hook_event\":\"PostToolUse\""));
        assert!(log.contains("\"event_id\":\"toolu-observed\""));
        assert!(log.contains("\"truncated\":true"));
        assert!(!log.contains("TOP-SECRET-MARKER"));
    }

    // --- iso_to_nanos ---
    #[test]
    fn test_iso_to_nanos_valid_timestamp() {
        let result = iso_to_nanos("2026-03-27T14:30:00.123Z");
        let nanos: i128 = result.parse().expect("should be a valid number");
        assert!(nanos > 0);
    }

    #[test]
    fn test_iso_to_nanos_without_fraction() {
        let result = iso_to_nanos("2026-03-27T14:30:00Z");
        let nanos: i128 = result.parse().expect("should be a valid number");
        assert!(nanos > 0);
    }

    #[test]
    fn test_iso_to_nanos_empty_falls_back() {
        let result = iso_to_nanos("");
        let nanos: i128 = result.parse().expect("should be a valid number");
        assert!(nanos > 0, "empty string should fall back to current time");
    }

    #[test]
    fn test_iso_to_nanos_invalid_falls_back() {
        let result = iso_to_nanos("not-a-timestamp");
        let nanos: i128 = result.parse().expect("should be a valid number");
        assert!(nanos > 0, "invalid string should fall back to current time");
    }

    #[test]
    fn test_iso_to_nanos_epoch() {
        let result = iso_to_nanos("1970-01-01T00:00:00.000Z");
        assert_eq!(result, "0");
    }

    #[test]
    fn test_resolve_trace_jsonl_path_honors_replay_override() {
        let old_debug_dir = crate::common::env::var("CCHOOKS_REPLAY_DIR").ok();

        std::env::set_var("CCHOOKS_REPLAY_DIR", "/tmp/cchooks-replays");

        let session_id = "abc12345-6789";
        let transcript = "/home/user/.claude/projects/-Users-test-myproject/abc12345-6789.jsonl";
        let path = resolve_trace_jsonl_path(session_id, transcript);
        assert_eq!(
            path,
            std::path::Path::new("/tmp/cchooks-replays/spans_-Users-test-myproject_abc12345.jsonl")
        );

        restore_env("CCHOOKS_REPLAY_DIR", old_debug_dir);
    }

    #[test]
    fn test_write_trace_jsonl_if_debug() {
        let old_debug = crate::common::env::var("CCHOOKS_DEBUG").ok();
        let old_plugin_root = crate::common::env::var("CLAUDE_PLUGIN_ROOT").ok();
        let old_replay_dir = crate::common::env::var("CCHOOKS_REPLAY_DIR").ok();

        let unique = format!(
            "cchooks-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        );
        let plugin_root = std::env::temp_dir().join(unique);
        let session_id = "test1234-5678";
        let transcript = format!(
            "{}/projects/-Users-test-proj/{}.jsonl",
            plugin_root.display(),
            session_id
        );
        let jsonl_path = plugin_root
            .join("replays")
            .join("spans_-Users-test-proj_test1234.jsonl");

        std::env::set_var("CCHOOKS_DEBUG", "true");
        std::env::set_var(
            "CCHOOKS_REPLAY_DIR",
            plugin_root.join("replays").to_string_lossy().as_ref(),
        );
        std::env::set_var("CLAUDE_PLUGIN_ROOT", plugin_root.to_string_lossy().as_ref());

        let logger = crate::common::logger::Logger::new(plugin_root.join("cchooks.log"), true);

        // First call with truncate=true (new session) — creates file
        write_trace_jsonl_if_debug(&json!({"turn": 1}), &logger, true, session_id, &transcript);
        // Second call with truncate=false — appends
        write_trace_jsonl_if_debug(&json!({"turn": 2}), &logger, false, session_id, &transcript);

        let data = fs::read_to_string(&jsonl_path).expect("jsonl file should exist");
        let lines: Vec<&str> = data.lines().collect();
        assert_eq!(lines.len(), 2);
        assert_eq!(
            serde_json::from_str::<Value>(lines[0]).unwrap(),
            json!({"turn": 1})
        );
        assert_eq!(
            serde_json::from_str::<Value>(lines[1]).unwrap(),
            json!({"turn": 2})
        );

        // New session call with truncate=true — overwrites previous content
        write_trace_jsonl_if_debug(
            &json!({"turn": "new"}),
            &logger,
            true,
            session_id,
            &transcript,
        );

        let data2 = fs::read_to_string(&jsonl_path).expect("jsonl file should exist");
        let lines2: Vec<&str> = data2.lines().collect();
        assert_eq!(lines2.len(), 1, "truncate should overwrite old data");
        assert_eq!(
            serde_json::from_str::<Value>(lines2[0]).unwrap(),
            json!({"turn": "new"})
        );

        let _ = fs::remove_file(plugin_root.join("cchooks.log"));
        let _ = fs::remove_dir_all(&plugin_root);

        restore_env("CCHOOKS_DEBUG", old_debug);
        restore_env("CLAUDE_PLUGIN_ROOT", old_plugin_root);
        restore_env("CCHOOKS_REPLAY_DIR", old_replay_dir);
    }

    fn restore_env(key: &str, value: Option<String>) {
        if let Some(value) = value {
            std::env::set_var(key, value);
        } else {
            std::env::remove_var(key);
        }
    }

    fn attr_string<'a>(attrs: &'a [Value], key: &str) -> Option<&'a str> {
        attrs.iter().find_map(|attr| {
            if attr["key"].as_str() == Some(key) {
                attr["value"]["stringValue"].as_str()
            } else {
                None
            }
        })
    }

    // --- days_from_epoch ---
    #[test]
    fn test_days_from_epoch_at_epoch() {
        assert_eq!(days_from_epoch(1970, 1, 1), 0);
    }

    #[test]
    fn test_days_from_epoch_day_after() {
        assert_eq!(days_from_epoch(1970, 1, 2), 1);
    }

    #[test]
    fn test_days_from_epoch_year_2000() {
        let days = days_from_epoch(2000, 1, 1);
        assert_eq!(days, 10957); // well-known value
    }

    // --- otel_trace_id ---
    #[test]
    fn test_otel_trace_id_length() {
        let trace_id = otel_trace_id();
        assert_eq!(trace_id.len(), 32, "trace ID should be 32 hex chars");
    }

    #[test]
    fn test_otel_trace_id_hex_chars() {
        let trace_id = otel_trace_id();
        assert!(trace_id.chars().all(|c| c.is_ascii_hexdigit()));
    }

    // --- otel_span_id ---
    #[test]
    fn test_otel_span_id_length() {
        let span_id = otel_span_id();
        assert_eq!(span_id.len(), 16, "span ID should be 16 hex chars");
    }

    #[test]
    fn test_otel_span_id_hex_chars() {
        let span_id = otel_span_id();
        assert!(span_id.chars().all(|c| c.is_ascii_hexdigit()));
    }

    // --- helper to build test data ---
    fn make_test_turn() -> Turn {
        Turn {
            chat_id: None,
            trigger: Default::default(),
            user_timestamp: "2026-03-27T14:30:00.000Z".to_string(),
            user_text: "Hello world".to_string(),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: "2026-03-27T14:30:01.000Z".to_string(),
                end_timestamp: "2026-03-27T14:30:02.000Z".to_string(),
                text: "Hi there!".to_string(),
                media_content: serde_json::Value::Null,
                model: "claude-sonnet-4-20250514".to_string(),
                usage: TokenUsage {
                    input: Some(100),
                    output: Some(50),
                    cache_creation_input_tokens: None,
                    cache_read_input_tokens: Some(80),
                },
                tool_uses: vec![],
                message_id: "msg_001".to_string(),
                stop_reason: "end_turn".to_string(),
                thinking_blocks: vec![],
                is_synthetic: false,
            }],
            tool_results: vec![],
            skill_name: None,
            api_errors: vec![],
            terminal_llm_error: None,
            compactions: vec![],
            cancelled: false,
            task_notifications: Vec::new(),
        }
    }

    fn exported_spans(request: &Value) -> &[Value] {
        request["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .expect("OTLP request should contain spans")
    }

    #[test]
    fn realtime_tool_builder_uses_persisted_identity_and_payload_only_fields() {
        let payload = json!({
            "session_id": "session-1",
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_use_id": "tool-1",
            "tool_input": {"file_path": "/tmp/a"},
            "tool_response": {"success": true}
        });
        let trace_id = "a".repeat(32);
        let root_span_id = "b".repeat(16);
        let tool_span_id = "c".repeat(16);
        let body = build_realtime_tool_export_request(
            &payload,
            &trace_id,
            &root_span_id,
            &tool_span_id,
            "session-1",
            "user",
            "test",
            &TimingData::new(),
            true,
        )
        .unwrap();
        let spans = exported_spans(&body);
        assert_eq!(spans.len(), 1);
        assert_eq!(spans[0]["traceId"], trace_id);
        assert_eq!(spans[0]["spanId"], tool_span_id);
        assert_eq!(spans[0]["parentSpanId"], root_span_id);
        assert!(spans[0]["attributes"]
            .as_array()
            .unwrap()
            .iter()
            .any(|attr| {
                attr["key"] == "gen_ai.chat.id" && attr["value"]["stringValue"] == trace_id
            }));
    }

    #[test]
    fn stop_context_reuses_root_and_filters_emitted_realtime_tool() {
        let mut turn = make_test_turn();
        turn.assistants[0].tool_uses.push(ToolUse {
            name: "Write".to_string(),
            id: "tool-1".to_string(),
            input: json!({"file_path": "/tmp/a"}),
        });
        turn.tool_results.push(ToolResultMessage {
            timestamp: "2026-03-27T14:30:03.000Z".to_string(),
            items: vec![ToolResultItem {
                tool_use_id: "tool-1".to_string(),
                content: json!({"success": true}),
                is_error: false,
                tool_denial_kind: None,
                tool_use_result_text: None,
                cancellation_reason: None,
                tool_name: Some("Write".to_string()),
                timestamp: "2026-03-27T14:30:03.000Z".to_string(),
                agent_id: None,
                agent_type: None,
                is_async: false,
            }],
        });
        let root_span_id = "d".repeat(16);
        let context = StoredTraceContext {
            trace_id: "e".repeat(32),
            root_span_id: Some(root_span_id.clone()),
            traceparent: None,
            sofa_traceid: None,
            sofa_rpcid: None,
            tool_use_ids: vec!["tool-1".to_string()],
            tool_span_ids: HashMap::from([("tool-1".to_string(), "f".repeat(16))]),
            emitted_tool_use_ids: vec!["tool-1".to_string()],
            generation_span_ids: HashMap::new(),
            accounted_generation_ids: Vec::new(),
            max_accounted_child_end_time_unix_nano: None,
        };
        let spans = collect_turn_spans(
            &turn,
            1,
            "session-1",
            "user",
            &iso_to_nanos("2026-03-27T14:30:03.000Z"),
            "",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            Some(&context),
        );
        assert!(spans.iter().any(|span| span.span_id == root_span_id));
        assert!(!spans.iter().any(|span| {
            span.attributes.iter().any(|attr| {
                attr.key == "gen_ai.tool.call.id"
                    && serde_json::to_value(&attr.value)
                        .ok()
                        .and_then(|value| value.get("stringValue").cloned())
                        == Some(Value::String("tool-1".to_string()))
            })
        }));
    }

    #[test]
    fn empty_tool_id_is_never_treated_as_accounted() {
        let context = StoredTraceContext {
            emitted_tool_use_ids: vec![String::new(), "tool-1".to_string()],
            ..StoredTraceContext::default()
        };
        assert!(!stored_tool_is_accounted("", Some(&context)));
        assert!(stored_tool_is_accounted("tool-1", Some(&context)));

        let mut turn = make_test_turn();
        turn.assistants[0].tool_uses.push(ToolUse {
            name: "Read".to_string(),
            id: String::new(),
            input: json!({"path": "/tmp/a"}),
        });
        let spans = collect_turn_spans(
            &turn,
            1,
            "session-empty-tool-id",
            "user",
            &iso_to_nanos("2026-03-27T14:30:03.000Z"),
            "",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            Some(&context),
        );
        assert!(spans.iter().any(|span| span.name == "tool Read"));
    }

    #[test]
    fn accounted_generation_end_still_bounds_turn_root() {
        let turn = make_test_turn();
        let context = StoredTraceContext {
            trace_id: "a".repeat(32),
            root_span_id: Some("b".repeat(16)),
            accounted_generation_ids: vec![turn.assistants[0].message_id.clone()],
            ..StoredTraceContext::default()
        };
        let spans = collect_turn_spans(
            &turn,
            1,
            "session-accounted-generation",
            "user",
            &iso_to_nanos("2026-03-27T14:30:01.500Z"),
            "",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            Some(&context),
        );
        assert_eq!(spans.len(), 1);
        assert_eq!(
            spans[0].end_time_unix_nano,
            iso_to_nanos("2026-03-27T14:30:02.000Z")
        );
    }

    #[test]
    fn stop_reuses_generation_identity_and_suppresses_only_accounted_messages() {
        let turn = make_test_turn();
        let message_id = turn.assistants[0].message_id.clone();
        let persisted_span_id = "7".repeat(16);
        let mut context = StoredTraceContext {
            trace_id: "8".repeat(32),
            root_span_id: Some("9".repeat(16)),
            traceparent: None,
            sofa_traceid: None,
            sofa_rpcid: None,
            tool_use_ids: Vec::new(),
            tool_span_ids: HashMap::new(),
            emitted_tool_use_ids: Vec::new(),
            generation_span_ids: HashMap::from([(message_id.clone(), persisted_span_id.clone())]),
            accounted_generation_ids: Vec::new(),
            max_accounted_child_end_time_unix_nano: None,
        };
        let spans = collect_turn_spans(
            &turn,
            1,
            "session-1",
            "user",
            &iso_to_nanos("2026-03-27T14:30:03.000Z"),
            "",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            Some(&context),
        );
        assert!(spans.iter().any(|span| span.span_id == persisted_span_id));

        context.accounted_generation_ids.push(message_id);
        let suppressed = collect_turn_spans(
            &turn,
            1,
            "session-1",
            "user",
            &iso_to_nanos("2026-03-27T14:30:03.000Z"),
            "",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            Some(&context),
        );
        assert!(!suppressed.iter().any(|span| {
            span.attributes.iter().any(|attribute| {
                attribute.key == "gen_ai.span.kind"
                    && serde_json::to_value(&attribute.value)
                        .ok()
                        .and_then(|value| value.get("stringValue").cloned())
                        == Some(Value::String("LLM".to_string()))
            })
        }));
    }

    #[test]
    fn accounted_structured_output_children_still_extend_turn_root_end() {
        let mut turn = make_test_turn();
        turn.assistants[0].text.clear();
        turn.assistants[0].tool_uses.push(ToolUse {
            name: "StructuredOutput".to_string(),
            id: "tool-structured".to_string(),
            input: json!({"visible_markdown": "done"}),
        });
        turn.assistants[0].stop_reason = "tool_use".to_string();
        turn.tool_results.push(ToolResultMessage {
            timestamp: "2026-03-27T14:30:07.000Z".to_string(),
            items: vec![ToolResultItem {
                tool_use_id: "tool-structured".to_string(),
                content: json!("Structured output provided successfully"),
                is_error: false,
                tool_denial_kind: None,
                tool_use_result_text: Some("Structured output provided successfully".to_string()),
                cancellation_reason: None,
                tool_name: Some("StructuredOutput".to_string()),
                timestamp: "2026-03-27T14:30:07.000Z".to_string(),
                agent_id: None,
                agent_type: None,
                is_async: false,
            }],
        });

        let message_id = turn.assistants[0].message_id.clone();
        let context = StoredTraceContext {
            trace_id: "a".repeat(32),
            root_span_id: Some("b".repeat(16)),
            traceparent: None,
            sofa_traceid: None,
            sofa_rpcid: None,
            tool_use_ids: vec!["tool-structured".to_string()],
            tool_span_ids: HashMap::from([("tool-structured".to_string(), "c".repeat(16))]),
            emitted_tool_use_ids: vec!["tool-structured".to_string()],
            generation_span_ids: HashMap::from([(message_id.clone(), "d".repeat(16))]),
            accounted_generation_ids: vec![message_id],
            max_accounted_child_end_time_unix_nano: Some(iso_to_nanos("2026-03-27T14:30:08.000Z")),
        };
        let spans = collect_turn_spans(
            &turn,
            1,
            "session-structured",
            "testuser",
            &iso_to_nanos("2026-03-27T14:30:03.000Z"),
            "",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            Some(&context),
        );

        assert_eq!(
            spans.len(),
            1,
            "accounted realtime generation/tool spans must not be exported twice"
        );
        assert!(
            spans[0].end_time_unix_nano.parse::<u128>().unwrap()
                >= iso_to_nanos("2026-03-27T14:30:08.000Z")
                    .parse::<u128>()
                    .unwrap(),
            "Turn root must contain the filtered StructuredOutput tool span"
        );

        let output = span_str_attr(&spans[0], "gen_ai.output.messages")
            .and_then(|value| serde_json::from_str::<Value>(&value).ok())
            .expect("Turn root should preserve output messages");
        assert_eq!(output[0]["finish_reason"], "tool_use");
        assert_eq!(
            output[0]["tool_calls"][0]["function"]["name"],
            "StructuredOutput"
        );
    }

    // --- build_root_span ---
    #[test]
    fn test_build_root_span_structure() {
        let mut turn = make_test_turn();
        turn.trigger = TurnTrigger::Interactive;
        let trace_id = "a".repeat(32);
        let span_id = "b".repeat(16);
        let now_ns = "1234567890000000000";

        let span = build_root_span(
            &trace_id,
            &span_id,
            "",
            &turn,
            "session-1",
            "chat-1",
            "testuser",
            now_ns,
            1,
            &None,
            "/tmp/test-cwd",
        );
        let span = serde_json::to_value(&span).unwrap();

        assert_eq!(span["traceId"], trace_id);
        assert_eq!(span["spanId"], span_id);
        assert!(
            span.get("parentSpanId").is_none(),
            "root span should omit parentSpanId"
        );
        assert_eq!(span["kind"], 2);
        assert_eq!(span["status"]["code"], 1);
        assert!(span["attributes"].is_array());
        assert!(span["name"].as_str().unwrap().contains("Turn"));
        let root_markers: Vec<_> = span["attributes"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|attribute| attribute["key"] == "_internal.meta.root")
            .collect();
        assert_eq!(root_markers.len(), 1);
        assert_eq!(root_markers[0]["value"]["stringValue"], "1");
        let trigger_attrs: Vec<_> = span["attributes"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|attribute| attribute["key"] == "agentic.turn.trigger")
            .collect();
        assert_eq!(trigger_attrs.len(), 1);
        assert_eq!(trigger_attrs[0]["value"]["stringValue"], "interactive");
    }

    #[test]
    fn test_turn_root_marker_survives_upstream_parent_and_is_not_copied_to_children() {
        let turn = make_test_turn();
        let trace_id = "a".repeat(32);
        let root_span_id = "b".repeat(16);
        let upstream_parent_span_id = "c".repeat(16);
        let context = StoredTraceContext {
            trace_id: trace_id.clone(),
            root_span_id: Some(root_span_id.clone()),
            traceparent: Some(format!("00-{trace_id}-{upstream_parent_span_id}-01")),
            sofa_traceid: None,
            sofa_rpcid: None,
            tool_use_ids: Vec::new(),
            tool_span_ids: HashMap::new(),
            emitted_tool_use_ids: Vec::new(),
            generation_span_ids: HashMap::new(),
            accounted_generation_ids: Vec::new(),
            max_accounted_child_end_time_unix_nano: None,
        };

        let spans = collect_turn_spans(
            &turn,
            1,
            "session-1",
            "testuser",
            "1234567890000000000",
            "/tmp/test-cwd",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            Some(&context),
        );

        let root = &spans[0];
        assert_eq!(root.span_id, root_span_id);
        assert_eq!(root.parent_span_id, upstream_parent_span_id);
        assert_eq!(
            span_str_attr(root, "_internal.meta.root").as_deref(),
            Some("1")
        );
        assert!(
            spans[1..]
                .iter()
                .all(|span| span_str_attr(span, "_internal.meta.root").is_none()),
            "only the logical Turn root may carry _internal.meta.root"
        );
    }

    #[test]
    fn test_agentic_turn_trigger_is_root_only_and_not_resource() {
        let mut turn = make_test_turn();
        turn.trigger = TurnTrigger::Programmatic;
        turn.assistants[0].tool_uses.push(ToolUse {
            name: "Read".to_string(),
            id: "tool-1".to_string(),
            input: json!({"file_path": "/tmp/a"}),
        });
        let spans = collect_turn_spans(
            &turn,
            1,
            "session-1",
            "testuser",
            &iso_to_nanos("2026-03-27T14:30:03.000Z"),
            "/tmp/test-cwd",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            None,
        );

        let root = &spans[0];
        assert_eq!(
            span_str_attr(root, "agentic.turn.trigger").as_deref(),
            Some("programmatic")
        );
        assert!(
            spans[1..]
                .iter()
                .all(|span| span_str_attr(span, "agentic.turn.trigger").is_none()),
            "agentic.turn.trigger must only be emitted on the root Turn span"
        );

        let request = cchooks_wrap_export_request(spans, "test");
        let resource_attrs = request["resourceSpans"][0]["resource"]["attributes"]
            .as_array()
            .expect("resource attributes");
        assert!(
            resource_attrs
                .iter()
                .all(|attr| attr["key"] != "agentic.turn.trigger"),
            "agentic.turn.trigger must not be emitted as a Resource attribute"
        );
    }

    #[test]
    fn test_agentic_turn_trigger_matches_live_and_replay_adapters() {
        let mut turn = make_test_turn();
        turn.trigger = TurnTrigger::Queued;
        let timing = TimingData::new();
        let replay = build_otel_export_request_for_turn(
            &turn,
            1,
            "session-1",
            "testuser",
            "test",
            "/tmp/session.jsonl",
            &timing,
        );
        let live = build_otel_export_request_for_turn_with_full_context(
            &turn,
            1,
            "session-1",
            "testuser",
            &test_config("http://127.0.0.1:4319".to_string()),
            "2026-03-27T14:30:03.000Z",
            "test",
            "/tmp/test-cwd",
            "/tmp/session.jsonl",
            &timing,
            &SubagentTimingData::new(),
            None,
        );

        for request in [&replay, &live] {
            let root = exported_spans(request)
                .iter()
                .find(|span| is_root_chat_export_span(span))
                .expect("root CHAT span");
            let triggers = root["attributes"]
                .as_array()
                .expect("root attributes")
                .iter()
                .filter(|attribute| attribute["key"] == "agentic.turn.trigger")
                .collect::<Vec<_>>();
            assert_eq!(triggers.len(), 1);
            assert_eq!(triggers[0]["value"]["stringValue"], "queued");
        }
    }

    #[test]
    fn queued_worker_traceparent_uses_the_thread_local_env_snapshot() {
        let turn = make_test_turn();
        let trace_id = "d".repeat(32);
        let parent_span_id = "e".repeat(16);
        let env = std::collections::BTreeMap::from([(
            "TRACEPARENT".to_string(),
            format!("00-{trace_id}-{parent_span_id}-01"),
        )]);

        let spans = crate::common::env::with_worker_env(&env, || {
            collect_turn_spans(
                &turn,
                1,
                "session-1",
                "testuser",
                "1234567890000000000",
                "/tmp/test-cwd",
                "",
                &TimingData::new(),
                &SubagentTimingData::new(),
                None,
            )
        });

        assert_eq!(spans[0].trace_id, trace_id);
        assert_eq!(spans[0].parent_span_id, parent_span_id);
    }

    #[test]
    fn test_build_root_span_with_skill_no_skill_attr() {
        let mut turn = make_test_turn();
        turn.skill_name = Some("my-skill".to_string());
        let trace_id = "a".repeat(32);
        let span_id = "b".repeat(16);

        let span = build_root_span(
            &trace_id,
            &span_id,
            "",
            &turn,
            "session-1",
            "chat-1",
            "testuser",
            "0",
            1,
            &turn.skill_name,
            "/tmp/test-cwd",
        );
        let span = serde_json::to_value(&span).unwrap();

        // Root span should NOT have agentic.skill.name — skill info lives in agentic.tool.* on tool spans
        let attrs = span["attributes"].as_array().unwrap();
        assert!(
            !attrs
                .iter()
                .any(|a| a["key"].as_str().is_some_and(|k| k.contains("skill"))),
            "root span should not have any skill-specific attribute"
        );
    }

    #[test]
    fn test_collect_turn_spans_command_skill_has_duration() {
        let mut turn = make_test_turn();
        turn.skill_name = Some("my-skill".to_string());
        let now_ns = iso_to_nanos("2026-03-27T14:30:05.000Z");

        let spans = collect_turn_spans(
            &turn,
            1,
            "session-1",
            "testuser",
            &now_ns,
            "",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            None::<&StoredTraceContext>,
        );

        let skill_span = spans.iter().find(|s| s.name == "skill my-skill").unwrap();
        let skill_json = serde_json::to_value(skill_span).unwrap();
        let attrs = skill_json["attributes"].as_array().unwrap();
        let has_duration = attrs
            .iter()
            .any(|a| a["key"] == "agentic.tool.duration_ms" && a["value"]["stringValue"] == "5000");
        assert!(
            has_duration,
            "command-style skill span should have duration_ms"
        );
    }

    #[test]
    fn test_collect_turn_spans_async_subagent_backfill() {
        use crate::common::transcript::TaskNotification;

        let agent_tool = |id: &str, extra: Value| {
            let mut input = json!({"description": format!("agent {}", id), "prompt": "go"});
            if let Value::Object(map) = extra {
                for (k, v) in map {
                    input[k] = v;
                }
            }
            ToolUse {
                name: "Agent".to_string(),
                id: id.to_string(),
                input,
            }
        };
        let assistant = |id: &str, ts: &str, tool: ToolUse| AssistantMessage {
            timestamp: ts.to_string(),
            end_timestamp: ts.to_string(),
            text: String::new(),
            media_content: serde_json::Value::Null,
            model: "k3".to_string(),
            usage: TokenUsage {
                input: Some(10),
                output: Some(5),
                cache_creation_input_tokens: None,
                cache_read_input_tokens: None,
            },
            tool_uses: vec![tool],
            message_id: id.to_string(),
            stop_reason: "tool_use".to_string(),
            thinking_blocks: vec![],
            is_synthetic: false,
        };
        let result_item = |id: &str, agent_id: &str, is_async: bool, ts: &str| ToolResultItem {
            tool_use_id: id.to_string(),
            content: json!("launched"),
            is_error: false,
            tool_denial_kind: None,
            tool_use_result_text: None,
            cancellation_reason: None,
            tool_name: Some("Agent".to_string()),
            timestamp: ts.to_string(),
            agent_id: Some(agent_id.to_string()),
            agent_type: None,
            is_async,
        };

        let mut turn = make_test_turn();
        turn.assistants = vec![
            assistant(
                "msg_a1",
                "2026-07-30T08:00:05.000Z",
                agent_tool("tool_async1", json!({})),
            ),
            assistant(
                "msg_a2",
                "2026-07-30T08:00:10.000Z",
                agent_tool("tool_async2", json!({})),
            ),
            assistant(
                "msg_s1",
                "2026-07-30T08:00:20.000Z",
                agent_tool("tool_sync1", json!({"run_in_background": false})),
            ),
        ];
        turn.tool_results = vec![crate::common::transcript::ToolResultMessage {
            timestamp: "2026-07-30T08:00:30.000Z".to_string(),
            items: vec![
                result_item("tool_async1", "agent_a", true, "2026-07-30T08:00:06.000Z"),
                result_item("tool_async2", "agent_b", true, "2026-07-30T08:00:11.000Z"),
                result_item("tool_sync1", "agent_c", false, "2026-07-30T08:00:30.000Z"),
            ],
        }];
        turn.task_notifications = vec![TaskNotification {
            task_id: "agent_a".to_string(),
            tool_use_id: "tool_async1".to_string(),
            status: "completed".to_string(),
            result: "[{\"file\":\"x.rs\"}]".to_string(),
            subagent_tokens: Some(12345),
            tool_uses: Some(7),
            duration_ms: Some(60000),
            timestamp: "2026-07-30T08:01:10.000Z".to_string(),
        }];

        let now_ns = iso_to_nanos("2026-07-30T08:01:20.000Z");
        let spans = collect_turn_spans(
            &turn,
            1,
            "session-1",
            "testuser",
            &now_ns,
            "",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            None::<&StoredTraceContext>,
        );

        let attr_str = |span: &OtelSpan, key: &str| -> Option<String> {
            span.attributes.iter().find_map(|a| {
                if a.key == key {
                    match &a.value {
                        agentic_otel_common::otlp::OtelAnyValue::String { string_value } => {
                            Some(string_value.clone())
                        }
                        agentic_otel_common::otlp::OtelAnyValue::Int { int_value } => {
                            Some(int_value.to_string())
                        }
                        _ => None,
                    }
                } else {
                    None
                }
            })
        };
        let by_call_id = |id: &str| {
            spans
                .iter()
                .find(|s| attr_str(s, "gen_ai.tool.call.id").as_deref() == Some(id))
                .unwrap_or_else(|| panic!("span for {} should exist", id))
        };

        // Async with notification: mode, real completion data backfilled.
        let s1 = by_call_id("tool_async1");
        assert_eq!(s1.name, "agent AsyncAgent");
        assert_eq!(
            attr_str(s1, "agentic.subagent.mode").as_deref(),
            Some("async")
        );
        assert_eq!(
            attr_str(s1, "agentic.subagent.status").as_deref(),
            Some("completed")
        );
        assert_eq!(
            attr_str(s1, "agentic.subagent.tokens").as_deref(),
            Some("12345")
        );
        assert_eq!(
            attr_str(s1, "agentic.subagent.tool_uses").as_deref(),
            Some("7")
        );
        assert!(attr_str(s1, "agentic.subagent.result")
            .map(|r| r.contains("x.rs"))
            .unwrap_or(false));
        assert_eq!(
            s1.end_time_unix_nano,
            iso_to_nanos("2026-07-30T08:01:10.000Z"),
            "end time should come from the task notification"
        );
        let dur: u128 = attr_str(s1, "agentic.tool.duration_ms")
            .and_then(|d| d.parse().ok())
            .unwrap_or(0);
        assert!(dur >= 60_000, "duration should cover the agent runtime");

        // Async without notification: marked unknown, not an error.
        let s2 = by_call_id("tool_async2");
        assert_eq!(s2.name, "agent AsyncAgent");
        assert_eq!(
            attr_str(s2, "agentic.subagent.status").as_deref(),
            Some("unknown")
        );
        assert!(attr_str(s2, "error.type").is_none());

        // Sync: untouched by backfill (no status attribute at all).
        let s3 = by_call_id("tool_sync1");
        assert_eq!(s3.name, "agent SyncAgent");
        assert_eq!(
            attr_str(s3, "agentic.subagent.mode").as_deref(),
            Some("sync")
        );
        assert!(attr_str(s3, "agentic.subagent.status").is_none());
    }

    // --- build_generation_span ---
    #[test]
    fn test_build_generation_span_structure() {
        let turn = make_test_turn();
        let assistant = &turn.assistants[0];
        let trace_id = "a".repeat(32);
        let parent_span_id = "c".repeat(16);

        let (gen_span_id, span) = build_generation_span(
            &trace_id,
            &parent_span_id,
            &turn,
            0,
            assistant,
            0,
            "0",
            "testuser",
            "test-session",
            "chat-1",
            &TimingData::new(),
        );
        let span = serde_json::to_value(&span).unwrap();

        assert_eq!(gen_span_id.len(), 16);
        assert_eq!(span["traceId"], trace_id);
        assert_eq!(span["parentSpanId"], parent_span_id);
        assert_eq!(span["kind"], 1);

        let attrs = span["attributes"].as_array().unwrap();
        let has_model = attrs.iter().any(|a| a["key"] == "gen_ai.request.model");
        assert!(has_model, "should have gen_ai.request.model attribute");
    }

    #[test]
    fn test_build_generation_span_with_usage() {
        let turn = make_test_turn();
        let assistant = &turn.assistants[0];
        let trace_id = "a".repeat(32);

        let (_, span) = build_generation_span(
            &trace_id,
            "p".repeat(16).as_str(),
            &turn,
            0,
            assistant,
            0,
            "0",
            "testuser",
            "test-session",
            "chat-1",
            &TimingData::new(),
        );
        let span = serde_json::to_value(&span).unwrap();

        let attrs = span["attributes"].as_array().unwrap();
        let has_input_tokens = attrs
            .iter()
            .any(|a| a["key"] == "gen_ai.usage.input_tokens");
        let has_output_tokens = attrs
            .iter()
            .any(|a| a["key"] == "gen_ai.usage.output_tokens");
        assert!(has_input_tokens);
        assert!(has_output_tokens);
    }

    // --- build_tool_spans ---
    #[test]
    fn test_build_tool_spans_empty() {
        let spans = build_tool_spans(
            &"a".repeat(32),
            &"b".repeat(16),
            &Vec::new(),
            &Vec::new(),
            "0",
            "0",
            "testuser",
            "test-session",
            "chat-1",
            None,
            None,
            &TimingData::new(),
        );
        assert!(spans.is_empty());
    }

    #[test]
    fn test_build_tool_spans_single() {
        let tool_use = ToolUse {
            name: "Read".to_string(),
            id: "toolu_001".to_string(),
            input: json!({"path": "test.txt"}),
        };
        let tool_result = ToolResultItem {
            tool_use_id: "toolu_001".to_string(),
            content: json!("file contents here"),
            is_error: false,
            tool_denial_kind: None,
            tool_use_result_text: None,
            cancellation_reason: None,
            tool_name: Some("Read".to_string()),
            timestamp: "2026-04-08T08:42:50.101Z".to_string(),
            agent_id: None,
            agent_type: None,
            is_async: false,
        };

        let spans = build_tool_spans(
            &"a".repeat(32),
            &"b".repeat(16),
            &[tool_use],
            &[tool_result],
            "0",
            "0",
            "testuser",
            "test-session",
            "chat-1",
            None,
            None,
            &TimingData::new(),
        );

        assert_eq!(spans.len(), 1);
        let span0 = serde_json::to_value(&spans[0]).unwrap();
        assert!(span0["name"].as_str().unwrap().contains("Read"));
        assert_eq!(span0["kind"], 1);
        // Builtin tool should NOT have agentic.tool.* attributes
        let attrs = span0["attributes"].as_array().unwrap();
        assert!(
            !attrs.iter().any(|a| a["key"] == "agentic.tool.type"),
            "builtin tool should not have agentic.tool.type"
        );
    }

    #[test]
    fn test_build_subagent_child_spans_uses_subagent_timing() {
        let turn = Turn {
            chat_id: None,
            trigger: Default::default(),
            user_timestamp: "2026-03-27T14:30:00.000Z".to_string(),
            user_text: "read a file".to_string(),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: "2026-03-27T14:30:01.000Z".to_string(),
                end_timestamp: "2026-03-27T14:30:02.000Z".to_string(),
                text: "I'll read it".to_string(),
                media_content: serde_json::Value::Null,
                model: "claude-sonnet-4-20250514".to_string(),
                usage: TokenUsage {
                    input: Some(10),
                    output: Some(3),
                    cache_creation_input_tokens: Some(500),
                    cache_read_input_tokens: Some(8000),
                },
                tool_uses: vec![ToolUse {
                    name: "Read".to_string(),
                    id: "child-tool".to_string(),
                    input: json!({"file_path": "a.txt"}),
                }],
                message_id: "msg-child".to_string(),
                stop_reason: "tool_use".to_string(),
                thinking_blocks: vec![],
                is_synthetic: false,
            }],
            tool_results: vec![ToolResultMessage {
                timestamp: "2026-03-27T14:30:03.000Z".to_string(),
                items: vec![ToolResultItem {
                    tool_use_id: "child-tool".to_string(),
                    content: json!("contents"),
                    is_error: false,
                    tool_denial_kind: None,
                    tool_use_result_text: None,
                    cancellation_reason: None,
                    tool_name: Some("Read".to_string()),
                    timestamp: "2026-03-27T14:30:03.000Z".to_string(),
                    agent_id: None,
                    agent_type: None,
                    is_async: false,
                }],
            }],
            skill_name: None,
            api_errors: vec![],
            terminal_llm_error: None,
            compactions: vec![],
            cancelled: false,
            task_notifications: Vec::new(),
        };
        let mut timing = TimingData::new();
        timing.insert(
            "child-tool".to_string(),
            crate::hooks::timing::ToolTiming {
                start_ns: "1000000000".to_string(),
                end_ns: Some("3500000000".to_string()),
                success: Some(true),
                tool_name: Some("Read".to_string()),
            },
        );

        let parent_session_id = "test-session";
        let conversation_id = derive_subagent_conversation_id(parent_session_id, "agent-child");
        let spans = build_subagent_child_spans(
            &"a".repeat(32),
            &"b".repeat(16),
            &[turn],
            "testuser",
            &conversation_id,
            "0",
            "4000000000",
            "general-purpose",
            "",
            &timing,
        );

        let tool_span = spans
            .iter()
            .find(|span| {
                span.attributes.iter().any(|attr| {
                    attr.key == "gen_ai.tool.call.id"
                        && matches!(
                            &attr.value,
                            agentic_otel_common::otlp::OtelAnyValue::String { string_value }
                                if string_value == "child-tool"
                        )
                })
            })
            .expect("child tool span should exist");

        assert_eq!(tool_span.start_time_unix_nano, "1000000000");
        assert_eq!(tool_span.end_time_unix_nano, "3500000000");
        let generation_span = spans
            .iter()
            .find(|span| {
                span.attributes.iter().any(|attr| {
                    attr.key == "gen_ai.usage.input_tokens"
                        && matches!(
                            &attr.value,
                            agentic_otel_common::otlp::OtelAnyValue::String { string_value }
                                if string_value == "8510"
                        )
                })
            })
            .expect("subagent generation must aggregate base and cache input tokens");
        assert_eq!(generation_span.name, "chat claude-sonnet-4-20250514");
        assert_ne!(conversation_id, parent_session_id);
        assert!(spans.iter().all(|span| {
            span_str_attr(span, "gen_ai.conversation.id").as_deref()
                == Some(conversation_id.as_str())
        }));
    }

    // --- parse_agentic_tool ---
    #[test]
    fn test_parse_agentic_tool_builtin() {
        let info = parse_agentic_tool("Read", "toolu_001", None);
        assert_eq!(info.tool_type, "builtin");
        assert_eq!(info.name, "Read");
        assert_eq!(info.id, "toolu_001");
    }

    #[test]
    fn test_parse_agentic_tool_mcp() {
        let info = parse_agentic_tool("mcp__code_search__query_code_with_keyword", "toolu_xyz", None);
        assert_eq!(info.tool_type, "mcp");
        assert_eq!(info.name, "code_search.query_code_with_keyword");
        assert_eq!(info.id, "toolu_xyz");
    }

    #[test]
    fn test_parse_agentic_tool_skill() {
        let info = parse_agentic_tool("Skill", "toolu_abc", Some("eve-beta-skill"));
        assert_eq!(info.tool_type, "skill");
        assert_eq!(info.name, "eve-beta-skill");
        assert_eq!(info.id, "toolu_abc");
    }

    #[test]
    fn test_parse_agentic_tool_skill_no_id() {
        let info = parse_agentic_tool("Skill", "", Some("my-skill"));
        assert_eq!(info.tool_type, "skill");
        assert_eq!(info.name, "my-skill");
        assert_eq!(info.id, "skill-my-skill");
    }

    #[test]
    fn test_parse_agentic_tool_skill_unknown() {
        let info = parse_agentic_tool("Skill", "toolu_x", None);
        assert_eq!(info.tool_type, "skill");
        assert_eq!(info.name, "unknown");
    }

    #[test]
    fn test_parse_agentic_tool_task_is_cchooks_local_subagent() {
        let info = parse_agentic_tool("Task", "toolu_task", None);
        assert_eq!(info.tool_type, "subagent");
        assert_eq!(info.name, "Task");
        assert_eq!(info.id, "toolu_task");
    }

    // --- extract_skill_name_from_tool_input ---

    #[test]
    fn test_extract_skill_from_input_three_parts() {
        // publisher:plugin:name → plugin:name
        let input = json!({"skill": "medeval:medeval:init-plugin"});
        assert_eq!(
            extract_skill_name_from_tool_input(&input),
            Some("medeval:init-plugin".to_string())
        );
    }

    #[test]
    fn test_extract_skill_from_input_two_parts() {
        // plugin:name stays as-is
        let input = json!({"skill": "medeval:task-one-shot"});
        assert_eq!(
            extract_skill_name_from_tool_input(&input),
            Some("medeval:task-one-shot".to_string())
        );
    }

    #[test]
    fn test_extract_skill_from_input_simple_name() {
        let input = json!({"skill": "commit"});
        assert_eq!(
            extract_skill_name_from_tool_input(&input),
            Some("commit".to_string())
        );
    }

    #[test]
    fn test_extract_skill_from_input_missing() {
        let input = json!({"args": "foo"});
        assert_eq!(extract_skill_name_from_tool_input(&input), None);
    }

    #[test]
    fn test_extract_skill_from_input_empty() {
        let input = json!({"skill": ""});
        assert_eq!(extract_skill_name_from_tool_input(&input), None);
    }

    // --- build_tool_spans with mcp attrs ---
    #[test]
    fn test_build_tool_spans_with_mcp_attrs() {
        let tool_use = ToolUse {
            name: "mcp__code_search__query_code_with_keyword".to_string(),
            id: "toolu_mcp1".to_string(),
            input: json!({"searchPattern": "hello"}),
        };
        let tool_result = ToolResultItem {
            tool_use_id: "toolu_mcp1".to_string(),
            content: json!("Found 3 matches"),
            is_error: false,
            tool_denial_kind: None,
            tool_use_result_text: None,
            cancellation_reason: None,
            tool_name: Some("mcp__code_search__query_code_with_keyword".to_string()),
            timestamp: "2026-04-08T08:42:51.097Z".to_string(),
            agent_id: None,
            agent_type: None,
            is_async: false,
        };

        let spans = build_tool_spans(
            &"a".repeat(32),
            &"b".repeat(16),
            &[tool_use],
            &[tool_result],
            "0",
            "0",
            "testuser",
            "test-session",
            "chat-1",
            None,
            None,
            &TimingData::new(),
        );

        assert_eq!(spans.len(), 1);
        let span0 = serde_json::to_value(&spans[0]).unwrap();
        let attrs = span0["attributes"].as_array().unwrap();

        let tool_type = attrs.iter().find(|a| a["key"] == "agentic.tool.type");
        assert!(
            tool_type.is_some(),
            "mcp span should have agentic.tool.type"
        );
        assert_eq!(tool_type.unwrap()["value"]["stringValue"], "mcp");

        let tool_name = attrs.iter().find(|a| a["key"] == "agentic.tool.name");
        assert!(
            tool_name.is_some(),
            "mcp span should have agentic.tool.name"
        );
        assert_eq!(
            tool_name.unwrap()["value"]["stringValue"],
            "code_search.query_code_with_keyword"
        );

        let tool_id = attrs.iter().find(|a| a["key"] == "agentic.tool.id");
        assert!(tool_id.is_some(), "mcp span should have agentic.tool.id");
        assert_eq!(tool_id.unwrap()["value"]["stringValue"], "toolu_mcp1");

        // span name should use "mcp" prefix with logical name
        assert_eq!(
            span0["name"].as_str().unwrap(),
            "mcp code_search.query_code_with_keyword"
        );
    }

    // --- build_otel_export_request ---
    #[test]
    fn test_build_otel_export_request_structure() {
        let turn = make_test_turn();
        let config = LangfuseConfig {
            public_key: Some("pk-test".to_string()),
            secret_key: Some("sk-test".to_string()),
            host: "https://test.langfuse.com".to_string(),
            headers: None,
        };

        let request = build_otel_export_request(
            &[turn],
            "session-1",
            "testuser",
            &config,
            "2026-03-27T14:30:02.000Z",
            "1.0.0",
            0,
            "/tmp/test-cwd",
            "/tmp/test.jsonl",
            &TimingData::new(),
        );

        assert!(request["resourceSpans"].is_array());
        let resource_spans = request["resourceSpans"].as_array().unwrap();
        assert_eq!(resource_spans.len(), 1);
        assert!(resource_spans[0]["resource"]["attributes"].is_array());
        assert!(resource_spans[0]["scopeSpans"].is_array());
        let spans = resource_spans[0]["scopeSpans"][0]["spans"]
            .as_array()
            .expect("spans should exist");
        let root_trace_id = spans[0]["traceId"].as_str().expect("root traceId");
        let root_attrs = spans[0]["attributes"].as_array().expect("root attrs");
        let root_chat_id = root_attrs
            .iter()
            .find(|attr| attr["key"] == "gen_ai.chat.id")
            .and_then(|attr| attr["value"]["stringValue"].as_str());
        assert_eq!(root_chat_id, Some(root_trace_id));
    }

    #[test]
    fn test_build_otel_export_request_uses_native_chat_id() {
        let mut turn = make_test_turn();
        turn.chat_id = Some("native-chat-1".to_string());
        let config = LangfuseConfig {
            public_key: Some("pk-test".to_string()),
            secret_key: Some("sk-test".to_string()),
            host: "https://test.langfuse.com".to_string(),
            headers: None,
        };

        let request = build_otel_export_request(
            &[turn],
            "session-1",
            "testuser",
            &config,
            "2026-03-27T14:30:02.000Z",
            "1.0.0",
            0,
            "/tmp/test-cwd",
            "/tmp/test.jsonl",
            &TimingData::new(),
        );

        let spans = request["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .expect("spans should exist");
        for span in spans {
            let attrs = span["attributes"].as_array().expect("span attrs");
            let chat_id = attrs
                .iter()
                .find(|attr| attr["key"] == "gen_ai.chat.id")
                .and_then(|attr| attr["value"]["stringValue"].as_str());
            assert_eq!(chat_id, Some("native-chat-1"));
        }
    }

    #[test]
    fn test_cchooks_otel_attributes_are_added_to_exported_spans() {
        let old_span_attrs = crate::common::env::var("CCHOOKS_OTEL_ATTRIBUTES").ok();
        std::env::set_var(
            "CCHOOKS_OTEL_ATTRIBUTES",
            "custom.cchooks.span=span-value,username=otel-user,_internal.meta.root=spoofed",
        );

        let turn = make_test_turn();
        let config = LangfuseConfig {
            public_key: Some("pk-test".to_string()),
            secret_key: Some("sk-test".to_string()),
            host: "https://test.langfuse.com".to_string(),
            headers: None,
        };
        let request = build_otel_export_request(
            &[turn],
            "session-1",
            "resolved-user",
            &config,
            "2026-03-27T14:30:02.000Z",
            "1.0.0",
            0,
            "/tmp/test-cwd",
            "/tmp/test.jsonl",
            &TimingData::new(),
        );
        let spans = request["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .expect("spans should exist");
        for span in spans {
            let attrs = span["attributes"].as_array().expect("span attrs");
            let marker_count = attrs
                .iter()
                .filter(|attr| attr["key"].as_str() == Some("_internal.meta.root"))
                .count();
            if span["name"] == "Turn 1" {
                assert_eq!(marker_count, 1);
                assert_eq!(attr_string(attrs, "_internal.meta.root"), Some("1"));
            } else {
                assert_eq!(marker_count, 0);
            }
        }
        let span_checks: Vec<(Option<String>, Option<String>, usize)> = spans
            .iter()
            .map(|span| {
                let attrs = span["attributes"].as_array().expect("span attrs");
                let custom = attr_string(attrs, "custom.cchooks.span").map(ToString::to_string);
                let username = attr_string(attrs, "ant.username").map(ToString::to_string);
                assert!(attr_string(attrs, "username").is_none());
                let username_count = attrs
                    .iter()
                    .filter(|attr| attr["key"].as_str() == Some("ant.username"))
                    .count();
                (custom, username, username_count)
            })
            .collect();

        restore_env("CCHOOKS_OTEL_ATTRIBUTES", old_span_attrs);

        assert!(!span_checks.is_empty());
        for (custom, username, username_count) in span_checks {
            assert_eq!(custom.as_deref(), Some("span-value"));
            assert_eq!(username.as_deref(), Some("resolved-user"));
            assert_eq!(
                username_count, 1,
                "CCHOOKS_OTEL_ATTRIBUTES must not duplicate reserved span keys"
            );
        }
    }

    #[test]
    fn test_build_otel_export_request_empty_turns() {
        let config = LangfuseConfig {
            public_key: Some("pk-test".to_string()),
            secret_key: Some("sk-test".to_string()),
            host: "https://test.langfuse.com".to_string(),
            headers: None,
        };

        let request = build_otel_export_request(
            &Vec::new(),
            "session-1",
            "testuser",
            &config,
            "2026-03-27T14:30:02.000Z",
            "1.0.0",
            0,
            "",
            "/tmp/test.jsonl",
            &TimingData::new(),
        );

        // Empty turns should produce an empty resourceSpans
        let resource_spans = request["resourceSpans"].as_array().unwrap();
        assert!(
            resource_spans.is_empty()
                || resource_spans[0]["scopeSpans"]
                    .as_array()
                    .is_none_or(|s| s.is_empty())
        );
    }

    // --- resolve_otel_url ---
    #[test]
    fn test_resolve_otel_url_bare_host() {
        assert_eq!(
            resolve_otel_url("https://langfuse.example.com", "/api/public/otel/v1/traces"),
            "https://langfuse.example.com/api/public/otel/v1/traces"
        );
    }

    #[test]
    fn test_resolve_otel_url_trailing_slash() {
        assert_eq!(
            resolve_otel_url(
                "https://langfuse.example.com/",
                "/api/public/otel/v1/traces"
            ),
            "https://langfuse.example.com/api/public/otel/v1/traces"
        );
    }

    #[test]
    fn test_resolve_otel_url_full_path() {
        assert_eq!(
            resolve_otel_url(
                "https://langfuse.example.com/api/public/otel/v1/traces",
                "/api/public/otel/v1/traces"
            ),
            "https://langfuse.example.com/api/public/otel/v1/traces"
        );
    }

    #[test]
    fn test_resolve_otel_url_custom_proxy() {
        assert_eq!(
            resolve_otel_url(
                "https://proxy.internal/v1/traces",
                "/api/public/otel/v1/traces"
            ),
            "https://proxy.internal/v1/traces"
        );
    }

    #[test]
    fn test_resolve_otel_url_custom_ingest() {
        assert_eq!(
            resolve_otel_url("https://custom.proxy/ingest", "/api/public/otel/v1/traces"),
            "https://custom.proxy/ingest"
        );
    }

    #[test]
    fn otlp_http_retryable_statuses_match_the_protocol_contract() {
        for status in [429, 502, 503, 504] {
            assert!(otlp_http_status_is_retryable(status), "status={status}");
            assert!(!OtelPostError::HttpStatus(status).is_non_retryable_http_status());
        }
        for status in [302, 400, 401, 403, 404, 409, 500, 501, 505] {
            assert!(!otlp_http_status_is_retryable(status), "status={status}");
            assert!(OtelPostError::HttpStatus(status).is_non_retryable_http_status());
        }
        assert!(!OtelPostError::Transport("reset".to_string()).is_non_retryable_http_status());
    }

    #[test]
    fn lifecycle_logs_success_and_failure_without_payload_or_endpoint_leaks() {
        for status in [200, 204, 302, 400, 500] {
            let dir = tempfile::tempdir().expect("tempdir");
            let log_path = dir.path().join(format!("lifecycle-{status}.log"));
            let logger = crate::common::logger::Logger::new(log_path.clone(), true);
            let response_sentinel = "SECRET_RESPONSE";
            let host = spawn_http_response(status, response_sentinel, std::time::Duration::ZERO);
            let config = test_config(host.clone());
            let body = json!({"secret": "SECRET_REQUEST"});
            let result = post_otel_to_langfuse_once_with_context(
                &config,
                &body,
                &logger,
                false,
                "session",
                "",
                std::time::Duration::from_secs(1),
                LangfuseAttemptContext::realtime_tool("ctx\nsecret", "tool\t1"),
            );
            let log = std::fs::read_to_string(log_path).expect("read lifecycle log");
            let lines = log
                .lines()
                .filter(|line| line.contains("event=langfuse_send_"))
                .collect::<Vec<_>>();
            assert_eq!(lines.len(), 2, "status={status} log={log}");
            assert!(lines[0].contains("langfuse_send_start"));
            assert!(lines[0].contains("operation=realtime_tool"));
            assert!(!lines[0].contains("body_bytes="));
            let start_id = lines[0]
                .split("attempt_id=")
                .nth(1)
                .and_then(|value| value.split_whitespace().next());
            let end_id = lines[1]
                .split("attempt_id=")
                .nth(1)
                .and_then(|value| value.split_whitespace().next());
            assert_eq!(start_id, end_id);
            assert!(lines[0].contains("context_id=ctx\\nsecret"));
            assert!(lines[0].contains("tool_use_id=tool\\t1"));
            // Endpoint host and response body never leak, regardless of outcome.
            assert!(!log.contains(&host));
            assert!(!log.contains(response_sentinel));
            if (200..300).contains(&status) {
                assert!(result.is_ok(), "status={status}");
                assert!(lines[1].contains("outcome=success"));
                assert!(lines[1].contains(&format!("http_status={status}")));
                assert!(lines[1].contains("body_bytes="));
                // Success path stays payload-silent: only byte count.
                assert!(!lines[1].contains("body_preview="));
                assert!(!log.contains("SECRET_REQUEST"));
            } else {
                assert_eq!(result, Err(OtelPostError::HttpStatus(status)));
                assert!(lines[1].contains("outcome=failure error_kind=http_status"));
                assert!(lines[1].contains(&format!("http_status={status}")));
                assert!(lines[1].contains("http_status_retryable=false"));
                assert!(lines[1].contains("body_bytes="));
                assert!(!lines[1].contains("body_preview="));
                assert!(!log.contains("SECRET_REQUEST"));
            }
        }
    }

    #[test]
    fn lifecycle_logs_each_retry_attempt_on_the_default_send_path() {
        let dir = tempfile::tempdir().expect("tempdir");
        let log_path = dir.path().join("retry.log");
        let logger = crate::common::logger::Logger::new(log_path.clone(), true);
        let host = spawn_http_responses(vec![500, 200]);
        let result = post_otel_to_langfuse(
            &test_config(host.clone()),
            &json!({"secret": "SECRET_REQUEST"}),
            &logger,
            false,
            "session",
            "",
        );
        assert!(result.is_ok());

        let log = std::fs::read_to_string(log_path).expect("read retry log");
        let lines = log
            .lines()
            .filter(|line| line.contains("event=langfuse_send_"))
            .collect::<Vec<_>>();
        assert_eq!(lines.len(), 4, "log={log}");
        assert!(lines[0].contains("operation=other"));
        assert!(lines[1].contains("outcome=failure error_kind=http_status"));
        assert!(lines[1].contains("http_status=500"));
        assert!(lines[2].contains("operation=other"));
        assert!(lines[3].contains("outcome=success http_status=200"));

        let attempt_ids = lines
            .iter()
            .map(|line| {
                line.split("attempt_id=")
                    .nth(1)
                    .and_then(|value| value.split_whitespace().next())
                    .expect("attempt id")
            })
            .collect::<Vec<_>>();
        assert_eq!(attempt_ids[0], attempt_ids[1]);
        assert_eq!(attempt_ids[2], attempt_ids[3]);
        assert_ne!(attempt_ids[0], attempt_ids[2]);
        assert!(!log.contains(&host));
        assert!(!log.contains("SECRET_REQUEST"));
        assert!(!log.contains("body_preview="));
    }

    #[test]
    fn default_send_records_each_http_attempt_once_in_worker_totals() {
        let dir = tempfile::tempdir().expect("tempdir");
        let log_path = dir.path().join("retry-worker-timing.log");
        let logger = crate::common::logger::Logger::new(log_path.clone(), true);
        let host = spawn_http_responses(vec![500, 200]);

        install_worker_log_context(&logger, "SubagentStop", "agent-http", "worker-http");
        let result = post_otel_to_langfuse(
            &test_config(host),
            &json!({}),
            &logger,
            false,
            "session",
            "",
        );
        log_worker_http_total(&logger);
        clear_worker_log_context();

        assert!(result.is_ok());
        let log = std::fs::read_to_string(log_path).expect("read timing log");
        let attempt_timings = log
            .lines()
            .filter(|line| line.contains("\"phase\":\"http\""))
            .count();
        assert_eq!(
            attempt_timings, 2,
            "one http timing entry must be emitted per network attempt: {log}"
        );
        assert_eq!(
            log.lines()
                .filter(|line| line.contains("\"phase\":\"http_total\""))
                .count(),
            1,
            "worker must emit exactly one http_total timing: {log}"
        );
    }

    #[test]
    fn lifecycle_logs_timeout_as_controlled_failure() {
        let dir = tempfile::tempdir().expect("tempdir");
        let log_path = dir.path().join("timeout.log");
        let logger = crate::common::logger::Logger::new(log_path.clone(), true);
        let host = spawn_http_response(
            200,
            "late response sentinel",
            std::time::Duration::from_millis(200),
        );
        let result = post_otel_to_langfuse_once_classified(
            &test_config(host),
            &json!({}),
            &logger,
            false,
            "session",
            "",
            std::time::Duration::from_millis(25),
        );
        assert!(matches!(result, Err(OtelPostError::Transport(_))));
        let log = std::fs::read_to_string(log_path).expect("read timeout log");
        let lines = log
            .lines()
            .filter(|line| line.contains("event=langfuse_send_"))
            .collect::<Vec<_>>();
        assert_eq!(lines.len(), 2, "log={log}");
        assert!(lines[0].contains("operation=other"));
        assert!(lines[1].contains("outcome=failure error_kind=timeout"));
        assert!(!lines[1].contains("http_status_retryable="));
        assert!(!log.contains("late response sentinel"));
        assert!(!lines[1].contains("body_preview="));
    }

    #[test]
    fn lifecycle_realtime_generation_chunks_get_distinct_attempt_ids() {
        // Each physical generation chunk must drive its own live attempt, so
        // each gets a fresh attempt_id (one start + one success end per chunk),
        // with per-chunk claim correlation. This is the runtime proof (not a
        // source-string guard) that chunk attempt IDs are distinct and paired.
        let dir = tempfile::tempdir().expect("tempdir");
        let log_path = dir.path().join("generation-chunks.log");
        let logger = crate::common::logger::Logger::new(log_path.clone(), true);
        let body = json!({});
        let mut hosts: Vec<String> = Vec::new();
        for (claim, message_id) in [("claim-a", "msg-1"), ("claim-b", "msg-2")] {
            // Each chunk gets its own one-shot sink so both attempts succeed.
            let host = spawn_http_response(200, "ok", std::time::Duration::ZERO);
            hosts.push(host.clone());
            let config = test_config(host);
            let result = post_otel_to_langfuse_once_with_context(
                &config,
                &body,
                &logger,
                false,
                "session",
                "",
                std::time::Duration::from_secs(1),
                LangfuseAttemptContext::realtime_generation(claim, &[message_id.to_string()]),
            );
            assert!(result.is_ok(), "chunk {claim} should succeed");
        }
        let log = std::fs::read_to_string(log_path).expect("read generation log");
        let lines = log
            .lines()
            .filter(|line| line.contains("langfuse_send_"))
            .collect::<Vec<_>>();
        assert_eq!(lines.len(), 4, "expected 2 start + 2 end, log={log}");
        let starts: Vec<&str> = lines
            .iter()
            .copied()
            .filter(|l| l.contains("send_start"))
            .collect();
        let ends: Vec<&str> = lines
            .iter()
            .copied()
            .filter(|l| l.contains("send_end"))
            .collect();
        assert_eq!(starts.len(), 2);
        assert_eq!(ends.len(), 2);
        let attempt_ids: Vec<Option<&str>> = starts
            .iter()
            .map(|line| {
                line.split("attempt_id=")
                    .nth(1)
                    .and_then(|value| value.split_whitespace().next())
            })
            .collect();
        assert_ne!(
            attempt_ids[0], attempt_ids[1],
            "each chunk must receive a distinct attempt_id: {attempt_ids:?}"
        );
        // Each end's attempt_id must match its preceding start.
        for (start, end) in starts.iter().zip(ends.iter()) {
            let start_id = start
                .split("attempt_id=")
                .nth(1)
                .and_then(|value| value.split_whitespace().next());
            let end_id = end
                .split("attempt_id=")
                .nth(1)
                .and_then(|value| value.split_whitespace().next());
            assert_eq!(
                start_id, end_id,
                "start/end attempt_id mismatch: {start} / {end}"
            );
            assert!(end.contains("outcome=success"));
            assert!(end.contains("operation=realtime_generation"));
        }
        // Per-chunk claim correlation is present and distinct.
        assert!(starts[0].contains("claim_id=claim-a") && starts[0].contains("message_ids=msg-1"));
        assert!(starts[1].contains("claim_id=claim-b") && starts[1].contains("message_ids=msg-2"));
        for host in &hosts {
            assert!(!log.contains(host), "host must not leak into lifecycle log");
        }
    }

    #[test]
    fn lifecycle_logs_preparation_failure_once_without_body_size() {
        let dir = tempfile::tempdir().expect("tempdir");
        let log_path = dir.path().join("preparation.log");
        let logger = crate::common::logger::Logger::new(log_path.clone(), true);
        let result = run_langfuse_attempt(
            &logger,
            std::time::Duration::from_millis(25),
            LangfuseAttemptContext::other(),
            || Err("serialization failed".to_string()),
        );
        assert_eq!(
            result,
            Err(OtelPostError::Preparation(
                "serialization failed".to_string()
            ))
        );
        let log = std::fs::read_to_string(log_path).expect("read preparation log");
        let lines = log
            .lines()
            .filter(|line| line.contains("event=langfuse_send_"))
            .collect::<Vec<_>>();
        assert_eq!(lines.len(), 2);
        assert!(lines[1].contains("error_kind=preparation"));
        assert!(!lines[1].contains("body_bytes="));
        // No body was ever serialized, so no preview either.
        assert!(!lines[1].contains("body_preview="));
    }

    #[test]
    fn typed_transport_classifier_is_controlled() {
        assert_eq!(
            classify_ureq_error(&ureq::Error::HostNotFound),
            LangfuseErrorKind::Dns
        );
        assert_eq!(
            classify_ureq_error(&ureq::Error::ConnectionFailed),
            LangfuseErrorKind::Connect
        );
        assert_eq!(
            classify_ureq_error(&ureq::Error::Io(std::io::Error::new(
                std::io::ErrorKind::ConnectionRefused,
                "sentinel"
            ))),
            LangfuseErrorKind::Connect
        );
        assert_eq!(
            classify_ureq_error(&ureq::Error::BadUri("sentinel".to_string())),
            LangfuseErrorKind::Transport
        );
    }

    #[test]
    fn lifecycle_correlation_is_utf8_bounded_and_single_line() {
        let context = LangfuseAttemptContext::realtime_generation(
            &format!("claim\n{}", "界".repeat(100)),
            &["message\r\tvalue".to_string()],
        );
        for field in context.correlation.split_whitespace() {
            let value = field.split_once('=').map(|(_, value)| value).unwrap_or("");
            assert!(value.len() <= 192, "field={field}");
            assert!(value.is_char_boundary(value.len()));
        }
        assert!(!context.correlation.contains('\n'));
        assert!(!context.correlation.contains('\r'));
        assert!(!context.correlation.contains('\t'));
        assert!(context.correlation.contains("\\n"));
    }

    // --- api_error spans ---

    #[test]
    fn test_build_api_error_sibling_spans() {
        use crate::common::transcript::ApiError;

        let api_errors = vec![
            ApiError {
                timestamp: "2026-03-27T10:00:02.000Z".to_string(),
                error: json!({"status": 502, "error": {"error": "Bad Gateway", "message": "proxy failed"}}),
                retry_attempt: 1,
                max_retries: 10,
                retry_in_ms: 568.69,
            },
            ApiError {
                timestamp: "2026-03-27T10:00:03.000Z".to_string(),
                error: json!({"status": 502, "error": {"error": "Bad Gateway", "message": "proxy failed"}}),
                retry_attempt: 2,
                max_retries: 10,
                retry_in_ms: 1200.0,
            },
        ];

        let trace_id = "a".repeat(32);
        let parent_span_id = "b".repeat(16);
        let recovery_ns = "1774882805000000000";

        let spans = build_api_error_spans(
            &trace_id,
            &parent_span_id,
            &api_errors,
            recovery_ns,
            "claude-sonnet-4-20250514",
            "testuser",
            "test-session",
            "chat-1",
        );
        assert_eq!(spans.len(), 2);

        // All spans should be children of the turn root span (sibling of generation)
        for span in &spans {
            assert_eq!(span.parent_span_id, parent_span_id);
            assert_eq!(span.trace_id, trace_id);
            assert_eq!(span.name, "chat api_error");
            assert_eq!(span.kind, SPAN_KIND_INTERNAL);
        }

        // First span: check attributes
        let s0 = serde_json::to_value(&spans[0]).unwrap();
        let attrs = s0["attributes"].as_array().unwrap();

        let has_span_kind = attrs
            .iter()
            .any(|a| a["key"] == "gen_ai.span.kind" && a["value"]["stringValue"] == "LLM");
        assert!(has_span_kind, "should have gen_ai.span.kind=LLM");

        let has_operation_name = attrs
            .iter()
            .any(|a| a["key"] == "gen_ai.operation.name" && a["value"]["stringValue"] == "chat");
        assert!(has_operation_name, "should have gen_ai.operation.name=chat");

        let has_request_model = attrs.iter().any(|a| {
            a["key"] == "gen_ai.request.model"
                && a["value"]["stringValue"] == "claude-sonnet-4-20250514"
        });
        assert!(has_request_model, "should have gen_ai.request.model");

        let has_error_type = attrs.iter().any(|a| {
            a["key"] == "agentic.llm.error.type" && a["value"]["stringValue"] == "api_error"
        });
        assert!(
            has_error_type,
            "should have agentic.llm.error.type=api_error"
        );
        assert!(
            !attrs
                .iter()
                .any(|a| a["key"] == "gen_ai.response.finish_reasons"),
            "api_error attempts must not synthesize finish reasons"
        );
        assert!(attrs.iter().any(|a| {
            a["key"] == "agentic.usage.input_tokens.missing" && a["value"]["stringValue"] == "true"
        }));
        assert!(attrs.iter().any(|a| {
            a["key"] == "agentic.usage.output_tokens.missing" && a["value"]["stringValue"] == "true"
        }));

        let has_status_code = attrs.iter().any(|a| {
            a["key"] == "agentic.llm.error.status_code" && a["value"]["stringValue"] == "502"
        });
        assert!(
            has_status_code,
            "should have agentic.llm.error.status_code=502"
        );

        let has_retry_attempt = attrs
            .iter()
            .any(|a| a["key"] == "agentic.llm.retry.attempt" && a["value"]["stringValue"] == "1");
        assert!(has_retry_attempt, "should have retry.attempt=1");

        let has_error_msg = attrs.iter().any(|a| {
            a["key"] == "agentic.llm.error.message" && a["value"]["stringValue"] == "proxy failed"
        });
        assert!(
            has_error_msg,
            "should have agentic.llm.error.message from nested error object"
        );

        let has_duration = attrs
            .iter()
            .any(|a| a["key"] == "agentic.llm.duration_ms" && a["value"]["stringValue"] == "1000");
        assert!(has_duration, "should have agentic.llm.duration_ms");

        // Status should be ERROR
        assert_eq!(s0["status"]["code"], 2);

        // Second span: end_time should be recovery_ns (last error)
        let s1 = serde_json::to_value(&spans[1]).unwrap();
        assert_eq!(s1["endTimeUnixNano"], recovery_ns);

        // First span: end_time should be second error's timestamp
        let s0_end = s0["endTimeUnixNano"].as_str().unwrap();
        let s1_start = s1["startTimeUnixNano"].as_str().unwrap();
        assert_eq!(
            s0_end, s1_start,
            "first error end should equal second error start"
        );
    }

    #[test]
    fn test_synthetic_generation_span_error_status() {
        let mut turn = make_test_turn();
        turn.assistants[0].is_synthetic = true;
        turn.assistants[0].model = "<synthetic>".to_string();
        turn.terminal_llm_error = Some(crate::common::transcript::TerminalLlmError {
            error_type: "invalid_model".to_string(),
            message: "model: claude-regression-invalid-20260824 is not valid".to_string(),
            status_code: Some(400),
            request_model: Some("claude-regression-invalid-20260824".to_string()),
        });
        turn.assistants[0].usage = TokenUsage {
            input: Some(0),
            output: Some(0),
            cache_creation_input_tokens: None,
            cache_read_input_tokens: None,
        };
        turn.api_errors = vec![crate::common::transcript::ApiError {
            timestamp: "2026-03-27T14:30:00.500Z".to_string(),
            error: json!({"status": 502}),
            retry_attempt: 1,
            max_retries: 3,
            retry_in_ms: 500.0,
        }];

        let trace_id = "a".repeat(32);
        let parent_span_id = "c".repeat(16);

        let (_, span) = build_generation_span(
            &trace_id,
            &parent_span_id,
            &turn,
            0,
            &turn.assistants[0],
            0,
            "0",
            "testuser",
            "test-session",
            "chat-1",
            &TimingData::new(),
        );
        let span = serde_json::to_value(&span).unwrap();

        // Status should be ERROR for synthetic
        assert_eq!(span["status"]["code"], 2);
        assert!(span["status"]["message"]
            .as_str()
            .unwrap()
            .contains("not valid"));

        // Should have is_synthetic attribute
        let attrs = span["attributes"].as_array().unwrap();
        let has_synthetic = attrs
            .iter()
            .any(|a| a["key"] == "agentic.llm.is_synthetic" && a["value"]["stringValue"] == "true");
        assert!(has_synthetic, "should have agentic.llm.is_synthetic=true");

        let has_error_type = attrs
            .iter()
            .any(|a| a["key"] == "error.type" && a["value"]["stringValue"] == "invalid_model");
        assert!(
            has_error_type,
            "synthetic retry exhaustion should set structured error.type"
        );
        assert!(attrs.iter().any(|a| {
            a["key"] == "agentic.llm.error.type" && a["value"]["stringValue"] == "invalid_model"
        }));
        assert!(attrs.iter().any(|a| {
            a["key"] == "gen_ai.request.model"
                && a["value"]["stringValue"] == "claude-regression-invalid-20260824"
        }));
        assert!(!attrs.iter().any(|a| a["key"] == "gen_ai.response.model"));
        assert!(!attrs.iter().any(|a| a["key"] == "gen_ai.response.id"));
        assert!(!attrs
            .iter()
            .any(|a| a["key"] == "gen_ai.response.finish_reasons"));
        assert!(!attrs.iter().any(|a| a["key"] == "gen_ai.output.messages"));

        let has_zero_input_tokens = attrs
            .iter()
            .any(|a| a["key"] == "gen_ai.usage.input_tokens" && a["value"]["stringValue"] == "0");
        assert!(
            has_zero_input_tokens,
            "synthetic span should preserve explicit zero input tokens"
        );

        let has_zero_output_tokens = attrs
            .iter()
            .any(|a| a["key"] == "gen_ai.usage.output_tokens" && a["value"]["stringValue"] == "0");
        assert!(
            has_zero_output_tokens,
            "synthetic span should preserve explicit zero output tokens"
        );
        assert!(attrs.iter().any(|a| {
            a["key"] == "agentic.usage.input_tokens.missing" && a["value"]["stringValue"] == "true"
        }));
        assert!(attrs.iter().any(|a| {
            a["key"] == "agentic.usage.output_tokens.missing" && a["value"]["stringValue"] == "true"
        }));
    }

    #[test]
    fn test_terminal_llm_error_sets_root_error() {
        let mut turn = make_test_turn();
        turn.assistants[0].is_synthetic = true;
        turn.assistants[0].model = "<synthetic>".to_string();
        turn.terminal_llm_error = Some(crate::common::transcript::TerminalLlmError {
            error_type: "invalid_model".to_string(),
            message: "model: claude-regression-invalid-20260824 is not valid".to_string(),
            status_code: Some(400),
            request_model: Some("claude-regression-invalid-20260824".to_string()),
        });

        let spans = collect_turn_spans(
            &turn,
            1,
            "session-1",
            "testuser",
            "1774882805000000000",
            "",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            None::<&StoredTraceContext>,
        );
        let root = serde_json::to_value(&spans[0]).unwrap();
        assert_eq!(root["status"]["code"], 2);
        let attrs = root["attributes"].as_array().unwrap();
        assert!(attrs
            .iter()
            .any(|a| a["key"] == "error.type" && a["value"]["stringValue"] == "invalid_model"));
    }

    #[test]
    fn test_retry_recovery_keeps_root_ok() {
        use crate::common::transcript::ApiError;

        let mut turn = make_test_turn();
        turn.api_errors = vec![ApiError {
            timestamp: "2026-03-27T14:30:00.500Z".to_string(),
            error: json!({"status": 502, "error": {"message": "proxy failed"}}),
            retry_attempt: 1,
            max_retries: 3,
            retry_in_ms: 500.0,
        }];

        let spans = collect_turn_spans(
            &turn,
            1,
            "session-1",
            "testuser",
            "1774882805000000000",
            "",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            None::<&StoredTraceContext>,
        );
        let root = serde_json::to_value(&spans[0]).unwrap();
        assert_eq!(root["status"]["code"], 1);
        assert!(root["attributes"]
            .as_array()
            .unwrap()
            .iter()
            .all(|a| a["key"] != "error.type"));
        assert_eq!(
            spans
                .iter()
                .filter(|span| span.name == "chat api_error")
                .count(),
            1
        );
    }

    #[test]
    fn test_collect_turn_spans_with_api_errors() {
        use crate::common::transcript::ApiError;

        let mut turn = make_test_turn();
        turn.assistants[0].is_synthetic = true;
        turn.assistants[0].model = "<synthetic>".to_string();
        turn.terminal_llm_error = Some(crate::common::transcript::TerminalLlmError {
            error_type: "api_error".to_string(),
            message: "Bad Gateway".to_string(),
            status_code: Some(502),
            request_model: Some("claude-sonnet-4-20250514".to_string()),
        });
        turn.api_errors = vec![
            ApiError {
                timestamp: "2026-03-27T14:30:00.500Z".to_string(),
                error: json!({"status": 502, "error": {"error": "Bad Gateway"}}),
                retry_attempt: 1,
                max_retries: 3,
                retry_in_ms: 500.0,
            },
            ApiError {
                timestamp: "2026-03-27T14:30:01.000Z".to_string(),
                error: json!({"status": 502, "error": {"error": "Bad Gateway"}}),
                retry_attempt: 2,
                max_retries: 3,
                retry_in_ms: 1000.0,
            },
        ];

        let spans = collect_turn_spans(
            &turn,
            1,
            "session-1",
            "testuser",
            "1774882805000000000",
            "",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            None::<&StoredTraceContext>,
        );

        // Should have: root + generation + 2 api_error sibling spans = 4
        assert_eq!(spans.len(), 4, "expected root + generation + 2 api_errors");

        // Check api_error spans are children of root turn span (siblings of generation)
        let root_span = &spans[0]; // root turn span is always first (excluded from sort)
        let gen_span = spans
            .iter()
            .find(|s| s.name.starts_with("chat ") && !s.name.contains("api_error"))
            .unwrap();
        let api_err_spans: Vec<_> = spans
            .iter()
            .filter(|s| s.name == "chat api_error")
            .collect();
        assert_eq!(api_err_spans.len(), 2);
        for err_span in &api_err_spans {
            assert_eq!(
                err_span.parent_span_id, root_span.span_id,
                "api_error should be child of turn root"
            );
            assert_ne!(
                err_span.parent_span_id, gen_span.span_id,
                "api_error should NOT be child of generation"
            );
        }

        // Spans should be sorted by startTimeUnixNano (after root)
        for i in 2..spans.len() {
            let prev_ns: u128 = spans[i - 1].start_time_unix_nano.parse().unwrap_or(0);
            let curr_ns: u128 = spans[i].start_time_unix_nano.parse().unwrap_or(0);
            assert!(curr_ns >= prev_ns, "spans should be sorted by start time");
        }

        // Generation span should have retry_count attribute
        let gen_json = serde_json::to_value(gen_span).unwrap();
        let gen_attrs = gen_json["attributes"].as_array().unwrap();
        let has_retry_count = gen_attrs
            .iter()
            .any(|a| a["key"] == "agentic.llm.retry_count" && a["value"]["stringValue"] == "2");
        assert!(
            has_retry_count,
            "generation span should have agentic.llm.retry_count=2"
        );
    }

    #[test]
    fn test_collect_turn_spans_cancelled_tool_sets_root_and_leaf_error() {
        let mut turn = make_test_turn();
        turn.cancelled = true;
        turn.assistants[0].tool_uses = vec![ToolUse {
            name: "Bash".to_string(),
            id: "toolu_cancel".to_string(),
            input: json!({"command": "sleep 60 && echo should-not-complete"}),
        }];
        turn.assistants[0].stop_reason = "tool_use".to_string();
        turn.tool_results = vec![ToolResultMessage {
            timestamp: "2026-03-27T14:30:03.000Z".to_string(),
            items: vec![ToolResultItem {
                tool_use_id: "toolu_cancel".to_string(),
                content: json!("User rejected tool use"),
                is_error: true,
                tool_denial_kind: Some("user-rejected".to_string()),
                tool_use_result_text: Some("User rejected tool use".to_string()),
                cancellation_reason: Some("user_rejected".to_string()),
                tool_name: Some("Bash".to_string()),
                timestamp: "2026-03-27T14:30:03.000Z".to_string(),
                agent_id: None,
                agent_type: None,
                is_async: false,
            }],
        }];

        let spans = collect_turn_spans(
            &turn,
            1,
            "session-1",
            "testuser",
            "1774881002000000000",
            "",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            None::<&StoredTraceContext>,
        );
        let root = serde_json::to_value(&spans[0]).unwrap();
        assert_eq!(root["status"]["code"], 2);
        assert!(root["name"].as_str().unwrap().contains("(Cancelled)"));
        let root_attrs = root["attributes"].as_array().unwrap();
        assert!(root_attrs
            .iter()
            .any(|a| a["key"] == "error.type" && a["value"]["stringValue"] == "cancelled"));

        let tool = spans
            .iter()
            .find(|s| s.name.contains("Bash"))
            .expect("tool span");
        let tool_json = serde_json::to_value(tool).unwrap();
        assert_eq!(tool_json["status"]["code"], 2);
        let tool_attrs = tool_json["attributes"].as_array().unwrap();
        assert!(tool_attrs
            .iter()
            .any(|a| a["key"] == "error.type" && a["value"]["stringValue"] == "cancelled"));
    }

    #[test]
    fn test_collect_turn_spans_extends_root_end_to_descendant_end() {
        let mut turn = make_test_turn();
        turn.assistants[0].tool_uses = vec![ToolUse {
            name: "Read".to_string(),
            id: "toolu_late".to_string(),
            input: json!({"file_path": "README.md"}),
        }];
        turn.assistants[0].stop_reason = "tool_use".to_string();
        turn.tool_results = vec![ToolResultMessage {
            timestamp: "2026-03-27T14:30:05.000Z".to_string(),
            items: vec![neutral_tool_result_item(
                "toolu_late".to_string(),
                json!("contents"),
                Some("Read".to_string()),
                "2026-03-27T14:30:05.000Z".to_string(),
                None,
                None,
                false,
            )],
        }];

        let spans = collect_turn_spans(
            &turn,
            1,
            "session-1",
            "testuser",
            "1774881002000000000",
            "",
            "",
            &TimingData::new(),
            &SubagentTimingData::new(),
            None::<&StoredTraceContext>,
        );
        let root_end = spans[0].end_time_unix_nano.parse::<u128>().unwrap();
        let max_child_end = spans[1..]
            .iter()
            .map(|span| span.end_time_unix_nano.parse::<u128>().unwrap())
            .max()
            .unwrap();
        assert!(root_end >= max_child_end);
    }

    #[test]
    fn test_api_error_with_empty_error_object() {
        use crate::common::transcript::ApiError;

        let api_errors = vec![ApiError {
            timestamp: "2026-03-27T10:00:02.000Z".to_string(),
            error: json!({}),
            retry_attempt: 1,
            max_retries: 3,
            retry_in_ms: 500.0,
        }];

        let spans = build_api_error_spans(
            &"a".repeat(32),
            &"b".repeat(16),
            &api_errors,
            "0",
            "claude-sonnet-4-20250514",
            "testuser",
            "test-session",
            "chat-1",
        );
        assert_eq!(spans.len(), 1);
        assert_eq!(spans[0].name, "chat api_error");

        let s = serde_json::to_value(&spans[0]).unwrap();
        let attrs = s["attributes"].as_array().unwrap();

        // Should have gen_ai.span.kind=LLM
        let has_span_kind = attrs
            .iter()
            .any(|a| a["key"] == "gen_ai.span.kind" && a["value"]["stringValue"] == "LLM");
        assert!(has_span_kind, "should have gen_ai.span.kind=LLM");

        // Empty error object → no status_code, error.message = "unknown error"
        let has_status_code = attrs
            .iter()
            .any(|a| a["key"] == "agentic.llm.error.status_code");
        assert!(!has_status_code, "empty error should not have status_code");

        let error_msg = attrs
            .iter()
            .find(|a| a["key"] == "agentic.llm.error.message")
            .unwrap();
        assert_eq!(error_msg["value"]["stringValue"], "unknown error");
    }

    #[test]
    fn test_api_error_span_uses_structured_error_type() {
        let api_errors = vec![crate::common::transcript::ApiError {
            timestamp: "2026-03-27T10:00:02.000Z".to_string(),
            error: json!({"status": 400, "error": {"type": "invalid_request_error", "message": "invalid model: claude-regression-invalid-20260824"}}),
            retry_attempt: 1,
            max_retries: 1,
            retry_in_ms: 0.0,
        }];

        let spans = build_api_error_spans(
            &"a".repeat(32),
            &"b".repeat(16),
            &api_errors,
            "0",
            "claude-regression-invalid-20260824",
            "testuser",
            "test-session",
            "chat-1",
        );
        let s = serde_json::to_value(&spans[0]).unwrap();
        let attrs = s["attributes"].as_array().unwrap();
        assert!(attrs.iter().any(|a| {
            a["key"] == "agentic.llm.error.type" && a["value"]["stringValue"] == "invalid_model"
        }));
        assert!(attrs
            .iter()
            .any(|a| a["key"] == "error.type" && a["value"]["stringValue"] == "invalid_model"));
    }

    #[test]
    fn test_api_error_recovery_timestamp() {
        use crate::common::transcript::ApiError;

        let api_errors = vec![
            ApiError {
                timestamp: "2026-03-27T10:00:02.000Z".to_string(),
                error: json!({"status": 502}),
                retry_attempt: 1,
                max_retries: 3,
                retry_in_ms: 500.0,
            },
            ApiError {
                timestamp: "2026-03-27T10:00:03.000Z".to_string(),
                error: json!({"status": 502}),
                retry_attempt: 2,
                max_retries: 3,
                retry_in_ms: 1000.0,
            },
        ];

        // recovery_timestamp_ns = first successful assistant's timestamp in nanos
        let recovery_ns = iso_to_nanos("2026-03-27T10:00:10.000Z");

        let spans = build_api_error_spans(
            &"a".repeat(32),
            &"b".repeat(16),
            &api_errors,
            &recovery_ns,
            "claude-sonnet-4-20250514",
            "testuser",
            "test-session",
            "chat-1",
        );

        // Last api_error span's end_time should be the recovery timestamp
        let last_span = serde_json::to_value(&spans[1]).unwrap();
        assert_eq!(
            last_span["endTimeUnixNano"].as_str().unwrap(),
            &recovery_ns,
            "last api_error end_time should equal recovery (first assistant) timestamp"
        );

        // First api_error span's end_time should be second error's start
        let first_span = serde_json::to_value(&spans[0]).unwrap();
        let second_span = serde_json::to_value(&spans[1]).unwrap();
        assert_eq!(
            first_span["endTimeUnixNano"].as_str().unwrap(),
            second_span["startTimeUnixNano"].as_str().unwrap(),
            "first error end should equal second error start"
        );
    }

    #[test]
    fn test_build_compaction_span() {
        use crate::common::transcript::CompactionEvent;

        let compaction = CompactionEvent {
            identity: "compact-1".to_string(),
            timestamp: "2026-04-11T16:18:31.889Z".to_string(),
            trigger: "auto".to_string(),
            pre_tokens: 169477,
            post_tokens: 100547,
            end_timestamp: "2026-04-11T16:18:42.530Z".to_string(),
            sequence: 1,
        };

        let trace_id = "a".repeat(32);
        let parent_span_id = "b".repeat(16);
        let span = build_compaction_span(
            &trace_id,
            &parent_span_id,
            &compaction,
            "0",
            "testuser",
            "test-session",
            "chat-1",
        );

        // Verify span basics
        assert_eq!(span.name, "compaction");
        assert_eq!(span.kind, SPAN_KIND_INTERNAL);
        assert_eq!(span.parent_span_id, parent_span_id);
        assert_eq!(span.trace_id, trace_id);

        let s = serde_json::to_value(&span).unwrap();
        let attrs = s["attributes"].as_array().unwrap();

        // Dedicated compaction span attributes (satisfy weaver validation)
        let has_span_kind = attrs
            .iter()
            .any(|a| a["key"] == "gen_ai.span.kind" && a["value"]["stringValue"] == "COMPACTION");
        assert!(has_span_kind, "should have gen_ai.span.kind=COMPACTION");

        let has_op_name = attrs.iter().any(|a| {
            a["key"] == "gen_ai.operation.name" && a["value"]["stringValue"] == "execute_tool"
        });
        assert!(
            has_op_name,
            "should have gen_ai.operation.name=execute_tool"
        );

        let has_tool_name = attrs
            .iter()
            .any(|a| a["key"] == "gen_ai.tool.name" && a["value"]["stringValue"] == "compaction");
        assert!(has_tool_name, "should have gen_ai.tool.name=compaction");

        let has_call_id = attrs
            .iter()
            .any(|a| a["key"] == "gen_ai.tool.call.id" && a["value"]["stringValue"] == "compact-1");
        assert!(has_call_id, "should have gen_ai.tool.call.id=compact-1");

        let has_call_args = attrs
            .iter()
            .any(|a| a["key"] == "gen_ai.tool.call.arguments");
        assert!(has_call_args, "should have gen_ai.tool.call.arguments");

        let has_call_result = attrs.iter().any(|a| a["key"] == "gen_ai.tool.call.result");
        assert!(has_call_result, "should have gen_ai.tool.call.result");

        let has_tool_type = attrs
            .iter()
            .any(|a| a["key"] == "agentic.tool.type" && a["value"]["stringValue"] == "system");
        assert!(has_tool_type, "should have agentic.tool.type=system");

        let has_tool_duration = attrs.iter().any(|a| a["key"] == "agentic.tool.duration_ms");
        assert!(has_tool_duration, "should have agentic.tool.duration_ms");

        // Rule 14.2 compaction-specific attributes
        let has_trigger = attrs.iter().any(|a| {
            a["key"] == "agentic.compaction.trigger" && a["value"]["stringValue"] == "auto"
        });
        assert!(has_trigger, "should have agentic.compaction.trigger=auto");

        let has_before = attrs.iter().any(|a| {
            a["key"] == "agentic.compaction.tokens.before" && a["value"]["stringValue"] == "169477"
        });
        assert!(has_before, "should have tokens.before=169477");

        let has_after = attrs.iter().any(|a| {
            a["key"] == "agentic.compaction.tokens.after" && a["value"]["stringValue"] == "100547"
        });
        assert!(has_after, "should have tokens.after=100547");

        let has_saved = attrs.iter().any(|a| {
            a["key"] == "agentic.compaction.tokens.saved" && a["value"]["stringValue"] == "68930"
        });
        assert!(has_saved, "should have tokens.saved=68930");

        // Rule 14.3 recommended attributes
        let has_sequence = attrs
            .iter()
            .any(|a| a["key"] == "agentic.compaction.sequence" && a["value"]["stringValue"] == "1");
        assert!(has_sequence, "should have sequence=1");

        let has_ratio = attrs
            .iter()
            .any(|a| a["key"] == "agentic.compaction.ratio" && a["value"]["stringValue"] == "0.59");
        assert!(has_ratio, "should have ratio=0.59");

        // Identity attributes
        let has_username = attrs
            .iter()
            .any(|a| a["key"] == "ant.username" && a["value"]["stringValue"] == "testuser");
        assert!(has_username, "should have ant.username");

        // Status should be OK (post < pre). OTel: 0=UNSET, 1=OK, 2=ERROR
        assert_eq!(s["status"]["code"], 1);
    }

    #[test]
    fn compaction_parent_anchor_is_extracted_from_turn_export() {
        let mut turn = make_test_turn();
        turn.chat_id = Some("native-chat-anchor".to_string());
        let body = build_otel_export_request_for_turn(
            &turn,
            7,
            "session-1",
            "testuser",
            "1.0.0",
            "/tmp/session.jsonl",
            &TimingData::new(),
        );

        let anchor = compaction_parent_anchor_from_turn_export(
            &body,
            "turn-stable-7",
            7,
            "/tmp/session.jsonl",
            123_635,
        )
        .unwrap();

        let spans = exported_spans(&body);
        let root = spans
            .iter()
            .find(|span| is_root_chat_export_span(span))
            .expect("root CHAT span");
        assert_eq!(anchor.turn_id, "turn-stable-7");
        assert_eq!(anchor.turn_number, 7);
        assert_eq!(anchor.trace_id, root["traceId"].as_str().unwrap());
        assert_eq!(anchor.root_span_id, root["spanId"].as_str().unwrap());
        assert_eq!(anchor.chat_id, "native-chat-anchor");
        assert_eq!(anchor.transcript_path, "/tmp/session.jsonl");
        assert_eq!(anchor.source_end_offset, 123_635);
    }

    #[test]
    fn late_compaction_export_uses_anchor_and_stable_child_identity() {
        use crate::common::transcript::CompactionEvent;

        let anchor = CompactionParentAnchor {
            turn_id: "turn-stable-3".to_string(),
            turn_number: 3,
            trace_id: "a".repeat(32),
            root_span_id: "b".repeat(16),
            chat_id: "chat-3".to_string(),
            transcript_path: "/tmp/session.jsonl".to_string(),
            source_end_offset: 113_463,
        };
        let compaction = CompactionEvent {
            identity: "compact-1".to_string(),
            timestamp: "2026-04-11T16:18:31.889Z".to_string(),
            trigger: "manual".to_string(),
            pre_tokens: 72_409,
            post_tokens: 4_703,
            end_timestamp: "2026-04-11T16:18:42.530Z".to_string(),
            sequence: 1,
        };

        let body =
            build_compaction_export_request(&anchor, &compaction, "session-1", "testuser", "1.0.0")
                .unwrap();
        let body_again =
            build_compaction_export_request(&anchor, &compaction, "session-1", "testuser", "1.0.0")
                .unwrap();

        let spans = exported_spans(&body);
        let spans_again = exported_spans(&body_again);
        assert_eq!(spans.len(), 1);
        assert_eq!(spans[0]["name"], "compaction");
        assert_eq!(spans[0]["traceId"], anchor.trace_id);
        assert_eq!(spans[0]["parentSpanId"], anchor.root_span_id);
        assert_eq!(spans[0]["spanId"], spans_again[0]["spanId"]);

        assert_eq!(
            export_span_string_attr(&spans[0], "gen_ai.span.kind").as_deref(),
            Some("COMPACTION")
        );
        assert_eq!(
            export_span_string_attr(&spans[0], "gen_ai.chat.id").as_deref(),
            Some("chat-3")
        );
        assert_eq!(
            export_span_string_attr(&spans[0], "agentic.compaction.tokens.after").as_deref(),
            Some("4703")
        );
    }

    #[test]
    fn test_compaction_span_error_when_no_reduction() {
        use crate::common::transcript::CompactionEvent;

        let compaction = CompactionEvent {
            identity: "compact-1".to_string(),
            timestamp: "2026-04-11T16:18:31.000Z".to_string(),
            trigger: "auto".to_string(),
            pre_tokens: 100000,
            post_tokens: 110000, // bigger than pre — anomaly
            end_timestamp: "2026-04-11T16:18:42.000Z".to_string(),
            sequence: 1,
        };

        let span = build_compaction_span(
            &"a".repeat(32),
            &"b".repeat(16),
            &compaction,
            "0",
            "testuser",
            "test-session",
            "chat-1",
        );
        let s = serde_json::to_value(&span).unwrap();

        // Status should be ERROR
        assert_eq!(s["status"]["code"], 2, "should be ERROR when post > pre");
        assert_eq!(s["status"]["message"], "compaction did not reduce tokens");
    }

    #[test]
    fn test_build_tool_spans_with_agent_subagent_attrs() {
        let tool_use = ToolUse {
            name: "Agent".to_string(),
            id: "toolu_agent1".to_string(),
            input: json!({"description": "search codebase", "prompt": "find auth module"}),
        };
        let tool_result = ToolResultItem {
            tool_use_id: "toolu_agent1".to_string(),
            content: json!("Found auth module at src/auth/mod.rs"),
            is_error: false,
            tool_denial_kind: None,
            tool_use_result_text: None,
            cancellation_reason: None,
            tool_name: Some("Agent".to_string()),
            timestamp: "2026-04-08T08:42:51.097Z".to_string(),
            agent_id: None,
            agent_type: None,
            is_async: false,
        };

        let spans = build_tool_spans(
            &"a".repeat(32),
            &"b".repeat(16),
            &[tool_use],
            &[tool_result],
            "0",
            "0",
            "testuser",
            "test-session",
            "chat-1",
            None,
            None,
            &TimingData::new(),
        );

        assert_eq!(spans.len(), 1);
        let span0 = serde_json::to_value(&spans[0]).unwrap();
        let attrs = span0["attributes"].as_array().unwrap();

        assert_eq!(
            attr_string(attrs, "gen_ai.conversation.id"),
            Some("test-session"),
            "the parent Agent invocation stays in the parent conversation"
        );

        let tool_type = attrs.iter().find(|a| a["key"] == "agentic.tool.type");
        assert!(
            tool_type.is_some(),
            "Agent span should have agentic.tool.type"
        );
        assert_eq!(tool_type.unwrap()["value"]["stringValue"], "subagent");

        let tool_name = attrs.iter().find(|a| a["key"] == "agentic.tool.name");
        assert!(
            tool_name.is_some(),
            "Agent span should have agentic.tool.name"
        );
        assert_eq!(tool_name.unwrap()["value"]["stringValue"], "Agent");

        let tool_id = attrs.iter().find(|a| a["key"] == "agentic.tool.id");
        assert!(tool_id.is_some(), "Agent span should have agentic.tool.id");
        assert_eq!(tool_id.unwrap()["value"]["stringValue"], "toolu_agent1");

        // Agent span should have AGENT/invoke_agent instead of TOOL/execute_tool
        let span_kind = attrs.iter().find(|a| a["key"] == "gen_ai.span.kind");
        assert_eq!(span_kind.unwrap()["value"]["stringValue"], "AGENT");
        let op_name = attrs.iter().find(|a| a["key"] == "gen_ai.operation.name");
        assert_eq!(op_name.unwrap()["value"]["stringValue"], "invoke_agent");

        // span name should use "agent SyncAgent" format (not "subagent Agent")
        assert_eq!(span0["name"].as_str().unwrap(), "agent SyncAgent");

        // Should have subagent mode attribute
        let mode_attr = attrs.iter().find(|a| a["key"] == "agentic.subagent.mode");
        assert!(
            mode_attr.is_some(),
            "Agent span should have agentic.subagent.mode"
        );
        assert_eq!(mode_attr.unwrap()["value"]["stringValue"], "sync");
    }

    #[test]
    fn test_build_tool_spans_from_results_missing_marker() {
        let tool_result = ToolResultItem {
            tool_use_id: "toolu_res1".to_string(),
            content: json!("some output"),
            is_error: false,
            tool_denial_kind: None,
            tool_use_result_text: None,
            cancellation_reason: None,
            tool_name: Some("Read".to_string()),
            timestamp: "2026-04-08T08:42:51.097Z".to_string(),
            agent_id: None,
            agent_type: None,
            is_async: false,
        };

        let spans = build_tool_spans_from_results(
            &"a".repeat(32),
            &"b".repeat(16),
            &[tool_result],
            "0",
            "0",
            "testuser",
            "test-session",
            "chat-1",
            None,
            |_| None,
        );

        assert_eq!(spans.len(), 1);
        let span0 = serde_json::to_value(&spans[0]).unwrap();
        let attrs = span0["attributes"].as_array().unwrap();

        // Verify agentic.tool.input.missing is set to "true"
        let missing = attrs
            .iter()
            .find(|a| a["key"] == "agentic.tool.input.missing");
        assert!(
            missing.is_some(),
            "result-only span should have agentic.tool.input.missing"
        );
        assert_eq!(missing.unwrap()["value"]["stringValue"], "true");

        // Verify arguments is empty string
        let args = attrs
            .iter()
            .find(|a| a["key"] == "gen_ai.tool.call.arguments");
        assert!(args.is_some());
        assert_eq!(args.unwrap()["value"]["stringValue"], "");
    }

    #[test]
    fn test_extract_agent_id_from_result_standard() {
        let text = "agentId: a87d5d293f61d5ecd (use SendMessage with to: 'a87d5d293f61d5ecd' to continue this agent)\n<usage>total_tokens: 44182</usage>";
        assert_eq!(
            extract_agent_id_from_result(text),
            Some("a87d5d293f61d5ecd".to_string())
        );
    }

    #[test]
    fn test_extract_agent_id_from_result_agent_prefix() {
        let text = "Result from agent-abc123def456: completed successfully";
        assert_eq!(
            extract_agent_id_from_result(text),
            Some("abc123def456".to_string())
        );
    }

    #[test]
    fn test_extract_agent_id_from_result_no_match() {
        let text = "Some random tool output with no agent info";
        assert_eq!(extract_agent_id_from_result(text), None);
    }

    #[test]
    fn subagent_conversation_id_is_stable_opaque_and_scoped() {
        let first = derive_subagent_conversation_id("parent-sensitive", "agent-sensitive");
        let replay = derive_subagent_conversation_id("parent-sensitive", "agent-sensitive");
        let other_agent = derive_subagent_conversation_id("parent-sensitive", "agent-other");
        let other_parent = derive_subagent_conversation_id("parent-other", "agent-sensitive");

        assert_eq!(first, replay);
        assert_ne!(first, other_agent);
        assert_ne!(first, other_parent);
        assert_ne!(
            derive_subagent_conversation_id("a", "bc"),
            derive_subagent_conversation_id("ab", "c")
        );
        assert!(first.starts_with("cc-subagent-"));
        assert!(!first.contains("parent-sensitive"));
        assert!(!first.contains("agent-sensitive"));
    }

    #[test]
    fn async_overlay_preserves_nested_subagent_semantics() {
        let nested_conversation = derive_subagent_conversation_id("parent-session", "nested-agent");
        let mut request = json!({
            "resourceSpans": [{
                "resource": {"attributes": []},
                "scopeSpans": [{"spans": [
                    {
                        "spanId": "outer-root",
                        "name": "Turn 1",
                        "attributes": [
                            {"key":"gen_ai.conversation.id","value":{"stringValue":"parent-session"}}
                        ]
                    },
                    {
                        "spanId": "outer-generation",
                        "parentSpanId": "outer-root",
                        "name": "chat claude-test",
                        "attributes": [
                            {"key":"gen_ai.conversation.id","value":{"stringValue":"parent-session"}}
                        ]
                    },
                    {
                        "spanId": "nested-call",
                        "parentSpanId": "outer-root",
                        "name": "tool Agent",
                        "attributes": [
                            {"key":"gen_ai.conversation.id","value":{"stringValue":"parent-session"}},
                            {"key":"agentic.tool.type","value":{"stringValue":"subagent"}},
                            {"key":"agentic.subagent","value":{"stringValue":"true"}},
                            {"key":"agentic.subagent.mode","value":{"stringValue":"sync"}},
                            {"key":"agentic.subagent.id","value":{"stringValue":"nested-agent"}},
                            {"key":"agentic.subagent.type","value":{"stringValue":"nested-type"}},
                            {"key":"agentic.subagent.description","value":{"stringValue":"nested task"}}
                        ]
                    },
                    {
                        "spanId": "nested-generation",
                        "parentSpanId": "nested-call",
                        "name": "chat claude-test",
                        "attributes": [
                            {"key":"gen_ai.conversation.id","value":{"stringValue":nested_conversation}},
                            {"key":"agentic.subagent","value":{"stringValue":"true"}},
                            {"key":"agentic.subagent.type","value":{"stringValue":"nested-type"}}
                        ]
                    },
                    {
                        "spanId": "nested-tool",
                        "parentSpanId": "nested-generation",
                        "name": "tool Read",
                        "attributes": [
                            {"key":"gen_ai.conversation.id","value":{"stringValue":nested_conversation}},
                            {"key":"agentic.subagent","value":{"stringValue":"true"}},
                            {"key":"agentic.subagent.type","value":{"stringValue":"nested-type"}}
                        ]
                    },
                    {
                        "spanId": "outer-tool",
                        "parentSpanId": "outer-root",
                        "name": "tool Read",
                        "attributes": [
                            {"key":"gen_ai.conversation.id","value":{"stringValue":"parent-session"}}
                        ]
                    }
                ]}]
            }]
        });

        apply_async_subagent_semantics(
            &mut request,
            "parent-session",
            "outer-agent",
            "outer-type",
            "outer task",
        );

        let spans = exported_spans(&request);
        let find = |span_id: &str| {
            spans
                .iter()
                .find(|span| span.get("spanId").and_then(Value::as_str) == Some(span_id))
                .unwrap()
        };
        let outer_conversation = derive_subagent_conversation_id("parent-session", "outer-agent");

        for span_id in ["outer-root", "outer-generation", "outer-tool"] {
            let span = find(span_id);
            assert_eq!(
                export_span_string_attr(span, "gen_ai.conversation.id").as_deref(),
                Some(outer_conversation.as_str())
            );
            assert_eq!(
                export_span_string_attr(span, "agentic.subagent.id").as_deref(),
                Some("outer-agent")
            );
        }

        let nested_call = find("nested-call");
        assert_eq!(
            export_span_string_attr(nested_call, "gen_ai.conversation.id").as_deref(),
            Some(outer_conversation.as_str())
        );
        assert_eq!(
            export_span_string_attr(nested_call, "agentic.subagent.id").as_deref(),
            Some("nested-agent")
        );
        assert_eq!(
            export_span_string_attr(nested_call, "agentic.subagent.mode").as_deref(),
            Some("sync")
        );
        assert_eq!(
            export_span_string_attr(nested_call, "agentic.subagent.description").as_deref(),
            Some("nested task")
        );

        for span_id in ["nested-generation", "nested-tool"] {
            let span = find(span_id);
            assert_eq!(
                export_span_string_attr(span, "gen_ai.conversation.id").as_deref(),
                Some(nested_conversation.as_str())
            );
            assert_eq!(
                export_span_string_attr(span, "agentic.subagent.type").as_deref(),
                Some("nested-type")
            );
            assert_eq!(export_span_string_attr(span, "agentic.subagent.id"), None);
        }
    }

    #[test]
    fn replay_emits_result_marked_async_subagent_as_independent_turns() {
        let dir = tempfile::tempdir().unwrap();
        let parent_path = dir.path().join("parent.jsonl");
        let subagent_dir = dir.path().join("parent/subagents");
        std::fs::create_dir_all(&subagent_dir).unwrap();
        let subagent_path = subagent_dir.join("agent-agent-async.jsonl");
        let child_lines = [
            json!({"type":"user","uuid":"child-turn-1","message":{"role":"user","content":"read"},"timestamp":"2026-09-02T00:00:02Z"}),
            json!({"type":"assistant","message":{"role":"assistant","id":"child-msg-1","content":[{"type":"tool_use","id":"child-tool","name":"Read","input":{"file_path":"a.txt"}}],"model":"claude-test","usage":{"input_tokens":1,"output_tokens":1}},"timestamp":"2026-09-02T00:00:03Z"}),
            json!({"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"child-tool","content":"ok"}]},"timestamp":"2026-09-02T00:00:04Z"}),
            json!({"type":"user","uuid":"child-turn-2","message":{"role":"user","content":"finish"},"timestamp":"2026-09-02T00:00:05Z"}),
            json!({"type":"assistant","message":{"role":"assistant","id":"child-msg-2","content":[{"type":"text","text":"done"}],"model":"claude-test","usage":{"input_tokens":1,"output_tokens":1}},"timestamp":"2026-09-02T00:00:06Z"}),
        ];
        std::fs::write(
            &subagent_path,
            child_lines
                .iter()
                .map(Value::to_string)
                .collect::<Vec<_>>()
                .join("\n"),
        )
        .unwrap();
        std::fs::write(
            subagent_dir.join("agent-agent-async.meta.json"),
            json!({"agentType":"Explore","description":"inspect code"}).to_string(),
        )
        .unwrap();

        let parent_lines = [
            json!({"type":"user","uuid":"parent-turn","sessionId":"parent-session","message":{"role":"user","content":"delegate"},"timestamp":"2026-09-02T00:00:00Z"}),
            json!({"type":"assistant","sessionId":"parent-session","message":{"role":"assistant","id":"parent-msg","content":[{"type":"tool_use","id":"parent-tool","name":"Agent","input":{"prompt":"inspect"}}],"model":"claude-test","usage":{"input_tokens":1,"output_tokens":1}},"timestamp":"2026-09-02T00:00:01Z"}),
            json!({"type":"user","sessionId":"parent-session","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"parent-tool","content":"launched"}]},"toolUseResult":{"agentId":"agent-async","agentType":"Explore","isAsync":true,"status":"async_launched"},"timestamp":"2026-09-02T00:00:01.500Z"}),
        ];
        std::fs::write(
            &parent_path,
            parent_lines
                .iter()
                .map(Value::to_string)
                .collect::<Vec<_>>()
                .join("\n"),
        )
        .unwrap();
        let turns = crate::common::transcript::build_turns(&parent_lines, None);
        let requests = build_otel_replay_export_requests_for_turn(
            &turns[0],
            1,
            "parent-session",
            "test-user",
            "test",
            parent_path.to_str().unwrap(),
            &TimingData::new(),
        );

        assert_eq!(requests.len(), 3, "parent plus two async child turns");
        let parent_spans = exported_spans(&requests[0]);
        let parent_agent = parent_spans
            .iter()
            .find(|span| {
                export_span_string_attr(span, "agentic.tool.type").as_deref() == Some("subagent")
            })
            .unwrap();
        assert_eq!(
            export_span_string_attr(parent_agent, "gen_ai.conversation.id").as_deref(),
            Some("parent-session")
        );
        assert_eq!(
            export_span_string_attr(parent_agent, "agentic.subagent.mode").as_deref(),
            Some("async")
        );

        let expected = derive_subagent_conversation_id("parent-session", "agent-async");
        let child_spans = requests[1..]
            .iter()
            .flat_map(|request| exported_spans(request).iter())
            .collect::<Vec<_>>();
        assert!(child_spans.iter().all(|span| {
            export_span_string_attr(span, "gen_ai.conversation.id").as_deref()
                == Some(expected.as_str())
        }));
        assert!(child_spans
            .iter()
            .any(|span| span["name"] == "Turn AsyncAgent"));
        assert!(child_spans.iter().any(|span| {
            export_span_string_attr(span, "gen_ai.tool.call.id").as_deref() == Some("child-tool")
        }));
    }

    #[test]
    fn test_subagent_dir_from_transcript_no_dir() {
        // Non-existent path should return None
        assert!(subagent_dir_from_transcript("/tmp/nonexistent-session.jsonl").is_none());
    }

    #[test]
    fn test_subagent_dir_from_transcript_empty() {
        assert!(subagent_dir_from_transcript("").is_none());
    }

    #[test]
    fn stop_export_identity_stabilization_is_repeatable_and_preserves_topology() {
        fn body(trace: &str, root: &str, child: &str) -> Value {
            json!({"resourceSpans": [{"scopeSpans": [{"spans": [
                {"traceId": trace, "spanId": root, "name": "turn"},
                {
                    "traceId": trace,
                    "spanId": child,
                    "parentSpanId": root,
                    "name": "generation"
                }
            ]}]}]})
        }

        let mut first = body(
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "1111111111111111",
            "2222222222222222",
        );
        let mut second = body(
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "3333333333333333",
            "4444444444444444",
        );
        stabilize_turn_export_identities(&mut first, "turn-stable", &[]);
        stabilize_turn_export_identities(&mut second, "turn-stable", &[]);

        let first_spans = &first["resourceSpans"][0]["scopeSpans"][0]["spans"];
        let second_spans = &second["resourceSpans"][0]["scopeSpans"][0]["spans"];
        assert_eq!(first_spans[0]["traceId"], second_spans[0]["traceId"]);
        assert_eq!(first_spans[0]["spanId"], second_spans[0]["spanId"]);
        assert_eq!(first_spans[1]["spanId"], second_spans[1]["spanId"]);
        assert_eq!(first_spans[1]["parentSpanId"], first_spans[0]["spanId"]);
        assert_eq!(second_spans[1]["parentSpanId"], second_spans[0]["spanId"]);
    }

    #[test]
    fn stop_export_identity_stabilization_keeps_realtime_claimed_ids() {
        let trace_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        let root_span_id = "1111111111111111";
        let generation_span_id = "2222222222222222";
        let context = StoredTraceContext {
            trace_id: trace_id.to_string(),
            root_span_id: Some(root_span_id.to_string()),
            generation_span_ids: HashMap::from([(
                "msg-1".to_string(),
                generation_span_id.to_string(),
            )]),
            ..StoredTraceContext::default()
        };
        let mut body = json!({"resourceSpans": [{"scopeSpans": [{"spans": [
            {"traceId": trace_id, "spanId": root_span_id, "name": "turn"},
            {
                "traceId": trace_id,
                "spanId": generation_span_id,
                "parentSpanId": root_span_id,
                "name": "generation"
            },
            {
                "traceId": trace_id,
                "spanId": "3333333333333333",
                "parentSpanId": root_span_id,
                "name": "extra"
            }
        ]}]}]});

        stabilize_turn_export_identities(&mut body, "turn-stable", &[context]);
        let spans = &body["resourceSpans"][0]["scopeSpans"][0]["spans"];
        assert_eq!(spans[0]["traceId"], trace_id);
        assert_eq!(spans[0]["spanId"], root_span_id);
        assert_eq!(spans[1]["spanId"], generation_span_id);
        assert_ne!(spans[2]["spanId"], "3333333333333333");
        assert_eq!(spans[2]["parentSpanId"], root_span_id);
    }

    #[test]
    fn stop_export_identity_stabilization_preserves_external_parent_trace() {
        let trace_id = "41049c989ea3510bafe6d446672d7b1c";
        let upstream_parent = "61c42769ba435f72";
        let mut body = json!({"resourceSpans": [{"scopeSpans": [{"spans": [
            {
                "traceId": trace_id,
                "spanId": "1111111111111111",
                "parentSpanId": upstream_parent,
                "name": "turn"
            },
            {
                "traceId": trace_id,
                "spanId": "2222222222222222",
                "parentSpanId": "1111111111111111",
                "name": "generation"
            }
        ]}]}]});

        stabilize_turn_export_identities(&mut body, "turn-stable", &[]);
        let spans = &body["resourceSpans"][0]["scopeSpans"][0]["spans"];
        assert_eq!(spans[0]["traceId"], trace_id);
        assert_eq!(spans[1]["traceId"], trace_id);
        assert_eq!(spans[0]["parentSpanId"], upstream_parent);
        assert_ne!(spans[0]["spanId"], "1111111111111111");
        assert_eq!(spans[1]["parentSpanId"], spans[0]["spanId"]);
    }

    #[test]
    fn selected_trace_is_protected_without_history_or_external_parent() {
        let trace_id = "41049c989ea3510bafe6d446672d7b1c";
        let mut body = json!({"resourceSpans": [{"scopeSpans": [{"spans": [
            {
                "traceId": trace_id,
                "spanId": "1111111111111111",
                "name": "turn"
            },
            {
                "traceId": trace_id,
                "spanId": "2222222222222222",
                "parentSpanId": "1111111111111111",
                "name": "generation"
            }
        ]}]}]});

        stabilize_turn_export_identities_with_selected_trace(
            &mut body,
            "turn-stable",
            &[],
            Some(trace_id),
        );
        let spans = &body["resourceSpans"][0]["scopeSpans"][0]["spans"];
        assert_eq!(spans[0]["traceId"], trace_id);
        assert_eq!(spans[1]["traceId"], trace_id);
        assert_ne!(spans[0]["spanId"], "1111111111111111");
        assert_eq!(spans[1]["parentSpanId"], spans[0]["spanId"]);
    }

    #[test]
    fn pure_llm_turn_keeps_injected_traceparent_through_stabilization() {
        let trace_id = "41049c989ea3510bafe6d446672d7b1c";
        let upstream_parent = "61c42769ba435f72";
        let turns = build_turns(
            &[
                json!({"type":"user","uuid":"turn-pure","message":{"role":"user","content":"hello"},"timestamp":"2026-08-24T00:00:00Z"}),
                json!({"type":"assistant","message":{"role":"assistant","id":"msg-pure","content":[{"type":"text","text":"done"}],"stop_reason":"end_turn","model":"claude-test","usage":{"input_tokens":1,"output_tokens":1}},"timestamp":"2026-08-24T00:00:01Z"}),
            ],
            None,
        );
        let context = StoredTraceContext {
            trace_id: trace_id.to_string(),
            traceparent: Some(format!("00-{trace_id}-{upstream_parent}-01")),
            ..StoredTraceContext::default()
        };
        let mut body = build_otel_export_request_for_turn_with_full_context(
            &turns[0],
            1,
            "session",
            "user",
            &test_config("http://127.0.0.1:4319".to_string()),
            "2026-08-24T00:00:01Z",
            "test",
            "/tmp",
            "/tmp/session.jsonl",
            &TimingData::new(),
            &SubagentTimingData::new(),
            Some(&context),
        );
        stabilize_turn_export_identities(&mut body, "turn-pure", &[context]);

        let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .unwrap();
        assert!(spans.iter().all(|span| span["traceId"] == trace_id));
        let root = &spans[0];
        let generation = spans
            .iter()
            .find(|span| span["spanId"] != root["spanId"] && span["parentSpanId"] == root["spanId"])
            .expect("generation span");
        assert_eq!(root["parentSpanId"], upstream_parent);
        assert_eq!(generation["parentSpanId"], root["spanId"]);
    }

    #[test]
    fn vcs_resource_injection_upserts_every_resource_span_once() {
        let mut body = json!({
            "resourceSpans": [
                {
                    "resource": {"attributes": [
                        {"key": "service.name", "value": {"stringValue": "first"}},
                        {"key": "vcs.repository.url.full", "value": {"stringValue": "stale"}},
                        {"key": "vcs.repository.url.full", "value": {"stringValue": "duplicate"}}
                    ]}
                },
                {"resource": {"attributes": []}},
                {}
            ]
        });

        inject_vcs_resource_attrs(&mut body, "git@git.example.com:team/repo.git", "feature");
        inject_vcs_resource_attrs(&mut body, "git@git.example.com:team/repo.git", "feature");

        for resource_span in body["resourceSpans"].as_array().unwrap() {
            let attributes = resource_span["resource"]["attributes"].as_array().unwrap();
            for (key, expected) in [
                (
                    "vcs.repository.url.full",
                    "git@git.example.com:team/repo.git",
                ),
                ("vcs.ref.head.name", "feature"),
            ] {
                let matches = attributes
                    .iter()
                    .filter(|attribute| attribute["key"] == key)
                    .collect::<Vec<_>>();
                assert_eq!(matches.len(), 1, "{key} must be unique");
                assert_eq!(matches[0]["value"]["stringValue"], expected);
            }
        }
        assert_eq!(
            body["resourceSpans"][0]["resource"]["attributes"][0]["value"]["stringValue"],
            "first"
        );
    }

    #[test]
    fn vcs_resource_injection_omits_empty_values() {
        let mut body = json!({"resourceSpans": [{"resource": {"attributes": []}}]});
        inject_vcs_resource_attrs(&mut body, "", " ");
        assert!(body["resourceSpans"][0]["resource"]["attributes"]
            .as_array()
            .unwrap()
            .is_empty());
    }
}
