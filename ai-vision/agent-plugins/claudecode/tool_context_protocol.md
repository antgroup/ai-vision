# Agentic Tool Context Protocol

跨 agent 的工具调用 trace context 传递协议。定义了 agent hook 系统注入到工具进程的标准环境变量，使任何工具脚本都能参与 OTel trace 链路。

## 设计原则

1. **标准优先**：尽可能使用 W3C / OTel 标准变量名
2. **Agent 无关**：变量名不绑定特定 agent（无 `CCHOOKS_`/`CDXHOOKS_` 前缀）
3. **零依赖可读**：工具脚本只需读环境变量 + HTTP POST，无需 OTel SDK
4. **安全默认**：未注入时所有变量为空，工具脚本应优雅降级

## 环境变量规范

### Trace 传播（W3C 标准）

| 变量 | 标准来源 | 格式 | 示例 |
|------|---------|------|------|
| `TRACEPARENT` | W3C Trace Context | `{version}-{trace_id}-{parent_span_id}-{flags}` | `00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01` |
| `SOFA_TRACEID` | SOFA RPC | hex string | `0a1b2c3d4e5f...` |
| `SOFA_RPCID` | SOFA RPC | dotted notation | `0.1.2.3` |

**`TRACEPARENT`** 是核心变量。工具脚本从中解析 `trace_id` 和 `parent_span_id`，构建 child span 时设置 `parentSpanId = parent_span_id`，`traceId = trace_id`。

### OTLP 导出配置（OTel 标准）

| 变量 | 标准来源 | 格式 | 说明 |
|------|---------|------|------|
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | OTel SDK Spec | URL | OTLP traces 上报地址 |
| `OTEL_EXPORTER_OTLP_AUTH` | 扩展 | Base64 string | `Basic base64(public_key:secret_key)` |
| `OTEL_EXPORTER_OTLP_HEADERS` | OTel SDK Spec | JSON | `{"x-antmonitor-forward-service":"antcollectorgw-local"}` |
| `OTEL_RESOURCE_ATTRIBUTES` | OTel SDK Spec | JSON | resource 级属性（`service.name`, `ant.agent.product` 等） |

> **注意**：标准 OTel SDK 的 `OTEL_EXPORTER_OTLP_HEADERS` 格式为 `key=value,key2=value2`（逗号分隔）。本协议使用 JSON 格式以支持值中的特殊字符。工具脚本应两种格式都能解析。

### Agentic 扩展

| 变量 | 格式 | 说明 |
|------|------|------|
| `AGENTIC_TOOL_USE_ID` | string | agent 分配的工具调用唯一标识符（如 Claude 的 `tool_use_id`） |
| `AGENTIC_SPAN_ATTRIBUTES` | JSON | span 级属性（`user.id`, `ant.username`, `gen_ai.conversation.id` 等） |

### 完整示例

工具进程启动时的环境变量：

```bash
TRACEPARENT=00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
AGENTIC_TOOL_USE_ID=call_bf482e784def4938a305fa78
SOFA_TRACEID=0a1b2c3d4e5f
SOFA_RPCID=0.1.2
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://collector.example.com/v1/traces
OTEL_EXPORTER_OTLP_AUTH=cHVibGljOmtleQ==
OTEL_EXPORTER_OTLP_HEADERS={"x-antmonitor-forward-service":"antcollectorgw-local"}
AGENTIC_SPAN_ATTRIBUTES={"user.id":"alice","ant.username":"alice","gen_ai.conversation.id":"sess-123"}
OTEL_RESOURCE_ATTRIBUTES={"service.name":"aivision","ant.agent.product":"aivision","ant.agent.type":"claudecode"}
```

## 工具脚本集成指南

### 最简集成（Python，零依赖）

```python
from tool_context_pure import ToolTraceContext, record_tool_span

ctx = ToolTraceContext.from_env()
if ctx.is_active():
    record_tool_span(ctx, name="my-operation", input="...", output="...", status="ok")
```

### 手动集成（任意语言）

```
1. 读取 TRACEPARENT → 解析 trace_id + parent_span_id
2. 生成随机 span_id (16 hex chars)
3. 构建 OTLP JSON: resourceSpans → scopeSpans → spans
4. POST 到 OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
   - Header: Content-Type: application/json
   - Header: Authorization: Basic {OTEL_EXPORTER_OTLP_AUTH}
   - Header: (merge OTEL_EXPORTER_OTLP_HEADERS)
5. 合并 AGENTIC_SPAN_ATTRIBUTES 到 span attributes
6. 合并 OTEL_RESOURCE_ATTRIBUTES 到 resource attributes
```

### 降级行为

| 条件 | 行为 |
|------|------|
| `TRACEPARENT` 为空 | 不发送 span，正常执行工具逻辑 |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 为空 | 不发送 span（可打印到 stdout 用于调试） |
| 发送失败（网络错误/非 2xx） | 忽略错误，不影响工具执行 |

## Span 属性规范

工具脚本生成的 child span 应包含以下属性（与 agent hook 系统的 tool span 对齐）：

### 必须

| 属性 | 值 |
|------|-----|
| `gen_ai.span.kind` | `"TOOL"` |
| `gen_ai.operation.name` | `"execute_tool"` |
| `gen_ai.tool.name` | span 名称 |
| `gen_ai.tool.call.id` | `AGENTIC_TOOL_USE_ID` |

### 建议

| 属性 | 值 |
|------|-----|
| `gen_ai.tool.call.arguments` | 工具输入（JSON string） |
| `gen_ai.tool.call.result` | 工具输出（JSON string） |
| `agentic.tool.type` | `"tool_script"` |
| `agentic.tool.duration_ms` | 执行耗时（毫秒） |
| `error.type` | `"tool_error"`（仅失败时） |
| `tool.script` | 脚本文件名 |

### 从环境继承

`AGENTIC_SPAN_ATTRIBUTES` 中的属性（`user.id`, `ant.username` 等）应合并到 span attributes。
`OTEL_RESOURCE_ATTRIBUTES` 中的属性（`service.name`, `ant.agent.product` 等）应设为 resource attributes。

## 适用范围

| Agent | Hook 系统 | 状态 |
|-------|----------|------|
| Claude Code | cchooks ToolContextHandler | ✅ 已实现 |
| Codex | cdxhooks | 待实现（使用相同协议） |
| OpenCode | observ-opencode | 待实现 |
| OpenClaw | observ-openclaw | 待实现 |

## 版本

- v0.1.0 (2026-06-29): 初始版本，从 cchooks `CCHOOKS_TOOL_*` 重命名为标准化变量名
