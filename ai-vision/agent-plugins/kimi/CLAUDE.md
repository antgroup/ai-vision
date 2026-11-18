# kimihooks 开发规范

## 项目概述

kimihooks 是 [kimi-cli](https://github.com/MoonshotAI/kimi-cli) 的可观测 hook 程序，Rust 实现，把 kimi-cli 的会话事件采集、转换为 OTLP，上报到 Langfuse / OTLP collector。

## 当前版本：v0.3

## 架构（v0.3）

```
kimi-cli → stdin JSON → kimihooks → kimihooks_state.json + Stop hook 重建 + OTLP 上报
```

**注册的 6 个 hook**：
- `Stop` / `StopFailure` —— 主路径，从 `wire.jsonl` 重建 turn 拓扑 + 合并 state.json + 上报
- `UserPromptSubmit` —— 缓存原始用户输入（与 wire 的 TurnBegin.user_input 对账）
- `SubagentStart` / `SubagentStop` —— 缓存子 agent 调用边界
- `PreCompact` / `PostCompact` —— 仅日志记录（wire 已等价支持，留作观测埋点）

**不注册**：`PreToolUse / PostToolUse / SessionStart / SessionEnd / Notification`。原因：hook payload 字段与 wire.jsonl 完全等价，无独家信息可贡献。

## 状态文件

`~/.kimi/observ/state/kimihooks_state.json`（可由 `KIMI_STATE_DIR` 覆盖）：

```jsonc
{
  "schema_version": 1,
  "sessions": {
    "<session_id>": {
      "wire_fingerprint": "<size>:<line_count>",
      "last_seen_ns": "<unix nanoseconds>",
      "user_prompts": [{ "ts_ns": "...", "prompt": "...", "cwd": "..." }],
      "subagent_starts": [{ "ts_ns": "...", "agent_name": "...", "prompt": "...", "cwd": "..." }],
      "subagent_stops": [{ "ts_ns": "...", "agent_name": "...", "response": "...", "cwd": "..." }]
    }
  }
}
```

写入流程：`StateLock::acquire(2000ms) → load_or_default → upsert_session → prune_older_than(24h) → save (atomic rename)`。

## 模块结构

```
src/
├── main.rs               # stdin → HookHandlerRegistry::dispatch → exit 0
├── lib.rs
├── common/
│   ├── hook.rs           # HookEvent + HookHandler trait + CommonContext (state_file 字段)
│   ├── state.rs          # KimihooksState / SessionState / 三个 *Entry 结构
│   ├── otel.rs           # HookRecord + OTLP build_* 函数
│   ├── dirs.rs / logger.rs / session_locator.rs / context_parser.rs / transcript.rs
│   ├── http_export.rs    # OTLP HTTP POST
│   ├── edition.rs / config.rs / report_config.rs
└── hooks/
    ├── mod.rs            # register_all 注册全部 6 个 handler
    ├── stop.rs           # 主路径：wire 重建 + state 合并
    ├── compact.rs        # PreCompact / PostCompact（仅日志）
    ├── user_prompt.rs    # UserPromptSubmit → state.json
    └── subagent.rs       # SubagentStart / SubagentStop → state.json
```

## 开发规范

### 新增 handler 步骤

1. 在 `src/hooks/<name>.rs` 实现 `HookHandler` trait
2. 在 `src/hooks/mod.rs` 的 `register_all` 中注册
3. 如需缓存数据到 state.json：在 `common/state.rs` 添加新 entry 类型 + SessionState 字段
4. 单元测试：用 `unique_state_path()` + `fake_ctx()` 模式（参考 `user_prompt.rs::tests`）
5. 失败 fail-open：handler 内部 `state.save` 等错误只记日志，不返回 `Err`

### 错误处理

- handler 必须 fail-open（任何 IO 错误只 log，不向上传播）
- StateLock timeout 后即使没拿到锁也继续（fcntl 行为）
- panic 由 `main.rs` 的 `panic::catch_unwind` 捕获，进程恒 exit 0

### 测试

```bash
cd kimi/kimihooks
cargo test --lib                    # 全量
cargo test --lib hooks              # 仅 handler 测试
cargo test --lib common::state      # 仅 state 模块
```

> 注意：测试对环境变量 `KIMI_STATE_DIR` 敏感，新增 handler 测试请用 `CommonContext::new(.., state_file)` 显式注入路径，不要依赖全局 env。

## 部署

```bash
cd kimi
make build                          # cargo build --release
cp kimihooks/target/release/kimihooks ~/.kimi/hooks/kimihooks
# 然后在 ~/.kimi/config.toml 注册（参考 README.md）
```

## 兼容性

- 删除文件：`hook_log_<sid>.ndjson`（v0.2 → v0.3 升级时无迁移需求，旧文件可手动清理）
- 删除文件：`uploaded_<sid>.marker`（同上）
- 新增文件：`kimihooks_state.json`（首次运行时自动创建）
- 二进制兼容：v0.2 的 `kimihooks-replay` 与 v0.3 的 `kimihooks` 共享 `HookRecord` 结构（已搬到 `common::otel`）

## 参考

- 上游 hook schema：`<kimi-cli>/src/kimi_cli/hooks/events.py`
- wire.jsonl schema：`<kimi-cli>/src/kimi_cli/wire/types.py`
- 路线图原稿：`.omc/plans/kimihooks-v0.3-state-and-blind-spots.md`
- 同构参考：`claudecode/cchooks/`
