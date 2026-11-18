# cchooks 环境变量配置

Claude Code 可观测 hook 的所有运行时环境变量。带 `>` 表示优先级从高到低的 fallback 链。

## 遥测控制

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CCHOOKS_ENABLE_TELEMETRY` | 遥测总开关，设为 `false` 或 `0` 关闭所有处理 | `true` |
| `CCHOOKS_DEBUG` | 调试模式，设为 `true` 开启详细日志 | `false` |
| `CCHOOKS_REALTIME_EXPORT` | 布尔值、数字或字符串形式的 `true`/`1` 开启，`false`/`0` 关闭；未设置或非法值使用默认值 | `true` |
| `CCHOOKS_REALTIME_LLM_EXPORT_BATCH_SIZE` | 触发一次 generation 上报所需的不同 assistant `message.id` 轮数；兼容正整数和数字字符串，非法值回退默认值 | `3` |
| `CCHOOKS_RETRY_POLICY` | Turn 上报策略：`at_least_once` 在 2xx 后 checkpoint，失败由后续 Stop/StopFailure/SessionEnd 补偿；`at_most_once` 在 POST 前 checkpoint，失败不重试但可能丢失 | `at_least_once` |

`CCHOOKS_REALTIME_EXPORT` 默认开启。显式设置为 `false` 或 `0` 时不会注册 realtime handler，
`Stop` / `StopFailure` / `SessionEnd` 的 transcript 重建、预算、checkpoint 和
网络行为保持原样。开启后，`PostToolUse` / `PostToolUseFailure` 直接使用
hook payload 提前发送已完成的 tool span；实时路径不读取或复制 transcript。
`Stop` 仍负责 generation/root span、缺失 tool span 和最终状态提交，并跳过
已经由实时路径确认的 tool span。

generation 使用同一个 `CCHOOKS_REALTIME_EXPORT` 总开关。这是近实时批量
上报，不是 token streaming：
`PostToolUse` / `PostToolUseFailure` 每次只对 hook 提供的 transcript 做一次
有界扫描，按不同 assistant `message.id` 计轮；同一 assistant 内的并行 tool
只计一轮。累计达到 session 快照中的 batch size 后，一次发送当前最大的连续
完整前缀；`1` 表示每轮发送。未达到阈值、transcript 尚未落全、扫描超限或锁
竞争时均不阻塞等待，由 Stop 最终补偿。实时扫描不复制 transcript，也不复用
Stop 的 offset/buffer。

`CCHOOKS_RETRY_POLICY` 只控制客户端跨 hook 的补偿语义，不保证
exactly-once。在实时模式下，相同权衡也应用到单个 tool：
`at_least_once` 可能在服务端已接收但响应或本地 checkpoint 丢失时由 Stop
补偿而产生重复；`at_most_once` 在 POST 前记账，可以避免客户端重发，但
真实的传输失败会造成该 tool span 丢失。

启用实时模式要求 Claude Code 同时调用 `UserPromptSubmit`、`PreToolUse`、
`PostToolUse` 和 `PostToolUseFailure`。tool hook 应在 Claude Code 配置层设为
异步，Stop/SessionEnd 注册保持不变。尚未注册这些事件的部署可以显式设置
`CCHOOKS_REALTIME_EXPORT=false`，待 hook 配置更新后再开启。

## Stop / SessionEnd 预算

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CCHOOKS_STOP_TRANSCRIPT_SETTLE_MS` | Stop / StopFailure 等待最新 turn 终止状态的最长时间；每 100ms 轮询，命中后立即处理 | `2000` |
| `CCHOOKS_STOP_BUDGET_MS` | Stop / StopFailure 总处理预算（毫秒） | `14000` |
| `CCHOOKS_SESSION_END_BUDGET_MS` | SessionEnd 总处理预算（毫秒） | `110000` |
| `CCHOOKS_STOP_HTTP_TIMEOUT_MS` | 单次 turn POST 的最大超时（毫秒）；实际值不超过 hook 剩余总预算 | `20000` |
| `CCHOOKS_STOP_CHECKPOINT_RESERVE_MS` | 为最终状态 checkpoint 预留的时间（毫秒） | `500` |

## 采集模式

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CCHOOKS_COLLECTION_MODE` > `AIVISION_COLLECTION_MODE` | 采集模式。`full` = 全量（含对话原文），`privacy` = 隐私（原文替换为 `[REDACTED]`） | `full` |

Privacy 模式下：
- `gen_ai.input.messages` / `gen_ai.output.messages` → `[REDACTED]`
- `gen_ai.tool.call.arguments` / `gen_ai.tool.call.result` → `[REDACTED]`
- `agentic.input.delta` / `agentic.llm.error.message` → `[REDACTED]`
- dynamic_handler（RL 训练数据采集）、turn_digest_handler（摘要提取）完全禁用

## 用户身份

优先级链：`AIVISION_ANT_USERNAME` > `CCHOOKS_USERNAME` > `LANGFUSE_ATTR_USERNAME` > `LANGFUSE_ATTR_ANT_USERNAME` > `CCHOOKS_OTEL_ATTRIBUTES` 中的 `ant.username` / `username` > `USER` / `USERNAME`

| 变量 | 说明 |
|------|------|
| `AIVISION_ANT_USERNAME` | 统一用户标识（最高优先级） |
| `CCHOOKS_USERNAME` | hook 专属用户名 |
| `LANGFUSE_ATTR_USERNAME` | Langfuse 用户名属性（fallback） |
| `LANGFUSE_ATTR_ANT_USERNAME` | Langfuse AI Vision用户名属性（fallback） |

## Agent 标识

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CCHOOKS_AGENT_PRODUCT` > `AIVISION_AGENT_PRODUCT` | Agent 产品名，写入 `ant.agent.product` | `ClaudeCode` |
| `CCHOOKS_AGENT_TYPE` > `AIVISION_AGENT_TYPE` | Agent 类型，写入 `ant.agent.type` | `cchooks` |
| `CCHOOKS_AGENT_SCENE` > `AIVISION_AGENT_SCENE` | 业务场景，写入 `ant.agent.scene` | `Default` |
| `CCHOOKS_AGENT_ENV` > `AIVISION_AGENT_ENV` | 部署环境，写入 `ant.agent.env` | `Default` |

## Langfuse 连接（common 版本必填）

common 版本从环境变量读取，aivision 版本内置默认值。

| 变量 | 说明 |
|------|------|
| `CCHOOKS_LANGFUSE_BASE_URL` > `LANGFUSE_BASE_URL` | Langfuse 服务地址 |
| `CCHOOKS_LANGFUSE_PUBLIC_KEY` > `LANGFUSE_PUBLIC_KEY` | Langfuse Public Key（common 版本必填） |
| `CCHOOKS_LANGFUSE_SECRET_KEY` > `LANGFUSE_SECRET_KEY` | Langfuse Secret Key（common 版本必填） |
| `CCHOOKS_LANGFUSE_ENV` > `LANGFUSE_ENV` | Langfuse 环境选择，`gray` 切换灰度（仅 aivision） |
| `CCHOOKS_LANGFUSE_HEADERS` > `LANGFUSE_HEADERS` | Langfuse HTTP headers（JSON 格式） |

## Langfuse Trace 元数据

| 变量 | 说明 | 格式 |
|------|------|------|
| `CCHOOKS_LANGFUSE_METADATA` > `LANGFUSE_METADATA` | Trace 自定义 metadata | JSON object |
| `CCHOOKS_LANGFUSE_TAGS` > `LANGFUSE_TAGS` | Trace 标签 | 逗号分隔字符串 |
| `CCHOOKS_ATTR_*` > `LANGFUSE_ATTR_*` | 自定义 Trace 属性，`CCHOOKS_ATTR_MY_KEY=val` → metadata `my_key=val` | 环境变量后缀转小写 |

## OpenTelemetry 扩展

| 变量 | 说明 | 格式 |
|------|------|------|
| `AIVISION_META_TRIGGER` | 调用方声明的触发上下文，写入 Resource `ant.meta.trigger`；可选值 `automation` / `interactive` / `unknown`，非法或空值规范化为 `unknown` | 字符串 |
| `CCHOOKS_OTEL_ATTRIBUTES` | Span 级自定义属性；`key=value` 会追加到每个生成的 span，`username` 会规范化为 `ant.username`，已有同名 span 属性不覆盖；仓库保留字段 `_internal.meta.root` 会被忽略 | `key1=val1,key2=val2` |
| `CCHOOKS_OTEL_RESOURCE_ATTRIBUTES` | Resource 级自定义属性（覆盖同名默认值） | `key1=val1,key2=val2` |
| `CCHOOKS_OTEL_EXPORTER_OTLP_HEADERS` | OTLP 导出额外 HTTP headers（合并到 Langfuse 请求） | `key1=val1,key2=val2` |
| `CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | OTLP Traces 导出端点（覆盖 Langfuse URL） | 完整 URL |

`ant.meta.trigger` 用于区分一次 agent 进程/任务是由外部自动化编排触发，还是由用户交互触发。它是 Resource
属性，适合由外层调度器或 hook 启动环境显式传入：

```fish
set -x AIVISION_META_TRIGGER automation
```

优先级：`AIVISION_META_TRIGGER` 高于
`CCHOOKS_OTEL_RESOURCE_ATTRIBUTES` 中的 `ant.meta.trigger`。如果未设置专用变量，则从
`CCHOOKS_OTEL_RESOURCE_ATTRIBUTES` 里按解析顺序取最后一个
`ant.meta.trigger` 作为 fallback；该值非法时结果为 `unknown`，不会继续回退到更早的重复项。专用变量存在但非法也会得到
`unknown`，且不会回退到通用 Resource override。最终 Resource 中只保留一个
`ant.meta.trigger`。两个入口均未提供时输出空字符串；显式提供空值仍视为非法值并规范化为 `unknown`。
metrics Resource 的 `telemetry.sdk.name` 使用 `cchooks`，与 trace/log 保持同一 producer identity。
如果已有 dashboard 或查询按旧值 `telemetry.sdk.name=agentic-otel-common` 过滤 metrics，需要迁移为 `cchooks`。

`agentic.turn.trigger` 是每个 Turn root span 上的字段，表示该轮从 ClaudeCode 会话事实里观察到的触发机制；它不是 Resource
属性，也不表示自动化任务归因。两者是独立事实，允许不一致，例如外层调度器以自动化任务启动 ClaudeCode，但某一轮仍来自交互式输入：

```text
Resource ant.meta.trigger=automation
Turn     agentic.turn.trigger=interactive
```

离线 replay 不会从 transcript 推断 `ant.meta.trigger`。需要在 replay 输出的 Resource 上带自动化/交互式上下文时，必须在 replay
进程环境中显式设置 `AIVISION_META_TRIGGER`，或通过
`CCHOOKS_OTEL_RESOURCE_ATTRIBUTES=ant.meta.trigger=...` 提供 fallback；均未提供时 replay Resource 使用空字符串。

## 目录路径

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CCHOOKS_LOG_DIR` | 日志目录 | `{root}/logs` |
| `CCHOOKS_STATE_DIR` | 状态文件目录 | `{root}/state` |
| `CCHOOKS_SESSION_DIR` | Transcript 副本保存目录 | 不设则不复制 |
| `CCHOOKS_REPLAY_DIR` | Debug 模式 OTLP replay 文件输出目录 | `{root}/replays` |
| `CLAUDE_PROJECTS_DIR` | Claude 项目数据目录 | `~/.claude/projects` |

common 版本默认使用 `~/.claude`，aivision 版本使用 `~/.aivision/cc`。

## Metrics 上报

| 变量 | 说明 |
|------|------|
| `CCHOOKS_METRICS_ENDPOINT` | Metrics OTLP 推送端点（覆盖内置值） |

## LLM 消息构建

| 变量 | 说明 | 可选值 |
|------|------|--------|
| `CCHOOKS_INPUT_MESSAGES_MODE` | 输入消息构建策略 | `full`（完整历史）/ `incremental`（增量，默认） |

## VCS 相关

| 变量 | 说明 |
|------|------|
| `CCHOOKS_VCS_NO_CACHE` | 设为 `true` 后，每次 `UserPromptSubmit` 都重新执行完整 remote 快照；默认同一 cwd 复用 remote，但 branch 每轮都会刷新 |

VCS 资源上下文仅由 `UserPromptSubmit` 采集并写入当前 session shard。remote
选择不要求存在 `origin`；多 remote 时按大小写不敏感的
`origin > fetch > lexical` 顺序确定。Stop、
StopFailure、SessionEnd、realtime tool/generation、Bash tool-context、async
subagent 和 turn-digest 只读取 cwd 匹配的 session 快照，不在导出阶段执行
Git fallback。OTLP 输出会在每个 `resourceSpans[]` 上幂等写入
`vcs.repository.url.full` 与 `vcs.ref.head.name`，缺失快照时保持正常上报并省略
这两个属性。

## Dynamic Data Handler（feature = dynamic）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CCHOOKS_DYNAMIC_UPLOAD_URL` | 动态数据上传地址 | 不设则不上传 |
| `CCHOOKS_DYNAMIC_MAX_STATE_SIZE` | Git state 总大小上限（bytes） | 512KB |
| `CCHOOKS_DYNAMIC_MAX_UNTRACKED_FILE_SIZE` | 单个 untracked 文件大小上限 | 64KB |
| `CCHOOKS_DYNAMIC_MAX_UNTRACKED_TOTAL` | Untracked 文件总大小上限 | 256KB |

## 网络

| 变量 | 说明 |
|------|------|
| `AIVISION_TLS_NOVERIFY` | 设为 `true` 跳过 TLS 证书验证（仅限调试） |

## 灰度控制

| 变量 | 说明 |
|------|------|
| `ANT_GRAY` | 设为 `true` 强制切换灰度环境（仅 aivision） |
