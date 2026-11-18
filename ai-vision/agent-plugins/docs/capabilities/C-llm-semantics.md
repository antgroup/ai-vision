# C 组：LLM 调用语义

← 返回 [CAPABILITIES.md](../../CAPABILITIES.md)

本组覆盖 7 项能力，关注**单次 LLM 调用**的最小闭环：耗时、错误、用户输入、系统提示词、模型输出、thinking 内容、token usage。

> 本组中 **C4（系统提示词）大概率全 ⚪**：当前 transcript 数据源中通常不暴露 system prompt（详见各能力下"数据源缺失根因"小节）。

## C1 LLM 精确耗时

### 定义

每次真实 LLM 调用对应一个 generation span，该 span 表达**单次调用的真实起止时间**，duration 非负。

不是 turn 整体耗时、不是流式片段聚合、不是事后统计。

### 必要属性 / Span 字段

- `gen_ai.span.kind = LLM`
- `gen_ai.operation.name = chat`
- `gen_ai.request.model`
- `agentic.llm.duration_ms`（非负）

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | 每次 LLM 调用都有独立 generation span；duration_ms 非负且与 start/end 时间一致 |
| 🟡 部分 | duration 存在但精度不够、缺一侧时间边界、或多次调用被合并 |
| ❌ 未实现 | hooks/transcript 中可见 LLM 调用边界，实现未采集 duration |
| ⚪ 不适用 | （C1 不适用 ⚪） |

### 机器验证方式

- [LLM span Rego 规则](../../weaver/policies/aivision/traces/llm/violations_forbidden.rego)检查 `agentic.llm.duration_ms` 是否为负值。
- 已纳入 M3 阶段

### 关联旧条款

- C2（LLM 请求、错误与耗时语义）

### cchooks 落地

- **代码**：`claudecode/cchooks/src/common/otel.rs` — 用 `assistant.timestamp → end_timestamp` 计算 `agentic.llm.duration_ms`
- **Replay 例子**：`~/.aivision/cc/replays/spans_S01_chat_single_turn_success_session_0.jsonl` — generation span 携带非负 duration_ms
- **关键设计决策**：多 chunk merge 后保留首尾时间戳，用 ns 精度

---

## C2 LLM 错误识别

### 定义

LLM 调用失败时，能在 trace 中区分"成功 / 失败"，并通过标准状态与错误属性同时表达。

错误首先挂在**实际失败的 generation span 自身**，不冒泡到 root span 或拆出独立"错误 span"。

### 必要属性 / Span 字段

失败时 generation span 必须具备：

- `status.code = ERROR`
- `error.type`
- 若有人可读错误描述：`status.message` 或 `error.message`

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | LLM 失败时 status.code=ERROR + error.type 同时存在；error.message 在可读时一并记录；错误挂在自身 span |
| 🟡 部分 | 仅记录 success=false / 仅有 status 没 error.type / 错误状态被错误地复制到父 span |
| ❌ 未实现 | hooks/transcript 中可见 API error / stop_reason error / HTTP 状态，实现未上报 |
| ⚪ 不适用 | （C2 不适用 ⚪） |

### 机器验证方式

- [LLM span Rego 规则](../../weaver/policies/aivision/traces/llm/improvements_recommended.rego)提示 `status.code=ERROR` 时缺失的 `error.type`。
- 已纳入 M3 阶段

### 关联旧条款

- C2（LLM 请求、错误与耗时语义）
- C14（Cancel / Retry 中关于错误冒泡的部分见 D4）

### cchooks 落地

- **代码**：`claudecode/cchooks/src/common/otel.rs` — `detect_llm_error()` 处理 `max_tokens` 与含 error 的 stop_reason
- **Replay 例子**：`~/.aivision/cc/replays/spans_S02_chat_llm_error_session_0.jsonl` — LLM 失败 span 携带 status.code=ERROR
- **关键设计决策**：当前 🟡 — synthetic LLM error span（API 重试耗尽合成）只设 status=ERROR 但缺 error.type，待补

---

## C3 LLM 用户输入内容识别

### 定义

每次 LLM 调用接收到的输入消息集合可在 generation span / turn root span 上观测。

它表达"模型实际看到了什么"，使用 OpenAI 兼容的结构化消息数组。

### 必要属性 / Span 字段

- `gen_ai.input.messages`（JSON-serialized 字符串，顶层为 JSON array）
- 顺序符合实际发生顺序

**Turn root span 与 Generation span 分层职责**：

| Span 类型 | input.messages 内容 |
|-----------|--------------------|
| Turn root span | **用户视角**，固定 1 条 user 消息（位于数组**末尾**，便于 Langfuse 取最后一条作 Input） |
| Generation span | **LLM 调用视角**，累积上下文：用户消息在开头（自然时序），后续为 assistant + tool_result 交替 |

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | turn root span 的 input.messages 含 1 条 user 消息；每个 generation span 的 input.messages 仅含相邻真实调用之间的新增输入；OpenAI 兼容 JSON array；顺序正确 |
| 🟡 部分 | 仅 turn 层有 input.messages，generation 缺失；或顺序被颠倒；或 tool_calls 被错误地压平为字符串 |
| ❌ 未实现 | transcript 可重建用户消息，实现未上报 input.messages |
| ⚪ 不适用 | （C3 不适用 ⚪） |

### 机器验证方式

- weaver-guard 校验 `gen_ai.input.messages` 以 `[` 开头
- 校验消息顺序逻辑（待补 Rego）

### 关联旧条款

- C9（Input / Output Messages 语义）
- C10（增量 Input 语义直接由 generation span 的 `gen_ai.input.messages` 承载）

### 细则：增量 Input

新组件的 generation `gen_ai.input.messages` **必须**只包含相邻调用之间的新增消息：首次调用为当前 user，后续调用为 preceding assistant tool_calls + tool results。该字段是唯一输入表示，不得再复制到自定义 delta 字段，也不得提供累加/增量模式开关。无法稳定构造边界时使用 `[]`（隐私模式为 `[REDACTED]`），不得伪造累积历史。

### 细则：软截断

> 20K 字符软限制时，从数组头部逐条对 content 软截断（保留前 100 字符 + `...[TRUNCATED]`）。消息数量与结构不变。`tool_calls.arguments` 始终保留原始内容。

### cchooks 落地

- **代码**：`claudecode/cchooks/src/common/message_format.rs` — `build_input_messages` / `build_generation_delta_input`
- **历史实现说明**：cchooks 仍保留旧的累积输入与自定义 delta 投影，属于待迁移 emitter，不作为新组件设计模板。
- **关键设计决策**：turn root 与 generation span 分层职责；新组件 generation 仅输出 per-call delta；软截断从头部逐条裁 content。

---

## C4 LLM 系统提示词识别

### 定义

每次 LLM 调用时实际生效的 system prompt / system instructions / system-level guidance 可被观测。

### 必要属性 / Span 字段

- `gen_ai.system_instructions`（JSON-serialized 字符串或 array）
- 不得写入裸文本

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | LLM span 携带 `gen_ai.system_instructions`，反映该次调用实际生效的 system prompt |
| 🟡 部分 | 仅记录原始 system 而无法反映运行时变换、或仅在部分调用上有 |
| ❌ 未实现 | hooks/transcript 中可见 system prompt 字段，实现未上报 |
| ⚪ 不适用 | **transcript 数据源中不存在 system prompt 字段**，无论实现如何都拿不到 |

### 数据源缺失根因（⚪ 普遍原因）

能否拿到 system prompt 取决于 agent 数据源是否暴露该字段（数据源分类参见 METHODOLOGY §3.1）：

- **双源 agent 中持久化 transcript 这一侧通常不暴露 system**：
  - Claude Code transcript（.jsonl）只记录用户消息、assistant 消息、tool 调用与结果，不暴露 SDK 内部组装的 system prompt
  - Codex transcript 同理：CLI 透传的 system prompt 由 `~/.codex/` 配置本地组装，不写入会话记录
- **Hook payload 这一侧有时携带 system**：
  - 某些 plugin lifecycle 事件（如 openclaw `llm_input`）直接在 payload 中给 `systemPrompt` 字段 → C4 唯一稳定 ✅ 来源
  - cchooks / cdxhooks 的 hook payload **不**含 system prompt（hook 触发在 SDK 之后，看不到原始 request body）

唯一可能拿到 system 的场景：

- Hook payload 直接携带 systemPrompt 字段（如 openclaw `llm_input` 事件）
- LLM 请求拦截层抓到原始 request body（需要 agent runtime 支持）
- Agent 自身配置中能稳定读到 system 模板（但要保证与实际生效内容一致）

**结论**：双源 agent 仅靠 transcript / cchooks-style hook payload 无法拿到 system，评 ⚪ 是合理状态；plugin lifecycle 直接给 systemPrompt 字段的 agent（如 openclaw）可评 ✅。

### 机器验证方式

- weaver-guard 校验 `gen_ai.system_instructions` 以 `"` 或 `[` 开头（仅在存在时）
- 当前 Rego 状态：**待补**

### 关联旧条款

- C12（System Instructions 语义，旧版标记"放弃"，本规范保留作为评估项）

### cchooks 落地

- **评分**：⚪ 不适用
- **数据源缺失根因**：claudecode transcript 完全不暴露 system prompt，源代码无任何 `system_instructions` 处理（grep 0 命中）
- 详见 [Claude Code C4 能力证据](../../claudecode/capabilities/C4.md)

---

## C5 LLM 模型输出识别

### 定义

每次 LLM 调用产出的 assistant 响应可在 generation span / turn root span 上观测。

### 必要属性 / Span 字段

- `gen_ai.output.messages`（JSON-serialized 字符串，顶层为 JSON array）
- 包含 `content`、`reasoning_content`（若有）、`tool_calls`（若有）、`finish_reason`

**Turn root span 与 Generation span 分层职责**：

| Span 类型 | output.messages 内容 |
|-----------|---------------------|
| Turn root span | **最终回复**，固定 1 条（取 `turn.assistants.last()`） |
| Generation span | 该次 LLM 调用产出的 assistant 响应（中间调用通常 finish_reason=tool_use） |

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | turn root output 仅含 1 条最终回复；每个 generation 携带该次响应；tool_calls 保留为结构化字段；finish_reason 准确 |
| 🟡 部分 | 仅 turn 层有 output；或 tool_calls 被错误压平为字符串；或 finish_reason 缺失 |
| ❌ 未实现 | transcript 可重建 assistant 响应，实现未上报 |
| ⚪ 不适用 | （C5 不适用 ⚪） |

### 机器验证方式

- weaver-guard 校验 `gen_ai.output.messages` 以 `[` 开头
- 当前 Rego 状态：**待补**

### 关联旧条款

- C9（Input / Output Messages 语义）

### cchooks 落地

- **代码**：`claudecode/cchooks/src/common/message_format.rs` — `build_output_messages`
- **Replay 例子**：`~/.aivision/cc/replays/spans_S01_chat_single_turn_success_session_0.jsonl` — turn root output.messages 仅含最终 assistant，generation span 含 content + tool_calls + finish_reason + reasoning_content
- **关键设计决策**：turn root 取 `turn.assistants.last()`；generation 保留每次调用结构化字段

---

## C6 LLM thinking 内容识别

### 定义

LLM 调用产出的 **thinking / reasoning 段落**（如 Claude `thinking` block、OpenAI o1 reasoning summary）可在 generation span 上观测。

它和 `content` 是两类独立内容：thinking 是模型的内部推理过程，content 是给用户的最终输出。

### 必要属性 / Span 字段

- 在 `gen_ai.output.messages` 中的 assistant 消息内携带 `reasoning_content` 字段
- 序列化为字符串（多个 thinking block 合并）
- 与 `content` 分离

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | thinking 内容在 generation span output.messages 的 `reasoning_content` 字段中；与 content 严格分离 |
| 🟡 部分 | thinking 与 content 被混在一起；或仅在部分调用上记录；或被截断且未标记 |
| ❌ 未实现 | hooks/transcript 中可见 thinking block，实现未上报 |
| ⚪ 不适用 | 模型/SDK 不返回 thinking 内容（如 Claude 不开启 extended thinking、GPT-4 经典模式） |

### 数据源缺失根因（⚪ 场景）

- 用户未开启 extended thinking 模式
- 模型不支持 thinking 输出
- SDK 在 streaming 模式下不暴露 thinking summary

### 机器验证方式

- 暂无 Rego（thinking 内容是 generation 内部字段，不强约束）
- 当前 Rego 状态：**待补**

### 关联旧条款

- 无（新增能力，旧 Constitution 未独立覆盖）

### cchooks 落地

- **代码**：[transcript 字段提取](../../claudecode/cchooks/src/common/transcript/fields.rs)中的 `extract_thinking_blocks` 解析 Claude `thinking` block，[消息格式化](../../claudecode/cchooks/src/common/message_format.rs)写入 `reasoning_content`。
- **Replay 例子**：`~/.aivision/cc/replays/spans_S01_chat_single_turn_success_session_0.jsonl` — generation span output.messages 中的 assistant 消息含 reasoning_content 字段
- **关键设计决策**：thinking 与 content 严格分离；多 thinking block 合并为单字符串

---

## C7 LLM Token usage 识别

### 定义

每次 LLM 调用消耗的 input / output token 可在 generation span 上观测，对应**单次真实调用**，**不重复累加**。

### 必要属性 / Span 字段

LLM span 必须保留源数据提供的 token 用量：

- `gen_ai.usage.input_tokens`（非负）
- `gen_ai.usage.output_tokens`（非负）

值要求：

- 来自原始 usage、可靠计数器，或与原始 transcript 一致的稳定回放
- 累计 usage 必须**先做差分**，再映射到当前单次 span
- 不得用 tokenizer 估算补齐
- turn / root span **不重复上报**聚合 token
- 某方向在源数据中不可获得时，省略其数值并写对应的 `agentic.usage.input_tokens.missing` 或 `agentic.usage.output_tokens.missing`，值必须精确为字符串 `true`；输入和输出分别判断，不用 `0` 代替未知
- 明确报告的 `0` 是有效已知用量；布尔值 `true`、字符串 `false`、空值或其它标记不豁免缺失的必填数值
- 成功的 `api_request` 事件在两个方向都已知时仍要求 total；任一方向明确未知时可省略 total。缺失标记不豁免模型、IO、耗时或其它必填属性

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | 每个 LLM span 都有 input/output tokens；累计 usage 已正确差分；turn span 不重复上报 |
| 🟡 部分 | 部分调用 token 缺失；或累计 usage 未差分被直接当单次 token；或 turn 层重复上报导致下游聚合双倍 |
| ❌ 未实现 | hooks/transcript 中可见 usage，实现未上报 |
| ⚪ 不适用 | 模型/SDK 不暴露 token usage（罕见） |

### 机器验证方式

- Rego: `weaver/policies/aivision/traces/llm/violations_required.rego` 与 `violations_forbidden.rego` —— 校验 input/output 来源已知时必填且非负；仅精确对应的字符串缺失标记允许省略未知方向
- `weaver/policies/aivision/events/api_request/violations_required.rego` —— 成功事件保留已知 input/output/total，并按同一缺失契约处理未知用量
- 已纳入 M3 阶段

### 关联旧条款

- C11（Token 语义）

### 细则：cache token

`cache_read_tokens` / `cache_creation_input_tokens` 等推荐字段在原始来源稳定时记录，缺失允许，不强约束。

### 细则：与 turn 层 token 聚合

turn / root span **不**复制聚合 token，避免下游基于子 span 累加时双倍。本条款是 C7 的核心 ❌ 触发点之一。

### cchooks 落地

- **代码**：[transcript 字段提取](../../claudecode/cchooks/src/common/transcript/fields.rs)中的 `get_usage` 读取 token 用量，[OTel 构造](../../claudecode/cchooks/src/common/otel.rs)将其写入 LLM span。
- **Replay 例子**：`~/.aivision/cc/replays/spans_S08_tokens_exact_usage_session_0.jsonl` — 多次 generation 各自 token 与原始 assistant.usage 一一对应
- **关键设计决策**：每 chunk 自带单次 usage 无需差分；turn root **不重复上报**；cache_* 按 OTel semconv 聚合进 input_tokens
