# opencode 能力评估

← 返回 [CAPABILITIES.md](../CAPABILITIES.md)

## 评分汇总

✅ 12 / 🟡 8 / ❌ 0 / ⚪ 1 （共 21 项）

当前结论以现行源码及所链接的回归为准。下文早期私有 replay 是历史样本，不等于新版宿主、真实供应商或平台/UI 验收。OpenCode 1.18.34 的原生 fake-provider 校验范围见 [C7](capabilities/C7.md)。

| 能力 | 评分 | 一句话结论 |
|------|------|-----------|
| [A1 多轮会话识别 + chain 拓扑](capabilities/A1.md) | ✅ | 每轮独立 trace，根 CHAT span 下 LLM/Tool/Compaction 全部为 sibling，不嵌套 |
| [B1 工具调用精确耗时](capabilities/B1.md) | ✅ | 每个 TOOL span 都有 `agentic.tool.duration_ms`，并用 message.part.updated 的历史时间精确闭合 |
| [B2 工具调用错误识别](capabilities/B2.md) | ✅ | 失败工具同时具备 `status.code=ERROR` + `error.type`（tool_error/invalid_tool/exit_code），错误挂在自身 span |
| [B3 工具调用输入参数识别](capabilities/B3.md) | ✅ | `gen_ai.tool.call.arguments` 为结构化 JSON，三态语义（real / `{}` / `[REDACTED]`） |
| [B4 工具调用输出内容识别](capabilities/B4.md) | ✅ | `gen_ai.tool.call.result` 真实输出，截断时显式标记 `agentic.tool.output.truncated` + `original_length` |
| [B5 MCP 调用识别](capabilities/B5.md) | ✅ | MCP 工具调用通过 `classifyTool` 打 `agentic.tool.type=mcp` + 解析后逻辑名（如 `oh-my-claudecode_t_notepad_read`） |
| [B6 Skill 调用识别](capabilities/B6.md) | 🟡 | command before/executed 已关联 Skill span；原生 skill 场景尚需验收 |
| [C1 LLM 精确耗时](capabilities/C1.md) | ✅ | LLM span 有 `agentic.llm.duration_ms`，正常路径精确（如 9954ms），来源于 assistantMsg.time.completed-created |
| [C2 LLM 错误识别](capabilities/C2.md) | ✅ | LLM span 同时挂 `status.code=ERROR` + `error.type=401`，并额外创建独立的 `api_error` CLIENT span 作为 sibling |
| [C3 LLM 用户输入内容识别](capabilities/C3.md) | ✅ | turn 保留用户入口，generation 的 `gen_ai.input.messages` 按真实模型调用记录增量；between-turn compaction 后由新 Turn 自然重置 cursor |
| [C4 LLM 系统提示词识别](capabilities/C4.md) | 🟡 | `opencode-replay` 已将 system transform 分派到共享 core，但缺仓库内可复现的 replay 正向样本 |
| [C5 LLM 模型输出识别](capabilities/C5.md) | ✅ | `gen_ai.output.messages` 含 content + tool_calls + finish_reason + reasoning_content，turn 层只保留最终一条 assistant |
| [C6 LLM thinking 内容识别](capabilities/C6.md) | ✅ | message.part.updated(type=reasoning) 捕获 reasoning 文本，写入 output.messages 的 `reasoning_content` 字段 |
| [C7 LLM Token usage 识别](capabilities/C7.md) | 🟡 | Turn 不聚合；OpenAI-compatible wire usage 区分未知/零，主调用与 title 分别对账；其它协议仍需验证 |
| [D1 Compact 识别](capabilities/D1.md) | 🟡 | supported between-turn compaction 已成独立 TOOL span（sibling，type=system，有 trigger=auto + sequence），同 session；active-turn callback 仅观测，不重置 cursor/history；仍缺 token before/after/saved |
| [D2 同步子 agent 调用识别](capabilities/D2.md) | ✅ | `task` 委派建模为 `agent SyncAgent` AGENT span，子 session 通过 `parentID` 内联到父 trace |
| [D3 异步子 agent 调用识别](capabilities/D3.md) | 🟡 | background task 可还原父子拓扑，但当前按用户等待结果的 `SyncAgent` 语义统一建模，未单独输出 AsyncAgent |
| [D4 Cancel / Retry 错误恢复识别](capabilities/D4.md) | 🟡 | 原生中断单次终态与请求计数，API diagnostic 不重复计数；仍缺自动 retry 序号/延迟与 timeout 归因 |
| E1 Git 仓库信息识别 | 🟡 | Git worktree 经过并行、有界探测后在 provider 初始化前写入 Resource；配置值按 key 优先，失败 fail-open，但当前缺含自动探测结果的可复现 replay。 |
| E2 上游 TRACEPARENT 串联 | ⚪ | 当前 OpenCode hook/plugin contract 没有可证实的 W3C upstream context source；内部 `context.active()` 不等同于外部 TRACEPARENT。 |
| [E3 非真实用户 / 外部触发归因识别](capabilities/E3.md) | 🟡 | 已支持显式 `AIVISION_META_TRIGGER` / Resource `ant.meta.trigger`；不生成 Turn trigger |

## 详细评估

### E2 上游调用 TRACEPARENT 串联 {#e2}

- **评分**: ⚪
- **结论**: 见 [独立证据](capabilities/E2.md)：OpenCode 当前没有可证实的外部 W3C upstream context source。

### E1 Git 仓库信息识别 {#e1}

- **评分**: 🟡
- **结论**: 见 [独立证据](capabilities/E1.md)：OpenCode 生产入口只对明确的 Git worktree 探测 remote/branch，具备超时、失败降级、配置优先和生产入口 E2E；但当前缺含自动探测结果的可复现 replay，revision 也未采集。

### A1 多轮会话识别 + chain 拓扑 {#a1}

- **评分**: ✅
- **结论**: 每轮交互生成独立 trace 与唯一 CHAT 根 span（`gen_ai.span.kind=CHAT`、`gen_ai.operation.name=invoke_agent`），其下 LLM / TOOL / Compaction / api_error 全部为 root 的直接子 sibling，没有跨层嵌套，多次 LLM 调用各自独立。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/index.ts:197-277`：`startNewTurn` 创建 turn 根 span 并设置 `CHAT` / `invoke_agent` / `agentic.turn.id` / `agentic.runtime.name=opencode`，`turnContext` 用作所有子 span 的 parent context
  - `opencode/observ-opencode/src/hooks/message.ts:143-148`：LLM span 以 `turn.turnContext` 为 parent
  - `opencode/observ-opencode/src/hooks/tool.ts:40-45`：tool span 以 `activeTurn.turnContext` 为 parent（明确"sibling of LLM"）
  - `opencode/observ-opencode/src/hooks/index.ts:572-594`：compaction span 以 `lastTurnContext` 为 parent，作为 sibling
  - `opencode/observ-opencode/src/hooks/message.ts:53-65`：用户消息变化时 `finishTurn` + `startNewTurn`，确保每轮独立
- **Replay 例子**:
  - `~/.aivision/ocd/replays/spans_3successtool_3errortool.jsonl`（Turn 1：1 CHAT root + 4 LLM + 3 TOOL 全部 sibling；Turn 2：1 CHAT root + 3 LLM + 3 TOOL 全部 sibling）
  - `~/.aivision/ocd/replays/spans_multi_turn.jsonl`（3 个 trace，Turn 3 内呈现 LLM→TOOL→LLM→TOOL→LLM→TOOL→LLM 链，全部直挂 CHAT）

### B1 工具调用精确耗时 {#b1}

- **评分**: ✅
- **结论**: 每个 TOOL span 都携带 `agentic.tool.duration_ms`，正常路径用 wall-clock 计算，message.part.updated 提供历史 start/end 后再做精确闭合（deferred close），以保证 replay 顺序与时长一致。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/tool.ts:43-44`：使用 `toolPartStartTimes` 中的历史起点
  - `opencode/observ-opencode/src/hooks/tool.ts:181`：`toolSpan.setAttribute('agentic.tool.duration_ms', ...)`
  - `opencode/observ-opencode/src/hooks/tool.ts:188-193`：deferred close，保留 callID 等待精确 end_time
  - `opencode/observ-opencode/src/hooks/message.ts:649-663`：`message.part.updated` 拿到 state.time.end 后用历史结束时间关闭 deferred span 并回写 duration_ms
- **Replay 例子**: `~/.aivision/ocd/replays/spans_3successtool_3errortool.jsonl` 中 `tool read` 的 `agentic.tool.duration_ms=142`，`tool invalid`=116，`mcp lsp_diagnostics`=10。

### B2 工具调用错误识别 {#b2}

- **评分**: ✅
- **结论**: 工具失败时同时设置 `status.code=ERROR` + `error.type` + `status.message`（错误文本）+ `gen_ai.tool.call.result`（错误信息也保留），错误挂在工具自身 span 不冒泡到 turn。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/tool.ts:132-159`：`metadataHasError` / `isInvalidTool` / `argsHasError` / `hasNonZeroExit` 多源识别，分类为 `tool_error` / `invalid_tool` / `exit_code`
  - `opencode/observ-opencode/src/hooks/tool.ts:154-156`：`setStatus({code: ERROR, message})` + `setAttribute('error.type', errorType)` 双通道
  - `opencode/observ-opencode/src/hooks/message.ts:592-594`：另一条路径（`state.status='error'`）也写入 `error.type='tool_error'` + ERROR status
- **Replay 例子**: `~/.aivision/ocd/replays/spans_3successtool_3errortool.jsonl` Turn 2 中 `tool read` 失败：`status.code=2`、`status.message='File not found: ...'`、`error.type=tool_error`，`tool invalid`：`error.type=invalid_tool`。

### B3 工具调用输入参数识别 {#b3}

- **评分**: ✅
- **结论**: 每个真实工具执行在 before hook 发出 `tool_call`，并通过 `gen_ai.tool.call.id` 与终态 `tool_result` 关联；task/subagent 不重复建模为工具事件。TOOL span 携带 `gen_ai.tool.call.arguments`，三态语义（真实 JSON / `{}` 占位 / `[REDACTED]`），`gen_ai.tool.name` 与 `gen_ai.tool.call.id` 同时存在；MCP 工具携带 `agentic.tool.type=mcp` 与解析后的 `agentic.tool.name`。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/tool.ts:46-55`：`gen_ai.tool.name`、`gen_ai.tool.call.id`、`agentic.tool.raw_name`、累积 `currentLlmToolCalls`
  - `opencode/observ-opencode/src/hooks/tool.ts:67-73`：参数三态写入
  - `opencode/observ-opencode/src/hooks/tool.ts:60-63`：classifyTool 输出 `agentic.tool.type/name/id`
- **Replay 例子**: `~/.aivision/ocd/replays/spans_3successtool_3errortool.jsonl` 中 `tool read` 的 `gen_ai.tool.call.arguments` 保留请求的 `filePath`；`mcp` 类工具有 `agentic.tool.type=mcp`、`agentic.tool.name=oh-my-claudecode_t_notepad_read`。

### B4 工具调用输出内容识别 {#b4}

- **评分**: ✅
- **结论**: TOOL span 的 `gen_ai.tool.call.result` 携带真实输出；超过阈值时显式打 `agentic.tool.output.truncated=true` + `agentic.tool.output.original_length`，错误路径仍保留错误描述。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/tool.ts:165-176`：`truncateWithMeta` + 截断元数据
  - `opencode/observ-opencode/src/hooks/tool.ts:170-176`：错误时强制写入真实错误文本，未脱敏
  - `opencode/observ-opencode/src/hooks/state.ts`：`truncateWithMeta` 实现保留 `originalLength`
- **Replay 例子**: `~/.aivision/ocd/replays/spans_3successtool_3errortool.jsonl` 中 Turn 1 的 `tool read` 携带 `agentic.tool.output.truncated=true`、`agentic.tool.output.original_length=7835`、`gen_ai.tool.call.result` 为目录列表；Turn 2 的 `tool read` 错误：result='File not found: /nonexistent/...'。

### B5 MCP 调用识别 {#b5}

- **评分**: ✅
- **结论**: `classifyTool` 解析 MCP 工具命名空间，TOOL span 携带 `agentic.tool.type=mcp` + 解析后逻辑名 + `agentic.tool.id`，与 builtin 工具明确区分。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/tool.ts:60-63` — classifyTool 输出 `agentic.tool.type/name/id`
- **Replay 例子**: `~/.aivision/ocd/replays/spans_3successtool_3errortool.jsonl` —— `mcp lsp_diagnostics` 携带 `agentic.tool.type=mcp` + `agentic.tool.name=oh-my-claudecode_t_notepad_read`；`~/.aivision/ocd/replays/spans_mcp.jsonl` 含更多 MCP 调用样本。

### B6 Skill 调用识别 {#b6}

- **评分**：🟡
- **结论**：见 [现行实现与边界](capabilities/B6.md)。command before/executed 已生成关联 Skill span，hook 回归不等于原生 skill 全场景验收。

### C1 LLM 精确耗时 {#c1}

- **评分**: ✅
- **结论**: 每个 LLM span 在正常路径都携带 `agentic.llm.duration_ms`（来源：`assistantMsg.time.completed - assistantMsg.time.created`，毫秒精度），span start/end 也用相同时间锚。错误路径下 fallback 为 0，但字段始终非负。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/message.ts:144-148`：`startSpanAntLlmSpan(..., {startTime: new Date(assistantMsg.time.created)})`
  - `opencode/observ-opencode/src/hooks/message.ts:170`：fallback `duration_ms=0`
  - `opencode/observ-opencode/src/hooks/message.ts:331-333`：`llmSpan.setAttribute('duration_ms', llmDurationMs)` 真实值
  - `opencode/observ-opencode/src/hooks/state.ts:closePreviousLlmSpan` 用 `currentLlmEndTime` 精确闭合
- **Replay 例子**: `~/.aivision/ocd/replays/spans_3successtool_3errortool.jsonl` 中 LLM span `agentic.llm.duration_ms=9954`、`2409`、`2005`、`3674`，与 endTime - startTime 一致。

### C2 LLM 错误识别 {#c2}

- **评分**: ✅
- **结论**: API 错误时 LLM span 同时具备 `status.code=ERROR(2)` + `error.type=401` + `error.message` + `http.response.status_code=401`，并额外建立 sibling `api_error 401` CLIENT span 表达 retry/diagnostics 上下文。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/index.ts:336-347`：从 `session.error` 取 SDK 错误，写入 `error.type` / `error.message` / `http.response.status_code` 到 currentLlmSpan
  - `opencode/observ-opencode/src/hooks/index.ts:349-365`：`startSpanAntClientSpan` 建立独立 api_error span 作为 sibling，`status.code=ERROR`、`agentic.llm.error.{type,message,status_code}`
  - `opencode/observ-opencode/src/hooks/state.ts:closePreviousLlmSpan`：关闭 LLM span 时把 `currentLlmError` 提升到 `setStatus({code: ERROR})`
  - `opencode/observ-opencode/src/hooks/message.ts:227-235`：Tier 2 detection（finish='other' + zero tokens → `network_error`）
- **Replay 例子**: `~/.aivision/ocd/replays/spans_api_error.jsonl` —— 每个 turn 同时有 `chat GLM-5.1` LLM span（`status.code=2`、`error.type=401`）和 `api_error 401` CLIENT span 作为 sibling 挂在 turn 下。

### C3 LLM 用户输入内容识别 {#c3}

- **评分**: ✅
- **结论**: turn root 携带 1 条 user 消息（用户视角）；generation 的 `gen_ai.input.messages` 是唯一的单次调用输入字段：首次调用仅含当前 user，前一 Generation 已 ERROR 且无 assistant/reasoning/tool 语义输出时，下一 observed attempt byte-for-byte 复用原 input；普通 tool continuation 仅含相邻调用新增的 assistant tool_calls 与 tool results。supported compaction 发生在 `session.idle` 后、下一用户消息前，所以下一用户消息创建新 Turn，并自然得到新的 `conversationHistory=[]` / `generationInputCursor=0` 边界。没有累加模式或模式开关，软截断保留消息结构（仅截 content，不删条目）。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/index.ts:91-94`：turn 根 input.messages = `[{role:user, content:userMessage}]`
  - `opencode/observ-opencode/src/hooks/message.ts`：普通调用从 `generationInputCursor` 切出并推进单次调用 input；retry 直接复用 pending snapshot 且不推进 cursor
  - `opencode/observ-opencode/src/hooks/state.ts:softTruncateMessages`：保留 `tool_calls.arguments` 原值，仅 content 软截断
  - `opencode/observ-opencode/src/hooks/state.ts:closePreviousLlmSpan` 把正常 assistant + tool_results 推入 conversationHistory；无语义输出的 ERROR attempt 不写空 assistant，而是保留 retry snapshot
- **回归证据**: `test-llm-input-delta.ts` 覆盖三次连续模型调用和第二个用户 Turn；`test-llm-retry-input.ts` 证明 retry input 相同、meaningful revision 不克隆、tool continuation 保持 delta、privacy 保持边界；`opencode/observ-opencode/scripts/test-compaction-delta-semantics.ts` 证明 between-turn compaction 后下一 Turn input 只包含新 user message。当前仓库尚无可证明真实 retry attempt 的 transcript/golden，因此此处是 hook 级代码与测试证据，不冒充 replay 双证据。

### C4 LLM 系统提示词识别 {#c4}

- **评分**: 🟡
- **结论**: hooks 链路通过 `experimental.chat.system.transform` 捕获 system 文本并写入 `gen_ai.system_instructions`（turn + generation 双层）；独立的 `opencode-replay` decoder/dispatcher 将该记录送入同一个 `observ-opencode/core` hook，运行时与 replay 不再分叉。当前含该字段的 `~/.aivision/ocd/replays/spans_multi_turn.jsonl` 属于明确不入 Git 的 runtime artifact，不能单独作为可复现的版本化 replay 证据。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/index.ts:451-471`：`experimental.chat.system.transform` 把 `output.system` 拼接为 `turn.systemPrompt`
  - `opencode/observ-opencode/src/hooks/index.ts:107-110`：finishTurn 时把 `gen_ai.system_instructions` 写到 turn 根 span
  - `opencode/observ-opencode/src/hooks/message.ts:207-211`：每个 LLM span 也写入 system_instructions（或 `[REDACTED]`）
- **Replay 证据缺口**: 本机 `~/.aivision/ocd/replays/spans_multi_turn.jsonl` 可检索到 `gen_ai.system_instructions`，但 runtime replay 按约定不入 Git；需在 `opencode/opencode-replay/tests/fixtures/` 增加脱敏输入并由测试重建后才能升级为 ✅。分派入口为 `opencode/opencode-replay/src/decoder.ts`，语义处理仍在 `opencode/observ-opencode/src/hooks/index.ts`。

### C5 LLM 模型输出识别 {#c5}

- **评分**: ✅
- **结论**: turn root 仅放最终 assistant 响应（1 条），generation 携带该次调用的 assistant content + `tool_calls`（结构化）+ `finish_reason` + 可选 `reasoning_content`，多次调用不合并。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/index.ts:99-105`：turn 根 `gen_ai.output.messages` 通过 `buildGenAiOutputMessages` 仅装 1 条
  - `opencode/observ-opencode/src/hooks/state.ts:137-189`（`buildGenAiOutputMessages`）保留 `tool_calls`、`reasoning_content`、`finish_reason`
  - `opencode/observ-opencode/src/hooks/state.ts:closePreviousLlmSpan`：每个 LLM span 关闭前都会 setAttribute(`gen_ai.output.messages`)
- **Replay 例子**: `~/.aivision/ocd/replays/spans_3successtool_3errortool.jsonl` 第一条 LLM span 的 output：`[{"role":"assistant","content":"嗨！让我为您并行模拟三个工具调用：","finish_reason":"tool_use","reasoning_content":...,"tool_calls":[...]}]`，turn span output 为最终答复 1 条。

### C6 LLM thinking 内容识别 {#c6}

- **评分**: ✅
- **结论**: OpenCode runtime 的 `message.part.updated(type=reasoning)` 提供 reasoning 文本，hook 层挂到 `turn.currentLlmReasoningText`，再注入 generation span 的 `gen_ai.output.messages` 中 assistant 消息的 `reasoning_content` 字段（与 content 严格分离）。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/message.ts:670-678`：`part.type === 'reasoning'` → `turn.currentLlmReasoningText`
  - `opencode/observ-opencode/src/hooks/state.ts:184-189`：`buildGenAiOutputMessages` 注入 `reasoning_content`
  - `opencode/observ-opencode/src/hooks/state.ts:64-68`：`reasoning_content` 软截断保护
- **Replay 例子**: `~/.aivision/ocd/replays/spans_multi_turn.jsonl` 中 LLM span output：`[{"role":"assistant","content":"Sure — here are three simulated tool calls...","reasoning_content":"The user is asking me to simulate three tool calls. This is a simple, trivial request..."}]`，`reasoning_content` 与 `content` 分字段共存。

### C7 LLM Token usage 识别 {#c7}

- **评分**：🟡
- **结论**：见 [现行用量契约](capabilities/C7.md)。Turn 不重复写聚合 token。正式 OpenAI-compatible provider hook 保留 wire reportedness、inclusive output 与 cache/reasoning；native title 参与计数。早期 replay 的 root 聚合问题不能用于描述当前源码。

### D1 Compact 识别 {#d1}

- **评分**: 🟡
- **结论**: Compaction 已被建模为独立 TOOL span（sibling，不被吞进相邻 LLM），具备 `gen_ai.tool.name=compaction`、`agentic.tool.type=system`、`agentic.compaction.trigger=auto`、`agentic.compaction.sequence`、有 status=OK 与 duration_ms。supported 路径是 `session.idle -> experimental.session.compacting -> session.compacted -> new user message`：会话 ID 保持不变，compaction 关联 latest Turn context，下一用户消息通过新 Turn 自然重置 `generationInputCursor`，不会继承压缩前 delta。若收到 active-Turn compacting callback，当前观测路径保持 active Turn、`conversationHistory` 和 cursor 不变，不阻塞 runtime compaction、不结束 Turn、不合成压缩后消息。但缺 `agentic.compaction.tokens.before/after/saved`，无法量化压缩效果。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/index.ts:572-594`：`experimental.session.compacting` 创建 compaction span，挂在 `lastTurnContext` 下作为 sibling，写 `agentic.tool.type=system`、`agentic.tool.name=compaction`、`agentic.tool.id=system__compaction`
  - `opencode/observ-opencode/src/hooks/index.ts:404-435`：`session.compacted` 关闭 compaction span，写 `agentic.compaction.trigger='auto'` + `agentic.compaction.sequence` + `agentic.tool.duration_ms`，并 emit `compaction_result` event
  - `opencode/observ-opencode/src/hooks/state.ts`：`SessionState` 维护 `compactionSpan` / `compactionStartTime` / `compactionSequence`
- **Replay 例子**: `~/.aivision/ocd/replays/spans_auto_compact.jsonl` —— `compaction` span 属性 `{gen_ai.tool.name:compaction, agentic.tool.type:system, agentic.tool.name:compaction, agentic.tool.id:system__compaction, agentic.tool.duration_ms:10, agentic.compaction.trigger:auto, agentic.compaction.sequence:1}`；**未见 tokens.before/after/saved**。
- **Hook 级回归证据**: `opencode/observ-opencode/scripts/test-compaction-delta-semantics.ts` 覆盖 fresh new-Turn delta、重复 between-turn compaction、active-Turn compacting callback 的 observational 行为、retry 后 compaction、privacy、duplicate/unmatched callback 清理。该文件不等同于正式 transcript/replay Golden。
- **缺失原因**: 应该做但没做。OpenCode 的 `experimental.session.compacting` 提供 `context: string[]`，可在压缩前后读取 turn 累计 token 并填入 before/after/saved；当前实现忽略了这一信息。

### D2 同步子 agent 调用识别 {#d2}

- **评分**: ✅
- **结论**: OpenCode 1.17.18 的 `task` 工具会创建带 `info.parentID` 的子 session。共享 hooks 将该委派建模为父 Turn 下的 `agent SyncAgent` AGENT span，子 session 不再生成独立 Turn root，其 LLM / TOOL 直接挂在 SyncAgent 下。
- **代码引证**:
  - `opencode/observ-opencode/src/hooks/tool.ts`：`task` 创建 `gen_ai.span.kind=AGENT` / `gen_ai.operation.name=invoke_agent` 的 `agent SyncAgent`
  - `opencode/observ-opencode/src/hooks/session.ts`：用真实 `session.info.parentID` 绑定 child session，不使用全局“下一个 session”猜测
  - `opencode/observ-opencode/src/hooks/index.ts`：子 session 复用 SyncAgent span/context 作为逻辑 turn root
- **Replay 例子**: `~/.aivision/ocd/golden/replays/spans_subagent.jsonl` — 同一 trace 中存在 `Turn 1 → agent SyncAgent → chat Qwen3-Coder... / tool bash`，并携带 parent/child thread id。

### D3 异步子 agent 调用识别 {#d3}

- **评分**: 🟡
- **结论**: `run_in_background=true` 的 task 也能依据 child `parentID` 还原拓扑；golden 中父 agent 通过 `background_output` 等待结果，因此按业务语义统一表示为 `agent SyncAgent`。纯 fire-and-forget 的 AsyncAgent 独立生命周期尚未建模。

### D4 Cancel / Retry 错误恢复识别 {#d4}

- **评分**: 🟡
- **结论**: 原生 `MessageAbortedError` 结束当前已观测 Generation，标记 ERROR 和 `finish_reasons=["error"]`，只产生一次请求事件/计数。官方宿主可能先发 `session.error`、`session.idle` 再发最终 assistant 更新；插件在真实中断事件边界结束请求，重复 idle 不重复产生 `session_end`。缺失 usage 保持未知，不用宿主零值补齐。
- **API 错误**: `APIError` / `ProviderAuthError` 保留独立 CLIENT diagnostic；已存在 Generation 时不增加同一请求的计数。同 Turn 的前次失败没有语义输出时，下一 observed attempt 可复用原 input 快照；`isRetryable` 仅表示资格，不证明实际重试。
- **代码引证**:
  - `observ-opencode/src/hooks/index.ts` / `provider-usage.ts`：原生中断终态、单次事件和指标。
  - `observ-opencode/src/hooks/provider-wire.ts`：沿用原 fetch / AbortSignal，解析真实响应中的 usage；取消不制造响应或 token。
  - `observ-opencode/src/hooks/session.ts`：同一 Turn 的 idle 去重。
  - `observ-opencode/scripts/test-llm-usage-terminal.ts` / `test-provider-wire-usage.ts`：取消事件顺序、重复回调、AbortSignal 和 unknown/zero 负例。
- **边界**: 仍不生成无法从宿主证明的 retry 序号/延迟、timeout 或 rate-limit 归因。自动传输层重试与原生 cancellation 的完整平台验收应以实际请求 ledger、三种 OTLP 信号及平台 readback 为准；hook 单元测试不能替代这些证据。历史 API 错误 replay 也不证明当前宿主全矩阵通过。

## L0-L3 历史合规评估（待原生矩阵复核）

参考 [CONSTITUTION §3.7](../CONSTITUTION.md) 定义的合规等级阶梯。以下等级来自早期 replay；本轮没有重做全量 Weaver/Golden 评估。已解决的 root token 重复、command Skill 和 Resource trigger 不再列为源码缺口。

| 等级 | 状态 | 阻塞项 |
|------|:----:|--------|
| L0 基础存在性 | 🟡 | L0-4 Resource 4 项中 `ant.agent.env` / `ant.agent.scene` / `ant.username` 部署时未传入（仅 `ant.agent.type` 通过）；L0-1/2/3/5/6/7/8 全部通过（A1 拓扑 flat sibling ✅，含 compaction/api_error 也作为 sibling） |
| L1 字段完整性 | 🟡 | L1-2 通过（C3/C5 ✅），L1-3 通过（B3/B4 ✅），L1-4 通过（B1/C1 ✅），L1-5 通过，L1-6 通过（B2/C2 错误双标记齐全），L1-7 通过；当前 C7 口径和证据已更新，L1-1 需按新版原生矩阵重核 |
| L2 时序与准确性 | 🟡 | L2-1/2/3/4 通过（deferred close 精确闭合），L2-6 cache_read/creation 细分三层；L2-5 需重新核对新版 reportedness 与辅助请求；L2-7 时间重叠未严格抽检 |
| L3 全量合规 + Golden | ❌ | L3-3 Golden baseline 未建立（仅有 5+ replay 文件覆盖 multi_turn/api_error/compact/mcp/tool 但未与 weaver baseline 对账）；L3-7 thinking 通过（C6 ✅）；L3-12 T1-T10 未完成覆盖；C4 system_instructions 已通过共享 core replay 补齐 |

**当前等级**：尚未基于新版宿主完整复评；不能把历史等级或本地 fake-provider 结果表述为所有能力验收通过。

**下一步**：
1. 部署侧补齐 `ant.agent.env` / `ant.agent.scene` / `ant.username` 三项 Resource 注入；
2. 为其它 provider 协议、宿主版本和自动 retry 补齐实际 wire 与 generation 对账；
3. `observ-opencode/src/hooks/index.ts:404-435` compaction 关闭时读取 `context: string[]` 前后 token 并填入 `agentic.compaction.tokens.{before,after,saved}`（补 D1）；
4. 建立 weaver baseline 并把现有 5 个 replay 加入 golden，推进 L3-3。

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
