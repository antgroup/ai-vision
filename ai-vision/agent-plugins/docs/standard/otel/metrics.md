# OpenTelemetry GenAI Semantic Conventions - Metrics

> **Status**: Development
> **Source**: [Semantic Conventions for Generative AI Metrics](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-metrics/)

## 概述

GenAI 指标分为两大类：
- **Client Metrics** — 客户端应用视角的操作指标
- **Server Metrics** — 模型服务端的运行指标

---

## Client Metrics

### Metric: `gen_ai.client.token.usage`

**Token 使用量直方图**（Recommended）

记录操作中使用的输入和输出 token 数量。当 GenAI 系统在流式响应中返回使用信息时 SHOULD 使用；当系统逐 token 返回时，插桩 SHOULD 计数输出 token 并记录结果。当系统同时报告 used tokens 和 billable tokens 时，MUST 报告 billable tokens。

| Property | Value |
|----------|-------|
| **Instrument Type** | Histogram |
| **Unit** | `{token}` |
| **Description** | Number of input and output tokens used |
| **ExplicitBucketBoundaries** | `[1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864]` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `chat` |
| `gen_ai.provider.name` | Required | string | GenAI 提供商标识 | `openai` |
| `gen_ai.token.type` | Required | string | Token 类型：`input` / `output` | `input` |
| `gen_ai.request.model` | Conditionally Required | string | 请求模型名称 | `gpt-4` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.response.model` | Recommended | string | 响应模型名称 | `gpt-4-0613` |
| `server.address` | Recommended | string | 服务地址 | `example.com` |

---

### Metric: `gen_ai.client.operation.duration`

**操作耗时直方图**（Required）

| Property | Value |
|----------|-------|
| **Instrument Type** | Histogram |
| **Unit** | `s` |
| **Description** | GenAI operation duration |
| **ExplicitBucketBoundaries** | `[0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92]` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `chat` |
| `gen_ai.provider.name` | Required | string | GenAI 提供商标识 | `openai` |
| `error.type` | Conditionally Required | string | 错误类型（操作出错时） | `timeout`, `500` |
| `gen_ai.request.model` | Conditionally Required | string | 请求模型名称 | `gpt-4` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.response.model` | Recommended | string | 响应模型名称 | `gpt-4-0613` |
| `server.address` | Recommended | string | 服务地址 | `example.com` |

---

## Server Metrics

### Metric: `gen_ai.server.request.duration`

**服务端请求耗时直方图**（Recommended）

报告模型服务端延迟，如 time-to-last-byte 或最后一个输出 token 的时间。

| Property | Value |
|----------|-------|
| **Instrument Type** | Histogram |
| **Unit** | `s` |
| **Description** | Generative AI server request duration |
| **ExplicitBucketBoundaries** | `[0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92]` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `chat` |
| `gen_ai.provider.name` | Required | string | GenAI 提供商标识 | `openai` |
| `error.type` | Conditionally Required | string | 错误类型 | `timeout` |
| `gen_ai.request.model` | Conditionally Required | string | 请求模型名称 | `gpt-4` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.response.model` | Recommended | string | 响应模型名称 | `gpt-4-0613` |
| `server.address` | Recommended | string | 服务地址 | `example.com` |

---

### Metric: `gen_ai.server.time_per_output_token`

**每输出 Token 耗时直方图**（Recommended）

衡量 LLM 推理 **decode 阶段** 的性能：从请求总耗时中减去首 token 生成时间，再除以首 token 之后生成的 token 数量。

| Property | Value |
|----------|-------|
| **Instrument Type** | Histogram |
| **Unit** | `s` |
| **Description** | Time per output token generated after the first token |
| **ExplicitBucketBoundaries** | `[0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 2.5]` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `chat` |
| `gen_ai.provider.name` | Required | string | GenAI 提供商标识 | `openai` |
| `gen_ai.request.model` | Conditionally Required | string | 请求模型名称 | `gpt-4` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.response.model` | Recommended | string | 响应模型名称 | `gpt-4-0613` |
| `server.address` | Recommended | string | 服务地址 | `example.com` |

---

### Metric: `gen_ai.server.time_to_first_token`

**首 Token 生成时间直方图**（Recommended）

衡量排队和 **prefill 阶段** 的耗时，对流式请求尤为重要。

| Property | Value |
|----------|-------|
| **Instrument Type** | Histogram |
| **Unit** | `s` |
| **Description** | Time to generate first token for successful responses |
| **ExplicitBucketBoundaries** | `[0.001, 0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0]` |

#### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `chat` |
| `gen_ai.provider.name` | Required | string | GenAI 提供商标识 | `openai` |
| `gen_ai.request.model` | Conditionally Required | string | 请求模型名称 | `gpt-4` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.response.model` | Recommended | string | 响应模型名称 | `gpt-4-0613` |
| `server.address` | Recommended | string | 服务地址 | `example.com` |

---

## 通用枚举值

### `gen_ai.provider.name` 预定义值

| Value | Description |
|-------|-------------|
| `anthropic` | Anthropic |
| `aws.bedrock` | AWS Bedrock |
| `azure.ai.inference` | Azure AI Inference |
| `azure.ai.openai` | Azure OpenAI |
| `cohere` | Cohere |
| `deepseek` | DeepSeek |
| `gcp.gemini` | Gemini (generativelanguage.googleapis.com) |
| `gcp.gen_ai` | Any Google generative AI endpoint |
| `gcp.vertex_ai` | Vertex AI (aiplatform.googleapis.com) |
| `groq` | Groq |
| `ibm.watsonx.ai` | IBM Watsonx AI |
| `mistral_ai` | Mistral AI |
| `openai` | OpenAI |
| `perplexity` | Perplexity |
| `x_ai` | xAI |

### `gen_ai.token.type` 预定义值

| Value | Description |
|-------|-------------|
| `input` | 输入 token（prompt、input 等） |
| `output` | 输出 token（completion、response 等） |
