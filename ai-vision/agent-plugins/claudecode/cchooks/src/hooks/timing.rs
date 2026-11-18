use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use agentic_otel_common::now_nanos;
use serde_json::{json, Value};

use crate::common::hook::{extract_session_id, extract_transcript_path, HookEvent};
use crate::hooks::langfuse::state::{SessionStateStore, StateTxnError};
use crate::hooks::{CommonContext, HandlerError, HookHandler};

/// A single timing entry for a tool execution.
#[derive(Debug, Clone)]
pub struct ToolTiming {
    pub start_ns: String,
    pub end_ns: Option<String>,
    pub success: Option<bool>,
    #[allow(dead_code)]
    pub tool_name: Option<String>,
}

/// Map of tool_use_id → ToolTiming.
pub type TimingData = HashMap<String, ToolTiming>;

/// Map of subagent agent_id → tool timing data.
pub type SubagentTimingData = HashMap<String, TimingData>;

/// Handler that records precise timestamps for tool execution.
///
/// On PreToolUse: records tool start time.
/// On PostToolUse/PostToolUseFailure: records tool end time.
/// On StopFailure: records a marker for error handling.
///
/// Parent tool timing is written to `state[session_id]["timing"]`.
/// Subagent-internal tool timing is written to
/// `state[session_id]["subagent_timing"][agent_id]["timing"]` so it survives
/// parent Stop cleanup and can be consumed by either parent Stop expansion or
/// a later SubagentStop async upload.
pub struct TimingHandler;

impl Default for TimingHandler {
    fn default() -> Self {
        Self::new()
    }
}

impl TimingHandler {
    pub fn new() -> Self {
        Self
    }
}

impl HookHandler for TimingHandler {
    fn name(&self) -> &str {
        "timing"
    }

    fn supported_events(&self) -> &[HookEvent] {
        &[
            HookEvent::PreToolUse,
            HookEvent::PostToolUse,
            HookEvent::PostToolUseFailure,
            HookEvent::StopFailure,
        ]
    }

    fn handle(
        &self,
        event: &HookEvent,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        let session_id = extract_session_id(payload);

        if session_id.is_empty() {
            context.logger.debug("timing: no session_id, skipping");
            return Ok(());
        }

        let ts = now_nanos();

        let transcript_path = timing_transcript_path(payload);
        // Claude Code injects `agent_id` into PreToolUse/PostToolUse payloads when
        // the tool is invoked inside a subagent. `session_id` and `transcript_path`
        // remain the parent's, so payload.agent_id is the authoritative signal.
        // Fall back to transcript-path parsing for SubagentStop/SubagentStart paths.
        let subagent_id = payload
            .get("agent_id")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
            .or_else(|| subagent_id_from_transcript_path(&transcript_path));

        let store = SessionStateStore::new(context.state_file.clone(), &session_id, 500);
        let update_result = store.update_session(|session| {
            ensure_timing_container(session, subagent_id.as_deref(), &transcript_path);

            match event {
                HookEvent::PreToolUse => {
                    let tool_use_id = payload
                        .get("tool_use_id")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    let tool_name = payload
                        .get("tool_name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    if tool_use_id.is_empty() {
                        return Ok(false);
                    }
                    let timing_map = timing_map_mut(session, subagent_id.as_deref());
                    if timing_map.get(tool_use_id).is_none() {
                        timing_map[tool_use_id] = json!({
                            "start_ns": ts,
                            "name": tool_name,
                        });
                    }
                }
                HookEvent::PostToolUse | HookEvent::PostToolUseFailure => {
                    let tool_use_id = payload
                        .get("tool_use_id")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    if tool_use_id.is_empty() {
                        return Ok(false);
                    }
                    let ok = matches!(event, HookEvent::PostToolUse);
                    let timing_map = timing_map_mut(session, subagent_id.as_deref());
                    if timing_map[tool_use_id].get("end_ns").is_some() {
                        return Ok(true);
                    }
                    if timing_map.get(tool_use_id).is_some() {
                        timing_map[tool_use_id]["end_ns"] = Value::String(ts.clone());
                        timing_map[tool_use_id]["ok"] = json!(ok);
                    } else {
                        timing_map[tool_use_id] = json!({
                            "end_ns": ts,
                            "ok": ok,
                        });
                    }
                }
                HookEvent::StopFailure => {
                    if let Some(agent_id) = subagent_id.as_deref() {
                        session["subagent_timing"][agent_id]["stop_failure_ts"] = Value::String(ts);
                    } else {
                        session["stop_failure_ts"] = Value::String(ts);
                    }
                }
                _ => return Ok(false),
            }
            Ok(true)
        });

        match update_result {
            Ok(true) => {}
            Ok(false) => {
                context
                    .logger
                    .debug(&format!("timing: {:?} missing required id", event));
                return Ok(());
            }
            Err(StateTxnError::Busy { .. }) => {
                context
                    .logger
                    .warn("timing: state lock unavailable; skipping state update");
                return Ok(());
            }
            Err(error) => {
                return Err(HandlerError {
                    message: format!("timing: failed to update state: {error}"),
                });
            }
        }

        context
            .logger
            .debug(&format!("timing: wrote {:?} event to state", event));
        Ok(())
    }
}

fn timing_transcript_path(payload: &Value) -> String {
    let direct = extract_transcript_path(payload);
    if !direct.is_empty() {
        return agentic_otel_common::hook::expand_home_path(&direct);
    }
    payload
        .get("agent_transcript_path")
        .and_then(|v| v.as_str())
        .map(agentic_otel_common::hook::expand_home_path)
        .unwrap_or_default()
}

fn subagent_id_from_transcript_path(transcript_path: &str) -> Option<String> {
    if transcript_path.is_empty() || !transcript_path.contains("/subagents/") {
        return None;
    }
    let file_name = Path::new(transcript_path).file_name()?.to_str()?;
    let agent_id = file_name.strip_prefix("agent-")?.strip_suffix(".jsonl")?;
    if agent_id.is_empty() {
        None
    } else {
        Some(agent_id.to_string())
    }
}

fn ensure_timing_container(session: &mut Value, subagent_id: Option<&str>, transcript_path: &str) {
    if let Some(agent_id) = subagent_id {
        if session.get("subagent_timing").is_none() {
            session["subagent_timing"] = json!({});
        }
        if session["subagent_timing"].get(agent_id).is_none() {
            session["subagent_timing"][agent_id] = json!({});
        }
        if !transcript_path.is_empty() {
            session["subagent_timing"][agent_id]["transcript_path"] =
                Value::String(transcript_path.to_string());
        }
        if session["subagent_timing"][agent_id].get("timing").is_none() {
            session["subagent_timing"][agent_id]["timing"] = json!({});
        }
    } else if session.get("timing").is_none() {
        session["timing"] = json!({});
    }
}

fn timing_map_mut<'a>(session: &'a mut Value, subagent_id: Option<&str>) -> &'a mut Value {
    if let Some(agent_id) = subagent_id {
        &mut session["subagent_timing"][agent_id]["timing"]
    } else {
        &mut session["timing"]
    }
}

/// Load timing data directly from an already-loaded state object.
/// Zero I/O — caller provides the state.
/// Backfills `stop_failure_ts` as `end_ns` for tools missing it.
pub fn load_timing_from_state(state: &Value, session_id: &str) -> TimingData {
    let session = match state.get(session_id) {
        Some(s) => s,
        None => return HashMap::new(),
    };

    let timing_obj = match session.get("timing").and_then(|t| t.as_object()) {
        Some(obj) => obj,
        None => return HashMap::new(),
    };

    let mut timing = parse_timing_object(timing_obj);

    // Backfill stop_failure_ts for tools with start but no end
    if let Some(stop_ts) = session.get("stop_failure_ts").and_then(|v| v.as_str()) {
        backfill_stop_failure(&mut timing, stop_ts);
    }

    timing
}

/// Load every subagent timing bucket for a parent session.
pub fn load_subagent_timing_from_state(state: &Value, session_id: &str) -> SubagentTimingData {
    let mut result = SubagentTimingData::new();
    let Some(subagents) = state
        .get(session_id)
        .and_then(|s| s.get("subagent_timing"))
        .and_then(|v| v.as_object())
    else {
        return result;
    };

    for (agent_id, entry) in subagents {
        let timing = parse_timing_node(entry);
        if !timing.is_empty() {
            result.insert(agent_id.clone(), timing);
        }
    }

    result
}

/// Load one subagent timing bucket from an already-selected session object.
pub fn load_subagent_timing_for_agent_from_session(session: &Value, agent_id: &str) -> TimingData {
    session
        .get("subagent_timing")
        .and_then(|s| s.get(agent_id))
        .map(parse_timing_node)
        .unwrap_or_default()
}

fn parse_timing_node(node: &Value) -> TimingData {
    let Some(obj) = node.get("timing").and_then(|t| t.as_object()) else {
        return TimingData::new();
    };

    let mut timing = parse_timing_object(obj);
    if let Some(stop_ts) = node.get("stop_failure_ts").and_then(|v| v.as_str()) {
        backfill_stop_failure(&mut timing, stop_ts);
    }
    timing
}

/// Parse a JSON object `{ "tool_use_id": { "start_ns": ..., ... }, ... }` into TimingData.
fn parse_timing_object(obj: &serde_json::Map<String, Value>) -> TimingData {
    let mut timing = TimingData::new();
    for (id, entry) in obj {
        let start_ns = entry
            .get("start_ns")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let end_ns = entry
            .get("end_ns")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        let success = entry.get("ok").and_then(|v| v.as_bool());
        let tool_name = entry
            .get("name")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        timing.insert(
            id.clone(),
            ToolTiming {
                start_ns,
                end_ns,
                success,
                tool_name,
            },
        );
    }
    timing
}

fn backfill_stop_failure(timing: &mut TimingData, stop_ts: &str) {
    if stop_ts.is_empty() {
        return;
    }
    for entry in timing.values_mut() {
        if !entry.start_ns.is_empty() && entry.end_ns.is_none() {
            entry.end_ns = Some(stop_ts.to_string());
            entry.success = Some(false);
        }
    }
}

fn backfill_timing_value_with_stop_failure(timing_val: &mut Value, stop_ts: &str) {
    if stop_ts.is_empty() {
        return;
    }
    if let Some(obj) = timing_val.as_object_mut() {
        for (_id, entry) in obj.iter_mut() {
            let has_start = entry
                .get("start_ns")
                .and_then(|v| v.as_str())
                .is_some_and(|s| !s.is_empty());
            let has_end = entry.get("end_ns").is_some();
            if has_start && !has_end {
                entry["end_ns"] = Value::String(stop_ts.to_string());
                entry["ok"] = json!(false);
            }
        }
    }
}

/// Clean up timing data from state, optionally exporting to a companion file.
///
/// When mutating live state, call this inside the owning `SessionStateStore`
/// transaction. A detached clone may be passed outside the lock for best-effort
/// archive emission without affecting persisted state.
pub fn cleanup_timing(state: &mut Value, session_id: &str, archive_path: Option<&Path>) {
    if let Some(dest) = archive_path {
        if let Some(mut timing_val) = state.get(session_id).and_then(|s| s.get("timing")).cloned() {
            // Backfill stop_failure_ts into entries missing end_ns so companion file is
            // consistent with the live path (load_timing_from_state does the same backfill).
            if let Some(stop_ts) = state
                .get(session_id)
                .and_then(|s| s.get("stop_failure_ts"))
                .and_then(|v| v.as_str())
            {
                if !stop_ts.is_empty() {
                    backfill_timing_value_with_stop_failure(&mut timing_val, stop_ts);
                }
            }
            if let Some(parent) = dest.parent() {
                let _ = fs::create_dir_all(parent);
            }
            let _ = fs::write(
                dest,
                serde_json::to_string_pretty(&timing_val).unwrap_or_default(),
            );
        }
    }

    if let Some(session) = state.get_mut(session_id).and_then(|s| s.as_object_mut()) {
        session.remove("timing");
        session.remove("stop_failure_ts");
        #[cfg(all(feature = "tool_context", not(windows)))]
        if crate::common::otel::tool_context_enabled() {
            session.remove("current_trace_context");
            session.remove("trace_context_history");
        }
    }
}

/// Remove one subagent timing bucket from an already-selected session object.
pub fn cleanup_subagent_timing_from_session(
    session: &mut Value,
    agent_id: &str,
    archive_path: Option<&Path>,
) {
    if agent_id.is_empty() {
        return;
    }

    if let Some(dest) = archive_path {
        if let Some(entry) = session
            .get("subagent_timing")
            .and_then(|s| s.get(agent_id))
            .cloned()
        {
            if let Some(mut timing_val) = entry.get("timing").cloned() {
                if let Some(stop_ts) = entry.get("stop_failure_ts").and_then(|v| v.as_str()) {
                    backfill_timing_value_with_stop_failure(&mut timing_val, stop_ts);
                }
                if let Some(parent) = dest.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                let _ = fs::write(
                    dest,
                    serde_json::to_string_pretty(&timing_val).unwrap_or_default(),
                );
            }
        }
    }

    if let Some(subagents) = session
        .get_mut("subagent_timing")
        .and_then(|s| s.as_object_mut())
    {
        subagents.remove(agent_id);
        if subagents.is_empty() {
            if let Some(session_obj) = session.as_object_mut() {
                session_obj.remove("subagent_timing");
            }
        }
    }
}

/// Load timing data from a companion file path (for replay/report).
/// Supports both JSON object format (new) and JSONL format (legacy).
pub fn load_timing_data_from_path(path: &Path) -> TimingData {
    let content = match fs::read_to_string(path) {
        Ok(c) => c,
        Err(_) => return HashMap::new(),
    };

    // Try JSON object format first (new format)
    if let Ok(obj) = serde_json::from_str::<Value>(&content) {
        if let Some(map) = obj.as_object() {
            return parse_timing_object(map);
        }
    }

    // Fallback: legacy JSONL format
    parse_timing_jsonl(&content)
}

/// Parse legacy JSONL timing format (pre/post event lines).
fn parse_timing_jsonl(content: &str) -> TimingData {
    let mut timing = TimingData::new();

    for line in content.lines() {
        if line.is_empty() {
            continue;
        }
        let obj: Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => continue,
        };

        let ev = obj.get("ev").and_then(|v| v.as_str()).unwrap_or("");

        match ev {
            "pre" => {
                let id = obj.get("id").and_then(|v| v.as_str()).unwrap_or("");
                let ts = obj.get("ts").and_then(|v| v.as_str()).unwrap_or("");
                let name = obj.get("name").and_then(|v| v.as_str()).unwrap_or("");
                if !id.is_empty() && !ts.is_empty() {
                    timing.insert(
                        id.to_string(),
                        ToolTiming {
                            start_ns: ts.to_string(),
                            end_ns: None,
                            success: None,
                            tool_name: if name.is_empty() {
                                None
                            } else {
                                Some(name.to_string())
                            },
                        },
                    );
                }
            }
            "post" => {
                let id = obj.get("id").and_then(|v| v.as_str()).unwrap_or("");
                let ts = obj.get("ts").and_then(|v| v.as_str()).unwrap_or("");
                let ok = obj.get("ok").and_then(|v| v.as_bool());
                if !id.is_empty() && !ts.is_empty() {
                    if let Some(entry) = timing.get_mut(id) {
                        entry.end_ns = Some(ts.to_string());
                        entry.success = ok;
                    } else {
                        timing.insert(
                            id.to_string(),
                            ToolTiming {
                                start_ns: String::new(),
                                end_ns: Some(ts.to_string()),
                                success: ok,
                                tool_name: None,
                            },
                        );
                    }
                }
            }
            _ => {}
        }
    }

    timing
}

/// Compute the companion timing file path for a transcript.
/// Given `foo.jsonl`, returns `foo.timing.jsonl`.
pub fn companion_timing_path(transcript_path: &str) -> PathBuf {
    let p = Path::new(transcript_path);
    let stem = p.file_stem().and_then(|s| s.to_str()).unwrap_or("unknown");
    p.with_file_name(format!("{}.timing.jsonl", stem))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::logger::Logger;
    use crate::hooks::{ExportMode, RetryPolicy};
    use agentic_otel_common::state::load_state;
    use serde_json::json;

    fn test_context(dir: &tempfile::TempDir) -> CommonContext {
        CommonContext {
            logger: Logger::new(dir.path().join("timing.log"), true),
            state_file: dir.path().join("state.json"),
            langfuse_config: None,
            username: "user".to_string(),
            projects_dir: String::new(),
            debug_enabled: false,
            hook_version: "test".to_string(),
            turn_seed: None,
            stop_transcript_end_offset: None,
            transcript_ingest_receipt: None,
            stop_budget: None,
            retry_policy: RetryPolicy::AtLeastOnce,
            export_mode: ExportMode::StopOnly,
            realtime_llm_batch_size: crate::hooks::DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
            metrics: None,
            send_queue: std::cell::RefCell::new(Vec::new()),
            send_errors: std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
            stop_outcome: std::cell::RefCell::new(crate::hooks::StopOutcome::IntentionallySkipped),
        }
    }

    #[test]
    fn duplicate_pretool_preserves_the_original_start() {
        let dir = tempfile::tempdir().unwrap();
        let context = test_context(&dir);
        let payload = json!({
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "tool_name": "Read"
        });
        let handler = TimingHandler::new();

        handler
            .handle(&HookEvent::PreToolUse, &payload, &context)
            .unwrap();
        let first = load_state(&context.state_file);
        std::thread::sleep(std::time::Duration::from_millis(1));
        handler
            .handle(&HookEvent::PreToolUse, &payload, &context)
            .unwrap();
        let second = load_state(&context.state_file);

        assert_eq!(
            first["session-1"]["timing"]["tool-1"],
            second["session-1"]["timing"]["tool-1"]
        );
    }

    #[test]
    fn duplicate_posttool_preserves_the_first_terminal_result() {
        let dir = tempfile::tempdir().unwrap();
        let context = test_context(&dir);
        let payload = json!({
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "tool_name": "Read"
        });
        let handler = TimingHandler::new();

        handler
            .handle(&HookEvent::PreToolUse, &payload, &context)
            .unwrap();
        handler
            .handle(&HookEvent::PostToolUse, &payload, &context)
            .unwrap();
        let first = load_state(&context.state_file);
        std::thread::sleep(std::time::Duration::from_millis(1));
        handler
            .handle(&HookEvent::PostToolUseFailure, &payload, &context)
            .unwrap();
        let second = load_state(&context.state_file);

        assert_eq!(
            first["session-1"]["timing"]["tool-1"],
            second["session-1"]["timing"]["tool-1"]
        );
        assert_eq!(second["session-1"]["timing"]["tool-1"]["ok"], true);
    }

    #[test]
    fn test_subagent_id_from_transcript_path() {
        let path = "/tmp/projects/session/subagents/agent-abc123.jsonl";
        assert_eq!(
            subagent_id_from_transcript_path(path),
            Some("abc123".to_string())
        );
        assert_eq!(subagent_id_from_transcript_path("/tmp/session.jsonl"), None);
    }

    #[test]
    fn test_load_subagent_timing_backfills_stop_failure() {
        let state = json!({
            "sess-1": {
                "subagent_timing": {
                    "agent-a": {
                        "timing": {
                            "tool-child": {
                                "start_ns": "100",
                                "name": "Read"
                            }
                        },
                        "stop_failure_ts": "250"
                    }
                }
            }
        });

        let timing = load_subagent_timing_for_agent_from_session(&state["sess-1"], "agent-a");
        let entry = timing.get("tool-child").unwrap();
        assert_eq!(entry.start_ns, "100");
        assert_eq!(entry.end_ns.as_deref(), Some("250"));
        assert_eq!(entry.success, Some(false));
    }

    #[test]
    fn test_cleanup_parent_timing_preserves_subagent_timing() {
        let mut state = json!({
            "sess-1": {
                "timing": {
                    "parent-tool": { "start_ns": "1", "end_ns": "2", "ok": true }
                },
                "subagent_timing": {
                    "agent-a": {
                        "timing": {
                            "child-tool": { "start_ns": "3", "end_ns": "4", "ok": true }
                        }
                    }
                }
            }
        });

        cleanup_timing(&mut state, "sess-1", None);

        assert!(state["sess-1"].get("timing").is_none());
        assert!(state["sess-1"]["subagent_timing"].get("agent-a").is_some());
    }

    #[test]
    fn test_cleanup_subagent_timing_removes_only_one_agent() {
        let dir = tempfile::tempdir().unwrap();
        let archive = dir.path().join("agent-a.timing.jsonl");
        let mut state = json!({
            "sess-1": {
                "subagent_timing": {
                    "agent-a": {
                        "timing": {
                            "tool-a": { "start_ns": "10", "end_ns": "20", "ok": true }
                        }
                    },
                    "agent-b": {
                        "timing": {
                            "tool-b": { "start_ns": "30", "end_ns": "40", "ok": true }
                        }
                    }
                }
            }
        });

        cleanup_subagent_timing_from_session(&mut state["sess-1"], "agent-a", Some(&archive));

        assert!(state["sess-1"]["subagent_timing"].get("agent-a").is_none());
        assert!(state["sess-1"]["subagent_timing"].get("agent-b").is_some());
        let archived = fs::read_to_string(archive).unwrap();
        assert!(archived.contains("tool-a"));
    }
}
