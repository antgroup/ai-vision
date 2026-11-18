//! kimihooks 入口：从 stdin 读取一条 hook payload → 路由到匹配 handler → 始终 exit 0。
//!
//! kimi-cli 的 hook 协议（来源 `src/kimi_cli/hooks/engine.py` + `events.py`）：
//! - 父进程通过 stdin 喂入一行 JSON
//! - hook 进程在 timeout 内退出，**任何非 0 退出码都不会阻塞**主流程（HookDef.timeout
//!   默认 30s，engine 是 fail-open 设计）
//!
//! 因此本程序的纪律：
//! - panic 必须被 catch，不允许 abort 父进程
//! - I/O 失败仅记录日志，绝不向上传播
//! - 始终以 `process::exit(0)` 结束

use std::panic::{self, AssertUnwindSafe};
use std::process;
use std::time::Instant;

use kimihooks::common::dirs;
use kimihooks::common::hook::{
    read_hook_payload, CommonContext, HookEvent, HookHandlerRegistry,
};
use kimihooks::common::logger::Logger;
use kimihooks::hooks;

const HOOK_VERSION: &str = env!("CARGO_PKG_VERSION");

fn main() {
    // Installer/CI runtime probes must not load config, create logs or read stdin.
    if matches!(
        std::env::args().nth(1).as_deref(),
        Some("--version" | "-V" | "version")
    ) {
        println!("kimihooks {}", HOOK_VERSION);
        return;
    }
    // 整个 run 用 catch_unwind 包裹，任何 panic 都被吞掉，确保退出码恒为 0。
    let _ = panic::catch_unwind(AssertUnwindSafe(run));
    process::exit(0);
}

/// Load `~/.aivision/kimi/config.env` into the process environment.
///
/// This file is written by `aivision-setup` at install time and carries
/// `AIVISION_ANT_USERNAME`, `KIMI_OTLP_*` endpoints and other config vars.
/// Using a dedicated `.env` file (instead of `~/.zshrc`) guarantees the
/// variables are available regardless of how Kimi CLI was launched.
fn load_dotenv() {
    let home = match std::env::var("HOME").or_else(|_| std::env::var("USERPROFILE")) {
        Ok(h) => h,
        Err(_) => return,
    };
    let env_path = std::path::PathBuf::from(home)
        .join(".aivision")
        .join("kimi")
        .join("config.env");
    if env_path.exists() {
        // Use override mode so config.env values take priority over any empty/unset
        // vars inherited from the Kimi CLI parent process.
        let _ = dotenvy::from_path_override(env_path);
    }
}

fn run() {
    // Load config.env before anything else so env vars are available everywhere.
    if std::env::var("AIVISION_MANAGED_INGEST").ok().as_deref() != Some("1") {
        load_dotenv();
    }

    if !agentic_otel_common::privacy::validate_mode("KIMI_COLLECTION_MODE") { return; }
    let t_start = Instant::now();
    let debug = dirs::debug_enabled();
    let log_file = dirs::log_dir().join("kimihooks.log");
    let logger = Logger::new(log_file, debug);
    logger.info(&format!("kimihooks {} start", HOOK_VERSION));

    // 1) 读 stdin（带 1s 超时 + TTY 检测，参见 agentic-otel-common::hook::read_hook_payload）
    let mut payload = match read_hook_payload() {
        Some(v) => v,
        None => {
            logger.warn("stdin 无有效 JSON payload，退出（无操作）");
            return;
        }
    };

    if agentic_otel_common::privacy::resolve_mode("KIMI_COLLECTION_MODE") != Ok("full") {
        agentic_otel_common::privacy::scrub_hook_payload(&mut payload);
    }

    // 2) 解析 hook_event_name
    let event_name = match payload.get("hook_event_name").and_then(|v| v.as_str()) {
        Some(n) => n.to_string(),
        None => {
            logger.warn(&format!(
                "缺少 hook_event_name，跳过。payload={}",
                payload
            ));
            return;
        }
    };
    let event = match HookEvent::parse(&event_name) {
        Some(e) => e,
        None => {
            logger.warn(&format!("未知 hook 事件 '{}', 跳过", event_name));
            return;
        }
    };
    logger.info(&format!("hook event = {:?}", event));

    // 3) 构建上下文 + 注册 handler
    let state_file = dirs::state_dir().join("kimihooks_state.json");
    let context = CommonContext::new(logger, debug, HOOK_VERSION.to_string(), state_file);
    let mut registry = HookHandlerRegistry::new();
    hooks::register_all(&mut registry);

    // 4) 调度（错误隔离；单 handler 失败不影响其他 handler）
    let errors = registry.dispatch(&event, &payload, &context);
    if !errors.is_empty() {
        context.logger.warn(&format!(
            "{} handler(s) failed for event {:?}",
            errors.len(),
            event
        ));
    }

    context.logger.info(&format!(
        "kimihooks done: total={}ms event={:?}",
        t_start.elapsed().as_millis(),
        event
    ));
}
