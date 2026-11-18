//! 每个 kimi-cli hook 事件一个文件，文件内只放本事件的 handler。
//!
//! 已实现 handler：
//!   - **Stop / StopFailure**：从 `wire.jsonl` + `context.jsonl` 重建完整 turn 拓扑
//!     （C1 root + C2 LLM + C3 tool + C6 subagent + C8 compaction），即使用户只注册一个 Stop hook
//!     也能拿到全量数据。详见 `stop.rs` 文档。
//!   - PreCompact / PostCompact（C8 compaction 实时钩子）
//!
//! 未实现 handler 的事件（`UserPromptSubmit` / `SubagentStart` / `SubagentStop` /
//! `Notification` / `PreToolUse` / `PostToolUse` / `PostToolUseFailure` /
//! `SessionStart` / `SessionEnd`）自动落到"无 handler"路径，不会产生错误。
//!
//! PreToolUse / PostToolUse / SessionStart / SessionEnd 曾有 handler，
//! 但 Stop handler 的 WireParser 已从 wire.jsonl 合成全部所需事件，
//! 故这些实时 handler 已移除。
pub mod compact;
mod kimi_code;
pub mod stop;
pub mod subagent;
pub mod user_prompt;

pub use compact::PostCompactHandler;
pub use compact::PreCompactHandler;
pub use stop::StopHandler;
pub use subagent::{SubagentStartHandler, SubagentStopHandler};
pub use user_prompt::UserPromptSubmitHandler;

/// 注册全部 handler。后续 sprint 在这里追加 `register()` 即可。
pub fn register_all(registry: &mut crate::common::hook::HookHandlerRegistry) {
    registry.register(Box::new(PreCompactHandler::new()));
    registry.register(Box::new(PostCompactHandler::new()));
    registry.register(Box::new(StopHandler::new()));
    registry.register(Box::new(UserPromptSubmitHandler::new()));
    registry.register(Box::new(SubagentStartHandler::new()));
    registry.register(Box::new(SubagentStopHandler::new()));
}