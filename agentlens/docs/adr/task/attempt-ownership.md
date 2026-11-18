---
object_key: task
topic: attempt-ownership
title: Task Attempt 所有权
---

# Task Attempt 所有权

## task-attempt-ownership@20260823-0234

**以 worker、attempt 和状态 fence 决定终态所有权**

```yaml
record_id: task/attempt-ownership@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - scheduler
  - task-runner
  - task-stats
  - diagnosis-record
canonical_doc: docs/architecture.md
```

### 背景

Rescue 可启动新 attempt，而旧进程可能仍在运行；终态和统计必须只有一个权威写入者。

### 决策

- Worker 以 `worker_id + attempt + RUNNING` 条件 CAS 写 SUCCEEDED 或 FAILED，并在同一更新中写完整 OB Stats。
- 只有终态 CAS 成功的 attempt 向旧版遥测存储上报 TaskStats。stale attempt 丢弃内存统计。
- DiagnosisRecord 允许旧 attempt 写物理版本，查询按逻辑 UUID 选择最高 attempt、同 attempt 最新 period。
- Runner 执行中不查询 attempt 所有权，不尝试主动终止旧 attempt。

### 保持不变

Master fenced token 按 Worker 最近观察到的租约 token 拒绝更旧的远端执行请求，但不替代 Task
attempt fence。

### 影响与风险

明细允许重复物理写入换取非抢占执行；读取去重和终态 CAS共同提供逻辑一致性。

### 验证

由 Runner terminal CAS、Rescue 并发、TaskStats 上报和 DiagnosisRecord 去重测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/architecture.md` 核对为已实施行为。


## task-attempt-ownership@20261009-1945

**Task Attempt 所有权：通用 Core 与可信有界执行**

```yaml
record_id: task/attempt-ownership@20261009-1945
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T19:45+08:00
decided_at: 2026-10-09T19:45+08:00
implemented_at: 2026-10-09T19:45+08:00
supersedes:
  - task/attempt-ownership@20260823-0234
affected_objects:
  - scheduler
  - task-runner
  - task-stats
  - diagnosis-record
  - llm-judge
canonical_doc: docs/architecture.md
```

### 背景

非抢占执行和唯一终态统计保留；bounded Judge 的新请求必须在持久 claim 时验证当前 attempt fence。

### 决策

- worker + attempt + RUNNING fence 的终态 CAS 与同事务完整 OB Stats 保持；只有 CAS 成功 attempt 上报 TaskStats，stale attempt 丢弃本地统计。
- DiagnosisRecord 允许旧 attempt 的物理版本，按逻辑 UUID/最高 attempt/同 attempt 最新 period 读取。
- 仅 bounded Judge request claim 在独立短事务锁读 Task，核对 RUNNING/worker/attempt 后 durable commit，再允许 HTTP。旧 attempt、已取消或已终态 Task 不取得新 ticket。
- 不主动抢占或终止旧进程。reserve commit 后仍有 rescue 窗口，旧 worker 已持 ticket 的请求可发送/完成，但总保守 cap 不超额。
- 旧 attempt 的真实响应可按 request ticket 幂等补账，不改状态/stats/snapshot，不因 owner 变化丢弃已发生的使用量。finish 失败不重发 HTTP。legacy 未启用预算执行保留非查询 ownership 合同。
- Master fenced token 仍以租约拒绝旧派单，不替代 Task attempt fence。

### 保持不变

T1 质量/信号分离、TaskStats total/good/bad/failed/skipped、不可变历史来源、CUSTOM/未涉及 legacy 合同，以及原始 Trace/runtime 身份保持。

### 影响与风险

ABI 1.3.0 发布前需排空旧可执行 Task 或保留兼容 Worker。reservation 是保守上界，不是账单或 provider receipt；材料和模型判断覆盖有明确限制。

### 验证

使用独立反例的 genuine RED→GREEN、mock transport 与事务边界离线测试。双引擎锁/并发、平台/API/GUI 与真实模型人工校准由主控另验收，本记录不宣称这些已通过。

### 实施说明

源实现和离线合同已实现；Task 4 报告记录精确检查、风险及待补真实 gate。
