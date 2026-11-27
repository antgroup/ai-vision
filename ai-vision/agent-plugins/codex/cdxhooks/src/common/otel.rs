use serde_json::{json, Value};
use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
#[cfg(test)]
use std::fs;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;

/// Options for replay-mode OTel export, enabling unified multi-session traces.
#[derive(Default)]
pub struct TurnReplayOpts {
    /// Shared trace ID for the entire session tree. If None, a fresh ID is generated per turn.
    pub trace_id: Option<String>,
    /// Stable CHAT/root span ID allocated when realtime state is created.
    /// Finalization reuses it so already-exported children retain the same parent.
    pub root_span_id: Option<String>,
    /// Parent span ID for this session's CHAT spans (used when this is a child session).
    pub parent_span_id: Option<String>,
    /// Original SOFA trace id from the upstream launcher, when available.
    pub sofa_traceid: Option<String>,
    /// Original SOFA rpc id from the upstream launcher, when available.
    pub sofa_rpcid: Option<String>,
    /// Explicit billing evidence captured for this live turn.
    pub billing_mode: Option<String>,
    /// Pre-allocated generation span IDs keyed by assistant index within this turn.
    /// Realtime and finalizer paths populate this to reuse the exact child identity.
    pub generation_span_ids: HashMap<usize, String>,
    /// Pre-allocated span IDs for SUBAGENT spans, keyed by new_thread_id.
    /// Ensures child session CHAT spans can reference the correct parent SUBAGENT span.
    pub subagent_span_ids: HashMap<String, String>,
}

use agentic_otel_common::{
    extract_skill_name_from_tool_input, iso_to_nanos,
    message::{serialize_messages_default, serialize_system_instructions},
    now_nanos, otel_span_id, otel_trace_id,
    otlp::{
        append_agentic_tool_attrs_typed, append_otel_span_attributes, wrap_trace_export,
        OtelAttribute, OtelSpan, OtelStatus, SPAN_KIND_INTERNAL, SPAN_KIND_SERVER,
    },
    parse_agentic_tool, tool_span_name, truncate_value_with_stats, ExportContext, TruncationStats,
    INTERNAL_META_ROOT_KEY, INTERNAL_META_ROOT_VALUE,
};

use crate::common::hook::{env_equals_ignore_case, env_is_truthy};
use crate::common::payload_budget::{
    enforce_span_budget, filter_inline_base64_images, validate_export_body_span_budget,
    InlineImageFilterStats, SpanBudgetOutcome,
};
use crate::common::transcript::{
    correlate_subagent_invocations, extract_skill_name_from_command_text, extract_skill_read_name,
    is_shell_tool, normalized_subagent_activations, shell_exit_code, subagent_anchor_turn_index,
    subagent_invocation_key, AssistantMessage, CollabAgentEvent, CollabInvocationSource, CollabInvocationState,
    SkillInvocation, SkillInvocationSource, SubagentActivationEvidence, SubagentIdentity,
    SubagentLifecycle, TokenUsage, ToolCall, Turn, TurnTerminalState,
};
use crate::hooks::langfuse::{
    build_basic_auth_header, build_custom_metadata, resolve_agent_product, resolve_agent_type,
    LangfuseConfig,
};

const HTTP_TIMEOUT_SECS: u64 = 20;

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
) {
    WORKER_LOG_CONTEXT.with(|slot| {
        *slot.borrow_mut() = Some(WorkerLogContext {
            logger: logger.clone(),
            hook_event: hook_event.to_string(),
            event_id: event_id.to_string(),
            worker_id: std::process::id().to_string(),
            http_total_ms: 0,
        });
    });
}

pub fn clear_worker_log_context() {
    WORKER_LOG_CONTEXT.with(|slot| *slot.borrow_mut() = None);
}

pub fn observe_worker_http_elapsed(elapsed_ms: u128) {
    WORKER_LOG_CONTEXT.with(|slot| {
        if let Some(context) = slot.borrow_mut().as_mut() {
            context.http_total_ms = context.http_total_ms.saturating_add(elapsed_ms);
        }
    });
}

pub fn current_worker_http_total_ms() -> u128 {
    WORKER_LOG_CONTEXT.with(|slot| {
        slot.borrow()
            .as_ref()
            .map(|context| context.http_total_ms)
            .unwrap_or(0)
    })
}

fn observe_truncation(stats: &TruncationStats, field_kind: &str) {
    WORKER_LOG_CONTEXT.with(|slot| {
        let context = slot.borrow();
        let Some(context) = context.as_ref() else {
            return;
        };
        let enriched = stats.clone().with_log_context(
            &stats.operation,
            &context.hook_event,
            &context.event_id,
            &context.worker_id,
            field_kind,
        );
        let line = format!("truncation {}", enriched.to_safe_log_fields());
        match enriched.log_level() {
            "WARN" => context.logger.warn(&line),
            "INFO" => context.logger.info(&line),
            _ => context.logger.debug(&line),
        }
    });
}

fn truncate_value_observed(value: &Value, field_kind: &str) -> (Value, Value) {
    let (value, metadata, stats) = truncate_value_with_stats(value, field_kind);
    observe_truncation(&stats, field_kind);
    (value, metadata)
}

fn observe_span_budget(outcome: &SpanBudgetOutcome, span_name: &str, span_id: &str) {
    if !outcome.truncated {
        return;
    }
    WORKER_LOG_CONTEXT.with(|slot| {
        let context = slot.borrow();
        let Some(context) = context.as_ref() else {
            return;
        };
        let fields = json!({
            "operation": "enforce_span_budget",
            "hook_event": context.hook_event,
            "event_id": context.event_id,
            "worker_id": context.worker_id,
            "span_name": span_name,
            "span_id": span_id,
            "original_bytes": outcome.original_length,
            "kept_bytes": outcome.final_length,
            "limit_bytes": crate::common::payload_budget::MAX_SERIALIZED_SPAN_BYTES,
            "replaced_values": outcome.replaced_values,
            "removed_attributes": outcome.removed_attributes,
            "within_budget": outcome.within_budget,
        });
        let line = format!("span_budget {fields}");
        if outcome.within_budget {
            context.logger.info(&line);
        } else {
            context.logger.warn(&line);
        }
    });
}

fn observe_inline_image_filter(stats: &InlineImageFilterStats, field_kind: &str) {
    if stats.filtered_count == 0 {
        return;
    }
    WORKER_LOG_CONTEXT.with(|slot| {
        let context = slot.borrow();
        let Some(context) = context.as_ref() else {
            return;
        };
        let fields = json!({
            "operation": "filter_inline_base64_images",
            "hook_event": context.hook_event,
            "event_id": context.event_id,
            "worker_id": context.worker_id,
            "field_kind": field_kind,
            "filtered_count": stats.filtered_count,
            "original_chars": stats.original_chars,
        });
        context
            .logger
            .info(&format!("inline_image_filter {fields}"));
    });
}

fn enforce_span_budget_observed(span: &mut OtelSpan) -> SpanBudgetOutcome {
    let span_name = span.name.clone();
    let span_id = span.span_id.clone();
    let outcome = enforce_span_budget(span);
    observe_span_budget(&outcome, &span_name, &span_id);
    outcome
}

fn observe_subagent_merge_failure(stage: &str, child_session_id: &str, span_id: Option<&str>) {
    WORKER_LOG_CONTEXT.with(|slot| {
        let context = slot.borrow();
        let Some(context) = context.as_ref() else {
            return;
        };
        let fields = json!({
            "operation": "merge_subagent_descendant",
            "hook_event": context.hook_event,
            "event_id": context.event_id,
            "worker_id": context.worker_id,
            "stage": stage,
            "child_session_id": child_session_id,
            "span_id": span_id.unwrap_or("unknown"),
        });
        context
            .logger
            .warn(&format!("subagent_merge_failure {fields}"));
    });
}

/// Generate the span id used by both PreToolUse propagation and transcript replay.
pub fn deterministic_tool_span_id(tool_use_id: &str) -> String {
    use sha2::{Digest, Sha256};

    let mut hasher = Sha256::new();
    hasher.update(b"cdxhooks-tool-span-id:");
    hasher.update(tool_use_id.as_bytes());
    let digest = hasher.finalize();
    digest[..8]
        .iter()
        .map(|byte| format!("{:02x}", byte))
        .collect()
}

fn deterministic_subagent_agent_span_id(
    child_session_id: &str,
    activation_tool_call_id: &str,
) -> String {
    use sha2::{Digest, Sha256};

    let mut hasher = Sha256::new();
    hasher.update(b"cdxhooks-subagent-agent-span-id:");
    hasher.update(child_session_id.as_bytes());
    hasher.update(b":");
    hasher.update(activation_tool_call_id.as_bytes());
    let digest = hasher.finalize();
    digest[..8]
        .iter()
        .map(|byte| format!("{:02x}", byte))
        .collect()
}

fn deterministic_subagent_generation_span_id(
    child_session_id: &str,
    activation_tool_call_id: &str,
    child_turn_id: &str,
    response_id: Option<&str>,
    assistant_index: usize,
) -> String {
    use sha2::{Digest, Sha256};

    let mut hasher = Sha256::new();
    hasher.update(b"cdxhooks-subagent-generation-span-id:");
    hasher.update(child_session_id.as_bytes());
    hasher.update(b":");
    hasher.update(activation_tool_call_id.as_bytes());
    hasher.update(b":");
    hasher.update(child_turn_id.as_bytes());
    hasher.update(b":");
    if let Some(response_id) = response_id.filter(|value| !value.trim().is_empty()) {
        hasher.update(b"response:");
        hasher.update(response_id.as_bytes());
    } else {
        hasher.update(b"index:");
        hasher.update(assistant_index.to_string().as_bytes());
    }
    let digest = hasher.finalize();
    digest[..8]
        .iter()
        .map(|byte| format!("{:02x}", byte))
        .collect()
}

#[cfg(test)]
std::thread_local! {
    static TEST_PRIVACY_MODE: std::cell::Cell<Option<bool>> = const { std::cell::Cell::new(None) };
}

#[cfg(test)]
pub(crate) struct TestPrivacyModeGuard {
    previous: Option<bool>,
}

#[cfg(test)]
impl TestPrivacyModeGuard {
    pub(crate) fn set(privacy: bool) -> Self {
        let previous = TEST_PRIVACY_MODE.replace(Some(privacy));
        Self { previous }
    }
}

#[cfg(test)]
impl Drop for TestPrivacyModeGuard {
    fn drop(&mut self) {
        TEST_PRIVACY_MODE.set(self.previous);
    }
}

pub fn is_privacy_mode() -> bool {
    #[cfg(test)]
    if let Some(privacy) = TEST_PRIVACY_MODE.get() {
        return privacy;
    }

    agentic_otel_common::privacy::resolve_mode("CDXHOOKS_COLLECTION_MODE") != Ok("full")
}

// --- Rule 7: Error detection helpers ---

/// Detect errors in tool output. Returns (error_type, error_message) if error found.
fn detect_tool_error_from_output(output: &Value) -> Option<(String, String)> {
    if let Some(err) = output.get("error").and_then(|v| v.as_str()) {
        return Some(("tool_error".to_string(), err.to_string()));
    }
    if let Some(s) = output.as_str() {
        let normalized = s.trim().to_ascii_lowercase();
        if [
            "aborted by user",
            "interrupted by user",
            "cancelled by user",
            "canceled by user",
        ]
        .iter()
        .any(|marker| normalized.starts_with(marker))
        {
            return Some(("cancelled".to_string(), s.to_string()));
        }
        if s.starts_with("Error:") || s.starts_with("error:") {
            return Some(("tool_error".to_string(), s.to_string()));
        }
    }
    None
}

// --- Rule 12: Truncation metadata helper ---

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

fn usage_attrs(usage: &TokenUsage) -> Vec<OtelAttribute> {
    let mut attrs = Vec::new();
    attrs.push(OtelAttribute::int_as_string(
        "gen_ai.usage.input_tokens",
        usage.input.unwrap_or(0),
    ));
    attrs.push(OtelAttribute::int_as_string(
        "gen_ai.usage.output_tokens",
        usage.output.unwrap_or(0),
    ));
    if let Some(total) = usage.total {
        attrs.push(OtelAttribute::int_as_string(
            "gen_ai.usage.total_tokens",
            total,
        ));
    }
    if let Some(cached_input) = usage.cached_input {
        attrs.push(OtelAttribute::int_as_string(
            "gen_ai.usage.cache_read_input_tokens",
            cached_input,
        ));
    }
    if let Some(response_id) = usage.response_id.as_deref() {
        attrs.push(OtelAttribute::string("gen_ai.response.id", response_id));
    }
    attrs
}

fn resolve_chat_id(turn: &Turn, trace_id: &str) -> String {
    turn.chat_id
        .as_deref()
        .filter(|value| !value.is_empty())
        .unwrap_or(trace_id)
        .to_string()
}

fn build_root_span(
    trace_id: &str,
    span_id: &str,
    turn: &Turn,
    session_id: &str,
    chat_id: &str,
    username: &str,
    now_ns: &str,
    turn_number: usize,
    parent_span_id_override: Option<&str>,
) -> OtelSpan {
    let start_ns = if !turn.user_timestamp.is_empty() {
        iso_to_nanos(&turn.user_timestamp)
    } else {
        now_ns.to_string()
    };
    let trace_name = format!("Turn {}", turn_number);
    let provider = agentic_otel_common::extract_provider_name(&turn.model).unwrap_or("openai");

    let (trace_input, trace_output, output_image_stats) = if is_privacy_mode() {
        (
            "[REDACTED]".to_string(),
            "[REDACTED]".to_string(),
            InlineImageFilterStats::default(),
        )
    } else {
        let input = serialize_messages_default(
            &crate::common::message_format::build_input_messages(turn),
            0,
        );
        let (output_messages, output_image_stats) =
            crate::common::message_format::build_output_messages_with_stats(turn);
        let output = serialize_messages_default(&output_messages, 0);
        (input, output, output_image_stats)
    };
    observe_inline_image_filter(&output_image_stats, "gen_ai.output.messages");

    let mut attributes = vec![
        OtelAttribute::string(INTERNAL_META_ROOT_KEY, INTERNAL_META_ROOT_VALUE),
        OtelAttribute::string("gen_ai.conversation.id", session_id),
        OtelAttribute::string("gen_ai.chat.id", chat_id),
        OtelAttribute::string("gen_ai.span.kind", "CHAT"),
        OtelAttribute::string("gen_ai.operation.name", "invoke_agent"),
        OtelAttribute::string("gen_ai.provider.name", provider),
        OtelAttribute::string("agentic.runtime.name", "codex"),
        OtelAttribute::int_as_string("agentic.turn.number", turn_number as i64),
        OtelAttribute::string("agentic.turn.trigger", turn.trigger.as_str()),
        OtelAttribute::string("ant.username", username),
        OtelAttribute::string("user.id", username),
    ];

    if !turn.cwd.is_empty() {
        attributes.push(OtelAttribute::string("agentic.runtime.cwd", &turn.cwd));
    }

    // Sub-agent origin: if this session is a sub-agent, add parent correlation attributes
    if let Some(ref src) = turn.subagent_source {
        attributes.push(OtelAttribute::string(
            "agentic.subagent.parent_thread_id",
            &src.parent_thread_id,
        ));
        attributes.push(OtelAttribute::int_as_string(
            "agentic.subagent.depth",
            src.depth as i64,
        ));
        if let Some(ref path) = src.agent_path {
            attributes.push(OtelAttribute::string("agentic.subagent.agent_path", path));
        }
        if let Some(ref nick) = src.agent_nickname {
            attributes.push(OtelAttribute::string(
                "agentic.subagent.agent_nickname",
                nick,
            ));
        }
        if let Some(ref role) = src.agent_role {
            attributes.push(OtelAttribute::string("agentic.subagent.agent_role", role));
        }
    }

    if !turn.model.is_empty() {
        attributes.push(OtelAttribute::string("gen_ai.request.model", &turn.model));
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
    attributes.push(OtelAttribute::string(
        "gen_ai.system_instructions",
        serialize_system_instructions(
            if is_privacy_mode() {
                "[REDACTED]"
            } else {
                &turn.system_instructions
            },
            20_000,
        ),
    ));

    for (key, value) in build_custom_metadata() {
        let rendered = match value {
            Value::String(text) => text,
            other => serde_json::to_string(&other).unwrap_or_default(),
        };
        attributes.push(OtelAttribute::string(
            &format!("ant.custom.{}", key),
            &rendered,
        ));
    }

    // Rule 7: Root span error — tool failures stay on ToolSpan. The root is
    // failed only when the turn itself is terminally failed/aborted.
    let status = match turn.terminal_state {
        TurnTerminalState::Completed | TurnTerminalState::HeuristicComplete => OtelStatus::ok(),
        TurnTerminalState::Aborted => {
            attributes.push(OtelAttribute::string("error.type", "cancelled"));
            OtelStatus::error("turn aborted or cancelled")
        }
        TurnTerminalState::Failed => {
            if let Some(error) = &turn.terminal_error {
                let message = if is_privacy_mode() {
                    "[REDACTED]"
                } else {
                    &error.message
                };
                attributes.push(OtelAttribute::string("error.type", &error.error_type));
                attributes.push(OtelAttribute::string("error.message", message));
                OtelStatus::error(message)
            } else {
                attributes.push(OtelAttribute::string("error.type", "llm_api_error"));
                OtelStatus::error("LLM request failed")
            }
        }
        TurnTerminalState::Superseded | TurnTerminalState::Open => OtelStatus::unset(),
    };

    OtelSpan {
        trace_id: trace_id.to_string(),
        span_id: span_id.to_string(),
        parent_span_id: parent_span_id_override.unwrap_or("").to_string(),
        name: trace_name,
        kind: SPAN_KIND_SERVER,
        start_time_unix_nano: start_ns,
        end_time_unix_nano: now_ns.to_string(),
        attributes,
        status,
    }
}

fn append_sofa_trace_attrs(
    root_span: &mut OtelSpan,
    sofa_traceid: Option<&str>,
    sofa_rpcid: Option<&str>,
) {
    if let Some(value) = sofa_traceid.filter(|value| !value.is_empty()) {
        root_span
            .attributes
            .push(OtelAttribute::string("_internal.sofa.trace_id", value));
    }
    if let Some(value) = sofa_rpcid.filter(|value| !value.is_empty()) {
        root_span
            .attributes
            .push(OtelAttribute::string("_internal.sofa.span_id", value));
    }
}

/// Compute the end time (nanos string) for a generation span at `assistant_index`.
///
/// Priority:
/// 1. Latest `ended_timestamp` among tool calls belonging to this generation
/// 2. Timestamp of the next assistant message (i.e., next generation started)
/// 3. The assistant's own timestamp (zero-duration span)
/// 4. `fallback_ns` if nothing else is available
/// Compute the start/end window of a sub-agent's actual task execution from
/// its own transcript. Used when merging a sync sub-agent into the parent
/// trace so the resulting AGENT span reflects the child's work duration
/// (not the parent's spawn→last-wait wall-clock window, which would
/// visually overlap with the now-sibling `wait_agent` tool spans).
///
/// Falls back to the parent-side `spawn_fallback` / `wait_fallback` when the
/// child has no usable turn (e.g. fork-context turn was dropped and no real
/// turn remains).
fn compute_child_task_window(
    child_turns: &[&Turn],
    spawn_fallback: &str,
    wait_fallback: &str,
) -> (String, String) {
    let Some(first_turn) = child_turns.first() else {
        return (spawn_fallback.to_string(), wait_fallback.to_string());
    };
    let Some(last_turn) = child_turns.last() else {
        return (spawn_fallback.to_string(), wait_fallback.to_string());
    };
    let start = if !first_turn.user_timestamp.is_empty() {
        iso_to_nanos(&first_turn.user_timestamp)
    } else {
        spawn_fallback.to_string()
    };
    // The parent-side wait is only a fallback. A wait may finish after the
    // child emitted its terminal assistant message, so seeding the max-time
    // calculation with it would incorrectly stretch the child task window.
    let child_end = compute_turn_end_ns(last_turn, "0");
    let start_ns = start.parse::<u128>().unwrap_or(0);
    let child_end_ns = child_end.parse::<u128>().unwrap_or(0);
    let end = if child_end_ns > 0 && (start_ns == 0 || child_end_ns >= start_ns) {
        child_end
    } else {
        wait_fallback.to_string()
    };
    (start, end)
}

fn child_execution_turns<'a>(
    child: &'a SubagentChild,
    parent_turns: &[Turn],
    spawn_timestamp: &str,
) -> Vec<&'a Turn> {
    let parent_turn_ids: HashSet<&str> = parent_turns
        .iter()
        .map(|turn| turn.turn_id.as_str())
        .collect();
    let spawn_ns = iso_to_nanos(spawn_timestamp).parse::<u128>().unwrap_or(0);

    let filtered: Vec<&Turn> = child
        .turns
        .iter()
        .filter(|turn| !parent_turn_ids.contains(turn.turn_id.as_str()))
        .filter(|turn| {
            let turn_ns = iso_to_nanos(&turn.user_timestamp)
                .parse::<u128>()
                .unwrap_or(0);
            spawn_ns == 0 || turn_ns == 0 || turn_ns >= spawn_ns
        })
        .collect();
    if filtered.is_empty() {
        child.turns.last().into_iter().collect()
    } else {
        filtered
    }
}

#[derive(Clone, Copy)]
struct SubagentInvocation<'a> {
    turn_index: usize,
    tool: &'a ToolCall,
}

fn subagent_invocations<'a>(
    parent_turns: &'a [Turn],
    child: &SubagentIdentity,
    spawn_call_id: &str,
) -> Vec<SubagentInvocation<'a>> {
    let mut invocations = Vec::new();
    for (turn_index, turn) in parent_turns.iter().enumerate() {
        for tool in &turn.tool_calls {
            let is_spawn = tool.call_id == spawn_call_id && tool.name == "spawn_agent";
            let is_followup = tool.name == "followup_task"
                && turn.collab_agent_events.iter().any(|event| {
                    matches!(
                        event,
                        CollabAgentEvent::SubagentActivity {
                            event_id,
                            agent_thread_id,
                            kind,
                            ..
                        } if event_id == &tool.call_id
                            && agent_thread_id == &child.session_id
                            && kind == "interacted"
                    )
                });
            if is_spawn || is_followup {
                invocations.push(SubagentInvocation { turn_index, tool });
            }
        }
    }
    invocations.sort_by_key(|invocation| {
        iso_to_nanos(&invocation.tool.started_timestamp)
            .parse::<u128>()
            .unwrap_or(0)
    });
    invocations.dedup_by(|left, right| left.tool.call_id == right.tool.call_id);
    invocations
}

fn child_turns_for_invocation<'a>(
    child_turns: &[&'a Turn],
    invocations: &[SubagentInvocation<'_>],
    invocation_index: usize,
) -> Vec<&'a Turn> {
    let start_ns = invocations
        .get(invocation_index)
        .map(|invocation| iso_to_nanos(&invocation.tool.started_timestamp))
        .and_then(|value| value.parse::<u128>().ok())
        .unwrap_or(0);
    let next_start_ns = invocations
        .get(invocation_index + 1)
        .map(|invocation| iso_to_nanos(&invocation.tool.started_timestamp))
        .and_then(|value| value.parse::<u128>().ok());

    child_turns
        .iter()
        .copied()
        .filter(|turn| {
            let turn_ns = iso_to_nanos(&turn.user_timestamp)
                .parse::<u128>()
                .unwrap_or(0);
            (start_ns == 0 || turn_ns == 0 || turn_ns >= start_ns)
                && next_start_ns.is_none_or(|next| turn_ns == 0 || turn_ns < next)
        })
        .collect()
}

fn child_invocation_output(child_turns: &[&Turn]) -> String {
    child_turns
        .iter()
        .rev()
        .flat_map(|turn| turn.assistants.iter().rev())
        .find(|assistant| !assistant.text.is_empty())
        .map(|assistant| assistant.text.clone())
        .unwrap_or_default()
}

fn child_turn_has_exportable_descendants(turn: &Turn) -> bool {
    turn.assistants
        .iter()
        .enumerate()
        .any(|(assistant_index, assistant)| {
            assistant.usage.has_any()
                || turn
                    .tool_calls
                    .iter()
                    .any(|tool| tool.assistant_index == assistant_index && !tool.output.is_null())
        })
        || turn.tool_calls.iter().any(|tool| !tool.output.is_null())
        || !turn.compactions.is_empty()
        || !turn.skill_invocations.is_empty()
}

fn child_turns_have_exportable_descendants(child_turns: &[&Turn]) -> bool {
    child_turns
        .iter()
        .any(|turn| child_turn_has_exportable_descendants(turn))
}

fn native_child_turns_for_activation<'a>(
    child: &'a SubagentChild,
    parent_turns: &[Turn],
    activations: &[&SubagentActivationEvidence],
    activation_index: usize,
) -> Vec<&'a Turn> {
    let parent_turn_ids: HashSet<&str> = parent_turns
        .iter()
        .map(|turn| turn.turn_id.as_str())
        .collect();
    let start_ns = activations
        .get(activation_index)
        .and_then(|activation| activation.started_at_unix_nano.as_deref())
        .and_then(|value| value.parse::<u128>().ok())
        .unwrap_or(0);
    let next_start_ns = activations
        .get(activation_index + 1)
        .and_then(|activation| activation.started_at_unix_nano.as_deref())
        .and_then(|value| value.parse::<u128>().ok());

    child
        .turns
        .iter()
        .filter(|turn| !parent_turn_ids.contains(turn.turn_id.as_str()))
        .filter(|turn| {
            let turn_ns = iso_to_nanos(&turn.user_timestamp)
                .parse::<u128>()
                .unwrap_or(0);
            (start_ns == 0 || turn_ns == 0 || turn_ns >= start_ns)
                && next_start_ns.is_none_or(|next| turn_ns == 0 || turn_ns < next)
        })
        .collect()
}

fn subagent_invocation_terminal_output(
    parent_turns: &[Turn],
    child: &SubagentIdentity,
    invocations: &[SubagentInvocation<'_>],
    invocation_index: usize,
) -> Option<String> {
    let child_path = child.agent_path.as_deref()?;
    let start_ns = invocations
        .get(invocation_index)
        .map(|invocation| iso_to_nanos(&invocation.tool.started_timestamp))
        .and_then(|value| value.parse::<u128>().ok())
        .unwrap_or(0);
    let next_start_ns = invocations
        .get(invocation_index + 1)
        .map(|invocation| iso_to_nanos(&invocation.tool.started_timestamp))
        .and_then(|value| value.parse::<u128>().ok());

    parent_turns
        .iter()
        .flat_map(|turn| turn.collab_agent_events.iter())
        .filter_map(|event| match event {
            CollabAgentEvent::AgentMessage {
                author,
                message_type: Some(message_type),
                content,
                timestamp,
                ..
            } if author == child_path && message_type == "FINAL_ANSWER" => {
                let timestamp_ns = iso_to_nanos(timestamp).parse::<u128>().unwrap_or(0);
                ((start_ns == 0 || timestamp_ns >= start_ns)
                    && next_start_ns.is_none_or(|next| timestamp_ns < next))
                .then_some((timestamp_ns, content.clone()))
            }
            _ => None,
        })
        .max_by_key(|(timestamp_ns, _)| *timestamp_ns)
        .map(|(_, content)| content)
}

/// Compute the end timestamp for a turn's root span.
/// Uses the latest timestamp found among: assistants, tool ends, or fallback.
fn compute_turn_end_ns(turn: &Turn, fallback_ns: &str) -> String {
    let mut max_ns: u128 = fallback_ns.parse().unwrap_or(0);

    if !turn.completion_timestamp.is_empty() {
        let ns = iso_to_nanos(&turn.completion_timestamp)
            .parse::<u128>()
            .unwrap_or(0);
        if ns > max_ns {
            max_ns = ns;
        }
    }
    for assistant in &turn.assistants {
        if !assistant.timestamp.is_empty() {
            let ns = iso_to_nanos(&assistant.timestamp)
                .parse::<u128>()
                .unwrap_or(0);
            if ns > max_ns {
                max_ns = ns;
            }
        }
    }
    for tool_call in &turn.tool_calls {
        if !tool_call.ended_timestamp.is_empty() {
            let ns = iso_to_nanos(&tool_call.ended_timestamp)
                .parse::<u128>()
                .unwrap_or(0);
            if ns > max_ns {
                max_ns = ns;
            }
        }
        if !tool_call.started_timestamp.is_empty() {
            let ns = iso_to_nanos(&tool_call.started_timestamp)
                .parse::<u128>()
                .unwrap_or(0);
            if ns > max_ns {
                max_ns = ns;
            }
        }
    }

    if max_ns == 0 {
        return fallback_ns.to_string();
    }
    max_ns.to_string()
}

/// Compute a generation start from invocation-local causal evidence.
///
/// `token_count` is deliberately excluded: it carries usage and may close an
/// invocation, but it is not evidence that the next invocation started.
fn compute_generation_start_ns(turn: &Turn, assistant_index: usize, turn_start_ns: &str) -> String {
    let assistant = &turn.assistants[assistant_index];
    if !assistant.invocation_started_timestamp.is_empty() {
        return iso_to_nanos(&assistant.invocation_started_timestamp);
    }
    if assistant_index == 0 {
        return turn_start_ns.to_string();
    }

    if let Some(previous_callback_end) = turn
        .tool_calls
        .iter()
        .filter(|tool| tool.assistant_index < assistant_index && !tool.ended_timestamp.is_empty())
        .filter_map(|tool| iso_to_nanos(&tool.ended_timestamp).parse::<u128>().ok())
        .max()
    {
        return previous_callback_end.to_string();
    }

    turn_start_ns.to_string()
}

/// Compute the end time (nanos string) for a generation span.
///
/// Normalized invocations carry their own completion timestamp. Legacy callers
/// may still use `token_count_timestamp` as a last-resort closure/upper bound,
/// never as the next generation's start.
///
/// - If tools exist in the window: gen_end = min(tools' started_timestamp)
///   (LLM stopped thinking, started executing tools — zero overlap)
/// - If no tools: gen_end = agent_message timestamp or token_count_timestamp
fn compute_generation_end_ns(
    turn: &Turn,
    assistant_index: usize,
    gen_start_ns: &str,
    fallback_ns: &str,
) -> String {
    let assistant = &turn.assistants[assistant_index];
    let start_u128: u128 = gen_start_ns.parse().unwrap_or(0);

    if !assistant.invocation_started_timestamp.is_empty() {
        if !assistant.timestamp.is_empty() {
            let end_ns = iso_to_nanos(&assistant.timestamp)
                .parse::<u128>()
                .unwrap_or(start_u128);
            return end_ns.max(start_u128).to_string();
        }
        if !assistant.token_count_timestamp.is_empty() {
            let end_ns = iso_to_nanos(&assistant.token_count_timestamp)
                .parse::<u128>()
                .unwrap_or(start_u128);
            return end_ns.max(start_u128).to_string();
        }
        return start_u128.to_string();
    }

    // Determine the time window upper bound (this LLM call's completion)
    let window_end: u128 = if !assistant.token_count_timestamp.is_empty() {
        iso_to_nanos(&assistant.token_count_timestamp)
            .parse()
            .unwrap_or(u128::MAX)
    } else {
        u128::MAX
    };

    // Find tools in this generation's time window
    let first_tool_start: Option<u128> = turn
        .tool_calls
        .iter()
        .filter(|tc| {
            if tc.started_timestamp.is_empty() {
                return false;
            }
            let tc_ns: u128 = iso_to_nanos(&tc.started_timestamp).parse().unwrap_or(0);
            tc_ns >= start_u128 && tc_ns < window_end
        })
        .filter_map(|tc| iso_to_nanos(&tc.started_timestamp).parse::<u128>().ok())
        .min();

    if let Some(tool_ns) = first_tool_start {
        if tool_ns >= start_u128 {
            return tool_ns.to_string();
        }
    }

    // No tools in window: use agent_message timestamp or token_count timestamp
    if !assistant.timestamp.is_empty() {
        let ts_ns: u128 = iso_to_nanos(&assistant.timestamp).parse().unwrap_or(0);
        if ts_ns >= start_u128 {
            return ts_ns.to_string();
        }
    }
    if !assistant.token_count_timestamp.is_empty() {
        return iso_to_nanos(&assistant.token_count_timestamp);
    }

    // Final fallback: turn-wide end so the last generation gets non-zero duration
    let turn_end = compute_turn_end_ns(turn, fallback_ns);
    if turn_end.parse::<u128>().unwrap_or(0) > start_u128 {
        return turn_end;
    }

    fallback_ns.to_string()
}

#[allow(clippy::too_many_arguments)]
fn build_generation_span_with_id(
    trace_id: &str,
    parent_span_id: &str,
    turn: &Turn,
    assistant: &AssistantMessage,
    assistant_index: usize,
    gen_start_ns: &str,
    end_ns: &str,
    username: &str,
    session_id: &str,
    chat_id: &str,
    span_id_override: Option<&str>,
) -> (String, OtelSpan) {
    let span_id = span_id_override
        .map(str::to_string)
        .unwrap_or_else(otel_span_id);
    let start_ns = gen_start_ns.to_string();
    let (gen_input, gen_output, gen_delta_attr) = if is_privacy_mode() {
        (
            "[REDACTED]".to_string(),
            "[REDACTED]".to_string(),
            Some("[REDACTED]".to_string()),
        )
    } else {
        let use_accumulated = env_equals_ignore_case("CDXHOOKS_INPUT_FORMAT", "accumulated");
        let (input, delta_attr) = if use_accumulated {
            let (accumulated, accumulated_image_stats) =
                crate::common::message_format::build_generation_accumulated_input_with_stats(
                    turn,
                    assistant_index,
                );
            let (delta, delta_image_stats) =
                crate::common::message_format::build_generation_delta_input_with_stats(
                    turn,
                    assistant_index,
                );
            observe_inline_image_filter(&accumulated_image_stats, "gen_ai.input.messages");
            observe_inline_image_filter(&delta_image_stats, "agentic.input.delta");
            let delta_count = delta.len();
            let input = serialize_messages_default(&accumulated, delta_count);
            let delta_str = serialize_messages_default(&delta, 0);
            (input, Some(delta_str))
        } else {
            let (delta, delta_image_stats) =
                crate::common::message_format::build_generation_delta_input_with_stats(
                    turn,
                    assistant_index,
                );
            observe_inline_image_filter(&delta_image_stats, "gen_ai.input.messages");
            let input = serialize_messages_default(&delta, 0);
            (input, None)
        };

        let (output_message, output_image_stats) =
            crate::common::message_format::build_generation_output_message_with_stats(
                assistant,
                assistant_index,
                turn,
            );
        observe_inline_image_filter(&output_image_stats, "gen_ai.output.messages");
        let output = serialize_messages_default(&[output_message], 0);

        (input, output, delta_attr)
    };

    let mut attributes = vec![
        OtelAttribute::string("gen_ai.span.kind", "LLM"),
        OtelAttribute::string("gen_ai.operation.name", "chat"),
        OtelAttribute::string("gen_ai.conversation.id", session_id),
        OtelAttribute::string("gen_ai.chat.id", chat_id),
        OtelAttribute::string("gen_ai.input.messages", &gen_input),
        OtelAttribute::string("gen_ai.output.messages", &gen_output),
        OtelAttribute::string(
            "gen_ai.system_instructions",
            serialize_system_instructions(
                if is_privacy_mode() {
                    "[REDACTED]"
                } else {
                    &turn.system_instructions
                },
                20_000,
            ),
        ),
        OtelAttribute::string("ant.username", username),
        OtelAttribute::string("user.id", username),
        OtelAttribute::int_as_string("agentic.turn.index", 0),
        OtelAttribute::int_as_string("agentic.response.index", assistant_index as i64),
        OtelAttribute::int_as_string(
            "agentic.tool.count",
            turn.tool_calls
                .iter()
                .filter(|tool| tool.assistant_index == assistant_index)
                .count() as i64,
        ),
    ];
    if let Some(ref delta_str) = gen_delta_attr {
        attributes.push(OtelAttribute::string("agentic.input.delta", delta_str));
    }
    let model = if turn.model.is_empty() {
        "unknown"
    } else {
        &turn.model
    };
    attributes.push(OtelAttribute::string("gen_ai.request.model", model));
    attributes.push(OtelAttribute::string("gen_ai.response.model", model));
    let provider = agentic_otel_common::extract_provider_name(model).unwrap_or("openai");
    attributes.push(OtelAttribute::string("gen_ai.provider.name", provider));

    // Infer finish reason: if THIS generation has tool calls, the LLM chose to call tools
    let has_tools = turn
        .tool_calls
        .iter()
        .any(|tc| tc.assistant_index == assistant_index);
    let has_later_observable_generation = turn
        .assistants
        .iter()
        .enumerate()
        .skip(assistant_index.saturating_add(1))
        .any(|(index, _)| turn.has_semantic_assistant_at(index));
    // A turn-level abort does not retroactively cancel generations that already
    // completed by emitting a tool call. Only the trailing observable
    // generation without a terminal action can have been interrupted.
    let generation_cancelled = turn.terminal_state == TurnTerminalState::Aborted
        && !has_tools
        && !has_later_observable_generation;
    let finish_reason = if generation_cancelled {
        "cancelled"
    } else if has_tools {
        "tool_calls"
    } else {
        "stop"
    };
    attributes.push(OtelAttribute::string(
        "gen_ai.response.finish_reasons",
        finish_reason,
    ));

    attributes.extend(usage_attrs(&assistant.usage));

    // Rule 5: LLM duration_ms
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

    let span_name = if turn.model.is_empty() {
        format!("chat unknown-{}", assistant_index + 1)
    } else {
        format!("chat {}", turn.model)
    };
    // Rule 7: Generation span error — tool failures stay on ToolSpan. A
    // turn-level abort applies only to the trailing interrupted generation,
    // not to earlier generations that completed with a terminal action.
    let status = if generation_cancelled {
        attributes.push(OtelAttribute::string("error.type", "cancelled"));
        OtelStatus::error("turn aborted or cancelled")
    } else if finish_reason == "max_tokens" || finish_reason.contains("error") {
        attributes.push(OtelAttribute::string("error.type", finish_reason));
        OtelStatus::error(format!("LLM finish_reason: {}", finish_reason))
    } else {
        OtelStatus::ok()
    };

    (
        span_id.clone(),
        OtelSpan {
            trace_id: trace_id.to_string(),
            span_id,
            parent_span_id: parent_span_id.to_string(),
            name: span_name,
            kind: SPAN_KIND_INTERNAL,
            start_time_unix_nano: start_ns,
            end_time_unix_nano: end_ns.to_string(),
            attributes,
            status,
        },
    )
}

fn build_synthetic_llm_error_span(
    trace_id: &str,
    parent_span_id: &str,
    turn: &Turn,
    username: &str,
    session_id: &str,
    chat_id: &str,
    fallback_ns: &str,
) -> Option<OtelSpan> {
    let error = turn.terminal_error.as_ref()?;
    let model = if turn.model.is_empty() {
        "unknown"
    } else {
        &turn.model
    };
    let start_ns = if turn.user_timestamp.is_empty() {
        fallback_ns.to_string()
    } else {
        iso_to_nanos(&turn.user_timestamp)
    };
    let raw_end_ns = if turn.completion_timestamp.is_empty() {
        fallback_ns.to_string()
    } else {
        iso_to_nanos(&turn.completion_timestamp)
    };
    let start_value = start_ns.parse::<u128>().unwrap_or(0);
    let end_value = raw_end_ns
        .parse::<u128>()
        .unwrap_or(start_value)
        .max(start_value);
    let end_ns = end_value.to_string();
    let message = if is_privacy_mode() {
        "[REDACTED]"
    } else {
        &error.message
    };
    let provider = agentic_otel_common::extract_provider_name(model).unwrap_or("openai");
    let mut attributes = vec![
        OtelAttribute::string("gen_ai.span.kind", "LLM"),
        OtelAttribute::string("gen_ai.operation.name", "chat"),
        OtelAttribute::string("gen_ai.conversation.id", session_id),
        OtelAttribute::string("gen_ai.chat.id", chat_id),
        OtelAttribute::string("gen_ai.request.model", model),
        OtelAttribute::string("gen_ai.provider.name", provider),
        OtelAttribute::int_as_string("gen_ai.usage.input_tokens", 0),
        OtelAttribute::int_as_string("gen_ai.usage.output_tokens", 0),
        OtelAttribute::string("agentic.usage.input_tokens.missing", "true"),
        OtelAttribute::string("agentic.usage.output_tokens.missing", "true"),
        OtelAttribute::int_as_string(
            "agentic.llm.duration_ms",
            end_value
                .saturating_sub(start_value)
                .saturating_div(1_000_000) as i64,
        ),
        OtelAttribute::string("agentic.llm.is_synthetic", "true"),
        OtelAttribute::string("agentic.llm.error.type", &error.error_type),
        OtelAttribute::string("agentic.llm.error.message", message),
        OtelAttribute::string("error.type", &error.error_type),
        OtelAttribute::string("error.message", message),
        OtelAttribute::string("ant.username", username),
        OtelAttribute::string("user.id", username),
        OtelAttribute::int_as_string("agentic.turn.index", 0),
        OtelAttribute::int_as_string("agentic.response.index", 0),
        OtelAttribute::int_as_string("agentic.tool.count", 0),
    ];
    if let Some(status_code) = error.status_code {
        attributes.push(OtelAttribute::int_as_string(
            "agentic.llm.error.status_code",
            status_code,
        ));
    }

    Some(OtelSpan {
        trace_id: trace_id.to_string(),
        span_id: otel_span_id(),
        parent_span_id: parent_span_id.to_string(),
        name: format!("chat {model}"),
        kind: SPAN_KIND_INTERNAL,
        start_time_unix_nano: start_ns,
        end_time_unix_nano: end_ns,
        attributes,
        status: OtelStatus::error(message),
    })
}

// --- Tool span builder ---

const CAUSAL_DISPLAY_EPSILON_NS: u128 = 1_000_000;

fn compute_tool_span_window(turn: &Turn, tool_call: &ToolCall, now_ns: &str) -> (String, String) {
    let raw_start = if !tool_call.started_timestamp.is_empty() {
        iso_to_nanos(&tool_call.started_timestamp)
    } else {
        now_ns.to_string()
    };
    let raw_end = if !tool_call.ended_timestamp.is_empty() {
        iso_to_nanos(&tool_call.ended_timestamp)
    } else {
        now_ns.to_string()
    };

    let Some(_) = turn.assistants.get(tool_call.assistant_index) else {
        return (raw_start, raw_end);
    };
    let turn_start = if !turn.user_timestamp.is_empty() {
        iso_to_nanos(&turn.user_timestamp)
    } else {
        now_ns.to_string()
    };
    let generation_start =
        compute_generation_start_ns(turn, tool_call.assistant_index, &turn_start)
            .parse::<u128>()
            .unwrap_or(0);
    let generation_end = compute_generation_end_ns(
        turn,
        tool_call.assistant_index,
        &generation_start.to_string(),
        &turn_start,
    )
    .parse::<u128>()
    .unwrap_or(generation_start);
    let raw_start_ns = raw_start.parse::<u128>().unwrap_or(0);
    let raw_end_ns = raw_end.parse::<u128>().unwrap_or(raw_start_ns);
    let adjusted_start = generation_start.saturating_add(CAUSAL_DISPLAY_EPSILON_NS);

    // Millisecond-resolution transcripts can collapse a tool-only generation
    // and its following tool call onto the same timestamp. Preserve the raw
    // causal association (`assistant_index` + generation end), but add the
    // smallest display epsilon when the tool has enough observed duration to
    // keep a valid span. Parallel tools in the same invocation receive the
    // same adjusted start.
    if generation_end.saturating_sub(generation_start) < CAUSAL_DISPLAY_EPSILON_NS
        && raw_start_ns == generation_end
        && raw_start_ns < adjusted_start
        && raw_end_ns >= adjusted_start
    {
        return (adjusted_start.to_string(), raw_end);
    }

    (raw_start, raw_end)
}

/// Build a tool span. Tool spans are siblings of generation spans (children of root).
#[cfg(test)]
fn build_tool_span(
    trace_id: &str,
    parent_span_id: &str,
    tool_call: &ToolCall,
    turn: &Turn,
    now_ns: &str,
    username: &str,
    session_id: &str,
    chat_id: &str,
    parallel_group: Option<&str>,
) -> OtelSpan {
    build_tool_span_with_suppression(
        trace_id,
        parent_span_id,
        tool_call,
        turn,
        now_ns,
        username,
        session_id,
        chat_id,
        parallel_group,
        &HashSet::new(),
    )
}

fn build_tool_span_with_suppression(
    trace_id: &str,
    parent_span_id: &str,
    tool_call: &ToolCall,
    turn: &Turn,
    now_ns: &str,
    username: &str,
    session_id: &str,
    chat_id: &str,
    parallel_group: Option<&str>,
    suppressed_command_path_skills: &HashSet<String>,
) -> OtelSpan {
    let span_id = if tool_call.call_id.is_empty() {
        otel_span_id()
    } else {
        deterministic_tool_span_id(&tool_call.call_id)
    };
    let (start_ns, end_ns) = compute_tool_span_window(turn, tool_call, now_ns);

    // Rule 12: preserve truncation metadata
    let mut truncation_input_meta = json!(null);
    let mut truncation_output_meta = json!(null);
    let privacy = is_privacy_mode();
    let (input_text, output_text) = if privacy {
        ("[REDACTED]".to_string(), "[REDACTED]".to_string())
    } else {
        let (filtered_input, input_image_stats) = filter_inline_base64_images(&tool_call.input);
        let (filtered_output, output_image_stats) = filter_inline_base64_images(&tool_call.output);
        observe_inline_image_filter(&input_image_stats, "gen_ai.tool.call.arguments");
        observe_inline_image_filter(&output_image_stats, "gen_ai.tool.call.result");
        let (truncated_input, input_meta_val) =
            truncate_value_observed(&filtered_input, "agentic.tool.input");
        let (truncated_output, output_meta_val) =
            truncate_value_observed(&filtered_output, "agentic.tool.output");
        truncation_input_meta = input_meta_val;
        truncation_output_meta = output_meta_val;
        (
            render_json_value(&truncated_input),
            render_json_value(&truncated_output),
        )
    };

    // Classify tool: subagent / builtin / mcp / skill
    // Subagent tools (spawn_agent, wait_agent, etc.) are identified by name prefix.
    // Skill classification is scoped to the current tool call. Turn-level skill hints
    // describe context only and must not re-label every tool span in the turn.
    const SUBAGENT_TOOL_NAMES: &[&str] = &[
        "spawn_agent",
        "wait_agent",
        "wait",
        "interrupt_agent",
        "send_input",
        "resume_agent",
        "close_agent",
        "followup_task",
        "send_message",
        "list_agents",
    ];
    let skill_read = extract_skill_read_name(tool_call);
    let is_shell = is_shell_tool(&tool_call.name);
    let success = if is_shell && tool_call.success.is_none() {
        shell_exit_code(&tool_call.output).map(|code| code == 0)
    } else {
        tool_call.success
    };
    let error = if success == Some(false) {
        Some(("tool_error".to_string(), output_text.clone()))
    } else if is_shell && success == Some(true) {
        None
    } else {
        detect_tool_error_from_output(&tool_call.output)
    };
    let skill_name = skill_read
        .clone()
        .or_else(|| extract_skill_name_from_tool_call(tool_call))
        .filter(|name| {
            let is_failed_read = skill_read.is_some() && error.is_some();
            tool_call.name == "Skill"
                || is_failed_read
                || !suppressed_command_path_skills.contains(&skill_match_key(name))
        });
    let info = if SUBAGENT_TOOL_NAMES.contains(&tool_call.name.as_str()) {
        use agentic_otel_common::AgenticToolInfo;
        AgenticToolInfo {
            tool_type: "subagent",
            name: tool_call.name.clone(),
            id: if tool_call.call_id.is_empty() {
                format!("subagent-{}", tool_call.name)
            } else {
                tool_call.call_id.clone()
            },
            raw_name: tool_call.name.clone(),
        }
    } else if let Some(ref sn) = skill_name {
        use agentic_otel_common::AgenticToolInfo;
        AgenticToolInfo {
            tool_type: "skill",
            name: sn.clone(),
            id: if tool_call.call_id.is_empty() {
                format!("skill-{}", sn)
            } else {
                tool_call.call_id.clone()
            },
            raw_name: tool_call.name.clone(),
        }
    } else {
        parse_agentic_tool(&tool_call.name, &tool_call.call_id, None)
    };

    let span_name = tool_span_name(&info);

    let mut attributes = vec![
        OtelAttribute::string("gen_ai.span.kind", "TOOL"),
        OtelAttribute::string("gen_ai.operation.name", "execute_tool"),
        OtelAttribute::string("gen_ai.conversation.id", session_id),
        OtelAttribute::string("gen_ai.chat.id", chat_id),
        OtelAttribute::string("gen_ai.tool.call.arguments", &input_text),
        OtelAttribute::string("gen_ai.tool.call.result", &output_text),
        OtelAttribute::string("gen_ai.tool.name", &span_name),
        OtelAttribute::string("gen_ai.tool.call.id", &tool_call.call_id),
        OtelAttribute::string("ant.username", username),
        OtelAttribute::string("user.id", username),
    ];

    append_agentic_tool_attrs_typed(&mut attributes, &info);
    if info.tool_type == "skill" && skill_read.as_deref() == Some(info.name.as_str()) {
        attributes.push(OtelAttribute::bool("agentic.tool.derived", true));
    }

    // Rule 5: duration_ms
    let start_u128 = start_ns.parse::<u128>().unwrap_or(0);
    let end_u128 = end_ns.parse::<u128>().unwrap_or(0);
    let duration_ms = if end_u128 > start_u128 {
        (end_u128 - start_u128) / 1_000_000
    } else {
        0
    };
    attributes.push(OtelAttribute::int_as_string(
        "agentic.tool.duration_ms",
        duration_ms as i64,
    ));

    // Rule 12: truncation metadata
    append_truncation_meta(
        &mut attributes,
        &truncation_input_meta,
        &truncation_output_meta,
    );

    // Rule 13: parallel group
    if let Some(pg) = parallel_group {
        attributes.push(OtelAttribute::string("agentic.tool.parallel_group", pg));
    }

    // Rule 15: subagent correlation — match tool call_id with CollabAgent events
    if info.tool_type == "subagent" && !tool_call.call_id.is_empty() {
        use crate::common::transcript::CollabAgentEvent;
        for event in &turn.collab_agent_events {
            match event {
                CollabAgentEvent::SpawnEnd {
                    call_id,
                    new_thread_id,
                    nickname,
                    role,
                    ..
                } if call_id == &tool_call.call_id => {
                    if let Some(ref tid) = new_thread_id {
                        attributes.push(OtelAttribute::string(
                            "agentic.subagent.child_thread_id",
                            tid,
                        ));
                    }
                    if let Some(ref nick) = nickname {
                        attributes.push(OtelAttribute::string("agentic.subagent.nickname", nick));
                    }
                    if let Some(ref r) = role {
                        attributes.push(OtelAttribute::string("agentic.subagent.role", r));
                    }
                    break;
                }
                CollabAgentEvent::InteractionBegin {
                    call_id,
                    receiver_thread_id,
                    ..
                } if call_id == &tool_call.call_id => {
                    attributes.push(OtelAttribute::string(
                        "agentic.subagent.receiver_thread_id",
                        receiver_thread_id,
                    ));
                    break;
                }
                CollabAgentEvent::InteractionEnd {
                    call_id,
                    receiver_thread_id,
                    receiver_nickname,
                    receiver_role,
                    ..
                } if call_id == &tool_call.call_id => {
                    attributes.push(OtelAttribute::string(
                        "agentic.subagent.receiver_thread_id",
                        receiver_thread_id,
                    ));
                    if let Some(ref nick) = receiver_nickname {
                        attributes.push(OtelAttribute::string("agentic.subagent.nickname", nick));
                    }
                    if let Some(ref r) = receiver_role {
                        attributes.push(OtelAttribute::string("agentic.subagent.role", r));
                    }
                    break;
                }
                CollabAgentEvent::SubagentActivity {
                    event_id,
                    agent_thread_id,
                    agent_path,
                    ..
                } if event_id == &tool_call.call_id => {
                    attributes.push(OtelAttribute::string(
                        "agentic.subagent.id",
                        agent_thread_id,
                    ));
                    attributes.push(OtelAttribute::string(
                        "agentic.subagent.child_thread_id",
                        agent_thread_id,
                    ));
                    if let Some(path) = agent_path {
                        attributes.push(OtelAttribute::string("agentic.subagent.agent_path", path));
                    }
                    break;
                }
                _ => {}
            }
        }
    }

    // Rule 7: Error dual-channel — status.code=2 AND error.type attribute
    let status = if let Some((error_type, error_msg)) = error {
        attributes.push(OtelAttribute::string("error.type", error_type));
        OtelStatus::error(if privacy { "[REDACTED]".to_string() } else { error_msg })
    } else if skill_read.is_some() && success.is_none() {
        OtelStatus::unset()
    } else {
        OtelStatus::ok()
    };

    OtelSpan {
        trace_id: trace_id.to_string(),
        span_id,
        parent_span_id: parent_span_id.to_string(),
        name: span_name,
        kind: SPAN_KIND_INTERNAL,
        start_time_unix_nano: start_ns,
        end_time_unix_nano: end_ns,
        attributes,
        status,
    }
}

fn normalized_skill_name(raw: &str) -> Option<String> {
    let raw = raw.trim().strip_prefix('$').unwrap_or(raw.trim());
    if raw.is_empty() {
        return None;
    }
    let parts: Vec<&str> = raw.splitn(3, ':').collect();
    if parts.len() == 3 {
        Some(format!("{}:{}", parts[1], parts[2]))
    } else {
        Some(raw.to_string())
    }
}

fn skill_match_key(raw: &str) -> String {
    normalized_skill_name(raw)
        .unwrap_or_default()
        .to_ascii_lowercase()
}

fn invocation_skill_name(invocation: &SkillInvocation) -> Option<String> {
    normalized_skill_name(
        invocation
            .resolved_workflow
            .as_deref()
            .unwrap_or(&invocation.name),
    )
}

fn invocation_source_name(source: SkillInvocationSource) -> &'static str {
    match source {
        SkillInvocationSource::CodexTextElement => "codex_text_element",
        SkillInvocationSource::OmxUserPromptSubmit => "omx_user_prompt_submit",
        SkillInvocationSource::LegacyMarkdown => "legacy_markdown",
        SkillInvocationSource::CommandPathFallback => "command_path_fallback",
    }
}

fn skill_invocation_call_id(turn: &Turn, invocation: &SkillInvocation, skill_name: &str) -> String {
    format!(
        "skill-invocation:{}:{}:{}:{}-{}",
        turn.turn_id,
        invocation.ordinal,
        skill_name,
        invocation
            .byte_start
            .map(|value| value.to_string())
            .unwrap_or_else(|| "none".to_string()),
        invocation
            .byte_end
            .map(|value| value.to_string())
            .unwrap_or_else(|| "none".to_string())
    )
}

fn skill_invocation_result(turn: &Turn) -> Value {
    if let Some(text) = turn
        .assistants
        .iter()
        .rev()
        .find(|assistant| assistant.phase == "final_answer" && !assistant.text.is_empty())
        .or_else(|| {
            turn.assistants
                .iter()
                .rev()
                .find(|assistant| !assistant.text.is_empty())
        })
        .map(|assistant| assistant.text.clone())
    {
        return Value::String(text);
    }

    match turn.terminal_state {
        TurnTerminalState::Completed | TurnTerminalState::HeuristicComplete => {
            json!({"status":"completed","result_missing":true})
        }
        TurnTerminalState::Failed => json!({"status":"failed"}),
        TurnTerminalState::Aborted => json!({"status":"aborted"}),
        TurnTerminalState::Superseded => json!({"status":"superseded","result_missing":true}),
        TurnTerminalState::Open => json!({"status":"open","result_missing":true}),
    }
}

fn build_skill_invocation_span(
    trace_id: &str,
    parent_span_id: &str,
    turn: &Turn,
    invocation: &SkillInvocation,
    skill_name: &str,
    root_start_ns: &str,
    root_end_ns: &str,
    username: &str,
    session_id: &str,
    chat_id: &str,
    privacy_mode: bool,
) -> OtelSpan {
    let root_start = root_start_ns.parse::<u128>().unwrap_or(0);
    let root_end = root_end_ns.parse::<u128>().unwrap_or(root_start);
    let minimum_child_start = root_start.saturating_add(1_000_000).min(root_end);
    let requested_start = if invocation.timestamp.is_empty() {
        root_start
    } else {
        iso_to_nanos(&invocation.timestamp)
            .parse::<u128>()
            .unwrap_or(root_start)
    };
    let start = requested_start.clamp(minimum_child_start, root_end);
    let requested_end = if turn.completion_timestamp.is_empty() {
        root_end
    } else {
        iso_to_nanos(&turn.completion_timestamp)
            .parse::<u128>()
            .unwrap_or(root_end)
    };
    let end = requested_end.clamp(start, root_end);
    let duration_ms = end.saturating_sub(start) / 1_000_000;
    let call_id = skill_invocation_call_id(turn, invocation, skill_name);

    let arguments = json!({
        "skill": skill_name,
        "invocation": invocation.raw_placeholder,
        "source": invocation_source_name(invocation.source),
        "byte_range": match (invocation.byte_start, invocation.byte_end) {
            (Some(start), Some(end)) => json!({"start":start,"end":end}),
            _ => Value::Null,
        }
    });
    let result = skill_invocation_result(turn);
    let mut truncation_input_meta = json!(null);
    let mut truncation_output_meta = json!(null);
    let (input_text, output_text) = if privacy_mode {
        ("[REDACTED]".to_string(), "[REDACTED]".to_string())
    } else {
        let (truncated_input, input_meta) =
            truncate_value_observed(&arguments, "agentic.skill.input");
        let (truncated_output, output_meta) =
            truncate_value_observed(&result, "agentic.skill.output");
        truncation_input_meta = input_meta;
        truncation_output_meta = output_meta;
        (
            render_json_value(&truncated_input),
            render_json_value(&truncated_output),
        )
    };

    let span_name = format!("skill {}", skill_name);
    let info = agentic_otel_common::AgenticToolInfo {
        tool_type: "skill",
        name: skill_name.to_string(),
        id: call_id.clone(),
        raw_name: "Skill".to_string(),
    };
    let mut attributes = vec![
        OtelAttribute::string("gen_ai.span.kind", "TOOL"),
        OtelAttribute::string("gen_ai.operation.name", "execute_tool"),
        OtelAttribute::string("gen_ai.conversation.id", session_id),
        OtelAttribute::string("gen_ai.chat.id", chat_id),
        OtelAttribute::string("gen_ai.tool.call.arguments", &input_text),
        OtelAttribute::string("gen_ai.tool.call.result", &output_text),
        OtelAttribute::string("gen_ai.tool.name", &span_name),
        OtelAttribute::string("gen_ai.tool.call.id", &call_id),
        OtelAttribute::string("ant.username", username),
        OtelAttribute::string("user.id", username),
        OtelAttribute::int_as_string("agentic.tool.duration_ms", duration_ms as i64),
    ];
    append_agentic_tool_attrs_typed(&mut attributes, &info);
    append_truncation_meta(
        &mut attributes,
        &truncation_input_meta,
        &truncation_output_meta,
    );

    let status = match turn.terminal_state {
        TurnTerminalState::Completed | TurnTerminalState::HeuristicComplete => OtelStatus::ok(),
        TurnTerminalState::Failed => {
            attributes.push(OtelAttribute::string("error.type", "skill_failed"));
            OtelStatus::error("skill invocation failed")
        }
        TurnTerminalState::Aborted => {
            attributes.push(OtelAttribute::string("error.type", "skill_aborted"));
            OtelStatus::error("skill invocation aborted")
        }
        TurnTerminalState::Superseded => OtelStatus::unset(),
        TurnTerminalState::Open => OtelStatus::unset(),
    };

    OtelSpan {
        trace_id: trace_id.to_string(),
        span_id: deterministic_tool_span_id(&call_id),
        parent_span_id: parent_span_id.to_string(),
        name: span_name,
        kind: SPAN_KIND_INTERNAL,
        start_time_unix_nano: start.to_string(),
        end_time_unix_nano: end.to_string(),
        attributes,
        status,
    }
}

fn matched_native_skill_calls(turn: &Turn) -> (HashSet<usize>, HashSet<usize>) {
    let mut matched_invocations = HashSet::new();
    let mut consumed_native = HashSet::new();
    for (invocation_index, invocation) in turn.skill_invocations.iter().enumerate() {
        let Some(invocation_name) = invocation_skill_name(invocation) else {
            continue;
        };
        let invocation_key = skill_match_key(&invocation_name);
        let invocation_start = if invocation.timestamp.is_empty() {
            0
        } else {
            iso_to_nanos(&invocation.timestamp)
                .parse::<u128>()
                .unwrap_or(0)
        };
        if let Some((index, _)) = turn.tool_calls.iter().enumerate().find(|(index, tool)| {
            if consumed_native.contains(index) || tool.name != "Skill" {
                return false;
            }
            let Some(native_name) = extract_skill_name_from_tool_call(tool) else {
                return false;
            };
            if skill_match_key(&native_name) != invocation_key {
                return false;
            }
            tool.started_timestamp.is_empty()
                || invocation_start == 0
                || iso_to_nanos(&tool.started_timestamp)
                    .parse::<u128>()
                    .unwrap_or(0)
                    >= invocation_start
        }) {
            matched_invocations.insert(invocation_index);
            consumed_native.insert(index);
        }
    }
    (matched_invocations, consumed_native)
}

fn extract_skill_name_from_tool_call(tool_call: &ToolCall) -> Option<String> {
    if tool_call.name == "Skill" {
        return extract_skill_name_from_tool_input(&tool_call.input)
            .or_else(|| extract_skill_name_field(&tool_call.input));
    }

    if !is_shell_tool(&tool_call.name) {
        return None;
    }

    let command = tool_call
        .input
        .get("command")
        .or_else(|| tool_call.input.get("cmd"))
        .or_else(|| tool_call.input.get("chars"))
        .and_then(|v| v.as_str())?;

    extract_skill_name_from_command_text(command)
}

fn has_physical_skill_read(turn: &Turn, skill_name: &str) -> bool {
    let key = skill_match_key(skill_name);
    turn.tool_calls
        .iter()
        .filter_map(extract_skill_read_name)
        .any(|name| skill_match_key(&name) == key)
}

fn suppresses_command_path(turn: &Turn, invocation: &SkillInvocation, name: &str) -> bool {
    match invocation.source {
        SkillInvocationSource::LegacyMarkdown => false,
        SkillInvocationSource::CommandPathFallback => !has_physical_skill_read(turn, name),
        _ => true,
    }
}

fn extract_skill_name_field(input: &Value) -> Option<String> {
    input
        .get("skill_name")
        .or_else(|| input.get("skillName"))
        .and_then(|v| v.as_str())
        .filter(|name| !name.is_empty())
        .map(ToString::to_string)
}

// --- Subagent span builder ---

/// Build a SUBAGENT span from a CollabAgentSpawnEnd event.
/// These spans represent child agent lifecycles and carry correlation attributes
/// for cross-trace linking between parent and child sessions.
fn build_subagent_span(
    trace_id: &str,
    parent_span_id: &str,
    event: &crate::common::transcript::CollabAgentEvent,
    now_ns: &str,
    username: &str,
    session_id: &str,
    chat_id: &str,
    span_id_override: Option<&str>,
) -> Option<OtelSpan> {
    use crate::common::transcript::CollabAgentEvent;
    match event {
        CollabAgentEvent::SpawnEnd {
            call_id,
            sender_thread_id,
            new_thread_id,
            nickname,
            role,
            prompt,
            model,
            status,
        } => {
            let span_id = span_id_override
                .map(String::from)
                .unwrap_or_else(otel_span_id);
            let span_name = if let Some(ref nick) = nickname {
                format!("SUBAGENT {}", nick)
            } else {
                "SUBAGENT".to_string()
            };

            let mut attributes = vec![
                OtelAttribute::string("gen_ai.span.kind", "INTERNAL"),
                OtelAttribute::string("gen_ai.operation.name", "subagent_spawn"),
                OtelAttribute::string("gen_ai.conversation.id", session_id),
                OtelAttribute::string("gen_ai.chat.id", chat_id),
                OtelAttribute::string("agentic.subagent.type", "spawn"),
                OtelAttribute::string("agentic.subagent.call_id", call_id),
                OtelAttribute::string("agentic.subagent.sender_thread_id", sender_thread_id),
                OtelAttribute::string("ant.username", username),
                OtelAttribute::string("user.id", username),
            ];

            if let Some(ref tid) = new_thread_id {
                attributes.push(OtelAttribute::string(
                    "agentic.subagent.child_thread_id",
                    tid,
                ));
            }
            if let Some(ref nick) = nickname {
                attributes.push(OtelAttribute::string("agentic.subagent.nickname", nick));
            }
            if let Some(ref r) = role {
                attributes.push(OtelAttribute::string("agentic.subagent.role", r));
            }
            if !prompt.is_empty() {
                attributes.push(OtelAttribute::string(
                    "agentic.subagent.prompt",
                    if is_privacy_mode() {
                        "[REDACTED]"
                    } else {
                        prompt
                    },
                ));
            }
            if !model.is_empty() {
                attributes.push(OtelAttribute::string("agentic.subagent.model", model));
            }
            attributes.push(OtelAttribute::string("agentic.subagent.status", status));

            let sub_status = if status == "completed" {
                OtelStatus::ok()
            } else {
                OtelStatus::error(format!("subagent status: {}", status))
            };

            Some(OtelSpan {
                trace_id: trace_id.to_string(),
                span_id,
                parent_span_id: parent_span_id.to_string(),
                name: span_name,
                kind: SPAN_KIND_INTERNAL,
                start_time_unix_nano: now_ns.to_string(),
                end_time_unix_nano: now_ns.to_string(),
                attributes,
                status: sub_status,
            })
        }
        _ => None,
    }
}

// --- Compaction span builder (Rule 14) ---

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
    let raw_end_ns = if !compaction.end_timestamp.is_empty() {
        iso_to_nanos(&compaction.end_timestamp)
    } else {
        now_ns.to_string()
    };
    let start_value = start_ns.parse::<u128>().unwrap_or(0);
    let end_value = raw_end_ns
        .parse::<u128>()
        .unwrap_or(start_value)
        .max(start_value);
    let end_ns = end_value.to_string();
    let duration_ms = end_value.saturating_sub(start_value) / 1_000_000;
    let trigger = "unknown";
    let tokens_saved = compaction
        .pre_tokens
        .zip(compaction.post_tokens)
        .map(|(before, after)| before.saturating_sub(after));
    let ratio = compaction
        .pre_tokens
        .filter(|before| *before > 0)
        .zip(compaction.post_tokens)
        .map(|(before, after)| format!("{:.2}", after as f64 / before as f64));

    let status = match (compaction.pre_tokens, compaction.post_tokens) {
        (Some(before), Some(after)) if after >= before => {
            OtelStatus::error("compaction did not reduce tokens")
        }
        _ => OtelStatus::ok(),
    };
    let mut arguments = serde_json::Map::new();
    arguments.insert("trigger".to_string(), Value::String(trigger.to_string()));
    if let Some(before) = compaction.pre_tokens {
        arguments.insert("pre_tokens".to_string(), Value::from(before));
    }
    let mut result = serde_json::Map::new();
    if let Some(after) = compaction.post_tokens {
        result.insert("post_tokens".to_string(), Value::from(after));
    }
    if let Some(saved) = tokens_saved {
        result.insert("tokens_saved".to_string(), Value::from(saved));
    }
    if let Some(ratio) = &ratio {
        if let Ok(value) = ratio.parse::<f64>() {
            result.insert("ratio".to_string(), Value::from(value));
        }
    }

    let mut attributes = vec![
        OtelAttribute::string("gen_ai.span.kind", "COMPACTION"),
        OtelAttribute::string("gen_ai.operation.name", "execute_tool"),
        OtelAttribute::string("gen_ai.conversation.id", session_id),
        OtelAttribute::string("gen_ai.chat.id", chat_id),
        OtelAttribute::string("gen_ai.tool.name", "compaction"),
        OtelAttribute::string(
            "gen_ai.tool.call.id",
            &format!("compact-{}", compaction.sequence),
        ),
        OtelAttribute::string(
            "gen_ai.tool.call.arguments",
            if is_privacy_mode() {
                "[REDACTED]".to_string()
            } else {
                Value::Object(arguments).to_string()
            },
        ),
        OtelAttribute::string(
            "gen_ai.tool.call.result",
            if is_privacy_mode() {
                "[REDACTED]".to_string()
            } else {
                Value::Object(result).to_string()
            },
        ),
        OtelAttribute::string("agentic.tool.type", "system"),
        OtelAttribute::int_as_string("agentic.tool.duration_ms", duration_ms as i64),
        OtelAttribute::string("agentic.compaction.trigger", trigger),
        OtelAttribute::int_as_string("agentic.compaction.sequence", compaction.sequence as i64),
        OtelAttribute::string("ant.username", username),
        OtelAttribute::string("user.id", username),
    ];
    if let Some(before) = compaction.pre_tokens {
        attributes.push(OtelAttribute::int_as_string(
            "agentic.compaction.tokens.before",
            before as i64,
        ));
    }
    if let Some(after) = compaction.post_tokens {
        attributes.push(OtelAttribute::int_as_string(
            "agentic.compaction.tokens.after",
            after as i64,
        ));
    }
    if let Some(saved) = tokens_saved {
        attributes.push(OtelAttribute::int_as_string(
            "agentic.compaction.tokens.saved",
            saved as i64,
        ));
    }
    if let Some(ratio) = &ratio {
        attributes.push(OtelAttribute::string("agentic.compaction.ratio", ratio));
    }

    OtelSpan {
        trace_id: trace_id.to_string(),
        span_id: otel_span_id(),
        parent_span_id: parent_span_id.to_string(),
        name: "compaction".to_string(),
        kind: SPAN_KIND_INTERNAL,
        start_time_unix_nano: start_ns,
        end_time_unix_nano: end_ns,
        attributes,
        status,
    }
}

fn render_json_value(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => serde_json::to_string(other).unwrap_or_default(),
    }
}

const MAX_CDXHOOKS_OTEL_ATTRIBUTES: usize = 64;
const MAX_CDXHOOKS_OTEL_ATTRIBUTE_KEY_BYTES: usize = 256;
const MAX_CDXHOOKS_OTEL_ATTRIBUTE_VALUE_BYTES: usize = 4096;

fn cdxhooks_otel_span_attr_pairs() -> Vec<(String, String)> {
    agentic_otel_common::otlp::parse_otel_span_attributes("CDXHOOKS_OTEL_ATTRIBUTES")
        .into_iter()
        // Logical-root ownership and per-turn billing are assigned separately.
        .filter(|(key, _)| {
            key != INTERNAL_META_ROOT_KEY
                && key != "aivision.billing.mode"
                && key != "aivision.billing.estimate_list_price"
                && key.len() <= MAX_CDXHOOKS_OTEL_ATTRIBUTE_KEY_BYTES
        })
        .take(MAX_CDXHOOKS_OTEL_ATTRIBUTES)
        .map(|(key, mut value)| {
            if value.len() > MAX_CDXHOOKS_OTEL_ATTRIBUTE_VALUE_BYTES {
                let mut end = MAX_CDXHOOKS_OTEL_ATTRIBUTE_VALUE_BYTES;
                while !value.is_char_boundary(end) {
                    end -= 1;
                }
                value.truncate(end);
            }
            (key, value)
        })
        .collect()
}

fn apply_cdxhooks_otel_span_attrs(spans: &mut [OtelSpan]) {
    let extra_attrs = cdxhooks_otel_span_attr_pairs();
    if extra_attrs.is_empty() {
        return;
    }
    for span in spans {
        append_otel_span_attributes(&mut span.attributes, &extra_attrs);
    }
}

fn is_synthetic_skill_invocation_span(span: &OtelSpan) -> bool {
    span.attributes.iter().any(|attribute| {
        if attribute.key != "gen_ai.tool.call.id" {
            return false;
        }
        match &attribute.value {
            agentic_otel_common::otlp::OtelAnyValue::String { string_value } => {
                string_value.starts_with("skill-invocation:")
            }
            _ => false,
        }
    })
}

pub fn wrap_realtime_child_spans(
    mut spans: Vec<OtelSpan>,
    session_id: &str,
    build_version: &str,
) -> Value {
    annotate_trace_tags(&mut spans, session_id);
    apply_cdxhooks_otel_span_attrs(&mut spans);
    for span in &mut spans {
        enforce_span_budget_observed(span);
    }
    cdxhooks_wrap_export_request(spans, build_version)
}

/// Remove only the exact child span identities already accounted by realtime delivery.
///
/// The caller owns the suppression set and must never include the root span ID. Traversing
/// every scope keeps this safe for merged subagent resources while avoiding assumptions about
/// root `parentSpanId` values when an upstream trace context exists.
pub fn suppress_child_spans_by_id(body: &mut Value, accounted_span_ids: &HashSet<String>) -> usize {
    if accounted_span_ids.is_empty() {
        return 0;
    }

    let mut removed = 0;
    let Some(resource_spans) = body.get_mut("resourceSpans").and_then(Value::as_array_mut) else {
        return 0;
    };
    for resource in resource_spans {
        let Some(scope_spans) = resource.get_mut("scopeSpans").and_then(Value::as_array_mut) else {
            continue;
        };
        for scope in scope_spans {
            let Some(spans) = scope.get_mut("spans").and_then(Value::as_array_mut) else {
                continue;
            };
            let before = spans.len();
            spans.retain(|span| {
                span.get("spanId")
                    .and_then(Value::as_str)
                    .is_none_or(|span_id| !accounted_span_ids.contains(span_id))
            });
            removed += before.saturating_sub(spans.len());
        }
    }
    removed
}

#[allow(clippy::too_many_arguments)]
pub fn build_realtime_tool_span_for_call(
    turn: &Turn,
    tool_call_id: &str,
    trace_id: &str,
    root_span_id: &str,
    username: &str,
    session_id: &str,
) -> Option<OtelSpan> {
    let tool_call = turn
        .tool_calls
        .iter()
        .find(|tool| tool.call_id == tool_call_id)?;
    let chat_id = resolve_chat_id(turn, trace_id);
    let now_ns = if !turn.user_timestamp.is_empty() {
        iso_to_nanos(&turn.user_timestamp)
    } else {
        now_nanos()
    };

    let mut tools_by_assistant: HashMap<usize, Vec<&ToolCall>> = HashMap::new();
    for tool in &turn.tool_calls {
        tools_by_assistant
            .entry(tool.assistant_index)
            .or_default()
            .push(tool);
    }
    let parallel_group = tools_by_assistant
        .get(&tool_call.assistant_index)
        .filter(|tools| tools.len() > 1)
        .map(|_| format!("realtime-parallel-{}", tool_call.assistant_index));

    let mut suppressed_command_path_skills = HashSet::new();
    for invocation in &turn.skill_invocations {
        if let Some(name) = invocation_skill_name(invocation) {
            if suppresses_command_path(turn, invocation, &name) {
                suppressed_command_path_skills.insert(skill_match_key(&name));
            }
        }
    }
    for tool in &turn.tool_calls {
        if tool.name == "Skill" {
            if let Some(name) = extract_skill_name_from_tool_call(tool) {
                suppressed_command_path_skills.insert(skill_match_key(&name));
            }
        }
    }

    Some(build_tool_span_with_suppression(
        trace_id,
        root_span_id,
        tool_call,
        turn,
        &now_ns,
        username,
        session_id,
        &chat_id,
        parallel_group.as_deref(),
        &suppressed_command_path_skills,
    ))
}

#[allow(clippy::too_many_arguments)]
pub fn build_realtime_generation_spans_for_batch(
    turn: &Turn,
    selections: &[(usize, String)],
    trace_id: &str,
    root_span_id: &str,
    username: &str,
    session_id: &str,
    billing_mode: Option<&str>,
) -> Vec<OtelSpan> {
    let chat_id = resolve_chat_id(turn, trace_id);
    let now_ns = if !turn.user_timestamp.is_empty() {
        iso_to_nanos(&turn.user_timestamp)
    } else {
        now_nanos()
    };
    let mut spans = Vec::new();
    for (assistant_index, span_id) in selections {
        let Some(assistant) = turn.assistants.get(*assistant_index) else {
            continue;
        };
        if !turn.has_semantic_assistant_at(*assistant_index) {
            continue;
        }
        let gen_start_ns = compute_generation_start_ns(turn, *assistant_index, &now_ns);
        let gen_end_ns = compute_generation_end_ns(turn, *assistant_index, &gen_start_ns, &now_ns);
        let (_, mut span) = build_generation_span_with_id(
            trace_id,
            root_span_id,
            turn,
            assistant,
            *assistant_index,
            &gen_start_ns,
            &gen_end_ns,
            username,
            session_id,
            &chat_id,
            Some(span_id),
        );
        append_billing_mode(&mut span, billing_mode);
        spans.push(span);
    }
    spans
}

fn append_billing_mode(span: &mut OtelSpan, billing_mode: Option<&str>) {
    if let Some(mode @ ("subscription" | "pay_as_you_go")) = billing_mode {
        span.attributes
            .push(OtelAttribute::string("aivision.billing.mode", mode));
    }
}

pub fn build_otel_export_request(
    turns: &[Turn],
    session_id: &str,
    username: &str,
    _config: &LangfuseConfig,
    _now: &str,
    build_version: &str,
    prev_turn_count: usize,
) -> Value {
    let Some(turn) = turns.last() else {
        return json!({"resourceSpans": []});
    };
    let turn_number = prev_turn_count + turns.len();
    build_otel_export_request_for_turn(turn, turn_number, session_id, username, build_version, None)
}

/// Build an OTel ExportTraceServiceRequest for a single turn (replay mode).
/// Unlike `build_otel_export_request`, this does not require a LangfuseConfig.
pub fn build_otel_export_request_for_turn(
    turn: &Turn,
    turn_number: usize,
    session_id: &str,
    username: &str,
    build_version: &str,
    opts: Option<&TurnReplayOpts>,
) -> Value {
    let trace_id = opts
        .and_then(|o| o.trace_id.as_deref())
        .map(String::from)
        .unwrap_or_else(otel_trace_id);
    let chat_id = resolve_chat_id(turn, &trace_id);
    let root_span_id = opts
        .and_then(|options| options.root_span_id.as_deref())
        .map(String::from)
        .unwrap_or_else(otel_span_id);
    let now_ns = if !turn.user_timestamp.is_empty() {
        iso_to_nanos(&turn.user_timestamp)
    } else {
        now_nanos()
    };

    // Compute root span end: latest timestamp among assistants, tools, or user_timestamp
    let root_end_ns = compute_turn_end_ns(turn, &now_ns);

    let mut root_span = build_root_span(
        &trace_id,
        &root_span_id,
        turn,
        session_id,
        &chat_id,
        username,
        &now_ns,
        turn_number,
        opts.and_then(|o| o.parent_span_id.as_deref()),
    );
    append_sofa_trace_attrs(
        &mut root_span,
        opts.and_then(|opts| opts.sofa_traceid.as_deref()),
        opts.and_then(|opts| opts.sofa_rpcid.as_deref()),
    );
    // Override root span end time with computed value
    root_span.end_time_unix_nano = root_end_ns.clone();
    let mut spans: Vec<OtelSpan> = vec![root_span];

    // Generation spans: one per normalized LLM invocation.
    //
    // Skip assistants that are neither text-bearing nor usage-bearing — these
    // are structural placeholders, not real LLM calls. Tool-only LLM calls
    // (empty text but has usage from token_count) DO get their own span.
    let mut _gen_span_ids: Vec<String> = Vec::new();
    for (assistant_index, assistant) in turn.assistants.iter().enumerate() {
        if !turn.has_semantic_assistant_at(assistant_index) {
            continue;
        }
        let generation_span_id = opts.and_then(|options| {
            options
                .generation_span_ids
                .get(&assistant_index)
                .map(String::as_str)
        });
        let gen_start_ns = compute_generation_start_ns(turn, assistant_index, &now_ns);
        let gen_end_ns = compute_generation_end_ns(turn, assistant_index, &gen_start_ns, &now_ns);
        let (gen_span_id, mut generation_span) = build_generation_span_with_id(
            &trace_id,
            &root_span_id,
            turn,
            assistant,
            assistant_index,
            &gen_start_ns,
            &gen_end_ns,
            username,
            session_id,
            &chat_id,
            generation_span_id,
        );
        append_billing_mode(
            &mut generation_span,
            opts.and_then(|opts| opts.billing_mode.as_deref()),
        );
        _gen_span_ids.push(gen_span_id);
        spans.push(generation_span);
    }

    if let Some(error_span) = build_synthetic_llm_error_span(
        &trace_id,
        &root_span_id,
        turn,
        username,
        session_id,
        &chat_id,
        &now_ns,
    ) {
        spans.push(error_span);
    }

    let (matched_invocations, _) = matched_native_skill_calls(turn);
    for (invocation_index, invocation) in turn.skill_invocations.iter().enumerate() {
        if matched_invocations.contains(&invocation_index) {
            continue;
        }
        let Some(skill_name) = invocation_skill_name(invocation) else {
            continue;
        };
        if invocation.source == SkillInvocationSource::CommandPathFallback
            && has_physical_skill_read(turn, &skill_name)
        {
            continue;
        }
        spans.push(build_skill_invocation_span(
            &trace_id,
            &root_span_id,
            turn,
            invocation,
            &skill_name,
            &now_ns,
            &root_end_ns,
            username,
            session_id,
            &chat_id,
            is_privacy_mode(),
        ));
    }

    // Tool spans: siblings of generation spans (children of root), per Rule 3
    // Group tool calls by assistant_index to detect parallel batches (Rule 13)
    let mut tools_by_assistant: std::collections::HashMap<usize, Vec<&ToolCall>> =
        std::collections::HashMap::new();
    for tool_call in &turn.tool_calls {
        tools_by_assistant
            .entry(tool_call.assistant_index)
            .or_default()
            .push(tool_call);
    }
    let mut parallel_groups: std::collections::HashMap<usize, String> =
        std::collections::HashMap::new();
    for (idx, tools) in &tools_by_assistant {
        if tools.len() > 1 {
            parallel_groups.insert(*idx, otel_span_id());
        }
    }

    let mut suppressed_command_path_skills = HashSet::new();
    for invocation in &turn.skill_invocations {
        if let Some(name) = invocation_skill_name(invocation) {
            if suppresses_command_path(turn, invocation, &name) {
                suppressed_command_path_skills.insert(skill_match_key(&name));
            }
        }
    }
    for tool_call in &turn.tool_calls {
        if tool_call.name == "Skill" {
            if let Some(name) = extract_skill_name_from_tool_call(tool_call) {
                suppressed_command_path_skills.insert(skill_match_key(&name));
            }
        }
    }

    for tool_call in &turn.tool_calls {
        let pg = parallel_groups
            .get(&tool_call.assistant_index)
            .map(|s| s.as_str());
        spans.push(build_tool_span_with_suppression(
            &trace_id,
            &root_span_id,
            tool_call,
            turn,
            &now_ns,
            username,
            session_id,
            &chat_id,
            pg,
            &suppressed_command_path_skills,
        ));
    }

    // Rule 14: Compaction spans
    for compaction in &turn.compactions {
        spans.push(build_compaction_span(
            &trace_id,
            &root_span_id,
            compaction,
            &now_ns,
            username,
            session_id,
            &chat_id,
        ));
    }

    // Subagent spans from CollabAgent events
    for event in &turn.collab_agent_events {
        if let Some(span) = build_subagent_span(
            &trace_id,
            &root_span_id,
            event,
            &now_ns,
            username,
            session_id,
            &chat_id,
            opts.and_then(|o| {
                if let crate::common::transcript::CollabAgentEvent::SpawnEnd {
                    new_thread_id: Some(ref tid),
                    ..
                } = event
                {
                    o.subagent_span_ids.get(tid).map(String::as_str)
                } else {
                    None
                }
            }),
        ) {
            spans.push(span);
        }
    }

    annotate_trace_tags(&mut spans, session_id);

    if spans.len() > 1 {
        let root_start = spans[0].start_time_unix_nano.parse::<u128>().unwrap_or(0);
        let root_end = spans[0]
            .end_time_unix_nano
            .parse::<u128>()
            .unwrap_or(root_start);
        let child_min_start = root_start.saturating_add(1_000_000).min(root_end);
        for span in &mut spans[1..] {
            if is_synthetic_skill_invocation_span(span) {
                continue;
            }
            let child_start = span.start_time_unix_nano.parse::<u128>().unwrap_or(0);
            if child_start <= root_start {
                span.start_time_unix_nano = child_min_start.to_string();
                let child_end = span
                    .end_time_unix_nano
                    .parse::<u128>()
                    .unwrap_or(child_min_start);
                if child_end < child_min_start {
                    span.end_time_unix_nano = child_min_start.to_string();
                }
            }
        }
    }

    apply_cdxhooks_otel_span_attrs(&mut spans);

    for span in &mut spans {
        enforce_span_budget_observed(span);
    }

    cdxhooks_wrap_export_request(spans, build_version)
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

pub fn post_otel_to_langfuse(
    config: &LangfuseConfig,
    body: &Value,
    logger: &crate::common::logger::Logger,
) -> Result<(), String> {
    post_otel_to_langfuse_with_policy(
        config,
        body,
        logger,
        std::time::Duration::from_secs(HTTP_TIMEOUT_SECS),
        true,
        true,
    )
}

pub fn post_otel_to_langfuse_once(
    config: &LangfuseConfig,
    body: &Value,
    logger: &crate::common::logger::Logger,
    timeout: std::time::Duration,
) -> Result<(), String> {
    post_otel_to_langfuse_with_policy(config, body, logger, timeout, false, false)
}

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

pub fn post_otel_to_langfuse_once_classified(
    config: &LangfuseConfig,
    body: &Value,
    logger: &crate::common::logger::Logger,
    timeout: std::time::Duration,
) -> Result<(), OtelPostError> {
    let build_started = std::time::Instant::now();
    validate_export_body_span_budget(body).map_err(OtelPostError::Preparation)?;
    let url = crate::common::env::var("CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        .ok()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| resolve_otel_url(&config.host, "/api/public/otel/v1/traces"));
    let body_str = serde_json::to_string(body)
        .map_err(|error| OtelPostError::Preparation(error.to_string()))?;
    log_remote_phase(
        logger,
        "build",
        build_started.elapsed(),
        body_str.len(),
        None,
        "ok",
    );
    logger.debug(&format!("POST {} body_len={}", url, body_str.len()));
    append_trace_jsonl_if_debug(body, logger);
    let result = send_otel_request(config, &url, &body_str, logger, timeout, false);
    if result.is_ok() {
        // Best-effort companion export: emit codex.* event logs so the AI Vision
        // data dashboard (which reads signoz_logs) has data. Never fails the trace.
        if let Err(err) = post_codex_event_logs(config, body, logger, timeout) {
            logger.debug(&format!("codex event logs export skipped: {err}"));
        }
    }
    result
}

/// Read a span attribute as string, tolerating stringValue / intValue encodings.
fn span_attr_str(span: &Value, key: &str) -> Option<String> {
    let attrs = span.get("attributes")?.as_array()?;
    let attr = attrs
        .iter()
        .find(|a| a.get("key").and_then(|k| k.as_str()) == Some(key))?;
    let value = attr.get("value")?;
    if let Some(s) = value.get("stringValue").and_then(|v| v.as_str()) {
        return Some(s.to_string());
    }
    value.get("intValue").map(|v| match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    })
}

fn span_ns(span: &Value, key: &str) -> u128 {
    span.get(key)
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse::<u128>().ok())
        .unwrap_or(0)
}

fn attr_string(key: &str, value: &str) -> Value {
    json!({ "key": key, "value": { "stringValue": value } })
}

fn attr_int(key: &str, value: i64) -> Value {
    json!({ "key": key, "value": { "intValue": value.to_string() } })
}

/// OTLP span status code: 0 = unset, 1 = OK, 2 = ERROR. Anything but an
/// explicit ERROR counts as success so legacy spans without a status stay green.
fn span_status_code(span: &Value) -> i64 {
    span.get("status")
        .and_then(|status| status.get("code"))
        .and_then(Value::as_i64)
        .unwrap_or(0)
}

fn span_succeeded(span: &Value) -> bool {
    span_status_code(span) != 2
}

/// Tool display name for the dashboard: prefer the raw tool name, else strip the
/// kind prefix that `tool_span_name` adds ("tool Bash" -> "Bash").
fn span_tool_name(span: &Value) -> String {
    if let Some(raw) = span_attr_str(span, "agentic.tool.raw_name") {
        if !raw.trim().is_empty() {
            return raw;
        }
    }
    let full = span_attr_str(span, "gen_ai.tool.name").unwrap_or_default();
    for prefix in ["subagent ", "skill ", "mcp ", "tool "] {
        if let Some(rest) = full.strip_prefix(prefix) {
            if !rest.is_empty() {
                return rest.to_string();
            }
        }
    }
    full
}

/// Pull the user's prose out of a serialized `gen_ai.input.messages` array so the
/// prompt tile renders text instead of raw JSON. `None` means "not parseable as a
/// message array" and the caller should fall back to the raw attribute.
fn last_user_message_text(raw: &str) -> Option<String> {
    let parsed: Value = serde_json::from_str(raw).ok()?;
    let messages = parsed.as_array()?;
    messages.iter().rev().find_map(|message| {
        if message.get("role").and_then(Value::as_str) != Some("user") {
            return None;
        }
        match message.get("content") {
            Some(Value::String(text)) if !text.trim().is_empty() => Some(text.clone()),
            Some(Value::Array(parts)) => {
                let text: String = parts
                    .iter()
                    .filter_map(|part| part.get("text").and_then(Value::as_str))
                    .collect::<Vec<_>>()
                    .join("\n");
                (!text.trim().is_empty()).then_some(text)
            }
            _ => None,
        }
    })
}

/// Resolve the OTLP logs endpoint: explicit env override, else the traces
/// endpoint with its /v1/traces suffix swapped for /v1/logs.
fn resolve_logs_url(traces_url: &str, config: &LangfuseConfig) -> String {
    if let Ok(custom) = crate::common::env::var("CDXHOOKS_OTEL_EXPORTER_OTLP_LOGS_ENDPOINT") {
        if !custom.is_empty() {
            return custom;
        }
    }
    if let Some(base) = traces_url.strip_suffix("/v1/traces") {
        return format!("{base}/v1/logs");
    }
    resolve_otel_url(&config.host, "/v1/logs")
}

/// Derive codex.* event log records from an exported trace body and POST them
/// to the OTLP logs endpoint using the same auth headers as traces.
fn post_codex_event_logs(
    config: &LangfuseConfig,
    trace_body: &Value,
    logger: &crate::common::logger::Logger,
    timeout: std::time::Duration,
) -> Result<(), OtelPostError> {
    let mut log_records: Vec<Value> = Vec::new();
    let resource_attrs = trace_body
        .pointer("/resourceSpans/0/resource/attributes")
        .cloned()
        .unwrap_or_else(|| json!([]));
    let spans = trace_body
        .pointer("/resourceSpans/0/scopeSpans/0/spans")
        .and_then(|s| s.as_array())
        .cloned()
        .unwrap_or_default();
    // LLM spans are instantaneous markers; the enclosing CHAT root carries the
    // real turn timing, so fall back to the widest span duration.
    let root_duration_ms = spans
        .iter()
        .map(|s| span_ns(s, "endTimeUnixNano").saturating_sub(span_ns(s, "startTimeUnixNano")) / 1_000_000)
        .max()
        .unwrap_or(0);

    for span in spans {
        let kind = span_attr_str(&span, "gen_ai.span.kind").unwrap_or_default();
        let trace_id = span.get("traceId").and_then(|v| v.as_str()).unwrap_or("");
        let span_id = span.get("spanId").and_then(|v| v.as_str()).unwrap_or("");
        let start_ns = span_ns(&span, "startTimeUnixNano");
        let end_ns = span_ns(&span, "endTimeUnixNano");
        let duration_ms = end_ns.saturating_sub(start_ns) / 1_000_000;
        let time_ns = if end_ns > 0 { end_ns } else { start_ns };
        let conversation_id = span_attr_str(&span, "gen_ai.conversation.id").unwrap_or_default();

        if kind == "LLM" {
            let model = span_attr_str(&span, "gen_ai.request.model").unwrap_or_default();
            let input_tokens = span_attr_str(&span, "gen_ai.usage.input_tokens")
                .and_then(|s| s.parse::<i64>().ok())
                .unwrap_or(0);
            let output_tokens = span_attr_str(&span, "gen_ai.usage.output_tokens")
                .and_then(|s| s.parse::<i64>().ok())
                .unwrap_or(0);
            let cached = span_attr_str(&span, "gen_ai.usage.cache_read_input_tokens")
                .and_then(|s| s.parse::<i64>().ok())
                .unwrap_or(0);
            // Rule 7 dual-channel: a failed generation carries status.code=2 plus
            // error.type. The dashboard error-rate tile reads `success` and
            // `http.response.status_code`, so both must follow the span instead of
            // being pinned to a green constant.
            let succeeded = span_succeeded(&span);
            let http_status = span_attr_str(&span, "agentic.llm.error.status_code")
                .and_then(|code| code.parse::<i64>().ok())
                .unwrap_or(if succeeded { 200 } else { 500 });
            let mut attributes = vec![
                attr_string("event.name", "codex.api_request"),
                attr_string("model", &model),
                attr_int("input_token_count", input_tokens),
                attr_int("output_token_count", output_tokens),
                attr_int("cached_token_count", cached),
                attr_int(
                    "duration_ms",
                    // LLM spans are sub-100ms markers; prefer the turn wall time.
                    (if duration_ms >= 100 { duration_ms } else { root_duration_ms }) as i64,
                ),
                attr_string("success", if succeeded { "true" } else { "false" }),
                attr_int("http.response.status_code", http_status),
            ];
            if let Some(error_type) = span_attr_str(&span, "error.type").filter(|t| !t.is_empty()) {
                attributes.push(attr_string("error.type", &error_type));
            }
            if !conversation_id.is_empty() {
                attributes.push(attr_string("conversation.id", &conversation_id));
            }
            log_records.push(json!({
                "timeUnixNano": time_ns.to_string(),
                "severityText": "INFO",
                "body": { "stringValue": "codex.api_request" },
                "attributes": attributes,
                "traceId": trace_id,
                "spanId": span_id,
            }));
            // Token-timeline / model-distribution tile. Carries NO token counts:
            // dashboard token tiles sum across events, and duplicating counts
            // here would double-count against codex.api_request.
            let mut sse_attrs = vec![
                attr_string("event.name", "codex.sse_event"),
                attr_string("event.kind", "response.completed"),
                attr_string("model", &model),
            ];
            if !conversation_id.is_empty() {
                sse_attrs.push(attr_string("conversation.id", &conversation_id));
            }
            log_records.push(json!({
                "timeUnixNano": time_ns.to_string(),
                "severityText": "INFO",
                "body": { "stringValue": "codex.sse_event" },
                "attributes": sse_attrs,
                "traceId": trace_id,
                "spanId": span_id,
            }));
        } else if kind == "CHAT" {
            // `gen_ai.input.messages` holds the serialized message array; the prompt
            // tile must show the user's prose, so unwrap the last user message and
            // only fall back to the raw JSON when it is not a message array.
            let raw_messages = span_attr_str(&span, "gen_ai.input.messages").unwrap_or_default();
            let prompt = last_user_message_text(&raw_messages).unwrap_or(raw_messages);
            let trimmed: String = prompt.chars().take(2000).collect();
            let mut attributes = vec![
                attr_string("event.name", "codex.user_prompt"),
                attr_string("prompt", &trimmed),
                attr_int("prompt_length", prompt.chars().count() as i64),
                // The CHAT root spans the whole turn, so its wall time is the cost
                // the prompt detail tile reports.
                attr_int("duration_ms", duration_ms as i64),
            ];
            if !conversation_id.is_empty() {
                attributes.push(attr_string("conversation.id", &conversation_id));
            }
            log_records.push(json!({
                "timeUnixNano": time_ns.to_string(),
                "severityText": "INFO",
                "body": { "stringValue": "codex.user_prompt" },
                "attributes": attributes,
                "traceId": trace_id,
                "spanId": span_id,
            }));
        } else if kind == "TOOL" {
            // The tool tiles (summary / detail) key off codex.tool_result. Tool
            // spans are nested inside the turn, so this duration describes per-tool
            // latency only; turn-level active time stays on codex.api_request.
            let tool_name = span_tool_name(&span);
            let result_text = span_attr_str(&span, "gen_ai.tool.call.result").unwrap_or_default();
            let mut attributes = vec![
                attr_string("event.name", "codex.tool_result"),
                attr_string("tool_name", &tool_name),
                attr_int("duration_ms", duration_ms as i64),
                attr_int("tool_result_size_bytes", result_text.len() as i64),
                attr_string(
                    "success",
                    if span_succeeded(&span) { "true" } else { "false" },
                ),
            ];
            if !conversation_id.is_empty() {
                attributes.push(attr_string("conversation.id", &conversation_id));
            }
            if let Some(error_type) = span_attr_str(&span, "error.type").filter(|t| !t.is_empty()) {
                attributes.push(attr_string("error.type", &error_type));
            }
            log_records.push(json!({
                "timeUnixNano": time_ns.to_string(),
                "severityText": "INFO",
                "body": { "stringValue": "codex.tool_result" },
                "attributes": attributes,
                "traceId": trace_id,
                "spanId": span_id,
            }));
            // Tool decision trend. Codex rollouts record no per-call approval
            // event, so the only honest signal is whether the call was let through:
            // `detect_tool_error_from_output` tags a user veto as error.type
            // "cancelled", everything else reached execution. Policy
            // auto-approval and an explicit user "allow" are indistinguishable in
            // the transcript and both map to "approved".
            let decision = match span_attr_str(&span, "error.type").as_deref() {
                Some("cancelled") => "cancelled",
                _ => "approved",
            };
            let mut decision_attrs = vec![
                attr_string("event.name", "codex.tool_decision"),
                attr_string("tool_name", &tool_name),
                attr_string("decision", decision),
            ];
            if !conversation_id.is_empty() {
                decision_attrs.push(attr_string("conversation.id", &conversation_id));
            }
            log_records.push(json!({
                "timeUnixNano": time_ns.to_string(),
                "severityText": "INFO",
                "body": { "stringValue": "codex.tool_decision" },
                "attributes": decision_attrs,
                "traceId": trace_id,
                "spanId": span_id,
            }));
        }
    }

    if log_records.is_empty() {
        return Ok(());
    }

    let traces_url = crate::common::env::var("CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        .ok()
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| resolve_otel_url(&config.host, "/api/public/otel/v1/traces"));
    let logs_url = resolve_logs_url(&traces_url, config);
    let logs_body = json!({
        "resourceLogs": [{
            "resource": { "attributes": resource_attrs },
            "scopeLogs": [{
                "scope": { "name": "cdxhooks" },
                "logRecords": log_records,
            }],
        }],
    });
    let body_str = serde_json::to_string(&logs_body)
        .map_err(|e| OtelPostError::Preparation(e.to_string()))?;
    logger.debug(&format!("POST {} (event logs) body_len={}", logs_url, body_str.len()));
    send_otel_request(config, &logs_url, &body_str, logger, timeout, false)
}

fn run_with_retry_policy<F, R>(
    retry_once: bool,
    mut operation: F,
    on_retry: R,
) -> Result<(), String>
where
    F: FnMut() -> Result<(), String>,
    R: FnOnce(&str),
{
    match operation() {
        Ok(()) => Ok(()),
        Err(error) if retry_once => {
            on_retry(&error);
            operation()
        }
        Err(error) => Err(error),
    }
}

fn post_otel_to_langfuse_with_policy(
    config: &LangfuseConfig,
    body: &Value,
    logger: &crate::common::logger::Logger,
    timeout: std::time::Duration,
    retry_once: bool,
    follow_redirects: bool,
) -> Result<(), String> {
    let build_started = std::time::Instant::now();
    validate_export_body_span_budget(body)?;
    let url = crate::common::env::var("CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        .ok()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| resolve_otel_url(&config.host, "/api/public/otel/v1/traces"));
    let body_str = serde_json::to_string(body).map_err(|err| err.to_string())?;
    log_remote_phase(
        logger,
        "build",
        build_started.elapsed(),
        body_str.len(),
        None,
        "ok",
    );
    logger.debug(&format!("POST {} body_len={}", url, body_str.len()));
    append_trace_jsonl_if_debug(body, logger);
    run_with_retry_policy(
        retry_once,
        || {
            send_otel_request(config, &url, &body_str, logger, timeout, follow_redirects)
                .map_err(|error| error.to_string())
        },
        |err| {
            logger.warn(&format!(
                "Langfuse OTel HTTP request failed, retrying once: {}",
                err
            ))
        },
    )
}

fn send_otel_request(
    config: &LangfuseConfig,
    url: &str,
    body: &str,
    logger: &crate::common::logger::Logger,
    timeout: std::time::Duration,
    follow_redirects: bool,
) -> Result<(), OtelPostError> {
    let http_started = std::time::Instant::now();
    let attempt_id = agentic_otel_common::otel_span_id();
    logger.info(&format!(
        "event=langfuse_send_start attempt_id={attempt_id} operation=other timeout_ms={}",
        timeout.as_millis()
    ));
    let agent =
        crate::common::hook::build_http_agent_with_timeout_and_redirects(timeout, follow_redirects);

    let mut request = agent
        .post(url)
        .header("Content-Type", "application/json")
        .header("x-langfuse-ingestion-version", "4");

    if let (Some(pk), Some(sk)) = (&config.public_key, &config.secret_key) {
        let auth_header = build_basic_auth_header(pk, sk);
        request = request.header("Authorization", &auth_header);
    }

    if let Some(ref headers) = config.headers {
        logger.debug(&format!("Config has {} custom headers", headers.len()));
        for (key, value) in headers {
            request = request.header(key, value);
            logger.debug(&format!("Adding custom header name: {key}"));
        }
    }

    let response = request.send(body.as_bytes()).map_err(|error| {
        let error_kind = classify_langfuse_send_error(&error);
        let (classified, status, outcome) = match error {
            ureq::Error::StatusCode(status) => (
                OtelPostError::HttpStatus(status),
                Some(status),
                "http_error",
            ),
            error => (
                OtelPostError::Transport(error.to_string()),
                None,
                "transport_error",
            ),
        };
        log_remote_phase(
            logger,
            "http",
            http_started.elapsed(),
            body.len(),
            status,
            outcome,
        );
        let status_fields = status.map_or_else(String::new, |status| {
            format!(
                " http_status={status} http_status_retryable={}",
                otlp_http_status_is_retryable(status)
            )
        });
        logger.error(&format!(
            "event=langfuse_send_end attempt_id={attempt_id} operation=other outcome=failure error_kind={error_kind} elapsed_ms={}{} body_bytes={}",
            http_started.elapsed().as_millis(),
            status_fields,
            body.len()
        ));
        classified
    })?;
    let status = response.status();
    let status_code = status.as_u16();
    log_remote_phase(
        logger,
        "http",
        http_started.elapsed(),
        body.len(),
        Some(status_code),
        if (200..300).contains(&status_code) {
            "ok"
        } else {
            "http_error"
        },
    );
    if !(200..300).contains(&status_code) {
        logger.error(&format!(
            "event=langfuse_send_end attempt_id={attempt_id} operation=other outcome=failure error_kind=http_status elapsed_ms={} http_status={status_code} http_status_retryable={} body_bytes={}",
            http_started.elapsed().as_millis(),
            otlp_http_status_is_retryable(status_code),
            body.len()
        ));
        return Err(OtelPostError::HttpStatus(status_code));
    }
    logger.info(&format!(
        "event=langfuse_send_end attempt_id={attempt_id} operation=other outcome=success http_status={status_code} elapsed_ms={} body_bytes={}",
        http_started.elapsed().as_millis(),
        body.len()
    ));
    Ok(())
}

fn classify_langfuse_send_error(error: &ureq::Error) -> &'static str {
    match error {
        ureq::Error::StatusCode(_) => "http_status",
        ureq::Error::Timeout(_) => "timeout",
        ureq::Error::HostNotFound => "dns",
        ureq::Error::ConnectionFailed => "connect",
        ureq::Error::Io(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::ConnectionRefused
                    | std::io::ErrorKind::ConnectionReset
                    | std::io::ErrorKind::ConnectionAborted
                    | std::io::ErrorKind::NotConnected
            ) =>
        {
            "connect"
        }
        _ => "transport",
    }
}

fn log_remote_phase(
    logger: &crate::common::logger::Logger,
    phase: &str,
    elapsed: std::time::Duration,
    request_bytes: usize,
    status: Option<u16>,
    outcome: &str,
) {
    let elapsed_ms = elapsed.as_millis();
    if phase == "http" {
        observe_worker_http_elapsed(elapsed_ms);
    }
    logger.log_raw(
        &serde_json::json!({
            "kind": "cdxhooks.remote_timing",
            "phase": phase,
            "elapsed_ms": elapsed_ms,
            "execution": "async",
            "request_bytes": request_bytes,
            "http_status": status,
            "outcome": outcome,
        })
        .to_string(),
    );
}

fn append_trace_jsonl_if_debug(body: &Value, logger: &crate::common::logger::Logger) {
    if !env_is_truthy("CDXHOOKS_DEBUG") {
        return;
    }

    let Some(session_id) = debug_session_id_from_otel_body(body) else {
        logger.debug("Skipped OTel debug JSONL: payload has no owning session id");
        return;
    };
    let Some(path) = resolve_trace_jsonl_path(&session_id) else {
        logger.debug("Skipped OTel debug JSONL: payload session id is empty");
        return;
    };
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

    let mut options = OpenOptions::new();
    options.append(true).create(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = match options.open(&path) {
        Ok(file) => file,
        Err(err) => {
            logger.debug(&format!(
                "Failed to open OTel debug JSONL file {}: {}",
                path.display(),
                err
            ));
            return;
        }
    };

    if let Err(err) = writeln!(file, "{}", line) {
        logger.debug(&format!(
            "Failed to append OTel debug JSONL file {}: {}",
            path.display(),
            err
        ));
        return;
    }

    logger.debug(&format!("Appended OTel trace JSONL to {}", path.display()));
}

fn debug_session_id_from_otel_body(body: &Value) -> Option<String> {
    body.get("resourceSpans")?
        .as_array()?
        .iter()
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
        .flat_map(|span| {
            span.get("attributes")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .find_map(|attribute| {
            (attribute.get("key").and_then(Value::as_str) == Some("gen_ai.conversation.id"))
                .then(|| {
                    attribute
                        .get("value")
                        .and_then(|value| value.get("stringValue"))
                        .and_then(Value::as_str)
                        .map(str::trim)
                        .filter(|value| !value.is_empty())
                        .map(ToOwned::to_owned)
                })
                .flatten()
        })
}

fn resolve_trace_jsonl_path(session_id: &str) -> Option<PathBuf> {
    crate::common::dirs::session_key(session_id).map(|key| {
        crate::common::dirs::log_dir()
            .join("sessions")
            .join(key)
            .join("otel.jsonl")
    })
}

/// Annotate spans with trace-level tags: skill:xxx, mcp:xxx, and aivision custom tags.
fn annotate_trace_tags(spans: &mut Vec<OtelSpan>, session_id: &str) {
    use agentic_otel_common::otlp::OtelAnyValue;

    #[cfg(not(feature = "skill"))]
    let _ = session_id;

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
        let registry = skill_tags::state_file_path_for_session(session_id)
            .as_deref()
            .and_then(skill_tags::load_registry);
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

/// Child session data for subagent merge (shared between replay and hook paths).
#[derive(Clone)]
pub struct SubagentChild {
    pub session_id: String,
    pub turns: Vec<Turn>,
    pub nickname: String,
    pub role: String,
    pub agent_path: Option<String>,
}

fn subagent_span_name(nickname: &str, cancelled: bool) -> String {
    let nickname = nickname.trim();
    let name = if nickname.is_empty() {
        "agent SyncAgent".to_string()
    } else {
        format!("agent {nickname}")
    };
    if cancelled {
        format!("{name} (Cancelled)")
    } else {
        name
    }
}

struct SubagentSpanSpec<'a> {
    activation_tool_call_id: &'a str,
    prompt: &'a str,
    terminal_output: &'a str,
    start_ns: &'a str,
    end_ns: &'a str,
    outcome: SubagentSpanOutcome,
}

#[derive(Debug)]
struct SubagentMergeFailure {
    stage: &'static str,
    span_id: String,
}

fn prepare_reparented_subagent_descendants(
    child_spans: &[Value],
    child_root_id: &str,
    agent_span_id: &str,
    child_session_id: &str,
) -> Result<Vec<Value>, SubagentMergeFailure> {
    let mut prepared = Vec::new();
    for child_span in child_spans {
        if child_span.get("parentSpanId").and_then(Value::as_str) != Some(child_root_id) {
            continue;
        }
        let span_id = child_span
            .get("spanId")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string();
        let mut reparented =
            serde_json::from_value::<OtelSpan>(child_span.clone()).map_err(|_| {
                SubagentMergeFailure {
                    stage: "deserialize_span",
                    span_id: span_id.clone(),
                }
            })?;
        reparented.parent_span_id = agent_span_id.to_string();
        reparented
            .attributes
            .push(OtelAttribute::string("agentic.subagent", "true"));
        reparented.attributes.push(OtelAttribute::string(
            "agentic.subagent.id",
            child_session_id,
        ));
        if !enforce_span_budget_observed(&mut reparented).within_budget {
            return Err(SubagentMergeFailure {
                stage: "span_budget",
                span_id,
            });
        }
        prepared.push(
            serde_json::to_value(reparented).map_err(|_| SubagentMergeFailure {
                stage: "serialize_span",
                span_id,
            })?,
        );
    }
    Ok(prepared)
}

fn span_string_attr(span: &Value, key: &str) -> Option<String> {
    span.get("attributes")
        .and_then(Value::as_array)
        .and_then(|attrs| find_otel_attr(attrs, key))
}

fn span_int_attr(span: &Value, key: &str) -> Option<i64> {
    span_string_attr(span, key).and_then(|value| value.parse().ok())
}

fn span_has_string_attr(span: &Value, key: &str, value: &str) -> bool {
    span.get("attributes")
        .and_then(Value::as_array)
        .and_then(|attrs| find_otel_attr(attrs, key))
        .is_some_and(|attr_value| attr_value == value)
}

fn span_topology(span: &Value) -> Result<(String, String), String> {
    let trace_id = span
        .get("traceId")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "parent root span missing traceId".to_string())?;
    let span_id = span
        .get("spanId")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "parent root span missing spanId".to_string())?;
    Ok((trace_id.to_string(), span_id.to_string()))
}

/// Resolve the parent turn CHAT/root topology from an OTel export body.
///
/// The canonical root marker is authoritative because upstream TRACEPARENT makes
/// the root span's `parentSpanId` non-empty. Older replay fixtures may not carry
/// the marker, so a single empty-parent span remains a legacy fallback.
pub fn resolve_parent_root_topology(body: &Value) -> Result<(String, String), String> {
    let spans = body
        .pointer("/resourceSpans/0/scopeSpans/0/spans")
        .and_then(Value::as_array)
        .ok_or_else(|| "parent export body missing spans array".to_string())?;
    let marked = spans
        .iter()
        .filter(|span| span_has_string_attr(span, INTERNAL_META_ROOT_KEY, INTERNAL_META_ROOT_VALUE))
        .collect::<Vec<_>>();
    match marked.len() {
        1 => return span_topology(marked[0]),
        n if n > 1 => return Err(format!("parent export body has {n} root markers")),
        _ => {}
    }

    let empty_parent = spans
        .iter()
        .filter(|span| {
            span.get("parentSpanId")
                .and_then(Value::as_str)
                .is_none_or(str::is_empty)
        })
        .collect::<Vec<_>>();
    match empty_parent.len() {
        1 => span_topology(empty_parent[0]),
        0 => Err("parent export body has no root marker or legacy empty-parent root".to_string()),
        n => Err(format!(
            "parent export body has {n} legacy empty-parent root candidates"
        )),
    }
}

fn is_completed_pending_child_progress_span(span: &Value) -> bool {
    match span_string_attr(span, "gen_ai.span.kind").as_deref() {
        Some("LLM") => {
            span_string_attr(span, "gen_ai.response.id").is_some()
                || [
                    "gen_ai.usage.input_tokens",
                    "gen_ai.usage.output_tokens",
                    "gen_ai.usage.total_tokens",
                ]
                .iter()
                .any(|key| span_int_attr(span, key).is_some_and(|value| value > 0))
        }
        Some("TOOL") => span_string_attr(span, "gen_ai.tool.call.result")
            .as_deref()
            .is_some_and(|result| {
                let trimmed = result.trim();
                !trimmed.is_empty() && trimmed != "{}" && trimmed != "null"
            }),
        Some("COMPACTION") | Some("SKILL") => {
            span.get("status")
                .and_then(|status| status.get("code"))
                .and_then(Value::as_i64)
                == Some(1)
        }
        _ => false,
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum SubagentSpanOutcome {
    Completed,
    Pending,
    Interrupted,
    Error,
}

impl SubagentSpanOutcome {
    fn status(self) -> &'static str {
        match self {
            Self::Completed => "completed",
            Self::Pending => "pending",
            Self::Interrupted => "interrupted",
            Self::Error => "error",
        }
    }

    fn error_type(self) -> Option<&'static str> {
        match self {
            Self::Completed | Self::Pending => None,
            Self::Interrupted => Some("cancelled"),
            Self::Error => Some("subagent_error"),
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn append_subagent_agent_span(
    spans: &mut Value,
    trace_id: &str,
    root_span_id: &str,
    child: &SubagentChild,
    child_turns: &[&Turn],
    username: &str,
    version: &str,
    extra_span_attrs: &[(String, String)],
    spec: &SubagentSpanSpec<'_>,
) -> bool {
    if !spans.is_array() {
        return false;
    }
    let agent_span_id =
        deterministic_subagent_agent_span_id(&child.session_id, spec.activation_tool_call_id);
    let agent_chat_id = child_turns
        .last()
        .map(|turn| resolve_chat_id(turn, trace_id))
        .unwrap_or_else(|| trace_id.to_string());
    let agent_status = spec.outcome.status();
    let mut agent_attrs = vec![
        OtelAttribute::string("gen_ai.span.kind", "AGENT"),
        OtelAttribute::string("gen_ai.operation.name", "invoke_agent"),
        OtelAttribute::string("gen_ai.conversation.id", &child.session_id),
        OtelAttribute::string("gen_ai.chat.id", agent_chat_id),
        OtelAttribute::string("agentic.tool.type", "subagent"),
        OtelAttribute::string("agentic.subagent.mode", "sync"),
        OtelAttribute::string("agentic.subagent.id", &child.session_id),
        OtelAttribute::string("agentic.subagent.child_thread_id", &child.session_id),
        OtelAttribute::string(
            "agentic.subagent.parent_tool_call_id",
            spec.activation_tool_call_id,
        ),
        OtelAttribute::string("agentic.subagent.status", agent_status),
        OtelAttribute::string("ant.username", username),
        OtelAttribute::string("user.id", username),
    ];
    if let Some(error_type) = spec.outcome.error_type() {
        agent_attrs.push(OtelAttribute::string("error.type", error_type));
    }
    if !child.nickname.trim().is_empty() {
        agent_attrs.push(OtelAttribute::string(
            "agentic.subagent.nickname",
            child.nickname.trim(),
        ));
    }
    let trimmed_role = child.role.trim();
    if !trimmed_role.is_empty() {
        agent_attrs.push(OtelAttribute::string("agentic.subagent.type", trimmed_role));
        agent_attrs.push(OtelAttribute::string("agentic.subagent.role", trimmed_role));
    }
    if let Some(ref agent_path) = child.agent_path {
        agent_attrs.push(OtelAttribute::string(
            "agentic.subagent.agent_path",
            agent_path,
        ));
    }
    if !spec.prompt.is_empty() {
        let value = if is_privacy_mode() {
            "[REDACTED]"
        } else {
            spec.prompt
        };
        agent_attrs.push(OtelAttribute::string("gen_ai.tool.call.arguments", value));
    }
    if !spec.terminal_output.is_empty() {
        let value = if is_privacy_mode() {
            "[REDACTED]"
        } else {
            spec.terminal_output
        };
        agent_attrs.push(OtelAttribute::string("gen_ai.tool.call.result", value));
    }
    append_otel_span_attributes(&mut agent_attrs, extra_span_attrs);

    let mut agent_span = OtelSpan {
        trace_id: trace_id.to_string(),
        span_id: agent_span_id.clone(),
        parent_span_id: root_span_id.to_string(),
        name: subagent_span_name(
            &child.nickname,
            spec.outcome == SubagentSpanOutcome::Interrupted,
        ),
        kind: SPAN_KIND_INTERNAL,
        start_time_unix_nano: spec.start_ns.to_string(),
        end_time_unix_nano: spec.end_ns.to_string(),
        attributes: agent_attrs,
        status: match spec.outcome {
            SubagentSpanOutcome::Error => OtelStatus {
                code: 2,
                message: None,
            },
            SubagentSpanOutcome::Pending => OtelStatus::unset(),
            SubagentSpanOutcome::Completed | SubagentSpanOutcome::Interrupted => OtelStatus::ok(),
        },
    };

    let mut pending_descendants = Vec::new();
    for (turn_index, turn) in child_turns.iter().enumerate() {
        let generation_span_ids = turn
            .assistants
            .iter()
            .enumerate()
            .filter(|(assistant_index, _)| turn.has_semantic_assistant_at(*assistant_index))
            .map(|(assistant_index, assistant)| {
                (
                    assistant_index,
                    deterministic_subagent_generation_span_id(
                        &child.session_id,
                        spec.activation_tool_call_id,
                        &turn.turn_id,
                        assistant.usage.response_id.as_deref(),
                        assistant_index,
                    ),
                )
            })
            .collect();
        let child_opts = TurnReplayOpts {
            trace_id: Some(trace_id.to_string()),
            root_span_id: None,
            parent_span_id: Some(agent_span_id.clone()),
            sofa_traceid: None,
            sofa_rpcid: None,
            billing_mode: None,
            generation_span_ids,
            subagent_span_ids: std::collections::HashMap::new(),
        };
        let child_otel = build_otel_export_request_for_turn(
            turn,
            turn_index + 1,
            &child.session_id,
            username,
            version,
            Some(&child_opts),
        );
        let Some(child_spans) = child_otel
            .pointer("/resourceSpans/0/scopeSpans/0/spans")
            .and_then(Value::as_array)
        else {
            observe_subagent_merge_failure("missing_spans_array", &child.session_id, None);
            return false;
        };
        let Some(child_root) = child_spans.iter().find(|child_root| {
            child_root
                .get("parentSpanId")
                .and_then(Value::as_str)
                .is_none_or(|parent| parent.is_empty() || parent == agent_span_id)
        }) else {
            observe_subagent_merge_failure("missing_root_span", &child.session_id, None);
            return false;
        };

        if let Some(child_attrs) = child_root.get("attributes").and_then(Value::as_array) {
            for key in &["gen_ai.input.messages", "gen_ai.output.messages"] {
                if agent_span
                    .attributes
                    .iter()
                    .any(|attribute| attribute.key == *key)
                {
                    continue;
                }
                if let Some(string_value) = child_attrs
                    .iter()
                    .find(|attribute| attribute["key"].as_str() == Some(key))
                    .and_then(|attribute| attribute.pointer("/value/stringValue"))
                    .and_then(Value::as_str)
                {
                    agent_span.attributes.push(OtelAttribute::string(
                        *key,
                        if is_privacy_mode() {
                            "[REDACTED]"
                        } else {
                            string_value
                        },
                    ));
                }
            }
        }

        let Some(child_root_id) = child_root["spanId"]
            .as_str()
            .filter(|span_id| !span_id.is_empty())
        else {
            observe_subagent_merge_failure("missing_root_span_id", &child.session_id, None);
            return false;
        };
        match prepare_reparented_subagent_descendants(
            child_spans,
            child_root_id,
            &agent_span_id,
            &child.session_id,
        ) {
            Ok(mut prepared) => pending_descendants.append(&mut prepared),
            Err(failure) => {
                observe_subagent_merge_failure(
                    failure.stage,
                    &child.session_id,
                    Some(&failure.span_id),
                );
                return false;
            }
        }
    }
    if spec.outcome == SubagentSpanOutcome::Pending {
        pending_descendants.retain(is_completed_pending_child_progress_span);
        if pending_descendants.is_empty() {
            return false;
        }
        let Some(spans_arr) = spans.as_array_mut() else {
            return false;
        };
        spans_arr.extend(pending_descendants);
        return true;
    }
    if !enforce_span_budget_observed(&mut agent_span).within_budget {
        observe_subagent_merge_failure(
            "agent_span_budget",
            &child.session_id,
            Some(&agent_span_id),
        );
        return false;
    }
    let agent_span = match serde_json::to_value(agent_span) {
        Ok(agent_span) => agent_span,
        Err(_) => {
            observe_subagent_merge_failure(
                "serialize_agent_span",
                &child.session_id,
                Some(&agent_span_id),
            );
            return false;
        }
    };

    let Some(spans_arr) = spans.as_array_mut() else {
        return false;
    };
    if let Some(root_span) = spans_arr
        .iter_mut()
        .find(|span| span["spanId"].as_str() == Some(root_span_id))
    {
        let current_end = root_span["endTimeUnixNano"]
            .as_str()
            .and_then(|value| value.parse::<u128>().ok())
            .unwrap_or(0);
        let child_end = spec.end_ns.parse::<u128>().unwrap_or(0);
        if child_end > current_end {
            root_span["endTimeUnixNano"] = Value::String(spec.end_ns.to_string());
        }
    }
    spans_arr.push(agent_span);
    spans_arr.extend(pending_descendants);
    true
}

/// Result of subagent merge and any completed children whose transcript is not ready.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SubagentInvocationOutcome {
    pub invocation_id: String,
    pub child_session_id: String,
    pub activation_tool_call_id: String,
    pub status: String,
}

#[derive(Default)]
pub struct MergeResult {
    pub merged_sync_ids: Vec<String>,
    pub merged_subagent_invocation_ids: Vec<String>,
    pub invocation_outcomes: Vec<SubagentInvocationOutcome>,
    pub correlation_reasons: Vec<(String, String)>,
    pub deferred_sync_ids: Vec<String>,
}

/// Merge subagent child sessions into the parent's OTel export body.
///
/// Shared by both the replay binary and the langfuse hook handler.
/// For each child: retains the exactly matched spawn_agent invocation and
/// wait_agent as parent siblings, marks failed spawns as ERROR, creates one
/// AGENT execution span named from the Codex nickname (falling back to
/// "agent SyncAgent"), and injects child generation+tool spans beneath it.
///
/// Children without exact lifecycle evidence are skipped. Current Codex V2
/// accepts either spawn+wait+typed terminal or the 0.148 foreground shape:
/// exact activity UUID/path plus a typed terminal from that child path.
pub fn merge_subagent_sessions_into_otel(
    body: &mut Value,
    parent_turn: &Turn,
    children: &[SubagentChild],
    username: &str,
    version: &str,
) -> MergeResult {
    merge_subagent_sessions_into_otel_across_turns(
        body,
        std::slice::from_ref(parent_turn),
        0,
        children,
        username,
        version,
    )
}

/// Session-aware variant used by replay and the live exporter.
///
/// The OTel body belongs to `parent_turns[anchor_turn_index]`, while child
/// completion evidence may come from later parent turns after
/// `followup_task`. Only children whose initial spawn belongs to this anchor
/// are considered, which avoids the previous parent-turn × child-session
/// `no_identity_match` log explosion.
pub fn merge_subagent_sessions_into_otel_across_turns(
    body: &mut Value,
    parent_turns: &[Turn],
    anchor_turn_index: usize,
    children: &[SubagentChild],
    username: &str,
    version: &str,
) -> MergeResult {
    let Some(parent_turn) = parent_turns.get(anchor_turn_index) else {
        return MergeResult::default();
    };
    let (trace_id, root_span_id) = match resolve_parent_root_topology(body) {
        Ok(topology) => topology,
        Err(reason) => {
            return MergeResult {
                correlation_reasons: vec![("parent_root".to_string(), reason)],
                ..MergeResult::default()
            };
        }
    };
    let spans = match body.pointer_mut("/resourceSpans/0/scopeSpans/0/spans") {
        Some(s) => s,
        None => return MergeResult::default(),
    };

    let mut merged_sync_ids: Vec<String> = Vec::new();
    let mut merged_invocation_ids: Vec<String> = Vec::new();
    let mut invocation_outcomes: Vec<SubagentInvocationOutcome> = Vec::new();
    let mut correlation_reasons = Vec::new();
    let mut deferred_sync_ids = Vec::new();
    let extra_span_attrs = cdxhooks_otel_span_attr_pairs();
    let normalized_activations = normalized_subagent_activations(parent_turns);
    let identities: Vec<SubagentIdentity> = children
        .iter()
        .map(|child| SubagentIdentity {
            session_id: child.session_id.clone(),
            agent_path: child.agent_path.clone(),
        })
        .collect();

    for (child, identity) in children.iter().zip(&identities) {
        let mut native_activations = normalized_activations
            .iter()
            .filter(|activation| {
                activation.source == CollabInvocationSource::NativeToolCall
                    && activation.tool_name == "spawn_agent"
                    && activation.child_thread_id.as_deref() == Some(child.session_id.as_str())
            })
            .collect::<Vec<_>>();
        native_activations.sort_by_key(|activation| {
            activation
                .started_at_unix_nano
                .as_deref()
                .and_then(|value| value.parse::<u128>().ok())
                .unwrap_or(0)
        });
        if !native_activations.is_empty() {
            for (activation_index, activation) in native_activations.iter().enumerate() {
                if activation.turn_index != anchor_turn_index {
                    continue;
                }
                let invocation_key =
                    subagent_invocation_key(&child.session_id, &activation.activation_id);
                let mut outcome = match activation.state {
                    CollabInvocationState::Completed => SubagentSpanOutcome::Completed,
                    CollabInvocationState::Interrupted => SubagentSpanOutcome::Interrupted,
                    CollabInvocationState::Error => SubagentSpanOutcome::Error,
                    CollabInvocationState::Pending => SubagentSpanOutcome::Pending,
                    CollabInvocationState::Malformed => {
                        correlation_reasons.push((invocation_key, activation.reason.clone()));
                        if !deferred_sync_ids.contains(&child.session_id) {
                            deferred_sync_ids.push(child.session_id.clone());
                        }
                        continue;
                    }
                };
                let invocation_child_turns = native_child_turns_for_activation(
                    child,
                    parent_turns,
                    &native_activations,
                    activation_index,
                );
                if invocation_child_turns
                    .last()
                    .is_some_and(|turn| turn.terminal_state == TurnTerminalState::Failed)
                {
                    outcome = SubagentSpanOutcome::Error;
                }
                let child_is_stable = match outcome {
                    SubagentSpanOutcome::Error => true,
                    SubagentSpanOutcome::Pending => {
                        child_turns_have_exportable_descendants(&invocation_child_turns)
                    }
                    SubagentSpanOutcome::Interrupted => invocation_child_turns
                        .last()
                        .is_some_and(|turn| turn.terminal_state == TurnTerminalState::Aborted),
                    SubagentSpanOutcome::Completed => {
                        invocation_child_turns.last().is_some_and(|turn| {
                            matches!(
                                turn.terminal_state,
                                TurnTerminalState::Completed | TurnTerminalState::HeuristicComplete
                            )
                        })
                    }
                };
                if outcome != SubagentSpanOutcome::Error
                    && (invocation_child_turns.is_empty() || !child_is_stable)
                {
                    let reason = if outcome == SubagentSpanOutcome::Pending {
                        "child_progress_pending"
                    } else {
                        "child_terminal_pending"
                    };
                    correlation_reasons.push((invocation_key, reason.to_string()));
                    if !deferred_sync_ids.contains(&child.session_id) {
                        deferred_sync_ids.push(child.session_id.clone());
                    }
                    continue;
                }
                let Some(start_ns) = activation.started_at_unix_nano.as_deref() else {
                    correlation_reasons.push((invocation_key, "malformed_timing".to_string()));
                    if !deferred_sync_ids.contains(&child.session_id) {
                        deferred_sync_ids.push(child.session_id.clone());
                    }
                    continue;
                };
                let computed_window;
                let end_ns = if outcome == SubagentSpanOutcome::Pending {
                    computed_window = compute_child_task_window(
                        &invocation_child_turns,
                        start_ns,
                        activation
                            .completed_at_unix_nano
                            .as_deref()
                            .unwrap_or(start_ns),
                    );
                    computed_window.1.as_str()
                } else if let Some(end_ns) = activation.completed_at_unix_nano.as_deref() {
                    end_ns
                } else {
                    correlation_reasons.push((invocation_key, "malformed_timing".to_string()));
                    if !deferred_sync_ids.contains(&child.session_id) {
                        deferred_sync_ids.push(child.session_id.clone());
                    }
                    continue;
                };
                let timing_valid = start_ns
                    .parse::<u128>()
                    .ok()
                    .zip(end_ns.parse::<u128>().ok())
                    .is_some_and(|(start, end)| start > 0 && end >= start);
                if !timing_valid {
                    correlation_reasons.push((invocation_key, "malformed_timing".to_string()));
                    if !deferred_sync_ids.contains(&child.session_id) {
                        deferred_sync_ids.push(child.session_id.clone());
                    }
                    continue;
                }

                let mut effective_child = child.clone();
                if effective_child.nickname.trim().is_empty() {
                    effective_child.nickname = activation.nickname.clone().unwrap_or_default();
                }
                if effective_child.role.trim().is_empty() {
                    effective_child.role = activation.role.clone().unwrap_or_default();
                }
                let prompt = activation.prompt.as_deref().unwrap_or("");
                let terminal_output = match outcome {
                    SubagentSpanOutcome::Interrupted => String::new(),
                    SubagentSpanOutcome::Pending => String::new(),
                    SubagentSpanOutcome::Completed | SubagentSpanOutcome::Error => activation
                        .terminal_output
                        .clone()
                        .unwrap_or_else(|| child_invocation_output(&invocation_child_turns)),
                };
                let spec = SubagentSpanSpec {
                    activation_tool_call_id: &activation.activation_id,
                    prompt,
                    terminal_output: &terminal_output,
                    start_ns,
                    end_ns,
                    outcome,
                };
                if outcome == SubagentSpanOutcome::Pending
                    && !deferred_sync_ids.contains(&child.session_id)
                {
                    deferred_sync_ids.push(child.session_id.clone());
                }
                if outcome == SubagentSpanOutcome::Pending {
                    correlation_reasons.push((
                        invocation_key.clone(),
                        format!("native_collab_{}", activation.reason),
                    ));
                }
                if !append_subagent_agent_span(
                    spans,
                    &trace_id,
                    &root_span_id,
                    &effective_child,
                    &invocation_child_turns,
                    username,
                    version,
                    &extra_span_attrs,
                    &spec,
                ) {
                    continue;
                }
                if outcome != SubagentSpanOutcome::Pending
                    && !merged_sync_ids.contains(&child.session_id)
                {
                    merged_sync_ids.push(child.session_id.clone());
                }
                if outcome != SubagentSpanOutcome::Pending {
                    if !merged_invocation_ids.contains(&invocation_key) {
                        merged_invocation_ids.push(invocation_key.clone());
                    }
                    invocation_outcomes.push(SubagentInvocationOutcome {
                        invocation_id: invocation_key.clone(),
                        child_session_id: child.session_id.clone(),
                        activation_tool_call_id: activation.activation_id.clone(),
                        status: outcome.status().to_string(),
                    });
                }
                if outcome != SubagentSpanOutcome::Pending {
                    correlation_reasons.push((
                        invocation_key,
                        format!("native_collab_{}", activation.reason),
                    ));
                }
            }
            continue;
        }

        if subagent_anchor_turn_index(parent_turns, identity, &identities).is_none() {
            continue;
        }
        let invocation_matches =
            correlate_subagent_invocations(parent_turns, identity, &identities, &child.turns);
        let Some(spawn_call_id) = invocation_matches
            .iter()
            .find_map(|matched| matched.correlation.spawn_call_id.as_deref())
        else {
            continue;
        };

        let invocations = subagent_invocations(parent_turns, identity, spawn_call_id);
        let spawn_timestamp = invocations
            .first()
            .map(|invocation| invocation.tool.started_timestamp.as_str())
            .unwrap_or("");
        let all_child_turns = child_execution_turns(child, parent_turns, spawn_timestamp);

        for (invocation_index, invocation) in invocations.iter().enumerate() {
            if invocation.turn_index != anchor_turn_index {
                continue;
            }
            let Some(invocation_match) = invocation_matches.iter().find(|matched| {
                matched.activation_turn_index == invocation.turn_index
                    && matched.correlation.activation_call_id.as_deref()
                        == Some(invocation.tool.call_id.as_str())
            }) else {
                continue;
            };
            let outcome = match invocation_match.correlation.lifecycle {
                SubagentLifecycle::CompletedSync => SubagentSpanOutcome::Completed,
                SubagentLifecycle::FailedSync => SubagentSpanOutcome::Error,
                SubagentLifecycle::InterruptedSync => SubagentSpanOutcome::Interrupted,
                SubagentLifecycle::IdentityCandidate => SubagentSpanOutcome::Pending,
                SubagentLifecycle::AmbiguousOrOrphan => continue,
            };
            let is_interrupted = outcome == SubagentSpanOutcome::Interrupted;
            let invocation_child_turns =
                child_turns_for_invocation(&all_child_turns, &invocations, invocation_index);
            if invocation_child_turns.is_empty() {
                continue;
            }
            if outcome == SubagentSpanOutcome::Pending
                && !child_turns_have_exportable_descendants(&invocation_child_turns)
            {
                if !deferred_sync_ids.contains(&child.session_id) {
                    deferred_sync_ids.push(child.session_id.clone());
                }
                correlation_reasons.push((
                    invocation_match.invocation_key.clone(),
                    "child_progress_pending".to_string(),
                ));
                continue;
            }

            let (tool_start, tool_end) = {
                let Some(spans_arr) = spans.as_array() else {
                    continue;
                };
                let Some(tool_span) = spans_arr.iter().find(|span| {
                    let Some(attrs) = span.get("attributes").and_then(Value::as_array) else {
                        return false;
                    };
                    find_otel_attr(attrs, "gen_ai.tool.call.id").as_deref()
                        == Some(invocation.tool.call_id.as_str())
                        && find_otel_attr(attrs, "agentic.tool.raw_name").as_deref()
                            == Some(invocation.tool.name.as_str())
                }) else {
                    continue;
                };
                (
                    tool_span["startTimeUnixNano"]
                        .as_str()
                        .unwrap_or("0")
                        .to_string(),
                    tool_span["endTimeUnixNano"]
                        .as_str()
                        .unwrap_or("0")
                        .to_string(),
                )
            };

            let (start_ns, end_ns) =
                compute_child_task_window(&invocation_child_turns, &tool_start, &tool_end);
            let agent_input_prompt = invocation
                .tool
                .input
                .get("message")
                .or_else(|| invocation.tool.input.get("prompt"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let agent_output_result = if is_interrupted || outcome == SubagentSpanOutcome::Pending {
                String::new()
            } else {
                subagent_invocation_terminal_output(
                    parent_turns,
                    identity,
                    &invocations,
                    invocation_index,
                )
                .unwrap_or_else(|| child_invocation_output(&invocation_child_turns))
            };
            let agent_status = outcome.status();
            let spec = SubagentSpanSpec {
                activation_tool_call_id: &invocation.tool.call_id,
                prompt: &agent_input_prompt,
                terminal_output: &agent_output_result,
                start_ns: &start_ns,
                end_ns: &end_ns,
                outcome,
            };
            if outcome == SubagentSpanOutcome::Pending
                && !deferred_sync_ids.contains(&child.session_id)
            {
                deferred_sync_ids.push(child.session_id.clone());
            }
            if outcome == SubagentSpanOutcome::Pending {
                correlation_reasons.push((
                    invocation_match.invocation_key.clone(),
                    invocation_match.correlation.reason.clone(),
                ));
            }
            if !append_subagent_agent_span(
                spans,
                &trace_id,
                &root_span_id,
                child,
                &invocation_child_turns,
                username,
                version,
                &extra_span_attrs,
                &spec,
            ) {
                continue;
            }

            if outcome != SubagentSpanOutcome::Pending
                && !merged_sync_ids.contains(&child.session_id)
            {
                merged_sync_ids.push(child.session_id.clone());
            }
            if outcome != SubagentSpanOutcome::Pending {
                if !merged_invocation_ids.contains(&invocation_match.invocation_key) {
                    merged_invocation_ids.push(invocation_match.invocation_key.clone());
                }
                invocation_outcomes.push(SubagentInvocationOutcome {
                    invocation_id: invocation_match.invocation_key.clone(),
                    child_session_id: child.session_id.clone(),
                    activation_tool_call_id: invocation.tool.call_id.clone(),
                    status: agent_status.to_string(),
                });
            }
            if outcome != SubagentSpanOutcome::Pending {
                correlation_reasons.push((
                    invocation_match.invocation_key.clone(),
                    invocation_match.correlation.reason.clone(),
                ));
            }
        }
    }

    // Determine spawn success from parsed parent transcript evidence. This is
    // intentionally outside the child loop so an active sibling is not marked
    // failed merely because another child completed first.
    let successful_spawn_ids: std::collections::HashSet<&str> = parent_turn
        .tool_calls
        .iter()
        .filter(|tool| tool.name == "spawn_agent")
        .filter(|tool| {
            ["agent_id", "new_thread_id", "task_name"]
                .iter()
                .any(|key| {
                    tool.output
                        .get(*key)
                        .and_then(Value::as_str)
                        .map_or(false, |value| !value.is_empty())
                })
                || parent_turn.collab_agent_events.iter().any(|event| {
                    matches!(event, CollabAgentEvent::SubagentActivity { event_id, .. } if event_id == &tool.call_id)
                })
        })
        .map(|tool| tool.call_id.as_str())
        .collect();
    if let Some(spans_arr) = spans.as_array_mut() {
        for span in spans_arr.iter_mut() {
            let attrs = match span.get("attributes").and_then(Value::as_array) {
                Some(attrs) => attrs,
                None => continue,
            };
            if find_otel_attr(attrs, "agentic.tool.raw_name").as_deref() != Some("spawn_agent") {
                continue;
            }
            let call_id = find_otel_attr(attrs, "gen_ai.tool.call.id");
            if call_id
                .as_deref()
                .map_or(false, |call_id| successful_spawn_ids.contains(call_id))
            {
                continue;
            }
            span["status"] = json!({"code": 2, "message": "spawn_agent failed"});
            if let Some(attrs) = span.get_mut("attributes").and_then(Value::as_array_mut) {
                if !attrs
                    .iter()
                    .any(|attr| attr["key"].as_str() == Some("error.type"))
                {
                    attrs.push(
                        json!({"key": "error.type", "value": {"stringValue": "spawn_error"}}),
                    );
                }
            }
        }
    }

    MergeResult {
        merged_sync_ids,
        merged_subagent_invocation_ids: merged_invocation_ids,
        invocation_outcomes,
        correlation_reasons,
        deferred_sync_ids,
    }
}

fn find_otel_attr(attrs: &[Value], key: &str) -> Option<String> {
    attrs.iter().find_map(|a| {
        if a["key"].as_str() == Some(key) {
            a["value"]["stringValue"]
                .as_str()
                .or_else(|| a["value"]["intValue"].as_str())
                .map(|s| s.to_string())
        } else {
            None
        }
    })
}

const VCS_REPOSITORY_URL_KEY: &str = "vcs.repository.url.full";
const VCS_BRANCH_KEY: &str = "vcs.ref.head.name";

fn resource_attributes_mut(resource_span: &mut Value) -> Option<&mut Vec<Value>> {
    let resource_span = resource_span.as_object_mut()?;
    let resource = resource_span.entry("resource").or_insert_with(|| json!({}));
    if !resource.is_object() {
        *resource = json!({});
    }
    let resource = resource.as_object_mut()?;
    let attributes = resource.entry("attributes").or_insert_with(|| json!([]));
    if !attributes.is_array() {
        *attributes = json!([]);
    }
    attributes.as_array_mut()
}

fn configure_vcs_resource(resource_span: &mut Value, remote_url: &str, branch: &str) {
    let Some(attrs) = resource_attributes_mut(resource_span) else {
        return;
    };
    attrs.retain(|attribute| {
        !matches!(
            attribute.get("key").and_then(Value::as_str),
            Some(VCS_REPOSITORY_URL_KEY | VCS_BRANCH_KEY)
        )
    });
    for (key, value) in [
        (VCS_REPOSITORY_URL_KEY, remote_url),
        (VCS_BRANCH_KEY, branch),
    ] {
        if value.is_empty() {
            continue;
        }
        if let Ok(attribute) = serde_json::to_value(OtelAttribute::string(key, value)) {
            attrs.push(attribute);
        }
    }
}

/// Idempotently upsert VCS attributes into every Resource in an OTLP request.
pub fn inject_vcs_resource_attrs(request: &mut Value, remote_url: &str, branch: &str) {
    let Some(resource_spans) = request
        .get_mut("resourceSpans")
        .and_then(Value::as_array_mut)
    else {
        return;
    };
    for resource_span in resource_spans {
        configure_vcs_resource(resource_span, remote_url, branch);
    }
}

fn span_belongs_to_session(span: &Value, session_id: &str) -> bool {
    span.get("attributes")
        .and_then(Value::as_array)
        .and_then(|attrs| find_otel_attr(attrs, "gen_ai.conversation.id"))
        .as_deref()
        == Some(session_id)
}

fn session_span_count(resource_span: &Value, session_id: &str) -> (usize, usize) {
    let mut matching = 0usize;
    let mut total = 0usize;
    if let Some(scope_spans) = resource_span.get("scopeSpans").and_then(Value::as_array) {
        for scope in scope_spans {
            if let Some(spans) = scope.get("spans").and_then(Value::as_array) {
                total = total.saturating_add(spans.len());
                matching = matching.saturating_add(
                    spans
                        .iter()
                        .filter(|span| span_belongs_to_session(span, session_id))
                        .count(),
                );
            }
        }
    }
    (matching, total)
}

fn retain_session_spans(resource_span: &mut Value, session_id: &str, keep_matching: bool) {
    let Some(scope_spans) = resource_span
        .get_mut("scopeSpans")
        .and_then(Value::as_array_mut)
    else {
        return;
    };
    for scope in scope_spans {
        if let Some(spans) = scope.get_mut("spans").and_then(Value::as_array_mut) {
            spans.retain(|span| span_belongs_to_session(span, session_id) == keep_matching);
        }
    }
}

/// Isolate one session's spans into its own Resource and apply only that
/// session's VCS snapshot. Empty values intentionally remove inherited VCS
/// attributes so a parent repository can never leak onto a child session.
pub fn isolate_session_vcs_resource(
    request: &mut Value,
    session_id: &str,
    remote_url: &str,
    branch: &str,
) -> usize {
    let Some(resource_spans) = request
        .get_mut("resourceSpans")
        .and_then(Value::as_array_mut)
    else {
        return 0;
    };
    let original_len = resource_spans.len();
    let mut isolated = Vec::new();
    let mut moved = 0usize;
    for resource_span in resource_spans.iter_mut().take(original_len) {
        let (matching, total) = session_span_count(resource_span, session_id);
        if matching == 0 {
            continue;
        }
        moved = moved.saturating_add(matching);
        if matching == total {
            configure_vcs_resource(resource_span, remote_url, branch);
            continue;
        }
        let mut session_resource = resource_span.clone();
        retain_session_spans(resource_span, session_id, false);
        retain_session_spans(&mut session_resource, session_id, true);
        configure_vcs_resource(&mut session_resource, remote_url, branch);
        isolated.push(session_resource);
    }
    resource_spans.extend(isolated);
    moved
}

fn cdxhooks_wrap_export_request(spans: Vec<OtelSpan>, build_version: &str) -> Value {
    let agent_env =
        agentic_otel_common::env_or_default("CDXHOOKS_AGENT_ENV", "AIVISION_AGENT_ENV", "Default");
    let agent_scene = agentic_otel_common::env_or_default(
        "CDXHOOKS_AGENT_SCENE",
        "AIVISION_AGENT_SCENE",
        "Default",
    );
    let agent_product = resolve_agent_product();
    let agent_type = resolve_agent_type();
    let ctx = ExportContext {
        agent_product: &agent_product,
        agent_type: &agent_type,
        sdk_name: "cdxhooks",
        build_version,
        agent_env: &agent_env,
        agent_scene: &agent_scene,
        collection_mode: if is_privacy_mode() { "privacy" } else { "full" },
        emit_meta_trigger: true,
    };
    wrap_trace_export(spans, &ctx)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::transcript::CollabAgentInvocationEvidence;
    use crate::common::transcript::TurnTrigger;
    use crate::common::transcript::{AssistantMessage, CollabAgentEvent, ToolCall, Turn};

    fn spawn_http_response(status: u16, body: &str) -> (String, std::thread::JoinHandle<()>) {
        use std::io::{self, BufRead, BufReader, Read, Write};
        use std::net::TcpListener;
        use std::time::{Duration, Instant};

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let address = listener.local_addr().unwrap();
        let body = body.to_string();
        let server = std::thread::spawn(move || {
            let timeout = Duration::from_secs(2);
            let deadline = Instant::now() + timeout;
            let mut stream = loop {
                match listener.accept() {
                    Ok((stream, _)) => break stream,
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        assert!(Instant::now() < deadline, "no HTTP test request arrived");
                        std::thread::sleep(Duration::from_millis(5));
                    }
                    Err(error) => panic!("accept HTTP test request: {error}"),
                }
            };
            stream.set_nonblocking(false).unwrap();
            stream.set_read_timeout(Some(timeout)).unwrap();
            stream.set_write_timeout(Some(timeout)).unwrap();

            let mut reader = BufReader::new(&mut stream);
            let mut line = String::new();
            let mut content_length = None;
            loop {
                line.clear();
                assert_ne!(reader.read_line(&mut line).unwrap(), 0, "incomplete HTTP headers");
                if line == "\r\n" {
                    break;
                }
                if let Some((name, value)) = line.split_once(':') {
                    if name.eq_ignore_ascii_case("content-length") {
                        assert!(content_length.replace(value.trim().parse::<u64>().unwrap()).is_none());
                    }
                }
            }
            let content_length = content_length.expect("HTTP test request must specify Content-Length");
            let drained = io::copy(&mut reader.take(content_length), &mut io::sink()).unwrap();
            assert_eq!(drained, content_length, "incomplete HTTP request body");
            let response = format!(
                "HTTP/1.1 {status} OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            stream.write_all(response.as_bytes()).unwrap();
        });
        (format!("http://{address}"), server)
    }

    #[test]
    fn custom_header_debug_log_redacts_values() {
        let dir = tempfile::tempdir().unwrap();
        let log_path = dir.path().join("header.log");
        let logger = crate::common::logger::Logger::new(log_path.clone(), true);
        let secret = "CUSTOM_HEADER_SECRET_CANARY";
        let config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host: "http://127.0.0.1:9".to_string(),
            headers: Some(std::collections::HashMap::from([(
                "x-private-token".to_string(),
                secret.to_string(),
            )])),
        };

        let _ = send_otel_request(
            &config,
            "http://127.0.0.1:9/v1/traces",
            "{}",
            &logger,
            std::time::Duration::from_millis(10),
            false,
        );

        let log = std::fs::read_to_string(log_path).unwrap();
        assert!(log.contains("Adding custom header name: x-private-token"));
        assert!(!log.contains(secret));
    }

    #[test]
    fn send_lifecycle_uses_shared_start_end_field_taxonomy() {
        let dir = tempfile::tempdir().unwrap();
        let log_path = dir.path().join("lifecycle.log");
        let logger = crate::common::logger::Logger::new(log_path.clone(), true);
        let (host, server) = spawn_http_response(200, "ok");
        let config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host: host.clone(),
            headers: None,
        };

        let response = post_otel_to_langfuse_once_classified(
            &config,
            &json!({"resourceSpans": []}),
            &logger,
            std::time::Duration::from_secs(1),
        );
        server.join().unwrap();
        response.unwrap();

        let log = std::fs::read_to_string(log_path).unwrap();
        let lifecycle = log
            .lines()
            .filter(|line| line.contains("event=langfuse_send_"))
            .collect::<Vec<_>>();
        assert_eq!(lifecycle.len(), 2, "log={log}");
        assert!(lifecycle[0].contains("event=langfuse_send_start"));
        assert!(lifecycle[0].contains("operation=other"));
        assert!(lifecycle[0].contains("timeout_ms="));
        assert!(lifecycle[1].contains("event=langfuse_send_end"));
        assert!(lifecycle[1].contains("outcome=success"));
        assert!(lifecycle[1].contains("http_status=200"));
        assert!(lifecycle[1].contains("elapsed_ms="));
        assert!(lifecycle[1].contains("body_bytes="));
        let attempt_id = |line: &str| {
            line.split("attempt_id=")
                .nth(1)
                .and_then(|value| value.split_whitespace().next())
                .unwrap()
                .to_string()
        };
        assert_eq!(attempt_id(lifecycle[0]), attempt_id(lifecycle[1]));
        assert!(!lifecycle.join("\n").contains(&host));
    }

    #[test]
    fn http_export_fails_closed_before_sending_an_oversized_bare_span() {
        let dir = tempfile::tempdir().unwrap();
        let logger = crate::common::logger::Logger::new(dir.path().join("oversized.log"), true);
        let config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host: "http://127.0.0.1:9".to_string(),
            headers: None,
        };
        let body = json!({
            "resourceSpans": [{
                "scopeSpans": [{
                    "spans": [{
                        "spanId": "oversized",
                        "name": "x".repeat(crate::common::payload_budget::MAX_SERIALIZED_SPAN_BYTES)
                    }]
                }]
            }]
        });

        let error = post_otel_to_langfuse_once_classified(
            &config,
            &body,
            &logger,
            std::time::Duration::from_millis(10),
        )
        .unwrap_err();

        assert!(
            matches!(error, OtelPostError::Preparation(message) if message.contains("otel_span_oversize"))
        );
    }

    #[test]
    fn legacy_http_export_wrapper_also_fails_closed_for_oversized_span() {
        let dir = tempfile::tempdir().unwrap();
        let logger = crate::common::logger::Logger::new(dir.path().join("oversized.log"), true);
        let config = LangfuseConfig {
            public_key: None,
            secret_key: None,
            host: "http://127.0.0.1:9".to_string(),
            headers: None,
        };
        let body = json!({
            "resourceSpans": [{
                "scopeSpans": [{
                    "spans": [{
                        "spanId": "oversized-legacy",
                        "name": "x".repeat(crate::common::payload_budget::MAX_SERIALIZED_SPAN_BYTES)
                    }]
                }]
            }]
        });

        let error = post_otel_to_langfuse_once(
            &config,
            &body,
            &logger,
            std::time::Duration::from_millis(10),
        )
        .unwrap_err();

        assert!(error.contains("otel_span_oversize"));
    }

    #[test]
    fn remote_timing_log_contains_sizes_and_phases_without_payload() {
        let dir = tempfile::tempdir().unwrap();
        let log_path = dir.path().join("logs/session.log");
        let logger = crate::common::logger::Logger::new(log_path.clone(), true);
        let payload_canary = "REMOTE_PAYLOAD_CANARY";
        log_remote_phase(
            &logger,
            "http",
            std::time::Duration::from_micros(42),
            payload_canary.len(),
            Some(200),
            "ok",
        );

        let text = std::fs::read_to_string(log_path).unwrap();
        assert!(text.contains("cdxhooks.remote_timing"));
        assert!(text.contains("\"phase\":\"http\""));
        assert!(text.contains("\"elapsed_ms\":0"));
        assert!(text.contains("\"execution\":\"async\""));
        assert!(!text.contains("elapsed_us"));
        assert!(text.contains(&format!("\"request_bytes\":{}", payload_canary.len())));
        assert!(!text.contains(payload_canary));
    }

    #[test]
    fn worker_http_total_includes_trace_attempts_and_metrics_flush() {
        let dir = tempfile::tempdir().unwrap();
        let logger = crate::common::logger::Logger::new(dir.path().join("worker.log"), true);
        install_worker_log_context(&logger, "PostToolUse", "event");

        log_remote_phase(
            &logger,
            "http",
            std::time::Duration::from_millis(12),
            1,
            Some(503),
            "http_error",
        );
        log_remote_phase(
            &logger,
            "http",
            std::time::Duration::from_millis(8),
            1,
            Some(200),
            "ok",
        );
        observe_worker_http_elapsed(3);

        assert_eq!(current_worker_http_total_ms(), 23);
        clear_worker_log_context();
    }

    #[test]
    fn span_budget_truncation_is_logged_without_payload_content() {
        let dir = tempfile::tempdir().unwrap();
        let log_path = dir.path().join("worker.log");
        let logger = crate::common::logger::Logger::new(log_path.clone(), true);
        install_worker_log_context(&logger, "Stop", "event-budget");
        let payload_canary = "SPAN_BUDGET_PAYLOAD_CANARY";
        let mut span = OtelSpan {
            trace_id: "11111111111111111111111111111111".to_string(),
            span_id: "2222222222222222".to_string(),
            parent_span_id: String::new(),
            name: "chat test".to_string(),
            kind: SPAN_KIND_INTERNAL,
            start_time_unix_nano: "1".to_string(),
            end_time_unix_nano: "2".to_string(),
            attributes: vec![
                OtelAttribute::string("gen_ai.operation.name", "chat"),
                OtelAttribute::string(
                    "gen_ai.input.messages",
                    format!(
                        "[{{\"role\":\"user\",\"content\":\"{}{}\"}}]",
                        payload_canary,
                        "x".repeat(crate::common::payload_budget::MAX_SERIALIZED_SPAN_BYTES + 4096)
                    ),
                ),
            ],
            status: OtelStatus::ok(),
        };

        enforce_span_budget_observed(&mut span);
        clear_worker_log_context();

        let text = std::fs::read_to_string(log_path).unwrap();
        assert!(text.contains("span_budget"));
        assert!(text.contains("\"operation\":\"enforce_span_budget\""));
        assert!(text.contains("\"within_budget\":true"));
        assert!(text.contains("\"original_bytes\":"));
        assert!(text.contains("\"kept_bytes\":"));
        assert!(!text.contains(payload_canary));
    }

    #[test]
    fn malformed_subagent_descendant_fails_atomically_and_logs_without_payload() {
        let dir = tempfile::tempdir().unwrap();
        let log_path = dir.path().join("worker.log");
        let logger = crate::common::logger::Logger::new(log_path.clone(), true);
        install_worker_log_context(&logger, "Stop", "event-subagent-merge");
        let payload_canary = "SUBAGENT_MERGE_PAYLOAD_CANARY";
        let valid_span = json!({
            "traceId": "11111111111111111111111111111111",
            "spanId": "2222222222222222",
            "parentSpanId": "child-root",
            "name": "chat child",
            "kind": SPAN_KIND_INTERNAL,
            "startTimeUnixNano": "1",
            "endTimeUnixNano": "2",
            "attributes": [],
            "status": {"code": 1}
        });
        let malformed_span = json!({
            "traceId": "11111111111111111111111111111111",
            "spanId": "3333333333333333",
            "parentSpanId": "child-root",
            "name": "chat malformed",
            "kind": SPAN_KIND_INTERNAL,
            "startTimeUnixNano": "1",
            "endTimeUnixNano": "2",
            "attributes": [{
                "key": "payload",
                "value": {"unsupportedValue": payload_canary}
            }],
            "status": {"code": 1}
        });

        let failure = prepare_reparented_subagent_descendants(
            &[valid_span, malformed_span],
            "child-root",
            "agent-span",
            "child-session",
        )
        .unwrap_err();
        observe_subagent_merge_failure(failure.stage, "child-session", Some(&failure.span_id));
        clear_worker_log_context();

        assert_eq!(failure.stage, "deserialize_span");
        assert_eq!(failure.span_id, "3333333333333333");
        let text = std::fs::read_to_string(log_path).unwrap();
        assert!(text.contains("subagent_merge_failure"));
        assert!(text.contains("\"operation\":\"merge_subagent_descendant\""));
        assert!(text.contains("\"stage\":\"deserialize_span\""));
        assert!(text.contains("\"child_session_id\":\"child-session\""));
        assert!(text.contains("\"span_id\":\"3333333333333333\""));
        assert!(!text.contains(payload_canary));
    }

    #[test]
    fn subagent_span_name_uses_nickname_with_sync_agent_fallback() {
        assert_eq!(subagent_span_name("Volta", false), "agent Volta");
        assert_eq!(
            subagent_span_name("  Nietzsche  ", false),
            "agent Nietzsche"
        );
        assert_eq!(subagent_span_name("", false), "agent SyncAgent");
        assert_eq!(subagent_span_name("   ", false), "agent SyncAgent");
        assert_eq!(subagent_span_name("Volta", true), "agent Volta (Cancelled)");
        assert_eq!(subagent_span_name("", true), "agent SyncAgent (Cancelled)");
    }

    #[test]
    fn stop_post_policy_does_not_retry() {
        let mut attempts = 0;
        let result = run_with_retry_policy(
            false,
            || {
                attempts += 1;
                Err("network down".to_string())
            },
            |_| panic!("single-attempt policy must not invoke retry callback"),
        );

        assert!(result.is_err());
        assert_eq!(attempts, 1);
    }

    #[test]
    fn report_post_policy_can_retry_once() {
        let mut attempts = 0;
        let result = run_with_retry_policy(
            true,
            || {
                attempts += 1;
                if attempts == 1 {
                    Err("transient".to_string())
                } else {
                    Ok(())
                }
            },
            |_| {},
        );

        assert!(result.is_ok());
        assert_eq!(attempts, 2);
    }

    fn make_turn() -> Turn {
        Turn {
            chat_id: None,
            turn_id: "turn-1".to_string(),
            user_timestamp: "2026-03-28T08:00:00.000Z".to_string(),
            user_text: "run diagnostics".to_string(),
            user_content: serde_json::Value::Null,
            assistants: vec![
                AssistantMessage {
                    timestamp: "2026-03-28T08:00:05.000Z".to_string(),
                    text: "Working through the repo".to_string(),
                    media_content: serde_json::Value::Null,
                    phase: "commentary".to_string(),
                    usage: TokenUsage::default(),
                    token_count_timestamp: String::new(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
                AssistantMessage {
                    timestamp: "2026-03-28T08:00:10.000Z".to_string(),
                    text: "Diagnostics complete".to_string(),
                    media_content: serde_json::Value::Null,
                    phase: "final_answer".to_string(),
                    usage: TokenUsage {
                        input: Some(120),
                        output: Some(60),
                        total: Some(180),
                        cached_input: Some(20),
                        reasoning_output: Some(10),
                        cache_creation_input_tokens: Some(5),
                        cache_read_input_tokens: Some(15),
                        response_id: None,
                        source: None,
                    },
                    token_count_timestamp: String::new(),
                    invocation_started_timestamp: String::new(),
                    invocation_end_source: None,
                },
            ],
            tool_calls: vec![ToolCall {
                call_id: "call-1".to_string(),
                name: "Bash".to_string(),
                input: json!({"command": "pwd"}),
                output: Value::String("/tmp/demo\n".to_string()),
                started_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
                ended_timestamp: "2026-03-28T08:00:03.000Z".to_string(),
                assistant_index: 0,
                success: None,
            }],
            model: "gpt-5.4".to_string(),
            cwd: "/tmp/demo".to_string(),
            system_instructions: "Follow the test policy.".to_string(),
            completed: true,
            completion_timestamp: "2026-03-28T08:00:10.000Z".to_string(),
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

    #[test]
    fn typed_media_survives_generation_and_tool_spans_without_changing_topology() {
        let dir = tempfile::tempdir().unwrap();
        let log_path = dir.path().join("inline-image.log");
        let logger = crate::common::logger::Logger::new(log_path.clone(), true);
        install_worker_log_context(&logger, "Stop", "event-inline-image");
        let encoded_chars = 700 * 1024;
        let png = format!("data:image/png;base64,{}", "A".repeat(encoded_chars));
        let jpeg = format!("data:image/jpeg;base64,{}", "B".repeat(encoded_chars));
        let mut turn = make_turn();
        turn.chat_id = Some("chat-inline-image".to_string());
        turn.tool_calls[0].name = "view_image".to_string();
        turn.tool_calls[0].input = json!({
            "image_url": png,
            "remote_image": "https://example.test/reference.png"
        });
        turn.tool_calls[0].output = json!({
            "content": [
                {"type": "input_image", "image_url": turn.tool_calls[0].input["image_url"]},
                {"type": "input_image", "image_url": jpeg}
            ]
        });

        let original_output = turn.tool_calls[0].output.clone();
        let trace_id = "11111111111111111111111111111111";
        let root_span_id = "2222222222222222";
        let opts = TurnReplayOpts {
            trace_id: Some(trace_id.to_string()),
            root_span_id: Some(root_span_id.to_string()),
            ..TurnReplayOpts::default()
        };
        let body = build_otel_export_request_for_turn(
            &turn,
            1,
            "session-inline-image",
            "tester",
            "1.0.0",
            Some(&opts),
        );
        clear_worker_log_context();
        let serialized_body = serde_json::to_string(&body).unwrap();
        let log_text = std::fs::read_to_string(log_path).unwrap();

        assert!(serialized_body.contains("data:image/"));
        assert!(log_text.contains("inline_image_filter"));
        assert!(log_text.contains("\"operation\":\"filter_inline_base64_images\""));
        assert!(log_text.contains("\"filtered_count\":"));
        assert!(log_text.contains("\"original_chars\":"));
        assert!(!log_text.contains("data:image/"));
        assert!(serialized_body.contains(&format!(
            "[FILTERED_INLINE_IMAGE mime=image/png encoded_chars={encoded_chars}]"
        )));
        assert!(serialized_body.contains("data:image/jpeg;base64,"));
        assert!(serialized_body.contains("https://example.test/reference.png"));
        assert_eq!(turn.tool_calls[0].output, original_output);

        let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .unwrap();
        assert_eq!(spans[0]["spanId"], root_span_id);
        for span in spans {
            assert!(
                serde_json::to_vec(span).unwrap().len()
                    <= crate::common::payload_budget::MAX_SERIALIZED_SPAN_BYTES
            );
            if span["spanId"] != root_span_id {
                assert_eq!(span["traceId"], trace_id);
                assert_eq!(span["parentSpanId"], root_span_id);
            }
        }
        let (finalizer_children, finalizer_root_span_id) =
            crate::hooks::langfuse::realtime::extract_finalizer_spans_root_last(&body).unwrap();
        let finalizer_plan = crate::hooks::langfuse::realtime::plan_realtime_payload_chunks(
            finalizer_children,
            crate::common::payload_budget::MAX_SERIALIZED_SPAN_BYTES,
        )
        .unwrap();
        assert_eq!(finalizer_root_span_id, root_span_id);
        assert!(finalizer_plan.oversized_children.is_empty());

        let tool_span = spans
            .iter()
            .find(|span| {
                attr_string(span["attributes"].as_array().unwrap(), "gen_ai.span.kind")
                    == Some("TOOL")
            })
            .unwrap();
        let tool_attrs = tool_span["attributes"].as_array().unwrap();
        let tool_arguments = attr_string(tool_attrs, "gen_ai.tool.call.arguments").unwrap();
        let tool_result = attr_string(tool_attrs, "gen_ai.tool.call.result").unwrap();
        assert!(!tool_arguments.contains("data:image/"));
        assert!(tool_result.contains("data:image/"));

        let second_generation = spans
            .iter()
            .filter(|span| {
                attr_string(span["attributes"].as_array().unwrap(), "gen_ai.span.kind")
                    == Some("LLM")
            })
            .nth(1)
            .unwrap();
        let generation_input = attr_string(
            second_generation["attributes"].as_array().unwrap(),
            "gen_ai.input.messages",
        )
        .unwrap();
        assert!(generation_input.contains("media_type"));
        assert!(generation_input.contains("image/jpeg"));
        assert!(generation_input.contains("image/png"));
    }

    #[test]
    fn test_root_span_emits_turn_trigger_once_and_children_do_not_inherit() {
        let mut turn = make_turn();
        turn.trigger = TurnTrigger::Interactive;
        let body = build_otel_export_request_for_turn(&turn, 1, "sess-1", "tester", "1.0.0", None);
        let resource_attrs = resource_attrs(&body);
        let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .expect("spans");
        let root = &spans[0];
        let root_attrs = root["attributes"].as_array().expect("root attrs");

        assert_eq!(
            attr_string(root_attrs, "agentic.turn.trigger"),
            Some("interactive")
        );
        assert_eq!(
            root_attrs
                .iter()
                .filter(|attr| attr["key"].as_str() == Some("agentic.turn.trigger"))
                .count(),
            1
        );
        assert!(!resource_attrs
            .iter()
            .any(|attr| attr["key"].as_str() == Some("agentic.turn.trigger")));
        assert_eq!(attr_string(root_attrs, "gen_ai.span.kind"), Some("CHAT"));
        assert_eq!(
            attr_string(root_attrs, "gen_ai.operation.name"),
            Some("invoke_agent")
        );
        assert_eq!(
            attr_string(root_attrs, "agentic.runtime.name"),
            Some("codex")
        );
        assert_eq!(attr_int(root_attrs, "agentic.turn.number"), Some(1));

        for child in spans.iter().skip(1) {
            assert!(
                !child["attributes"].as_array().is_some_and(|attrs| attrs
                    .iter()
                    .any(|attr| attr["key"].as_str() == Some("agentic.turn.trigger"))),
                "child span {:?} must not inherit agentic.turn.trigger",
                child["name"]
            );
        }
    }

    #[test]
    fn invalid_model_turn_exports_error_root_and_synthetic_llm_error() {
        let parsed = include_str!("../../tests/fixtures/invalid_model_terminal_error.jsonl")
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect::<Vec<Value>>();
        let turn = crate::common::transcript::build_turns(&parsed)
            .into_iter()
            .next()
            .unwrap();
        let body = build_otel_export_request_for_turn(
            &turn,
            1,
            "invalid-model-session",
            "tester",
            "1.0.0",
            None,
        );
        let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .unwrap();

        assert_eq!(spans.len(), 2);
        assert_eq!(spans[0]["status"]["code"], 2);
        let root_attrs = spans[0]["attributes"].as_array().unwrap();
        assert_eq!(
            attr_string(root_attrs, "error.type"),
            Some("invalid_request_error")
        );

        let llm = spans
            .iter()
            .find(|span| {
                attr_string(span["attributes"].as_array().unwrap(), "gen_ai.span.kind")
                    == Some("LLM")
            })
            .unwrap();
        assert_eq!(llm["status"]["code"], 2);
        let attrs = llm["attributes"].as_array().unwrap();
        assert_eq!(
            attr_string(attrs, "gen_ai.request.model"),
            Some("aivision-regression-model-does-not-exist-0001")
        );
        assert_eq!(attr_string(attrs, "agentic.llm.is_synthetic"), Some("true"));
        assert_eq!(
            attr_string(attrs, "agentic.llm.error.type"),
            Some("invalid_request_error")
        );
        assert_eq!(
            attr_string(attrs, "error.type"),
            Some("invalid_request_error")
        );
        assert_eq!(attr_int(attrs, "agentic.llm.error.status_code"), Some(400));
        assert_eq!(attr_int(attrs, "gen_ai.usage.input_tokens"), Some(0));
        assert_eq!(attr_int(attrs, "gen_ai.usage.output_tokens"), Some(0));
        assert_eq!(
            attr_string(attrs, "agentic.usage.input_tokens.missing"),
            Some("true")
        );
        assert_eq!(
            attr_string(attrs, "agentic.usage.output_tokens.missing"),
            Some("true")
        );
        assert!(!attrs.iter().any(|attribute| {
            matches!(
                attribute["key"].as_str(),
                Some(
                    "gen_ai.response.model"
                        | "gen_ai.response.id"
                        | "gen_ai.output.messages"
                        | "gen_ai.response.finish_reasons"
                )
            )
        }));
        let error_message = attr_string(attrs, "agentic.llm.error.message").unwrap();
        assert!(error_message.contains("[REDACTED]"));
        assert!(!error_message.contains("sk-sensitive-value"));
    }

    #[test]
    fn compaction_only_turn_exports_trusted_tokens_and_monotonic_time() {
        let parsed = include_str!("../../tests/fixtures/compaction_manual_two_turns.jsonl")
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect::<Vec<Value>>();
        let turns = crate::common::transcript::build_turns(&parsed);
        let body = build_otel_export_request_for_turn(
            &turns[1],
            2,
            "compaction-session",
            "tester",
            "1.0.0",
            None,
        );
        let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .unwrap();

        assert_eq!(spans.len(), 2);
        assert_eq!(spans[0]["status"]["code"], 1);
        let compaction = &spans[1];
        assert_eq!(compaction["name"], "compaction");
        assert!(
            compaction["endTimeUnixNano"].as_str().unwrap()
                >= compaction["startTimeUnixNano"].as_str().unwrap()
        );
        let attrs = compaction["attributes"].as_array().unwrap();
        assert_eq!(attr_string(attrs, "gen_ai.span.kind"), Some("COMPACTION"));
        assert_eq!(
            attr_string(attrs, "agentic.compaction.trigger"),
            Some("unknown")
        );
        assert_eq!(
            attr_int(attrs, "agentic.compaction.tokens.before"),
            Some(36941)
        );
        assert_eq!(
            attr_int(attrs, "agentic.compaction.tokens.after"),
            Some(5552)
        );
        assert_eq!(
            attr_int(attrs, "agentic.compaction.tokens.saved"),
            Some(31389)
        );
    }

    #[test]
    fn legacy_generation_start_never_uses_previous_token_count_timestamp() {
        let mut turn = make_turn();
        turn.assistants[0].token_count_timestamp = "2026-03-28T08:00:06.000Z".to_string();

        let start =
            compute_generation_start_ns(&turn, 1, &iso_to_nanos("2026-03-28T08:00:00.000Z"));
        assert_eq!(start, iso_to_nanos("2026-03-28T08:00:03.000Z"));
        assert_ne!(start, iso_to_nanos("2026-03-28T08:00:06.000Z"));

        turn.tool_calls.clear();
        let fallback =
            compute_generation_start_ns(&turn, 1, &iso_to_nanos("2026-03-28T08:00:00.000Z"));
        assert_eq!(fallback, iso_to_nanos("2026-03-28T08:00:00.000Z"));
    }

    fn skill_invocation(
        name: &str,
        ordinal: usize,
        timestamp: &str,
        byte_start: Option<u64>,
        byte_end: Option<u64>,
    ) -> SkillInvocation {
        SkillInvocation {
            name: name.to_string(),
            raw_placeholder: format!("${name}"),
            source: SkillInvocationSource::CodexTextElement,
            timestamp: timestamp.to_string(),
            byte_start,
            byte_end,
            resolved_workflow: Some(name.to_string()),
            definition_path: None,
            original_text_element_index: Some(ordinal),
            ordinal,
        }
    }

    fn exported_spans(body: &Value) -> &[Value] {
        body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .expect("spans")
    }

    fn span_attrs(span: &Value) -> &[Value] {
        span["attributes"].as_array().expect("span attributes")
    }

    fn span_named<'a>(body: &'a Value, name: &str) -> &'a Value {
        exported_spans(body)
            .iter()
            .find(|span| span["name"].as_str() == Some(name))
            .unwrap_or_else(|| panic!("missing span {name}"))
    }

    fn count_spans_named(body: &Value, name: &str) -> usize {
        exported_spans(body)
            .iter()
            .filter(|span| span["name"].as_str() == Some(name))
            .count()
    }

    #[test]
    fn test_followup_task_tool_span_carries_v2_child_identity() {
        let mut turn = make_turn();
        turn.tool_calls = vec![ToolCall {
            call_id: "call-followup".to_string(),
            name: "followup_task".to_string(),
            input: json!({"target":"/root/reviewer","message":"recheck"}),
            output: json!({"delivered":true}),
            started_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
            ended_timestamp: "2026-03-28T08:00:03.000Z".to_string(),
            assistant_index: 0,
            success: Some(true),
        }];
        turn.collab_agent_events = vec![CollabAgentEvent::SubagentActivity {
            event_id: "call-followup".to_string(),
            agent_thread_id: "child-reviewer".to_string(),
            agent_path: Some("/root/reviewer".to_string()),
            kind: "interacted".to_string(),
            timestamp: "2026-03-28T08:00:02.100Z".to_string(),
        }];

        let body = build_otel_export_request_for_turn(&turn, 1, "parent", "tester", "1.0.0", None);
        let span = span_named(&body, "subagent followup_task");
        let attrs = span_attrs(span);
        assert_eq!(
            attr_string(attrs, "agentic.subagent.id"),
            Some("child-reviewer")
        );
        assert_eq!(
            attr_string(attrs, "agentic.subagent.agent_path"),
            Some("/root/reviewer")
        );
    }

    #[test]
    fn test_usage_attrs_defaults_missing_llm_tokens_to_zero() {
        let attrs = usage_attrs(&TokenUsage::default());
        let attr_value = |key: &str| -> Option<&str> {
            attrs
                .iter()
                .find(|a| a.key == key)
                .and_then(|a| match &a.value {
                    agentic_otel_common::otlp::OtelAnyValue::String { string_value } => {
                        Some(string_value.as_str())
                    }
                    _ => None,
                })
        };

        assert_eq!(attr_value("gen_ai.usage.input_tokens"), Some("0"));
        assert_eq!(attr_value("gen_ai.usage.output_tokens"), Some("0"));
    }

    #[test]
    fn test_generation_span_includes_native_response_id_when_present() {
        let mut turn = make_turn();
        turn.assistants[0].usage.response_id = Some("resp-native-1".to_string());

        let body = build_otel_export_request_for_turn(&turn, 1, "sess", "user", "1", None);
        let generation = span_named(&body, "chat gpt-5.4");
        assert_eq!(
            attr_string(span_attrs(generation), "gen_ai.response.id"),
            Some("resp-native-1")
        );
    }

    #[test]
    fn test_superseded_root_status_is_unset() {
        let mut turn = make_turn();
        turn.completed = false;
        turn.terminal_state = TurnTerminalState::Superseded;
        turn.completion_timestamp = "2026-03-28T08:00:10.000Z".to_string();

        let body = build_otel_export_request_for_turn(&turn, 1, "sess", "user", "1", None);
        let root = &exported_spans(&body)[0];
        assert_eq!(root["status"]["code"], 0);
    }

    #[test]
    fn native_pending_superseded_text_stays_on_root_without_generation_until_usage_arrives() {
        let pending_lines = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"old"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"old prompt"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"old output"}]}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"next"}}),
            json!({"timestamp":"2026-09-05T00:00:02.010Z","type":"event_msg","payload":{"type":"user_message","message":"next prompt"}}),
        ];
        let before_turns =
            crate::common::transcript::build_turns_with_usage_context(&pending_lines, None, true);
        let before = &before_turns[0];
        assert_eq!(
            before.assistants[0].invocation_end_source,
            Some(crate::common::transcript::InvocationEndSource::NativeUsagePending)
        );
        assert_eq!(before.semantic_assistant_count(), 0);

        let before_body = build_otel_export_request_for_turn(before, 1, "sess", "user", "1", None);
        let before_spans = exported_spans(&before_body);
        assert_eq!(before_spans.len(), 1);
        assert!(before_spans
            .iter()
            .all(|span| { attr_string(span_attrs(span), "gen_ai.span.kind") != Some("LLM") }));
        assert!(
            attr_string(span_attrs(&before_spans[0]), "gen_ai.output.messages")
                .unwrap()
                .contains("old output")
        );

        let mut complete_lines = pending_lines;
        complete_lines.push(json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"token_usage_record","payload":{"turn_id":"old","response_id":"resp-old-late","usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120}}}));
        let after_turns =
            crate::common::transcript::build_turns_with_usage_context(&complete_lines, None, true);
        let after = &after_turns[0];
        assert_eq!(after.assistants.len(), 1);
        assert_eq!(after.assistants[0].text, "old output");
        assert_eq!(
            after.assistants[0].usage.response_id.as_deref(),
            Some("resp-old-late")
        );

        let after_body = build_otel_export_request_for_turn(after, 1, "sess", "user", "1", None);
        let llm_spans = exported_spans(&after_body)
            .iter()
            .filter(|span| attr_string(span_attrs(span), "gen_ai.span.kind") == Some("LLM"))
            .collect::<Vec<_>>();
        assert_eq!(llm_spans.len(), 1);
        assert_eq!(
            attr_string(span_attrs(llm_spans[0]), "gen_ai.response.id"),
            Some("resp-old-late")
        );
    }

    #[test]
    fn mixed_completed_and_pending_native_messages_export_only_completed_generation() {
        let lines = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"old"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"old prompt"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"completed output"}]}}),
            json!({"timestamp":"2026-09-05T00:00:01.500Z","type":"token_usage_record","payload":{"turn_id":"old","response_id":"resp-completed","usage":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"pending output"}]}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"next"}}),
            json!({"timestamp":"2026-09-05T00:00:03.010Z","type":"event_msg","payload":{"type":"user_message","message":"next prompt"}}),
        ];
        let turns = crate::common::transcript::build_turns_with_usage_context(&lines, None, true);
        let old = &turns[0];
        assert_eq!(old.assistants.len(), 2);
        assert_eq!(old.semantic_assistant_count(), 1);
        assert!(!old.assistants[0].is_pending_generation());
        assert!(old.assistants[1].is_pending_generation());

        let body = build_otel_export_request_for_turn(old, 1, "sess", "user", "1", None);
        let spans = exported_spans(&body);
        let llm_spans = spans
            .iter()
            .filter(|span| attr_string(span_attrs(span), "gen_ai.span.kind") == Some("LLM"))
            .collect::<Vec<_>>();
        assert_eq!(llm_spans.len(), 1);
        assert_eq!(
            attr_string(span_attrs(llm_spans[0]), "gen_ai.response.id"),
            Some("resp-completed")
        );
        assert!(attr_string(span_attrs(&spans[0]), "gen_ai.output.messages")
            .unwrap()
            .contains("pending output"));
    }

    #[test]
    fn test_build_otel_export_request() {
        let config = LangfuseConfig {
            public_key: Some("pk-test".to_string()),
            secret_key: Some("sk-test".to_string()),
            host: "https://test.langfuse.com".to_string(),
            headers: None,
        };
        let body = build_otel_export_request(
            &[make_turn()],
            "sess-1",
            "tester",
            &config,
            "2026-03-28T08:00:11.000Z",
            "1.0.0",
            0,
        );

        let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .expect("spans should exist");
        assert_eq!(spans.len(), 4);
        assert_eq!(spans[0]["name"], "Turn 1");
        assert_eq!(spans[1]["name"], "chat gpt-5.4");
        assert_eq!(spans[2]["name"], "chat gpt-5.4");
        assert_eq!(spans[3]["name"], "tool Bash");

        let root_attrs = spans[0]["attributes"].as_array().expect("root attrs");
        let root_keys: Vec<&str> = root_attrs
            .iter()
            .filter_map(|attr| attr["key"].as_str())
            .collect();
        assert!(root_keys.contains(&"gen_ai.conversation.id"));
        assert!(root_keys.contains(&"gen_ai.chat.id"));
        assert_eq!(attr_string(root_attrs, "_internal.meta.root"), Some("1"));
        assert!(spans.iter().skip(1).all(|span| {
            span["attributes"].as_array().map_or(true, |attrs| {
                attr_string(attrs, "_internal.meta.root").is_none()
            })
        }));
        let root_trace_id = spans[0]["traceId"].as_str().expect("root traceId");
        let root_chat_id = root_attrs
            .iter()
            .find(|attr| attr["key"] == "gen_ai.chat.id")
            .and_then(|attr| attr["value"]["stringValue"].as_str());
        assert_eq!(root_chat_id, Some(root_trace_id));
        assert!(root_keys.contains(&"gen_ai.span.kind"));
        assert!(root_keys.contains(&"gen_ai.operation.name"));
        assert!(root_keys.contains(&"agentic.runtime.name"));
        assert!(root_keys.contains(&"agentic.turn.number"));
        assert!(root_keys.contains(&"ant.username"));
        assert!(!root_keys.contains(&"session.id"));
        assert!(!root_keys.contains(&"source"));
        assert!(!root_keys.contains(&"turn_number"));

        let tool_attrs = spans[3]["attributes"].as_array().expect("tool attrs");
        let tool_keys: Vec<&str> = tool_attrs
            .iter()
            .filter_map(|attr| attr["key"].as_str())
            .collect();
        assert!(tool_keys.contains(&"gen_ai.tool.call.id"));
        assert!(tool_keys.contains(&"gen_ai.tool.name"));
        assert!(tool_keys.contains(&"gen_ai.tool.call.arguments"));
        assert!(tool_keys.contains(&"gen_ai.tool.call.result"));
        assert!(tool_keys.contains(&"gen_ai.span.kind"));
        assert!(tool_keys.contains(&"ant.username"));
        assert!(!tool_keys.contains(&"tool.use_id"));
        assert!(!tool_keys.contains(&"langfuse.observation.input"));

        let resource_attrs = body["resourceSpans"][0]["resource"]["attributes"]
            .as_array()
            .expect("resource attrs");
        let resource_keys: Vec<&str> = resource_attrs
            .iter()
            .filter_map(|attr| attr["key"].as_str())
            .collect();
        assert!(resource_keys.contains(&"ant.agent.instance"));
        assert!(resource_keys.contains(&"ant.agent.product"));
        assert!(resource_keys.contains(&"ant.agent.type"));
        assert!(!resource_keys.contains(&"agent.instance"));
    }

    #[test]
    fn test_build_otel_export_request_uses_native_chat_id() {
        let config = LangfuseConfig {
            public_key: Some("pk-test".to_string()),
            secret_key: Some("sk-test".to_string()),
            host: "https://test.langfuse.com".to_string(),
            headers: None,
        };
        let mut turn = make_turn();
        turn.chat_id = Some("native-chat-1".to_string());

        let body = build_otel_export_request(
            &[turn],
            "sess-1",
            "tester",
            &config,
            "2026-03-28T08:00:11.000Z",
            "1.0.0",
            0,
        );

        let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
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
    fn test_upstream_context_and_deterministic_tool_span_survive_replay() {
        let trace_id = "0af7651916cd43dd8448eb211c80319c".to_string();
        let parent_span_id = "b7ad6b7169203331".to_string();
        let opts = TurnReplayOpts {
            trace_id: Some(trace_id.clone()),
            root_span_id: None,
            parent_span_id: Some(parent_span_id.clone()),
            sofa_traceid: Some("0a1b2c3d161716800000010012345".to_string()),
            sofa_rpcid: Some("0.1.2.3".to_string()),
            billing_mode: None,
            generation_span_ids: HashMap::new(),
            subagent_span_ids: HashMap::new(),
        };

        let body = build_otel_export_request_for_turn(
            &make_turn(),
            1,
            "sess-upstream",
            "tester",
            "1.0.0",
            Some(&opts),
        );
        let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .expect("spans");
        let root = spans
            .iter()
            .find(|span| span["name"] == "Turn 1")
            .expect("turn root");
        assert_eq!(root["traceId"], trace_id);
        assert_eq!(root["parentSpanId"], parent_span_id);
        let root_attrs = root["attributes"].as_array().expect("root attrs");
        assert_eq!(attr_string(root_attrs, "_internal.meta.root"), Some("1"));
        assert_eq!(
            attr_string(root_attrs, "_internal.sofa.trace_id"),
            Some("0a1b2c3d161716800000010012345")
        );
        assert_eq!(
            attr_string(root_attrs, "_internal.sofa.span_id"),
            Some("0.1.2.3")
        );

        let tool = spans
            .iter()
            .find(|span| {
                span["attributes"].as_array().is_some_and(|attrs| {
                    attr_string(attrs, "gen_ai.tool.call.id") == Some("call-1")
                })
            })
            .expect("tool span");
        assert_eq!(tool["traceId"], trace_id);
        assert_eq!(tool["spanId"], deterministic_tool_span_id("call-1"));
        assert!(spans
            .iter()
            .filter(|span| span["spanId"] != root["spanId"])
            .all(|span| {
                span["attributes"].as_array().map_or(true, |attrs| {
                    attr_string(attrs, "_internal.meta.root").is_none()
                })
            }));
    }

    #[test]
    fn finalizer_reuses_preallocated_generation_span_identity() {
        let opts = TurnReplayOpts {
            trace_id: Some("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".to_string()),
            root_span_id: Some("bbbbbbbbbbbbbbbb".to_string()),
            parent_span_id: None,
            sofa_traceid: None,
            sofa_rpcid: None,
            billing_mode: None,
            generation_span_ids: HashMap::from([(0, "cccccccccccccccc".to_string())]),
            subagent_span_ids: HashMap::new(),
        };

        let body = build_otel_export_request_for_turn(
            &make_turn(),
            1,
            "session-1",
            "tester",
            "1.0.0",
            Some(&opts),
        );
        let generation = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .unwrap()
            .iter()
            .find(|span| {
                span["attributes"].as_array().is_some_and(|attributes| {
                    attr_string(attributes, "gen_ai.span.kind") == Some("LLM")
                })
            })
            .expect("generation span");
        let root = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .unwrap()
            .iter()
            .find(|span| span["spanId"] == "bbbbbbbbbbbbbbbb")
            .expect("root span");

        assert_eq!(root["spanId"], "bbbbbbbbbbbbbbbb");
        assert_eq!(generation["spanId"], "cccccccccccccccc");
        assert_eq!(generation["parentSpanId"], root["spanId"]);
    }

    #[test]
    fn test_cdxhooks_otel_resource_attributes_are_merged_and_override_defaults() {
        let _guard = env_lock();
        let old_resource_attrs = std::env::var("CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES").ok();
        let old_meta_trigger = std::env::var("AIVISION_META_TRIGGER").ok();
        std::env::remove_var("AIVISION_META_TRIGGER");
        std::env::set_var(
            "CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES",
            "service.name=custom-cdxhooks,custom.cdxhooks.resource=resource-value",
        );

        let body =
            build_otel_export_request_for_turn(&make_turn(), 1, "sess-1", "tester", "1.0.0", None);
        let resource_attrs = body["resourceSpans"][0]["resource"]["attributes"]
            .as_array()
            .expect("resource attrs");
        let service_name = attr_string(resource_attrs, "service.name").map(ToString::to_string);
        let custom_resource =
            attr_string(resource_attrs, "custom.cdxhooks.resource").map(ToString::to_string);
        let meta_trigger = attr_string(resource_attrs, "ant.meta.trigger").map(ToString::to_string);
        let service_name_count = resource_attrs
            .iter()
            .filter(|attr| attr["key"].as_str() == Some("service.name"))
            .count();
        let meta_trigger_count = resource_attrs
            .iter()
            .filter(|attr| attr["key"].as_str() == Some("ant.meta.trigger"))
            .count();

        restore_env("CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES", old_resource_attrs);
        restore_env("AIVISION_META_TRIGGER", old_meta_trigger);

        assert_eq!(service_name.as_deref(), Some("custom-cdxhooks"));
        assert_eq!(custom_resource.as_deref(), Some("resource-value"));
        assert_eq!(meta_trigger.as_deref(), Some(""));
        assert_eq!(
            service_name_count, 1,
            "resource override must not duplicate keys"
        );
        assert_eq!(
            meta_trigger_count, 1,
            "cdxhooks resource opt-in must emit one ant.meta.trigger"
        );
    }

    #[test]
    fn test_cdxhooks_trace_resource_meta_trigger_uses_dedicated_then_component_fallback() {
        let _guard = env_lock();
        let old_resource_attrs = std::env::var("CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES").ok();
        let old_meta_trigger = std::env::var("AIVISION_META_TRIGGER").ok();

        std::env::set_var("AIVISION_META_TRIGGER", "interactive");
        std::env::set_var(
            "CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES",
            "ant.meta.trigger=automation",
        );
        let dedicated_body =
            build_otel_export_request_for_turn(&make_turn(), 1, "sess-1", "tester", "1.0.0", None);
        let dedicated_attrs = resource_attrs(&dedicated_body);
        let dedicated_trigger = trigger_values(dedicated_attrs);

        std::env::remove_var("AIVISION_META_TRIGGER");
        std::env::set_var(
            "CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES",
            "foo=1,ant.meta.trigger=interactive,ant.meta.trigger=automation",
        );
        let fallback_body =
            build_otel_export_request_for_turn(&make_turn(), 1, "sess-1", "tester", "1.0.0", None);
        let fallback_attrs = resource_attrs(&fallback_body);
        let fallback_trigger = trigger_values(fallback_attrs);

        restore_env("CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES", old_resource_attrs);
        restore_env("AIVISION_META_TRIGGER", old_meta_trigger);

        assert_eq!(dedicated_trigger, vec!["interactive"]);
        assert_eq!(fallback_trigger, vec!["automation"]);
        assert_eq!(
            attr_string(fallback_attrs, "telemetry.sdk.name"),
            Some("cdxhooks")
        );
    }

    #[test]
    fn test_cdxhooks_trace_resource_meta_trigger_invalid_dedicated_blocks_fallback() {
        let _guard = env_lock();
        let old_resource_attrs = std::env::var("CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES").ok();
        let old_meta_trigger = std::env::var("AIVISION_META_TRIGGER").ok();

        std::env::set_var("AIVISION_META_TRIGGER", "scheduled");
        std::env::set_var(
            "CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES",
            "ant.meta.trigger=automation",
        );
        let body =
            build_otel_export_request_for_turn(&make_turn(), 1, "sess-1", "tester", "1.0.0", None);
        let trigger = trigger_values(resource_attrs(&body));

        restore_env("CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES", old_resource_attrs);
        restore_env("AIVISION_META_TRIGGER", old_meta_trigger);

        assert_eq!(trigger, vec!["unknown"]);
    }

    #[test]
    fn test_cdxhooks_otel_attributes_are_added_to_exported_spans() {
        let _guard = env_lock();
        let old_span_attrs = std::env::var("CDXHOOKS_OTEL_ATTRIBUTES").ok();
        std::env::set_var(
            "CDXHOOKS_OTEL_ATTRIBUTES",
            "custom.cdxhooks.span=span-value,username=otel-user,_internal.meta.root=spoofed",
        );

        let body = build_otel_export_request_for_turn(
            &make_turn(),
            1,
            "sess-1",
            "resolved-user",
            "1.0.0",
            None,
        );
        let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
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
                let custom = attr_string(attrs, "custom.cdxhooks.span").map(ToString::to_string);
                let username = attr_string(attrs, "ant.username").map(ToString::to_string);
                assert!(attr_string(attrs, "username").is_none());
                let username_count = attrs
                    .iter()
                    .filter(|attr| attr["key"].as_str() == Some("ant.username"))
                    .count();
                (custom, username, username_count)
            })
            .collect();

        restore_env("CDXHOOKS_OTEL_ATTRIBUTES", old_span_attrs);

        assert_eq!(span_checks.len(), 4);
        for (custom, username, username_count) in span_checks {
            assert_eq!(custom.as_deref(), Some("span-value"));
            assert_eq!(username.as_deref(), Some("resolved-user"));
            assert_eq!(
                username_count, 1,
                "CDXHOOKS_OTEL_ATTRIBUTES must not duplicate reserved span keys"
            );
        }
    }

    #[test]
    fn test_cdxhooks_otel_attributes_are_bounded_before_span_append() {
        let _guard = env_lock();
        let old_span_attrs = std::env::var("CDXHOOKS_OTEL_ATTRIBUTES").ok();
        let mut configured = vec![
            format!(
                "{}=ignored",
                "k".repeat(MAX_CDXHOOKS_OTEL_ATTRIBUTE_KEY_BYTES + 1)
            ),
            format!("custom.long={}", "é".repeat(3_000)),
        ];
        configured.extend((0..70).map(|index| format!("custom.{index}=value")));
        std::env::set_var("CDXHOOKS_OTEL_ATTRIBUTES", configured.join(","));

        let pairs = cdxhooks_otel_span_attr_pairs();

        restore_env("CDXHOOKS_OTEL_ATTRIBUTES", old_span_attrs);
        assert_eq!(pairs.len(), MAX_CDXHOOKS_OTEL_ATTRIBUTES);
        assert!(pairs.iter().all(|(key, value)| key.len()
            <= MAX_CDXHOOKS_OTEL_ATTRIBUTE_KEY_BYTES
            && value.len() <= MAX_CDXHOOKS_OTEL_ATTRIBUTE_VALUE_BYTES));
        assert_eq!(
            pairs
                .iter()
                .find(|(key, _)| key == "custom.long")
                .map(|(_, value)| value.len()),
            Some(MAX_CDXHOOKS_OTEL_ATTRIBUTE_VALUE_BYTES)
        );
    }

    #[test]
    fn test_resolve_trace_jsonl_path_honors_log_override_and_is_session_owned() {
        let _guard = env_lock();
        let old_log_dir = std::env::var("CDXHOOKS_LOG_DIR").ok();

        std::env::set_var("CDXHOOKS_LOG_DIR", "/tmp/cdxhooks-logs");

        let key = crate::common::dirs::session_key("session/a").unwrap();
        let path = resolve_trace_jsonl_path("session/a").unwrap();
        assert_eq!(
            path,
            std::path::Path::new("/tmp/cdxhooks-logs")
                .join("sessions")
                .join(key)
                .join("otel.jsonl")
        );
        assert!(resolve_trace_jsonl_path("").is_none());

        restore_env("CDXHOOKS_LOG_DIR", old_log_dir);
    }

    #[test]
    fn test_append_trace_jsonl_if_debug_appends_line() {
        let _guard = env_lock();
        let old_debug = std::env::var("CDXHOOKS_DEBUG").ok();
        let old_plugin_root = std::env::var("CODEX_PLUGIN_ROOT").ok();
        let old_log_dir = std::env::var("CDXHOOKS_LOG_DIR").ok();

        let unique = format!(
            "cdxhooks-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        );
        let plugin_root = std::env::temp_dir().join(unique);
        let session_id = "debug/session";
        let jsonl_path = plugin_root
            .join("logs")
            .join("sessions")
            .join(crate::common::dirs::session_key(session_id).unwrap())
            .join("otel.jsonl");

        std::env::set_var("CDXHOOKS_DEBUG", "true");
        std::env::set_var("CODEX_PLUGIN_ROOT", plugin_root.to_string_lossy().as_ref());
        std::env::set_var(
            "CDXHOOKS_LOG_DIR",
            plugin_root.join("logs").to_string_lossy().as_ref(),
        );

        let logger = crate::common::logger::Logger::new(plugin_root.join("cdxhooks.log"), true);
        let body = |span_name: &str| {
            json!({
                "resourceSpans": [{
                    "scopeSpans": [{
                        "spans": [{
                            "name": span_name,
                            "attributes": [{
                                "key": "gen_ai.conversation.id",
                                "value": {"stringValue": session_id}
                            }]
                        }]
                    }]
                }]
            })
        };
        append_trace_jsonl_if_debug(&body("first"), &logger);
        append_trace_jsonl_if_debug(&body("second"), &logger);

        let data = fs::read_to_string(&jsonl_path).expect("jsonl file should exist");
        let lines: Vec<&str> = data.lines().collect();
        assert_eq!(lines.len(), 2);
        assert_eq!(
            serde_json::from_str::<Value>(lines[0]).unwrap(),
            body("first")
        );
        assert_eq!(
            serde_json::from_str::<Value>(lines[1]).unwrap(),
            body("second")
        );
        assert!(!plugin_root.join("logs").join("otel.jsonl").exists());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(&jsonl_path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }

        let _ = fs::remove_file(plugin_root.join("cdxhooks.log"));
        let _ = fs::remove_dir_all(&plugin_root);

        restore_env("CDXHOOKS_DEBUG", old_debug);
        restore_env("CODEX_PLUGIN_ROOT", old_plugin_root);
        restore_env("CDXHOOKS_LOG_DIR", old_log_dir);
    }

    fn restore_env(key: &str, value: Option<String>) {
        if let Some(value) = value {
            std::env::set_var(key, value);
        } else {
            std::env::remove_var(key);
        }
    }

    fn env_lock() -> std::sync::MutexGuard<'static, ()> {
        crate::test_sync::env_lock()
    }

    fn attr_string<'a>(attrs: &'a [Value], key: &str) -> Option<&'a str> {
        attrs
            .iter()
            .find(|attr| attr["key"].as_str() == Some(key))
            .and_then(|attr| attr["value"]["stringValue"].as_str())
    }

    fn resource_attrs(body: &Value) -> &[Value] {
        body["resourceSpans"][0]["resource"]["attributes"]
            .as_array()
            .expect("resource attrs")
    }

    fn trigger_values(attrs: &[Value]) -> Vec<&str> {
        attrs
            .iter()
            .filter(|attr| attr["key"].as_str() == Some("ant.meta.trigger"))
            .filter_map(|attr| attr["value"]["stringValue"].as_str())
            .collect()
    }

    fn attr_int(attrs: &[Value], key: &str) -> Option<i64> {
        attrs
            .iter()
            .find(|attr| attr["key"].as_str() == Some(key))
            .and_then(|attr| {
                attr["value"]["intValue"]
                    .as_i64()
                    .or_else(|| {
                        attr["value"]["intValue"]
                            .as_str()
                            .and_then(|value| value.parse().ok())
                    })
                    .or_else(|| {
                        attr["value"]["stringValue"]
                            .as_str()
                            .and_then(|value| value.parse().ok())
                    })
            })
    }

    // --- parse_agentic_tool tests ---

    #[test]
    fn test_parse_agentic_tool_mcp_normal() {
        let info = parse_agentic_tool(
            "mcp__openaiDeveloperDocs__search_openai_docs",
            "call_123",
            None,
        );
        assert_eq!(info.tool_type, "mcp");
        assert_eq!(info.name, "openaiDeveloperDocs.search_openai_docs");
        assert_eq!(info.id, "call_123");
    }

    #[test]
    fn test_parse_agentic_tool_mcp_no_method() {
        // mcp__servername with no __ separator after server
        let info = parse_agentic_tool("mcp__code_search", "call_456", None);
        assert_eq!(info.tool_type, "mcp");
        assert_eq!(info.name, "code_search");
        assert_eq!(info.id, "call_456");
    }

    #[test]
    fn test_parse_agentic_tool_mcp_bare_prefix() {
        // bare "mcp__" with nothing after — fallback to builtin
        let info = parse_agentic_tool("mcp__", "call_789", None);
        assert_eq!(info.tool_type, "builtin");
        assert_eq!(info.name, "mcp__");
    }

    #[test]
    fn test_parse_agentic_tool_mcp_multiple_underscores() {
        // mcp__plugin_oh-my-claudecode_t__ast_grep_search
        // first __ after stripping mcp__ → server = "plugin_oh-my-claudecode_t", method = "ast_grep_search"
        let info = parse_agentic_tool(
            "mcp__plugin_oh-my-claudecode_t__ast_grep_search",
            "call_x",
            None,
        );
        assert_eq!(info.tool_type, "mcp");
        assert_eq!(info.name, "plugin_oh-my-claudecode_t.ast_grep_search");
    }

    #[test]
    fn test_parse_agentic_tool_skill() {
        let info = parse_agentic_tool("Skill", "call_s1", Some("commit"));
        assert_eq!(info.tool_type, "skill");
        assert_eq!(info.name, "commit");
        assert_eq!(info.id, "call_s1");
    }

    #[test]
    fn test_parse_agentic_tool_skill_no_name() {
        let info = parse_agentic_tool("Skill", "call_s2", None);
        assert_eq!(info.tool_type, "skill");
        assert_eq!(info.name, "unknown");
    }

    #[test]
    fn test_parse_agentic_tool_skill_empty_id() {
        let info = parse_agentic_tool("Skill", "", Some("review-pr"));
        assert_eq!(info.tool_type, "skill");
        assert_eq!(info.id, "skill-review-pr");
    }

    #[test]
    fn test_parse_agentic_tool_builtin() {
        let info = parse_agentic_tool("Bash", "call_b1", None);
        assert_eq!(info.tool_type, "builtin");
        assert_eq!(info.name, "Bash");
    }

    #[test]
    fn test_parse_agentic_tool_empty_name() {
        let info = parse_agentic_tool("", "call_e1", None);
        assert_eq!(info.tool_type, "builtin");
        assert_eq!(info.name, "unknown");
    }

    // --- span name & attribute integration tests ---

    #[test]
    fn test_mcp_tool_span_name_and_attrs() {
        let turn = make_turn();
        let tool_call = ToolCall {
            call_id: "call_mcp1".to_string(),
            name: "mcp__code_search__query_code_with_keyword".to_string(),
            input: json!({
                "pattern": "hello",
                "command": "node /Users/testuser/.codex/skills/eve-beta/scripts/query.js"
            }),
            output: json!("Found 3 matches"),
            started_timestamp: "2026-03-28T08:00:01.000Z".to_string(),
            ended_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
            assistant_index: 0,
            success: None,
        };
        let now = "1711612800000000000";
        let span = build_tool_span(
            "trace1",
            "parent1",
            &tool_call,
            &turn,
            now,
            "100001",
            "test-session",
            "chat-1",
            None,
        );
        let span = serde_json::to_value(&span).unwrap();

        // Verify span name
        assert_eq!(span["name"], "mcp code_search.query_code_with_keyword");

        // Verify agentic attrs present
        let attrs = span["attributes"].as_array().unwrap();
        let find_attr = |key: &str| -> Option<String> {
            attrs
                .iter()
                .find(|a| a["key"] == key)
                .map(|a| a["value"]["stringValue"].as_str().unwrap().to_string())
        };
        assert_eq!(find_attr("agentic.tool.type").as_deref(), Some("mcp"));
        assert_eq!(
            find_attr("agentic.tool.name").as_deref(),
            Some("code_search.query_code_with_keyword")
        );
        assert_eq!(find_attr("agentic.tool.id").as_deref(), Some("call_mcp1"));

        // gen_ai.tool.name has prefix (display name)
        assert_eq!(
            find_attr("gen_ai.tool.name").as_deref(),
            Some("mcp code_search.query_code_with_keyword")
        );
    }

    #[test]
    fn test_builtin_tool_span_no_agentic_attrs() {
        let turn = make_turn();
        let tool_call = ToolCall {
            call_id: "call_b1".to_string(),
            name: "Bash".to_string(),
            input: json!({"command": "ls"}),
            output: json!("file.txt"),
            started_timestamp: "2026-03-28T08:00:01.000Z".to_string(),
            ended_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
            assistant_index: 0,
            success: None,
        };
        let now = "1711612800000000000";
        let span = build_tool_span(
            "trace1",
            "parent1",
            &tool_call,
            &turn,
            now,
            "100001",
            "test-session",
            "chat-1",
            None,
        );
        let span = serde_json::to_value(&span).unwrap();

        assert_eq!(span["name"], "tool Bash");

        let attrs = span["attributes"].as_array().unwrap();
        let find_attr = |key: &str| -> Option<String> {
            attrs
                .iter()
                .find(|a| a["key"] == key)
                .map(|a| a["value"]["stringValue"].as_str().unwrap().to_string())
        };

        // gen_ai.tool.name has prefix (display name)
        assert_eq!(find_attr("gen_ai.tool.name").as_deref(), Some("tool Bash"));

        assert!(
            !attrs.iter().any(|a| a["key"] == "agentic.tool.type"),
            "builtin tool should not have agentic.tool.type"
        );
    }

    #[test]
    fn cancelled_tool_output_emits_error_status_and_cancelled_type() {
        let turn = make_turn();
        let tool = ToolCall {
            call_id: "call-cancelled".to_string(),
            name: "exec".to_string(),
            input: json!({"command": "sleep 30"}),
            output: json!("aborted by user after 0.1s"),
            started_timestamp: "2026-03-28T08:00:01.000Z".to_string(),
            ended_timestamp: "2026-03-28T08:00:01.100Z".to_string(),
            assistant_index: 0,
            success: None,
        };
        let span = build_tool_span(
            "0123456789abcdef0123456789abcdef",
            "0123456789abcdef",
            &tool,
            &turn,
            "1000000000",
            "tester",
            "session-1",
            "chat-1",
            None,
        );
        let span = serde_json::to_value(&span).unwrap();

        assert_eq!(span["status"]["code"], 2);
        let attrs = span["attributes"].as_array().unwrap();
        assert_eq!(attr_string(attrs, "error.type"), Some("cancelled"));
    }

    #[test]
    fn test_turn_skill_name_does_not_reclassify_builtin_tool_span() {
        let mut turn = make_turn();
        turn.skill_name = Some("eve-beta".to_string());

        let tool_call = ToolCall {
            call_id: "call-s1".to_string(),
            name: "Bash".to_string(),
            input: json!({"command": "ls"}),
            output: json!("result"),
            started_timestamp: "2026-03-28T08:00:01.000Z".to_string(),
            ended_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
            assistant_index: 0,
            success: None,
        };
        let now = "1711612800000000000";
        let span = build_tool_span(
            "trace1",
            "parent1",
            &tool_call,
            &turn,
            now,
            "100001",
            "test-session",
            "chat-1",
            None,
        );
        let span = serde_json::to_value(&span).unwrap();

        assert_eq!(span["name"], "tool Bash");

        let attrs = span["attributes"].as_array().unwrap();
        let find_attr = |key: &str| -> Option<String> {
            attrs
                .iter()
                .find(|a| a["key"] == key)
                .map(|a| a["value"]["stringValue"].as_str().unwrap().to_string())
        };
        assert_eq!(find_attr("gen_ai.tool.name").as_deref(), Some("tool Bash"));
        assert!(
            !attrs.iter().any(|a| a["key"] == "agentic.tool.type"),
            "turn-level skill hints must not turn ordinary tools into skill spans"
        );
        assert_eq!(find_attr("agentic.tool.raw_name").as_deref(), Some("Bash"));
    }

    #[test]
    fn test_skill_tool_span_from_tool_command_path() {
        let turn = make_turn();

        let tool_call = ToolCall {
            call_id: "call-s1".to_string(),
            name: "Bash".to_string(),
            input: json!({"command": "node /Users/testuser/.codex/skills/eve-beta/scripts/query.js"}),
            output: json!("result"),
            started_timestamp: "2026-03-28T08:00:01.000Z".to_string(),
            ended_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
            assistant_index: 0,
            success: None,
        };
        let now = "1711612800000000000";
        let span = build_tool_span(
            "trace1",
            "parent1",
            &tool_call,
            &turn,
            now,
            "100001",
            "test-session",
            "chat-1",
            None,
        );
        let span = serde_json::to_value(&span).unwrap();

        assert_eq!(span["name"], "skill eve-beta");

        let attrs = span["attributes"].as_array().unwrap();
        let find_attr = |key: &str| -> Option<String> {
            attrs
                .iter()
                .find(|a| a["key"] == key)
                .map(|a| a["value"]["stringValue"].as_str().unwrap().to_string())
        };
        assert_eq!(find_attr("agentic.tool.type").as_deref(), Some("skill"));
        assert_eq!(find_attr("agentic.tool.name").as_deref(), Some("eve-beta"));
        assert_eq!(
            find_attr("gen_ai.tool.name").as_deref(),
            Some("skill eve-beta")
        );
        assert_eq!(find_attr("agentic.tool.raw_name").as_deref(), Some("Bash"));
    }

    #[test]
    fn test_codex_skill_read_uses_its_own_outcome_in_both_collection_modes() {
        let path = "/private/probe-root/.codex/skills/missing-probe/SKILL.md";
        let command = format!("cat {path}");
        let failure = format!("cat: {path}: No such file or directory");
        for (output, expected_status) in [
            (format!("Exit code: 1\nOutput:\n{failure}"), 2),
            (format!("Chunk ID: 8a50\nWall time: 0.001 seconds\nProcess exited with code 1\nOriginal token count: 9\nOutput:\n{failure}"), 2),
            ("Exit code: 0\nOutput:\nExample error text: Exit code: 1".to_string(), 1),
        ] {
            let lines = vec![
                json!({"timestamp":"2026-11-27T08:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-probe"}}),
                json!({"timestamp":"2026-11-27T08:00:01.000Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","call_id":"read-probe","arguments":json!({"cmd":command}).to_string()}}),
                json!({"timestamp":"2026-11-27T08:00:02.000Z","type":"response_item","payload":{"type":"function_call_output","call_id":"read-probe","output":output}}),
                json!({"timestamp":"2026-11-27T08:00:03.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-probe","last_agent_message":"The file could not be read."}}),
            ];
            let turns = crate::common::transcript::build_turns(&lines);
            let turn = turns.last().expect("probe turn");
            assert!(turn.completed);
            for privacy in [false, true] {
                let _mode = TestPrivacyModeGuard::set(privacy);
                let body = build_otel_export_request_for_turn(turn, 1, "sess", "user", "1", None);
                assert_eq!(
                    exported_spans(&body)
                        .iter()
                        .filter(|span| {
                            attr_string(span_attrs(span), "agentic.tool.name") == Some("missing-probe")
                        })
                        .count(),
                    1
                );
                let span = exported_spans(&body).iter()
                    .find(|span| attr_string(span_attrs(span), "gen_ai.tool.call.id") == Some("read-probe"))
                    .expect("physical failed read span");
                assert_eq!(span["status"]["code"], expected_status);
                let attrs = span_attrs(span);
                assert_eq!(
                    attr_string(attrs, "error.type"),
                    (expected_status == 2).then_some("tool_error")
                );
                assert_eq!(attr_string(attrs, "agentic.tool.type"), Some("skill"));
                assert_eq!(attr_string(attrs, "agentic.tool.name"), Some("missing-probe"));
                assert_eq!(attr_string(attrs, "agentic.tool.raw_name"), Some("Bash"));
                assert!(attrs.iter().any(|attr| attr["key"] == "agentic.tool.derived"
                    && attr["value"]["boolValue"] == true));
                if privacy {
                    assert!(!body.to_string().contains(path));
                    assert!(!body.to_string().contains(&failure));
                    let result = attr_string(attrs, "gen_ai.tool.call.result").unwrap();
                    assert_eq!(result.trim_matches('"'), "[REDACTED]");
                }
            }
        }
    }

    #[test]
    fn test_codex_reads_keep_individual_outcomes_alongside_an_explicit_invocation() {
        let _mode = TestPrivacyModeGuard::set(true);
        let mut turn = make_turn();
        let path = "/private/probe-root/.agents/skills/demo/SKILL.md";
        let mut successful_read = turn.tool_calls[0].clone();
        successful_read.call_id = "first-read".to_string();
        successful_read.input = json!({"command": format!("cat {path}")});
        successful_read.output = json!("Exit code: 0\nOutput:\ncontent");

        let mut failed_read = successful_read.clone();
        failed_read.call_id = "second-read".to_string();
        failed_read.input = json!({"command":["/bin/zsh","-lc", format!("cat {path}")]});
        failed_read.output = json!("cat: No such file or directory");
        failed_read.success = Some(false);
        turn.tool_calls = vec![successful_read, failed_read];

        for explicit in [false, true] {
            if explicit {
                turn.skill_invocations.push(skill_invocation(
                    "demo", 0, "2026-03-28T08:00:01.000Z", Some(0), Some(5),
                ));
            }
            let body = build_otel_export_request_for_turn(&turn, 1, "sess", "user", "1", None);
            let mut native = 0;
            let mut reads = Vec::new();
            for span in exported_spans(&body) {
                let attrs = span_attrs(span);
                if attr_string(attrs, "agentic.tool.name") != Some("demo") {
                    continue;
                }
                if attr_string(attrs, "agentic.tool.raw_name") == Some("Skill") {
                    native += 1;
                    assert_eq!(span["status"]["code"], 1);
                } else {
                    reads.push((
                        attr_string(attrs, "gen_ai.tool.call.id").unwrap(),
                        span["status"]["code"].as_u64().unwrap(),
                    ));
                    assert!(attrs.iter().any(|attr| attr["key"] == "agentic.tool.derived"
                        && attr["value"]["boolValue"] == true));
                }
            }
            assert_eq!(native, usize::from(explicit));
            if explicit {
                assert_eq!(reads, [("second-read", 2)]);
            } else {
                assert_eq!(reads, [("first-read", 1), ("second-read", 2)]);
            }
            assert!(!body.to_string().contains(path));
        }
    }

    #[test]
    fn test_codex_shell_evidence_applies_only_to_the_execution_and_not_stdout() {
        let _mode = TestPrivacyModeGuard::set(true);
        let mut turn = make_turn();
        let mut tool = turn.tool_calls[0].clone();
        let path = "/private/probe-root/.codex/skills/demo/SKILL.md";
        for (name, command, output, expected_status, derived) in [
            ("mcp__demo__check", format!("cat {path}"), "Exit code: 1\nOutput:\ntext", 1, false),
            ("Bash", format!("cat {path}"), "Output:\nExit code: 1", 0, true),
            ("Bash", format!("cat {path}"), "Exit code: 1", 0, true),
            ("Bash", format!("cat {path}"), "Process exited with code 1", 0, true),
            ("Bash", format!("cat README.md; echo {path}"), "Exit code: 1\nOutput:\ntext", 2, false),
            ("Bash", format!("cat {path} another-file"), "Exit code: 1\nOutput:\ntext", 2, false),
            ("Bash", format!("cat -- {path} -another-file"), "Exit code: 1\nOutput:\ntext", 2, false),
            ("Bash", format!("cat > {path}"), "Exit code: 1\nOutput:\ntext", 2, false),
            ("Bash", "node /tmp/.codex/skills/demo/scripts/query.js".to_string(), "Exit code: 1\nOutput:\ntext", 2, false),
        ] {
            tool.name = name.to_string();
            tool.input = json!({"command":command});
            tool.output = json!(output);
            turn.tool_calls[0] = tool.clone();
            let body = build_otel_export_request_for_turn(&turn, 1, "sess", "user", "1", None);
            let span = exported_spans(&body).iter()
                .find(|span| attr_string(span_attrs(span), "gen_ai.tool.call.id") == Some("call-1"))
                .expect("physical tool span");
            assert_eq!(span["status"]["code"], expected_status);
            assert_eq!(span_attrs(span).iter().any(|attr| attr["key"] == "agentic.tool.derived"), derived);
        }
    }

    #[test]
    fn test_skill_tool_span_from_skill_tool_input_field() {
        let turn = make_turn();

        let tool_call = ToolCall {
            call_id: "call-s2".to_string(),
            name: "Skill".to_string(),
            input: json!({"skill": "publisher:plugin:init"}),
            output: json!("started"),
            started_timestamp: "2026-03-28T08:00:01.000Z".to_string(),
            ended_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
            assistant_index: 0,
            success: None,
        };
        let now = "1711612800000000000";
        let span = build_tool_span(
            "trace1",
            "parent1",
            &tool_call,
            &turn,
            now,
            "100001",
            "test-session",
            "chat-1",
            None,
        );
        let span = serde_json::to_value(&span).unwrap();

        assert_eq!(span["name"], "skill plugin:init");

        let attrs = span["attributes"].as_array().unwrap();
        let find_attr = |key: &str| -> Option<String> {
            attrs
                .iter()
                .find(|a| a["key"] == key)
                .map(|a| a["value"]["stringValue"].as_str().unwrap().to_string())
        };
        assert_eq!(find_attr("agentic.tool.type").as_deref(), Some("skill"));
        assert_eq!(
            find_attr("agentic.tool.name").as_deref(),
            Some("plugin:init")
        );
        assert_eq!(find_attr("agentic.tool.raw_name").as_deref(), Some("Skill"));
    }

    #[test]
    fn test_turn_skill_hint_does_not_create_repeated_skill_spans() {
        let mut turn = make_turn();
        turn.skill_name = Some("ralplan".to_string());
        turn.tool_calls = vec![
            ToolCall {
                call_id: "call-b1".to_string(),
                name: "Bash".to_string(),
                input: json!({"command": "rg foo"}),
                output: json!("match"),
                started_timestamp: "2026-03-28T08:00:01.000Z".to_string(),
                ended_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
                assistant_index: 0,
                success: None,
            },
            ToolCall {
                call_id: "call-b2".to_string(),
                name: "Bash".to_string(),
                input: json!({"command": "sed -n '1,20p' README.md"}),
                output: json!("content"),
                started_timestamp: "2026-03-28T08:00:03.000Z".to_string(),
                ended_timestamp: "2026-03-28T08:00:04.000Z".to_string(),
                assistant_index: 0,
                success: None,
            },
            ToolCall {
                call_id: "call-m1".to_string(),
                name: "mcp__code_search__query_code_with_keyword".to_string(),
                input: json!({"pattern": "ralplan"}),
                output: json!("found"),
                started_timestamp: "2026-03-28T08:00:05.000Z".to_string(),
                ended_timestamp: "2026-03-28T08:00:06.000Z".to_string(),
                assistant_index: 0,
                success: None,
            },
        ];

        let body = build_otel_export_request_for_turn(&turn, 1, "sess-1", "tester", "1.0.0", None);
        let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .expect("spans should exist");

        let skill_ralplan_spans = spans
            .iter()
            .filter(|span| span["name"].as_str() == Some("skill ralplan"))
            .count();
        assert_eq!(
            skill_ralplan_spans, 0,
            "turn-level skill hints must not create repeated skill ralplan spans"
        );
    }

    #[test]
    fn test_structured_skill_invocation_builds_sibling_tool_span_with_stable_fields() {
        let mut turn = make_turn();
        turn.skill_invocations = vec![skill_invocation(
            "analyze",
            0,
            "2026-03-28T08:00:00.000Z",
            Some(0),
            Some(8),
        )];
        turn.completion_timestamp = "2026-03-28T08:00:12.000Z".to_string();

        let body = build_otel_export_request_for_turn(&turn, 1, "sess-1", "tester", "1.0.0", None);
        let root = &exported_spans(&body)[0];
        let skill = span_named(&body, "skill analyze");
        let attrs = span_attrs(skill);

        assert_eq!(skill["parentSpanId"], root["spanId"]);
        assert_eq!(skill["status"]["code"], 1);
        assert_eq!(
            root["endTimeUnixNano"],
            iso_to_nanos(&turn.completion_timestamp)
        );
        assert_eq!(attr_string(attrs, "gen_ai.span.kind"), Some("TOOL"));
        assert_eq!(
            attr_string(attrs, "gen_ai.operation.name"),
            Some("execute_tool")
        );
        assert_eq!(
            attr_string(attrs, "gen_ai.tool.name"),
            Some("skill analyze")
        );
        assert_eq!(attr_string(attrs, "agentic.tool.type"), Some("skill"));
        assert_eq!(attr_string(attrs, "agentic.tool.name"), Some("analyze"));
        assert_eq!(attr_string(attrs, "agentic.tool.raw_name"), Some("Skill"));
        assert_eq!(
            attr_string(attrs, "agentic.tool.id"),
            attr_string(attrs, "gen_ai.tool.call.id")
        );
        assert_eq!(
            attr_string(attrs, "gen_ai.tool.call.result"),
            Some("Diagnostics complete")
        );
        let args: Value =
            serde_json::from_str(attr_string(attrs, "gen_ai.tool.call.arguments").unwrap())
                .expect("skill args json");
        assert_eq!(args["skill"], "analyze");
        assert_eq!(args["source"], "codex_text_element");
        assert_eq!(args["byte_range"], json!({"start":0,"end":8}));

        let start = skill["startTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse::<u128>()
            .unwrap();
        let end = skill["endTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse::<u128>()
            .unwrap();
        let root_start = root["startTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse::<u128>()
            .unwrap();
        let root_end = root["endTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse::<u128>()
            .unwrap();
        assert!(start > root_start);
        assert!(end <= root_end);
        let expected_duration = ((end - start) / 1_000_000).to_string();
        assert_eq!(
            attr_string(attrs, "agentic.tool.duration_ms"),
            Some(expected_duration.as_str())
        );

        let bash = span_named(&body, "tool Bash");
        assert_eq!(attr_string(span_attrs(bash), "agentic.tool.type"), None);
    }

    #[test]
    fn test_skill_invocation_status_and_missing_result_follow_terminal_state() {
        for (state, expected_code, expected_error, expected_result) in [
            (
                TurnTerminalState::Completed,
                1,
                None,
                json!({"status":"completed","result_missing":true}),
            ),
            (
                TurnTerminalState::HeuristicComplete,
                1,
                None,
                json!({"status":"completed","result_missing":true}),
            ),
            (
                TurnTerminalState::Aborted,
                2,
                Some("skill_aborted"),
                json!({"status":"aborted"}),
            ),
            (
                TurnTerminalState::Superseded,
                0,
                None,
                json!({"status":"superseded","result_missing":true}),
            ),
            (
                TurnTerminalState::Open,
                0,
                None,
                json!({"status":"open","result_missing":true}),
            ),
        ] {
            let mut turn = make_turn();
            turn.assistants.clear();
            turn.tool_calls.clear();
            turn.terminal_state = state;
            turn.completed = state == TurnTerminalState::Completed;
            turn.completion_timestamp = if state == TurnTerminalState::Open {
                String::new()
            } else {
                "2026-03-28T08:00:04.000Z".to_string()
            };
            turn.skill_invocations = vec![skill_invocation(
                "analyze",
                0,
                "2026-03-28T08:00:01.000Z",
                Some(0),
                Some(8),
            )];

            let body = build_otel_export_request_for_turn(&turn, 1, "sess", "user", "1", None);
            let skill = span_named(&body, "skill analyze");
            let attrs = span_attrs(skill);
            assert_eq!(skill["status"]["code"], expected_code);
            assert_eq!(attr_string(attrs, "error.type"), expected_error);
            let result: Value =
                serde_json::from_str(attr_string(attrs, "gen_ai.tool.call.result").unwrap())
                    .expect("structured missing result");
            assert_eq!(result, expected_result);
        }
    }

    #[test]
    fn test_zero_length_root_keeps_skill_window_contained_and_duration_consistent() {
        let mut turn = make_turn();
        turn.user_timestamp = "2026-03-28T08:00:00.000Z".to_string();
        turn.assistants.clear();
        turn.tool_calls.clear();
        turn.completion_timestamp = turn.user_timestamp.clone();
        turn.skill_invocations = vec![skill_invocation(
            "analyze",
            0,
            "2026-03-28T08:00:00.000Z",
            Some(0),
            Some(8),
        )];

        let body = build_otel_export_request_for_turn(&turn, 1, "sess", "user", "1", None);
        let root = &exported_spans(&body)[0];
        let skill = span_named(&body, "skill analyze");
        assert_eq!(skill["startTimeUnixNano"], root["startTimeUnixNano"]);
        assert_eq!(skill["endTimeUnixNano"], root["endTimeUnixNano"]);
        assert_eq!(
            attr_string(span_attrs(skill), "agentic.tool.duration_ms"),
            Some("0")
        );
    }

    #[test]
    fn test_skill_invocation_id_is_stable_across_open_and_completed_replay() {
        let mut open = make_turn();
        open.assistants.clear();
        open.tool_calls.clear();
        open.terminal_state = TurnTerminalState::Open;
        open.completed = false;
        open.completion_timestamp.clear();
        open.skill_invocations = vec![skill_invocation(
            "analyze",
            3,
            "2026-03-28T08:00:01.000Z",
            Some(5),
            Some(13),
        )];

        let mut completed = open.clone();
        completed.terminal_state = TurnTerminalState::Completed;
        completed.completed = true;
        completed.completion_timestamp = "2026-03-28T08:00:10.000Z".to_string();
        completed.assistants.push(AssistantMessage {
            timestamp: completed.completion_timestamp.clone(),
            text: "done".to_string(),
            media_content: serde_json::Value::Null,
            phase: "final_answer".to_string(),
            usage: TokenUsage::default(),
            token_count_timestamp: String::new(),
            invocation_started_timestamp: String::new(),
            invocation_end_source: None,
        });

        let open_body = build_otel_export_request_for_turn(&open, 1, "sess", "user", "1", None);
        let completed_body =
            build_otel_export_request_for_turn(&completed, 1, "sess", "user", "1", None);
        let open_skill = span_named(&open_body, "skill analyze");
        let completed_skill = span_named(&completed_body, "skill analyze");

        assert_eq!(open_skill["spanId"], completed_skill["spanId"]);
        assert_eq!(
            attr_string(span_attrs(open_skill), "gen_ai.tool.call.id"),
            attr_string(span_attrs(completed_skill), "gen_ai.tool.call.id")
        );
    }

    #[test]
    fn test_native_skill_consumes_one_matching_invocation_and_suppresses_same_name_path_only() {
        let mut turn = make_turn();
        turn.skill_invocations = vec![
            skill_invocation("foo", 0, "2026-03-28T08:00:00.100Z", Some(0), Some(4)),
            skill_invocation("foo", 1, "2026-03-28T08:00:00.200Z", Some(5), Some(9)),
        ];
        turn.tool_calls = vec![
            ToolCall {
                call_id: "native-foo".to_string(),
                name: "Skill".to_string(),
                input: json!({"skill":"foo"}),
                output: json!("native done"),
                started_timestamp: "2026-03-28T08:00:01.000Z".to_string(),
                ended_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            },
            ToolCall {
                call_id: "read-foo".to_string(),
                name: "Bash".to_string(),
                input: json!({"command":"sed -n '1,20p' ~/.codex/skills/foo/SKILL.md"}),
                output: json!("foo contents"),
                started_timestamp: "2026-03-28T08:00:03.000Z".to_string(),
                ended_timestamp: "2026-03-28T08:00:04.000Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            },
            ToolCall {
                call_id: "read-review".to_string(),
                name: "Bash".to_string(),
                input: json!({"command":"sed -n '1,20p' ~/.codex/skills/code-review/SKILL.md"}),
                output: json!("review contents"),
                started_timestamp: "2026-03-28T08:00:05.000Z".to_string(),
                ended_timestamp: "2026-03-28T08:00:06.000Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            },
        ];

        let body = build_otel_export_request_for_turn(&turn, 1, "sess", "user", "1", None);
        assert_eq!(count_spans_named(&body, "skill foo"), 2);
        assert_eq!(count_spans_named(&body, "skill code-review"), 1);
        assert_eq!(count_spans_named(&body, "tool Bash"), 1);

        let foo_call_ids: Vec<&str> = exported_spans(&body)
            .iter()
            .filter(|span| span["name"].as_str() == Some("skill foo"))
            .filter_map(|span| attr_string(span_attrs(span), "gen_ai.tool.call.id"))
            .collect();
        assert!(foo_call_ids.contains(&"native-foo"));
        assert!(foo_call_ids.iter().any(|id| id.contains(":1:foo:")));
    }

    #[test]
    fn test_directory_only_skill_path_remains_a_normal_exec_tool_span() {
        let mut turn = make_turn();
        turn.tool_calls = vec![ToolCall {
            call_id: "find-atc-lock".to_string(),
            name: "exec_command".to_string(),
            input: json!({
                "cmd": "find /Users/testuser/.codex/skills/atc-cli -maxdepth 2 -name atc-lock.json -print"
            }),
            output: json!(""),
            started_timestamp: "2026-03-28T08:00:01.000Z".to_string(),
            ended_timestamp: "2026-03-28T08:00:02.000Z".to_string(),
            assistant_index: 0,
            success: Some(true),
        }];

        let body = build_otel_export_request_for_turn(&turn, 1, "sess", "user", "1", None);
        assert_eq!(count_spans_named(&body, "tool exec_command"), 1);
        assert_eq!(
            exported_spans(&body)
                .iter()
                .filter(|span| span["name"]
                    .as_str()
                    .is_some_and(|name| name.starts_with("skill atc-cli")))
                .count(),
            0
        );
    }

    #[test]
    fn test_skill_invocation_privacy_redacts_arguments_and_result() {
        let mut turn = make_turn();
        let invocation =
            skill_invocation("analyze", 0, "2026-03-28T08:00:01.000Z", Some(0), Some(8));
        turn.skill_invocations = vec![invocation.clone()];
        let span = build_skill_invocation_span(
            "trace",
            "root",
            &turn,
            &invocation,
            "analyze",
            &iso_to_nanos(&turn.user_timestamp),
            &iso_to_nanos(&turn.completion_timestamp),
            "user",
            "sess",
            "chat",
            true,
        );
        let span = serde_json::to_value(span).expect("serialize skill span");
        let arguments = attr_string(span_attrs(&span), "gen_ai.tool.call.arguments");
        let result = attr_string(span_attrs(&span), "gen_ai.tool.call.result");

        assert_eq!(arguments, Some("[REDACTED]"));
        assert_eq!(result, Some("[REDACTED]"));
    }

    #[test]
    fn test_skill_trace_tag_is_deduplicated() {
        let mut turn = make_turn();
        turn.skill_invocations = vec![
            skill_invocation("analyze", 0, "2026-03-28T08:00:01.000Z", Some(0), Some(8)),
            skill_invocation("analyze", 1, "2026-03-28T08:00:01.100Z", Some(9), Some(17)),
        ];
        let body = build_otel_export_request_for_turn(&turn, 1, "sess", "user", "1", None);
        let root_attrs = span_attrs(&exported_spans(&body)[0]);
        let tags = root_attrs
            .iter()
            .find(|attr| attr["key"].as_str() == Some("langfuse.trace.tags"))
            .and_then(|attr| attr["value"]["arrayValue"]["values"].as_array())
            .expect("trace tags");
        assert_eq!(
            tags.iter()
                .filter(|value| value["stringValue"].as_str() == Some("skill:analyze"))
                .count(),
            1
        );
    }

    #[cfg(feature = "skill")]
    #[test]
    fn test_skill_tags_load_from_session_registry() {
        let _guard = env_lock();
        let saved_state_dir = std::env::var("CDXHOOKS_STATE_DIR").ok();
        let dir = tempfile::tempdir().unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", dir.path());

        let mut registry = crate::common::skill_tags::SkillTagRegistry::new();
        registry.insert("analyze".to_string(), vec!["theta".to_string()]);
        let registry_path = crate::common::skill_tags::state_file_path_for_session("sess").unwrap();
        crate::common::skill_tags::save_registry(&registry, &registry_path).unwrap();

        let mut turn = make_turn();
        turn.skill_invocations = vec![skill_invocation(
            "analyze",
            0,
            "2026-03-28T08:00:01.000Z",
            Some(0),
            Some(8),
        )];
        let body = build_otel_export_request_for_turn(&turn, 1, "sess", "user", "1", None);
        let skill = span_named(&body, "skill analyze");
        let skill_tags = span_attrs(skill)
            .iter()
            .find(|attr| attr["key"].as_str() == Some("agentic.skill.tags"))
            .and_then(|attr| attr["value"]["arrayValue"]["values"].as_array())
            .expect("skill tags");
        assert!(skill_tags
            .iter()
            .any(|value| value["stringValue"].as_str() == Some("theta")));

        let root_attrs = span_attrs(&exported_spans(&body)[0]);
        let trace_tags = root_attrs
            .iter()
            .find(|attr| attr["key"].as_str() == Some("langfuse.trace.tags"))
            .and_then(|attr| attr["value"]["arrayValue"]["values"].as_array())
            .expect("trace tags");
        assert!(trace_tags
            .iter()
            .any(|value| value["stringValue"].as_str() == Some("theta")));

        match saved_state_dir {
            Some(value) => std::env::set_var("CDXHOOKS_STATE_DIR", value),
            None => std::env::remove_var("CDXHOOKS_STATE_DIR"),
        }
    }

    #[cfg(feature = "skill")]
    #[test]
    fn test_skill_tags_use_hook_session_not_conversation_id() {
        let _guard = env_lock();
        let saved_state_dir = std::env::var("CDXHOOKS_STATE_DIR").ok();
        let dir = tempfile::tempdir().unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", dir.path());

        let mut registry = crate::common::skill_tags::SkillTagRegistry::new();
        registry.insert("analyze".to_string(), vec!["theta".to_string()]);
        let hook_registry_path =
            crate::common::skill_tags::state_file_path_for_session("hook-session").unwrap();
        crate::common::skill_tags::save_registry(&registry, &hook_registry_path).unwrap();

        let mut spans = vec![OtelSpan {
            trace_id: "trace".to_string(),
            span_id: "span".to_string(),
            parent_span_id: String::new(),
            name: "skill analyze".to_string(),
            kind: SPAN_KIND_INTERNAL,
            start_time_unix_nano: "1".to_string(),
            end_time_unix_nano: "2".to_string(),
            attributes: vec![
                OtelAttribute::string("gen_ai.conversation.id", "conversation-not-session"),
                OtelAttribute::string("agentic.tool.type", "skill"),
                OtelAttribute::string("agentic.tool.name", "analyze"),
            ],
            status: OtelStatus::ok(),
        }];

        annotate_trace_tags(&mut spans, "hook-session");
        let tags = spans[0]
            .attributes
            .iter()
            .find(|attr| attr.key == "agentic.skill.tags")
            .expect("skill tags");
        match &tags.value {
            agentic_otel_common::otlp::OtelAnyValue::Array { array_value } => {
                assert!(array_value.values.iter().any(|value| {
                    matches!(
                        value,
                        agentic_otel_common::otlp::OtelAnyValue::String { string_value }
                        if string_value == "theta"
                    )
                }));
            }
            _ => panic!("skill tags must be an array"),
        }

        match saved_state_dir {
            Some(value) => std::env::set_var("CDXHOOKS_STATE_DIR", value),
            None => std::env::remove_var("CDXHOOKS_STATE_DIR"),
        }
    }

    #[test]
    fn test_subagent_tool_span_preserves_name_and_type() {
        let turn = make_turn();
        let tool_call = ToolCall {
            call_id: "call-spawn-1".to_string(),
            name: "spawn_agent".to_string(),
            input: json!({"task_name": "repo_file_counter", "message": "count files"}),
            output: json!({"task_name": "/root/repo_file_counter"}),
            started_timestamp: "2026-03-28T08:00:01.000Z".to_string(),
            ended_timestamp: "2026-03-28T08:00:03.000Z".to_string(),
            assistant_index: 0,
            success: None,
        };
        let now = "1711612800000000000";
        let span = build_tool_span(
            "trace1",
            "parent1",
            &tool_call,
            &turn,
            now,
            "100001",
            "test-session",
            "chat-1",
            None,
        );
        let span = serde_json::to_value(&span).unwrap();

        // Span name should be "subagent spawn_agent" (type prefix + name)
        assert_eq!(span["name"], "subagent spawn_agent");

        let attrs = span["attributes"].as_array().unwrap();
        let find_attr = |key: &str| -> Option<String> {
            attrs
                .iter()
                .find(|a| a["key"] == key)
                .map(|a| a["value"]["stringValue"].as_str().unwrap().to_string())
        };
        assert_eq!(find_attr("agentic.tool.type").as_deref(), Some("subagent"));
        assert_eq!(
            find_attr("agentic.tool.name").as_deref(),
            Some("spawn_agent")
        );
        assert_eq!(
            find_attr("gen_ai.tool.name").as_deref(),
            Some("subagent spawn_agent")
        );
        assert_eq!(
            find_attr("agentic.tool.id").as_deref(),
            Some("call-spawn-1")
        );
    }

    #[test]
    fn test_subagent_wait_agent_tool_span() {
        let turn = make_turn();
        let tool_call = ToolCall {
            call_id: "call-wait-1".to_string(),
            name: "wait_agent".to_string(),
            input: json!({"targets": ["/root/task1"], "timeout_ms": 30000}),
            output: json!({"statuses": {"/root/task1": "completed"}, "timed_out": false}),
            started_timestamp: "2026-03-28T08:00:04.000Z".to_string(),
            ended_timestamp: "2026-03-28T08:00:15.000Z".to_string(),
            assistant_index: 0,
            success: None,
        };
        let now = "1711612800000000000";
        let span = build_tool_span(
            "trace1",
            "parent1",
            &tool_call,
            &turn,
            now,
            "100001",
            "test-session",
            "chat-1",
            None,
        );
        let span = serde_json::to_value(&span).unwrap();

        assert_eq!(span["name"], "subagent wait_agent");

        let attrs = span["attributes"].as_array().unwrap();
        let find_attr = |key: &str| -> Option<String> {
            attrs
                .iter()
                .find(|a| a["key"] == key)
                .map(|a| a["value"]["stringValue"].as_str().unwrap().to_string())
        };
        assert_eq!(find_attr("agentic.tool.type").as_deref(), Some("subagent"));
        assert_eq!(
            find_attr("agentic.tool.name").as_deref(),
            Some("wait_agent")
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

    // --- Compaction span tests (Rule 14) ---

    #[test]
    fn test_build_compaction_span_basic() {
        use crate::common::transcript::CompactionEvent;
        let compaction = CompactionEvent {
            timestamp: "2026-04-12T10:00:10.000Z".to_string(),
            end_timestamp: "2026-04-12T10:00:16.000Z".to_string(),
            pre_tokens: Some(215600),
            post_tokens: Some(44649),
            sequence: 1,
        };
        let span = build_compaction_span(
            "trace1",
            "parent1",
            &compaction,
            "0",
            "100001",
            "test-session",
            "chat-1",
        );
        let span = serde_json::to_value(&span).unwrap();

        assert_eq!(span["name"], "compaction");

        let attrs = span["attributes"].as_array().unwrap();
        let find_attr = |key: &str| -> Option<String> {
            attrs.iter().find(|a| a["key"] == key).map(|a| {
                a["value"]["stringValue"]
                    .as_str()
                    .or_else(|| a["value"]["intValue"].as_str())
                    .unwrap_or("")
                    .to_string()
            })
        };

        assert_eq!(find_attr("gen_ai.span.kind").as_deref(), Some("COMPACTION"));
        assert_eq!(
            find_attr("gen_ai.operation.name").as_deref(),
            Some("execute_tool")
        );
        assert_eq!(find_attr("gen_ai.tool.name").as_deref(), Some("compaction"));
        assert_eq!(
            find_attr("gen_ai.tool.call.id").as_deref(),
            Some("compact-1")
        );
        assert_eq!(find_attr("agentic.tool.type").as_deref(), Some("system"));
        assert_eq!(
            find_attr("agentic.compaction.trigger").as_deref(),
            Some("unknown")
        );
        assert_eq!(
            find_attr("agentic.compaction.tokens.before").as_deref(),
            Some("215600")
        );
        assert_eq!(
            find_attr("agentic.compaction.tokens.after").as_deref(),
            Some("44649")
        );
        assert_eq!(
            find_attr("agentic.compaction.tokens.saved").as_deref(),
            Some("170951")
        );
        assert_eq!(
            find_attr("agentic.compaction.sequence").as_deref(),
            Some("1")
        );
        assert_eq!(
            find_attr("agentic.compaction.ratio").as_deref(),
            Some("0.21")
        );

        // Status should be OK (tokens were reduced)
        assert_eq!(span["status"]["code"], 1);
    }

    #[test]
    fn test_build_compaction_span_no_backfill() {
        use crate::common::transcript::CompactionEvent;
        // None means a trustworthy post-compaction observation is unavailable.
        let compaction = CompactionEvent {
            timestamp: "2026-04-12T10:44:40.840Z".to_string(),
            end_timestamp: String::new(),
            pre_tokens: Some(215273),
            post_tokens: None,
            sequence: 2,
        };
        let now_ns = "1712915080000000000";
        let span = build_compaction_span(
            "trace2",
            "parent2",
            &compaction,
            now_ns,
            "100001",
            "test-session",
            "chat-1",
        );
        let span = serde_json::to_value(&span).unwrap();

        let attrs = span["attributes"].as_array().unwrap();
        let find_attr = |key: &str| -> Option<String> {
            attrs.iter().find(|a| a["key"] == key).map(|a| {
                a["value"]["stringValue"]
                    .as_str()
                    .or_else(|| a["value"]["intValue"].as_str())
                    .unwrap_or("")
                    .to_string()
            })
        };

        assert_eq!(find_attr("agentic.compaction.tokens.after"), None);
        assert_eq!(find_attr("agentic.compaction.tokens.saved"), None);
        assert_eq!(find_attr("agentic.compaction.ratio"), None);
        assert_eq!(
            find_attr("agentic.compaction.sequence").as_deref(),
            Some("2")
        );
        // Status stays OK when the post-compaction observation is unknown.
        assert_eq!(span["status"]["code"], 1);
    }

    #[test]
    fn test_compaction_spans_in_otel_export() {
        use crate::common::transcript::CompactionEvent;
        let mut turn = make_turn();
        turn.compactions = vec![CompactionEvent {
            timestamp: "2026-03-28T08:00:06.000Z".to_string(),
            end_timestamp: "2026-03-28T08:00:08.000Z".to_string(),
            pre_tokens: Some(100000),
            post_tokens: Some(25000),
            sequence: 1,
        }];

        let body = build_otel_export_request_for_turn(&turn, 1, "sess-1", "tester", "1.0.0", None);
        let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .expect("spans should exist");

        // root(1) + generation(2) + tool(1) + compaction(1) = 5
        assert_eq!(spans.len(), 5);
        assert_eq!(spans[4]["name"], "compaction");

        let compact_attrs = spans[4]["attributes"].as_array().unwrap();
        let has_key = |key: &str| compact_attrs.iter().any(|a| a["key"] == key);
        let attr_value = |key: &str| -> Option<&str> {
            compact_attrs
                .iter()
                .find(|a| a["key"] == key)
                .and_then(|a| a["value"]["stringValue"].as_str())
        };
        assert_eq!(attr_value("gen_ai.span.kind"), Some("COMPACTION"));
        assert!(has_key("agentic.compaction.tokens.before"));
        assert!(has_key("agentic.compaction.tokens.after"));
        assert!(has_key("agentic.compaction.tokens.saved"));
        assert!(has_key("agentic.compaction.sequence"));
        assert!(has_key("agentic.compaction.trigger"));
    }

    // --- Subagent span and correlation tests ---

    #[test]
    fn test_subagent_span_from_spawn_end() {
        use crate::common::transcript::CollabAgentEvent;
        let event = CollabAgentEvent::SpawnEnd {
            call_id: "call-spawn-1".to_string(),
            sender_thread_id: "thread-parent".to_string(),
            new_thread_id: Some("thread-child-1".to_string()),
            nickname: Some("worker".to_string()),
            role: Some("coder".to_string()),
            prompt: "analyze the code".to_string(),
            model: "gpt-5.4".to_string(),
            status: "completed".to_string(),
        };
        let span = build_subagent_span(
            "trace1",
            "parent1",
            &event,
            "1711612800000000000",
            "100001",
            "test-session",
            "chat-1",
            None,
        )
        .expect("should produce a SUBAGENT span");

        let span = serde_json::to_value(&span).unwrap();
        assert_eq!(span["name"], "SUBAGENT worker");

        let attrs = span["attributes"].as_array().unwrap();
        let find_attr = |key: &str| -> Option<String> {
            attrs
                .iter()
                .find(|a| a["key"] == key)
                .map(|a| a["value"]["stringValue"].as_str().unwrap().to_string())
        };
        assert_eq!(find_attr("gen_ai.span.kind").as_deref(), Some("INTERNAL"));
        assert_eq!(
            find_attr("gen_ai.operation.name").as_deref(),
            Some("subagent_spawn")
        );
        assert_eq!(find_attr("agentic.subagent.type").as_deref(), Some("spawn"));
        assert_eq!(
            find_attr("agentic.subagent.call_id").as_deref(),
            Some("call-spawn-1")
        );
        assert_eq!(
            find_attr("agentic.subagent.sender_thread_id").as_deref(),
            Some("thread-parent")
        );
        assert_eq!(
            find_attr("agentic.subagent.child_thread_id").as_deref(),
            Some("thread-child-1")
        );
        assert_eq!(
            find_attr("agentic.subagent.nickname").as_deref(),
            Some("worker")
        );
        assert_eq!(find_attr("agentic.subagent.role").as_deref(), Some("coder"));
        assert_eq!(
            find_attr("agentic.subagent.prompt").as_deref(),
            Some("analyze the code")
        );
        assert_eq!(
            find_attr("agentic.subagent.model").as_deref(),
            Some("gpt-5.4")
        );
        assert_eq!(
            find_attr("agentic.subagent.status").as_deref(),
            Some("completed")
        );
        // Status should be OK for "completed"
        assert_eq!(span["status"]["code"], 1);
    }

    #[test]
    fn test_subagent_span_spawn_end_error_status() {
        use crate::common::transcript::CollabAgentEvent;
        let event = CollabAgentEvent::SpawnEnd {
            call_id: "call-spawn-err".to_string(),
            sender_thread_id: "thread-parent".to_string(),
            new_thread_id: None,
            nickname: None,
            role: None,
            prompt: String::new(),
            model: String::new(),
            status: "error".to_string(),
        };
        let span = build_subagent_span(
            "trace1",
            "parent1",
            &event,
            "0",
            "100001",
            "test-session",
            "chat-1",
            None,
        )
        .expect("should produce a SUBAGENT span");

        let span = serde_json::to_value(&span).unwrap();
        assert_eq!(span["name"], "SUBAGENT");
        // Error status
        assert_ne!(span["status"]["code"], 1);
    }

    #[test]
    fn test_subagent_span_interaction_begin_not_produced() {
        use crate::common::transcript::CollabAgentEvent;
        let event = CollabAgentEvent::InteractionBegin {
            call_id: "call-interact-1".to_string(),
            sender_thread_id: "thread-parent".to_string(),
            receiver_thread_id: "thread-child-1".to_string(),
            prompt: "review this".to_string(),
        };
        let result = build_subagent_span(
            "trace1",
            "parent1",
            &event,
            "0",
            "100001",
            "test-session",
            "chat-1",
            None,
        );
        assert!(
            result.is_none(),
            "InteractionBegin should not produce a SUBAGENT span"
        );
    }

    #[test]
    fn test_root_span_subagent_source_attrs() {
        let mut turn = make_turn();
        turn.subagent_source = Some(crate::common::transcript::SubagentSourceInfo {
            parent_thread_id: "thread-parent-abc".to_string(),
            depth: 2,
            agent_path: Some("/root/worker/coder".to_string()),
            agent_nickname: Some("coder".to_string()),
            agent_role: Some("code-writer".to_string()),
            protocol_version: Some("v2".to_string()),
        });

        let config = LangfuseConfig {
            public_key: Some("pk-test".to_string()),
            secret_key: Some("sk-test".to_string()),
            host: "https://test.langfuse.com".to_string(),
            headers: None,
        };
        let body = build_otel_export_request(
            &[turn],
            "sess-1",
            "tester",
            &config,
            "2026-03-28T08:00:11.000Z",
            "1.0.0",
            0,
        );
        let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .expect("spans should exist");

        // Root span is first
        let root_attrs = spans[0]["attributes"].as_array().unwrap();
        let find_attr = |key: &str| -> Option<String> {
            root_attrs
                .iter()
                .find(|a| a["key"] == key)
                .map(|a| a["value"]["stringValue"].as_str().unwrap().to_string())
        };
        assert_eq!(
            find_attr("agentic.subagent.parent_thread_id").as_deref(),
            Some("thread-parent-abc")
        );
        assert_eq!(find_attr("agentic.subagent.depth").as_deref(), Some("2"));
        assert_eq!(
            find_attr("agentic.subagent.agent_path").as_deref(),
            Some("/root/worker/coder")
        );
        assert_eq!(
            find_attr("agentic.subagent.agent_nickname").as_deref(),
            Some("coder")
        );
        assert_eq!(
            find_attr("agentic.subagent.agent_role").as_deref(),
            Some("code-writer")
        );
    }

    #[test]
    fn test_tool_span_subagent_correlation_with_spawn_end() {
        use crate::common::transcript::CollabAgentEvent;
        let mut turn = make_turn();
        turn.tool_calls = vec![ToolCall {
            call_id: "call-spawn-1".to_string(),
            name: "spawn_agent".to_string(),
            input: json!({"message": "analyze code"}),
            output: json!({"task_name": "/root/worker"}),
            started_timestamp: "2026-03-28T08:00:01.000Z".to_string(),
            ended_timestamp: "2026-03-28T08:00:03.000Z".to_string(),
            assistant_index: 0,
            success: None,
        }];
        turn.collab_agent_events = vec![CollabAgentEvent::SpawnEnd {
            call_id: "call-spawn-1".to_string(),
            sender_thread_id: "thread-parent".to_string(),
            new_thread_id: Some("thread-child-42".to_string()),
            nickname: Some("worker".to_string()),
            role: Some("coder".to_string()),
            prompt: "analyze code".to_string(),
            model: "gpt-5.4".to_string(),
            status: "completed".to_string(),
        }];

        let now = "1711612800000000000";
        let span = build_tool_span(
            "trace1",
            "parent1",
            &turn.tool_calls[0],
            &turn,
            now,
            "100001",
            "test-session",
            "chat-1",
            None,
        );
        let span = serde_json::to_value(&span).unwrap();

        let attrs = span["attributes"].as_array().unwrap();
        let find_attr = |key: &str| -> Option<String> {
            attrs
                .iter()
                .find(|a| a["key"] == key)
                .map(|a| a["value"]["stringValue"].as_str().unwrap().to_string())
        };
        // Subagent correlation attrs from matched CollabAgentSpawnEnd
        assert_eq!(
            find_attr("agentic.subagent.child_thread_id").as_deref(),
            Some("thread-child-42")
        );
        assert_eq!(
            find_attr("agentic.subagent.nickname").as_deref(),
            Some("worker")
        );
        assert_eq!(find_attr("agentic.subagent.role").as_deref(), Some("coder"));
    }

    #[test]
    fn test_tool_span_subagent_correlation_with_interaction() {
        use crate::common::transcript::CollabAgentEvent;
        let mut turn = make_turn();
        turn.tool_calls = vec![ToolCall {
            call_id: "call-send-1".to_string(),
            name: "send_message".to_string(),
            input: json!({"message": "please review"}),
            output: json!({"status": "delivered"}),
            started_timestamp: "2026-03-28T08:00:05.000Z".to_string(),
            ended_timestamp: "2026-03-28T08:00:06.000Z".to_string(),
            assistant_index: 0,
            success: None,
        }];
        turn.collab_agent_events = vec![CollabAgentEvent::InteractionBegin {
            call_id: "call-send-1".to_string(),
            sender_thread_id: "thread-parent".to_string(),
            receiver_thread_id: "thread-child-42".to_string(),
            prompt: "please review".to_string(),
        }];

        let now = "1711612800000000000";
        let span = build_tool_span(
            "trace1",
            "parent1",
            &turn.tool_calls[0],
            &turn,
            now,
            "100001",
            "test-session",
            "chat-1",
            None,
        );
        let span = serde_json::to_value(&span).unwrap();

        let attrs = span["attributes"].as_array().unwrap();
        let find_attr = |key: &str| -> Option<String> {
            attrs
                .iter()
                .find(|a| a["key"] == key)
                .map(|a| a["value"]["stringValue"].as_str().unwrap().to_string())
        };
        assert_eq!(
            find_attr("agentic.subagent.receiver_thread_id").as_deref(),
            Some("thread-child-42")
        );
    }

    /// Builds a minimal parent OTel body containing one `spawn_agent` tool span
    /// (success) and one `wait_agent` tool span pointing at `child_session_id`,
    /// for testing the sync-subagent merge behaviour.
    fn parent_body_with_spawn_and_wait(child_session_id: &str) -> serde_json::Value {
        json!({
            "resourceSpans": [{
                "resource": { "attributes": [] },
                "scopeSpans": [{
                    "spans": [
                        {
                            "traceId": "0000000000000000000000000000abcd",
                            "spanId": "0000000000000001",
                            "parentSpanId": "",
                            "name": "Turn 1",
                            "kind": 1,
                            "startTimeUnixNano": "100000000000",
                            "endTimeUnixNano":   "200000000000",
                            "attributes": [
                                {"key": "gen_ai.span.kind", "value": {"stringValue": "CHAT"}}
                            ],
                            "status": {"code": 1}
                        },
                        {
                            "traceId": "0000000000000000000000000000abcd",
                            "spanId": "0000000000000010",
                            "parentSpanId": "0000000000000001",
                            "name": "subagent spawn_agent",
                            "kind": 1,
                            "startTimeUnixNano": "110000000000",
                            "endTimeUnixNano":   "111000000000",
                            "attributes": [
                                {"key": "gen_ai.span.kind", "value": {"stringValue": "TOOL"}},
                                {"key": "agentic.tool.type", "value": {"stringValue": "subagent"}},
                                {"key": "agentic.tool.raw_name", "value": {"stringValue": "spawn_agent"}},
                                {"key": "gen_ai.tool.call.id", "value": {"stringValue": "call-spawn"}},
                                {"key": "gen_ai.tool.call.result", "value": {"stringValue": format!("{{\"agent_id\":\"{}\"}}", child_session_id)}}
                            ],
                            "status": {"code": 1}
                        },
                        {
                            "traceId": "0000000000000000000000000000abcd",
                            "spanId": "0000000000000020",
                            "parentSpanId": "0000000000000001",
                            "name": "subagent wait_agent",
                            "kind": 1,
                            "startTimeUnixNano": "112000000000",
                            "endTimeUnixNano":   "180000000000",
                            "attributes": [
                                {"key": "gen_ai.span.kind", "value": {"stringValue": "TOOL"}},
                                {"key": "agentic.tool.type", "value": {"stringValue": "subagent"}},
                                {"key": "agentic.tool.raw_name", "value": {"stringValue": "wait_agent"}},
                                {"key": "gen_ai.tool.call.id", "value": {"stringValue": "call-wait"}},
                                {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": format!("{{\"targets\":[\"{}\"]}}", child_session_id)}},
                                {"key": "gen_ai.tool.call.result", "value": {"stringValue": "{\"status\":{\"done\":true}}"}}
                            ],
                            "status": {"code": 1}
                        }
                    ]
                }]
            }]
        })
    }

    fn parent_turn_with_spawn_and_wait(child_session_id: &str) -> Turn {
        let mut turn = make_turn();
        turn.tool_calls = vec![
            ToolCall {
                call_id: "call-spawn".to_string(),
                name: "spawn_agent".to_string(),
                input: json!({"name":"spawn_agent","message":"do task"}),
                output: json!({"agent_id": child_session_id}),
                started_timestamp: "2026-05-24T13:16:20.000Z".to_string(),
                ended_timestamp: "2026-05-24T13:16:21.000Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            },
            ToolCall {
                call_id: "call-wait".to_string(),
                name: "wait_agent".to_string(),
                input: json!({"name":"wait_agent","targets":[child_session_id]}),
                output: json!({"status":{"done":true}}),
                started_timestamp: "2026-05-24T13:16:22.000Z".to_string(),
                ended_timestamp: "2026-05-24T13:16:43.000Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            },
        ];
        turn
    }

    fn v2_parent_turn(child_session_id: &str, include_active_sibling: bool) -> Turn {
        let mut turn = make_turn();
        turn.tool_calls = vec![
            ToolCall {
                call_id: "call-spawn".to_string(),
                name: "spawn_agent".to_string(),
                input: json!({"task_name":"hume","message":"do task"}),
                output: json!({"task_name":"/root/hume"}),
                started_timestamp: "2026-05-24T13:16:20.000Z".to_string(),
                ended_timestamp: "2026-05-24T13:16:21.000Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            },
            ToolCall {
                call_id: "call-wait".to_string(),
                name: "wait_agent".to_string(),
                input: json!({"timeout_ms":10000}),
                output: json!({"message":"Wait timed out.","timed_out":true}),
                started_timestamp: "2026-05-24T13:16:22.000Z".to_string(),
                ended_timestamp: "2026-05-24T13:16:40.000Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            },
        ];
        turn.collab_agent_events = vec![
            CollabAgentEvent::SubagentActivity {
                event_id: "call-spawn".to_string(),
                agent_thread_id: child_session_id.to_string(),
                agent_path: Some("/root/hume".to_string()),
                kind: "started".to_string(),
                timestamp: "2026-05-24T13:16:20.500Z".to_string(),
            },
            CollabAgentEvent::AgentMessage {
                author: "/root/hume".to_string(),
                recipient: "/root".to_string(),
                message_type: Some("FINAL_ANSWER".to_string()),
                content: "done from typed terminal".to_string(),
                timestamp: "2026-05-24T13:16:39.000Z".to_string(),
            },
        ];
        if include_active_sibling {
            turn.tool_calls.push(ToolCall {
                call_id: "call-spawn-active".to_string(),
                name: "spawn_agent".to_string(),
                input: json!({"task_name":"active","message":"keep working"}),
                output: json!({"task_name":"/root/active"}),
                started_timestamp: "2026-05-24T13:16:23.000Z".to_string(),
                ended_timestamp: "2026-05-24T13:16:24.000Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            });
            turn.collab_agent_events
                .push(CollabAgentEvent::SubagentActivity {
                    event_id: "call-spawn-active".to_string(),
                    agent_thread_id: "child-active".to_string(),
                    agent_path: Some("/root/active".to_string()),
                    kind: "started".to_string(),
                    timestamp: "2026-05-24T13:16:23.500Z".to_string(),
                });
        }
        turn
    }

    fn make_child_turn(user_ts: &str, end_ts: &str) -> Turn {
        Turn {
            chat_id: None,
            turn_id: "child-t1".to_string(),
            user_timestamp: user_ts.to_string(),
            user_text: "do task".to_string(),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: end_ts.to_string(),
                text: "result".to_string(),
                media_content: serde_json::Value::Null,
                phase: "final_answer".to_string(),
                usage: TokenUsage::default(),
                token_count_timestamp: String::new(),
                invocation_started_timestamp: String::new(),
                invocation_end_source: None,
            }],
            tool_calls: vec![],
            model: "gpt-5.4".to_string(),
            cwd: String::new(),
            system_instructions: "Follow the child policy.".to_string(),
            completed: true,
            completion_timestamp: end_ts.to_string(),
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

    fn make_open_child_turn(user_ts: &str, assistant_ts: &str) -> Turn {
        let mut turn = make_child_turn(user_ts, assistant_ts);
        turn.completed = false;
        turn.completion_timestamp.clear();
        turn.terminal_state = TurnTerminalState::Open;
        turn.assistants[0].phase = "assistant".to_string();
        turn.assistants[0].text = "partial result".to_string();
        turn.assistants[0].usage.input = Some(11);
        turn.assistants[0].usage.output = Some(7);
        turn.assistants[0].usage.total = Some(18);
        turn.assistants[0].usage.response_id = Some("resp-child-progress".to_string());
        turn
    }

    fn find_span_by_raw_name<'a>(
        body: &'a serde_json::Value,
        raw: &str,
    ) -> Vec<&'a serde_json::Value> {
        body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .map(|spans| {
                spans
                    .iter()
                    .filter(|s| {
                        s["attributes"]
                            .as_array()
                            .map(|attrs| {
                                attrs.iter().any(|a| {
                                    a["key"] == "agentic.tool.raw_name"
                                        && a["value"]["stringValue"] == raw
                                })
                            })
                            .unwrap_or(false)
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    fn find_span_by_name<'a>(
        body: &'a serde_json::Value,
        name: &str,
    ) -> Option<&'a serde_json::Value> {
        body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()?
            .iter()
            .find(|s| s["name"] == name)
    }

    #[test]
    fn test_merge_keeps_spawn_and_wait_as_siblings() {
        let child_id = "019e5a21-1ae5-7000-0000-000000000001";
        let mut body = parent_body_with_spawn_and_wait(child_id);
        let child = SubagentChild {
            session_id: child_id.to_string(),
            turns: vec![make_child_turn(
                "2026-05-24T13:16:26.000Z",
                "2026-05-24T13:16:42.000Z",
            )],
            nickname: "Plato".to_string(),
            role: "worker".to_string(),
            agent_path: Some("/root/plato".to_string()),
        };

        let parent_turn = parent_turn_with_spawn_and_wait(child_id);
        let result =
            merge_subagent_sessions_into_otel(&mut body, &parent_turn, &[child], "tester", "1.0.0");
        assert_eq!(result.merged_sync_ids.len(), 1, "should classify as sync");

        // The spawn TOOL is the invocation fact; the AGENT span below is the
        // child execution. Both are required to preserve causal ordering.
        let spawn_spans = find_span_by_raw_name(&body, "spawn_agent");
        assert_eq!(spawn_spans.len(), 1, "spawn_agent must remain visible once");
        // wait_agent KEPT as sibling under parent root
        let wait_spans = find_span_by_raw_name(&body, "wait_agent");
        assert_eq!(
            wait_spans.len(),
            1,
            "wait_agent must remain visible as a sibling tool span"
        );
        // Sibling = parented to root span (not to SyncAgent)
        let root_span_id = body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["spanId"]
            .as_str()
            .unwrap();
        assert_eq!(
            spawn_spans[0]["parentSpanId"].as_str().unwrap(),
            root_span_id,
            "spawn_agent must remain attached to the parent turn"
        );
        assert_eq!(
            wait_spans[0]["parentSpanId"].as_str().unwrap(),
            root_span_id,
            "wait_agent must remain attached to root, not be re-parented"
        );

        // The AGENT span uses the Codex-assigned nickname.
        let agent_span =
            find_span_by_name(&body, "agent Plato").expect("named AGENT span should be created");
        assert_eq!(agent_span["parentSpanId"].as_str().unwrap(), root_span_id);
        let agent_attrs = agent_span["attributes"].as_array().expect("agent attrs");
        assert_eq!(attr_string(agent_attrs, "_internal.meta.root"), None);
    }

    #[test]
    fn test_merge_uses_root_marker_when_upstream_traceparent_sets_parent() {
        let child_id = "019e5a21-1ae5-7000-0000-0000000000aa";
        let mut body = parent_body_with_spawn_and_wait(child_id);
        let root = &mut body["resourceSpans"][0]["scopeSpans"][0]["spans"][0];
        root["parentSpanId"] = json!("upstream-parent-span");
        root["attributes"].as_array_mut().unwrap().push(json!({
            "key": "_internal.meta.root",
            "value": {"stringValue": "1"}
        }));
        let root_span_id = root["spanId"].as_str().unwrap().to_string();
        let child = SubagentChild {
            session_id: child_id.to_string(),
            turns: vec![make_child_turn(
                "2026-05-24T13:16:26.000Z",
                "2026-05-24T13:16:42.000Z",
            )],
            nickname: "Upstream".to_string(),
            role: "worker".to_string(),
            agent_path: Some("/root/upstream".to_string()),
        };

        let parent_turn = parent_turn_with_spawn_and_wait(child_id);
        let result =
            merge_subagent_sessions_into_otel(&mut body, &parent_turn, &[child], "tester", "1.0.0");

        assert_eq!(result.merged_sync_ids, vec![child_id]);
        assert!(!result
            .correlation_reasons
            .iter()
            .any(|(key, _)| key == "parent_root"));
        let agent_span = find_span_by_name(&body, "agent Upstream").expect("agent span");
        assert_eq!(
            agent_span["traceId"].as_str().unwrap(),
            "0000000000000000000000000000abcd"
        );
        assert_eq!(agent_span["parentSpanId"].as_str().unwrap(), root_span_id);
    }

    #[test]
    fn test_merge_reports_ambiguous_root_markers() {
        let child_id = "019e5a21-1ae5-7000-0000-0000000000bb";
        let mut body = parent_body_with_spawn_and_wait(child_id);
        for span in body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .take(2)
        {
            span["attributes"].as_array_mut().unwrap().push(json!({
                "key": "_internal.meta.root",
                "value": {"stringValue": "1"}
            }));
        }
        let parent_turn = parent_turn_with_spawn_and_wait(child_id);

        let result =
            merge_subagent_sessions_into_otel(&mut body, &parent_turn, &[], "tester", "1.0.0");

        assert!(result.merged_sync_ids.is_empty());
        assert_eq!(
            result.correlation_reasons,
            vec![(
                "parent_root".to_string(),
                "parent export body has 2 root markers".to_string()
            )]
        );
    }

    #[test]
    fn test_v2_merge_uses_activity_and_typed_terminal_result() {
        let child_id = "019f4b3b-a33d-7483-a6f1-45af05c98a1d";
        let mut body = parent_body_with_spawn_and_wait(child_id);
        let child = SubagentChild {
            session_id: child_id.to_string(),
            turns: vec![make_child_turn(
                "2026-05-24T13:16:26.000Z",
                "2026-05-24T13:16:38.000Z",
            )],
            nickname: "Hume".to_string(),
            role: String::new(),
            agent_path: Some("/root/hume".to_string()),
        };
        let parent_turn = v2_parent_turn(child_id, false);

        let result =
            merge_subagent_sessions_into_otel(&mut body, &parent_turn, &[child], "tester", "1.0");

        assert_eq!(result.merged_sync_ids, vec![child_id]);
        assert_eq!(find_span_by_raw_name(&body, "spawn_agent").len(), 1);
        assert_eq!(find_span_by_raw_name(&body, "wait_agent").len(), 1);
        let agent_span = find_span_by_name(&body, "agent Hume").expect("named v2 AGENT span");
        let attrs = agent_span["attributes"].as_array().unwrap();
        assert_eq!(
            attr_string(attrs, "gen_ai.tool.call.result"),
            Some("done from typed terminal")
        );
        assert_eq!(
            attr_string(attrs, "agentic.subagent.agent_path"),
            Some("/root/hume")
        );
    }

    #[test]
    fn test_failed_v2_child_is_exported_as_error_and_role_is_trimmed() {
        let child_id = "child-failed";
        let parent_turn = v2_parent_turn(child_id, false);
        let mut child_turn =
            make_child_turn("2026-05-24T13:16:26.000Z", "2026-05-24T13:16:38.000Z");
        child_turn.completed = false;
        child_turn.terminal_state = TurnTerminalState::Failed;
        child_turn.assistants.clear();
        let child = SubagentChild {
            session_id: child_id.to_string(),
            turns: vec![child_turn],
            nickname: "Hume".to_string(),
            role: "  coder  ".to_string(),
            agent_path: Some("/root/hume".to_string()),
        };
        let mut body = build_otel_export_request_for_turn(
            &parent_turn,
            1,
            "parent-session",
            "tester",
            "1.0",
            None,
        );

        let result =
            merge_subagent_sessions_into_otel(&mut body, &parent_turn, &[child], "tester", "1.0");

        assert_eq!(result.deferred_sync_ids, Vec::<String>::new());
        assert_eq!(result.merged_sync_ids, vec![child_id]);
        assert_eq!(result.invocation_outcomes.len(), 1);
        assert_eq!(result.invocation_outcomes[0].status, "error");
        let agent_span = find_span_by_name(&body, "agent Hume").expect("failed AGENT span");
        assert_eq!(agent_span["status"]["code"], 2);
        let attrs = agent_span["attributes"].as_array().expect("agent attrs");
        assert_eq!(attr_string(attrs, "agentic.subagent.status"), Some("error"));
        assert_eq!(attr_string(attrs, "error.type"), Some("subagent_error"));
        assert_eq!(attr_string(attrs, "agentic.subagent.type"), Some("coder"));
        assert_eq!(attr_string(attrs, "agentic.subagent.role"), Some("coder"));
    }

    #[test]
    fn test_native_completed_activation_with_failed_child_is_stable_error() {
        let parent_lines =
            include_str!("../../tests/fixtures/codex_collab_tool_call_same_turn_parent.jsonl")
                .lines()
                .map(|line| serde_json::from_str::<Value>(line).unwrap())
                .collect::<Vec<_>>();
        let parent_turns = crate::common::transcript::build_turns(&parent_lines);
        let mut child_turn =
            make_child_turn("2026-08-17T02:48:22.000Z", "2026-08-17T02:48:37.000Z");
        child_turn.completed = false;
        child_turn.terminal_state = TurnTerminalState::Failed;
        child_turn.assistants.clear();
        let child = SubagentChild {
            session_id: "child-collab".to_string(),
            turns: vec![child_turn],
            nickname: "Archimedes".to_string(),
            role: "executor".to_string(),
            agent_path: None,
        };
        let mut body = build_otel_export_request_for_turn(
            &parent_turns[0],
            1,
            "parent-collab",
            "tester",
            "1.0",
            None,
        );

        let result = merge_subagent_sessions_into_otel_across_turns(
            &mut body,
            &parent_turns,
            0,
            &[child],
            "tester",
            "1.0",
        );

        assert!(result.deferred_sync_ids.is_empty());
        assert_eq!(result.merged_sync_ids, vec!["child-collab"]);
        assert_eq!(result.invocation_outcomes[0].status, "error");
        let agent_span = find_span_by_name(&body, "agent Archimedes").expect("native AGENT span");
        assert_eq!(agent_span["status"]["code"], 2);
        let attrs = agent_span["attributes"].as_array().unwrap();
        assert_eq!(attr_string(attrs, "error.type"), Some("subagent_error"));
    }

    #[test]
    fn test_v2_interrupted_merge_emits_cancelled_agent_without_success_result() {
        let child_id = "child-interrupted";
        let mut parent_turn = v2_parent_turn(child_id, false);
        parent_turn
            .collab_agent_events
            .retain(|event| !matches!(event, CollabAgentEvent::AgentMessage { .. }));
        parent_turn.tool_calls.push(ToolCall {
            call_id: "call-interrupt".to_string(),
            name: "interrupt_agent".to_string(),
            input: json!({"target":"/root/hume"}),
            output: json!({"interrupted":true}),
            started_timestamp: "2026-05-24T13:16:39.000Z".to_string(),
            ended_timestamp: "2026-05-24T13:16:39.500Z".to_string(),
            assistant_index: 0,
            success: Some(true),
        });
        parent_turn
            .collab_agent_events
            .push(CollabAgentEvent::SubagentActivity {
                event_id: "call-interrupt".to_string(),
                agent_thread_id: child_id.to_string(),
                agent_path: Some("/root/hume".to_string()),
                kind: "interrupted".to_string(),
                timestamp: "2026-05-24T13:16:39.100Z".to_string(),
            });
        let mut child_turn =
            make_child_turn("2026-05-24T13:16:26.000Z", "2026-05-24T13:16:38.000Z");
        child_turn.completed = false;
        child_turn.terminal_state = TurnTerminalState::Aborted;
        child_turn.assistants.clear();
        let child = SubagentChild {
            session_id: child_id.to_string(),
            turns: vec![child_turn],
            nickname: "Gibbs".to_string(),
            role: String::new(),
            agent_path: Some("/root/hume".to_string()),
        };
        let mut body = build_otel_export_request_for_turn(
            &parent_turn,
            1,
            "parent-session",
            "tester",
            "1.0",
            None,
        );

        let result =
            merge_subagent_sessions_into_otel(&mut body, &parent_turn, &[child], "tester", "1.0");

        assert_eq!(
            result.merged_subagent_invocation_ids,
            vec!["child-interrupted:call-spawn"]
        );
        assert_eq!(result.invocation_outcomes.len(), 1);
        assert_eq!(result.invocation_outcomes[0].status, "interrupted");
        let agent_span =
            find_span_by_name(&body, "agent Gibbs (Cancelled)").expect("cancelled AGENT");
        let attrs = agent_span["attributes"].as_array().unwrap();
        assert_eq!(
            attr_string(attrs, "agentic.subagent.status"),
            Some("interrupted")
        );
        assert_eq!(attr_string(attrs, "error.type"), Some("cancelled"));
        assert_eq!(attr_string(attrs, "gen_ai.tool.call.result"), None);
        assert_eq!(agent_span["status"]["code"], 1);
        let interrupt_spans = find_span_by_raw_name(&body, "interrupt_agent");
        assert_eq!(interrupt_spans.len(), 1);
        assert_eq!(interrupt_spans[0]["name"], "subagent interrupt_agent");
        let interrupt_attrs = interrupt_spans[0]["attributes"].as_array().unwrap();
        assert_eq!(
            attr_string(interrupt_attrs, "agentic.tool.type"),
            Some("subagent")
        );
    }

    #[test]
    fn test_v2_reactivated_child_emits_one_outcome_per_activation() {
        let child_id = "child-reactivated";
        let mut spawn_turn = v2_parent_turn(child_id, false);
        spawn_turn
            .collab_agent_events
            .retain(|event| !matches!(event, CollabAgentEvent::AgentMessage { .. }));
        spawn_turn.tool_calls.push(ToolCall {
            call_id: "call-interrupt".to_string(),
            name: "interrupt_agent".to_string(),
            input: json!({"target":"/root/hume"}),
            output: json!({"interrupted":true}),
            started_timestamp: "2026-05-24T13:16:39.000Z".to_string(),
            ended_timestamp: "2026-05-24T13:16:39.500Z".to_string(),
            assistant_index: 0,
            success: Some(true),
        });
        spawn_turn
            .collab_agent_events
            .push(CollabAgentEvent::SubagentActivity {
                event_id: "call-interrupt".to_string(),
                agent_thread_id: child_id.to_string(),
                agent_path: Some("/root/hume".to_string()),
                kind: "interrupted".to_string(),
                timestamp: "2026-05-24T13:16:39.100Z".to_string(),
            });

        let mut followup_turn = make_turn();
        followup_turn.turn_id = "parent-followup".to_string();
        followup_turn.user_timestamp = "2026-05-24T14:00:00.000Z".to_string();
        followup_turn.completion_timestamp = "2026-05-24T14:00:09.000Z".to_string();
        followup_turn.tool_calls = vec![
            ToolCall {
                call_id: "call-followup".to_string(),
                name: "followup_task".to_string(),
                input: json!({"target":"/root/hume","message":"resume"}),
                output: json!({"delivered":true}),
                started_timestamp: "2026-05-24T14:00:01.000Z".to_string(),
                ended_timestamp: "2026-05-24T14:00:01.500Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            },
            ToolCall {
                call_id: "call-wait-followup".to_string(),
                name: "wait_agent".to_string(),
                input: json!({"timeout_ms":10000}),
                output: json!({"timed_out":false}),
                started_timestamp: "2026-05-24T14:00:02.000Z".to_string(),
                ended_timestamp: "2026-05-24T14:00:08.000Z".to_string(),
                assistant_index: 0,
                success: Some(true),
            },
        ];
        followup_turn.collab_agent_events = vec![
            CollabAgentEvent::SubagentActivity {
                event_id: "call-followup".to_string(),
                agent_thread_id: child_id.to_string(),
                agent_path: Some("/root/hume".to_string()),
                kind: "interacted".to_string(),
                timestamp: "2026-05-24T14:00:01.100Z".to_string(),
            },
            CollabAgentEvent::AgentMessage {
                author: "/root/hume".to_string(),
                recipient: "/root".to_string(),
                message_type: Some("FINAL_ANSWER".to_string()),
                content: "completed after resume".to_string(),
                timestamp: "2026-05-24T14:00:07.000Z".to_string(),
            },
        ];

        let mut aborted_turn =
            make_child_turn("2026-05-24T13:16:26.000Z", "2026-05-24T13:16:38.000Z");
        aborted_turn.completed = false;
        aborted_turn.terminal_state = TurnTerminalState::Aborted;
        aborted_turn.assistants.clear();
        let completed_turn =
            make_child_turn("2026-05-24T14:00:01.600Z", "2026-05-24T14:00:06.500Z");
        let child = SubagentChild {
            session_id: child_id.to_string(),
            turns: vec![aborted_turn, completed_turn],
            nickname: "Gibbs".to_string(),
            role: String::new(),
            agent_path: Some("/root/hume".to_string()),
        };
        let parent_turns = vec![spawn_turn, followup_turn];

        let mut spawn_body = build_otel_export_request_for_turn(
            &parent_turns[0],
            1,
            "parent-session",
            "tester",
            "1.0",
            None,
        );
        let spawn_result = merge_subagent_sessions_into_otel_across_turns(
            &mut spawn_body,
            &parent_turns,
            0,
            std::slice::from_ref(&child),
            "tester",
            "1.0",
        );
        assert_eq!(
            spawn_result.merged_subagent_invocation_ids,
            vec!["child-reactivated:call-spawn"]
        );
        assert_eq!(spawn_result.invocation_outcomes[0].status, "interrupted");

        let mut followup_body = build_otel_export_request_for_turn(
            &parent_turns[1],
            2,
            "parent-session",
            "tester",
            "1.0",
            None,
        );
        let followup_result = merge_subagent_sessions_into_otel_across_turns(
            &mut followup_body,
            &parent_turns,
            1,
            &[child],
            "tester",
            "1.0",
        );
        assert_eq!(
            followup_result.merged_subagent_invocation_ids,
            vec!["child-reactivated:call-followup"]
        );
        assert_eq!(followup_result.invocation_outcomes[0].status, "completed");
        let completed_agent =
            find_span_by_name(&followup_body, "agent Gibbs").expect("completed AGENT");
        let attrs = completed_agent["attributes"].as_array().unwrap();
        assert_eq!(
            attr_string(attrs, "gen_ai.tool.call.result"),
            Some("completed after resume")
        );
        assert_eq!(completed_agent["status"]["code"], 1);
    }

    #[test]
    fn pending_child_usage_accepts_both_encodings_without_promoting_empty_usage() {
        for encoding in ["stringValue", "intValue"] {
            for (tokens, completed) in [
                ("18", true),
                ("0", false),
                ("-1", false),
                ("invalid", false),
            ] {
                let span = json!({"attributes":[
                    {"key":"gen_ai.span.kind","value":{"stringValue":"LLM"}},
                    {"key":"gen_ai.usage.total_tokens","value":{(encoding):tokens}}
                ]});
                assert_eq!(is_completed_pending_child_progress_span(&span), completed);
            }
        }
    }

    #[test]
    fn test_v2_pending_activation_exports_child_progress_without_completion_checkpoint() {
        for native in [true, false] {
            let child_id = "child-v2-pending";
            let mut parent_turn = v2_parent_turn(child_id, false);
            parent_turn
                .collab_agent_events
                .retain(|event| !matches!(event, CollabAgentEvent::AgentMessage { .. }));
            let mut child = SubagentChild {
                session_id: child_id.to_string(),
                turns: vec![make_open_child_turn(
                    "2026-05-24T13:16:26.000Z",
                    "2026-05-24T13:16:38.000Z",
                )],
                nickname: "Hume".to_string(),
                role: String::new(),
                agent_path: Some("/root/hume".to_string()),
            };
            if !native {
                child.turns[0].assistants[0].usage.response_id = None;
            }
            let mut first_body = build_otel_export_request_for_turn(
                &parent_turn,
                1,
                "parent-session",
                "tester",
                "1.0",
                None,
            );
            let mut second_body = first_body.clone();

            let first_result = merge_subagent_sessions_into_otel(
                &mut first_body,
                &parent_turn,
                std::slice::from_ref(&child),
                "tester",
                "1.0",
            );
            let second_result = merge_subagent_sessions_into_otel(
                &mut second_body,
                &parent_turn,
                &[child],
                "tester",
                "1.0",
            );

            assert_eq!(first_result.deferred_sync_ids, vec![child_id]);
            assert!(first_result.merged_sync_ids.is_empty());
            assert!(first_result.merged_subagent_invocation_ids.is_empty());
            assert!(first_result.invocation_outcomes.is_empty());
            assert_eq!(second_result.deferred_sync_ids, vec![child_id]);

            assert!(find_span_by_name(&first_body, "agent Hume").is_none());
            assert!(find_span_by_name(&second_body, "agent Hume").is_none());
            let stable_parent_id = deterministic_subagent_agent_span_id(child_id, "call-spawn");
            let child_generation = exported_spans(&first_body)
                .iter()
                .find(|span| {
                    span["name"].as_str() == Some("chat gpt-5.4")
                        && span["parentSpanId"].as_str() == Some(stable_parent_id.as_str())
                })
                .expect("pending child generation should be exported");
            let second_child_generation = exported_spans(&second_body)
                .iter()
                .find(|span| {
                    span["name"].as_str() == Some("chat gpt-5.4")
                        && span["parentSpanId"].as_str() == Some(stable_parent_id.as_str())
                })
                .expect("second pending child generation should be exported");
            assert_eq!(
                child_generation["spanId"],
                second_child_generation["spanId"]
            );
            assert_eq!(child_generation["status"]["code"], 1);
        }
    }

    #[test]
    fn test_subagent_generation_span_id_uses_response_id_before_index() {
        let child_id = "child-response-stable";
        let mut parent_turn = v2_parent_turn(child_id, false);
        parent_turn
            .collab_agent_events
            .retain(|event| !matches!(event, CollabAgentEvent::AgentMessage { .. }));

        let mut base_turn =
            make_open_child_turn("2026-05-24T13:16:26.000Z", "2026-05-24T13:16:38.000Z");
        base_turn.turn_id = "child-turn-stable".to_string();
        base_turn.assistants[0].usage.response_id = Some("resp-stable-native".to_string());

        let mut shifted_turn = base_turn.clone();
        shifted_turn.assistants.insert(
            0,
            AssistantMessage {
                timestamp: "2026-05-24T13:16:27.000Z".to_string(),
                text: String::new(),
                media_content: serde_json::Value::Null,
                phase: "assistant".to_string(),
                usage: TokenUsage::default(),
                token_count_timestamp: String::new(),
                invocation_started_timestamp: String::new(),
                invocation_end_source: None,
            },
        );
        shifted_turn.assistants[1].timestamp = "2026-05-24T13:16:39.000Z".to_string();

        let export_child = |turn: Turn| {
            let child = SubagentChild {
                session_id: child_id.to_string(),
                turns: vec![turn],
                nickname: "Hume".to_string(),
                role: String::new(),
                agent_path: Some("/root/hume".to_string()),
            };
            let mut body = build_otel_export_request_for_turn(
                &parent_turn,
                1,
                "parent-session",
                "tester",
                "1.0",
                None,
            );
            let result = merge_subagent_sessions_into_otel(
                &mut body,
                &parent_turn,
                &[child],
                "tester",
                "1.0",
            );
            assert_eq!(result.deferred_sync_ids, vec![child_id]);
            let stable_parent_id = deterministic_subagent_agent_span_id(child_id, "call-spawn");
            exported_spans(&body)
                .iter()
                .find(|span| {
                    span["parentSpanId"].as_str() == Some(stable_parent_id.as_str())
                        && span["attributes"].as_array().is_some_and(|attrs| {
                            attr_string(attrs, "gen_ai.response.id") == Some("resp-stable-native")
                        })
                })
                .and_then(|span| span["spanId"].as_str())
                .expect("response-id generation should be exported")
                .to_string()
        };

        assert_eq!(export_child(base_turn), export_child(shifted_turn));
    }

    #[test]
    fn test_native_pending_activation_exports_child_progress_without_completion_checkpoint() {
        let child_id = "child-native-pending";
        let mut parent_turn = make_turn();
        parent_turn.collab_agent_events = vec![CollabAgentEvent::ToolInvocation(
            CollabAgentInvocationEvidence {
                turn_index: 0,
                source: CollabInvocationSource::NativeToolCall,
                invocation_id: "native-spawn".to_string(),
                tool_name: "spawn_agent".to_string(),
                sender_thread_id: Some("parent-session".to_string()),
                child_thread_id: Some(child_id.to_string()),
                nickname: Some("Native".to_string()),
                role: Some("executor".to_string()),
                prompt: Some("do native work".to_string()),
                model: Some("gpt-5.4".to_string()),
                started_at_unix_nano: Some(iso_to_nanos("2026-05-24T13:16:20.000Z")),
                completed_at_unix_nano: None,
                state: CollabInvocationState::Pending,
                status: "pending".to_string(),
                terminal_output: None,
                reason: "terminal_pending".to_string(),
            },
        )];
        let child = SubagentChild {
            session_id: child_id.to_string(),
            turns: vec![make_open_child_turn(
                "2026-05-24T13:16:26.000Z",
                "2026-05-24T13:16:38.000Z",
            )],
            nickname: String::new(),
            role: String::new(),
            agent_path: None,
        };
        let opts = TurnReplayOpts {
            trace_id: Some("0000000000000000000000000000cafe".to_string()),
            root_span_id: Some("1111111111111111".to_string()),
            parent_span_id: Some("upstream-parent-span".to_string()),
            ..TurnReplayOpts::default()
        };
        let mut body = build_otel_export_request_for_turn(
            &parent_turn,
            1,
            "parent-session",
            "tester",
            "1.0",
            Some(&opts),
        );

        let result = merge_subagent_sessions_into_otel_across_turns(
            &mut body,
            std::slice::from_ref(&parent_turn),
            0,
            &[child],
            "tester",
            "1.0",
        );

        assert_eq!(result.deferred_sync_ids, vec![child_id]);
        assert!(result.merged_sync_ids.is_empty());
        assert!(result.merged_subagent_invocation_ids.is_empty());
        assert!(result.invocation_outcomes.is_empty());
        assert!(result.correlation_reasons.contains(&(
            format!("{child_id}:native-spawn"),
            "native_collab_terminal_pending".to_string()
        )));
        assert!(find_span_by_name(&body, "agent Native").is_none());
        let root = &exported_spans(&body)[0];
        assert_eq!(root["spanId"].as_str(), Some("1111111111111111"));
        assert_eq!(root["parentSpanId"].as_str(), Some("upstream-parent-span"));
        let stable_parent_id = deterministic_subagent_agent_span_id(child_id, "native-spawn");
        assert!(exported_spans(&body).iter().any(|span| {
            span["name"].as_str() == Some("chat gpt-5.4")
                && span["parentSpanId"].as_str() == Some(stable_parent_id.as_str())
        }));
    }

    #[test]
    fn test_v2_active_sibling_spawn_is_not_marked_failed() {
        let child_id = "child-completed";
        let mut body = parent_body_with_spawn_and_wait(child_id);
        let active_spawn = json!({
            "traceId": "0000000000000000000000000000abcd",
            "spanId": "0000000000000030",
            "parentSpanId": "0000000000000001",
            "name": "subagent spawn_agent",
            "kind": 1,
            "startTimeUnixNano": "123000000000",
            "endTimeUnixNano": "124000000000",
            "attributes": [
                {"key": "gen_ai.span.kind", "value": {"stringValue": "TOOL"}},
                {"key": "agentic.tool.type", "value": {"stringValue": "subagent"}},
                {"key": "agentic.tool.raw_name", "value": {"stringValue": "spawn_agent"}},
                {"key": "gen_ai.tool.call.id", "value": {"stringValue": "call-spawn-active"}},
                {"key": "gen_ai.tool.call.result", "value": {"stringValue": "{\"task_name\":\"/root/active\"}"}}
            ],
            "status": {"code": 1}
        });
        body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array_mut()
            .unwrap()
            .push(active_spawn);

        let children = vec![
            SubagentChild {
                session_id: child_id.to_string(),
                turns: vec![make_child_turn(
                    "2026-05-24T13:16:26.000Z",
                    "2026-05-24T13:16:38.000Z",
                )],
                nickname: "Hume".to_string(),
                role: String::new(),
                agent_path: Some("/root/hume".to_string()),
            },
            SubagentChild {
                session_id: "child-active".to_string(),
                turns: vec![make_child_turn(
                    "2026-05-24T13:16:27.000Z",
                    "2026-05-24T13:16:37.000Z",
                )],
                nickname: "Active".to_string(),
                role: String::new(),
                agent_path: Some("/root/active".to_string()),
            },
        ];
        let parent_turn = v2_parent_turn(child_id, true);

        let result =
            merge_subagent_sessions_into_otel(&mut body, &parent_turn, &children, "tester", "1.0");

        assert_eq!(result.merged_sync_ids, vec![child_id]);
        let remaining_spawn = find_span_by_raw_name(&body, "spawn_agent");
        assert_eq!(remaining_spawn.len(), 2);
        let call_ids = remaining_spawn
            .iter()
            .filter_map(|span| {
                span["attributes"].as_array().and_then(|attrs| {
                    attrs.iter().find_map(|attr| {
                        (attr["key"] == "gen_ai.tool.call.id")
                            .then(|| attr["value"]["stringValue"].as_str())
                            .flatten()
                    })
                })
            })
            .collect::<HashSet<_>>();
        assert_eq!(call_ids, HashSet::from(["call-spawn", "call-spawn-active"]));
        for spawn in remaining_spawn {
            assert_eq!(spawn["status"]["code"], 1);
            assert!(!spawn["attributes"]
                .as_array()
                .unwrap()
                .iter()
                .any(|attr| attr["key"] == "error.type"));
        }
    }

    #[test]
    fn test_sync_agent_window_matches_child_task() {
        let child_id = "019e5a21-1ae5-7000-0000-000000000002";
        let mut body = parent_body_with_spawn_and_wait(child_id);
        // child task: 13:16:26 → 13:16:42 (16 seconds)
        let child = SubagentChild {
            session_id: child_id.to_string(),
            turns: vec![make_child_turn(
                "2026-05-24T13:16:26.000Z",
                "2026-05-24T13:16:42.000Z",
            )],
            nickname: "Plato".to_string(),
            role: "worker".to_string(),
            agent_path: Some("/root/plato".to_string()),
        };

        let parent_turn = parent_turn_with_spawn_and_wait(child_id);
        merge_subagent_sessions_into_otel(&mut body, &parent_turn, &[child], "tester", "1.0.0");

        let agent_span =
            find_span_by_name(&body, "agent Plato").expect("named AGENT span should be created");
        let start_ns: u128 = agent_span["startTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse()
            .unwrap();
        let end_ns: u128 = agent_span["endTimeUnixNano"]
            .as_str()
            .unwrap()
            .parse()
            .unwrap();

        // child task window: 13:16:26 (start) → 13:16:42 (end of last assistant)
        // = 1716556586000000000 → 1716556602000000000 = 16 seconds
        let expected_start: u128 = iso_to_nanos("2026-05-24T13:16:26.000Z").parse().unwrap();
        let expected_end: u128 = iso_to_nanos("2026-05-24T13:16:42.000Z").parse().unwrap();
        assert_eq!(
            start_ns, expected_start,
            "SyncAgent start must match child task user_timestamp"
        );
        assert_eq!(
            end_ns, expected_end,
            "SyncAgent end must match child task last assistant ts (compute_turn_end_ns)"
        );
        assert_eq!(
            (end_ns - start_ns) / 1_000_000_000,
            16,
            "SyncAgent duration must equal child task duration (16s), not parent's spawn→wait window"
        );
    }

    #[test]
    fn test_sync_agent_otel_attributes_do_not_duplicate_preserved_child_root_keys() {
        let _guard = env_lock();
        let old_span_attrs = std::env::var("CDXHOOKS_OTEL_ATTRIBUTES").ok();
        std::env::set_var(
            "CDXHOOKS_OTEL_ATTRIBUTES",
            "gen_ai.conversation.id=env-conversation,custom.cdxhooks.merge=merge-value,_internal.meta.root=spoofed",
        );

        let child_id = "019e5a21-1ae5-7000-0000-000000000003";
        let mut body = parent_body_with_spawn_and_wait(child_id);
        let child = SubagentChild {
            session_id: child_id.to_string(),
            turns: vec![make_child_turn(
                "2026-05-24T13:16:26.000Z",
                "2026-05-24T13:16:42.000Z",
            )],
            nickname: "Plato".to_string(),
            role: "worker".to_string(),
            agent_path: Some("/root/plato".to_string()),
        };

        let parent_turn = parent_turn_with_spawn_and_wait(child_id);
        merge_subagent_sessions_into_otel(&mut body, &parent_turn, &[child], "tester", "1.0.0");
        let agent_span =
            find_span_by_name(&body, "agent Plato").expect("named AGENT span should be created");
        let attrs = agent_span["attributes"].as_array().expect("agent attrs");
        let conversation_count = attrs
            .iter()
            .filter(|attr| attr["key"].as_str() == Some("gen_ai.conversation.id"))
            .count();
        let conversation = attr_string(attrs, "gen_ai.conversation.id").map(ToString::to_string);
        let custom_merge = attr_string(attrs, "custom.cdxhooks.merge").map(ToString::to_string);
        let root_marker = attr_string(attrs, "_internal.meta.root").map(ToString::to_string);

        restore_env("CDXHOOKS_OTEL_ATTRIBUTES", old_span_attrs);

        assert_eq!(
            conversation_count, 1,
            "CDXHOOKS_OTEL_ATTRIBUTES must not duplicate child-root keys preserved on SyncAgent"
        );
        assert_eq!(conversation.as_deref(), Some(child_id));
        assert_eq!(custom_merge.as_deref(), Some("merge-value"));
        assert_eq!(root_marker, None);
    }

    #[test]
    fn test_sync_agent_privacy_redacts_prompt_and_terminal_output() {
        let _privacy_mode = TestPrivacyModeGuard::set(true);

        let child_id = "019e5a21-1ae5-7000-0000-000000000004";
        let mut body = parent_body_with_spawn_and_wait(child_id);
        let child = SubagentChild {
            session_id: child_id.to_string(),
            turns: vec![make_child_turn(
                "2026-05-24T13:16:26.000Z",
                "2026-05-24T13:16:42.000Z",
            )],
            nickname: "Plato".to_string(),
            role: "worker".to_string(),
            agent_path: Some("/root/plato".to_string()),
        };

        let parent_turn = parent_turn_with_spawn_and_wait(child_id);
        merge_subagent_sessions_into_otel(&mut body, &parent_turn, &[child], "tester", "1.0.0");
        let agent_span =
            find_span_by_name(&body, "agent Plato").expect("named AGENT span should be created");
        let attrs = agent_span["attributes"].as_array().expect("agent attrs");
        let prompt = attr_string(attrs, "gen_ai.tool.call.arguments").map(ToString::to_string);
        let output = attr_string(attrs, "gen_ai.tool.call.result").map(ToString::to_string);

        assert_eq!(prompt.as_deref(), Some("[REDACTED]"));
        assert_eq!(output.as_deref(), Some("[REDACTED]"));
    }

    #[test]
    fn test_subagent_spans_in_otel_export() {
        use crate::common::transcript::CollabAgentEvent;
        let mut turn = make_turn();
        turn.collab_agent_events = vec![CollabAgentEvent::SpawnEnd {
            call_id: "call-spawn-1".to_string(),
            sender_thread_id: "thread-parent".to_string(),
            new_thread_id: Some("thread-child-1".to_string()),
            nickname: Some("worker".to_string()),
            role: Some("coder".to_string()),
            prompt: "analyze code".to_string(),
            model: "gpt-5.4".to_string(),
            status: "completed".to_string(),
        }];

        let body = build_otel_export_request_for_turn(&turn, 1, "sess-1", "tester", "1.0.0", None);
        let spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
            .as_array()
            .expect("spans should exist");

        // root(1) + generation(2) + tool(1) + subagent(1) = 5
        assert_eq!(spans.len(), 5);
        assert_eq!(spans[4]["name"], "SUBAGENT worker");

        let sub_attrs = spans[4]["attributes"].as_array().unwrap();
        let has_key = |key: &str| sub_attrs.iter().any(|a| a["key"] == key);
        assert!(has_key("agentic.subagent.type"));
        assert!(has_key("agentic.subagent.call_id"));
        assert!(has_key("agentic.subagent.sender_thread_id"));
        assert!(has_key("agentic.subagent.child_thread_id"));
        assert!(has_key("agentic.subagent.nickname"));
        assert!(has_key("agentic.subagent.status"));
        assert!(!has_key("_internal.meta.root"));
    }

    #[test]
    fn suppress_child_spans_removes_only_exact_accounted_ids_across_scopes() {
        let mut body = json!({
            "resourceSpans": [
                {
                    "scopeSpans": [
                        {"spans": [
                            {"spanId": "root", "name": "root"},
                            {"spanId": "accounted-tool", "name": "tool"},
                            {"spanId": "missing-generation", "name": "generation"}
                        ]},
                        {"spans": [
                            {"spanId": "accounted-generation", "name": "generation"},
                            {"spanId": "subagent-root", "name": "subagent"}
                        ]}
                    ]
                }
            ]
        });
        let accounted = HashSet::from([
            "accounted-tool".to_string(),
            "accounted-generation".to_string(),
        ]);

        assert_eq!(suppress_child_spans_by_id(&mut body, &accounted), 2);
        let remaining: Vec<&str> = body["resourceSpans"][0]["scopeSpans"]
            .as_array()
            .unwrap()
            .iter()
            .flat_map(|scope| scope["spans"].as_array().unwrap())
            .filter_map(|span| span["spanId"].as_str())
            .collect();
        assert_eq!(
            remaining,
            vec!["root", "missing-generation", "subagent-root"]
        );
    }

    #[test]
    fn suppress_child_spans_returns_zero_when_accounted_ids_do_not_match() {
        let mut body = json!({
            "resourceSpans": [{
                "scopeSpans": [{
                    "spans": [
                        {"spanId": "root", "name": "root"},
                        {"spanId": "child", "name": "child"}
                    ]
                }]
            }]
        });
        let original = body.clone();
        let accounted = HashSet::from(["other-child".to_string()]);

        assert_eq!(suppress_child_spans_by_id(&mut body, &accounted), 0);
        assert_eq!(body, original);
    }

    #[test]
    fn vcs_injection_upserts_every_resource_without_duplicates() {
        let mut body = json!({
            "resourceSpans": [
                {
                    "resource": {"attributes": [
                        {"key": "service.name", "value": {"stringValue": "codex"}},
                        {"key": "vcs.repository.url.full", "value": {"stringValue": "old"}},
                        {"key": "vcs.repository.url.full", "value": {"stringValue": "duplicate"}}
                    ]},
                    "scopeSpans": []
                },
                {
                    "resource": {},
                    "scopeSpans": []
                }
            ]
        });

        inject_vcs_resource_attrs(&mut body, "ssh://example/repo.git", "main");
        let once = body.clone();
        inject_vcs_resource_attrs(&mut body, "ssh://example/repo.git", "main");
        assert_eq!(body, once, "repeated enrichment must be byte-stable JSON");

        for resource in body["resourceSpans"].as_array().unwrap() {
            let attrs = resource["resource"]["attributes"].as_array().unwrap();
            assert_eq!(
                attrs
                    .iter()
                    .filter(|attr| attr["key"] == VCS_REPOSITORY_URL_KEY)
                    .count(),
                1
            );
            assert_eq!(
                attrs
                    .iter()
                    .filter(|attr| attr["key"] == VCS_BRANCH_KEY)
                    .count(),
                1
            );
            assert_eq!(
                attr_string(attrs, VCS_REPOSITORY_URL_KEY),
                Some("ssh://example/repo.git")
            );
            assert_eq!(attr_string(attrs, VCS_BRANCH_KEY), Some("main"));
        }
    }

    #[test]
    fn child_session_isolation_replaces_parent_vcs_provenance() {
        let span = |id: &str, session_id: &str| {
            json!({
                "spanId": id,
                "attributes": [{
                    "key": "gen_ai.conversation.id",
                    "value": {"stringValue": session_id}
                }]
            })
        };
        let mut body = json!({
            "resourceSpans": [{
                "resource": {"attributes": [
                    {"key": "service.name", "value": {"stringValue": "codex"}},
                    {"key": "vcs.repository.url.full", "value": {"stringValue": "parent.git"}},
                    {"key": "vcs.ref.head.name", "value": {"stringValue": "parent"}}
                ]},
                "scopeSpans": [{"spans": [
                    span("parent", "parent-session"),
                    span("child", "child-session")
                ]}]
            }]
        });

        assert_eq!(
            isolate_session_vcs_resource(
                &mut body,
                "child-session",
                "ssh://example/child.git",
                "child"
            ),
            1
        );
        let once = body.clone();
        assert_eq!(
            isolate_session_vcs_resource(
                &mut body,
                "child-session",
                "ssh://example/child.git",
                "child"
            ),
            1
        );
        assert_eq!(body, once, "session resource isolation must be idempotent");
        assert_eq!(body["resourceSpans"].as_array().unwrap().len(), 2);

        let resources = body["resourceSpans"].as_array().unwrap();
        let parent_attrs = resources[0]["resource"]["attributes"].as_array().unwrap();
        let child_attrs = resources[1]["resource"]["attributes"].as_array().unwrap();
        assert_eq!(
            attr_string(parent_attrs, VCS_REPOSITORY_URL_KEY),
            Some("parent.git")
        );
        assert_eq!(
            attr_string(child_attrs, VCS_REPOSITORY_URL_KEY),
            Some("ssh://example/child.git")
        );
        assert_eq!(attr_string(child_attrs, VCS_BRANCH_KEY), Some("child"));

        isolate_session_vcs_resource(&mut body, "child-session", "", "");
        let child_attrs = body["resourceSpans"][1]["resource"]["attributes"]
            .as_array()
            .unwrap();
        assert_eq!(attr_string(child_attrs, VCS_REPOSITORY_URL_KEY), None);
        assert_eq!(attr_string(child_attrs, VCS_BRANCH_KEY), None);
    }

    #[test]
    fn last_user_message_text_unwraps_prompt_prose() {
        // Codex stores gen_ai.input.messages as a serialized array; the dashboard
        // prompt tile must receive the prose, not the JSON envelope.
        assert_eq!(
            last_user_message_text(r#"[{"role":"user","content":"2+2等于几？"}]"#).as_deref(),
            Some("2+2等于几？")
        );
        // Multimodal content parts are concatenated.
        assert_eq!(
            last_user_message_text(
                r#"[{"role":"user","content":[{"type":"input_text","text":"a"},{"type":"input_text","text":"b"}]}]"#
            )
            .as_deref(),
            Some("a\nb")
        );
        // The trailing user message wins over earlier ones and over other roles.
        assert_eq!(
            last_user_message_text(
                r#"[{"role":"user","content":"first"},{"role":"assistant","content":"reply"},{"role":"user","content":"second"}]"#
            )
            .as_deref(),
            Some("second")
        );
        // Not a message array / empty content -> caller keeps the raw attribute.
        assert_eq!(last_user_message_text("not json"), None);
        assert_eq!(last_user_message_text(r#"{"role":"user"}"#), None);
        assert_eq!(
            last_user_message_text(r#"[{"role":"user","content":"   "}]"#),
            None
        );
    }

    #[test]
    fn span_tool_name_prefers_raw_name_and_strips_kind_prefix() {
        let raw = json!({
            "attributes": [
                { "key": "gen_ai.tool.name", "value": { "stringValue": "tool Bash" } },
                { "key": "agentic.tool.raw_name", "value": { "stringValue": "Bash" } },
            ]
        });
        assert_eq!(span_tool_name(&raw), "Bash");

        // MCP/skill spans keep their resolved name when raw_name is missing.
        let prefixed = json!({
            "attributes": [
                { "key": "gen_ai.tool.name", "value": { "stringValue": "mcp node_repl.js" } },
            ]
        });
        assert_eq!(span_tool_name(&prefixed), "node_repl.js");

        let blank = json!({
            "attributes": [
                { "key": "agentic.tool.raw_name", "value": { "stringValue": "  " } },
                { "key": "gen_ai.tool.name", "value": { "stringValue": "tool exec" } },
            ]
        });
        assert_eq!(span_tool_name(&blank), "exec");

        assert_eq!(span_tool_name(&json!({ "attributes": [] })), "");
    }

    #[test]
    fn span_success_follows_otel_status_code() {
        // Rule 7: only an explicit ERROR status counts as a failure; unset (0) and
        // OK (1) stay green so legacy spans are not retroactively marked failed.
        assert!(span_succeeded(&json!({})));
        assert!(span_succeeded(&json!({ "status": { "code": 1 } })));
        assert!(!span_succeeded(&json!({ "status": { "code": 2, "message": "boom" } })));
        assert_eq!(span_status_code(&json!({ "status": { "code": 2 } })), 2);
    }
}
