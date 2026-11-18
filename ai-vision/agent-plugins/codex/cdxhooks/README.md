# cdxhooks

Codex 的 Langfuse 采集 hook，使用 Rust 实现，整体架构、模块边界、测试标准和发布方式对齐 `claudecode/cchooks`。

## 能力范围

- 从 hook stdin 读取 Codex payload，支持 1000ms 超时保护
- 解析 Codex transcript JSONL，按 turn 聚合 user、assistant、tool spans
- 基于增量 offset/state 读取 transcript，避免重复上报
- 每次 `PostToolUse` 只发布 session-local、无网络的 Flush；worker 在累计到完整 Generation 批次后，把已结束的 `chat <model>` 与对应 Tool children 合并成一个 OTLP Batch
- `Stop` / `SessionEnd` 异步上报稳定的 root Chat span，并只补偿实时链路未记账的 children
- 文件日志、原子 state 持久化、灰度/正式环境切换、跨平台发布脚本

实时导出默认开启。`UserPromptSubmit` 先有界采集当前仓库资源信息，再与 trace/realtime rotation 一起写入当前 session 独占的 state shard；`PreToolUse` / `PostToolUse` 只做有界状态更新、原子 spool 发布和 detached worker 唤醒，前台 hook 不执行网络请求。`Stop` 和 `SessionEnd` 共用权威 finalizer：第一次发送 root Chat span，并复用持久化的 trace/root/child IDs，只补发实时链路未成功记账的 Tool/Generation children。hook 输入模型、事件字段和 transcript 结构参考 [codex-hooks-overview.md](../codex-hooks-overview.md)。

## 目录结构

```text
src/
├── common/
│   ├── hook.rs         # stdin 读取、事件枚举、session/transcript 提取
│   ├── logger.rs       # 文件日志与轮转
│   ├── otel.rs         # OTel span 构建与 Langfuse 导出
│   └── transcript/     # Codex transcript JSONL 解析与 turn 聚合
├── hooks/
│   ├── mod.rs
│   ├── dynamic/        # 预留的动态注册扩展点
│   └── langfuse/
│       ├── langfuse_handler.rs
│       └── state.rs
└── main.rs
```

## 构建

```bash
# 默认构建 AI Vision 版（需要 aivision.keys.json）
cargo build

# 运行测试
cargo test

# 构建 common 版本（不带内置 key，保留实时 hook handler）
cargo build --no-default-features --features common,tool_context --bin cdxhooks

# 当前平台 release
cargo build --release --bin cdxhooks

# 跨平台发布构建（需要 cargo-zigbuild 及各版构建配置）
make release
```

## Hook 接入

Codex hooks 通常配置在 `~/.codex/hooks.json` 或项目下 `.codex/hooks.json`。实时模式需要把同一个 `cdxhooks` command 挂到 `UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop` 和 `SessionEnd`：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/<username>/.codex/hooks/cdxhooks"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/<username>/.codex/hooks/cdxhooks"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/<username>/.codex/hooks/cdxhooks"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": ".*",
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
        "matcher": ".*",
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

一个常见的本机安装方式：

```bash
cargo build --release --bin cdxhooks
mkdir -p ~/.codex/hooks
cp target/release/cdxhooks ~/.codex/hooks/cdxhooks
```

## 运行时约定

- 优先从 stdin payload 读取 `session_id`、`transcript_path`、`hook_event_name`
- `Stop`/`SessionEnd` 必须携带 `session_id` 与 `transcript_path`；缺失时 fail-closed，不扫描其他 session
- `PreToolUse` 为每个 `tool_use_id` 持久化开始时间；`PostToolUse` 持久化结束时间并计算精确正 duration。transcript timing 仅在 hook pair 不可用时作为 fallback
- Tool timing 缺失、倒序或非正时不构造零耗时实时 span；该 Tool 留给 Stop/SessionEnd finalizer 权威补偿
- transcript 先按 host 事件顺序归一化为不可变的 Model Invocation 与 Tool Callback，再由同一语义路径构建 realtime、Stop-only 和 replay span；`token_count` 只属于对应 invocation 的 usage/闭合证据，永远不作为下一次 Generation 的开始时间
- `response_item.reasoning` 是已完成的模型输出，其写入时间不是调用起点。首个 Generation 使用已有 user submission 或 `task_started` 时间，后续使用前一 callback 的结束时间；已有独立 ModelEvent 开始证据仍优先保留。宿主生命周期包含原生请求等待，不替换为外部 provider HTTP 时长；缺失因果起点时只保留已有观测边界，不补造更早的时间
- 每个已完成 host callback 都独立生成一个 Tool span；`write_stdin` 等 PTY continuation 只保留内部关联，不会回写或延长此前已导出的 Tool span
- Generation 在发起 Tool 的 action 时间结束，Tool 从同一因果边界开始；二者保持 `Turn -> Generation/Tool` sibling 拓扑，不会把 PTY 等待时间计入 Generation
- worker 从 host transcript 做最多 1 MiB / 100 ms 的无 settle 扫描；schema-v2 scan 使用原生 `turn_id` 定位开放 turn，以最后一个已完成 ToolCall 的 assistant index 为安全边界，Generation 只按完整批次发送，默认批次为 3
- 批次大小统计的是已结束 Generation/chat 数量，不是 `PostToolUse` callback 次数；一个 Generation 可以关联多个 Tool
- 正常 v2 实时链路只有 Batch 会发 HTTP；Flush 的 `otel` 为 `null`，只负责唤醒 session worker
- 实时 Batch HTTP 最长 5 秒且只尝试一次。失败会原子标记整个 Batch 及其 children，不做 transport retry；最多由下一次 `PostToolUse` 创建一个新的显式 compensation Batch，之后只由 `Stop` / `SessionEnd` 权威补齐
- 旧 schema-v1 durable scan job 仍使用严格 tool ID 匹配；新任务不会把 hook `exec-*` ID 当作 transcript `call_*` ID
- 同一 Batch 内的 Tool/Generation children 使用同一持久化 turn root `parentSpanId`，二者互为 sibling；允许 children 先于 root 到达
- `Stop` / `SessionEnd` finalizer 第一次发送 root Chat span，并只包含缺失/未记账 children
- transcript 采用增量读取：每个 session 独立保存 offset、buffer、turn count、realtime sequence/high-water 和稳定 IDs
- `task_complete` / `turn_aborted` 后可以权威 flush 整个 turn；开放 turn 只上报已完成工具边界前的完整 Generation 批次，未满批次的尾部保留到后续 hook 或 `Stop` / `SessionEnd`
- 前台 hook 不做 HTTP；detached worker 负责 transcript 扫描、OTLP 构建、HTTP 和 delivery checkpoint
- 所有可变 runtime artifact 都在 collision-resistant session 目录下；state/spool/log root 只作为容器，不存在跨 session 的 state、lock、lease、journal、index 或 queue
- `UserPromptSubmit` 是 session repository snapshot 的唯一 writer：在 state lock 外最多执行一次 `git remote -v` 和一次 branch 查询，每个命令上限 250ms；随后在一个最长 25ms 的 session transaction 中写入 VCS、trace 与 realtime rotation
- Git remote 支持非 `origin`。多个 remote 依次优先 `origin`、fetch、remote 名称和 URL 的稳定字典序，与托管平台无关
- Git 暂时不可用时，同 cwd 保留 last-known-good snapshot；cwd 已变化或确认不是 Git 仓库时清除旧 snapshot。采集或 state lock 超时均 fail-open，不阻断交互
- `Stop`、`SessionEnd` 和 worker 只读取匹配 cwd 的 session snapshot，缺失或不匹配时省略 VCS 属性，不再执行 Git 或从 transcript 推断仓库
- `vcs.repository.url.full` / `vcs.ref.head.name` 在 realtime/Stop payload 计算 digest 和发布 spool 之前注入该 session 拥有的每个 `resourceSpans[]`；Bash child 的 `OTEL_RESOURCE_ATTRIBUTES` 复用同一 snapshot
- hosted tool、拓扑不明确的 subagent/orchestration tool、partial Generation batch 由 Stop 补偿
- AI Vision 版内置 prod/gray 配置，支持运行时环境变量覆盖。

## 环境变量

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `CDXHOOKS_LANGFUSE_ENV` | `gray` 时走灰度环境（fallback: `LANGFUSE_ENV`） | `prod` |
| `ANT_GRAY` | `true` 时走灰度环境 | `false` |
| `CDXHOOKS_LANGFUSE_BASE_URL` | 覆盖 Langfuse 地址（fallback: `LANGFUSE_BASE_URL`） | 无 |
| `CDXHOOKS_LANGFUSE_PUBLIC_KEY` | 覆盖 public key（fallback: `LANGFUSE_PUBLIC_KEY`） | 无 |
| `CDXHOOKS_LANGFUSE_SECRET_KEY` | 覆盖 secret key（fallback: `LANGFUSE_SECRET_KEY`） | 无 |
| `CDXHOOKS_USERNAME` | 上报用户名（fallback: `LANGFUSE_ATTR_USERNAME`） | `$USER` |
| `CDXHOOKS_LANGFUSE_METADATA` | 额外 metadata JSON（fallback: `LANGFUSE_METADATA`） | 无 |
| `CDXHOOKS_LANGFUSE_TAGS` | 逗号分隔 tags（fallback: `LANGFUSE_TAGS`） | 无 |
| `CDXHOOKS_ATTR_*` | 额外 metadata 字段（fallback: `LANGFUSE_ATTR_*`） | 无 |
| `CDXHOOKS_REALTIME_EXPORT` | 实时 child 导出开关；支持 `1`/`0`/`true`/`false`（大小写与首尾空白不敏感），非法值回退为开启 | `true` |
| `CDXHOOKS_REALTIME_LLM_EXPORT_BATCH_SIZE` | Generation 实时完整批次大小；接受正整数文本/数值，非法、0、负数或溢出回退默认值 | `3` |
| `CDXHOOKS_RETRY_POLICY` | finalizer/legacy delivery checkpoint 策略；v2 realtime Batch 始终单次尝试，失败走显式 Post/Stop 补偿 | `at_least_once` |
| `CDXHOOKS_STOP_HTTP_TIMEOUT_MS` | 单次 turn POST 的最大超时（毫秒），实际值受 hook 剩余总预算限制 | `20000` |
| `CDXHOOKS_DEBUG` | 打开 debug 日志 | `false` |
| `CDXHOOKS_LOG_DIR` | 覆盖日志目录 | `{edition root}/logs` |
| `CDXHOOKS_STATE_DIR` | 覆盖状态目录 | `{edition root}/state` |
| `CDXHOOKS_SPOOL_DIR` | 覆盖 durable worker 队列目录 | `${AIVISION_HOME:-~/.aivision}/cdx/spool` |
| `CODEX_SESSIONS_DIR` | 覆盖 transcript 根目录 | `$CODEX_HOME/sessions` |
| `CODEX_HOME` | Codex home 目录 | `~/.codex` |

默认 AI Vision 构建由 `build.rs` 从 crate 目录的 `aivision.keys.json` 注入 prod/gray Langfuse 配置。Common 版使用 `--no-default-features --features common`。

`CDXHOOKS_RETRY_POLICY` 不提供 exactly-once。对 session-v2 realtime Batch，无论该值为何都只发送一次；失败后不做 worker transport retry，而是最多一次后续 Post compensation，之后交给 finalizer。旧任务与 Stop/finalizer 的 checkpoint 语义仍按策略处理：

- `at_least_once` 是默认值。children 仅在明确 2xx 后记账；失败由显式 compensation/finalizer 补偿，因此响应丢失场景仍可能重复。
- `at_most_once` 在 HTTP 前持久化 attempt/policy-terminal 状态。请求失败也不会重试或被 finalizer 重发，因此降低客户端重复概率，但可能丢失。

每个 session 的 spool 独立使用 `pending` / `claimed` / `done` / `failed` 状态。实时 job 采用 prepare/register/publish 协议；未成功注册或 digest 不匹配的 job 不可 claim。可用 `cdxhooks worker status` 跨 session 临时汇总积压，命令不会创建共享索引或锁；使用 `CDXHOOKS_DEBUG=true` 查看配置解析、enqueue/dedupe、scan defer、delivery、stale recovery 和 Stop compensation 诊断。日志不会记录凭据、完整 prompt、完整工具参数或结果。

### Session runtime 布局

`<session-key>` 是清洗后的可读前缀加稳定 hash，避免不同原始 session id 清洗后冲突：

```text
${CDXHOOKS_STATE_DIR}/sessions/<session-key>/
├── state.json
└── tools/<tool-key>.json

${CDXHOOKS_SPOOL_DIR}/sessions/<session-key>/
├── pending/
├── claimed/
├── done/
└── failed/

${CDXHOOKS_LOG_DIR}/cdxhooks_<sanitized-session-id>.log
```

`<sanitized-session-id>` 仅保留 `[A-Za-z0-9._-]`，其他字符替换为 `_`，因此日志始终直接写在 `CDXHOOKS_LOG_DIR` 下。

缺少 `session_id` 的 hook 不创建持久化 runtime artifact。cdxhooks 不读取、迁移或维护旧版跨 session 的全局 state/spool。

`make install-aivision` 将构建的二进制复制到 `~/.aivision/cdx/hooks/`。`make install-common` 将构建的二进制复制到 `~/.codex/hooks/`。在 `~/.codex/hooks.json` 中配置对应命令。

## 测试

```bash
# 默认 AI Vision 版测试（需要 aivision.keys.json）
cargo test --all-targets

# 仅 transcript 集成测试
cargo test --test transcript_integration

# 仅 state 集成测试
cargo test --test state_integration

# common 版本测试
cargo test --no-default-features --features common --all-targets
```

测试重点：

- `common::transcript`：Codex transcript turn 聚合、invocation/callback 归一化、token usage、PTY continuation 独立记录与前缀稳定性
- `common::otel`：Langfuse OTel 请求结构和 span 属性
- `hooks::langfuse::state`：state 原子写入、损坏恢复、session 隔离
- `common::hook`：hook 事件解析与路径提取；Stop 缺少 session/transcript 身份时 fail-closed，跨 session 恢复仅由显式 worker recovery 触发

## 发布产物

在此目录执行 `make release` 会将各版产物写入插件目录下的 `releases/codex/<edition>/<version>/`。可使用 `make aivision` 或 `make common` 单独构建对应版本。
