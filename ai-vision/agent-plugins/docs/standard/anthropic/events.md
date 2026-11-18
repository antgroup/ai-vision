# Claude Code 可观测性规范 — Events

> **Source**: [Claude Code Monitoring - Usage](https://code.claude.com/docs/en/monitoring-usage)
> **协议**: OpenTelemetry Logs/Events
> **service.name**: `claude-code`

## 概述

Claude Code 通过 OpenTelemetry Logs/Events 协议导出事件数据。所有事件使用 `claude_code.*` 前缀。需设置 `CLAUDE_CODE_ENABLE_TELEMETRY=1` 和 `OTEL_LOGS_EXPORTER` 启用事件采集。

---

## 一、标准属性（所有事件共享）

| Attribute | Type | Description | 控制方式 |
|-----------|------|-------------|---------|
| `session.id` | string | 会话唯一标识 | 始终包含 |
| `app.version` | string | Claude Code 版本号 | 始终包含 |
| `organization.id` | string | 组织 UUID（OAuth 认证后可用） | 可用时自动包含 |
| `user.account_uuid` | string | 账号 UUID（认证后可用） | 可用时自动包含 |
| `user.id` | string | 匿名设备/安装标识 | 始终包含 |
| `user.email` | string | 用户邮箱（OAuth 认证后可用） | 可用时自动包含 |
| `terminal.type` | string | 终端类型 | 检测到时自动包含 |

### 事件通用属性

| Attribute | Type | Description |
|-----------|------|-------------|
| `event.name` | string | 事件名称 |
| `event.timestamp` | string | ISO 8601 时间戳 |
| `event.sequence` | int | 会话内单调递增序号，用于事件排序 |

### 事件关联属性

| Attribute | Type | Description |
|-----------|------|-------------|
| `prompt.id` | string | UUID v4，关联同一个用户 prompt 产生的所有事件 |

> ⚠️ `prompt.id` 仅用于事件级别的分析和审计追踪，**不应** 包含在 Metrics 中（每个 prompt 生成唯一 ID，会导致指标基数无限增长）。

通过特定 `prompt.id` 过滤事件，可以获取该 prompt 触发的 `user_prompt` 事件、所有 `api_request` 事件和所有 `tool_result` 事件。

---

## 二、事件总览

| 事件名称 | 触发时机 | 描述 |
|----------|---------|------|
| `claude_code.user_prompt` | 用户提交 prompt | 记录用户输入 |
| `claude_code.tool_result` | 工具执行完成 | 记录工具执行结果 |
| `claude_code.api_request` | API 请求完成 | 记录 Claude API 调用详情 |
| `claude_code.api_error` | API 请求失败 | 记录 Claude API 调用错误 |
| `claude_code.tool_decision` | 工具权限决策 | 记录工具使用的授权决策 |

---

## 三、事件详细定义

### `claude_code.user_prompt` — 用户输入事件

用户提交 prompt 时记录。

| Attribute | Type | Requirement | Description | Example |
|-----------|------|-------------|-------------|---------|
| `event.name` | string | Required | 固定值 `"user_prompt"` | `"user_prompt"` |
| `event.timestamp` | string | Required | ISO 8601 时间戳 | `"2026-03-21T16:00:00Z"` |
| `event.sequence` | int | Required | 会话内递增序号 | `1` |
| `prompt.id` | string | Required | 关联 prompt 的 UUID | `"550e8400-..."` |
| `prompt_length` | int | Required | prompt 字符长度 | `256` |
| `prompt` | string | Opt-In | prompt 内容（默认不采集） | `"帮我写一个排序算法"` |

> 需设置 `OTEL_LOG_USER_PROMPTS=1` 才会记录 `prompt` 内容。

---

### `claude_code.tool_result` — 工具执行结果事件

工具执行完成时记录。

| Attribute | Type | Requirement | Description | Example |
|-----------|------|-------------|-------------|---------|
| `event.name` | string | Required | 固定值 `"tool_result"` | `"tool_result"` |
| `event.timestamp` | string | Required | ISO 8601 时间戳 | `"2026-03-21T16:00:02Z"` |
| `event.sequence` | int | Required | 会话内递增序号 | `3` |
| `prompt.id` | string | Required | 关联 prompt 的 UUID | `"550e8400-..."` |
| `tool_name` | string | Required | 工具名称 | `"Read"`, `"Edit"`, `"Bash"` |
| `success` | string | Required | 是否成功 | `"true"`, `"false"` |
| `duration_ms` | int | Required | 执行耗时（毫秒） | `120` |
| `error` | string | Conditionally Required | 错误信息（失败时） | `"file not found"` |
| `decision_type` | string | Required | 决策类型 | `"accept"`, `"reject"` |
| `decision_source` | string | Required | 决策来源 | `"config"`, `"hook"`, `"user_permanent"`, `"user_temporary"`, `"user_abort"`, `"user_reject"` |
| `tool_result_size_bytes` | int | Recommended | 工具返回结果大小（字节） | `4096` |
| `mcp_server_scope` | string | Recommended | MCP 服务范围标识（MCP 工具时） | `"github-mcp"` |
| `tool_parameters` | string | Recommended | 工具参数 JSON 字符串 | — |

#### `tool_parameters` 详细说明

`tool_parameters` 是一个 JSON 字符串，包含工具特定的参数：

- **Bash 工具**：包含 `bash_command`、`full_command`、`timeout`、`description`、`dangerouslyDisableSandbox`、`git_commit_id`（`git commit` 成功时的 commit SHA）
- **MCP 工具**（需 `OTEL_LOG_TOOL_DETAILS=1`）：包含 `mcp_server_name`、`mcp_tool_name`
- **Skill 工具**（需 `OTEL_LOG_TOOL_DETAILS=1`）：包含 `skill_name`

> ⚠️ `tool_parameters` 可能包含敏感值（如 bash 命令中的密钥），建议在遥测后端配置过滤或脱敏规则。

---

### `claude_code.api_request` — API 请求事件

每次 Claude API 请求完成时记录。

| Attribute | Type | Requirement | Description | Example |
|-----------|------|-------------|-------------|---------|
| `event.name` | string | Required | 固定值 `"api_request"` | `"api_request"` |
| `event.timestamp` | string | Required | ISO 8601 时间戳 | `"2026-03-21T16:00:01Z"` |
| `event.sequence` | int | Required | 会话内递增序号 | `2` |
| `prompt.id` | string | Required | 关联 prompt 的 UUID | `"550e8400-..."` |
| `model` | string | Required | 使用的模型标识 | `"claude-sonnet-4-6"` |
| `cost_usd` | float | Required | 本次请求的近似费用（USD） | `0.045` |
| `duration_ms` | int | Required | 请求耗时（毫秒） | `2350` |
| `input_tokens` | int | Required | 输入 token 数 | `1500` |
| `output_tokens` | int | Required | 输出 token 数 | `800` |
| `cache_read_tokens` | int | Recommended | 缓存读取 token 数 | `500` |
| `cache_creation_tokens` | int | Recommended | 缓存创建 token 数 | `200` |
| `speed` | string | Required | 速度模式 | `"fast"`, `"normal"` |

---

### `claude_code.api_error` — API 错误事件

Claude API 请求失败时记录。

| Attribute | Type | Requirement | Description | Example |
|-----------|------|-------------|-------------|---------|
| `event.name` | string | Required | 固定值 `"api_error"` | `"api_error"` |
| `event.timestamp` | string | Required | ISO 8601 时间戳 | `"2026-03-21T16:00:01Z"` |
| `event.sequence` | int | Required | 会话内递增序号 | `4` |
| `prompt.id` | string | Required | 关联 prompt 的 UUID | `"550e8400-..."` |
| `model` | string | Required | 使用的模型标识 | `"claude-sonnet-4-6"` |
| `error` | string | Required | 错误信息 | `"rate_limit_exceeded"` |
| `status_code` | string | Required | HTTP 状态码，非 HTTP 错误时为 `"undefined"` | `"429"` |
| `duration_ms` | int | Required | 请求耗时（毫秒） | `150` |
| `attempt` | int | Recommended | 重试次数（第几次尝试） | `2` |
| `speed` | string | Required | 速度模式 | `"fast"`, `"normal"` |

---

### `claude_code.tool_decision` — 工具决策事件

工具使用权限决策时记录（接受/拒绝）。

| Attribute | Type | Requirement | Description | Example |
|-----------|------|-------------|-------------|---------|
| `event.name` | string | Required | 固定值 `"tool_decision"` | `"tool_decision"` |
| `event.timestamp` | string | Required | ISO 8601 时间戳 | `"2026-03-21T16:00:02Z"` |
| `event.sequence` | int | Required | 会话内递增序号 | `5` |
| `prompt.id` | string | Required | 关联 prompt 的 UUID | `"550e8400-..."` |
| `tool_name` | string | Required | 工具名称 | `"Read"`, `"Edit"`, `"Write"`, `"NotebookEdit"` |
| `decision` | string | Required | 决策结果 | `"accept"`, `"reject"` |
| `source` | string | Required | 决策来源 | `"config"`, `"hook"`, `"user_permanent"`, `"user_temporary"`, `"user_abort"`, `"user_reject"` |

### `source` 决策来源枚举值

| Value | Description |
|-------|-------------|
| `config` | 配置文件自动决策 |
| `hook` | Hook 脚本决策 |
| `user_permanent` | 用户永久授权 |
| `user_temporary` | 用户临时授权 |
| `user_abort` | 用户中止操作 |
| `user_reject` | 用户拒绝操作 |

---

## 四、事件链路还原示例

通过 `prompt.id` 关联同一个用户 prompt 产生的所有事件：

```
prompt.id = "550e8400-e29b-41d4-a716-446655440000"

  [seq=1] claude_code.user_prompt   → prompt_length=256
  [seq=2] claude_code.api_request   → claude-sonnet-4-6, 1500 input, 800 output, $0.045
  [seq=3] claude_code.tool_decision → Edit: accept (source=config)
  [seq=4] claude_code.tool_result   → Edit: success, 120ms
  [seq=5] claude_code.api_request   → claude-sonnet-4-6, 2000 input, 500 output, $0.038
  [seq=6] claude_code.tool_decision → Bash: accept (source=user_temporary)
  [seq=7] claude_code.tool_result   → Bash: success, 3500ms
```

---

## 五、配置项

| 环境变量 | 描述 | 默认值 |
|---------|------|--------|
| `CLAUDE_CODE_ENABLE_TELEMETRY` | 启用遥测采集（必需） | `0`（关闭） |
| `OTEL_LOGS_EXPORTER` | Events/Logs 导出器（逗号分隔） | — |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | OTLP 协议 | `grpc` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP 采集端点 | `http://localhost:4317` |
| `OTEL_EXPORTER_OTLP_HEADERS` | 认证 Headers | — |
| `OTEL_LOGS_EXPORT_INTERVAL` | Logs 导出间隔（ms） | `5000` |
| `OTEL_LOG_USER_PROMPTS` | 记录用户 prompt 内容 | `0`（关闭） |
| `OTEL_LOG_TOOL_DETAILS` | 记录 MCP server/tool 名称和 skill 名称 | `0`（关闭） |

### 支持的导出器

| Value | Description |
|-------|-------------|
| `otlp` | OTLP 协议导出 |
| `console` | 控制台输出（调试用） |

---

## 六、安全与隐私

- **遥测默认关闭**，需显式设置 `CLAUDE_CODE_ENABLE_TELEMETRY=1` 开启
- **用户 prompt 内容默认不采集**，仅记录长度；需设置 `OTEL_LOG_USER_PROMPTS=1` 开启
- **MCP server/tool 名称和 skill 名称默认不记录**（可能暴露用户配置），需设置 `OTEL_LOG_TOOL_DETAILS=1` 开启
- **原始文件内容和代码片段不包含在遥测数据中**
- `tool_parameters` 字段中的 bash 命令和文件路径可能包含敏感值，建议在遥测后端配置过滤或脱敏规则
- OAuth 认证用户的 `user.email` 会包含在遥测属性中，如有隐私顾虑，请在后端配置过滤

---

## 七、事件分析场景

| 分析维度 | 方法 |
|---------|------|
| **工具使用模式** | 通过 `tool_result` 事件分析最常用工具、成功率、平均耗时 |
| **性能监控** | 通过 `api_request` 的 `duration_ms` 和 `tool_result` 的 `duration_ms` 定位瓶颈 |
| **错误分析** | 通过 `api_error` 的 `status_code` 和 `error` 分析错误模式 |
| **推理链路还原** | 通过 `prompt.id` 关联 `user_prompt` → `api_request` → `tool_result`，还原完整处理过程 |
| **决策审计** | 通过 `tool_decision` 分析工具授权模式和来源分布 |
