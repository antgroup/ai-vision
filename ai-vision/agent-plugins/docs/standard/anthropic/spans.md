# Anthropic 可观测性规范 — Spans

> **Source**: [OTel Semantic Conventions for Anthropic](https://opentelemetry.io/docs/specs/semconv/gen-ai/anthropic/)
> **Status**: Development
> **协议**: OpenTelemetry Traces

## 概述

本规范定义 Anthropic 客户端操作的 Span 语义规范。Anthropic 语义规范扩展并覆盖了通用的 [GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)。

### 核心约束

- `gen_ai.provider.name` **MUST** 设为 `"anthropic"`，且 **SHOULD** 在 Span 创建时提供
- **Span Name** SHOULD 为 `{gen_ai.operation.name} {gen_ai.request.model}`
- **Span Kind** SHOULD 为 `CLIENT`
- **Span Status** SHOULD 遵循 [Recording Errors](https://opentelemetry.io/docs/specs/semconv/gen-ai/) 文档

---

## 一、Inference Span

描述 Anthropic 模型推理操作（chat、text_completion 等）。

| Property | Value |
|----------|-------|
| **Span Name** | `{gen_ai.operation.name} {gen_ai.request.model}` |
| **Span Kind** | `CLIENT` |

### 属性

| Key | Requirement | Type | Description | Example |
|-----|-------------|------|-------------|---------|
| `gen_ai.operation.name` | Required | string | 操作名称 | `"chat"`, `"text_completion"` |
| `gen_ai.provider.name` | Required | string | 固定为 `"anthropic"` | `"anthropic"` |
| `error.type` | Conditionally Required | string | 错误类型（操作出错时） | `"timeout"`, `"500"`, `"_OTHER"` |
| `gen_ai.conversation.id` | Conditionally Required | string | 会话标识（可用时） | `"conv_5j66UpCpwteGg4YSxUnt7lPY"` |
| `gen_ai.output.type` | Conditionally Required | string | 请求的输出内容类型（适用且请求中包含时） | `"text"`, `"json"`, `"image"` |
| `gen_ai.request.choice.count` | Conditionally Required | int | 候选补全数量（可用且 ≠1 时） | `3` |
| `gen_ai.request.model` | Conditionally Required | string | 请求模型名称（可用时） | `"claude-sonnet-4-6"` |
| `gen_ai.request.seed` | Conditionally Required | int | 随机种子（适用且请求中包含时） | `100` |
| `server.port` | Conditionally Required | int | 服务端口（`server.address` 已设置时） | `443` |
| `gen_ai.request.frequency_penalty` | Recommended | double | 频率惩罚 | `0.1` |
| `gen_ai.request.max_tokens` | Recommended | int | 最大生成 token 数 | `100` |
| `gen_ai.request.presence_penalty` | Recommended | double | 存在惩罚 | `0.1` |
| `gen_ai.request.stop_sequences` | Recommended | string[] | 停止序列 | `["forest", "lived"]` |
| `gen_ai.request.temperature` | Recommended | double | 温度参数 | `0.0` |
| `gen_ai.request.top_p` | Recommended | double | Top-p 参数 | `1.0` |
| `gen_ai.response.finish_reasons` | Recommended | string[] | 停止原因 | `["stop"]`, `["stop", "length"]` |
| `gen_ai.response.id` | Recommended | string | 补全唯一标识 | `"chatcmpl-123"` |
| `gen_ai.response.model` | Recommended | string | 实际响应模型名称 | `"claude-sonnet-4-6-20250514"` |
| `gen_ai.usage.cache_creation.input_tokens` | Recommended | int | 写入缓存的输入 token 数 | `25` |
| `gen_ai.usage.cache_read.input_tokens` | Recommended | int | 从缓存读取的输入 token 数 | `50` |
| `gen_ai.usage.input_tokens` | Recommended | int | 输入 token 总数 | `100` |
| `gen_ai.usage.output_tokens` | Recommended | int | 输出 token 数 | `180` |
| `server.address` | Recommended | string | 服务地址 | `"api.anthropic.com"` |
| `gen_ai.input.messages` | Opt-In | any | 聊天历史（⚠️ 敏感信息） | — |
| `gen_ai.output.messages` | Opt-In | any | 模型返回消息（⚠️ 敏感信息） | — |
| `gen_ai.system_instructions` | Opt-In | any | 系统指令（⚠️ 敏感信息） | — |
| `gen_ai.tool.definitions` | Opt-In | any | 工具定义列表 | — |

### Span 创建时应提供的采样决策属性

以下属性 SHOULD 在 Span 创建时提供，以便采样器做出决策：

- `gen_ai.operation.name`
- `gen_ai.provider.name`（固定为 `"anthropic"`）
- `gen_ai.request.model`
- `server.address`
- `server.port`

---

## 二、Anthropic Token 计算规则

> ⚠️ **重要**：Anthropic 的 `input_tokens` 不包含缓存 token，需要特殊计算。

### 计算公式

```
gen_ai.usage.input_tokens = input_tokens + cache_read_input_tokens + cache_creation_input_tokens
```

### 字段说明

| 字段 | Anthropic API 返回值 | OTel 属性 | 说明 |
|------|---------------------|-----------|------|
| `input_tokens` | `usage.input_tokens` | — | Anthropic 原始值，**不含**缓存 token |
| `cache_read_input_tokens` | `usage.cache_read_input_tokens` | `gen_ai.usage.cache_read.input_tokens` | 从缓存读取的 token 数 |
| `cache_creation_input_tokens` | `usage.cache_creation_input_tokens` | `gen_ai.usage.cache_creation.input_tokens` | 写入缓存的 token 数 |
| — | — | `gen_ai.usage.input_tokens` | **计算值** = 以上三者之和 |

### 示例

```json
// Anthropic API 返回
{
  "usage": {
    "input_tokens": 100,
    "output_tokens": 180,
    "cache_read_input_tokens": 50,
    "cache_creation_input_tokens": 25
  }
}

// OTel Span 属性
{
  "gen_ai.usage.input_tokens": 175,           // 100 + 50 + 25
  "gen_ai.usage.output_tokens": 180,
  "gen_ai.usage.cache_read.input_tokens": 50,
  "gen_ai.usage.cache_creation.input_tokens": 25
}
```

---

## 三、Embedding Span

Anthropic Embedding 操作遵循通用的 [Embedding Span 定义](https://opentelemetry.io/docs/specs/semconv/gen-ai/)。

| Property | Value |
|----------|-------|
| **gen_ai.operation.name** | `"embeddings"` |
| **Span Name** | `embeddings {gen_ai.request.model}` |
| **Span Kind** | `CLIENT` |

---

## 四、`gen_ai.operation.name` 预定义值

| Value | Description | 典型场景 |
|-------|-------------|---------|
| `chat` | Chat completion 操作 | Anthropic Messages API |
| `text_completion` | 文本补全操作 | Anthropic Text Completions API (Legacy) |
| `generate_content` | 多模态内容生成 | — |
| `embeddings` | Embedding 操作 | — |
| `create_agent` | 创建 GenAI Agent | — |
| `invoke_agent` | 调用 GenAI Agent | — |
| `execute_tool` | 执行工具 | Tool Use |
| `retrieval` | 检索操作 | — |

---

## 五、`gen_ai.output.type` 预定义值

| Value | Description |
|-------|-------------|
| `text` | 纯文本 |
| `json` | JSON 对象（已知或未知 schema） |
| `image` | 图片 |
| `speech` | 语音 |

---

## 六、`error.type` 预定义值

| Value | Description |
|-------|-------------|
| `_OTHER` | 兜底错误值（instrumentation 未定义自定义值时使用） |

`error.type` SHOULD 匹配 Anthropic 返回的错误码、异常的规范名称或其他低基数错误标识。常见 Anthropic 错误码：

| Error Code | Description |
|------------|-------------|
| `invalid_request_error` | 请求参数无效 |
| `authentication_error` | 认证失败 |
| `permission_error` | 权限不足 |
| `not_found_error` | 资源未找到 |
| `rate_limit_error` | 速率限制 |
| `api_error` | API 内部错误 |
| `overloaded_error` | 服务过载 |
| `timeout` | 请求超时 |

---

## 七、Opt-In 属性的 JSON Schema

### `gen_ai.input.messages`

遵循 [Input messages JSON schema](https://opentelemetry.io/docs/specs/semconv/gen-ai/)。消息 MUST 按发送给模型的顺序提供。

```json
[
  {
    "role": "user",
    "parts": [
      { "type": "text", "content": "Weather in Paris?" }
    ]
  },
  {
    "role": "assistant",
    "parts": [
      {
        "type": "tool_call",
        "id": "call_VSPygqKTWdrhaFErNvMV18Yl",
        "name": "get_weather",
        "arguments": { "location": "Paris" }
      }
    ]
  },
  {
    "role": "tool",
    "parts": [
      {
        "type": "tool_call_response",
        "id": "call_VSPygqKTWdrhaFErNvMV18Yl",
        "result": "rainy, 57°F"
      }
    ]
  }
]
```

### `gen_ai.output.messages`

遵循 [Output messages JSON schema](https://opentelemetry.io/docs/specs/semconv/gen-ai/)。每条消息对应一个生成结果（choice/candidate）。

```json
[
  {
    "role": "assistant",
    "parts": [
      {
        "type": "text",
        "content": "The weather in Paris is currently rainy with a temperature of 57°F."
      }
    ],
    "finish_reason": "stop"
  }
]
```

### `gen_ai.system_instructions`

遵循 [System instructions JSON schema](https://opentelemetry.io/docs/specs/semconv/gen-ai/)。

```json
[
  { "type": "text", "content": "You are a helpful coding assistant." },
  { "type": "text", "content": "Always explain your reasoning step by step." }
]
```

### `gen_ai.tool.definitions`

工具定义列表，匹配源系统的工具定义格式。由于可能较大，**不建议** 默认填充。

```json
[
  {
    "type": "function",
    "name": "get_current_weather",
    "description": "Get the current weather in a given location",
    "parameters": {
      "type": "object",
      "properties": {
        "location": { "type": "string", "description": "The city and state" },
        "unit": { "type": "string", "enum": ["celsius", "fahrenheit"] }
      },
      "required": ["location", "unit"]
    }
  }
]
```

---

## 八、安全与隐私

- `gen_ai.input.messages`、`gen_ai.output.messages`、`gen_ai.system_instructions` 可能包含 **用户/PII 敏感数据**
- 这些属性为 **Opt-In**，默认不采集
- Instrumentation MAY 提供过滤或截断机制
- `gen_ai.tool.definitions` 由于可能较大，**不建议** 默认填充

---

## 九、版本兼容性说明

现有 GenAI instrumentation 使用 v1.36.0 或更早版本的规范时：

- **SHOULD NOT** 默认更改发出的 GenAI conventions 版本
- **SHOULD** 引入环境变量 `OTEL_SEMCONV_STABILITY_OPT_IN`（逗号分隔列表）：
  - `gen_ai_latest_experimental` — 发出最新实验版本的 GenAI conventions，不再发出旧版本（v1.36.0 或更早）
- 默认行为是继续发出 instrumentation 之前发出的版本
