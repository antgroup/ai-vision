# OpenClaw observ-openclaw 轨迹字段

## 范围

本文是本仓库 `observ-openclaw` 生产 trace 的字段说明，以 [`semconv.ts`](../../../openclaw/observ-openclaw/src/semconv.ts)、telemetry resource builder 与 hook span call site 为准。它不包含 [事件 LogRecord](./events.md)、Metrics 或 replay raw callback envelope。

状态均为 `当前上报` 或明确的 `条件上报`。生产事实来自 OpenClaw hook event/context；replay 只作为离线复核输入。

## 拓扑

```text
CHAT Turn
├── LLM generation
├── TOOL execution
├── TOOL compaction（条件：compaction hook）
└── AGENT SyncAgent（条件：subagent delegation）
```

Turn span 由 before_model_resolve 创建；普通 `before_agent_start` 只记录 agent metadata，不创建 AGENT span。只有 `subagent_spawned` 找到 active parent turn 时，才在该 Turn 下创建 SyncAgent AGENT span。LLM、普通 TOOL、条件 compaction TOOL，以及条件 AGENT 都是 Turn 的子 span。Between-turn compaction 可创建 `Turn (compaction)` synthetic CHAT span，再把 compaction TOOL 挂在该 synthetic turn 下。

只有 `before_model_resolve` 创建且由 OpenClaw 拥有的真实用户 Turn 写 `_internal.meta.root="1"`。diagnostic upstream OTel parent 不会抑制该标记；child session 借用的 SyncAgent AGENT span 和 `Turn (compaction)` synthetic CHAT span 明确不标记。

Span helper 固定写入 `gen_ai.span.kind`：CHAT span 是 `SpanKind.SERVER`，LLM/AGENT 是 `SpanKind.CLIENT`，TOOL 是 `SpanKind.INTERNAL`。

## Resource attributes

| 字段 | 类型 | 状态/条件 | 来源与含义 | 隐私 |
|---|---|---|---|---|
| `service.name` | string | 当前上报 | `serviceName` 配置、`OTEL_SERVICE_NAME` 或默认 `openclaw` | 低风险 |
| `service.version` | string | 当前上报 | 插件版本 `PLUGIN_VERSION` | 低风险 |
| `telemetry.sdk.language` / `name` / `version` | string | 当前上报 | `nodejs`、`observ-openclaw`、插件版本 | 低风险 |
| `ant.agent.type` / `ant.agent.product` | string | 当前上报 | resourceAttributes 或默认 `openclaw` / `OpenClaw` | 低风险 |
| `ant.agent.instance` | string | 当前上报 | resource override、hostname 或 `unknown` | 环境指纹 |
| `ant.agent.env` / `ant.agent.scene` | string | 当前上报 | resource override 或默认 `Default` / `default` | 部署信息 |
| `deployment.environment` / `.name` | string | 当前上报 | 跟随 `ant.agent.env` | 部署信息 |
| `agentic.collection.mode` | string | 当前上报 | `full` 或 `privacy` | 安全控制信息 |
| 自定义 resource keys | scalar | 显式配置 | edition config、`otelExporter.resourceAttributes`、`OTEL_RESOURCE_ATTRIBUTES`、`AIVISION_OTEL_RESOURCE_ATTRIBUTES`、`OBSERV_OPENCLAW_OTEL_RESOURCE_ATTRIBUTES` | 部署方负责 |

Resource builder 会删除 `ant.username`、`ant.uid` 和裸 `username`；用户身份属于 span/event 级字段，不属于 Resource。

## 所有 span 的公共字段

| 字段 | 类型 | 状态/条件 | 适用 span | 来源/含义 | 隐私 |
|---|---|---|---|---|---|
| `gen_ai.span.kind` | string | 当前上报 | 全部 helper span | `CHAT` / `LLM` / `TOOL` / `AGENT` | 低风险 |
| `gen_ai.operation.name` | string | 当前上报 | 全部业务 span | `invoke_agent`、`chat`、`execute_tool` | 低风险 |
| `gen_ai.conversation.id` | string | 当前上报 | Turn/LLM/TOOL/AGENT | session id；缺失时回退 session key | 会话关联 |
| `gen_ai.chat.id` | string | 当前上报；部分 synthetic compaction 缺失 | Turn/LLM/TOOL/AGENT | prompt/turn id | 高基数关联键 |
| `agentic.runtime.name` | string | 当前上报 | Turn/事件相关 span；LLM/TOOL 也可由 common span attributes 合并 | `openclaw` | 低风险 |
| `agentic.turn.number` | number | 当前上报 | Turn/子 span | session turn counter 或 synthetic turn number | 低风险 |
| `ant.username` / `user.id` | string | 当前上报 | Turn/LLM/TOOL/AGENT | resolved username；缺失时 `unknown` | 用户身份 |
| `ant.uid` | string | 条件上报 | Turn/LLM | dynamic uid 可用时 | 用户身份 |
| `_internal.sofa.trace_id` / `.span_id` / `.request_id` | string | 条件上报 | Turn | upstream SOFA trace context | 内部关联信息 |
| `_internal.span_link.role` | string | 条件上报 | Turn | SOFA span link role | 内部关联信息 |

## CHAT / Turn span

| 字段 | 类型 | 状态/条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `_internal.meta.root` | string | owned real Turn 固定为 `"1"` | 下游 trace 列表查询使用的逻辑 Turn 根；有 upstream parent 时仍保留 | 内部索引元数据 |
| `gen_ai.input.messages` | JSON string 或 `[REDACTED]` | 当前上报 | before_model_resolve prompt；`config.log.spans.userPrompts` 控制内容 | prompt 敏感 |
| `gen_ai.output.messages` | JSON string 或 `[REDACTED]` | 条件上报 | assistant output 归并到 turn span | response 敏感 |
| `gen_ai.response.model` | string | 条件上报 | LLM output 后写入 turn span | 模型路由 |
| `gen_ai.response.finish_reasons` | JSON string | 条件上报 | assistant stop reason | 低风险 |
| `gen_ai.agent.name` | string | 条件上报 | agent id 或 synthetic compaction turn 的 agent name | 低风险 |
| `agentic.session.missing` | bool | 条件上报 | framework 未提供 session key 时 | 数据质量标识 |
| `agentic.turn.synthetic` | bool | 条件上报 | between-turn compaction synthetic turn | 低风险 |
| `gen_ai.system_instructions` | JSON string 或 `[REDACTED]` | 条件上报 | prompt/system instruction hook | system prompt 敏感 |
| `agentic.output.boundary.inferred` | bool | 条件上报 | 输出边界由代码推断 | 数据质量标识 |
| `langfuse.trace.tags` | string[] | 条件上报 | tool/skill trace tag 聚合 | 可能暴露工具/skill 名 |

## LLM span

| 字段 | 类型 | 状态/条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `gen_ai.request.model` / `gen_ai.response.model` | string | 当前上报；模型可能缺失 | request 与 response model | 模型路由 |
| `gen_ai.provider.name` | string | 当前上报 | provider；缺失时可为 `unknown` | 低风险 |
| `gen_ai.response.id` | string | 条件上报 | assistant message id | 高基数关联键 |
| `gen_ai.response.finish_reasons` | JSON string | 条件上报 | stop reason | 低风险 |
| `gen_ai.usage.input_tokens` / `.output_tokens` / `.total_tokens` | int | 条件上报 | 单次 LLM usage | 使用量信息 |
| `gen_ai.usage.cache_read.input_tokens` / `.cache_creation.input_tokens` | int | 条件上报 | cache token usage | 使用量信息 |
| `gen_ai.input.messages` / `gen_ai.output.messages` | JSON string 或 `[REDACTED]` | 条件上报 | model input/output；受 `config.log.spans.*` 控制 | prompt/response 敏感 |
| `gen_ai.system_instructions` | JSON string 或 `[REDACTED]` | 条件上报 | system prompt snapshot | system prompt 敏感 |
| `agentic.llm.duration_ms` / `.ttfb_ms` | number | 条件上报 | LLM duration / first token timing | 性能信息 |
| `agentic.model_call.id` | string | 条件上报 | model-call hook id | 高基数关联键 |
| `agentic.model.missing` | bool | 条件上报 | model 不可解析 | 数据质量标识 |
| `error.type` / `error.message` | string | 条件上报 | LLM error | message 可能敏感，privacy 分支脱敏 |
| `agentic.llm.error.type` / `.message` / `agentic.error.category` | string | 条件上报 | normalized error details | 错误信息 |

## TOOL span

| 字段 | 类型 | 状态/条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `gen_ai.tool.name` / `gen_ai.tool.call.id` | string | 当前上报 | raw tool name 与 call id | 工具身份/关联键 |
| `gen_ai.tool.call.arguments` | JSON string 或 `[REDACTED]` | 当前上报 | tool input；`config.log.spans.toolDetails` 控制内容 | 高敏感 |
| `gen_ai.tool.call.result` | string 或 `[REDACTED]` | 条件上报 | tool output；`config.log.spans.toolOutputs` 控制内容 | 高敏感 |
| `agentic.tool.type` / `.name` / `.raw_name` / `.id` | string | 当前上报 | `classifyTool()` 归类：`builtin`、`mcp`、`skill`；compaction 为 `system` | 工具身份 |
| `agentic.tool.call_id.synthetic` | bool | 条件上报 | call id 自动生成时 | 数据质量标识 |
| `agentic.tool.input.truncated` / `.input.original_length` | bool/int | 条件上报 | input 截断元数据 | 内容规模 |
| `agentic.tool.output.truncated` / `.output.original_length` | bool/int | 条件上报 | output 截断元数据 | 内容规模 |
| `agentic.tool.parallel_group` | string | 条件上报 | 多个 active tool 并行时分组 | 关联键 |
| `agentic.tool.duration_ms` | number | 条件上报 | after_tool_call duration | 性能信息 |
| `error.type` / `error.message` | string | 条件上报 | tool error | message 可能敏感 |

## AGENT / SyncAgent span

| 字段 | 类型 | 状态/条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `gen_ai.agent.id` / `.name` | string | 当前上报 | child session key 与 `SyncAgent` | 子会话关联 |
| `agentic.tool.type/name/id` | string | 当前上报 | subagent 作为 agent/tool 语义桥接 | 任务身份 |
| `gen_ai.tool.call.id` | string | 当前上报 | run id 或 child session key | 高基数关联键 |
| `agentic.subagent.session_key` | string | 当前上报 | child session key | 会话关联 |
| `agentic.subagent.parent_thread_id` / `.child_thread_id` | string | 当前上报 | parent/child thread 关联 | 会话关联 |
| `agentic.subagent.name` / `.label` / `.mode` | string | 当前/条件上报 | subagent 名称、label、固定 sync mode | 可能暴露任务类型 |
| `agentic.subagent.run_id` | string | 条件上报 | framework run id | 高基数关联键 |
| `agentic.subagent.description` | string | 条件上报 | description/prompt；`config.log.spans.toolDetails` 控制 | 任务内容敏感 |
| `agentic.subagent.outcome` / `.reason` / `.target_kind` | string | 条件上报 | subagent 结束状态 | 运行状态 |
| `agentic.subagent.duration_ms` | number | 当前上报；结束时 | event duration 或本地 elapsed | 性能信息 |
| `agentic.subagent.result` | string | 条件上报 | result/output；`config.log.spans.toolOutputs` 控制 | 输出敏感 |
| `error.type` / `error.message` / `agentic.subagent.error` | string | 条件上报 | subagent error | 错误信息 |

## Compaction TOOL span

| 字段 | 类型 | 状态/条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `gen_ai.tool.name` | string | 当前上报 | 固定 `compaction` | 低风险 |
| `agentic.tool.type/name/id` | string | 当前上报 | 固定 `system` / `compaction` | 低风险 |
| `agentic.compaction.messages_before` / `.messages_after` / `.messages_compacted` | int | 条件上报 | before/after compaction payload | 使用规模 |
| `agentic.compaction.tokens_before` / `.tokens_after` / `.tokens_saved` | int | 条件上报 | token count delta | 使用量信息 |
| `agentic.compaction.reason` | string | 条件上报 | before_compaction reason | 低风险 |
| `duration_ms` | number | 条件上报 | elapsed compaction time | 性能信息 |
| `agentic.compaction.error` / `error.type` / `error.message` | string | 条件上报 | compaction failure | 错误信息 |
| `gen_ai.tool.call.result` | JSON string 或 `[REDACTED]` | 当前上报 | after_compaction result，受 `config.log.spans.toolOutputs` 控制 | 内容敏感 |

## 已知边界与验证

- `_internal.meta.root` 是仓库保留字段；`otelExporter.attributes` 和 `OBSERV_OPENCLAW_OTEL_ATTRIBUTES` 中的同名键会被忽略，其他 `_internal.*` 自定义键不受影响。

- `gen_ai.input.messages`、`gen_ai.output.messages`、`gen_ai.system_instructions`、tool arguments/results 必须按 collection mode 与 `config.log.spans.*` 控制，不应从 replay raw envelope 直接透传到生产 trace。
- token 只归属单次 LLM 调用；Turn span 不应重复累计 LLM usage。
- `tool_decision` 只有事件 wrapper 常量，没有当前生产 span 拓扑。
- 字段注册参见 [`docs/semconv/spans.yaml`](../../semconv/spans.yaml) 与 [`attribute_groups.yaml`](../../semconv/attribute_groups.yaml)；是否当前 emit 仍以 `observ-openclaw` 生产代码为准。
