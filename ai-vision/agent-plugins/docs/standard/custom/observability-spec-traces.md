# Agent 可观测技术埋点规范设计方案
> **文档版本**: v2.0  
**创建日期**: 2026-03-16  
**适用范围**: Agent Runtime 可观测平台所有接入方  
**基础规范**: AI 可观测字段规范新版（黄金标准）+ Spanory 字段矩阵 + 评测链路扩展
>

---

## 一、概述
### 1.1 规范目标
本文档定义 AI Agent 系统的可观测字段规范，基于以下原则：

1. **兼容 OTel 标准**：优先采用 OpenTelemetry GenAI Semantic Conventions
2. **分层设计**：按通用程度分为 L1（通用）、L2（场景化）、L3（业务自定义）
3. **Span 类型驱动**：按 CHAT/AGENT/TOOL/MCP/RETRIEVER/RERANKER/EMBEDDING/GUARDRAIL/LLM 分类定义
4. **向后兼容**：存量系统可逐步迁移，不强制一次性改造

### 1.2 字段级别定义
| 级别 | 范围 | 说明 |
| --- | --- | --- |
| **L1** | 所有 Agent Runtime 通用 | 基础字段，必须上报 |
| **L2** | 场景化字段 | 按 Agent 类型、业务场景、评测链路分类 |
| **L3** | 业务自定义 | 业务方在命名空间隔离下的自定义字段 |


### 1.3 必填级别定义
| 级别 | 说明 |
| --- | --- |
| **必须** | 缺失将严重影响可观测性，强制要求 |
| **推荐** | 显著增强分析能力，建议实现 |
| **可选** | 完整对标生产级需求，按需实现 |


### 1.4 业务痛点
| 痛点类型 | 具体问题 |
| --- | --- |
| **来源辨识度** | 无法区分线上/灰度/预发环境产生的 trace |
| **业务场景区分** | 测试流量、白名单业务、投流、对话 App、后置算法训练 trace 混为一体 |
| **评测链路打通** | trace → golden/bad case dataset → 评测 → prompt/工具调用/RAG 优化流程需要字段支撑 |
| **双轨 trace 混乱** | 日志采集（非 OTel 规范）+ OTel 协议上报，trace_id 不统一，需手动关联 |


---

## 二、通用字段（L1 - 所有 Span 类型）
以下字段适用于所有 Span 类型，是 Agent 可观测的基础。

### 2.1 调用链与身份标识
| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| `trace_id` | String | OTel Trace ID（32位十六进制） | `abc123...` | 必须 |
| `span_id` | String | OTel Span ID（16位十六进制） | `def456...` | 必须 |
| `parent_span_id` | String | 父 Span ID | `ghi789...` | 推荐 |
| `gen_ai.span.kind` | String | Span 类型 | `AGENT`, `TOOL`, `LLM` | 必须 |
| `gen_ai.operation.name` | String | 操作子类型 | `invoke_agent`, `execute_tool` | 推荐 |


### 2.2 服务与部署
| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| `service.name` | String | 服务名 | `agent-runtime` | 必须 |
| `service.version` | String | 服务版本 | `v2.0.1` | 推荐 |
| `deployment.environment.name` | String | 部署环境（OTel 标准） | `production`, `staging`, `development` | 必须 |
| `deployment.region` | String | 部署地域 | `cn-beijing`, `cn-shanghai` | 推荐 |


### 2.3 会话与用户
| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| `gen_ai.conversation.id` | String | 会话（Session）ID | `ad8c7585...-s` | 必须 |
| `session.id` | String | 会话 ID（简化版） | `session-abc123` | 必须 |
| `gen_ai.user.id` | String | 用户 ID | `2088123456788802` | 推荐 |
| `user.type` | String | 用户类型 | `test`, `whitelist`, `normal`, `bot` | 推荐 |


---

## 三、按 Span 类型的字段定义
### 3.1 CHAT Span（会话层）
CHAT span 包含了会话层的可观测字段。会话层代表接近客户端的应用集合，如网关（mobilegw、streamhub、openapi 等）、业务应用。

| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| `gen_ai.span.kind` | String | 固定为 `CHAT` | `CHAT` | 必须 |
| `gen_ai.conversation.id` | String | 会话 ID | `ad8c7585...-s` | 必须 |
| `gen_ai.chat.id` | String | 对话 ID（一次会话中多次对话不同） | `f64d85f4...-c` | 推荐 |
| `gen_ai.user.id` | String | 用户 ID | `2088123456788802` | 推荐 |
| `gen_ai.response.time_to_first_token` | Long | 首 token 时间（毫秒） | `5782` | 必须 |
| `gen_ai.response.time_per_output_token` | Long | 每个响应报文发送时间（毫秒） | `38` | 推荐（流式有效） |
| `response.total_packets` | Long | 流式返回报文次数 | `52` | 推荐（流式有效） |
| `response.duration` | Long | 调用总耗时（毫秒） | `13825` | 可选 |
| `response.result_state` | String | 业务调用状态 | `SUCCESS`, `ERROR` | 可选，otel span 一级属性已有。 |
| `http.response.status_code` | Integer | HTTP 返回码 | `200` | HTTP 调用必填 |
| `gen_ai.input.messages` | String | 输入给模型的内容（JSON） | - | 推荐 |
| `gen_ai.output.messages` | String | 会话最终输出内容（JSON） | - | 推荐 |
| `input.intent` | String | 从 query 识别的意图 | `document_search` | 推荐 |


### 3.2 AGENT Span（Agent 服务调用）
AGENT span 包含了 agent 服务调用的字段集合。

| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| `gen_ai.span.kind` | String | 固定为 `AGENT` | `AGENT` | 必须 |
| `gen_ai.agent.id` | String | Agent ID | `29794` | 必须 |
| `gen_ai.agent.name` | String | Agent 名称 | `自助保障范围` | 必须 |
| `gen_ai.agent.version` | String | Agent 版本 | `v1.2.3` | 推荐 |
| `gen_ai.response.time_to_first_token` | Long | 首响应时间（毫秒） | `5782` | 必须 |
| `response.result_state` | String | 业务调用状态 | `SUCCESS`, `ERROR` | 可选，otel span 一级属性已有。 |
| `http.response.status_code` | Integer | HTTP 返回码 | `200` | HTTP 调用必填 |


**AGENT Span 的 L2 扩展字段**：

| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| **环境/来源** |  |  |  |  |
| `traffic.source` | String | 流量来源 | `direct`, `ad`, `organic` | 推荐 |
| **业务场景** |  |  |  |  |
| `business.scene` | String | 业务场景 | `chat`, `training`, `eval` | 推荐 |
| `business.app_id` | String | 应用唯一标识 | `app_xxx_123` | 推荐 |
| **评测链路** |  |  |  |  |
| `eval.trace_source` | String | trace 来源类型 | `prod`, `sample`, `synthetic` | 评测场景必填 |
| `eval.dataset.id` | String | 数据集 ID | `dataset_golden_v1` | 评测场景必填 |
| `eval.prompt.id` | String | Prompt 唯一标识 | `prompt_customer_service_v2` | 评测场景必填 |
| `eval.case.id` | String | 案例唯一标识 | `case_001` | 评测场景必填 |
| `eval.case.type` | String | 案例类型 | `golden`, `bad` | 评测场景必填 |
| **Agentic 运行时扩展** |  |  |  |  |
| `agentic.runtime.name` | String | Runtime 名称 | `claude-code`, `codex` | 推荐 |
| `agentic.runtime.version` | String | Runtime 版本 | `v1.0.0` | 推荐 |
| `agentic.turn.id` | String | Turn ID | `turn-1` | 推荐 |


### 3.3 TOOL Span（工具调用）
TOOL span 包含了 Tool 调用相关的字段。

| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| `gen_ai.span.kind` | String | 固定为 `TOOL` | `TOOL` | 必须 |
| `gen_ai.tool.call.id` | String | Tool Call 唯一标识 | `call_mszuSIzqtI65i1wAUOE8w5H4` | 推荐 |
| `gen_ai.tool.name` | String | Tool 名 | `Flights` | 推荐 |
| `gen_ai.tool.type` | String | Tool 类型 | `function`, `extension` | 推荐 |
| `response.result_state` | String | 业务调用状态 | `SUCCESS`, `ERROR` | 可选，otel span 一级属性已有。 |


**TOOL Span 的 L2 扩展字段（Coding Agent）**：

| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| `code.tool.name` | String | 命令名 | `bash`, `read`, `edit` | Coding Agent 推荐 |
| `code.tool.exit_code` | Int | 命令返回码 | `0`, `1` | Coding Agent 推荐 |
| `code.file.operation` | String | 文件操作类型 | `read`, `write`, `delete` | Coding Agent 推荐 |
| `code.file.path` | String | 操作文件路径 | `/src/main.py` | Coding Agent 推荐 |


### 3.4 MCP Span（MCP 协议调用）
MCP span 包含了 MCP 协议调用的相关字段。

| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| `gen_ai.span.kind` | String | 固定为 `MCP` | `MCP` | 必须 |
| `mcp.server_id` | String | MCP Server ID | `mcp.example.logs` | 必须 |
| `mcp.request_type` | String | MCP 调用类型 | `tools/list`, `tools/call` | 必须 |
| `mcp.tool_name` | String | 调用的 tool 名 | `queryAppLogContentByTraceId` | tools/call 必填 |
| `response.result_state` | String | 业务调用状态 | `SUCCESS`, `ERROR` | 可选，otel span 一级属性已有。 |


### 3.5 RETRIEVER Span（召回操作）
RETRIEVER span 包含了召回操作相关的字段。

| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| `gen_ai.span.kind` | String | 固定为 `RETRIEVER` | `RETRIEVER` | 必须 |
| `retrieval.query` | String | 召回操作的 query | `what is the topic?` | 推荐 |
| `retrieval.kind` | String | 召回种类 | `vector_query`, `text_query` | 必须 |
| `retrieval.system` | String | 召回数据库类别 | `zsearch`, `tbase` | 必须 |
| `retrieval.request.top_k` | Integer | 召回最 K 个相近文档 | `3` | 必须 |


### 3.6 RERANKER Span（重排操作）
RERANKER span 包含了内容重排相关的字段。

| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| `gen_ai.span.kind` | String | 固定为 `RERANKER` | `RERANKER` | 必须 |
| `reranker.model_name` | String | 精排模型名称 | `antllm` | 必须 |
| `reranker.top_k` | Integer | 精排返回最 K 个文档 | `3` | 推荐 |


### 3.7 EMBEDDING Span（Embedding 操作）
EMBEDDING span 包含了 embedding 操作相关的字段。

| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| `gen_ai.span.kind` | String | 固定为 `EMBEDDING` | `EMBEDDING` | 必须 |
| `gen_ai.usage.input_tokens` | Integer | 输入 tokens | `10` | 推荐 |
| `gen_ai.usage.total_tokens` | Integer | 总 tokens | `10` | 推荐 |
| `embedding.model_name` | String | Embedding 模型名 | `text-embedding-v1` | 推荐 |


### 3.8 GUARDRAIL Span（拦截服务）
GUARDRAIL span 包含了拦截服务调用相关的字段。

| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| `gen_ai.span.kind` | String | 固定为 `GUARDRAIL` | `GUARDRAIL` | 必须 |
| `guardrail.input` | String | 待检测的内容 | - | 必须 |
| `guardrail.result` | String | 检测结果 | - | 必须 |
| `guardrail.action` | String | 通过/拦截等 | `pass`, `block` | 必须 |


### 3.9 LLM Span（模型服务调用）
LLM span 包含了一次模型服务调用相关的字段。

| 字段 | 类型 | 说明 | 示例 | 级别 |
| --- | --- | --- | --- | --- |
| `gen_ai.span.kind` | String | 固定为 `LLM` | `LLM` | 必须 |
| `gen_ai.provider.name` | String | LLM 提供者 | `openai`, `anthropic`, `qwen` | 必须 |
| `gen_ai.request.model` | String | 请求模型名 | `gpt-4` | 必须 |
| `gen_ai.response.model` | String | 实际响应模型名 | `gpt-4-0613` | 推荐 |
| `gen_ai.request.temperature` | Float | 温度值 | `0.7` | 推荐 |
| `gen_ai.request.max_tokens` | Integer | 最大 tokens | `100` | 推荐 |
| `gen_ai.response.time_to_first_token` | Integer | 首 token 时间（毫秒） | `2000` | 推荐 |
| `gen_ai.usage.input_tokens` | Integer | 输入 tokens | `100` | 推荐 |
| `gen_ai.usage.output_tokens` | Integer | 输出 tokens | `200` | 推荐 |
| `gen_ai.usage.total_tokens` | Integer | 总 tokens | `300` | 推荐 |
| `http.response.status_code` | Integer | HTTP 返回码 | `200` | HTTP 调用必填 |


---

## 四、Token 使用字段详解
### 4.1 基础 Token 字段
| 字段 | 类型 | 说明 | 适用 Span |
| --- | --- | --- | --- |
| `gen_ai.usage.input_tokens` | Int | 输入 tokens | CHAT, AGENT, LLM |
| `gen_ai.usage.output_tokens` | Int | 输出 tokens | CHAT, AGENT, LLM |
| `gen_ai.usage.total_tokens` | Int | 总 tokens | CHAT, AGENT, LLM |


### 4.2 缓存 Token 字段（扩展）
| 字段 | 类型 | 说明 | 适用 Span |
| --- | --- | --- | --- |
| `gen_ai.usage.details.cache_read_input_tokens` | Int | 缓存读取 tokens | LLM |
| `gen_ai.usage.details.cache_creation_input_tokens` | Int | 缓存创建 tokens | LLM |
| `gen_ai.usage.details.cache_hit_rate` | Double | 缓存命中率 | LLM |


---

## 五、评测链路打通方案
### 5.1 数据流向
```plain
Production Trace (携带 eval.dataset.id + eval.case.id 标记)
        ↓ 筛选/采样
Golden/Bad Case 数据集 (通过 eval.case.id 关联)
        ↓ 触发评测任务
评测任务 (对比不同 eval.prompt.version 的表现)
        ↓ 生成优化建议
优化迭代 (Prompt 版本更新 → eval.prompt.version 变化)
        ↓ 新版本上线
新版本 Trace (携带新的 eval.prompt.version，形成闭环)
```

### 5.2 关键字段关联
| 阶段 | 关键字段 | 作用 |
| --- | --- | --- |
| Trace 采集 | `eval.dataset.id`, `eval.case.id` | 标记该 trace 属于哪个评测案例 |
| Dataset 构建 | `eval.case.type` | 区分 golden/bad case |
| 评测执行 | `eval.prompt.id`, `eval.prompt.version` | 对比不同 Prompt 版本的效果 |
| 结果分析 | `eval.metrics` | 存储评测指标得分 |
| 人工标注 | `eval.feedback.score`, `eval.feedback.comment` | 人工反馈闭环 |


---

## 六、双轨 Trace 统一方案
### 6.1 现状问题
+ **系统 A**：日志规范输出 → 日志采集 → 存储（SOFA trace_id 非 OTel 格式）
+ **系统 B**：OTel 协议 → 可观测系统（标准 OTel trace_id）
+ **问题**：两个系统 trace_id 完全不同，业务排查需手动关联

### 6.2 统一方案架构
```plain
┌──────────────────────────────────────────────────────────────────────┐
│                         系统 A（存量系统）                             │
│  日志输出: {trace_id: "sofa-123", span_id: "span-1", ...}              │
│  通过 RPC/HTTP Header 传递: X-Legacy-Trace-Id: sofa-123                │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
           ┌───────────────┴───────────────┐
           │                               │
           ▼                               ▼
┌──────────────────────┐      ┌──────────────────────────────────────┐
│     日志采集器        │      │           系统 B（新系统）              │
│                      │      │                                      │
│  1. 读取日志中        │      │  1. 从 Header 获取 X-Legacy-Trace-Id  │
│     trace_id          │      │     (value: sofa-123)                 │
│                      │      │                                      │
│  2. 调用转换函数      │      │  2. 调用同样转换函数                  │
│     → MD5 Hash        │      │     → MD5 Hash(sofa-123)              │
│     → 32位十六进制    │      │     → 32位十六进制                    │
│                      │      │                                      │
│  3. 生成 OTel Span    │      │  3. 注入 OTel SDK                     │
│     trace_id=hash     │      │     trace_id=hash (非随机生成)        │
│                      │      │                                      │
│  4. 上报 OTel         │      │  4. 上报 OTel                         │
│     Collector         │      │     Collector                        │
└──────────┬───────────┘      └──────────┬───────────────────────────┘
           │                               │
           └───────────────┬───────────────┘
                           ▼
              ┌───────────────────────┐
              │    OTel Collector     │
              │  (统一 trace 存储)    │
              │  trace_id 一致可串联  │
              └───────────────────────┘
```

核心机制：

1. **上游传递**：系统 A 通过 RPC/HTTP Header (`X-Legacy-Trace-Id`) 将原始 trace_id 传递给下游系统 B
2. **统一转换**：两边使用**相同的 Hash 函数**（如 MD5）将原始 trace_id 转换为 OTel 格式的 32 位十六进制 trace_id
3. **主动注入**：系统 B 通过 OTel SDK API **主动设置 trace_id**，而非让 SDK 随机生成

### 6.3 trace_id 转换与注入
```python
import hashlib

def convert_to_otel_trace_id(legacy_trace_id: str) -> str:
    """
    将 legacy trace_id 转换为 OTel 标准的 32 位十六进制 trace_id
    采用 MD5 哈希方案，确保同一 legacy_id 总是生成相同的 OTel trace_id
    """
    hash_bytes = hashlib.md5(legacy_trace_id.encode('utf-8')).digest()
    return hash_bytes.hex()  # 返回 32 位十六进制字符串
```

### 6.4 迁移路径
| 阶段 | 系统 A（存量） | 系统 B（新） | 说明 |
| --- | --- | --- | --- |
| **短期** | 日志输出 + Header 传递 | 接收 Header → 转换 → 注入 OTel SDK | 双轨并存，链路已打通 |
| **中期** | 增加 OTel Exporter 输出 | 标准 OTel SDK（无需注入） | 存量系统逐步支持 OTel 协议 |
| **长期** | 完全 OTel 化 | 标准 OTel SDK | 统一协议，废弃转换逻辑 |


---

## 七、业务自定义字段（L3）
### 7.1 命名规范
```yaml
# 格式: custom.<namespace>.<field_name>
custom.arec.module_name: "intent_recognition"
custom.arec.intent: "query_balance"
custom.xxx.biz_line: "finance"
custom.xxx.product_code: "app_001"
```

### 7.2 管理规范
1. **命名空间注册**：业务方需向平台注册命名空间，避免冲突
2. **类型限制**：字段值类型限制为 `string`, `number`, `boolean`, `json_string`
3. **敏感字段声明**：敏感字段需在注册时声明，平台自动脱敏
4. **审核机制**：新增命名空间需经过平台审核

---

## 八、附录
### 8.1 Span 类型速查表
| Span 类型 | 核心字段 | 典型场景 |
| --- | --- | --- |
| **CHAT** | `gen_ai.conversation.id`, `gen_ai.chat.id` | 网关层、业务应用层 |
| **AGENT** | `gen_ai.agent.id`, `gen_ai.agent.name`, `business.scene`, `eval.*` | Agent 服务调用 |
| **TOOL** | `gen_ai.tool.name`, `gen_ai.tool.call.id`, `code.tool.*` | 工具调用、Bash 执行 |
| **MCP** | `mcp.server_id`, `mcp.request_type`, `mcp.tool_name` | MCP 协议调用 |
| **RETRIEVER** | `retrieval.kind`, `retrieval.system`, `retrieval.request.*` | RAG 向量召回 |
| **RERANKER** | `reranker.model_name`, `reranker.input/output_document` | RAG 结果重排 |
| **EMBEDDING** | `embedding.model_name`, `gen_ai.usage.*` | 文本向量化 |
| **GUARDRAIL** | `guardrail.input`, `guardrail.result`, `guardrail.action` | 内容安全检查 |
| **LLM** | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.*` | 模型服务调用 |


### 8.2 字段来源对照
| 来源 | 字段前缀 | 说明 |
| --- | --- | --- |
| OTel 官方 | `gen_ai.*`, `session.id`, `service.*`, `deployment.*` | OpenTelemetry GenAI Semantic Conventions |
| 阿里云 ARMS | `response.*`, `retrieval.*`, `reranker.*`, `embedding.*`, `guardrail.*` | 阿里云可观测规范 |
| Spanory 扩展 | `agentic.*`, `mcp.*` | Spanory Runtime 实践字段 |
| 评测链路 | `eval.*` | 评测场景专用字段 |
| 业务自定义 | `custom.<namespace>.*` | 业务方自定义字段 |


### 8.3 相关文档
+ [OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/)
+ [OpenTelemetry GenAI Agent Spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)
+ [阿里云 ARMS LLM Trace 字段定义](https://www.alibabacloud.com/help/en/arms/application-monitoring/developer-reference/llm-trace-field-definition-description)
