# OpenClaw observ-openclaw 事件字段

## 范围与证据

本文记录本仓库 `observ-openclaw` 当前生产 LogRecord 事件，不记录 Metrics，也不把 replay 插件捕获的 raw callback envelope 当作生产事件字段。生产事件目录来自 [`semconv.ts`](../../../openclaw/observ-openclaw/src/semconv.ts) 的事件常量与 emitter；字段以各 hook call site 为准。

状态：`当前上报` 表示生产代码存在 emitter 调用；`条件上报` 表示还需要 telemetry、events、内容日志或特定 hook 分支；`已定义未触发` 表示只有 semconv 常量/emitter、没有生产 call site；`raw-only` 表示只属于开发者 replay raw callback 输入；`不可用` 表示当前生产 emitter 没有证据。

## 启用、版本与隐私边界

| 项目 | 状态/条件 | 说明 | 代码来源 |
|---|---|---|---|
| Telemetry master switch | 条件上报 | `enableTelemetry` 默认为 false，可由 edition config 或 `AGENT_ENABLE_TELEMETRY` 启用 | [`config.ts`](../../../openclaw/observ-openclaw/src/config.ts) |
| Events provider | 条件上报 | 事件只在 telemetry runtime 返回 `eventsEnabled` 且 hook 中 `hctx.eventsEnabled` 为 true 时 emit | [`telemetry.ts`](../../../openclaw/observ-openclaw/src/telemetry.ts)、[`hooks/index.ts`](../../../openclaw/observ-openclaw/src/hooks/index.ts) |
| Collection mode | 条件上报 | `AIVISION_COLLECTION_MODE` 或配置决定 `full` / `privacy`；内容日志开关由 collection mode 派生 | [`config.ts`](../../../openclaw/observ-openclaw/src/config.ts) |
| 内容字段 | 条件上报 | user prompt、model output、system prompt、tool detail、tool output 分别受 `config.log.events.*` 控制；关闭时写 `[REDACTED]` 或空数组 | [`hooks/session.ts`](../../../openclaw/observ-openclaw/src/hooks/session.ts)、[`hooks/llm.ts`](../../../openclaw/observ-openclaw/src/hooks/llm.ts)、[`hooks/tool.ts`](../../../openclaw/observ-openclaw/src/hooks/tool.ts) |
| Plugin version | 当前上报 | 版本在 Resource 的 `service.version` / `telemetry.sdk.version` 中体现，不是每个事件的专属字段 | [`telemetry.ts`](../../../openclaw/observ-openclaw/src/telemetry.ts) |
| raw callback envelope | raw-only | 插件 replay 显式启用后可记录 raw input/context/result；生产 LogRecord 不直接上报 raw envelope | [`AGENTS.md`](../../../AGENTS.md) |

## 公共事件属性

`emitEvent()` 总是把事件名写入 LogRecord attributes 与 body，并清理旧式 body 别名。`withCommonEventAttributes()` 还会把 Resource/上下文中的公共 agent 属性合并到 LogRecord attributes。

| 字段 | 类型 | 状态/条件 | 含义 | 隐私 |
|---|---|---|---|---|
| `event.name` | string | 当前上报 | `session_start` 等事件名 | 低风险 |
| `event.sequence` | number | 当前上报 | session 内递增事件序号 | 关联键 |
| `gen_ai.conversation.id` | string | 当前上报 | OpenClaw session id；缺失时回退 session key | 会话关联 |
| `gen_ai.chat.id` | string | 当前上报；部分 compaction 缺失 | turn/prompt id；session lifecycle 可回退 conversation id | 高基数关联键 |
| `gen_ai.operation.name` | string | 当前上报 | `chat`、`execute_tool`、`invoke_agent` | 低风险 |
| `agentic.turn.number` | number | 当前上报 | turn 序号；部分 LLM/compaction event 使用 event sequence | 低风险 |
| `agentic.runtime.name` | string | 当前上报 | 固定为 `openclaw` | 低风险 |
| `ant.username` | string | 当前上报 | 当前解析出的用户名，缺失时多处写 `unknown` | 用户身份 |
| `ant.agent.type` / `ant.agent.product` | string | 当前上报 | logger common attributes 或 compaction call site 注入 | 低风险 |

## 事件目录

| 事件 | 状态/触发 | 增量字段 | 配置、隐私与边界 | 代码来源 |
|---|---|---|---|---|
| `session_start` | 当前上报；session_start hook 且 events enabled | 可选 `gen_ai.session.id` | body 只含 `event.name`；关联到 active turn span 或 retained context | [`hooks/session-lifecycle.ts`](../../../openclaw/observ-openclaw/src/hooks/session-lifecycle.ts) |
| `session_end` | 当前上报；session_end hook 且 events enabled | 可选 `gen_ai.session.id`；`agentic.turn.number` 为 turn count | 结束后清理 session state；chat id 可来自 active turn、last chat id 或 conversation id | [`hooks/session-lifecycle.ts`](../../../openclaw/observ-openclaw/src/hooks/session-lifecycle.ts) |
| `user_prompt` | 条件上报；before_model_resolve 创建 turn，且 prompt 存在 | `gen_ai.input.messages` | `config.log.events.userPrompts` 为 true 时写 OpenAI-compatible user message JSON；否则 `[REDACTED]` | [`hooks/session.ts`](../../../openclaw/observ-openclaw/src/hooks/session.ts) |
| `api_request` | 当前上报；LLM 成功、LLM 错误或 fallback request 结束 | `gen_ai.request.model`、`gen_ai.response.model`、`gen_ai.provider.name`、`duration_ms`、token usage、`gen_ai.input.messages`、`gen_ai.output.messages`、`gen_ai.system_instructions`、错误字段 | 成功和错误共用单事件模型；错误时加 `error.type`、`error.message`、`agentic.error.category`，privacy/fallback 分支会脱敏错误或内容 | [`hooks/llm.ts`](../../../openclaw/observ-openclaw/src/hooks/llm.ts) |
| `tool_call` | 当前上报；before_tool_call 创建 TOOL span | `gen_ai.tool.name`、`gen_ai.tool.call.id`、`gen_ai.tool.call.arguments`、`agentic.tool.type/name/raw_name/id` | arguments 受 `config.log.events.toolDetails` 控制；工具分类来自 `classifyTool()` | [`hooks/tool.ts`](../../../openclaw/observ-openclaw/src/hooks/tool.ts)、[`semconv.ts`](../../../openclaw/observ-openclaw/src/semconv.ts) |
| `tool_result` | 当前上报；tool_result_persist 完成 TOOL span | `gen_ai.tool.name`、`gen_ai.tool.call.id`、`duration_ms`、`agentic.tool.*`、`gen_ai.tool.call.arguments`、`gen_ai.tool.call.result`、可选错误字段 | arguments 受 `toolDetails` 控制；result 受 `toolOutputs` 控制；失败时 `error.type=tool_error` | [`hooks/tool.ts`](../../../openclaw/observ-openclaw/src/hooks/tool.ts) |
| `subagent_spawned` | 当前上报；subagent_spawned hook 有 active parent turn | `gen_ai.agent.id/name`、`agentic.subagent.session_key`、parent/child thread、name、mode、可选 run id | 当前实现将前台 subagent 表示为 sync `AGENT` span；事件关联该 span | [`hooks/subagent.ts`](../../../openclaw/observ-openclaw/src/hooks/subagent.ts) |
| `subagent_ended` | 当前上报；subagent_ended hook 找到 pending/relation span | `gen_ai.agent.id/name`、`duration_ms`、`agentic.subagent.*` outcome/reason/target kind/run id、可选错误字段 | result 内容只写在 span attribute，事件本身不带 result；错误 message 截断到 500 字符 | [`hooks/subagent.ts`](../../../openclaw/observ-openclaw/src/hooks/subagent.ts) |
| `compaction_result` | 条件上报；after_compaction 完成 compaction TOOL span | `gen_ai.tool.name=compaction`、`agentic.tool.type=system`、`duration_ms`、tokens before/after/saved、messages after、可选错误字段 | token/message 字段只在 hook payload/pre-state 有值时出现；失败用 Error severity | [`hooks/compaction.ts`](../../../openclaw/observ-openclaw/src/hooks/compaction.ts) |

## raw-only 与不可用项目

| 项目 | 状态 | 说明 |
|---|---|---|
| raw callback `event` / `ctx` / output / error envelope | raw-only | 只属于显式启用的 replay recorder 输入；生产事件只上报 normalization 后字段 |
| `tool_decision` | 已定义未触发 | `semconv.ts` 有常量和 emitter wrapper，但当前生产 hook 没有调用证据 |
| 旧式事件时间字段 | 不可用 | 当前生产 event call site 没有设置业务时间属性；OTLP LogRecord 自身时间不等同于业务字段 |
| 旧式 prompt id 字段 | 不可用 | 当前使用 `gen_ai.chat.id` 和 `agentic.turn.number` 关联 prompt/turn |
| 旧字段表中的未证明字段 | 不可用 | 不从 `openclaw/observ-openclaw/EVENTS.md` 继承字段；以生产 emitter call site 为准 |
