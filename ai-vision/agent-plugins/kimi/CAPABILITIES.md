# kimi 能力评估

← 返回 [CAPABILITIES.md](../CAPABILITIES.md)

## 评分汇总

✅ 16 / 🟡 0 / ❌ 2 / ⚪ 3 （共 21 项）

| 能力 | 评分 | 一句话结论 |
|------|------|-----------|
| A1 多轮会话 + chain | ✅ | 每个 turn 生成唯一 root span（CHAT/invoke_agent），LLM/TOOL/Compaction/api_error 全部作为 sibling 挂在 root 下，chain 拓扑稳定。 |
| B1 工具耗时 | ✅ | 每个 TOOL span 从 wire.jsonl 时间戳计算 `agentic.tool.duration_ms`，非负。 |
| B2 工具错误 | ✅ | 失败时 `status.code=ERROR` + `error.type=tool_error` 同时存在，错误挂自身 span。 |
| B3 工具输入参数 | ✅ | `gen_ai.tool.call.arguments` 序列化原始 tool_use input（JSON 字符串）。 |
| B4 工具输出内容 | ✅ | `gen_ai.tool.call.result` 序列化 tool_result，ContentLogConfig 控制是否填充内容。 |
| B5 MCP 调用识别 | ✅ | `mcp__server__method` 命名前缀检测，TOOL span 携带 `agentic.tool.type=mcp`。 |
| B6 Skill 调用识别 | ✅ | `skill_` 命名前缀检测，TOOL span 携带 `agentic.tool.type=skill`。 |
| C1 LLM 耗时 | ✅ | 每个 generation span 用 wire 时间差计算 `agentic.llm.duration_ms`。 |
| C2 LLM 错误 | ✅ | StepRetry 事件生成独立 CLIENT span，`status.code=ERROR` + `error.type` 全覆盖。 |
| C3 LLM 用户输入 | ✅ | turn root 的 `gen_ai.input.messages` 含 user 消息（末尾），generation span 累积输入。 |
| C4 LLM 系统提示词 | ⚪ | kimi-cli wire.jsonl 不暴露 system prompt，数据源不可获取。 |
| C5 LLM 模型输出 | ✅ | `gen_ai.output.messages` 含 content / tool_calls / finish_reason / reasoning_content。 |
| C6 LLM thinking | ✅ | wire AssistantMessage 的 `reasoning_content` 写入 output 消息，与 content 严格分离。 |
| C7 LLM Token usage | ✅ | 每个 LLM span 写 `gen_ai.usage.input_tokens` / `output_tokens`；wire 提供累计值，代码做差分。 |
| D1 Compact | ✅ | 独立 TOOL span（tool.name=compaction），`agentic.compaction.{trigger,tokens.before,tokens.after,tokens.saved}` 完整。 |
| D2 同步子 agent | ✅ | SubagentStart/Stop 识别为 `agentic.tool.type=subagent`，独立 CHAT span 携带 `agentic.subagent.*`。 |
| D3 异步子 agent | ⚪ | kimi-cli 不支持异步子 agent 模式，数据源不可获取。 |
| D4 Cancel/Retry | ✅ | StepRetry → 独立 `chat api_error` CLIENT span（attempt/delay/status_code/error.type），多 retry 不压平。 |
| E1 Git 仓库信息 | ❌ | hook payload 有 cwd，但当前 Kimi 未采集或上报 Git remote/branch/revision。 |
| E2 上游 TRACEPARENT 串联 | ⚪ | Kimi CLI/wire 没有 W3C upstream trace context source。 |
| E3 非真实用户 / 外部触发归因识别 | ❌ | 当前未实现 外部触发归因规范要求的 `AIVISION_META_TRIGGER` / `meta.trigger`、`ant.meta.trigger` 与 `agentic.turn.trigger`。 | [E3](capabilities/E3.md) |

## 详细评估

### E2 上游调用 TRACEPARENT 串联 {#e2}

- **评分**: ⚪
- **结论**: 见 [独立证据](capabilities/E2.md)：Kimi CLI/wire contract 不暴露 W3C upstream context。

### E1 Git 仓库信息识别 {#e1}

- **评分**: ❌
- **结论**: 见 [独立证据](capabilities/E1.md)：Kimi hook 已暴露 cwd，但当前没有 Git collector、session VCS state 或 Resource 注入。

### A1 多轮会话识别 + chain 拓扑 {#a1}

- **评分**: ✅
- **结论**: wire.jsonl 中每个 TurnStart→TurnEnd 边界形成一个 turn，生成唯一 root span（CHAT/invoke_agent）；LLM / TOOL / Compaction / api_error 全部作为 sibling 挂在 root 下，chain 拓扑稳定。多 turn 时每个 turn 独立 trace_id。
- **代码引证**:
  - `kimi/kimihooks/src/common/otel.rs:1935` — `build_root_span_numbered` 写入 `gen_ai.span.kind=CHAT` + `gen_ai.operation.name=invoke_agent` + `gen_ai.conversation.id` + `agentic.turn.number`
  - `kimi/kimihooks/src/common/otel.rs:2032` — root span 属性 `gen_ai.span.kind=CHAT`
  - `kimi/kimihooks/src/common/otel.rs:1833-1885` — `build_single_turn` 把 tool/generation/compaction/error spans 的 `parent_span_id = root_span_id`（全部 sibling）
  - `kimi/kimihooks/src/hooks/stop.rs:450` — `rebuild_hook_log_from_wire` 按 TurnStart/TurnEnd 切分 turn 边界
- **Replay 例子**:
  - `~/.aivision/kimi/replays/spans_wire_basic.jsonl` — `Turn 1`(CHAT) → `chat unknown`(LLM)，共 2 spans，同 parent
  - `~/.aivision/kimi/replays/spans_wire_tools.jsonl` — `Turn 1`(CHAT) → 5 LLM + 4 TOOL，10 spans 全部 sibling
  - `~/.aivision/kimi/replays/spans_wire_advanced.jsonl` — 2 个 CHAT root（Turn 1 + subagent），含 LLM/TOOL/CLIENT 混合

### B1 工具调用精确耗时 {#b1}

- **评分**: ✅
- **结论**: 每个 TOOL span 从 wire.jsonl 的 ToolUseBegin/ToolUseEnd 时间戳对计算非负 `agentic.tool.duration_ms`。
- **代码引证**:
  - `kimi/kimihooks/src/common/otel.rs:279` — `OtelAttribute::int_as_string("agentic.tool.duration_ms", duration_ms as i64)`
  - `kimi/kimihooks/src/common/otel.rs:75-310` — `build_tool_spans` 使用 start/end 时间戳对计算 duration
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_tools.jsonl` — `tool Read` 携带 `agentic.tool.duration_ms=600`，MCP 工具 span 携带 `agentic.tool.duration_ms=1000`。

### B2 工具调用错误识别 {#b2}

- **评分**: ✅
- **结论**: 工具失败时同时设置 `status.code=ERROR` + `error.type=tool_error`，错误挂在工具自身 span。另有 `error.type=terminated` 用于未配对的 ToolUseBegin（无 ToolUseEnd）。
- **代码引证**:
  - `kimi/kimihooks/src/common/otel.rs:304` — `OtelAttribute::string("error.type", "tool_error")` + OtelStatus::error
  - `kimi/kimihooks/src/common/otel.rs:310` — `OtelAttribute::string("error.type", "terminated")` 用于 orphan tool
  - `kimi/kimihooks/src/common/otel.rs:530` — tool event 中同样标记 `error.type=tool_error`
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_tools.jsonl` 中所有工具均成功（status=OK）；错误路径由 `kimi/kimihooks/src/common/otel.rs` 单测覆盖（`test_build_tool_spans_*`）。

### B3 工具调用输入参数识别 {#b3}

- **评分**: ✅
- **结论**: 每个 TOOL span 携带 `gen_ai.tool.call.arguments`（序列化 JSON 字符串），来自 wire ToolUseBegin 的 input 字段。
- **代码引证**:
  - `kimi/kimihooks/src/common/otel.rs:268` — `OtelAttribute::string("gen_ai.tool.call.arguments", ...)`
  - `kimi/kimihooks/src/common/otel.rs:613, 631` — tool event 中同样写入 arguments
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_tools.jsonl` — `tool Read` 携带 `gen_ai.tool.call.arguments="{\"file_path\":\"/tmp/test/main.rs\"}"` 完整原始参数。

### B4 工具调用输出内容识别 {#b4}

- **评分**: ✅
- **结论**: `gen_ai.tool.call.result` 序列化 tool_result 输出；ContentLogConfig 控制是否填充实际内容，禁用时写空字符串（属性始终存在）。
- **代码引证**:
  - `kimi/kimihooks/src/common/otel.rs:288-302` — 内容开关：启用写 `out_str`，禁用写空字符串 `""`
  - `kimi/kimihooks/src/common/otel.rs:308` — fallback 路径（无 output）也写空 `gen_ai.tool.call.result`
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_tools.jsonl` — TOOL span 均携带 `gen_ai.tool.call.result` 属性。

### B5 MCP 调用识别 {#b5}

- **评分**: ✅
- **结论**: wire 中 `tool_name` 以 `mcp__` 开头时识别为 MCP 调用，TOOL span 携带 `agentic.tool.type=mcp`；span name 转换为 `mcp {server}.{method}` 格式。
- **代码引证**:
  - `kimi/kimihooks/src/hooks/stop.rs` — wire 重建时保留原始 `tool_name`（含 `mcp__` 前缀）
  - `kimi/kimihooks/src/common/otel.rs:234-254` — `build_tool_spans` 中 `ToolCallInfo` 解析命名前缀，MCP 分支设置 `tool_type="mcp"`
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_tools.jsonl` — MCP 工具 span 携带 `agentic.tool.type=mcp` + `agentic.tool.duration_ms=1000`。

### B6 Skill 调用识别 {#b6}

- **评分**: ✅
- **结论**: wire 中 `tool_name` 以 `skill_` 开头时识别为 Skill 调用，TOOL span 携带 `agentic.tool.type=skill`。
- **代码引证**:
  - `kimi/kimihooks/src/common/otel.rs:234-254` — `ToolCallInfo` 解析 `skill_` 前缀，设置 `tool_type="skill"`
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_tools.jsonl` — `skill unknown` span 携带 `agentic.tool.type=skill` + `agentic.tool.duration_ms=700`。

### C1 LLM 精确耗时 {#c1}

- **评分**: ✅
- **结论**: 每个 generation span 用 wire StepStart/StepEnd 时间戳对计算 `agentic.llm.duration_ms`，非负。
- **代码引证**:
  - `kimi/kimihooks/src/common/otel.rs:2083-2260` — `build_generation_spans`（内联在 `build_single_turn`）使用 start/end 时间戳
  - `kimi/kimihooks/src/common/otel.rs:2186` — LLM span 属性 `gen_ai.span.kind=LLM`
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_basic.jsonl` — `chat unknown` LLM span 的 start/end nano 时间差对应正确 duration。

### C2 LLM 错误识别 {#c2}

- **评分**: ✅
- **结论**: wire StepRetry 事件生成独立 `chat api_error` CLIENT span，携带 `status.code=ERROR` + `error.type`（如 `rate_limit_error`）+ retry 元数据（attempt / max_retries / delay_ms / status_code）。多次 retry 各自独立 span，不压平。
- **代码引证**:
  - `kimi/kimihooks/src/hooks/stop.rs:978` — `"StepRetry"` 分支：从 wire 合成 ApiRetry 事件
  - `kimi/kimihooks/src/common/otel.rs:2283` — `build_api_error_spans` 为每个 retry 生成 CLIENT span
  - `kimi/kimihooks/src/common/otel.rs:2312-2332` — 写入 `gen_ai.span.kind=CLIENT` + `error.type` + `retry.*` 属性 + `OtelStatus::error`
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_advanced.jsonl` — `chat api_error` CLIENT span 携带 `error.type=rate_limit_error`，status=ERROR。

### C3 LLM 用户输入内容识别 {#c3}

- **评分**: ✅
- **结论**: turn root span 的 `gen_ai.input.messages` 携带 user 消息（末尾），generation span 携带累积输入（user 在头部）。
- **代码引证**:
  - `kimi/kimihooks/src/common/otel.rs:2040` — root span 写 `gen_ai.input.messages`
  - `kimi/kimihooks/src/common/otel.rs:2194` — generation span 写 `gen_ai.input.messages`
  - `kimi/kimihooks/src/common/otel.rs:1935-2080` — `build_root_span_numbered` 从 records 中提取 user 消息构建 input
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_basic.jsonl` — Turn root 携带 `gen_ai.input.messages` 含 user 输入文本。

### C4 LLM 系统提示词识别 {#c4}

- **评分**: ⚪
- **结论**: kimi-cli 的 wire.jsonl 不暴露 system prompt（系统指令在 CLI 内部组装后直接发给模型 API，不写入 wire 事件流），数据源根本不可获取。
- **代码引证**: `grep -rn "system_instructions\|system_prompt\|SystemPrompt" kimi/kimihooks/src/` 0 命中。
- **数据源缺失根因**: kimi-cli wire.jsonl 仅记录 TurnStart/TurnEnd/StepStart/StepEnd/ToolUseBegin/ToolUseEnd/CompactionBegin/CompactionEnd/StepRetry/SubagentStart/SubagentStop 事件，不含请求体中的 system prompt。要获取需改为 LLM 请求层拦截，当前 Stop hook + wire 重建架构无法实现。

### C5 LLM 模型输出识别 {#c5}

- **评分**: ✅
- **结论**: turn root 和 generation span 均携带 `gen_ai.output.messages`，包含 `content` / `tool_calls` / `finish_reason` / `reasoning_content`（如有）。
- **代码引证**:
  - `kimi/kimihooks/src/common/otel.rs:2044` — root span 写 `gen_ai.output.messages`
  - `kimi/kimihooks/src/common/otel.rs:2197` — generation span 写 `gen_ai.output.messages`
  - `kimi/kimihooks/src/common/otel.rs:2025, 2148` — reasoning_content 字段注入 output message
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_basic.jsonl` — LLM span 携带 `gen_ai.output.messages` 含 content + finish_reason。

### C6 LLM thinking 内容识别 {#c6}

- **评分**: ✅
- **结论**: wire AssistantMessage 中的 `reasoning_content` 字段解析后写入 output 消息的 `reasoning_content` key，与 `content` 严格分离。
- **代码引证**:
  - `kimi/kimihooks/src/common/otel.rs:2025` — root span 路径：`m.reasoning_content = Some(thinking.to_string())`
  - `kimi/kimihooks/src/common/otel.rs:2148` — generation span 路径：同上
  - `kimi/kimihooks/src/hooks/stop.rs` — wire 重建时保留 AssistantMessage 的 `reasoning_content` 字段
- **Replay 例子**: wire_basic 的 fixture 含 reasoning_content 时，LLM span 输出消息中 `reasoning_content` 与 `content` 分离呈现。

### C7 LLM Token usage 识别 {#c7}

- **评分**: ✅
- **结论**: kimi-cli wire 中 StepEnd 提供**累计** usage，代码做差分后映射到当前 LLM span 的 `gen_ai.usage.input_tokens` / `output_tokens`。Turn root span 不重复上报 usage。
- **代码引证**:
  - `kimi/kimihooks/src/common/otel.rs:2210` — LLM span 写 `gen_ai.usage.input_tokens`
  - `kimi/kimihooks/src/common/otel.rs:2214` — LLM span 写 `gen_ai.usage.output_tokens`
  - `kimi/kimihooks/src/common/otel.rs:2219-2248` — cache_read / cache_creation / total_tokens 扩展属性
  - `kimi/kimihooks/src/hooks/stop.rs` — wire 重建时对累计 usage 做差分（当前 - 前次），确保每个 span 对应单次调用增量
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_advanced.jsonl` — LLM spans 携带 `gen_ai.usage.input_tokens=2000/4500/5200` 等差分后的单次值。

### D1 Compact 识别 {#d1}

- **评分**: ✅
- **结论**: wire CompactionBegin/CompactionEnd 配对合并为单条 Compaction 事件，生成独立 TOOL span（tool.name=compaction）；`agentic.compaction.{trigger,tokens.before,tokens.after,tokens.saved}` 齐全。
- **代码引证**:
  - `kimi/kimihooks/src/hooks/stop.rs:1017-1050` — wire 重建：CompactionBegin 暂存 → CompactionEnd 配对合并
  - `kimi/kimihooks/src/common/otel.rs:2344` — `build_compaction_spans` 生成 TOOL span
  - `kimi/kimihooks/src/common/otel.rs:2377-2398` — 属性：`gen_ai.span.kind=TOOL` + `agentic.tool.type=builtin` + `agentic.compaction.tokens.before/after/saved` + `trigger`
  - `kimi/kimihooks/src/common/otel.rs:2434` — `build_compaction_events` 生成对应 log event
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_advanced.jsonl` — `compaction` TOOL span 携带 `agentic.tool.type=builtin` + `agentic.tool.duration_ms=1500`。

### D2 同步子 agent 调用识别 {#d2}

- **评分**: ✅
- **结论**: wire SubagentStart/SubagentStop 事件 + state.json 缓存识别同步子 agent，生成独立 CHAT span 携带 `agentic.tool.type=subagent` + `agentic.subagent.*` 属性。
- **代码引证**:
  - `kimi/kimihooks/src/hooks/stop.rs:242-270` — state.json 中 SubagentStart/SubagentStop 条目注入 wire 重建流
  - `kimi/kimihooks/src/common/otel.rs:2529` — `build_subagent_spans` 入口
  - `kimi/kimihooks/src/common/otel.rs:2637` — `agentic.tool.type=subagent`
  - `kimi/kimihooks/src/common/otel.rs:2630-2653` — span 属性：`gen_ai.span.kind=CHAT` + `agentic.subagent.{id,type,parent_tool_call_id}` + `ant.agent.scene=default`
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_advanced.jsonl` — `subagent code-reviewer` CHAT span 携带 `agentic.tool.type=subagent` + `ant.agent.scene=default`。

### D3 异步子 agent 调用识别 {#d3}

- **评分**: ⚪
- **结论**: kimi-cli 当前不支持异步子 agent 模式（无 `run_in_background` 语义），wire.jsonl 中无对应事件类型，数据源根本不可获取。
- **代码引证**: `grep -rn "async.*subagent\|run_in_background\|background.*agent" kimi/kimihooks/src/` 0 命中。
- **数据源缺失根因**: kimi-cli 的子 agent 调用均为同步阻塞模式（SubagentStart → 等待 → SubagentStop），不存在异步分叉后独立 trace 上报的场景。

### D4 Cancel / Retry 错误恢复识别 {#d4}

- **评分**: ✅
- **结论**: wire StepRetry 事件每次生成独立 `chat api_error` CLIENT span，携带 `error.type` + `retry.attempt` + `retry.max_retries` + `retry.delay_ms` + `retry.status_code` + `error.message`，`status.code=ERROR`。多次 retry 各自独立 span，不压平。
- **代码引证**:
  - `kimi/kimihooks/src/hooks/stop.rs:978` — `"StepRetry"` 匹配分支，从 wire payload 合成 ApiRetry 事件
  - `kimi/kimihooks/src/common/otel.rs:2283` — `build_api_error_spans` 入口
  - `kimi/kimihooks/src/common/otel.rs:2301-2332` — 解析 error_type / status_code / retry_attempt / retry_max / retry_delay_ms / error_message，写入 CLIENT span 属性 + `OtelStatus::error`
- **Replay 例子**: `~/.aivision/kimi/replays/spans_wire_advanced.jsonl` — `chat api_error` CLIENT span：`error.type=rate_limit_error`，独立于 LLM/TOOL sibling 序列。

## L0-L3 合规等级自评

参考 [CONSTITUTION §3.7](../CONSTITUTION.md) 定义的合规等级阶梯。

| 等级 | 状态 | 说明 |
|------|:----:|------|
| L0 基础存在性 | ✅ | Resource 4 项齐全（ant.agent.env / ant.agent.scene / ant.agent.type / ant.username span-only）；replay 产出 traces + logs + metrics 三信号 |
| L1 字段完整性 | ✅ | C7/C3/C5/B1/C1/B3/B4 全部字段正确；C2 通过 CLIENT span 实现完整错误双标记 |
| L2 时序与准确性 | ✅ | 累计 usage 做差分、时间戳对齐、同级 span 不重叠；weaver-guard 0 violations |
| L3 全量合规 + Golden | 🟡 | 3 个 wire fixture 覆盖全部 15/18 可实现能力；Golden baseline 待 CI 持续验证 |

**当前等级**：L2（卡在 L3 的 Golden CI 自动回归）

**验证结果**：`make validate` → **0 VIOLATIONS, 4 IMPROVEMENTS, 27 SEMCONV ISSUES (PASS)**

## 单项能力证据索引

- [E1](capabilities/E1.md)
- [E2](capabilities/E2.md)
- [E3](capabilities/E3.md)
- [A1](capabilities/A1.md)
- [B1](capabilities/B1.md)
- [B2](capabilities/B2.md)
- [B3](capabilities/B3.md)
- [B4](capabilities/B4.md)
- [B5](capabilities/B5.md)
- [B6](capabilities/B6.md)
- [C1](capabilities/C1.md)
- [C2](capabilities/C2.md)
- [C3](capabilities/C3.md)
- [C4](capabilities/C4.md)
- [C5](capabilities/C5.md)
- [C6](capabilities/C6.md)
- [C7](capabilities/C7.md)
- [D1](capabilities/D1.md)
- [D2](capabilities/D2.md)
- [D3](capabilities/D3.md)
- [D4](capabilities/D4.md)
