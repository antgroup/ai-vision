# OpenCode observ-opencode 事件字段

## 范围与证据

本文是本仓库 `observ-opencode` 生产 LogRecord 事件的 canonical 字段说明。事实来源是生产 emitter 定义与 call site：[semconv.ts](../../../opencode/observ-opencode/src/semconv.ts)、[hooks/message.ts](../../../opencode/observ-opencode/src/hooks/message.ts)、[hooks/tool.ts](../../../opencode/observ-opencode/src/hooks/tool.ts)、[hooks/index.ts](../../../opencode/observ-opencode/src/hooks/index.ts)、[hooks/session.ts](../../../opencode/observ-opencode/src/hooks/session.ts)。

状态：`当前上报` 表示有生产 emitter 调用证据；`已定义未触发` 表示只在 semconv 中定义常量/函数但当前没有 call site；`条件上报` 表示还需配置、版本或运行分支。Metrics 和 trace span 字段不在本文范围。

## 全局上报条件

事件属于 OTel Logs signal。必须先启用 `enableTelemetry`，并且 events/logs signal 有可用 processor/exporter，运行时 `ctx.runtime.eventsEnabled` 才为 true。事件字段会通过 `emitEvent()` 同时放入 body 的 `event.name` 和 attributes；LogRecord context 来自 turn、LLM、tool 或 compaction span context，因此可与对应 trace 关联。

内容字段由 `collectionMode` 派生：`full` 时 user/system/model/tool 内容开关全部开启；`privacy` 时全部关闭，相关内容字段仍保留但写为 `[REDACTED]`。旧 `AGENT_LOG_*` 内容开关已被忽略。

## 公共事件字段

| 字段 | 类型 | 状态/条件 | 含义 | 隐私 |
|---|---|---|---|---|
| `event.name` | string | 当前上报 | 事件名；body 和 attributes 均包含 | 低风险 |
| `gen_ai.operation.name` | string | 当前上报 | `chat` 或 `execute_tool` | 低风险 |
| `ant.username` | string | 当前上报 | signal scoped 用户名；来自配置/动态解析，默认 `opencode` | 用户身份 |
| `gen_ai.conversation.id` | string | 当前上报 | OpenCode session id | 高基数关联键 |
| `gen_ai.chat.id` | string | turn 可用时 | observ-opencode turn id；`session_end` 缺 chat id 时跳过事件 | 高基数关联键 |
| `agentic.runtime.name` | string | 当前上报 | 固定 `opencode` | 低风险 |
| `agentic.turn.number` | int | 当前上报 | 会话内 turn 序号 | 低风险 |
| `event.sequence` | int | 当前上报 | session 内单调递增事件序号；缺 session state 的 fallback 为 0 | 排序关联键 |

## 当前上报事件目录

| 事件 | 状态/触发 | 增量字段 | 配置、版本与隐私 | 代码来源 |
|---|---|---|---|---|
| `session_start` | 当前上报；首个真实 user turn span 创建后，仅每 session 一次 | `gen_ai.session.id` | 依赖 events signal；推迟到 turn span 存在后发送，保证 trace/span context | [hooks/message.ts](../../../opencode/observ-opencode/src/hooks/message.ts) |
| `user_prompt` | 当前上报；user message 内容已捕获且同 turn 未发过 | `gen_ai.input.messages` | `full` 写 OpenAI-compatible user message array；`privacy` 写 `[REDACTED]`；内容未捕获时不提前发 | [hooks/message.ts](../../../opencode/observ-opencode/src/hooks/message.ts) |
| `api_request` | 当前上报；assistant message token 完成后每条 assistant message 去重发送；也覆盖 API/network error 分支 | `gen_ai.request.model`、`duration_ms`、`gen_ai.response.model`、token fields、provider、cache/reasoning/raw token breakdown、`gen_ai.input.messages`、`gen_ai.system_instructions`、`gen_ai.output.messages`、条件 `error.type` / `error.message` / `agentic.llm.error.status_code` | 内容字段受 `collectionMode` gate；token event 等待 `time.completed` 避免重复和 0 duration；zero-token network/API error 与 `session.error` 会发错误型 `api_request` | [hooks/message.ts](../../../opencode/observ-opencode/src/hooks/message.ts)、[hooks/index.ts](../../../opencode/observ-opencode/src/hooks/index.ts) |
| `tool_call` | 当前上报；`tool.execute.before` 且非 `task` subagent | `gen_ai.tool.name`、`gen_ai.tool.call.id`、`gen_ai.tool.call.arguments`、`agentic.tool.type`、`agentic.tool.name`、`agentic.tool.id`、`agentic.tool.raw_name` | `task` 被建模为 AGENT/subagent，不发 tool_call；arguments 在 `full` 且 tool details 可用时写入，否则 `[REDACTED]`；部分 OpenCode 版本缺 `tool.execute.before` 时不会补造历史 tool_call 事件 | [hooks/tool.ts](../../../opencode/observ-opencode/src/hooks/tool.ts) |
| `tool_result` | 当前上报；`tool.execute.after` 终态，或 message part `status=error` 且非 `task` | `gen_ai.tool.name`、`duration_ms`、条件 `error.type` / `error.message`、tool classification、`gen_ai.tool.call.id`、`gen_ai.tool.call.arguments`、`gen_ai.tool.call.result` | result 只在 `full` 且 tool output 可用时写入，否则 `[REDACTED]`；error message 在 privacy 模式下脱敏；`task` subagent 结果不作为 TOOL event 上报 | [hooks/tool.ts](../../../opencode/observ-opencode/src/hooks/tool.ts)、[hooks/message.ts](../../../opencode/observ-opencode/src/hooks/message.ts) |
| `session_end` | 当前上报；OpenCode `session.idle`，关闭当前 turn 后发送 | `gen_ai.session.id` | OpenCode 每 turn 后都会 `session.idle`，所以这是 idle/turn-end 语义，不是进程最终退出；没有 chat id 时跳过 | [hooks/session.ts](../../../opencode/observ-opencode/src/hooks/session.ts) |
| `compaction_result` | 条件上报；`experimental.session.compacting` 已创建 compaction span，随后 `session.compacted` 关闭 | `gen_ai.tool.name=compaction`、`agentic.tool.type=system`、`duration_ms` | 需要 traces signal 先创建 compaction span，events signal 再发结果；当前字段不包含 compaction 前后 token | [hooks/index.ts](../../../opencode/observ-opencode/src/hooks/index.ts) |

## 定义但当前未上报

| 事件 | 状态 | 说明 | 代码来源 |
|---|---|---|---|
| `tool_decision` | 已定义未触发 | `EventAgentToolDecisionEventName` 和 `emitEventAgentToolDecisionEvent()` 存在，但当前 hooks 中没有调用点；不能把它列为生产事件 | [semconv.ts](../../../opencode/observ-opencode/src/semconv.ts) |

## 条件、fallback、raw-only 与不可用边界

- API error 不是独立事件名；当前统一复用 `api_request`，并补 `error.type` / `error.message`。
- `tool.execute.after` fallback 会补建 TOOL span 和 `tool_result`，但不会补造缺失的 `tool_call` start event。
- `message.part.updated(type=tool,status=error)` 可在没有 after callback 时关闭错误 tool span 并发 `tool_result`。
- OpenCode `task` 工具归类为 `subagent`/AGENT span；当前不发 `tool_call` / `tool_result` 事件，避免把子 agent 误分类为普通 TOOL。
- `collectionMode=privacy` 不改变事件生命周期、token、duration、id 和分类字段，只脱敏 prompt、system、model output、tool args/result 等内容字段。
- 生产插件忽略 legacy replay/local settings；离线 raw callback envelope 属于 `opencode-replay`，不是 `observ-opencode` 生产事件。
- 当前没有独立的 raw request/response body 事件、permission/tool decision 事件、auth 事件、MCP 连接事件或 plugin lifecycle 事件。
