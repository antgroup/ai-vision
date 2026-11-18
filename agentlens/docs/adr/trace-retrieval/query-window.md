---
object_key: trace-retrieval
topic: query-window
title: Trace 查询窗口
---

# Trace 查询窗口

## trace-retrieval-query-window@20260823-0234

**在读取前固定数据源查询窗口与业务时间**

```yaml
record_id: trace-retrieval/query-window@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - dataset
  - task-runner
  - task-stats
canonical_doc: docs/scheduling.md
```

### 背景

数据读取窗口同时约束样本范围和统计业务时间，必须在执行前稳定下来。

### 决策

- 旧版 Trace 查询源根据 `scheduled_at` 和 Dataset offset/range 计算半开查询窗口，窗口中点作为业务时间。
- 离线数仓按逻辑调度时间和 `time_offset` 选择 `ds/hh` 分区，分区开始后 30 分钟作为业务时间。
- 窗口解析在读取前完成。解析失败使 Task 失败，旧版 Trace 查询源的 TaskStats 不生成业务时间。

### 保持不变

实际写入或上报时间不替代诊断对象业务时间，查询条件继续受 Dataset spec 约束。

### 影响与风险

窗口口径影响重复调度去重、统计趋势和详情查询；修改需要同步调度与统计合同。

### 验证

由窗口解析、旧版 Trace 查询源、离线数仓来源和 TaskStats 时间测试验证边界与失败路径。

### 实施说明

本记录已按当前代码、测试和 `docs/scheduling.md` 核对为已实施行为。

## trace-retrieval-query-window@20260916-1130

**保留 SigNoz 半开窗口与业务时间**

```yaml
record_id: trace-retrieval/query-window@20260916-1130
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-09-16T11:30+08:00
decided_at: 2026-09-16T11:30+08:00
implemented_at: 2026-09-16T11:30+08:00
supersedes:
  - trace-retrieval/query-window@20260823-0234
affected_objects:
  - task-runner
canonical_doc: docs/scheduling.md
```

### 背景

移除分区离线数据源后，诊断只保留现役 SigNoz 窗口口径。

### 决策

执行读取前，依据 scheduled_at 与 Dataset offset/range 固定半开区间。窗口中点为业务时间；解析失败使任务失败，不伪造统计时间。实时读取、重试和上报时间不能重算窗口。

### 保持不变

候选数量、任务调度、数据延迟、窗口边界和历史统计语义不变。

### 影响与风险

移除分区时间计算；已有数据集迁移保留原 offset/range。

### 验证

窗口解析、Runner 与 TaskStats 时间边界测试。

### 实施说明

依据本轮用户批准的 SigNoz / Langfuse 裁剪方案实施。历史记录保留原文，当前行为以本记录及对应文档为准。
