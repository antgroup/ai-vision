//! kimihooks: Kimi-CLI 可观测 hook 程序（外部二进制模式 A）
//!
//! - `common/`：与单个 hook 事件解耦的基础能力（目录解析、stdin 读取、transcript 落盘等）
//! - `hooks/`：每个 kimi-cli hook 事件类型一个文件，实现 [`HookHandler`] trait
//!
//! main 入口仅负责：解析 stdin → 路由事件 → 调度 handler → 始终 exit 0（fail-open）。

pub mod common;
pub mod hooks;
