# AI Vision Agent 可观测性规范 — Events

> **版本**: v2.0
> **基于**: [OTel GenAI Semantic Conventions - Events](../otel/events.md) + [AI Vision 身份标识规范](../custom/observability-identity-spec.md)
> **协议**: OpenTelemetry Logs/Events

## 概述

本规范定义AI Vision Agent 系统的 Events 语义规范。在 OTel 官方 `gen_ai.*` 事件基础上，补充AI Vision侧统一身份字段 `ant.username` 和 `ant.uid`，并保留AI Vision自有的 `agent.*` 业务事件，形成完整的事件采集标准。

### 事件分层

```
┌─────────────────────────────────────────────────────┐
│                  AI Vision Events 体系                     │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  OTel 标准层（gen_ai.*）                        │  │
│  │  inference.operation.details / evaluation ... │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  AI Vision业务层（业务事件）                          │  │
│  │  user_prompt / llm_request / tool_result ...  │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  AI Vision 身份层（ant.username / ant.uid）           │  │
│  │  所有事件共享的身份标识属性                       │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## 一、通用身份属性（所有事件共享）

所有 Events 必须携带以下AI Vision 身份标识属性。`resource.md` 中定义的公共 Resource Attributes 不在本规范重复展开。

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `ant.username` | string | 用户标识 | `"100001"` |
| `ant.uid` | string | 外部客户 UID（可用时） | `"2088xxxx"` |

### 事件通用属性

| Key | Type | Requirement | Description |
|-----|------|-------------|-------------|
| `event.name` | string | Required | 事件名称 |
| `event.timestamp` | string | Required | ISO 8601 时间戳 |
| `event.sequence` | int | Required | 会话内单调递增序号，用于事件排序 |

### 事件关联属性

| Key | Type | Requirement | Description |
|-----|------|-------------|-------------|
| `prompt.id` | string | Required | UUID v4，关联同一个用户 prompt 产生的所有事件 |

> ⚠️ `prompt.id` 仅用于事件级别的分析和审计追踪，**不应** 包含在 Metrics 中。

---

## 二、OTel 标准事件（gen_ai.*）

基于 [OTel GenAI Events 规范](../otel/events.md)，在AI Vision场景下的适配。

### Event: `gen_ai.client.inference.operation.details`

描述 GenAI 推理请求的详细信息，包括聊天历史和参数。此事件为 **opt-in**，可独立于 traces 存储输入输出详情。

#### 必需属性 (Required)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `gen_ai.operation.name` | string | 操作名称 | `chat`, `generate_content`, `text_completion` |
| `ant.username` | string | 用户标识 | `"100001"` |

#### 条件必需属性 (Conditionally Required)

| Key | Condition | Type | Description | Example |
|-----|-----------|------|-------------|---------|
| `error.type` | 操作以错误结束时 | string | 错误类型 | `timeout`, `500` |
| `gen_ai.conversation.id` | 可用时 | string | 会话唯一标识 | `conv_5j66UpCpwteGg4YSxUnt7lPY` |
| `gen_ai.output.type` | 请求包含输出格式时 | string | 客户端请求的内容类型 | `text`, `json`, `image` |
| `gen_ai.request.choice.count` | 可用且 !=1 时 | int | 候选补全数量 | `3` |
| `gen_ai.request.model` | 可用时 | string | 请求的模型名称 | `claude-sonnet-4-6` |
| `gen_ai.request.seed` | 请求包含 seed 时 | int | 随机种子 | `100` |
| `server.port` | `server.address` 已设置时 | int | 服务端口 | `443` |
| `ant.uid` | 可用时 | string | 外部客户 UID | `"2088xxxx"` |

#### 推荐属性 (Recommended)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `gen_ai.request.frequency_penalty` | double | 频率惩罚 | `0.1` |
| `gen_ai.request.max_tokens` | int | 最大生成 token 数 | `100` |
| `gen_ai.request.presence_penalty` | double | 存在惩罚 | `0.1` |
| `gen_ai.request.stop_sequences` | string[] | 停止序列列表 | `["forest", "lived"]` |
| `gen_ai.request.temperature` | double | 温度参数 | `0.0` |
| `gen_ai.request.top_p` | double | Top-p 采样参数 | `1.0` |
| `gen_ai.response.finish_reasons` | string[] | 停止原因数组 | `["stop"]` |
| `gen_ai.response.id` | string | 补全唯一标识 | `chatcmpl-123` |
| `gen_ai.response.model` | string | 响应模型名称 | `claude-sonnet-4-6-20250514` |
| `gen_ai.usage.cache_creation.input_tokens` | int | 写入缓存的输入 token 数 | `25` |
| `gen_ai.usage.cache_read.input_tokens` | int | 从缓存读取的输入 token 数 | `50` |
| `gen_ai.usage.input_tokens` | int | 输入 token 总数 | `100` |
| `gen_ai.usage.output_tokens` | int | 输出 token 数 | `180` |
| `server.address` | string | 服务地址 | `api.anthropic.com` |

#### Opt-In 属性

| Key | Type | Description |
|-----|------|-------------|
| `gen_ai.input.messages` | any | 提供给模型的聊天历史（⚠️ 可能包含敏感信息） |
| `gen_ai.output.messages` | any | 模型返回的消息，每条消息对应一个生成选项 |
| `gen_ai.system_instructions` | any | 独立于聊天历史的系统指令 |
| `gen_ai.tool.definitions` | any | 可用工具定义列表（不建议默认填充） |

---

### Event: `gen_ai.evaluation.result`

捕获对 GenAI 输出的质量、准确性等评估结果。

#### 必需属性 (Required)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `gen_ai.evaluation.name` | string | 评估指标名称 | `Relevance`, `IntentResolution` |
| `ant.username` | string | 用户标识 | `"100001"` |

#### 条件必需属性 (Conditionally Required)

| Key | Condition | Type | Description | Example |
|-----|-----------|------|-------------|---------|
| `error.type` | 操作以错误结束时 | string | 错误类型 | `timeout`, `500` |
| `gen_ai.evaluation.score.label` | 适用时 | string | 评估分数标签 | `relevant`, `pass`, `fail` |
| `gen_ai.evaluation.score.value` | 适用时 | double | 评估分数值 | `4.0` |
| `ant.uid` | 可用时 | string | 外部客户 UID | `"2088xxxx"` |

#### 推荐属性 (Recommended)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `gen_ai.evaluation.explanation` | string | 评估解释 | `The response is factually accurate.` |
| `gen_ai.response.id` | string | 被评估的补全标识 | `chatcmpl-123` |

---

## 三、AI Vision业务事件

AI Vision自有的业务层事件，补充 OTel 标准事件未覆盖的 Agent 运营维度。

相关属性 **优先复用** `gen_ai.*`、`error.*`、`http.*` 等标准字段命名；仅当 OTel 未覆盖时，保留AI Vision业务扩展字段。

### 事件总览

| 事件名称 | 触发时机 | 描述 |
|----------|---------|------|
| `user_prompt` | 用户提交 prompt | 记录用户输入 |
| `llm_request` | LLM API 请求完成 | 记录 LLM 调用详情 |
| `llm_error` | LLM API 请求失败 | 记录 LLM 调用错误 |
| `tool_result` | 工具执行完成 | 记录工具执行结果 |
| `tool_decision` | 工具权限决策 | 记录工具使用的授权决策 |
| `agent_step` | Agent 完成一个推理步骤 | 记录 Agent 的推理/规划步骤 |

---

### Event: `user_prompt` — 用户输入事件

用户提交 prompt 时记录。

**属性**：

| Key | Type | Requirement | Description | Example |
|-----|------|-------------|-------------|---------|
| `event.name` | string | Required | 固定值 `"user_prompt"` | `"user_prompt"` |
| `event.timestamp` | string | Required | ISO 8601 时间戳 | `"2026-03-21T16:00:00Z"` |
| `event.sequence` | int | Required | 会话内单调递增序号 | `1` |
| `prompt.id` | string | Required | 关联 prompt 的 UUID | `"uuid-xxx"` |
| `gen_ai.conversation.id` | string | Recommended | 会话唯一标识（可用时） | `"conv_5j66UpCpwteGg4YSxUnt7lPY"` |
| `prompt_length` | int | Required | prompt 字符长度 | `256` |
| `gen_ai.input.messages` | any | Opt-In | 用户输入消息，推荐使用 OTel 标准消息结构 | `[{"role":"user","parts":[{"type":"text","content":"帮我写一个排序算法"}]}]` |
| `input_type` | string | Recommended | 输入类型 | `"text"`, `"voice"`, `"image"`, `"multimodal"` |

---

### Event: `llm_request` — LLM 请求事件

每次 LLM API 请求完成时记录。

**属性**：

| Key | Type | Requirement | Description | Example |
|-----|------|-------------|-------------|---------|
| `event.name` | string | Required | 固定值 `"llm_request"` | `"llm_request"` |
| `event.timestamp` | string | Required | ISO 8601 时间戳 | `"2026-03-21T16:00:01Z"` |
| `event.sequence` | int | Required | 会话内单调递增序号 | `2` |
| `prompt.id` | string | Required | 关联 prompt 的 UUID | `"uuid-xxx"` |
| `gen_ai.operation.name` | string | Required | 操作名称 | `"chat"`, `"text_completion"`, `"embeddings"` |
| `gen_ai.request.model` | string | Required | 请求模型标识 | `"claude-sonnet-4-6"` |
| `duration_ms` | int | Required | 请求耗时（毫秒） | `2350` |
| `gen_ai.usage.input_tokens` | int | Required | 输入 token 数 | `1500` |
| `gen_ai.usage.output_tokens` | int | Required | 输出 token 数 | `800` |
| `gen_ai.provider.name` | string | Recommended | LLM 提供商 | `"anthropic"`, `"openai"`, `"alibaba"` |
| `gen_ai.conversation.id` | string | Recommended | 会话唯一标识 | `"conv_5j66UpCpwteGg4YSxUnt7lPY"` |
| `gen_ai.response.id` | string | Recommended | 本次补全唯一标识 | `"msg_abc123"` |
| `gen_ai.response.model` | string | Recommended | 实际响应模型名称 | `"claude-sonnet-4-6-20250514"` |
| `gen_ai.response.finish_reasons` | string[] | Recommended | 停止原因数组 | `["stop"]` |
| `gen_ai.output.type` | string | Recommended | 输出内容类型 | `"text"`, `"json"`, `"image"` |
| `gen_ai.request.max_tokens` | int | Recommended | 最大生成 token 数 | `4000` |
| `gen_ai.request.temperature` | double | Recommended | 温度参数 | `0.7` |
| `gen_ai.request.top_p` | double | Recommended | Top-p 参数 | `1.0` |
| `gen_ai.request.stop_sequences` | string[] | Recommended | 停止序列列表 | `["</tool_result>"]` |
| `gen_ai.request.choice.count` | int | Recommended | 候选补全数量 | `1` |
| `gen_ai.request.seed` | int | Recommended | 随机种子（设置时） | `100` |
| `gen_ai.usage.cache_read.input_tokens` | int | Recommended | 缓存读取 token 数 | `500` |
| `gen_ai.usage.cache_creation.input_tokens` | int | Recommended | 缓存创建 token 数 | `200` |
| `server.address` | string | Recommended | 服务地址 | `"api.anthropic.com"` |
| `server.port` | int | Conditionally Required | 服务端口（设置 `server.address` 时） | `443` |
| `gen_ai.system_instructions` | string | Opt-In | 系统指令（独立于聊天历史） | `"You are a coding assistant."` |
| `gen_ai.input.messages` | any | Opt-In | 输入消息列表 | `[{"role":"user","parts":[{"type":"text","content":"帮我优化这段代码"}]}]` |
| `gen_ai.output.messages` | any | Opt-In | 输出消息列表 | `[{"role":"assistant","parts":[{"type":"text","content":"我先检查这段代码的瓶颈。"}],"finish_reason":"stop"}]` |
| `gen_ai.tool.definitions` | any | Opt-In | 请求携带的工具定义列表 | — |

---

### Event: `llm_error` — LLM 错误事件

LLM API 请求失败时记录。

**属性**：

| Key | Type | Requirement | Description | Example |
|-----|------|-------------|-------------|---------|
| `event.name` | string | Required | 固定值 `"llm_error"` | `"llm_error"` |
| `event.timestamp` | string | Required | ISO 8601 时间戳 | `"2026-03-21T16:00:01Z"` |
| `event.sequence` | int | Required | 会话内单调递增序号 | `3` |
| `prompt.id` | string | Required | 关联 prompt 的 UUID | `"uuid-xxx"` |
| `gen_ai.operation.name` | string | Required | 操作名称 | `"chat"`, `"text_completion"`, `"embeddings"` |
| `gen_ai.request.model` | string | Required | 请求模型标识 | `"claude-sonnet-4-6"` |
| `error.type` | string | Required | 错误类型 | `"rate_limit_exceeded"`, `"timeout"` |
| `duration_ms` | int | Required | 请求耗时（毫秒） | `150` |
| `http.response.status_code` | int | Conditionally Required | HTTP 状态码（HTTP 错误时） | `429` |
| `attempt` | int | Recommended | 重试次数（第几次尝试） | `2` |
| `gen_ai.provider.name` | string | Recommended | LLM 提供商 | `"anthropic"` |
| `gen_ai.conversation.id` | string | Recommended | 会话唯一标识 | `"conv_5j66UpCpwteGg4YSxUnt7lPY"` |
| `server.address` | string | Recommended | 服务地址 | `"api.anthropic.com"` |
| `server.port` | int | Conditionally Required | 服务端口（设置 `server.address` 时） | `443` |
| `gen_ai.system_instructions` | string | Opt-In | 系统指令（便于失败回放） | `"You are a coding assistant."` |
| `gen_ai.input.messages` | any | Opt-In | 输入消息列表（便于失败回放） | — |
| `gen_ai.tool.definitions` | any | Opt-In | 请求携带的工具定义列表 | — |

> 建议在 `llm_error` 中，按可用性继续补齐与 `llm_request` 相同的 `gen_ai.request.*` 字段，便于故障定位和回放。

---

### Event: `tool_result` — 工具执行结果事件

工具执行完成时记录。

**属性**：

| Key | Type | Requirement | Description | Example |
|-----|------|-------------|-------------|---------|
| `event.name` | string | Required | 固定值 `"tool_result"` | `"tool_result"` |
| `event.timestamp` | string | Required | ISO 8601 时间戳 | `"2026-03-21T16:00:02Z"` |
| `event.sequence` | int | Required | 会话内单调递增序号 | `4` |
| `prompt.id` | string | Required | 关联 prompt 的 UUID | `"uuid-xxx"` |
| `gen_ai.operation.name` | string | Required | 固定为 `"execute_tool"` | `"execute_tool"` |
| `gen_ai.tool.name` | string | Required | 工具名称 | `"code_edit"`, `"web_search"` |
| `success` | string | Required | 是否成功 | `"true"`, `"false"` |
| `duration_ms` | int | Required | 执行耗时（毫秒） | `120` |
| `error.type` | string | Conditionally Required | 错误类型（失败时） | `"file_not_found"` |
| `gen_ai.tool.call.id` | string | Recommended | 工具调用唯一标识 | `"call_mszuSIzqtI65i1wAUOE8w5H4"` |
| `gen_ai.tool.type` | string | Recommended | 工具类型 | `"function"`, `"extension"`, `"datastore"` |
| `gen_ai.tool.description` | string | Recommended | 工具描述 | `"Read file content"` |
| `tool_result_size_bytes` | int | Recommended | 工具返回结果大小（字节） | `4096` |
| `tool_source` | string | Recommended | 工具来源 | `"builtin"`, `"mcp"`, `"plugin"`, `"custom"` |
| `mcp.server_id` | string | Opt-In | MCP Server 标识（MCP 工具时） | `"github-mcp"` |
| `gen_ai.tool.call.arguments` | any | Opt-In | 工具调用参数（需显式开启） | `{"path":"src/main.ts"}` |
| `gen_ai.tool.call.result` | any | Opt-In | 工具调用结果（需显式开启） | `{"updated":true}` |

**Coding Agent / MCP 补充属性**：

| Key | Type | Requirement | Description | Example |
|-----|------|-------------|-------------|---------|
| `code.tool.name` | string | Recommended | 命令名 | `"bash"`, `"read"`, `"edit"` |
| `code.tool.exit_code` | int | Recommended | 命令返回码 | `0`, `1` |
| `code.file.operation` | string | Recommended | 文件操作类型 | `"read"`, `"write"`, `"delete"` |
| `code.file.path` | string | Recommended | 操作文件路径 | `"/src/main.ts"` |

---

### Event: `tool_decision` — 工具决策事件

工具使用权限决策时记录。

**属性**：

| Key | Type | Requirement | Description | Example |
|-----|------|-------------|-------------|---------|
| `event.name` | string | Required | 固定值 `"tool_decision"` | `"tool_decision"` |
| `event.timestamp` | string | Required | ISO 8601 时间戳 | `"2026-03-21T16:00:02Z"` |
| `event.sequence` | int | Required | 会话内单调递增序号 | `5` |
| `prompt.id` | string | Required | 关联 prompt 的 UUID | `"uuid-xxx"` |
| `gen_ai.operation.name` | string | Recommended | 关联的工具执行操作，固定为 `"execute_tool"` | `"execute_tool"` |
| `gen_ai.tool.name` | string | Required | 工具名称 | `"code_edit"` |
| `decision` | string | Required | 决策结果 | `"accept"`, `"reject"` |
| `source` | string | Required | 决策来源 | `"config"`, `"hook"`, `"user"`, `"policy"`, `"auto"` |
| `gen_ai.tool.call.id` | string | Recommended | 工具调用唯一标识 | `"call_mszuSIzqtI65i1wAUOE8w5H4"` |
| `gen_ai.tool.type` | string | Recommended | 工具类型 | `"function"`, `"extension"`, `"datastore"` |
| `gen_ai.tool.description` | string | Recommended | 工具描述 | `"Write file content"` |
| `mcp.server_id` | string | Opt-In | MCP Server 标识（MCP 工具时） | `"github-mcp"` |
| `gen_ai.tool.call.arguments` | any | Opt-In | 候选工具参数（需显式开启） | `{"path":"src/main.ts"}` |

---

### Event: `agent_step` — Agent 推理步骤事件

Agent 完成一个推理/规划步骤时记录，用于追踪 Agent 的多步推理过程。

**属性**：

| Key | Type | Requirement | Description | Example |
|-----|------|-------------|-------------|---------|
| `event.name` | string | Required | 固定值 `"agent_step"` | `"agent_step"` |
| `event.timestamp` | string | Required | ISO 8601 时间戳 | `"2026-03-21T16:00:03Z"` |
| `event.sequence` | int | Required | 会话内单调递增序号 | `6` |
| `prompt.id` | string | Required | 关联 prompt 的 UUID | `"uuid-xxx"` |
| `step_type` | string | Required | 步骤类型 | `"planning"`, `"reasoning"`, `"action"`, `"observation"`, `"reflection"` |
| `step_index` | int | Required | 当前步骤序号（从 0 开始） | `0` |
| `duration_ms` | int | Required | 步骤耗时（毫秒） | `3500` |
| `gen_ai.operation.name` | string | Recommended | 当前步骤对应的 GenAI 操作 | `"chat"`, `"execute_tool"`, `"retrieval"` |
| `gen_ai.request.model` | string | Recommended | 本步骤使用的模型 | `"claude-sonnet-4-6"` |
| `gen_ai.conversation.id` | string | Recommended | 会话唯一标识 | `"conv_5j66UpCpwteGg4YSxUnt7lPY"` |
| `gen_ai.response.id` | string | Recommended | 与本步骤关联的响应标识 | `"msg_abc123"` |
| `gen_ai.system_instructions` | string | Opt-In | 该步骤使用的系统指令 | `"You are a coding assistant."` |
| `gen_ai.input.messages` | any | Opt-In | 该步骤的输入消息 | — |
| `gen_ai.output.messages` | any | Opt-In | 该步骤的输出消息 | — |

---

## 四、上报示例

### OTel 标准事件示例

```json
{
  "name": "gen_ai.client.inference.operation.details",
  "attributes": {
    "gen_ai.operation.name": "chat",
    "gen_ai.request.model": "claude-sonnet-4-6",
    "gen_ai.response.model": "claude-sonnet-4-6-20250514",
    "gen_ai.usage.input_tokens": 1500,
    "gen_ai.usage.output_tokens": 800,
    "gen_ai.response.finish_reasons": ["stop"],
    "gen_ai.response.id": "msg_abc123",
    "server.address": "api.anthropic.com",
    "ant.username": "100001",
    "ant.uid": "2088xxxx"
  }
}
```

### AI Vision业务事件示例

```json
{
  "name": "llm_request",
  "attributes": {
    "event.name": "llm_request",
    "event.timestamp": "2026-03-21T16:00:01Z",
    "event.sequence": 2,
    "prompt.id": "550e8400-e29b-41d4-a716-446655440000",
    "gen_ai.operation.name": "chat",
    "gen_ai.request.model": "claude-sonnet-4-6",
    "gen_ai.response.model": "claude-sonnet-4-6-20250514",
    "gen_ai.response.id": "msg_abc123",
    "gen_ai.system_instructions": "You are a coding assistant.",
    "gen_ai.input.messages": [
      {
        "role": "user",
        "parts": [
          { "type": "text", "content": "帮我优化这段代码" }
        ]
      }
    ],
    "gen_ai.output.messages": [
      {
        "role": "assistant",
        "parts": [
          { "type": "text", "content": "我先检查这段代码的瓶颈。" }
        ],
        "finish_reason": "stop"
      }
    ],
    "duration_ms": 2350,
    "gen_ai.usage.input_tokens": 1500,
    "gen_ai.usage.output_tokens": 800,
    "gen_ai.usage.cache_read.input_tokens": 500,
    "gen_ai.provider.name": "anthropic",
    "ant.username": "100001",
    "ant.uid": "2088xxxx"
  }
}
```

### 推理链路还原示例

通过 `prompt.id` 关联同一个用户 prompt 产生的所有事件：

```
prompt.id = "550e8400-e29b-41d4-a716-446655440000"

  [seq=1] user_prompt     → 用户输入 "帮我优化这段代码"
  [seq=2] agent_step      → planning: 分析代码结构
  [seq=3] llm_request     → claude-sonnet-4-6, 1500 input, 800 output
  [seq=4] tool_decision   → code_edit: accept (source=auto)
  [seq=5] tool_result     → code_edit: success, 120ms
  [seq=6] agent_step      → reflection: 验证修改结果
  [seq=7] llm_request     → claude-sonnet-4-6, 2000 input, 500 output
```

---

## 五、与 OTel 标准的属性映射

| OTel 标准属性 | AI Vision属性 | 映射说明 |
|--------------|---------|---------|
| `gen_ai.operation.name` | `llm_request`, `llm_error`, `tool_result`, `tool_decision`, `agent_step` | 业务事件直接复用标准操作名 |
| `gen_ai.request.model` | `llm_request`, `llm_error`, `agent_step` | 替代原 `model` 简写 |
| `gen_ai.provider.name` | `llm_request`, `llm_error` | 替代原 `provider` 简写 |
| `gen_ai.usage.input_tokens` | `llm_request` | 替代原 `input_tokens` |
| `gen_ai.usage.output_tokens` | `llm_request` | 替代原 `output_tokens` |
| `gen_ai.usage.cache_read.input_tokens` | `llm_request` | 替代原 `cache_read_tokens` |
| `gen_ai.usage.cache_creation.input_tokens` | `llm_request` | 替代原 `cache_creation_tokens` |
| `gen_ai.system_instructions` | `llm_request`, `llm_error`, `agent_step` | 统一记录系统指令，类型收敛为 `string` |
| `gen_ai.input.messages` | `user_prompt`, `llm_request`, `llm_error`, `agent_step` | 统一记录消息输入内容 |
| `gen_ai.output.messages` | `llm_request`, `agent_step` | 统一记录消息输出内容 |
| `gen_ai.tool.name` | `tool_result`, `tool_decision` | 替代原 `tool_name` |
| `gen_ai.tool.call.arguments` | `tool_result`, `tool_decision` | 替代原 `tool_parameters` 一类工具参数字段 |
| `error.type` | `llm_error`, `tool_result` | 替代原 `error` 字段，统一错误类型命名 |
| `http.response.status_code` | `llm_error` | 替代原 `status_code` |
| — | `ant.username` | AI Vision扩展，OTel 标准中无对应 |
| — | `ant.uid` | AI Vision扩展，外部客户 UID（可用时） |

---

## 六、通用枚举值

### `gen_ai.operation.name` 预定义值

| Value | Description |
|-------|-------------|
| `chat` | 聊天补全操作 |
| `create_agent` | 创建 GenAI Agent |
| `embeddings` | 嵌入操作 |
| `execute_tool` | 执行工具 |
| `generate_content` | 多模态内容生成 |
| `invoke_agent` | 调用 GenAI Agent |
| `retrieval` | 检索操作 |
| `text_completion` | 文本补全操作（Legacy） |

### `gen_ai.output.type` 预定义值

| Value | Description |
|-------|-------------|
| `image` | 图片 |
| `json` | JSON 对象 |
| `speech` | 语音 |
| `text` | 纯文本 |

---

## 七、安全与隐私

- **遥测默认关闭**，需显式设置 `AGENT_ENABLE_TELEMETRY=1` 开启
- **用户 prompt 内容默认不采集**，仅记录长度；需设置 `AGENT_LOG_USER_PROMPTS=1` 开启
- **工具参数详情默认不采集**，需设置 `AGENT_LOG_TOOL_DETAILS=1` 开启
- **OTel 标准的 Opt-In 属性**（`gen_ai.input.messages`、`gen_ai.output.messages`、`gen_ai.system_instructions`、`gen_ai.tool.call.arguments`、`gen_ai.tool.call.result`）默认不采集
- 认证用户的 `ant.username` 和外部客户的 `ant.uid` 可能会包含在遥测属性中，如有隐私顾虑，请在后端配置过滤规则

### 隐私控制环境变量

| 环境变量 | 描述 | 默认值 |
|---------|------|--------|
| `AGENT_ENABLE_TELEMETRY` | 启用遥测采集 | `0`（关闭） |
| `AGENT_LOG_USER_PROMPTS` | 记录用户 prompt 内容 | `0`（关闭） |
| `AGENT_LOG_TOOL_DETAILS` | 记录工具参数/结果详情 | `0`（关闭） |
