//! `SubagentStart` / `SubagentStop` handler — 缓存子 agent 调用边界到 state.json。
//!
//! 价值：wire.jsonl 的 `SubagentEvent` 是嵌套结构（`event.type=TurnBegin/TurnEnd`），
//! 不直接给独立的 start/stop 时间戳和 `prompt` / `response` 字段。本 handler 把 hook
//! 视角的精确边界缓存下来，Stop handler 重建 turn 时与 wire 嵌套版本互相校验。
//!
//! Payload schema（来源 `kimi_cli/hooks/events.py:117-142`）：
//! ```json
//! { "hook_event_name": "SubagentStart", "session_id": "...", "cwd": "...",
//!   "agent_name": "<name>", "prompt": "<启动 prompt>" }
//! { "hook_event_name": "SubagentStop", "session_id": "...", "cwd": "...",
//!   "agent_name": "<name>", "response": "<最终响应>" }
//! ```

use serde_json::Value;

use agentic_otel_common::state::StateLock;

use crate::common::hook::{
    extract_cwd, extract_session_id, CommonContext, HandlerError, HookEvent, HookHandler,
};
use crate::common::state::{
    KimihooksState, SubagentStartEntry, SubagentStopEntry, DEFAULT_TTL_NS,
};

pub struct SubagentStartHandler;

impl SubagentStartHandler {
    pub fn new() -> Self {
        Self
    }
}

impl Default for SubagentStartHandler {
    fn default() -> Self {
        Self::new()
    }
}

impl HookHandler for SubagentStartHandler {
    fn name(&self) -> &str {
        "subagent_start"
    }

    fn supported_events(&self) -> &[HookEvent] {
        const EVENTS: [HookEvent; 1] = [HookEvent::SubagentStart];
        &EVENTS
    }

    fn handle(
        &self,
        _event: &HookEvent,
        payload: &Value,
        ctx: &CommonContext,
    ) -> Result<(), HandlerError> {
        let session_id = extract_session_id(payload);
        if session_id.is_empty() {
            ctx.logger.debug("SubagentStart 缺少 session_id，跳过");
            return Ok(());
        }
        let agent_name = payload
            .get("agent_name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let prompt = payload
            .get("prompt")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let cwd = extract_cwd(payload);
        ctx.logger.info(&format!(
            "SubagentStart session={} agent={} prompt_len={}",
            session_id,
            agent_name,
            prompt.len()
        ));

        let _lock = StateLock::acquire(&ctx.state_file, 2000);
        let now = agentic_otel_common::now_nanos();
        let mut state = KimihooksState::load_or_default(&ctx.state_file);
        state.upsert_session(&session_id, |sess| {
            sess.subagent_starts.push(SubagentStartEntry {
                ts_ns: now.clone(),
                agent_name,
                prompt,
                cwd,
            });
            sess.last_seen_ns = now.clone();
        });
        state.prune_older_than(DEFAULT_TTL_NS);

        if let Err(e) = state.save(&ctx.state_file) {
            ctx.logger
                .warn(&format!("SubagentStart state save 失败: {}", e));
        }
        Ok(())
    }
}

pub struct SubagentStopHandler;

impl SubagentStopHandler {
    pub fn new() -> Self {
        Self
    }
}

impl Default for SubagentStopHandler {
    fn default() -> Self {
        Self::new()
    }
}

impl HookHandler for SubagentStopHandler {
    fn name(&self) -> &str {
        "subagent_stop"
    }

    fn supported_events(&self) -> &[HookEvent] {
        const EVENTS: [HookEvent; 1] = [HookEvent::SubagentStop];
        &EVENTS
    }

    fn handle(
        &self,
        _event: &HookEvent,
        payload: &Value,
        ctx: &CommonContext,
    ) -> Result<(), HandlerError> {
        let session_id = extract_session_id(payload);
        if session_id.is_empty() {
            ctx.logger.debug("SubagentStop 缺少 session_id，跳过");
            return Ok(());
        }
        let agent_name = payload
            .get("agent_name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let response = payload
            .get("response")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let cwd = extract_cwd(payload);
        ctx.logger.info(&format!(
            "SubagentStop session={} agent={} response_len={}",
            session_id,
            agent_name,
            response.len()
        ));

        let _lock = StateLock::acquire(&ctx.state_file, 2000);
        let now = agentic_otel_common::now_nanos();
        let mut state = KimihooksState::load_or_default(&ctx.state_file);
        state.upsert_session(&session_id, |sess| {
            sess.subagent_stops.push(SubagentStopEntry {
                ts_ns: now.clone(),
                agent_name,
                response,
                cwd,
            });
            sess.last_seen_ns = now.clone();
        });
        state.prune_older_than(DEFAULT_TTL_NS);

        if let Err(e) = state.save(&ctx.state_file) {
            ctx.logger
                .warn(&format!("SubagentStop state save 失败: {}", e));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::logger::Logger;
    use serde_json::json;

    fn unique_state_path(label: &str) -> std::path::PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "kimihooks-{}-{}-{}.json",
            label,
            std::process::id(),
            nanos
        ))
    }

    fn fake_ctx(state_file: &std::path::Path) -> CommonContext {
        let log_path = state_file.with_extension("log");
        CommonContext::new(
            Logger::new(log_path, false),
            false,
            "test".to_string(),
            state_file.to_path_buf(),
        )
    }

    #[test]
    fn start_appends_entry_to_state() {
        let path = unique_state_path("subagent-start-append");
        let _ = std::fs::remove_file(&path);
        let ctx = fake_ctx(&path);
        let payload = json!({
            "hook_event_name": "SubagentStart",
            "session_id": "sess-sa-1",
            "cwd": "/work",
            "agent_name": "coder",
            "prompt": "do work"
        });
        SubagentStartHandler::new()
            .handle(&HookEvent::SubagentStart, &payload, &ctx)
            .unwrap();

        let state = KimihooksState::load_or_default(&path);
        let sess = state.get_session("sess-sa-1").expect("session must exist");
        assert_eq!(sess.subagent_starts.len(), 1);
        assert_eq!(sess.subagent_starts[0].agent_name, "coder");
        assert_eq!(sess.subagent_starts[0].prompt, "do work");
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn stop_appends_entry_to_state() {
        let path = unique_state_path("subagent-stop-append");
        let _ = std::fs::remove_file(&path);
        let ctx = fake_ctx(&path);
        let payload = json!({
            "hook_event_name": "SubagentStop",
            "session_id": "sess-sa-2",
            "cwd": "/work",
            "agent_name": "coder",
            "response": "all done"
        });
        SubagentStopHandler::new()
            .handle(&HookEvent::SubagentStop, &payload, &ctx)
            .unwrap();

        let state = KimihooksState::load_or_default(&path);
        let sess = state.get_session("sess-sa-2").expect("session must exist");
        assert_eq!(sess.subagent_stops.len(), 1);
        assert_eq!(sess.subagent_stops[0].agent_name, "coder");
        assert_eq!(sess.subagent_stops[0].response, "all done");
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn fail_silent_without_session_id() {
        let path = unique_state_path("subagent-no-sid");
        let _ = std::fs::remove_file(&path);
        let ctx = fake_ctx(&path);

        SubagentStartHandler::new()
            .handle(
                &HookEvent::SubagentStart,
                &json!({"agent_name": "x"}),
                &ctx,
            )
            .unwrap();
        SubagentStopHandler::new()
            .handle(
                &HookEvent::SubagentStop,
                &json!({"agent_name": "x"}),
                &ctx,
            )
            .unwrap();
        assert!(!path.exists());
    }

    #[test]
    fn start_and_stop_share_session_state() {
        let path = unique_state_path("subagent-shared");
        let _ = std::fs::remove_file(&path);
        let ctx = fake_ctx(&path);
        let sid = "sess-sa-shared";

        SubagentStartHandler::new()
            .handle(
                &HookEvent::SubagentStart,
                &json!({
                    "session_id": sid, "cwd": "/w",
                    "agent_name": "a", "prompt": "p"
                }),
                &ctx,
            )
            .unwrap();
        SubagentStopHandler::new()
            .handle(
                &HookEvent::SubagentStop,
                &json!({
                    "session_id": sid, "cwd": "/w",
                    "agent_name": "a", "response": "r"
                }),
                &ctx,
            )
            .unwrap();

        let state = KimihooksState::load_or_default(&path);
        let sess = state.get_session(sid).unwrap();
        assert_eq!(sess.subagent_starts.len(), 1);
        assert_eq!(sess.subagent_stops.len(), 1);
        let _ = std::fs::remove_file(&path);
    }
}
