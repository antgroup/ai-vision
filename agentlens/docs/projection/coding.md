# AgentLens Coding Projector 当前实现

本文是 `CodingProjector` 字段提取逻辑的权威实现说明。公共 Trace / Projection 合同见
[`docs/projection/trace-contract.md`](trace-contract.md)。

## 1. 定位与路由

系统只有一个 Coding Projector family：

| Canonical runtime | Aliases |
|---|---|
| `cc` | `claudecode`、`codex`、`codefuse`、`antcc`、`antcodex`、`dsh` |

Alias 不产生独立实现。每条 source trace 必须代表一个 Coding turn；存在多个 CHAT anchor 时返回不可
投影，不拆分、不合并，也不静默选择第一轮。原始 `Trace.spans` 不被排序或改写。

```text
Trace.spans
  -> CodingProjector
  -> adapter.project_coding_raw()
       稳定时间排序副本
       CHAT anchor 校验
       对话 / LLM / TOOL / subagent 提取
       outcome 与 steps 组装
  -> CodingProjection
```

## 2. Span 识别

| 语义 | 首选证据 | 兼容证据 | 排除边界 |
|---|---|---|---|
| CHAT | `attributes.gen_ai.span.kind == "CHAT"` | SERVER span 且 name 包含 `Turn` | INTERNAL/CLIENT 本身不构成 CHAT |
| LLM | `attributes.gen_ai.span.kind == "LLM"` | 无 | 不按 name 猜测 |
| TOOL | `attributes.gen_ai.span.kind == "TOOL"` 且 operation 为空或 `execute_tool` | call ID；或 `tool ` name 同时存在 arguments/result | INTERNAL 或 `tool ` name 单独不构成工具 |
| Subagent | `attributes.agentic.subagent.id` | 无 | 缺少 ID 时不补造 |

工作副本按 start、end、span ID、parent ID、name 和稳定 JSON 依次排序。时间支持 OTel epoch 纳秒和
ISO-8601；无法解析的时间排在有效时间后。

## 3. 字段提取表

| Projection 字段 | 原始节点 / Span | 原始路径 | 选择与优先级 | 缺失行为 | 示例 |
|---|---|---|---|---|---|
| `timestamp` | 当前 turn 全部 rows | `start_time_unix_nano` | 最早可解析开始时间 | `None` | `2026-08-27T10:00:00+00:00` |
| `user_query` | CHAT 或 LLM | `attributes.gen_ai.input.messages[].content` | CHAT 优先的首个含请求 row 中最后一个 user message；更早消息为 history；当前 user 无可见文本时为缺失，不提升历史；所有 message 均缺失时取第一个 `attributes.agentic.input.delta` | `""` | `修复登录失败` |
| `user_query_span_id` | 与 query 相同 | `span_id` | 跟随命中的 query 来源 | `None` | `chat-1` |
| `agent_output` | CHAT 或 LLM | `attributes.gen_ai.output.messages[].content` | CHAT anchor 的 assistant output 优先；否则取时间最晚的 LLM assistant；最新 assistant 无可见文本时保持缺失，不提升旧回答 | `""` | `已修复并通过测试` |
| `agent_output_span_id` | 与 output 相同 | `span_id` | 跟随命中的 output 来源 | `None` | `llm-2` |
| `outcome.has_error` | 全部 rows | `status_code` | 任一值为 `2` | `false` | `false` |
| `outcome.interrupted` | CHAT | name / error type / status message | name 含 `(cancelled)`、error type 为 cancelled 或 status message 含 cancelled；DSH CHAT（`agentic.runtime.name=dsh`）的 `error.type=aborted` 也表示主动中断 | `false` | `false` |
| `outcome.total_latency_ms` | 全部 rows | start/end | 最早 start 到最晚 end | `None` | `1250` |
| `steps` | LLM、TOOL | 标准 messages 或 tool arguments/result | 按稳定时间序保留 | 空列表 | `[{"type":"TOOL"}]` |
| `node_seq` | CHAT + steps | span name | CHAT name 后拼接每个 step name/type | 仅包含 fallback CHAT 名 | `Turn → tool Edit → LLM` |
| `tool_actions` | 确认 TOOL | `gen_ai.tool.*`、`agentic.tool.*`、status/time | 每个确认 TOOL 生成一项，JSON arguments/result 尝试解码 | 空列表 | `Edit(path=a.py)` |
| `file_mutations` | `tool_actions` | 工具名、参数、Shell 命令 | 仅确认代码/配置变更且能提取代码路径；内容前后都可确认时为 exact | 空列表 | `edit a.py` |
| `verification_actions` | `tool_actions` | 已确认测试/构建/Lint 命令与状态 | 分类为 test 的工具动作派生 | 空列表 | `pytest: passed` |
| `subagents` | 任意 row | `agentic.subagent.*` | 每个有明确 subagent ID 的 span 一项 | 空列表 | `worker-1` |

`file_mutations` 和 `verification_actions` 是 canonical `tool_actions` 的确定性派生属性，不建立第二套
工具事实来源。无法确认身份、路径、动作类型或状态时保留空值或跳过，不凭 span name 补造。

工具执行状态优先使用明确的进程退出码，再使用结构化结果或工具状态。验证结果独立保留具名
verification_result/status；显式 unknown 不用传输成功补齐。无具名结果时，仅未被尾命令
掩盖的检查可由退出码/状态推导 passed/failed，复杂或掩盖序列派生 unknown，不拆分原动作。具名 Shell 工具的 DSH
输出末行 `[exit code: N]` 是退出码 carrier：`0` 对应 `passed`，非零对应 `failed`，即使原始工具
状态为 `success` 也按退出码派生结果。它必须是普通文本输出中独立的最后一行；read/edit 等工具
返回的文件内容、正文中的示例和畸形标记不作为该 carrier。结构化顶层退出码优先于文本 footer，
footer 优先于 stdout/stderr 正文里的示例退出码。原始 `tool_actions[].status/result` 保持不变。

## 4. Trace 对照示例

脱敏后的最小 Trace：

```json
[
  {
    "span_id": "chat-1",
    "name": "Turn",
    "start_time_unix_nano": "1787805600000000000",
    "attributes.gen_ai.span.kind": "CHAT",
    "attributes.gen_ai.input.messages": [
      {"role": "user", "content": "把 app.py 中的端口改成 8081"}
    ]
  },
  {
    "span_id": "tool-1",
    "name": "tool Edit",
    "start_time_unix_nano": "1787805601000000000",
    "attributes.gen_ai.span.kind": "TOOL",
    "attributes.gen_ai.operation.name": "execute_tool",
    "attributes.gen_ai.tool.call.id": "call-1",
    "attributes.gen_ai.tool.call.arguments": {
      "path": "app.py", "old_string": "8080", "new_string": "8081"
    },
    "status_code": 1
  },
  {
    "span_id": "llm-1",
    "name": "final response",
    "start_time_unix_nano": "1787805602000000000",
    "attributes.gen_ai.span.kind": "LLM",
    "attributes.gen_ai.output.messages": [
      {"role": "assistant", "content": "端口已改为 8081。"}
    ]
  }
]
```

| Projection 字段 | 原始路径 | 投影结果 |
|---|---|---|
| `user_query` | `chat-1.attributes.gen_ai.input.messages[0].content` | `把 app.py 中的端口改成 8081` |
| `agent_output` | `llm-1.attributes.gen_ai.output.messages[0].content` | `端口已改为 8081。` |
| `tool_actions[0]` | `tool-1.attributes.gen_ai.tool.*` | call ID `call-1`、name `Edit`、status `success` |
| `file_mutations[0]` | `tool_actions[0].arguments` | `app.py`、operation `edit`、evidence `exact` |
| `steps` | `tool-1`、`llm-1` | 两个按稳定时间序排列的 ExecutionStep |

## 5. Prompt 变量

Coding Projector 暴露：`user_query`、`agent_output`、`outcome`、`node_sequence`、`steps`、
`tool_actions`、`file_mutations`、`verification_actions`、`subagents`，以及 `current_request`、
`final_output`、`history`、`provided_context`、`model_instructions`。新增事实附来源 ID 与原始字段路径，
缺失为 None；history[i].content 与 history 根变量读取同一来源，旧 user_query/agent_output 与当前事实一致。
纯正文字符串保持字面值，SDK 信封只在消息载体边界解码。系统指令保留在 model_instructions，不能当作检索材料。复杂表达式只在公开且有界的
Projection 序列化结果上解析。

专项消费上述实际材料及SourceReference，缺失/不支持/超界不会补造或截短后判GOOD。
操作说明见[专项指南](../diagnoser/specialists.md)。

## 6. 失败与代码入口

多个 CHAT anchor 返回不可投影；没有 CHAT 时使用首 row span ID 作为 fallback anchor，仍只按确认的
LLM/TOOL 语义提取。可选字段缺失时局部为空；声明为结构化消息的畸形 JSON 使投影失败，不能当作缺材料跳过。Projector 异常由
`project_trace()` 包装为 `projection_error`。

| 文件 | 职责 |
|---|---|
| `service/datasource/projection/registry.py` | 显式 Projector 清单与 runtime 路由 |
| `service/datasource/projection/projectors/coding/projector.py` | Projection 调度和 Prompt 变量 |
| `service/datasource/projection/projectors/coding/adapter.py` | Span 识别、字段提取与 Projection 组装 |
| `service/datasource/projection/projectors/coding/models.py` | ToolAction、Mutation、Verification、Subagent 与 CodingProjection |
| `service/datasource/projection/projectors/coding/tool_actions.py` | 工具动作分类、路径与命令语义 |
