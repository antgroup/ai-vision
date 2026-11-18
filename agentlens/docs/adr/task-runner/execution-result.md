---
object_key: task-runner
topic: execution-result
title: Task Runner 执行结果
---

# Task Runner 执行结果

## task-runner-execution-result@20260823-0234

**终态 CAS 成功后才发布权威执行结果**

```yaml
record_id: task-runner/execution-result@20260823-0234
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - task
  - diagnosis-record
  - task-stats
canonical_doc: docs/architecture.md
```

### 背景

记录、OB Stats、旧版遥测存储中的 TaskStats 和 Task 终态分属不同存储，需要明确何时结果成为权威。

### 决策

- Runner 全程在内存累计 TaskStats，并异步上报按策略选中的 DiagnosisRecord。
- 成功或失败通过带 worker、attempt 和 RUNNING fence 的 CAS 写 Task 终态及完整 OB Stats。
- 仅 CAS 成功后上报全局终态 TaskStats；需要 owner 快照时从同一内存事实派生并上报。
- Task message 使用 `saved_records=<N>` 表达已入队明细数，不把它当作完整诊断数量。

### 保持不变

RUNNING Task 不展示持续进度；分析接口不从 DiagnosisRecord 反推完整统计。

### 影响与风险

外部明细可能先于终态可见，且 stale attempt 明细可能存在；权威 TaskStats 由终态 fence 保证。

### 验证

由 Runner 成功、失败、stale attempt、上报失败和 saved_records 测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/architecture.md` 核对为已实施行为。
