# Pi observ-pi 事件字段

## 范围与证据

本文只记录本仓库 `observ-pi` 生产 telemetry 当前会通过 OpenTelemetry Logs 发出的事件。事实来源是 [`SemanticEngine`](../../../pi/observ-pi/src/semantic.ts) 的 emitter call site；[`CanonicalNormalizer`](../../../pi/observ-pi/src/normalize.ts) 生成的 hook envelope 和 `pireplay` raw 输入只属于 normalization/replay 输入平面，不是本文的已上报 LogRecord 事件。

状态说明：`当前上报` 表示 `semantic.ts` 有直接 `type: 'event'` 调用；`条件上报` 表示需要对应 hook、运行分支或配置；`raw-only` 表示仅存在于 canonical hook envelope 或 replay 输入；`不可用` 表示当前生产 emitter 没有证据会上报。

事件发送还受生产开关和 signal 配置约束：`OBSERV_PI_ENABLE_TELEMETRY=true` 或 `AGENT_ENABLE_TELEMETRY=true` 才初始化 telemetry；`eventsConfig.enabled` 和 logs endpoint 决定实时 logs exporter；`OBSERV_PI_DEBUG=true` 只会额外把同一 OTLP logs 写到本地 debug JSONL，不会创建新的事件语义。隐私模式由 `OBSERV_PI_COLLECTION_MODE` 或 `AIVISION_COLLECTION_MODE` 选择 `full` / `privacy`。

## 当前事件目录

`observ-pi` 当前只发出下列七类事件。

| 事件 | 状态/触发 | 主要字段 | 配置、隐私与边界 | 代码来源 |
|---|---|---|---|---|
| `api_request` | 当前上报；assistant `message_end` 完成当前 LLM generation，或 `session_shutdown` 强制关闭未完成 generation | 公共字段、`event.name`、`event.sequence`、`gen_ai.operation.name=chat`、`agentic.turn.number`、`duration_ms`、请求/响应 model、provider、token usage、cache read tokens、input/output messages、system instructions、可选 response/error 字段 | 内容字段在 canonical normalization 之前受 `privacy` 投影影响；正常完成有 usage 和 output，shutdown 路径用 missing token 标记和 `finish_reasons=["cancelled"]`；还会带 `api_error` 或 `cancelled` 错误 | [`finishGeneration`](../../../pi/observ-pi/src/semantic.ts) |
| `tool_result` | 当前上报；`tool_execution_end` 找到已开始 tool，或 `session_shutdown` 强制关闭未完成 tool | 公共字段、`event.name`、`event.sequence`、`gen_ai.operation.name=execute_tool`、turn number、tool name/call id/arguments/result、tool type/name/raw_name/id、`duration_ms`、可选 `error.type` / `error.message` | `tool_execution_end.isError=true` 标为 `tool_error`；shutdown result 为取消占位值；arguments/result 可能包含工具输入输出，privacy 模式在 normalizer 投影层脱敏 | [`finishTool`](../../../pi/observ-pi/src/semantic.ts) |
| `compaction_result` | 条件上报；`session_compact` 结束已开始 compaction，或 `session_shutdown` 强制关闭未完成 compaction | 公共字段、`event.name`、`event.sequence`、`gen_ai.operation.name=execute_tool`、`gen_ai.tool.name=compaction`、`agentic.tool.type=system`、`agentic.runtime.name=pi`、turn number、`duration_ms`、可选 error | 只有先看到 `session_before_compact` 才有 compaction span/event；shutdown 路径带 `cancelled` 错误 | [`finishCompaction`](../../../pi/observ-pi/src/semantic.ts) |
| `session_start` | 当前上报；`session_start` hook | 公共字段、`event.name`、`event.sequence`、`gen_ai.operation.name=chat`、`gen_ai.session.id` | 这是 session 级事件，通常没有 spanKey；若 logs exporter 未启用则不导出 | [`session_start` case](../../../pi/observ-pi/src/semantic.ts) |
| `user_prompt` | 当前上报；`before_agent_start` 开启一个 Turn | 公共字段、turn `gen_ai.chat.id`、`event.name`、`event.sequence`、`gen_ai.operation.name=chat`、`agentic.turn.number`、`gen_ai.input.messages` | prompt 内容敏感；privacy 模式会在 canonical event payload 投影中脱敏后进入 semantic engine；同时开始 CHAT span | [`before_agent_start` case](../../../pi/observ-pi/src/semantic.ts) |
| `tool_call` | 当前上报；`tool_execution_start` 接收到有效 call id/name 且未重复 | 公共字段、`event.name`、`event.sequence`、`gen_ai.operation.name=execute_tool`、turn number、tool name/call id/type/name/raw_name/id、可选 arguments、`agentic.tool.input.missing`、`agentic.tool.input.truncated` | arguments 仅当 Pi payload 存在 `args` 时上报；缺失时明确标记 input missing；截断标记来自 normalizer projection | [`tool_execution_start` case](../../../pi/observ-pi/src/semantic.ts) |
| `session_end` | 当前上报；`session_shutdown` hook 收尾后 | 公共字段、`event.name`、`event.sequence`、`gen_ai.operation.name=chat`、`gen_ai.session.id`、`agentic.turn.number` | shutdown 会先强制结束未完成 LLM/tool/compaction/turn，再发 session_end；session_end 本身是 session 级事件 | [`session_shutdown` case](../../../pi/observ-pi/src/semantic.ts) |

## 公共事件字段

这些字段由 `semantic.ts` 构造的 `common` 属性和 event wrapper 注入；是否真正导出还取决于 OpenTelemetry logs provider 是否存在。

| 字段 | 类型 | 状态/条件 | 来源与含义 | 隐私 |
|---|---|---|---|---|
| `event.name` | string | 当前上报 | 与 LogRecord body/事件名相同 | 低风险 |
| `event.sequence` | int | state 可用时上报 | `SemanticEngine` 使用本地 high-water 区间保证 resume 后单调递增；无法预留持久区间时省略，避免产生重复序号 | 会话内关联 |
| `gen_ai.conversation.id` | string | 当前上报 | Pi session id | 高基数关联键 |
| `gen_ai.chat.id` | string | 当前上报 | `session_id:turn_number`；session 事件使用当前/默认 turn number | 高基数关联键 |
| `agentic.runtime.name` | string | 当前上报 | 固定 `pi` | 低风险 |
| `user.id` | string | 当前上报 | `ant.uid` 或配置解析出的 `userId` | 用户身份 |
| `ant.username` / `ant.uid` | string | 当前上报；可动态覆盖 | `otelExporter.attributes`、`OBSERV_PI_OTEL_ATTRIBUTES`、`AIVISION_ANT_USERNAME`、`AIVISION_ANT_UID` 等 signal attributes | 用户身份 |
| 自定义 signal attributes | scalar | 显式配置 | `otelExporter.attributes` 和 `OBSERV_PI_OTEL_ATTRIBUTES` | 部署方负责控制 |

## raw-only / 不可用项目

- `schema_version`、`observed_at_unix_nano`、`event_id`、`sequence`、`previous_checksum`、`checksum`、`hook`、`correlation`、`payload`、`projection`、`input_materialization` 是 [`CanonicalEvent`](../../../pi/observ-pi/src/types.ts) / replay envelope 字段，状态为 `raw-only`。它们不会按原名自动成为 LogRecord attributes。
- `turn_start`、`message_update`、`tool_execution_update`、`model_select`、`after_provider_response` 是生产 hook 输入或状态更新；当前没有对应同名 emitted event。`message_update` 和 `tool_execution_update` 还在 runtime 入口被标记为高频并直接忽略。
- `api_error`、`tool_error`、`compaction` 当前不是独立事件名；错误通过 `api_request`、`tool_result`、`compaction_result` 的 `error.*` 字段表达。
- Pi 官方原生事件目录当前不可用；本文不从 Pi hook 名称推导官方事件，也不把 replay capture 当作生产事件。
