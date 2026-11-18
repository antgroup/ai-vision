# B 组：工具调用语义

← 返回 [CAPABILITIES.md](../../CAPABILITIES.md)

本组覆盖 6 项能力，关注**单次工具调用**的最小闭环：耗时、错误、输入参数、输出内容、MCP 类型识别、Skill 类型识别。并发等横切语义作为细则收纳在每项能力下。

## B1 工具调用精确耗时

### 定义

每次真实发生的工具调用对应一个 TOOL span，该 span 表达**单次调用的真实起止时间**，duration 非负。

它不是工具决策耗时、不是聚合统计时间，也不是流式片段拼接时间。

### 必要属性 / Span 字段

- `gen_ai.span.kind = TOOL`
- `gen_ai.operation.name = execute_tool`
- `agentic.tool.duration_ms`（非负）
- 若数据源可得，span 的 start_time / end_time 反映真实调用边界

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | 每个工具调用都有独立 TOOL span，duration_ms 非负且与 start/end 时间一致 |
| 🟡 部分 | duration 存在但精度不够（如只有秒级粒度），或部分调用缺时间边界，或多次调用被合并成一个 span |
| ❌ 未实现 | hooks/transcript 中存在 tool start/end 时间字段，实现没采集 |
| ⚪ 不适用 | （B1 不适用 ⚪ —— 任意运行过的工具都有时间） |

### 机器验证方式

- [Tool span Rego 规则](../../weaver/policies/aivision/traces/tool/violations_forbidden.rego)检查 `agentic.tool.duration_ms` 是否为负值。
- 已纳入 M3 阶段

### 关联旧条款

- C3（Tool 调用、错误与耗时语义）

### 当前实现取向

每个工具完成事件 ↔ 一个 TOOL span，时间边界优先用原始事件时间，缺失时使用安全退化（不伪造）。

### cchooks 落地

- **代码**：`claudecode/cchooks/src/common/otel.rs:421-428` — `duration_ms = (end_u128 - start_u128) / 1_000_000`，end 来自 result_ts，start 来自 assistant_ts_ns
- **Replay 例子**：`~/.aivision/cc/replays/spans_session_skill_mcp_tool.jsonl` — `tool AskUserQuestion` 携带 `agentic.tool.duration_ms` 与匹配的 start/end nano
- **关键设计决策**：用真实 transcript 时间戳做 ns 级精度；缺失时退化到 now_ns 不伪造

---

## B2 工具调用错误识别

### 定义

工具调用失败时，能在 trace 中区分"成功 / 失败"，并通过标准状态与错误属性同时表达。

它要求错误首先挂在**实际失败的 TOOL span 自身**，而不是冒泡到 root span 或拆出"错误 span"。

### 必要属性 / Span 字段

失败时 TOOL span 必须具备：

- `status.code = ERROR`
- `error.type`
- 若有人可读错误描述：`status.message` 或 `error.message`

成功与失败统一在同一 TOOL span 语义下，不拆分成"正常 span"和"错误 span"两套。

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | 工具失败时 status.code=ERROR + error.type 同时存在；error.message 在可读时一并记录；错误挂在自身 span 不冒泡 |
| 🟡 部分 | 仅记录 `success=false` 这类简化字段，或仅有 status.code 没有 error.type，或错误状态被错误地复制到父 span |
| ❌ 未实现 | hooks/transcript 中存在工具失败标识或退出码，实现未上报 status/error 属性 |
| ⚪ 不适用 | （B2 不适用 ⚪ —— 任意工具都可能失败） |

### 机器验证方式

- [Tool span Rego 规则](../../weaver/policies/aivision/traces/tool/improvements_recommended.rego)提示 `status.code=ERROR` 时缺失的 `error.type`。
- 已纳入 M3 阶段

### 关联旧条款

- C3（Tool 调用、错误与耗时语义）
- C14（Cancel / Retry 语义中关于错误冒泡的部分见 D4）

### cchooks 落地

- **代码**：`claudecode/cchooks/src/common/otel.rs:33-62` — `detect_tool_error` 三路检测（is_error=true / error 字段 / Error: 前缀）
- **Replay 例子**：当前 28 个 replay 样本未触发 tool error；代码路径已被 `claudecode/cchooks/src/common/otel.rs` 中 `test_detect_tool_error*` 单测覆盖
- **关键设计决策**：错误统一打 `status.code=ERROR + error.type=tool_error + status.message`，挂在自身 span 不冒泡

---

## B3 工具调用输入参数识别

### 定义

每次工具调用的 TOOL span 必须携带 **真实输入参数**，而不是空字段、占位字符串或推测重建的近似值。

参数应为**结构化** JSON（与原始 tool_use 调用形态一致），而不是简单的字符串拼接。

### 必要属性 / Span 字段

- `gen_ai.tool.call.arguments`（JSON-serialized 字符串）
- 若工具身份可得：`gen_ai.tool.name`、`gen_ai.tool.call.id`

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | 每个 TOOL span 都有 arguments；序列化为 JSON 字符串；内容反映真实输入参数 |
| 🟡 部分 | arguments 存在但被过度截断、被脱敏成不可还原、或部分场景下被回退为空字符串；或 MCP / Skill 等特殊工具拿不到 arguments |
| ❌ 未实现 | hooks/transcript 中可见工具调用参数，实现未上报或仅写入 `{}` 占位 |
| ⚪ 不适用 | 工具调用本身没有输入参数（极少见，如某些 system 触发） |

### 机器验证方式

- weaver-guard 校验 `gen_ai.tool.call.arguments` 字段以 `{` 或 `[` 开头（不是空字符串）
- 当前 Rego 状态：**待补**。

### 关联旧条款

- C3（Tool 调用、错误与耗时语义）
- C9（Input / Output Messages 语义中关于 tool 参数的部分）
- C15（脱敏与截断）—— 截断 / 脱敏时必须显式标记，不得静默丢失

### 细则：MCP 工具的输入参数

MCP 调用是工具调用的特化场景。其 arguments 应保留协议层原始参数，并配合：

- `gen_ai.tool.name`（保留运行时原始值）
- `agentic.tool.type = mcp`
- `agentic.tool.name`（解析后的逻辑名）
- `agentic.tool.id`（调用稳定标识）

### 细则：Skill 工具的输入参数

Skill 调用同样属于工具调用。其 arguments 反映 skill 的真实启动参数，并配合：

- `agentic.tool.type = skill`
- `agentic.tool.name`（skill 逻辑名）
- `agentic.tool.id`（优先真实 ID，无则允许合成）

### cchooks 落地

- **代码**：`claudecode/cchooks/src/common/otel.rs:409-419, 454, 510` — `truncate_value(&tool_use.input)` 后序列化为 JSON 字符串
- **Replay 例子**：`~/.aivision/cc/replays/spans_session_skill_mcp_tool.jsonl` — `mcp plugin_oh-my-claudecode_t.state_write` 携带原始 arguments JSON
- **关键设计决策**：三态语义（真实 JSON / `agentic.tool.input.missing=true` 占位 / truncated 标记），不写空字符串冒充

---

## B4 工具调用输出内容识别

### 定义

每次工具调用的 TOOL span 必须携带 **真实输出结果**，反映该次调用的实际返回值。

成功的输出与失败的错误信息都属于"输出"范畴，**统一**记录在同一 span 上。

### 必要属性 / Span 字段

- `gen_ai.tool.call.result`（JSON-serialized 字符串或文本）
- 失败场景下，`error.type` / `error.message` 与 `gen_ai.tool.call.result` 可同时存在

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | 每个 TOOL span 都有 result；内容反映真实输出；截断或脱敏时显式标记（如 `agentic.tool.output.truncated`） |
| 🟡 部分 | result 存在但部分场景缺失（如 MCP）、被静默截断、或仅记录摘要而非真实输出 |
| ❌ 未实现 | hooks/transcript 中可见工具结果，实现未上报或退化为空字符串 |
| ⚪ 不适用 | 工具调用本身无返回（罕见，如 fire-and-forget） |

### 机器验证方式

- weaver-guard 校验 `gen_ai.tool.call.result` 字段非空
- 当前 Rego 状态：**待补**。

### 关联旧条款

- C3（Tool 调用、错误与耗时语义）
- C9（Input / Output Messages 语义）
- C15（脱敏、截断与缺失值语义）

### 细则：截断与缺失

- 静默截断（看似完整但其实被切掉）必须避免
- 已截断时，应在 span 上显式标记 `agentic.tool.output.truncated = true`
- 缺失（拿不到）≠ 空字符串（人为留空）—— 必须区分

### 细则：并发工具调用的输出

并发触发的多个工具调用在 trace 中保持为**同级 sibling**，每个 span 独立记录自己的 result，不合并成一个批处理 span。共享分组语义可用 `agentic.tool.parallel_group`（待 semconv 注册前为推荐项）。

### cchooks 落地

- **代码**：`claudecode/cchooks/src/common/otel.rs:455, 511, 638` — 三条构造路径都写入 `gen_ai.tool.call.result`
- **Replay 例子**：`~/.aivision/cc/replays/spans_too_long_message.jsonl` — TOOL span 携带 `agentic.tool.output.truncated=true` + `original_length`
- **关键设计决策**：截断时显式标记元数据；三态显式区分（截断/缺失/脱敏）

---

## B5 MCP 调用识别

### 定义

工具调用为 **MCP（Model Context Protocol）** 类型时，能在 TOOL span 上稳定识别并标记，与 builtin / skill / subagent 区分。

它要求实现能区分"协议层 MCP 调用"与"普通工具调用"，并保留协议层原始命名（如 `mcp__server__method`）。

### 必要属性 / Span 字段

MCP 工具的 TOOL span 在保留 B1-B4 基础闭环之外，必须额外具备：

- `agentic.tool.type = mcp`
- `gen_ai.tool.name`（保留运行时原始值，含 `mcp__` 前缀或框架原始名）
- `agentic.tool.name`（解析后的逻辑名，去除前缀）
- `agentic.tool.id`（调用稳定标识）

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | 每个可识别为 MCP 的工具调用都打 `agentic.tool.type=mcp`；`gen_ai.tool.name` 与 `agentic.tool.name` 角色分离；replay 中能明确看到 MCP 工具被分类 |
| 🟡 部分 | 能打 `type=mcp` 但缺 `name` / `id`；或 `name` 解析不准确；或部分 MCP 工具未被识别 |
| ❌ 未实现 | hooks/transcript 中可见 `mcp__` 前缀或 MCP 协议事件，实现未把 `agentic.tool.type=mcp` 写到 span |
| ⚪ 不适用 | Agent 不支持 MCP 工具调用 |

### 机器验证方式

- weaver-guard 校验存在 `agentic.tool.type=mcp` 的 TOOL span
- 当前 Rego 状态：**待补**

### 关联旧条款

- C4（MCP 工具调用特殊语义）

### 各 agent 评估关注点

- MCP 工具是如何被识别的（命名前缀 / 框架内建工具白名单 / 协议事件）
- 原始工具名从哪里获得
- 逻辑名称如何从原始名解析
- 调用 ID 是否稳定可得
- 是否存在无 `mcp__` 前缀但本质为 MCP 的工具，分类依据是什么

### cchooks 落地

- **代码**：`claudecode/cchooks/src/common/otel.rs:517` — `append_agentic_tool_attrs_typed` 写入 `agentic.tool.{type,name,id}`，type=mcp 由命名前缀触发
- **Replay 例子**：`~/.aivision/cc/replays/spans_session_skill_mcp_tool.jsonl` — `mcp plugin_oh-my-claudecode_t.state_write` 携带 `agentic.tool.type=mcp` + 解析后逻辑名
- **关键设计决策**：`mcp__<server>__<method>` 命名空间解析为 type=mcp + 解析后 name + 调用 ID

---

## B6 Skill 调用识别

### 定义

工具调用为 **Skill / Command-style 扩展能力** 类型时，能在 TOOL span 上稳定识别并标记，与 builtin / mcp / subagent 区分。

它要求实现能区分"skill 启动"与"普通工具调用"，包括命令型 skill（如 slash command）和工具型 skill。若运行时没有原生 Skill hook，但能从成功完成的通用工具调用精确证明 Skill 指令已加载，可以额外发出下述派生加载观察；该观察不是第二次物理工具执行。

### 必要属性 / Span 字段

Skill 工具的 TOOL span 在保留 B1-B4 基础闭环之外，必须额外具备：

- `agentic.tool.type = skill`
- `gen_ai.tool.name`（原生调用保留运行时原始值；派生加载使用 `Skill`，来源名写入 `agentic.tool.raw_name`）
- `agentic.tool.name`（skill 逻辑名，如 `code-review`、`commit`）
- `agentic.tool.id`（优先真实调用 ID；若无可用合成 ID）

原生 Skill 调用继续完整遵循 B1-B4。派生 Skill 加载观察是 B6 的显式例外，必须同时满足：

- 仅在来源工具成功完成且路径证据精确、fail-closed 时产生；失败或歧义不产生
- 与来源 span 同属一个 Turn、在来源结束后开始，并通过 OTel Link 指向来源
- `agentic.tool.derived = true`，`agentic.tool.duration_ms = 0`
- `gen_ai.tool.call.id` 使用独立的 synthetic ID，不复用来源物理调用 ID
- `agentic.tool.id` 仍遵循 Skill 的真实来源 ID 优先规则；OTel Link 是来源关联的主机制
- 不伪装成原生 Skill payload；full 模式可记录规范化派生语义 `{"skill":"<name>"}` / `{"status":"loaded"}`，privacy 模式写显式 `[REDACTED]`
- 不产生第二个 `tool_result`，不增加 `agent.tool_call.*` datapoint

因此 B1-B4 仍只评价真实物理调用；派生加载观察只按本节规则评价，不能用来补足 B1-B4 能力证据。

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | 每个可识别为 skill 的原生调用都打 `agentic.tool.type=skill`；逻辑名解析正确；若实现派生加载观察，则满足独立 ID、derived 标记、Link、零耗时、无推测 payload 和无重复 event/metrics 的全部约束 |
| 🟡 部分 | 能打 `type=skill` 但缺 `name` / `id`；或仅识别命令型 skill 漏识别工具型；或 ID 合成规则不稳定 |
| ❌ 未实现 | hooks/transcript 中可见 skill 调用证据（结构化 command placeholder / 精确路由消息 / 命令路径 / Skill 工具调用），实现未把 `agentic.tool.type=skill` 写到 span |
| ⚪ 不适用 | Agent 不支持 skill / command-style 扩展能力 |

### 机器验证方式

- weaver-guard 校验存在 `agentic.tool.type=skill` 的 TOOL span（仅在该能力 ✅/🟡 时校验）
- 当前 Rego 状态：**待补**

### 关联旧条款

- C5（Skill 工具调用特殊语义）

### 各 agent 评估关注点

- skill 调用是如何被识别的（原生工具名 / 结构化 user-message placeholder / 精确路由消息 / legacy 命令路径）
- skill 逻辑名从哪里获得
- skill 调用 ID 是否原生可得；若不可得，合成规则是什么
- 是否存在命令型 skill 与工具型 skill 两套来源
- 已加载 skill definition 是否只作为元数据，避免把未调用的定义误报为 span

### Codex transcript v2 约束

- `event_msg.payload.type=user_message` 的 `text_elements[*].placeholder="$name"` 是命令型 skill 的主要调用证据，必须按 UTF-8 `byte_range` 精确验证。
- 精确 OMX UserPromptSubmit developer 路由消息只补强同名 occurrence；没有 placeholder 时才创建一个隐式 occurrence。
- user-role `<skill><name>...<path>...</skill>` 仅表示已加载定义，可补充同名元数据，但绝不能独立激活。
- 命令型 invocation 生成独立的 `skill <name>` TOOL span，作为 turn root 的 sibling child；内部 builtin / MCP / subagent span 不重分类、不重挂父级。
- 同名原生 `Skill` call 按 occurrence 一对一消费；同名命令路径 fallback 被抑制，异名 legacy skill 仍保留。
- 无独立 skill-end 信号时，时长使用 turn-bounded 窗口并明确保持在 root 内；合成 ID 不依赖输出或结束时间，确保 progressive replay 稳定。

### cchooks 落地

- **代码**：`claudecode/cchooks/src/common/otel.rs:454, 510` — Skill 与普通 TOOL 都走 `gen_ai.tool.call.arguments`，并由 `append_agentic_tool_attrs_typed` 打 type=skill
- **Replay 例子**：`~/.aivision/cc/replays/spans_session_skill_mcp_tool.jsonl` — Skill 类 TOOL span 携带 `agentic.tool.type=skill` + skill 逻辑名
- **关键设计决策**：Skill 工具走专门构造路径；与 builtin/mcp/subagent 区分
