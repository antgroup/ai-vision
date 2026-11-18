//! semantics ownership module.

use super::types::{classify_compaction_usage, TokenUsageSource};
use super::{
    extract_agent_message_content, extract_agent_message_type, extract_pty_session_id,
    extract_response_message_text, mcp_tool_result_output, normalize_tool_call,
    parse_jsonish_value, AssistantMessage, TokenUsage, ToolCall, TurnTerminalState,
};
use super::{iso_to_nanos, json, looks_like_iso_timestamp, HashMap, HashSet, Value};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InvocationTerminalSource {
    UsageAndCallbacks,
    TurnTerminal,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InvocationStartSource {
    ModelEvent,
    UserSubmission,
    PreviousCallback,
    TurnStart,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InvocationEndSource {
    AssistantOutputOrAction,
    TurnTerminal,
    UsageFallback,
    NativeUsagePending,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InvocationTiming {
    pub started_at: String,
    pub completed_at: String,
    pub start_source: InvocationStartSource,
    pub end_source: InvocationEndSource,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ModelInvocation {
    pub ordinal: usize,
    pub opened_at: String,
    /// A persisted response item is completed model output, not a lifecycle
    /// start event. Its write timestamp must not replace a causal start.
    pub opened_from_reasoning_output: bool,
    pub reasoning_timestamps: Vec<String>,
    pub messages: Vec<AssistantMessage>,
    pub usage: TokenUsage,
    pub usage_timestamp: String,
    pub callback_ids: Vec<String>,
    pub output_at: String,
    pub terminal: bool,
    pub terminal_source: Option<InvocationTerminalSource>,
}

impl ModelInvocation {
    pub fn has_output_or_action(&self) -> bool {
        !self.messages.is_empty() || !self.callback_ids.is_empty()
    }

    pub fn has_native_usage_record(&self) -> bool {
        self.usage.has_any() && self.usage.is_native_record()
    }
}

fn native_usage_record_payload(entry: &Value) -> Option<&Value> {
    match entry.get("type").and_then(Value::as_str) {
        Some("token_usage_record") => entry.get("payload"),
        Some("compacted") => entry
            .get("payload")
            .and_then(|payload| payload.get("latest_token_usage_record")),
        _ => None,
    }
}

pub(crate) fn has_native_usage_record(entry: &Value) -> bool {
    let Some(payload) = native_usage_record_payload(entry) else {
        return false;
    };
    let has_response_id = payload
        .get("response_id")
        .and_then(Value::as_str)
        .is_some_and(|value| !value.is_empty());
    let has_usage = payload
        .get("usage")
        .map(TokenUsage::from_usage_value)
        .is_some_and(|usage| usage.has_any());
    has_response_id && has_usage
}

fn usage_progress_value(usage: &TokenUsage) -> Option<i64> {
    usage.total.or_else(|| match (usage.input, usage.output) {
        (Some(input), Some(output)) => Some(input.saturating_add(output)),
        (Some(input), None) => Some(input),
        (None, Some(output)) => Some(output),
        _ => None,
    })
}

fn cumulative_usage_allows_legacy_record(
    previous: Option<&TokenUsage>,
    current: Option<&TokenUsage>,
) -> bool {
    let Some(current) = current else {
        return true;
    };
    let Some(current_progress) = usage_progress_value(current) else {
        return true;
    };
    let Some(previous_progress) = previous.and_then(usage_progress_value) else {
        return true;
    };
    current_progress != previous_progress
}

#[derive(Debug, Clone, PartialEq)]
pub struct ToolCallback {
    pub callback_id: String,
    pub invocation_ordinal: usize,
    pub name: String,
    pub input: Value,
    pub output: Value,
    pub requested_at: String,
    pub completed_at: String,
    pub success: Option<bool>,
    pub continuation_of: Option<String>,
    pub terminal: bool,
    /// The callback is an `exec` wrapper for concrete item-level tools.
    /// Keep it for invocation accounting, but suppress its duplicate span.
    pub suppress_if_nested: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct NormalizedTurnSemantics {
    pub turn_id: String,
    pub turn_started_at: String,
    pub user_submitted_at: String,
    pub terminal_at: String,
    pub native_usage_mode: bool,
    pub invocations: Vec<ModelInvocation>,
    pub callbacks: Vec<ToolCallback>,
    pub terminal_state: TurnTerminalState,
}

pub(super) fn timestamp_nanos(timestamp: &str) -> u128 {
    iso_to_nanos(timestamp).parse::<u128>().unwrap_or(0)
}

impl NormalizedTurnSemantics {
    fn assistant_from_invocation(&self, invocation: &ModelInvocation) -> Option<AssistantMessage> {
        let timing = self.invocation_timing(invocation.ordinal);
        let text = invocation
            .messages
            .iter()
            .map(|message| message.text.trim())
            .filter(|text| !text.is_empty())
            .collect::<Vec<_>>()
            .join("\n\n");
        let phase = invocation
            .messages
            .last()
            .map(|message| message.phase.clone())
            .unwrap_or_else(|| "assistant".to_string());
        let timestamp = timing
            .as_ref()
            .map(|timing| timing.completed_at.clone())
            .unwrap_or_else(|| invocation.output_at.clone());
        if timestamp.is_empty() {
            return None;
        }
        Some(AssistantMessage {
            media_content: Value::Array(invocation.messages.iter().filter_map(|m| m.media_content.as_array()).flatten().cloned().collect()),
            timestamp,
            text,
            phase,
            usage: invocation.usage.clone(),
            token_count_timestamp: invocation.usage_timestamp.clone(),
            invocation_started_timestamp: timing
                .as_ref()
                .map(|timing| timing.started_at.clone())
                .unwrap_or_else(|| invocation.opened_at.clone()),
            invocation_end_source: timing.map(|timing| timing.end_source),
        })
    }

    fn should_derive_assistant(
        &self,
        invocation: &ModelInvocation,
        preserve_superseded_messages: bool,
    ) -> bool {
        invocation.terminal
            && (invocation.has_output_or_action() || invocation.has_native_usage_record())
            || preserve_superseded_messages
                && self.native_usage_mode
                && self.terminal_state == TurnTerminalState::Superseded
                && !invocation.messages.is_empty()
    }

    pub fn invocation_timing(&self, ordinal: usize) -> Option<InvocationTiming> {
        let invocation = self.invocations.get(ordinal)?;
        let first_callback_at = self
            .callbacks
            .iter()
            .filter(|callback| {
                callback.invocation_ordinal == ordinal && !callback.requested_at.is_empty()
            })
            .min_by_key(|callback| timestamp_nanos(&callback.requested_at))
            .map(|callback| callback.requested_at.clone());
        let output_boundary = first_callback_at.as_deref().or_else(|| {
            (!invocation.output_at.is_empty()).then_some(invocation.output_at.as_str())
        });
        let opened_is_output_boundary = invocation.opened_from_reasoning_output
            || !invocation.opened_at.is_empty()
                && output_boundary == Some(invocation.opened_at.as_str());
        let previous_callback_at = self
            .callbacks
            .iter()
            .filter(|callback| {
                callback.invocation_ordinal < ordinal
                    && callback.terminal
                    && !callback.completed_at.is_empty()
            })
            .max_by_key(|callback| timestamp_nanos(&callback.completed_at))
            .map(|callback| callback.completed_at.clone());

        let (started_at, start_source) =
            if !invocation.opened_at.is_empty() && !opened_is_output_boundary {
                (
                    invocation.opened_at.clone(),
                    InvocationStartSource::ModelEvent,
                )
            } else if ordinal == 0 && !self.user_submitted_at.is_empty() {
                (
                    self.user_submitted_at.clone(),
                    InvocationStartSource::UserSubmission,
                )
            } else if let Some(completed_at) = previous_callback_at {
                (completed_at, InvocationStartSource::PreviousCallback)
            } else if ordinal == 0 && !self.turn_started_at.is_empty() {
                (
                    self.turn_started_at.clone(),
                    InvocationStartSource::TurnStart,
                )
            } else if !invocation.opened_at.is_empty() {
                (
                    invocation.opened_at.clone(),
                    InvocationStartSource::ModelEvent,
                )
            } else {
                (
                    self.turn_started_at.clone(),
                    InvocationStartSource::TurnStart,
                )
            };

        let (completed_at, end_source) = if let Some(requested_at) = first_callback_at {
            (requested_at, InvocationEndSource::AssistantOutputOrAction)
        } else if !invocation.output_at.is_empty() {
            (
                invocation.output_at.clone(),
                InvocationEndSource::AssistantOutputOrAction,
            )
        } else if invocation.terminal_source == Some(InvocationTerminalSource::TurnTerminal)
            && !self.terminal_at.is_empty()
        {
            (self.terminal_at.clone(), InvocationEndSource::TurnTerminal)
        } else if !invocation.usage_timestamp.is_empty() {
            (
                invocation.usage_timestamp.clone(),
                InvocationEndSource::UsageFallback,
            )
        } else {
            return None;
        };

        let start_ns = timestamp_nanos(&started_at);
        let end_ns = timestamp_nanos(&completed_at);
        let completed_at = if end_ns < start_ns {
            started_at.clone()
        } else {
            completed_at
        };
        Some(InvocationTiming {
            started_at,
            completed_at,
            start_source,
            end_source,
        })
    }

    pub fn derived_assistants(&self) -> Vec<AssistantMessage> {
        self.derived_assistants_with_index_map(false).0
    }

    pub(crate) fn derived_turn_parts(
        &self,
        preserve_superseded_messages: bool,
    ) -> (Vec<AssistantMessage>, Vec<ToolCall>) {
        let (assistants, ordinal_to_assistant_index) =
            self.derived_assistants_with_index_map(preserve_superseded_messages);
        let tool_calls = self
            .derived_tool_calls_with_assistant_index(&ordinal_to_assistant_index, assistants.len());
        (assistants, tool_calls)
    }

    fn derived_assistants_with_index_map(
        &self,
        preserve_superseded_messages: bool,
    ) -> (Vec<AssistantMessage>, HashMap<usize, usize>) {
        let mut assistants = Vec::new();
        let mut ordinal_to_assistant_index = HashMap::new();
        for invocation in &self.invocations {
            if !self.should_derive_assistant(invocation, preserve_superseded_messages) {
                continue;
            }
            let Some(mut assistant) = self.assistant_from_invocation(invocation) else {
                continue;
            };
            if !invocation.terminal {
                assistant.usage = TokenUsage::default();
                assistant.token_count_timestamp.clear();
                assistant.invocation_end_source = Some(InvocationEndSource::NativeUsagePending);
            }
            let assistant_index = assistants.len();
            assistants.push(assistant);
            ordinal_to_assistant_index.insert(invocation.ordinal, assistant_index);
        }
        (assistants, ordinal_to_assistant_index)
    }

    pub fn derived_tool_calls(&self) -> Vec<ToolCall> {
        let (assistants, ordinal_to_assistant_index) =
            self.derived_assistants_with_index_map(false);
        self.derived_tool_calls_with_assistant_index(&ordinal_to_assistant_index, assistants.len())
    }

    fn derived_tool_calls_with_assistant_index(
        &self,
        ordinal_to_assistant_index: &HashMap<usize, usize>,
        assistant_count: usize,
    ) -> Vec<ToolCall> {
        self.callbacks
            .iter()
            .filter(|callback| !callback.suppress_if_nested)
            .map(|callback| ToolCall {
                call_id: callback.callback_id.clone(),
                name: callback.name.clone(),
                input: callback.input.clone(),
                output: callback.output.clone(),
                started_timestamp: callback.requested_at.clone(),
                ended_timestamp: callback.completed_at.clone(),
                assistant_index: ordinal_to_assistant_index
                    .get(&callback.invocation_ordinal)
                    .copied()
                    .unwrap_or_else(|| {
                        if assistant_count == 0 {
                            callback.invocation_ordinal
                        } else {
                            assistant_count.saturating_add(callback.invocation_ordinal)
                        }
                    }),
                success: callback.success,
            })
            .collect()
    }
}

#[derive(Debug, Default)]
pub(super) struct NormalizedTurnBuilder {
    turn_id: String,
    turn_started_at: String,
    user_submitted_at: String,
    terminal_at: String,
    invocations: Vec<ModelInvocation>,
    callbacks: Vec<ToolCallback>,
    callback_index: HashMap<String, usize>,
    seen_native_usage_response_ids: HashSet<String>,
    native_usage_mode: bool,
    pty_root_by_session: HashMap<String, String>,
    last_exec_wrapper: Option<ExecWrapperContext>,
    terminal_state: TurnTerminalState,
}

#[derive(Debug, Clone)]
struct ExecWrapperContext {
    callback_id: String,
    invocation_ordinal: usize,
}

impl NormalizedTurnBuilder {
    fn last_observed_timestamp(&self) -> String {
        let mut timestamps = vec![
            self.turn_started_at.as_str(),
            self.user_submitted_at.as_str(),
            self.terminal_at.as_str(),
        ];
        for invocation in &self.invocations {
            timestamps.push(invocation.opened_at.as_str());
            timestamps.push(invocation.usage_timestamp.as_str());
            timestamps.push(invocation.output_at.as_str());
            for timestamp in &invocation.reasoning_timestamps {
                timestamps.push(timestamp.as_str());
            }
            for message in &invocation.messages {
                timestamps.push(message.timestamp.as_str());
                timestamps.push(message.token_count_timestamp.as_str());
                timestamps.push(message.invocation_started_timestamp.as_str());
            }
        }
        for callback in &self.callbacks {
            timestamps.push(callback.requested_at.as_str());
            timestamps.push(callback.completed_at.as_str());
        }
        timestamps
            .into_iter()
            .filter(|timestamp| !timestamp.is_empty())
            .max_by(|left, right| {
                timestamp_nanos(left)
                    .cmp(&timestamp_nanos(right))
                    .then_with(|| left.cmp(right))
            })
            .unwrap_or("")
            .to_string()
    }

    fn mark_superseded(&mut self) {
        self.terminal_state = TurnTerminalState::Superseded;
        self.terminal_at = self.last_observed_timestamp();
    }

    fn accepts_terminal_for(&self, payload_turn_id: Option<&str>) -> bool {
        payload_turn_id.is_none_or(|payload_turn_id| {
            payload_turn_id.is_empty() || self.turn_id.is_empty() || self.turn_id == payload_turn_id
        })
    }

    fn current_invocation_is_closed(&self) -> bool {
        let Some(invocation) = self.invocations.last() else {
            return false;
        };
        if invocation.has_native_usage_record() {
            return true;
        }
        invocation.has_output_or_action()
            && invocation.usage.has_any()
            && invocation.callback_ids.iter().all(|callback_id| {
                self.callback_index
                    .get(callback_id)
                    .and_then(|index| self.callbacks.get(*index))
                    .is_some_and(|callback| callback.terminal)
            })
    }

    fn current_invocation_has_completed_callbacks(&self) -> bool {
        let Some(invocation) = self.invocations.last() else {
            return false;
        };
        !invocation.callback_ids.is_empty()
            && invocation.callback_ids.iter().all(|callback_id| {
                self.callback_index
                    .get(callback_id)
                    .and_then(|index| self.callbacks.get(*index))
                    .is_some_and(|callback| callback.terminal)
            })
    }

    fn ensure_invocation(&mut self, timestamp: &str) -> usize {
        let starts_new_invocation = self.invocations.is_empty()
            || self.current_invocation_is_closed()
            || self.current_invocation_has_completed_callbacks();
        if starts_new_invocation {
            let ordinal = self.invocations.len();
            self.invocations.push(ModelInvocation {
                ordinal,
                opened_at: timestamp.to_string(),
                opened_from_reasoning_output: false,
                reasoning_timestamps: Vec::new(),
                messages: Vec::new(),
                usage: TokenUsage::default(),
                usage_timestamp: String::new(),
                callback_ids: Vec::new(),
                output_at: String::new(),
                terminal: false,
                terminal_source: None,
            });
            self.last_exec_wrapper = None;
        }
        self.invocations.len().saturating_sub(1)
    }

    fn record_reasoning(&mut self, timestamp: &str, completed_output: bool) {
        let invocation_count = self.invocations.len();
        let ordinal = self.ensure_invocation(timestamp);
        let invocation = &mut self.invocations[ordinal];
        if ordinal == invocation_count {
            invocation.opened_from_reasoning_output = completed_output;
        }
        if !timestamp.is_empty()
            && !invocation
                .reasoning_timestamps
                .iter()
                .any(|existing| existing == timestamp)
        {
            invocation.reasoning_timestamps.push(timestamp.to_string());
        }
    }

    fn record_message(&mut self, timestamp: &str, text: &str, phase: &str) {
        let text = text.trim();
        if text.is_empty() {
            return;
        }
        let ordinal = self.ensure_invocation(timestamp);
        let invocation = &mut self.invocations[ordinal];
        if invocation
            .messages
            .iter()
            .any(|message| message.text == text && message.phase == phase)
        {
            return;
        }
        invocation.messages.push(AssistantMessage {
            timestamp: timestamp.to_string(),
            text: text.to_string(),
            media_content: serde_json::Value::Null,
            phase: phase.to_string(),
            usage: TokenUsage::default(),
            token_count_timestamp: String::new(),
            invocation_started_timestamp: String::new(),
            invocation_end_source: None,
        });
        if !timestamp.is_empty() {
            invocation.output_at = timestamp.to_string();
        }
    }

    fn record_usage(&mut self, timestamp: &str, usage: TokenUsage) {
        let Some(invocation) = self.invocations.last_mut() else {
            return;
        };
        if !invocation.has_output_or_action() || invocation.usage.has_any() {
            return;
        }
        invocation.usage = usage
            .clone()
            .with_identity(None, TokenUsageSource::LegacyTokenCount);
        invocation.usage_timestamp = timestamp.to_string();
        if let Some(message) = invocation.messages.last_mut() {
            message.usage = invocation.usage.clone();
            message.token_count_timestamp = timestamp.to_string();
        }
    }

    fn record_native_usage(&mut self, timestamp: &str, payload: &Value) -> bool {
        let response_id = payload
            .get("response_id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_string);
        let Some(response_id) = response_id else {
            return false;
        };

        if let Some(payload_turn_id) = payload
            .get("turn_id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        {
            if self.turn_id.is_empty() {
                self.turn_id = payload_turn_id.to_string();
            } else if self.turn_id != payload_turn_id {
                return false;
            }
        }

        let Some(raw_usage) = payload.get("usage") else {
            return false;
        };
        let usage = TokenUsage::from_usage_value(raw_usage)
            .with_identity(Some(response_id), TokenUsageSource::NativeRecord);
        if !usage.has_any() {
            return false;
        }
        let Some(response_id) = usage.response_id.clone() else {
            return false;
        };
        if !self.seen_native_usage_response_ids.insert(response_id) {
            return false;
        }

        let ordinal = if self.invocations.last().is_some_and(|invocation| {
            !invocation.usage.has_any()
                || invocation.usage.source == Some(TokenUsageSource::LegacyTokenCount)
        }) {
            self.invocations.len().saturating_sub(1)
        } else {
            self.ensure_invocation(timestamp)
        };
        let invocation = &mut self.invocations[ordinal];
        if invocation.usage.has_any()
            && invocation.usage.source != Some(TokenUsageSource::LegacyTokenCount)
        {
            return false;
        }
        invocation.usage = usage.clone();
        invocation.usage_timestamp = timestamp.to_string();
        if let Some(message) = invocation.messages.last_mut() {
            message.usage = usage;
            message.token_count_timestamp = timestamp.to_string();
        }
        if self.terminal_state == TurnTerminalState::Superseded {
            self.terminal_at = self.last_observed_timestamp();
        }
        true
    }

    fn record_callback(
        &mut self,
        timestamp: &str,
        callback_id: &str,
        raw_name: &str,
        parsed_args: &Value,
    ) {
        if callback_id.is_empty() || self.callback_index.contains_key(callback_id) {
            return;
        }
        let invocation_ordinal = self.ensure_invocation(timestamp);
        let (name, input) = normalize_tool_call(raw_name, parsed_args);
        let continuation_of = (raw_name == "write_stdin")
            .then(|| {
                parsed_args
                    .get("session_id")
                    .and_then(|value| {
                        value
                            .as_i64()
                            .map(|value| value.to_string())
                            .or_else(|| value.as_str().map(str::to_string))
                    })
                    .and_then(|session_id| self.pty_root_by_session.get(&session_id).cloned())
            })
            .flatten();
        let index = self.callbacks.len();
        self.callbacks.push(ToolCallback {
            callback_id: callback_id.to_string(),
            invocation_ordinal,
            name,
            input,
            output: Value::Null,
            requested_at: timestamp.to_string(),
            completed_at: String::new(),
            success: None,
            continuation_of,
            terminal: false,
            suppress_if_nested: false,
        });
        self.callback_index.insert(callback_id.to_string(), index);
        if raw_name == "exec" {
            self.last_exec_wrapper = Some(ExecWrapperContext {
                callback_id: callback_id.to_string(),
                invocation_ordinal,
            });
        }
        self.invocations[invocation_ordinal]
            .callback_ids
            .push(callback_id.to_string());
        if !timestamp.is_empty() {
            self.invocations[invocation_ordinal].output_at = timestamp.to_string();
        }
    }

    fn complete_callback(
        &mut self,
        timestamp: &str,
        callback_id: &str,
        output: Value,
        success: Option<bool>,
    ) {
        let Some(index) = self.callback_index.get(callback_id).copied() else {
            return;
        };
        let Some(callback) = self.callbacks.get_mut(index) else {
            return;
        };
        callback.output = output.clone();
        callback.completed_at = timestamp.to_string();
        callback.success = success.or(callback.success);
        callback.terminal = true;
        if callback.name == "Bash" {
            if let Some(session_id) = output.as_str().and_then(extract_pty_session_id) {
                self.pty_root_by_session
                    .insert(session_id, callback.callback_id.clone());
            }
        }
    }

    fn record_immediate_callback(
        &mut self,
        timestamp: &str,
        callback_id: String,
        name: &str,
        input: Value,
        output: Value,
        success: Option<bool>,
    ) {
        let invocation_ordinal = self.ensure_invocation(timestamp);
        let index = self.callbacks.len();
        self.callbacks.push(ToolCallback {
            callback_id: callback_id.clone(),
            invocation_ordinal,
            name: name.to_string(),
            input,
            output,
            requested_at: timestamp.to_string(),
            completed_at: timestamp.to_string(),
            success,
            continuation_of: None,
            terminal: true,
            suppress_if_nested: false,
        });
        self.callback_index.insert(callback_id.clone(), index);
        self.invocations[invocation_ordinal]
            .callback_ids
            .push(callback_id);
        if !timestamp.is_empty() {
            self.invocations[invocation_ordinal].output_at = timestamp.to_string();
        }
    }

    fn record_item_completed_tool(&mut self, timestamp: &str, payload: &Value) {
        let Some(item) = payload.get("item") else {
            return;
        };
        let item_type = item.get("type").and_then(Value::as_str).unwrap_or("");
        let callback_id = item
            .get("id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or("");
        if callback_id.is_empty() || self.callback_index.contains_key(callback_id) {
            return;
        }

        let (raw_name, parsed_args, output, success) = match item_type {
            "CommandExecution" => {
                let command = item.get("command").cloned().unwrap_or(Value::Null);
                let args = json!({"command": command});
                let output = item
                    .get("aggregated_output")
                    .or_else(|| item.get("formatted_output"))
                    .or_else(|| item.get("stdout"))
                    .or_else(|| item.get("stderr"))
                    .cloned()
                    .unwrap_or_else(|| item.clone());
                let success = match item.get("status").and_then(Value::as_str) {
                    Some("completed") => item
                        .get("exit_code")
                        .and_then(Value::as_i64)
                        .map(|code| code == 0)
                        .or(Some(true)),
                    Some("failed") | Some("cancelled") | Some("aborted") => Some(false),
                    _ => item
                        .get("exit_code")
                        .and_then(Value::as_i64)
                        .map(|code| code == 0),
                };
                ("exec_command".to_string(), args, output, success)
            }
            "McpToolCall" => {
                let server = item.get("server").and_then(Value::as_str).unwrap_or("");
                let tool = item.get("tool").and_then(Value::as_str).unwrap_or("");
                if server.is_empty() || tool.is_empty() {
                    return;
                }
                let result = item.get("result").cloned().unwrap_or(Value::Null);
                let is_error = result
                    .get("isError")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                let wrapped_result = if is_error {
                    json!({"Err": result})
                } else {
                    json!({"Ok": result})
                };
                let success = match item.get("status").and_then(Value::as_str) {
                    Some("completed") => Some(!is_error),
                    Some("failed") | Some("cancelled") | Some("aborted") => Some(false),
                    _ => None,
                };
                (
                    format!("mcp__{server}__{tool}"),
                    item.get("arguments").cloned().unwrap_or(Value::Null),
                    mcp_tool_result_output(&wrapped_result),
                    success,
                )
            }
            _ => return,
        };

        // Codex emits the concrete item after the custom `exec` wrapper. Keep
        // all children of the same wrapper in its invocation so OTel can
        // assign one parallel group to the batch.
        let parent_index = self
            .last_exec_wrapper
            .as_ref()
            .filter(|wrapper| {
                wrapper.invocation_ordinal == self.invocations.len().saturating_sub(1)
            })
            .and_then(|wrapper| self.callback_index.get(&wrapper.callback_id).copied());
        let invocation_ordinal = parent_index
            .and_then(|index| {
                self.callbacks
                    .get(index)
                    .map(|callback| callback.invocation_ordinal)
            })
            .unwrap_or_else(|| self.ensure_invocation(timestamp));
        if let Some(parent_index) = parent_index {
            if let Some(parent) = self.callbacks.get_mut(parent_index) {
                parent.suppress_if_nested = true;
            }
        }

        let (name, input) = normalize_tool_call(&raw_name, &parsed_args);
        let started_timestamp = payload
            .get("started_at_ms")
            .or_else(|| item.get("started_at_ms"))
            .and_then(Value::as_u64)
            .map(|millis| (u128::from(millis) * 1_000_000).to_string())
            .unwrap_or_else(|| timestamp.to_string());
        let ended_timestamp = payload
            .get("completed_at_ms")
            .or_else(|| item.get("completed_at_ms"))
            .and_then(Value::as_u64)
            .map(|millis| (u128::from(millis) * 1_000_000).to_string())
            .unwrap_or_else(|| timestamp.to_string());
        let index = self.callbacks.len();
        self.callbacks.push(ToolCallback {
            callback_id: callback_id.to_string(),
            invocation_ordinal,
            name,
            input,
            output,
            requested_at: started_timestamp,
            completed_at: ended_timestamp,
            success,
            continuation_of: None,
            terminal: true,
            suppress_if_nested: false,
        });
        self.callback_index.insert(callback_id.to_string(), index);
        if let Some(invocation) = self.invocations.get_mut(invocation_ordinal) {
            invocation.callback_ids.push(callback_id.to_string());
            invocation.output_at = timestamp.to_string();
        }
    }

    fn finish(mut self) -> NormalizedTurnSemantics {
        let turn_terminal = matches!(
            self.terminal_state,
            TurnTerminalState::Completed | TurnTerminalState::Failed | TurnTerminalState::Aborted
        );
        for invocation in &mut self.invocations {
            let callbacks_terminal = invocation.callback_ids.iter().all(|callback_id| {
                self.callback_index
                    .get(callback_id)
                    .and_then(|index| self.callbacks.get(*index))
                    .is_some_and(|callback| callback.terminal)
            });
            let usage_terminal = if invocation.has_native_usage_record() {
                true
            } else {
                invocation.has_output_or_action()
                    && invocation.usage.has_any()
                    && callbacks_terminal
            };
            let authoritative_terminal = turn_terminal
                && (invocation.has_output_or_action() || invocation.has_native_usage_record())
                && callbacks_terminal;
            invocation.terminal = usage_terminal || authoritative_terminal;
            invocation.terminal_source = if usage_terminal {
                Some(InvocationTerminalSource::UsageAndCallbacks)
            } else if authoritative_terminal {
                Some(InvocationTerminalSource::TurnTerminal)
            } else {
                None
            };
        }
        NormalizedTurnSemantics {
            turn_id: self.turn_id,
            turn_started_at: self.turn_started_at,
            user_submitted_at: self.user_submitted_at,
            terminal_at: self.terminal_at,
            native_usage_mode: self.native_usage_mode,
            invocations: self.invocations,
            callbacks: self.callbacks,
            terminal_state: self.terminal_state,
        }
    }
}

pub fn normalize_turn_semantics(parsed_lines: &[Value]) -> Vec<NormalizedTurnSemantics> {
    normalize_turn_semantics_with_usage_mode(parsed_lines, false)
}

pub(crate) fn normalize_turn_semantics_with_usage_mode(
    parsed_lines: &[Value],
    initial_native_usage_mode: bool,
) -> Vec<NormalizedTurnSemantics> {
    let mut completed_turns: Vec<NormalizedTurnBuilder> = Vec::new();
    let mut current: Option<NormalizedTurnBuilder> = None;
    let mut pending_compaction_usage = false;
    let mut native_usage_mode = initial_native_usage_mode;
    let mut last_cumulative_token_usage: Option<TokenUsage> = None;

    for entry in parsed_lines {
        let timestamp = entry
            .get("timestamp")
            .and_then(Value::as_str)
            .filter(|value| looks_like_iso_timestamp(value))
            .unwrap_or("");
        let entry_type = entry.get("type").and_then(Value::as_str).unwrap_or("");
        let payload = entry.get("payload").cloned().unwrap_or(Value::Null);
        let payload_type = payload.get("type").and_then(Value::as_str).unwrap_or("");

        if entry_type == "event_msg" && payload_type == "task_started" {
            let native_turn_id = payload
                .get("turn_id")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty());
            if current
                .as_ref()
                .is_some_and(|turn| native_turn_id.is_some_and(|turn_id| turn.turn_id == turn_id))
            {
                continue;
            }
            if let Some(mut turn) = current.take() {
                turn.mark_superseded();
                completed_turns.push(turn);
            }
            current = Some(NormalizedTurnBuilder {
                turn_id: native_turn_id.unwrap_or("").to_string(),
                turn_started_at: timestamp.to_string(),
                native_usage_mode,
                ..NormalizedTurnBuilder::default()
            });
            continue;
        }

        if entry_type == "turn_context" {
            let turn = current.get_or_insert_with(NormalizedTurnBuilder::default);
            turn.native_usage_mode = native_usage_mode;
            if let Some(turn_id) = payload
                .get("turn_id")
                .and_then(Value::as_str)
                .filter(|turn_id| !turn_id.is_empty())
            {
                turn.turn_id = turn_id.to_string();
            }
            continue;
        }

        if entry_type == "token_usage_record" {
            let payload_turn_id = payload
                .get("turn_id")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty());
            let routed = payload_turn_id
                .and_then(|turn_id| {
                    completed_turns
                        .iter_mut()
                        .rev()
                        .find(|turn| turn.turn_id == turn_id)
                })
                .or_else(|| current.as_mut());
            if let Some(turn) = routed {
                if turn.record_native_usage(timestamp, &payload) {
                    native_usage_mode = true;
                    turn.native_usage_mode = true;
                    if let Some(current) = current.as_mut() {
                        current.native_usage_mode = true;
                    }
                }
            }
            continue;
        }

        if entry_type == "compacted" {
            if let Some(latest) = payload.get("latest_token_usage_record") {
                let payload_turn_id = latest
                    .get("turn_id")
                    .and_then(Value::as_str)
                    .filter(|value| !value.is_empty());
                let routed = payload_turn_id
                    .and_then(|turn_id| {
                        completed_turns
                            .iter_mut()
                            .rev()
                            .find(|turn| turn.turn_id == turn_id)
                    })
                    .or_else(|| current.as_mut());
                if let Some(turn) = routed {
                    if turn.record_native_usage(timestamp, latest) {
                        native_usage_mode = true;
                        turn.native_usage_mode = true;
                        if let Some(current) = current.as_mut() {
                            current.native_usage_mode = true;
                        }
                    }
                }
            }
            pending_compaction_usage = !native_usage_mode;
            continue;
        }

        let Some(turn) = current.as_mut() else {
            continue;
        };

        if entry_type == "event_msg" {
            match payload_type {
                "user_message" => {
                    if turn.user_submitted_at.is_empty() {
                        turn.user_submitted_at = timestamp.to_string();
                    }
                }
                "reasoning" => turn.record_reasoning(timestamp, false),
                "agent_message" => {
                    let text = payload.get("message").and_then(Value::as_str).unwrap_or("");
                    let phase = payload
                        .get("phase")
                        .and_then(Value::as_str)
                        .unwrap_or("assistant");
                    turn.record_message(timestamp, text, phase);
                }
                "token_count" => {
                    if native_usage_mode {
                        pending_compaction_usage = false;
                        continue;
                    }
                    let cumulative_usage = payload
                        .get("info")
                        .and_then(|info| info.get("total_token_usage"))
                        .map(TokenUsage::from_usage_value)
                        .filter(TokenUsage::has_any);
                    if let Some(last) = payload
                        .get("info")
                        .and_then(|info| info.get("last_token_usage"))
                    {
                        let usage = TokenUsage::from_usage_value(last);
                        if usage.has_any() {
                            if !cumulative_usage_allows_legacy_record(
                                last_cumulative_token_usage.as_ref(),
                                cumulative_usage.as_ref(),
                            ) {
                                continue;
                            }
                            if pending_compaction_usage {
                                pending_compaction_usage = false;
                                if classify_compaction_usage(&usage).metadata_only {
                                    if let Some(cumulative_usage) = cumulative_usage {
                                        last_cumulative_token_usage = Some(cumulative_usage);
                                    }
                                    continue;
                                }
                            }
                            turn.record_usage(timestamp, usage);
                        }
                    }
                    if let Some(cumulative_usage) = cumulative_usage {
                        last_cumulative_token_usage = Some(cumulative_usage);
                    }
                }
                "dynamic_tool_call_response" => {
                    let callback_id = payload.get("call_id").and_then(Value::as_str).unwrap_or("");
                    let output = payload
                        .get("content_items")
                        .and_then(Value::as_array)
                        .and_then(|items| items.first())
                        .and_then(|item| item.get("text"))
                        .and_then(Value::as_str)
                        .map(|text| Value::String(text.to_string()))
                        .unwrap_or(Value::Null);
                    turn.complete_callback(
                        timestamp,
                        callback_id,
                        output,
                        payload.get("success").and_then(Value::as_bool),
                    );
                }
                "mcp_tool_call_end" => {
                    let callback_id = payload.get("call_id").and_then(Value::as_str).unwrap_or("");
                    let result = payload.get("result").cloned().unwrap_or(Value::Null);
                    let output = mcp_tool_result_output(&result);
                    let success = if result.get("Ok").is_some() {
                        Some(true)
                    } else if result.get("Err").is_some() {
                        Some(false)
                    } else {
                        None
                    };
                    turn.complete_callback(timestamp, callback_id, output, success);
                }
                "item_completed" => {
                    turn.record_item_completed_tool(timestamp, &payload);
                }
                "task_complete" | "turn_aborted" => {
                    let payload_turn_id = payload
                        .get("turn_id")
                        .and_then(Value::as_str)
                        .filter(|value| !value.is_empty());
                    if let Some(routed_turn) = payload_turn_id.and_then(|turn_id| {
                        completed_turns
                            .iter_mut()
                            .rev()
                            .find(|turn| turn.turn_id == turn_id)
                    }) {
                        routed_turn.terminal_at = timestamp.to_string();
                        routed_turn.terminal_state = if payload_type == "task_complete" {
                            if payload.get("error").is_some() {
                                TurnTerminalState::Failed
                            } else {
                                TurnTerminalState::Completed
                            }
                        } else {
                            TurnTerminalState::Aborted
                        };
                        if let Some(text) = payload
                            .get("last_agent_message")
                            .and_then(Value::as_str)
                            .filter(|text| !text.trim().is_empty())
                        {
                            let already_recorded =
                                routed_turn.invocations.iter().any(|invocation| {
                                    invocation
                                        .messages
                                        .iter()
                                        .any(|message| message.text == text.trim())
                                });
                            if !already_recorded {
                                routed_turn.record_message(timestamp, text, "final_answer");
                            }
                        }
                        continue;
                    }
                    if !turn.accepts_terminal_for(payload_turn_id) {
                        continue;
                    }
                    if turn.turn_id.is_empty() {
                        if let Some(payload_turn_id) = payload_turn_id {
                            turn.turn_id = payload_turn_id.to_string();
                        }
                    }
                    turn.terminal_at = timestamp.to_string();
                    turn.terminal_state = if payload_type == "task_complete" {
                        if payload.get("error").is_some() {
                            TurnTerminalState::Failed
                        } else {
                            TurnTerminalState::Completed
                        }
                    } else {
                        TurnTerminalState::Aborted
                    };
                    if let Some(text) = payload
                        .get("last_agent_message")
                        .and_then(Value::as_str)
                        .filter(|text| !text.trim().is_empty())
                    {
                        let already_recorded = turn.invocations.iter().any(|invocation| {
                            invocation
                                .messages
                                .iter()
                                .any(|message| message.text == text.trim())
                        });
                        if !already_recorded {
                            turn.record_message(timestamp, text, "final_answer");
                        }
                    }
                    completed_turns.push(current.take().expect("current turn"));
                }
                _ => {}
            }
            continue;
        }

        if entry_type != "response_item" {
            continue;
        }

        match payload_type {
            "agent_message" => {
                let content = extract_agent_message_content(&payload);
                if extract_agent_message_type(&content).as_deref() == Some("NEW_TASK")
                    && turn.user_submitted_at.is_empty()
                {
                    turn.user_submitted_at = timestamp.to_string();
                }
            }
            "reasoning" => turn.record_reasoning(timestamp, true),
            "message" if payload.get("role").and_then(Value::as_str) == Some("assistant") => {
                let text = extract_response_message_text(&payload);
                let phase = payload
                    .get("phase")
                    .and_then(Value::as_str)
                    .unwrap_or("assistant");
                let media = payload.get("content").and_then(agentic_otel_common::media::content_blocks);
                if media.is_some() && text.trim().is_empty() {
                    let ordinal = turn.ensure_invocation(timestamp);
                    turn.invocations[ordinal].messages.push(AssistantMessage {
                        timestamp: timestamp.to_string(), text: String::new(), phase: phase.to_string(),
                        media_content: Value::Array(media.unwrap()), usage: TokenUsage::default(),
                        token_count_timestamp: String::new(), invocation_started_timestamp: String::new(), invocation_end_source: None,
                    });
                    turn.invocations[ordinal].output_at = timestamp.to_string();
                } else {
                    turn.record_message(timestamp, &text, phase);
                    if let Some(media) = media {
                        if let Some(message) = turn.invocations.last_mut().and_then(|i| i.messages.last_mut()) { message.media_content = Value::Array(media); }
                    }
                }
            }
            "function_call" | "custom_tool_call" => {
                let tool_name = super::response_tool_name(&payload);
                let raw_name = tool_name.as_str();
                let raw_args = if payload_type == "custom_tool_call" {
                    payload.get("input").cloned().unwrap_or(Value::Null)
                } else {
                    payload.get("arguments").cloned().unwrap_or(Value::Null)
                };
                let parsed_args = parse_jsonish_value(&raw_args);
                let callback_id = payload
                    .get("call_id")
                    .or_else(|| payload.get("callId"))
                    .and_then(Value::as_str)
                    .unwrap_or("");
                turn.record_callback(timestamp, callback_id, raw_name, &parsed_args);
            }
            "function_call_output" | "custom_tool_call_output" => {
                let callback_id = payload
                    .get("call_id")
                    .or_else(|| payload.get("callId"))
                    .and_then(Value::as_str)
                    .unwrap_or("");
                let output =
                    parse_jsonish_value(&payload.get("output").cloned().unwrap_or(Value::Null));
                turn.complete_callback(timestamp, callback_id, output, None);
            }
            "tool_search_call" => {
                if payload.get("execution").and_then(Value::as_str) == Some("client") {
                    let callback_id = payload.get("call_id").and_then(Value::as_str).unwrap_or("");
                    // Client-side discovery finishes only when its separate
                    // tool_search_output arrives, even if the call has status.
                    turn.record_callback(
                        timestamp,
                        callback_id,
                        "ToolSearch",
                        payload.get("arguments").unwrap_or(&Value::Null),
                    );
                    continue;
                }
                let callback_id = payload
                    .get("call_id")
                    .and_then(Value::as_str)
                    .filter(|call_id| !call_id.is_empty())
                    .map(str::to_string)
                    .unwrap_or_else(|| format!("tool-search-{}", turn.callbacks.len() + 1));
                let status = payload
                    .get("status")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown");
                turn.record_immediate_callback(
                    timestamp,
                    callback_id,
                    "ToolSearch",
                    payload.get("arguments").cloned().unwrap_or(Value::Null),
                    json!({"status": status}),
                    Some(status == "completed"),
                );
            }
            "tool_search_output" => {
                let callback_id = payload.get("call_id").and_then(Value::as_str).unwrap_or("");
                let pending_search = turn
                    .callback_index
                    .get(callback_id)
                    .and_then(|index| turn.callbacks.get(*index))
                    .is_some_and(|call| call.name == "ToolSearch" && !call.terminal);
                if pending_search {
                    turn.complete_callback(
                        timestamp,
                        callback_id,
                        json!({"status": payload.get("status"), "tools": payload.get("tools")}),
                        payload
                            .get("status")
                            .and_then(Value::as_str)
                            .map(|status| status == "completed"),
                    );
                }
            }
            "web_search_call" => {
                let status = payload
                    .get("status")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown");
                turn.record_immediate_callback(
                    timestamp,
                    format!("web-search-{}", turn.callbacks.len() + 1),
                    "WebSearch",
                    payload.get("action").cloned().unwrap_or(Value::Null),
                    json!({"status": status}),
                    Some(status == "completed"),
                );
            }
            _ => {}
        }
    }

    if let Some(turn) = current {
        completed_turns.push(turn);
    }
    completed_turns
        .into_iter()
        .map(NormalizedTurnBuilder::finish)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn usage_payload(input: i64, output: i64, total: i64) -> Value {
        json!({
            "input_tokens": input,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "output_tokens": output,
            "reasoning_output_tokens": 0,
            "total_tokens": total
        })
    }

    #[test]
    fn whitespace_variants_deduplicate_within_each_invocation() {
        let mut lines = vec![
            json!({"timestamp":"2026-09-06T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-text"}}),
        ];
        for response_id in ["first", "second"] {
            lines.extend([
                json!({"timestamp":"2026-09-06T00:00:01Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Hello world"}]}}),
                json!({"timestamp":"2026-09-06T00:00:01Z","type":"event_msg","payload":{"type":"agent_message","message":"  Hello world\n"}}),
                json!({"timestamp":"2026-09-06T00:00:02Z","type":"token_usage_record","payload":{"turn_id":"turn-text","response_id":response_id,"usage":usage_payload(10,2,12)}}),
            ]);
        }
        let turns = normalize_turn_semantics(&lines);
        let assistants = turns[0].derived_assistants();
        assert_eq!(assistants.len(), 2, "distinct calls must remain distinct");
        for assistant in assistants {
            assert_eq!(assistant.text, "Hello world");
            assert_eq!(assistant.usage.total, Some(12));
        }
    }

    #[test]
    fn native_token_usage_record_overrides_repeated_legacy_token_count() {
        let lines = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-native"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"synthetic prompt"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"synthetic output"}]}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"token_usage_record","payload":{"turn_id":"turn-native","response_id":"resp-native-1","usage":usage_payload(100,20,120)}}),
            json!({"timestamp":"2026-09-05T00:00:02.100Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120}}}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"task_complete"}}),
        ];

        let turns = normalize_turn_semantics(&lines);
        let assistants = turns[0].derived_assistants();

        assert_eq!(assistants.len(), 1);
        assert_eq!(assistants[0].usage.total, Some(120));
        assert_eq!(
            assistants[0].usage.source,
            Some(TokenUsageSource::NativeRecord)
        );
        assert_eq!(
            assistants[0].usage.response_id.as_deref(),
            Some("resp-native-1")
        );
    }

    #[test]
    fn native_usage_record_detector_requires_response_id_and_usage() {
        assert!(has_native_usage_record(&json!({
            "type":"token_usage_record",
            "payload":{"response_id":"resp-valid","usage":usage_payload(1,1,2)}
        })));
        assert!(has_native_usage_record(&json!({
            "type":"compacted",
            "payload":{"latest_token_usage_record":{"response_id":"resp-compact","usage":usage_payload(1,1,2)}}
        })));
        assert!(!has_native_usage_record(&json!({
            "type":"token_usage_record",
            "payload":{"response_id":"","usage":usage_payload(1,1,2)}
        })));
        assert!(!has_native_usage_record(&json!({
            "type":"token_usage_record",
            "payload":{"response_id":"resp-empty","usage":{}}
        })));
    }

    #[test]
    fn legacy_prefix_is_preserved_before_native_usage_mode_starts() {
        let lines = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-legacy-prefix"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"legacy prompt"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"legacy output"}]}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":7,"output_tokens":3,"total_tokens":10}}}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"task_complete"}}),
            json!({"timestamp":"2026-09-05T00:01:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-native-mode"}}),
            json!({"timestamp":"2026-09-05T00:01:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"native prompt"}}),
            json!({"timestamp":"2026-09-05T00:01:01.000Z","type":"token_usage_record","payload":{"turn_id":"turn-native-mode","response_id":"resp-native-mode","usage":usage_payload(20,2,22)}}),
            json!({"timestamp":"2026-09-05T00:01:02.000Z","type":"event_msg","payload":{"type":"task_complete"}}),
        ];

        let turns = normalize_turn_semantics(&lines);
        let legacy_assistants = turns[0].derived_assistants();
        let native_assistants = turns[1].derived_assistants();

        assert_eq!(legacy_assistants[0].usage.total, Some(10));
        assert_eq!(
            legacy_assistants[0].usage.source,
            Some(TokenUsageSource::LegacyTokenCount)
        );
        assert_eq!(native_assistants[0].usage.total, Some(22));
        assert_eq!(
            native_assistants[0].usage.source,
            Some(TokenUsageSource::NativeRecord)
        );
    }

    #[test]
    fn late_native_usage_replaces_same_invocation_legacy_fallback() {
        let lines = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-replace"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"synthetic prompt"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"synthetic output"}]}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":95,"output_tokens":15,"total_tokens":110}}}}),
            json!({"timestamp":"2026-09-05T00:00:02.100Z","type":"token_usage_record","payload":{"turn_id":"turn-replace","response_id":"resp-replace","usage":usage_payload(100,20,120)}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"task_complete"}}),
        ];

        let turns = normalize_turn_semantics(&lines);
        let assistants = turns[0].derived_assistants();

        assert_eq!(assistants.len(), 1);
        assert_eq!(assistants[0].usage.total, Some(120));
        assert_eq!(
            assistants[0].usage.source,
            Some(TokenUsageSource::NativeRecord)
        );
        assert_eq!(
            assistants[0].usage.response_id.as_deref(),
            Some("resp-replace")
        );
    }

    #[test]
    fn invalid_native_usage_record_does_not_disable_legacy_fallback() {
        let lines = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-invalid-native"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"synthetic prompt"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"synthetic output"}]}}),
            json!({"timestamp":"2026-09-05T00:00:01.500Z","type":"token_usage_record","payload":{"turn_id":"turn-invalid-native","response_id":"","usage":usage_payload(100,20,120)}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":7,"output_tokens":3,"total_tokens":10}}}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"task_complete"}}),
        ];

        let turns = normalize_turn_semantics(&lines);
        let assistants = turns[0].derived_assistants();

        assert_eq!(assistants.len(), 1);
        assert_eq!(assistants[0].usage.total, Some(10));
        assert_eq!(
            assistants[0].usage.source,
            Some(TokenUsageSource::LegacyTokenCount)
        );
    }

    #[test]
    fn repeated_legacy_token_count_without_cumulative_progress_does_not_attach_stale_usage() {
        let lines = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-one"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"first prompt"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"first output"}]}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"output_tokens":10,"total_tokens":110},"total_token_usage":{"input_tokens":100,"output_tokens":10,"total_tokens":110}}}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-one"}}),
            json!({"timestamp":"2026-09-05T00:01:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-two"}}),
            json!({"timestamp":"2026-09-05T00:01:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"second prompt"}}),
            json!({"timestamp":"2026-09-05T00:01:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"second output"}]}}),
            json!({"timestamp":"2026-09-05T00:01:02.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"output_tokens":10,"total_tokens":110},"total_token_usage":{"input_tokens":100,"output_tokens":10,"total_tokens":110}}}}),
            json!({"timestamp":"2026-09-05T00:01:03.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-two"}}),
        ];

        let turns = normalize_turn_semantics(&lines);
        let first = turns[0].derived_assistants();
        let second = turns[1].derived_assistants();

        assert_eq!(first[0].usage.total, Some(110));
        assert!(!second[0].usage.has_any());
    }

    #[test]
    fn equal_legacy_last_usage_is_distinct_when_cumulative_total_advances() {
        let lines = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-equal-legacy"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"synthetic prompt"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"first output"}]}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":7,"output_tokens":3,"total_tokens":10},"total_token_usage":{"input_tokens":7,"output_tokens":3,"total_tokens":10}}}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"second output"}]}}),
            json!({"timestamp":"2026-09-05T00:00:04.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":7,"output_tokens":3,"total_tokens":10},"total_token_usage":{"input_tokens":14,"output_tokens":6,"total_tokens":20}}}}),
            json!({"timestamp":"2026-09-05T00:00:05.000Z","type":"event_msg","payload":{"type":"task_complete"}}),
        ];

        let turns = normalize_turn_semantics(&lines);
        let assistants = turns[0].derived_assistants();
        let totals = assistants
            .iter()
            .map(|assistant| assistant.usage.total)
            .collect::<Vec<_>>();

        assert_eq!(totals, vec![Some(10), Some(10)]);
    }

    #[test]
    fn legacy_cumulative_reset_allows_new_baseline() {
        let lines = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-reset"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"synthetic prompt"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"first output"}]}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"output_tokens":10,"total_tokens":110},"total_token_usage":{"input_tokens":100,"output_tokens":10,"total_tokens":110}}}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"after reset"}]}}),
            json!({"timestamp":"2026-09-05T00:00:04.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":8,"output_tokens":2,"total_tokens":10},"total_token_usage":{"input_tokens":8,"output_tokens":2,"total_tokens":10}}}}),
            json!({"timestamp":"2026-09-05T00:00:05.000Z","type":"event_msg","payload":{"type":"task_complete"}}),
        ];

        let turns = normalize_turn_semantics(&lines);
        let assistants = turns[0].derived_assistants();
        let totals = assistants
            .iter()
            .map(|assistant| assistant.usage.total)
            .collect::<Vec<_>>();

        assert_eq!(totals, vec![Some(110), Some(10)]);
    }

    #[test]
    fn compacted_latest_usage_record_is_a_deduped_usage_only_invocation() {
        let lines = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-compact"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"synthetic prompt"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"token_usage_record","payload":{"turn_id":"turn-compact","response_id":"resp-compact-1","usage":usage_payload(231032,2374,233406)}}),
            json!({"timestamp":"2026-09-05T00:00:01.010Z","type":"compacted","payload":{"compaction_response_id":"resp-compact-1","latest_token_usage_record":{"response_id":"resp-compact-1","usage":usage_payload(231032,2374,233406)}}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"compacted","payload":{"compaction_response_id":"resp-compact-2","latest_token_usage_record":{"response_id":"resp-compact-2","usage":usage_payload(225141,3489,228630)}}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"task_complete"}}),
        ];

        let turns = normalize_turn_semantics(&lines);
        let assistants = turns[0].derived_assistants();
        let totals = assistants
            .iter()
            .map(|assistant| assistant.usage.total)
            .collect::<Vec<_>>();

        assert_eq!(totals, vec![Some(233406), Some(228630)]);
        assert!(assistants.iter().all(|assistant| assistant.text.is_empty()));
        assert!(assistants
            .iter()
            .all(|assistant| assistant.usage.is_native_record()));
    }

    #[test]
    fn equal_native_usage_amounts_with_different_response_ids_remain_distinct() {
        let lines = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-equal"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"synthetic prompt"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"token_usage_record","payload":{"turn_id":"turn-equal","response_id":"resp-equal-1","usage":usage_payload(50,5,55)}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"token_usage_record","payload":{"turn_id":"turn-equal","response_id":"resp-equal-2","usage":usage_payload(50,5,55)}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"task_complete"}}),
        ];

        let turns = normalize_turn_semantics(&lines);
        let assistants = turns[0].derived_assistants();
        let response_ids = assistants
            .iter()
            .filter_map(|assistant| assistant.usage.response_id.as_deref())
            .collect::<Vec<_>>();

        assert_eq!(response_ids, vec!["resp-equal-1", "resp-equal-2"]);
    }

    #[test]
    fn task_started_and_late_terminal_events_are_turn_id_aware() {
        let lines = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-open"}}),
            json!({"timestamp":"2026-09-05T00:00:00.100Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-open"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"working"}]}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn-current"}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-open","last_agent_message":"late old terminal"}}),
            json!({"timestamp":"2026-09-05T00:00:04.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-current","last_agent_message":"done"}}),
        ];

        let turns = normalize_turn_semantics(&lines);

        assert_eq!(turns.len(), 2);
        assert_eq!(turns[0].turn_id, "turn-open");
        assert_eq!(turns[0].terminal_state, TurnTerminalState::Completed);
        assert_eq!(turns[0].terminal_at, "2026-09-05T00:00:03.000Z");
        assert_eq!(turns[1].turn_id, "turn-current");
        assert_eq!(turns[1].terminal_state, TurnTerminalState::Completed);
        assert_eq!(turns[1].terminal_at, "2026-09-05T00:00:04.000Z");
    }

    #[test]
    fn delayed_prior_turn_native_usage_backfills_after_successor_started() {
        let lines = vec![
            json!({"timestamp":"2026-09-05T00:00:00.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"old"}}),
            json!({"timestamp":"2026-09-05T00:00:00.010Z","type":"event_msg","payload":{"type":"user_message","message":"old prompt"}}),
            json!({"timestamp":"2026-09-05T00:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"old output"}]}}),
            json!({"timestamp":"2026-09-05T00:00:02.000Z","type":"event_msg","payload":{"type":"task_started","turn_id":"next"}}),
            json!({"timestamp":"2026-09-05T00:00:02.010Z","type":"event_msg","payload":{"type":"user_message","message":"next prompt"}}),
            json!({"timestamp":"2026-09-05T00:00:03.000Z","type":"token_usage_record","payload":{"turn_id":"old","response_id":"resp-old-late","usage":usage_payload(100,20,120)}}),
            json!({"timestamp":"2026-09-05T00:00:04.000Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"next","last_agent_message":"next done"}}),
        ];

        let turns = normalize_turn_semantics(&lines);
        let old_assistants = turns[0].derived_assistants();
        let next_assistants = turns[1].derived_assistants();

        assert_eq!(turns[0].turn_id, "old");
        assert_eq!(turns[0].terminal_state, TurnTerminalState::Superseded);
        assert_eq!(turns[0].terminal_at, "2026-09-05T00:00:03.000Z");
        assert_eq!(old_assistants.len(), 1);
        assert_eq!(old_assistants[0].usage.total, Some(120));
        assert_eq!(
            old_assistants[0].usage.response_id.as_deref(),
            Some("resp-old-late")
        );
        assert_eq!(turns[1].turn_id, "next");
        assert_eq!(turns[1].terminal_state, TurnTerminalState::Completed);
        assert!(next_assistants
            .iter()
            .all(|assistant| assistant.usage.response_id.as_deref() != Some("resp-old-late")));
    }
}
