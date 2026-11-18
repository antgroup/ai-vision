# Agent 事件语义规范

<!-- markdownlint-disable MD013 -->

> 状态：现行规范说明  
> 生成日期：2026-09-02  
> 规范来源：[`events.yaml`](./events.yaml) v4.1、[`attribute_groups.yaml`](./attribute_groups.yaml)  
> 相关轨迹规范：[`traces.md`](./traces.md)

本文定义当前仓库使用的 OTel Logs 事件，以仓库 `events.yaml` 的 event group 为准。

## 1. 事件模型

### 1.1 载荷约定

- 事件使用 OTel Logs signal。
- `body` 只包含 `event.name`，形式为 `{ "event.name": "<name>" }`。
- 所有可检索数据放在 LogRecord `attributes`；`attributes.event.name` 与 `body.event.name` 必须一致。
- 时间使用 LogRecord timestamp，不新增 `event.timestamp` attribute。
- 事件应绑定当前 Turn、LLM、Tool、Subagent 或 Compaction 的可证明 SpanContext；没有开始信号时不得从结束回调反造 `tool_call`。
- 成功与失败共用同一种终态事件，通过 `error.type` 是否存在区分，不另造 `*_error` 事件。

### 1.2 要求级别

| 级别 | 含义 |
| --- | --- |
| 必填 | 事件产生时必须存在 |
| 条件必填 | 条件成立时必须存在 |
| 推荐 | 数据源可得时应上报 |
| Opt-in | 仅在显式开启或采集策略允许时上报；存在原始数据本身不构成授权 |

### 1.3 Ant 事件的公共字段

下表是每个 Ant event group 实际重复声明的公共核心。个别事件会把推荐字段提升为必填，具体以各事件定义为准。

| 字段 | 类型 | 默认级别 | 定义 |
| --- | --- | --- | --- |
| `event.name` | string | 必填 | 事件类型；与 `body.event.name` 相同 |
| `gen_ai.operation.name` | enum | 必填 | `user_prompt`/session 使用 `chat`，tool/compaction 使用 `execute_tool`，subagent 使用 `invoke_agent` |
| `ant.username` | string | 必填 | 用户标识 |
| `gen_ai.conversation.id` | string | 必填 | 会话标识 |
| `gen_ai.chat.id` | string | 必填 | 当前 Turn/Chat 标识 |
| `event.sequence` | int | 推荐 | 单次 prompt 处理周期内单调递增的序号 |
| `agentic.runtime.name` | string | 推荐 | Runtime 名称；部分 session/compaction 事件提升为必填 |
| `agentic.turn.number` | int | 推荐 | 从 1 开始的 Turn 序号；部分事件提升为必填 |

`events.yaml` 文件头注释仍提到 `ant.agent.type`、`ant.agent.product` 和“10 个公共字段”，但各 Ant event group 的生效 attribute 列表没有引用这两个字段。本说明按 group 定义记录；若两者通过 Resource 上报，它们不属于这里的事件 attributes 契约。

## 2. 事件总览

| 事件 | `gen_ai.operation.name` | 触发时机 | 关键关联键 |
| --- | --- | --- | --- |
| `user_prompt` | `chat` | 用户提交 prompt | conversation/chat/turn |
| `api_request` | `chat` | 一次 LLM API 请求结束，无论成功或失败 | conversation/chat/turn/model |
| `tool_call` | `execute_tool` | 已授权工具真正开始执行 | `gen_ai.tool.call.id` |
| `tool_result` | `execute_tool` | 工具执行结束，无论成功或失败 | tool name；有 call id 时与 `tool_call` 配对 |
| `tool_decision` | `execute_tool` | 工具权限决策完成 | tool name/call id |
| `session_start` | `chat` | Agent session 激活 | `gen_ai.session.id` |
| `session_end` | `chat` | Agent session 进入终态 | `gen_ai.session.id` |
| `compaction_result` | `execute_tool` | 明确观察到上下文压缩结束 | conversation/chat/turn |
| `subagent_spawned` | `invoke_agent` | 同步子 Agent 挂接到父 Turn | `agentic.subagent.session_key` |
| `subagent_ended` | `invoke_agent` | 同步子 Agent 完成或终止 | `agentic.subagent.session_key` |

## 3. 各事件定义

### 3.1 `user_prompt`

用户提交 prompt 时记录。内容字段可能包含敏感信息，因此默认 Opt-in。

| 级别 | 字段 |
| --- | --- |
| 必填 | `event.name=user_prompt`、`gen_ai.operation.name=chat`、`ant.username`、`gen_ai.conversation.id`、`gen_ai.chat.id` |
| 推荐 | `event.sequence`、`agentic.runtime.name`、`agentic.turn.number` |
| Opt-in | `gen_ai.input.messages`：OpenAI 兼容输入消息 JSON array 字符串 |

### 3.2 `api_request`

一次 LLM API 请求完成时记录。v4 将旧 `llm_request` 更名为 `api_request`，并把旧 `llm_error` 合并到同一事件；失败由 `error.type` 标记。

#### 基础与 RED 字段

| 级别 | 字段 | 定义 |
| --- | --- | --- |
| 必填 | `event.name=api_request`、`gen_ai.operation.name=chat`、`ant.username`、`gen_ai.conversation.id`、`gen_ai.chat.id` | 公共关联字段 |
| 必填 | `gen_ai.request.model` | 请求模型 |
| 必填 | `duration_ms` | LLM 请求耗时，毫秒；RED Duration |
| 条件必填 | `error.type` | API 请求失败时；RED Error |
| 条件必填 | `error.message` | 可获得人类可读错误信息时 |
| 条件必填 | `http.response.status_code` | HTTP 错误时 |
| 推荐 | `event.sequence`、`agentic.runtime.name`、`agentic.turn.number` | 顺序、Runtime、Turn 上下文 |
| 推荐 | `gen_ai.response.model`、`gen_ai.provider.name`、`attempt` | 实际模型、Provider、从 1 开始的重试次数 |
| Opt-in | `gen_ai.output.messages`、`gen_ai.system_instructions` | 输出内容与系统指令 |

OpenCode 官方 provider hooks 观察到的 `title` / `summary` / `compaction` 辅助模型调用可使用 boolean `agentic.llm.auxiliary=true`，保留实际 `gen_ai.agent.name` 与 conversation，并由官方源 message ID 派生 `gen_ai.chat.id=aux:<agent>:<message id>`。它们没有用户 Turn 编号，允许省略 `agentic.turn.number`；span 保持独立根，不创建 CHAT 或借用当前用户 Turn。该例外不适用于主请求、未知 agent、字符串 `true` 或缺失源 ID，也不放宽模型、耗时、身份、usage 与 IO 的其它要求。

#### Token 三层模型

| 层 | 字段 | 级别与定义 |
| --- | --- | --- |
| L1 归一化 | `gen_ai.usage.input_tokens` | 成功时条件必填；对应 missing 精确为字符串 `true` 时可省略；总输入包含 cache read/write |
| L1 归一化 | `gen_ai.usage.output_tokens` | 成功时条件必填；对应 missing 精确为字符串 `true` 时可省略；总输出包含 reasoning/thinking |
| L1 归一化 | `gen_ai.usage.total_tokens` | 成功且两方向均未标记未知时条件必填；`input_tokens + output_tokens` |
| L1 归一化 | `gen_ai.usage.cache_read.input_tokens` | 推荐；缓存命中 token |
| L1 归一化 | `gen_ai.usage.cache_creation.input_tokens` | 推荐；缓存创建 token |
| L2 拆分 | `agentic.usage.fresh_input_tokens` | 推荐；不含缓存的输入 token |
| L2 拆分 | `agentic.usage.cache_read_tokens` | 推荐；输入 token 的缓存命中子集 |
| L2 拆分 | `agentic.usage.cache_write_tokens` | 推荐；输入 token 的缓存创建子集 |
| L2 拆分 | `agentic.usage.reasoning_tokens` | 推荐；输出 token 的 reasoning/thinking 子集 |
| L2 拆分 | `agentic.usage.tool_tokens` | 推荐；跨输入/输出的工具相关 token |
| 缺失标记 | `agentic.usage.input_tokens.missing`、`agentic.usage.output_tokens.missing` | Opt-in；仅精确字符串 `true` 表示对应方向未知；任一方向未知时可省略 total |
| L3 Anthropic 原值 | `agentic.usage.raw.input_tokens`、`agentic.usage.raw.cache_read_input_tokens`、`agentic.usage.raw.cache_creation_input_tokens`、`agentic.usage.raw.output_tokens` | Opt-in |
| L3 OpenAI 原值 | `agentic.usage.raw.input_token_count`、`agentic.usage.raw.output_token_count`、`agentic.usage.raw.reasoning_token_count`、`agentic.usage.raw.tool_token_count` | Opt-in |
| L3 Google 原值 | `agentic.usage.raw.prompt_token_count`、`agentic.usage.raw.candidates_token_count`、`agentic.usage.raw.cached_content_token_count`、`agentic.usage.raw.thoughts_token_count` | Opt-in |

输入和输出缺失标记独立生效。布尔值 `true`、字符串 `false`、空值或其它字符串都不能豁免缺失的 token 字段，也不能豁免 response model、messages、身份和耗时。源数据明确报告的 `0` 必须保留；未知值不要补成 `0`。

### 3.3 `tool_call`

已授权工具真正开始执行时记录。必须用稳定的 `gen_ai.tool.call.id` 与终态 `tool_result` 关联；没有 start hook 时不得从 terminal callback 补造。

| 级别 | 字段 | 定义 |
| --- | --- | --- |
| 必填 | `event.name=tool_call`、`gen_ai.operation.name=execute_tool`、`ant.username`、`gen_ai.conversation.id`、`gen_ai.chat.id` | 公共字段 |
| 必填 | `agentic.turn.number` | 当前 Turn 序号 |
| 必填 | `gen_ai.tool.name` | Runtime 上报的原始工具名 |
| 必填 | `gen_ai.tool.call.id` | 与 `tool_result` 共享的稳定关联键 |
| 必填 | `agentic.tool.type` | `builtin`、`mcp`、`skill` 或 `system` |
| 必填 | `agentic.tool.name` | 解析后的逻辑工具名 |
| 必填 | `agentic.tool.id` | 工具调用标识 |
| 推荐 | `event.sequence`、`agentic.runtime.name` | 顺序与 Runtime |
| Opt-in | `agentic.tool.raw_name` | 未解析的 Runtime 工具标识 |
| 条件必填 | `gen_ai.tool.call.arguments` | start payload 存在 `args` 属性时；显式 `null` 也算存在，编码为 JSON 字符串 |
| 条件必填 | `agentic.tool.input.missing="true"` | start payload 不含 `args` 时；值是字符串 `true`，与 arguments 互斥 |
| 条件必填 | `agentic.tool.input.truncated="true"` | 字段级 canonical projection 能证明参数被截断时；值是字符串 `true`，没有 arguments 时不得出现 |

工具输入状态必须满足：有完整/截断后的 `arguments`，或使用 `input.missing`，两者不能同时表示“无输入”。

### 3.4 `tool_result`

工具执行结束时记录。成功和失败共用此事件；失败时增加 `error.type`。

| 级别 | 字段 | 定义 |
| --- | --- | --- |
| 必填 | `event.name=tool_result`、`gen_ai.operation.name=execute_tool`、`ant.username`、`gen_ai.conversation.id`、`gen_ai.chat.id` | 公共字段 |
| 必填 | `gen_ai.tool.name` | 必须与配对 `tool_call` 的原始工具名一致 |
| 必填 | `duration_ms` | 工具执行耗时，毫秒；RED Duration |
| 条件必填 | `error.type` | 工具执行失败时 |
| 条件必填 | `error.message` | 可获得人类可读错误信息时 |
| 条件必填 | `agentic.tool.type`、`agentic.tool.name`、`agentic.tool.id` | 工具分类可得时三者同时提供 |
| 推荐 | `event.sequence`、`agentic.runtime.name`、`agentic.turn.number`、`gen_ai.tool.call.id` | 顺序、Runtime、Turn 与调用关联 |
| Opt-in | `agentic.tool.raw_name`、`gen_ai.tool.call.arguments` | 原始工具标识与参数 |

派生 Skill-load Span 不重复产生 `tool_result`。当前 `events.yaml` 没有把 `gen_ai.tool.call.result` 注册到 `tool_result`，实现不得仅依据旧 `event.txt` 将其视为现行事件字段。

### 3.5 `tool_decision`

工具权限决策完成时记录，用于安全审计与权限分析。

| 级别 | 字段 | 定义 |
| --- | --- | --- |
| 必填 | `event.name=tool_decision`、`gen_ai.operation.name=execute_tool`、`ant.username`、`gen_ai.conversation.id`、`gen_ai.chat.id` | 公共字段 |
| 必填 | `gen_ai.tool.name` | 工具原始名称 |
| 必填 | `agentic.tool.decision` | `allow`、`deny`、`skip`、`unknown` |
| 必填 | `agentic.tool.decision.source` | 内置值为 `user`、`auto`、`policy`、`hook`、`config`；允许自定义值 |
| 条件必填 | `agentic.tool.type`、`agentic.tool.name` | 工具分类可得时 |
| 推荐 | `event.sequence`、`agentic.runtime.name`、`agentic.turn.number`、`gen_ai.tool.call.id` | 顺序与关联字段 |
| Opt-in | `gen_ai.tool.call.arguments` | 工具参数 |

### 3.6 `session_start`

Agent session 激活时记录，不表示一次 LLM 请求。

| 级别 | 字段 |
| --- | --- |
| 必填 | `event.name=session_start`、`gen_ai.operation.name=chat`、`ant.username`、`gen_ai.conversation.id`、`gen_ai.chat.id`、`agentic.runtime.name`、`gen_ai.session.id` |
| 推荐 | `event.sequence`、`agentic.turn.number` |

### 3.7 `session_end`

Agent session 到达终态时只记录一次。

| 级别 | 字段 |
| --- | --- |
| 必填 | `event.name=session_end`、`gen_ai.operation.name=chat`、`ant.username`、`gen_ai.conversation.id`、`gen_ai.chat.id`、`agentic.runtime.name`、`gen_ai.session.id`、`agentic.turn.number` |
| 推荐 | `event.sequence` |

### 3.8 `compaction_result`

明确观察到上下文压缩完成时记录。只有真实 Runtime lifecycle signal 才能触发。

| 级别 | 字段 | 定义 |
| --- | --- | --- |
| 必填 | `event.name=compaction_result`、`gen_ai.operation.name=execute_tool`、`ant.username`、`gen_ai.conversation.id`、`gen_ai.chat.id` | 公共字段 |
| 必填 | `agentic.runtime.name`、`agentic.turn.number` | Runtime 与 Turn |
| 必填 | `gen_ai.tool.name=compaction`、`agentic.tool.type=system` | 固定工具语义 |
| 必填 | `duration_ms` | 压缩耗时，毫秒 |
| 条件必填 | `error.type`、`error.message` | 失败时 / 可获得错误信息时 |
| 推荐 | `event.sequence` | 事件序号 |
| Opt-in | `gen_ai.tool.call.result` | 压缩结果内容 |

### 3.9 `subagent_spawned`

同步子 Agent 挂接到父 Turn 时记录。

| 级别 | 字段 | 定义 |
| --- | --- | --- |
| 必填 | `event.name=subagent_spawned`、`gen_ai.operation.name=invoke_agent`、`ant.username`、`gen_ai.conversation.id`、`gen_ai.chat.id` | 公共字段 |
| 必填 | `gen_ai.agent.id`、`gen_ai.agent.name` | 子 Agent 身份 |
| 必填 | `agentic.subagent.session_key` | 跨 trace 关联子 Agent 的稳定 key |
| 条件必填 | `agentic.subagent.run_id` | Runtime 提供 run id 时 |
| 推荐 | `event.sequence`、`agentic.runtime.name`、`agentic.turn.number` | 顺序与上下文 |
| 推荐 | `agentic.subagent.parent_thread_id`、`agentic.subagent.child_thread_id`、`agentic.subagent.name`、`agentic.subagent.mode` | 父子线程、名称与执行模式 |

### 3.10 `subagent_ended`

同步子 Agent 完成或终止时记录。

| 级别 | 字段 | 定义 |
| --- | --- | --- |
| 必填 | `event.name=subagent_ended`、`gen_ai.operation.name=invoke_agent`、`ant.username`、`gen_ai.conversation.id`、`gen_ai.chat.id` | 公共字段 |
| 必填 | `gen_ai.agent.id`、`gen_ai.agent.name`、`agentic.subagent.session_key` | 子 Agent 身份与关联 key |
| 必填 | `duration_ms` | 子 Agent 执行耗时，毫秒 |
| 条件必填 | `agentic.subagent.run_id` | Runtime 提供 run id 时 |
| 条件必填 | `agentic.subagent.reason` | 存在终止原因时 |
| 条件必填 | `error.type`、`error.message` | 子 Agent 失败时 / 可获得错误信息时 |
| 推荐 | `event.sequence`、`agentic.runtime.name`、`agentic.turn.number` | 顺序与上下文 |
| 推荐 | `agentic.subagent.parent_thread_id`、`agentic.subagent.child_thread_id`、`agentic.subagent.mode`、`agentic.subagent.outcome`、`agentic.subagent.target_kind` | 父子线程、模式、结果与目标类型 |

## 4. OTel 标准参考事件

`events.yaml` 还保留两个 `gen_ai` 标准事件作为参考，不用于当前 Agentic 主流程。

### 4.1 `gen_ai.client.inference.operation.details`

- 必填：`gen_ai.operation.name`、`ant.username`。
- 条件必填：`error.type`、`gen_ai.conversation.id`、`gen_ai.output.type`、`gen_ai.request.choice.count`、`gen_ai.request.model`、`gen_ai.request.seed`、`server.port`、`ant.uid`。
- 推荐：`gen_ai.request.frequency_penalty`、`gen_ai.request.max_tokens`、`gen_ai.request.presence_penalty`、`gen_ai.request.stop_sequences`、`gen_ai.request.temperature`、`gen_ai.request.top_p`、`gen_ai.response.finish_reasons`、`gen_ai.response.model`、`gen_ai.usage.cache_creation.input_tokens`、`gen_ai.usage.cache_read.input_tokens`、`gen_ai.usage.input_tokens`、`gen_ai.usage.output_tokens`、`server.address`。
- Opt-in：`gen_ai.response.id`、`gen_ai.input.messages`、`gen_ai.output.messages`、`gen_ai.system_instructions`、`gen_ai.tool.definitions`。

### 4.2 `gen_ai.evaluation.result`

- 必填：`gen_ai.evaluation.name`、`ant.username`。
- 条件必填：失败时 `error.type`；适用时 `gen_ai.evaluation.score.label`、`gen_ai.evaluation.score.value`；可得时 `ant.uid`。
- 推荐：`gen_ai.evaluation.explanation`。
- Opt-in：`gen_ai.response.id`。

## 5. 生命周期与关联规则

### 5.1 Tool 生命周期

```text
tool_decision（如果存在权限判断）
  → tool_call（真实开始执行）
  → tool_result（唯一终态；成功或失败）
```

- `tool_call` 与 `tool_result` 优先通过同一个 `gen_ai.tool.call.id` 关联。
- `gen_ai.tool.name` 使用 Runtime 原始值，配对事件必须一致；展示友好名称放在 Span name，逻辑分类放在 `agentic.tool.name`。
- RED：Rate = `tool_result` 数量；Error = 含 `error.type` 的 `tool_result` 数量；Duration = `duration_ms` 直方图。

### 5.2 API 生命周期

- 每次模型 API 尝试产生一个 `api_request`；重试用 `attempt` 区分。
- 不再发送 `llm_error`；错误 `api_request` 必须含 `error.type`。
- Token 只归属这一次模型调用，不复制到其他 `api_request` 或 Turn 事件。

### 5.3 Session、Turn 与 Subagent

- `gen_ai.session.id` 只承担 session lifecycle 关联，不替代 `gen_ai.conversation.id` 或 `gen_ai.chat.id`。
- `session_end` 每个 session 只产生一次。
- `subagent_spawned` 与 `subagent_ended` 使用相同的 `agentic.subagent.session_key` 配对；有 run id 时同时携带 `agentic.subagent.run_id`。

## 6. 实现检查清单

- `body` 只含 `event.name`，且与 attributes 中的 `event.name` 一致。
- 固定 `event.name` 与固定 `gen_ai.operation.name` 必须匹配事件类型。
- `event.sequence` 在单次 prompt 周期内单调递增。
- 失败终态必须使用同名事件加 `error.type`，不另造错误事件。
- `duration_ms` 在 `api_request`、`tool_result`、`compaction_result`、`subagent_ended` 上必填。
- `tool_call` 的三态输入规则、Tool 配对规则、Subagent 配对规则必须满足。
- 内容字段必须执行显式 Opt-in、脱敏和截断；不得落盘或上报 token、credential 等秘密。
- 最终以 weaver-guard 的 `0 violations` 为验收门槛。
