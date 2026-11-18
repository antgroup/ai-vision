# AI Vision Agent 可观测性规范 — 环境变量与配置约定

> **版本**: v2.0
> **协议**: OpenTelemetry

## 概述

本规范定义AI Vision Agent 可观测性系统的环境变量和程序配置约定。每个配置项同时提供 **环境变量**（`UPPER_SNAKE_CASE`）和 **程序配置**（`camelCase`，通用前缀用点号连接）两种形式，适配不同的集成方式。

---

> **Resource Attributes 定义**（AI Vision 身份标识属性、SDK 自动上报属性）已拆分至 [resource.md](../aivision/resource.md)。

---

## 一、OTel 标准配置

### 导出器端点配置

| 环境变量 | 程序配置 | 描述 | 默认值 |
|---------|---------|------|--------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `otel.exporter.otlp.endpoint` | Base OTLP endpoint（所有信号共用） | `http://localhost:4317` |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | `otel.exporter.otlp.traces.endpoint` | Traces 专用端点（覆盖 base） | — |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | `otel.exporter.otlp.metrics.endpoint` | Metrics 专用端点（覆盖 base） | — |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` | `otel.exporter.otlp.logs.endpoint` | Logs/Events 专用端点（覆盖 base） | — |
| `OTEL_EXPORTER_OTLP_HEADERS` | `otel.exporter.otlp.headers` | 附加 Headers（逗号分隔 key=value） | — |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `otel.exporter.otlp.protocol` | OTLP 协议 | `grpc` |

### 导出器类型配置

| 环境变量 | 程序配置 | 描述 | 可选值 | 默认值 |
|---------|---------|------|--------|--------|
| `OTEL_METRICS_EXPORTER` | `otel.metrics.exporter` | Metrics 导出器（逗号分隔） | `otlp`, `prometheus`, `console` | — |
| `OTEL_LOGS_EXPORTER` | `otel.logs.exporter` | Logs/Events 导出器（逗号分隔） | `otlp`, `console` | — |
| `OTEL_TRACES_EXPORTER` | `otel.traces.exporter` | Traces 导出器（逗号分隔） | `otlp`, `console` | — |

### 导出间隔配置

| 环境变量 | 程序配置 | 描述 | 默认值 |
|---------|---------|------|--------|
| `OTEL_METRIC_EXPORT_INTERVAL` | `otel.metric.exportInterval` | Metrics 导出间隔（毫秒） | `60000` |
| `OTEL_LOGS_EXPORT_INTERVAL` | `otel.logs.exportInterval` | Logs/Events 导出间隔（毫秒） | `5000` |

### Metrics 时间性配置

| 环境变量 | 程序配置 | 描述 | 可选值 | 默认值 |
|---------|---------|------|--------|--------|
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | `otel.exporter.otlp.metrics.temporalityPreference` | 指标时间性偏好 | `delta`, `cumulative` | `delta` |

### Resource 属性

| 环境变量 | 程序配置 | 描述 | 示例 |
|---------|---------|------|------|
| `OTEL_RESOURCE_ATTRIBUTES` | `otel.resource.attributes` | 自定义 Resource 属性（逗号分隔 key=value） | `department=engineering,team.id=platform` |

---

## 二、AI Vision 身份标识配置

### 核心身份属性

| 环境变量 | 程序配置 | 描述 | 必填 | 默认值 | 示例 |
|---------|---------|------|------|--------|------|
| `ANT_USERNAME` | `ant.username` | 用户标识 | ✅ 必填 | — | `"100001"` |
| `ANT_AGENT_TYPE` | `ant.agent.type` | Agent 引擎类型 | ✅ 必填 | — | `"claudecode"` |
| `ANT_AGENT_PRODUCT` | `ant.agent.product` | 产品名称 | 推荐 | `"default"` | `"antclaudecode"` |
| `ANT_AGENT_INSTANCE` | `ant.agent.instance` | 实例标识（格式 `{product}-{identifier}`） | 推荐 | `"default"` | `"antclaudecode-100001"` |
| `ANT_PUBLIC_KEY` | `ant.publicKey` | 接入密钥（业务场景必填） | 条件必填 | — | `"pk-av-xyz789"` |

### `ANT_AGENT_TYPE` 可选值

| Value | Description |
|-------|-------------|
| `claudecode` | 基于 Claude Code 引擎 |
| `opencode` | 基于 OpenCode 引擎 |
| `openclaw` | 基于 OpenClaw 引擎 |

### `ANT_AGENT_PRODUCT` 可选值

| Value | agent.type | Description |
|-------|-----------|-------------|
| `claudecode` | `claudecode` | 原版 Claude Code |
| `antclaudecode` | `claudecode` | 定制版 Claude Code |
| `openclaw` | `openclaw` | OpenClaw 标准版 |
| `homeclaw` | `openclaw` | OpenClaw 家庭版 / 定制版 |
| `opencode` | `opencode` | OpenCode 标准版 |

---

## 三、遥测总开关

| 环境变量 | 程序配置 | 描述 | 可选值 | 默认值 |
|---------|---------|------|--------|--------|
| `AGENT_ENABLE_TELEMETRY` | `agent.enableTelemetry` | 启用遥测采集（总开关） | `0` 关闭, `1` 开启 | `0` |

> ⚠️ 遥测默认关闭，必须显式设置 `AGENT_ENABLE_TELEMETRY=1` 才会启用数据采集。

---

## 四、内容记录控制

控制是否记录敏感内容。所有内容记录开关默认关闭，需显式开启。

### 用户 Prompt 内容

| 环境变量 | 程序配置 | 描述 | 可选值 | 默认值 |
|---------|---------|------|--------|--------|
| `AGENT_LOG_USER_PROMPTS` | `agent.log.userPrompts` | 记录用户 prompt 原文 | `0` 关闭, `1` 开启 | `0` |

**影响范围**：

- AI Vision事件 `agent.user_prompt` 中的 `prompt` 字段
- OTel 事件 `gen_ai.client.inference.operation.details` 中的 `gen_ai.input.messages` 属性
- 关闭时仅记录 `prompt_length`（字符长度）

### 系统 Prompt / 系统指令

| 环境变量 | 程序配置 | 描述 | 可选值 | 默认值 |
|---------|---------|------|--------|--------|
| `AGENT_LOG_SYSTEM_PROMPTS` | `agent.log.systemPrompts` | 记录系统 prompt / 系统指令 | `0` 关闭, `1` 开启 | `0` |

**影响范围**：

- OTel Span 的 `gen_ai.system_instructions` 属性
- OTel 事件中的系统指令相关字段

### 模型输出内容

| 环境变量 | 程序配置 | 描述 | 可选值 | 默认值 |
|---------|---------|------|--------|--------|
| `AGENT_LOG_MODEL_OUTPUTS` | `agent.log.modelOutputs` | 记录模型输出原文 | `0` 关闭, `1` 开启 | `0` |

**影响范围**：

- OTel Span 的 `gen_ai.output.messages` 属性
- OTel 事件 `gen_ai.client.inference.operation.details` 中的输出内容
- 关闭时仅记录 `gen_ai.usage.output_tokens`（token 数量）

### 工具调用详情

| 环境变量 | 程序配置 | 描述 | 可选值 | 默认值 |
|---------|---------|------|--------|--------|
| `AGENT_LOG_TOOL_DETAILS` | `agent.log.toolDetails` | 记录工具调用参数和 MCP/Skill 名称 | `0` 关闭, `1` 开启 | `0` |

**影响范围**：

- AI Vision事件 `agent.tool_result` 中的 `tool_parameters` 字段
- MCP 工具的 `mcp_server_name`、`mcp_tool_name` 字段
- Skill 工具的 `skill_name` 字段
- OTel Span 的 `gen_ai.tool.definitions` 属性
- OTel Span 的 `gen_ai.tool.call.arguments` 属性

### 工具调用输出

| 环境变量 | 程序配置 | 描述 | 可选值 | 默认值 |
|---------|---------|------|--------|--------|
| `AGENT_LOG_TOOL_OUTPUTS` | `agent.log.toolOutputs` | 记录工具执行结果原文 | `0` 关闭, `1` 开启 | `0` |

**影响范围**：

- OTel Span 的 `gen_ai.tool.call.result` 属性
- 关闭时仅记录 `tool_result_size_bytes`（结果大小）

---

## 五、Metrics 基数控制

控制 Metrics 中包含哪些高基数属性。

| 环境变量 | 程序配置 | 描述 | 可选值 | 默认值 |
|---------|---------|------|--------|--------|
| `OTEL_METRICS_INCLUDE_SESSION_ID` | `otel.metrics.includeSessionId` | Metrics 中包含 `session.id` | `true`, `false` | `true` |
| `OTEL_METRICS_INCLUDE_VERSION` | `otel.metrics.includeVersion` | Metrics 中包含 `app.version` | `true`, `false` | `false` |
| `OTEL_METRICS_INCLUDE_ACCOUNT_UUID` | `otel.metrics.includeAccountUuid` | Metrics 中包含 `user.account_uuid` | `true`, `false` | `true` |

> ⚠️ 降低基数可以减少存储成本和提升查询性能，但会降低分析粒度。`prompt.id` 等每次请求唯一的标识 **不应** 包含在 Metrics 中。

---

## 六、配置速查表

### 环境变量 → 程序配置 完整对照

| 环境变量 | 程序配置 | 分类 |
|---------|---------|------|
| **OTel 标准** | | |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `otel.exporter.otlp.endpoint` | 端点 |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | `otel.exporter.otlp.traces.endpoint` | 端点 |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | `otel.exporter.otlp.metrics.endpoint` | 端点 |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` | `otel.exporter.otlp.logs.endpoint` | 端点 |
| `OTEL_EXPORTER_OTLP_HEADERS` | `otel.exporter.otlp.headers` | 认证 |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `otel.exporter.otlp.protocol` | 协议 |
| `OTEL_METRICS_EXPORTER` | `otel.metrics.exporter` | 导出器 |
| `OTEL_LOGS_EXPORTER` | `otel.logs.exporter` | 导出器 |
| `OTEL_TRACES_EXPORTER` | `otel.traces.exporter` | 导出器 |
| `OTEL_METRIC_EXPORT_INTERVAL` | `otel.metric.exportInterval` | 间隔 |
| `OTEL_LOGS_EXPORT_INTERVAL` | `otel.logs.exportInterval` | 间隔 |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | `otel.exporter.otlp.metrics.temporalityPreference` | Metrics |
| `OTEL_RESOURCE_ATTRIBUTES` | `otel.resource.attributes` | Resource |
| `OTEL_METRICS_INCLUDE_SESSION_ID` | `otel.metrics.includeSessionId` | 基数控制 |
| `OTEL_METRICS_INCLUDE_VERSION` | `otel.metrics.includeVersion` | 基数控制 |
| `OTEL_METRICS_INCLUDE_ACCOUNT_UUID` | `otel.metrics.includeAccountUuid` | 基数控制 |
| **AI Vision 身份标识** | | |
| `ANT_USERNAME` | `ant.username` | 身份 |
| `ANT_AGENT_TYPE` | `ant.agent.type` | 身份 |
| `ANT_AGENT_PRODUCT` | `ant.agent.product` | 身份 |
| `ANT_AGENT_INSTANCE` | `ant.agent.instance` | 身份 |
| `ANT_PUBLIC_KEY` | `ant.publicKey` | 身份 |
| **遥测控制** | | |
| `AGENT_ENABLE_TELEMETRY` | `agent.enableTelemetry` | 总开关 |
| `AGENT_LOG_USER_PROMPTS` | `agent.log.userPrompts` | 内容记录 |
| `AGENT_LOG_SYSTEM_PROMPTS` | `agent.log.systemPrompts` | 内容记录 |
| `AGENT_LOG_MODEL_OUTPUTS` | `agent.log.modelOutputs` | 内容记录 |
| `AGENT_LOG_TOOL_DETAILS` | `agent.log.toolDetails` | 内容记录 |
| `AGENT_LOG_TOOL_OUTPUTS` | `agent.log.toolOutputs` | 内容记录 |

---

## 七、命名转换规则

### 环境变量 → 程序配置的转换规则

1. **OTel 标准前缀**：`OTEL_` → `otel.`，后续按 `_` 分隔转驼峰
   - `OTEL_EXPORTER_OTLP_ENDPOINT` → `otel.exporter.otlp.endpoint`
   - `OTEL_METRIC_EXPORT_INTERVAL` → `otel.metric.exportInterval`
   - `OTEL_METRICS_INCLUDE_SESSION_ID` → `otel.metrics.includeSessionId`

2. **AI Vision 身份前缀**：`ANT_` → `ant.`，后续按 `_` 分隔转驼峰
   - `ANT_USERNAME` → `ant.username`
   - `ANT_AGENT_TYPE` → `ant.agent.type`
   - `ANT_PUBLIC_KEY` → `ant.publicKey`

3. **Agent 控制前缀**：`AGENT_` → `agent.`，后续按 `_` 分隔转驼峰
   - `AGENT_ENABLE_TELEMETRY` → `agent.enableTelemetry`
   - `AGENT_LOG_USER_PROMPTS` → `agent.log.userPrompts`

### 通用规则

- 通用前缀（`otel`、`ant`、`agent`）用 **点号** `.` 连接
- 语义分组（`exporter.otlp`、`agent.type`、`log`）用 **点号** `.` 连接
- 末端字段名用 **驼峰** 命名（`exportInterval`、`userPrompts`、`publicKey`）
- 已有标准缩写保持原样（`otlp`、`uuid`）

---

## 八、集成示例

### 环境变量方式

```bash
# 总开关
export AGENT_ENABLE_TELEMETRY=1

# AI Vision 身份
export ANT_USERNAME=100001
export ANT_AGENT_TYPE=claudecode
export ANT_AGENT_PRODUCT=antclaudecode
export ANT_AGENT_INSTANCE=antclaudecode-100001

# OTel 端点
export OTEL_EXPORTER_OTLP_ENDPOINT=http://collector.example.com:4317
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer your-token"

# 导出器
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_TRACES_EXPORTER=otlp

# 导出间隔
export OTEL_METRIC_EXPORT_INTERVAL=60000
export OTEL_LOGS_EXPORT_INTERVAL=5000

# 内容记录（按需开启）
export AGENT_LOG_USER_PROMPTS=0
export AGENT_LOG_SYSTEM_PROMPTS=0
export AGENT_LOG_MODEL_OUTPUTS=0
export AGENT_LOG_TOOL_DETAILS=0
export AGENT_LOG_TOOL_OUTPUTS=0
```

### 程序配置方式（JSON）

```json
{
  "agent": {
    "enableTelemetry": true,
    "log": {
      "userPrompts": false,
      "systemPrompts": false,
      "modelOutputs": false,
      "toolDetails": false,
      "toolOutputs": false
    }
  },
  "ant": {
    "username": "100001",
    "agent": {
      "type": "claudecode",
      "product": "antclaudecode",
      "instance": "antclaudecode-100001"
    },
    "publicKey": ""
  },
  "otel": {
    "exporter": {
      "otlp": {
        "endpoint": "http://collector.example.com:4317",
        "protocol": "grpc",
        "headers": "Authorization=Bearer your-token",
        "traces": {
          "endpoint": ""
        },
        "metrics": {
          "endpoint": "",
          "temporalityPreference": "delta"
        },
        "logs": {
          "endpoint": ""
        }
      }
    },
    "metrics": {
      "exporter": "otlp",
      "includeSessionId": true,
      "includeVersion": false,
      "includeAccountUuid": true
    },
    "logs": {
      "exporter": "otlp",
      "exportInterval": 5000
    },
    "traces": {
      "exporter": "otlp"
    },
    "metric": {
      "exportInterval": 60000
    },
    "resource": {
      "attributes": ""
    }
  }
}
```

### 程序配置方式（TypeScript 接口）

```typescript
interface AgentTelemetryConfig {
  agent: {
    enableTelemetry: boolean;
    log: {
      userPrompts: boolean;
      systemPrompts: boolean;
      modelOutputs: boolean;
      toolDetails: boolean;
      toolOutputs: boolean;
    };
  };
  ant: {
    username: string;
    agent: {
      type: "claudecode" | "opencode" | "openclaw";
      product: string;
      instance: string;
    };
    publicKey?: string;
  };
  otel: {
    exporter: {
      otlp: {
        endpoint: string;
        protocol: "grpc" | "http/json" | "http/protobuf";
        headers?: string;
        traces?: { endpoint?: string };
        metrics?: {
          endpoint?: string;
          temporalityPreference?: "delta" | "cumulative";
        };
        logs?: { endpoint?: string };
      };
    };
    metrics: {
      exporter: string;
      includeSessionId: boolean;
      includeVersion: boolean;
      includeAccountUuid: boolean;
    };
    logs: {
      exporter: string;
      exportInterval: number;
    };
    traces: {
      exporter: string;
    };
    metric: {
      exportInterval: number;
    };
    resource?: {
      attributes?: string;
    };
  };
}
```

---

## 九、内容记录决策矩阵

| 内容类型 | 环境变量 | 开启时记录的字段 | 关闭时记录的字段 | 安全风险 |
|---------|---------|----------------|----------------|---------|
| **用户 Prompt** | `AGENT_LOG_USER_PROMPTS` | `prompt`（原文） | `prompt_length`（长度） | 🔴 高 — 可能含 PII |
| **系统 Prompt** | `AGENT_LOG_SYSTEM_PROMPTS` | `gen_ai.system_instructions` | 不记录 | 🟡 中 — 可能含业务逻辑 |
| **模型输出** | `AGENT_LOG_MODEL_OUTPUTS` | `gen_ai.output.messages` | `gen_ai.usage.output_tokens` | 🔴 高 — 可能含生成的敏感内容 |
| **工具调用详情** | `AGENT_LOG_TOOL_DETAILS` | `tool_parameters`, `mcp_server_name`, `skill_name` | `tool_name`, `success`, `duration_ms` | 🟡 中 — 可能含命令/路径 |
| **工具调用输出** | `AGENT_LOG_TOOL_OUTPUTS` | `gen_ai.tool.call.result` | `tool_result_size_bytes` | 🔴 高 — 可能含文件内容 |

### 推荐配置

| 环境 | 用户 Prompt | 系统 Prompt | 模型输出 | 工具详情 | 工具输出 |
|------|:---------:|:---------:|:------:|:------:|:------:|
| **生产环境** | ❌ | ❌ | ❌ | ❌ | ❌ |
| **预发环境** | ❌ | ✅ | ❌ | ✅ | ❌ |
| **开发/调试** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **评测链路** | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 十、配置优先级

当同一配置项通过多种方式设置时，优先级从高到低：

1. **Managed Settings**（MDM 下发的托管配置，不可覆盖）
2. **环境变量**
3. **程序配置文件**（JSON / YAML）
4. **默认值**

> 环境变量和程序配置同时存在时，环境变量优先。
