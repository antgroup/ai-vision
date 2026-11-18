# AI Agent OpenTelemetry Demo (Java)

基于 aivision-agents 语义约定（`docs/semconv/*.yaml`）的 Java OpenTelemetry 示例，模拟 Claude Code 多轮对话产生符合规范的 Span 数据。

## 快速开始

```bash
cd demos/java-demo
mvn compile exec:exec
```

### OTLP Exporter 配置

默认上报到 `http://localhost:4318/v1/traces`（HTTP/protobuf），可通过环境变量覆盖：

```bash
# 自定义 endpoint
OTEL_EXPORTER_OTLP_ENDPOINT=http://your-collector:4318/v1/traces mvn compile exec:exec

# 自定义 headers（逗号分隔 key=value）
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces \
OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic xxx,X-Tenant=demo" \
mvn compile exec:exec
```

本地测试可用 [Jaeger](https://www.jaegertracing.io/) all-in-one：

```bash
./jaeger-all-in-one   # OTLP HTTP :4318, UI :16686
mvn compile exec:exec # spans 上报后在 http://localhost:16686 查看
```

## Span 拓扑 (A1)

```
Turn 1 (CHAT, invoke_agent) [SERVER]
├── chat claude-sonnet-4-6  (LLM, generation 0) [CLIENT]    ← token usage L1/L2
├── execute_tool Read       (TOOL, builtin)      [INTERNAL]  ← 85ms, exit_code=0
└── chat claude-sonnet-4-6  (LLM, generation 1) [CLIENT]

Turn 2 (CHAT, invoke_agent) [SERVER]
├── chat claude-sonnet-4-6  (LLM, generation 0) [CLIENT]
├── execute_tool mcp search (TOOL, mcp, ERROR)   [INTERNAL]  ← timeout 错误 (B2)
├── chat claude-sonnet-4-6  (LLM, generation 1) [CLIENT]    ← agent 恢复
└── execute_tool Bash       (TOOL, builtin)      [INTERNAL]  ← fallback 工具
```

## 覆盖的语义约定

| 能力 | 覆盖 | 说明 |
|------|------|------|
| A1 多轮会话拓扑 | ✅ | CHAT root → LLM/TOOL sibling |
| B1 工具精确耗时 | ✅ | `agentic.tool.duration_ms` |
| B2 工具错误识别 | ✅ | `status.code=ERROR` + `error.type` |
| B5 MCP 调用识别 | ✅ | `agentic.tool.type=mcp` |
| C1 LLM 精确耗时 | ✅ | `agentic.llm.duration_ms` |
| C5 LLM 模型输出 | ✅ | `gen_ai.output.messages` |
| C7 Token usage  | ✅ | L1 normalized + L2 breakdown |
| Resource 4 必填 | ✅ | `ant.agent.type/env/scene/username` |

## 项目结构

```
src/main/java/io/aivision/demo/
├── AgentOtelDemo.java          # 主入口，模拟 2-turn 对话
└── semconv/
    ├── SpanAttributes.java     # Span 属性常量 (gen_ai.* / agentic.* / ant.*)
    └── AgentResource.java      # Resource 构建器 (entities.yaml)
```

## 技术栈

- JDK 1.8+
- Maven
- OpenTelemetry Java SDK 1.40.0

## 属性来源映射

| Java 常量 | semconv YAML | 文件 |
|-----------|-------------|------|
| `GEN_AI_SPAN_KIND` | `gen_ai.span.kind` | attribute_groups.yaml |
| `GEN_AI_OPERATION_NAME` | `gen_ai.operation.name` | attribute_groups.yaml |
| `GEN_AI_REQUEST_MODEL` | `gen_ai.request.model` | attribute_groups.yaml |
| `GEN_AI_USAGE_INPUT_TOKENS` | `gen_ai.usage.input_tokens` | attribute_groups.yaml |
| `AGENTIC_TOOL_TYPE` | `agentic.tool.type` | attribute_groups.yaml |
| `AGENTIC_TOOL_DURATION_MS` | `agentic.tool.duration_ms` | attribute_groups.yaml |
| `AGENTIC_LLM_DURATION_MS` | `agentic.llm.duration_ms` | attribute_groups.yaml |
| `ANT_AGENT_TYPE` | `ant.agent.type` | entities.yaml |
| `ANT_AGENT_ENV` | `ant.agent.env` | entities.yaml |
