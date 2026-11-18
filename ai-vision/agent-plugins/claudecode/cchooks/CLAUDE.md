# cchooks 开发规范

## 项目概述

cchooks 是 Claude Code 的 hook 程序，用 Rust 实现，负责将 Claude Code 会话数据上报到 Langfuse 做可观测性追踪。

## 构建与部署

### 开发构建
```bash
cargo build --no-default-features --features common
cargo test --no-default-features --features common
```

### 打包并部署到本机 hook
```bash
make install-common
```

修改代码后，重新运行安装目标，将新二进制复制到 `~/.claude/settings.json` 所引用的 `~/.claude/hooks/cchooks`。

## Hook 配置

本程序作为 Claude Code 的 `Stop` hook 运行，配置在 `~/.claude/settings.json`：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/Users/<username>/.claude/hooks/cchooks"
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
- 公共字段（`session_id`、`transcript_path`、`cwd`）均来自 stdin payload，字段名为 snake_case。
- stdin 读取有 1000ms 超时，超时或 TTY 时返回 `None`。

### Stop 事件的 `stop_hook_active` 守卫

`Stop` 事件的 payload 包含 `stop_hook_active` 字段。默认情况下该字段为 `true` 时必须立即返回，避免 hook 输出触发无限循环。唯一例外是 structured-output Stop coordinator 已为同一持久化 intent 写入内部 finalizer authorization；该授权只在 detached worker envelope 内使用。

```rust
if crate::hooks::active_stop_should_skip(payload) {
    return Ok(()); // 防止无限循环
}
```

### 模块结构

```
src/
├── common/          # 通用工具模块（hook 解析、日志、OTel、transcript）
│   ├── hook.rs      # HookEvent 枚举、stdin 读取、字段提取工具函数
│   ├── logger.rs
│   ├── otel.rs
│   └── transcript/
├── hooks/           # Hook 专有模块
│   ├── mod.rs       # HookHandler trait、HookHandlerRegistry、CommonContext
│   ├── langfuse/    # Langfuse handler
│   │   ├── mod.rs
│   │   ├── langfuse_handler.rs # Stop 事件 → 上报 Langfuse
│   │   └── state.rs           # 会话状态持久化
│   └── dynamic/     # 动态（运行时注册）handlers（预留扩展）
│       └── mod.rs
├── main.rs
└── lib.rs
```

### HookEvent 枚举

`src/common/hook.rs` 中的 `HookEvent` 枚举覆盖了所有 25 个官方事件，与 `claude-hooks-reference.md` 保持同步。新增事件时需同时更新枚举和 `parse()` 方法。

### 新增 Handler 步骤

1. 在 `src/hooks/` 下按职责添加 handler 模块
2. 实现 `HookHandler` trait（`name()`、`supported_events()`、`handle()`）
3. 在对应的 `mod.rs` 中声明模块
4. 在 `src/main.rs` 的 `registry.register(...)` 处注册

### 错误处理

- 单个 handler 失败不影响其他 handler（`HookHandlerRegistry::dispatch` 做了错误隔离）
- handler 内部错误返回 `Err(HandlerError)`，由 registry 统一记录日志

## 参考文档

- `claude-hooks-reference.md`：Claude Code hooks 官方规范整理（校对时间 2026-03-28）
- [生产 Trace 字段](../../docs/data-use/claudecode/traces.md)
