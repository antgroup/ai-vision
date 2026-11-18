---
object_key: task
title: Task 决策档案
scope: Lifecycle, attempt ownership, and retention of one task execution
---

# Task 决策档案

一次任务执行的生命周期、attempt 所有权和保留。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [attempt-ownership](attempt-ownership.md) | [task/attempt-ownership@20261009-1945](attempt-ownership.md#task-attempt-ownership20261009-1945) | [task/attempt-ownership@20261009-1945](attempt-ownership.md#task-attempt-ownership20261009-1945) |
| [execution-snapshot](execution-snapshot.md) | [task/execution-snapshot@20261103-1505](execution-snapshot.md#task-execution-snapshot20261103-1505) | [task/execution-snapshot@20261103-1505](execution-snapshot.md#task-execution-snapshot20261103-1505) |
| [judge-request-budget](judge-request-budget.md) | [task/judge-request-budget@20261028-1611](judge-request-budget.md#task-judge-request-budget20261028-1611) | [task/judge-request-budget@20261028-1611](judge-request-budget.md#task-judge-request-budget20261028-1611) |
| [lifecycle](lifecycle.md) | [task/lifecycle@20260823-0234](lifecycle.md#task-lifecycle20260823-0234) | [task/lifecycle@20260823-0234](lifecycle.md#task-lifecycle20260823-0234) |
| [retention](retention.md) | [task/retention@20260823-0234](retention.md#task-retention20260823-0234) | [task/retention@20260823-0234](retention.md#task-retention20260823-0234) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Diagnoser → Task | [diagnoser/builtin-definition-source@20261009-1515](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261009-1515) | 八 Rule 的受管身份、版本与有限用户策略 |
| Diagnoser → Task | [diagnoser/builtin-definition-source@20261009-1945](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261009-1945) | 内置诊断器定义来源：通用 Core 与可信有界执行 |
| Diagnoser → Task | [diagnoser/builtin-definition-source@20261010-0350](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261010-0350) | 保留通用定义源并建立独立专项定义 |
| Diagnoser → Task | [diagnoser/builtin-definition-source@20261117-1340](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261117-1340) | Provision four Core presets on demand in personal Spaces |
| Diagnoser → Task | [diagnoser/core-material-policy@20261009-1945](../diagnoser/core-material-policy.md#diagnoser-core-material-policy20261009-1945) | 通用 Core 材料与裁决范围：通用 Core 与可信有界执行 |
| Diagnoser → Task | [diagnoser/model-reference@20261009-1945](../diagnoser/model-reference.md#diagnoser-model-reference20261009-1945) | Diagnoser 模型引用：通用 Core 与可信有界执行 |
| Diagnoser → Task | [diagnoser/specialist-material-policy@20261010-0350](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261010-0350) | 五项可选专项的有限政策、真实材料与裁决范围 |
| Diagnoser → Task | [diagnoser/specialist-material-policy@20261117-1152](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261117-1152) | Specialist configuration, evidence, and seven executable checks |
| Diagnoser → Task | [diagnoser/type-and-mode@20261117-1056](../diagnoser/type-and-mode.md#diagnoser-type-and-mode20261117-1056) | Preserve builtin Rule execution when creating a custom copy |
| Executor → Task | [executor/sandbox-backend@20261103-1505](../executor/sandbox-backend.md#executor-sandbox-backend20261103-1505) | Enhanced Python runtime on the existing sandbox backend |
| Job → Task | [job/quick-request-receipt@20261009-2200](../job/quick-request-receipt.md#job-quick-request-receipt20261009-2200) | 可信 quick Job 永久保留原 Task 身份 |
| Job → Task | [job/retirement-migration@20261010-0350](../job/retirement-migration.md#job-retirement-migration20261010-0350) | 退役资源迁移遵守授权、排空与回读边界 |
| Job → Task | [job/transaction-publication@20261009-2128](../job/transaction-publication.md#job-transaction-publication20261009-2128) | Job 与 owned 资源发布依赖数据库真实事务模式 |
| LLM Judge → Task | [llm-judge/request-attempt-hooks@20261009-1945](../llm-judge/request-attempt-hooks.md#llm-judge-request-attempt-hooks20261009-1945) | LLM Judge 请求尝试 hooks：通用 Core 与可信有界执行 |
| Sandbox Connection → Task | [sandbox-connection/resource-contract@20261102-1700](../sandbox-connection/resource-contract.md#sandbox-connection-resource-contract20261102-1700) | Sandbox Connection contract |
| Sandbox Connection → Task | [sandbox-connection/resource-contract@20261103-1242](../sandbox-connection/resource-contract.md#sandbox-connection-resource-contract20261103-1242) | Automate deployment-host enrollment while retaining explicit verified activation |
| Sandbox Execution → Task | [sandbox-execution/lifecycle@20261102-1700](../sandbox-execution/lifecycle.md#sandbox-execution-lifecycle20261102-1700) | Sandbox Execution contract |
| Sandbox Execution → Task | [sandbox-execution/lifecycle@20261102-1833](../sandbox-execution/lifecycle.md#sandbox-execution-lifecycle20261102-1833) | Clarify OpenShell workspace ownership and delete preconditions |
| Sandbox Execution → Task | [sandbox-execution/lifecycle@20261103-1505](../sandbox-execution/lifecycle.md#sandbox-execution-lifecycle20261103-1505) | Qualify enhanced image identity before admission |
| Scheduler → Task | [scheduler/leader-election@20260823-0234](../scheduler/leader-election.md#scheduler-leader-election20260823-0234) | 以数据库租约和递增 fenced token 选主 |
| Scheduler → Task | [scheduler/task-dispatch@20260823-0234](../scheduler/task-dispatch.md#scheduler-task-dispatch20260823-0234) | Master 扫描到期 Task 并以 CAS 派发 |
| Scheduler → Task | [scheduler/task-dispatch@20260909-1639](../scheduler/task-dispatch.md#scheduler-task-dispatch20260909-1639) | 以已分配 Task 负载和逐次容量检查进行派单 |
| Scheduler → Task | [scheduler/task-recovery@20260823-0234](../scheduler/task-recovery.md#scheduler-task-recovery20260823-0234) | 按超时、心跳和 attempt 上限恢复异常 Task |
| Security → Task | [security/authorization-boundary@20261010-0350](../security/authorization-boundary.md#security-authorization-boundary20261010-0350) | 保持 Django 最终用户授权；专项与 owned CLI 显式授权 |
| Security → Task | [security/private-check-policy@20261010-0350](../security/private-check-policy.md#security-private-check-policy20261010-0350) | 私密政策内部执行冻结；公共 DTO 与新 Record 不回显 marker |
| Task Runner → Task | [task-runner/execution-result@20260823-0234](../task-runner/execution-result.md#task-runner-execution-result20260823-0234) | 终态 CAS 成功后才发布权威执行结果 |
| Task Stats → Task | [task-stats/persistence@20260823-0234](../task-stats/persistence.md#task-stats-persistence20260823-0234) | 只持久化赢得终态 fence 的完整统计快照 |
| Dataset ↔ Task | [dataset/target-selection@20261009-2200](../dataset/target-selection.md#dataset-target-selection20261009-2200) | 保留 Selector 合同并增加可信固定 Trace 选择 |
| Diagnoser ↔ Task | [diagnoser/builtin-definition-source@20261009-1515](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261009-1515) | 八 Rule 的受管身份、版本与有限用户策略 |
| Diagnoser ↔ Task | [diagnoser/builtin-definition-source@20261010-0350](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261010-0350) | 保留通用定义源并建立独立专项定义 |
| Diagnoser ↔ Task | [diagnoser/builtin-definition-source@20261117-1340](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261117-1340) | Provision four Core presets on demand in personal Spaces |
| Diagnoser ↔ Task | [diagnoser/specialist-material-policy@20261010-0350](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261010-0350) | 五项可选专项的有限政策、真实材料与裁决范围 |
| Diagnoser ↔ Task | [diagnoser/specialist-material-policy@20261117-1152](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261117-1152) | Specialist configuration, evidence, and seven executable checks |
| Diagnoser ↔ Task | [diagnoser/type-and-mode@20261117-1056](../diagnoser/type-and-mode.md#diagnoser-type-and-mode20261117-1056) | Preserve builtin Rule execution when creating a custom copy |
| Executor ↔ Task | [executor/sandbox-backend@20261103-1505](../executor/sandbox-backend.md#executor-sandbox-backend20261103-1505) | Enhanced Python runtime on the existing sandbox backend |
| Job ↔ Task | [job/quick-request-receipt@20261009-2200](../job/quick-request-receipt.md#job-quick-request-receipt20261009-2200) | 可信 quick Job 永久保留原 Task 身份 |
| Job ↔ Task | [job/retirement-migration@20261010-0350](../job/retirement-migration.md#job-retirement-migration20261010-0350) | 退役资源迁移遵守授权、排空与回读边界 |
| Projection ↔ Task | [projection/projector-architecture@20261010-0350](../projection/projector-architecture.md#projection-projector-architecture20261010-0350) | 保留现役通用投影与原始 CUSTOM 路径 |
| Projection ↔ Task | [projection/projector-architecture@20261010-1409](../projection/projector-architecture.md#projection-projector-architecture20261010-1409) | 显式接通 Langfuse SDK runtime 与既有 Framework 事实映射 |
| Sandbox Execution ↔ Task | [sandbox-execution/lifecycle@20261103-1505](../sandbox-execution/lifecycle.md#sandbox-execution-lifecycle20261103-1505) | Qualify enhanced image identity before admission |
| Security ↔ Task | [security/private-check-policy@20261010-0350](../security/private-check-policy.md#security-private-check-policy20261010-0350) | 私密政策内部执行冻结；公共 DTO 与新 Record 不回显 marker |
| Task Runner ↔ Task | [task-runner/execution-pipeline@20261009-1800](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20261009-1800) | 从 Task 快照执行并维持结果、跳过与终态汇流 |
| Task Stats ↔ Task | [task-stats/aggregation-model@20261009-1800](../task-stats/aggregation-model.md#task-stats-aggregation-model20261009-1800) | 以 TaskStats v3 冻结定义和保守质量策略解释历史 |
<!-- adr-related:end -->
