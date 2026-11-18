# Kimi-CLI 第一章证据调研：Turn、LLM 与 Tool

本文档按“数据项”而不是按空泛条款进行举证。每个数据项固定回答：

- 结论：支持 / 部分支持 / 暂不支持
- 约束建议：必须项 / 推荐项 / 可选项 / 放弃
- 数据来源 / 获取方式 / 正证据 / 反证据 / 推导链路 / 风险与边界 / 管控动作

证据指向的源码均位于 `<kimi-cli>`（独立的 kimi-cli 上游仓库），下文路径为该仓库相对路径。

## 总览矩阵

| 条款 | 结论 | 主要数据来源 | 可靠性 | 实现代价 | 架构影响 | 约束建议 |
|---|---|---|---|---|---|---|
| C1 Turn / Root Span 组织规则 | 支持 | `UserPromptSubmit` + `Stop` / `StopFailure` hooks + wire `TurnBegin` / `TurnEnd` | 原生可得 | 低 | 低 | 必须项 |
| C2 LLM 请求、错误与耗时语义 | 部分支持 | `wire.jsonl` `StepBegin` + `StatusUpdate.token_usage` + `context.jsonl` assistant message | 弱时间边界 + 强消息内容 | 中 | 低 | 必须项 |
| C3 Tool 调用、错误与耗时语义 | 支持 | `PreToolUse` + `PostToolUse` / `PostToolUseFailure` hooks（含 `tool_call_id`） | 原生可得 | 低 | 低 | 必须项 |

## C1 Turn / Root Span 组织规则

### 数据项 C1.1：Root span 的语义身份与最小属性

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook（公共字段 `session_id` / `cwd`）
- 内部状态（`agentic.runtime.name = kimi-cli` 由实现层写入）

#### 获取方式

- 关键事件：`UserPromptSubmit`（turn 开始锚点）
- 关键字段：`session_id`、`cwd`、`hook_event_name`
- 代码入口：`src/kimi_cli/hooks/events.py::user_prompt_submit()`，公共字段由 `_base()` 注入

#### 正证据

- `events.py:_base()` 保证 13 个事件的 stdin payload 中均含 `hook_event_name` / `session_id` / `cwd`，可直接映射为：
  - `gen_ai.span.kind = CHAT`
  - `gen_ai.operation.name = invoke_agent`
  - `gen_ai.conversation.id = session_id`
  - `agentic.runtime.cwd = cwd`
  - `agentic.runtime.name = kimi-cli`
- `UserPromptSubmit` 是每个 turn 的唯一开始锚点（`src/kimi_cli/cli/__init__.py:643` `await instance.soul.hook_engine.trigger("UserPromptSubmit", ...)`），无歧义。

#### 反证据

- `session_id` 在 ContextVar 中传递（hooks 文档 “Context Variables” 节）；当 wire 客户端调用而非 CLI 调用时，部分场景 ContextVar 为空，`session_id` 字段值可能为 `""`。
- `agentic.runtime.name` 没有 hook 字段直接对应，需实现层硬编码 `"kimi-cli"`，属于实现约定而非数据来源。

#### 推导链路

- `gen_ai.chat.id = "{session_id}-turn-{turn_number}"`，`turn_number` 由实现层在 hook 进程内维护（基于 `UserPromptSubmit` 自增），属稳定推导。

#### 风险与边界

- `kimihooks` 是无状态二进制，`turn_number` 必须依赖外部状态（如读取 `wire.jsonl` 中已发生的 `TurnBegin` 计数），否则跨进程不可重现。replay 路径下没有此问题。

#### 管控动作

- `CHAT + invoke_agent + session_id + cwd` 可作为硬约束。
- `turn_number` 在 hook 路径下退化为可选项，replay 路径下作为必须项。
- golden 校验应覆盖：`session_id` 非空 + `agentic.runtime.name = kimi-cli`。

### 数据项 C1.2：Root span 与子 span 的组织关系

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`PreToolUse` / `PostToolUse` / `PreCompact` / `PostCompact` / `SubagentStart` / `SubagentStop`
- transcript：`wire.jsonl` `TurnBegin` / `StepBegin` / `CompactionBegin`

#### 获取方式

- 关键事件：所有 turn 内事件均带 `session_id` 公共字段，可在同一 trace 下挂为 root 的 sibling
- 代码入口：`src/kimi_cli/soul/kimisoul.py:603 / 646 / 896 / 949 / 1151 / 1219` 多处 `self._hook_engine.trigger(...)`

#### 正证据

- kimi-cli 的 hooks 触发顺序与宪章 sibling 拓扑天然契合：
  - `UserPromptSubmit` → root 开始
  - `PreToolUse` / `PostToolUse[Failure]` → tool sibling
  - `PreCompact` / `PostCompact` → compaction sibling
  - `SubagentStart` / `SubagentStop` → subagent sibling
  - `Stop` / `StopFailure` → root 结束
- `kimisoul.py` 中 hooks 与 LLM step、tool execute、compaction 一一对应触发，无嵌套调用关系。

#### 反证据

- LLM call 本身没有 `PreLLM` / `PostLLM` 事件（与 `claudecode` 的 `assistant` transcript 不同），generation span 的边界必须从 `wire.jsonl` `StepBegin` + `StatusUpdate.token_usage` 推导（详见 C2.2）。
- `wire.jsonl` 中 LLM 流事件与 hook 事件按时间交错，但 `tool_call_id` 是关联 `PreToolUse` 与 wire `ToolCall` 的唯一键。

#### 推导链路

- generation span 的开始时间 = 上一个 step 边界（`StepBegin` 或 `UserPromptSubmit`）；结束时间 = 第一个 `PreToolUse` 或 `Stop` 之前。

#### 风险与边界

- 同一 turn 内多 step（assistant → tool → assistant → tool ...）的 generation 数量必须正确切分；需以 `StepBegin` 为权威边界，不能仅按 `PreToolUse` 计数。

#### 管控动作

- “tool / compaction / subagent 与 LLM 同级，均挂 root 之下”可作为硬约束。
- “generation span 与 tool span 严格按 step 切分”应进入 golden 校验。

### 数据项 C1.3：Root span 的开始时间

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`UserPromptSubmit` 触发时的本地时间

#### 获取方式

- 关键事件：`UserPromptSubmit`
- 代码入口：`src/kimi_cli/hooks/events.py::user_prompt_submit()`

#### 正证据

- `UserPromptSubmit` 是 turn 开始的唯一锚点；hook 进程读取 stdin 时即可获取触发时间（`SystemTime::now()` 在 hook 进程入口处采样），与 turn 开始时间误差 < hook 启动开销。
- `wire.jsonl` 中 `TurnBegin` 事件可作为旁证（更精确的内部时间）。

#### 反证据

- hook 进程启动开销不可忽略（fork-exec + Python JSON 序列化），实测可达 10-50ms 量级；hook 看到的时间是 turn 开始 + 启动延迟。
- 若以 `wire.jsonl` `TurnBegin` 为时间源（replay 阶段），则需要 `wire.jsonl` 在 turn 触发瞬间就已写入；该假设需在 P0 实测验证。

#### 推导链路

- 优先 `wire.jsonl` `TurnBegin` 时间戳；缺失退化为 `UserPromptSubmit` 触发时刻。

#### 风险与边界

- 与 `claudecode` `turn.user_timestamp` 相比，kimi 的 wire 事件时间精度依赖 `wire.jsonl` 实际 flush 行为（追加写时机）。

#### 管控动作

- “turn 开始时间必须有原始来源”可作为硬约束。
- 时间精度等级需在文档中明确：wire `TurnBegin` > hook 触发时刻 > replay 构建时刻。

### 数据项 C1.4：Root span 的结束时间

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`Stop` 或 `StopFailure` 触发时的本地时间
- transcript：`wire.jsonl` `TurnEnd` 时间戳

#### 获取方式

- 关键事件：`Stop`（正常结束）/ `StopFailure`（异常结束）
- 代码入口：`src/kimi_cli/hooks/events.py::stop()` / `stop_failure()`

#### 正证据

- 与 `claudecode` 不同，kimi 有原生 turn 结束 hook：
  - `Stop` 表示正常结束（含 `stop_hook_active` 防循环字段）
  - `StopFailure` 表示异常结束（含 `error_type` / `error_message`）
- `wire.jsonl` 同时有 `TurnEnd` 事件可作为旁证。

#### 反证据

- `Stop` hook 触发时间 = soul 主循环判定 turn 结束的时刻 + hook 启动延迟，与 `claudecode` 的 `now_ns` 兜底相比仍有微小漂移。
- `wire.jsonl` `TurnEnd` 在 turn 异常中断时可能未写出（`types.py:56` `TurnEnd: ... If the turn is interrupted, this event may be omitted`）。

#### 推导链路

- 正常路径：`wire.jsonl` `TurnEnd` 优先；缺失退化为 `Stop` / `StopFailure` 触发时刻。
- 中断路径：必须依赖 `StopFailure` 或 wire `StepInterrupted`，不应回填精确边界。

#### 风险与边界

- “turn 中断但无 `TurnEnd`”是真实场景，必须显式建模为 `error.type = "interrupted"`，不可补假时间。

#### 管控动作

- “turn 结束时间必须来自 hook 或 wire 原始事件”可作为硬约束（**优于 `claudecode`**：cc 是构建时刻 `now_ns`）。
- golden 应覆盖 `StopFailure` 场景，验证 `error.type` 与结束时间真实性。

### 数据项 C1.5：Root span 的输入输出消息

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`UserPromptSubmit.prompt`（输入）
- transcript：`context.jsonl` 中本 turn 的所有 user / assistant / tool messages（输出）

#### 获取方式

- 关键事件：`UserPromptSubmit` 提供本 turn 起点的 user prompt
- 代码入口：
  - `src/kimi_cli/hooks/events.py::user_prompt_submit()` 携带 `prompt` 字段
  - `context.jsonl` 由 `src/kimi_cli/soul/context.py` 持续追加（kosong 标准 message schema）

#### 正证据

- `context.jsonl` 是 kosong 标准格式，含完整 `role` / `content` / `tool_calls` / `tool_results`，可直接映射为 OpenAI JSON array。
- `UserPromptSubmit.prompt` 字段提供 turn 开始时的纯文本 user input，便于 root 的 `gen_ai.input.messages` 末位定位（按 cc 约定 user 在末尾）。

#### 反证据

- 多模态输入（`ContentPart` 含 `ImageURLPart` / `AudioURLPart` / `VideoURLPart`，见 `wire/types.py:15`）：`UserPromptSubmit.prompt` 仅是字符串，不能完整表达多模态；多模态完整数据须从 `context.jsonl` 取。
- `context.jsonl` 在 turn 过程中持续追加，replay 阶段需按 turn 边界切窗读取（依赖 `wire.jsonl` `TurnBegin` / `TurnEnd` 时间戳）。

#### 推导链路

- output messages：从 `context.jsonl` 读取本 turn 内所有 assistant / tool 消息，按 kosong 顺序排列。
- input messages：本 turn 之前的所有 messages + 当前 user prompt（user 在末尾）。

#### 风险与边界

- `context.jsonl` 与 `wire.jsonl` 的写入并非原子同步，replay 路径需以 `wire.jsonl` 时间为权威边界，`context.jsonl` 提供消息体。

#### 管控动作

- “消息符合 OpenAI JSON array + user 在末尾”可作为硬约束。
- golden 校验应覆盖：纯文本 turn / 含 tool 调用的 turn / 多模态 turn 三类场景。

### 数据项 C1.6：Root span 的失败汇总

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`StopFailure.error_type` + `StopFailure.error_message`

#### 获取方式

- 关键事件：`StopFailure`
- 代码入口：`src/kimi_cli/hooks/events.py::stop_failure()`

#### 正证据

- kimi 是少数原生提供 turn 失败 hook 的 agent：`StopFailure` 直接给出 `error_type` 与 `error_message`，可直接映射为 root span 的 `error.type` + status=ERROR。
- 与 `claudecode` 相比（cc 只能从 assistant `stop_reason` 嗅探），kimi 的失败汇总更完整。

#### 反证据

- `StopFailure` 的 `error_type` 取值范围在文档中未列举（matcher 字段为 “Error type” 但无枚举），`kimihooks` 实现需要保留原值或建立映射表。
- 若 turn 在 LLM 错误后仍正常进入 `Stop`（例如错误已被 retry 化解），则 `Stop` hook 不会暴露此前的 LLM 错误；root 失败汇总只反映 turn 终态而非过程内错误。

#### 推导链路

- 无需推导。

#### 风险与边界

- 过程内错误（LLM api_error 已重试成功 / tool error 已被忽略）不进入 root 失败汇总，但应在对应 generation / tool span 各自记录。

#### 管控动作

- “root 失败必须来自 `StopFailure`”可作为硬约束。
- “过程内错误不归并到 root”应作为显式语义在文档中说明，避免与 cc 的“轻量汇总”混淆。
- golden 应覆盖 3 种场景：正常 `Stop` / `StopFailure` / 过程错误已恢复的 `Stop`。

## C2 LLM 请求、错误与耗时语义

### 数据项 C2.1：LLM span 的语义身份与模型字段

#### 结论

部分支持

#### 约束建议

必须项

#### 数据来源

- 内部状态：`ChatProvider.model_name`（`packages/kosong/src/kosong/chat_provider/__init__.py:25-31`，所有 provider 必须实现的 `@property model_name`）
- transcript：`wire.jsonl` `StatusUpdate.message_id`（`wire/types.py:159` `StatusUpdate`）

#### 获取方式

- 模型名：通过 hook 进程从 `kimi config show` 或解析 `~/.kimi/config.toml` 获得 turn 当前 chat_provider 的 `model_name`；replay 时若同 session 目录有 config 快照则直读，否则退化为 `unknown`。
- 响应 id：`StatusUpdate.message_id`

#### 正证据

- kosong `ChatProvider` Protocol 强制要求 `model_name` 属性（`chat_provider/__init__.py:25` `@property def model_name`），所有 provider 实现（`chat_provider/kimi.py:118` `self.model: str = model`，`:134` `def model_name(self) -> str: return self.model`）均原生暴露。
- `wire.jsonl` `StatusUpdate` 是 streaming 累积事件（实测于 `~/.kimi/sessions/.../wire.jsonl`），含 `message_id` 字段可映射为 `gen_ai.response.id`。
- `gen_ai.span.kind = LLM` / `gen_ai.operation.name = chat` 是宪章固有约束，无需数据来源。

#### 反证据

- **kosong `Message` 类（`packages/kosong/src/kosong/message.py:243-302`）的字段固定为 `role / name / content / tool_calls / tool_call_id / partial`，不含 `model` 字段**；context.jsonl 中 assistant message 无法直接读出 model 名。
- `wire.jsonl` `StatusUpdate` 也不带 model 字段（`wire/types.py:159-191` 仅含 `context_usage / context_tokens / max_context_tokens / token_usage / message_id / plan_mode / mcp_status`）。
- 因此 model 名必须从 chat_provider 实例 / 配置文件外部读取，是 hook 实现层的运行时拼接而非 transcript 字段。

#### 推导链路

- 优先级：当前活跃 ChatProvider 实例的 `model_name` → 同 session 的 `config.toml` 当前模型 → `unknown`。
- replay 阶段无活跃实例，必须依赖 session 目录内的 config 快照（kimi 是否自动落盘待确认；如不自动落盘则 replay 模型名只能写 `unknown`）。

#### 风险与边界

- 一个 turn 内允许通过 `/model` slash 命令切换模型（`src/kimi_cli/soul/slash.py` 有 model 切换逻辑），单 turn 多 model 场景需用 step 边界而非 turn 边界拼接。
- replay 阶段缺失 config 快照时 model 名退化为 `unknown`，不应视为 violation。

#### 管控动作

- `gen_ai.span.kind / operation.name` 直接进入硬约束。
- `gen_ai.request.model` 列为必须项但允许 `unknown` 兜底；实现侧应在 P1 阶段评估“session 启动时落盘 config 快照”。
- `gen_ai.response.id = StatusUpdate.message_id` 进入推荐项。

### 数据项 C2.2：LLM span 的开始时间

#### 结论

部分支持

#### 约束建议

必须项

#### 数据来源

- transcript：`wire.jsonl` `StepBegin` 事件时间戳（每个 step = 一次 LLM 调用 + 后续 tools）
- 推导：上一个 `Stop` / `PostToolUse[Failure]` 完成后到下一个 `StepBegin` 之间

#### 获取方式

- 关键事件：`wire.jsonl` `StepBegin(n)`（`wire/types.py:66`）
- 代码入口：`src/kimi_cli/soul/kimisoul.py` 主循环中每次 LLM 调用前发出 `StepBegin`

#### 正证据

- `wire/types.py:66` `StepBegin: Indicates the beginning of a new agent step. This event must be sent before any other event in the step.` —— 语义上是 LLM 调用的开始边界。

#### 反证据

- `StepBegin` 不是 hook 事件，必须依赖 `wire.jsonl` 已写入；hook 路径下无原生 LLM 开始时间。
- `StepBegin.n` 是 step 编号，但不带模型名 / 输入 messages 等内容，必须与 `context.jsonl` 同步窗口取数。
- **kosong `Message` 类（`packages/kosong/src/kosong/message.py:243`）字段固定为 `role / name / content / tool_calls / tool_call_id / partial`，明确不含 `timestamp`**；assistant message 在 `context.jsonl` 中无原生时间戳。
- 因此实时 hook 路径下，generation 开始时间没有原生数据源；唯一精确时间来自 `wire.jsonl` 顶层 `timestamp` 字段（实测 `wire.jsonl` 行格式为 `{"timestamp": <unix_float>, "message": {"type": ..., "payload": ...}}`，protocol_version 1.9）。

#### 推导链路

- replay 阶段：`wire.jsonl` 流扫描，每遇 `StepBegin` 取该行顶层 `timestamp` 作为 generation span 起点。
- 实时 hook 阶段：无原生 LLM 开始事件，generation span 起点退化为 `UserPromptSubmit` / `PostToolUse` 完成时刻。

#### 风险与边界

- 实时 hook 路径下 generation 开始时间精度严重受限；replay 路径才是 LLM span 的权威来源。
- kosong 不在 message 上承载时间戳是设计选择，不会因 kimi 升级而改变；后续若要更精确的 step 起止时间必须依赖 `wire.jsonl` 自身的时间标注，不能寄望于 message schema。

#### 管控动作

- `claudecode` 的“原始时间优先 + 安全退化”规则同样适用。
- 应在 `kimi/constitution/00-总览.md` 明确：C2 是 kimi 相对 cc 的弱项之一。

### 数据项 C2.3：LLM span 的结束时间与耗时

#### 结论

部分支持

#### 约束建议

必须项

#### 数据来源

- transcript：`wire.jsonl` `StatusUpdate.token_usage` 通常在 LLM response 完成后发出
- 推导：第一个 `PreToolUse` 触发时刻或下一个 `StepBegin` 前

#### 获取方式

- 关键事件：`wire.jsonl` `StatusUpdate` 出现 `token_usage` 非空时
- 关键字段：`StatusUpdate.message_id` 可关联 assistant message
- 代码入口：`wire/types.py:159` `StatusUpdate`

#### 正证据

- `StatusUpdate.token_usage` 类型 `kosong.chat_provider.TokenUsage`，每 step 在 LLM 完成后更新。
- LLM 完成后通常立即触发 `PreToolUse`（若有工具调用）或 `Stop`（若直接结束），可作为结束时间锚点。

#### 反证据

- `StatusUpdate` 是 streaming 累积事件，token_usage 可能多次更新；必须取“本 step 内最后一次非空 `token_usage`”作为完成时刻。
- 实时 hook 路径下，generation 结束时间退化为“下一个 hook 触发时刻”，可能引入数十毫秒到秒级误差。

#### 推导链路

- `agentic.llm.duration_ms = end_ns - start_ns`，与 cc 一致总能写出。

#### 风险与边界

- LLM 流过程中无原生 `StreamingEnd` 事件可用；duration 精度受 wire 与 hook 时间错位影响。

#### 管控动作

- `duration_ms` 保留必须项，因为总能写出。
- “结束时间必须来自原始来源”不适合升 blocker，应标记为部分支持。

### 数据项 C2.4：LLM span 的输入输出消息与增量输入

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- transcript：`context.jsonl` 完整 messages 历史
- 推导：按 step 边界切窗 + 计算与上一个 step 的 delta

#### 获取方式

- 关键文件：`context.jsonl` (kosong messages)
- 切窗依据：`wire.jsonl` `StepBegin(n)` 时间戳

#### 正证据

- `context.jsonl` 是单调追加的完整 message 流，含 user / assistant / tool_calls / tool_results 全部字段。
- 与 cc 一致，`build_generation_accumulated_input` 把 user 放数组首位 + tool_results 在自然时序，`build_generation_delta_input` 取“上一条 assistant + 对应 tool results”。

#### 反证据

- 增量计算依赖 step 边界正确切分；若 `wire.jsonl` 与 `context.jsonl` 写入异步，可能出现“context 已追加但 wire 未刷盘”的窗口错位。
- **kosong `Role` 类型（`message.py:230-241`）枚举为 `system / user / assistant / tool`，与 OpenAI 完全一致**，但 kimi 在 `context.jsonl` 中实测会出现伪角色 `_system_prompt`（已在 `~/.kimi/sessions/.../context.jsonl` dump 中观察到首行 `{"role": "_system_prompt", ...}`）；该行用于持久化系统提示词，**必须在映射时识别为 system 并按 C12 策略决定是否纳入 `gen_ai.input.messages`**。
- `Message.content` 序列化器（`message.py:268-273` `_serialize_content`）对“单一 TextPart”特例输出为字符串，对多 part 输出为 list，需在拼接 OpenAI JSON array 时同时处理两种形态。
- 多模态 `ContentPart` 子类有 `text / think / image_url / audio_url / video_url`（`message.py:71-167`）；`think` 是 kimi/kosong 特有的思维链分段，OpenAI schema 无对应字段，需走 `agentic.llm.message.thinking` 扩展属性或归并到 `assistant.content`。

#### 推导链路

- replay 阶段：`for step in steps: messages_at_step = context_messages[:step.boundary_index]`
- delta = `messages_at_step[N] - messages_at_step[N-1]`
- content 归一化：单 TextPart → string；多 part → list[dict]；`ThinkPart` → 单独抽取放入扩展字段。
- `_system_prompt` 行：识别后按 C12 “暂不支持”策略默认丢弃，或在 evidence-index 中标记为 kimi 特有。

#### 风险与边界

- `Message.partial: bool | None` 字段表示 streaming 中的不完整消息，replay 必须丢弃 `partial=true` 的中间状态行（如 context.jsonl 写入了 streaming 快照）。
- `ToolCall.function.arguments` 是 JSON 字符串（`message.py:183` `arguments: str | None`），不是 dict；需 `json.loads` 后写入 `gen_ai.assistant.message.tool_calls[].function.arguments`。

#### 管控动作

- “消息 OpenAI JSON array + delta 三段式”进入硬约束。
- `_system_prompt` 处理策略写入 evidence-index：默认丢弃，golden 验证“user / assistant / tool 三类齐全”。
- `ThinkPart` 处理策略列为 kimi 扩展，semconv 待 L3 注册。

### 数据项 C2.5：LLM span 的 input/output tokens

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- transcript：`wire.jsonl` `StatusUpdate.token_usage`，类型 `kosong.chat_provider.TokenUsage`（`packages/kosong/src/kosong/chat_provider/__init__.py:99-118`）

#### 获取方式

- 关键事件：`wire.jsonl` `StatusUpdate` 中 `token_usage` 字段
- 代码入口：`wire/types.py:171` `token_usage: TokenUsage | None`
- 字段权威定义：`chat_provider/__init__.py:99-118`

#### 正证据

- `kosong.TokenUsage` 字段经源码核查为：
  - `input_other: int` — 不含缓存读写的纯输入 token
  - `output: int` — 输出 token
  - `input_cache_read: int = 0` — 缓存命中
  - `input_cache_creation: int = 0` — 缓存写入（仅 Anthropic）
  - 派生属性 `input = input_other + input_cache_read + input_cache_creation`，`total = input + output`
- 字段名稳定（kosong 在 OSS 中以 protocol 方式暴露，跨 provider 强一致），可直接映射：
  - `gen_ai.usage.input_tokens = TokenUsage.input`
  - `gen_ai.usage.output_tokens = TokenUsage.output`
  - `agentic.llm.tokens.cache_read = TokenUsage.input_cache_read`
  - `agentic.llm.tokens.cache_write = TokenUsage.input_cache_creation`

#### 反证据

- **`TokenUsage` 不含 `reasoning_tokens` 字段**——OpenAI 风格的 reasoning token 在当前 kosong schema 中没有承载位置；如需采集需扩展 kosong 上游或归并到 `output`（与 OpenAI Responses provider 实际行为一致：reasoning 计入 output）。
- `StatusUpdate.token_usage` 是 streaming 累积事件，可能多次更新；replay 必须取“本 step 边界内最后一次非空 `token_usage`”作为权威值，不能累加。
- cache 字段命名与 cc 漂移：cc 是 `cache_creation_input_tokens / cache_read_input_tokens`，kimi 是 `input_cache_creation / input_cache_read`，需在 mapping 层标准化。

#### 推导链路

- 直接读 `StatusUpdate.token_usage` 字段，按 step 边界取最后一次。

#### 风险与边界

- cache 字段在非 Anthropic provider 默认为 0；不要把 0 当成“无数据”。
- reasoning token 暂时无原生来源，semconv 中如需 `agentic.llm.tokens.reasoning` 应标记为可选项并在 kimi 实现侧固定为缺失。

#### 管控动作

- `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` 进硬约束。
- `agentic.llm.tokens.cache_read` / `cache_write` 进推荐项，命名通过实现层映射统一到宪章 L2/L3 标准。
- reasoning token 列入“暂不支持”，与 C12 同处理。

### 数据项 C2.6：LLM span 的错误表达

#### 结论

部分支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`StopFailure.error_type` + `StopFailure.error_message`（仅当错误导致 turn 终止时）
- transcript：`wire.jsonl` `StepInterrupted` 事件
- kosong 层异常类（不进入 wire）：`packages/kosong/src/kosong/chat_provider/__init__.py:130-159` `APIConnectionError / APITimeoutError / APIStatusError(status_code, request_id) / APIEmptyResponseError / ChatProviderError`

#### 获取方式

- 关键事件：`wire.jsonl` `StepInterrupted`（`src/kimi_cli/wire/types.py:71-74`）+ `StopFailure` hook
- 代码入口：`src/kimi_cli/hooks/events.py::stop_failure()`

#### 正证据

- `wire/types.py:71-74` `class StepInterrupted(BaseModel)` 文档原文：*"Indicates the current step was interrupted, either by user intervention or an error."*——明确指出 step 被中断的二元语义（用户中断或错误），但本身**无 payload 字段**（仅 `pass`），无法承载 error_type。
- `StopFailure` 显式提供 `error_type` 与 `error_message` 两个字段，是 turn 级错误的唯一权威来源。

#### 反证据

- **kimi-cli 主流程无 LLM 层 api_error wire 事件**：grep `retry|api_error|APIError|backoff` 全仓核查（结果只命中 `ui/shell/__init__.py:676` IPC 层 retry、`auth/oauth.py:330` OAuth retry、`auth/platforms.py:122` API key 切换 retry），均与 LLM 调用层错误无关。
- kosong 的 `APIConnectionError / APITimeoutError / APIStatusError` 异常在 `ChatProvider.generate()` 内部被抛出，由 kimi soul 主循环捕获后**仅通过 `StopFailure` 单一通道汇总**，不会单独写入 wire（除 `wire/types.py` 中无 `APIError` / `LLMError` / `RetryAttempt` 等事件类型确认）。
- `StepInterrupted` 不区分"用户中断 vs LLM 错误 vs API 错误"，且无字段；error_type 推导必须结合后续 `StopFailure.error_type`（若 turn 终止）或上下文（若中间 step 出错但 turn 继续）。
- 与 `claudecode` 的 `api_error` 独立 sibling span 模式相比，kimi 没有 step 级的 retry / api_error 元数据；retry 计数必须依赖 kosong provider 内部状态（hook 进程不可见）。

#### 推导链路

- generation span 错误：
  - 若 turn 终止：从 `StopFailure.error_type` 取值，归因到本 turn 最后一个 generation span。
  - 若 step 中断但 turn 未终止：从 wire `StepInterrupted` 标记 ERROR status，error.type 退化为 `step_interrupted`（kimi 特定 enum 值）。
- API retry 错误：当前无原生事件，连 retry 计数都无法从 wire 取得；kimi 实现侧应在 evidence-index 中标记为"暂不支持 step 级 retry 元数据"。

#### 风险与边界

- `StopFailure` 给的是 turn 级 error，不一定能精确归因到某个 generation span；与 cc 的 `api_error` 独立 sibling span 模式有差异。
- 一个 turn 内多 step 时，`StopFailure.error_type` 只能归到最后一个 step；中间 step 的 LLM 错误若已被 retry 化解，则完全不可见。

#### 管控动作

- "LLM 错误必须有 error.type + status=ERROR" 可作为硬约束，但来源声明为"StopFailure 反推 + StepInterrupted 兜底"，不要求 step 级精度。
- API retry 语义放到 C14 章节进一步评估，结论倾向"暂不支持"（与 cc 部分支持有差距）。
- `error.type` 取值约定：`stop_failure:<error_type>` / `step_interrupted` 两类，不与 cc 的 `api_error` 直接对齐。

## C3 Tool 调用、错误与耗时语义

### 数据项 C3.1：TOOL span 的语义身份与 sibling 拓扑

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`PreToolUse` / `PostToolUse` / `PostToolUseFailure`

#### 获取方式

- 关键事件：`PreToolUse` 创建 tool span 起点；`PostToolUse[Failure]` 关闭终点
- 关键字段：`tool_call_id`（关联 Pre / Post）
- 代码入口：
  - `src/kimi_cli/hooks/events.py::pre_tool_use() / post_tool_use() / post_tool_use_failure()`
  - 触发点 `src/kimi_cli/soul/toolset.py:163-200, 254`

#### 正证据

- 三个 tool 事件均直接映射为：
  - `gen_ai.span.kind = TOOL`
  - `gen_ai.operation.name = execute_tool`
- `tool_call_id` 是 Pre / Post 配对的稳定键。
- `toolset.py:163-200` 实现：先 `await hook_engine.trigger("PreToolUse", ...)` 检查 block，再执行工具，最后 fire-and-forget `PostToolUse` 或 `PostToolUseFailure`。

#### 反证据

- `kimisoul.py:1151` 还有 fallback 路径触发 `PreToolUse`（多触发源），需要 `tool_call_id` 唯一性保证；幸而 `tool_call_id` 由 kosong 在 `ToolCall.id` 处生成（`packages/kosong/src/kosong/message.py:177-181`），全局唯一。
- `wire.jsonl` 中 hook 镜像事件结构经源码核查为：
  - `HookTriggered(event: str, target: str = "", hook_count: int = 1)`（`src/kimi_cli/wire/types.py:94-103`）
  - `HookResolved(event: str, target: str = "", action: Literal["allow", "block"] = "allow", reason: str = "", duration_ms: int = 0)`（`src/kimi_cli/wire/types.py:105-115`）
  - 两者均**不含 `tool_call_id`**（仅 `target` = tool_name 之类的字符串），无法用于跨 hook 严格关联；replay 时必须以 hook payload 内 `tool_call_id` 为主键，wire 镜像仅用于时间/动作校验。
- `PostToolUse` / `PostToolUseFailure` 是 `asyncio.create_task` 异步触发（`toolset.py:159, 197`），与同步的 `PreToolUse` 之间存在乱序风险；`tool_call_id` 是唯一可靠的配对键。

#### 推导链路

- 无需推导。

#### 风险与边界

- 若 hook 进程 panic 导致 `PostToolUse[Failure]` 未触发，tool span 无关闭事件；replay 阶段必须能识别孤儿 `PreToolUse` 并合理收尾。

#### 管控动作

- `TOOL + execute_tool + sibling topology` 进入硬约束。
- golden 应覆盖：成功路径 / 失败路径 / 孤儿 PreToolUse 三种。

### 数据项 C3.2：Tool 参数与结果

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`PreToolUse.tool_input`（参数）+ `PostToolUse.tool_output`（结果）+ `PostToolUseFailure.error`（错误）

#### 获取方式

- 代码入口：`src/kimi_cli/hooks/events.py:18-58`

#### 正证据

- `PreToolUse.tool_input: dict[str, Any]` 直接给出工具调用参数：在 `toolset.py:108-110` `arguments = json.loads(tool_call.function.arguments or "{}", strict=False)` 完成 JSON 字符串到 dict 的转换，再以 `tool_input_dict = arguments if isinstance(arguments, dict) else {}` 传入 hook payload（`toolset.py:118-128`）。
- `PostToolUse.tool_output: str` 由 `events.post_tool_use(..., tool_output=str(ret)[:2000], ...)` 构造（`toolset.py:209`），**主流程已硬截断到 2000 字符**。
- `PostToolUseFailure.error: str` 由 `events.post_tool_use_failure(..., error=str(e), ...)` 构造（`toolset.py:163-173`）。
- 三者均由 kimi 主流程在 PreToolUse 阻塞返回后立即构造或在异常路径 fire-and-forget 触发，配对关系由 `tool_call_id` 强制（`toolset.py:122, 131, 175, 211`）。

#### 反证据

- 与 `claudecode` 不同，kimi 没有“result-only fallback”路径：`PreToolUse` 必有 `tool_input`，`PostToolUse[Failure]` 也必有对应输入；不存在 result-only 的孤儿场景，因此 `agentic.tool.input.missing` 标记**不需要在 kimi 实现**（除非 hook 进程 panic）。
- **`tool_output` 已被主流程硬截断到 2000 字符**（`toolset.py:209` `str(ret)[:2000]`）：这意味着 kimihooks 拿到的输出可能不是完整内容，**`agentic.tool.output.truncated` 必须默认为 `true` 且 `original_length` 不可知**（除非从 `context.jsonl` 中找到对应 `ToolResult` 取完整字符串）。
- `tool_input` 是 dict，序列化为 JSON 字符串后才能写入 span 属性；嵌套结构需保留原层级。
- 当 `tool_call.function.arguments` 为非合法 JSON 时，`json.loads` 抛 `json.JSONDecodeError`，主流程返回 `ToolParseError`（`toolset.py:111-114`），此时 **不会触发 `PreToolUse`**——参数解析失败的工具调用对 hook 不可见。

#### 推导链路

- 完整 `tool_output` 重建：优先从 `context.jsonl` 同 turn 内匹配 `tool_call_id` 的 `ToolResult` 取，hook 截断版本仅做兜底。

#### 风险与边界

- `tool_input` 中可能包含敏感数据（如 Shell 命令含 token），需在 hook 实现层做截断而非脱敏（C15 范围）。
- `tool_output` 是字符串，二进制输出（如 `read_media`）需要先 base64 化或截断。
- JSON 解析失败的工具调用是“静默丢失”场景，应在实现侧通过 wire `ToolCallRequest` 事件（`wire/types.py`）补齐。

#### 管控动作

- `gen_ai.tool.call.arguments` / `gen_ai.tool.call.result` 进入硬约束。
- “tool_output 默认带 `output.truncated=true`”进入 evidence-index，作为 kimi 比 cc 弱的项。
- 截断与缺失元数据走 C15。
- “JSON 解析失败的工具调用”需要从 wire `ToolCallRequest` 补齐 → 写入 C3 推荐项。

### 数据项 C3.3：Tool 名称、分类与调用标识

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`PreToolUse.tool_name` + `PreToolUse.tool_call_id`
- 内部状态：`KimiToolset._tool_dict` 工具注册表（`src/kimi_cli/soul/toolset.py:88` `self._tool_dict: dict[str, ToolType] = {}`）
- 类型识别：`MCPTool` 类（`toolset.py:561` `class MCPTool[T: ClientTransport](CallableTool)`）+ `AgentTool` 常量（`src/kimi_cli/tools/agent/__init__.py:15` `NAME = "Agent"`）

#### 获取方式

- 代码入口：`hooks/events.py:8-22`
- 工具注册：`KimiToolset.add(tool: ToolType)` 以 `tool.name` 为 key 注册（`toolset.py:103-104`）；`tool.name` 即为 `tool_call.function.name` 的来源（`toolset.py:115` `if tool_call.function.name not in self._tool_dict`）。

#### 正证据

- `tool_name` 直接对应 kosong tool 注册名（`toolset.py:115, 122`），是 hook 与 wire 之间的一致键。
- 分类策略基于源码事实：
  - **MCP 工具**：通过 `MCPTool(server_name, tool, client, runtime=runtime)` 实例注册（`toolset.py:453`）；**kimi 源码全文 grep 无 `mcp__` 前缀拼接**，工具名取决于 `MCPTool.__init__` 内部如何拼装 `self.name`（待 C4 章节读取 `toolset.py:561-588` 完整定义后给出权威分类规则）。当前可行的兜底是：在 `kimihooks` 实现侧维护 `mcp_tool_names: set[str]` 通过 wire 事件 `MCPLoadingEnd` / `MCPServerSnapshot` 收集。
  - **Subagent 工具**：常量 `NAME = "Agent"`（`tools/agent/__init__.py:15`），首字母大写，单一名称，可硬编码识别。
  - **Skill 命令**：`SKILL_COMMAND_PREFIX = "skill:"` / `FLOW_COMMAND_PREFIX = "flow:"`（`src/kimi_cli/soul/kimisoul.py:94-95` 与 `src/kimi_cli/ui/shell/slash.py:53`），仅在 slash 命令路径出现，**不进入 tool_call 路径**，因此 PreToolUse 不会带 skill: 前缀；skill 走的是 `_make_skill_runner` 通道（`kimisoul.py:776`），与工具调用语义解耦。
  - **内置工具**：`Shell / WriteFile / ReadFile / TaskList / TaskOutput / TaskStop` 等通过 `KimiToolset.add` 注册的 `CallableTool / CallableTool2` 实例（`toolset.py:80` `type ToolType = CallableTool | CallableTool2[Any]`）。
  - **WireExternalTool**：`toolset.py:235` 列出，由 wire 客户端动态注册的外部工具，分类为 `external`。
- `tool_call_id` 是 kosong 在 `ToolCall(id=...)` 中生成的稳定键（`message.py:177-181`），由 chat_provider 在解析 LLM 输出时分配。

#### 反证据

- **kimi 源码全文未发现 `mcp__` 字符串**（grep `mcp__\|mcp_prefix\|MCP_TOOL_PREFIX\|TOOL_NAME_SEPARATOR` 在 `src/` 与 `packages/` 下零命中），意味着分类规则不能照搬 cc 的“前缀匹配”策略；必须靠类实例 / 工具来源运行时跟踪。
- `MCPTool.__init__` 的具体 `self.name` 拼装逻辑尚需在 C4 章节中读 `toolset.py:561` 完整定义后确认；当前无法断言 MCP 工具名格式。
- 同名冲突：`KimiToolset.register_external_tool` 允许 `WireExternalTool` 占用与原生工具同名的位置（`toolset.py:236` 检查），分类时必须先按实例类型再按名称。

#### 推导链路

- 分类策略：实例类型识别（MCPTool / AgentTool / WireExternalTool / CallableTool）→ 名称硬编码匹配（`Agent` / `Shell` / ...）→ 退化 `native` 或 `unknown`。
- 由于 hook 进程无法直接访问 `KimiToolset._tool_dict`，分类必须靠 hook 侧维护的实例化时机收集（如 `MCPLoadingEnd` 事件提供的 server/tool 列表）。

#### 风险与边界

- 用户自定义 MCP 工具名若与内置工具同名将造成分类错位；以 `MCPLoadingEnd` 列表为权威来源即可避免。
- `WireExternalTool` 的语义在宪章 C4-C7 中未有专属位置，建议归入 `external` 而非强行映射到 mcp / native。

#### 管控动作

- `gen_ai.tool.name` / `gen_ai.tool.call.id` / `agentic.tool.type` 均为硬约束。
- 分类来源声明：必须基于 `MCPLoadingEnd` 维护的 MCP 工具白名单 + 硬编码 `Agent` 名称；不可使用前缀字符串匹配。
- `unknown` 兜底允许，但应在 metrics 中监测占比，超过 1% 触发告警。

### 数据项 C3.4：Tool 开始时间、结束时间与耗时

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`PreToolUse` / `PostToolUse[Failure]` 触发时刻
- 内部度量：`time.monotonic()` 包裹 tool 执行（`src/kimi_cli/soul/toolset.py:144` `t0 = time.monotonic()` + `:151, 191` `tool_elapsed = time.monotonic() - t0`）
- transcript：`wire.jsonl` 顶层 `timestamp` 字段（实测 `wire.jsonl` 行格式 `{"timestamp": <unix_float>, "message": {...}}`，protocol_version 1.9）

#### 获取方式

- hook 进程读取 stdin 后立即采样 `SystemTime::now()`
- replay 阶段：从 `wire.jsonl` 中对应 `HookTriggered` / `HookResolved` 行的顶层 `timestamp` 取时间

#### 正证据

- kimi 的 hook 事件粒度比 cc 更细：`PreToolUse` 在 `await self._hook_engine.trigger("PreToolUse", ...)` 中阻塞触发（`toolset.py:120-141`），是真实"工具执行前"锚点。
- `PreToolUse` 完成后立即 `t0 = time.monotonic()`（`toolset.py:144`），随后才执行 `await tool.call(arguments)`，时间序列严格 `PreToolUse_ts < t0 < tool_call < t1 < PostToolUse_ts`。
- duration 三源可校验：
  - 来源 1：`(Post hook 进程时间) - (Pre hook 进程时间)`
  - 来源 2：`wire HookResolved.duration_ms`（hook 批次墙钟时间，`wire/types.py:114-115` 字段定义）
  - 来源 3：`toolset.py` 内 `tool_elapsed` 仅落 logger / telemetry，不进 wire / hook payload
- 总能写出至少一种 duration。

#### 反证据

- hook 进程启动延迟（10-50ms 量级）会同时影响 Pre 和 Post 时间，duration 误差极小但绝对时间漂移。
- `PostToolUse` / `PostToolUseFailure` 是 `asyncio.create_task(...)` 异步触发（`toolset.py:159-176, 197-215`），不阻塞主流程；触发完成时间相对工具结束时间存在不可忽略延迟（毫秒到秒级）。
- `PreToolUse` 与 `PostToolUse[Failure]` 之间没有 `tool_call_id` 之外的强配对约束；若同名工具高并发执行，必须严格依赖 `tool_call_id` 配对。
- `wire HookResolved.duration_ms` 是 batch 级别（多个 hook 并发的总墙钟时间，注释原文 *"Wall-clock time for the entire batch"*），不能精确归因到单个 hook subscription。

#### 推导链路

- 优先用 hook 触发时刻（hook 进程内 `SystemTime::now()`）；replay 路径下从 `wire.jsonl` 顶层 `timestamp` 取时间，与 hook 内嵌时间二选一取最早。
- duration 校验：`(post_ts - pre_ts)` 与 `wire HookResolved.duration_ms` 在单 hook 场景下应一致；多 hook 并发时 wire 值是上界。

#### 风险与边界

- `fire-and-forget` 模式下，`PostToolUse` 的事件顺序与 wire 中 `ToolCallRequest` 后续事件可能错位；不应据此断言因果顺序，必须以 `tool_call_id` 配对而非时间排序。
- 若 hook 进程被 timeout（`src/kimi_cli/hooks/config.py:30` `timeout: int = Field(default=30, ge=1, le=600)`，**默认 30 秒，可配 1-600 秒**），`PostToolUse` 可能不触发或超时返回；engine 层默认 fail-open（`engine.py` 失败视为 allow），replay 必须能识别孤儿 `PreToolUse`。

#### 管控动作

- `agentic.tool.duration_ms` 进入硬约束，来源声明为"Pre/Post hook 时差，wire `HookResolved.duration_ms` 作为校验"。
- "tool 时间无人为偏移"作为 kimi 优于 cc 的差异点写入 evidence-index（cc 需 +1ms UI 偏移）。
- 孤儿 `PreToolUse` 必须在 replay 阶段补 ERROR status 并标记 `agentic.tool.outcome = orphan`。

### 数据项 C3.5：Tool 错误表达

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`PostToolUseFailure.error`

#### 获取方式

- 代码入口：`hooks/events.py:43`

#### 正证据

- kimi 设计上把成功 / 失败分流到两个独立 hook（`PostToolUse` vs `PostToolUseFailure`），实现层无需嗅探 `is_error` / `error` 字段 / `Error:` 前缀。
- 直接映射：`error.type = "tool_error"`，error message 写入 `gen_ai.tool.call.result` 或专属错误属性。

#### 反证据

- `error` 字段类型是 `str`，缺乏结构化错误分类；细分类与 cc 一致需走推导（暂不进入硬约束）。

#### 推导链路

- 无需推导，原生事件分流。

#### 风险与边界

- 工具执行成功但返回内容是错误信息（如 `Shell` 返回 stderr 但 exit 0）：会进入 `PostToolUse` 而非 `PostToolUseFailure`，与“事件分流”预期不一致；这部分需要在实现层额外嗅探（与 cc 一致）。

#### 管控动作

- “失败必须有 error.type + status=ERROR” 进入硬约束。
- “成功 hook 内的伪错误”作为推荐补强项。

### 数据项 C3.6：Tool 内容裁剪与语义保留

#### 结论

部分支持

#### 约束建议

推荐项

#### 数据来源

- 主流程上游截断（已发生）：`src/kimi_cli/soul/toolset.py:209` `events.post_tool_use(..., tool_output=str(ret)[:2000], ...)` —— **kimi 主流程在传给 hook 前已硬截断 tool_output 到 2000 字符**
- hook 层补充截断：`kimihooks` 实现侧的 `truncate_value()`（可拷自 cc，针对 `tool_input` 等仍完整的字段）
- 完整原文兜底：`context.jsonl` 中同 turn 内的 `ToolResult.return_value`（kosong 标准结构）

#### 获取方式

- `tool_output` 已上游截断 → hook 层无法拿到完整文本，必须从 `context.jsonl` 取
- `tool_input` / `error` 由主流程完整传入 → hook 层按 cc 约定二次截断（默认 20K 字符）

#### 正证据

- `tool_input` 与 `error` 在 hook payload 中是完整数据（无主流程截断），可由 hook 实现层按需截断并标 `agentic.tool.input.truncated` + `original_length`，与 cc 一致。
- `context.jsonl` 中 `ToolResult` 是 kosong 标准结构（`packages/kosong/src/kosong/tooling/__init__.py` 中 `ToolResult` 定义），承载完整 tool return value，可作为完整原文重建源。

#### 反证据

- **`tool_output` 在 hook 看到时已被主流程硬截断到 2000 字符**（与 cc 不同：cc 透传完整 message text，由 hook 决定截断），意味着：
  - hook 层 `agentic.tool.output.truncated = true` 必须**默认开启**
  - `agentic.tool.output.original_length` **不可知**（hook 看不到原长度）；仅当从 `context.jsonl` 取到完整原文时才能填准确值
- 当前 kimi 没有原生"缺失"场景（参见 C3.2），`agentic.tool.input.missing` 仅用于 hook 进程异常的兜底标记。
- 主流程截断阈值 `2000` 是硬编码常量（`toolset.py:209`），无配置项；若上游升级阈值需同步调整 evidence-index。

#### 推导链路

- output 完整化策略：优先 `context.jsonl` `ToolResult` → 退化 hook payload 截断版本（标 `output.truncated=true`）。
- input 截断策略：hook 层按 20K 阈值截断，与 cc 对齐，`tool_calls.arguments` 永不截断（沿用 cc 约束）。

#### 风险与边界

- `context.jsonl` 与 hook 触发不严格同步：异步 fire-and-forget 的 `PostToolUse` 与 context.jsonl 写入可能交错；replay 必须按 `tool_call_id` 匹配，不依赖时间排序。
- 主流程上游截断意味着 kimi 在"完整 output 重建"维度比 cc 弱，必须接受 replay 路径才能取完整数据，hook 实时路径只能保留前 2000 字符。

#### 管控动作

- `agentic.tool.input.truncated` / `agentic.tool.output.truncated` 进推荐项。
- `agentic.tool.output.original_length` 来源声明：仅当从 `context.jsonl` 取到完整原文时填，否则缺失（不可写 `0` 或 `2000`）。
- 主流程 2000 字符硬编码进入 evidence-index，标记为"kimi 上游截断"已知差异点。
- semconv 注册沿用 cc 既有项（`agentic.tool.*.truncated` / `agentic.tool.*.original_length`），无需新增 kimi 专属属性。