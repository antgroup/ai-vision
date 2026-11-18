# Kimi-CLI 第三章证据调研：Compaction、Messages 与 Tokens

本文档按"数据项"举证各条款。格式沿用第一章。

证据指向的源码均位于 `<kimi-cli>`（独立的 kimi-cli 上游仓库），下文路径为该仓库相对路径。

## 总览矩阵

| 条款 | 结论 | 主要数据来源 | 可靠性 | 实现代价 | 架构影响 | 约束建议 |
|---|---|---|---|---|---|---|
| C8 Compaction | 支持 | `PreCompact` / `PostCompact` hooks + wire `CompactionBegin/End` | 原生可得 | 低 | 低 | 必须项 |
| C9 Input/Output Messages | 支持 | `context.jsonl` kosong messages + `UserPromptSubmit.prompt` | 原生可得 | 低 | 低 | 必须项 |
| C10 增量 Input | 支持 | `context.jsonl` 按 step 边界差分化（`context_parser.rs`） | 推导 | 中 | 低 | 必须项 |
| C11 Token 语义 | 支持 | wire `StatusUpdate.token_usage`（`kosong.chat_provider.TokenUsage`） | 原生可得 | 低 | 低 | 必须项 |
| C12 System Instructions | 暂不支持 | `context.jsonl` 含 `_system_prompt` 行，但无稳定获取路径 | 不适用 | 高 | 中 | 放弃 |

---

## C8 Compaction 语义

### 数据项 C8.1：Compaction 的 hook 事件与边界

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`PreCompact`（`src/kimi_cli/soul/kimisoul.py:1151-1160`）+ `PostCompact`（`:1218-1230`）
- transcript：wire `CompactionBegin` / `CompactionEnd`（`wire/types.py`）
- telemetry：`compaction_triggered`（`src/kimi_cli/telemetry/__init__.py`）

#### 获取方式

- `PreCompact` payload：
  - `session_id`, `cwd`, `trigger`（`"auto"` 或 `"manual"`）, `token_count`（压缩前 token 数）
  - `matcher_value` = trigger reason
- `PostCompact` payload：
  - `session_id`, `cwd`, `trigger`, `estimated_token_count`（压缩后估计 token 数）
  - `matcher_value` = trigger reason
- 自动触发条件（`kimisoul.py` compact 逻辑）：
  - `context_tokens + reserved_context_size >= max_context_size` 或
  - `context_tokens >= max_context_size * compaction_trigger_ratio`（默认 0.85）

#### 正证据

- kimi 的 compaction 事件比 cc 更丰富：**Pre + Post 双事件 + 原生 token 计数**，无需从相邻 LLM span token 变化反推
- `token_count` 在 `PreCompact` 触发时由 kimi 主流程计算（`kimisoul.py:1152` `current_tokens`），**不经过 kosong message schema**，直接来自 context counter
- `PostCompact` 的 `estimated_token_count` 是压缩后重建的 context token 估计值，可在 span 中写 `agentic.compaction.tokens.after`
- `trigger` 区分自动/手动，可直接映射 `agentic.compaction.trigger`（`auto` / `manual`）

#### 反证据

- `PostCompact` 是 `asyncio.create_task` fire-and-forget 触发（`kimisoul.py:1228`），存在乱序风险：在极端并发场景下 PostCompact 可能比下一个 turn 的 `UserPromptSubmit` 更晚到达 hook 进程
- `estimated_token_count` 是"估计"值而非精确值（变量名中的 `estimated_`），精度取决于 kimi 的 token 估算准确度
- wire `CompactionBegin/End` 事件在 `wire/types.py` 中定义了但在 Stop handler 的 `WireParser` 中**未消费**（`stop.rs feed_typed` 无 compaction 分支）；compaction span 当前只靠 `PreCompact` / `PostCompact` hook 事件（需用户注册这些 hook）

#### 推导链路

- `otel.rs` `build_compaction_spans()`（`:1185-1255`）：
  - 扫描 hook_log 中 hook_event_name=`Compaction` 的记录
  - 单条 `Compaction` 事件（来自 Stop handler 合成的合并记录）→ 1 个 compaction span
  - tokens_saved = tokens_before - tokens_after
- Stop handler 目前**不**合成 Compaction 事件（`WireParser` 不处理 `CompactionBegin/End`），这是已知 gap

#### 风险与边界

- 如果用户只注册了 Stop hook（推荐配置），compaction 事件不会自动重建，需要额外注册 `PreCompact` + `PostCompact` hooks
- 宪章 C8 要求 compaction span "must not be silently absorbed into an adjacent LLM span"，当前实现独立产生 compaction span 满足此要求

#### 管控动作

- `agentic.compaction.tokens.before/after/saved` + `agentic.compaction.trigger` 进入硬约束
- `gen_ai.tool.name = compaction` + `gen_ai.span.kind = TOOL`（对齐 C3 Tool span 模型）
- **实现 gap**：Stop handler 的 `WireParser` 需要增加 `CompactionBegin/End` → 合成 Compaction hook 事件。当前需用户手动注册 `PreCompact` / `PostCompact` hooks
- golden 应覆盖：自动压缩（trigger=auto）/ 手动压缩（用户在 prompt 中显式要求压缩）

---

## C9 Input / Output Messages 语义

### 数据项 C9.1：Messages 的数据来源与格式

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- transcript：`context.jsonl`（kosong 标准 message schema，JSONL 格式，逐行追加）
- runtime hook：`UserPromptSubmit.prompt`（本 turn 起始的 user input）
- 旁证：wire `TurnBegin.user_input`（`stop.rs` 中 `extract_user_input` 解析）

#### 获取方式

- `context.jsonl` 每行一个 kosong JSON 对象，role 枚举：
  - `_system_prompt` → 系统提示词（`context_parser.rs:69-76` 转为 `"role": "system"`）
  - `user` → 用户输入
  - `assistant` → LLM 回复（content + tool_calls + partial 标记）
  - `tool` → 工具执行结果（tool_call_id + content）
  - `_usage` → token 用量（内部行，`context_parser.rs:65-66` 过滤）
  - `_checkpoint` → 内部检查点（同上过滤）

#### 正证据

- `context.jsonl` 是单调追加的完整 message 流，可直接用 `context_parser.rs:42-47` `parse_context_file()` 一次性读全
- `context_parser.rs:51-103` `parse_context_lines()` 实现：
  - 第一遍：过滤 `_usage` / `_checkpoint`，`_system_prompt` → `system` 转换
  - 第二遍：按 `assistant` 行切分 LLM 调用 → `LlmCallContext` 列表
  - 每个 `assistant` 行前的所有消息 = 该次 LLM 调用的 input_messages
- `tool_calls` 在 `assistant` 行中是 `ToolCall.function.arguments`（JSON 字符串），`context_parser.rs:106-116` `extract_tool_call_ids()` 从 `tool_calls[].id` 提取
- `tool` 行有 `tool_call_id` 用于关联 assistant 的 tool_calls
- Message 结构对齐 OpenAI 格式：
  - `content` 可能是字符串（单 `TextPart`）或 `[{type: "text", text: "..."}]` 数组（多 part）——`context_parser.rs:133-148` 同时处理两种形态
  - `tool_calls` 在 assistant 行中标准嵌套
  - `ThinkPart` 特殊处理：`type: "think"` → `[thinking] {content}` （`context_parser.rs:140-141`）

#### 反证据

- **kimi 特有伪角色 `_system_prompt`**：`context.jsonl` 首行常见 `{"role": "_system_prompt", ...}`（实测于 `~/.kimi/sessions/.../context.jsonl`）。`context_parser.rs` 将其转为 `"role": "system"`，按 C12 "暂不支持" 策略在 messages 中包含（不丢弃），**但不标记为 C12 的 system instructions**（C12 语义不同：system instructions 是 agent 配置层面的指令，不是 context.jsonl 中的 system role）
- `Message.partial: bool | None` 字段（`message.py`）：streaming 过程中的不完整 message。`context_parser.rs` 当前**不检查** `partial` 字段，streaming 快照可能被当作正式 message。需在 `parse_context_lines` 中过滤 `partial=true` 行
- `Message.content` 多 part 场景：`context_parser.rs:137-143` 把 `think` / `text` 拼成单一字符串（`[thinking] reasoning\nanswer`），丢弃 `image_url` / `audio_url` 等的结构化信息（简化了但符合当前 semconv 没有多模态消息字段的现状）
- `ToolCall.function.arguments` 类型为 `str`（非 dict）：`context_parser.rs:109-113` 仅提取 tool_calls.id，不处理 arguments 内容

#### 推导链路

- `otel.rs` messages 构建遵循三层模型：
  1. **Root span**（`:961-967`）：`gen_ai.input.messages` = user 在末尾 1 条（C9 root 规则），protected_tail=1
  2. **Root span**（`:969-989`）：`gen_ai.output.messages` = 所有 LLMStep.output_text 拼成 assistant messages（按出现顺序）
  3. **LLM span**（`:1080-1087`）：`gen_ai.input.messages` = 从 `_input_messages_json`（context.jsonl 注入）直接读取；`gen_ai.output.messages` = 当前 step 的 assistant response + reasoning_content
- serialization 统一走 `agentic_otel_common::serialize_messages_default()`，带 20K 软截断 + protected_tail

#### 风险与边界

- `context.jsonl` 与 `wire.jsonl` 异步写入：Stop hook 触发时 wire.jsonl 已刷盘（主循环结束才触发），但 `context.jsonl` 的最后几行可能还在 buffer 中未 flush（罕见但可能）。如果读取时 context 比 wire 少几行，input messages 完整性受损
- `partial=true` 未过滤会导致 streaming dump 的中间状态进入正式 messages——**实现 gap**，需在 `context_parser.rs` 中追加 `if msg.partial == true { continue }`
- kosong 的 `ThinkPart` 无 OpenAI schema 对应字段，当前归并到 `assistant.content` 字符串是折中方案

#### 管控动作

- "user 在 root span 末尾" 进入硬约束
- golden 应覆盖 3 种 message 场景：纯文本 turn / 含 tool 调用 turn / 多 LLM step turn
- `partial=true` 过滤为 P1 实现 gap
- `_system_prompt` → system 转换不做验证（C12 放弃的连锁影响）

---

## C10 增量 Input（Delta）语义

### 数据项 C10.1：Delta 的可行性与实现

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- `context.jsonl` 完整 message 序列 → replay 阶段按 step 切窗 → 差分化
- `context_parser.rs:51-103` `parse_context_lines()` 按 assistant 行分割各轮 LLM 调用的 input

#### 获取方式

- `context_parser.rs:81-100`：每个 `assistant` 行的 `accumulated_input` 就是该次的累积 input
- Delta = 当前 step 的 `input_messages` - 上一 step 的 `input_messages + assistant_response`
- 具体：新的 user 消息 + 上一轮的 assistant response（含 tool_calls）+ 新执行的 tool results

#### 正证据

- `context.jsonl` 的单调追加 + assistant 分割天然支持 delta 计算：
  - 第一个 generation：delta = 全部 input messages
  - 后续 generation：delta = 上一轮 assistant + 本轮的 tool results（如果有）
- `context_parser.rs:84` `input_messages: accumulated_input.clone()` 在每次遇到 assistant 时抓快照
- 无需 post-hoc string diffing（宪章 C10 明确禁止）

#### 反证据

- delta 计算依赖 step 边界正确切分：`context.jsonl` 中的 assistant 行是唯一分割依据。如果 kosong 在 streaming 中写入多个 partial=true 的 assistant 行，delta 切分会失效（必须过滤 partial=true）
- **hook 实时路径无 delta**：只有在 Stop hook 的 replay 路径（读 `wire.jsonl` + `context.jsonl`）才能计算 delta。实时 `PreToolUse` / `PostToolUse` hook 路径只有单个 tool 调用，无法获取 step 边界
- delta **只在 generation span 上写入**，不在 root span（`otel.rs:961` root span 只有 1 条 user message，不写 delta）

#### 推导链路

- replay 阶段：`for step_i in steps: delta = input_messages[step_i] - input_messages[step_i - 1]`
- `serialize_messages_default(&delta_messages, 0)`（delta 不需要 protected_tail）
- 字段路径：`agentic.input.delta`（JSON array 字符串，与 `gen_ai.input.messages` 相同格式）

#### 风险与边界

- 当前 `otel.rs` 中 `build_llm_spans` **已支持** `gen_ai.input.messages`（从 `_input_messages_json` 读取），但 `agentic.input.delta` **尚未写入**（字段已预留，需在 build_llm_spans 内部追加 delta 计算逻辑）
- delta 计算需要持有"上一个 step 的 input_snapshot"，`otel.rs` 当前是每条 LLMStep 独立处理（无上下文共享），需在 `build_llm_spans` 中改为顺序处理并持有 `previous_input`

#### 管控动作

- "delta 在 generation span 上，不在 root span" 进入硬约束
- delta 写入为 P1 实现 gap：`otel.rs` `build_llm_spans` 需加跨 step 状态
- golden 应覆盖：首次 generation（delta=input）/ 工具调用后 generation（delta=上一轮 assistant + tool results）

---

## C11 Token 语义

### 数据项 C11.1：Token Usage 的数据来源与准确性

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- transcript：wire `StatusUpdate.token_usage`，类型 `kosong.chat_provider.TokenUsage`（`packages/kosong/src/kosong/chat_provider/__init__.py:99-118`）
- wire event：`StatusUpdate.token_usage`（`wire/types.py:171` `token_usage: TokenUsage | None`）

#### 获取方式

- Stop handler 的 `WireParser`: `feed_typed("StatusUpdate", ..., ts)` 读到 token_usage → 写入 `LLMStepAccum.token_usage` → `flush_step()` 时作为合成 event 的 `token_usage` 字段（`stop.rs:677-683`）
- replay 阶段 `otel.rs:1090-1097`：从 `LLMStep` 记录的 `token_usage` 直接提取

#### 正证据

- `kosong.TokenUsage` 字段定义（源码确认）：
  - `input_other: int` — 不含缓存的纯输入 token
  - `output: int` — 输出 token
  - `input_cache_read: int = 0` — 缓存命中（Anthropic provider 特有）
  - `input_cache_creation: int = 0` — 缓存写入（同上）
  - 派生 `input = input_other + input_cache_read + input_cache_creation`，`total = input + output`
- 映射已实现（`otel.rs:1091-1111`）：
  - `gen_ai.usage.input_tokens = TokenUsage.input`
  - `gen_ai.usage.output_tokens = TokenUsage.output`
  - `gen_ai.usage.cache_read_input_tokens = input_cache_read`（仅 >0 时写入）
  - `gen_ai.usage.cache_creation_input_tokens = input_cache_creation`（同上）
- `StatusUpdate.token_usage` 每 step 取**最后一次非空**值（`stop.rs:681` `acc.token_usage = Some(tu.clone())`），直接覆盖而非累加——因为 kimi 的 StatusUpdate 是 streaming 累积，不是增量更新

#### 反证据

- `TokenUsage` **不含 `reasoning_tokens` 字段**：OpenAI 风格的 reasoning token 在当前 kosong schema 中无承载位置。如需采集需扩展 kosong 上游或归并到 `output`（当前实现：reasoning 计入 output total）
- `StatusUpdate.token_usage` 是 streaming 累积值不是增量值：**kimi 不做 differencing**（与 cc 的 "累计必须先做差分" 不同），因为每个 step 内最后一次 StatusUpdate 就是完整值
- cache 字段命名与 semconv 有漂移：
  - kimi: `input_cache_read` / `input_cache_creation`
  - semconv: `cache_read_input_tokens` / `cache_creation_input_tokens`
  - `otel.rs:1138-1149` 已做映射标准化
- `TokenUsage` 可能为 None（StatusUpdate 在 token 还没算出来时已触发，多见于 streaming 前期）

#### 推导链路

- 无需推导：`TokenUsage.input` / `TokenUsage.output` 直接写入
- 当 `token_usage = null` 时：`otel.rs:1093-1098` 退化为 0（`unwrap_or(0)`）

#### 风险与边界

- token 只在 LLM span 上承载（C11 关键约束），**不在** root span 上聚合（防止下游重复累加）——`otel.rs` `build_root_span` 确认没有 token 属性
- reasoning token 缺原生来源，不影响 `gen_ai.usage.*` 必须属性（input/output 都有）

#### 管控动作

- `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` 进硬约束
- "不在 root span 重复聚合" 进硬约束
- `agentic.llm.tokens.cache_read` / `cache_write` 进推荐项
- reasoning token 暂不采集（与 C12 同策略）
- golden 应覆盖：token 非零 / token 为零（StatusUpdate 缺失）/ 有 cache_read（若 Anthropic provider 触发）

---

## C12 System Instructions 语义

### 结论

暂不支持

### 约束建议

放弃

### 数据来源

- 内部状态：kimi-cli 的 `system_prompt` 由 `ChatProvider.build_chat_params` 内部拼接（`packages/kosong` provider 层）
- `context.jsonl`：含 `_system_prompt` 行（实测首行），但这是**实际发送给 LLM 的 system message**，不是 agent 配置层面的"system instructions"
- **不在 hook 中暴露**：13 个 hook 事件均不含 `system_prompt` / `system_instructions` 字段

### 获取方式

- 不适用

### 正证据

- `context.jsonl` 首行通常为 `{"role": "_system_prompt", "content": "..."}`，`context_parser.rs:69-76` 已转为 `"role": "system"` 纳入 messages

#### 反证据

- kosong `ChatProvider.build_chat_params` 中 `system_prompt` 的构造逻辑在 provider 层内部，无 hook / wire 出口
- `_system_prompt` 在 context.jsonl 中的位置不确定（依赖 session 初始化时机），不是所有 session 首行都是它
- kimi 的 system 内容可能包含 Moonshot 私有模板（敏感），即使有 hook 出口也不适合默认采集
- 与 cc 一致：cc 的 system prompt 来自 `claude.md` / CLAUDE.md 等文件拼接，kimi 类似，都是多来源拼装
- **宪章 C12 明确允许暂不支持**："All Agents do NOT need to record system instructions" + "No blind-guessing final system from config files"

### 推导链路

- 不存在可推导路径："从 config 文件猜测"被明确禁止
- 退而求其次可选做法：在 `context.jsonl` 的 `_system_prompt`（已转 system）中提取纯文本，但不标记为 C12 的 `gen_ai.system_instructions`（那是 agent 配置层面的，context.jsonl 中是 LLM 输入层面的）

### 风险与边界

- 无

### 管控动作

- C12 与 cc 同设为"暂不支持 / 放弃"
- `00-总览.md` 已标记为"放弃"，无需变更
- golden 和 weaver 都不校验 `gen_ai.system_instructions` 的存在性