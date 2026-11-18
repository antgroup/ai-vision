# AI Vision Agent 可观测性规范 — Metrics

> **版本**: v2.0
> **基于**: [OTel GenAI Semantic Conventions - Metrics](../otel/metrics.md) + [AI Vision 身份标识规范](../custom/observability-identity-spec.md)
> **协议**: OpenTelemetry Metrics

## 概述

本规范定义AI Vision Agent 系统的 Metrics 语义规范。在 OTel 官方 `gen_ai.*` 指标基础上，补充AI Vision侧统一身份字段 `ant.username` 和 `ant.uid`，并保留AI Vision自有的 `agent.*` 业务指标，形成完整的指标采集标准。

### 指标分层

```
┌─────────────────────────────────────────────────────┐
│                  AI Vision Metrics 体系                    │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  OTel 标准层（gen_ai.*）                        │  │
│  │  token.usage / operation.duration / TTFT ...  │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  AI Vision业务层（agent.*）                          │  │
│  │  session / cost / tool_call / error ...       │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  AI Vision 身份层（ant.username / ant.uid）           │  │
│  │  所有指标共享的身份标识属性                       │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## 一、通用身份属性（所有指标共享）

所有 Metrics 必须携带以下AI Vision 身份标识属性。`resource.md` 中定义的公共 Resource Attributes 不在本规范重复展开。

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `ant.username` | string | 用户标识 | `"100001"` |
| `ant.uid` | string | 外部客户 UID（可用时） | `"2088xxxx"` |

---

## 二、OTel 标准指标（gen_ai.*）

基于 [OTel GenAI Metrics 规范](../otel/metrics.md)，在AI Vision场景下的适配。

### Client Metrics

#### Metric: `gen_ai.client.token.usage`

**Token 使用量直方图**（Recommended）

记录操作中使用的输入和输出 token 数量。

| Property | Value |
|----------|-------|
| **Instrument Type** | Histogram |
| **Unit** | `{token}` |
| **Description** | Number of input and output tokens used |
| **ExplicitBucketBoundaries** | `[1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864]` |

##### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `chat`, `generate_content` |
| `gen_ai.provider.name` | Required | string | GenAI 提供商标识 | `openai`, `anthropic` |
| `gen_ai.token.type` | Required | string | Token 类型 | `input`, `output` |
| `gen_ai.request.model` | Conditionally Required | string | 请求模型名称 | `claude-sonnet-4-6` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.response.model` | Recommended | string | 响应模型名称 | `claude-sonnet-4-6-20250514` |
| `server.address` | Recommended | string | 服务地址 | `api.anthropic.com` |
| `ant.username` | Required | string | 用户标识 | `"100001"` |
| `ant.uid` | Conditionally Required | string | 外部客户 UID（可用时） | `"2088xxxx"` |

---

#### Metric: `gen_ai.client.operation.duration`

**操作耗时直方图**（Required）

| Property | Value |
|----------|-------|
| **Instrument Type** | Histogram |
| **Unit** | `s` |
| **Description** | GenAI operation duration |
| **ExplicitBucketBoundaries** | `[0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92]` |

##### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `chat` |
| `gen_ai.provider.name` | Required | string | GenAI 提供商标识 | `anthropic` |
| `error.type` | Conditionally Required | string | 错误类型（操作出错时） | `timeout`, `500` |
| `gen_ai.request.model` | Conditionally Required | string | 请求模型名称 | `claude-sonnet-4-6` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.response.model` | Recommended | string | 响应模型名称 | `claude-sonnet-4-6-20250514` |
| `server.address` | Recommended | string | 服务地址 | `api.anthropic.com` |
| `ant.username` | Required | string | 用户标识 | `"100001"` |
| `ant.uid` | Conditionally Required | string | 外部客户 UID（可用时） | `"2088xxxx"` |

---

### Server Metrics

#### Metric: `gen_ai.server.request.duration`

**服务端请求耗时直方图**（Recommended）

| Property | Value |
|----------|-------|
| **Instrument Type** | Histogram |
| **Unit** | `s` |
| **Description** | Generative AI server request duration |
| **ExplicitBucketBoundaries** | `[0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92]` |

##### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `chat` |
| `gen_ai.provider.name` | Required | string | GenAI 提供商标识 | `anthropic` |
| `error.type` | Conditionally Required | string | 错误类型 | `timeout` |
| `gen_ai.request.model` | Conditionally Required | string | 请求模型名称 | `claude-sonnet-4-6` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.response.model` | Recommended | string | 响应模型名称 | `claude-sonnet-4-6-20250514` |
| `server.address` | Recommended | string | 服务地址 | `api.anthropic.com` |

---

#### Metric: `gen_ai.server.time_per_output_token`

**每输出 Token 耗时直方图**（Recommended）

衡量 LLM 推理 **decode 阶段** 的性能。

| Property | Value |
|----------|-------|
| **Instrument Type** | Histogram |
| **Unit** | `s` |
| **Description** | Time per output token generated after the first token |
| **ExplicitBucketBoundaries** | `[0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 2.5]` |

##### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `chat` |
| `gen_ai.provider.name` | Required | string | GenAI 提供商标识 | `anthropic` |
| `gen_ai.request.model` | Conditionally Required | string | 请求模型名称 | `claude-sonnet-4-6` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.response.model` | Recommended | string | 响应模型名称 | `claude-sonnet-4-6-20250514` |
| `server.address` | Recommended | string | 服务地址 | `api.anthropic.com` |

---

#### Metric: `gen_ai.server.time_to_first_token`

**首 Token 生成时间直方图**（Recommended）

衡量排队和 **prefill 阶段** 的耗时，对流式请求尤为重要。

| Property | Value |
|----------|-------|
| **Instrument Type** | Histogram |
| **Unit** | `s` |
| **Description** | Time to generate first token for successful responses |
| **ExplicitBucketBoundaries** | `[0.001, 0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0]` |

##### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `chat` |
| `gen_ai.provider.name` | Required | string | GenAI 提供商标识 | `anthropic` |
| `gen_ai.request.model` | Conditionally Required | string | 请求模型名称 | `claude-sonnet-4-6` |
| `server.port` | Conditionally Required | int | 服务端口 | `443` |
| `gen_ai.response.model` | Recommended | string | 响应模型名称 | `claude-sonnet-4-6-20250514` |
| `server.address` | Recommended | string | 服务地址 | `api.anthropic.com` |

---

## 三、AI Vision业务指标（agent.*）

AI Vision自有的业务层指标，补充 OTel 标准指标未覆盖的 Agent 运营维度。

### 指标总览

| 指标名称 | 类型 | 单位 | Requirement | 描述 |
|----------|------|------|-------------|------|
| `agent.session.count` | Counter | `count` | Recommended | Agent 会话启动次数 |
| `agent.cost.usage` | Counter | `USD` | Recommended | 会话累计费用（近似值） |
| `agent.token.usage` | Counter | `tokens` | Recommended | Token 使用量（与 gen_ai.client.token.usage 互补） |
| `agent.tool_call.count` | Counter | `count` | Recommended | 工具调用次数 |
| `agent.llm_request.count` | Counter | `count` | Recommended | LLM API 请求次数 |
| `agent.llm_request.duration` | Histogram | `ms` | Recommended | LLM API 请求耗时分布 |
| `agent.active_time.total` | Counter | `s` | Recommended | 活跃时间（排除空闲） |
| `agent.error.count` | Counter | `count` | Recommended | 错误发生次数 |

> **与 OTel 标准指标的关系**：`agent.token.usage`（Counter）与 `gen_ai.client.token.usage`（Histogram）互补。前者适合简单累加统计，后者适合分布分析。建议同时上报。

---

### 指标详细定义

#### `agent.session.count` — 会话计数器

每次 Agent 会话启动时递增。

**属性**：通用身份属性（见第一章）

---

#### `agent.cost.usage` — 费用计数器

每次 LLM API 请求完成后递增，记录近似费用。

**额外属性**：

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `model` | Required | string | 模型标识 | `"claude-sonnet-4-6"` |

---

#### `agent.token.usage` — Token 计数器

每次 LLM API 请求完成后递增。

**额外属性**：

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `type` | Required | string | Token 类型 | `"input"`, `"output"`, `"cache_read"`, `"cache_creation"` |
| `model` | Required | string | 模型标识 | `"claude-sonnet-4-6"` |

---

#### `agent.tool_call.count` — 工具调用计数器

每次工具调用完成后递增。

**额外属性**：

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `tool_name` | Required | string | 工具名称 | `"web_search"`, `"code_edit"` |
| `success` | Required | string | 是否成功 | `"true"`, `"false"` |

---

#### `agent.llm_request.count` — LLM 请求计数器

每次 LLM API 请求完成后递增。

**额外属性**：

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `model` | Required | string | 模型标识 | `"claude-sonnet-4-6"` |
| `status` | Required | string | 请求状态 | `"success"`, `"error"` |

---

#### `agent.llm_request.duration` — LLM 请求耗时

记录每次 LLM API 请求的耗时分布。

**额外属性**：

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `model` | Required | string | 模型标识 | `"claude-sonnet-4-6"` |

---

#### `agent.active_time.total` — 活跃时间计数器

追踪 Agent 实际活跃时间，排除空闲等待。

**额外属性**：

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `type` | Required | string | 活跃类型 | `"user"` 用户交互, `"agent"` Agent 处理 |

---

#### `agent.error.count` — 错误计数器

每次发生错误时递增。

**额外属性**：

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `error_type` | Required | string | 错误类型 | `"llm_api"`, `"tool_execution"`, `"timeout"`, `"rate_limit"`, `"auth"` |

---

## 四、上报示例

### OTel 标准指标示例

```
gen_ai.client.token.usage{
  gen_ai.operation.name="chat",
  gen_ai.provider.name="anthropic",
  gen_ai.token.type="input",
  gen_ai.request.model="claude-sonnet-4-6",
  server.address="api.anthropic.com",
  ant.username="100001",
  ant.uid="2088xxxx"
} 1500

gen_ai.client.operation.duration{
  gen_ai.operation.name="chat",
  gen_ai.provider.name="anthropic",
  gen_ai.request.model="claude-sonnet-4-6",
  ant.username="100001",
  ant.uid="2088xxxx"
} 2.35
```

### AI Vision业务指标示例

```
agent.token.usage{
  ant.username="100001",
  ant.uid="2088xxxx",
  type="input",
  model="claude-sonnet-4-6"
} 1500

agent.cost.usage{
  ant.username="100001",
  ant.uid="2088xxxx",
  model="claude-sonnet-4-6"
} 0.045

agent.tool_call.count{
  ant.username="100001",
  ant.uid="2088xxxx",
  tool_name="code_edit",
  success="true"
} 1
```

---

## 五、与 OTel 标准的属性映射

| OTel 标准属性 | AI Vision属性 | 映射说明 |
|--------------|---------|---------|
| `gen_ai.provider.name` | `provider`（agent.* 指标中） | OTel 指标保留原名，AI Vision业务指标使用简称 |
| `gen_ai.request.model` | `model`（agent.* 指标中） | OTel 指标保留原名，AI Vision业务指标使用简称 |
| `gen_ai.token.type` | `type`（agent.token.usage 中） | OTel 指标保留原名，AI Vision业务指标使用简称 |
| — | `ant.username` | AI Vision扩展，OTel 标准中无对应 |
| — | `ant.uid` | AI Vision扩展，外部客户 UID（可用时） |

---

## 六、通用枚举值

### `gen_ai.provider.name` 预定义值

| Value | Description |
|-------|-------------|
| `anthropic` | Anthropic |
| `openai` | OpenAI |
| `alibaba` | 阿里巴巴（通义千问等） |
| `aws.bedrock` | AWS Bedrock |
| `azure.ai.openai` | Azure OpenAI |
| `deepseek` | DeepSeek |
| `gcp.gemini` | Gemini |
| `gcp.vertex_ai` | Vertex AI |
| `mistral_ai` | Mistral AI |

### `gen_ai.token.type` / `type` 预定义值

| Value | Description |
|-------|-------------|
| `input` | 输入 token |
| `output` | 输出 token |
| `cache_read` | 缓存读取 token（AI Vision扩展） |
| `cache_creation` | 缓存创建 token（AI Vision扩展） |

---

## 七、基数控制

| 环境变量 | 描述 | 默认值 |
|---------|------|--------|
| `OTEL_METRICS_INCLUDE_VERSION` | Metrics 中包含 `app.version` | `false` |

> ⚠️ `prompt.id` 等高基数属性 **不应** 包含在 Metrics 中，会导致指标基数无限增长。
