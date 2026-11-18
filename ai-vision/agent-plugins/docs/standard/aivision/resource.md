# AI Vision Agent 可观测性规范 — Resource Attributes

> **版本**: v2.0
> **协议**: OpenTelemetry

`ant.*` 为既有遥测字段名，继续兼容已采集的数据；其值表示账号或 Agent 身份，不要求特定组织账号。

## 概述

Resource Attributes 用于标识产生遥测数据的实体（Entity），在 SDK 初始化时设置，并附加到所有 Traces、Metrics、Logs 上。

---

## AI Vision 身份标识 Resource Attributes

以下为 AI Vision Agent 特有的 Resource Attributes，用于标识 Agent 的身份和归属：

| 程序配置 | Attribute Key | 描述 | 必填 | 默认值 | 示例 |
|---------|---------------|------|------|--------|------|
| `ant.agent.type` | `ant.agent.type` | Agent 引擎类型 | ✅ 必填 | — | `"claudecode"` |
| `ant.agent.product` | `ant.agent.product` | 产品名称 | 推荐 | `"default"` | `"antclaudecode"` |
| `ant.agent.instance` | `ant.agent.instance` | 实例标识（格式 `{product}-{identifier}`） | 推荐 | `"default"` | `"antclaudecode-100001"` |
| `ant.publicKey` | `ant.publicKey` | 接入密钥（业务场景必填） | 条件必填 | — | `"pk-av-xyz789"` |

`ant.agent.type` 和 `ant.agent.product` 均接受自定义字符串，不做固定枚举校验。下表仅列出常见示例，不是允许值全集。

### `ant.agent.type` 常见示例

| Value | Description |
|-------|-------------|
| `claudecode` | 基于 Claude Code 引擎 |
| `opencode` | 基于 OpenCode 引擎 |
| `openclaw` | 基于 OpenClaw 引擎 |

### `ant.agent.product` 常见示例

| Value | agent.type | Description |
|-------|-----------|-------------|
| `claudecode` | `claudecode` | 原版 Claude Code |
| `antclaudecode` | `claudecode` | 定制版 Claude Code |
| `openclaw` | `openclaw` | OpenClaw 标准版 |
| `homeclaw` | `openclaw` | OpenClaw 家庭版 / 定制版 |
| `opencode` | `opencode` | OpenCode 标准版 |

---

## OpenTelemetry SDK 自动上报属性

OpenTelemetry SDK 初始化时会自动检测并上报以下 Resource Attributes，**无需手动配置**：

| Attribute Key | 描述 | 示例值 | 来源 |
|---------------|------|--------|------|
| `telemetry.sdk.name` | SDK 名称 | `"opentelemetry"` | SDK 自动设置 |
| `telemetry.sdk.language` | SDK 语言 | `"python"`, `"javascript"`, `"go"` | SDK 自动设置 |
| `telemetry.sdk.version` | SDK 版本 | `"1.25.0"` | SDK 自动设置 |

## 其他系统属性

| Key | Description | Example |
|-----|-------------|---------|
| `service.name` | 服务名称（映射自 `ant.agent.product`） | `"antclaudecode"` |
| `service.version` | 服务版本 | `"1.2.0"` |
| `service.instance.id` | 实例标识（映射自 `ant.agent.instance`） | `"antclaudecode-100001"` |
| `deployment.environment` | 部署环境 | `"production"`, `"staging"` |
| `os.type` | 操作系统类型 | `"darwin"`, `"linux"` |
| `host.arch` | 主机架构 | `"arm64"` |
