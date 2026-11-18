# OpenTelemetry GenAI Semantic Conventions - Events

> **Status**: Development
> **Source**: [Semantic Conventions for Generative AI Events](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-events/)

## 概述

GenAI 插桩 **MAY** 将发送给模型的用户输入和从模型接收的响应捕获为 **Events**。Events 目前处于开发阶段，部分语言 SDK 尚未支持。

---

## Event: `gen_ai.client.inference.operation.details`

描述 GenAI 推理请求的详细信息，包括聊天历史和参数。此事件为 **opt-in**，可独立于 traces 存储输入输出详情。

### 必需属性 (Required)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `gen_ai.operation.name` | string | 操作名称 | `chat`, `generate_content`, `text_completion` |

### 条件必需属性 (Conditionally Required)

| Key | Condition | Type | Description | Example |
|-----|-----------|------|-------------|---------|
| `error.type` | 操作以错误结束时 | string | 错误类型 | `timeout`, `500` |
| `gen_ai.conversation.id` | 可用时 | string | 会话唯一标识 | `conv_5j66UpCpwteGg4YSxUnt7lPY` |
| `gen_ai.output.type` | 请求包含输出格式时 | string | 客户端请求的内容类型 | `text`, `json`, `image` |
| `gen_ai.request.choice.count` | 可用且 !=1 时 | int | 候选补全数量 | `3` |
| `gen_ai.request.model` | 可用时 | string | 请求的模型名称 | `gpt-4` |
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
| `gen_ai.request.top_p` | double | Top-p 采样参数 | `1.0` |
| `gen_ai.response.finish_reasons` | string[] | 停止原因数组 | `["stop"]` |
| `gen_ai.response.id` | string | 补全唯一标识 | `chatcmpl-123` |
| `gen_ai.response.model` | string | 响应模型名称 | `gpt-4-0613` |
| `gen_ai.usage.cache_creation.input_tokens` | int | 写入缓存的输入 token 数 | `25` |
| `gen_ai.usage.cache_read.input_tokens` | int | 从缓存读取的输入 token 数 | `50` |
| `gen_ai.usage.input_tokens` | int | 输入 token 总数 | `100` |
| `gen_ai.usage.output_tokens` | int | 输出 token 数 | `180` |
| `server.address` | string | 服务地址 | `example.com` |

### Opt-In 属性

| Key | Type | Description |
|-----|------|-------------|
| `gen_ai.input.messages` | any | 提供给模型的聊天历史（⚠️ 可能包含敏感信息） |
| `gen_ai.output.messages` | any | 模型返回的消息，每条消息对应一个生成选项 |
| `gen_ai.system_instructions` | any | 独立于聊天历史的系统指令 |
| `gen_ai.tool.definitions` | any | 可用工具定义列表（不建议默认填充） |

### 输入消息 JSON 结构示例

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

### 输出消息 JSON 结构示例

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

---

## Event: `gen_ai.evaluation.result`

捕获对 GenAI 输出的质量、准确性等评估结果。此事件 **SHOULD** 作为被评估的 GenAI 操作 span 的子事件。

### 必需属性 (Required)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `gen_ai.evaluation.name` | string | 评估指标名称 | `Relevance`, `IntentResolution` |

### 条件必需属性 (Conditionally Required)

| Key | Condition | Type | Description | Example |
|-----|-----------|------|-------------|---------|
| `error.type` | 操作以错误结束时 | string | 错误类型 | `timeout`, `500` |
| `gen_ai.evaluation.score.label` | 适用时 | string | 评估分数的人类可读标签 | `relevant`, `pass`, `fail` |
| `gen_ai.evaluation.score.value` | 适用时 | double | 评估分数值 | `4.0` |

### 推荐属性 (Recommended)

| Key | Type | Description | Example |
|-----|------|-------------|---------|
| `gen_ai.evaluation.explanation` | string | 评估解释 | `The response is factually accurate but lacks detail.` |
| `gen_ai.response.id` | string | 被评估的补全标识 | `chatcmpl-123` |

---

## 通用枚举值

### `gen_ai.operation.name` 预定义值

| Value | Description |
|-------|-------------|
| `chat` | 聊天补全操作（如 OpenAI Chat API） |
| `create_agent` | 创建 GenAI Agent |
| `embeddings` | 嵌入操作（如 OpenAI Embeddings API） |
| `execute_tool` | 执行工具 |
| `generate_content` | 多模态内容生成（如 Gemini Generate Content） |
| `invoke_agent` | 调用 GenAI Agent |
| `retrieval` | 检索操作（如 OpenAI Search Vector Store API） |
| `text_completion` | 文本补全操作（Legacy） |

### `gen_ai.output.type` 预定义值

| Value | Description |
|-------|-------------|
| `image` | 图片 |
| `json` | JSON 对象 |
| `speech` | 语音 |
| `text` | 纯文本 |
