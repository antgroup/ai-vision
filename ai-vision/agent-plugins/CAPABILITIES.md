# AI Agent 可观测能力规范（Capability Spec）

本文件是 `aivision-agents` 项目的**可观测能力**总纲与评分卡。

> 单项能力证据已拆分到各插件目录：`claudecode/capabilities/`、`codex/capabilities/`、`kimi/capabilities/`、`hermes/capabilities/`、`opencode/capabilities/`、`pi/capabilities/`、`openclaw/capabilities/`。当前目录按 `A1.md`、`B1.md` … `D4.md`、`E1.md`、`E2.md`、`E3.md` 保存已评估能力证据；E4 本轮只登记标准，暂不生成 agent 评分文件。

它回答的问题是：**"这个 agent 实现到底捕获了哪些观测信号？"** —— 用 22 项能力 + 四态评分横向对比每个 agent 的可观测落地完成度。

> 本文件是项目**顶层三件套**之一：
>
> - **CAPABILITIES.md（本文件）**：要捕获什么、谁捕获了 —— 22 项能力 + 评分卡
> - **[CONSTITUTION.md](CONSTITUTION.md)**：怎么决定捕获什么、为什么这样决定 —— 9 条原则 + 治理
> - **[METHODOLOGY.md](METHODOLOGY.md)**：怎么做 —— 6 工作流 / 数据模型 / 接入指南

## 阅读顺序

| 文档 | 内容 |
|------|------|
| [docs/capabilities/A-session-topology.md](docs/capabilities/A-session-topology.md) | A 组：会话与拓扑（A1） |
| [docs/capabilities/B-tool-semantics.md](docs/capabilities/B-tool-semantics.md) | B 组：工具调用语义（B1-B6） |
| [docs/capabilities/C-llm-semantics.md](docs/capabilities/C-llm-semantics.md) | C 组：LLM 调用语义（C1-C7） |
| [docs/capabilities/D-advanced-semantics.md](docs/capabilities/D-advanced-semantics.md) | D 组：高阶语义（D1-D4） |
| [docs/capabilities/E-repository-context.md](docs/capabilities/E-repository-context.md) | E1：Git 仓库信息识别 |
| [docs/capabilities/E-upstream-trace-context.md](docs/capabilities/E-upstream-trace-context.md) | E2：上游调用 TRACEPARENT 串联 |
| [docs/capabilities/E-meta-trigger.md](docs/capabilities/E-meta-trigger.md) | E3：非真实用户 / 外部触发归因识别（AIVISION_META_TRIGGER / meta.trigger） |
| [docs/capabilities/E-session-privacy.md](docs/capabilities/E-session-privacy.md) | E4：SESSION 级别隐私模式设置（AIVISION_COLLECTION_MODE） |

## 四态判定标准

每项能力按下表四态判定，**所有 agent 评估必须严格遵循**：

| 状态 | 判定 |
|------|------|
| ✅ **完全** | 实现已采集该能力的全部必要属性，replay 中能直接看到对应字段且语义正确 |
| 🟡 **部分** | 实现已采集，但存在以下任一情况：缺失部分必要属性、拓扑不对、字段语义偏差、覆盖场景不全 |
| ❌ **未实现** | 数据源**可以**拿到（hooks / transcript / SDK 中存在原始数据），但当前实现**没有采集**或没有上报 |
| ⚪ **不适用** | 数据源**根本拿不到**该能力对应的原始数据，无论实现怎么改都无法支持 |

### ❌ vs ⚪ 关键区分

- **❌ "应该做但没做"** —— 工程缺口，可补。例：transcript 里有 `tool_result` 字段，实现却没把它写到 span，应评 ❌。
- **⚪ "想做也做不了"** —— 数据源缺口，不可补。例：transcript 中根本不存在 system prompt 字段，无论实现怎么改都拿不到，应评 ⚪。

每项 ❌ 必须在 agent 评估报告中说明"数据源在哪里、为什么没采集"。
每项 ⚪ 必须在能力规范中说明"数据源缺失的根因（hook 时机 / transcript 字段缺失 / SDK 不暴露 等）"。

> **参考实现**：能力规范共 22 项，当前 agent 评分覆盖其中 21 项；`claudecode/cchooks` 在已评估维度中覆盖最完整（15 ✅ / 4 🟡 / 1 ❌ / 1 ⚪）。E4 本轮只登记 SESSION 隐私模式标准，不计入当前 agent 评分；其余部分能力已有实现但仍缺真实 replay 正向样本。E1 的 OpenClaw/Hermes ⚪ 是常驻程序边界下的“不要求实现”，E2 的其他 ⚪ 是当前没有 W3C 上游 context source，均不是把缺失数据伪造成成功。详细评分见 [claudecode/CAPABILITIES.md](claudecode/CAPABILITIES.md)，每项能力的落地点位见 `docs/capabilities/` 各能力章节末尾。
>
> ⚠️ **L0 等级 disclaimer**：cchooks 在 [CONSTITUTION §3.7](CONSTITUTION.md) 定义的 L0 基础存在性检查中，**Resource 必填 4 项尚未补齐**（部署时 `ant.agent.env / scene / username` 环境变量未传入），实测 `make doctor cc` 报 3 项 WARN。该 gap 与能力评分独立 —— L0 是部署侧约束；当前 agent 评分覆盖 21 项，完整能力规范共 22 项，E4 暂不计入评分。L0 修复在 follow-up，不影响 cchooks 作为采集语义参考实现的地位。

## 22 项能力一览

### A. 会话与拓扑（1 项）

| ID | 能力 | 一句话定义 | 关联旧条款 | 验证 |
|----|------|-----------|-----------|------|
| A1 | 多轮会话识别 + chain 拓扑 | 一轮交互有唯一 root span，其下 generation/tool 为同级 sibling，形成 turn → chat → tool → chat → tool → chat 的链式调用 | C1 | 待补 Rego |

### B. 工具调用语义（6 项）

| ID | 能力 | 一句话定义 | 关联旧条款 | 验证 |
|----|------|-----------|-----------|------|
| B1 | 工具调用精确耗时 | 每个 tool span 表达单次调用的真实起止时间，duration 非负 | C3 | **可机器验证** |
| B2 | 工具调用错误识别 | tool 失败时 `status.code=ERROR` + `error.type`，挂在自身 span | C3 | **可机器验证** |
| B3 | 工具调用输入参数识别 | 每个 tool span 携带 `gen_ai.tool.call.arguments`，反映真实输入参数 | C3 / C9 | 待补 Rego |
| B4 | 工具调用输出内容识别 | 每个 tool span 携带 `gen_ai.tool.call.result`，反映真实输出 | C3 / C9 | 待补 Rego |
| B5 | MCP 调用识别 | tool span 携带 `agentic.tool.type=mcp`，与 builtin / skill / subagent 区分 | C4 | 待补 Rego |
| B6 | Skill 调用识别 | tool span 携带 `agentic.tool.type=skill`，区分命令型 / 工具型 skill | C5 | 待补 Rego |

### C. LLM 调用语义（7 项）

| ID | 能力 | 一句话定义 | 关联旧条款 | 验证 |
|----|------|-----------|-----------|------|
| C1 | LLM 精确耗时 | 每个 LLM span 表达单次调用的真实起止时间，duration 非负 | C2 | **可机器验证** |
| C2 | LLM 错误识别 | LLM 失败时 `status.code=ERROR` + `error.type`，挂在自身 span | C2 | **可机器验证** |
| C3 | LLM 用户输入内容识别 | LLM span / Turn span 携带 `gen_ai.input.messages`，反映真实送入模型的消息 | C9 | 待补 Rego |
| C4 | LLM 系统提示词识别 | LLM span 携带 `gen_ai.system_instructions`，反映该次调用的 system prompt | C12（旧版放弃） | 待补 Rego |
| C5 | LLM 模型输出识别 | LLM span / Turn span 携带 `gen_ai.output.messages`，反映模型实际输出 | C9 | 待补 Rego |
| C6 | LLM thinking 内容识别 | LLM span 携带模型 thinking / reasoning 段落（如 Claude `thinking` block） | （新增） | 待补 Rego |
| C7 | LLM Token usage 识别 | LLM span 携带 `gen_ai.usage.input_tokens` + `output_tokens`，对应单次真实调用 | C11 | **可机器验证** |

### D. 高阶语义（4 项）

| ID | 能力 | 一句话定义 | 关联旧条款 | 验证 |
|----|------|-----------|-----------|------|
| D1 | Compact 识别 | Compaction 作为独立 tool span，携带 `agentic.compaction.*` 扩展属性 | C8 | **可机器验证** |
| D2 | 同步子 agent 调用识别 | Subagent 委派表现为独立 span，携带 `agentic.tool.type=subagent` 与 subagent 标识 | C6 | 待补 Rego |
| D3 | 异步子 agent 调用识别 | 异步触发的 subagent 仍可识别（含跨 trace 关联或父子关系还原） | C7（旧版放弃） | 待补 Rego |
| D4 | Cancel / Retry 错误恢复识别 | api_error 独立 span 携带 retry 属性；cancel 场景可被识别 | C14 | 待补 Rego |

### E. 开发环境与上游调用上下文（2 项）

| ID | 能力 | 一句话定义 | 关联旧条款 | 验证 |
|----|------|-----------|-----------|------|
| E1 | Git 仓库信息识别 | 在可信 worktree/cwd 上识别仓库远端 URL 与 HEAD 引用，并按条件写入 Resource；无事实时保持缺失 | （新增） | 待补 Rego |
| E2 | 上游调用 TRACEPARENT 串联 | 消费有效 W3C TRACEPARENT，将当前 Turn 与上游 trace/span 串联，并向下游传播新的 child context | （新增） | 待补 Rego |
| E3 | 非真实用户 / 外部触发归因识别 | 通过 `AIVISION_META_TRIGGER` 或 `meta.trigger` 记录外部任务归因，并在逻辑 Turn root 记录宿主观察到的触发机制；不从 prompt 推断用户身份 | （新增） | 待补 Rego |
| E4 | SESSION 级别隐私模式设置 | 支持外部 `AIVISION_COLLECTION_MODE=privacy` 覆盖配置，并将最终 privacy mode 一致应用于 session 的 traces、logs/events、metrics 和 replay | （新增） | 待补 Rego |

## 7×22 横向对比矩阵（E4 暂不评分）

每个评分链接到对应 agent 的单项证据文件；完整说明仍保留在聚合评估页：[claudecode](claudecode/CAPABILITIES.md) / [codex](codex/CAPABILITIES.md) / [kimi](kimi/CAPABILITIES.md) / [openclaw](openclaw/CAPABILITIES.md) / [opencode](opencode/CAPABILITIES.md) / [pi](pi/CAPABILITIES.md) / [hermes](hermes/CAPABILITIES.md)。OpenClaw 与 Hermes 的 E1 按常驻程序边界评为“不要求实现”。

| 能力 | claudecode | codex | kimi | openclaw | opencode | pi | hermes | 验证 |
|------|:----------:|:-----:|:-----:|:--------:|:--------:|:----:|:------:|:----:|
| **A. 会话与拓扑** | | | | | | | | |
| [A1 多轮会话 + chain](docs/capabilities/A-session-topology.md#a1-多轮会话识别--chain-拓扑) | [✅](claudecode/capabilities/A1.md) | [✅](codex/capabilities/A1.md) | [✅](kimi/capabilities/A1.md) | [✅](openclaw/capabilities/A1.md) | [✅](opencode/capabilities/A1.md) | [✅](pi/capabilities/A1.md) | [✅](hermes/capabilities/A1.md) | 待补 Rego |
| **B. 工具调用语义** | | | | | | | | |
| [B1 工具耗时](docs/capabilities/B-tool-semantics.md#b1-工具调用精确耗时) | [✅](claudecode/capabilities/B1.md) | [✅](codex/capabilities/B1.md) | [✅](kimi/capabilities/B1.md) | [✅](openclaw/capabilities/B1.md) | [✅](opencode/capabilities/B1.md) | [✅](pi/capabilities/B1.md) | [✅](hermes/capabilities/B1.md) | 可机器验证 |
| [B2 工具错误](docs/capabilities/B-tool-semantics.md#b2-工具调用错误识别) | [✅](claudecode/capabilities/B2.md) | [✅](codex/capabilities/B2.md) | [✅](kimi/capabilities/B2.md) | [✅](openclaw/capabilities/B2.md) | [✅](opencode/capabilities/B2.md) | [✅](pi/capabilities/B2.md) | [✅](hermes/capabilities/B2.md) | 可机器验证 |
| [B3 工具输入参数](docs/capabilities/B-tool-semantics.md#b3-工具调用输入参数识别) | [✅](claudecode/capabilities/B3.md) | [✅](codex/capabilities/B3.md) | [✅](kimi/capabilities/B3.md) | [✅](openclaw/capabilities/B3.md) | [✅](opencode/capabilities/B3.md) | [✅](pi/capabilities/B3.md) | [✅](hermes/capabilities/B3.md) | 待补 Rego |
| [B4 工具输出内容](docs/capabilities/B-tool-semantics.md#b4-工具调用输出内容识别) | [✅](claudecode/capabilities/B4.md) | [✅](codex/capabilities/B4.md) | [✅](kimi/capabilities/B4.md) | [✅](openclaw/capabilities/B4.md) | [✅](opencode/capabilities/B4.md) | [✅](pi/capabilities/B4.md) | [✅](hermes/capabilities/B4.md) | 待补 Rego |
| [B5 MCP 调用识别](docs/capabilities/B-tool-semantics.md#b5-mcp-调用识别) | [✅](claudecode/capabilities/B5.md) | [✅](codex/capabilities/B5.md) | [✅](kimi/capabilities/B5.md) | [🟡](openclaw/capabilities/B5.md) | [✅](opencode/capabilities/B5.md) | [⚪](pi/capabilities/B5.md) | [🟡](hermes/capabilities/B5.md) | 待补 Rego |
| [B6 Skill 调用识别](docs/capabilities/B-tool-semantics.md#b6-skill-调用识别) | [✅](claudecode/capabilities/B6.md) | [✅](codex/capabilities/B6.md) | [✅](kimi/capabilities/B6.md) | [✅](openclaw/capabilities/B6.md) | [🟡](opencode/capabilities/B6.md) | [✅](pi/capabilities/B6.md) | [🟡](hermes/capabilities/B6.md) | 待补 Rego |
| **C. LLM 调用语义** | | | | | | | | |
| [C1 LLM 耗时](docs/capabilities/C-llm-semantics.md#c1-llm-精确耗时) | [✅](claudecode/capabilities/C1.md) | [✅](codex/capabilities/C1.md) | [✅](kimi/capabilities/C1.md) | [✅](openclaw/capabilities/C1.md) | [✅](opencode/capabilities/C1.md) | [✅](pi/capabilities/C1.md) | [✅](hermes/capabilities/C1.md) | 可机器验证 |
| [C2 LLM 错误](docs/capabilities/C-llm-semantics.md#c2-llm-错误识别) | [🟡](claudecode/capabilities/C2.md) | [🟡](codex/capabilities/C2.md) | [✅](kimi/capabilities/C2.md) | [🟡](openclaw/capabilities/C2.md) | [✅](opencode/capabilities/C2.md) | [🟡](pi/capabilities/C2.md) | [🟡](hermes/capabilities/C2.md) | 可机器验证 |
| [C3 LLM 用户输入](docs/capabilities/C-llm-semantics.md#c3-llm-用户输入内容识别) | [✅](claudecode/capabilities/C3.md) | [✅](codex/capabilities/C3.md) | [✅](kimi/capabilities/C3.md) | [✅](openclaw/capabilities/C3.md) | [✅](opencode/capabilities/C3.md) | [✅](pi/capabilities/C3.md) | [✅](hermes/capabilities/C3.md) | 待补 Rego |
| [C4 LLM 系统提示词](docs/capabilities/C-llm-semantics.md#c4-llm-系统提示词识别) | [⚪](claudecode/capabilities/C4.md) | [✅](codex/capabilities/C4.md) | [⚪](kimi/capabilities/C4.md) | [✅](openclaw/capabilities/C4.md) | [🟡](opencode/capabilities/C4.md) | [✅](pi/capabilities/C4.md) | [✅](hermes/capabilities/C4.md) | 待补 Rego |
| [C5 LLM 模型输出](docs/capabilities/C-llm-semantics.md#c5-llm-模型输出识别) | [✅](claudecode/capabilities/C5.md) | [✅](codex/capabilities/C5.md) | [✅](kimi/capabilities/C5.md) | [✅](openclaw/capabilities/C5.md) | [✅](opencode/capabilities/C5.md) | [✅](pi/capabilities/C5.md) | [✅](hermes/capabilities/C5.md) | 待补 Rego |
| [C6 LLM thinking](docs/capabilities/C-llm-semantics.md#c6-llm-thinking-内容识别) | [✅](claudecode/capabilities/C6.md) | [⚪](codex/capabilities/C6.md) | [✅](kimi/capabilities/C6.md) | [⚪](openclaw/capabilities/C6.md) | [✅](opencode/capabilities/C6.md) | [✅](pi/capabilities/C6.md) | [⚪](hermes/capabilities/C6.md) | 待补 Rego |
| [C7 LLM Token usage](docs/capabilities/C-llm-semantics.md#c7-llm-token-usage-识别) | [✅](claudecode/capabilities/C7.md) | [✅](codex/capabilities/C7.md) | [✅](kimi/capabilities/C7.md) | [✅](openclaw/capabilities/C7.md) | [🟡](opencode/capabilities/C7.md) | [✅](pi/capabilities/C7.md) | [✅](hermes/capabilities/C7.md) | 可机器验证 |
| **D. 高阶语义** | | | | | | | | |
| [D1 Compact](docs/capabilities/D-advanced-semantics.md#d1-compact-识别) | [✅](claudecode/capabilities/D1.md) | [✅](codex/capabilities/D1.md) | [✅](kimi/capabilities/D1.md) | [🟡](openclaw/capabilities/D1.md) | [🟡](opencode/capabilities/D1.md) | [🟡](pi/capabilities/D1.md) | [⚪](hermes/capabilities/D1.md) | 可机器验证 |
| [D2 同步子 agent](docs/capabilities/D-advanced-semantics.md#d2-同步子-agent-调用识别) | [✅](claudecode/capabilities/D2.md) | [✅](codex/capabilities/D2.md) | [✅](kimi/capabilities/D2.md) | [✅](openclaw/capabilities/D2.md) | [✅](opencode/capabilities/D2.md) | [⚪](pi/capabilities/D2.md) | [✅](hermes/capabilities/D2.md) | 待补 Rego |
| [D3 异步子 agent](docs/capabilities/D-advanced-semantics.md#d3-异步子-agent-调用识别) | [🟡](claudecode/capabilities/D3.md) | [⚪](codex/capabilities/D3.md) | [⚪](kimi/capabilities/D3.md) | [⚪](openclaw/capabilities/D3.md) | [🟡](opencode/capabilities/D3.md) | [⚪](pi/capabilities/D3.md) | [⚪](hermes/capabilities/D3.md) | 待补 Rego |
| [D4 Cancel/Retry](docs/capabilities/D-advanced-semantics.md#d4-cancel--retry-错误恢复识别) | [🟡](claudecode/capabilities/D4.md) | [🟡](codex/capabilities/D4.md) | [✅](kimi/capabilities/D4.md) | [🟡](openclaw/capabilities/D4.md) | [🟡](opencode/capabilities/D4.md) | [🟡](pi/capabilities/D4.md) | [🟡](hermes/capabilities/D4.md) | 待补 Rego |
| **E. 开发环境与上游调用上下文** | | | | | | | | |
| [E1 Git 仓库信息识别](docs/capabilities/E-repository-context.md#e1-git-仓库信息识别) | [🟡](claudecode/capabilities/E1.md) | [🟡](codex/capabilities/E1.md) | [❌](kimi/capabilities/E1.md) | [⚪](openclaw/capabilities/E1.md) | [🟡](opencode/capabilities/E1.md) | [❌](pi/capabilities/E1.md) | [⚪](hermes/capabilities/E1.md) | 待补 Rego |
| [E2 上游调用 TRACEPARENT 串联](docs/capabilities/E-upstream-trace-context.md#e2-上游调用-traceparent-串联) | [✅](claudecode/capabilities/E2.md) | [✅](codex/capabilities/E2.md) | [⚪](kimi/capabilities/E2.md) | [🟡](openclaw/capabilities/E2.md) | [⚪](opencode/capabilities/E2.md) | [⚪](pi/capabilities/E2.md) | [⚪](hermes/capabilities/E2.md) | 待补 Rego |
| [E3 非真实用户 / 外部触发归因识别](docs/capabilities/E-meta-trigger.md#e3-非真实用户--外部触发归因识别) | [❌](claudecode/capabilities/E3.md) | [❌](codex/capabilities/E3.md) | [❌](kimi/capabilities/E3.md) | [❌](openclaw/capabilities/E3.md) | [🟡](opencode/capabilities/E3.md) | [❌](pi/capabilities/E3.md) | [❌](hermes/capabilities/E3.md) | 待补 Rego |
| [E4 SESSION 级别隐私模式设置](docs/capabilities/E-session-privacy.md#e4-session-级别隐私模式设置) | — | — | — | — | — | — | — | 待评估 |

| Agent | ✅ 完全 | 🟡 部分 | ❌ 未实现 | ⚪ 不适用 |
|-------|:------:|:------:|:--------:|:--------:|
| 说明 | 以上汇总仅覆盖已评估的 21 项能力；E4 SESSION 隐私模式本轮不计入 | | | |
| claudecode | 15 | 4 | 1 | 1 |
| codex | 15 | 3 | 1 | 2 |
| kimi | 16 | 0 | 2 | 3 |
| openclaw | 12 | 5 | 1 | 3 |
| opencode | 12 | 8 | 0 | 1 |
| pi | 12 | 3 | 2 | 4 |
| hermes | 11 | 4 | 1 | 5 |

### 共性 gap 总结

横向对比当前纳入矩阵的 7 个 agent 后，识别出以下**最大共性短板**：

1. **D4 Cancel / Retry — 多数 agent 为 🟡**
   普遍缺 retry attempt 序号、用户主动取消或真实 provider-error replay。openclaw 已通过 `model_call_ended` hook 补齐 `failureKind` 精确错误分类，但仍缺 cancel/retry 真实样本，按双证据规则保持 🟡；Hermes 已覆盖 cancel/interrupted pending span 结束语义，但缺逐次 retry span 和真实 provider-error replay。

2. **C7 LLM Token usage — 少数 agent 为 🟡**
   codex 只把 token 写到最后一个 assistant，前几次 LLM span 无 token。opencode 已按具体 generation 记录 token，Turn 根不再重复聚合；明确配置的 OpenAI-compatible Chat Completions 已区分 provider 未知用量与真实零用量，并独立记录 title 等辅助请求。其它协议和多个传输 attempt 的完整对账仍需各自证据，保持 🟡。openclaw 已通过 Anthropic Token Normalizer L1 三层架构补齐（✅）。

3. **C2 LLM 错误 — 多数 agent 为 🟡**
   主要 gap 是错误叶子或真实 provider-error replay 证据不完整：claudecode / codex / openclaw / Pi / Hermes 仍存在不同程度的错误分类、冒泡或正向 replay 覆盖不足；opencode 与 kimi 已有较完整的错误路径证据。

4. **E1 Git 仓库信息 — 实时实现与 replay 证据不对称**
   Claude Code、Codex与 OpenCode 已有可信 cwd/worktree 上的 remote/branch 探测；当前缺少包含 VCS Resource 的可复现 replay 时统一保持 🟡。Kimi 与 Pi 的 cwd 数据源存在但尚未采集，评为 ❌；OpenClaw 与 Hermes 按常驻 gateway/observer 产品边界不要求 E1，评为 ⚪。

5. **E2 上游 TRACEPARENT — 二进制 hook 已支持，插件侧多为数据源缺口**
   Claude Code 与 Codex 能严格解析有效 W3C `TRACEPARENT`，将上游 trace/span 接到当前 Turn；OpenClaw 已有上游诊断 parent 与 SOFA link，但尚未证明 W3C `TRACEPARENT` 进入 Turn parent 拓扑。Kimi、OpenCode、Pi、Hermes 当前没有可证实的 W3C source，按既有 E2 评估处理。

6. **E3 非真实用户 / 外部触发归因 — Resource 与 Turn 两层证据仍不完整**
   外部触发归因规范要求 `AIVISION_META_TRIGGER` / `meta.trigger`、`ant.meta.trigger` Resource 与 `agentic.turn.trigger` Turn root 语义。opencode 已支持调用者显式配置的 Resource 归因，但没有宿主 Turn trigger 和该能力的独立平台/UI 验收，保持 🟡；其它 6 个 agent 保留各自 ❌ 基线。

**单点亮点**（与共性 gap 互补）：
- **C4 系统提示词**：openclaw 通过 runtime hook 直接取得 `event.systemPrompt`，为 ✅；Hermes 从 observer request messages/body 提取 system，为 ✅；Codex 也从 `session_meta.payload.base_instructions.text` 提取并写入 root/Generation，为 ✅。opencode 的独立 replay dispatcher 已把 system transform 送入共享 core，但当前缺可复现的仓库内 replay 正向样本，保持 🟡。
- **A1 拓扑**：当前纳入矩阵的 agent 均已符合 sibling 拓扑（全 ✅）。openclaw 此前 Tool 嵌套在 LLM 下，已修复为挂 turn root。
- **D2 同步子 agent**：claudecode、codex、kimi、openclaw、opencode 和 Hermes ✅（分别识别 Task/协作工具、subagent_spawned hook、`task` 委派产生的 `SyncAgent` span，以及 Hermes `subagent_start/stop` lifecycle）；Pi 因当前配置没有 subagent tool 评为 ⚪。opencode 的后台 task 当前统一按用户等待结果的同步语义建模，因此 D3 仍为 🟡。

> **如何用 cchooks 作为参照**：
> - 看每项能力**怎么实现**：[docs/capabilities/](docs/capabilities/) 各分组文件末尾的 "cchooks 落地" 子节（含代码 file:line + replay 样本 + 关键设计决策）
> - 看 cchooks 完整评分依据：[claudecode/CAPABILITIES.md](claudecode/CAPABILITIES.md)
> - 看工程方法论（6 个工作流 / 数据模型 / 接入指南）：[METHODOLOGY.md](METHODOLOGY.md)，全部以 cchooks 为参考实现

## 实现中（暂不评估）

以下 agent 仍处于实现中或暂不纳入本轮 7×22 成熟度矩阵（本轮不生成逐项评分）：

| Agent | 状态 | 备注 |
|-------|------|------|
| dsh | 🚧 实现中 | 见 `deepseek/`（自研捕获 + traces/metrics/events + replay 已过 weaver-guard，已评估能力双证据待完整跑；E4 暂不评估） |

## 治理与机器验证

- **机器验证**：B1 / B2 / C1 / C2 / C7 / D1 共 6 项已通过 `weaver/policies/` 中 Rego 策略机器验证，详见各能力章节的"验证方式"。E1/E2/E3 已做字段注册和人工/测试证据检查；E4 本轮只做能力标准登记，均尚未升级为硬 Rego。
- **待补 Rego**：其余能力依赖人工评估或 transcript 比对，后续按需补 Rego。
- **文档结构验证**：运行 `python3 scripts/validate_capabilities.py` 检查 7 个 Agent 已评分的 21 项能力证据、代码引用、聚合评分、汇总计数和矩阵链接。
- **新 agent 接入**：按 [docs/capabilities/](docs/capabilities/) 各分组文件中的"必要属性"定义采集字段，写 `<agent>/CAPABILITIES.md` 并在 `<agent>/capabilities/<ID>.md` 给出逐项评分与证据。E1 的字段注册说明见 [`docs/semconv/vcs.md`](docs/semconv/vcs.md)；E2 的传播契约见 [`docs/capabilities/E-upstream-trace-context.md`](docs/capabilities/E-upstream-trace-context.md)；E3 的触发归因契约见 [`docs/capabilities/E-meta-trigger.md`](docs/capabilities/E-meta-trigger.md)。

## 维护约定

- 根目录 `CAPABILITIES.md` 只保留总纲、四态判定、能力一览与横向矩阵
- 每项能力的详细规则正文写入 `docs/capabilities/`
- 每个 agent 的聚合评估写入 `<agent>/CAPABILITIES.md`；每项能力的评分与**代码 file:line + replay/source 证据**写入 `<agent>/capabilities/<ID>.md`
- 新增或修改能力时，应同步更新对应章节、矩阵以及相关 agent 评估报告
- 工程原则、治理流程、新增能力流程见 [CONSTITUTION.md](CONSTITUTION.md)；本文件不重复
