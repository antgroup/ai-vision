# 工程原则与治理

本文件是 `aivision-agents` 项目的**工程原则与治理流程**，是**顶层三件套**之一：

| 文档 | 回答的问题 |
|------|-----------|
| [CAPABILITIES.md](CAPABILITIES.md) | "要捕获什么、谁捕获了" —— 22 项能力 + 评分卡 |
| **本文件 (CONSTITUTION.md)** | "怎么决定捕获什么、为什么这样决定" —— 9 条原则 + 治理 |
| [METHODOLOGY.md](METHODOLOGY.md) | "怎么做" —— 5 工作流 / 数据模型 / 接入指南 |

读者建议先看 CAPABILITIES.md 了解项目要做什么，再看 METHODOLOGY.md 知道怎么做，本文件最后回看决策原则。

## 1. 制定原则

下列 9 条原则约束 **能力如何被加入或拒绝**、**实现如何取舍**、**评估如何下结论**。

### 1.1 数据来源优先于理想语义

一条规则是否成立，首先取决于数据是否有稳定来源，而不是该语义"看起来合理"。"想要"不构成纳入约束的理由。

> **cchooks 实例**：C4 系统提示词在 Claude Code transcript 中不暴露，cchooks 直接评 ⚪ 不伪造（详见 `claudecode/CAPABILITIES.md#c4`）。

### 1.2 原生可得优先于推断构造

可直接从 transcript、runtime hook、原始事件、模型 usage 或工具返回中获取的数据，优先纳入规则。依赖多事件拼接、弱推断或经验补全的语义，只能降级为推荐项或可选项。

> **cchooks 实例**：[transcript 字段提取](claudecode/cchooks/src/common/transcript/fields.rs)中的 `get_usage` 直接读取原生 usage。

### 1.3 低侵入优先于架构改造

若某条规则需要显著破坏现有 hooks 架构、引入双实现、或要求 replay 另写一套逻辑，则该规则默认不进入硬约束。先修方法论，不轻易改地基。

> **cchooks 实例**：cchooks 复用 Claude Code 自带 transcript `.jsonl`，不重写也不合成；hook 仅做 `PostToolUse` / `Stop` / `SessionEnd` 三个最小生命周期接入，不绑定 Claude Code 内部状态机。

### 1.4 跨 Agent 一致优先于局部特例

规范优先定义跨 Agent 可共享的最小语义合同。Agent 特有能力可以扩展，但不得反向绑架通用规则。

> **cchooks 实例**：MCP 工具识别走 `gen_ai.tool.name` + `agentic.tool.type=mcp` 跨 agent 通用规则（`claudecode/cchooks/src/common/otel.rs:517` 的 `append_agentic_tool_attrs_typed`），不引入 cchooks 专属命名空间。

### 1.5 缺失可接受，伪造不可接受

若某字段在原始数据中确实不可得，可以缺失，也可以显式标记为不可用。**不得**为了满足规则而估算、伪造或硬编码看似合理的数据。"缺失 → ⚪ 不适用"是合法状态。

> **cchooks 实例**：tool 调用结果若 transcript 仅 fallback path 拿到，cchooks 写 `agentic.tool.input.missing=true`（`otel.rs:662-663`），**不**写空字符串冒充。

### 1.6 Replay 必须复用主流程逻辑

对于支持 replay 的 Agent，replay 必须复用主流程中的 span 组织与语义映射逻辑。replay 不得为了"补齐语义"而单独发明另一套规则。

> **cchooks 实例**：`cchooks-replay` 二进制（`claudecode/cchooks/src/bin/cchooks-replay.rs:598`）直接调主流程的 `build_otel_export_request_for_turn`（导入位 `:16`），与实时 hook 共享同一 span 构造。

### 1.7 规范条款必须可验证

任何进入规范的条款，最终都应能通过以下至少一种方式被验证：

- weaver 策略验证（Rego）
- golden transcript / replay 样例验证
- Agent 级评估文档（`<agent>/CAPABILITIES.md`）的代码 file:line + replay 双证据

> **cchooks 实例**：cchooks 已评估的 21 项能力均带代码 file:line + replay/source 双证据；E4 本轮只登记标准（参见 `claudecode/CAPABILITIES.md`），可被 weaver-guard 与人工审核回溯。

### 1.8 起止时间优先使用原始来源，不强行补齐

span 的开始时间与结束时间，优先使用数据源中可直接获得的原始时间。若原始开始时间、结束时间或其中之一不可得，**不应**为了"看起来完整"而强行从前后相邻流程、兄弟节点或后续恢复事件中推断精确边界。允许使用安全退化时间，但必须在评估文档中明确说明时间来源等级。

> **cchooks 实例**：tool span 用 `assistant_ts → result_ts` 真实时间（`otel.rs:421-428`），缺失时退化到 `now_ns()`，**不**从前后流程推断精确边界。

### 1.9 顺序必须符合事件实际发生顺序

同一父节点下的 sibling spans，以及 `gen_ai.input.messages` / `gen_ai.output.messages` 中的消息数组，都应保持与事件真实发生顺序一致。**不得**为适配展示层或下游系统主动颠倒顺序。

> **新组件约束**：turn root 保留用户入口视角；generation span 的 `gen_ai.input.messages` 按真实模型调用记录增量，首次为当前 user，后续为相邻调用新增的 assistant/tool 消息，不为展示层颠倒顺序，也不提供累加模式开关。

## 2. 明确不纳入硬约束的边界

为防止规范无限扩张和实现过度采集，下列项**显式不要求**：

### 2.1 内容采集边界
- 不要求 root span 承载完整的每一步 reasoning 内容
- 不要求 root span 复制所有子 span 的明细属性
- 不要求 turn root 与每个 LLM span 上重复复制同一份 system / messages

### 2.2 Span 拓扑边界
- 不要求为每个流式 chunk、message part 或 token 回调创建独立 span
- 不要求为错误场景额外创建独立"错误 span"（错误挂在自身 span 即可）
- 不要求为工具"决策阶段"单独创建 span
- 不要求把 cancel / timeout / interrupt 拆成独立 span 类型
- 不要求所有 MCP 调用都额外生成独立 `MCP` 专用 span（仍是 TOOL span 的特化）

### 2.3 内容补齐边界
- 不允许通过 tokenizer 估算补齐 token usage
- 不允许在原始 usage 缺失时使用本地 tokenizer 估算
- 不允许在工具名 / system prompt / cancel 类型不可得时凭推测填充
- 不允许在没有稳定 ID 时伪造看似真实的上游 ID

### 2.4 跨 trace 边界
- 不要求所有子 Agent trace 必须与父 trace 使用同一 trace id
- 不要求在无法稳定建立链接时强行构造 parent span id
- 不允许使用未注册的自定义属性长期承载关键关联语义

## 3. 治理与修订流程

### 3.1 能力分级

| 级别 | 进入条件 |
|------|---------|
| **必须项**（✅ 是合规） | 数据来源稳定、语义边界清晰、至少 1 个 agent 完成证明、不破坏现有架构、可被 weaver 或 golden 验证 |
| **推荐项**（🟡 提示） | 数据来源存在但非全 agent 稳定可得、需中等推导、短期不适合升硬约束 |
| **可选项** | 仅在特定 agent / 部署模式 / 调试场景下成立 |
| **明确放弃**（⚪ 不适用） | 需估算/伪造、需大重构、仅靠脆弱推断、无法形成可执行验证 |

### 3.2 新增能力的最低条件

新增能力进入 `CAPABILITIES.md` 时，必须同步：

1. `docs/capabilities/` 中对应分组文件补能力章节（含必要属性、四态判定、机器验证）；E1/E2/E3/E4 使用 `docs/capabilities/E-repository-context.md`、`docs/capabilities/E-upstream-trace-context.md`、`docs/capabilities/E-meta-trigger.md`、`docs/capabilities/E-session-privacy.md` 与相关 semconv 注册
2. 至少 1 个 agent 在其 `<agent>/CAPABILITIES.md` 中给出评分（含双证据）
3. 若引入新扩展属性，须先完成 `docs/semconv/` 注册，再考虑纳入 Rego 硬约束
4. 顶层 `CAPABILITIES.md` 的 N×M 矩阵同步更新（当前为 7×22）

### 3.3 升级为机器验证的条件

一条能力从"待补 Rego"升级为"可机器验证"前，必须：

- 已完成至少一轮 agent 评估
- 已确认不会逼迫其他 agent 伪造数据
- 对应 golden replay 样本可复现
- 若涉及新属性，semconv / weaver 对齐已完成
- 新 Rego 在当前纳入矩阵的采集插件已有 replay 上 dry-run **不引入新 violations**（与 baseline 对比）。若引入，**必须放宽 Rego 而非修 replay**。

### 3.4 评估文档的硬约束

Agent 评估（`<agent>/CAPABILITIES.md`）的职责是：

- 说明能力能否实现
- 说明数据从何而来
- 说明获取边界与成本
- 给出 ✅ / 🟡 / ❌ / ⚪ 评分

评估文档**不得**：

- 改写顶层能力定义
- 用局部现实覆盖跨 agent 的总约束
- 在证据不足时把重构设想写成既成事实
- 把"希望如此"写成"当前已支持"
- 只给结论不给证据

### 3.5 评分证据门槛

| 评分 | 必须提供 |
|------|---------|
| ✅ | 代码 file:line 引证 + replay 例子（指向具体 trace/span） |
| 🟡 | 缺什么 + 为什么 + 哪些场景不覆盖 |
| ❌ | 数据源在哪里 + 为什么没采集 + 修复路径 |
| ⚪ | 数据源缺失根因（hook 时机 / transcript 字段缺失 / SDK 不暴露 等） |

### 3.6 Golden 与 baseline 维护

- 不得因为当前实现未满足规范而直接覆盖 baseline 掩盖问题
- 每次更新 baseline，应说明是能力提升、规则调整、还是样例修正
- baseline 变更应能回溯到具体能力 ID 和具体 agent

### 3.7 合规等级 L0-L3（agent 成熟度阶梯）

合规等级是**纵向**的成熟度阶梯，与 CAPABILITIES.md 的**横向**四态评分（✅🟡❌⚪）正交：

- **四态评分** 回答 "某项能力做到了什么程度"（per capability）
- **合规等级** 回答 "整个 agent 实现达到了什么成熟度"（per agent，跨能力）

#### L0 — 基础存在性

> 最低可接受标准：有 span 产出，基本结构正确

| # | 检查项 | 通过条件 |
|---|--------|---------|
| L0-1 | Span 类型区分 | 所有 span 都有 `gen_ai.span.kind`，且值在 `CHAT/AGENT/LLM/TOOL/CLIENT` 枚举内 |
| L0-2 | Span 层级 | A1 拓扑正确：CHAT 为根，LLM/TOOL 为 sibling，不嵌套 |
| L0-3 | Span 命名 | 遵循 METHODOLOGY §2.8 命名规范 |
| L0-4 | Resource 必填 4 项 | `ant.agent.env` / `ant.agent.scene` / `ant.username` / `ant.agent.type` 全部非空（强制约束闭集，与 METHODOLOGY §2.2 一致） |
| L0-5 | OTel Resource 标准 | `service.name` / `service.version` 存在（OTel 标准属性） |
| L0-6 | Runtime 标识 | `agentic.runtime.name` 在已注册枚举内（claudecode / codex / openclaw / opencode / kimi / 等） |
| L0-7 | LLM 有 model | 所有 LLM span 携带 `gen_ai.request.model` |
| L0-8 | Tool 有 name | 所有 TOOL span 携带 `gen_ai.tool.name` |

#### L1 — 字段完整性

> 所有必需字段到位，数据可用于基础分析

| # | 检查项 | 通过条件 |
|---|--------|---------|
| L1-1 | LLM token 完整 | LLM span 携带 `gen_ai.usage.input_tokens` 与 `output_tokens`（C7 ✅） |
| L1-2 | LLM 消息记录 | LLM span 携带 `gen_ai.input.messages` 与 `output.messages`（C3/C5 ✅） |
| L1-3 | Tool 参数记录 | TOOL span 携带 `gen_ai.tool.call.arguments` 与 `result`（B3/B4 ✅） |
| L1-4 | 耗时属性 | LLM 有 `agentic.llm.duration_ms`，TOOL 有 `agentic.tool.duration_ms`（B1/C1 ✅） |
| L1-5 | 消息格式合规 | OpenAI Chat Completions 格式（METHODOLOGY §2.7） |
| L1-6 | 错误双标记 | 错误 span 同时有 `status.code=ERROR` + `error.type`（B2/C2 ✅） |
| L1-7 | Session 上下文 | CHAT span 携带 `agentic.turn.number` 与 conversation 关联（A1 ✅） |

#### L2 — 时序与准确性

> 数据可信赖，可用于性能分析与成本核算

| # | 检查项 | 通过条件 |
|---|--------|---------|
| L2-1 | 时间戳准确 | startTime/endTime 来自原始事件（METHODOLOGY §2.3） |
| L2-2 | 耗时一致 | `duration_ms` 与 span 时间范围一致（±10ms） |
| L2-3 | 顺序正确 | 同级 span 时间顺序与实际调用顺序一致（CONSTITUTION 1.9） |
| L2-4 | 无时间越界 | 子 span 时间范围不超出父 span |
| L2-5 | Token 100% 准确 | 与 LLM API 返回值完全一致，非估算（CONSTITUTION 1.5） |
| L2-6 | Cache token | 有缓存时 cache_read / cache_creation 正确记录（C7 L2 层） |
| L2-7 | 无时间重叠 | 同级非并发 span 时间范围不重叠 |

#### L3 — 全量合规（Golden 可选）

> 生产级质量，支持自动化回归

| # | 检查项 | 通过条件 |
|---|--------|---------|
| L3-1 | Weaver 零 violation | `make validate` 输出 0 个 violation（baseline 内） |
| L3-2 | Replay 幂等 | 同一 transcript 多次 replay 产生语义一致 OTLP（CONSTITUTION 1.6） |
| L3-3 ⚪ | Golden baseline（可选） | 维护 `~/.aivision/<component>/golden/`，本机自动比对（METHODOLOGY §1.6）；无 golden 目录时跳过 |
| L3-4 | Event 完整 | event 通过 `event_common.rego` 验证 |
| L3-5 | Event-Span 一致 | event 与对应 span 的数据不矛盾 |
| L3-6 | 推荐属性覆盖 | `gen_ai.response.model` / `finish_reasons` 等推荐属性填充 |
| L3-7 | Thinking 记录 | LLM thinking/reasoning 完整记录（C6 ✅） |
| L3-8 | 多模态支持 | OpenAI content parts 格式 |
| L3-9 | 跨 Agent 等价 | 等价操作在所有 agent 间结构一致（CONSTITUTION 1.4） |
| L3-10 | 内容完整性 | 无静默截断，截断有显式标记（METHODOLOGY §2.7） |
| L3-11 | 并发工具标记 | 并发有 `parallel_group` 标记 |
| L3-12 | Transcript 覆盖 | T1-T10 全覆盖（METHODOLOGY §1.6） |
| L3-13 ⚪ | Golden 回归（可选） | 所有 golden transcript replay 与 baseline 一致；仅在 L3-3 已建立时适用 |

#### 评估流程

1. 在 agent-plugins 目录运行 `make validate <agent> FORMAT=json`，生成检查数据。
2. 运行 `make doctor <agent>`，检查插件骨架、Resource 和端到端流程。
3. 人工抽检 L2 时序准确性 + L3 跨 agent 等价

#### 当前 agent 等级

> 由各 agent 在自己的 `<agent>/CAPABILITIES.md` 末尾自评，AGENTS.md 不再汇总（参见 AGENTS.md "Agent 评分卡"）。

## 4. 可行性评估方法论（写 `<agent>/CAPABILITIES.md` 时遵守）

### 4.1 拆到数据项粒度

不应只按"能力结论"泛泛描述，而应拆到**数据项**粒度举证。

例：评估 C7 LLM Token usage 不应只写"支持 Token usage"，而应分别回答：

- 单次 input_tokens 来源
- 单次 output_tokens 来源
- 累计 vs 差分 usage 处理
- cache token 是否可得
- turn root 是否重复上报

### 4.2 证据必须可定位

证据必须可回溯到以下任一对象：

- 源代码文件 + 具体行号（`<file>:<line>`）
- transcript 样例
- replay JSONL 样例（`~/.aivision/<component>/replays/<file>.jsonl`，可指到 trace_id / span_name）
- 生命周期文档

不允许只写"经测试可得"、"理论上可得"这类不可回溯表述。

### 4.3 负证据必须写清搜索边界

若结论是 ❌ 或 ⚪，至少应写清：

- 搜索了哪些 hook、transcript 字段或 parser 路径
- 当前缺失是**结构性**缺失（数据源不存在）还是**实现性**缺失（数据源存在但实现未采集）
- 若需补能力，代价位于哪里

### 4.4 顺序、时间、消息构造必须单独检查

下列三类问题不得被总述带过：

- sibling spans 顺序是否符合实际事件顺序
- messages 数组顺序是否符合实际场景
- span 起止时间是否来自原始来源还是后处理回填

若为适配展示层主动调整顺序、为兼容下游产品颠倒消息顺序、或从前后流程推导精确时间，**必须**在对应数据项中显式写出。

### 4.5 推导数据默认不进 ✅

- 仅依赖稳定推导的数据，最多 🟡
- 仅依赖弱推导的数据，默认不进 ✅，倾向 🟡 或 ❌

## 5. 与 CAPABILITIES.md 的职责边界

| 问题 | 归属 |
|------|------|
| 一项能力是什么、必要属性有哪些 | `docs/capabilities/` |
| 各 agent 各项能力是否实现 | `<agent>/CAPABILITIES.md` + `<agent>/capabilities/<ID>.md` + 顶层 N×22 矩阵 |
| Rego 策略 | `weaver/policies/` |
| **为什么 X 不能伪造、Y 不能拆 span** | 本文件第 1-2 节 |
| **新增能力流程、评分证据门槛** | 本文件第 3 节 |
| **如何写评估文档** | 本文件第 4 节 |

本文件**不**应：

- 列出具体能力（C1, C2... 已废弃；新使用 A1 / B1-B6 / C1-C7 / D1-D4 / E1-E4，列在 CAPABILITIES.md）
- 描述具体属性 / 字段（在 docs/capabilities/）
- 给具体 agent 评分（在 `<agent>/CAPABILITIES.md`）
