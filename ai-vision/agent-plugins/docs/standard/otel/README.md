# OpenTelemetry GenAI Semantic Conventions

> **规范状态**: Development
> **官方文档**: [Semantic Conventions for Generative AI Systems](https://opentelemetry.io/docs/specs/semconv/gen-ai/)

## 概述

OpenTelemetry 为 Generative AI 操作定义了一套语义规范（Semantic Conventions），用于标准化 GenAI 系统的可观测性数据采集。这些规范涵盖了从客户端调用到服务端推理的完整链路。

## 规范版本与兼容性

当前所有 GenAI 规范处于 **Development** 阶段。已有的 GenAI 插桩实现：

- **SHOULD NOT** 默认更改所发出的 GenAI 规范版本
- **SHOULD** 引入环境变量 `OTEL_SEMCONV_STABILITY_OPT_IN`，支持以逗号分隔的值列表：
  - `gen_ai_latest_experimental` — 发出最新实验版本的 GenAI 规范，不再发出旧版本（v1.36.0 或更早）

## 文档索引

| 文档 | 描述 |
|------|------|
| [Events](./events.md) | GenAI 输入输出事件规范，包括推理操作详情事件和评估结果事件 |
| [Metrics](./metrics.md) | GenAI 操作指标规范，包括客户端 token 使用量、操作耗时，以及服务端请求耗时、首 token 时间、每 token 时间 |
| [Model Spans](./model-spans.md) | GenAI 模型操作 Span 规范，包括推理、嵌入、检索和工具执行四种 Span 类型 |
| [Agent Spans](./agent-spans.md) | GenAI Agent 操作 Span 规范，包括 Agent 创建和调用两种 Span 类型 |

## 信号类型总览

### Events

| Event Name | Description |
|------------|-------------|
| `gen_ai.client.inference.operation.details` | 推理操作详情（含聊天历史和参数），opt-in |
| `gen_ai.evaluation.result` | 对 GenAI 输出的评估结果 |

### Metrics

| Metric Name | Type | Unit | Requirement | Description |
|-------------|------|------|-------------|-------------|
| `gen_ai.client.token.usage` | Histogram | `{token}` | Recommended | 输入/输出 token 使用量 |
| `gen_ai.client.operation.duration` | Histogram | `s` | **Required** | 客户端操作耗时 |
| `gen_ai.server.request.duration` | Histogram | `s` | Recommended | 服务端请求耗时 |
| `gen_ai.server.time_per_output_token` | Histogram | `s` | Recommended | 每输出 token 耗时（decode 阶段） |
| `gen_ai.server.time_to_first_token` | Histogram | `s` | Recommended | 首 token 生成时间（prefill 阶段） |

### Model Spans

| Span Type | operation.name | Span Kind | Description |
|-----------|---------------|-----------|-------------|
| Inference | `chat` / `generate_content` / `text_completion` | `CLIENT` | 模型推理调用 |
| Embeddings | `embeddings` | `CLIENT` | 嵌入向量生成 |
| Retrievals | `retrieval` | `CLIENT` | 向量数据库/搜索系统检索 |
| Execute Tool | `execute_tool` | `INTERNAL` | 工具执行 |

### Agent Spans

| Span Type | operation.name | Span Kind | Description |
|-----------|---------------|-----------|-------------|
| Create Agent | `create_agent` | `CLIENT` | 创建远程 Agent |
| Invoke Agent | `invoke_agent` | `CLIENT` / `INTERNAL` | 调用 Agent（远程/本地） |

## 核心属性速查

### 通用必需属性

| Key | Description |
|-----|-------------|
| `gen_ai.operation.name` | 操作名称（chat、embeddings、invoke_agent 等） |
| `gen_ai.provider.name` | 提供商标识（openai、anthropic、aws.bedrock 等） |

### 常用推荐属性

| Key | Description |
|-----|-------------|
| `gen_ai.request.model` | 请求模型名称 |
| `gen_ai.response.model` | 响应模型名称 |
| `gen_ai.usage.input_tokens` | 输入 token 数 |
| `gen_ai.usage.output_tokens` | 输出 token 数 |
| `server.address` | 服务地址 |
| `server.port` | 服务端口 |
| `error.type` | 错误类型 |

### 支持的 GenAI 提供商

`anthropic` · `aws.bedrock` · `azure.ai.inference` · `azure.ai.openai` · `cohere` · `deepseek` · `gcp.gemini` · `gcp.gen_ai` · `gcp.vertex_ai` · `groq` · `ibm.watsonx.ai` · `mistral_ai` · `openai` · `perplexity` · `x_ai`

## 厂商特定规范

OpenTelemetry 还为以下 GenAI 系统定义了厂商特定的语义规范：

- [Anthropic](https://opentelemetry.io/docs/specs/semconv/gen-ai/anthropic/)
- [Azure AI Inference](https://opentelemetry.io/docs/specs/semconv/gen-ai/azure-ai-inference/)
- [AWS Bedrock](https://opentelemetry.io/docs/specs/semconv/gen-ai/aws-bedrock/)
- [OpenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/openai/)

## 相关规范

- [Model Context Protocol (MCP)](https://opentelemetry.io/docs/specs/semconv/gen-ai/mcp/) — MCP 语义规范
