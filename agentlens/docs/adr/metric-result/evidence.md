---
object_key: metric-result
topic: evidence
title: Metric Result 证据
---

# Metric Result 证据

## metric-result-evidence@20260823-0234

**以结构化 evidence 保存可追溯诊断依据**

```yaml
record_id: metric-result/evidence@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - diagnosis-record
  - security
canonical_doc: docs/diagnoser/README.md
```

### 背景

诊断理由需要定位到 Trace 事实，同时必须避免把敏感输入或 Prompt 在多个层级重复持久化。

### 决策

- 可定位 Rule 使用 `evidence.bad_spans` 保存来源 `span_id` 和结构化 findings。
- 无法可靠定位时不伪造步骤索引或占位 span；reason 可以说明非定位性结论。
- 通用 one-shot/custom LLM Executor 将本次唯一渲染输入保存在记录级
  `evidence.agent_run.rendered_prompt`，不在每个指标重复；专用 Executor 按自身合同保存结构化
  调用证据，可以不持久化完整 Prompt。
- evidence 由具体 Executor 定义，但必须可序列化并遵守敏感信息边界。

### 保持不变

Evidence 支撑解释与审计，不改变 score、passed 或 evaluation status。

### 影响与风险

完整 evidence 会增加记录体积并可能包含用户内容，新增字段需评估脱敏、保存和读取边界。

### 验证

由 bad_spans helper、Rule/LLM Executor、记录序列化和敏感字段测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/diagnoser/README.md` 核对为已实施行为。

## metric-result-evidence@20260823-1933

**分离 Metric 判定证据与 Execution 执行观测**

```yaml
record_id: metric-result/evidence@20260823-1933
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T19:33+08:00
decided_at: 2026-08-23T19:42+08:00
implemented_at: 2026-08-23T20:59+08:00
supersedes:
  - metric-result/evidence@20260823-0234
related_records:
  - metric-result/fact-model@20260823-1933
  - executor/plugin-contract@20260823-1933
  - diagnosis-record/query-contract@20260823-1933
affected_objects:
  - executor
  - diagnosis-record
  - security
canonical_doc: docs/diagnoser/README.md
```

### 背景

当前记录级 evidence 同时承载渲染 Prompt、执行耗时、失败分类和诊断证据，专用 Executor 又可能把
单指标证据放在记录级，导致两层 evidence 边界不稳定。

### 决策

- `Metric.evidence` 只保存支撑该 Metric verdict 的结构化证据；可定位 Rule 继续使用 `bad_spans` 和
  findings，无法可靠定位时不得伪造 span。
- DiagnosisResult 使用 `execution` 保存整次执行观测。Execution 固定包含 `status`、`code`、`msg`、
  `evidence`；code/msg 描述执行结果，execution.evidence 保存渲染 Prompt、模型调用、source hash、
  执行耗时等共享信息。
- 同一证据不得在 execution 和多个 Metric 中重复；Metric reason 解释业务结论，Execution msg 解释
  执行状态。
- evidence 只要求是可 JSON 序列化对象，不强制保存分数、阈值或原始测量值。
- 所有层级继续遵守敏感信息边界；code/msg 必须是可安全展示的稳定信息，不直接暴露未经处理的底层异常。

### 保持不变

Evidence 只支撑解释、复现和审计，不改变 Metric verdict、DiagnosisRecord verdict 或 TaskStats。

### 影响与风险

顶层 `evidence` 改名为 `execution`，调用方必须迁移读取路径。成功执行仍可保存 Prompt 等观测，记录
体积和敏感信息风险继续存在，需要沿用现有脱敏与读取约束。

### 验证

覆盖 bad_spans、Execution code/msg/evidence、共享证据不重复、JSON 序列化和敏感字段边界。

### 实施说明

已实施：指标证据与 execution 共享执行观测已分层，记录顶层 evidence 已删除。
