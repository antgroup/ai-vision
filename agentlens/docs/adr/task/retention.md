---
object_key: task
topic: retention
title: Task 保留
---

# Task 保留

## task-retention@20260823-0234

**Master 分批清理超过保留期的终态 Task**

```yaml
record_id: task/retention@20260823-0234
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - scheduler
canonical_doc: docs/scheduling.md
```

### 背景

OceanBase Task 会持续增长，活动任务和旧版遥测存储中的诊断数据具有独立生命周期。

### 决策

- 仅 Master 启动 TaskCleanupLoop，当选后立即执行一批，之后按配置周期执行。
- 只删除 `finished_at` 超过保留期的 SUCCEEDED、FAILED、CANCELED Task；默认 30 天。
- 每批按 `finished_at, id` 查询有限内部 ID，并在短事务中删除；默认每 600 秒最多 1000 条。
- 不删除活动 Task、`finished_at IS NULL` 记录、DiagnosisRecord 或旧版遥测存储中的 TaskStats。

### 保持不变

不按 Job 保留最新 Task，也不级联清理外部观测数据。

### 影响与风险

清理依赖 `status, finished_at, id` 索引；批次失败回滚并等待下个周期。

### 验证

由 cleanup loop、Store 批删、边界时间和部署 schema 检查验证。

### 实施说明

本记录已按当前代码、测试和 `docs/scheduling.md` 核对为已实施行为。
