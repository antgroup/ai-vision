# OpenTelemetry GenAI Semantic Conventions - Agent Spans

> **Status**: Development
> **Source**: [Semantic Conventions for GenAI Agent and Framework Spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)

## 概述

GenAI 模型可以被训练使用工具来访问实时信息或执行现实世界的操作。当模型具备推理、逻辑和外部信息访问能力，并以自主方式规划和执行任务时，就形成了 **Agent** 的概念。

Agent Spans 语义规范 **扩展并覆盖** 了 [Model Spans](./model-spans.md) 的语义规范，适用于远程 Agent 服务和本地框架中的 Agent 操作。

---

## Create Agent Span

描述 GenAI Agent 的创建，通常适用于远程 Agent 服务场景。

| Property | Value |
|----------|-------|
| **operation.name** | `create_agent` |
| **Span Name** | `create_agent {gen_ai.agent.name}` |
| **Span Kind** | `CLIENT` |

### 必需属性 (Required)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `gen_ai.operation.name` | string | 操作名称 | `create_agent` |
| `gen_ai.provider.name` | string | GenAI 提供商标识 | `openai` |

### 条件必需属性 (Conditionally Required)

| Key | Condition | Type | Description | Example |
|-----|-----------|------|-------------|---------|
| `error.type` | 操作出错时 | string | 错误类型 | `timeout`, `500` |
| `gen_ai.agent.description` | 应用提供时 | string | Agent 描述 | `Helps with math problems` |
| `gen_ai.agent.id` | 适用时 | string | Agent 唯一标识 | `asst_5j66UpCpwteGg4YSxUnt7lPY` |
| `gen_ai.agent.name` | 应用提供时 | string | Agent 名称 | `Math Tutor` |
| `gen_ai.agent.version` | 应用提供时 | string | Agent 版本 | `1.0.0` |
| `gen_ai.request.model` | 可用时 | string | 请求模型名称 | `gpt-4` |
| `server.port` | `server.address` 已设置时 | int | 服务端口 | `443` |

### 推荐属性 (Recommended)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `server.address` | string | 服务地址 | `example.com` |

### Opt-In 属性

| Key | Type | Description |
|-----|------|-------------|
| `gen_ai.system_instructions` | any | 系统指令 |

### Span 创建时应提供的采样决策属性

- `gen_ai.operation.name`
- `gen_ai.provider.name`
- `gen_ai.request.model`
- `server.address`
- `server.port`

---

## Invoke Agent Span

描述 GenAI Agent 的调用。

| Property | Value |
|----------|-------|
| **operation.name** | `invoke_agent` |
| **Span Name** | `invoke_agent {gen_ai.agent.name}`（名称不可用时为 `invoke_agent`） |
| **Span Kind** | `CLIENT`（同进程 Agent MAY 使用 `INTERNAL`） |

### Span Kind 选择指南

| Kind | 场景 |
|------|------|
| `CLIENT` | 远程 Agent 服务（如 OpenAI Assistants API、AWS Bedrock Agents） |
| `INTERNAL` | 进程内 Agent（如 LangChain agents、CrewAI agents） |

### 必需属性 (Required)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `gen_ai.operation.name` | string | 操作名称 | `invoke_agent` |
| `gen_ai.provider.name` | string | GenAI 提供商标识 | `openai` |

### 条件必需属性 (Conditionally Required)

| Key | Condition | Type | Description | Example |
|-----|-----------|------|-------------|---------|
| `error.type` | 操作出错时 | string | 错误类型 | `timeout` |
| `gen_ai.agent.description` | 可用时 | string | Agent 描述 | `Helps with math problems` |
| `gen_ai.agent.id` | 适用时 | string | Agent 唯一标识 | `asst_5j66UpCpwteGg4YSxUnt7lPY` |
| `gen_ai.agent.name` | 可用时 | string | Agent 名称 | `Math Tutor` |
| `gen_ai.agent.version` | 可用时 | string | Agent 版本 | `1.0.0` |
| `gen_ai.conversation.id` | 可用时 | string | 会话标识 | `conv_5j66UpCpwteGg4YSxUnt7lPY` |
| `gen_ai.data_source.id` | 适用时 | string | 数据源标识 | `H7STPQYOND` |
| `gen_ai.output.type` | 请求包含输出格式时 | string | 内容类型 | `text`, `json` |
| `gen_ai.request.choice.count` | 可用且 !=1 时 | int | 候选数量 | `3` |
| `gen_ai.request.model` | 可用时 | string | 模型名称 | `gpt-4` |
| `gen_ai.request.seed` | 请求包含 seed 时 | int | 随机种子 | `100` |
| `server.port` | `server.address` 已设置时 | int | 服务端口 | `443` |

### 推荐属性 (Recommended)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `gen_ai.request.frequency_penalty` | double | 频率惩罚 | `0.1` |
| `gen_ai.request.max_tokens` | int | 最大生成 token 数 | `100` |
| `gen_ai.request.presence_penalty` | double | 存在惩罚 | `0.1` |
| `gen_ai.request.stop_sequences` | string[] | 停止序列 | `["forest", "lived"]` |
| `gen_ai.request.temperature` | double | 温度参数 | `0.0` |
| `gen_ai.request.top_p` | double | Top-p 参数 | `1.0` |
| `gen_ai.response.finish_reasons` | string[] | 停止原因 | `["stop"]` |
| `gen_ai.response.id` | string | 补全标识 | `chatcmpl-123` |
| `gen_ai.response.model` | string | 响应模型名称 | `gpt-4-0613` |
| `gen_ai.usage.cache_creation.input_tokens` | int | 写入缓存的输入 token 数 | `25` |
| `gen_ai.usage.cache_read.input_tokens` | int | 从缓存读取的输入 token 数 | `50` |
| `gen_ai.usage.input_tokens` | int | 输入 token 总数 | `100` |
| `gen_ai.usage.output_tokens` | int | 输出 token 数 | `180` |
| `server.address` | string | 服务地址（span kind 为 CLIENT 时推荐） | `example.com` |

### Opt-In 属性

| Key | Type | Description |
|-----|------|-------------|
| `gen_ai.input.messages` | any | 聊天历史（⚠️ 敏感信息） |
| `gen_ai.output.messages` | any | 模型返回消息（⚠️ 敏感信息） |
| `gen_ai.system_instructions` | any | 系统指令（⚠️ 敏感信息） |
| `gen_ai.tool.definitions` | any | 工具定义列表 |

### Span 创建时应提供的采样决策属性

- `gen_ai.operation.name`
- `gen_ai.provider.name`
- `gen_ai.request.model`
- `server.address`
- `server.port`

---

## Execute Tool Span

Agent 中的工具执行请参考 [Model Spans - Execute Tool Span](./model-spans.md#execute-tool-span)。

---

## Agent 特有属性说明

### Agent 标识属性

| Key | Type | Description |
|-----|------|-------------|
| `gen_ai.agent.id` | string | Agent 唯一标识符（如 `asst_5j66UpCpwteGg4YSxUnt7lPY`） |
| `gen_ai.agent.name` | string | 人类可读的 Agent 名称（如 `Math Tutor`） |
| `gen_ai.agent.description` | string | Agent 的自由文本描述 |
| `gen_ai.agent.version` | string | Agent 版本号（如 `1.0.0`、`2025-05-01`） |

### 数据源属性

| Key | Type | Description |
|-----|------|-------------|
| `gen_ai.data_source.id` | string | AI Agent 和 RAG 应用使用的数据源标识，SHOULD 匹配 GenAI 系统使用的标识符 |

---

## 通用枚举值

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

### `gen_ai.provider.name` 预定义值

| Value | Description |
|-------|-------------|
| `anthropic` | Anthropic |
| `aws.bedrock` | AWS Bedrock |
| `azure.ai.inference` | Azure AI Inference |
| `azure.ai.openai` | Azure OpenAI |
| `cohere` | Cohere |
| `deepseek` | DeepSeek |
| `gcp.gemini` | Gemini |
| `gcp.gen_ai` | Any Google generative AI endpoint |
| `gcp.vertex_ai` | Vertex AI |
| `groq` | Groq |
| `ibm.watsonx.ai` | IBM Watsonx AI |
| `mistral_ai` | Mistral AI |
| `openai` | OpenAI |
| `perplexity` | Perplexity |
| `x_ai` | xAI |
