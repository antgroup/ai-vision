# observ-openclaw 配置说明

配置位置：`~/.openclaw/openclaw.json` -> `plugins.entries.observ-openclaw.config`

## 顶层配置

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
| --- | --- | --- | --- | --- |
| `enableTelemetry` | `boolean` | `false` | `AGENT_ENABLE_TELEMETRY` | 总开关 |
| `serviceName` | `string` | `"openclaw"` | `OTEL_SERVICE_NAME` | OTel service.name |
| `collectionMode` | `"full" \| "privacy"` | `"full"` | `AIVISION_COLLECTION_MODE` | 内容采集模式 |
| `dynamicUsername` | `boolean` | `false` | — | 每次记录信号时从 `AIVISION_ANT_USERNAME` 解析 `ant.username` |
| `dynamicUid` | `boolean` | `false` | — | 每次记录信号时从 `AIVISION_ANT_UID` 解析 `ant.uid` |
| `metricExportInterval` | `number` | `60000` | `OTEL_METRIC_EXPORT_INTERVAL` | Metrics 导出间隔，毫秒 |
| `logsExportInterval` | `number` | `5000` | `OTEL_LOGS_EXPORT_INTERVAL` | Events/Logs 导出间隔，毫秒 |
| `logDir` | `string` | common: OpenClaw runtime `logs`; AIVision: `~/.aivision/ocw/logs` | `AIVISION_LOG_DIR`, `OBSERV_OPENCLAW_LOG_DIR` | 插件独立日志 `observ-openclaw.log` 的目录 |

日志目录优先级：`logDir` > `AIVISION_LOG_DIR` > `OBSERV_OPENCLAW_LOG_DIR` > edition 默认值。common edition 跟随 `OPENCLAW_STATE_DIR/logs`（未设置时为 `~/.openclaw/logs`）。

`collectionMode` 是唯一内容采集开关：

- `full`：写入当前单轮的用户输入、系统指令、模型输出、工具参数、工具结果。
- `privacy`：不写入原文；span/event 对应内容字段稳定存在并写入 `[REDACTED]`。

旧的 `log.userPrompts` / `log.events.*` / `log.spans.*` 五类独立开关、`captureContent`、`AGENT_LOG_*` 环境变量不再是支持的配置面。

## Transcript / replay 输出

`observ-openclaw` 只负责实时 hook 到 OTel 的语义转换与 HTTP 上报。Transcript 持久化、本地 OTLP JSONL 双写和离线 replay/report 由独立的 [`openclaw-replay`](../openclaw-replay/README.md) 插件负责；离线 replay 直接调用 `observ-openclaw.createHooks()`，不维护第二份 span 构造逻辑。

## `otelExporter`

| 字段 | 类型 | 默认值 | 环境变量 | 说明 |
| --- | --- | --- | --- | --- |
| `endpoint` | `string` | — | `OTEL_EXPORTER_OTLP_ENDPOINT` | 全局 OTLP endpoint |
| `protocol` | `"grpc" \| "http/json" \| "http/protobuf"` | `"grpc"` | `OTEL_EXPORTER_OTLP_PROTOCOL` | OTLP 协议 |
| `headers` | `object` | `{}` | `OTEL_EXPORTER_OTLP_HEADERS` | 请求头，环境变量格式 `key1=val1,key2=val2` |
| `attributes` | `object` | `{}` | `OBSERV_OPENCLAW_OTEL_ATTRIBUTES` | 注入所有 span、event 和 metric 的通用 attributes；`ant.username`、`ant.uid` 在此配置 |
| `resourceAttributes` | `object` | `{}` | `OTEL_RESOURCE_ATTRIBUTES`, `AIVISION_OTEL_RESOURCE_ATTRIBUTES`, `OBSERV_OPENCLAW_OTEL_RESOURCE_ATTRIBUTES` | Resource attribute 覆盖；`ant.agent.*` 在此配置 |
| `metricsTemporalityPreference` | `"delta" \| "cumulative"` | `"delta"` | `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | Metrics 时间性偏好 |

Resource attributes 合并优先级从低到高：

1. OpenClaw 内置 resource 字段。
2. 配置文件 `otelExporter.resourceAttributes`。
3. 标准 `OTEL_RESOURCE_ATTRIBUTES`。
4. `AIVISION_OTEL_RESOURCE_ATTRIBUTES`。
5. `OBSERV_OPENCLAW_OTEL_RESOURCE_ATTRIBUTES`。
6. `agentic.collection.mode` 由最终 `collectionMode` 强制写入，不能被配置或环境变量覆盖。

环境变量格式为逗号分隔 `key=value`，重复 key 由更高优先级覆盖。最终 resource 会传入 traces、metrics、events/logs 三类 provider。

`OBSERV_OPENCLAW_OTEL_ATTRIBUTES` 同样使用逗号分隔 `key=value`。这些字段会写入所有 span/event/metric 的 attributes；当 key 与具体 hook 生成的语义字段重复时，具体 hook 字段优先。

旧的顶层 `ant` 配置及 `AIVISION_USER_ID`、`OBSERV_OPENCLAW_USER_ID`、`AIVISION_AGENT_*` 不再支持。若仍传入顶层 `ant`，插件会明确报错。启用顶层 `dynamicUsername` / `dynamicUid` 后，插件会在每次记录 span、event/log 或 metric 时分别读取 `AIVISION_ANT_USERNAME` / `AIVISION_ANT_UID`；环境变量为空时回退到 `otelExporter.attributes` 中的静态值。`ant.uid` 是信号属性；即使通过 Resource 环境变量提供，也会从 Resource 中移除。

内置 resource 字段包括：

- `service.name`
- `service.version`
- `agentic.runtime.name`
- `ant.agent.type`
- `ant.agent.product`
- `ant.agent.instance`
- `ant.agent.env`
- `ant.agent.scene`
- `deployment.environment`
- `deployment.environment.name`
- `telemetry.sdk.language`
- `telemetry.sdk.name`
- `telemetry.sdk.version`
- `agentic.collection.mode`，强制等于最终 `collectionMode`

## `tracesConfig` / `metricsConfig` / `eventsConfig`

每个信号可独立配置 endpoint、headers、enabled，覆盖全局 `otelExporter`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `endpoint` | `string` | 该信号 OTLP endpoint |
| `headers` | `object` | 该信号请求头 |
| `enabled` | `boolean` | 是否启用该信号，默认 `true` |

环境变量：

- Traces: `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` / `OTEL_EXPORTER_OTLP_TRACES_HEADERS`
- Metrics: `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` / `OTEL_EXPORTER_OTLP_METRICS_HEADERS`
- Events: `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` / `OTEL_EXPORTER_OTLP_LOGS_HEADERS`

## `openclaw-replay` 插件环境变量

`OPENCLAW_REPLAY_CAPTURE_ENABLED`、`OPENCLAW_REPLAY_SESSION_DIR`、`OPENCLAW_REPLAY_SESSION_NAME`、`OPENCLAW_REPLAY_COLLECTION_MODE`、`OPENCLAW_REPLAY_STDOUT` 和 `OPENCLAW_REPLAY_DEBUG` 属于独立的 `openclaw-replay` 插件。生产 `OBSERV_OPENCLAW_*` 变量不会启用或配置 replay；旧 replay/session 变量仅触发迁移警告，不采用其值。

## 示例

```json
{
  "enabled": true,
  "config": {
	    "enableTelemetry": true,
	    "serviceName": "openclaw",
	    "collectionMode": "privacy",
    "otelExporter": {
      "endpoint": "http://localhost:5080/api/default",
      "protocol": "http/protobuf",
      "headers": {
        "Authorization": "Basic <base64-credentials>"
      },
      "attributes": {
        "ant.username": "100001",
        "ant.uid": "user-uid"
      },
      "resourceAttributes": {
        "ant.agent.type": "openclaw",
        "ant.agent.product": "OpenClaw",
        "ant.agent.env": "Prod",
        "ant.agent.scene": "default",
        "deployment.region": "cn-hangzhou"
      }
    },
    "tracesConfig": {
      "endpoint": "https://aivision.example.com/api/public/otel/v1/traces",
      "headers": {
        "Authorization": "Basic <langfuse-base64-credentials>"
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
```
