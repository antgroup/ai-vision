---
object_key: task-stats
topic: query-contract
title: Task Stats 查询合同
---

# Task Stats 查询合同

## task-stats-query-contract@20260823-0234

**统一查询成功终态快照并稳定去重**

```yaml
record_id: task-stats/query-contract@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - http-api
  - security
canonical_doc: docs/api.md
```

### 背景

同一 Task 可能有多个 attempt 和重报版本，同一业务窗口也可能产生重复成功 Task。

### 决策

- `diagnosis-stats` 只查询 `name=task_stats` 且 status=succeeded 的快照。
- 同一 Task 先选最大 attempt、同 attempt 最新上报；同一 Job/scope/owner/业务 period 再选最新成功 Task。
- 时间范围按业务 period 的半开区间过滤，默认最近 6 小时、最大 7 天。
- 可选 owner 精确路由 owner 快照；无匹配返回空，不回退 global。

### 保持不变

接口不提供从 DiagnosisRecord 或 OB Stats 的兜底，也不负责 owner 授权。

### 影响与风险

去重保留最新成功运行，重复调度不会在趋势中重复展示；过滤缺失可能暴露全局结果。

### 验证

由 diagnosis-stats 路由、旧版 Trace 查询源读取、attempt/period 去重和 owner 测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/api.md` 核对为已实施行为。

## task-stats-query-contract@20261009-2321

**保留趋势查询并提供精确持久 Task 统计点**

```yaml
record_id: task-stats/query-contract@20261009-2321
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T23:21+08:00
decided_at: 2026-10-09T23:21+08:00
implemented_at: 2026-10-09T23:21+08:00
supersedes:
  - task-stats/query-contract@20260823-0234
affected_objects:
  - http-api
  - security
canonical_doc: docs/api.md
```

### 背景

完成的固定选择 Task 需要读取自己的持久统计点。Job 默认时间窗口及业务 period 去重不能代替
显式 Task 身份，空响应缓存也不能阻挡刚完成的异步上报。

### 决策

只查询 name=task_stats 且 status=succeeded。先按 Task/scope/owner 选最大 attempt，再选该 attempt
最新上报；同一 Job/scope/owner/业务 period 取最新成功 Task。owner 精确路由 owner 快照，无匹配
返回空，不回退 global。

省略 task_uuid：继续按业务 period 半开区间过滤，默认 6 小时、最大 7 天。指定 task_uuid：先核对
持久 Task 属于请求 Job，在业务 period 去重前过滤此 Task，忽略时间窗口；不会生成虚构统计点。
代理要求单个 Job/Task/Agent，校验当前 Space/Agent/Task 关联；Quick 要求收据本人和当前 EXECUTE，
普通业务历史保持 READ。授权先于缓存；精确点空响应不缓存，已有空缓存不命中。非空点仍可缓存。

### 保持不变

categories/tasks 响应、冻结定义解释、保守评分、默认趋势缓存不变；不从 Task.stats 或 Records
兜底重算质量，AL 仍信任平台身份并由 Django 承担终端用户 owner 授权。

### 影响与风险

Task 已清理或不属于该 Job 时精确查询返回 not found。异步尚未落盘时空结果可重试，不能伪称缺失
的统计为零分。历史趋势不受新的精确点查询影响。

### 备选方案

扩大默认趋势窗口仍无法保证读取指定 Task；客户端重算会偏离冻结统计合同，均不采用。

### 验证

生产 Store writer/reader、旧时间点与同 period 竞争点、Task/Job 关联、跨 Scope/权限、空缓存到可见点
以及原趋势窗口测试；真实持久库由隔离环境验证。

### 实施说明

依据本轮明确批准的 additive task_uuid 合同实施，不修改 Core 定义或 executor ABI。
