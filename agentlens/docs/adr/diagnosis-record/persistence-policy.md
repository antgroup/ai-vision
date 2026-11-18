---
object_key: diagnosis-record
topic: persistence-policy
title: Diagnosis Record 保存策略
---

# Diagnosis Record 保存策略

## diagnosis-record-persistence-policy@20260823-0234

**按 Job 策略稀疏保存 evaluate 明细**

```yaml
record_id: diagnosis-record/persistence-policy@20260823-0234
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - job
  - task-runner
  - task-stats
canonical_doc: docs/architecture.md
```

### 背景

完整质量统计需要覆盖所有目标，但逐目标保存全部明细会带来较高存储和查询成本。

### 决策

- pre-evaluate final 的 GOOD、BAD、FAILED 始终只进入 TaskStats，不保存 DiagnosisRecord。
- `evaluate()` 结果先累计统计，再按 Job `spec.save_badcase_only` 选择明细。
- 缺省只保存 BAD；显式为 false 时保存 evaluate 的 GOOD、BAD、FAILED。
- 记录以固定 span name 上报，经采集网关仅写入旧版遥测存储。

### 保持不变

TaskStats 始终保留完整诊断计数；保存策略不改变分母、分数或 Executor 结果。

### 影响与风险

列表不是完整样本集，调用方必须使用 TaskStats 计算质量；策略变化只影响后续记录。

### 验证

由 Runner pre/evaluate 分支、Job spec、saved_records 和 Antcollector 上报测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/architecture.md` 核对为已实施行为。
