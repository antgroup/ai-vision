# OpenTelemetry GenAI Semantic Conventions - Model Spans

> **Status**: Development
> **Source**: [Semantic Conventions for Generative Client AI Spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/)

## 概述

Model Spans 描述客户端对 GenAI 模型或服务的调用，涵盖推理（Inference）、嵌入（Embeddings）、检索（Retrievals）和工具执行（Execute Tool）四种操作类型。

---

## Inference Span

表示客户端对 GenAI 模型的推理调用，模型基于输入 prompt 生成响应或请求工具调用。

| Property | Value |
|----------|-------|
| **Span Name** | `{gen_ai.operation.name} {gen_ai.request.model}` |
| **Span Kind** | `CLIENT`（同进程模型 MAY 使用 `INTERNAL`） |

### 必需属性 (Required)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `gen_ai.operation.name` | string | 操作名称 | `chat`, `generate_content` |
| `gen_ai.provider.name` | string | GenAI 提供商标识 | `openai`, `aws.bedrock` |

### 条件必需属性 (Conditionally Required)

| Key | Condition | Type | Description | Example |
|-----|-----------|------|-------------|---------|
| `error.type` | 操作出错时 | string | 错误类型 | `timeout`, `500` |
| `gen_ai.conversation.id` | 可用时 | string | 会话唯一标识 | `conv_5j66UpCpwteGg4YSxUnt7lPY` |
| `gen_ai.output.type` | 请求包含输出格式时 | string | 请求的内容类型 | `text`, `json`, `image` |
| `gen_ai.request.choice.count` | 可用且 !=1 时 | int | 候选补全数量 | `3` |
| `gen_ai.request.model` | 可用时 | string | 请求模型名称 | `gpt-4` |
| `gen_ai.request.seed` | 请求包含 seed 时 | int | 随机种子 | `100` |
| `server.port` | `server.address` 已设置时 | int | 服务端口 | `443` |

### 推荐属性 (Recommended)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `gen_ai.request.frequency_penalty` | double | 频率惩罚 | `0.1` |
| `gen_ai.request.max_tokens` | int | 最大生成 token 数 | `100` |
| `gen_ai.request.presence_penalty` | double | 存在惩罚 | `0.1` |
| `gen_ai.request.stop_sequences` | string[] | 停止序列列表 | `["forest", "lived"]` |
| `gen_ai.request.temperature` | double | 温度参数 | `0.0` |
| `gen_ai.request.top_k` | double | Top-k 采样参数 | `1.0` |
| `gen_ai.request.top_p` | double | Top-p 采样参数 | `1.0` |
| `gen_ai.response.finish_reasons` | string[] | 停止原因数组 | `["stop"]` |
| `gen_ai.response.id` | string | 补全唯一标识 | `chatcmpl-123` |
| `gen_ai.response.model` | string | 响应模型名称 | `gpt-4-0613` |
| `gen_ai.usage.cache_creation.input_tokens` | int | 写入缓存的输入 token 数 | `25` |
| `gen_ai.usage.cache_read.input_tokens` | int | 从缓存读取的输入 token 数 | `50` |
| `gen_ai.usage.input_tokens` | int | 输入 token 总数（含缓存） | `100` |
| `gen_ai.usage.output_tokens` | int | 输出 token 数 | `180` |
| `server.address` | string | 服务地址 | `example.com` |

### Opt-In 属性

| Key | Type | Description |
|-----|------|-------------|
| `gen_ai.input.messages` | any | 聊天历史（⚠️ 敏感信息） |
| `gen_ai.output.messages` | any | 模型返回消息 |
| `gen_ai.system_instructions` | any | 系统指令 |
| `gen_ai.tool.definitions` | any | 工具定义列表 |

### Span 创建时应提供的采样决策属性

- `gen_ai.operation.name`
- `gen_ai.provider.name`
- `gen_ai.request.model`
- `server.address`
- `server.port`

---

## Embeddings Span

描述嵌入请求 — 基于输入生成嵌入向量。

| Property | Value |
|----------|-------|
| **operation.name** | `embeddings` |
| **Span Name** | `{gen_ai.operation.name} {gen_ai.request.model}` |
| **Span Kind** | `CLIENT` |

### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `embeddings` |
| `gen_ai.provider.name` | Required | string | 提供商标识 | `openai` |
| `error.type` | Conditionally Required | string | 错误类型 | `timeout` |
| `gen_ai.request.model` | Conditionally Required | string | 模型名称 | `text-embedding-ada-002` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.embeddings.dimension.count` | Recommended | int | 输出嵌入维度数 | `1024` |
| `gen_ai.request.encoding_formats` | Recommended | string[] | 请求的编码格式 | `["float", "binary"]` |
| `gen_ai.usage.input_tokens` | Recommended | int | 输入 token 数 | `100` |
| `server.address` | Recommended | string | 服务地址 | `example.com` |

---

## Retrievals Span

描述检索请求 — 从向量数据库或搜索系统中检索相关信息。

| Property | Value |
|----------|-------|
| **operation.name** | `retrieval` |
| **Span Name** | `{gen_ai.operation.name} {gen_ai.data_source.id}` |
| **Span Kind** | `CLIENT` |

### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `retrieval` |
| `error.type` | Conditionally Required | string | 错误类型 | `timeout` |
| `gen_ai.data_source.id` | Conditionally Required | string | 数据源标识 | `H7STPQYOND` |
| `gen_ai.provider.name` | Conditionally Required | string | 提供商标识 | `openai` |
| `gen_ai.request.model` | Conditionally Required | string | 模型名称 | `gpt-4` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.request.top_k` | Recommended | double | Top-k 参数 | `1.0` |
| `server.address` | Recommended | string | 服务地址 | `example.com` |
| `gen_ai.retrieval.documents` | Opt-In | any | 检索到的文档列表 |  |
| `gen_ai.retrieval.query.text` | Opt-In | string | 检索查询文本（⚠️ 敏感信息） |  |

### 检索文档 JSON 结构示例

```json
[
  { "id": "doc_123", "score": 0.95 },
  { "id": "doc_456", "score": 0.87 },
  { "id": "doc_789", "score": 0.82 }
]
```

---

## Execute Tool Span

描述工具执行调用。

| Property | Value |
|----------|-------|
| **operation.name** | `execute_tool` |
| **Span Name** | `execute_tool {gen_ai.tool.name}` |
| **Span Kind** | `INTERNAL` |

### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `execute_tool` |
| `error.type` | Conditionally Required | string | 错误类型 | `timeout` |
| `gen_ai.tool.call.id` | Recommended | string | 工具调用标识 | `call_mszuSIzqtI65i1wAUOE8w5H4` |
| `gen_ai.tool.description` | Recommended | string | 工具描述 | `Multiply two numbers` |
| `gen_ai.tool.name` | Recommended | string | 工具名称 | `Flights` |
| `gen_ai.tool.type` | Recommended | string | 工具类型 | `function`, `extension`, `datastore` |
| `gen_ai.tool.call.arguments` | Opt-In | any | 工具调用参数（⚠️ 敏感信息） |  |
| `gen_ai.tool.call.result` | Opt-In | any | 工具调用结果（⚠️ 敏感信息） |  |

### 工具类型说明

| Type | Description |
|------|-------------|
| `extension` | Agent 侧执行，直接调用外部 API |
| `function` | 客户端侧执行，Agent 生成参数，客户端执行逻辑 |
| `datastore` | 访问和查询结构化/非结构化外部数据 |

---

## 内容捕获策略

OpenTelemetry 插桩 **SHOULD NOT** 默认捕获指令、输入和输出，但 **SHOULD** 提供 opt-in 选项。推荐的使用模式：

1. **默认**：不记录指令、输入或输出
2. **Span 属性记录**：通过 `gen_ai.system_instructions`、`gen_ai.input.messages`、`gen_ai.output.messages` 属性记录（适合预生产环境）
3. **外部存储**：将内容存储到外部系统，在 span 上记录引用（推荐生产环境使用，支持独立访问控制）

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
