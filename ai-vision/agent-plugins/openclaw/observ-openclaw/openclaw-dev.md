## observ-openclaw 开发环境测试安装手册

本文用于开发环境测试。使用前将占位地址和凭据替换为当前测试环境的配置。

### 前置条件

- OpenClaw CLI 已安装
- Node.js 与 npm 已安装
- 如需直连 Langfuse，从目标项目的设置中获取对应的 Public Key 和 Secret Key。

### 步骤一：构建本地安装包

在 `observ-openclaw/` 目录执行：

```bash
EDITION=common npm pack
```

### 步骤二：安装插件

```bash
openclaw plugins install ./aivision-observ-openclaw-<version>.tgz
```

### 步骤三：配置插件

在 OpenClaw 配置文件（`openclaw.json` 或对应的 plugins 配置段）中添加：

下面演示经认证 OTLP 网关上报；地址和采集凭据使用当前部署提供的值。

```json
{
  "observ-openclaw": {
    "enabled": true,
    "config": {
      "enableTelemetry": true,
      "serviceName": "openclaw",
      "collectionMode": "full",
      "otelExporter": {
        "resourceAttributes": {
          "ant.agent.product": "OpenClaw"
        }
      },
      "tracesConfig": {
        "endpoint": "https://<OTLP_GATEWAY_HOST>/v1/traces",
        "headers": {
          "Authorization": "Bearer <INGEST_TOKEN>"
        },
        "enabled": true
      },
      "metricsConfig": {
        "endpoint": "https://<OTLP_GATEWAY_HOST>/v1/metrics",
        "enabled": true,
        "headers": {
          "Authorization": "Bearer <INGEST_TOKEN>"
        }
      },
      "eventsConfig": {
        "endpoint": "https://<OTLP_GATEWAY_HOST>/v1/logs",
        "headers": {
          "Authorization": "Bearer <INGEST_TOKEN>"
        },
        "enabled": true
      }
    }
  }
}
```

### 配置说明

| 配置项 | 说明 |
|--------|------|
| **enableTelemetry** | 遥测总开关，必须为 `true` 才会采集数据 |
| **serviceName** | OTel service name，标识服务来源 |
| **collectionMode** | 内容采集模式。`full` 记录当前单轮内容；`privacy` 隐私模式下内容字段写入 `[REDACTED]`。开发环境可用 `full`，生产环境建议用 `AIVISION_COLLECTION_MODE=privacy` |
| **otelExporter.resourceAttributes** | Resource 属性；用 `ant.agent.product` 配置产品标识 |
| **tracesConfig** | Traces 导出配置，endpoint 指向 AIVision 平台 |
| **metricsConfig** | Metrics 导出配置，endpoint 指向 AntCollector |
| **eventsConfig** | Events/Logs 导出配置，endpoint 指向日志收集服务 |

### 验证安装

启动 OpenClaw 后，查看日志中是否出现：

```
[observ-openclaw] Plugin initialized successfully
[observ-openclaw] Registered before_model_resolve hook
[observ-openclaw] Registered before_agent_start hook
[observ-openclaw] Registered llm_input/llm_output hooks
[observ-openclaw] Registered tool hooks
[observ-openclaw] Registered agent_end hook
```

### 更新插件

```bash
openclaw plugins install ./aivision-observ-openclaw-<version>.tgz
```

### 卸载插件

```bash
openclaw plugins uninstall observ-openclaw
```
