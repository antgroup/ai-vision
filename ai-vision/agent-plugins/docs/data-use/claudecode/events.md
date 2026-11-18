# Claude Code 事件字段

## 范围与证据

本页同时记录两个彼此独立的数据平面，不能按名称相互推导：

- **官方原生**：Claude Code 自身在启用 OpenTelemetry logs 后产生的 `claude_code.*` 事件。依据为 [Claude Code Monitoring](https://code.claude.com/docs/en/monitoring-usage#available-metrics-and-events)，证据日期为 **2026-07-27**。
- **当前上报**：本仓库 `cchooks` 产生的自定义 `dynamic_stop` / `dynamic.stop` LogRecord。依据为生产 emitter call site。

状态：`官方原生` 表示上游能力；`当前上报` 表示本仓库 emitter 有调用证据；`条件上报` 表示还需版本、配置或运行分支；`待补充` 表示当前证据不足。Metrics 不在本文范围。

## 官方原生公共字段

启用条件是 `CLAUDE_CODE_ENABLE_TELEMETRY=1` 且 `OTEL_LOGS_EXPORTER` 已配置。下列字段来自官方页面，不表示 `cchooks` 会生成这些字段。

| 字段 | 类型 | 状态/条件 | 含义 | 隐私 |
|---|---|---|---|---|
| `session.id` | string | 官方原生；默认包含 | 会话标识 | 可关联整段会话 |
| `app.version` | string | `OTEL_METRICS_INCLUDE_VERSION`，默认 false | Claude Code 版本 | 低风险 |
| `app.entrypoint` | string | `OTEL_METRICS_INCLUDE_ENTRYPOINT`，默认 false | `cli`、SDK、IDE 等入口 | 低风险 |
| `organization.id` | string | 已认证且可用 | 组织 UUID | 组织身份 |
| `user.account_uuid` / `user.account_id` | string | `OTEL_METRICS_INCLUDE_ACCOUNT_UUID`，默认 true | 账户标识 | 用户身份 |
| `user.id` | string | 总是包含；gateway 会改为 IdP subject | 匿名安装标识或网关主体 | 可关联用户 |
| `user.email` | string | OAuth 已认证时 | 用户邮箱 | 敏感，建议后端过滤 |
| `terminal.type` | string | 检测到时 | 终端/宿主类型 | 环境指纹 |
| `prompt.id` | string | 事件专用 | 同一用户 prompt 触发链的 UUID | 高基数关联键 |
| `workspace.host_paths` | string[] | Desktop workspace 可用时 | 宿主工作区目录 | 路径敏感 |
| `workflow.run_id` / `workflow.name` | string | Workflow run；v2.1.202+ | 工作流关联 | 用户名称默认归一为 `custom` |
| `message.uuid` | string | 指定消息事件；v2.1.214+ | transcript 消息关联 | 内部关联键 |
| `client_request_id` | string | 一方 API；v2.1.214+ | 请求/失败关联 UUID | 高基数关联键 |
| `OTEL_RESOURCE_ATTRIBUTES` 自定义键 | scalar | 显式配置 | 自定义 Resource/Event 标签 | 由部署方负责控制 |

所有官方事件还带 `event.name`、`event.timestamp`、会话内单调递增的 `event.sequence` 和适用的公共字段。

## 官方原生 Resource 与 OTLP 基础字段

Claude Code 官方说明所有 metrics 和 events 都携带以下 OTel Resource 属性。进入 OTel 逻辑 后，字段按 Resource 扁平化规则增加 `resource.attributes.` 前缀：

| Claude Code / OTel Resource 键 | OTel 逻辑 字段 | 条件 |
|---|---|---|
| `service.name` | `resource.attributes.service.name` | 始终存在；终端会话为 `claude-code`，Desktop Code tab 为 `claude-code-desktop` |
| `service.version` | `resource.attributes.service.version` | 始终存在 |
| `os.type` | `resource.attributes.os.type` | 始终存在 |
| `os.version` | `resource.attributes.os.version` | 始终存在 |
| `host.arch` | `resource.attributes.host.arch` | 始终存在 |
| `wsl.version` | `resource.attributes.wsl.version` | 仅 Windows Subsystem for Linux |
| `telemetry.sdk.language` | `resource.attributes.telemetry.sdk.language` | OTel SDK 默认 Resource 元数据 |
| `telemetry.sdk.name` | `resource.attributes.telemetry.sdk.name` | OTel SDK 默认 Resource 元数据 |
| `telemetry.sdk.version` | `resource.attributes.telemetry.sdk.version` | OTel SDK 默认 Resource 元数据 |

前六项是 Claude Code 官方 Monitoring 文档明确列出的 Resource 键；`telemetry.sdk.*` 三项是 OpenTelemetry SDK 规定的默认 Resource 元数据，不是 Claude Code 专属键。

`OTEL_RESOURCE_ATTRIBUTES` 可以增加任意部署自定义 Resource 键；对应 归一化字段为 `resource.attributes.<key>`。由于键集合取决于部署配置，静态字段清单只列官方固定键与 OTel SDK 默认键，不臆测自定义键。Claude Code 还会把这些自定义键复制到事件 attributes；同理，无法在静态清单中穷举。

两张 ClaudeCode 底层事件表的字段清单以同一份官方 schema 为准：


清单中的 `time_unix_nano`、`observed_time_unix_nano`、`body`、`dropped_attributes_count` 是 OTel 扁平化后的 OTel LogRecord 基础字段。`trace_id`、`span_id` 是条件字段：Agent SDK 或 `claude -p` 会话传入 `TRACEPARENT` 时，官方事件 LogRecord 会携带这两个关联字段；交互式会话不会继承环境中的 `TRACEPARENT`。

## 官方原生事件目录

表中“增量字段”是在公共字段之外的事件字段；字段类型按官方页面语义标注。所有字段来源均为上面的官方页面。

| 事件 | 状态/触发 | 增量字段（类型） | 配置、版本与隐私 |
|---|---|---|---|
| `claude_code.user_prompt` | 官方原生；提交 prompt | `prompt_length` int、`prompt` string、`message.uuid` string、`command_name` string、`command_source` string | `prompt` 默认脱敏，`OTEL_LOG_USER_PROMPTS=1` 才记录；命令细节受 `OTEL_LOG_TOOL_DETAILS` 控制 |
| `claude_code.assistant_response` | 官方原生；API 返回文本；v2.1.193+ | `response_length` int、`response` string、`model` string、`request_id` string、`message.uuid` string、`query_source` string | 响应默认脱敏；`OTEL_LOG_ASSISTANT_RESPONSES=1` 才记录，默认 60 KB 上限 |
| `claude_code.tool_result` | 官方原生；工具完成，拒绝不产生 | `tool_name` string、`tool_use_id` string、`success` bool-string、`duration_ms` number、`error_type` string、`error` string、`decision_type` string、`decision_source` string、输入/结果 size、`mcp_server_scope`、`tool_parameters`、`tool_input` | 错误全文、参数和输入需 `OTEL_LOG_TOOL_DETAILS=1`；输入单值 512 字符、整体约 4 KB 上限 |
| `claude_code.api_request` | 官方原生；每次 Claude API 请求 | `model`、`cost_usd`、`cost_usd_micros`、`duration_ms`、input/output/cache token、`request_id`、`client_request_id`、`speed`、`query_source`、`effort` 及 agent/skill/plugin/MCP 归因 | 第三方 provider 可能无请求 ID；归因名称有脱敏规则 |
| `claude_code.api_error` | 官方原生；API 最终失败 | `model`、`error`、`status_code`、`duration_ms`、`attempt`、request IDs、`speed`、`query_source`、`effort` 及归因 | `error` 可能含服务信息；HTTP 之外无 `status_code` |
| `claude_code.api_refusal` | 官方原生；`stop_reason=refusal` | `model`、`request_id`、`query_source`、`speed`、`attempt`、`effort`、`server_fallback_hop`、`has_category`、`has_explanation`、`category` 及归因 | `category` 仅在 `OTEL_LOG_TOOL_DETAILS=1` 且存在类别时；fallback hop 不代表最终用户可见拒绝 |
| `claude_code.api_request_body` | 条件上报；每次请求尝试 | `body` 或 `body_ref`、`body_length`、`body_truncated`、`model`、`query_source` | 仅 `OTEL_LOG_RAW_API_BODIES=1` 或 `file:<dir>`；包含完整上下文，极敏感；thinking 始终脱敏 |
| `claude_code.api_response_body` | 条件上报；成功响应 | `body` 或 `body_ref`、`body_length`、`body_truncated`、`model`、`query_source`、`request_id` | 同上；inline 默认 60 KB 上限，file 模式写本地绝对路径 |
| `claude_code.tool_decision` | 官方原生；权限 accept/reject | `tool_name`、`tool_use_id`、`decision`、`tool_source`、`source`、`tool_parameters` | `tool_source` v2.1.214+；参数通常需 `OTEL_LOG_TOOL_DETAILS=1`；拒绝命令可能敏感 |
| `claude_code.permission_mode_changed` | 官方原生；权限模式变化 | `from_mode`、`to_mode`、`trigger` | 记录安全策略状态；SDK/bridge 变化可能无 `trigger` |
| `claude_code.auth` | 官方原生；`/login` 或 `/logout` 完成 | `action`、`success`、`auth_method`、`error_category`、`status_code` | 不含原始错误消息或凭据 |
| `claude_code.mcp_server_connection` | 官方原生；MCP 连接/断开/失败 | `status`、`transport_type`、`server_scope`、`duration_ms`、`error_code`、`is_plugin`、`plugin_id_hash`、`plugin.name`、`server_name`、`error` | server 名和完整错误需 `OTEL_LOG_TOOL_DETAILS=1`；第三方 plugin 名默认脱敏 |
| `claude_code.internal_error` | 条件上报；未预期内部错误 | `error_name`、`error_code` | 不含 message/stack；Bedrock、Google Agent Platform、Foundry 或 `DISABLE_ERROR_REPORTING` 下不发 |
| `claude_code.plugin_installed` | 官方原生；插件安装完成 | `marketplace.is_official`、`install.trigger`、`plugin.name`、`plugin.version`、`marketplace.name` | 第三方名称/版本/marketplace 默认需 `OTEL_LOG_TOOL_DETAILS=1` |
| `claude_code.plugin_loaded` | 官方原生；session start 每个 enabled plugin | plugin/marketplace/version/scope、`enabled_via`、`plugin_id_hash`、hook/MCP/path counts、`host_owned_mcp`、`safe_mode` | 第三方名称默认脱敏；部分字段有 v2.1.169/172+ gate |
| `claude_code.skill_activated` | 官方原生；Skill tool 或 `/` 命令 | `skill.name`、`invocation_trigger`、`skill.source`、`skill.kind`、plugin/marketplace | 用户及第三方 skill 默认 `custom_skill`，细节需 `OTEL_LOG_TOOL_DETAILS=1` |
| `claude_code.at_mention` | 条件上报；mention 完成解析路径 | `mention_type`、`success` | 权限拒绝、超大文件等早退路径不发；可暴露引用类别但不含内容 |
| `claude_code.api_retries_exhausted` | 官方原生；多次尝试后失败 | `model`、`error`、`status_code`、`total_attempts`、`total_retry_duration_ms`、`speed` | 与最终 `api_error` 同时产生；错误信息可能敏感 |
| `claude_code.hook_registered` | 官方原生；session start 每个 hook | `hook_event`、`hook_type`、`hook_source`、`safe_mode`、`hook_matcher`、plugin 名/hash | matcher 需 `OTEL_LOG_TOOL_DETAILS=1`；plugin 名按来源脱敏 |
| `claude_code.hook_execution_start` | 官方原生；匹配 hooks 开始 | `hook_event`、`hook_name`、`num_hooks`、`managed_only`、`hook_source`、`safe_mode`、`hook_definitions` | definitions 仅 detailed beta tracing 且 `OTEL_LOG_TOOL_DETAILS=1`，可能含配置内容 |
| `claude_code.hook_execution_complete` | 官方原生；匹配 hooks 完成 | start 字段加 success/block/error/cancel counts 与 `total_duration_ms` | definitions 的隐私 gate 同上 |
| `claude_code.hook_plugin_metrics` | 条件上报；官方 marketplace plugin hook | `plugin_id`、`hook_event`、最多 20 个受命名约束的 bool/number 键 | 第三方及用户 hook 不发；插件标识会进入配置的 exporter |
| `claude_code.compaction` | 官方原生；compaction 完成 | `trigger`、`success`、`duration_ms`、`pre_tokens`、`post_tokens`、`error`、`precompute_reuse` | `precompute_reuse` 仅 manual，v2.1.153+；失败时 error 可能敏感 |
| `claude_code.feedback_survey` | 官方原生；survey 展示/回答 | `event_type`、`appearance_id`、`survey_type`、`response`、`enabled_via_override` | 包含用户选择；override 受 `CLAUDE_CODE_ENABLE_FEEDBACK_SURVEY_FOR_OTEL` 控制 |

## 本仓库 cchooks 自定义 LogRecord

这些事件不是 `claude_code.*` 官方事件，也不代表官方事件已由本仓库重发。

| 事件 | 状态/触发 | 字段 | 条件与隐私 | 代码来源 |
|---|---|---|---|---|
| `dynamic_stop` | 当前上报；dynamic handler 在 Stop/合成 Plan Stop 后上传 | `event.name`、`gen_ai.chat.id`、`agentic.turn.id`、turn start/end、VCS URL/ref/revision、cwd、`agentic.state.post_unchanged`、可选 username/user.id、transcript path、query、input/output messages、压缩 pre/post state、skip reason、synthetic | 包含 prompt/response、路径、仓库和压缩状态；由独立 dynamic endpoint/config 决定是否发送 | [`dynamic_handler.rs`](../../../claudecode/cchooks/src/hooks/dynamic/dynamic_handler.rs) |
| `dynamic.stop` | 条件上报；turn-digest Stop | chat/turn/query/cwd/VCS、username、input/output messages、request model | `privacy` collection mode 直接禁用；还受 feature/endpoint 和 stop-hook 状态控制 | [`turn_digest_handler.rs`](../../../claudecode/cchooks/src/hooks/turn_digest/turn_digest_handler.rs) |

LogRecord 的 OTLP `timeUnixNano` 来自 turn/消息时间；`body` 与 `event.name` 使用事件名。当前 builder 没有为这些自定义记录注入可证明的 `traceId`/`spanId`，因此不能宣称它们一定与 cchooks trace 做 OTLP 原生关联。

## 不应混入当前上报的项目

- 官方 24 类事件是 Claude Code host 能力，不是 cchooks emitter inventory。
- Hook payload、host transcript 和 replay 输入不是自动上报的 LogRecord 字段。
- 官方 Metrics 和官方 detailed tracing 另属不同 signal，不在本文事件字段表内。
