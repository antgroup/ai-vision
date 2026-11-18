# Kimi-CLI 第四章证据调研：并发、错误治理与内容处理

本文档按"数据项"举证各条款。格式沿用第一章。

证据指向的源码均位于 `<kimi-cli>`（独立的 kimi-cli 上游仓库），下文路径为该仓库相对路径。

## 总览矩阵

| 条款 | 结论 | 主要数据来源 | 可靠性 | 实现代价 | 架构影响 | 约束建议 |
|---|---|---|---|---|---|---|
| C13 并发工具 | 部分支持 | 同一 assistant response 内 `tool_calls` 数组（wire `ToolCall` 在同一 StepBegin 内） | 推导 | 中 | 低 | 推荐项 |
| C14 Cancel/Retry | 部分支持 | `StopFailure(error_type, error_message)` + wire `StepInterrupted` + `PostToolUseFailure.error` | 原生可得 + 局部缺失 | 低 | 低 | 推荐项 |
| C15 截断与缺失 | 推荐 | 实现层 `truncate_value()` + 上游 `tool_output[:2000]` 硬截断 + `agentic.tool.{input/output}.truncated` 元数据 | 实现可得 | 低 | 低 | 推荐项 |

---

## C13 并发工具调用语义

### 数据项 C13.1：并发工具的识别

#### 结论

部分支持

#### 约束建议

推荐项

#### 数据来源

- transcript：wire.jsonl 中同一 `StepBegin` 内连续出现的 `ToolCall` 序列
- runtime hook：同一 turn 内的多个 `PreToolUse`，共享相似的触发时间窗
- 上游实现：`KimiToolset.handle` 内部无并发执行（`await tool.call(arguments)` 串行，`toolset.py`），但 LLM 单次 response 可产出 `tool_calls[]` 多个

#### 获取方式

- wire `StepBegin` → 随后多条 `ToolCall`（无中间 `StepBegin`）→ 并发候选
- hook `PreToolUse` 记录 `tool_call_id` + 触发时间；同一 turn 内时间间隔 < 阈值（如 50ms）的多个 Pre 视为并发组

#### 正证据

- kosong assistant message 支持 `tool_calls[]` 数组（`packages/kosong/src/kosong/message.py:177-181` `ToolCall`），一次 LLM 调用可返回多个 tool call
- wire `ToolCall` 在 wire.jsonl 中以时序方式连续出现：同一 `StepBegin` 后的 `ToolCall` → `ContentPart`/`StatusUpdate` → `ToolCall`(s) → `ToolResult`(s) 都是同一轮 LLM 决策的产出
- **kimi 的 tool 执行是串行的**（`toolset.py` handle 逐个 await），但在语义上同一 assistant 产出的多个 tool calls 是"并发决策"，符合 C13 "concurrent tools share same LLM call" 的语义层次

#### 反证据

- kimi-cli **没有原生并发执行 tool**（`toolset.py:144` `tool_elapsed = time.monotonic() - t0` 逐个工具计时），**真正的并行执行不存在**（不像 cc 有 `asyncio.gather` 并行 tool）
- `tool_call_id` 之间没有共享的 group key；并发组必须从 assistant response 的 `tool_calls` 数组推导
- wire `ToolCall` 在 wire 流中是串行输出的（每个 ToolCall 一个 wire event），无法直接判断"同组"
- **`agentic.tool.parallel_group` 需要实现层主动分配**：replay 阶段扫描同一 turn 内、同一 `assistant` 行产出的所有 tool_calls → 分配共享 group ID

#### 推导链路

- replay 阶段（当前未实现）：
  1. 扫描 `wire.jsonl`：在两个相邻 `StepBegin` 之间的所有 `ToolCall` → 同 batch
  2. 扫描 `context.jsonl`：同一 `assistant` 行的 `tool_calls[]` 数组长度 > 1 → 并发候选
  3. 为同 batch 的所有 tool span 写入共享 `agentic.tool.parallel_group = batch_{assistant_message_id}`
- 实时 hook 路径（需 `PreToolUse` 时间窗推断）：
  1. `PreToolUse` 触发时采样时间
  2. 连续多个 Pre（无中间 Post）→ 时间窗 < 阈值（如 100ms）→ 并发组

#### 风险与边界

- **kimi 实际上没有并发执行 tool**：串行 `await tool.call()` 意味着 tool 执行时间是完全错开的（sequential）。"并发决策"和"并发执行"在 kimi 上语义不一致
- C13 的 `parallel_group` 是推荐项（宪章明确 "not a blocking requirement until semconv-registered"）
- 若 kimi 未来支持真正的 `asyncio.gather` 并发 tool，时间窗判定会更稳定

#### 管控动作

- `agentic.tool.parallel_group` **推荐项**，不作为 weaver hard constraint
- 实现建议：
  - P2：replay 阶段从 `context.jsonl` `/ wire tool_calls` 分配 `parallel_group = tool_calls_{message_id}`
  - 仅当 1 个 assistant response 产出 >= 2 个 tool_calls 时才写此字段
- golden 应覆盖（future）：同一 assistant 产出 3 个 tool_calls → 3 个 tool span 共享 1 个 parallel_group

---

## C14 Cancel / Interrupt / Timeout / Retry 语义

### 数据项 C14.1：StopFailure — Turn 级失败的总管道

#### 结论

部分支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`StopFailure`（`src/kimi_cli/hooks/events.py::stop_failure()`）
- `matcher_value` = Python exception class name（如 `"APIStatusError"`）

#### 获取方式

- payload：`error_type`（exception class name）+ `error_message`（exception text）
- 触发时机：soul 主循环 catch 到异常 → `hook_engine.trigger("StopFailure", ...)` fire-and-forget（`kimisoul.py:893-907`）

#### 正证据

- kimi 是少数原生提供 turn 失败 hook 的 agent：`StopFailure` 直接给出 `error_type` + `error_message`
- vs cc：cc 只能从 assistant `stop_reason` 字段嗅探（`end_turn`/`tool_use`），kimi 有独立失败 hook
- `StopHandler` 同时监听 `Stop` 和 `StopFailure`（`stop.rs:98` `const EVENTS = [Stop, StopFailure]`），两个事件都走同一 wire.jsonl 重建 + HTTP 上报链路

#### 反证据

- `StopFailure.error_type` 值无枚举定义（源码中是 Python exception class name），依赖运行时捕获；映射到 `error.type` 语义需要处理多种 class name 格式（如 `APIStatusError` / `APIConnectionError` / `ToolError`）

#### 推导链路

- `StopFailure.error_type` → `status.code = ERROR` + `error.type = stop_failure:{error_type}`
- kimi 实现侧映射建议：
  - `APIStatusError` → `error.type = api_error`
  - `APIConnectionError` → `error.type = api_connection_error`
  - `APITimeoutError` → `error.type = timeout`
  - 其他 exception name → 保持原值

#### 风险与边界

- `StopFailure` 是 fire-and-forget：hook 进程触发时 kimi 主流程可能还在 unwind 中，wire.jsonl 可能还没刷完最后几行——Stop handler 中的 `wire.jsonl` read 可能截断在错误发生前。**这是比 cc 弱的地方**（cc 的 transcript 在主流程完成后才落盘）

#### 管控动作

- `StopFailure` → root span status=ERROR 进硬约束
- `error.type` 映射规则写入 evidence-index
- golden 应覆盖：API 错误导致 turn 终止 / 工具错误导致 turn 终止 / 正常 Stop 后 StopFailure 不触发

### 数据项 C14.2：StepInterrupted — Step 级中断

#### 结论

部分支持

#### 约束建议

推荐项

#### 数据来源

- transcript：wire `StepInterrupted`（`wire/types.py:71-74`）

#### 获取方式

- wire.jsonl `StepInterrupted` 作为 step 边界的异常闭包

#### 正证据

- `StepInterrupted` 文档原文："Indicates the current step was interrupted, either by user intervention or an error"
- 可以作为 generation span 的 ERROR status 触发源

#### 反证据

- `StepInterrupted` 无 payload 字段（空的 `BaseModel`，仅类型标签）
- 不区分"用户中断"、"API 错误"、"其它错误"——没有 `error_type`
- **Stop handler 当前不处理** `StepInterrupted`：`WireParser.feed_typed` 中无此分支（`stop.rs:618-828`）
- kimi-cli 主流程无 LLM api_error wire 事件（grep `retry|api_error` 全仓只有 IPC/OAuth retry，无 LLM call retry event）
- API retry 元数据（retry 次数、延迟）完全不可见（kosong provider 内部，hook 进程不可达）

#### 推导链路

- 若处理：StepInterrupted 出现 → 当前未 flush 的 step flush 为 ERROR status（`error.type = interrupted`）

#### 风险与边界

- 与 cc 的 `api_error` 独立 sibling span 模式相比，kimi 完全缺少 per-attempt retry/error 元数据
- "每个 retry attempt = 独立 span" 对 kimi **不可实现**

#### 管控动作

- `StepInterrupted` 处理为推荐项（P2），不升硬约束
- API retry 暂不支持（与宪章 C14 "full structured retry attempt not required for all Agents" 一致）
- 实现 gap：`WireParser` 需增加 `StepInterrupted` → flush step + error.type

### 数据项 C14.3：PostToolUseFailure — Tool 级错误

#### 结论

支持

#### 约束建议

必须项

#### 数据来源

- runtime hook：`PostToolUseFailure.error: str`（`src/kimi_cli/hooks/events.py:43`）
- Stop handler wire 重建：`ToolResult.return_value.is_error = true` → `PostToolUseFailure` 合成事件（`stop.rs:755-777`）

#### 获取方式

- 原生成功/失败分流：`PostToolUse` vs `PostToolUseFailure` — **无需额外错误嗅探**
- error 字段内容：Python exception `str(e)`（`toolset.py:163-173`）

#### 正证据

- vs cc：cc 工具结果在助理 reply 中统一输出，需要从结果消息嗅探错误标记（如 `Error:` 前缀）。kimi **原生区分成功/失败**，消除了误判
- `otel.rs:254-261` `ToolOutcome::Failure { error }` → `status.code = ERROR` + `error.type = "tool_error"`
- wire 重建也支持：`stop.rs:735-739` 从 kosong `ToolResult.return_value.is_error` 字段区分

#### 反证据

- error 字段格式为 `str`（非结构化），无法直接提取结构化 error 信息
- 场景缺口：Shell exit=0 但 stderr 非空 → 会被路由到 `PostToolUse`（成功），不触发 failure hook

#### 推导链路

- 无需推导：事件自带成功/失败分类

#### 风险与边界

- Shell exit=0 但实际内容表示出错的"伪成功"不在 hook 语义范围内，需实现侧内容嗅探（P3）

#### 管控动作

- `PostToolUseFailure` → tool span status=ERROR 进硬约束
- golden 应覆盖：Shell 失败 / File 失败 / 并发工具中某个失败

---

## C15 截断与缺失

### 数据项 C15.1：上游硬截断（tool_output）

#### 结论

支持（但标记已知上游截断）

#### 约束建议

推荐项

#### 数据来源

- **kimi 主流程硬截断**：`src/kimi_cli/soul/toolset.py:209` `str(ret)[:2000]` —— `PostToolUse.tool_output` 在主流程中**已经截断到 2000 字符**
- 完整原文兜底：`context.jsonl` 中同 turn 内 `ToolResult.return_value.output`（kosong 完整 tool return value）

#### 获取方式

- hook payload 中 `tool_output` 恒被上游截断
- 完整 tool_output 必须从 `context.jsonl` 按 `tool_call_id` 取 `ToolResult.return_value.output`
- `tool_input` / `error` 在 hook 中是完整值（无上游截断），由 hook 实现层按需截断

#### 正证据

- `otel.rs` 的 `truncate_value()` 处理 `tool_input` 和 `error`（`:225-234, 248-258`）：
  - >20K 字符 → 截断 + sha256 + orig_len/kept_len 元数据
  - ≤20K 字符 → 不做任何标记，原值写入
- C15 元数据实现已完备（`:290-314` `append_truncation_meta`）：
  - `agentic.tool.{input|output}.truncated = "true"`
  - `agentic.tool.{input|output}.original_length = <orig_len>`
  - `agentic.tool.{input|output}.sha256 = <sha256_of_original>`
- 参数若为 object → 序列化为 JSON 字符串后整体截断（`otel.rs:225`），不破 JSON 结构

#### 反证据

- **tool_output 的截断是上游做的，不是 hook 层做的**：hook 完全不知道原始长度。导致：
  - `agentic.tool.output.truncated` 必须默认 `true`（除非从 `context.jsonl` 取到完整原文验证未超 2000）
  - `agentic.tool.output.original_length` 不可知（除非从 `context.jsonl` 取）
- 上游截断阈值 2000 是硬编码常量（`toolset.py:209`），kimi 升级可能调整，但当前无影响
- **`agentic.tool.output.truncated` 当前未写入**：实现 gap——已知 `tool_output` 在主流程已截断，但 `otel.rs` 成功路径调用 `truncate_value` 后再截断一次（双重截断），需要记录"主流程已截断 → 2000 → original_length 不可知"

#### 推导链路

- output 截断路径：
  - hook 实时：`agentic.tool.output.truncated = true` + `original_length = null`（上游截断，原始长度不可知）
  - replay 从 `context.jsonl` 取完整 output：`truncated = (完整output长度 > 2000)` + `original_length = 实际长度`
- input 截断路径（hook 实现层）：>20K → `agentic.tool.input.truncated = true` + sha256 + orig_len

#### 风险与边界

- 双重截断（主流程 2000 + hook 20K → 实际保留 2000）：下游看到的最大 content 只有 2000 字符
- 主流程截断与 hook 截断互不知晓，C15 metadata 写不全——**需在 `otel.rs` 中区分输入/输出的截断来源**

#### 管控动作

- `agentic.tool.input.truncated` / `agentic.tool.output.truncated` 进推荐项（P1）
- "主流程 2000 → output.truncated=true + original_length=null" 写入 evidence-index
- golden 应覆盖：<2K 不截断 / >2K 标注 truncated / object 入参不破 JSON

### 数据项 C15.2：缺失标记

#### 结论

部分支持

#### 约束建议

推荐项

#### 数据来源

- kimi 的 hook 设计保证了 input/output 不会出现"缺失"场景（与 cc 不同：cc 有 "result-only" 场景缺失）
- 缺失只在 hook 进程异常时才产生：如 PostToolUse hook 未触发（进程 panic / timeout）→ 孤儿 PreToolUse

#### 获取方式

- 孤儿 `PreToolUse`（未配对）在 `otel.rs:150-163` 中生成 `ToolOutcome::Terminated` span：
  - `error.type = "terminated"`
  - `agentic.tool.terminated = "true"`
  - end_time = pre.start_time（duration 0）
- 这是目前唯一的"缺失/中断"场景

#### 正证据

- `otel.rs:150-163` 处理所有 `pending` HashMap 中遗留的 orphan PreToolUse：每次 replay 自动补全
- orphan 数量在 `build_tool_metrics` 中作为 `aivision.agentic.exec_error(error.type=terminated)` 上报

#### 反证据

- 缺失标记 `agentic.tool.input.missing` 在 kimi 场景**不适用**（所有 tool input 都有值，不存在 result-only tool call）
- 孤儿标记使用 `terminated` 而非 `missing` 语义（语义精确度差异）
- 宪章 C15 "missing / redacted / truncated 三者可区分" 中 kimi 的 `missing` 仅对应 orphan 场景（不是内容缺失）

#### 推导链路

- 孤儿 PreToolUse → `error.type = "terminated"` + `agentic.tool.terminated = "true"`

#### 风险与边界

- "孤儿 tool call" 的原因不可知（hook 进程被 kill / timeout / panic），无法给出精确归因
- 孤儿 tool call 的时间戳退化为"Pre 触发时刻"（无有效 Post 可配对），duration 为 0

#### 管控动作

- orphan tool span（terminated）进硬约束：每个未配对的 PreToolUse 必须有 span
- `agentic.tool.terminated` 进入 golden 验证