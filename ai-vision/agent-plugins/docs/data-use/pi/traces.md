# Pi observ-pi 轨迹字段

## 范围

本文是本仓库 `observ-pi` 生产 trace 的 canonical 字段说明。事实来源是 [`SemanticEngine`](../../../pi/observ-pi/src/semantic.ts)、[`createTelemetry`](../../../pi/observ-pi/src/telemetry.ts) 和配置解析 [`config.ts`](../../../pi/observ-pi/src/config.ts)。它不包含 [Pi 事件字段](./events.md)、Metrics，也不把 `pireplay` raw callback envelope 当作 trace span。

状态说明：`当前上报` 表示生产 semantic engine 直接构造 span attribute；`条件上报` 表示需要对应 hook、配置或分支；`raw-only` 表示只在 canonical input/replay envelope 中；`不可用` 表示当前生产 trace 没有证据会上报。

生产 trace 受 `OBSERV_PI_ENABLE_TELEMETRY=true` 或 `AGENT_ENABLE_TELEMETRY=true` 开关控制；`tracesConfig.enabled` 和 trace endpoint 决定实时 trace exporter。`OBSERV_PI_DEBUG=true` 会把同一 OTLP spans 额外写到 `OBSERV_PI_REPLAY_DIR` 下 `spans_<session>.jsonl`，用于 debug/golden parity，不改变生产 span 语义。

## 拓扑

```text
CHAT / Turn
├── LLM generation
├── TOOL execution
├── TOOL synthetic skill（命令 skill 或 catalog-read 派生 skill）
└── COMPACTION
```

`LLM`、`TOOL` 和 `COMPACTION` 都是同一 `CHAT` Turn 的子 span。`COMPACTION` 在 Pi 当前实现中是独立 span kind，不应写成普通 tool span；它的 LogRecord 事件仍用 `gen_ai.operation.name=execute_tool` 和 `gen_ai.tool.name=compaction` 表达。合成 skill span 是 TOOL span：命令 skill 在 Turn 结束时补发；catalog-read 派生 skill 会 link 到已完成的 read tool span，而不是把原 read tool 改名。

每个 `before_agent_start` 构造的用户 Turn 写 `_internal.meta.root="1"`；即使 OTel 当前上下文已有 upstream parent 也保留。LLM、TOOL、COMPACTION、event 和 metric 不继承该字段。

## Resource attributes

Resource 来自 [`parseConfig`](../../../pi/observ-pi/src/config.ts) 的 `config.resource`，并在 [`createTelemetry`](../../../pi/observ-pi/src/telemetry.ts) 中传给 traces/logs/metrics provider。

| 字段 | 类型 | 状态/条件 | 来源与含义 | 隐私 |
|---|---|---|---|---|
| `service.name` | string | 当前上报 | `OTEL_SERVICE_NAME`、配置 `serviceName`，默认 `pi` | 低风险 |
| `service.version` | string | 当前上报 | 当前硬编码为 `0.1.0`，与 `observ-pi` package 版本一致 | 低风险 |
| `ant.agent.instance` | string | 当前上报 | `AIVISION_AGENT_INSTANCE` 或 hostname | 环境指纹 |
| `ant.agent.env` | string | 当前上报 | `AIVISION_AGENT_ENV`，默认 `Default` | 部署信息 |
| `ant.agent.scene` | string | 当前上报 | `AIVISION_AGENT_SCENE`，默认 `default` | 部署信息 |
| `ant.agent.type` | string | 当前上报 | 固定 `pi` | 低风险 |
| `ant.agent.product` | string | 当前上报 | 固定 `Pi` | 低风险 |
| `agentic.collection.mode` | string | 当前上报 | `OBSERV_PI_COLLECTION_MODE` / `AIVISION_COLLECTION_MODE`，默认 `full` | 安全控制信息 |
| `ant.meta.trigger` | string | 当前上报 | 外部调用方通过最终合并的 `otelExporter.resourceAttributes` 配置，或由最高优先级 `AIVISION_META_TRIGGER` 覆盖；未显式提供时为空字符串，显式非法值为 `unknown`，不从 Pi 或 replay transcript 推断 | 任务归因 |
| `vcs.repository.url.full` / `vcs.ref.head.name` / `vcs.ref.head.revision` | string | 当前未上报 | E1 需要从可信 `ctx.cwd` 显式探测并按 session 写入 Resource；无事实时省略 | 仓库/分支敏感 |
| `otelExporter.resourceAttributes` 自定义键 | scalar | 显式配置 | 配置文件、`OTEL_RESOURCE_ATTRIBUTES`、`AIVISION_OTEL_RESOURCE_ATTRIBUTES`、`OBSERV_PI_OTEL_RESOURCE_ATTRIBUTES` | 部署方负责 |
| `ant.username` / `ant.uid` / `username` | string | 不作为 Resource | `config.ts` 会从 Resource attributes 删除这些键；它们属于 signal/span/event attributes | 用户身份 |

`ant.meta.trigger` 未配置时输出空字符串，不会默认补 `unknown`；只有显式空值或非法值才规范化为 `unknown`。同一规范化后的 Resource 同时交给 traces、metrics 和 events/logs provider。该字段不投影到 Turn，因此 Pi 不生成 `agentic.turn.trigger`。

## Signal / span 公共字段

这些字段由 `semantic.ts` 的 `common` 属性和各 span start/end 属性注入。

| 字段 | 类型 | 状态/条件 | 适用 span | 来源/含义 | 隐私 |
|---|---|---|---|---|---|
| `gen_ai.span.kind` | string | 当前上报 | span start | `CHAT` / `LLM` / `TOOL` / `COMPACTION` | 低风险 |
| `gen_ai.operation.name` | string | 当前上报 | 全部 span | `invoke_agent`、`chat`、`execute_tool` | 低风险 |
| `gen_ai.conversation.id` | string | 当前上报 | 全部 span | Pi session id | 高基数关联键 |
| `gen_ai.chat.id` | string | 当前上报 | Turn 及子 span | `session_id:turn_number` | 高基数关联键 |
| `agentic.runtime.name` | string | 当前上报 | 全部 span | 固定 `pi` | 低风险 |
| `agentic.turn.number` | int | 当前上报 | CHAT、event 关联；部分子 span 通过 events 表达 | 用户 turn 计数 | 低风险 |
| `ant.username` / `ant.uid` | string | 当前上报；可动态 | 全部 span / events | signal attributes；`dynamicUsername` / `dynamicUid` 时每次 emit 从环境重读 | 用户身份 |
| `user.id` | string | 当前上报 | 全部 span / events | `ant.uid` 或解析出的 `userId` | 用户身份 |
| `TRACEPARENT`（不作为 span 属性） | host context | 当前未暴露 | Pi host contract 没有外部上游 W3C context source；不得从内部 `context.active()` 推断 | 跨进程关联信息 |
| `langfuse.trace.tags` | string[] | 条件上报 | CHAT end | 当前 Turn 内 tool/skill 分类标签 | 可能暴露工具或 skill 名称 |

## CHAT / Turn

| 字段 | 类型 | 状态/条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `_internal.meta.root` | string | 每个 owned Turn 固定为 `"1"` | 下游 trace 列表查询使用的逻辑 Turn 根；不表示物理 OTel root | 内部索引元数据 |
| `gen_ai.operation.name=invoke_agent` | string | 当前上报 | `before_agent_start` 创建 Turn span | 低风险 |
| `gen_ai.input.messages` | JSON string | 当前上报 | user prompt 组成的 OpenAI-compatible message array | prompt 敏感；privacy 模式先在 canonical payload 中脱敏 |
| `gen_ai.output.messages` | JSON string | 条件上报 | Turn 正常结束且已有 assistant output 时写入 | 响应敏感 |
| `langfuse.trace.tags` | string[] | 条件上报 | tool/skill 出现时由 `addToolTraceTag` 聚合 | 可能暴露工具或 skill 名称 |
| `error.type` / `error.message` | string | 条件上报 | `session_shutdown` 强制关闭 Turn 时写 `cancelled` | 错误信息 |

## LLM

| 字段 | 类型 | 状态/条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `gen_ai.operation.name=chat` | string | 当前上报 | `before_provider_request` 创建 LLM span | 低风险 |
| `gen_ai.request.model` | string | 当前上报 | provider request payload、`model_select` 状态或默认模型 | 模型路由信息 |
| `gen_ai.provider.name` | string | 当前上报 | provider request / assistant message / model selection 归一化 | 模型路由信息 |
| `gen_ai.input.messages` | JSON string | 当前上报或条件上报 | input materializer 选出的 messages；不可用时退回 provider payload 或 Turn prompt | prompt/上下文敏感；privacy 模式可脱敏 |
| `gen_ai.system_instructions` | JSON/string | 当前上报 | provider payload system 或 Turn systemPrompt | 系统提示敏感 |
| `gen_ai.response.model` | string | 条件上报 | assistant `message_end` 或 shutdown fallback | 模型路由信息 |
| `gen_ai.response.id` | string | 条件上报 | assistant message responseId | 高基数关联键 |
| `gen_ai.response.finish_reasons` | JSON string | 条件上报 | stopReason，缺失为 `unknown`；shutdown 为 `cancelled` | 低风险 |
| `gen_ai.output.messages` | JSON string | 条件上报 | assistant message content | 响应敏感 |
| `gen_ai.usage.input_tokens` / `.output_tokens` | int | 条件上报 | assistant message usage；shutdown 路径为 0 并带 missing 标记 | 使用量信息 |
| `agentic.usage.cache_read_tokens` | int | 条件上报 | assistant message usage cached tokens | 使用量信息 |
| `agentic.usage.input_tokens.missing` / `.output_tokens.missing` | string | 条件上报 | shutdown 关闭未完成 generation 时标记 usage 不可得 | 低风险 |
| `agentic.llm.duration_ms` | number | 当前上报 | generation start 到 message_end/shutdown 的耗时 | 性能信息 |
| `error.type` / `error.message` | string | 条件上报 | provider response status >= 400、message errorMessage 或 shutdown cancelled | 错误信息可能敏感 |

## TOOL

| 字段 | 类型 | 状态/条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `gen_ai.operation.name=execute_tool` | string | 当前上报 | `tool_execution_start` 创建 TOOL span | 低风险 |
| `gen_ai.tool.name` | string | 当前上报 | Pi tool name 归一化后的 rawName | 工具身份 |
| `gen_ai.tool.call.id` | string | 当前上报 | Pi toolCallId | 高基数关联键 |
| `gen_ai.tool.call.arguments` | JSON string | 条件上报 | tool args；合成 skill span 在 privacy 模式为 `[REDACTED]` | 高敏感 |
| `gen_ai.tool.call.result` | JSON string | 条件上报 | `tool_execution_end.result`、shutdown 取消值或合成 skill loaded 值 | 高敏感 |
| `agentic.tool.type` | string | 当前上报 | `toolIdentity` 分类，如 skill/system 等 | 工具身份 |
| `agentic.tool.name` | string | 当前上报 | 逻辑工具名或 skill 名 | 工具/skill 身份 |
| `agentic.tool.raw_name` | string | 当前上报 | Pi 原始工具名 | 工具身份 |
| `agentic.tool.id` | string | 当前上报 | call id 或合成 call id | 高基数关联键 |
| `agentic.tool.duration_ms` | number | 条件上报 | tool start 到 end/shutdown 的耗时；合成 derived skill 为 0 | 性能信息 |
| `agentic.tool.input.missing` | string | 条件上报 | `tool_call` event 在 args 不存在时标记；当前 tool span start 总是序列化 args 值 | 低风险 |
| `agentic.tool.input.truncated` | string | 条件上报 | normalizer projection 标记 args 截断 | 安全元数据 |
| `agentic.tool.derived` | bool | 条件上报 | catalog-read 派生 skill span | 低风险 |
| `agentic.tool.call_id.synthetic` | bool | 条件上报 | command skill 或派生 skill span 使用合成 call id | 低风险 |
| `error.type` / `error.message` | string | 条件上报 | tool error 或 shutdown cancelled | tool result 可能敏感 |

## COMPACTION

| 字段 | 类型 | 状态/条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `gen_ai.span.kind=COMPACTION` | string | 条件上报 | `session_before_compact` 创建 compaction span | 低风险 |
| `gen_ai.operation.name=execute_tool` | string | 条件上报 | compaction 被建模为可执行系统操作 | 低风险 |
| `agentic.compaction.trigger` | string | 条件上报 | `session_before_compact.reason`，缺失为 `unknown` | 低风险 |
| `error.type` / `error.message` | string | 条件上报 | shutdown 强制关闭未完成 compaction | 错误信息 |
| token before/after/saved | n/a | 不可用 | 当前 compaction span/event 不从 `session_compact.compactionEntry` 映射 token 字段 | n/a |

## force-close / error 行为

- `session_shutdown` 会先结束未完成 LLM generation，写 `api_request` event、LLM span error、missing token 标记和 `cancelled` finish reason。
- 同一 shutdown 分支会结束所有未完成 tool，写 `tool_result` event、tool span error、`[cancelled: session shutdown]` result。
- 未完成 compaction 会通过 `compaction_result` event 和 COMPACTION span error 收尾。
- Turn 关闭时如果带 error，CHAT span 不写最新 output，只写 error status；最后发 `session_end` event。
- Telemetry runtime `shutdown()` 还会 end 内存中残留 spans，但这只是 SDK 清理兜底，不会补齐上述 semantic event fields。

## Skill 派生与链接

Pi 有三种 skill 证据路径，均由 production normalizer/semantic engine 处理，不从邻近时间或名称猜测：

- native `Skill` tool：`tool_execution_start` 直接识别为 skill tool，并记录 occurrence id，避免后续重复合成。
- `/skill:<name>` 命令：`before_agent_start` 解析 structured skill prompt；若未观察到同一 occurrence 的 native skill，Turn 结束时补发一个 synthetic TOOL span。
- catalog-read 派生：`read` 工具读取系统 prompt 中列出的 skill path 且成功结束后，Turn 结束时补发 derived skill TOOL span，并通过 OpenTelemetry span link 指向原 read tool span。

派生 span 的 arguments 在 `privacy` 模式为 `[REDACTED]`；full 模式为 `{"skill": "<name>"}`。派生 span result 在 privacy 模式为 `[REDACTED]`，full 模式为 `{"status":"loaded"}`。

## raw-only / 不可用项目

- `_internal.meta.root` 是仓库保留字段；`otelExporter.attributes` 和 `OBSERV_PI_OTEL_ATTRIBUTES` 中的同名键会被忽略，其他 `_internal.*` 自定义键不受影响。
- `CanonicalEvent` 的 checksum、projection、field status、hook correlation 和 raw payload 是 `raw-only`，除非 `semantic.ts` 明确映射，否则不会自动成为 span attribute。
- `message_update` 和 `tool_execution_update` 当前被 runtime 入口作为高频 hook 忽略，不生成 span、event 或 metric。
- 当前 trace 不上报 VCS URL/ref/revision、cwd、workspace path、terminal type、organization id、request id、retry attempt、cost、speed 或 provider HTTP status code。HTTP status 只参与错误判断，不作为 span attribute 保存。E1 Git 仓库上下文目前未实现；`ctx.cwd` 仅用于项目配置解析。
- Metrics 是独立 signal；`agent.api_request.*` 和 `agent.tool_call.*` 不属于本文 trace 字段。
