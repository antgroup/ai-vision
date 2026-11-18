---
object_key: task-stats
topic: persistence
title: Task Stats 持久化
---

# Task Stats 持久化

## task-stats-persistence@20260823-0234

**只持久化赢得终态 fence 的完整统计快照**

```yaml
record_id: task-stats/persistence@20260823-0234
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - task
  - task-runner
canonical_doc: docs/architecture.md
```

### 背景

并发 attempt 都可能完成，若全部发布统计会造成同一 Task 重复计数。

### 决策

- RUNNING 期间统计只存在 Runner 内存，不周期写入。
- 成功或失败终态 CAS 在 OceanBase Task 行写入完整 Stats。
- 只有该 CAS 成功的 attempt 通过采集网关向旧版遥测存储上报 `name=task_stats` 的快照。
- 窗口解析前失败仍可写 OB 失败终态，但不伪造业务时间或向旧版遥测存储上报快照。

### 保持不变

DiagnosisRecord 的异步写入与 TaskStats 权威发布使用不同策略。

### 影响与风险

stale attempt 不发布统计，保证统一分析每个逻辑 Task 只有权威快照。

### 验证

由 Runner terminal fence、窗口失败、Antcollector 和 stale attempt 测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/architecture.md` 核对为已实施行为。
