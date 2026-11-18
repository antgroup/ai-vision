# codex 能力评估

← 返回 [CAPABILITIES.md](../CAPABILITIES.md)

## 评分汇总

✅ 15 / 🟡 3 / ❌ 1 / ⚪ 2 （共 21 项）

| 能力 | 评分 | 一句话结论 |
|------|------|-----------|
| [A1 多轮会话识别 + chain 拓扑](capabilities/A1.md) | ✅ | Turn 根 span 唯一且以 `_internal.meta.root="1"` 标识，所有 generation/tool/compaction 全部作为直接子节点同级 sibling，多次 LLM 调用保留独立 span；root Turn 额外携带会话来源触发快照 `agentic.turn.trigger` |
| [B1 工具调用精确耗时](capabilities/B1.md) | ✅ | 每个 tool span 携带 `agentic.tool.duration_ms` 与 `started/ended_timestamp` 推导的真实起止时间 |
| [B2 工具调用错误识别](capabilities/B2.md) | ✅ | `success=false` 或输出含 `error:` 时同时设置 `status.code=ERROR` + `error.type=tool_error` + `status.message` 三连 |
| [B3 工具调用输入参数识别](capabilities/B3.md) | ✅ | `gen_ai.tool.call.arguments` 反映真实参数，配合 `agentic.tool.type/name/id/raw_name`，带 `truncated` 元信息 |
| [B4 工具调用输出内容识别](capabilities/B4.md) | ✅ | `gen_ai.tool.call.result` 反映原始输出，带 `truncated` 标记，MCP/PTY 输出回填 |
| [B5 MCP 调用识别](capabilities/B5.md) | ✅ | `mcp_tool_call_end` / `dynamic_tool_call_response` 解析 MCP 协议事件，TOOL span 携带 `agentic.tool.type=mcp` + 解析后名 |
| [B6 Skill 调用识别](capabilities/B6.md) | ✅ | 同时覆盖 Codex v2 结构化 `$placeholder` 命令型调用、原生 `Skill` call 与同名限定的 legacy 命令路径 fallback |
| [C1 LLM 精确耗时](capabilities/C1.md) | ✅ | `agentic.llm.duration_ms` 由 assistant 时间戳与下一个事件时间推导，duration 非负 |
| [C2 LLM 错误识别](capabilities/C2.md) | 🟡 | tool 错误已限制在失败叶子；aborted 映射 cancelled，但 transcript 仍缺稳定 LLM-level API/stream error 信号 |
| [C3 LLM 用户输入内容识别](capabilities/C3.md) | ✅ | turn 根 span 仅含 1 条 user，generation span 同时携带累计 input 与 `agentic.input.delta` |
| [C4 LLM 系统提示词识别](capabilities/C4.md) | ✅ | session_meta 的 `base_instructions.text` 被提取并写入 root/Generation 的 `gen_ai.system_instructions` |
| [C5 LLM 模型输出识别](capabilities/C5.md) | ✅ | `gen_ai.output.messages` 携带 assistant 内容、tool_calls 与 inferred `finish_reason` |
| [C6 LLM thinking 内容识别](capabilities/C6.md) | ⚪ | codex transcript 中 reasoning summary 普遍为空数组、内容存于不可解 `encrypted_content` |
| [C7 LLM Token usage 识别](capabilities/C7.md) | ✅ | 每个 `token_count.last_token_usage` 对齐对应 assistant/Generation，root 不重复 token，实时与 finalizer 共用同一 usage |
| [D1 Compact 识别](capabilities/D1.md) | ✅ | 独立 compaction span，`agentic.compaction.{trigger,tokens.before,tokens.after,tokens.saved,sequence,ratio}` 齐全 |
| [D2 同步子 agent 调用识别](capabilities/D2.md) | ✅ | 同时覆盖 0.133.x targeted-wait 与 multi-agent v2 activity/typed-terminal；优先内联为 `agent {nickname}`，无法识别名称时回退 `agent SyncAgent`，保留 sibling waits |
| [D3 异步子 agent 调用识别](capabilities/D3.md) | ⚪ | 当前已验证 transcript 只有同步/前台样本，尚无 detached/background 生命周期证据；0.3.0 移除了无证据的 async 兜底路径 |
| [D4 Cancel / Retry 错误恢复识别](capabilities/D4.md) | 🟡 | `turn_aborted` 已映射 `error.type=cancelled`/ERROR/finish_reason；仍无独立 retry/cancel span |
| E1 Git 仓库信息识别 | 🟡 | UserPromptSubmit 采集 session-scoped remote/branch，Resource 注入、session 隔离和非 Git 清理均有测试；当前缺含 VCS Resource 的可复现 replay。 |
| E2 上游 TRACEPARENT 串联 | ✅ | Turn seed 与 ToolContext 严格消费上游 TRACEPARENT，Turn/root 保留 upstream trace/parent，并向下游传播 child context。 |
| E3 非真实用户 / 外部触发归因识别 | ❌ | 当前未实现 外部触发归因规范要求的 `AIVISION_META_TRIGGER` / `meta.trigger`、`ant.meta.trigger` 与 `agentic.turn.trigger`。 | [E3](capabilities/E3.md) |

## 详细评估

### E2 上游调用 TRACEPARENT 串联 {#e2}

- **评分**: ✅
- **结论**: 见 [独立证据](capabilities/E2.md)：Codex 的 Turn seed、实时 ToolContext、worker/finalizer 均保持上游 W3C trace context。

### E1 Git 仓库信息识别 {#e1}

- **评分**: 🟡
- **结论**: 见 [独立证据](capabilities/E1.md)：Codex 在 `UserPromptSubmit` 根据可信 cwd 获取并保存 remote/branch，后续 Resource 构造和 child-session isolation 复用该快照；当前缺含 VCS Resource 的可复现 replay，revision 也未采集。

### A1 多轮会话识别 + chain 拓扑 {#a1}

- **评分**: ✅
- **结论**: Turn 根 span 唯一 (`gen_ai.span.kind=CHAT` + `gen_ai.operation.name=invoke_agent`) 并携带字符串 `_internal.meta.root="1"`；即使存在上游 `parentSpanId` 也保留逻辑根标记。所有 generation / tool / compaction 全部以根 span_id 为父，构成同级 sibling 链；多次 LLM 调用保留独立 generation span，LLM/TOOL/AGENT 等非 Turn span 不继承 marker。Codex 还把 session-origin trigger snapshot 归一化为 typed `TurnTrigger`，仅在 logical root Turn span 写 `agentic.turn.trigger`；当前 corpus-backed 映射覆盖 `interactive` / `subagent` / `unknown`，不会从 transcript 推断外部自动化。
- **代码引证**:
  - 根 span 类型、逻辑根 marker 与可选上游 parent：`codex/cdxhooks/src/common/otel.rs` 的 `build_root_span`
  - root-only `agentic.turn.trigger` 写入：`codex/cdxhooks/src/common/otel.rs` 的 `build_root_span`
  - Codex session-origin trigger 归一化与 future per-turn override seam：`codex/cdxhooks/src/common/transcript/session_meta.rs` 的 `resolve_session_origin_turn_trigger` / `resolve_turn_trigger`
  - typed trigger snapshot 传播到每个 Turn：`codex/cdxhooks/src/common/transcript/types.rs` 的 `TurnTrigger` / `Turn.trigger`，以及 `codex/cdxhooks/src/common/transcript/turn_builder.rs` 的 `build_turns`
  - 子 span 全部父指向 `root_span_id`：`codex/cdxhooks/src/common/otel.rs` 的 `build_otel_export_request_for_turn`
  - 合成 AGENT 只复制 child Turn 的 input/output，不复制 root marker：`codex/cdxhooks/src/common/otel.rs` 的 `merge_subagent_sessions_into_otel`
  - 多生成支持（按 `assistant_index` 拆分）：`codex/cdxhooks/src/common/transcript/turn_builder.rs:117-128, 285-790`
- **Fixture / test 证据**:
  - `codex/cdxhooks/src/common/transcript/tests.rs`：覆盖 structured subagent、`thread_source=subagent` fallback、`thread_source=user`、user + `exec_command` 仍为 `interactive`、冲突/缺失/unsupported 为 `unknown`，以及 per-turn direct evidence 覆盖 snapshot seam
  - `codex/cdxhooks/src/common/otel.rs::test_root_span_emits_turn_trigger_once_and_children_do_not_inherit`：验证 `agentic.turn.trigger` 只出现在 root Turn span，Resource、Generation、Tool 等 child 不继承
  - `codex/cdxhooks/tests/transcript_integration.rs::turn_trigger_live_and_single_turn_replay_paths_match`：验证 live 与单 Turn replay 路径对同一 Turn 使用相同 trigger 值
- **Replay 例子**:
  - `~/.aivision/cdx/replays/spans_compact.jsonl` 第 1 行：`Turn 1` 之下 5 个 `chat gpt-5.4` + 25 个 `tool Bash`，全部 parent=root（无嵌套）
  - `~/.aivision/cdx/replays/spans_codex-skill.jsonl`：`Turn 1` 之下 4 个 `chat unknown-N` + 3 个 `skill eve-beta` 同级
  - `codex/cdxhooks/tests/fixtures/codex_multi_agent_v2_parent.jsonl`：版本化 multi-agent v2 replay 同时覆盖带 marker 的 Turn 与不带 marker 的合成 AGENT

---

### B1 工具调用精确耗时 {#b1}

- **评分**: ✅
- **结论**: 每个 tool span 都基于 `started_timestamp` / `ended_timestamp` 转纳秒生成 `start_time_unix_nano` / `end_time_unix_nano`，并附 `agentic.tool.duration_ms`，duration 非负。
- **代码引证**:
  - 起止时间映射：`codex/cdxhooks/src/common/otel.rs:311-320`
  - duration 计算并下入属性：`codex/cdxhooks/src/common/otel.rs:364-371`
- **Replay 例子**:
  - `~/.aivision/cdx/replays/spans_compact.jsonl`：`tool Bash` 携带 `agentic.tool.duration_ms`，且 start/end 不相等
  - `~/.aivision/cdx/replays/spans_codex-skill.jsonl`：`skill eve-beta` 同样携带

---

### B2 工具调用错误识别 {#b2}

- **评分**: ✅
- **结论**: tool 失败时 `status.code=ERROR` + `error.type=tool_error` + `status.message`（可读错误）三件齐发；既支持 transcript 显式 `success=false`，也通过 `detect_tool_error_from_output` 识别输出中的 `error:` 字段。
- **代码引证**:
  - 双通道错误：`codex/cdxhooks/src/common/otel.rs:381-391`
  - 错误识别 helper：`codex/cdxhooks/src/common/otel.rs:28-38`
  - mcp_tool_call_end / dynamic_tool_call_response 提取 success 与 output：`codex/cdxhooks/src/common/transcript/turn_builder.rs:565-622`
- **Replay 例子**:
  - `~/.aivision/cdx/replays/spans_tool_error.jsonl` 第 2 行：`tool Bash` `status.code=2`、`status.message="No app terminal session is attached..."`、`error.type=tool_error`
  - 第 3 行：`tool read_mcp_resource` `status.code=2` + `error.type=tool_error`

---

### B3 工具调用输入参数识别 {#b3}

- **评分**: ✅
- **结论**: 每个 tool span 都带 `gen_ai.tool.call.arguments`，原始 JSON-stringified；超长时显式标 `agentic.tool.input.truncated` + `original_length`；MCP/Skill/Builtin 通过 `agentic.tool.{type,name,id,raw_name}` 区分语义。
- **代码引证**:
  - arguments 写入与工具识别：`codex/cdxhooks/src/common/otel.rs:323-361`
  - 截断元数据：`codex/cdxhooks/src/common/otel.rs:42-54, 374`
  - skill TOOL span 识别（当前 tool call 的 Skill input / Bash 命令路径）：`codex/cdxhooks/src/common/otel.rs:505-675`
- **Replay 例子**:
  - `~/.aivision/cdx/replays/spans_codex-skill.jsonl`：`skill eve-beta` 携带 `agentic.tool.type=skill`、`agentic.tool.name=eve-beta`、`agentic.tool.raw_name="exec_command"`
  - `~/.aivision/cdx/replays/spans_rollout-skill-mcp-tool.jsonl`：含 `agentic.tool.type=mcp` 与 mcp 名解析

---

### B4 工具调用输出内容识别 {#b4}

- **评分**: ✅
- **结论**: `gen_ai.tool.call.result` 反映工具原始输出（PTY/MCP 都做了回填），并通过 `agentic.tool.output.truncated` + `original_length` 标记静默截断。
- **代码引证**:
  - result 写入：`codex/cdxhooks/src/common/otel.rs:354`
  - 输出截断元数据：`codex/cdxhooks/src/common/otel.rs:49-54, 374`
  - PTY 会话粘合（`write_stdin` 复用同一 Bash 工具调用）：`codex/cdxhooks/src/common/transcript/turn_builder.rs:701-720, 752-758`
- **Replay 例子**:
  - `~/.aivision/cdx/replays/spans_compact.jsonl`：`tool Bash` 携带较长 result 字符串
  - `~/.aivision/cdx/replays/spans_tool_error.jsonl`：失败工具 result 与 error 共存（参见 B2）

---

### B5 MCP 调用识别 {#b5}

- **评分**: ✅
- **结论**: cdxhooks 解析 codex 协议中的 `mcp_tool_call_end` / `dynamic_tool_call_response` 事件，TOOL span 携带 `agentic.tool.type=mcp` + 解析后逻辑名 + 调用 ID。
- **代码引证**:
  - `codex/cdxhooks/src/common/transcript/turn_builder.rs:565-622` — mcp_tool_call_end / dynamic_tool_call_response 提取
  - `codex/cdxhooks/src/common/otel.rs:323-361` — `agentic.tool.{type,name,id,raw_name}` 写入，type=mcp 由事件类型驱动
- **Replay 例子**: `~/.aivision/cdx/replays/spans_rollout-skill-mcp-tool.jsonl` —— 含 `agentic.tool.type=mcp` 与 mcp 名解析；`~/.aivision/cdx/replays/spans_tool_error.jsonl` 第 3 行 `tool read_mcp_resource` 也是 MCP 调用。

---

### B6 Skill 调用识别 {#b6}

- **评分**: ✅
- **结论**: Codex v2 的 `user_message.text_elements[*].placeholder="$name"` 会生成一个独立、稳定 ID 的 `skill <name>` TOOL span；精确 OMX route 可补强或提供隐式调用证据。原生 `Skill` tool call 仍为 canonical，按同名 occurrence 一对一去重；同名 `.codex/skills/<name>/` 路径只作为 fallback 并被抑制。user-role `<skill>` definition 仅作为同名元数据，未调用的定义不会产生 span，内部 builtin/MCP/subagent 工具也不会被 turn-wide 重分类。
- **代码引证**:
  - `codex/cdxhooks/src/common/transcript/turn_builder.rs::parse_skill_invocations_from_text_elements` / `parse_omx_user_prompt_submit_route` / `finalize_skill_invocations` — 结构化 byte-range 验证、路由关联、定义元数据与 deterministic ordinal
  - `codex/cdxhooks/src/common/otel.rs::build_skill_invocation_span` / `matched_native_skill_calls` — sibling TOOL span、稳定 ID、turn-bounded timing、terminal status 与一对一 native correlation
  - `codex/cdxhooks/src/common/otel.rs::build_tool_span_with_suppression` — 只抑制同名 command-path fallback，保持异名 legacy skill
- **Replay 例子**:
  - `~/.aivision/cdx/replays/spans_session_codex-skill-v2_019f6a12.jsonl` —— 一个 `$analyze` occurrence 生成一个 `skill analyze`；未调用的 `omx-setup` definition 为零 span；内部 `exec_command` 保持 builtin
  - `~/.aivision/cdx/replays/spans_codex-skill.jsonl` —— legacy `skill eve-beta` 兼容路径继续携带 `agentic.tool.type=skill` + `agentic.tool.name=eve-beta`

---

### C1 LLM 精确耗时 {#c1}

- **评分**: ✅
- **结论**: 每个 generation span 用 assistant 时间戳作为 start，end 优先取属于本 generation 的最晚 tool 结束时间，否则取下一条 assistant 时间戳，最后兜底用 fallback；duration_ms 非负。
- **代码引证**:
  - end 时间多级回退：`codex/cdxhooks/src/common/otel.rs:165-195`
  - duration_ms 计算：`codex/cdxhooks/src/common/otel.rs:257-264`
- **Replay 例子**:
  - `~/.aivision/cdx/replays/spans_compact.jsonl`：每个 `chat gpt-5.4` 的 `agentic.llm.duration_ms` 与 start/end 一致

---

### C2 LLM 错误识别 {#c2}

- **评分**: 🟡
- **结论**: tool 失败只标记实际失败的 Tool leaf，不再冒泡到 root/Generation；`turn_aborted` 会在 root/Generation 标为 ERROR + `error.type=cancelled`。仍缺少稳定的 LLM-level API/stream error 数据源，非 abort 的 finish reason 主要由 tool_calls/stop 推断。
- **代码引证**:
  - root/Generation 只按 turn abort 标记错误：`codex/cdxhooks/src/common/otel.rs::build_root_span` / `build_generation_span_with_id`
  - Tool leaf 错误识别：`codex/cdxhooks/src/common/otel.rs::build_tool_span_with_suppression`
- **测试证据**:
  - `hooks::langfuse::realtime::tests::tool_failure_stays_on_tool_span_in_stop_semantics`
  - `hooks::langfuse::realtime::tests::aborted_turn_is_explicitly_cancelled_and_keeps_json_system_instructions`
- **缺失原因**:
  - 当前已验证 Codex transcript 没有稳定的 API/stream_error/max_tokens 原生终态字段；不能通过猜测补值

---

### C3 LLM 用户输入内容识别 {#c3}

- **评分**: ✅
- **结论**: turn root span 的 `gen_ai.input.messages` 仅包含 1 条 user 消息（满足 Langfuse Input 取末尾约定）；每个 generation span 同时携带累计 input 与 `agentic.input.delta`；OpenAI 兼容 message 数组，软截断保护增量消息数。
- **代码引证**:
  - root input 仅 user：`codex/cdxhooks/src/common/message_format.rs:14-21`
  - generation 累计与 delta：`codex/cdxhooks/src/common/message_format.rs:81-129`
  - 软截断 protected_tail：`codex/cdxhooks/src/common/otel.rs:213-217`
- **Replay 例子**:
  - `~/.aivision/cdx/replays/spans_compact.jsonl` 第 1 行 `Turn 1` 与 `chat gpt-5.4` 都携带 `gen_ai.input.messages`，generation 还多带 `agentic.input.delta`

---

### C4 LLM 系统提示词识别 {#c4}

- **评分**: ✅
- **结论**: codex transcript 的 `session_meta.payload.base_instructions.text` 被提取到每个 turn，并以规范 JSON 形式写入 root 与每个 Generation 的 `gen_ai.system_instructions`；privacy mode 下脱敏。
- **代码引证**:
  - session-level提取与 turn 传播：`codex/cdxhooks/src/common/transcript/turn_builder.rs::build_turns`
  - root/Generation 属性构造：`codex/cdxhooks/src/common/otel.rs::build_root_span` / `build_generation_span_with_id`
- **测试证据**:
  - `common::transcript::tests::test_session_meta_base_instructions_are_attached_to_every_turn`
  - `hooks::langfuse::realtime::tests::aborted_turn_is_explicitly_cancelled_and_keeps_json_system_instructions`

---

### C5 LLM 模型输出识别 {#c5}

- **评分**: ✅
- **结论**: `gen_ai.output.messages` 在 turn root（仅最后一条 assistant）与每个 generation span（对应该次回复，含 `tool_calls`、推断 `finish_reason`）上都正确写入。
- **代码引证**:
  - root output：`codex/cdxhooks/src/common/message_format.rs:30-46`
  - generation output：`codex/cdxhooks/src/common/message_format.rs:132-144`
  - `tool_calls` 结构化注入：`codex/cdxhooks/src/common/message_format.rs:52-74`
- **Replay 例子**:
  - `~/.aivision/cdx/replays/spans_compact.jsonl` 第 1 行 `chat gpt-5.4` 的 `gen_ai.output.messages` 含 `content` + `tool_calls[]` + `finish_reason="tool_calls"` 或 `"stop"`

---

### C6 LLM thinking 内容识别 {#c6}

- **评分**: ⚪
- **结论**: codex transcript 中的 `response_item.type=reasoning` 条目，`summary` 字段在所有现有 transcript 中都是空数组 `[]`，真实推理摘要保存在 `encrypted_content`（服务端 ZDR 加密），hooks 无法解密；现实可拿到的「thinking 文本」恒为空。
- **数据源证据**:
  - `grep '"summary":\[\{'` 在 4 份 codex transcript 中 0 命中
  - `~/.aivision/cdx/transcripts/tool_error.jsonl` 等所有 reasoning 事件均形如 `{"type":"reasoning","summary":[],"content":null,"encrypted_content":"gAAAAA..."}`
- **代码引证**:
  - cdxhooks 仅采集 `reasoning_output_tokens` 计数：`codex/cdxhooks/src/common/transcript/turn_builder.rs:51-53`
  - `gen_ai.output.messages` 的 assistant 消息**未**写入 `reasoning_content` 字段：`codex/cdxhooks/src/common/message_format.rs:52-74`
- **数据源缺失根因 (⚪)**: codex CLI 实际运行模式默认会把 reasoning summary 加密为 `encrypted_content`，明文 `summary` 数组为空。即使实现去解析也拿不到原文 —— 属于 SDK / 后端契约性的数据源缺口。

---

### C7 LLM Token usage 识别 {#c7}

- **评分**: ✅
- **结论**: Codex 在每次模型调用后发出的 `token_count.last_token_usage` 会对齐到对应 assistant；没有文本载荷但有 usage 的调用保留 usage-bearing placeholder Generation。每个 Generation 只消费自身 usage，turn root 不重复 token。
- **代码引证**:
  - token_count → assistant/placeholder 对齐：`codex/cdxhooks/src/common/transcript/turn_builder.rs::build_turns`
  - Generation usage 属性：`codex/cdxhooks/src/common/otel.rs::usage_attrs` / `build_generation_span_with_id`
  - realtime batch 与 finalizer 共用 Generation builder：`codex/cdxhooks/src/hooks/langfuse/realtime.rs`
- **测试证据**:
  - `common::transcript::tests::test_token_count_placeholder_uses_own_timestamp`
  - `common::message_format::tests::test_multi_generation_tool_grouping`
  - `hooks::langfuse::realtime::tests::realtime_generation_semantics_match_stop_builder_for_same_assistant`

---

### D1 Compact 识别 {#d1}

- **评分**: ✅
- **结论**: codex 顶层 `type:"compacted"` 事件被识别为独立 compaction span，挂在 turn root 下；`pre_tokens` / `post_tokens` 通过前后 `token_count.last_token_usage.input_tokens` 双向回填；`agentic.compaction.*` 全套属性 + `agentic.tool.type=system` 完整。
- **代码引证**:
  - 顶层 `compacted` 事件解析与 sequence/pre_tokens：`codex/cdxhooks/src/common/transcript/turn_builder.rs:629-650`
  - post_tokens 回填：`codex/cdxhooks/src/common/transcript/turn_builder.rs:464-481`
  - compaction span 构造与全套属性：`codex/cdxhooks/src/common/otel.rs:410-485`
- **Replay 例子**:
  - `~/.aivision/cdx/replays/spans_compact.jsonl` 第 16 行：1 条 `tool compaction` span，`tokens.before=215600 / after=44649 / saved=170951 / sequence=1 / ratio=0.21 / trigger=auto / agentic.tool.type=system`
  - 同文件第 25 行：第 2 次 compaction，`sequence=2 / ratio=0.23`，验证多次 compaction 的累计计数

---

### D2 同步子 agent 调用识别 {#d2}

- **评分**: ✅
- **结论**: cdxhooks 同时识别两代同步子 agent 协议。Codex Desktop 0.133.x 通过 spawn 输出 `agent_id` + targeted `wait_agent.targets` 建立 UUID 关联；multi-agent v2 通过 `sub_agent_activity.event_id → agent_thread_id` 把 spawn call 精确桥接到 child UUID，并以 canonical `agent_path`、global wait intent、typed `agent_message` 的 `FINAL_ANSWER` 证明同步完成。两种协议都把 child 内联为 parent trace 下的 `agent {nickname}`（名称不可用时为 `agent SyncAgent`），child chat/tool span 挂在其下，所有 parent waits 保持 sibling TOOL span；关联含糊时 fail closed，不按 `parent_thread_id` 或字符串包含关系误合并。
- **代码引证**:
  - child metadata、v2 activity/typed message 解析：`codex/cdxhooks/src/common/transcript/turn_builder.rs::parse_subagent_source` / `build_turns`
  - legacy/v2 有序精确关联与生命周期判定：`codex/cdxhooks/src/common/transcript/turn_builder.rs::correlate_subagent`
  - 子 agent 内联合并：`codex/cdxhooks/src/common/otel.rs::merge_subagent_sessions_into_otel`
  - SyncAgent 时长 = 子 agent 真实执行窗口：`codex/cdxhooks/src/common/otel.rs::compute_child_task_window`
  - hook child defer、稳定性重试、导出成功后原子提交：`codex/cdxhooks/src/hooks/langfuse/langfuse_handler.rs::handle_stop` / `merge_child_sessions_into_body`
  - `agentic.tool.type=subagent` / `agentic.subagent.mode=sync` / `agentic.subagent.id` / `agentic.subagent.agent_path` / nickname 等属性齐全
- **Replay 例子**:
  - 0.133.x oracle：`~/.aivision/cdx/replays/spans_sync1.jsonl` —— 单条 trace 内含 `agent SyncAgent` + child chat/tool + parent 两次 targeted wait
  - v2：`~/.aivision/cdx/replays/spans_codex_multi_agent_v2_parent.jsonl` —— activity UUID bridge + `/root/sleep_foreground` + timeout/completed 两次 global wait + typed terminal result `done`
- **回归证据**: `codex/cdxhooks/tests/fixtures/codex_multi_agent_v2_{parent,child}.jsonl` 与 [Stop 回归脚本](cdxhooks/tests/simulate_codex_multi_agent_v2_stop.sh)覆盖 child Stop defer、缺失 child 延迟、export fail/retry、成功后 merged state 原子提交及重复 Stop 去重。

---

### D3 异步子 agent 调用识别 {#d3}

- **评分**: ⚪
- **结论**: 当前 0.133.x 与 multi-agent v2 的已验证 transcript 都是前台同步调用；v2 的 global wait 只能证明本样本存在同步等待意图，不能反向证明协议永远不支持 detached/background。cdxhooks 历史上的 "Turn AsyncAgent" 防御兜底从未被真实数据触发，已在 0.3.0 移除，仅保留有 transcript 证据的 child defer + sync merge 主路径。
- **数据源缺失根因 (⚪)**: 现有样本没有 detached/background 子 agent 的启动、独立完成或跨 trace 关联生命周期。未来若出现此类 transcript，应按新证据重新评估 D3，而不是从 global wait 形状推断。
- **代码引证**:
  - 历史 async 兜底设计文档不在当前仓库版本化 source 中；当前实现的变更记录见 `codex/cdxhooks/CHANGELOG.md:1-80`。
  - 来源边界：不把仓库外设计文档当作可复现代码证据；当前仓库没有 detached/background fixture。

---

### D4 Cancel / Retry 错误恢复识别 {#d4}

- **评分**: 🟡
- **结论**: cancel 边界数据**存在**（`event_msg.type="turn_aborted"`）但被实现处理为「与 task_complete 等价」的轮结束，仅置 `turn.completed=false`，没有任何 `error.type=cancelled / interrupted / timeout`，root status 仍为 OK；retry 维度上没有独立的 api_error / CLIENT span，也无 `agentic.error.retry_attempt` 系列属性 —— 实现层面把 cancel 静默吞掉。
- **代码引证**:
  - `turn_aborted` 检测仅设置 completed 标志：`codex/cdxhooks/src/common/transcript/turn_builder.rs:527-563`
  - root status 仅看 tool 失败、不看 turn aborted：`codex/cdxhooks/src/common/otel.rs:136-143`
  - 全仓无 `cancelled` / `interrupted` / `api_error` 相关 span 构造
- **Replay 例子**: 现有 replay 中没有专门 cancel 例子，但参考 C2 的代码路径，aborted 轮会以正常 OK status 上报，违反 spec「cancel 被记作成功 → 🟡」。
- **缺失原因 (🟡)**: turn_aborted 数据源存在却没有被映射成可识别错误；retry 数据源在 transcript 层比较弱，但 cancel 这条肯定是工程缺口。

## L0-L3 合规等级自评

参考 [CONSTITUTION §3.7](../CONSTITUTION.md) 定义的合规等级阶梯。

| 等级 | 状态 | 阻塞项 |
|------|:----:|--------|
| L0 基础存在性 | 🟡 | L0-4 Resource 4 项中 `ant.agent.env` / `ant.agent.scene` / `ant.username` 部署时未传入（仅 `ant.agent.type` 通过）；L0-1/2/3/5/6/7/8 通过 |
| L1 字段完整性 | 🟡 | L1-1 部分通过（C7 🟡 累计 usage 差分仅写最后一个 assistant，多 generation 前若干 LLM span 无 token）；L1-6 部分通过（C2 tool 错误被错误冒泡到 generation + root，违反"错误挂在实际失败 leaf"）；L1-7 通过 |
| L2 时序与准确性 | 🟡 | L2-3 顺序正确，L2-5 token 单次准确度部分（差分 gap 见 L1-1），L2-6 cache 部分；turn 不重复上报 token 已满足 |
| L3 全量合规 + Golden | ❌ | L3-3 Golden baseline 已建立（5 个 replay 文件覆盖 compact/skill/mcp/tool_error/multi_turn），但 L3-9 跨 agent 等价、L3-12 T1-T10 覆盖未完成；C4 system_instructions 是 ❌ 工程缺口（数据源存在但未读取）阻塞 L3-6 推荐属性覆盖 |

**当前等级**：L1（卡在 L2，主要 gap 是 C7 token 差分未对齐到单次调用 + C2 错误冒泡到父 span）

**下一步**：
1. 部署侧补齐 `ant.agent.env` / `ant.agent.scene` / `ant.username` 三项 Resource 注入；
2. `codex/cdxhooks/src/common/transcript/turn_builder.rs:326-338` 把累计 usage 差分结果对齐到每个 generation（按 token_count 与 assistant 时间对齐），消除前若干 LLM span 无 token 的现象；
3. `cdxhooks/src/common/otel.rs:271-283, 136-143` 移除 tool 错误向 generation/root 的冒泡，让 error 仅留在实际失败的 TOOL leaf；
4. 在 transcript 解析层把 `session_meta.base_instructions.text` 抽出并写入 `gen_ai.system_instructions`（补 C4）；
5. 把 `turn_aborted` 映射为 `error.type=cancelled` + `status=ERROR`，闭合 D4 cancel 缺口。

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
