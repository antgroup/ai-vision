## AI Agent 可观测埋点规范 v1.0

> 本规范定义了 AI Agent 应用的通用可观测性埋点标准，基于 OpenTelemetry 协议，覆盖 Metrics（指标）和 Events（事件）两大类信号。适用于任意类型的 AI Agent 系统，不绑定特定厂商或框架。

---

### 一、设计原则

- **通用性**：不绑定特定 Agent 框架（如 LangChain、AutoGPT、Claude Code 等），适用于所有 AI Agent 场景
- **可扩展性**：通过 `agent_name` 属性区分不同 Agent 实例，支持多 Agent 系统
- **OpenTelemetry 原生**：遵循 OTel 语义约定，Metrics 使用标准 metrics 协议，Events 使用 logs/events 协议
- **隐私优先**：用户输入内容默认不采集，需显式开启

---

### 二、标准属性（Standard Attributes）

所有 Metrics 和 Events 共享以下标准属性：

| 属性 | 类型 | 必选 | 描述 |
|---|---|---|---|
| `agent_name` | string | ✅ | Agent 名称标识，如 `"code-assistant"`, `"customer-service-bot"` |
| `session.id` | string | ✅ | 会话唯一标识（UUID） |
| `user.id` | string | ✅ | 用户标识（匿名设备/安装 ID 或业务用户 ID） |
| `user.account_uuid` | string | 可选 | 用户账号 UUID（认证后可用） |
| `user.email` | string | 可选 | 用户邮箱（OAuth 认证后可用） |
| `organization.id` | string | 可选 | 组织/租户标识 |
| `app.version` | string | 可选 | Agent 应用版本号 |
| `deployment.environment` | string | 可选 | 部署环境，如 `"production"`, `"staging"`, `"development"` |

#### Resource 属性

所有遥测数据携带以下 OTel Resource 属性：

| 属性 | 描述 | 示例 |
|---|---|---|
| `service.name` | 服务名称 | `"my-ai-agent"` |
| `service.version` | 服务版本 | `"1.2.0"` |
| `os.type` | 操作系统类型 | `"linux"`, `"darwin"`, `"windows"` |
| `os.version` | 操作系统版本 | `"14.0"` |
| `host.arch` | 主机架构 | `"amd64"`, `"arm64"` |

---

### 三、Metrics（指标）

所有指标使用 `agent.` 前缀，采用 Counter 或 Histogram 类型。

#### 3.1 指标总览

| 指标名称 | 类型 | 单位 | 描述 |
|---|---|---|---|
| `agent.session.count` | Counter | `count` | Agent 会话启动次数 |
| `agent.cost.usage` | Counter | `USD` | 会话累计费用（近似值） |
| `agent.token.usage` | Counter | `tokens` | Token 使用量 |
| `agent.tool_call.count` | Counter | `count` | 工具调用次数 |
| `agent.llm_request.count` | Counter | `count` | LLM API 请求次数 |
| `agent.llm_request.duration` | Histogram | `ms` | LLM API 请求耗时分布 |
| `agent.active_time.total` | Counter | `s` | 活跃时间（排除空闲） |
| `agent.error.count` | Counter | `count` | 错误发生次数 |

#### 3.2 指标详细定义

##### `agent.session.count` — 会话计数器

每次 Agent 会话启动时递增。

**额外属性**：无（仅标准属性）

---

##### `agent.cost.usage` — 费用计数器

每次 LLM API 请求完成后递增，记录近似费用。

**额外属性**：

| 属性 | 类型 | 描述 |
|---|---|---|
| `model` | string | 模型标识，如 `"gpt-4o"`, `"claude-sonnet-4-6"`, `"qwen-max"` |

---

##### `agent.token.usage` — Token 计数器

每次 LLM API 请求完成后递增。

**额外属性**：

| 属性 | 类型 | 描述 |
|---|---|---|
| `type` | string | Token 类型：`"input"`, `"output"`, `"cache_read"`, `"cache_creation"` |
| `model` | string | 模型标识 |

---

##### `agent.tool_call.count` — 工具调用计数器

每次工具调用完成后递增。

**额外属性**：

| 属性 | 类型 | 描述 |
|---|---|---|
| `tool_name` | string | 工具名称，如 `"web_search"`, `"code_edit"`, `"file_read"` |
| `success` | string | 是否成功：`"true"`, `"false"` |

---

##### `agent.llm_request.count` — LLM 请求计数器

每次 LLM API 请求完成后递增。

**额外属性**：

| 属性 | 类型 | 描述 |
|---|---|---|
| `model` | string | 模型标识 |
| `status` | string | 请求状态：`"success"`, `"error"` |

---

##### `agent.llm_request.duration` — LLM 请求耗时

记录每次 LLM API 请求的耗时分布。

**额外属性**：

| 属性 | 类型 | 描述 |
|---|---|---|
| `model` | string | 模型标识 |

---

##### `agent.active_time.total` — 活跃时间计数器

追踪 Agent 实际活跃时间，排除空闲等待。

**额外属性**：

| 属性 | 类型 | 描述 |
|---|---|---|
| `type` | string | 活跃类型：`"user"` 用户交互, `"agent"` Agent 处理 |

---

##### `agent.error.count` — 错误计数器

每次发生错误时递增。

**额外属性**：

| 属性 | 类型 | 描述 |
|---|---|---|
| `error_type` | string | 错误类型：`"llm_api"`, `"tool_execution"`, `"timeout"`, `"rate_limit"`, `"auth"` |

---

### 四、Events（事件）

所有事件使用 `agent.` 前缀，通过 OTel Logs/Events 协议导出。

#### 4.1 事件关联属性

当用户提交一个 prompt 后，Agent 可能发起多次 LLM 调用和工具执行。通过 `prompt.id` 将所有相关事件关联到触发它们的那个 prompt。

| 属性 | 类型 | 描述 |
|---|---|---|
| `prompt.id` | string | UUID v4，关联同一个用户 prompt 产生的所有事件 |

> ⚠️ `prompt.id` 仅用于事件级别的分析和审计追踪，**不应**包含在 Metrics 中（因为每个 prompt 生成唯一 ID，会导致指标基数无限增长）。

#### 4.2 事件总览

| 事件名称 | 触发时机 | 描述 |
|---|---|---|
| `agent.user_prompt` | 用户提交 prompt | 记录用户输入 |
| `agent.tool_result` | 工具执行完成 | 记录工具执行结果 |
| `agent.llm_request` | LLM API 请求完成 | 记录 LLM 调用详情 |
| `agent.llm_error` | LLM API 请求失败 | 记录 LLM 调用错误 |
| `agent.tool_decision` | 工具权限决策 | 记录工具使用的授权决策 |
| `agent.agent_step` | Agent 完成一个推理步骤 | 记录 Agent 的推理/规划步骤 |

#### 4.3 事件详细定义

##### `agent.user_prompt` — 用户输入事件

用户提交 prompt 时记录。

**属性**：

| 属性 | 类型 | 必选 | 描述 |
|---|---|---|---|
| `event.name` | string | ✅ | 固定值 `"user_prompt"` |
| `event.timestamp` | string | ✅ | ISO 8601 时间戳 |
| `event.sequence` | int | ✅ | 会话内单调递增序号，用于事件排序 |
| `prompt.id` | string | ✅ | 关联 prompt 的 UUID |
| `prompt_length` | int | ✅ | prompt 字符长度 |
| `prompt` | string | 可选 | prompt 内容（默认不采集，需显式开启） |
| `input_type` | string | 可选 | 输入类型：`"text"`, `"voice"`, `"image"`, `"multimodal"` |

---

##### `agent.tool_result` — 工具执行结果事件

工具执行完成时记录。

**属性**：

| 属性 | 类型 | 必选 | 描述 |
|---|---|---|---|
| `event.name` | string | ✅ | 固定值 `"tool_result"` |
| `event.timestamp` | string | ✅ | ISO 8601 时间戳 |
| `event.sequence` | int | ✅ | 会话内单调递增序号 |
| `prompt.id` | string | ✅ | 关联 prompt 的 UUID |
| `tool_name` | string | ✅ | 工具名称 |
| `success` | string | ✅ | `"true"` 或 `"false"` |
| `duration_ms` | int | ✅ | 执行耗时（毫秒） |
| `error` | string | 可选 | 错误信息（失败时） |
| `tool_result_size_bytes` | int | 可选 | 工具返回结果大小（字节） |
| `tool_parameters` | string | 可选 | 工具参数 JSON 字符串（需显式开启） |
| `tool_source` | string | 可选 | 工具来源：`"builtin"`, `"mcp"`, `"plugin"`, `"custom"` |
| `mcp_server_name` | string | 可选 | MCP 服务名称（MCP 工具时，需显式开启） |

---

##### `agent.llm_request` — LLM 请求事件

每次 LLM API 请求完成时记录。

**属性**：

| 属性 | 类型 | 必选 | 描述 |
|---|---|---|---|
| `event.name` | string | ✅ | 固定值 `"llm_request"` |
| `event.timestamp` | string | ✅ | ISO 8601 时间戳 |
| `event.sequence` | int | ✅ | 会话内单调递增序号 |
| `prompt.id` | string | ✅ | 关联 prompt 的 UUID |
| `model` | string | ✅ | 使用的模型标识 |
| `cost_usd` | float | ✅ | 本次请求的近似费用（USD） |
| `duration_ms` | int | ✅ | 请求耗时（毫秒） |
| `input_tokens` | int | ✅ | 输入 token 数 |
| `output_tokens` | int | ✅ | 输出 token 数 |
| `cache_read_tokens` | int | 可选 | 缓存读取 token 数 |
| `cache_creation_tokens` | int | 可选 | 缓存创建 token 数 |
| `provider` | string | 可选 | LLM 提供商：`"openai"`, `"anthropic"`, `"google"`, `"alibaba"`, `"bedrock"`, `"azure"` |
| `request_type` | string | 可选 | 请求类型：`"chat"`, `"completion"`, `"embedding"` |

---

##### `agent.llm_error` — LLM 错误事件

LLM API 请求失败时记录。

**属性**：

| 属性 | 类型 | 必选 | 描述 |
|---|---|---|---|
| `event.name` | string | ✅ | 固定值 `"llm_error"` |
| `event.timestamp` | string | ✅ | ISO 8601 时间戳 |
| `event.sequence` | int | ✅ | 会话内单调递增序号 |
| `prompt.id` | string | ✅ | 关联 prompt 的 UUID |
| `model` | string | ✅ | 使用的模型标识 |
| `error` | string | ✅ | 错误信息 |
| `status_code` | string | ✅ | HTTP 状态码，非 HTTP 错误时为 `"undefined"` |
| `duration_ms` | int | ✅ | 请求耗时（毫秒） |
| `attempt` | int | 可选 | 重试次数（第几次尝试） |
| `provider` | string | 可选 | LLM 提供商 |

---

##### `agent.tool_decision` — 工具决策事件

工具使用权限决策时记录（接受/拒绝）。

**属性**：

| 属性 | 类型 | 必选 | 描述 |
|---|---|---|---|
| `event.name` | string | ✅ | 固定值 `"tool_decision"` |
| `event.timestamp` | string | ✅ | ISO 8601 时间戳 |
| `event.sequence` | int | ✅ | 会话内单调递增序号 |
| `prompt.id` | string | ✅ | 关联 prompt 的 UUID |
| `tool_name` | string | ✅ | 工具名称 |
| `decision` | string | ✅ | 决策结果：`"accept"`, `"reject"` |
| `source` | string | ✅ | 决策来源：`"config"`, `"hook"`, `"user"`, `"policy"`, `"auto"` |

---

##### `agent.agent_step` — Agent 推理步骤事件

Agent 完成一个推理/规划步骤时记录。此事件为本规范新增，用于追踪 Agent 的多步推理过程。

**属性**：

| 属性 | 类型 | 必选 | 描述 |
|---|---|---|---|
| `event.name` | string | ✅ | 固定值 `"agent_step"` |
| `event.timestamp` | string | ✅ | ISO 8601 时间戳 |
| `event.sequence` | int | ✅ | 会话内单调递增序号 |
| `prompt.id` | string | ✅ | 关联 prompt 的 UUID |
| `step_type` | string | ✅ | 步骤类型：`"planning"`, `"reasoning"`, `"action"`, `"observation"`, `"reflection"` |
| `step_index` | int | ✅ | 当前步骤序号（从 0 开始） |
| `duration_ms` | int | ✅ | 步骤耗时（毫秒） |
| `model` | string | 可选 | 本步骤使用的模型 |

---

### 五、配置项

以下环境变量控制遥测行为：

| 环境变量 | 描述 | 默认值 |
|---|---|---|
| `AGENT_ENABLE_TELEMETRY` | 启用遥测采集（必需） | `0`（关闭） |
| `OTEL_METRICS_EXPORTER` | Metrics 导出器 | `otlp` |
| `OTEL_LOGS_EXPORTER` | Events/Logs 导出器 | `otlp` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP 采集端点 | `http://localhost:4317` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | OTLP 协议 | `grpc` |
| `OTEL_EXPORTER_OTLP_HEADERS` | 认证 Headers | — |
| `OTEL_METRIC_EXPORT_INTERVAL` | Metrics 导出间隔（ms） | `60000` |
| `OTEL_LOGS_EXPORT_INTERVAL` | Logs 导出间隔（ms） | `5000` |
| `AGENT_LOG_USER_PROMPTS` | 是否记录用户 prompt 内容 | `0`（关闭） |
| `AGENT_LOG_TOOL_DETAILS` | 是否记录工具参数详情 | `0`（关闭） |

#### 基数控制

| 环境变量 | 描述 | 默认值 |
|---|---|---|
| `OTEL_METRICS_INCLUDE_SESSION_ID` | Metrics 中包含 session.id | `true` |
| `OTEL_METRICS_INCLUDE_VERSION` | Metrics 中包含 app.version | `false` |
| `OTEL_METRICS_INCLUDE_ACCOUNT_UUID` | Metrics 中包含 user.account_uuid | `true` |

---

### 六、安全与隐私

- **遥测默认关闭**，需显式设置 `AGENT_ENABLE_TELEMETRY=1` 开启
- **用户 prompt 内容默认不采集**，仅记录长度；需设置 `AGENT_LOG_USER_PROMPTS=1` 开启
- **工具参数详情默认不采集**，需设置 `AGENT_LOG_TOOL_DETAILS=1` 开启
- **原始文件内容和代码片段不包含在遥测数据中**
- 工具执行事件中的 `tool_parameters` 可能包含敏感信息，建议在遥测后端配置过滤或脱敏规则
- 认证用户的 `user.email` 会包含在遥测属性中，如有隐私顾虑，请在后端过滤

---

### 七、分析场景参考

#### 使用量监控

| 指标 | 分析维度 |
|---|---|
| `agent.token.usage` | 按 `type`（input/output）、`agent_name`、`model`、`user.id` 分析 |
| `agent.session.count` | 追踪各 Agent 的采纳率和活跃度 |
| `agent.tool_call.count` | 分析工具使用频率和成功率 |

#### 成本监控

- 通过 `agent.cost.usage` 按 `agent_name`、`model`、`organization.id` 追踪费用趋势
- 识别高消耗会话进行优化

#### 告警建议

- 费用突增（按 `agent_name` + `organization.id` 分组）
- Token 消耗异常
- 工具执行失败率升高
- LLM API 错误率 / 延迟升高

#### 事件分析

- **工具使用模式**：通过 `agent.tool_result` 分析最常用工具、成功率、平均耗时
- **Agent 推理链路**：通过 `prompt.id` 关联 `agent.agent_step` → `agent.llm_request` → `agent.tool_result`，还原完整推理过程
- **性能瓶颈**：通过 `agent.llm_request` 的 `duration_ms` 和 `agent.tool_result` 的 `duration_ms` 定位慢环节

---

### 八、与 Claude Code 原始事件的映射关系

本规范基于 Claude Code 的 monitoring events 设计，以下为映射对照：

| Claude Code 原始事件/指标 | 本规范对应 | 变更说明 |
|---|---|---|
| `claude_code.user_prompt` | `agent.user_prompt` | 前缀改为 `agent`，新增 `input_type` |
| `claude_code.tool_result` | `agent.tool_result` | 前缀改为 `agent`，新增 `tool_source` |
| `claude_code.api_request` | `agent.llm_request` | 重命名为更通用的 `llm_request`，新增 `provider`, `request_type` |
| `claude_code.api_error` | `agent.llm_error` | 重命名为 `llm_error`，新增 `provider` |
| `claude_code.tool_decision` | `agent.tool_decision` | 前缀改为 `agent`，`source` 值通用化 |
| `claude_code.session.count` | `agent.session.count` | 前缀改为 `agent` |
| `claude_code.cost.usage` | `agent.cost.usage` | 前缀改为 `agent` |
| `claude_code.token.usage` | `agent.token.usage` | 前缀改为 `agent`，`cacheRead` → `cache_read` |
| `claude_code.active_time.total` | `agent.active_time.total` | 前缀改为 `agent`，`cli` → `agent` |
| `claude_code.lines_of_code.count` | — | 移除（代码行数为 coding agent 特有，不通用） |
| `claude_code.pull_request.count` | — | 移除（PR 为 coding agent 特有，不通用） |
| `claude_code.commit.count` | — | 移除（commit 为 coding agent 特有，不通用） |
| `claude_code.code_edit_tool.decision` | — | 合并入 `agent.tool_decision`（通用化） |
| — | `agent.agent_step` | **新增**：追踪 Agent 多步推理过程 |
| — | `agent.tool_call.count` | **新增**：工具调用计数指标 |
| — | `agent.llm_request.count` | **新增**：LLM 请求计数指标 |
| — | `agent.llm_request.duration` | **新增**：LLM 请求耗时 Histogram |
| — | `agent.error.count` | **新增**：通用错误计数指标 |
| — | 标准属性 `agent_name` | **新增**：区分不同 Agent 实例 |
| — | 标准属性 `deployment.environment` | **新增**：区分部署环境 |
