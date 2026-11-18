# OpenCode observ-opencode 轨迹字段

## 范围

本文是本仓库 `observ-opencode` 生产 trace 的 canonical 字段说明，依据为 [semconv.ts](../../../opencode/observ-opencode/src/semconv.ts)、[telemetry.ts](../../../opencode/observ-opencode/src/telemetry.ts)、[hooks/index.ts](../../../opencode/observ-opencode/src/hooks/index.ts)、[hooks/message.ts](../../../opencode/observ-opencode/src/hooks/message.ts)、[hooks/tool.ts](../../../opencode/observ-opencode/src/hooks/tool.ts) 与 [hooks/session.ts](../../../opencode/observ-opencode/src/hooks/session.ts)。它不包含 [OpenCode 事件字段](./events.md) 或 Metrics。

状态均为 `当前上报` 或明确的 `条件上报`。生产事实来自 OpenCode hook/event payload 与运行时状态；replay 只作为离线复核输入。

## 拓扑

```text
普通 turn：
CHAT / Turn
├── LLM generation
├── TOOL execution
├── CLIENT api_error（session.error 分支）
└── COMPACTION system tool（compacting/compacted，通常关联最近 turn context）

task 子 agent：
父 CHAT / Turn
└── AGENT / SyncAgent（在子会话中复用为 child turn root）
    ├── LLM generation
    └── TOOL execution
```

普通 turn 以 CHAT span 为根，LLM、TOOL、CLIENT 是同一 Turn 下的兄弟语义节点，不应把 TOOL 写成 LLM 的子 span。OpenCode `task` 调用会先创建 AGENT/subagent span；子会话开始真实 turn 时会复用这个 AGENT span 作为 child turn root，而不是再把 AGENT 画成普通 CHAT 的固定子节点。普通 builtin/MCP/Skill 工具使用 TOOL span。`session.idle` 会结束当前 turn，但不代表整个进程生命周期结束。

只有普通、由 OpenCode 拥有的用户 Turn 写 `_internal.meta.root="1"`。上游 OTel parent 不会抑制该逻辑标记；复用的 subagent AGENT span、LLM、TOOL、CLIENT、COMPACTION、event 和 metric 均不写该字段。

## Resource attributes

| 字段 | 类型 | 状态/条件 | 来源与含义 | 隐私 |
|---|---|---|---|---|
| `service.name` | string | 当前上报 | `serviceName` / `OTEL_SERVICE_NAME`，默认 `opencode` | 低风险 |
| `service.version` | string | 当前上报 | 构建注入版本或 package version | 低风险 |
| `agentic.collection.mode` | string | 当前上报 | `full` 或 `privacy` | 安全控制信息 |
| `ant.agent.type` | string | 当前上报 | Resource 配置，默认 `opencode` | 低风险 |
| `ant.agent.product` | string | 当前上报 | Resource 配置，默认 `OpenCode` | 低风险 |
| `ant.agent.instance` | string | 当前上报 | Resource 配置或 hostname | 环境指纹 |
| `ant.agent.env` / `deployment.environment` | string | 当前上报 | Resource 配置，默认 `Default` | 部署信息 |
| `ant.agent.scene` | string | 当前上报 | Resource 配置，默认 `default` | 部署信息 |
| `ant.meta.trigger` | string | 当前上报 | 外部调用方通过最终合并的 `otelExporter.resourceAttributes` 配置，或由最高优先级 `AIVISION_META_TRIGGER` 覆盖；未显式提供时为空字符串，显式非法值为 `unknown`，不从 OpenCode 或 replay transcript 推断 | 任务归因 |
| `vcs.repository.url.full` / `vcs.ref.head.name` / `vcs.ref.head.revision` | string | Git worktree 可用且事实非空时 | OpenCode host worktree 上的 Git remote、HEAD ref 与可选 revision | 仓库/分支敏感；条件上报 |
| 自定义 Resource keys | scalar string | 显式配置 | edition/config/env resource attributes；`ant.username` / `ant.uid` 被移出 Resource | 部署方负责 |

`ant.meta.trigger` 未配置时输出空字符串，不会默认补 `unknown`；只有显式空值或非法值才规范化为 `unknown`。同一规范化后的 Resource 同时交给 traces、metrics 和 events/logs provider。该字段不投影到 Turn 或子 span，因此 OpenCode 不生成 `agentic.turn.trigger`。

## 所有 span 的语义与关联字段

| 字段 | 类型 | 状态/条件 | 适用 span | 来源/含义 | 隐私 |
|---|---|---|---|---|---|
| `gen_ai.span.kind` | string | 当前上报 | 全部业务 span | `CHAT` / `LLM` / `TOOL` / `AGENT` / `CLIENT` / `COMPACTION` | 低风险 |
| `gen_ai.operation.name` | string | 当前上报 | 全部业务 span | `invoke_agent`、`chat`、`execute_tool` | 低风险 |
| `gen_ai.conversation.id` | string | 当前上报 | Turn 及子 span | OpenCode session id | 高基数关联键 |
| `gen_ai.chat.id` | string | 当前上报；compaction 可能取最近 turn | Turn 及子 span | observ-opencode turn id | 高基数关联键 |
| `agentic.runtime.name` | string | 当前上报 | Turn、events；部分子 span | 固定 `opencode` | 低风险 |
| `agentic.turn.id` | string | 当前上报 | Turn | 与 `gen_ai.chat.id` 相同的 turn id | 高基数关联键 |
| `agentic.turn.number` | int | 当前上报 | Turn、events | 会话内 turn 序号 | 低风险 |
| `ant.username` / `user.id` | string | 当前上报 | Turn 及子 span | signal scoped 用户名 | 用户身份 |
| `langfuse.trace.tags` | string[] | skill/MCP 工具存在且非 subagent turn | Turn | `skill:<name>` / `mcp:<name>` 标签集合 | 可能暴露工具/技能名 |
| `error.type` / `error.message` | string | 错误分支 | LLM、TOOL、CLIENT | API、network、tool 错误 | message 可能敏感，privacy 模式下会脱敏 |
| `TRACEPARENT`（不作为 span 属性） | raw callback context | 当前 host 提供外部上游 context 时 | 本版 OpenCode contract 未暴露该 source；不得从 `context.active()` 推断 | 跨进程关联信息 |

## CHAT / Turn

| 字段 | 类型 | 条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `_internal.meta.root` | string | 普通 owned Turn 固定为 `"1"` | 下游 trace 列表查询使用的逻辑 Turn 根；不表示物理 OTel root | 内部索引元数据 |
| `gen_ai.input.messages` | JSON string 或 `[REDACTED]` | turn 结束时总是设置 | 用户视角输入，只含当前 user message | prompt 敏感；privacy 模式脱敏 |
| `gen_ai.output.messages` | JSON string 或 `[REDACTED]` | turn 结束时总是设置 | 用户视角最终 assistant 输出；可包含最后一轮 tool_calls/reasoning | response 敏感；privacy 模式脱敏 |
| `gen_ai.system_instructions` | JSON string 或 `[REDACTED]` | turn 结束时总是设置 | `experimental.chat.system.transform` 捕获的 system segments | system prompt 敏感 |
| `gen_ai.response.model` | string | assistant model 可用 | 最近 assistant message model | 模型路由信息 |
| `gen_ai.response.finish_reasons` | JSON string array | finish reason 可用 | 最近 LLM finish reason | 低风险 |
| `langfuse.trace.tags` | string[] | tool 分类产生 tags | Turn-level trace tags | 可能暴露工具/技能名 |

Turn root 不写 aggregate token，避免与单次 LLM 调用 token 双计数。Tool 错误只标记 tool span；turn span 默认 OK。

## LLM

| 字段 | 类型 | 条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `gen_ai.request.model` / `gen_ai.response.model` | string | assistant message 可用 | OpenCode `modelID` | 模型路由信息 |
| `gen_ai.provider.name` | string | provider 可解析时 | model/provider 解析 | 低风险 |
| `gen_ai.input.messages` | JSON string 或 `[REDACTED]` | LLM span 创建/错误诊断 | 当前 generation 输入窗口 | prompt/history 敏感 |
| `gen_ai.output.messages` | JSON string 或 `[REDACTED]` | LLM span close 且输出可构造 | assistant message，含 finish reason、tool_calls、reasoning | response 敏感 |
| `gen_ai.usage.input_tokens` / `.output_tokens` / `.total_tokens` | int | token 可用 | 单次 assistant message；input 按 OpenAI convention 包含 cache | 使用量信息 |
| `gen_ai.usage.cache_read.input_tokens` / `.cache_creation.input_tokens` | int | cache token 可用 | cache read/write token | 使用量信息 |
| `gen_ai.usage.reasoning_tokens` | int | reasoning token 可用 | reasoning token | 使用量信息 |
| `agentic.llm.duration_ms` | number | completed/created time 可用 | LLM 耗时 | 性能信息 |
| `gen_ai.response.cost_usd` | number | cost > 0 | OpenCode cost | 成本信息 |
| `agentic.tool.count` | int | LLM span close | 当前 assistant response 内 tool call 数 | 低风险 |
| `gen_ai.response.finish_reasons` | JSON string array | LLM span close | `tool_use` 或 `end_turn` 等 | 低风险 |
| `error.type` / `error.message` / `agentic.llm.error.status_code` | string/int | API/network error | assistant error 或 zero-token network failure | 错误信息可能敏感 |

## TOOL / AGENT / COMPACTION

| 字段 | 类型 | 条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `gen_ai.tool.name` / `gen_ai.tool.call.id` | string | TOOL/COMPACTION；AGENT 也保留 call id | 原始工具名与 callID | 工具身份/关联键 |
| `gen_ai.tool.call.arguments` | JSON string 或 `[REDACTED]` | tool span 创建时设置 | tool args snapshot；span/event 各自按配置导出 | 参数高敏感 |
| `gen_ai.tool.call.result` | string 或 `[REDACTED]` | tool span 创建/结束时设置 | tool output；error 时可写错误消息 | 结果高敏感 |
| `agentic.tool.type` / `.name` / `.raw_name` / `.id` | string | tool 分类 | `builtin` / `mcp` / `skill` / `subagent` / `system` | 工具身份 |
| `agentic.tool.duration_ms` | number | 普通 tool/compaction | tool start/end 或 fallback wall time | 性能信息 |
| `agentic.tool.input.truncated` / `.input.original_length` | bool/int | args snapshot 截断时 | 输入截断元数据 | 内容规模 |
| `agentic.tool.output.truncated` / `.output.original_length` | bool/int | output 截断时 | 输出截断元数据 | 内容规模 |
| `agentic.tool.parallel_group` | string | 并发 tool 检测到时 | 同一 response 内并发分组 | 关联键 |
| `code.tool.exit_code` | int | command metadata exit 可用 | 非零 exit code | 运行状态 |
| `gen_ai.agent.id` / `.name` | string | `task` subagent | SyncAgent id/name | 子任务关联 |
| `agentic.subagent.mode` / `.depth` / `.parent_thread_id` / `.call_id` / `.status` / `.duration_ms` / `.name` | string/int/number | `task` subagent | 同步子 agent 元数据 | 名称/状态可能暴露任务语义 |
| `agentic.compaction.trigger` / `.sequence` | string/int | compaction close | compaction 触发与序号 | 低风险 |

Tool 分类规则来自 `classifyTool()`：`task` -> `subagent`，`Skill` -> `skill`，已知 OpenCode builtin -> `builtin`，`mcp__server__tool` 或未知 OpenCode tool -> `mcp`。

## CLIENT api_error

| 字段 | 类型 | 条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `gen_ai.span.kind` | string | `session.error` 分支 | `CLIENT` | 低风险 |
| `gen_ai.operation.name` | string | 当前上报 | `chat` | 低风险 |
| `gen_ai.request.model` / `gen_ai.response.model` | string | model 可推断时 | request model | 模型路由信息 |
| `gen_ai.input.messages` | JSON string 或 `[REDACTED]` | diagnostic input 可用 | 当前 generation 或 pending retry input | prompt/history 敏感 |
| `agentic.llm.error.type` / `.message` / `.status_code` | string/int | API error | 错误分类、消息、HTTP status | message 可能敏感 |
| `error.type` | string | 当前上报 | OTel 通用错误类型 | 错误信息 |

## 已知边界与验证

- traces signal 由 `enableTelemetry` 和 trace endpoint/config 决定；没有 trace provider 时 span 不会上报。
- `_internal.meta.root` 是仓库保留字段；`otelExporter.attributes` 和 `OBSERV_OPENCODE_OTEL_ATTRIBUTES` 中的同名键会被忽略，其他 `_internal.*` 自定义键不受影响。
- `collectionMode=privacy` 只影响内容字段脱敏，不影响拓扑、token、duration、id、status 或 tool 分类。
- `tool.execute.after` 可在缺 `tool.execute.before` 的 OpenCode 版本中补建 terminal TOOL span，但不会补造历史 start event。
- `message.part.updated(type=tool,status=error)` 能创建/关闭错误 TOOL span；subagent 错误仍保持 AGENT 语义。
- `experimental.session.compacting` 先建 `COMPACTION` span，`session.compacted` 再关闭并补 duration/sequence；当前不记录 compaction 前后 token。
- 共享字段规则参见 [`docs/semconv/spans.yaml`](../../semconv/spans.yaml) 与 [`attribute_groups.yaml`](../../semconv/attribute_groups.yaml)；是否当前 emit 仍以 `observ-opencode` 生产代码为准。
