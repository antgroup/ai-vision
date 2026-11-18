# cdxhooks 开发规范

## 项目概述

`cdxhooks` 是 Codex 的 Langfuse hook，使用 Rust 实现，目标是在实现标准、模块划分、测试策略和发布方式上与 `claudecode/cchooks` 保持一致。

## 构建与部署

### 开发构建
```bash
cargo build --no-default-features --features common
cargo test --no-default-features --features common
```

上述命令使用 common 版。Cargo 默认启用 `aivision`，在当前 crate 目录读取 `aivision.keys.json`。

### 打包并部署到本机 hook
```bash
make install-common
```

每次修改 hook 二进制后，都需要重新复制到 `~/.codex/hooks/` 下，让本机 hook 配置生效。

## Hook 配置

本程序作为 Codex 的 `Stop` / `SessionEnd` hook 运行，常见配置位置为 `~/.codex/hooks.json` 或项目下 `.codex/hooks.json`：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/Users/<username>/.codex/hooks/cdxhooks"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/Users/<username>/.codex/hooks/cdxhooks"
          }
        ]
      }
    ]
  }
}
```

## 核心开发规范

### Hook 输入读取

- **`hook_event_name` 必须从 stdin JSON payload 的 `hook_event_name` 字段读取**，不是环境变量。
- 公共字段优先从 stdin payload 读取：`session_id`、`transcript_path`、`cwd`、`model`。
- stdin 读取有 1000ms 超时；超时或 TTY 时返回 `None`。

### Codex 事件模型

当前实现处理以下 Codex hook 事件：

- `SessionStart`
- `PreToolUse`
- `PostToolUse`
- `UserPromptSubmit`
- `Stop`
- `SessionEnd`

Langfuse 上报 handler 对 `Stop` 和 `SessionEnd` 使用同一处理流程。`task_complete` / `turn_aborted` 等原生终态证据决定 turn 的真实完成或中断状态；后继 turn 开始所推断的 `Superseded` 不是成功或失败终态。

为补采已有调用，finalizer 可以导出 `Superseded` turn 中有真实输出或 usage 证据的数据：root status 保持 unset，不伪造 `task_complete`，缺失结果标记为 missing；迟到的原生 usage 按 `turn_id` 回填，并按 `response_id` 去重。pending 子会话只增量导出已完成调用，AGENT 生命周期 span 等真实终态到达后再补齐。不得把无 usage 的待关联内容误报为已完成的模型调用，也不得恢复 stale legacy token 快照。

失败补偿语义由 `CDXHOOKS_RETRY_POLICY` 控制。增量采集沿用现有 `HookHandler`/`HookHandlerRegistry` 和 session worker 机制，不新增常驻扫描或伪造 Stop；无 Stop 且无后续 hook 的静默中断尾部不在自动补采保证范围内。

### transcript 读取策略

- 优先使用 payload 中的 `transcript_path`
- 如果 payload 未提供 transcript，则回退扫描 `CODEX_SESSIONS_DIR` 或 `~/.codex/sessions`
- transcript 按 session 增量读取，state 中至少维护 `offset`、`buffer`、`turn_count`
- 新增 turn 的构造规则统一放在 [turn builder](src/common/transcript/turn_builder.rs)，handler 复用该模块解析后的数据。

### 模块结构

```text
src/
├── common/          # 通用工具模块（hook 解析、日志、OTel、transcript）
│   ├── hook.rs
│   ├── logger.rs
│   ├── otel.rs
│   └── transcript/
├── hooks/           # Hook 专有模块
│   ├── mod.rs       # HookHandler trait、HookHandlerRegistry、CommonContext
│   ├── langfuse/    # 静态内置 handler
│   │   ├── mod.rs
│   │   ├── langfuse_handler.rs
│   │   └── state.rs
│   └── dynamic/     # 动态（运行时注册）handlers（预留扩展）
├── main.rs
└── lib.rs
```

### 新增 Handler 步骤

1. 在 `src/hooks/` 下创建新的 handler 模块
2. 实现 `HookHandler` trait（`name()`、`supported_events()`、`handle()`）
3. 在对应 `mod.rs` 中声明模块
4. 在 `src/main.rs` 的 `registry.register(...)` 处注册

### 错误处理

- 单个 handler 失败不影响其他 handler，`HookHandlerRegistry::dispatch` 做统一隔离
- handler 内部错误返回 `Err(HandlerError)`，由 registry 统一记录日志
- Langfuse 配置缺失时允许优雅退出，但要给出明确日志

## 参考文档

- [codex-hooks-overview.md](../codex-hooks-overview.md)：Codex hooks 输入输出整理
- `OTEL_LANGFUSE_NOTES.md`：OTel → Langfuse 上报格式说明
- `claude-hooks-reference.md`：上游 `cchooks` 所依据的 Claude hooks 规范快照，用于对照实现风格
