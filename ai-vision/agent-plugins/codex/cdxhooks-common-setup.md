# cdxhooks Common 版接入说明

本文档说明如何使用显式启用 `common` feature 的 `cdxhooks` 接入自建服务。

## 1. 适用场景

- 使用运行时提供的连接配置
- 上报目标由外部显式提供
- 适合开源、自建 Langfuse 或外部分发

## 2. 构建方式

common 版必须显式关闭默认 feature，并显式启用 `common,tool_context`；缺少 `tool_context` 时不会注册实时 Pre/Post handler：

```bash
cd /path/to/repo/codex/cdxhooks
cargo build --release --no-default-features --features common,tool_context --bin cdxhooks
mkdir -p ~/.codex/hooks
cp target/release/cdxhooks ~/.codex/hooks/cdxhooks
```

## 3. 启用 Codex hooks

在 `~/.codex/config.toml` 中开启：

```toml
[features]
codex_hooks = true
```

## 4. 配置实时 + Stop / SessionEnd hooks

推荐使用全局配置 `~/.codex/hooks.json`。同一条 command 需要同时注册 `UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop` 和 `SessionEnd`；完整 JSON 示例见 [README](cdxhooks/README.md#hook-接入)。

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "env LANGFUSE_BASE_URL=https://cloud.langfuse.com LANGFUSE_PUBLIC_KEY=<pk> LANGFUSE_SECRET_KEY=<sk> LANGFUSE_ATTR_USERNAME=<your_user> /Users/<your_user>/.codex/hooks/cdxhooks"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "env LANGFUSE_BASE_URL=https://cloud.langfuse.com LANGFUSE_PUBLIC_KEY=<pk> LANGFUSE_SECRET_KEY=<sk> LANGFUSE_ATTR_USERNAME=<your_user> /Users/<your_user>/.codex/hooks/cdxhooks"
          }
        ]
      }
    ]
  }
}
```

注意：

- 顶层必须是 `"hooks"`
- `matcher` 建议写 `".*"`
- 五个事件使用同一个命令，`PreToolUse` matcher 必须覆盖 Bash 与非 Bash 工具；默认实时模式下前台 hook 不执行 HTTP
- `UserPromptSubmit` 还负责把当前 Git repository snapshot 写入 session 独占状态；即使关闭实时 child 导出也必须保留该 hook，否则 `Stop`/`SessionEnd` 不会回退执行 Git
- 多 remote 时依次优先 `origin`、fetch，再按名称和 URL 的稳定字典序选择，与托管平台无关
- Git 或 25ms session state lock 暂时失败时 fail-open；同 cwd 可保留上次快照，cwd 变化或确认非 Git 时清除，所有 exporter 仅消费 cwd 匹配的 session snapshot
- `Stop` 与 `SessionEnd` 的 finalizer 第一次发送 root Chat span，并补偿未记账 Tool/Generation children
- `make install-common` 负责构建和复制 binary，但不会修改当前生效的 `~/.codex/hooks.json`

## 5. 环境变量

common 版必须显式配置 trace 导出端点。

### 必填

| 变量 | 说明 |
| --- | --- |
| `CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`，或 `CDXHOOKS_LANGFUSE_BASE_URL` > `LANGFUSE_BASE_URL` | 完整 OTLP traces endpoint，或 Langfuse base URL |

### 常用可选项

| 变量 | 说明 |
| --- | --- |
| `CDXHOOKS_LANGFUSE_PUBLIC_KEY` > `LANGFUSE_PUBLIC_KEY` | Langfuse public key |
| `CDXHOOKS_LANGFUSE_SECRET_KEY` > `LANGFUSE_SECRET_KEY` | Langfuse secret key |
| `LANGFUSE_ATTR_USERNAME` | 上报用户名 |
| `CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS` | OTLP 导出额外 HTTP headers |
| `LANGFUSE_METADATA` | 额外 metadata JSON |
| `LANGFUSE_TAGS` | 逗号分隔 tags |
| `LANGFUSE_ATTR_*` | 额外 metadata 字段 |
| `CDXHOOKS_DEBUG` | 打开 debug 日志 |
| `CODEX_HOME` | 覆盖 Codex home 目录 |
| `CODEX_SESSIONS_DIR` | 覆盖 transcript 根目录 |
| `CDXHOOKS_LOG_DIR` | 覆盖日志目录 |
| `CDXHOOKS_STATE_DIR` | 覆盖状态目录 |
| `CDXHOOKS_SPOOL_DIR` | 覆盖 durable worker 队列目录 |
| `CDXHOOKS_REALTIME_EXPORT` | `1`/`true` 开启，`0`/`false` 关闭；默认开启 |
| `CDXHOOKS_REALTIME_LLM_EXPORT_BATCH_SIZE` | Generation 完整批次大小；默认 `3`，兼容正整数文本/数值 |
| `CDXHOOKS_RETRY_POLICY` | finalizer/legacy checkpoint 策略；v2 realtime Batch 始终单次尝试 |

AI Vision 版的 prod/gray 配置与切换变量见 [配置说明](cdxhooks/config.md#灰度控制)。

## 6. 运行目录约定

默认目录如下：

| 变量 | 默认值 |
| --- | --- |
| `CODEX_HOME` | `~/.codex` |
| `CODEX_SESSIONS_DIR` | `~/.codex/sessions` |
| `CDXHOOKS_LOG_DIR` | `~/.codex/logs` |
| `CDXHOOKS_STATE_DIR` | `~/.codex/state` |

常见文件：

- 日志：`~/.codex/logs/cdxhooks_<sanitized-session-id>.log`
- 状态：`~/.codex/state/sessions/<session-key>/state.json`
- Tool timing：`~/.codex/state/sessions/<session-key>/tools/<tool-key>.json`
- durable queue：`${CDXHOOKS_SPOOL_DIR}/sessions/<session-key>/{pending,claimed,done,failed}`

`<session-key>` 包含可读前缀和稳定 hash。所有可变文件和 lock 都属于单个 session；root 目录只是容器，不保存共享 state、queue、lease、journal 或 index。缺少 `session_id` 时不会创建持久化文件。

## 7. 验证方法

跑一轮 Codex 会话后检查：

```bash
find ~/.codex/logs -name 'cdxhooks*.log' -type f -print
```

成功标志：

- 启动日志出现 `Realtime LLM export: enabled=true configured_batch_size=3`
- 每次 `PostToolUse` 只排队一个无网络 Flush；累计满 3 个已结束 Generation（不是 3 次 callback）后，worker 在一个 HTTP Batch 中一起发送 `chat <model>` 与已完成 Tool siblings
- Tool duration 来自对应 `PreToolUse`/`PostToolUse` 的持久化时间差；transcript 仅作 fallback，不会伪造 `<1ms`/零耗时
- realtime Batch HTTP 上限 5 秒、只尝试一次；失败后最多由下一次 Post 生成一个显式 compensation Batch，之后由 Stop/SessionEnd 补齐
- worker 日志出现 realtime Batch delivery success；Stop/SessionEnd 出现 finalizer enqueue/compensation

工具可能等待完整 Generation 批次或 Stop 才出现，这是批次语义，不是丢失。

## 8. 常见问题

### 提示 `Missing Langfuse configuration`

说明 common 版没有拿到以下任一连接配置：

- `CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`
- `CDXHOOKS_LANGFUSE_BASE_URL`
- `LANGFUSE_BASE_URL`

### 有 transcript 但没上报

优先检查：

- `~/.codex/config.toml` 是否开启了 `codex_hooks = true`
- `~/.codex/hooks.json` 结构是否正确
- hooks 命令里是否把环境变量带全了
- Langfuse host 是否可连通

## 9. 相关文档

- [codex-hooks-overview.md](codex-hooks-overview.md)
- [README.md](cdxhooks/README.md)
