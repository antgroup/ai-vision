---
object_key: diagnosis-record
topic: verdict-derivation
title: Diagnosis Record Verdict 派生
---

# Diagnosis Record Verdict 派生

## diagnosis-record-verdict-derivation@20260823-0234

**从 MetricResult 列表唯一派生记录 verdict**

```yaml
record_id: diagnosis-record/verdict-derivation@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - metric-result
  - task-stats
canonical_doc: docs/api.md
```

### 背景

Trace 级记录需要紧凑结论，但不能建立独立于指标结果的第二套判定。

### 决策

- 任一 EVALUATED MetricResult 未通过时记录 verdict 为 bad；否则任一 MetricResult FAILED 时为 failed；
  其余为 good。因此同一记录同时包含未通过和执行失败结果时，bad 优先。
- displayed score 取 results 中非空 score 的最小值；failed metric names 收集未通过或 FAILED 的结果名。
- summary 由 Executor 在构造 DiagnosisRecord 时单独写入并随记录持久化，不由领域模型从 results
  机械派生或校验。
- 反馈只追加 review 状态和内容，不修改原始 verdict。

### 保持不变

列表 verdict 只使用 good、bad、failed；MetricResult 继续保留更细的评估状态和证据。

### 影响与风险

verdict 派生顺序决定筛选、保存和统计读模型，修改会重新解释已有物理记录；summary 仍需由各
Executor 保持与结果一致。

### 验证

由 DiagnosisRecord 构造、反馈版本和列表摘要测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/api.md` 核对为已实施行为。

## diagnosis-record-verdict-derivation@20260823-1933

**从 Execution 与 Metric 派生记录结论和摘要**

```yaml
record_id: diagnosis-record/verdict-derivation@20260823-1933
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T19:33+08:00
decided_at: 2026-08-23T19:42+08:00
implemented_at: 2026-08-23T20:59+08:00
supersedes:
  - diagnosis-record/verdict-derivation@20260823-0234
related_records:
  - metric-result/evaluation-status@20260823-1933
  - executor/plugin-contract@20260823-1933
  - diagnosis-record/query-contract@20260823-1933
affected_objects:
  - metric-result
  - executor
  - task-stats
  - http-api
canonical_doc: docs/api.md
```

### 背景

当前 verdict 从 status/passed 派生，但 summary 由 Executor 自由填写，displayed score 和 failed metric
names 又从结果二次派生，允许同一记录出现互相矛盾的结论和摘要。

### 决策

- DiagnosisRecord 使用 `metrics`；任一 Metric bad 时 verdict 为 bad，否则任一 Metric failed 时为 failed，
  其余为 good，继续保持 bad 高于 failed。
- `execution.status=failed` 时 Runner 为全部声明 Metric 生成 failed，Execution code/msg 非空；
  `execution.status=succeeded` 可以包含由可信 Executor 产生的单 Metric failed，并保留其他有效 Metric。
- summary 不再由 Executor 或模型单独输入。整体执行失败时取 execution.msg；存在 bad/failed Metric 时按
  本次 metrics 返回顺序收集 bad/failed 的非空 reason；全部 good 时收集 good 的非空 reason。收集结果
  均稳定去重并以中文分号连接；没有可用 reason 时固定为“全部诊断项通过”。
- 删除 confidence、displayed score 和 failed metric names；反馈继续只追加 review 状态与内容，不修改
  原始 metrics 或派生 verdict。
- verdict 和 summary 是领域与 HTTP 读模型中的派生属性，不是独立诊断事实。

### 保持不变

记录级 verdict 继续只使用 good/bad/failed，保存策略继续根据派生 verdict 决定是否写明细。

### 影响与风险

summary 自由文案退出后，列表摘要完全依赖 Metric reason 的质量；bad/failed reason 必填且必须安全可
展示，good reason 可选，提供后也会进入全部通过记录的摘要。存在异常指标时忽略 good reason，避免正常
项说明稀释异常摘要。删除 displayed score 会移除明细分数排序能力。

### 验证

覆盖 good/bad/failed 与混合优先级、执行级失败、单 Metric 失败、summary 顺序与去重、全部 GOOD 有无
reason、异常记录忽略 good reason、反馈不改 verdict，以及被删除派生字段不再出现。

### 实施说明

已实施：verdict 和 summary 已由 Metric/Execution 统一派生；全部 good 时保留非空 Metric reason，旧摘要
与分数字段已删除。
