# Langfuse Traces 接入指南（通过 observ-openclaw 插件）

## 概述

通过 `observ-openclaw` 插件的 `tracesConfig` 独立信号配置，将 OTel traces 数据单独发送到 Langfuse，同时 metrics 和 events 继续发送到其他后端（如 OpenObserve）。

## 前置条件

- Langfuse 实例已部署并可访问
- 已获取 Langfuse 的 Public Key 和 Secret Key

## Langfuse OTel 接口说明

Langfuse 的 OTLP traces 接口路径为：

```
{LANGFUSE_BASE_URL}/api/public/otel/v1/traces
```

> **注意**：Langfuse 使用 `/api/public/otel` 前缀，不是标准的 OTLP `/v1/traces` 路径。

认证方式为 **HTTP Basic Auth**，格式为 `Basic base64(public_key:secret_key)`。

## 配置步骤

### 1. 生成 Basic Auth 认证串

```bash
echo -n "<LANGFUSE_PUBLIC_KEY>:<LANGFUSE_SECRET_KEY>" | base64
```

示例：

```bash
echo -n "pk-lf-xxx:sk-lf-yyy" | base64
# 输出: cGstbGYteHh4OnNrLWxmLXl5eQ==
```

### 2. 修改 `~/.openclaw/openclaw.json`

在 `plugins.entries.observ-openclaw.config` 中配置 `tracesConfig`：

```json
{
  "plugins": {
    "entries": {
      "observ-openclaw": {
        "enabled": true,
        "config": {
          "enableTelemetry": true,
          "otelExporter": {
            "endpoint": "http://localhost:5080/api/default",
            "protocol": "http/protobuf",
            "headers": {
              "Authorization": "Basic <原有后端的认证>"
            }
          },
          "tracesConfig": {
            "endpoint": "https://<LANGFUSE_HOST>/api/public/otel/v1/traces",
            "headers": {
              "Authorization": "Basic <base64(pk:sk)>"
            },
            "enabled": true
          },
          "metricsConfig": {
            "endpoint": "http://localhost:5080/api/default/v1/metrics",
            "enabled": true
          },
          "eventsConfig": {
            "endpoint": "http://localhost:5080/api/default/v1/logs",
            "enabled": true
          }
        }
      }
    }
  }
}
```

**关键点**：

- `tracesConfig.endpoint` 必须是完整的 URL（含 `/api/public/otel/v1/traces`），因为 `telemetry.ts` 中的 `getEndpointAndHeaders` 检测到 URL 包含 `/v1/` 时会直接使用，不再拼接路径
- `tracesConfig.headers` 会覆盖 `otelExporter.headers`，实现独立认证
- `metricsConfig` 和 `eventsConfig` 保持原有后端不变

### 3. 重启 openclaw

```bash
# 重启 openclaw gateway 使配置生效
openclaw restart
```

## 工作原理

`telemetry.ts` 中的信号路由逻辑：

```
tracesConfig.endpoint  → Langfuse (traces only)
metricsConfig.endpoint → OpenObserve (metrics only)
eventsConfig.endpoint  → OpenObserve (events only)
```

每个信号独立创建 OTel exporter，互不影响：

- **Traces** → `OTLPTraceExporter` → Langfuse
- **Metrics** → `OTLPMetricExporter` → OpenObserve
- **Events** → `OTLPLogExporter` → OpenObserve

## 验证

启动后检查日志输出：

```
[observ-openclaw] Traces initialized: https://aivision.example.com/api/public/otel/v1/traces
[observ-openclaw] Metrics initialized: http://localhost:5080/api/default/v1/metrics
[observ-openclaw] Events initialized: http://localhost:5080/api/default/v1/logs
```

在 Langfuse 控制台的 Traces 页面应能看到 `openclaw.chat` 根 span 及其子 span（`agent.turn`、`llm.call`、`tool.call`）。

## 注意事项

- Langfuse **仅支持 OTLP HTTP**，不支持 gRPC，`protocol` 应为 `http/protobuf` 或 `http/json`
- Langfuse 当前仅接收 **traces**（spans），不接收 metrics 和 logs
- `tracesConfig.headers` 中的 Authorization 会覆盖全局 `otelExporter.headers`，确保 Langfuse 使用自己的认证
- 如需恢复原配置，备份文件位于 `~/.openclaw/openclaw.json.bak.*`
