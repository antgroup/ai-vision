---
object_key: task
topic: lifecycle
title: Task 生命周期
---

# Task 生命周期

## task-lifecycle@20260823-0234

**Task 使用明确状态机和非抢占式执行**

```yaml
record_id: task/lifecycle@20260823-0234
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - scheduler
  - task-runner
  - http-api
canonical_doc: docs/scheduling.md
```

### 背景

Task 在 Master 与 Worker 间异步流转，需要状态 CAS 防止重复派发和非法终态覆盖。

### 决策

- Task 状态按 PENDING、DISPATCHED、RUNNING 和 SUCCEEDED/FAILED/CANCELED 终态流转。
- 关键迁移使用期望状态 CAS；`DISPATCHED -> RUNNING` 时增加 attempt。
- PENDING 或 DISPATCHED 可取消，RUNNING 不接受取消且 Runner 不轮询控制状态。
- FAILED 不自动重试；再次执行由手动触发创建新 Task。

### 保持不变

Task 不保存 Job 配置快照，不支持断点续跑；Runner 启动时读取到的配置用于该 attempt。

### 影响与风险

RUNNING 非抢占保证执行路径简单，但 Rescue 后旧 attempt 可能继续自然运行。

### 验证

由 Task 路由、Store CAS、Runner 终态和并发状态测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/scheduling.md` 核对为已实施行为。
