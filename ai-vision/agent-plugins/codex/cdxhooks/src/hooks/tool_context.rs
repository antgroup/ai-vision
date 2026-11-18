use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::common::hook::{env_is_truthy, extract_session_id, HookEvent};
use crate::common::logger::now_iso8601_pub;
use crate::common::otel::deterministic_tool_span_id;
use crate::common::spool::{self, RealtimeSpoolKind};
use crate::hooks::langfuse::realtime_state;
use crate::hooks::langfuse::state::{
    get_current_trace_context, get_vcs_for_cwd, load_state, set_current_trace_context,
    update_state_transaction_fallible_with_timeout, update_state_transaction_with_timeout,
    ToolTraceContext, VcsInfo, FOREGROUND_STATE_LOCK_TIMEOUT_MS,
};
use crate::hooks::langfuse::{build_basic_auth_header, resolve_agent_product, resolve_agent_type};
use crate::hooks::{CommonContext, HandlerError, HookHandler, RetryPolicy};

#[derive(Default)]
pub struct ToolContextHandler;

impl ToolContextHandler {
    pub fn new() -> Self {
        Self
    }
}

/// Build the control response that Codex must receive before the hook parent
/// exits. Native-turn identity is committed before injection so a rotated
/// TurnSeed cannot leak a second trace/root into the same Codex turn. Durable
/// Tool timing and delivery enrichment remain owned by the detached worker.
pub fn build_pretooluse_control_response(
    payload: &Value,
    context: &CommonContext,
) -> Option<Value> {
    if !env_is_truthy("CDXHOOKS_TOOL_CONTEXT") || extract_tool_name(payload) != "Bash" {
        return None;
    }
    let session_id = extract_session_id(payload);
    let tool_use_id = extract_tool_use_id(payload);
    let command = extract_command(payload)?;
    if session_id.is_empty() || tool_use_id.is_empty() {
        return None;
    }
    let upstream_traceparent_raw = non_empty_env("TRACEPARENT");
    let candidate_trace_id = match crate::common::turn_seed::trace_id_for_tool(
        &session_id,
        upstream_traceparent_raw.as_deref(),
    ) {
        Ok(trace_id) => trace_id,
        Err(error) => {
            context
                .logger
                .warn(&format!("PreToolUse turn seed read failed open: {error}"));
            agentic_otel_common::otel_trace_id()
        }
    };
    let sofa_traceid = non_empty_env("SOFA_TRACEID");
    let sofa_rpcid = non_empty_env("SOFA_RPCID");
    let native_turn_id = extract_native_turn_id(payload);
    let context_created_at = now_iso8601_pub();
    let upstream_traceparent = agentic_otel_common::parse_traceparent_env();
    let mut claimed_context = None;
    let mut claim_error = None;
    let mut updated_state = json!({});
    if let Err(error) = update_state_transaction_with_timeout(
        &context.state_file,
        &mut updated_state,
        FOREGROUND_STATE_LOCK_TIMEOUT_MS,
        |state| match realtime_state::claim_tool_for_native_turn_in_state(
            state,
            &session_id,
            &tool_use_id,
            native_turn_id.as_deref(),
            &context_created_at,
            &candidate_trace_id,
            upstream_traceparent_raw.as_deref(),
            upstream_traceparent
                .as_ref()
                .map(|traceparent| traceparent.parent_span_id.as_str()),
            sofa_traceid.as_deref(),
            sofa_rpcid.as_deref(),
            context.realtime_llm_batch_size,
            context.retry_policy,
        ) {
            Ok(claimed) => {
                let traceparent = format!(
                    "00-{}-{}-01",
                    claimed.context.trace_id,
                    deterministic_tool_span_id(&tool_use_id)
                );
                set_current_trace_context(
                    state,
                    &session_id,
                    &ToolTraceContext {
                        trace_id: claimed.context.trace_id.clone(),
                        traceparent,
                        upstream_parent_span_id: claimed
                            .context
                            .trace
                            .upstream_parent_span_id
                            .clone(),
                        sofa_traceid: claimed.context.trace.sofa_traceid.clone(),
                        sofa_rpcid: claimed.context.trace.sofa_rpcid.clone(),
                    },
                );
                claimed_context = Some(claimed);
            }
            Err(error) => claim_error = Some(error),
        },
    ) {
        context.logger.warn(&format!(
            "PreToolUse native-turn identity commit failed open for session={session_id} tool_use_id={tool_use_id}: {error}"
        ));
        return None;
    }
    if let Some(error) = claim_error {
        context.logger.warn(&format!(
            "PreToolUse native-turn identity deferred for session={session_id} turn_id={} tool_use_id={tool_use_id}: {error}",
            native_turn_id.as_deref().unwrap_or("")
        ));
        return None;
    }
    let claimed = claimed_context?;
    context.logger.debug(&format!(
        "PreToolUse native-turn identity action={} session={} turn_id={} context_id={} aliases={}",
        claimed.action.as_str(),
        session_id,
        native_turn_id.as_deref().unwrap_or(""),
        claimed.context.context_id,
        claimed.aliases.len()
    ));
    let traceparent = format!(
        "00-{}-{}-01",
        claimed.context.trace_id,
        deterministic_tool_span_id(&tool_use_id)
    );
    let vcs = crate::common::runtime_state::DomainStateStore::for_session(
        &session_id,
        crate::common::runtime_state::StateDomain::Resource,
    )
    .and_then(|store| store.load())
    .ok()
    .and_then(|resource| {
        let mut composite = json!({});
        composite[&session_id] = resource;
        get_vcs_for_cwd(&composite, &session_id, extract_cwd(payload).as_deref())
    });
    let injected_command = inject_env_exports(
        &command,
        &session_id,
        &tool_use_id,
        &traceparent,
        claimed.context.trace.sofa_traceid.as_deref(),
        claimed.context.trace.sofa_rpcid.as_deref(),
        vcs.as_ref(),
        context,
    );
    Some(json!({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {"command": injected_command}
        }
    }))
}

impl HookHandler for ToolContextHandler {
    fn name(&self) -> &str {
        "tool_context"
    }

    fn supported_events(&self) -> &[HookEvent] {
        &[HookEvent::PreToolUse, HookEvent::PostToolUse]
    }

    fn handle(
        &self,
        event: &HookEvent,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        match event {
            HookEvent::PreToolUse => self.handle_pre_tool_use(payload, context),
            HookEvent::PostToolUse => self.handle_post_tool_use(payload, context, false),
            _ => Ok(()),
        }
    }
}

pub(crate) mod tool_records {
    use super::*;
    use std::path::Path;

    const TOOL_RECORD_SCHEMA_VERSION: u32 = 1;

    #[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
    pub struct ToolRecord {
        pub schema_version: u32,
        pub session_id: String,
        pub tool_use_id: String,
        pub tool_span_id: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub context_id: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub trace_id: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub root_span_id: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub turn_id: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub assistant_index: Option<usize>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub semantic_generation_ordinal: Option<u64>,
        pub generation_batch_size: usize,
        pub retry_policy: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub started_at_unix_nano: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub completed_at_unix_nano: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub duration_unix_nano: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub timing_source: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub upstream_traceparent: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub upstream_parent_span_id: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub sofa_traceid: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub sofa_rpcid: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub state_reconciled_at: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub last_error: Option<String>,
        pub created_at: String,
        pub updated_at: String,
    }

    impl ToolRecord {
        pub fn completed_timing(&self) -> Result<CompletedToolTiming, String> {
            let started = self
                .started_at_unix_nano
                .as_deref()
                .ok_or_else(|| "tool record missing start timestamp".to_string())?;
            let completed = self
                .completed_at_unix_nano
                .as_deref()
                .ok_or_else(|| "tool record missing completion timestamp".to_string())?;
            let started_nanos =
                parse_unix_nanos(started).ok_or_else(|| "invalid tool start".to_string())?;
            let completed_nanos =
                parse_unix_nanos(completed).ok_or_else(|| "invalid tool completion".to_string())?;
            if completed_nanos <= started_nanos {
                return Err("tool record interval is not positive".to_string());
            }
            Ok(CompletedToolTiming {
                started_at_unix_nano: started.to_string(),
                completed_at_unix_nano: completed.to_string(),
                duration_unix_nano: (completed_nanos - started_nanos).to_string(),
            })
        }
    }

    #[derive(Debug, Clone, PartialEq, Eq)]
    pub struct CompletedToolTiming {
        pub started_at_unix_nano: String,
        pub completed_at_unix_nano: String,
        pub duration_unix_nano: String,
    }

    pub fn record_pre(
        context: &CommonContext,
        session_id: &str,
        tool_use_id: &str,
        tool_span_id: &str,
        context_id: Option<&str>,
        trace_id: &str,
        root_span_id: &str,
        turn_id: Option<&str>,
        upstream_traceparent: Option<&str>,
        upstream_parent_span_id: Option<&str>,
        sofa_traceid: Option<&str>,
        sofa_rpcid: Option<&str>,
        generation_batch_size: usize,
        retry_policy: RetryPolicy,
        started_at_unix_nano: &str,
    ) -> Result<ToolRecord, String> {
        update_record(context, session_id, tool_use_id, |record, now| {
            record.schema_version = TOOL_RECORD_SCHEMA_VERSION;
            record.session_id = session_id.to_string();
            record.tool_use_id = tool_use_id.to_string();
            if record.tool_span_id.is_empty() {
                record.tool_span_id = tool_span_id.to_string();
            }
            if record.context_id.is_none() {
                record.context_id = context_id.map(str::to_string);
            }
            if record.trace_id.is_none() {
                record.trace_id = Some(trace_id.to_string());
            }
            if record.root_span_id.is_none() {
                record.root_span_id = Some(root_span_id.to_string());
            }
            if record.context_id.is_none() {
                record.context_id = Some(realtime_state::stable_context_id(trace_id, root_span_id));
            }
            if record.turn_id.is_none() {
                record.turn_id = turn_id.map(str::to_string);
            }
            if record.generation_batch_size == 0 {
                record.generation_batch_size = generation_batch_size.max(1);
            }
            if record.retry_policy.is_empty() {
                record.retry_policy = retry_policy.as_str().to_string();
            }
            if record.upstream_traceparent.is_none() {
                record.upstream_traceparent = upstream_traceparent.map(str::to_string);
            }
            if record.upstream_parent_span_id.is_none() {
                record.upstream_parent_span_id = upstream_parent_span_id.map(str::to_string);
            }
            if record.sofa_traceid.is_none() {
                record.sofa_traceid = sofa_traceid.map(str::to_string);
            }
            if record.sofa_rpcid.is_none() {
                record.sofa_rpcid = sofa_rpcid.map(str::to_string);
            }
            let Some(started_at) = parse_unix_nanos(started_at_unix_nano) else {
                record.last_error = Some("invalid_start_timestamp".to_string());
                return;
            };
            let should_replace = record
                .started_at_unix_nano
                .as_deref()
                .and_then(parse_unix_nanos)
                .map(|existing| started_at < existing)
                .unwrap_or(true);
            if should_replace {
                record.started_at_unix_nano = Some(started_at_unix_nano.to_string());
                record.timing_source = Some("hook_pair".to_string());
                if let Some(completed) = record
                    .completed_at_unix_nano
                    .as_deref()
                    .and_then(parse_unix_nanos)
                {
                    if completed > started_at {
                        record.duration_unix_nano = Some((completed - started_at).to_string());
                        record.last_error = None;
                    } else {
                        record.duration_unix_nano = None;
                        record.last_error = Some("invalid_or_nonpositive_interval".to_string());
                    }
                } else {
                    record.last_error = None;
                }
            }
            if record.created_at.is_empty() {
                record.created_at = now.to_string();
            }
        })
    }

    pub fn record_post(
        context: &CommonContext,
        session_id: &str,
        tool_use_id: &str,
        completed_at_unix_nano: &str,
    ) -> Result<(ToolRecord, CompletedToolTiming), String> {
        let record = update_record(context, session_id, tool_use_id, |record, _now| {
            let Some(completed_at) = parse_unix_nanos(completed_at_unix_nano) else {
                record.last_error = Some("invalid_completion_timestamp".to_string());
                return;
            };
            let Some(started_at) = record
                .started_at_unix_nano
                .as_deref()
                .and_then(parse_unix_nanos)
            else {
                record.last_error = Some("missing_start_timestamp".to_string());
                return;
            };
            if completed_at <= started_at {
                record.last_error = Some("invalid_or_nonpositive_interval".to_string());
                return;
            }
            if record.completed_at_unix_nano.is_none() {
                record.completed_at_unix_nano = Some(completed_at_unix_nano.to_string());
                record.duration_unix_nano = Some((completed_at - started_at).to_string());
            }
            record.last_error = None;
        })?;
        let timing = record.completed_timing()?;
        Ok((record, timing))
    }

    pub fn mark_reconciled(
        context: &CommonContext,
        session_id: &str,
        tool_use_id: &str,
    ) -> Result<(), String> {
        update_record(context, session_id, tool_use_id, |record, now| {
            record.state_reconciled_at = Some(now.to_string());
        })
        .map(|_| ())
    }

    #[cfg(test)]
    pub(super) fn read(
        context: &CommonContext,
        session_id: &str,
        tool_use_id: &str,
    ) -> Option<ToolRecord> {
        read_domain_record(&context.state_file, session_id, tool_use_id)
    }

    #[cfg(test)]
    pub fn read_for_state_file(
        state_file: &Path,
        session_id: &str,
        tool_use_id: &str,
    ) -> Option<ToolRecord> {
        read_domain_record(state_file, session_id, tool_use_id)
    }

    pub fn completed_for_turn(
        state_file: &Path,
        session_id: &str,
        turn_id: &str,
    ) -> Vec<ToolRecord> {
        let mut records = tool_domain_state(state_file, session_id)
            .get("records")
            .and_then(Value::as_object)
            .into_iter()
            .flat_map(|records| records.values())
            .filter_map(|value| serde_json::from_value::<ToolRecord>(value.clone()).ok())
            .filter(|record| {
                record.session_id == session_id
                    && record.turn_id.as_deref() == Some(turn_id)
                    && record.completed_timing().is_ok()
            })
            .collect::<Vec<_>>();
        records.sort_by(|left, right| {
            left.started_at_unix_nano
                .cmp(&right.started_at_unix_nano)
                .then_with(|| left.tool_use_id.cmp(&right.tool_use_id))
        });
        records
    }

    pub fn persist_semantic_mapping(
        state_file: &Path,
        session_id: &str,
        tool_use_id: &str,
        assistant_index: usize,
        semantic_generation_ordinal: u64,
    ) -> Result<ToolRecord, String> {
        let store = tool_domain_store(state_file, session_id)?;
        store
            .update(|state| {
                let record_value = state
                    .get_mut("records")
                    .and_then(Value::as_object_mut)
                    .and_then(|records| records.get_mut(tool_use_id))
                    .ok_or_else(|| "tool record does not exist".to_string())?;
                let mut record: ToolRecord = serde_json::from_value(record_value.clone())
                    .map_err(|error| error.to_string())?;
                if record.session_id != session_id || record.tool_use_id != tool_use_id {
                    return Err("tool record identity mismatch".to_string());
                }
                if let Some(existing) = record.semantic_generation_ordinal {
                    if existing != semantic_generation_ordinal
                        || record.assistant_index != Some(assistant_index)
                    {
                        return Err("tool semantic mapping conflict".to_string());
                    }
                    return Ok(record);
                }
                record.assistant_index = Some(assistant_index);
                record.semantic_generation_ordinal = Some(semantic_generation_ordinal);
                record.updated_at = now_iso8601_pub();
                *record_value = serde_json::to_value(&record).map_err(|e| e.to_string())?;
                Ok(record)
            })
            .map(|(record, _changed)| record)
    }

    fn update_record<F>(
        context: &CommonContext,
        session_id: &str,
        tool_use_id: &str,
        update: F,
    ) -> Result<ToolRecord, String>
    where
        F: FnOnce(&mut ToolRecord, &str),
    {
        let store = tool_domain_store(&context.state_file, session_id)?;
        store
            .update(|state| {
                ensure_records_object(state);
                let now = now_iso8601_pub();
                let mut record = state["records"]
                    .get(tool_use_id)
                    .cloned()
                    .and_then(|value| serde_json::from_value::<ToolRecord>(value).ok())
                    .unwrap_or_else(|| ToolRecord {
                        schema_version: TOOL_RECORD_SCHEMA_VERSION,
                        session_id: session_id.to_string(),
                        tool_use_id: tool_use_id.to_string(),
                        tool_span_id: deterministic_tool_span_id(tool_use_id),
                        generation_batch_size: 0,
                        retry_policy: String::new(),
                        created_at: now.clone(),
                        updated_at: now.clone(),
                        ..Default::default()
                    });
                update(&mut record, &now);
                record.updated_at = now;
                state["records"][tool_use_id] =
                    serde_json::to_value(&record).map_err(|error| error.to_string())?;
                Ok(record)
            })
            .map(|(record, _changed)| record)
    }

    fn tool_domain_store(
        state_file: &Path,
        session_id: &str,
    ) -> Result<crate::common::runtime_state::DomainStateStore, String> {
        crate::common::runtime_state::DomainStateStore::for_compat_state_file(
            session_id,
            crate::common::runtime_state::StateDomain::Tools,
            state_file,
        )
    }

    fn tool_domain_state(state_file: &Path, session_id: &str) -> Value {
        tool_domain_store(state_file, session_id)
            .and_then(|store| store.load())
            .unwrap_or_else(|_| json!({}))
    }

    #[cfg(test)]
    fn read_domain_record(
        state_file: &Path,
        session_id: &str,
        tool_use_id: &str,
    ) -> Option<ToolRecord> {
        tool_domain_state(state_file, session_id)
            .get("records")?
            .get(tool_use_id)
            .cloned()
            .and_then(|value| serde_json::from_value(value).ok())
            .filter(|record: &ToolRecord| {
                record.session_id == session_id && record.tool_use_id == tool_use_id
            })
    }

    fn ensure_records_object(state: &mut Value) {
        if !state.is_object() {
            *state = json!({});
        }
        if !state.get("records").is_some_and(Value::is_object) {
            state["records"] = json!({});
        }
    }

    fn parse_unix_nanos(value: &str) -> Option<u128> {
        value.parse::<u128>().ok().filter(|value| *value > 0)
    }
}

fn stable_fallback_root_span_id(
    session_id: &str,
    turn_id: Option<&str>,
    tool_use_id: &str,
) -> String {
    let boundary = turn_id
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or(tool_use_id);
    deterministic_tool_span_id(&format!(
        "cdxhooks.realtime.turn-root.v1:{session_id}:{boundary}"
    ))
}

fn realtime_context_from_tool_record(
    record: &tool_records::ToolRecord,
) -> Option<realtime_state::RealtimeContext> {
    let context_id = record.context_id.as_deref()?.trim();
    let trace_id = record.trace_id.as_deref()?.trim();
    let root_span_id = record.root_span_id.as_deref()?.trim();
    if context_id.is_empty() || trace_id.is_empty() || root_span_id.is_empty() {
        return None;
    }
    let mut tool_span_ids = std::collections::HashMap::new();
    tool_span_ids.insert(record.tool_use_id.clone(), record.tool_span_id.clone());
    Some(realtime_state::RealtimeContext {
        schema_version: realtime_state::REALTIME_STATE_SCHEMA_VERSION,
        context_id: context_id.to_string(),
        trace_id: trace_id.to_string(),
        root_span_id: root_span_id.to_string(),
        turn_id: record.turn_id.clone(),
        created_at: record.created_at.clone(),
        generation_batch_size: record.generation_batch_size.max(1),
        retry_policy: RetryPolicy::parse(&record.retry_policy)
            .unwrap_or(RetryPolicy::AtLeastOnce)
            .as_str()
            .to_string(),
        trace: realtime_state::RealtimeTraceSnapshot {
            traceparent: record.upstream_traceparent.clone(),
            upstream_parent_span_id: record.upstream_parent_span_id.clone(),
            sofa_traceid: record.sofa_traceid.clone(),
            sofa_rpcid: record.sofa_rpcid.clone(),
        },
        tool_use_ids: vec![record.tool_use_id.clone()],
        tool_span_ids,
        ..realtime_state::RealtimeContext::default()
    })
}

fn ensure_realtime_context_from_tool_record_in_state(
    state: &mut Value,
    session_id: &str,
    record: &tool_records::ToolRecord,
) -> Result<realtime_state::RealtimeContext, String> {
    if let Some(context_id) = record.context_id.as_deref() {
        if realtime_state::context_alias_exists(state, session_id, context_id) {
            let resolved = realtime_state::resolve_context_alias(state, session_id, context_id)
                .map_err(|error| error.to_string())?;
            realtime_state::add_tool_to_context_in_state(
                state,
                session_id,
                &resolved,
                &record.tool_use_id,
            )?;
            return realtime_state::contexts(state, session_id)
                .into_iter()
                .find(|context| context.context_id == resolved)
                .ok_or_else(|| {
                    format!(
                        "tool record alias target disappeared: context_id={context_id} target={resolved}"
                    )
                });
        }
    }
    let context = realtime_context_from_tool_record(record)
        .ok_or_else(|| "tool record is missing realtime topology".to_string())?;
    realtime_state::ensure_context_in_state(state, session_id, &context)
}

fn reconcile_completed_tool_record_in_state(
    state: &mut Value,
    session_id: &str,
    record: &tool_records::ToolRecord,
) -> Result<(), String> {
    let context = ensure_realtime_context_from_tool_record_in_state(state, session_id, record)?;
    let started_at = record
        .started_at_unix_nano
        .as_deref()
        .ok_or_else(|| "tool record is missing start timestamp".to_string())?;
    let completed_at = record
        .completed_at_unix_nano
        .as_deref()
        .ok_or_else(|| "tool record is missing completion timestamp".to_string())?;
    realtime_state::record_tool_start_in_state(
        state,
        session_id,
        &context.context_id,
        &record.tool_use_id,
        started_at,
    )?;
    realtime_state::complete_tool_timing_in_state(
        state,
        session_id,
        &record.tool_use_id,
        completed_at,
    )
    .map(|_| ())
}

fn transcript_tool_timing(
    payload: &Value,
    tool_use_id: &str,
) -> Option<tool_records::CompletedToolTiming> {
    let transcript_path = extract_transcript_path(payload)?;
    let content = std::fs::read_to_string(transcript_path).ok()?;
    let entries = content
        .lines()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .collect::<Vec<_>>();
    let mut matches = crate::common::transcript::build_turns(&entries)
        .into_iter()
        .flat_map(|turn| turn.tool_calls)
        .filter(|tool| tool.call_id == tool_use_id)
        .filter_map(|tool| {
            let started = strict_timestamp_to_nanos(&tool.started_timestamp)?;
            let completed = strict_timestamp_to_nanos(&tool.ended_timestamp)?;
            let started_nanos = started.parse::<u128>().ok()?;
            let completed_nanos = completed.parse::<u128>().ok()?;
            (completed_nanos > started_nanos).then_some(tool_records::CompletedToolTiming {
                started_at_unix_nano: started,
                completed_at_unix_nano: completed,
                duration_unix_nano: (completed_nanos - started_nanos).to_string(),
            })
        });
    let timing = matches.next()?;
    matches.next().is_none().then_some(timing)
}

fn strict_timestamp_to_nanos(timestamp: &str) -> Option<String> {
    let timestamp = timestamp.trim();
    if timestamp.is_empty() {
        return None;
    }
    if timestamp.bytes().all(|byte| byte.is_ascii_digit()) {
        return timestamp
            .parse::<u128>()
            .ok()
            .filter(|value| *value > 0)
            .map(|value| value.to_string());
    }
    let looks_iso = timestamp.len() >= 19
        && timestamp.as_bytes().get(4) == Some(&b'-')
        && timestamp.as_bytes().get(7) == Some(&b'-')
        && matches!(timestamp.as_bytes().get(10), Some(b'T' | b' '));
    if !looks_iso {
        return None;
    }
    agentic_otel_common::iso_to_nanos(timestamp)
        .parse::<u128>()
        .ok()
        .filter(|value| *value > 0)
        .map(|value| value.to_string())
}

impl ToolContextHandler {
    fn handle_pre_tool_use(
        &self,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        let tool_name = extract_tool_name(payload);
        let tool_use_id = extract_tool_use_id(payload);
        if tool_use_id.is_empty() {
            context
                .logger
                .debug("ToolContext: PreToolUse without tool_use_id, skipping");
            return Ok(());
        }

        let session_id = extract_session_id(payload);
        if session_id.is_empty() {
            return Ok(());
        }
        if should_defer_realtime_tool(payload, &tool_name) {
            context.logger.debug(&format!(
                "ToolContext: deferring ambiguous realtime PreToolUse tool_name={tool_name} tool_use_id={tool_use_id} to Stop"
            ));
            return Ok(());
        }

        let upstream_traceparent = agentic_otel_common::parse_traceparent_env();
        let upstream_traceparent_raw = non_empty_env("TRACEPARENT");
        let authoritative_trace_id = crate::common::turn_seed::trace_id_for_tool(
            &session_id,
            upstream_traceparent_raw.as_deref(),
        )
        .map_err(HandlerError::from)?;
        let sofa_traceid = non_empty_env("SOFA_TRACEID");
        let sofa_rpcid = non_empty_env("SOFA_RPCID");
        let tool_span_id = deterministic_tool_span_id(&tool_use_id);
        let hook_started_at_unix_nano = agentic_otel_common::now_nanos();
        let context_created_at = now_iso8601_pub();
        let native_turn_id = extract_native_turn_id(payload);
        let mut traceparent = String::new();
        let mut record_trace_id = authoritative_trace_id.clone();
        let mut record_upstream_traceparent = upstream_traceparent_raw.clone();
        let mut record_upstream_parent_span_id = upstream_traceparent
            .as_ref()
            .map(|tp| tp.parent_span_id.clone());
        let mut record_sofa_traceid = sofa_traceid.clone();
        let mut record_sofa_rpcid = sofa_rpcid.clone();
        let mut record_root_span_id =
            stable_fallback_root_span_id(&session_id, native_turn_id.as_deref(), &tool_use_id);
        let mut realtime_context_id = None;
        let mut claim_error = None;
        let mut identity_committed = false;
        let mut updated_state = serde_json::json!({});
        let update_result = update_state_transaction_with_timeout(
            &context.state_file,
            &mut updated_state,
            FOREGROUND_STATE_LOCK_TIMEOUT_MS,
            |candidate| {
                let current_context = get_current_trace_context(candidate, &session_id);
                let trace_id = authoritative_trace_id.clone();
                let upstream_parent_span_id = current_context
                    .as_ref()
                    .and_then(|ctx| ctx.upstream_parent_span_id.clone())
                    .or_else(|| {
                        upstream_traceparent
                            .as_ref()
                            .map(|tp| tp.parent_span_id.clone())
                    });
                if context.export_mode.realtime_enabled() {
                    match realtime_state::claim_tool_for_native_turn_in_state(
                        candidate,
                        &session_id,
                        &tool_use_id,
                        native_turn_id.as_deref(),
                        &context_created_at,
                        &trace_id,
                        upstream_traceparent_raw.as_deref(),
                        upstream_parent_span_id.as_deref(),
                        sofa_traceid.as_deref(),
                        sofa_rpcid.as_deref(),
                        context.realtime_llm_batch_size,
                        context.retry_policy,
                    ) {
                        Ok(claimed) => {
                            let realtime_context = claimed.context;
                            record_trace_id = realtime_context.trace_id.clone();
                            record_root_span_id = realtime_context.root_span_id.clone();
                            record_upstream_traceparent =
                                realtime_context.trace.traceparent.clone();
                            record_upstream_parent_span_id =
                                realtime_context.trace.upstream_parent_span_id.clone();
                            record_sofa_traceid = realtime_context.trace.sofa_traceid.clone();
                            record_sofa_rpcid = realtime_context.trace.sofa_rpcid.clone();
                            realtime_context_id = Some(realtime_context.context_id.clone());
                            traceparent = format!("00-{}-{}-01", record_trace_id, tool_span_id);
                            let trace_context = ToolTraceContext {
                                trace_id: record_trace_id.clone(),
                                traceparent: traceparent.clone(),
                                upstream_parent_span_id: record_upstream_parent_span_id.clone(),
                                sofa_traceid: record_sofa_traceid.clone(),
                                sofa_rpcid: record_sofa_rpcid.clone(),
                            };
                            set_current_trace_context(candidate, &session_id, &trace_context);
                            context.logger.debug(&format!(
                                "ToolContext: native turn claim action={} session={} turn_id={} context_id={} aliases={}",
                                claimed.action.as_str(),
                                session_id,
                                native_turn_id.as_deref().unwrap_or(""),
                                realtime_context.context_id,
                                claimed.aliases.len()
                            ));
                            identity_committed = true;
                        }
                        Err(error) => {
                            claim_error = Some(error);
                        }
                    }
                } else {
                    record_trace_id = trace_id.clone();
                    record_upstream_parent_span_id = upstream_parent_span_id.clone();
                    traceparent = format!("00-{}-{}-01", trace_id, tool_span_id);
                    let trace_context = ToolTraceContext {
                        trace_id,
                        traceparent: traceparent.clone(),
                        upstream_parent_span_id,
                        sofa_traceid: sofa_traceid.clone(),
                        sofa_rpcid: sofa_rpcid.clone(),
                    };
                    set_current_trace_context(candidate, &session_id, &trace_context);
                    identity_committed = true;
                }
            },
        );
        if let Err(err) = update_result {
            context.logger.warn(&format!(
                "ToolContext: state busy; candidate identity not committed; Stop will compensate: {err}"
            ));
            identity_committed = false;
        }
        if let Some(err) = claim_error.as_ref() {
            context.logger.warn(&format!(
                "ToolContext: native turn claim deferred for session={session_id} turn_id={} tool_use_id={tool_use_id}: {err}",
                native_turn_id.as_deref().unwrap_or("")
            ));
            identity_committed = false;
        }

        if identity_committed {
            if let Err(err) = tool_records::record_pre(
                context,
                &session_id,
                &tool_use_id,
                &tool_span_id,
                realtime_context_id.as_deref(),
                &record_trace_id,
                &record_root_span_id,
                native_turn_id.as_deref(),
                record_upstream_traceparent.as_deref(),
                record_upstream_parent_span_id.as_deref(),
                record_sofa_traceid.as_deref(),
                record_sofa_rpcid.as_deref(),
                context.realtime_llm_batch_size,
                context.retry_policy,
                &hook_started_at_unix_nano,
            ) {
                context.logger.warn(&format!(
                    "ToolContext: realtime Tool start record was not persisted for tool_use_id={tool_use_id}; Stop will compensate: {err}"
                ));
            }
        } else {
            context.logger.debug(&format!(
                "ToolContext: authoritative ToolRecord deferred for tool_use_id={tool_use_id}"
            ));
        }

        if tool_name != "Bash" || !env_is_truthy("CDXHOOKS_TOOL_CONTEXT") {
            context.logger.debug(&format!(
                "ToolContext: persisted realtime identity without Bash injection for tool_use_id={tool_use_id}"
            ));
            return Ok(());
        }

        let Some(command) = extract_command(payload) else {
            context
                .logger
                .debug("ToolContext: Bash PreToolUse without command, skipping injection");
            return Ok(());
        };
        if !identity_committed {
            context.logger.debug(&format!(
                "ToolContext: Bash trace injection deferred for tool_use_id={tool_use_id}"
            ));
            return Ok(());
        }

        let payload_cwd = extract_cwd(payload);
        let vcs_info = get_vcs_for_cwd(&updated_state, &session_id, payload_cwd.as_deref());
        let injected_command = inject_env_exports(
            &command,
            &session_id,
            &tool_use_id,
            &traceparent,
            record_sofa_traceid.as_deref(),
            record_sofa_rpcid.as_deref(),
            vcs_info.as_ref(),
            context,
        );
        let response = json!({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {
                    "command": injected_command
                }
            }
        });
        println!("{}", response);
        context.logger.debug(&format!(
            "ToolContext: injected trace context for Bash tool_use_id={}",
            tool_use_id
        ));
        Ok(())
    }

    pub(crate) fn handle_post_tool_use(
        &self,
        payload: &Value,
        context: &CommonContext,
        _spawn_worker: bool,
    ) -> Result<(), HandlerError> {
        if !context.export_mode.realtime_enabled() {
            return Ok(());
        }

        let session_id = extract_session_id(payload);
        if session_id.is_empty() {
            return Ok(());
        }
        let tool_use_id = extract_tool_use_id(payload);
        if tool_use_id.is_empty() {
            context
                .logger
                .debug("ToolContext: PostToolUse without tool_use_id, deferring to Stop");
            return Ok(());
        }
        let tool_name = extract_tool_name(payload);
        if should_defer_realtime_tool(payload, &tool_name) {
            context.logger.debug(&format!(
                "ToolContext: deferring ambiguous realtime PostToolUse tool_name={tool_name} tool_use_id={tool_use_id} to Stop"
            ));
            return Ok(());
        }

        let hook_completed_at_unix_nano = agentic_otel_common::now_nanos();
        let tool_record_result = tool_records::record_post(
            context,
            &session_id,
            &tool_use_id,
            &hook_completed_at_unix_nano,
        );
        let native_turn_id = extract_native_turn_id(payload);
        let mut state_snapshot = load_state(&context.state_file);
        let mut turn_binding_error = None;
        if let Some(turn_id) = native_turn_id.as_deref() {
            match realtime_state::canonicalize_turn_contexts_in_state(
                &mut state_snapshot,
                &session_id,
                turn_id,
            ) {
                Ok(Some(canonical)) => context.logger.debug(&format!(
                    "ToolContext: PostToolUse canonicalization action={} session={} turn_id={} context_id={} aliases={}",
                    canonical.action.as_str(),
                    session_id,
                    turn_id,
                    canonical.context.context_id,
                    canonical.aliases.len()
                )),
                Ok(None) => {}
                Err(error) => turn_binding_error = Some(error.to_string()),
            }
        }
        let context_before_binding = realtime_state::contexts(&state_snapshot, &session_id)
            .into_iter()
            .find(|context| context.tool_use_ids.iter().any(|id| id == &tool_use_id));
        if turn_binding_error.is_none() {
            if let Some(realtime_context) = context_before_binding.as_ref() {
                if let Err(err) = realtime_state::bind_turn_id_in_state(
                    &mut state_snapshot,
                    &session_id,
                    &realtime_context.context_id,
                    native_turn_id.as_deref(),
                ) {
                    turn_binding_error = Some(err);
                }
            }
        }
        if let Some(err) = turn_binding_error.as_deref() {
            context.logger.warn(&format!(
                "ToolContext: native turn correlation rejected for tool_use_id={tool_use_id}; realtime Flush deferred: {err}"
            ));
        }

        let completed_timing = match tool_record_result {
            Ok((record, timing)) => Some((Some(record), timing)),
            Err(record_err) => match transcript_tool_timing(payload, &tool_use_id) {
                Some(timing) => {
                    context.logger.warn(&format!(
                            "ToolContext: realtime Tool hook timing unavailable for tool_use_id={tool_use_id}; using transcript fallback: {record_err}"
                        ));
                    Some((None, timing))
                }
                None => {
                    context.logger.warn(&format!(
                            "ToolContext: realtime Tool timing unavailable for tool_use_id={tool_use_id}; Stop will compensate: {record_err}"
                        ));
                    None
                }
            },
        };

        let realtime_context = realtime_state::contexts(&state_snapshot, &session_id)
            .into_iter()
            .find(|candidate| candidate.tool_use_ids.iter().any(|id| id == &tool_use_id))
            .or_else(|| {
                completed_timing
                    .as_ref()
                    .and_then(|(record, _)| record.as_ref())
                    .and_then(realtime_context_from_tool_record)
            });
        let Some(realtime_context) = realtime_context else {
            context.logger.warn(&format!(
                "ToolContext: PostToolUse missing realtime context for tool_use_id={tool_use_id}; Stop will compensate"
            ));
            return Ok(());
        };
        let prepared_flush = if turn_binding_error.is_none() {
            prepare_realtime_flush(
                &session_id,
                payload,
                context,
                &state_snapshot,
                &realtime_context,
                &tool_use_id,
            )
        } else {
            None
        };
        let mut updated_state = json!({});
        let mut flush_registration_error = None;
        let mut timing_persistence_error = None;
        let mut turn_binding_persistence_error = None;
        let turn_binding_needs_persistence = context_before_binding
            .as_ref()
            .is_some_and(|before| before.turn_id != realtime_context.turn_id);
        let state_update_needed = prepared_flush.is_some()
            || turn_binding_needs_persistence
            || completed_timing.is_some();
        let register_result = state_update_needed.then(|| {
            update_state_transaction_fallible_with_timeout(
                &context.state_file,
                &mut updated_state,
                FOREGROUND_STATE_LOCK_TIMEOUT_MS,
                |candidate| {
                    if let Some(turn_id) = native_turn_id.as_deref() {
                        if let Err(error) = realtime_state::canonicalize_turn_contexts_in_state(
                            candidate,
                            &session_id,
                            turn_id,
                        ) {
                            let error = error.to_string();
                            turn_binding_persistence_error = Some(error.clone());
                            return Err(error);
                        }
                    }
                    if turn_binding_needs_persistence {
                        if let Err(err) = realtime_state::bind_turn_id_in_state(
                            candidate,
                            &session_id,
                            &realtime_context.context_id,
                            native_turn_id.as_deref(),
                        ) {
                            turn_binding_persistence_error = Some(err.clone());
                            return Err(err);
                        }
                    }
                    if let Some(record) = completed_timing
                        .as_ref()
                        .and_then(|(record, _)| record.as_ref())
                    {
                        if let Err(err) =
                            reconcile_completed_tool_record_in_state(candidate, &session_id, record)
                        {
                            timing_persistence_error = Some(err.clone());
                            return Err(err);
                        }
                    }
                    if let Some(flush) = prepared_flush.as_ref() {
                        realtime_state::ensure_generation_state_in_state(
                            candidate,
                            &session_id,
                            realtime_context.generation_batch_size,
                        );
                        if let Err(err) = realtime_state::register_realtime_job_in_state(
                            candidate,
                            &session_id,
                            &realtime_context.context_id,
                            RealtimeSpoolKind::Flush.as_str(),
                            &flush.job_id,
                            flush.payload_digest.as_deref().unwrap_or_default(),
                        ) {
                            flush_registration_error = Some(err.clone());
                            return Err(err);
                        }
                        if let Err(err) = realtime_state::register_pending_flush_in_state(
                            candidate,
                            &session_id,
                            flush,
                        ) {
                            flush_registration_error = Some(err.clone());
                            return Err(err);
                        }
                    }
                    Ok(())
                },
            )
        });
        let registration_committed = matches!(register_result, Some(Ok(())));
        let retryable_registration_error = register_result
            .as_ref()
            .and_then(|result| result.as_ref().err())
            .filter(|error| error.starts_with("state lock unavailable"))
            .cloned();
        if let Some(Err(ref err)) = register_result {
            context.logger.warn(&format!(
                "ToolContext: realtime registration transaction failed for tool_use_id={tool_use_id}; worker will reconcile published jobs: {err}"
            ));
        }
        if let Some(err) = flush_registration_error {
            context.logger.warn(&format!(
                "ToolContext: realtime Flush registration rejected for tool_use_id={tool_use_id}; worker will reconcile the published job: {err}"
            ));
        }
        if let Some(err) = timing_persistence_error {
            context.logger.warn(&format!(
                "ToolContext: realtime Tool timing checkpoint failed for tool_use_id={tool_use_id}; published jobs remain recoverable: {err}"
            ));
        } else if registration_committed && completed_timing.is_some() {
            if completed_timing
                .as_ref()
                .and_then(|(record, _)| record.as_ref())
                .is_some()
            {
                if let Err(err) = tool_records::mark_reconciled(context, &session_id, &tool_use_id)
                {
                    context.logger.warn(&format!(
                        "ToolContext: realtime Tool record reconcile marker failed for tool_use_id={tool_use_id}: {err}"
                    ));
                }
            }
        }
        if let Some(err) = turn_binding_persistence_error {
            context.logger.warn(&format!(
                "ToolContext: native turn checkpoint failed for tool_use_id={tool_use_id}; published jobs remain recoverable: {err}"
            ));
        }

        if registration_committed && prepared_flush.is_some() {
            context.logger.debug(&format!(
                "ToolContext: realtime Flush registered in state session={session_id} tool_use_id={tool_use_id}"
            ));
        }
        if let Some(error) = retryable_registration_error {
            return Err(HandlerError::retryable(format!(
                "ToolContext state reconciliation retry required for tool_use_id={tool_use_id}: {error}"
            )));
        }
        Ok(())
    }
}

fn extract_tool_name(payload: &Value) -> String {
    payload
        .get("tool_name")
        .or_else(|| payload.get("toolName"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

fn extract_tool_use_id(payload: &Value) -> String {
    payload
        .get("tool_use_id")
        .or_else(|| payload.get("toolUseId"))
        .or_else(|| payload.get("call_id"))
        .or_else(|| payload.get("callId"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

fn extract_command(payload: &Value) -> Option<String> {
    payload
        .get("tool_input")
        .or_else(|| payload.get("toolInput"))
        .or_else(|| payload.get("input"))
        .and_then(|input| input.get("command"))
        .and_then(|command| command.as_str())
        .map(String::from)
}

fn should_defer_realtime_tool(payload: &Value, tool_name: &str) -> bool {
    payload
        .get("agent_id")
        .or_else(|| payload.get("agentId"))
        .and_then(Value::as_str)
        .is_some_and(|value| !value.is_empty())
        || is_orchestration_tool(tool_name)
}

fn is_orchestration_tool(tool_name: &str) -> bool {
    matches!(
        tool_name,
        "spawn_agent"
            | "followup_task"
            | "send_message"
            | "wait_agent"
            | "interrupt_agent"
            | "list_agents"
            | "collaboration.spawn_agent"
            | "collaboration.followup_task"
            | "collaboration.send_message"
            | "collaboration.wait_agent"
            | "collaboration.interrupt_agent"
            | "collaboration.list_agents"
    )
}

fn extract_transcript_path(payload: &Value) -> Option<String> {
    payload
        .get("transcript_path")
        .or_else(|| payload.get("transcriptPath"))
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(String::from)
}

fn extract_native_turn_id(payload: &Value) -> Option<String> {
    payload
        .get("turn_id")
        .or_else(|| payload.get("turnId"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(String::from)
}

fn extract_cwd(payload: &Value) -> Option<String> {
    payload
        .get("cwd")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(String::from)
}

fn prepare_realtime_flush(
    session_id: &str,
    payload: &Value,
    context: &CommonContext,
    state_snapshot: &Value,
    realtime_context: &realtime_state::RealtimeContext,
    tool_use_id: &str,
) -> Option<spool::SpoolJob> {
    let Some(transcript_path) = extract_transcript_path(payload) else {
        context
            .logger
            .debug("ToolContext: PostToolUse without transcript_path; realtime Flush deferred");
        return None;
    };
    let scan_anchor = realtime_state::generation_scan_anchor(state_snapshot, session_id);
    let generation_batch_size = realtime_context.generation_batch_size.max(1);
    let Some(turn_id) = realtime_context
        .turn_id
        .clone()
        .or_else(|| extract_native_turn_id(payload))
    else {
        context
            .logger
            .debug("ToolContext: PostToolUse without native turn_id; realtime Flush deferred");
        return None;
    };
    if !realtime_context
        .tool_use_ids
        .iter()
        .any(|candidate| candidate == tool_use_id)
    {
        context.logger.warn(
            "ToolContext: realtime Flush subject does not belong to its session context; Stop will compensate",
        );
        return None;
    }
    let trigger_subject = format!("{turn_id}:{tool_use_id}:{scan_anchor}");
    let null_digest = spool::payload_digest(&Value::Null).ok()?;
    let trigger_id = spool::deterministic_realtime_job_id_for_session_schema(
        RealtimeSpoolKind::Flush,
        spool::CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
        session_id,
        &realtime_context.context_id,
        &trigger_subject,
        &null_digest,
    );
    let manifest = spool::RealtimeFlushManifest {
        schema_version: 1,
        trigger_id: trigger_id.clone(),
        turn_id,
        transcript_path,
        scan_anchor,
        generation_batch_size,
        subject_id: tool_use_id.to_string(),
        cwd: Some(extract_cwd(payload).unwrap_or_else(|| context.sessions_dir.clone())),
        username: Some(context.username.clone()),
        hook_version: Some(context.hook_version.clone()),
    };

    Some(spool::SpoolJob {
        schema_version: 1,
        session_schema_version: spool::CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
        job_kind: spool::SpoolJobKind::Realtime(RealtimeSpoolKind::Flush),
        job_id: trigger_id.clone(),
        session_id: session_id.to_string(),
        turn_id: Some(manifest.turn_id.clone()),
        created_at: now_iso8601_pub(),
        claimed_at: None,
        attempts: 1,
        last_error: None,
        otel: Value::Null,
        transcript_path: Some(manifest.transcript_path.clone()),
        cwd: manifest.cwd.clone(),
        username: manifest.username.clone(),
        hook_version: manifest.hook_version.clone(),
        subject_id: Some(trigger_id),
        context_id: Some(realtime_context.context_id.clone()),
        payload_digest: Some(null_digest),
        batch_manifest: None,
        flush_manifest: Some(manifest),
    })
}

fn inject_env_exports(
    command: &str,
    session_id: &str,
    tool_use_id: &str,
    traceparent: &str,
    sofa_traceid: Option<&str>,
    sofa_rpcid: Option<&str>,
    vcs: Option<&VcsInfo>,
    context: &CommonContext,
) -> String {
    let mut exports = vec![
        export_stmt("TRACEPARENT", traceparent),
        export_stmt("AGENTIC_TOOL_USE_ID", tool_use_id),
    ];

    if let Some(value) = sofa_traceid {
        exports.push(export_stmt("SOFA_TRACEID", value));
    }
    if let Some(value) = sofa_rpcid {
        exports.push(export_stmt("SOFA_RPCID", value));
    }
    if let Some(ref config) = context.langfuse_config {
        let endpoint = resolve_otel_url(&config.host, "/api/public/otel/v1/traces");
        exports.push(export_stmt("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", &endpoint));
        if let (Some(pk), Some(sk)) = (&config.public_key, &config.secret_key) {
            let auth = build_basic_auth_header(pk, sk);
            exports.push(export_stmt("OTEL_EXPORTER_OTLP_AUTH", &auth));
        }
        if let Some(ref headers) = config.headers {
            if let Ok(headers_json) = serde_json::to_string(headers) {
                exports.push(export_stmt("OTEL_EXPORTER_OTLP_HEADERS", &headers_json));
            }
        }
    }

    let span_attrs = build_tool_span_attrs(session_id, tool_use_id, context);
    exports.push(export_stmt(
        "AGENTIC_SPAN_ATTRIBUTES",
        &span_attrs.to_string(),
    ));

    let resource_attrs = build_tool_resource_attrs(context, vcs);
    exports.push(export_stmt(
        "OTEL_RESOURCE_ATTRIBUTES",
        &resource_attrs.to_string(),
    ));

    format!("{}; {}", exports.join("; "), command)
}

fn build_tool_span_attrs(session_id: &str, tool_use_id: &str, context: &CommonContext) -> Value {
    json!({
        "ant.username": context.username.as_str(),
        "user.id": context.username.as_str(),
        "gen_ai.conversation.id": session_id,
        "gen_ai.span.kind": "TOOL",
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.call.id": tool_use_id,
        "agentic.tool.id": tool_use_id,
    })
}

fn build_tool_resource_attrs(context: &CommonContext, vcs: Option<&VcsInfo>) -> Value {
    let agent_product = resolve_agent_product();
    let mut attributes = json!({
        "service.name": agent_product,
        "ant.agent.product": agent_product,
        "ant.agent.type": resolve_agent_type(),
        "ant.agent.instance": format!("codex-{}", context.username),
        "telemetry.sdk.name": "cdxhooks",
        "telemetry.sdk.version": context.hook_version.as_str(),
    });
    if let Some(vcs) = vcs {
        if !vcs.remote_url.is_empty() {
            attributes["vcs.repository.url.full"] = json!(vcs.remote_url);
        }
        if !vcs.branch.is_empty() {
            attributes["vcs.ref.head.name"] = json!(vcs.branch);
        }
    }
    attributes
}

fn export_stmt(key: &str, value: &str) -> String {
    format!("export {}={}", key, shell_escape(value))
}

fn shell_escape(value: &str) -> String {
    if value.is_empty() {
        return "''".to_string();
    }
    format!("'{}'", value.replace('\'', "'\"'\"'"))
}

fn non_empty_env(key: &str) -> Option<String> {
    std::env::var(key).ok().filter(|value| !value.is_empty())
}

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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shell_escape_handles_quotes() {
        assert_eq!(shell_escape("a'b"), "'a'\"'\"'b'");
    }

    #[test]
    fn extract_command_from_tool_input() {
        let payload = json!({
            "tool_name": "Bash",
            "tool_use_id": "call-1",
            "tool_input": {"command": "echo hi"}
        });
        assert_eq!(extract_tool_name(&payload), "Bash");
        assert_eq!(extract_tool_use_id(&payload), "call-1");
        assert_eq!(extract_command(&payload).as_deref(), Some("echo hi"));
    }

    #[test]
    fn native_turn_id_does_not_fall_back_to_chat_aliases() {
        assert_eq!(
            extract_native_turn_id(&json!({"chat_id": "chat", "turn_id": "turn"})).as_deref(),
            Some("turn")
        );
        assert_eq!(extract_native_turn_id(&json!({"chat_id": "chat"})), None);
    }

    #[test]
    fn tool_span_attrs_include_session_and_call_id() {
        let context = test_context();
        let attrs = build_tool_span_attrs("session-1", "call-1", &context);

        assert_eq!(attrs["gen_ai.conversation.id"], "session-1");
        assert_eq!(attrs["gen_ai.tool.call.id"], "call-1");
        assert_eq!(attrs["agentic.tool.id"], "call-1");
        assert_eq!(attrs["gen_ai.span.kind"], "TOOL");
        assert_eq!(attrs["gen_ai.operation.name"], "execute_tool");
    }

    #[test]
    fn tool_resource_attrs_include_identity() {
        let context = test_context();
        let attrs = build_tool_resource_attrs(&context, None);

        assert!(attrs.get("service.name").and_then(|v| v.as_str()).is_some());
        assert!(attrs
            .get("ant.agent.product")
            .and_then(|v| v.as_str())
            .is_some());
        assert_eq!(attrs["ant.agent.type"], "codex");
        assert_eq!(attrs["ant.agent.instance"], "codex-user-1");
    }

    #[test]
    fn tool_resource_attrs_merge_non_empty_session_vcs() {
        let context = test_context();
        let attrs = build_tool_resource_attrs(
            &context,
            Some(&VcsInfo {
                remote_url: "ssh://example/repo.git".to_string(),
                branch: String::new(),
                cwd: "/repo".to_string(),
            }),
        );

        assert_eq!(attrs["vcs.repository.url.full"], "ssh://example/repo.git");
        assert!(attrs.get("vcs.ref.head.name").is_none());
    }

    #[test]
    fn inject_env_exports_inlines_context_before_original_command() {
        let dir = tempfile::tempdir().unwrap();
        let mut context =
            test_context_with_state_file(dir.path().join("state/cdxhooks_state.json"));
        context.langfuse_config = Some(crate::hooks::langfuse::LangfuseConfig {
            public_key: Some("pk-test".to_string()),
            secret_key: Some("sk-secret".to_string()),
            host: "https://example.test".to_string(),
            headers: Some(std::collections::HashMap::from([(
                "x-test".to_string(),
                "value".to_string(),
            )])),
        });

        let command = inject_env_exports(
            "python3 train.py",
            "session-1",
            "call-1",
            "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
            None,
            None,
            None,
            &context,
        );

        assert!(command.contains("export TRACEPARENT="));
        assert!(command.contains("export AGENTIC_TOOL_USE_ID="));
        assert!(command.contains("export OTEL_EXPORTER_OTLP_AUTH='Basic "));
        assert!(command.contains("export AGENTIC_SPAN_ATTRIBUTES="));
        assert!(command.contains("export OTEL_RESOURCE_ATTRIBUTES="));
        assert!(!command.contains("CDXHOOKS_TOOL_"));
        assert!(!command.contains("AGENTIC_RESOURCE_ATTRIBUTES"));
        assert!(command.contains("gen_ai.conversation.id"));
        assert!(command.ends_with("python3 train.py"));
        assert!(!command.contains("sk-secret"));
        assert!(!command.contains("rm -f"));
        assert!(!command.contains("cdxhooks_tool_env"));
    }

    #[test]
    fn pre_tool_use_persists_realtime_context_snapshot() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state/cdxhooks_state.json");
        let context = test_context_with_state_file(state_file.clone());
        let payload = json!({
            "session_id": "session-1",
            "tool_name": "Bash",
            "tool_use_id": "call-1",
            "tool_input": {"command": "echo hi"}
        });

        let result = ToolContextHandler::new().handle(&HookEvent::PreToolUse, &payload, &context);

        assert!(result.is_ok());
        let state = crate::hooks::langfuse::state::load_state(&state_file);
        let realtime = &state["session-1"]["realtime_export"]["current"];
        assert_eq!(realtime["schema_version"], 1);
        assert_eq!(realtime["tool_use_ids"], json!(["call-1"]));
        assert_eq!(
            realtime["tool_span_ids"]["call-1"],
            deterministic_tool_span_id("call-1")
        );
        assert_eq!(
            realtime["generation_batch_size"],
            realtime_state::DEFAULT_REALTIME_LLM_BATCH_SIZE
        );
        assert_eq!(realtime["retry_policy"], "at_least_once");
    }

    #[test]
    fn pre_tool_use_persists_realtime_identity_for_non_bash_tools() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state/cdxhooks_state.json");
        let context = test_context_with_state_file(state_file.clone());
        let payload = json!({
            "session_id": "session-1",
            "tool_name": "apply_patch",
            "tool_use_id": "call-apply",
            "tool_input": {"patch": "*** Begin Patch\n*** End Patch\n"}
        });

        let result = ToolContextHandler::new().handle(&HookEvent::PreToolUse, &payload, &context);

        assert!(result.is_ok());
        let state = crate::hooks::langfuse::state::load_state(&state_file);
        let realtime = &state["session-1"]["realtime_export"]["current"];
        assert_eq!(realtime["tool_use_ids"], json!(["call-apply"]));
        assert_eq!(
            realtime["tool_span_ids"]["call-apply"],
            deterministic_tool_span_id("call-apply")
        );
    }

    #[test]
    fn control_response_reuses_pending_native_turn_after_turn_seed_rotation() {
        let _guard = crate::test_sync::env_lock();
        let saved_tool_context = std::env::var("CDXHOOKS_TOOL_CONTEXT").ok();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("runtime-state");
        let _runtime = crate::common::dirs::override_runtime_dirs_for_test(
            state_dir,
            dir.path().join("spool"),
        );
        std::env::set_var("CDXHOOKS_TOOL_CONTEXT", "true");
        let state_file = dir.path().join("state/cdxhooks_state.json");
        let context = test_context_with_state_file(state_file.clone());
        let first_trace_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        let second_trace_id = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        crate::common::turn_seed::replace_for_prompt(
            "session-1",
            &json!({"turn_id": "turn-1"}),
            Some(format!("00-{first_trace_id}-1111111111111111-01")),
        )
        .unwrap();

        let first = build_pretooluse_control_response(
            &json!({
                "session_id": "session-1",
                "turn_id": "turn-1",
                "tool_name": "Bash",
                "tool_use_id": "call-1",
                "tool_input": {"command": "echo first"}
            }),
            &context,
        )
        .expect("first control response");
        assert!(first
            .pointer("/hookSpecificOutput/updatedInput/command")
            .and_then(Value::as_str)
            .unwrap()
            .contains(first_trace_id));

        let mut state = load_state(&state_file);
        realtime_state::rotate_current_in_state(&mut state, "session-1");
        crate::hooks::langfuse::state::save_state(&state_file, &state).unwrap();
        crate::common::turn_seed::replace_for_prompt(
            "session-1",
            &json!({"turn_id": "turn-2-seed"}),
            Some(format!("00-{second_trace_id}-2222222222222222-01")),
        )
        .unwrap();

        let second = build_pretooluse_control_response(
            &json!({
                "session_id": "session-1",
                "turn_id": "turn-1",
                "tool_name": "Bash",
                "tool_use_id": "call-2",
                "tool_input": {"command": "echo second"}
            }),
            &context,
        )
        .expect("same-turn control response");
        let second_command = second
            .pointer("/hookSpecificOutput/updatedInput/command")
            .and_then(Value::as_str)
            .unwrap();
        assert!(second_command.contains(first_trace_id));
        assert!(!second_command.contains(second_trace_id));

        let state = load_state(&state_file);
        let contexts = realtime_state::contexts(&state, "session-1");
        assert_eq!(contexts.len(), 1);
        assert_eq!(contexts[0].trace_id, first_trace_id);
        assert_eq!(contexts[0].turn_id.as_deref(), Some("turn-1"));
        assert_eq!(contexts[0].tool_use_ids, vec!["call-1", "call-2"]);

        match saved_tool_context {
            Some(value) => std::env::set_var("CDXHOOKS_TOOL_CONTEXT", value),
            None => std::env::remove_var("CDXHOOKS_TOOL_CONTEXT"),
        }
    }

    #[test]
    fn control_response_defers_multiple_published_roots_without_state_mutation() {
        let _guard = crate::test_sync::env_lock();
        let saved_tool_context = std::env::var("CDXHOOKS_TOOL_CONTEXT").ok();
        let dir = tempfile::tempdir().unwrap();
        let _runtime = crate::common::dirs::override_runtime_dirs_for_test(
            dir.path().join("runtime-state"),
            dir.path().join("spool"),
        );
        std::env::set_var("CDXHOOKS_TOOL_CONTEXT", "true");
        let state_file = dir.path().join("state/cdxhooks_state.json");
        let context = test_context_with_state_file(state_file.clone());
        let mut state = json!({});
        let first = realtime_state::claim_tool_in_state(
            &mut state,
            "session-1",
            "call-1",
            "first",
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
            "session-1",
            &first.context_id,
            Some("turn-1"),
        )
        .unwrap();
        realtime_state::rotate_current_in_state(&mut state, "session-1");
        let second = realtime_state::claim_tool_in_state(
            &mut state,
            "session-1",
            "call-2",
            "second",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            "session-1",
            &second.context_id,
            Some("turn-1"),
        )
        .unwrap();
        for (context_id, child_id) in [
            (first.context_id.as_str(), "child-1"),
            (second.context_id.as_str(), "child-2"),
        ] {
            let batch = realtime_state::prepare_realtime_batch_in_state(
                &mut state,
                "session-1",
                context_id,
                "tool",
                &[child_id.to_string()],
                child_id,
            )
            .unwrap();
            realtime_state::publish_realtime_batch_in_state(
                &mut state,
                "session-1",
                &batch.batch_id,
            )
            .unwrap();
            realtime_state::succeed_realtime_batch_in_state(
                &mut state,
                "session-1",
                &batch.batch_id,
            )
            .unwrap();
        }
        crate::hooks::langfuse::state::save_state(&state_file, &state).unwrap();
        let before = load_state(&state_file);

        let response = build_pretooluse_control_response(
            &json!({
                "session_id": "session-1",
                "turn_id": "turn-1",
                "tool_name": "Bash",
                "tool_use_id": "call-3",
                "tool_input": {"command": "echo ambiguous"}
            }),
            &context,
        );

        assert!(response.is_none());
        assert_eq!(load_state(&state_file), before);
        assert!(realtime_state::contexts(&before, "session-1")
            .iter()
            .all(|context| !context.tool_use_ids.iter().any(|id| id == "call-3")));
        assert!(tool_records::read(&context, "session-1", "call-3").is_none());

        match saved_tool_context {
            Some(value) => std::env::set_var("CDXHOOKS_TOOL_CONTEXT", value),
            None => std::env::remove_var("CDXHOOKS_TOOL_CONTEXT"),
        }
    }

    #[test]
    fn codex_hooks_without_payload_timestamps_emit_nonzero_tool_duration() {
        let _guard = crate::test_sync::env_lock();
        let saved_spool_dir = std::env::var("CDXHOOKS_SPOOL_DIR").ok();
        let dir = tempfile::tempdir().unwrap();
        let spool_dir = dir.path().join("spool");
        std::env::set_var("CDXHOOKS_SPOOL_DIR", &spool_dir);
        let session_spool = spool::session_root(&spool_dir, "session-redacted").unwrap();
        let state_file = dir.path().join("state/cdxhooks_state.json");
        let context = test_context_with_state_file(state_file);
        let hooks = include_str!("../../tests/fixtures/codex_realtime_tool_hooks.jsonl")
            .lines()
            .map(|line| serde_json::from_str::<Value>(line).unwrap())
            .collect::<Vec<_>>();

        ToolContextHandler::new()
            .handle(&HookEvent::PreToolUse, &hooks[0], &context)
            .unwrap();
        std::thread::sleep(std::time::Duration::from_millis(10));
        ToolContextHandler::new()
            .handle_post_tool_use(&hooks[1], &context, false)
            .unwrap();

        let record = tool_records::read(&context, "session-redacted", "exec-redacted")
            .expect("completed per-tool record");
        let timing = record
            .completed_timing()
            .expect("positive hook-pair timing");
        assert_ne!(timing.started_at_unix_nano, timing.completed_at_unix_nano);
        assert!(
            timing.duration_unix_nano.parse::<u128>().unwrap() > 0,
            "PreToolUse and PostToolUse hook-now timestamps must define Tool duration"
        );
        let state = crate::hooks::langfuse::state::load_state(&context.state_file);
        let pending = realtime_state::pending_flush_jobs(&state, "session-redacted");
        assert_eq!(pending.len(), 1);
        assert!(pending[0].otel.is_null());
        assert!(!session_spool.exists());

        match saved_spool_dir {
            Some(value) => std::env::set_var("CDXHOOKS_SPOOL_DIR", value),
            None => std::env::remove_var("CDXHOOKS_SPOOL_DIR"),
        }
    }

    #[test]
    fn post_tool_use_persists_timing_when_spool_is_unavailable() {
        let _guard = crate::test_sync::env_lock();
        let saved_spool_dir = std::env::var("CDXHOOKS_SPOOL_DIR").ok();
        let dir = tempfile::tempdir().unwrap();
        let spool_blocker = dir.path().join("spool-blocker");
        std::fs::write(&spool_blocker, "not a directory").unwrap();
        std::env::set_var("CDXHOOKS_SPOOL_DIR", &spool_blocker);
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(&transcript_path, "{}\n").unwrap();
        let state_file = dir.path().join("state/cdxhooks_state.json");
        let context = test_context_with_state_file(state_file.clone());
        let pre = json!({
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_use_id": "call-1",
            "tool_input": {"command": "true"}
        });
        ToolContextHandler::new()
            .handle(&HookEvent::PreToolUse, &pre, &context)
            .unwrap();
        let post = json!({
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_use_id": "call-1",
            "transcript_path": transcript_path,
            "tool_input": {"command": "true"},
            "tool_response": "{\"exit_code\":0}"
        });
        ToolContextHandler::new()
            .handle_post_tool_use(&post, &context, false)
            .unwrap();

        let record =
            tool_records::read(&context, "session-1", "call-1").expect("completed per-tool record");
        assert!(record.completed_timing().is_ok());
        let state = crate::hooks::langfuse::state::load_state(&state_file);
        let realtime_context = realtime_state::contexts(&state, "session-1")
            .into_iter()
            .find(|candidate| candidate.tool_use_ids.iter().any(|id| id == "call-1"))
            .expect("timing reconciled into session state");
        assert!(
            realtime_context
                .tool_timings
                .get("call-1")
                .and_then(|timing| timing.completed_at_unix_nano.as_ref())
                .is_some(),
            "Stop compensation must retain completed hook-pair timing even when Flush publication fails"
        );

        match saved_spool_dir {
            Some(value) => std::env::set_var("CDXHOOKS_SPOOL_DIR", value),
            None => std::env::remove_var("CDXHOOKS_SPOOL_DIR"),
        }
    }

    #[test]
    fn post_tool_use_publishes_and_registers_flush_only() {
        let _guard = crate::test_sync::env_lock();
        let saved_spool_dir = std::env::var("CDXHOOKS_SPOOL_DIR").ok();
        let dir = tempfile::tempdir().unwrap();
        let spool_dir = dir.path().join("spool");
        std::env::set_var("CDXHOOKS_SPOOL_DIR", &spool_dir);
        let session_spool = spool::session_root(&spool_dir, "session-1").unwrap();
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(&transcript_path, "{}\n").unwrap();

        let state_file = dir.path().join("state/cdxhooks_state.json");
        let context = test_context_with_state_file(state_file.clone());
        let pre = json!({
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_use_id": "call-1",
            "timestamp": "2026-07-27T10:00:00.000Z",
            "cwd": "/repo",
            "tool_input": {"command": "echo hi"}
        });
        ToolContextHandler::new()
            .handle(&HookEvent::PreToolUse, &pre, &context)
            .unwrap();

        let post = json!({
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_use_id": "call-1",
            "timestamp": "2026-07-27T10:00:01.000Z",
            "cwd": "/repo",
            "transcript_path": transcript_path,
            "tool_input": {"command": "echo hi"},
            "tool_response": "{\"stdout\":\"hi\\n\",\"stderr\":\"\",\"exit_code\":0}"
        });
        ToolContextHandler::new()
            .handle_post_tool_use(&post, &context, false)
            .unwrap();

        let state = crate::hooks::langfuse::state::load_state(&state_file);
        let current = &state["session-1"]["realtime_export"]["current"];
        let context_id = current["context_id"].as_str().unwrap();
        let pending = realtime_state::pending_flush_jobs(&state, "session-1");
        assert_eq!(pending.len(), 1);
        assert!(!session_spool.exists());
        assert!(pending[0].otel.is_null());
        assert!(pending[0].flush_manifest.is_some());
        let job_id = pending[0].job_id.as_str();
        let digest = pending[0]
            .payload_digest
            .as_deref()
            .expect("realtime Flush payload digest");
        assert!(realtime_state::registered_realtime_job_matches(
            &state,
            "session-1",
            job_id,
            digest
        ));
        assert_eq!(
            realtime_state::registered_realtime_job(&state, "session-1", job_id)
                .unwrap()
                .context_id,
            context_id
        );
        let registrations = state["session-1"]["realtime_export"]["registered_jobs"]
            .as_object()
            .unwrap();
        assert_eq!(registrations.len(), 1);
        assert!(registrations
            .values()
            .all(|job| job["kind"] == RealtimeSpoolKind::Flush.as_str()));
        assert_eq!(
            state["session-1"]["realtime_export"]["generation"]["scan_anchor"],
            0
        );

        match saved_spool_dir {
            Some(value) => std::env::set_var("CDXHOOKS_SPOOL_DIR", value),
            None => std::env::remove_var("CDXHOOKS_SPOOL_DIR"),
        }
    }

    #[test]
    fn post_tool_use_queues_network_free_flush_for_stringified_bash_response() {
        let _guard = crate::test_sync::env_lock();
        let saved_spool_dir = std::env::var("CDXHOOKS_SPOOL_DIR").ok();
        let dir = tempfile::tempdir().unwrap();
        let spool_dir = dir.path().join("spool");
        std::env::set_var("CDXHOOKS_SPOOL_DIR", &spool_dir);
        let session_spool = spool::session_root(&spool_dir, "session-1").unwrap();
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(&transcript_path, "{}\n").unwrap();

        let state_file = dir.path().join("state/cdxhooks_state.json");
        let context = test_context_with_state_file(state_file);
        let pre = json!({
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_use_id": "call-fail",
            "timestamp": "2026-07-27T10:00:00.000Z",
            "cwd": "/repo",
            "tool_input": {"command": "exit 1"}
        });
        ToolContextHandler::new()
            .handle(&HookEvent::PreToolUse, &pre, &context)
            .unwrap();

        let post = json!({
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_use_id": "call-fail",
            "timestamp": "2026-07-27T10:00:01.000Z",
            "cwd": "/repo",
            "transcript_path": transcript_path,
            "tool_input": {"command": "exit 1"},
            "tool_response": "{\"output\":\"\",\"metadata\":{\"exit_code\":1},\"exit_code\":1}"
        });
        ToolContextHandler::new()
            .handle_post_tool_use(&post, &context, false)
            .unwrap();

        let record =
            tool_records::read(&context, "session-1", "call-fail").expect("completed tool record");
        assert!(record.completed_timing().is_ok());
        let state = crate::hooks::langfuse::state::load_state(&context.state_file);
        let pending = realtime_state::pending_flush_jobs(&state, "session-1");
        assert_eq!(pending.len(), 1);
        assert!(pending[0].otel.is_null());
        assert!(!session_spool.exists());
        match saved_spool_dir {
            Some(value) => std::env::set_var("CDXHOOKS_SPOOL_DIR", value),
            None => std::env::remove_var("CDXHOOKS_SPOOL_DIR"),
        }
    }

    #[test]
    fn duplicate_post_tool_use_deduplicates_flush_jobs() {
        let _guard = crate::test_sync::env_lock();
        let saved_spool_dir = std::env::var("CDXHOOKS_SPOOL_DIR").ok();
        let dir = tempfile::tempdir().unwrap();
        let spool_dir = dir.path().join("spool");
        std::env::set_var("CDXHOOKS_SPOOL_DIR", &spool_dir);
        let session_spool = spool::session_root(&spool_dir, "session-1").unwrap();
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(&transcript_path, "{}\n").unwrap();
        let state_file = dir.path().join("state/cdxhooks_state.json");
        let context = test_context_with_state_file(state_file.clone());
        let pre = json!({
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "apply_patch",
            "tool_use_id": "call-1",
            "tool_input": {"patch": "patch"}
        });
        ToolContextHandler::new()
            .handle(&HookEvent::PreToolUse, &pre, &context)
            .unwrap();
        let post = json!({
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "apply_patch",
            "tool_use_id": "call-1",
            "transcript_path": transcript_path,
            "tool_input": {"patch": "patch"},
            "tool_response": {"ok": true},
            "success": true
        });

        for _ in 0..2 {
            ToolContextHandler::new()
                .handle_post_tool_use(&post, &context, false)
                .unwrap();
        }

        let state = crate::hooks::langfuse::state::load_state(&state_file);
        assert_eq!(
            realtime_state::pending_flush_jobs(&state, "session-1").len(),
            1
        );
        assert!(!session_spool.exists());
        assert_eq!(
            state["session-1"]["realtime_export"]["registered_jobs"]
                .as_object()
                .unwrap()
                .len(),
            1
        );
        assert!(state["session-1"]["realtime_export"]["registered_jobs"]
            .as_object()
            .unwrap()
            .values()
            .all(|job| job["kind"] == RealtimeSpoolKind::Flush.as_str()));
        match saved_spool_dir {
            Some(value) => std::env::set_var("CDXHOOKS_SPOOL_DIR", value),
            None => std::env::remove_var("CDXHOOKS_SPOOL_DIR"),
        }
    }

    #[test]
    fn post_tool_use_rejects_staged_canonicalization_when_flush_registration_fails() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state/cdxhooks_state.json");
        let context = test_context_with_state_file(state_file.clone());
        let transcript_path = dir.path().join("session.jsonl");
        std::fs::write(&transcript_path, "{}\n").unwrap();
        let mut state = json!({});
        let first = realtime_state::claim_tool_in_state(
            &mut state,
            "session-1",
            "call-1",
            "2026-08-17T00:00:00Z",
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
            "session-1",
            &first.context_id,
            Some("turn-1"),
        )
        .unwrap();
        realtime_state::rotate_current_in_state(&mut state, "session-1");
        let second = realtime_state::claim_tool_in_state(
            &mut state,
            "session-1",
            "call-2",
            "2026-08-17T00:00:01Z",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            "session-1",
            &second.context_id,
            Some("turn-1"),
        )
        .unwrap();
        tool_records::record_pre(
            &context,
            "session-1",
            "call-2",
            &deterministic_tool_span_id("call-2"),
            Some(&second.context_id),
            &second.trace_id,
            &second.root_span_id,
            Some("turn-1"),
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
            "1000",
        )
        .unwrap();

        let post = json!({
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_use_id": "call-2",
            "transcript_path": transcript_path,
            "tool_input": {"command": "true"},
            "tool_response": "{\"exit_code\":0}"
        });
        let mut preview = state.clone();
        realtime_state::canonicalize_turn_contexts_in_state(&mut preview, "session-1", "turn-1")
            .unwrap()
            .expect("preview canonicalization");
        let canonical_context = realtime_state::contexts(&preview, "session-1")
            .into_iter()
            .find(|candidate| candidate.tool_use_ids.iter().any(|id| id == "call-2"))
            .expect("canonical context contains call-2");
        let flush = prepare_realtime_flush(
            "session-1",
            &post,
            &context,
            &preview,
            &canonical_context,
            "call-2",
        )
        .expect("preview flush");
        realtime_state::register_realtime_job_in_state(
            &mut state,
            "session-1",
            "conflicting-context",
            RealtimeSpoolKind::Flush.as_str(),
            &flush.job_id,
            "different-digest",
        )
        .unwrap();
        crate::hooks::langfuse::state::save_state(&state_file, &state).unwrap();
        let before = crate::hooks::langfuse::state::load_state(&state_file);

        ToolContextHandler::new()
            .handle_post_tool_use(&post, &context, false)
            .unwrap();

        let after = crate::hooks::langfuse::state::load_state(&state_file);
        assert_eq!(
            after["session-1"]["realtime_export"], before["session-1"]["realtime_export"],
            "canonicalization aliases and timing must remain staged when later registration fails"
        );
        let contexts = realtime_state::contexts(&after, "session-1");
        assert_eq!(contexts.len(), 2);
        assert!(!realtime_state::context_alias_exists(
            &after,
            "session-1",
            &second.context_id
        ));
        assert!(tool_records::read(&context, "session-1", "call-2")
            .expect("PostToolUse still records independent tool timing")
            .state_reconciled_at
            .is_none());
    }

    #[test]
    fn ambiguous_subagent_and_orchestration_tools_defer_to_stop() {
        let _guard = crate::test_sync::env_lock();
        let saved_spool_dir = std::env::var("CDXHOOKS_SPOOL_DIR").ok();
        let dir = tempfile::tempdir().unwrap();
        std::env::set_var("CDXHOOKS_SPOOL_DIR", dir.path().join("spool"));
        let state_file = dir.path().join("state/cdxhooks_state.json");
        let context = test_context_with_state_file(state_file.clone());
        let agent_payload = json!({
            "session_id": "session-1",
            "agent_id": "agent-child",
            "tool_name": "Bash",
            "tool_use_id": "call-agent",
            "tool_input": {"command": "pwd"}
        });
        let orchestration_payload = json!({
            "session_id": "session-1",
            "tool_name": "spawn_agent",
            "tool_use_id": "call-spawn",
            "tool_input": {"message": "work"}
        });

        ToolContextHandler::new()
            .handle(&HookEvent::PreToolUse, &agent_payload, &context)
            .unwrap();
        ToolContextHandler::new()
            .handle(&HookEvent::PreToolUse, &orchestration_payload, &context)
            .unwrap();
        ToolContextHandler::new()
            .handle_post_tool_use(&agent_payload, &context, false)
            .unwrap();
        ToolContextHandler::new()
            .handle_post_tool_use(&orchestration_payload, &context, false)
            .unwrap();

        let state = crate::hooks::langfuse::state::load_state(&state_file);
        assert!(realtime_state::contexts(&state, "session-1").is_empty());
        assert_eq!(spool::status(&dir.path().join("spool")).unwrap().pending, 0);
        match saved_spool_dir {
            Some(value) => std::env::set_var("CDXHOOKS_SPOOL_DIR", value),
            None => std::env::remove_var("CDXHOOKS_SPOOL_DIR"),
        }
    }

    #[test]
    fn post_tool_use_fails_open_without_persisted_context() {
        let _guard = crate::test_sync::env_lock();
        let saved_spool_dir = std::env::var("CDXHOOKS_SPOOL_DIR").ok();
        let dir = tempfile::tempdir().unwrap();
        std::env::set_var("CDXHOOKS_SPOOL_DIR", dir.path().join("spool"));
        let context = test_context_with_state_file(dir.path().join("state/cdxhooks_state.json"));
        let payload = json!({
            "session_id": "session-1",
            "tool_name": "Bash",
            "tool_use_id": "call-1",
            "tool_response": "hi\n"
        });

        let result = ToolContextHandler::new().handle_post_tool_use(&payload, &context, false);

        assert!(result.is_ok());
        assert_eq!(spool::status(&dir.path().join("spool")).unwrap().pending, 0);
        match saved_spool_dir {
            Some(value) => std::env::set_var("CDXHOOKS_SPOOL_DIR", value),
            None => std::env::remove_var("CDXHOOKS_SPOOL_DIR"),
        }
    }

    #[test]
    fn duplicate_pretool_keeps_earliest_start_and_stable_span_id() {
        let dir = tempfile::tempdir().unwrap();
        let context = test_context_with_state_file(dir.path().join("state/state.json"));
        let first = tool_records::record_pre(
            &context,
            "session-1",
            "call-1",
            "1111111111111111",
            Some("context-1"),
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "bbbbbbbbbbbbbbbb",
            Some("turn-1"),
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
            "2000",
        )
        .unwrap();
        let duplicate = tool_records::record_pre(
            &context,
            "session-1",
            "call-1",
            "2222222222222222",
            Some("context-2"),
            "cccccccccccccccccccccccccccccccc",
            "dddddddddddddddd",
            Some("turn-2"),
            None,
            None,
            None,
            None,
            9,
            RetryPolicy::AtMostOnce,
            "1000",
        )
        .unwrap();

        assert_eq!(
            first.tool_span_id,
            deterministic_tool_span_id("call-1"),
            "record identity is derived from the tool call rather than caller timing"
        );
        assert_eq!(duplicate.tool_span_id, first.tool_span_id);
        assert_eq!(duplicate.started_at_unix_nano.as_deref(), Some("1000"));
        assert_eq!(duplicate.context_id.as_deref(), Some("context-1"));
        assert_eq!(duplicate.turn_id.as_deref(), Some("turn-1"));
        assert_eq!(duplicate.generation_batch_size, 3);
        assert_eq!(duplicate.retry_policy, "at_least_once");
    }

    #[test]
    fn session_owner_serial_tools_persist_distinct_records() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state/state.json");
        let context = test_context_with_state_file(state_file.clone());
        for index in 0..20_u32 {
            let tool_use_id = format!("call-{index}");
            tool_records::record_pre(
                &context,
                "session-1",
                &tool_use_id,
                &deterministic_tool_span_id(&tool_use_id),
                Some("context-1"),
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "bbbbbbbbbbbbbbbb",
                Some("turn-1"),
                None,
                None,
                None,
                None,
                3,
                RetryPolicy::AtLeastOnce,
                &(1000_u128 + u128::from(index)).to_string(),
            )
            .unwrap();
        }

        let tools = crate::common::runtime_state::DomainStateStore::for_compat_state_file(
            "session-1",
            crate::common::runtime_state::StateDomain::Tools,
            &state_file,
        )
        .unwrap()
        .load()
        .unwrap();
        assert_eq!(
            tools["records"]
                .as_object()
                .map_or(0, |records| records.len()),
            20
        );
        assert!(!state_file.parent().unwrap().join("tools").exists());
    }

    #[test]
    fn transcript_timing_is_fallback_and_invalid_timing_is_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let transcript = dir.path().join("session.jsonl");
        std::fs::write(
            &transcript,
            include_str!("../../tests/fixtures/codex_realtime_open_turn_exec_call.jsonl"),
        )
        .unwrap();
        let payload = json!({"transcript_path": transcript});
        let timing =
            transcript_tool_timing(&payload, "call_redacted").expect("valid transcript timing");
        assert_eq!(timing.duration_unix_nano, "1000000000");

        let invalid = dir.path().join("invalid.jsonl");
        std::fs::write(
            &invalid,
            concat!(
                "{\"timestamp\":\"2026-07-29T01:00:00Z\",\"type\":\"event_msg\",\"payload\":{\"type\":\"task_started\",\"turn_id\":\"turn\"}}\n",
                "{\"timestamp\":\"bad\",\"type\":\"response_item\",\"payload\":{\"type\":\"function_call\",\"name\":\"exec_command\",\"arguments\":\"{}\",\"call_id\":\"call-bad\"}}\n",
                "{\"timestamp\":\"also-bad\",\"type\":\"response_item\",\"payload\":{\"type\":\"function_call_output\",\"call_id\":\"call-bad\",\"output\":\"done\"}}\n"
            ),
        )
        .unwrap();
        assert!(transcript_tool_timing(&json!({"transcript_path": invalid}), "call-bad").is_none());
    }

    #[test]
    fn completed_for_turn_returns_only_completed_matching_records() {
        let dir = tempfile::tempdir().unwrap();
        let context = test_context_with_state_file(dir.path().join("state/state.json"));
        let matching = tool_records::record_pre(
            &context,
            "session-1",
            "exec-1",
            "1111111111111111",
            Some("context-1"),
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "bbbbbbbbbbbbbbbb",
            Some("turn-1"),
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
            "1000",
        )
        .unwrap();
        tool_records::record_post(&context, "session-1", &matching.tool_use_id, "1500").unwrap();
        let other_turn = tool_records::record_pre(
            &context,
            "session-1",
            "exec-2",
            "2222222222222222",
            Some("context-2"),
            "cccccccccccccccccccccccccccccccc",
            "dddddddddddddddd",
            Some("turn-2"),
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
            "2000",
        )
        .unwrap();
        tool_records::record_post(&context, "session-1", &other_turn.tool_use_id, "2500").unwrap();
        tool_records::record_pre(
            &context,
            "session-1",
            "exec-incomplete",
            "3333333333333333",
            Some("context-1"),
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "bbbbbbbbbbbbbbbb",
            Some("turn-1"),
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
            "3000",
        )
        .unwrap();

        let records = tool_records::completed_for_turn(&context.state_file, "session-1", "turn-1");
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].tool_use_id, matching.tool_use_id);
        assert_eq!(records[0].context_id, matching.context_id);
    }

    #[test]
    fn tool_record_reconcile_is_idempotent_and_metadata_only() {
        let dir = tempfile::tempdir().unwrap();
        let context = test_context_with_state_file(dir.path().join("state/state.json"));
        let (record, _) = {
            tool_records::record_pre(
                &context,
                "session-1",
                "call-1",
                "1111111111111111",
                Some("context-1"),
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "bbbbbbbbbbbbbbbb",
                Some("turn-1"),
                None,
                None,
                None,
                None,
                3,
                RetryPolicy::AtLeastOnce,
                "1000",
            )
            .unwrap();
            tool_records::record_post(&context, "session-1", "call-1", "3000").unwrap()
        };
        let serialized = serde_json::to_string(&record).unwrap();
        assert!(!serialized.contains("tool_input"));
        assert!(!serialized.contains("tool_output"));
        assert!(!serialized.contains("command"));
        assert!(!serialized.contains("result"));

        let mut state = json!({});
        reconcile_completed_tool_record_in_state(&mut state, "session-1", &record).unwrap();
        let once = state.clone();
        reconcile_completed_tool_record_in_state(&mut state, "session-1", &record).unwrap();
        assert_eq!(state, once);
    }

    #[test]
    fn tool_record_reconcile_resolves_canonical_context_alias() {
        let dir = tempfile::tempdir().unwrap();
        let context = test_context_with_state_file(dir.path().join("state/state.json"));
        let mut state = json!({});
        let winner = realtime_state::claim_tool_in_state(
            &mut state,
            "session-1",
            "winner-tool",
            "first",
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
            "session-1",
            &winner.context_id,
            Some("turn-1"),
        )
        .unwrap();
        realtime_state::rotate_current_in_state(&mut state, "session-1");
        let loser = realtime_state::claim_tool_in_state(
            &mut state,
            "session-1",
            "loser-tool",
            "second",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            "session-1",
            &loser.context_id,
            Some("turn-1"),
        )
        .unwrap();
        let canonical =
            realtime_state::canonicalize_turn_contexts_in_state(&mut state, "session-1", "turn-1")
                .unwrap()
                .unwrap();
        assert_eq!(canonical.context.context_id, winner.context_id);

        tool_records::record_pre(
            &context,
            "session-1",
            "late-tool",
            "3333333333333333",
            Some(&loser.context_id),
            &loser.trace_id,
            &loser.root_span_id,
            Some("turn-1"),
            None,
            None,
            None,
            None,
            3,
            RetryPolicy::AtLeastOnce,
            "1000",
        )
        .unwrap();
        let (record, _) =
            tool_records::record_post(&context, "session-1", "late-tool", "2000").unwrap();

        reconcile_completed_tool_record_in_state(&mut state, "session-1", &record).unwrap();

        let contexts = realtime_state::contexts(&state, "session-1");
        assert_eq!(contexts.len(), 1);
        assert_eq!(contexts[0].context_id, winner.context_id);
        assert!(contexts[0].tool_use_ids.iter().any(|id| id == "late-tool"));
        assert_eq!(
            realtime_state::resolve_context_alias(&state, "session-1", &loser.context_id).unwrap(),
            winner.context_id
        );
    }

    #[test]
    fn posttool_lock_contention_is_retryable_while_timing_remains_durable() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let state_dir = dir.path().join("state");
        let spool_dir = dir.path().join("spool");
        let _runtime =
            crate::common::dirs::override_runtime_dirs_for_test(state_dir, spool_dir.clone());
        let state_file = crate::common::dirs::session_state_file("session-1").unwrap();
        let context = test_context_with_state_file(state_file.clone());
        let handler = ToolContextHandler::new();
        let pre = json!({
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "Read",
            "tool_use_id": "call-1",
            "tool_input": {"path": "/tmp/input"}
        });
        crate::common::tool_event::publish_ingress_fact(&pre).unwrap();
        handler
            .handle(&HookEvent::PreToolUse, &pre, &context)
            .unwrap();
        std::thread::sleep(std::time::Duration::from_millis(2));
        let state_lock = crate::hooks::langfuse::state::StateLock::acquire(&state_file, 100);
        assert!(state_lock.is_acquired());
        let post = json!({
            "hook_event_name": "PostToolUse",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "Read",
            "tool_use_id": "call-1",
            "tool_input": {"path": "/tmp/input"},
            "tool_response": {"ok": true}
        });
        crate::common::tool_event::publish_ingress_fact(&post).unwrap();
        let ready = crate::common::tool_event::begin_ready("session-1", "call-1")
            .unwrap()
            .expect("published Pre/Post pair is recoverable");
        let error = handler
            .handle_post_tool_use(&post, &context, false)
            .unwrap_err();
        assert!(error.is_retryable());
        assert!(error
            .message
            .contains("state reconciliation retry required"));
        crate::common::tool_event::mark_retryable_error("session-1", &ready, &error.message)
            .unwrap();

        let record =
            tool_records::read(&context, "session-1", "call-1").expect("completed tool record");
        assert!(record.completed_timing().is_ok());
        assert_eq!(
            spool::status(&spool::session_root(&spool_dir, "session-1").unwrap())
                .unwrap()
                .pending,
            0,
            "the held session-state lock must not block the independent per-tool timing record"
        );
        drop(state_lock);
        assert!(
            crate::common::tool_event::begin_ready("session-1", "call-1")
                .unwrap()
                .is_some()
        );
    }

    fn test_context() -> CommonContext {
        test_context_with_state_file(std::path::PathBuf::from("/tmp/cdxhooks-test-state.json"))
    }

    fn test_context_with_state_file(state_file: std::path::PathBuf) -> CommonContext {
        CommonContext {
            logger: crate::common::logger::Logger::new(
                std::path::PathBuf::from("/tmp/cdxhooks-tool-context-test.log"),
                false,
            ),
            state_file,
            langfuse_config: None,
            username: "user-1".to_string(),
            sessions_dir: "/tmp".to_string(),
            hook_version: "test-version".to_string(),
            stop_budget: None,
            retry_policy: crate::hooks::RetryPolicy::AtLeastOnce,
            export_mode: crate::hooks::RealtimeExportMode::RealtimeWithStopFinalizer,
            realtime_llm_batch_size: realtime_state::DEFAULT_REALTIME_LLM_BATCH_SIZE,
            metrics: None,
        }
    }
}
