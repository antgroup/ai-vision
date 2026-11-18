---
object_key: trace-retrieval
topic: detail-replay
title: Trace 详情回放
---

# Trace 详情回放

## trace-retrieval-detail-replay@20260823-0234

**通过注入的查询源回放托管数仓轨迹**

```yaml
record_id: trace-retrieval/detail-replay@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - diagnosis-record
  - http-api
  - security
canonical_doc: docs/api.md
```

### 背景

托管离线数仓任务的记录详情需要重建 Trace。旧版 Trace 查询源的连接和查询细节由服务端管理。

### 决策

- Trace Projection 接口接收 `trace_id`、`job_uuid` 和 `center_time`，由 Job 定位 Dataset；
  DiagnosisRecord 详情接口本身不内联回放 Trace。
- 托管离线数仓 Dataset 的旧版查询源回放配置由 AI Vision 服务端解析并通过隐藏参数注入。AgentLens 按
  target 读取 source spans，并按 Dataset `runtime_type` 重建 Projection。
- 详情回放不套用 Dataset `query.where`；该条件用于批量选样，不保证匹配 Trace 内全部节点。
- 回放失败不修改已保存的 DiagnosisRecord；Trace Projection 接口按原因返回 404 或 422。

### 保持不变

普通旧版 Trace 数据集和非托管来源继续使用各自既有详情读取路径。旧版 Trace 查询源的连接仍由 AgentLens 管理。

### 影响与风险

服务端回放将凭证和查询保留在同一边界内。部署环境需要配置对应的旧版查询源读取权限。

### 验证

由离线数仓详情来源、DiagnosisRecord 详情路由及 AI Vision 代理测试验证端到端回放。

### 实施说明

本记录已按当前代码、测试和 `docs/api.md` 核对为已实施行为。

## trace-retrieval-detail-replay@20260916-1130

**通过 Job 保存的 SigNoz 数据集回放详情**

```yaml
record_id: trace-retrieval/detail-replay@20260916-1130
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-09-16T11:30+08:00
decided_at: 2026-09-16T11:30+08:00
implemented_at: 2026-09-16T11:30+08:00
supersedes:
  - trace-retrieval/detail-replay@20260823-0234
affected_objects:
  - task-runner
canonical_doc: docs/api.md
```

### 背景

退役离线数据源后，不再需要由浏览器或代理注入另一套回放连接。

### 决策

接口接收 trace_id、job_uuid、center_time，由 Job 定位保存的数据集并读取 SigNoz。Django 验证 Job 属于当前空间，移除客户端 replay_dataset_spec。回放保留空间范围，但不套用批量选样 where，以获取完整 Trace；按数据集 runtime 重建 Projection。失败返回明确错误，不修改历史记录。

### 保持不变

DiagnosisRecord 详情不内联大体积 Trace；现有投影、算法及敏感信息规则不变。

### 影响与风险

需要现有 SigNoz 读配置；原离线任务在迁移预检中阻止升级。

### 验证

Trace Projection 路由、源读取和跨空间代理拒绝测试。

### 实施说明

依据本轮用户批准的 SigNoz / Langfuse 裁剪方案实施。历史记录保留原文，当前行为以本记录及对应文档为准。
