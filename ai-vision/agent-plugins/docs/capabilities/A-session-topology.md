# A 组：会话与拓扑

← 返回 [CAPABILITIES.md](../../CAPABILITIES.md)

本组只包含 1 项能力，是所有其他能力的拓扑基础：**会话能否被识别为多轮 chain，而不是被压平成扁平工具调用列表**。

## A1 多轮会话识别 + chain 拓扑

### 定义

A1 表达的是：一次用户驱动的 Agent 交互能否被识别为唯一的 root span，以及该 root span 之下的 LLM / Tool / Compaction 操作是否形成 **`turn → chat → tool → chat → tool → chat`** 的链式 sibling 拓扑。

它不只是"有 turn span"——更要求 turn 之下能展开出真实的子 span 链路，反映 agent 的多步推理与工具调用过程。

### 必要属性 / Span 字段

Root span（必须满足）：

- `gen_ai.span.kind = CHAT`
- `gen_ai.operation.name = invoke_agent`
- 唯一 root span（每轮交互一个）
- 携带会话关联（`session.id`）与用户身份（`user.name`）
- 若数据源可得，携带真实起止时间

Root span 之下（拓扑要求）：

- LLM / Tool / Compaction span 必须**作为 root 的直接子节点**（sibling 关系），而**不是相互嵌套**
- 同一父节点下子 span 顺序与事件真实发生顺序一致
- 多次 LLM 调用产生多个独立 generation span，**不被合并**

```text
Turn / Root Span (CHAT, invoke_agent)
├── Generation Span (LLM, chat)           # 第 1 次模型调用
├── Tool Span (TOOL, execute_tool)        # sibling，不挂在 generation 下
├── Tool Span (TOOL, execute_tool)        # sibling
├── Compaction Span (TOOL, execute_tool)  # sibling
└── Generation Span (LLM, chat)           # 第 2 次模型调用
```

### 四态判定标准

| 状态 | 判定细则 |
|------|----------|
| ✅ 完全 | root span 存在且唯一；其下 generation/tool/compaction 均为 sibling；多次 LLM 调用形成独立 generation span 链；replay 中能直接看到 chain 拓扑 |
| 🟡 部分 | 满足以下任一情况：(a) 有 root 但子 span 嵌套关系错误（如 tool 挂在 generation 下）；(b) 多次 LLM 调用被合并成单个 generation；(c) 仅有 turn 层而下方全是工具调用，缺 chain 拓扑 |
| ❌ 未实现 | hooks/transcript 中存在 turn / message / tool 生命周期事件，但实现没有构建 root span 或没有保留拓扑 |
| ⚪ 不适用 | （A1 不适用 ⚪ —— 任意 agent 都至少有 turn 边界数据） |

### 机器验证方式

- weaver-guard 校验 root span 存在且 `gen_ai.span.kind=CHAT`
- weaver-guard 校验同一 trace 中 generation 与 tool 不嵌套（待补 Rego）
- golden replay 比对：对相同 transcript，replay 输出的 trace 拓扑应稳定一致

> 当前 Rego 状态：**待补**。机器验证目前仅覆盖根 span 类型，sibling 拓扑校验需在 M3 后续阶段补充。

### 关联旧条款

- C1（Turn / Root Span 组织规则）

### 适用范围

本能力适用于当前纳入矩阵的采集插件。Root span 边界的具体来源因 agent 数据源（Hook payload + 可选 transcript 持久化，参见 METHODOLOGY §3.1）而不同：

- **双源 agent**（claudecode / codex）：hook payload 提供 turn 启动事件 + transcript 提供用户消息 / assistant 消息 / tool 结果用作 replay 重建
- **纯 hook payload agent**（openclaw / opencode）：session / message / tool 生命周期事件直接构造 root span，无持久化 transcript

### 各 agent 评估关注点

每个 agent 的 `<agent>/CAPABILITIES.md` 在评估 A1 时必须回答：

- turn 边界来自哪里
- root span 的开始时间与结束时间如何获得
- 一轮内多次 LLM 调用是否仍只保留一个 root span
- tool / compaction / subagent 是否挂载到 root span 下作为 sibling
- replay 中能否看到 `turn → chat → tool → chat → tool → chat` 的实际链路

### 当前实现取向

```text
Turn / Root Span (CHAT, invoke_agent)
├── Generation Span (LLM, chat)
├── Tool Span (TOOL, execute_tool)
├── Tool Span (TOOL, execute_tool)
├── Compaction Span (TOOL, execute_tool)
├── API Error Span (CLIENT)              # retry 错误也是 sibling
└── Generation Span (LLM, chat)
```

如存在子 Agent 调用，应在该轮内先表达"委派动作"（D2 / D3），再视能力决定是否建立独立子 trace 或显式关联。

### cchooks 落地

- **代码**：`claudecode/cchooks/src/common/otel.rs:1187-1322` — `collect_turn_spans` 把 generation/tool 的 parent_span_id 直接指向 root_span_id（不嵌套）
- **Replay 例子**：`~/.aivision/cc/replays/spans_S05_chat_tool_chat_chain_session_0.jsonl` — Turn 1（CHAT）→ chat GLM-5.1（LLM）+ tool Read（TOOL）同 parent_span_id
- **关键设计决策**：每 turn 独立 trace_id，sibling 拓扑稳定无嵌套；多 turn 时 `build_otel_export_request` 为每 turn 独立调用 `collect_turn_spans`
- **Claude Code 逻辑根标记**：Turn 根额外写 `_internal.meta.root="1"`，供下游按 Turn 查询；有上游 `parentSpanId` 时仍标记。该字段只属于 Turn 构造角色，不下发到 `LLM + chat` generation 或其他子 span；共享生产者范围为 Claude Code、Codex、OpenCode、OpenClaw 与 Pi。

### cdxhooks 落地

- **代码**：`codex/cdxhooks/src/common/otel.rs` 的 `build_root_span` 只在逻辑 Turn 根写 `_internal.meta.root="1"`；`build_otel_export_request_for_turn` 同时服务实时 Stop/finalizer 与 replay 的 Turn 构造。
- **Replay 例子**：`codex/cdxhooks/tests/fixtures/codex_multi_agent_v2_parent.jsonl` 及配套 child fixture 可重建 Turn → LLM / TOOL / AGENT sibling 拓扑，并用于验证 Turn 与合成 AGENT 的标记边界。
- **关键设计决策**：marker 表达当前 agent 的逻辑 Turn 角色，不表达物理 trace root；因此上游 `parentSpanId` 不抑制 marker。LLM `chat`、TOOL、合成 AGENT、INTERNAL、COMPACTION 与独立 SUBAGENT span 均不继承 marker。

### observ-opencode 落地

- **代码**：`opencode/observ-opencode/src/hooks/index.ts` 只在非 subagent 的 `startNewTurn` 分支写 `_internal.meta.root="1"`；复用的 SyncAgent AGENT span 不标记。
- **配置边界**：`opencode/observ-opencode/src/config.ts` 在全部 signal attributes 合并后删除精确保留键；`_internal.sofa.*` 等其他内部键保留。
- **Replay 证据**：`opencode/opencode-replay/scripts/test-local-report.ts` 验证 file/HTTP replay 对单个 owned Turn 都仅产生一个 marker，且子 span 不继承。

### observ-openclaw 落地

- **代码**：`openclaw/observ-openclaw/src/hooks/session.ts` 仅在 owned `requestSpan` 构造时写 `_internal.meta.root="1"`。
- **排除项**：child session 借用的 SyncAgent AGENT span 与 `openclaw/observ-openclaw/src/hooks/compaction.ts` 创建的 `Turn (compaction)` synthetic CHAT span 均不标记。
- **Replay 证据**：`openclaw/openclaw-replay/scripts/test-local-report.ts` 在带 diagnostic upstream parent 的 fixture 中验证单个逻辑根 marker。

### observ-pi 落地

- **代码**：`pi/observ-pi/src/semantic.ts` 在 `before_agent_start` 的 CHAT observation 上写 `_internal.meta.root="1"`，并由实时与 `pireplay` 共用的 SemanticEngine 消费。
- **配置边界**：`pi/observ-pi/src/config.ts` 精确过滤同名 signal attribute，不过滤其他 `_internal.*` 键。
- **验证证据**：`pi/observ-pi/test/telemetry-lifecycle.test.ts` 证明 upstream OTel parent 与逻辑 marker 可同时存在；`pi/pireplay/test/transcript.test.ts` 验证每个 replay owned Turn 仅有一个 marker。
