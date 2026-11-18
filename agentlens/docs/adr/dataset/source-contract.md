---
object_key: dataset
topic: source-contract
title: Dataset 数据源合同
---

# Dataset 数据源合同

## dataset-source-contract@20260916-1130

**诊断数据源统一采用 SigNoz**

```yaml
record_id: dataset/source-contract@20260916-1130
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-09-16T11:30+08:00
decided_at: 2026-09-16T11:30+08:00
implemented_at: 2026-09-16T11:30+08:00
supersedes: []
affected_objects:
  - task-runner
canonical_doc: docs/configuration.md
```

### 背景

现役读取器实际访问 SigNoz OceanBase 遥测表。旧类型名与空连接配置会误导调用方；Langfuse 保持平台观测能力，不纳入本轮诊断。

### 决策

数据集类型仅为 `signoz`。`TraceQuerySpec` 定义 table、where、limit、offset、range 及可信空间、用户范围。删除旧连接参数和离线数据源分支。迁移只转换实际指向 SigNoz 表且无旧后端连接或 Selector 依赖的数据集；任何退役依赖阻止整批迁移并列出 UUID。

### 保持不变

保留 UUID、任务引用、时间窗口、过滤条件、运行时列转换、算法、历史记录和空间隔离。

### 影响与风险

前端、Django、AgentLens 同步切换；回滚需恢复备份与原构建。Langfuse 配置不参与退役预检。

### 验证

数据集路由和迁移测试；数据库副本与空库升级；SigNoz 端到端回归。

### 实施说明

依据本轮用户批准的 SigNoz / Langfuse 裁剪方案实施。历史记录保留原文，当前行为以本记录及对应文档为准。
