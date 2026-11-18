# Codex cdxhooks 轨迹字段

## 范围与拓扑

本文记录本仓库 `cdxhooks` 生产 trace，不包含 [Codex 官方原生 event categories](./events.md)、dynamic LogRecord 或 Metrics。主要事实来源为 [`common/otel.rs`](../../../codex/cdxhooks/src/common/otel.rs)、[`langfuse_handler.rs`](../../../codex/cdxhooks/src/hooks/langfuse/langfuse_handler.rs) 和 transcript/merge fixture。

```text
Turn / CHAT
├── LLM generation
├── TOOL execution
└── AGENT / INTERNAL（collaboration/subagent 或内部语义）
```

LLM、TOOL 与 AGENT 是 Turn 的兄弟节点；官方 SSE/WebSocket event categories 不是 cdxhooks span kind。

这里的 Turn 是 cdxhooks 构造的逻辑 agent 根，而不是无 OTel parent 的物理根：即使接入上游 trace、Turn 带有 `parentSpanId`，它仍是当前 Codex turn 的逻辑根。LLM `chat` generation 只是 Turn 的子 span，不是 root span。

## Resource attributes

| 字段 | 状态/条件 | 含义/来源 | 隐私 |
|---|---|---|---|
| `service.name` / `service.version` | 当前上报 | 服务名与 cdxhooks 版本 | 低风险 |
| `ant.agent.instance` / `.product` / `.type` | 当前上报 | 主机、产品、`codex` | 环境指纹 |
| `telemetry.sdk.language` / `.name` / `.version` | 当前上报 | SDK 元数据 | 低风险 |
| `ant.agent.env` / `deployment.environment` / `ant.agent.scene` | 条件上报 | 环境变量 | 部署信息 |
| `agentic.collection.mode` | 当前上报 | `full`/`privacy` | 安全控制信息 |
| `vcs.repository.url.full` / `vcs.ref.head.name` / `vcs.ref.head.revision` | Git 可用且事实非空时 | 仓库、分支与可选 HEAD revision | 仓库敏感；条件上报 |
| 自定义 Resource keys | 显式配置 | OTel Resource 扩展 | 部署方负责 |

## 公共与 Turn 字段

| 字段 | 类型 | 状态/条件 | 含义/来源 | 隐私 |
|---|---|---|---|---|
| `gen_ai.span.kind` | string | 当前上报 | `CHAT`/`LLM`/`TOOL`/`AGENT`/`INTERNAL` | 低风险 |
| `_internal.meta.root` | string | 仅 Turn 根 | 固定为字符串 `"1"`，供 trace 列表识别逻辑 Turn 根；有上游 parent 时仍保留，LLM/TOOL/AGENT/INTERNAL/COMPACTION/SUBAGENT 不携带 | 内部索引元数据 |
| `gen_ai.operation.name` | string | 当前上报 | `chat`、`execute_tool` 等 | 低风险 |
| `gen_ai.conversation.id` / `gen_ai.chat.id` | string | 当前上报 | session/child-session 与 turn 关联 | 高基数 ID |
| `agentic.runtime.name` / `.cwd` | string | 当前/有 transcript 时 | `codex` 与工作目录 | cwd 敏感 |
| `agentic.turn.number` / `.index` | int | 当前上报 | turn 序号 | 低风险 |
| `gen_ai.input.messages` / `gen_ai.output.messages` | JSON string | 内容可用 | OpenAI-compatible message arrays | prompt/response 敏感 |
| `agentic.input.delta` | JSON string | 可计算时 | generation 输入增量 | 内容敏感 |
| `agentic.tool.count` | int | 当前上报 | turn/response 工具数 | 低风险 |
| `ant.username` / `user.id` | string | username 可用 | 用户上下文 | 身份敏感 |
| `agentic.skill.tags` / `langfuse.trace.tags` | string[] | skill/tool 可分类 | 标签 | 名称可能敏感 |
| `ant.custom.*` | scalar | 显式环境配置 | 自定义 metadata | 部署方负责 |

## LLM 字段

| 字段 | 类型 | 条件 | 含义 | 隐私 |
|---|---|---|---|---|
| `gen_ai.request.model` / `.response.model` / `gen_ai.provider.name` | string | 模型可用 | 请求/响应模型与 provider | 路由信息 |
| `gen_ai.response.finish_reasons` | string[] | 完成时 | stop reason | 低风险 |
| `gen_ai.usage.input_tokens` / `.output_tokens` / `.total_tokens` | int | usage 可用 | 单次 LLM token | 使用量信息 |
| `gen_ai.usage.cache_read_input_tokens` | int | cache usage 可用 | cache-read input token | 使用量信息；按当前实现名记录 |
| `agentic.response.index` | int | 多响应 | turn 内序号 | 低风险 |
| `agentic.llm.duration_ms` | number | timing 可用 | LLM 耗时 | 性能信息 |
| `error.type` | string | 失败 | LLM/tool/spawn 错误类型 | 错误类别 |
| `TRACEPARENT`（不作为 span 属性） | 环境/持久化 context | 有效上游调用时 | Turn root 复用 trace ID/parent span；下游工具生成 child context | 跨进程关联信息，按部署策略控制 |

## TOOL 字段

| 字段 | 类型 | 条件 | 含义 | 隐私 |
|---|---|---|---|---|
| `gen_ai.tool.name` / `gen_ai.tool.call.id` | string | tool span | 工具展示名与调用 ID | 名称/关联键 |
| `gen_ai.tool.call.arguments` / `.result` | JSON string | 内容可用且策略允许 | 工具输入/输出 | 高敏感，可能截断 |
| `agentic.tool.raw_name` / `.type` / `.name` / `.id` | string | 分类可用 | 原始与归一化工具信息 | 工具身份 |
| `agentic.tool.duration_ms` | number | 结束时 | 工具耗时 | 性能信息 |
| input/output `.truncated` / `.original_length` | bool/int | 截断时 | 截断证据 | 内容规模 |
| `agentic.tool.parallel_group` | string | 并行工具 | 并行分组 | 关联键 |

## Collaboration / subagent 字段

| 字段组 | 类型 | 条件 | 含义/来源 | 隐私 |
|---|---|---|---|---|
| `agentic.subagent` / `.mode` / `.id` / `.type` / `.status` | bool/string | subagent merge | 子 agent 基础身份与状态 | 高基数关联 |
| `.call_id`、`.sender_thread_id`、`.receiver_thread_id`、`.child_thread_id`、`.parent_thread_id` | string | activity/transcript 可用 | collaboration 关联 | 内部 thread 标识 |
| `.depth` / `.agent_path` | int/string | v2 metadata | 嵌套深度与 canonical path | 内部拓扑 |
| `.agent_nickname` / `.agent_role` / `.nickname` / `.role` | string | metadata 可用 | 昵称与角色 | 可能暴露组织命名 |
| `.prompt` / `.model` | string | activity 含值 | 子 agent prompt 与模型 | prompt 高敏感 |

Legacy spawn output 与 v2 activity bridge 的 ID 来源不同；只有满足实现中的身份一致性/唯一 fallback 规则时才能合并，不能按昵称猜测 parent-child。

## Compaction 与验证边界

条件字段：`agentic.compaction.trigger`、`.tokens.before`、`.tokens.after`、`.tokens.saved`、`.sequence`、`.ratio`。它们只在 compaction 分支存在。

- 内容字段遵守 collection mode、截断与 missing 语义；不得用空 JSON 伪造缺失输入。
- token 只归属单次 LLM span，Turn 不重复上报。
- stray `q` 和重复 `gen_ai.provider.name` 不属于字段契约，已从 canonical 文档移除。
- 共享规则见 [`spans.yaml`](../../semconv/spans.yaml)；当前 emit 以 cdxhooks 代码和 fixtures 为准。
