---
object_key: scheduler
topic: leader-election
title: Scheduler 选主
---

# Scheduler 选主

## scheduler-leader-election@20260823-0234

**以数据库租约和递增 fenced token 选主**

```yaml
record_id: scheduler/leader-election@20260823-0234
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

多实例部署只允许一个 Master 执行调度和恢复，同时必须拒绝过期 Master 的延迟请求。

### 决策

- 实例通过 `agentlens_leader_lease` 的数据库 CAS 获取或续约租约。
- 每次新任期递增 fenced token，Master 调度请求携带该 token。
- 每个实例的 Elector 周期性读取数据库租约 token；远端 `/internal/execute` 将请求 token 与本实例最近
  观察到的 token 比较并拒绝更小值。请求 token 不写入 Task，也不会反向更新 Elector。
- 租约失效后其他实例可接管；本实例在 Elector 的成功 tick 观察到角色变化时启动或停止 Master
  协程。

### 保持不变

Worker 心跳和 Task attempt fence 仍分别管理实例健康与单任务终态所有权。

### 影响与风险

数据库时间和租约参数影响切主延迟；续约 tick 连续失败时，旧实例在再次成功观察角色变化前可能仍保留
Master 协程。Worker 观察新 token 前也存在一个检查滞后窗口。token 检查不能终止已启动 Runner，
Task 终态仍依赖 attempt fence。

### 验证

由租约 Store、并发抢主、续约、过期请求和故障切主测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/scheduling.md` 核对为已实施行为。
