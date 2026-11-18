//! `PreCompact` / `PostCompact` handler。
//!
//! v0.3 起这两个 handler **仅做日志记录**：
//!
//! - 之前依赖 `hook_log_<sid>.ndjson` 做 Pre↔Post 配对再合成 `Compaction` 事件
//! - 现在 wire.jsonl 的 `CompactionBegin/End` 已被 `WireParser`（stop.rs）等价合成，
//!   实时 hook 已无独家信息可贡献，因此本 handler 退化为观测埋点。
//!
//! 保留它们的目的是用户可以在 `~/.kimi/config.toml` 注册这两个事件并通过日志确认 hook 链路工作正常。
//! 后续若发现 wire.jsonl 在 Compaction 边界确有数据缺漏，再在 state.json 中扩展 compaction 缓存。

use serde_json::Value;

use crate::common::hook::{
    extract_session_id, CommonContext, HandlerError, HookEvent, HookHandler,
};

pub struct PreCompactHandler;
pub struct PostCompactHandler;

impl PreCompactHandler {
    pub fn new() -> Self {
        Self
    }
}

impl Default for PreCompactHandler {
    fn default() -> Self {
        Self::new()
    }
}

impl HookHandler for PreCompactHandler {
    fn name(&self) -> &str {
        "pre_compact"
    }

    fn supported_events(&self) -> &[HookEvent] {
        const EVENTS: [HookEvent; 1] = [HookEvent::PreCompact];
        &EVENTS
    }

    fn handle(
        &self,
        _event: &HookEvent,
        payload: &Value,
        ctx: &CommonContext,
    ) -> Result<(), HandlerError> {
        let session_id = extract_session_id(payload);
        let trigger = payload
            .get("trigger")
            .and_then(|v| v.as_str())
            .unwrap_or("auto");
        let token_count = payload
            .get("token_count")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        ctx.logger.info(&format!(
            "PreCompact session={} trigger={} tokens={}",
            session_id, trigger, token_count
        ));
        Ok(())
    }
}

impl PostCompactHandler {
    pub fn new() -> Self {
        Self
    }
}

impl Default for PostCompactHandler {
    fn default() -> Self {
        Self::new()
    }
}

impl HookHandler for PostCompactHandler {
    fn name(&self) -> &str {
        "post_compact"
    }

    fn supported_events(&self) -> &[HookEvent] {
        const EVENTS: [HookEvent; 1] = [HookEvent::PostCompact];
        &EVENTS
    }

    fn handle(
        &self,
        _event: &HookEvent,
        payload: &Value,
        ctx: &CommonContext,
    ) -> Result<(), HandlerError> {
        let session_id = extract_session_id(payload);
        let trigger = payload
            .get("trigger")
            .and_then(|v| v.as_str())
            .unwrap_or("auto");
        let tokens_after = payload
            .get("estimated_token_count")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        ctx.logger.info(&format!(
            "PostCompact session={} trigger={} est_tokens_after={}",
            session_id, trigger, tokens_after
        ));
        Ok(())
    }
}
