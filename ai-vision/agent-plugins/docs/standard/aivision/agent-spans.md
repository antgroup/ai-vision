# AI Vision Agent 可观测性规范 — Agent Spans

> **版本**: v2.0
> **基于**: [OTel GenAI Agent Spans](../otel/agent-spans.md) + [AI Vision Traces 规范](../custom/observability-spec-traces.md) + [AI Vision 身份标识规范](../custom/observability-identity-spec.md)
> **协议**: OpenTelemetry Traces

## 概述

本规范定义AI Vision Agent 系统的 Agent Spans 语义规范。在 OTel 官方 GenAI Agent Spans 基础上，补充AI Vision侧统一身份字段 `ant.username` 和 `ant.uid`，并结合AI Vision已有的 Traces 规范（多 Span 类型体系），形成完整的 Agent 链路追踪标准。

### Span 类型体系

AI Vision Agent Spans 在 OTel 标准的 Create Agent / Invoke Agent / Execute Tool 基础上，扩展了AI Vision业务场景所需的 Span 类型：

```
┌─────────────────────────────────────────────────────────────────┐
│                    AI Vision Agent Span 体系                          │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  OTel 标准层                                               │  │
│  │  Create Agent / Invoke Agent / Execute Tool               │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  AI Vision扩展层                                                │  │
│  │  CHAT / AGENT / LLM / TOOL / MCP / RETRIEVER /           │  │
│  │  RERANKER / EMBEDDING / GUARDRAIL                         │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  AI Vision 身份层（ant.username / ant.uid）                       │  │
│  │  所有 Span 共享的身份标识属性                                │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 一、通用身份属性（所有 Span 共享）

所有 Span 必须携带以下AI Vision 身份标识属性。`resource.md` 中定义的公共 Resource Attributes 不在本规范重复展开。

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `ant.username` | string | 用户标识 | `"100001"` |
| `ant.uid` | string | 外部客户 UID（可用时） | `"2088xxxx"` |

---

## 二、OTel 标准 Agent Spans

基于 [OTel GenAI Agent Spans 规范](../otel/agent-spans.md)，在AI Vision场景下的适配。

### Create Agent Span

描述 GenAI Agent 的创建，通常适用于远程 Agent 服务场景。

| Property | Value |
|----------|-------|
| **operation.name** | `create_agent` |
| **Span Name** | `create_agent {gen_ai.agent.name}` |
| **Span Kind** | `CLIENT` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `create_agent` |
| `gen_ai.provider.name` | Required | string | GenAI 提供商标识 | `anthropic`, `openai` |
| `ant.username` | Required | string | 用户标识 | `"100001"` |
| `ant.uid` | Conditionally Required | string | 外部客户 UID（可用时） | `"2088xxxx"` |
| `error.type` | Conditionally Required | string | 错误类型（操作出错时） | `timeout`, `500` |
| `gen_ai.agent.description` | Conditionally Required | string | Agent 描述（应用提供时） | `Helps with math problems` |
| `gen_ai.agent.id` | Conditionally Required | string | Agent 唯一标识（适用时） | `asst_5j66UpCpwteGg4YSxUnt7lPY` |
| `gen_ai.agent.name` | Conditionally Required | string | Agent 名称（应用提供时） | `Math Tutor` |
| `gen_ai.agent.version` | Conditionally Required | string | Agent 版本（应用提供时） | `1.0.0` |
| `gen_ai.request.model` | Conditionally Required | string | 请求模型名称（可用时） | `claude-sonnet-4-6` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `server.address` | Recommended | string | 服务地址 | `api.anthropic.com` |
| `gen_ai.system_instructions` | Opt-In | any | 系统指令 | — |

#### Span 创建时应提供的采样决策属性

- `gen_ai.operation.name`
- `gen_ai.provider.name`
- `gen_ai.request.model`
- `server.address`
- `server.port`

---

### Invoke Agent Span

描述 GenAI Agent 的调用。

| Property | Value |
|----------|-------|
| **operation.name** | `invoke_agent` |
| **Span Name** | `invoke_agent {gen_ai.agent.name}`（名称不可用时为 `invoke_agent`） |
| **Span Kind** | `CLIENT`（同进程 Agent MAY 使用 `INTERNAL`） |

#### Span Kind 选择指南

| Kind | 场景 | AI Vision示例 |
|------|------|---------|
| `CLIENT` | 远程 Agent 服务 | OpenAI Assistants API、AWS Bedrock Agents |
| `INTERNAL` | 进程内 Agent | LangChain agents、Claude Code 本地 Agent、OpenClaw 本地 Agent |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `invoke_agent` |
| `gen_ai.provider.name` | Required | string | GenAI 提供商标识 | `anthropic` |
| `ant.username` | Required | string | 用户标识 | `"100001"` |
| `ant.uid` | Conditionally Required | string | 外部客户 UID（可用时） | `"2088xxxx"` |
| `error.type` | Conditionally Required | string | 错误类型 | `timeout` |
| `gen_ai.agent.description` | Conditionally Required | string | Agent 描述 | `Helps with math problems` |
| `gen_ai.agent.id` | Conditionally Required | string | Agent 唯一标识 | `asst_5j66UpCpwteGg4YSxUnt7lPY` |
| `gen_ai.agent.name` | Conditionally Required | string | Agent 名称 | `Math Tutor` |
| `gen_ai.agent.version` | Conditionally Required | string | Agent 版本 | `1.0.0` |
| `gen_ai.conversation.id` | Conditionally Required | string | 会话标识 | `conv_5j66UpCpwteGg4YSxUnt7lPY` |
| `gen_ai.data_source.id` | Conditionally Required | string | 数据源标识 | `H7STPQYOND` |
| `gen_ai.output.type` | Conditionally Required | string | 内容类型 | `text`, `json` |
| `gen_ai.request.choice.count` | Conditionally Required | int | 候选数量 | `3` |
| `gen_ai.request.model` | Conditionally Required | string | 模型名称 | `claude-sonnet-4-6` |
| `gen_ai.request.seed` | Conditionally Required | int | 随机种子 | `100` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.request.frequency_penalty` | Recommended | double | 频率惩罚 | `0.1` |
| `gen_ai.request.max_tokens` | Recommended | int | 最大生成 token 数 | `100` |
| `gen_ai.request.presence_penalty` | Recommended | double | 存在惩罚 | `0.1` |
| `gen_ai.request.stop_sequences` | Recommended | string[] | 停止序列 | `["forest", "lived"]` |
| `gen_ai.request.temperature` | Recommended | double | 温度参数 | `0.0` |
| `gen_ai.request.top_p` | Recommended | double | Top-p 参数 | `1.0` |
| `gen_ai.response.finish_reasons` | Recommended | string[] | 停止原因 | `["stop"]` |
| `gen_ai.response.id` | Recommended | string | 补全标识 | `chatcmpl-123` |
| `gen_ai.response.model` | Recommended | string | 响应模型名称 | `claude-sonnet-4-6-20250514` |
| `gen_ai.usage.cache_creation.input_tokens` | Recommended | int | 写入缓存的输入 token 数 | `25` |
| `gen_ai.usage.cache_read.input_tokens` | Recommended | int | 从缓存读取的输入 token 数 | `50` |
| `gen_ai.usage.input_tokens` | Recommended | int | 输入 token 总数 | `100` |
| `gen_ai.usage.output_tokens` | Recommended | int | 输出 token 数 | `180` |
| `server.address` | Recommended | string | 服务地址 | `api.anthropic.com` |
| `gen_ai.input.messages` | Opt-In | any | 聊天历史（⚠️ 敏感信息） | — |
| `gen_ai.output.messages` | Opt-In | any | 模型返回消息（⚠️ 敏感信息） | — |
| `gen_ai.system_instructions` | Opt-In | any | 系统指令（⚠️ 敏感信息） | — |
| `gen_ai.tool.definitions` | Opt-In | any | 工具定义列表 | — |

---

## 三、AI Vision扩展 Span 类型

基于 [AI Vision Traces 规范](../custom/observability-spec-traces.md)，通过 `gen_ai.span.kind` 区分不同的 Span 类型。

### 3.1 CHAT Span（会话入口）

会话入口 Span，记录一次完整的用户对话。

| Property | Value |
|----------|-------|
| **gen_ai.span.kind** | `CHAT` |
| **Span Kind** | `SERVER` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.span.kind` | Required | string | 固定为 `CHAT` | `CHAT` |
| `gen_ai.conversation.id` | Required | string | 会话 ID | `conv-abc-123` |
| `gen_ai.chat.id` | Required | string | 单次对话 ID | `chat-456` |
| `ant.username` | Required | string | 用户标识 | `"100001"` |
| `ant.uid` | Conditionally Required | string | 外部客户 UID（可用时） | `"2088xxxx"` |
| `gen_ai.usage.input_tokens` | Recommended | int | 输入 tokens | `1500` |
| `gen_ai.usage.output_tokens` | Recommended | int | 输出 tokens | `800` |
| `gen_ai.usage.total_tokens` | Recommended | int | 总 tokens | `2300` |
| `gen_ai.response.time_to_first_token` | Recommended | long | 首 token 时间（毫秒） | `538` |
| `response.total_packets` | Recommended | long | 流式返回报文次数 | `52` |
| `response.duration` | Recommended | long | 调用总耗时（毫秒） | `13825` |
| `http.response.status_code` | Conditionally Required | int | HTTP 返回码（HTTP 调用时） | `200` |
| `gen_ai.input.messages` | Recommended | string | 输入给模型的内容（JSON） | — |
| `gen_ai.output.messages` | Recommended | string | 会话最终输出内容（JSON） | — |
| `input.intent` | Recommended | string | 从 query 识别的意图 | `document_search` |

---

### 3.2 AGENT Span（Agent 服务调用）

Agent 服务调用 Span，对应 OTel 标准的 Invoke Agent Span 在AI Vision业务场景下的扩展。

| Property | Value |
|----------|-------|
| **gen_ai.span.kind** | `AGENT` |
| **Span Kind** | `CLIENT` 或 `INTERNAL` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.span.kind` | Required | string | 固定为 `AGENT` | `AGENT` |
| `gen_ai.agent.id` | Required | string | Agent ID | `29794` |
| `gen_ai.agent.name` | Required | string | Agent 名称 | `自助保障范围` |
| `ant.username` | Required | string | 用户标识 | `"100001"` |
| `ant.uid` | Conditionally Required | string | 外部客户 UID（可用时） | `"2088xxxx"` |
| `gen_ai.agent.version` | Recommended | string | Agent 版本 | `v1.2.3` |
| `gen_ai.response.time_to_first_token` | Required | long | 首响应时间（毫秒） | `5782` |
| `http.response.status_code` | Conditionally Required | int | HTTP 返回码（HTTP 调用时） | `200` |

#### L2 扩展字段

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `traffic.source` | Recommended | string | 流量来源 | `direct`, `ad`, `organic` |
| `business.scene` | Recommended | string | 业务场景 | `chat`, `training`, `eval` |
| `business.app_id` | Recommended | string | 应用唯一标识 | `app_xxx_123` |
| `agentic.runtime.name` | Recommended | string | Runtime 名称 | `claude-code`, `codex` |
| `agentic.runtime.version` | Recommended | string | Runtime 版本 | `v1.0.0` |
| `agentic.turn.id` | Recommended | string | Turn ID | `turn-1` |

#### 评测链路字段（评测场景必填）

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `eval.trace_source` | Conditionally Required | string | trace 来源类型 | `prod`, `sample`, `synthetic` |
| `eval.dataset.id` | Conditionally Required | string | 数据集 ID | `dataset_golden_v1` |
| `eval.prompt.id` | Conditionally Required | string | Prompt 唯一标识 | `prompt_customer_service_v2` |
| `eval.case.id` | Conditionally Required | string | 案例唯一标识 | `case_001` |
| `eval.case.type` | Conditionally Required | string | 案例类型 | `golden`, `bad` |

---

### 3.3 LLM Span（模型服务调用）

一次模型服务调用的 Span。

| Property | Value |
|----------|-------|
| **gen_ai.span.kind** | `LLM` |
| **Span Kind** | `CLIENT` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.span.kind` | Required | string | 固定为 `LLM` | `LLM` |
| `gen_ai.provider.name` | Required | string | LLM 提供者 | `openai`, `anthropic`, `qwen` |
| `gen_ai.request.model` | Required | string | 请求模型名 | `claude-sonnet-4-6` |
| `gen_ai.response.model` | Recommended | string | 实际响应模型名 | `claude-sonnet-4-6-20250514` |
| `gen_ai.request.temperature` | Recommended | float | 温度值 | `0.7` |
| `gen_ai.request.max_tokens` | Recommended | int | 最大 tokens | `100` |
| `gen_ai.response.time_to_first_token` | Recommended | int | 首 token 时间（毫秒） | `2000` |
| `gen_ai.usage.input_tokens` | Recommended | int | 输入 tokens | `100` |
| `gen_ai.usage.output_tokens` | Recommended | int | 输出 tokens | `200` |
| `gen_ai.usage.total_tokens` | Recommended | int | 总 tokens | `300` |
| `gen_ai.usage.details.cache_read_input_tokens` | Recommended | int | 缓存读取 tokens | `50` |
| `gen_ai.usage.details.cache_creation_input_tokens` | Recommended | int | 缓存创建 tokens | `25` |
| `gen_ai.usage.details.cache_hit_rate` | Recommended | double | 缓存命中率 | `0.85` |
| `http.response.status_code` | Conditionally Required | int | HTTP 返回码（HTTP 调用时） | `200` |

---

### 3.4 TOOL Span（工具调用）

工具调用 Span，对应 OTel 标准的 Execute Tool Span。

| Property | Value |
|----------|-------|
| **gen_ai.span.kind** | `TOOL` |
| **Span Name** | `execute_tool {gen_ai.tool.name}` |
| **Span Kind** | `INTERNAL` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.span.kind` | Required | string | 固定为 `TOOL` | `TOOL` |
| `gen_ai.tool.call.id` | Recommended | string | Tool Call 唯一标识 | `call_mszuSIzqtI65i1wAUOE8w5H4` |
| `gen_ai.tool.name` | Recommended | string | Tool 名 | `Flights` |
| `gen_ai.tool.type` | Recommended | string | Tool 类型 | `function`, `extension`, `datastore` |

#### Coding Agent 扩展字段

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `code.tool.name` | Recommended | string | 命令名 | `bash`, `read`, `edit` |
| `code.tool.exit_code` | Recommended | int | 命令返回码 | `0`, `1` |
| `code.file.operation` | Recommended | string | 文件操作类型 | `read`, `write`, `delete` |
| `code.file.path` | Recommended | string | 操作文件路径 | `/src/main.py` |

---

### 3.5 MCP Span（MCP 协议调用）

MCP 协议调用 Span。

| Property | Value |
|----------|-------|
| **gen_ai.span.kind** | `MCP` |
| **Span Kind** | `CLIENT` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.span.kind` | Required | string | 固定为 `MCP` | `MCP` |
| `mcp.server_id` | Required | string | MCP Server ID | `mcp.example.logs` |
| `mcp.request_type` | Required | string | MCP 调用类型 | `tools/list`, `tools/call` |
| `mcp.tool_name` | Conditionally Required | string | 调用的 tool 名（tools/call 时必填） | `queryAppLogContentByTraceId` |

---

### 3.6 RETRIEVER Span（召回操作）

向量数据库或搜索系统的召回操作 Span。

| Property | Value |
|----------|-------|
| **gen_ai.span.kind** | `RETRIEVER` |
| **Span Kind** | `CLIENT` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.span.kind` | Required | string | 固定为 `RETRIEVER` | `RETRIEVER` |
| `retrieval.query` | Recommended | string | 召回操作的 query | `what is the topic?` |
| `retrieval.kind` | Required | string | 召回种类 | `vector_query`, `text_query` |
| `retrieval.system` | Required | string | 召回数据库类别 | `zsearch`, `tbase` |
| `retrieval.request.top_k` | Required | int | 召回最 K 个相近文档 | `3` |

---

### 3.7 RERANKER Span（重排操作）

内容重排 Span。

| Property | Value |
|----------|-------|
| **gen_ai.span.kind** | `RERANKER` |
| **Span Kind** | `INTERNAL` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.span.kind` | Required | string | 固定为 `RERANKER` | `RERANKER` |
| `reranker.model_name` | Required | string | 精排模型名称 | `antllm` |
| `reranker.top_k` | Recommended | int | 精排返回最 K 个文档 | `3` |

---

### 3.8 EMBEDDING Span（Embedding 操作）

文本向量化 Span。

| Property | Value |
|----------|-------|
| **gen_ai.span.kind** | `EMBEDDING` |
| **Span Kind** | `CLIENT` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.span.kind` | Required | string | 固定为 `EMBEDDING` | `EMBEDDING` |
| `gen_ai.usage.input_tokens` | Recommended | int | 输入 tokens | `10` |
| `gen_ai.usage.total_tokens` | Recommended | int | 总 tokens | `10` |
| `embedding.model_name` | Recommended | string | Embedding 模型名 | `text-embedding-v1` |

---

### 3.9 GUARDRAIL Span（拦截服务）

内容安全检查 Span。

| Property | Value |
|----------|-------|
| **gen_ai.span.kind** | `GUARDRAIL` |
| **Span Kind** | `INTERNAL` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.span.kind` | Required | string | 固定为 `GUARDRAIL` | `GUARDRAIL` |
| `guardrail.input` | Required | string | 待检测的内容 | — |
| `guardrail.result` | Required | string | 检测结果 | — |
| `guardrail.action` | Required | string | 通过/拦截等 | `pass`, `block` |

---

## 四、Token 使用字段详解

### 基础 Token 字段

| Key | Type | Description | 适用 Span |
|-----|------|-------------|----------|
| `gen_ai.usage.input_tokens` | int | 输入 tokens | CHAT, AGENT, LLM |
| `gen_ai.usage.output_tokens` | int | 输出 tokens | CHAT, AGENT, LLM |
| `gen_ai.usage.total_tokens` | int | 总 tokens | CHAT, AGENT, LLM |

### 缓存 Token 字段（扩展）

| Key | Type | Description | 适用 Span |
|-----|------|-------------|----------|
| `gen_ai.usage.details.cache_read_input_tokens` | int | 缓存读取 tokens | LLM |
| `gen_ai.usage.details.cache_creation_input_tokens` | int | 缓存创建 tokens | LLM |
| `gen_ai.usage.details.cache_hit_rate` | double | 缓存命中率 | LLM |

---

## 五、典型链路示例

### Coding Agent 对话链路

```
CHAT (conversation.id=conv-123, ant.username=100001, ant.uid=2088xxxx)
  ├── AGENT (agent.name=CodeAssistant, agentic.runtime.name=claude-code)
  │     ├── LLM (model=claude-sonnet-4-6, input_tokens=1500, output_tokens=800)
  │     ├── TOOL (tool.name=code_edit, code.file.path=/src/main.py)
  │     ├── TOOL (tool.name=bash, code.tool.exit_code=0)
  │     ├── MCP (mcp.server_id=github-mcp, mcp.tool_name=create_pull_request)
  │     └── LLM (model=claude-sonnet-4-6, input_tokens=2000, output_tokens=500)
  └── (end)
```

### RAG Agent 对话链路

```
CHAT (conversation.id=conv-456, ant.username=100001, ant.uid=2088xxxx)
  ├── AGENT (agent.name=自助保障范围, business.scene=chat)
  │     ├── GUARDRAIL (guardrail.action=pass)
  │     ├── LLM (model=qwen-max, input_tokens=500, output_tokens=100)
  │     ├── RETRIEVER (retrieval.kind=vector_query, retrieval.system=zsearch, top_k=3)
  │     ├── RERANKER (reranker.model_name=antllm, top_k=3)
  │     ├── LLM (model=qwen-max, input_tokens=2000, output_tokens=800)
  │     └── GUARDRAIL (guardrail.action=pass)
  └── (end)
```

---

## 六、与 OTel 标准的属性映射

| OTel 标准属性 | AI Vision属性 | 映射说明 |
|--------------|---------|---------|
| `gen_ai.operation.name` = `invoke_agent` | `gen_ai.span.kind` = `AGENT` | OTel 通过 operation.name 区分，AI Vision通过 span.kind 区分 |
| `gen_ai.operation.name` = `execute_tool` | `gen_ai.span.kind` = `TOOL` | 语义一致 |
| `gen_ai.operation.name` = `chat` | `gen_ai.span.kind` = `LLM` | OTel 的 chat 操作对应AI Vision的 LLM Span |
| `gen_ai.operation.name` = `embeddings` | `gen_ai.span.kind` = `EMBEDDING` | 语义一致 |
| `gen_ai.operation.name` = `retrieval` | `gen_ai.span.kind` = `RETRIEVER` | 语义一致 |
| `gen_ai.provider.name` | `gen_ai.provider.name` | AI Vision Traces 使用 OTel 标准 provider 字段 |
| — | `ant.username` | AI Vision扩展，OTel 标准中无对应 |
| — | `ant.uid` | AI Vision扩展，外部客户 UID（可用时） |
| — | `gen_ai.span.kind` | AI Vision扩展，区分 Span 类型（CHAT/AGENT/LLM/TOOL 等） |
| — | `agentic.runtime.name` | AI Vision扩展，标识 Agent Runtime |
| — | `eval.*` | AI Vision扩展，评测链路字段 |
| — | `mcp.*` | AI Vision扩展，MCP 协议字段 |

---

## 七、通用枚举值

### `gen_ai.span.kind` 预定义值

| Value | Description |
|-------|-------------|
| `CHAT` | 会话入口 |
| `AGENT` | Agent 服务调用 |
| `LLM` | 模型服务调用 |
| `TOOL` | 工具调用 |
| `MCP` | MCP 协议调用 |
| `RETRIEVER` | 召回操作 |
| `RERANKER` | 重排操作 |
| `EMBEDDING` | Embedding 操作 |
| `GUARDRAIL` | 拦截服务 |

### `gen_ai.provider.name` 预定义值

| Value | Description |
|-------|-------------|
| `anthropic` | Anthropic |
| `openai` | OpenAI |
| `qwen` | 通义千问 |
| `alibaba` | 阿里巴巴 |
| `aws.bedrock` | AWS Bedrock |
| `azure.ai.openai` | Azure OpenAI |
| `deepseek` | DeepSeek |
| `gcp.gemini` | Gemini |
| `gcp.vertex_ai` | Vertex AI |
| `mistral_ai` | Mistral AI |

### `gen_ai.tool.type` 预定义值

| Value | Description |
|-------|-------------|
| `function` | 客户端侧执行，Agent 生成参数，客户端执行逻辑 |
| `extension` | Agent 侧执行，直接调用外部 API |
| `datastore` | 访问和查询结构化/非结构化外部数据 |

---

## 八、业务自定义字段（L3）

业务方可通过 `custom.<namespace>.*` 格式添加自定义字段。

### 命名规范

```yaml
# 格式: custom.<namespace>.<field_name>
custom.arec.module_name: "intent_recognition"
custom.arec.intent: "query_balance"
custom.xxx.biz_line: "finance"
```

### 管理规范

1. **命名空间注册**：业务方需向平台注册命名空间，避免冲突
2. **类型限制**：字段值类型限制为 `string`, `number`, `boolean`, `json_string`
3. **敏感字段声明**：敏感字段需在注册时声明，平台自动脱敏
4. **审核机制**：新增命名空间需经过平台审核

---

## 九、Span 类型速查表

| Span 类型 | 核心字段 | 典型场景 |
|-----------|---------|---------|
| **CHAT** | `gen_ai.conversation.id`, `gen_ai.chat.id` | 网关层、业务应用层 |
| **AGENT** | `gen_ai.agent.id`, `gen_ai.agent.name`, `business.scene`, `eval.*` | Agent 服务调用 |
| **LLM** | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.*` | 模型服务调用 |
| **TOOL** | `gen_ai.tool.name`, `gen_ai.tool.call.id`, `code.tool.*` | 工具调用、Bash 执行 |
| **MCP** | `mcp.server_id`, `mcp.request_type`, `mcp.tool_name` | MCP 协议调用 |
| **RETRIEVER** | `retrieval.kind`, `retrieval.system`, `retrieval.request.*` | RAG 向量召回 |
| **RERANKER** | `reranker.model_name`, `reranker.top_k` | RAG 结果重排 |
| **EMBEDDING** | `embedding.model_name`, `gen_ai.usage.*` | 文本向量化 |
| **GUARDRAIL** | `guardrail.input`, `guardrail.result`, `guardrail.action` | 内容安全检查 |

---

## 十、字段来源对照

| 来源 | 字段前缀 | 说明 |
|------|---------|------|
| OTel 官方 | `gen_ai.*` | OpenTelemetry GenAI Semantic Conventions |
| AI Vision 身份标识 | `ant.username`, `ant.uid` | AI Vision 身份标识规范 |
| 阿里云 ARMS | `response.*`, `retrieval.*`, `reranker.*`, `embedding.*`, `guardrail.*` | 阿里云可观测规范 |
| Spanory 扩展 | `agentic.*`, `mcp.*` | Spanory Runtime 实践字段 |
| 评测链路 | `eval.*` | 评测场景专用字段 |
| 业务自定义 | `custom.<namespace>.*` | 业务方自定义字段 |
