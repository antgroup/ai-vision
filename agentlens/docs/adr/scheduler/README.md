---
object_key: scheduler
title: Scheduler 决策档案
scope: Leader election, task dispatch, and failure recovery
---

# Scheduler 决策档案

选主、任务派发和异常恢复。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [leader-election](leader-election.md) | [scheduler/leader-election@20260823-0234](leader-election.md#scheduler-leader-election20260823-0234) | [scheduler/leader-election@20260823-0234](leader-election.md#scheduler-leader-election20260823-0234) |
| [task-dispatch](task-dispatch.md) | [scheduler/task-dispatch@20260909-1639](task-dispatch.md#scheduler-task-dispatch20260909-1639) | [scheduler/task-dispatch@20260909-1639](task-dispatch.md#scheduler-task-dispatch20260909-1639) |
| [task-recovery](task-recovery.md) | [scheduler/task-recovery@20260823-0234](task-recovery.md#scheduler-task-recovery20260823-0234) | [scheduler/task-recovery@20260823-0234](task-recovery.md#scheduler-task-recovery20260823-0234) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Job → Scheduler | [job/retirement-migration@20261010-0350](../job/retirement-migration.md#job-retirement-migration20261010-0350) | 退役资源迁移遵守授权、排空与回读边界 |
| Security → Scheduler | [security/service-authentication@20260823-0234](../security/service-authentication.md#security-service-authentication20260823-0234) | HTTP 服务使用预共享 token 和执行 fence |
| Security → Scheduler | [security/service-authentication@20261020-1127](../security/service-authentication.md#security-service-authentication20261020-1127) | 匿名存活与数据库就绪探针，业务接口保留服务认证 |
| Task → Scheduler | [task/attempt-ownership@20260823-0234](../task/attempt-ownership.md#task-attempt-ownership20260823-0234) | 以 worker、attempt 和状态 fence 决定终态所有权 |
| Task → Scheduler | [task/attempt-ownership@20261009-1945](../task/attempt-ownership.md#task-attempt-ownership20261009-1945) | Task Attempt 所有权：通用 Core 与可信有界执行 |
| Task → Scheduler | [task/execution-snapshot@20261009-1800](../task/execution-snapshot.md#task-execution-snapshot20261009-1800) | 创建 Task 时冻结有效执行定义与语义版本 |
| Task → Scheduler | [task/execution-snapshot@20261009-1945](../task/execution-snapshot.md#task-execution-snapshot20261009-1945) | 创建 Task 时冻结有效执行定义与语义版本：通用 Core 与可信有界执行 |
| Task → Scheduler | [task/execution-snapshot@20261010-0350](../task/execution-snapshot.md#task-execution-snapshot20261010-0350) | ABI1.4 专项冻结；安全历史读取与全 pool 物理发布门禁 |
| Task → Scheduler | [task/execution-snapshot@20261028-1611](../task/execution-snapshot.md#task-execution-snapshot20261028-1611) | Freeze one saved check for an authorized Trace selection |
| Task → Scheduler | [task/lifecycle@20260823-0234](../task/lifecycle.md#task-lifecycle20260823-0234) | Task 使用明确状态机和非抢占式执行 |
| Task → Scheduler | [task/retention@20260823-0234](../task/retention.md#task-retention20260823-0234) | Master 分批清理超过保留期的终态 Task |
<!-- adr-related:end -->
