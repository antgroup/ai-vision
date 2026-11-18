---
object_key: scheduler
topic: task-recovery
title: Scheduler 任务恢复
---

# Scheduler 任务恢复

## scheduler-task-recovery@20260823-0234

**按超时、心跳和 attempt 上限恢复异常 Task**

```yaml
record_id: scheduler/task-recovery@20260823-0234
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - task
  - task-runner
canonical_doc: docs/scheduling.md
```

### 背景

派发请求丢失、Worker 崩溃或进程重启会留下 DISPATCHED/RUNNING Task，需要有限恢复而不能无限重跑。

### 决策

- DISPATCHED 超时和死 Worker 的 RUNNING Task由 RescueLoop 检测；Worker 启动时也恢复属于自己的遗留任务。
- attempt 未达上限时保留 `scheduled_at`、清理 worker 与旧 OB Stats并回退 PENDING。
- 达到 `MAX_TASK_ATTEMPTS` 时清理旧 Stats并直接 FAILED；默认最多开始两次实际执行。
- Runner 主动写出的 FAILED 不进入自动恢复。

### 保持不变

恢复从头执行，不保存检查点，也不主动终止可能仍存活的旧 attempt。

### 影响与风险

有限 attempt 防止永久重试；非抢占会产生短期并行物理写入，由终态和读取 fence 收口。

### 验证

由 Dispatch timeout、dead worker、startup recovery、attempt 边界和 stale Runner 测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/scheduling.md` 核对为已实施行为。
