# Codex 事件字段

## 范围与状态

本文记录 Codex 官方原生 OpenTelemetry event categories。官方证据来自 [Agent approvals & security：Event categories](https://learn.chatgpt.com/docs/agent-approvals-security#event-categories)，检索日期 **2026-07-27**。该页面明确称这些是 **representative event types**，不是完整字段 catalog；精确字段名同时以 Codex 原生 OTel 生产代码为证据。

本页描述 Codex 原生 OTel schema，不依赖具体存储表名，也不纳入 `cdxhooks` 自定义的 `dynamic_stop` 事件。

## 官方原生 representative categories

| 事件 | 状态 | 官方页面明确描述的内容 | 隐私/边界 | 来源 |
|---|---|---|---|---|
| `codex.conversation_starts` | 官方原生 | model、reasoning settings、sandbox/approval policy | 暴露运行策略，不宣称具体 attribute key | 官方页面 |
| `codex.api_request` | 官方原生 | attempt、status/success、duration、error details | 错误细节可能敏感；字段名待完整 catalog | 官方页面 |
| `codex.sse_event` | 官方原生 | stream event kind、success/failure、duration；`response.completed` 带 token counts | 流式协议事件，不是本仓库 span kind | 官方页面 |
| `codex.websocket_request` | 官方原生 | request duration | 页面把 request 与 per-message event 并列；不臆造更多字段 | 官方页面 |
| `codex.websocket_event` | 官方原生 | per-message kind、success/error | WebSocket 消息类别，不代表 cdxhooks 重放该事件 | 官方页面 |
| `codex.user_prompt` | 官方原生 | prompt length；显式启用前 content redacted | 内容默认脱敏；具体启用键应以对应 Codex 版本配置文档为准 | 官方页面 |
| `codex.tool_decision` | 官方原生 | approved/denied；source 为 configuration 或 user | 安全审计信息；官方页未给完整字段名/枚举 | 官方页面 |
| `codex.tool_result` | 官方原生 | duration、success、output snippet | output snippet 可能含命令/文件内容 | 官方页面 |

官方页面还提到相应 metrics，但 Metrics 不在本文范围。SSE/WebSocket category 也不能据此写入本仓库 trace topology。

## 证据边界

- 官方页面列出的 8 个 `codex.*` 项是代表性类别；完整字段集合同时依据 Codex 原生 OTel 生产代码，不从类别摘要补猜。
- `dynamic_stop` 是 AiVision 自定义事件，已明确排除，不能映射或重命名为任一官方 `codex.*` category。
- transcript 中的内部事件、collaboration activity、SSE/WebSocket 原始记录只有在生产 LogRecord emitter 调用被证明后才能标记为本仓库事件。
