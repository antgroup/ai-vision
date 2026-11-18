# claudecode 能力评估

← 返回 [CAPABILITIES.md](../CAPABILITIES.md)

## 评分汇总

✅ 15 / 🟡 4 / ❌ 1 / ⚪ 1 （共 21 项）

| 能力 | 评分 | 一句话结论 |
|------|------|-----------|
| A1 多轮会话 + chain | ✅ | 每个 user→assistant 边界生成一个 root（CHAT/invoke_agent），LLM/TOOL/COMPACT/api_error 全部以 sibling 挂在 root 下，chain 拓扑稳定。 |
| B1 工具耗时 | ✅ | 每个 TOOL span 用 assistant_ts → tool_result_ts 计算 `agentic.tool.duration_ms`，时间边界来自原始 transcript 时间戳。 |
| B2 工具错误 | ✅ | `detect_tool_error` 同时设置 `status.code=ERROR` + `error.type=tool_error` + 错误消息，覆盖 `is_error=true` / `error` 字段 / `Error:` 前缀三种来源。 |
| B3 工具输入参数 | ✅ | `gen_ai.tool.call.arguments` 序列化原始 `tool_use.input`，并通过 `truncate_value` 显式标记 `agentic.tool.input.truncated`。 |
| B4 工具输出内容 | ✅ | `gen_ai.tool.call.result` 序列化原始 tool_result，并显式标记 truncated / missing。 |
| B5 MCP 调用识别 | ✅ | `parse_agentic_tool` 识别 `mcp__server__method` 命名空间，TOOL span 携带 `agentic.tool.type=mcp` + 解析后的 name + 调用 ID。 |
| B6 Skill 调用识别 | ✅ | Skill 工具走专门构造路径，TOOL span 携带 `agentic.tool.type=skill` + skill 逻辑名 + 调用 ID。 |
| C1 LLM 耗时 | ✅ | 用 `assistant.timestamp → end_timestamp` 计算 `agentic.llm.duration_ms`，多 chunk merge 后保留首尾时间。 |
| C2 LLM 错误 | 🟡 | `detect_llm_error` 处理 `max_tokens` 和 stop_reason 含 `error` 的情形 ✅；但 `isApiErrorMessage=true` 的 synthetic（API 重试耗尽）只设 `status=ERROR`，未补 `error.type`。 |
| C3 LLM 用户输入 | ✅ | turn root 的 `gen_ai.input.messages` 仅含 1 条 user（末尾），generation span 携带累积输入（自然时序）+ `agentic.input.delta`。 |
| C4 LLM 系统提示词 | ⚪ | claudecode transcript 完全不暴露 system prompt；源代码无任何 `system_instructions` 处理（grep 0 命中）。 |
| C5 LLM 模型输出 | ✅ | turn root + generation 各自携带 `gen_ai.output.messages`，含 `content` / `tool_calls` / `finish_reason` / `reasoning_content`。 |
| C6 LLM thinking | ✅ | `extract_thinking_blocks` 解析 Claude `thinking` block，写入 generation 输出消息的 `reasoning_content`，与 content 严格分离。 |
| C7 LLM Token usage | ✅ | 每个 assistant chunk 自带单次调用 usage（无需差分），LLM span 写 `input/output_tokens`，cache_* 按 semconv 聚合进 `input_tokens`；turn root 不重复上报。 |
| D1 Compact | ✅ | 独立 compaction span（TOOL/execute_tool, tool.name=compaction），`agentic.compaction.{trigger,tokens.before,tokens.after,tokens.saved,sequence,ratio}` 完整。 |
| D2 同步子 agent | ✅ | Task 工具识别为 `agentic.tool.type=subagent` + `mode=sync` + `subagent.id/type/description`，并扩展子 transcript 的 generation/tool 子 span。 |
| D3 异步子 agent | 🟡 | 异步 subagent 走独立 trace 上传，父子两端共享 `agentic.subagent.id` 形成属性级弱关联，但无 span_link / 共享 trace_id。 |
| D4 Cancel/Retry | 🟡 | 多次 retry 各生成独立 `chat api_error` CLIENT span（attempt/delay/status_code/message 全有，status=ERROR）✅；用户取消仅在 trace name 加 `(Cancelled)` 后缀，无 `error.type=cancelled`、无 status=ERROR。 |
| E1 Git 仓库信息 | 🟡 | `UserPromptSubmit` 从可信 cwd 采集 session-scoped remote/branch，注入 Resource；生产代码和隔离测试已覆盖，但缺含 VCS Resource 的可复现 replay。 |
| E2 上游 TRACEPARENT 串联 | ✅ | 严格解析 W3C TRACEPARENT，Turn 复用 upstream trace/parent，ToolContext 向下游生成新的 child TRACEPARENT。 |
| E3 非真实用户 / 外部触发归因识别 | ❌ | 当前未实现 外部触发归因规范要求的 `AIVISION_META_TRIGGER` / `meta.trigger`、`ant.meta.trigger` 与 `agentic.turn.trigger`。 | [E3](capabilities/E3.md) |

### E2 上游调用 TRACEPARENT 串联 {#e2}

- **评分**: ✅
- **结论**: 见 [独立证据](capabilities/E2.md)：Claude Code 的 Turn seed、Stop/realtime 构造和 ToolContext 均保留上游 W3C trace context。

### E1 Git 仓库信息识别 {#e1}

- **评分**: 🟡
- **结论**: 见 [独立证据](capabilities/E1.md)：Claude Code 在 `UserPromptSubmit` 的可信 `cwd` 上采集并缓存仓库 remote/branch，实时 Resource 注入路径消费该快照；但当前没有含 VCS Resource 的可复现 replay，revision 也未采集。

## 详细评估

### A1 多轮会话识别 + chain 拓扑 {#a1}

- **评分**: ✅
- **结论**: 每轮 user→assistant 形成唯一 root span，LLM / TOOL / Compaction / api_error 全部作为 sibling，多次 LLM 调用产生独立 generation span，chain 拓扑稳定。
- **代码引证**:
  - `claudecode/cchooks/src/common/otel.rs:138-147` — root span 写入 `gen_ai.span.kind=CHAT` + `gen_ai.operation.name=invoke_agent` + `gen_ai.conversation.id` + `agentic.turn.number`
  - `claudecode/cchooks/src/common/otel.rs:1187-1197, 1268-1322` — `collect_turn_spans` 把 generation/tool 的 `parent_span_id = root_span_id`（不是 generation→tool 嵌套）
  - `claudecode/cchooks/src/common/transcript/turns.rs:568-720` — `build_turns` 按 user 边界切分，每个 user 消息开启新 turn
  - `claudecode/cchooks/src/common/otel.rs:1545-1576` — 多 turn 时 `build_otel_export_request` 为每 turn 独立调用 `collect_turn_spans`（独立 trace_id）
- **Replay 例子**:
  - `~/.aivision/cc/replays/spans_S05_chat_tool_chat_chain_session_0.jsonl` — `Turn 1` (CHAT) → `chat GLM-5.1` (LLM) + `tool Read` (TOOL) 同 parent_span_id
  - `~/.aivision/cc/replays/spans_compact.jsonl` — 单 turn 内交错出现 22+ 个 LLM / TOOL / SKILL / AGENT / COMPACT span，全部 sibling

### B1 工具调用精确耗时 {#b1}

- **评分**: ✅
- **结论**: 每个 TOOL span 用真实 assistant 时间戳 → tool_result 时间戳计算非负 duration_ms。
- **代码引证**:
  - `claudecode/cchooks/src/common/otel.rs:421-428` — `duration_ms = (end_u128 - start_u128) / 1_000_000`，end 来自 `result_ts`，start 来自 `assistant_ts_ns`
  - `claudecode/cchooks/src/common/otel.rs:515` — `OtelAttribute::int_as_string("agentic.tool.duration_ms", duration_ms as i64)`
  - `claudecode/cchooks/src/common/otel.rs:546-547` — `start_time_unix_nano = assistant_ts`, `end_time_unix_nano = end_ns`
- **Replay 例子**: `~/.aivision/cc/replays/spans_session_skill_mcp_tool.jsonl` — `tool AskUserQuestion` 携带 `agentic.tool.duration_ms` 与匹配的 start/end nano。

### B2 工具调用错误识别 {#b2}

- **评分**: ✅
- **结论**: `detect_tool_error` 检测 `is_error=true` / `error` 字段 / `Error:` 前缀三种形态，同时设置 `status.code=ERROR` + `error.type=tool_error` + status.message。
- **代码引证**:
  - `claudecode/cchooks/src/common/otel.rs:33-62` — `detect_tool_error()` 三路检测
  - `claudecode/cchooks/src/common/otel.rs:430-436` — 用错误返回值构造 `OtelStatus::error("tool_error: <msg>")`
  - `claudecode/cchooks/src/common/otel.rs:529-532` — `attributes.push(OtelAttribute::string("error.type", err_type))`
  - 错误挂在工具自身 span（不冒泡到 root）；root 只在 LLM stop_reason 错误时才染色（`claudecode/cchooks/src/common/otel.rs:170-181`）。
- **Replay 例子**: 当前 28 个 replay 样本中暂无触发 tool error 的 transcript（人工选取的 case 主要是成功 / api_error 路径）；代码路径已被 `cchooks/src/common/otel.rs` 中 `test_detect_tool_error*` 单测覆盖。

### B3 工具调用输入参数识别 {#b3}

- **评分**: ✅
- **结论**: 每个 TOOL span 携带 `gen_ai.tool.call.arguments`（序列化 JSON 字符串），同时显式标记 truncated / missing 元数据。
- **代码引证**:
  - `claudecode/cchooks/src/common/otel.rs:409-419` — `truncate_value(&tool_use.input)` 后序列化为 JSON 字符串
  - `claudecode/cchooks/src/common/otel.rs:454, 510` — Skill / 普通 TOOL 都写入 `gen_ai.tool.call.arguments`
  - `claudecode/cchooks/src/common/otel.rs:82-103` — `append_truncation_meta` 写入 `agentic.tool.input.truncated` + `original_length`
  - `claudecode/cchooks/src/common/otel.rs:662-663` — fallback path（仅 result 时）显式标记 `agentic.tool.input.missing=true`，不写空占位
  - `claudecode/cchooks/src/common/otel.rs:517` 调用 `append_agentic_tool_attrs_typed` 把 `agentic.tool.{type,name,id}` 等也补齐（MCP/Skill 命名空间正确解析）
- **Replay 例子**:
  - `~/.aivision/cc/replays/spans_session_skill_mcp_tool.jsonl` — `mcp plugin_oh-my-claudecode_t.state_write` 同时携带 `agentic.tool.type=mcp` 与原始 arguments JSON
  - `~/.aivision/cc/replays/spans_S05_chat_tool_chat_chain_session_0.jsonl` — `tool Read` 携带完整 `{"file_path":"..."}` arguments

### B4 工具调用输出内容识别 {#b4}

- **评分**: ✅
- **结论**: `gen_ai.tool.call.result` 写入序列化 tool_result，截断时显式标记 `agentic.tool.output.truncated` + `original_length`。
- **代码引证**:
  - `claudecode/cchooks/src/common/otel.rs:410, 416-419` — `truncate_value(&raw_output)` + JSON 序列化
  - `claudecode/cchooks/src/common/otel.rs:455, 511, 638` — 三条构造路径都写入 `gen_ai.tool.call.result`
  - `claudecode/cchooks/src/common/otel.rs:93-102` — output 被截断时 push `agentic.tool.output.truncated=true` + `original_length`
- **Replay 例子**: `~/.aivision/cc/replays/spans_too_long_message.jsonl` — TOOL span 携带 `agentic.tool.output.truncated=true` + `original_length`，证实截断元数据被正确写入。

### B5 MCP 调用识别 {#b5}

- **评分**: ✅
- **结论**: `parse_agentic_tool` 识别 `mcp__<server>__<method>` 命名空间并打 `agentic.tool.type=mcp`，同时保留原始名（`gen_ai.tool.name`）和解析后的逻辑名（`agentic.tool.name`）。
- **代码引证**:
  - `claudecode/cchooks/src/common/otel.rs:517` — `append_agentic_tool_attrs_typed` 写入 `agentic.tool.{type,name,id}`，type=mcp 分支由命名前缀触发
  - `claudecode/cchooks/src/common/transcript/fields.rs:1-1025` `parse_agentic_tool()` —— 拆分 `mcp__` 前缀，识别 server / method
- **Replay 例子**: `~/.aivision/cc/replays/spans_session_skill_mcp_tool.jsonl` —— TOOL span `mcp plugin_oh-my-claudecode_t.state_write` 携带 `agentic.tool.type=mcp` + `agentic.tool.name=plugin_oh-my-claudecode_t.state_write` + `agentic.tool.id` 调用 ID。

### B6 Skill 调用识别 {#b6}

- **评分**: ✅
- **结论**: Skill 工具调用走专门构造路径（`build_tool_spans` Skill 分支），TOOL span 携带 `agentic.tool.type=skill` + skill 逻辑名 + 调用 ID。
- **代码引证**:
  - `claudecode/cchooks/src/common/otel.rs:454, 510` — Skill 与普通 TOOL 都写入 `gen_ai.tool.call.arguments`
  - `claudecode/cchooks/src/common/otel.rs:517` — `append_agentic_tool_attrs_typed` Skill 分支写入 `agentic.tool.type=skill`
- **Replay 例子**: `~/.aivision/cc/replays/spans_session_skill_mcp_tool.jsonl` —— Skill 类 TOOL span（如 `tool AskUserQuestion`）携带 `agentic.tool.type=skill` + skill 逻辑名 + 调用 ID。

### C1 LLM 精确耗时 {#c1}

- **评分**: ✅
- **结论**: 每个 generation span 用 `assistant.timestamp → end_timestamp` 计算 `agentic.llm.duration_ms`；多 chunk merge 时 start 取首 chunk、end 取末 chunk。
- **代码引证**:
  - `claudecode/cchooks/src/common/otel.rs:250-254, 318-334` — start/end 直接来自 transcript 原始时间戳，duration_ms 计算
  - `claudecode/cchooks/src/common/transcript/turns.rs:558-563` — `merge_assistant_messages` 把最新 chunk 的时间记为 `_end_timestamp`，保留 startTime 为首 chunk
- **Replay 例子**: `~/.aivision/cc/replays/spans_S01_chat_single_turn_success_session_0.jsonl` — `chat GLM-5.1` 的 `agentic.llm.duration_ms=898`。

### C2 LLM 错误识别 {#c2}

- **评分**: 🟡
- **结论**: 普通 LLM 错误（max_tokens / stop_reason 含 "error"）正确写 `status.code=ERROR` + `error.type` + status.message ✅；但 `isApiErrorMessage=true` 的 synthetic generation span 只设 `status=ERROR("API retries exhausted")`，未补 `error.type`，违反 "同时存在" 要求。
- **代码引证**:
  - `claudecode/cchooks/src/common/otel.rs:65-77` — `detect_llm_error` 仅匹配 `max_tokens` 和 stop_reason 含 `error`
  - `claudecode/cchooks/src/common/otel.rs:336-353` — synthetic 分支 push `agentic.llm.is_synthetic=true` 但 **没有** push `error.type`，仅 `OtelStatus::error("API retries exhausted")`
- **Replay 例子**: `~/.aivision/cc/replays/spans_api_error.jsonl` — synthetic 路径未触发；fixture 内的 `chat GLM-5.1` 多 status=1（OK）。
- **缺失原因**: synthetic LLM span 缺 `error.type`。补法：在 `is_synthetic` 分支同时 push `error.type="api_error"`（与 D4 的 CLIENT span 保持一致），并把 detect_llm_error 扩展到 stop_reason 中所有 Anthropic 已知非 ok 值（`refusal`、`pause_turn` 等）。注：claudecode 的真实 LLM 失败大多通过独立 `chat api_error` CLIENT span 表达（D4 ✅），LLM span 自身的 error 仅是兜底。

### C3 LLM 用户输入内容识别 {#c3}

- **评分**: ✅
- **结论**: turn root 的 input 仅含 1 条 user（末尾），每个 generation span 携带累积输入（user 在头部）+ `agentic.input.delta`，OpenAI 兼容 JSON array。
- **代码引证**:
  - `claudecode/cchooks/src/common/message_format.rs:20-24` — `build_input_messages` 仅返回 `[user]`
  - `claudecode/cchooks/src/common/message_format.rs:36-63` — `build_generation_accumulated_input` 把 user 放头部 + 之前所有 assistant + tool_result
  - `claudecode/cchooks/src/common/message_format.rs:70-94` — `build_generation_delta_input` 仅返回与上次调用之间的增量
  - `claudecode/cchooks/src/common/otel.rs:262, 277-279` — generation span 写 `gen_ai.input.messages` + `agentic.input.delta`
  - `claudecode/cchooks/src/common/otel.rs:154` — root span 写 `gen_ai.input.messages`
- **Replay 例子**: `~/.aivision/cc/replays/spans_S07_messages_order_and_delta_session_0.jsonl` — turn root 仅 1 条 user；后续 generation span 中 user 在数组头部，delta 仅含相邻新增 assistant + tool_result。

### C4 LLM 系统提示词识别 {#c4}

- **评分**: ⚪
- **结论**: claudecode transcript 完全不写入 system prompt，cchooks 源代码也没有任何 system_instructions 处理逻辑。
- **代码引证**: `grep -rn "system_instructions\|systemInstructions\|system_prompt" claudecode/cchooks/src/` 0 命中。
- **Replay 例子**: 28 个 replay 中 `gen_ai.system_instructions` 仅作为某条 tool_result 文本里的字符串出现（即 doc 中提到该字段名），**无任何实际属性**写入；新版 cchooks 不再生成该属性。
- **数据源缺失根因**: Claude Code transcript 仅记录 user / assistant / tool_use / tool_result / api_error 事件；CLI 内部组装的 system prompt（含 CLAUDE.md、@-mention 注入、agent persona 等）不会写到 `~/.claude/projects/<dir>/<session>.jsonl`。要拿到必须改成"runtime hook 在 LLM 请求层拦截 request body"，目前 cchooks 是 Stop hook + transcript 重建，无法拿到。

### C5 LLM 模型输出识别 {#c5}

- **评分**: ✅
- **结论**: turn root 取最后 1 条 assistant，generation span 取本次 assistant；都包含 `content` / `tool_calls` / `finish_reason` / `reasoning_content`。
- **代码引证**:
  - `claudecode/cchooks/src/common/message_format.rs:102-117` — `build_output_messages` 取 `turn.assistants.last()` + 强制 finish_reason
  - `claudecode/cchooks/src/common/message_format.rs:130-160` — `assistant_to_openai_message` 把 tool_uses 序列化为结构化 `tool_calls`
  - `claudecode/cchooks/src/common/otel.rs:265-278` — generation span 自构造 output 消息并附加 finish_reason
  - `claudecode/cchooks/src/common/otel.rs:157, 278` — root + generation 均写 `gen_ai.output.messages`
- **Replay 例子**: `~/.aivision/cc/replays/spans_S01_chat_single_turn_success_session_0.jsonl` — turn root + LLM span 都携带 output，`finish_reason=end_turn`，tool_calls 时序列化为 OpenAI 兼容结构（见 `spans_S05_chat_tool_chat_chain_session_0.jsonl`）。

### C6 LLM thinking 内容识别 {#c6}

- **评分**: ✅
- **结论**: 解析 Claude API 的 `thinking` 内容块，注入 generation span 输出消息的 `reasoning_content`，与 `content` 严格分离。
- **代码引证**:
  - `claudecode/cchooks/src/common/transcript/turns.rs:227-252` — `extract_thinking_blocks` 过滤 `type=="thinking"`，读 `thinking` 字段，构造 `{role:assistant, content:text}`
  - `claudecode/cchooks/src/common/transcript/turns.rs:430, 469` — `AssistantMessage.thinking_blocks` 字段填充
  - `claudecode/cchooks/src/common/message_format.rs:147-156` — 多 thinking block 用 `\n\n` 拼接后赋给 `msg.reasoning_content`
- **Replay 例子**:
  - 27/28 replay 中 `gen_ai.output.messages` 包含 `reasoning_content` 字段
  - `~/.aivision/cc/replays/spans_S01_chat_single_turn_success_session_0.jsonl` 的 LLM span：`"reasoning_content":"Simple factual question, no tools needed."` 与 `"content":"The Fibonacci sequence..."` 严格分离

### C7 LLM Token usage 识别 {#c7}

- **评分**: ✅
- **结论**: Claude Code transcript 每个 assistant chunk 自带本次单 API 调用的 usage（非累计），所以 cchooks 不需要差分；LLM span 直接写 `gen_ai.usage.input_tokens` / `output_tokens`，cache_creation / cache_read 按 OTel 1.30 semconv 聚合到 input_tokens；turn root span 完全不写 usage（避免下游双倍）。
- **代码引证**:
  - `claudecode/cchooks/src/common/transcript/turns.rs:274-301, 539-548` — `get_usage` 直接读 `usage.{input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens}`；多 chunk merge 时取最新 chunk 的 usage（不累加）
  - `claudecode/cchooks/src/common/otel.rs:305-316` — `total_input = input + cache_creation + cache_read`，写 `gen_ai.usage.input_tokens` 与 `output_tokens`；明确注释 cache_* 已聚合到 input_tokens
  - `claudecode/cchooks/src/common/otel.rs:107-195` — root span 构造函数中**没有任何 usage 属性**
- **Replay 例子**:
  - `~/.aivision/cc/replays/spans_S08_tokens_exact_usage_session_0.jsonl` — `Turn 1` root span 0 个 usage 属性；`chat GLM-5.1` LLM span `input_tokens=49084 output_tokens=4`
  - `~/.aivision/cc/replays/spans_S01_chat_single_turn_success_session_0.jsonl` — 单轮 input/output 一对一

### D1 Compact 识别 {#d1}

- **评分**: ✅
- **结论**: 每次 compaction 生成独立 TOOL span，挂在 turn root 下；前后 token / sequence / ratio / trigger 全部齐备；post_tokens 通过下一 turn 第一个 assistant 的 usage 回填。
- **代码引证**:
  - `claudecode/cchooks/src/common/transcript/turns.rs:46-53` — `CompactionEvent` 字段定义（pre/post tokens, trigger, sequence, end_timestamp）
  - `claudecode/cchooks/src/common/transcript/turns.rs:626-658, 723-740` — 在 `build_turns` 中识别 compaction 事件并 `pending_compaction_backfill` 用下一 turn 第一个 assistant 的 usage 回填 `post_tokens`
  - `claudecode/cchooks/src/common/otel.rs:751-823` — `build_compaction_span` 写 `gen_ai.{span.kind=TOOL, operation.name=execute_tool, tool.name=compaction}` + `agentic.tool.type=system` + `agentic.compaction.{trigger,tokens.before,tokens.after,tokens.saved,sequence,ratio}` + duration_ms
  - `claudecode/cchooks/src/common/otel.rs:1325-1333` — compaction 作为 root 的 sibling
- **Replay 例子**: `~/.aivision/cc/replays/spans_session_compact_44ac85e7.jsonl` — `tool compaction` span 携带 `tokens.before=169477 tokens.after=100547 tokens.saved=68930 ratio=0.59 sequence=1 trigger=auto`。

### D2 同步子 agent 调用识别 {#d2}

- **评分**: ✅
- **结论**: Claude Code 的 Task 工具被识别为 `agentic.tool.type=subagent`，span_kind=AGENT, operation=invoke_agent；同步 mode 下还会读取 `<session>/subagents/agent-<id>.jsonl` 展开成 generation/tool 子 span 链。
- **代码引证**:
  - `claudecode/cchooks/src/common/otel.rs:489-528` — `parse_agentic_tool` 把 Task 解析为 `tool_type=subagent`；is_agent 时改用 `("AGENT","invoke_agent")` 与 `agentic.subagent.{mode=sync, id, type, description}`
  - `claudecode/cchooks/src/common/otel.rs:1020-1162` — `expand_subagent_spans` 读取子 transcript（`<parent>.jsonl` 同名目录下的 subagents/）并构建 `build_subagent_child_spans` 的 generation / tool 子 span，**仅同步**
  - `claudecode/cchooks/src/common/otel.rs:866-1018` — `build_subagent_child_spans` 给每条子 span 附加 `agentic.subagent=true` + `agentic.subagent.type`
- **Replay 例子**: `~/.aivision/cc/replays/spans_subagentx.jsonl` 与 `spans_compact.jsonl` — `agent SyncAgent` AGENT span（`gen_ai.span.kind=AGENT`、`gen_ai.operation.name=invoke_agent`、`agentic.tool.type=subagent`、`agentic.subagent.mode=sync`），其下展开多个 LLM/TOOL 子 span。

### D3 异步子 agent 调用识别 {#d3}

- **评分**: 🟡
- **结论**: 异步 subagent（Task `run_in_background:true`）走独立 SubagentStop hook 链路上传一条独立 trace；父 trace 的 Agent span 与子 trace 的 root span 双方都携带 `agentic.subagent.id`/`type` 形成属性级弱关联，但**没有 trace_id 共享、没有 OTel span_link、没有 parent span_id**。
- **代码引证**:
  - `claudecode/cchooks/src/hooks/subagent/subagent_handler.rs:248-302` — `build_and_upload_async_turn` 重新调用 `build_otel_export_request_for_turn` 生成新 trace_id，独立上传
  - `claudecode/cchooks/src/hooks/subagent/subagent_handler.rs:491-554` — `inject_async_subagent_attrs` 给 root span 注入 `agentic.subagent={true,mode=async,id,type,description}`
  - `claudecode/cchooks/src/common/otel.rs:519-528` — 父 trace 的 Agent TOOL span 也写 `agentic.subagent.id` + `mode=async`
  - 全仓 `grep -n "span.link\|trace_link\|cross.trace"` 0 命中
- **Replay 例子**: `~/.aivision/cc/replays/spans_subagentx.jsonl` 主要是同步 case；异步 case 的双 trace 关联需在 Langfuse 端按 `agentic.subagent.id` 手动 join，replay 文件本身只能看到任一侧。
- **缺失原因**: 当前实现仅做属性级关联（弱 ✅ 部分场景）。补强思路：
  - 强方案：父 trace 的 Agent span 创建 OTLP `Span.links` 指向子 trace_id/span_id（需要 SubagentHandler 把子 trace_id 回写给父 hook，但父 Stop 在子 SubagentStop 之前，时序不易保证）
  - 弱+方案：父 span 加 `agentic.subagent.transcript_path`，子 root span 加 `agentic.subagent.parent_session_id` + `agentic.subagent.parent_trace_id`，便于下游 join

### D4 Cancel / Retry 错误恢复识别 {#d4}

- **评分**: 🟡
- **结论**: Retry 部分 ✅ —— 每次 api_error 独立 CLIENT span，含 attempt/delay/status_code/error.message + status=ERROR；多次 retry 不会被压平。Cancel 部分 🟡 —— 仅在 trace name 加 `(Cancelled)` 后缀，未设 `error.type=cancelled`、未设 status=ERROR。
- **代码引证**:
  - `claudecode/cchooks/src/common/transcript/turns.rs:37-44` — `ApiError` 结构（timestamp, retry_attempt, max_retries, retry_in_ms, error）
  - `claudecode/cchooks/src/common/transcript/turns.rs:632-650` — `build_turns` 收集 `type=system, subtype=api_error` 事件
  - `claudecode/cchooks/src/common/otel.rs:682-746` — `build_api_error_spans` 每个 retry 生成独立 `chat api_error` CLIENT span：`agentic.llm.error.type=api_error` + `retry.attempt` + `retry.max` + `retry.delay_ms` + `retry.status_code` + `error.message`，OtelStatus::error
  - `claudecode/cchooks/src/common/otel.rs:1336-1364` — api_error span 作为 root sibling 排列
  - **Cancel**: `claudecode/cchooks/src/common/otel.rs:126-129` —— `if turn.cancelled { format!("Turn {} (Cancelled)", ...) }`，仅修改 trace name，无 error.type / status 变化
  - `claudecode/cchooks/src/common/transcript/turns.rs:115-117, 700-704` — cancel 检测来自下一条 user 消息含 `[Request interrupted by user]`
- **Replay 例子**:
  - Retry 路径：`~/.aivision/cc/replays/spans_api_error.jsonl` — 单 turn 内出现 5+ 个 `chat api_error` span，每个 status_code=2 + retry.attempt=1..5 + retry.delay_ms 递增
  - Cancel 路径：`~/.aivision/cc/replays/spans_S09_user_cancel_and_reconcile_session_0.jsonl` 中的 spans 状态码全部 1（OK），无 cancelled error.type
- **缺失原因**: cancel 只染色 name，未在 root / 末尾 generation span 上写 `error.type=cancelled` 或 `status.code=ERROR`。补法：在 `build_root_span` 内 `turn.cancelled=true` 时加 `error.type=cancelled` + `OtelStatus::error("user_cancelled")`，并在最后一个未完成的 generation span 上做相同标注。

## L0-L3 合规等级自评

参考 [CONSTITUTION §3.7](../CONSTITUTION.md) 定义的合规等级阶梯。

| 等级 | 状态 | 阻塞项 |
|------|:----:|--------|
| L0 基础存在性 | 🟡 | L0-4 Resource 4 项中 `ant.agent.env` / `ant.agent.scene` / `ant.username` 部署时未传入（仅 `ant.agent.type` 通过）；L0-1/2/3/5/6/7/8 全部通过 |
| L1 字段完整性 | 🟡 | L1-1/2/3/4/5/7 已通过（C7/C3/C5/B1/C1/B3/B4 ✅），L1-6 部分通过 —— C2 synthetic error 仅设 `status=ERROR` 未补 `error.type`，违反"错误双标记"约束 |
| L2 时序与准确性 | 🟡 | L2-1/2/3/4/5 通过（assistant chunk 自带单次 usage，无需差分），L2-6 cache token 部分覆盖（聚合到 input_tokens），L2-7 同级非并发不重叠未做严格抽检 |
| L3 全量合规 + Golden | ❌ | L3-3 Golden baseline 已建立（8 个 replay 文件覆盖 S01-S09），但 L3-9 跨 agent 等价、L3-11 并发工具 `parallel_group` 标记、L3-12 T1-T10 覆盖（T8/T10 缺）未完成 |

**当前等级**：L1（卡在 L2，主要 gap 是 L0-4 Resource 部署侧未传入 + C2 synthetic error 缺 error.type）

**下一步**：
1. 部署侧补齐 `ant.agent.env` / `ant.agent.scene` / `ant.username` 三项 Resource 注入；
2. `cchooks/src/common/otel.rs:336-353` synthetic LLM span 分支补 `error.type="api_error"`，与 D4 CLIENT span 保持一致；
3. 抽检 L2-7 时间重叠（同 trace 内同级 sibling 是否存在非并发重叠区间）；
4. 补 T8（compaction 多次）/ T10（混合 cancel+retry）的 transcript 覆盖以推进 L3-12。

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
