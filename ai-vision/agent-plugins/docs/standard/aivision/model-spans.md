# AI Vision Agent 可观测性规范 — Model Spans

> **版本**: v2.0
> **基于**: [OTel GenAI Model Spans](../otel/model-spans.md) + [AI Vision 身份标识规范](../custom/observability-identity-spec.md)
> **协议**: OpenTelemetry Traces

## 概述

本规范定义AI Vision Agent 系统的 Model Spans 语义规范。在 OTel 官方 GenAI Model Spans 基础上，补充AI Vision侧统一身份字段 `ant.username` 和 `ant.uid`，覆盖推理（Inference）、嵌入（Embeddings）、检索（Retrievals）和工具执行（Execute Tool）四种操作类型。

---

## 一、通用身份属性（所有 Span 共享）

所有 Model Span 必须携带以下AI Vision 身份标识属性。`resource.md` 中定义的公共 Resource Attributes 不在本规范重复展开。

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `ant.username` | string | 用户标识 | `"100001"` |
| `ant.uid` | string | 外部客户 UID（可用时） | `"2088xxxx"` |

---

## 二、Inference Span（推理调用）

描述客户端对 GenAI 模型的推理调用，模型基于输入 prompt 生成响应或请求工具调用。

| Property | Value |
|----------|-------|
| **Span Name** | `{gen_ai.operation.name} {gen_ai.request.model}` |
| **Span Kind** | `CLIENT`（同进程模型 MAY 使用 `INTERNAL`） |

### 必需属性 (Required)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `gen_ai.operation.name` | string | 操作名称 | `"chat"`, `"generate_content"` |
| `gen_ai.provider.name` | string | GenAI 提供商标识 | `"anthropic"`, `"openai"`, `"qwen"` |
| `ant.username` | string | 用户标识 | `"100001"` |

### 条件必需属性 (Conditionally Required)

| Key | Condition | Type | Description | Example |
|-----|-----------|------|-------------|---------|
| `error.type` | 操作出错时 | string | 错误类型 | `"timeout"`, `"500"` |
| `gen_ai.conversation.id` | 可用时 | string | 会话唯一标识 | `"conv_5j66UpCpwteGg4YSxUnt7lPY"` |
| `gen_ai.output.type` | 请求包含输出格式时 | string | 请求的内容类型 | `"text"`, `"json"`, `"image"` |
| `gen_ai.request.choice.count` | 可用且 ≠1 时 | int | 候选补全数量 | `3` |
| `gen_ai.request.model` | 可用时 | string | 请求模型名称 | `"claude-sonnet-4-6"` |
| `gen_ai.request.seed` | 请求包含 seed 时 | int | 随机种子 | `100` |
| `ant.uid` | 可用时 | string | 外部客户 UID | `"2088xxxx"` |
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
| `gen_ai.response.id` | string | 补全唯一标识 | `"chatcmpl-123"` |
| `gen_ai.response.model` | string | 响应模型名称 | `"claude-sonnet-4-6-20250514"` |
| `gen_ai.usage.cache_creation.input_tokens` | int | 写入缓存的输入 token 数 | `25` |
| `gen_ai.usage.cache_read.input_tokens` | int | 从缓存读取的输入 token 数 | `50` |
| `gen_ai.usage.input_tokens` | int | 输入 token 总数（含缓存） | `100` |
| `gen_ai.usage.output_tokens` | int | 输出 token 数 | `180` |
| `server.address` | string | 服务地址 | `"api.anthropic.com"` |

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

## 三、Embeddings Span（嵌入操作）

描述嵌入请求 — 基于输入生成嵌入向量。

除以下属性外，仍应携带第一章中的通用身份属性（包括 `ant.username` 和 `ant.uid`）。

| Property | Value |
|----------|-------|
| **operation.name** | `embeddings` |
| **Span Name** | `embeddings {gen_ai.request.model}` |
| **Span Kind** | `CLIENT` |

### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 固定为 `"embeddings"` | `"embeddings"` |
| `gen_ai.provider.name` | Required | string | 提供商标识 | `"openai"`, `"qwen"` |
| `ant.username` | Required | string | 用户标识 | `"100001"` |
| `ant.uid` | Conditionally Required | string | 外部客户 UID（可用时） | `"2088xxxx"` |
| `error.type` | Conditionally Required | string | 错误类型（出错时） | `"timeout"` |
| `gen_ai.request.model` | Conditionally Required | string | 模型名称（可用时） | `"text-embedding-v1"` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.embeddings.dimension.count` | Recommended | int | 输出嵌入维度数 | `1024` |
| `gen_ai.request.encoding_formats` | Recommended | string[] | 请求的编码格式 | `["float", "binary"]` |
| `gen_ai.usage.input_tokens` | Recommended | int | 输入 token 数 | `100` |
| `server.address` | Recommended | string | 服务地址 | `"dashscope.aliyuncs.com"` |

---

## 四、Retrievals Span（检索操作）

描述检索请求 — 从向量数据库或搜索系统中检索相关信息。

| Property | Value |
|----------|-------|
| **operation.name** | `retrieval` |
| **Span Name** | `retrieval {gen_ai.data_source.id}` |
| **Span Kind** | `CLIENT` |

### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 固定为 `"retrieval"` | `"retrieval"` |
| `ant.username` | Required | string | 用户标识 | `"100001"` |
| `ant.uid` | Conditionally Required | string | 外部客户 UID（可用时） | `"2088xxxx"` |
| `error.type` | Conditionally Required | string | 错误类型（出错时） | `"timeout"` |
| `gen_ai.data_source.id` | Conditionally Required | string | 数据源标识（可用时） | `"H7STPQYOND"` |
| `gen_ai.provider.name` | Conditionally Required | string | 提供商标识（可用时） | `"openai"` |
| `gen_ai.request.model` | Conditionally Required | string | 模型名称（可用时） | `"gpt-4"` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.request.top_k` | Recommended | double | Top-k 参数 | `3` |
| `server.address` | Recommended | string | 服务地址 | `"zsearch.git.example.com"` |
| `gen_ai.retrieval.documents` | Opt-In | any | 检索到的文档列表 | — |
| `gen_ai.retrieval.query.text` | Opt-In | string | 检索查询文本（⚠️ 敏感信息） | — |

### 检索文档 JSON 结构示例

```json
[
  { "id": "doc_123", "score": 0.95 },
  { "id": "doc_456", "score": 0.87 },
  { "id": "doc_789", "score": 0.82 }
]
```

---

## 五、Execute Tool Span（工具执行）

描述工具执行调用。

| Property | Value |
|----------|-------|
| **operation.name** | `execute_tool` |
| **Span Name** | `execute_tool {gen_ai.tool.name}` |
| **Span Kind** | `INTERNAL` |

### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 固定为 `"execute_tool"` | `"execute_tool"` |
| `ant.username` | Required | string | 用户标识 | `"100001"` |
| `ant.uid` | Conditionally Required | string | 外部客户 UID（可用时） | `"2088xxxx"` |
| `error.type` | Conditionally Required | string | 错误类型（出错时） | `"timeout"` |
| `gen_ai.tool.call.id` | Recommended | string | 工具调用标识 | `"call_mszuSIzqtI65i1wAUOE8w5H4"` |
| `gen_ai.tool.description` | Recommended | string | 工具描述 | `"Read file content"` |
| `gen_ai.tool.name` | Recommended | string | 工具名称 | `"Read"`, `"Edit"`, `"Bash"` |
| `gen_ai.tool.type` | Recommended | string | 工具类型 | `"function"`, `"extension"`, `"datastore"` |
| `gen_ai.tool.call.arguments` | Opt-In | any | 工具调用参数（⚠️ 敏感信息） | — |
| `gen_ai.tool.call.result` | Opt-In | any | 工具调用结果（⚠️ 敏感信息） | — |

### 工具类型说明

| Type | Description | AI Vision示例 |
|------|-------------|---------|
| `function` | 客户端侧执行，Agent 生成参数，客户端执行逻辑 | Claude Code 的 Read、Edit、Bash 工具 |
| `extension` | Agent 侧执行，直接调用外部 API | MCP Server 工具 |
| `datastore` | 访问和查询结构化/非结构化外部数据 | 向量数据库查询 |

### Coding Agent 扩展字段

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `code.tool.name` | Recommended | string | 命令名 | `"bash"`, `"read"`, `"edit"` |
| `code.tool.exit_code` | Recommended | int | 命令返回码 | `0`, `1` |
| `code.file.operation` | Recommended | string | 文件操作类型 | `"read"`, `"write"`, `"delete"` |
| `code.file.path` | Recommended | string | 操作文件路径 | `"/src/main.py"` |

---

## 六、内容捕获策略

OpenTelemetry 插桩 **SHOULD NOT** 默认捕获指令、输入和输出，但 **SHOULD** 提供 opt-in 选项。

| 策略 | 描述 | 适用环境 |
|------|------|---------|
| **默认** | 不记录指令、输入或输出 | 生产环境 |
| **Span 属性记录** | 通过 `gen_ai.system_instructions`、`gen_ai.input.messages`、`gen_ai.output.messages` 属性记录 | 预生产环境 |
| **外部存储** | 将内容存储到外部系统（如 Langfuse），在 Span 上记录引用 | 生产环境（推荐） |

> ⚠️ AI Vision场景下，敏感内容（用户 prompt、模型输出）建议通过 Langfuse Trace 存储，Model Span 仅记录 token 用量和性能指标。

---

## 七、典型链路示例

### Coding Agent 推理链路

```
Inference Span (chat claude-sonnet-4-6)
  ant.username = "100001"
  ant.uid = "2088xxxx"
  gen_ai.provider.name = "anthropic"
  gen_ai.request.model = "claude-sonnet-4-6"
  gen_ai.usage.input_tokens = 1500
  gen_ai.usage.output_tokens = 800
  gen_ai.usage.cache_read.input_tokens = 500
  gen_ai.response.finish_reasons = ["end_turn"]
  │
  ├── Execute Tool Span (execute_tool Edit)
  │     gen_ai.tool.name = "Edit"
  │     gen_ai.tool.type = "function"
  │     code.file.path = "/src/main.py"
  │     code.file.operation = "write"
  │
  ├── Execute Tool Span (execute_tool Bash)
  │     gen_ai.tool.name = "Bash"
  │     gen_ai.tool.type = "function"
  │     code.tool.exit_code = 0
  │
  └── Execute Tool Span (execute_tool mcp_github)
        gen_ai.tool.name = "create_pull_request"
        gen_ai.tool.type = "extension"
```

### RAG Agent 推理链路

```
Inference Span (chat qwen-max)
  ant.username = "100001"
  ant.uid = "2088xxxx"
  gen_ai.provider.name = "qwen"
  gen_ai.request.model = "qwen-max"
  │
  ├── Retrievals Span (retrieval zsearch-knowledge-base)
  │     gen_ai.data_source.id = "zsearch-knowledge-base"
  │     gen_ai.request.top_k = 3
  │
  ├── Embeddings Span (embeddings text-embedding-v1)
  │     gen_ai.request.model = "text-embedding-v1"
  │     gen_ai.usage.input_tokens = 50
  │     gen_ai.embeddings.dimension.count = 1024
  │
  └── Inference Span (chat qwen-max)
        gen_ai.usage.input_tokens = 2000
        gen_ai.usage.output_tokens = 800
```

---

## 八、与 OTel 标准的属性映射

| OTel 标准属性 | AI Vision属性 | 映射说明 |
|--------------|---------|---------|
| `gen_ai.operation.name` | `gen_ai.operation.name` | 完全保留 |
| `gen_ai.provider.name` | `gen_ai.provider.name` | 完全保留 |
| `gen_ai.request.model` | `gen_ai.request.model` | 完全保留 |
| `gen_ai.usage.*` | `gen_ai.usage.*` | 完全保留 |
| — | `ant.username` | AI Vision扩展，OTel 标准中无对应 |
| — | `ant.uid` | AI Vision扩展，外部客户 UID（可用时） |
| — | `code.tool.*` | AI Vision扩展，Coding Agent 工具字段 |
| — | `code.file.*` | AI Vision扩展，Coding Agent 文件操作字段 |

---

## 九、通用枚举值

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

### `gen_ai.output.type` 预定义值

| Value | Description |
|-------|-------------|
| `text` | 纯文本 |
| `json` | JSON 对象 |
| `image` | 图片 |
| `speech` | 语音 |

---

## 十、字段来源对照

| 来源 | 字段前缀 | 说明 |
|------|---------|------|
| OTel 官方 | `gen_ai.*`, `server.*`, `error.*` | OpenTelemetry GenAI Semantic Conventions |
| AI Vision 身份标识 | `ant.username`, `ant.uid` | AI Vision 身份标识规范 |
| Coding Agent 扩展 | `code.tool.*`, `code.file.*` | Coding Agent 场景专用字段 |
