---
object_key: scheduler
topic: task-dispatch
title: Scheduler 任务派发
---

# Scheduler 任务派发

## scheduler-task-dispatch@20260823-0234

**Master 扫描到期 Task 并以 CAS 派发**

```yaml
record_id: scheduler/task-dispatch@20260823-0234
decision_status: superseded
implementation_status: retired
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

定时和手动 Task 需要共用派发路径，并在多轮扫描中避免同一 Task 被并发领取。

### 决策

- ScheduleScanner 根据 Job cron 和逻辑时间创建缺失的 PENDING Task。
- DispatchLoop 周期扫描 PENDING，通过 CAS 写入 DISPATCHED 和 worker；远端派发另在内部执行请求中
  携带当前 Master token，该 token 不持久化到 Task。
- 手动触发写入 PENDING 并通知 Master 立即 tick，周期扫描作为兜底。
- worker 选择依据健康心跳和当前 RUNNING 数量；Master 是否执行任务由配置控制，Master 本地执行另受
  本地并发配置约束。

### 保持不变

attempt 只在 Worker 接受 `DISPATCHED -> RUNNING` 时增加，重复 dispatch 超时不消耗 attempt。

### 影响与风险

通知丢失最多增加一个扫描周期延迟；CAS 保证多轮或切主扫描不会合法派发同一状态两次。

### 验证

由 cron scanner、DispatchLoop、手动通知、worker 选择和 CAS 冲突测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/scheduling.md` 核对为已实施行为。

## scheduler-task-dispatch@20260909-1639

**以已分配 Task 负载和逐次容量检查进行派单**

```yaml
record_id: scheduler/task-dispatch@20260909-1639
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-09-09T16:39+08:00
decided_at: 2026-09-09T16:39+08:00
implemented_at: 2026-09-09T16:42+08:00
supersedes:
  - scheduler/task-dispatch@20260823-0234
affected_objects:
  - task
  - task-runner
canonical_doc: docs/scheduling.md
```

### 背景

只统计 RUNNING 会漏掉已派发但尚未开始的任务；仅在批次开始时排除满载 Worker，会使批内新增派单
超过剩余容量。用户确认同时修正这两点，并明确保持同负载时的稳定选机顺序，不改为轮询。

### 决策

- ScheduleScanner 根据 Job cron 和逻辑时间创建缺失的 PENDING Task；手动触发写入 PENDING 并通知
  Master 立即 tick，周期扫描作为兜底。
- DispatchLoop 从健康心跳确定候选 Worker，Master 是否参与由 MASTER_EXECUTE_TASKS 控制。
- 按 worker 聚合 RUNNING + DISPATCHED 作为已分配负载；无已分配任务的候选实例计为 0。
- 每条 PENDING Task 派发前，重新根据批内预计负载排除达到 WORKER_EXECUTE_CONCURRENCY 的远端
  Worker。Master 自执行继续按 MASTER_EXECUTE_CONCURRENCY 对应的进程内剩余槽位限制。
- 有容量的候选中，已分配负载最少优先；相同时非 Master 优先，再按 Worker UUID 稳定排序，不轮询。
- CAS 成功写入 DISPATCHED 和 worker 后，立即增加该实例的批内预计负载；CAS 失败不消耗容量。
  所有候选满载时停止本批派单，剩余任务保留 PENDING。跨轮扫描重新读取已分配负载。
- 自派任务进程内执行；远端推送携带当前 Master token，该 token 不持久化到 Task。远端推送失败
  （包括既有 Worker 满载 429）按现有 CAS 回退 PENDING，后续轮次可重新分配。

### 保持不变

Task 状态机、attempt fence、取消和恢复不变；attempt 只在 DISPATCHED -> RUNNING 时增加。
Worker 入口并发兜底和 Master 本地执行槽位不改动，也不新增 CPU 采样或容量心跳字段。

### 影响与风险

已派发未启动的任务会保留容量，进入 RUNNING 后继续占用，直到结束、取消或恢复回 PENDING；通知
失败可能使本批保守少派，下一批恢复。
Master 按自身 WORKER_EXECUTE_CONCURRENCY 预判远端容量，各实例配置不一致时仍依赖 Worker 入口
兜底。数据库负载快照不等于跨调度调用的原子容量预留，不宣称提供分布式硬限额。
无需表结构迁移；回滚会重新采用 RUNNING 口径和原批次检查，可能恢复漏计和超派问题。

### 验证

覆盖聚合状态范围、已派发任务跨轮占位、批内触顶、满载不派、CAS 失败不占位、推送失败释放容量，
以及负载相同时的非 Master 优先和 UUID 稳定排序；原 Master 本地槽位测试继续通过。

### 实施说明

已实现 TaskTable 的已分配负载聚合与 DispatchLoop 批内逐条容量检查，保持原稳定选机排序。
日志字段改为 assigned_tasks_by_worker，配置和调度文档同步新口径。
