//! 与 hook 事件相关的通用类型与基础设施。
//!
//! - [`HookEvent`]：kimi-cli 13 个 hook 事件枚举（来源：
//!   `src/kimi_cli/hooks/config.py:5-19` `HookEventType`）。
//! - [`CommonContext`] / [`HookHandler`] / [`HookHandlerRegistry`]：与 cchooks 同构的
//!   错误隔离调度框架。
//! - [`read_hook_payload`]：从 stdin 读取一行 JSON。

use std::cell::RefCell;
use std::fmt;
use std::path::PathBuf;

use serde_json::Value;

use crate::common::logger::Logger;

/// kimi-cli 全部 13 个 hook 事件。
///
/// 字段值与 stdin payload 中 `hook_event_name` 字段一一对应。
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum HookEvent {
    PreToolUse,
    PostToolUse,
    PostToolUseFailure,
    UserPromptSubmit,
    Stop,
    StopFailure,
    Interrupt,
    SessionStart,
    SessionEnd,
    SubagentStart,
    SubagentStop,
    PreCompact,
    PostCompact,
    Notification,
}

impl HookEvent {
    pub fn parse(name: &str) -> Option<Self> {
        Some(match name {
            "PreToolUse" => Self::PreToolUse,
            "PostToolUse" => Self::PostToolUse,
            "PostToolUseFailure" => Self::PostToolUseFailure,
            "UserPromptSubmit" => Self::UserPromptSubmit,
            "Stop" => Self::Stop,
            "StopFailure" => Self::StopFailure,
            "Interrupt" => Self::Interrupt,
            "SessionStart" => Self::SessionStart,
            "SessionEnd" => Self::SessionEnd,
            "SubagentStart" => Self::SubagentStart,
            "SubagentStop" => Self::SubagentStop,
            "PreCompact" => Self::PreCompact,
            "PostCompact" => Self::PostCompact,
            "Notification" => Self::Notification,
            _ => return None,
        })
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            Self::PreToolUse => "PreToolUse",
            Self::PostToolUse => "PostToolUse",
            Self::PostToolUseFailure => "PostToolUseFailure",
            Self::UserPromptSubmit => "UserPromptSubmit",
            Self::Stop => "Stop",
            Self::StopFailure => "StopFailure",
            Self::Interrupt => "Interrupt",
            Self::SessionStart => "SessionStart",
            Self::SessionEnd => "SessionEnd",
            Self::SubagentStart => "SubagentStart",
            Self::SubagentStop => "SubagentStop",
            Self::PreCompact => "PreCompact",
            Self::PostCompact => "PostCompact",
            Self::Notification => "Notification",
        }
    }
}

/// 从 stdin 读取一行 JSON。直接复用 `agentic-otel-common` 中带 1s 超时 + TTY 检测的实现，
/// 避免在 hook 进程被误调用（无管道输入）时无限阻塞。
pub fn read_hook_payload() -> Option<Value> {
    agentic_otel_common::hook::read_hook_payload()
}

/// 提取 `session_id` 字段（kimi-cli 在所有事件中都会提供，见 `_base()`）。
pub fn extract_session_id(payload: &Value) -> String {
    payload
        .get("session_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

/// 提取 `cwd` 字段（kimi-cli 公共字段）。
pub fn extract_cwd(payload: &Value) -> String {
    payload
        .get("cwd")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

/// Handler 错误。`message` 用作 metric 维度，建议使用稳定枚举值。
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

impl From<&str> for HandlerError {
    fn from(message: &str) -> Self {
        Self {
            message: message.to_string(),
        }
    }
}

/// 跨 handler 共享的运行上下文。
pub struct CommonContext {
    pub logger: Logger,
    pub debug_enabled: bool,
    pub hook_version: String,
    /// `kimihooks_state.json` 的绝对路径。v0.3 起所有 handler 通过此字段读写 state，
    /// 不再各自调用 `dirs::state_dir()`，便于测试注入。
    pub state_file: PathBuf,
    /// 上下文对象需要可被 handler 借用 + 局部修改时使用 RefCell 包装。Sprint 1 暂时只用日志。
    #[allow(dead_code)]
    pub scratch: RefCell<()>,
}

impl CommonContext {
    pub fn new(
        logger: Logger,
        debug_enabled: bool,
        hook_version: String,
        state_file: PathBuf,
    ) -> Self {
        Self {
            logger,
            debug_enabled,
            hook_version,
            state_file,
            scratch: RefCell::new(()),
        }
    }
}

/// 所有 handler 实现的统一接口。与 cchooks 一致，单独失败的 handler 不影响其他 handler。
pub trait HookHandler {
    fn name(&self) -> &str;
    fn supported_events(&self) -> &[HookEvent];
    fn handle(
        &self,
        event: &HookEvent,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError>;
}

/// 顺序调度，错误隔离。
pub struct HookHandlerRegistry {
    handlers: Vec<Box<dyn HookHandler>>,
}

impl HookHandlerRegistry {
    pub fn new() -> Self {
        Self {
            handlers: Vec::new(),
        }
    }

    pub fn register(&mut self, handler: Box<dyn HookHandler>) {
        self.handlers.push(handler);
    }

    pub fn dispatch(
        &self,
        event: &HookEvent,
        payload: &Value,
        context: &CommonContext,
    ) -> Vec<(String, HandlerError)> {
        let mut errors = Vec::new();
        for handler in &self.handlers {
            if !handler.supported_events().contains(event) {
                continue;
            }
            let t = std::time::Instant::now();
            let result = handler.handle(event, payload, context);
            context.logger.info(&format!(
                "handler '{}' {:?}: {}ms",
                handler.name(),
                event,
                t.elapsed().as_millis()
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

impl Default for HookHandlerRegistry {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parse_known_events() {
        for name in [
            "PreToolUse",
            "PostToolUse",
            "PostToolUseFailure",
            "UserPromptSubmit",
            "Stop",
            "StopFailure",
            "SessionStart",
            "SessionEnd",
            "SubagentStart",
            "SubagentStop",
            "PreCompact",
            "PostCompact",
            "Notification",
        ] {
            let event = HookEvent::parse(name).expect(name);
            assert_eq!(event.as_str(), name);
        }
        assert!(HookEvent::parse("BogusEvent").is_none());
    }

    #[test]
    fn extract_helpers_handle_missing_fields() {
        let p = json!({"hook_event_name": "Stop"});
        assert_eq!(extract_session_id(&p), "");
        assert_eq!(extract_cwd(&p), "");
    }
}
