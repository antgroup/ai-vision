# cdxhooks 环境变量配置

Codex 可观测 hook 的所有运行时环境变量。带 `>` 表示优先级从高到低的 fallback 链。

## 遥测控制

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CDXHOOKS_ENABLE_TELEMETRY` | 遥测总开关，设为 `false` 或 `0` 关闭所有处理 | `true` |
| `CDXHOOKS_DEBUG` | 调试模式，设为 `true` 开启详细日志 | `false` |

## 实时导出

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CDXHOOKS_REALTIME_EXPORT` | 实时 Tool/Generation child 导出开关；支持 `1`、`0`、`true`、`false`，字符串大小写与首尾空白不敏感 | `true` |
| `CDXHOOKS_REALTIME_LLM_EXPORT_BATCH_SIZE` | Generation 实时完整批次大小；配置边界兼容正整数数值和数字字符串 | `3` |

未设置或非法的实时开关回退为开启；未设置、`0`、负数、小数、空串、非法文本或溢出的批次值回退为 `3`，并输出不含敏感内容的有界 warning。实时关闭时保留 Stop/SessionEnd 的完整导出。已创建的 realtime context 会保存当时解析出的开关相关状态、批次和 retry policy，后续环境变量变化不会破坏该 turn 的 finalization。

实时请求是 child-only：`PostToolUse` 先使单个 Tool span 可发送；Tool duration 使用同一 `tool_use_id` 的 `PreToolUse`/`PostToolUse` hook-local wall-clock 时间差，缺失、倒序或非正区间不发送错误的零耗时实时 span，而是留给 Stop/SessionEnd 补偿。Generation worker 通过原生 `turn_id` 在开放 turn 中找到最后一个已完成 ToolCall 边界，只发送完整批次；root Chat span 第一次由 Stop/SessionEnd finalizer 发送，并只补偿未记账 children。默认实时模式下所有前台 hook 均不执行 HTTP；显式关闭后保留既有 Stop-only 导出。

## 用户身份

优先级链：`AIVISION_ANT_USERNAME` > `CDXHOOKS_USERNAME` > `LANGFUSE_ATTR_USERNAME` > `LANGFUSE_ATTR_ANT_USERNAME` > `CDXHOOKS_OTEL_ATTRIBUTES` 中的 `ant.username` / `username` > `USER` / `USERNAME`

| 变量 | 说明 |
|------|------|
| `AIVISION_ANT_USERNAME` | 统一用户标识（最高优先级） |
| `CDXHOOKS_USERNAME` | hook 专属用户名 |
| `LANGFUSE_ATTR_USERNAME` | Langfuse 用户名属性（fallback） |
| `LANGFUSE_ATTR_ANT_USERNAME` | Langfuse AI Vision用户名属性（fallback） |

## Agent 标识

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CDXHOOKS_AGENT_PRODUCT` > `AIVISION_AGENT_PRODUCT` | Agent 产品名，写入 `ant.agent.product` | `Codex` |
| `CDXHOOKS_AGENT_TYPE` > `AIVISION_AGENT_TYPE` | Agent 类型，写入 `ant.agent.type` | `cdxhooks` |
| `CDXHOOKS_AGENT_SCENE` > `AIVISION_AGENT_SCENE` | 业务场景，写入 `ant.agent.scene` | `Default` |
| `CDXHOOKS_AGENT_ENV` > `AIVISION_AGENT_ENV` | 部署环境，写入 `ant.agent.env` | `Default` |

## Trace 导出连接（common 版本必填）

common 版本必须显式提供完整 OTLP traces endpoint 或 Langfuse base URL。AI Vision 版内置默认值。Public Key 和 Secret Key 均为可选项。

| 变量 | 说明 |
|------|------|
| `CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | 完整 OTLP Traces 导出端点；设置后无需额外提供 Langfuse base URL |
| `CDXHOOKS_LANGFUSE_BASE_URL` > `LANGFUSE_BASE_URL` | Langfuse 服务地址 |
| `CDXHOOKS_LANGFUSE_PUBLIC_KEY` > `LANGFUSE_PUBLIC_KEY` | Langfuse Public Key（可选） |
| `CDXHOOKS_LANGFUSE_SECRET_KEY` > `LANGFUSE_SECRET_KEY` | Langfuse Secret Key（可选） |
| `CDXHOOKS_LANGFUSE_ENV` > `LANGFUSE_ENV` | Langfuse 环境选择，`gray` 切换灰度（仅 AI Vision 版） |
| `CDXHOOKS_LANGFUSE_HEADERS` > `LANGFUSE_HEADERS` | Langfuse HTTP headers（JSON 格式） |

## Langfuse Trace 元数据

| 变量 | 说明 | 格式 |
|------|------|------|
| `CDXHOOKS_LANGFUSE_METADATA` > `LANGFUSE_METADATA` | Trace 自定义 metadata | JSON object |
| `CDXHOOKS_LANGFUSE_TAGS` > `LANGFUSE_TAGS` | Trace 标签 | 逗号分隔字符串 |
| `CDXHOOKS_ATTR_*` > `LANGFUSE_ATTR_*` | 自定义 Trace 属性，`CDXHOOKS_ATTR_MY_KEY=val` → metadata `my_key=val` | 环境变量后缀转小写 |

## OpenTelemetry 扩展

| 变量 | 说明 | 格式 |
|------|------|------|
| `AIVISION_META_TRIGGER` | 调用方声明的触发上下文，写入 Resource `ant.meta.trigger`；可选值 `automation` / `interactive` / `unknown`，非法或空值规范化为 `unknown` | 字符串 |
| `CDXHOOKS_OTEL_ATTRIBUTES` | Span 级自定义属性；`key=value` 会追加到每个生成的 span，`username` 会规范化为 `ant.username`，已有同名 span 属性不覆盖；仓库保留字段 `_internal.meta.root` 会被忽略。最多接受 64 项，key 最长 256 bytes，value 最长 4096 bytes（按 UTF-8 边界截断） | `key1=val1,key2=val2` |
| `CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES` | Resource 级自定义属性（覆盖同名默认值） | `key1=val1,key2=val2` |
| `CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS` | OTLP 导出额外 HTTP headers（合并到 Langfuse 请求） | `key1=val1,key2=val2` |
| `CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | OTLP Traces 导出端点（覆盖 Langfuse URL，也可作为 common 版本的独立连接配置） | 完整 URL |

`ant.meta.trigger` 用于区分一次 Codex/cdxhooks 进程或任务是由外部自动化编排触发，还是由用户交互触发。它是 Resource
属性，适合由外层调度器或 hook 启动环境显式传入：

```fish
set -x AIVISION_META_TRIGGER automation
```

优先级：`AIVISION_META_TRIGGER` 高于
`CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES` 中的 `ant.meta.trigger`。如果未设置专用变量，则从
`CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES` 里按解析顺序取最后一个
`ant.meta.trigger` 作为 fallback；该值非法时结果为 `unknown`，不会继续回退到更早的重复项。专用变量存在但非法也会得到
`unknown`，且不会回退到通用 Resource override。最终 trace、dynamic log 和 metrics 的 Resource 中只保留一个
`ant.meta.trigger`；metrics Resource 的 `telemetry.sdk.name` 使用 `cdxhooks`，与 trace/log 保持同一 producer identity。
两个入口均未提供时输出空字符串；显式提供空值仍视为非法值并规范化为 `unknown`。
如果已有 dashboard 或查询按旧值 `telemetry.sdk.name=agentic-otel-common` 过滤 metrics，需要迁移为 `cdxhooks`。

`agentic.turn.trigger` 是每个 logical root Turn span 上的字段，表示该轮从 Codex 会话来源事实里观察到的触发机制快照；它不是 Resource
属性，也不表示自动化任务归因。两者是独立事实，允许不一致，例如外层调度器以自动化任务启动 Codex，但某一轮仍来自交互式用户输入：

```text
Resource ant.meta.trigger=automation
Turn     agentic.turn.trigger=interactive
```

当前 Codex corpus-backed 映射只启用 `interactive`、`subagent` 和 `unknown`：结构化 subagent 来源或
`thread_source=subagent` 映射为 `subagent`，`thread_source=user` 映射为 `interactive`，缺失、冲突或尚无真实样本支撑的来源映射为
`unknown`。`source=cli` / `source=vscode` 不单独作为交互式证据，Turn 内出现
`exec_command` 也不会把 `thread_source=user` 重分类为 programmatic。`programmatic`、`queued`、`system` 是保留的 semconv 合法值，只有未来拿到真实 Codex 原生来源样本后才启用；若未来出现更强的 per-turn direct evidence，应通过显式 resolver seam 覆盖 session-origin snapshot。

离线 replay 不会从 transcript 推断 `ant.meta.trigger`。需要在 replay 输出的 Resource 上带自动化/交互式上下文时，必须在 replay
进程环境中显式设置 `AIVISION_META_TRIGGER`，或通过
`CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES=ant.meta.trigger=...` 提供 fallback；均未提供时 replay Resource 使用空字符串。

## Turn 导出与补偿

当一次 Codex 启动使用已知且固定的计费连接时，可通过环境变量 `CDXHOOKS_BILLING_MODE`
声明 `subscription`（订阅用量）或 `pay_as_you_go`（按量 API）。例如，使用明确由套餐覆盖的连接时，
以 `CDXHOOKS_BILLING_MODE=subscription codex` 启动。通过 ChatGPT 登录本身不足以证明调用由订阅额度覆盖，
额外积分、企业按量计费或在同一次启动中切换连接时，应留空，除非调用链能确认实际计费方式。

采集器在 `UserPromptSubmit` 时按会话和轮次 ID 保存声明。实时和最终导出仅将匹配的声明写入模型调用 Span
的 `aivision.billing.mode`。未配置、配置无效或缺少轮次证据时不添加该属性。离线回放和未匹配的历史轮次
不使用当前环境配置。通用 `CDXHOOKS_OTEL_ATTRIBUTES` 不能设置计费属性。

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CDXHOOKS_RETRY_POLICY` | `at_least_once`：仅在 2xx 后 checkpoint，失败由后续 Stop/SessionEnd 补偿；`at_most_once`：POST 前 checkpoint，失败不重试并可能丢失 | `at_least_once` |
| `CDXHOOKS_STOP_BUDGET_MS` | Stop 总处理预算（毫秒）；terminal marker 轮询仍独立限制为最多 10 秒 | `30000` |
| `CDXHOOKS_SESSION_END_BUDGET_MS` | SessionEnd 总处理预算（毫秒） | `110000` |
| `CDXHOOKS_STOP_HTTP_TIMEOUT_MS` | 单次 turn POST 的最大超时（毫秒）；实际值不超过 hook 剩余总预算 | `20000` |
| `CDXHOOKS_STOP_CHECKPOINT_RESERVE_MS` | 为 state checkpoint 预留的预算（毫秒） | `500` |

两种策略下，每次 hook 对同一个 turn 都只进行一次 HTTP 请求。`at_least_once` 允许跨 hook 重试，可能因响应丢失产生重复；`at_most_once` 禁止跨 hook 重试，可能丢失。两者都不等价于服务端 exactly-once。

实时路径的 checkpoint 顺序同样受该策略控制：`at_least_once` 仅在明确 2xx 后记账；`at_most_once` 在 HTTP 前持久化 attempt。Stop/SessionEnd finalizer 是完整性权威路径。

## 目录路径

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CDXHOOKS_LOG_DIR` | 日志目录 | `{root}/logs` |
| `CDXHOOKS_STATE_DIR` | 状态文件目录 | `{root}/state` |
| `CDXHOOKS_SPOOL_DIR` | durable worker 队列目录 | `${AIVISION_HOME:-~/.aivision}/cdx/spool` |
| `CODEX_SESSIONS_DIR` | Codex 会话 transcript 目录 | `~/.codex/sessions` |

common 版的 root 优先使用 `CODEX_HOME`，默认指向 `~/.codex`。AI Vision 版使用 `~/.aivision/cdx`。

## Metrics 上报

| 变量 | 说明 |
|------|------|
| `CDXHOOKS_METRICS_ENDPOINT` | Metrics OTLP 推送端点（覆盖内置值） |

## Transcript 解析

| 变量 | 说明 | 可选值 |
|------|------|--------|
| `CDXHOOKS_INPUT_FORMAT` | Transcript 输入格式 | `accumulated`（累积格式）/ 默认增量格式 |

## Dynamic Data Handler（feature = dynamic）

| 变量 | 说明 |
|------|------|
| `CDXHOOKS_DYNAMIC_UPLOAD_URL` | 动态数据上传地址，不设则不上传 |

## 网络

| 变量 | 说明 |
|------|------|
| `AIVISION_TLS_NOVERIFY` | 设为 `true` 跳过 TLS 证书验证（仅限调试） |

## 灰度控制

| 变量 | 说明 |
|------|------|
| `ANT_GRAY` | 设为 `true` 强制切换灰度环境（仅 AI Vision 版） |
