# Claude Code 可观测性规范 — Metrics

> **Source**: [Claude Code Monitoring - Usage](https://code.claude.com/docs/en/monitoring-usage)
> **协议**: OpenTelemetry Metrics
> **Meter Name**: `com.anthropic.claude_code`
> **service.name**: `claude-code`

## 概述

Claude Code 通过 OpenTelemetry 协议导出 Metrics 时序数据。所有指标使用 `claude_code.*` 前缀，采用 Counter 类型。需设置 `CLAUDE_CODE_ENABLE_TELEMETRY=1` 启用遥测采集。

---

## 一、标准属性（所有指标共享）

| Attribute | Type | Description | 控制方式 |
|-----------|------|-------------|---------|
| `session.id` | string | 会话唯一标识 | `OTEL_METRICS_INCLUDE_SESSION_ID`（默认 `true`） |
| `app.version` | string | Claude Code 版本号 | `OTEL_METRICS_INCLUDE_VERSION`（默认 `false`） |
| `organization.id` | string | 组织 UUID（OAuth 认证后可用） | 可用时自动包含 |
| `user.account_uuid` | string | 账号 UUID（认证后可用） | `OTEL_METRICS_INCLUDE_ACCOUNT_UUID`（默认 `true`） |
| `user.id` | string | 匿名设备/安装标识，每次安装生成 | 始终包含 |
| `user.email` | string | 用户邮箱（OAuth 认证后可用） | 可用时自动包含 |
| `terminal.type` | string | 终端类型 | 检测到时自动包含 |

### `terminal.type` 已知值

| Value | Description |
|-------|-------------|
| `iTerm.app` | iTerm2 |
| `vscode` | VS Code |
| `cursor` | Cursor |
| `tmux` | tmux |

### Resource 属性

| Attribute | Description | Example |
|-----------|-------------|---------|
| `service.name` | 固定为 `claude-code` | `claude-code` |
| `service.version` | Claude Code 版本 | `1.0.0` |
| `os.type` | 操作系统类型 | `linux`, `darwin`, `windows` |
| `os.version` | 操作系统版本 | `14.0` |
| `host.arch` | 主机架构 | `amd64`, `arm64` |
| `wsl.version` | WSL 版本号（仅 WSL 环境） | `2` |

---

## 二、指标总览

| 指标名称 | 类型 | 单位 | 描述 |
|----------|------|------|------|
| `claude_code.session.count` | Counter | `count` | CLI 会话启动次数 |
| `claude_code.lines_of_code.count` | Counter | `count` | 修改的代码行数 |
| `claude_code.pull_request.count` | Counter | `count` | 创建的 Pull Request 数 |
| `claude_code.commit.count` | Counter | `count` | 创建的 Git Commit 数 |
| `claude_code.cost.usage` | Counter | `USD` | 会话费用（近似值） |
| `claude_code.token.usage` | Counter | `tokens` | Token 使用量 |
| `claude_code.code_edit_tool.decision` | Counter | `count` | 代码编辑工具权限决策次数 |
| `claude_code.active_time.total` | Counter | `s` | 活跃时间（排除空闲） |

---

## 三、指标详细定义

### `claude_code.session.count` — 会话计数器

每次 CLI 会话启动时递增。

**额外属性**：无（仅标准属性）

---

### `claude_code.lines_of_code.count` — 代码行数计数器

代码被添加或删除时递增。

**额外属性**：

| Attribute | Type | Description | Values |
|-----------|------|-------------|--------|
| `type` | string | 修改类型 | `"added"`, `"removed"` |

---

### `claude_code.pull_request.count` — Pull Request 计数器

通过 Claude Code 创建 Pull Request 时递增。

**额外属性**：无（仅标准属性）

---

### `claude_code.commit.count` — Commit 计数器

通过 Claude Code 创建 Git Commit 时递增。

**额外属性**：无（仅标准属性）

---

### `claude_code.cost.usage` — 费用计数器

每次 API 请求完成后递增，记录近似费用。

**额外属性**：

| Attribute | Type | Description | Example |
|-----------|------|-------------|---------|
| `model` | string | 模型标识 | `"claude-sonnet-4-6"` |

> ⚠️ 费用为近似值。官方计费数据请参考 API 提供商（Claude Console、AWS Bedrock 或 Google Cloud Vertex）。

---

### `claude_code.token.usage` — Token 计数器

每次 API 请求完成后递增。

**额外属性**：

| Attribute | Type | Description | Values |
|-----------|------|-------------|--------|
| `type` | string | Token 类型 | `"input"`, `"output"`, `"cacheRead"`, `"cacheCreation"` |
| `model` | string | 模型标识 | `"claude-sonnet-4-6"` |

---

### `claude_code.code_edit_tool.decision` — 代码编辑工具决策计数器

用户接受或拒绝 Edit、Write 或 NotebookEdit 工具使用时递增。

**额外属性**：

| Attribute | Type | Description | Values |
|-----------|------|-------------|--------|
| `tool_name` | string | 工具名称 | `"Edit"`, `"Write"`, `"NotebookEdit"` |
| `decision` | string | 用户决策 | `"accept"`, `"reject"` |
| `source` | string | 决策来源 | `"config"`, `"hook"`, `"user_permanent"`, `"user_temporary"`, `"user_abort"`, `"user_reject"` |
| `language` | string | 编辑文件的编程语言 | `"TypeScript"`, `"Python"`, `"JavaScript"`, `"Markdown"`, `"unknown"` |

---

### `claude_code.active_time.total` — 活跃时间计数器

追踪实际使用 Claude Code 的活跃时间，排除空闲时间。在用户交互（打字、阅读响应）和 CLI 处理（工具执行、AI 响应生成）期间递增。

**额外属性**：

| Attribute | Type | Description | Values |
|-----------|------|-------------|--------|
| `type` | string | 活跃类型 | `"user"`（键盘交互）, `"cli"`（工具执行和 AI 响应） |

---

## 四、配置项

### 基础配置

| 环境变量 | 描述 | 默认值 |
|---------|------|--------|
| `CLAUDE_CODE_ENABLE_TELEMETRY` | 启用遥测采集（必需） | `0`（关闭） |
| `OTEL_METRICS_EXPORTER` | Metrics 导出器（逗号分隔） | — |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | OTLP 协议 | `grpc` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP 采集端点 | `http://localhost:4317` |
| `OTEL_EXPORTER_OTLP_HEADERS` | 认证 Headers | — |
| `OTEL_METRIC_EXPORT_INTERVAL` | Metrics 导出间隔（ms） | `60000` |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | 指标时间性偏好 | `delta` |

### 支持的导出器

| Value | Description |
|-------|-------------|
| `otlp` | OTLP 协议导出 |
| `prometheus` | Prometheus 格式 |
| `console` | 控制台输出（调试用） |

### 基数控制

| 环境变量 | 描述 | 默认值 |
|---------|------|--------|
| `OTEL_METRICS_INCLUDE_SESSION_ID` | 包含 `session.id` | `true` |
| `OTEL_METRICS_INCLUDE_VERSION` | 包含 `app.version` | `false` |
| `OTEL_METRICS_INCLUDE_ACCOUNT_UUID` | 包含 `user.account_uuid` | `true` |

---

## 五、分析场景

| 指标 | 分析维度 |
|------|---------|
| `claude_code.token.usage` | 按 `type`（input/output）、`user.account_uuid`、`model` 分析 |
| `claude_code.session.count` | 追踪采纳率和活跃度 |
| `claude_code.lines_of_code.count` | 按 `type`（added/removed）衡量生产力 |
| `claude_code.commit.count` + `claude_code.pull_request.count` | 衡量对开发工作流的影响 |
| `claude_code.cost.usage` | 按 `model`、`organization.id`、`user.account_uuid` 追踪费用趋势 |
| `claude_code.active_time.total` | 按 `type`（user/cli）分析时间分布 |

### 告警建议

- 费用突增（按 `organization.id` 分组）
- Token 消耗异常
- 特定用户的高会话量
- 所有指标可按 `user.account_uuid`、`organization.id`、`session.id`、`model`、`app.version` 分段
