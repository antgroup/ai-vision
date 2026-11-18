# Claude Code cchooks 轨迹字段

## 范围

本文是本仓库 `cchooks` 生产 trace 的 canonical 字段说明，迁移自旧字段表并以 [`common/otel.rs`](../../../claudecode/cchooks/src/common/otel.rs)、Langfuse handler、semconv 与 replay 路径复核。它不包含 [官方 Claude Code 原生事件](./events.md) 或 Metrics，也不把官方 detailed tracing 的 `claude_code.interaction` 拓扑当作 cchooks 拓扑。

状态均为 `当前上报` 或明确的 `条件上报`。生产事实来自 host hook payload/runtime/transcript；replay 只作为离线复核输入。

## 拓扑

```text
Turn / CHAT
├── LLM generation
├── TOOL execution
└── AGENT / CLIENT（存在 subagent 或客户端语义时）
```

LLM、TOOL、AGENT 是同一 Turn 的兄弟节点，不应把 TOOL 写成 LLM 子节点。Compaction/error/retry 只在对应 hook/transcript 分支存在时出现。

## Resource attributes

| 字段 | 类型 | 状态/条件 | 来源与含义 | 隐私 |
|---|---|---|---|---|
| `service.name` | string | 当前上报 | 配置的 agent 服务名 | 低风险 |
| `service.version` | string | 当前上报 | cchooks 构建版本 | 低风险 |
| `ant.agent.instance` | string | 当前上报 | 主机名 | 环境指纹 |
| `ant.agent.product` / `ant.agent.type` | string | 当前上报 | 产品与 `claudecode` 类型 | 低风险 |
| `telemetry.sdk.language` / `name` / `version` | string | 当前上报 | SDK 元数据 | 低风险 |
| `ant.agent.env` / `deployment.environment` / `ant.agent.scene` | string | 条件上报 | 环境变量 | 部署信息 |
| `agentic.collection.mode` | string | 当前上报 | `full` 或 `privacy` | 安全控制信息 |
| `vcs.repository.url.full` / `vcs.ref.head.name` / `vcs.ref.head.revision` | string | Git 可用且事实非空时 | Git 探测；revision 为可选增强字段 | 仓库/分支敏感；条件上报 |
| `agentic.subagent`、`.mode`、`.id`、`.type`、`.description` | bool/string | async subagent trace | subagent 元数据 | 任务描述可能敏感 |
| 自定义 Resource keys | scalar | 显式配置 | OTel Resource 扩展 | 部署方负责 |

## 所有 span 的语义与关联字段

| 字段 | 类型 | 状态/条件 | 适用 span | 来源/含义 | 隐私 |
|---|---|---|---|---|---|
| `gen_ai.span.kind` | string | 当前上报 | 全部 | `CHAT`/`LLM`/`TOOL`/`AGENT`/`CLIENT` | 低风险 |
| `gen_ai.operation.name` | string | 当前上报 | 全部 | `chat`、`execute_tool` 等 | 低风险 |
| `gen_ai.conversation.id` | string | 当前上报 | 全部 | host session id | 关联标识 |
| `gen_ai.chat.id` | string | 当前上报 | Turn 及子 span | session+turn | 高基数关联键 |
| `agentic.runtime.name` | string | 当前上报 | 全部 | `claude-code` | 低风险 |
| `agentic.runtime.cwd` | string | 有 payload 时 | Turn/相关 span | 工作目录 | 路径敏感 |
| `agentic.turn.number` / `.index` | int | 当前上报 | Turn/子 span | 会话 turn 与批内序号 | 低风险 |
| `ant.username` / `user.id` | string | username 可用时 | 相关 span | hook 上下文 | 用户身份 |
| `langfuse.trace.tags` / `agentic.skill.tags` | string[] | skill/tool 分类时 | Turn/tool | 标签 | 可能暴露技能名称 |
| `custom.*`、`_internal.sofa.trace_id`、`_internal.sofa.span_id` | scalar | 显式环境可用时 | 相关 span | 自定义/SOFA 关联 | 部署与内部关联信息 |

## Turn root (`gen_ai.span.kind=CHAT`)

这里的 `CHAT` 是 Turn 根 span 的类型，不是 LLM 子 span 的 `gen_ai.operation.name=chat`。
Turn 根是逻辑上的 Agent 交互入口；即使通过 `TRACEPARENT` 接入上游而拥有
`parentSpanId`，仍保持 Turn 根语义。

| 字段 | 类型 | 条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `_internal.meta.root` | string | 当前上报，固定为 `"1"` | 仅 Turn 根；供 trace 列表索引与展示，不表示物理 OTel 无父节点 | 低风险 |
| `gen_ai.input.messages` / `gen_ai.output.messages` | JSON string | 内容可构造时 | OpenAI-compatible message arrays；transcript | prompt/response 敏感，privacy 模式按实现裁剪 |
| `agentic.input.delta` | JSON string | 增量可计算时 | 本次 generation 新增输入 | 内容敏感 |
| `agentic.tool.count` | int | 当前上报 | assistant 响应内工具数 | 低风险 |
| `agentic.compaction.trigger` | string | compaction | 触发原因 | 低风险 |
| `agentic.compaction.tokens.before` / `.after` / `.saved` | int | compaction | compaction token 统计 | 使用量信息 |
| `agentic.compaction.sequence` / `.ratio` | int/number | compaction | 序号与压缩比 | 低风险 |

## LLM

| 字段 | 类型 | 条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `gen_ai.request.model` / `gen_ai.response.model` / `gen_ai.provider.name` | string | 模型可用时 | transcript/model 解析 | 模型路由信息 |
| `gen_ai.response.id` | string | 响应有 ID | assistant message id | 高基数关联键 |
| `gen_ai.response.finish_reasons` | string[] | 响应完成 | stop reason | 低风险 |
| `gen_ai.usage.input_tokens` / `.output_tokens` | int | usage 可用 | 单次 LLM 调用 token | 使用量信息 |
| `agentic.response.index` | int | 多响应时 | turn 内响应序号 | 低风险 |
| `agentic.llm.duration_ms` | number | timing 可用 | LLM 耗时 | 性能信息 |
| `agentic.llm.is_synthetic` | bool | API error 合成消息 | 合成错误响应标识 | 低风险 |
| `agentic.llm.retry_count`、`.retry.attempt`、`.retry.max`、`.retry.delay_ms` | int | retry 分支 | retry 次数/序号/等待 | 运行状态 |
| `agentic.llm.error.type` / `.status_code` / `.message` | string/int | API error | LLM 错误 | message 可能敏感 |
| `TRACEPARENT`（不作为 span 属性） | 环境/持久化 Turn seed | Agent SDK/`claude -p` 提供且通过严格校验时 | Turn root 复用上游 trace/parent；ToolContext 为下游生成 child context | 跨进程关联信息，按部署策略控制 |
| `error.type` | string | span 失败 | OTel 通用错误类型 | 错误信息 |

## TOOL / AGENT

| 字段 | 类型 | 条件 | 含义与来源 | 隐私 |
|---|---|---|---|---|
| `gen_ai.tool.name` / `gen_ai.tool.call.id` | string | tool span | 展示名与调用 ID | 名称/关联键 |
| `gen_ai.tool.call.arguments` / `.result` | JSON string | full mode 且内容可用 | tool input/result | 高敏感；可能截断 |
| `agentic.tool.raw_name` / `.type` / `.name` / `.id` | string | tool 分类 | 原始、类型、归一名、ID | 工具身份 |
| `agentic.tool.duration_ms` | number | 结束时间可用 | 工具耗时 | 性能信息 |
| `agentic.tool.input.truncated` / `.output.truncated` | bool | 截断时 | 截断标识 | 安全元数据 |
| `agentic.tool.input.original_length` / `.output.original_length` | int | 截断时 | 原始长度 | 内容规模 |
| `agentic.tool.parallel_group` | string | 并行 tool_use | 并行分组 | 关联键 |
| `agentic.tool.input.missing` | bool | 仅 result 可见 | 明确输入缺失，不应同时伪造 arguments | 低风险 |
| `agentic.subagent` / `.mode` / `.id` / `.type` / `.description` | bool/string | subagent | 同步/异步 subagent 元数据 | description 可能含任务内容 |

## 已知边界与验证

- `gen_ai.input.messages`、`gen_ai.output.messages`、tool arguments/results 必须保持合法 JSON array/string，并遵守 collection mode 与截断实现。
- token 只归属单次 LLM 调用，Turn 不重复累计。
- Rare error、retry、compaction 和 async subagent 字段可能只由代码分支证明，fixture 未覆盖时应标为条件上报。
- 共享字段规则参见 [`docs/semconv/spans.yaml`](../../semconv/spans.yaml) 与 [`attribute_groups.yaml`](../../semconv/attribute_groups.yaml)；是否当前 emit 仍以 cchooks 生产代码为准。
