//! `UserPromptSubmit` handler — 缓存原始用户输入到 `kimihooks_state.json`。
//!
//! 价值：wire.jsonl 的 `TurnBegin.user_input` 是 kimi-cli 内部预处理后的版本，
//! 而 hook payload 的 `prompt` 是用户提交瞬间的原始文本。Stop handler 在重建 turn
//! 时合并本字段，可对账 prompt 是否被裁剪 / 模板套接。
//!
//! Payload schema（来源 `kimi_cli/hooks/events.py:64-70`）：
//! ```json
//! {
//!   "hook_event_name": "UserPromptSubmit",
//!   "session_id": "<uuid>",
//!   "cwd": "<abs path>",
//!   "prompt": "<原始用户输入>"
//! }
//! ```

use serde_json::Value;

use agentic_otel_common::state::StateLock;

use crate::common::hook::{
    extract_cwd, extract_session_id, CommonContext, HandlerError, HookEvent, HookHandler,
};
use crate::common::state::{KimihooksState, UserPromptEntry, DEFAULT_TTL_NS};

pub struct UserPromptSubmitHandler;

impl UserPromptSubmitHandler {
    pub fn new() -> Self {
        Self
    }
}

impl Default for UserPromptSubmitHandler {
    fn default() -> Self {
        Self::new()
    }
}

impl HookHandler for UserPromptSubmitHandler {
    fn name(&self) -> &str {
        "user_prompt_submit"
    }

    fn supported_events(&self) -> &[HookEvent] {
        const EVENTS: [HookEvent; 1] = [HookEvent::UserPromptSubmit];
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
            ctx.logger
                .debug("UserPromptSubmit 缺少 session_id，跳过");
            return Ok(());
        }
        let cwd = extract_cwd(payload);
        let prompt = payload
            .get("prompt")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        ctx.logger.info(&format!(
            "UserPromptSubmit session={} prompt_len={}",
            session_id,
            prompt.len()
        ));

        let _lock = StateLock::acquire(&ctx.state_file, 2000);
        let now = agentic_otel_common::now_nanos();
        let mut state = KimihooksState::load_or_default(&ctx.state_file);
        state.upsert_session(&session_id, |sess| {
            sess.user_prompts.push(UserPromptEntry {
                ts_ns: now.clone(),
                prompt,
                cwd,
            });
            sess.last_seen_ns = now.clone();
        });
        state.prune_older_than(DEFAULT_TTL_NS);

        if let Err(e) = state.save(&ctx.state_file) {
            ctx.logger
                .warn(&format!("UserPromptSubmit state save 失败: {}", e));
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
    fn appends_prompt_to_state() {
        let path = unique_state_path("user-prompt-append");
        let _ = std::fs::remove_file(&path);
        let ctx = fake_ctx(&path);
        let payload = json!({
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-up-1",
            "cwd": "/work",
            "prompt": "hello world"
        });
        UserPromptSubmitHandler::new()
            .handle(&HookEvent::UserPromptSubmit, &payload, &ctx)
            .unwrap();

        let state = KimihooksState::load_or_default(&path);
        let sess = state.get_session("sess-up-1").expect("session must exist");
        assert_eq!(sess.user_prompts.len(), 1);
        assert_eq!(sess.user_prompts[0].prompt, "hello world");
        assert_eq!(sess.user_prompts[0].cwd, "/work");
        assert!(!sess.last_seen_ns.is_empty());
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn fail_silent_when_session_id_missing() {
        let path = unique_state_path("user-prompt-no-sid");
        let _ = std::fs::remove_file(&path);
        let ctx = fake_ctx(&path);
        let payload = json!({"hook_event_name": "UserPromptSubmit", "prompt": "no sid"});
        UserPromptSubmitHandler::new()
            .handle(&HookEvent::UserPromptSubmit, &payload, &ctx)
            .unwrap();
        // No state file should be written.
        assert!(!path.exists(), "state file must not be created without session_id");
    }
}
