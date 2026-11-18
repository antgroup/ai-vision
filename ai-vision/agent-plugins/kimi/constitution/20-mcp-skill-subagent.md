# Kimi-CLI 第二章证据调研：MCP、Skill 与 Subagent

本文档按"数据项"举证各条款。格式沿用第一章。

证据指向的源码均位于 `<kimi-cli>`（独立的 kimi-cli 上游仓库），下文路径为该仓库相对路径。

## 总览矩阵

| 条款 | 结论 | 主要数据来源 | 可靠性 | 实现代价 | 架构影响 | 约束建议 |
|---|---|---|---|---|---|---|
| C4 MCP 工具 | 支持 | `tool_name` 运行时 `mcp__{server}__{tool}` + wire `MCPLoadingEnd` 白名单 | 原生可得 | 低 | 低 | 必须项 |
| C5 Skill 工具 | 部分支持 | `UserPromptSubmit.prompt` 中 `/skill:` `/flow:` 前缀 | 原生可得 | 低 | 低 | 推荐项 |
| C6 Subagent | 支持 | `SubagentStart` / `SubagentStop` hooks + wire `SubagentEvent` + `subagents/` 独立目录 | 原生可得 | 低 | 低 | 必须项 |
| C7 跨 Trace 关联 | 暂不支持 | 无 hook 携带 trace context；kimi-cli 无 OTel 集成 | 不适用 | 高 | 高 | 放弃 |

---

## C4 MCP 工具调用特殊语义

### 数据项 C4.1：MCP 工具的识别与分类

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`PreToolUse.tool_name`（运行时格式 `mcp__{server}__{tool}`，来自 `MCPTool.__init__` 动态拼接）
- wire 事件：`MCPLoadingEnd`（`wire/types.py`），包含服务器名 + 工具列表
- 内部状态：`KimiToolset._tool_dict` 中的 `MCPTool` 实例（`src/kimi_cli/soul/toolset.py:561` `class MCPTool[T](CallableTool)`）

#### 获取方式

- 关键字段：`tool_name` 命名空间格式 `mcp__{server_name}__{tool_name}`
- 代码入口：
  - `toolset.py:453` `MCPTool(server_name, tool, client, runtime=runtime)` 实例构造
  - `toolset.py:561-588` `MCPTool.__init__` 中 `self.name` 的拼装
  - `wire/types.py` `MCPLoadingEnd` 事件在 MCP server 连接完成后发出

#### 正证据

- MCP 工具运行时名称格式为 `mcp__{server}__{tool}`（Python MCP ecosystem / fastmcp 标准前缀），可通过前缀匹配识别：
  - `gen_ai.tool.name` = 原始运行时值（如 `mcp__code_search__list_pull_requests`）
  - `agentic.tool.name` = 解析后的逻辑名（`code_search/list_pull_requests`）
  - `agentic.tool.type = mcp`
- `kimihooks` 实现层通过 `agentic_otel_common::parse_agentic_tool()` 复用作前缀解析（经测试 `mcp_tool_classified_correctly` 覆盖，`otel.rs:1539-1573`）
- `wire MCPLoadingEnd` 事件可以在 hook 进程启动时建立 MCP 工具白名单作为兜底

#### 反证据

- **kimi 源码全文 grep `mcp__` 零命中**：前缀来自 `MCPTool.__init__` 运行时动态拼接，不是常量。这意味着如果 Moonshot 改了命名格式，前缀匹配会失效。
- 兜底策略依赖 `MCPLoadingEnd` 事件：Stop hook 中重建 wire 时需单独收集 MCP server/tool 列表，在 Stop handler 内维护白名单（当前实现未建白名单，依赖 `parse_agentic_tool` 的纯前缀逻辑）
- 同名冲突：用户 MCP 工具名若与内置工具同名（理论可能），需以 `MCPTool` 实例类型为权威，但 hook 进程无法直接访问 `KimiToolset._tool_dict`

#### 推导链路

- 优先：`tool_name` 前缀匹配 `mcp__` → `parse_agentic_tool()` 直接分类
- 兜底：`MCPLoadingEnd` 维护的白名单（Stop handler 在会话生命周期内收集）
- 退化：前缀不匹配且不在白名单 → `native` / `external` / `unknown`

#### 风险与边界

- `MCPTool` 命名格式是 Moonshot/fastmcp 内部约定，非文档承诺。若上游改变格式，`parse_agentic_tool` 的分类会退化到 `native`，需监控 `agentic.tool.type=unknown` 占比
- `WireExternalTool`（`toolset.py:235`）是 wire IDE 动态注册的工具，分类为 `external`，不能误划入 MCP

#### 管控动作

- `agentic.tool.type = mcp` + `agentic.tool.name` 进入硬约束
- 分类来源声明：前缀匹配为主，`MCPLoadingEnd` 白名单为辅
- golden 应覆盖三类 MCP 场景：单 MCP server、多 MCP server、用户中断 MCP 连接
- `unknown` 兜底允许，但应在 metrics `agent.tool_call.count` 中监测 `agentic.tool.type=unknown` 占比 >1% 触发告警

### 数据项 C4.2：MCP 工具的 span 拓扑

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- 同一 C3 Tool span 拓扑（MCP 不改变 span 结构）

#### 获取方式

- 继承 C3 完整配对逻辑：`PreToolUse` + `PostToolUse[Failure]`（`tool_call_id` 配对）

#### 正证据

- MCP 工具走同一 `KimiToolset.handle(tool_call)` 通道（`toolset.py:108-115`），hook 触发时机与内置工具一致
- `tool_call_id` 由 kosong 统一生成（`packages/kosong/src/kosong/message.py:177-181`），MCP 与内置工具无区别

#### 反证据

- 无

#### 推导链路

- 无需推导：MCP 工具 span 就是标准 TOOL span + `agentic.tool.type=mcp` 扩展

#### 风险与边界

- 无

#### 管控动作

- MCP 工具 span 必须与 C3 同一拓扑（sibling under root），不创建额外 span 类型
- 不创建 "MCP client span" 包裹（宪章 C4 明确放弃协议级 MCP client span）

---

## C5 Skill 工具调用特殊语义

### 数据项 C5.1：Skill/Flow 的识别与触发路径

#### 结论

部分支持

#### 约束建议

推荐项

#### 数据来源

- runtime hook：`UserPromptSubmit.prompt`（slash 命令纯文本）
- 内部常量：`SKILL_COMMAND_PREFIX = "skill:"` / `FLOW_COMMAND_PREFIX = "flow:"`（`src/kimi_cli/soul/kimisoul.py:94-95` + `src/kimi_cli/ui/shell/slash.py:53`）

#### 获取方式

- 关键字段：`UserPromptSubmit.prompt` 字符串前缀匹配
- 代码入口：
  - `kimisoul.py:776` `_make_skill_runner` — skill 执行通道
  - `kimisoul.py:94-95` 常量定义

#### 正证据

- `/skill:` 和 `/flow:` 是 kimi 原生 slash 命令，用户可直接触发
- `UserPromptSubmit.prompt` 承载完整用户输入文本（`src/kimi_cli/hooks/events.py::user_prompt_submit()`），可从字符串前缀 `/skill:` / `/flow:` 识别
- telemetry 有 `skill_invoked` / `flow_invoked` 事件（`src/kimi_cli/telemetry/__init__.py`），证明 skill 生命周期有原生监控

#### 反证据

- **Skill/Flow 不走 `tool_call` 路径**：slash 命令进入 kimi soul 主循环的特殊处理分支（`_make_skill_runner`），不经过 `KimiToolset.handle()`。因此：
  - `PreToolUse` hook **不会**携带 skill: 前缀
  - skill 执行过程中产生的工具调用（如 Shell/WriteFile）是普通 TOOL span，不标记 `agentic.tool.type=skill`
- Skill 的"调用-完成"边界没有专用 hook（不像 Subagent 有 Start/Stop），只能从 `UserPromptSubmit` + 后续 `Stop` 推断
- `Skill` 作为"命令型 skill"复用 TOOL span 模型是宪章 C5 的明确设计，但缺少 skill 执行的身份锚

#### 推导链路

- Skill 识别：在 `UserPromptSubmit.prompt` 中检测 `/skill:` 或 `/flow:` 前缀 → 标记 `agentic.tool.type = skill`（仅用于分类）
- Skill span 生成：依赖 `Stop` hook → wire.jsonl 中的 `TurnBegin` + `StatusUpdate` 还原 → 嵌入 `agentic.tool.type = skill`

#### 风险与边界

- 用户输入可能包含 `/skill:` 字样但不是真正的 slash 命令（例如在普通对话中提到），会导致误分类
- 多层 skill 嵌套（skill 触发 subagent）的子 span 无法追溯到顶层 skill
- 当前 kimi 实现的 `agentic.tool.type = skill` **尚未落地**（`otel.rs` 中 `parse_agentic_tool` 不识别 skill 前缀）

#### 管控动作

- Skill 识别为推荐项（不是所有 kimi 用户都用 skill）
- 如果实现：`agentic.tool.type = skill` 写入 generation/tool span 的属性扩展
- 实现侧建议：`Stop` handler 中扫描 `UserPromptSubmit.prompt` 是否以 `skill:` / `flow:` 开头，若是则写入 `agentic.turn.skill_name` 到 root span

---

## C6 Subagent 委派语义

### 数据项 C6.1：Subagent 的 hook 事件与字段完整性

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`SubagentStart`（`src/kimi_cli/subagents/runner.py:267-280`）+ `SubagentStop`（`:295-310`）
- transcript：`wire.jsonl` `SubagentEvent`（`wire/types.py`）
- 文件系统：`<session>/subagents/<agent_id>/wire.jsonl` + `meta.json`

#### 获取方式

- Hook 路径（按需）：
  - `SubagentStart` → `session_id`, `cwd`, `agent_name`, `prompt`（前 500 字符）
  - `SubagentStop` → `session_id`, `cwd`, `agent_name`, `response`（前 500 字符）
  - `matcher_value` = subagent type name（如 `"coder"`）
- Stop 重建路径（当前实现）：
  - 内联：`wire.jsonl` 中每条 `SubagentEvent` → `parent_tool_call_id` + `agent_id` + `subagent_type` + `event`（嵌套 wire message）
  - 独立：扫描 `subagents/<agent_id>/wire.jsonl` + `meta.json`

#### 正证据

- kimi 是全项目唯一原生提供 subagent 生命周期 hook 的 agent（Claude Code 仅在 transcript 中有 `spawn_agent` 标记）
- **三种数据来源互为冗余**：
  1. `SubagentStart/Stop` hooks — 实时事件，有精确时间
  2. wire `SubagentEvent`（内联模式）— 父 wire.jsonl 中嵌入子事件流
  3. `subagents/` 独立目录（独立模式）— 子 agent 有独立 session 目录
- `stop.rs` 的实现已覆盖所有三种来源（`enrich_with_subagent_dirs` 处理独立目录，`WireParser` 处理内联 SubagentEvent）
- `ToolCall(name=Agent)` 与 `SubagentEvent.parent_tool_call_id` 可关联：`stop.rs:383-396` 收集所有 Agent ToolCall 作为候选 parent

#### 反证据

- `SubagentStart` / `SubagentStop` 是 `HookEngine.trigger()` awaited/fire-and-forget 事件（非同步双生）。如果在两个事件之间 kimi 进程 panic，`SubagentStop` 可能不触发；但 wire.jsonl 已写入的子事件可以恢复
- `prompt` / `response` 截断到 500 字符（`runner.py:270, 310`），完整 prompt 需从 `context.jsonl` 取
- 内联与独立两种格式的选择由 kimi-cli 内部决定（取决于 subagent 是通过 wire spawn 还是本地 spawn），`stop.rs` 通过 `has_inline` 检测避免重复：有内联时不扫独立目录

#### 推导链路

- Stop handler 中 `rebuild_hook_log_from_wire` 的 `SubagentEvent` 解析路径：
  1. wire `SubagentEvent` 的 `event` 字段递归传入 `WireParser.feed_typed()`
  2. 每个 parent_tool_call_id 维护独立 `SubagentAccum`（独立 `WireParser` 实例）
  3. `finish()` 时统一 flush 所有 subagent parser 并输出合并后的 `SubagentEvent` 合成事件，含 `nested_events` 数组
- `otel.rs` `build_subagent_spans()`：
  1. 按 `parent_tool_call_id` 分组所有 `SubagentEvent`
  2. 每组所有 `nested_events` flatten 成 `Vec<HookRecord>`
  3. 递归调用 `build_llm_spans` + `build_tool_spans`，parent = 本 subagent span
  4. subagent span 挂到 `tool_id_to_span` 映射的对应 tool span 下（找不到则退化挂在 turn root）

#### 风险与边界

- **孤儿 SubagentEvent**：若 `parent_tool_call_id` 在 tool_id_to_span 找不到（例如 Agent ToolCall 在 wire 重建中被跳过），subagent span 退化挂在 turn root，不丢失但拓扑不精确
- `nested_events` 中的 tool 调用可能也是 `Agent` 类型（subagent 嵌套 subagent），当前实现只递归一层（不会无限递归，因为 subagent 的 tool calls 被 `build_tool_spans` 消费后不再触发新的 `build_subagent_spans`）
- subagent 独立目录的 `meta.json` 是可选的（找不到时 `agent_id` 退化为目录名），不要视为 violation

#### 管控动作

- `agentic.tool.type = subagent` + `agentic.subagent.id` + `agentic.subagent.type` 进入硬约束
- subagent span 的 `gen_ai.operation.name = invoke_agent`（`otel.rs:1360`），对齐 C6 "preferred an independent agent invocation span"
- golden 应覆盖：内联 SubagentEvent / 独立 subagents 目录 / subagent 内嵌 tool/LLM spans

### 数据项 C6.2：Subagent span 的时间边界

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- `SubagentStart` hook 触发时刻（子 agent 开始）
- `SubagentStop` hook 触发时刻（子 agent 结束）
- wire SubagentEvent：`_first_ts` / `_last_ts`（合并组的时间范围）

#### 获取方式

- `otel.rs:1311-1334`：优先 `_first_ts` / `_last_ts`（合并后的 SubagentEvent 有精确范围），退化 `_source_timestamp`

#### 正证据

- `_first_ts` / `_last_ts` 在 `enrich_with_subagent_dirs` 扫描时从 `nested` 首/尾事件提取
- `stop.rs` 中的 `SubagentAccum` 维护 `first_ts` / `last_ts`，每喂一个 event 更新范围

#### 反证据

- 内联 SubagentEvent 的时间范围由每个 event 的 `_source_timestamp` 聚合。如相邻 event 之间有间隙（如 subagent 长时间思考），`_first_ts` / `_last_ts` 只覆盖有 wire 事件的区间

#### 推导链路

- 直接可用，无需推导

#### 风险与边界

- 时间范围不能精确区分"subagent 正在执行但无事件发出"的空窗

#### 管控动作

- 无额外管控动作，按 C1 同层级处理时间精度

---

## C7 子 Agent Trace 跨 Trace 关联

### 结论

暂不支持

### 约束建议

放弃

### 数据来源

- 无（kimi-cli 未在任何数据层面暴露 trace context）

### 获取方式

- 不适用

### 正证据

- （不存在）

### 反证据

- kimi-cli **未集成 OpenTelemetry SDK**：全代码 grep `trace_id|span_id|traceparent|tracestate` 零命中（`packages/kosong` 与 `src/kimi_cli` 均无）
- hook payload 中不含任何 trace context 字段：`events.py:_base()` 仅注入 `session_id` / `cwd` / `hook_event_name`
- `wire.jsonl` metadata 行只有 `protocol_version`（`wire/file.py`），无 trace propagation 字段
- `context.jsonl` 的 kosong Message schema 无 trace context

### 推导链路

- 不存在可推导路径：跨 trace 关联需要父 trace 至少能产生一个关联锚点（trace_id / span_id / correlation_id），kimi 全栈不产生任何此类锚点

### 风险与边界

- 即使未来 Moonshot 在 kimi-cli 中加入 W3C trace context，仍需评估：
  - 子 agent 的 trace context 是否独立或继承父 trace
  - `session_id` 是否能同时映射到父和子的 `gen_ai.conversation.id`
- 当前实现：
  - 每个 turn 生成独立 `trace_id`（`otel.rs:176-178` `new_trace_id()` —— 每次 replay 随机生成）
  - subagent span 挂在父 trace 内的 tool span 下（同一 trace），属于"strong association"模式
  - 若未来需要跨 trace 链接，需在 stop handler 中把 subagent trace 单独构建、并在父 trace 的 subagent span 上创建 span link

### 管控动作

- "暂不支持"是宪章允许的（C7 明确"Agent 无法访问子 trace 时可标记 partial support"）
- `00-总览.md` 已标记 C7 为"放弃"，无需额外变更
- 若子 agent 未来独立暴露 trace context，应在此文档更新"暂不支持→部分支持"