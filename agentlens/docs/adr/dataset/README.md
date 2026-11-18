---
object_key: dataset
title: Dataset 决策档案
scope: Dataset definitions and diagnostic target selection
---

# Dataset 决策档案

数据集定义与诊断目标选择。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [diagnosis-input-purpose](diagnosis-input-purpose.md) | [dataset/diagnosis-input-purpose@20261010-0350](diagnosis-input-purpose.md#dataset-diagnosis-input-purpose20261010-0350) | [dataset/diagnosis-input-purpose@20261009-2240](diagnosis-input-purpose.md#dataset-diagnosis-input-purpose20261009-2240) |
| [source-contract](source-contract.md) | [dataset/source-contract@20260916-1130](source-contract.md#dataset-source-contract20260916-1130) | [dataset/source-contract@20260916-1130](source-contract.md#dataset-source-contract20260916-1130) |
| [target-selection](target-selection.md) | [dataset/target-selection@20261009-2200](target-selection.md#dataset-target-selection20261009-2200) | [dataset/target-selection@20261009-2200](target-selection.md#dataset-target-selection20261009-2200) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Executor → Dataset | [executor/pre-evaluation@20260823-0234](../executor/pre-evaluation.md#executor-pre-evaluation20260823-0234) | pre-evaluate 只决定轻量终结或继续完整评估 |
| Executor → Dataset | [executor/pre-evaluation@20260823-1933](../executor/pre-evaluation.md#executor-pre-evaluation20260823-1933) | pre-evaluate 以 DiagnosisResult 返回轻量终态 |
| Executor → Dataset | [executor/pre-evaluation@20260902-1729](../executor/pre-evaluation.md#executor-pre-evaluation20260902-1729) | pre-evaluate 支持诊断器局部跳过 |
| Job → Dataset | [job/quick-request-receipt@20261009-2200](../job/quick-request-receipt.md#job-quick-request-receipt20261009-2200) | 可信 quick Job 永久保留原 Task 身份 |
| Job → Dataset | [job/retirement-migration@20261010-0350](../job/retirement-migration.md#job-retirement-migration20261010-0350) | 退役资源迁移遵守授权、排空与回读边界 |
| Job → Dataset | [job/transaction-publication@20261009-2128](../job/transaction-publication.md#job-transaction-publication20261009-2128) | Job 与 owned 资源发布依赖数据库真实事务模式 |
| Resource Sharing → Dataset | [resource-sharing/discovery@20260823-0234](../resource-sharing/discovery.md#resource-sharing-discovery20260823-0234) | 共享 Space 只扩展显式列表发现范围 |
| Resource Sharing → Dataset | [resource-sharing/usage-policy@20260823-0234](../resource-sharing/usage-policy.md#resource-sharing-usage-policy20260823-0234) | 共享资源必须在同 Scope 合同下使用 |
| Scope → Dataset | [scope/reference-consistency@20260823-0234](../scope/reference-consistency.md#scope-reference-consistency20260823-0234) | Job 只绑定完全相同 Scope 的资源 |
| Scope → Dataset | [scope/resource-identity@20260823-0234](../scope/resource-identity.md#scope-resource-identity20260823-0234) | 资源以精确 Scope 和业务 UUID 标识 |
| Security → Dataset | [security/authorization-boundary@20261010-0350](../security/authorization-boundary.md#security-authorization-boundary20261010-0350) | 保持 Django 最终用户授权；专项与 owned CLI 显式授权 |
| Trace Retrieval → Dataset | [trace-retrieval/candidate-hydration@20260823-0234](../trace-retrieval/candidate-hydration.md#trace-retrieval-candidate-hydration20260823-0234) | 按 pre-evaluate 候选并集补拉完整 Trace |
| Trace Retrieval → Dataset | [trace-retrieval/candidate-hydration@20261028-1728](../trace-retrieval/candidate-hydration.md#trace-retrieval-candidate-hydration20261028-1728) | Limit diagnosis candidates by distinct Trace count before reading lightweight fields |
| Trace Retrieval → Dataset | [trace-retrieval/query-window@20260823-0234](../trace-retrieval/query-window.md#trace-retrieval-query-window20260823-0234) | 在读取前固定数据源查询窗口与业务时间 |
| Diagnoser ↔ Dataset | [diagnoser/specialist-material-policy@20261010-0350](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261010-0350) | 五项可选专项的有限政策、真实材料与裁决范围 |
| Diagnoser ↔ Dataset | [diagnoser/specialist-material-policy@20261117-1152](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261117-1152) | Specialist configuration, evidence, and seven executable checks |
| Job ↔ Dataset | [job/quick-request-receipt@20261009-2200](../job/quick-request-receipt.md#job-quick-request-receipt20261009-2200) | 可信 quick Job 永久保留原 Task 身份 |
| Security ↔ Dataset | [security/authorization-boundary@20261010-0350](../security/authorization-boundary.md#security-authorization-boundary20261010-0350) | 保持 Django 最终用户授权；专项与 owned CLI 显式授权 |
| Task Stats ↔ Dataset | [task-stats/business-time@20261009-2240](../task-stats/business-time.md#task-stats-business-time20261009-2240) | 按选择模式固定统计业务时间 |
| Task ↔ Dataset | [task/execution-snapshot@20261009-1945](../task/execution-snapshot.md#task-execution-snapshot20261009-1945) | 创建 Task 时冻结有效执行定义与语义版本：通用 Core 与可信有界执行 |
<!-- adr-related:end -->
