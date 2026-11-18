# openclaw 能力评估

← 返回 [CAPABILITIES.md](../CAPABILITIES.md)

> 评估对象：`@aivision/observ-openclaw@0.5.2` 与独立 `openclaw-replay`。
>
> 代码证据：`openclaw/observ-openclaw/src/`；Golden 证据：
> `~/.aivision/ocw/golden/transcripts/`、`~/.aivision/ocw/golden/replays/spans_session_golden-superloop.jsonl`、
> `~/.aivision/ocw/golden/replays/events_session_golden-superloop.jsonl`。
>
> 本次评估已废弃旧的 `~/.aivision/ocw/replays/spans_hooks.jsonl` 结论。旧样本来自扁平拓扑修复之前，不能代表当前实现。

## 评分汇总

✅ 12 / 🟡 5 / ❌ 1 / ⚪ 3 （共 21 项）

| 能力 | 评分 | 当前结论 | 主要证据 |
|------|:----:|----------|----------|
| [A1 多轮会话 + chain 拓扑](capabilities/A1.md) | ✅ | 主 Turn 下的 LLM/Tool 为 sibling；同步子 agent 单独表现为 `agent SyncAgent`，不再产生子 Turn/announce Turn | Golden 6 CHAT / 8 LLM / 10 TOOL / 1 AGENT；`subagent-correlation.test.ts` |
| [B1 工具耗时](capabilities/B1.md) | ✅ | TOOL span 记录 framework duration，缺失时使用墙钟时间 | `openclaw/observ-openclaw/src/hooks/tool.ts:401-403`；Golden TOOL spans |
| [B2 工具错误](capabilities/B2.md) | ✅ | 失败工具同时具有 ERROR status、`error.type`、`error.message` | Golden 2 个失败 `builtin read` span |
| [B3 工具输入参数](capabilities/B3.md) | ✅ | 工具参数写入 `gen_ai.tool.call.arguments`，支持隐私配置和截断标记 | `openclaw/observ-openclaw/src/hooks/tool.ts:92-115`；Golden tool spans |
| [B4 工具输出内容](capabilities/B4.md) | ✅ | 工具结果写入 `gen_ai.tool.call.result`，支持截断元数据 | `openclaw/observ-openclaw/src/hooks/tool.ts:366-399`；Golden tool spans |
| [B5 MCP 调用识别](capabilities/B5.md) | 🟡 | `mcp__server__tool` 分类实现完整，但当前 Golden 缺真实 MCP 调用样本 | `openclaw/observ-openclaw/src/semconv.ts:438-477`；分类逻辑测试/类型检查 |
| [B6 Skill 调用识别](capabilities/B6.md) | ✅ | session 缓存 `<available_skills>` location 映射；成功 read 后追加 linked 0ms Skill span，同时保留 builtin read | `skill-detection.ts`；清洗 fixture replay `builtin read` → `skill weather` |
| [C1 LLM 耗时](capabilities/C1.md) | ✅ | 每次 provider call 单独记录 `agentic.llm.duration_ms` 和 TTFB | `openclaw/observ-openclaw/src/hooks/model-call.ts:87-92`；Golden 8 个 LLM span |
| [C2 LLM 错误](capabilities/C2.md) | 🟡 | `model_call_ended` 保留真实 `failureKind`，但当前 Golden 缺 provider-error replay | `openclaw/observ-openclaw/src/hooks/model-call.ts:94-110`；`api-error-semantics.test.ts` |
| [C3 LLM 用户输入](capabilities/C3.md) | ✅ | 每个 LLM span 和 Turn 均记录该次调用对应的真实输入消息；active-Turn compaction 后只重建消息选择边界，不重置 session/Turn | `llm.ts` input attributes；Golden 8/8 LLM spans；`compaction-delta-semantics.test.ts` |
| [C4 LLM 系统提示词](capabilities/C4.md) | ✅ | LLM span 记录实际 system prompt，缺失时从 history messages 回填 | `openclaw/observ-openclaw/src/hooks/llm.ts:149-155`；Golden 8/8 LLM spans |
| [C5 LLM 模型输出](capabilities/C5.md) | ✅ | LLM/Turn 记录 assistant output 与真实 finish reason，agent_end 可按同 epoch 调用顺序补缺且不覆盖 authoritative output | `llm.ts`、`session.ts`；Golden 8/8 LLM spans；`compaction-delta-semantics.test.ts` |
| [C6 LLM thinking](capabilities/C6.md) | ⚪ | OpenClaw hook 只暴露 `assistantTexts`，不暴露 reasoning/thinking 原始块 | `llm.ts` hook payload |
| [C7 LLM Token usage](capabilities/C7.md) | ✅ | token 一一对应单次 LLM 调用；CHAT/TOOL/AGENT 不重复写 token | Golden：8 LLM 有 token，6 CHAT + 10 TOOL + 1 AGENT 均无 token |
| [D1 Compact](capabilities/D1.md) | 🟡 | before/after compaction 建模完整；active Turn 使用内部 compaction epoch/selective rebase，between-turn 仍使用 synthetic Turn；当前 Golden 未覆盖 compaction | `compaction.ts`；hook 注册 `openclaw/observ-openclaw/src/hooks/index.ts:318-319`；`compaction-delta-semantics.test.ts` |
| [D2 同步子 agent](capabilities/D2.md) | ✅ | 父 Turn 下创建 `agent SyncAgent`，关联 child session/run，并承载子 LLM/Tool | Golden subagent trace；`subagent-correlation.test.ts` |
| [D3 异步子 agent](capabilities/D3.md) | ⚪ | 当前数据源没有稳定的 fire-and-forget 子 agent 跨 trace link | OpenClaw hook contract |
| [D4 Cancel / Retry](capabilities/D4.md) | 🟡 | provider 错误分类与同 epoch retry input 快照复用已实现；compaction 阻断跨 epoch retry 克隆；cancel、retry 序号/延迟仍不完整，且缺真实时序样本 | `model-call.ts`；`response-id-semantics.test.ts`；`compaction-delta-semantics.test.ts`；`api-error-semantics.test.ts` |
| E1 Git 仓库信息识别 | ⚪ | OpenClaw 是常驻 gateway/service，本轮不要求按每个会话推断 Git worktree。 | [E1](capabilities/E1.md) |
| E2 上游 TRACEPARENT 串联 | 🟡 | 可捕获 W3C header 与 diagnostic parent，但尚未将 W3C traceparent 直接用于 Turn parent 拓扑。 | [E2](capabilities/E2.md) |
| E3 非真实用户 / 外部触发归因识别 | ❌ | 当前未实现 `AIVISION_META_TRIGGER` / `meta.trigger`、`ant.meta.trigger` 与 `agentic.turn.trigger`。 | [E3](capabilities/E3.md) |

## 当前 Golden 基线

`spans_session_golden-superloop.jsonl` 当前包含 26 个 span：

| Span 类型 | 数量 | 说明 |
|-----------|:----:|------|
| CHAT | 6 | 六个主场景 Turn；不包含子 agent 的伪 Turn |
| LLM | 8 | 每次真实 provider call 一个 span |
| TOOL | 11 | 10 个物理调用 + 1 个派生 Skill load |
| AGENT | 1 | `agent SyncAgent`，表达同步委派 |

`events_session_golden-superloop.jsonl` 当前包含 35 个事件，其中包括：

- `subagent_spawned` × 1
- `subagent_ended` × 1
- `api_request` × 8
- `tool_call` × 9（另 1 个物理调用缺失 before hook，仅保留 `tool_result`）
- `tool_result` × 10
- `user_prompt` × 6

Golden replay 已通过 weaver 校验：61 samples、0 violations、0 improvements、0 semconv issues。

## 关键能力证据

### A1 多轮会话识别 + chain 拓扑 {#a1}

- **评分**：✅
- 主会话的 `LLM` 和 `TOOL` span 都以 Turn span 为直接 parent；同一轮中的多次 provider call 保持在同一 trace。
- `session.ts` 创建 `Turn N` CHAT root；`llm.ts` 与 `tool.ts` 均使用 `turn.requestContext`。
- 同步子 agent 例外地位于 `agent SyncAgent` 下：这是委派内部结构，不是旧版多余的 `agent.main` wrapper。
- Golden subagent 场景只产生一个主 Turn 和一个 SyncAgent，不再产生子 `Turn 1` 或 completion announce Turn。

### B1-B5 工具语义 {#b1}

<a id="b2"></a><a id="b3"></a><a id="b4"></a><a id="b5"></a>

- **B1 ✅**：`agentic.tool.duration_ms` 在工具结束时写入。
- **B2 ✅**：Golden tool-error 中两个 `builtin read` span 都是 status ERROR，并带 `error.type=tool_error`。
- **B3 ✅**：工具开始时发出 `tool_call`，并通过 `gen_ai.tool.call.id` 与终态 `tool_result` 关联；参数具有真实 JSON、`{}` 或 `[REDACTED]` 三态，且支持 input truncation metadata。缺失 before hook 时不会从终态回填伪造的 `tool_call`。
- **B4 ✅**：结果写入 `gen_ai.tool.call.result`，长结果附带 original length/truncated 标记。
- **B5 🟡**：`classifyTool()` 统一产生 `agentic.tool.type/name/raw_name/id`，事件和 metrics 复用同一次分类结果；当前 Golden 无真实 MCP 样本，尚未满足双证据。

### B6 Skill 调用识别 {#b6}

- **评分**：✅
- `llm_input.systemPrompt.<available_skills>` 被解析为 session 级三态 location 映射；有效 snapshot 原子替换，缺失/无效 snapshot 保留 last-known-good，reset/end/idle cleanup 防止跨会话污染。
- 通用 `read` 始终保留 builtin 分类和真实耗时。仅在成功完成且路径精确命中 session catalog 时，才在 read 结束后追加一个 linked 0ms Skill span；没有可用 catalog 时才允许严格 `/.../skills/<name>/SKILL.md` fallback。失败读取不产生 Skill span，因为这里表达的是“Skill 指令成功加载”，不是“尝试调用 Skill”。
- read 独占原 `tool_result` 与 RED count/duration，派生 Skill 不重复计数。Skill span 使用 `agentic.tool.derived=true`、独立 synthetic `gen_ai.tool.call.id`、`gen_ai.tool.name=Skill`、catalog 逻辑名和 `agentic.tool.raw_name=read`；full 模式输出规范化 `{"skill":"<name>"}` / `{"status":"loaded"}`，privacy 模式显式脱敏。按照 cchooks/shared 规范，`agentic.tool.id` 优先真实来源 `toolCallId`，无真实 ID 才合成 `skill-{name}`。
- **代码证据**：`openclaw/observ-openclaw/src/hooks/skill-detection.ts`；`llm.ts` session cache 更新；`tool.ts` success-gated derived span emission；`skill-detection.test.ts` parser/path/cache/failure/event/metric coverage。
- **Replay 证据**：`openclaw/observ-openclaw/scripts/fixtures/skill-catalog-read.jsonl` 产生先后两个 TOOL spans：真实耗时的 `builtin read` 和 linked 0ms `skill weather`；Skill raw name 为 `read`、逻辑名 `weather`、真实 identity 为 `call-weather`，call ID 为独立的 `skill-load-call-weather`。
- **能力边界**：已加载 Skill 的后续无读取复用不可观测；下游 `web_fetch`/`exec` 不继承 Skill 分类；`skill_workshop` 仍是普通 builtin。

### C1-C7 LLM 语义 {#c1}

<a id="c2"></a><a id="c3"></a><a id="c4"></a><a id="c5"></a><a id="c6"></a><a id="c7"></a>

- **C1 ✅**：`model_call_started` / `model_call_ended` 提供 per-call 生命周期及 duration/TTFB；start hook 缺失时按框架 `durationMs` 回填 span startTime，duration 也缺失时才使用本地时间计算。
- **C2 🟡**：错误写在真实失败的 LLM 叶子 span，`rate_limit_error` 等 provider failureKind 不再被压成通用 `llm_error`；当前只有代码和测试证据，缺真实 provider-error replay。
- **C3 ✅**：每个 LLM span 的 `gen_ai.input.messages` 按 provider call 记录：首次调用仅含当前 user，普通 tool continuation 仅含上次 assistant/tool 新增消息；同一 runtime run 内、前一 provider attempt 已 ERROR 且无介入 tool 语义时，retry 复用前一 attempt 的 write-once input 快照。active-Turn compaction 后，内部 `compactionEpoch` 只重建历史派生的 assistant boundary；`after_compaction` 的 count 只作为诊断，不能直接成为新 baseline。`agent_end` 只补缺，不覆盖 authoritative input/output 快照；Turn 同时保留用户入口消息，不提供累加模式开关。
- **C4 ✅**：8/8 Golden LLM spans 具有 `gen_ai.system_instructions`。
- **C5 ✅**：8/8 Golden LLM spans 具有 `gen_ai.output.messages` 与 `gen_ai.response.finish_reasons`。
- **C6 ⚪**：hook payload 不包含 reasoning block，因此不能补造 thinking。
- **C7 ✅**：8/8 LLM spans 具有 input/output tokens；非 LLM span 全部没有 token 属性，避免重复聚合。

### D1 Compact 识别 {#d1}

- **评分**：🟡
- `before_compaction` 在当前 Turn 下创建 `compaction` TOOL span，保持同一个 `gen_ai.conversation.id` 和 active Turn；Turn 不存在时创建 synthetic Turn 表示 between-turn compaction。
- `after_compaction` 写入 messages/tokens before/after、tokens saved、compacted count、duration 和 result。active Turn 路径会推进内部 compaction epoch 并标记 pending rebase，但不会重置 session、Turn、event/compaction sequence、dedupe set 或已完成的 LLM run。
- compaction 后只有首个完整、role-aware 的 history snapshot 可以建立新 epoch 的 assistant baseline；count-only payload 保持 unknown/inferred，不从数量推导消息身份或内容。
- LLM run 在创建时记录 write-once epoch；retry source 查找和 `agent_end` 最终 run/message pairing 都要求 epoch 相同。`llm_output -> compaction -> agent_end` 顺序下，authoritative `gen_ai.input.messages` 和 `gen_ai.output.messages` 均保持 byte-for-byte 不变。
- compaction 未减少消息时记录 `error.type=compaction_error`、exception 与 ERROR status。
- **Hook 级回归证据**：`openclaw/observ-openclaw/scripts/compaction-delta-semantics.test.ts` 覆盖 mid-turn rebase、between-turn synthetic Turn、重复/缺失 compaction callback、跨 epoch retry 阻断、同 epoch retry 保持、authoritative input/output 保护、privacy 和缺少完整 post-compaction history 的 unknown 行为。
- 当前 Golden 场景未包含 compaction；上述证据不是正式 transcript/replay 双证据，因此 D1 按规则保持 🟡。

### D2 同步子 agent {#d2}

- **评分**：✅
- `subagent_spawned` 创建 `agent SyncAgent`，携带 `agentic.tool.type=subagent`、parent/child thread、session key、run ID 和同步模式。
- 子 session 的 LLM/Tool 直接附着到 SyncAgent；completion announcement 复用原父 Turn。
- `subagent_ended` 无论 pending span registry 是否完整，都会清理 relation/pending 状态并解除父 Turn 阻塞。
- Golden 同时包含 `subagent_spawned` / `subagent_ended` 事件，二者关联到同一 SyncAgent span。

### D3 异步子 agent {#d3}

- **评分**：⚪
- OpenClaw 当前 foreground/run 模式能提供同步生命周期，但没有稳定的异步 child trace link 或可用于跨 trace 还原的父 span context。

### D4 Cancel / Retry {#d4}

- **评分**：🟡
- 每个 `model_call_ended` 对应一个真实 provider-call LLM span；失败保留 `failureKind` 和 `errorCategory`。
- `timeout`、`aborted`、provider error 可分别进入 `error.type`，不会只留下最终 Turn 状态。
- 同一且明确的 runtime run id 内，连续失败/重试 provider attempt 复用首次 authoritative `gen_ai.input.messages`；run id 缺失/不同、失败后发生 tool activity、或 compaction epoch 已变化时不克隆，避免把未知边界、普通续调用或压缩后的新请求误判成 retry。
- `agent_end` 检测到迟到的 assistant error 时修正尚未结束的对应 LLM span，不额外合成重复错误节点。
- standalone error 只有在首次请求 prompt 已知时才复用该 input；已有 provider call 但无法关联边界时省略 input，不伪造 `[]`。
- `command:stop` 当前不会把活动 Turn/LLM 标记为 cancelled，代码也未记录 `retry_attempt` / `retry_delay`；这是实现缺口。
- `response-id-semantics.test.ts` 已用 hook 级 span exporter 证明三次 provider attempt 的 input byte-for-byte 相同，以及 runtime-id 缺失/不一致、tool activity 的负向边界和无 provider call id 时的 attempt 绑定；`compaction-delta-semantics.test.ts` 证明 compaction 会阻断跨 epoch retry snapshot 克隆，同时同 epoch retry 仍 byte-for-byte 保持。当前 Golden 仍未包含真实 provider error、用户 cancel 或 retry transcript，因此 D4 尚未满足双证据。

## 仍需补齐的验证样本

以下事项是当前 4 个 🟡 能力的 replay 证据缺口；补齐并验证后才可升级为 ✅：

1. compaction：覆盖 mid-turn 和 between-turn synthetic Turn 两种路径；
2. MCP：配置可用的 MCP 工具后补真实 `agentic.tool.type=mcp` 样本；
3. LLM provider error：补 rate-limit、timeout、aborted 的真实 transcript/replay；
4. cancel/retry：补多次 provider call 和用户主动停止的时序样本。

## 自动化成熟度

当前等级：**L5 Automated**。

- `make golden-replay-all` 可从固定 transcript 重建 trace/event replay；
- `make validate` / weaver-guard 作为语义门禁；
- `make install ocw-replay` 可构建、注册 replay 插件并配置 transcript 目录；
- runtime 上报与离线 replay 复用 `observ-openclaw` 的同一 hook/span 构造主流程。

### E2 上游调用 TRACEPARENT 串联 {#e2}

- **评分**: 🟡
- **结论**: 见 [独立证据](capabilities/E2.md)：OpenClaw 已有 W3C header bridge、diagnostic parent 与 SOFA link，但 W3C header 尚未直接进入 Turn 的 parent 拓扑。

### E1 Git 仓库信息识别 {#e1}

- **评分**: ⚪
- **结论**: 见 [独立证据](capabilities/E1.md)：OpenClaw 按常驻 gateway/service 边界不要求 E1，不将缺少 session-scoped worktree 归属误判为实现缺口。

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
