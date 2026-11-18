---
object_key: task-stats
topic: business-time
title: Task Stats 业务时间
---

# Task Stats 业务时间

## task-stats-business-time@20260823-0234

**统计时间使用诊断数据窗口而非执行时间**

```yaml
record_id: task-stats/business-time@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - trace-retrieval
  - task-runner
canonical_doc: docs/architecture.md
```

### 背景

Task 实际执行可能延迟或重试，按写入时间绘制趋势会把诊断对象归入错误时间窗。

### 决策

- 旧版 Trace 查询源的 TaskStats 使用数据查询窗口中点作为 `period/start_time_unix_nano`。
- 离线数仓使用目标 `ds/hh` 分区开始后 30 分钟作为业务时间。
- `end_time_unix_nano` 保存实际上报时间，只用于版本选择和观测延迟。
- 重复业务 period 的成功 Task按上报时间选择最新版本。

### 保持不变

应用 Python 时间仍使用部署环境本地时间 `datetime.now()`；本主题不引入 UTC 业务口径。

### 影响与风险

业务时间使趋势与被诊断数据对齐，但和 Task 创建、开始、完成时间可能不同。

### 验证

由窗口解析、TaskStats 上报和统计时间过滤/去重测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/architecture.md` 核对为已实施行为。

## task-stats-business-time@20261009-2240

**按选择模式固定统计业务时间**

```yaml
record_id: task-stats/business-time@20261009-2240
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T22:40+08:00
decided_at: 2026-10-09T22:40+08:00
implemented_at: 2026-10-09T22:40+08:00
supersedes:
  - task-stats/business-time@20260823-0234
related_records:
  - dataset/target-selection@20261009-2200
affected_objects:
  - task-runner
canonical_doc: docs/scheduling.md
```

### 背景

可信固定 Trace 集合没有查询时间窗；旧 Runner 在读取前解析 quick 的 `0s` offset 导致 FAILED。
为了构造窗口而换成 `1h` 会错误引入未授权的采样条件。

### 决策

SigNoz legacy window TaskStats 的 period 使用冻结查询窗口中点，已退役离线数仓的历史 period 保留原
分区开始后 30 分钟口径。可信 explicit selection 完全跳过相对窗口解析与 cutoff，业务时间使用 Task
持久 scheduled_at，不使用实时读取、实际执行或写入时间，重试不漂移。

end_time_unix_nano 仍为实际上报时间，用于版本选择与观测延迟；重复业务 period 的成功 Task 按上报
时间选最新版。DiagnosisRecord 优先保留 Trace 自身时间，Task 业务时间只作原有缺失回退。

### 保持不变

本地 Python 时间口径不变；固定 Trace 仍读取所有选中 ID 的完整授权 spans，legacy 窗口中点不变。

### 影响与风险

explicit 统计按请求的逻辑调度时间定位；不能把该时间解释为全部输入 Trace 的生成时间。

### 备选方案

伪造相对窗口会重新限制旧 Trace；按当前执行时间定位会令重试漂移，均不采用。

### 验证

真实 Runner 方法覆盖已冻结 `0s`、2000 年旧 Trace、完整 material 与 partial source；legacy 窗口测试。
实际 owned 平台终态与结果验证由控制器运行。

### 实施说明

依据本轮实际 StageB 阻塞修复授权实施；Core ABI 与历史 Task 快照无需重写。
