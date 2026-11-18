---
object_key: job
title: Job 决策档案
scope: Definitions of recurring or manual diagnostic jobs
---

# Job 决策档案

周期性或手动诊断任务定义。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [quick-request-receipt](quick-request-receipt.md) | [job/quick-request-receipt@20261009-2200](quick-request-receipt.md#job-quick-request-receipt20261009-2200) | [job/quick-request-receipt@20261009-2200](quick-request-receipt.md#job-quick-request-receipt20261009-2200) |
| [retirement-migration](retirement-migration.md) | [job/retirement-migration@20261010-0350](retirement-migration.md#job-retirement-migration20261010-0350) | - |
| [transaction-publication](transaction-publication.md) | [job/transaction-publication@20261009-2128](transaction-publication.md#job-transaction-publication20261009-2128) | [job/transaction-publication@20261009-2128](transaction-publication.md#job-transaction-publication20261009-2128) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Dataset → Job | [dataset/diagnosis-input-purpose@20261010-0350](../dataset/diagnosis-input-purpose.md#dataset-diagnosis-input-purpose20261010-0350) | 中性 AUTH scope exact Trace readonly lift；保留 whole-output 边界 |
| Diagnoser → Job | [diagnoser/builtin-definition-source@20261010-0350](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261010-0350) | 保留通用定义源并建立独立专项定义 |
| Diagnoser → Job | [diagnoser/builtin-definition-source@20261117-1340](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261117-1340) | Provision four Core presets on demand in personal Spaces |
| Diagnosis Record → Job | [diagnosis-record/persistence-policy@20260823-0234](../diagnosis-record/persistence-policy.md#diagnosis-record-persistence-policy20260823-0234) | 按 Job 策略稀疏保存 evaluate 明细 |
| Resource Sharing → Job | [resource-sharing/discovery@20260823-0234](../resource-sharing/discovery.md#resource-sharing-discovery20260823-0234) | 共享 Space 只扩展显式列表发现范围 |
| Resource Sharing → Job | [resource-sharing/usage-policy@20260823-0234](../resource-sharing/usage-policy.md#resource-sharing-usage-policy20260823-0234) | 共享资源必须在同 Scope 合同下使用 |
| Scope → Job | [scope/reference-consistency@20260823-0234](../scope/reference-consistency.md#scope-reference-consistency20260823-0234) | Job 只绑定完全相同 Scope 的资源 |
| Scope → Job | [scope/resource-identity@20260823-0234](../scope/resource-identity.md#scope-resource-identity20260823-0234) | 资源以精确 Scope 和业务 UUID 标识 |
| Security → Job | [security/authorization-boundary@20261010-0350](../security/authorization-boundary.md#security-authorization-boundary20261010-0350) | 保持 Django 最终用户授权；专项与 owned CLI 显式授权 |
| Task → Job | [task/execution-snapshot@20261010-0350](../task/execution-snapshot.md#task-execution-snapshot20261010-0350) | ABI1.4 专项冻结；安全历史读取与全 pool 物理发布门禁 |
| Task → Job | [task/execution-snapshot@20261028-1611](../task/execution-snapshot.md#task-execution-snapshot20261028-1611) | Freeze one saved check for an authorized Trace selection |
| Dataset ↔ Job | [dataset/diagnosis-input-purpose@20261010-0350](../dataset/diagnosis-input-purpose.md#dataset-diagnosis-input-purpose20261010-0350) | 中性 AUTH scope exact Trace readonly lift；保留 whole-output 边界 |
| Dataset ↔ Job | [dataset/target-selection@20261009-2200](../dataset/target-selection.md#dataset-target-selection20261009-2200) | 保留 Selector 合同并增加可信固定 Trace 选择 |
| Diagnoser ↔ Job | [diagnoser/builtin-definition-source@20261010-0350](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261010-0350) | 保留通用定义源并建立独立专项定义 |
| Diagnoser ↔ Job | [diagnoser/builtin-definition-source@20261117-1340](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261117-1340) | Provision four Core presets on demand in personal Spaces |
| Scope ↔ Job | [scope/reference-consistency@20260823-0234](../scope/reference-consistency.md#scope-reference-consistency20260823-0234) | Job 只绑定完全相同 Scope 的资源 |
| Security ↔ Job | [security/authorization-boundary@20260823-0234](../security/authorization-boundary.md#security-authorization-boundary20260823-0234) | AgentLens 不承担最终用户与 owner 授权 |
| Security ↔ Job | [security/authorization-boundary@20261010-0350](../security/authorization-boundary.md#security-authorization-boundary20261010-0350) | 保持 Django 最终用户授权；专项与 owned CLI 显式授权 |
| Task ↔ Job | [task/execution-snapshot@20261009-1945](../task/execution-snapshot.md#task-execution-snapshot20261009-1945) | 创建 Task 时冻结有效执行定义与语义版本：通用 Core 与可信有界执行 |
| Task ↔ Job | [task/execution-snapshot@20261010-0350](../task/execution-snapshot.md#task-execution-snapshot20261010-0350) | ABI1.4 专项冻结；安全历史读取与全 pool 物理发布门禁 |
| Task ↔ Job | [task/execution-snapshot@20261028-1611](../task/execution-snapshot.md#task-execution-snapshot20261028-1611) | Freeze one saved check for an authorized Trace selection |
<!-- adr-related:end -->
