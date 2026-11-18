# Hermes 能力评估

← 返回 [CAPABILITIES.md](../CAPABILITIES.md)

本轮原生验收固定使用 PyPI `hermes-agent==0.18.2`（包内发布日期
`2026.7.7.2`），通过官方 headless CLI 和 production plugin loader 执行。
主调用额外使用官方 `llm_execution` middleware 获取同调用 reported usage：
只在内存暂存合法计数及关联 ID，保持宿主返回值、异常和取消行为。缺失计数以
`agentic.usage.*.missing=true` 区分于真实零值；未知字段不生成完整总计或 token metric。
`agentic.model_call.id` 保留宿主 API 请求 ID，并有独立的 live/replay 源字段测试；
冻结的 OpenCode 共享事实快照仍逐字段比较其原始契约。
Span 和 API event 使用 canonical cache read/write 与 reasoning token 字段；
OpenAI fresh input metric 扣除已报告 cache 子集，reasoning 仍属于 output，
不再次计入完整总量。这不表示已获得独立 thinking/reasoning 内容。

新版 Hermes `main` 的辅助调用 hook 仅在宿主声明时注册，可观测已有 turn 中的
辅助尝试及 retry；当前辅助 usage 经过宿主归一化，零值 reportedness 和流式 usage
保持未知。0.18.2 没有这两个 hook，因此不能把新 hook 的源码契约测试称为原生
辅助调用完整覆盖；idle/cron 没有 turn 身份时也不推断会话关联。

原生假 provider 验收覆盖 tool-loop、multi-turn/resume、tool-error、stream、cancel、
unknown-usage、zero-usage、cache-usage。Hosted NVIDIA、平台三信号读回和诊断
能力分别验收；AgentLens 当前 coding registry 未登记 Hermes，采集通过不等于诊断可用。
官方 provider、版本差异、短期 ingest token 与 source-install 配置见
[README](README.md#collector-development-setup)。
Cancel 使用官方 ACP `session/cancel`，依赖 `hermes-agent[acp]==0.18.2`；
macOS 进程已实证，Windows 需独立矩阵验收，不用控制台 signal 推断通过。

## 评分汇总

✅ 11 / 🟡 4 / ❌ 1 / ⚪ 5 （共 21 项）

| 能力 | 评分 | 一句话结论 |
|------|------|-----------|
| A1 多轮会话 + chain | ✅ | 每个 turn 生成唯一 CHAT/invoke_agent root，API/tool/subagent span 作为 root sibling 挂载。 |
| B1 工具耗时 | ✅ | 每个 tool terminal 用原始 `duration_ms` 或 start/end nano 计算 `agentic.tool.duration_ms`。 |
| B2 工具错误 | ✅ | 工具失败通过 `status=ERROR` + `error.type` + 可读错误消息标在 tool span 自身。 |
| B3 工具输入参数 | ✅ | tool start 写结构化 `gen_ai.tool.call.arguments`，缺失和截断显式标记。 |
| B4 工具输出内容 | ✅ | tool end 写 `gen_ai.tool.call.result`，失败输出与 `error.type` 保持同 span。 |
| B5 MCP 调用识别 | 🟡 | `mcp__` / `mcp:` / `mcp_` 前缀可分类为 MCP，但依赖命名启发式，非协议层识别。 |
| B6 Skill 调用识别 | 🟡 | `Skill` / `skill:` / `skill-creator` 可分类为 skill，但仅覆盖命名可识别场景。 |
| C1 LLM 耗时 | ✅ | 每个 API request span 写单次 `agentic.llm.duration_ms`，来自 API duration 或 start/end nano。 |
| C2 LLM 错误 | 🟡 | API terminal error 写 `status=ERROR` + `error.type`，但 golden 中 api-error 场景仍为未授权 skip，缺真实正向 replay。 |
| C3 LLM 用户输入 | ✅ | turn root 和 LLM span 均写 OpenAI 兼容 `gen_ai.input.messages`。 |
| C4 LLM 系统提示词 | ✅ | Hermes observer payload 可暴露 request/system，LLM span 写 JSON 序列化 `gen_ai.system_instructions`。 |
| C5 LLM 模型输出 | ✅ | LLM span 与 turn root 写 `gen_ai.output.messages`，模型 response/finish reason 保留。 |
| C6 LLM thinking | ⚪ | Hermes/OpenAI SDK 当前 observer 数据源不暴露独立 thinking/reasoning block。 |
| C7 LLM Token usage | ✅ | 主调用按同调用 reported counters 区分 missing/zero；OpenAI cache 是 prompt 子集，Anthropic fresh+cache 合计；辅助零值保持未知。 |
| D1 Compact | ⚪ | Hermes v0.18.2 无 documented compaction hook，golden finalizer 将 compaction 标为 honest skip。 |
| D2 同步子 agent | ✅ | `subagent_start/stop` 生成 AGENT/invoke_agent span，携带子 agent 身份与耗时/结果。 |
| D3 异步子 agent | ⚪ | 当前 Hermes observer 数据源没有可稳定跨 trace 关联的异步 subagent 链路。 |
| D4 Cancel/Retry | 🟡 | cancel/interrupted 可结束未完成 API/tool/subagent 并写 `error.type=cancelled`；retry 只保留最终 API error，缺逐次 retry span。 |
| E1 Git 仓库信息 | ⚪ | Hermes 是常驻 observer/runtime，本轮不要求按会话推断 Git worktree。 |
| E2 上游 TRACEPARENT 串联 | ⚪ | Hermes v0.18.2 observer contract 没有 W3C upstream trace context source。 |
| E3 非真实用户 / 外部触发归因识别 | ❌ | 当前未实现 外部触发归因规范要求的 `AIVISION_META_TRIGGER` / `meta.trigger`、`ant.meta.trigger` 与 `agentic.turn.trigger`。 | [E3](capabilities/E3.md) |

## 详细评估

### E2 上游调用 TRACEPARENT 串联 {#e2}

- **评分**: ⚪
- **结论**: 见 [独立证据](capabilities/E2.md)：Hermes v0.18.2 observer contract 没有 W3C upstream trace context source。
- **代码引证**:
  - `hermes/observ_hermes/pipeline.py:59` — canonical pipeline 只接收 observer hook payload，没有 traceparent 输入。
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/provenance.json` — pinned observer golden 不包含 upstream W3C context。
- **数据源缺失根因**: Hermes v0.18.2 observer SDK 不暴露 session-scoped 上游 trace context。

### E3 非真实用户 / 外部触发归因识别 {#e3}

- **评分**: ❌
- **结论**: 见 [独立证据](capabilities/E3.md)：本轮仅登记 外部触发归因能力标准和统一默认基线，未修改 Hermes runtime、配置或 replay 实现。
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/resource.py:14-24` — canonical Resource builder 没有 E3 meta-trigger 字段。
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/provenance.json` — 当前 golden 没有 E3 trigger 字段，不能作为正向证据。
- **数据源缺失根因**: 本轮仅新增能力标准，Hermes observer/replay 尚未接入显式外部触发归因契约。

### E1 Git 仓库信息识别 {#e1}

- **评分**: ⚪
- **结论**: 见 [独立证据](capabilities/E1.md)：Hermes 按常驻 observer 产品边界不要求 E1，当前 observer contract 没有 session-scoped worktree/VCS source。
- **代码引证**:
  - `hermes/observ_hermes/pipeline.py:15` and `hermes/observ_hermes/pipeline.py:59` — observer pipeline 只接收 session/task/turn lifecycle payload，没有 worktree/VCS 输入。
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/provenance.json` — 当前 golden 记录的是 observer 场景；按常驻程序边界不生成 Git 仓库探测样本。
- **数据源缺失根因**: Hermes v0.18.2 observer contract 未暴露 session-scoped worktree/repository provenance；常驻 observer 进程的 cwd 不能代表任一会话仓库。

### A1 多轮会话识别 + chain 拓扑 {#a1}

- **评分**: ✅
- **结论**: `_turn()` 创建唯一 root span，API/tool/subagent 均以 turn span key 作为 parent，形成 sibling 拓扑。
- **续接边界**: 0.2.2 生产采集使用独立 SQLite 账本，跨进程保持同一采集配置下的已观测 turn 序号；不补算安装前历史，partial replay 仍在输入范围内编号。独立 chat 身份与原始时间保持不变，账本删除、故障及配置隔离边界见 [README](README.md)。
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:183` — `_turn()` 按 `turn_id` / `parent_turn_id` / `task_id` 管理 active turn。
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:206` — root attributes 写 `CHAT` + `invoke_agent`。
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:295` — LLM span parent 指向 `turn.span_key`。
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:460` — TOOL span parent 指向 `turn.span_key`。
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:564` — AGENT span parent 指向 `turn.span_key`。
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180521_514a02_91af86abba47ce67.jsonl` — subagent scenario includes CHAT root plus LLM/TOOL/AGENT sibling spans.
  - `~/.aivision/hermes/golden/provenance.json` — `subagent` scenario passed with validation `violations=0`.

### B1 工具调用精确耗时 {#b1}

- **评分**: ✅
- **结论**: tool span end 写入非负 `agentic.tool.duration_ms`，优先使用 Hermes payload 的 `duration_ms`，否则由 observed start/end 计算。
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:169` — `_duration_ms()` 保证 provided duration 或 start/end 差值非负。
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:483` — tool terminal 计算 duration。
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:492` — tool span end 写 `agentic.tool.duration_ms`。
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180445_67a2ed_19e535e386b7806c.jsonl` — tool-success replay includes `agentic.tool.duration_ms`.
  - `~/.aivision/hermes/golden/provenance.json` — `tool-success` scenario passed with validation `violations=0`.

### B2 工具调用错误识别 {#b2}

- **评分**: ✅
- **结论**: tool failure is detected from explicit error fields, status, or structured result failure and marks the tool span itself as ERROR with `error.type`.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/normalize.py:96` — `_tool_result_failure()` detects non-zero exit/error in structured tool result.
  - `hermes/hermes-core/aivision_hermes_core/normalize.py:205` — post-tool normalization inspects structured result failure before deriving `error_type=tool_error`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:487` — terminal tool failure detection.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:498` — failed tool span writes `error.type`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:502` — failed tool span ends with `status=ERROR`.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180455_9b89fa_e130f18894822755.jsonl` — tool-error replay includes `error.type` on the tool span.
  - `~/.aivision/hermes/golden/provenance.json` — `tool-error` scenario passed with validation `violations=0`.

### B3 工具调用输入参数识别 {#b3}

- **评分**: ✅
- **结论**: tool start and tool_call event preserve arguments as canonical JSON, with explicit missing/truncated markers instead of silent empty placeholders.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:444` — records whether `args` was present.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:456` — span start writes `gen_ai.tool.call.arguments` or `agentic.tool.input.missing`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:458` — writes `agentic.tool.input.truncated` when projection truncated args.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:509` — tool_result event repeats stored arguments from start state.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180445_67a2ed_19e535e386b7806c.jsonl` — tool-success replay includes `gen_ai.tool.call.arguments`.
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180505_4a68ef_e461877473953e35.jsonl` — multi-tool replay includes arguments for several sibling tool calls.

### B4 工具调用输出内容识别 {#b4}

- **评分**: ✅
- **结论**: tool terminal writes actual result as canonical JSON; missing result is encoded as JSON null and errors remain on the same span.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:490` — records whether `result` was present.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:494` — span end writes `gen_ai.tool.call.result`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:496` — writes output truncation marker when needed.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:513` — tool_result event writes the same result.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180445_67a2ed_19e535e386b7806c.jsonl` — successful tool replay includes `gen_ai.tool.call.result`.
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180455_9b89fa_e130f18894822755.jsonl` — failed tool replay keeps result and error fields on the tool path.

### B5 MCP 调用识别 {#b5}

- **评分**: 🟡
- **结论**: Hermes can tag MCP-like tools, but classification is name-prefix based rather than protocol-layer evidence, so non-prefixed MCP calls may be missed.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/normalize.py:66` — `classify_tool()` maps `mcp__`, `mcp:`, and `mcp_` names to `mcp`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:439` — tool type is read from normalized correlation.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:453` — tool span receives identity attributes.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:792` — `_tool_identity()` writes `agentic.tool.type/name/id`.
- **Runtime evidence**:
  - `hermes/tests/test_normalize.py:81` and `hermes/tests/test_semantic_state.py:149` — versioned tests prove that a `mcp__server__read` observer payload becomes `agentic.tool.type=mcp`.
  - `~/.aivision/hermes/golden/provenance.json` — the real `mcp-tool` probe is honestly `skipped` because no MCP surface was configured; accepted runtime replays therefore contain no positive MCP call.
- **缺口**: prefix-only classification cannot prove every protocol-level MCP call is covered, and a real configured-MCP replay is still required.

### B6 Skill 调用识别 {#b6}

- **评分**: 🟡
- **结论**: Hermes can tag explicitly named skill tools, but it does not model command placeholders or derived skill-loading observations.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/normalize.py:66` — `classify_tool()` maps `Skill`, `skill:`, and `skill-creator` names to `skill`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:439` — unknown tool types are safely downgraded to builtin.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:792` — `_tool_identity()` writes `agentic.tool.type=skill` when classified.
- **Runtime evidence**:
  - `hermes/tests/test_normalize.py:82` — versioned test proves that a physical tool named `Skill` is classified as `skill`.
  - `~/.aivision/hermes/golden/provenance.json` — the real `skill-call` probe is honestly `skipped` because no installed skill surface was available; accepted runtime replays therefore contain no positive skill tool call.
- **缺口**: command-style skills without the recognized names are not proven covered, and a real installed-skill replay is still required.

### C1 LLM 精确耗时 {#c1}

- **评分**: ✅
- **结论**: each API request creates one LLM span and ends it with non-negative duration.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:278` — API request state stores start timestamp/nano.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:313` — terminal API duration is converted from seconds or derived from timestamps.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:344` — span end writes `agentic.llm.duration_ms`.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180439_d40d6d_464e0983698b0378.jsonl` — basic-llm replay includes `agentic.llm.duration_ms`.
  - `~/.aivision/hermes/golden/provenance.json` — `basic-llm` scenario passed with validation `violations=0`.

### C2 LLM 错误识别 {#c2}

- **评分**: 🟡
- **结论**: implementation writes error status and `error.type` for `api_request_error`, but current accepted golden lacks a real provider-error replay because the api-error probe is skipped when unauthorized.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/normalize.py:75` — `classify_error()` maps rate limit/auth/timeout/cancel/network/validation categories.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:300` — `api_request_error` routes to `_finish_api(is_error=True)`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:363` — error LLM span writes `error.type`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:367` — error LLM span ends with `status=ERROR`.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/provenance.json` — `api-error` is an honest skipped scenario due to missing authorization, so C2 lacks positive replay coverage.
  - `hermes/tests/fixtures/hooks/v0.18.2/observer_envelopes.json:243` and `hermes/tests/test_semantic_state.py:143` — versioned v0.18.2-shaped fixture/test exercise the provider-error serialization path without presenting it as real golden evidence.
- **缺口**: needs one accepted real `api_request_error` replay before upgrading to ✅.

### C3 LLM 用户输入内容识别 {#c3}

- **评分**: ✅
- **结论**: turn root gets user-facing input and API request span gets the actual request message array.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:230` — `pre_llm_call` stores user message and emits user_prompt event.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:260` — API request uses `request_messages` when present.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:292` — LLM span writes `gen_ai.input.messages`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:638` — turn root end writes user-facing `gen_ai.input.messages`.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180439_d40d6d_464e0983698b0378.jsonl` — basic LLM replay includes `gen_ai.input.messages`.
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180521_514a02_91af86abba47ce67.jsonl` — subagent replay includes request input messages.

### C4 LLM 系统提示词识别 {#c4}

- **评分**: ✅
- **结论**: Hermes observer exposes request messages/body fields; the semantic engine extracts system/developer content or request body `system` and writes `gen_ai.system_instructions`.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/normalize.py:25` — `pre_api_request` payload allowlist includes `request` and `request_messages`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:266` — extracts system/developer messages.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:271` — falls back to request body `system`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:293` — LLM span writes JSON `gen_ai.system_instructions` when non-empty.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180439_d40d6d_464e0983698b0378.jsonl` — replay includes `gen_ai.system_instructions`.
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180505_4a68ef_e461877473953e35.jsonl` — multi-tool replay includes `gen_ai.system_instructions`.

### C5 LLM 模型输出识别 {#c5}

- **评分**: ✅
- **结论**: API terminal output and turn final assistant response are serialized as OpenAI-compatible output message arrays.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:357` — LLM span end writes `gen_ai.output.messages` from `assistant_message`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:381` — api_request event writes output messages.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:640` — turn root end writes final assistant output.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180439_d40d6d_464e0983698b0378.jsonl` — basic-llm replay includes `gen_ai.output.messages`.
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180445_67a2ed_19e535e386b7806c.jsonl` — tool-success replay includes model output around tool use.

### C6 LLM thinking 内容识别 {#c6}

- **评分**: ⚪
- **结论**: Hermes observer payloads expose assistant messages, but no independent thinking/reasoning block is provided by the current Hermes/OpenAI SDK hook data.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/normalize.py:30` — `post_api_request` admits `assistant_message` and response fields, not a thinking block.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:357` — output serialization passes through assistant_message only.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180439_d40d6d_464e0983698b0378.jsonl` — output messages are present without a separate reasoning field.
  - `~/.aivision/hermes/golden/provenance.json` — accepted scenarios do not include a thinking/reasoning source field.
- **数据源缺失根因**: current Hermes observer callbacks do not expose a separate thinking block.

### C7 LLM Token usage 识别 {#c7}

- **评分**: ✅
- **结论**: terminal API usage is normalized into per-call input/output tokens on the LLM span; turn root does not duplicate usage attributes.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:319` — reads `usage` from API terminal payload.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:324` — handles fresh/cache/prompt token fields.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:349` — writes `gen_ai.usage.input_tokens`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:353` — writes `gen_ai.usage.output_tokens`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:637` — root end attributes include input/output messages but no usage fields.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180439_d40d6d_464e0983698b0378.jsonl` — LLM span includes `gen_ai.usage.input_tokens` / `output_tokens`.
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180505_4a68ef_e461877473953e35.jsonl` — multi-tool replay includes per-call usage fields.

### D1 Compact 识别 {#d1}

- **评分**: ⚪
- **结论**: Hermes v0.18.2 has no documented observer hook for compaction, so the golden workflow records compaction as an honest skip.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/normalize.py:18` — supported hook set has session/API/tool/subagent hooks, no compaction hook.
  - `hermes/tests/test_golden_manifest.py:74` — finalizer test expects compaction status `skipped`.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/provenance.json` — `compaction` scenario is skipped with documented reason.
  - `~/.aivision/hermes/golden/replays/diagnostics_session_20260720_181520_851adf_e26e2371389a839f.ndjson` — accepted diagnostic-only lifecycle output contains no fabricated compaction span.
- **数据源缺失根因**: current Hermes observer API does not emit compaction boundaries or before/after token counts.

### D2 同步子 agent 调用识别 {#d2}

- **评分**: ✅
- **结论**: synchronous subagent lifecycle creates a dedicated AGENT span with child identity and terminal outcome.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/normalize.py:43` — `subagent_start` payload includes child goal/role.
  - `hermes/hermes-core/aivision_hermes_core/normalize.py:47` — correlation includes parent and child subagent/session identifiers.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:543` — `subagent_start` creates a subagent state.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:563` — AGENT span writes `invoke_agent` semantics.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:577` — subagent end writes duration/outcome.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:804` — subagent identity writes child id/name/session key.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180521_514a02_91af86abba47ce67.jsonl` — subagent scenario includes AGENT span with `agentic.subagent.*`.
  - `~/.aivision/hermes/golden/provenance.json` — `subagent` scenario passed with validation `violations=0`.

### D3 异步子 agent 调用识别 {#d3}

- **评分**: ⚪
- **结论**: accepted Hermes data does not expose a stable async child trace link/span-link/session join distinct from synchronous subagent lifecycle.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:543` — subagent modeling is scoped to active parent turn state.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:563` — subagent span is represented in the same parent trace.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:804` — identity contains child session key, but no OTel span link is constructed.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/replays/spans_session_20260720_180521_514a02_91af86abba47ce67.jsonl` — replay proves same-turn subagent modeling only.
  - `~/.aivision/hermes/golden/provenance.json` — accepted scenarios contain `subagent`, not a separate async-subagent scenario.
- **数据源缺失根因**: no accepted observer evidence currently identifies fire-and-forget subagents with cross-trace correlation.

### D4 Cancel / Retry 错误恢复识别 {#d4}

- **评分**: 🟡
- **结论**: cancellation/interruption is explicit for pending API/tool/subagent spans, but API retries are not expanded into one span/event per attempt.
- **代码引证**:
  - `hermes/hermes-core/aivision_hermes_core/normalize.py:75` — `classify_error()` recognizes cancel/interrupt, timeout, rate limit, auth, and network classes.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:627` — closing a turn cancels all pending API/tool/subagent spans.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:677` — cancelled API span ends with `status=ERROR`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:687` — cancelled API span writes `error.type=cancelled`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:728` — cancelled tool span ends with `status=ERROR`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:772` — cancelled subagent span writes `error.type=cancelled`.
  - `hermes/hermes-core/aivision_hermes_core/semantic.py:303` — `api_request_error` terminates the current request once, not each retry attempt.
- **Runtime evidence**:
  - `~/.aivision/hermes/golden/provenance.json` — `api-error` scenario is skipped without authorization, and accepted scenarios validate with 0 violations.
  - `hermes/tests/test_semantic_state.py:267` — versioned state-machine test proves pending work is finalized with `agentic.error.category=cancelled`; this is not claimed as real retry golden evidence.
- **缺口**: retry attempt/count payload is not represented as independent retry spans; needs real provider-error/retry golden coverage.

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
