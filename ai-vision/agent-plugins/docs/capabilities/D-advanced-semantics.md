# D 组：高阶语义

← 返回 [CAPABILITIES.md](../../CAPABILITIES.md)

本组覆盖 4 项能力，关注**复杂场景**的语义表达：上下文压缩、子 agent 委派（同步 / 异步）、错误恢复（cancel / retry）。

> **D3（异步子 agent）通常较弱**：当前纳入矩阵的 agent 中，子 agent 链路通常无法稳定跨 trace 关联（详见 D3 小节）。

## D1 Compact 识别

### 定义

Agent 在上下文接近窗口上限或会话过长时发生的**上下文压缩 / 重写 / 摘要化**动作可被识别为独立 span，而不是被静默吞并进相邻 LLM span。

### 必要属性 / Span 字段

Compaction 应表现为一个独立 span，挂载在 turn / root span 下：

- `gen_ai.span.kind = TOOL`
- `gen_ai.operation.name = execute_tool`
- `gen_ai.tool.name = compaction`

推荐扩展属性（项目级别）：

- `agentic.tool.type = system`
- `agentic.compaction.trigger`
- `agentic.compaction.tokens.before`
- `agentic.compaction.tokens.after`
- `agentic.compaction.tokens.saved`

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | compaction 作为独立 span；位于触发它的 LLM 调用之后、压缩后下一次 LLM 调用之前；记录压缩前后 token；失败有 error status |
| 🟡 部分 | 仅有 compaction 事实（marker），无完整 span；或前后 token 缺失；或时间边界为推算 |
| ❌ 未实现 | hooks/transcript 中可见 compaction 边界与 token 变化，实现未生成独立 span（被静默吞进 LLM span） |
| ⚪ 不适用 | Agent 自身不支持上下文压缩（罕见） |

### 机器验证方式

- [Compaction span Rego 规则](../../weaver/policies/aivision/traces/compaction/)检查 span 类型，并提示缺失的 `agentic.compaction.*` 属性。
- 已纳入 M3 阶段

### 关联旧条款

- C8（Compaction 语义）

### 各 agent 评估关注点

- compaction 生命周期线索来自哪里（transcript boundary / runtime hook / token 回填）
- 前后 token 是原生值、回填值还是推导值
- 是否存在"only-marker 场景"（仅知道发生过，无法形成完整 span）

### cchooks 落地

- **代码**：[OTel 构造](../../claudecode/cchooks/src/common/otel.rs)中的 `build_compaction_span` 使用 [turn 构造模块](../../claudecode/cchooks/src/common/transcript/turns.rs)识别的压缩边界。
- **Replay 例子**：`~/.aivision/cc/replays/spans_session_compact_44ac85e7.jsonl` — 独立 compaction span（TOOL/execute_tool, tool.name=compaction）携带 `agentic.compaction.{trigger,tokens.before,tokens.after,tokens.saved,sequence,ratio}`
- **关键设计决策**：compaction 作为 turn root 的 sibling，不静默吞并进相邻 LLM span

---

## D2 同步子 agent 调用识别

### 定义

父 Agent 同步等待子 Agent 完成的**委派动作**可被识别为独立 span 节点。

它表达"委派发生了"，而不是子 Agent 内部执行细节的全部展开。

### 必要属性 / Span 字段

首选表达：独立的 agent invocation span：

- `gen_ai.operation.name = invoke_agent`
- `agentic.tool.type = subagent`
- 可选 subagent 标识属性（agent_id / agent_type / agent_name）

挂载在父 turn / root span 之下，能定位委派归属。

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | 委派动作有独立 span，挂在父 turn 下；携带 `agentic.tool.type=subagent` 与 subagent 身份；同步等待边界（开始-结束）准确 |
| 🟡 部分 | 委派被记录为普通 TOOL span，未标 subagent 类型；或仅有委派事实无独立 span；或缺 agent 身份 |
| ❌ 未实现 | hooks/transcript 中可见 subagent 生命周期（如 Claude Code Task tool），实现未识别为 subagent |
| ⚪ 不适用 | Agent 不支持子 Agent 能力 |

### 机器验证方式

- weaver-guard 校验存在 `agentic.tool.type=subagent` 的 span
- 当前 Rego 状态：**待补**

### 关联旧条款

- C6（Subagent 委派语义）

### cchooks 落地

- **代码**：`claudecode/cchooks/src/hooks/subagent/subagent_handler.rs:629`
- **Replay 例子**：`~/.aivision/cc/replays/spans_subagentx.jsonl` — Task 工具识别为 `agentic.tool.type=subagent` + `mode=sync` + subagent.id/type/description
- **关键设计决策**：Claude Code Task 工具显式识别，扩展子 transcript 的 generation/tool 子 span

---

## D3 异步子 agent 调用识别

### 定义

异步触发的子 Agent（fire-and-forget / 后台执行 / 跨 trace）能被识别，且父子链路存在某种稳定关联（同 trace、span link、属性映射或外部线索）。

### 必要属性 / Span 字段

允许的关联强度从强到弱：

1. 共享 trace 上下文或明确父子 span 关系
2. 显式 span link
3. 已注册的关联属性（如 subagent transcript path）
4. 外部可定位的 transcript / session id

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | 异步子 agent 有独立 trace，且与父 trace 通过 span link 或共享 trace_id 强关联 |
| 🟡 部分 | 仅有弱关联（如属性中含 subagent transcript path），或仅能拿到委派事实但无法定位子链路 |
| ❌ 未实现 | hooks/transcript 中可见异步 subagent 生命周期，实现未做关联 |
| ⚪ 不适用 | **异步子 agent 在当前 transcript 数据源中无法跨 trace 关联**，或 Agent 本身不支持异步 subagent |

### 数据源缺失根因（⚪ 普遍原因）

当前纳入矩阵的 agent 的异步子 agent 链路普遍缺乏稳定跨 trace 关联：

- **claudecode**：`Task` 工具调用在 transcript 中以 `tool_use` 形式记录，但子 agent 的执行不写入主 transcript，子 trace 单独由子 hook 链路产生，二者间**没有共享 trace_id 或 span_id**
- **codex**：CLI 透传子任务时同样缺乏跨进程 trace 关联
- **openclaw / opencode**：尚未有显式 subagent 异步触发机制（部分能力可能不适用）

旧 Constitution C7 已标记此条为"放弃"，本规范保留作为评估项以诚实反映 gap。

### 机器验证方式

- 暂无 Rego（异步关联依赖跨 trace 检索，超出 weaver-guard 单 trace 校验范围）
- 当前 Rego 状态：**待补**

### 关联旧条款

- C7（子 Agent Trace 关联语义，旧版标记"放弃"）

### cchooks 落地

- **代码**：`claudecode/cchooks/src/hooks/subagent/subagent_handler.rs` — 异步 subagent 独立 trace 上传
- **Replay 例子**：`~/.aivision/cc/replays/spans_subagentx.jsonl` — 父子两端共享 `agentic.subagent.id` 形成属性级弱关联
- **关键设计决策**：当前 🟡 — 仅属性级弱关联，无 span_link / 共享 trace_id（数据源限制：子 agent 走独立 hook 链路）

---

## D4 Cancel / Retry 错误恢复识别

### 定义

API 错误重试、用户主动取消、超时中断等异常控制流可被识别：

- 每次真实 retry 对应独立 span 或事件，**不被压平为单条最终状态**
- cancel / interrupt 可被显式表达为 `cancelled` / `interrupted` 等语义，而非记作普通成功
- 用户主动取消的 turn 即使缺当下 stop hook，也能被延迟补齐（不静默丢失）

### 必要属性 / Span 字段

错误最低闭环（与 B2 / C2 一致）：

- `status.code = ERROR`
- `error.type`

retry 场景：

- 每次真实 retry 对应一个独立 span（推荐 `CLIENT` kind 表达 api_error）
- 推荐 `agentic.error.retry_attempt` 序号
- 推荐 `agentic.error.retry_delay_ms`

cancel / timeout 场景：

- `error.type` 取值细化：`cancelled` / `interrupted` / `timeout` / `rate_limit_exceeded`

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | 多次 retry 对应多个独立 span；cancel/timeout/interrupt 通过 error.type 区分；用户主动取消的 turn 能被延迟补齐且不重复 |
| 🟡 部分 | 仅记录最终状态（多次 retry 被压平）；或 cancel 被记作成功；或重复补齐导致同一 turn 被上报多次 |
| ❌ 未实现 | hooks/transcript 中可见 retry/cancel/timeout 标识，实现未上报 |
| ⚪ 不适用 | （D4 不适用 ⚪——所有运行中的 agent 都有可能遇到错误） |

### 机器验证方式

- 暂无独立 Rego（依赖 B2 / C2 的错误状态校验已可覆盖大部分场景）
- 当前 Rego 状态：**待补**

### 关联旧条款

- C14（Cancel / Interrupt / Timeout / Retry 语义）

### 细则：用户主动取消的延迟补齐

当用户主动取消当前轮、且当前运行时未立即触发终止 hook 时：

- 允许延迟补齐该轮 trace，但不得静默丢失
- 补齐优先级：当前轮终止 hook → 会话结束或失败事件 → 下一次可用 hook 的 transcript reconciliation
- 补齐必须复用原始 turn 的时间边界与原始 transcript 数据，不得用补齐发生时刻冒充真实结束时间
- 补齐结果必须可去重（不重复上报同一 turn）

### 细则：错误冒泡

错误优先挂在真实失败的叶子 span 上（B2 / C2 已规定）。父 span **仅在自身也因此失败时**再汇总错误，不为了"显示更明显"而复制多层错误状态。

### cchooks 落地

- **代码**：`claudecode/cchooks/src/common/otel.rs` 的 `build_api_error_spans`
- **Replay 例子**：`~/.aivision/cc/replays/spans_api_error.jsonl`（API 重试）+ `~/.aivision/cc/replays/spans_S09_user_cancel_and_reconcile_session_0.jsonl`（用户取消）
- **关键设计决策**：多次 retry 各生成独立 `chat api_error` CLIENT span（attempt/delay/status_code/message + status=ERROR）✅；用户取消当前 🟡 — 仅 trace name 后缀 `(Cancelled)`，未打 `error.type=cancelled` 待补
