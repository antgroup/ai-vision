# Skill & MCP Tool Span 字段规范

本文档定义了 `span.ai_agent.tool` 在 Skill 和 MCP 场景下的字段映射规范。

所有工具调用（builtin / mcp / skill）复用同一个 span type，通过 `agentic.tool.type` 区分类别。

## Turn 级 Langfuse 标签

Pi、OpenClaw 与 OpenCode 在一个逻辑 Turn 内识别到 Skill 或 MCP 后，会在该 Turn
结束前把摘要写到 owned CHAT 根 span：

| 属性 | OTel 类型 | 值规则 |
|---|---|---|
| `langfuse.trace.tags` | `string[]` | `skill:<agentic.tool.name>` / `mcp:<agentic.tool.name>` |

标签按首次识别顺序保存，使用完整字符串精确去重。完整标签最长 200 个字符；超过
限制时直接省略，不截断名称。没有 Skill/MCP 的 Turn 不输出该属性。OpenClaw 的
推导式 Skill 只有在派生 Skill span 成功创建后才贡献标签；失败的源 `read` 不贡献
Skill 标签，原生 Skill/MCP 的失败调用仍按已创建的分类 span 贡献标签。

该属性只写到插件拥有的 `gen_ai.span.kind=CHAT` Turn 根。子 session 借用的
`AGENT` span 不写入，子 session 的标签也不会向父 Turn 继承。当前兼容范围刻意保持
为 Turn-only；Tool span、event、metrics、拓扑以及 `agentic.skill.tags` 等其他字段均
不受影响。

## 字段总览

| 属性 | 类型 | 说明 | 适用场景 |
|---|---|---|---|
| `gen_ai.span.kind` | string | 固定值 `TOOL` | 全部 |
| `gen_ai.operation.name` | string | 固定值 `execute_tool` | 全部 |
| `gen_ai.tool.name` | string | 物理调用保留 transcript 原始工具名；命令型 Skill 使用 `skill <name>`；派生 Skill 固定为 `Skill`，来源保存在 `agentic.tool.raw_name` | 全部 |
| `gen_ai.tool.call.id` | string | 物理调用保留 transcript 原始 ID；命令型和派生 Skill 使用独立、稳定的 synthetic ID | 全部 |
| `gen_ai.tool.call.arguments` | string | 工具输入参数（JSON，可能被截断） | 全部 |
| `gen_ai.tool.call.result` | string | 工具输出结果（可能被截断） | 全部 |
| `agentic.tool.type` | string | 工具分类：`builtin` / `mcp` / `skill` | 全部 |
| `agentic.tool.name` | string | 解析后的逻辑名称 | 全部 |
| `agentic.tool.id` | string | 工具调用标识符 | 全部 |
| `agentic.tool.derived` | boolean | `true` 表示派生语义观察，不是第二次物理工具执行 | 派生 Skill |
| `agentic.tool.call_id.synthetic` | boolean | `true` 表示 `gen_ai.tool.call.id` 由本地合成 | 合成 ID 场景 |

## 推断规则

`agentic.tool.*` 三个字段由原始工具名或运行时特有的调用证据推断：

```
gen_ai.tool.name 以 "mcp__" 开头  → agentic.tool.type = "mcp"
gen_ai.tool.name == "Skill" 或 "skill <name>"
                                   → agentic.tool.type = "skill"
成功的通用 `read` 精确匹配 session `<available_skills>` location
                                   → read 保持 builtin；其后派生 0ms Skill span
subagent 生命周期 hook 触发        → agentic.tool.type = "subagent"
compaction 生命周期 hook 触发      → agentic.tool.type = "system"
其他                               → agentic.tool.type = "builtin"
```

`agentic.tool.name` 的推导：

| type | 推导方式 | 示例 |
|---|---|---|
| `builtin` | 等于 `gen_ai.tool.name` | `Read` |
| `mcp` | 去掉 `mcp__` 前缀，`__` 替换为 `.` | `mcp__code_search__query_code_with_keyword` → `code_search.query_code_with_keyword` |
| `skill` | 从原生 Skill input，或命令型 skill 的结构化 invocation 证据提取 | `eve-beta-skill` |
| `skill`（推导加载） | session catalog 的逻辑名称；仅无可用 catalog 时允许严格目录 fallback | `weather` |
| `subagent` | `subagent.{agentId}` | `subagent.helper` |
| `system` | 固定值 `compaction` | `compaction` |

`agentic.tool.id` 的推导：

| type | 推导方式 | 示例 |
|---|---|---|
| `builtin` | 等于 `gen_ai.tool.call.id` | `toolu_001` |
| `mcp` | 等于 `gen_ai.tool.call.id` | `toolu_xyz789` |
| `skill` | 原生调用使用真实 ID；命令型调用按 turn、occurrence、名称和 byte range 合成稳定 ID；推导加载沿用来源调用 ID 或按名称合成 | `toolu_abc123`、`skill-invocation:turn-1:0:analyze:0-8` 或 `skill-weather` |
| `subagent` | childSessionKey（子 agent session 标识） | `agent:helper:task-1` |
| `system` | 固定值 `compaction` | `compaction` |

---

## Skill 场景

Skill 有三种可观测入口：

1. **原生工具型 Skill**：transcript 中存在 `name="Skill"` 的真实 tool call，沿用真实 call ID 和工具起止时间。
2. **命令型 Skill**：运行时只在用户消息的结构化 placeholder（例如 `$analyze`）中暴露调用。此时生成独立的 `skill analyze` TOOL span，作为 turn root 的直接子节点；内部 builtin / MCP / subagent 调用仍保持原分类。
3. **推导加载型 Skill**：运行时没有原生 Skill hook，但成功的读取行为可由 session skill catalog 精确映射为一次逻辑 Skill 加载。

Codex transcript v2 中，user-role `<skill><name>...<path>...</skill>` 是已加载定义元数据，不是调用证据，不能单独产生 skill span。精确的 OMX UserPromptSubmit 路由消息可补强同名 placeholder，或在没有 placeholder 时作为隐式路由证据。

以 `eve-beta-skill` 为例，用户说 "帮我用eve-skill查询模型详情"：

| 属性 | 值 | 来源 |
|---|---|---|
| `gen_ai.span.kind` | `TOOL` | 固定 |
| `gen_ai.operation.name` | `execute_tool` | 固定 |
| `gen_ai.tool.name` | `Skill` | transcript tool_use.name |
| `gen_ai.tool.call.id` | `toolu_abc123` | transcript tool_use.id |
| `gen_ai.tool.call.arguments` | `{"skill": "eve-beta-skill"}` | transcript tool_use.input |
| `gen_ai.tool.call.result` | `"Launching skill: eve-beta-skill"` | transcript tool_result |
| `agentic.tool.type` | `skill` | 推断：gen_ai.tool.name == "Skill" |
| `agentic.tool.name` | `eve-beta-skill` | 从 isMeta 或关键字匹配提取 |
| `agentic.tool.id` | `toolu_abc123` | 有真实 id 用真实的，否则合成 |

### Codex 命令型 Skill

命令型 `$analyze` 示例：

| 属性 | 值 | 来源 |
|---|---|---|
| `gen_ai.span.kind` | `TOOL` | 固定 |
| `gen_ai.operation.name` | `execute_tool` | 固定 |
| `gen_ai.tool.name` | `skill analyze` | 命令型 skill 统一前缀 |
| `gen_ai.tool.call.id` | `skill-invocation:<turn>:0:analyze:0-8` | 稳定合成 ID |
| `gen_ai.tool.call.arguments` | `{"skill":"analyze","invocation":"$analyze","source":"codex_text_element","byte_range":{"start":0,"end":8}}` | transcript 结构化 invocation |
| `gen_ai.tool.call.result` | 最终 assistant 输出，或显式 terminal-state 缺失结果 | turn 结果 |
| `agentic.tool.type` | `skill` | 固定分类 |
| `agentic.tool.name` | `analyze` | 规范化逻辑名 |
| `agentic.tool.raw_name` | `Skill` | 与原生 Skill 统一的逻辑原始类型 |
| `agentic.tool.id` | 与 `gen_ai.tool.call.id` 相同 | 稳定合成 ID |

命令型 skill 的结束时间受 turn 边界约束，因为当前 Codex 协议没有独立 skill-end 事件。其 start/end 必须包含在 turn root 内，`agentic.tool.duration_ms` 必须与最终输出时间戳一致。若同名原生 `Skill` call 已存在，则按名称、可用时间和 occurrence 顺序一对一消费，避免重复；同名 `.codex/skills/<name>/...` 命令路径仅作为 legacy fallback，不再重复分类。

### Pi Skill 识别

Pi 同时提供两类可验证证据：

1. 用户执行 `/skill:<name>` 时，Pi 在 `before_agent_start` 前把命令展开为精确的
   `<skill name="..." location="...">...</skill>` 结构。插件只接受具有合法名称和路径的
   完整单块结构，据此创建一个 Turn 直系子 TOOL span，生命周期受 Turn 边界约束，并写入
   `skill:<name>` Turn 标签。命令块不依赖 catalog 匹配，因为可显式调用未暴露给模型的
   Skill。当前 Pi hook 没有暴露“此块确由 slash command 展开”的可信来源位，因此字节
   完全相同的手工输入只能识别为 Pi-compatible 结构，不能对外声明可信的 `/skill:*`
   provenance。
2. 模型自主选择 Skill 时，Pi 在有效 system prompt 的 `<available_skills>` 中暴露名称和
   `SKILL.md` 绝对路径，并通过 builtin `read` 加载。插件只在成功 read 的路径与 catalog
   location 逐字节相等时派生 0ms Skill span；read 保持 builtin，派生 span 通过 OTel Link
   指向 read，且不重复产生 tool event 或 RED metric。

catalog 解析有大小和条目数上限，并且严格限定在单个 Turn：每次
`before_agent_start` 都替换快照，缺失或非法 catalog 会清空上一 Turn 数据；Turn 结束和
session 边界也会清空。完全相同的重复项幂等，若同一 location 映射到不同名称，则该
location 整体不可用。结构异常、非规范绝对路径、失败 read、Turn 已结束或仅凭路径形状
猜测时均 fail closed。Pi 不启用无 catalog 目录 fallback。

canonical transcript 只保存已验证的 Skill 逻辑名，不持久化额外 location 元数据；
full/privacy replay 因此复用同一 `SemanticEngine`，且 privacy 模式的派生参数和结果仍为
`[REDACTED]`。成功 read 的 Link context 是单次消费、Turn-bound，并受 256 项 FIFO 上限
约束，避免长会话中无界增长或跨 Turn 错链。

若未来 Pi 原生暴露 `Skill` tool，物理 tool span/event/metric 始终优先保留。只有运行时
显式提供且通过校验的 causal `occurrenceId` 才能把命令/读取 fallback 与原生调用配对并
抑制重复；不同 occurrence 保持独立。原生通道已出现但没有可验证相关 ID 的混合证据按
fail-closed 处理：保留所有物理调用，抑制无法证明独立的合成 Skill span。识别逻辑通过
固定版本契约夹具覆盖 Pi `0.80.6` 与 `0.80.10` 的真实命令展开和 catalog 格式。

### OpenClaw 推导式 Skill 加载

OpenClaw 没有原生 `Skill` tool hook。`llm_input.systemPrompt` 中的
`<available_skills>` catalog 按 session 缓存。通用 `read` 始终保留为 builtin TOOL
span、原始耗时、`tool_result` 和 RED metrics。成功读取的路径精确匹配 catalog
location 后，在 read span 结束之后派生一个独立的 0ms Skill TOOL span；没有任何
可用 catalog 时，可使用严格绝对路径 `/.../skills/<name>/SKILL.md` fallback。
派生 Skill span 与 read 同为 Turn 的直接子节点，并通过 OTel Link 指向已完成的
read span。失败读取不产生 Skill span，因为该信号表达“Skill 指令成功加载”，
而不是“尝试调用 Skill”。

| 属性 | read span | 派生 Skill span |
|---|---|---|
| `gen_ai.tool.name` | `read` | `Skill` |
| `gen_ai.tool.call.id` | `call-weather` | `skill-load-call-weather`，独立 synthetic ID |
| `gen_ai.tool.call.arguments` | 实际 read 参数 | full: `{"skill":"weather"}`；privacy: `[REDACTED]` |
| `gen_ai.tool.call.result` | 实际 read 输出 | full: `{"status":"loaded"}`；privacy: `[REDACTED]` |
| `agentic.tool.raw_name` | `read` | `read`，表示派生来源 |
| `agentic.tool.type` | `builtin` | `skill` |
| `agentic.tool.name` | `read` | `weather`（catalog name） |
| `agentic.tool.id` | 沿用 builtin 规则 | 有真实 ID 时为 `call-weather`；否则 `skill-weather` |
| `agentic.tool.duration_ms` | runtime 真实读取耗时 | `0` |
| `agentic.tool.derived` | 不设置 | `true` |
| `agentic.tool.call_id.synthetic` | 视物理调用 ID 来源而定 | `true` |

OTel Link 是派生 span 关联来源 read 的主机制；独立 `gen_ai.tool.call.id` 避免把两个
TOOL spans 解释成同一次物理执行，`agentic.tool.id` 则保持 cchooks 的 Skill identity
规则。full 模式的 input/output 是规范化的“加载 Skill 名称/加载成功”派生语义，不冒充
运行时原生 Skill tool payload；privacy 模式继续脱敏。catalog version 与推断来源只
保留在内存中，不作为未知属性输出。后续 `web_fetch`
或其他工具不会继承 Skill 类型。派生 Skill span 不产生第二个 `tool_result`，也不
增加 `agent.tool_call.*` datapoint；已加载 Skill 的无读取复用在没有原生 hook 时不可观测。

## MCP 场景

以 `mcp__code_search__query_code_with_keyword` 为例：

| 属性 | 值 | 来源 |
|---|---|---|
| `gen_ai.span.kind` | `TOOL` | 固定 |
| `gen_ai.operation.name` | `execute_tool` | 固定 |
| `gen_ai.tool.name` | `mcp__code_search__query_code_with_keyword` | transcript 原始值 |
| `gen_ai.tool.call.id` | `toolu_xyz789` | transcript tool_use.id |
| `gen_ai.tool.call.arguments` | `{"searchPattern": "hello", ...}` | transcript tool_use.input |
| `gen_ai.tool.call.result` | `"Found 3 matches..."` | transcript tool_result |
| `agentic.tool.type` | `mcp` | 推断：gen_ai.tool.name 以 `mcp__` 开头 |
| `agentic.tool.name` | `code_search.query_code_with_keyword` | 去 `mcp__` 前缀，`__` → `.` |
| `agentic.tool.id` | `toolu_xyz789` | 等于 gen_ai.tool.call.id |

## Builtin 对照

普通 `Read` 调用：

| 属性 | 值 | 来源 |
|---|---|---|
| `gen_ai.span.kind` | `TOOL` | 固定 |
| `gen_ai.operation.name` | `execute_tool` | 固定 |
| `gen_ai.tool.name` | `Read` | transcript 原始值 |
| `gen_ai.tool.call.id` | `toolu_001` | transcript tool_use.id |
| `gen_ai.tool.call.arguments` | `{"path": "/src/main.ts"}` | transcript tool_use.input |
| `gen_ai.tool.call.result` | `"file contents here..."` | transcript tool_result |
| `agentic.tool.type` | `builtin` | 推断：不匹配 mcp/skill 规则 |
| `agentic.tool.name` | `Read` | 等于 gen_ai.tool.name |
| `agentic.tool.id` | `toolu_001` | 等于 gen_ai.tool.call.id |

## Subagent 场景

父 agent 委派子 agent `helper` 执行任务：

| 属性 | 值 | 来源 |
|---|---|---|
| `gen_ai.span.kind` | `TOOL` | 固定 |
| `gen_ai.operation.name` | `execute_tool` | 固定 |
| `gen_ai.tool.name` | `helper` | event.agentId（原始值） |
| `agentic.tool.type` | `subagent` | subagent 生命周期 hook 触发 |
| `agentic.tool.name` | `subagent.helper` | `subagent.{agentId}` |
| `agentic.tool.id` | `agent:helper:task-1` | childSessionKey |
| `agentic.subagent.session_key` | `agent:helper:task-1` | 跨 trace 关联标识 |
| `agentic.subagent.mode` | `run` | 执行模式：run / session |
| `agentic.subagent.outcome` | `ok` | 终止结果：ok / error / timeout / killed |

## System (Compaction) 场景

上下文压缩操作：

| 属性 | 值 | 来源 |
|---|---|---|
| `gen_ai.span.kind` | `TOOL` | 固定 |
| `gen_ai.operation.name` | `execute_tool` | 固定 |
| `gen_ai.tool.name` | `compaction` | 固定 |
| `gen_ai.tool.call.arguments` | `{"trigger":"system"}` | 系统触发 |
| `gen_ai.tool.call.result` | `{"messagesAfter":1,"tokensSaved":64322}` | 压缩结果摘要 |
| `agentic.tool.type` | `system` | 系统操作 |
| `agentic.tool.name` | `compaction` | 固定 |
| `agentic.tool.id` | `compaction` | 固定 |
| `agentic.compaction.tokens_saved` | `64322` | before - after 差值 |
