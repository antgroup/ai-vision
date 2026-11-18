---
object_key: security
title: Security 决策档案
scope: Service authentication, authorization responsibilities, and sensitive-information boundaries
---

# Security 决策档案

服务鉴权、授权责任和敏感信息边界。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [authorization-boundary](authorization-boundary.md) | [security/authorization-boundary@20261010-0350](authorization-boundary.md#security-authorization-boundary20261010-0350) | [security/authorization-boundary@20260823-0234](authorization-boundary.md#security-authorization-boundary20260823-0234) |
| [ingest-credentials](ingest-credentials.md) | [security/ingest-credentials@20260915-1750](ingest-credentials.md#security-ingest-credentials20260915-1750) | [security/ingest-credentials@20260915-1750](ingest-credentials.md#security-ingest-credentials20260915-1750) |
| [private-check-policy](private-check-policy.md) | [security/private-check-policy@20261010-0350](private-check-policy.md#security-private-check-policy20261010-0350) | - |
| [service-authentication](service-authentication.md) | [security/service-authentication@20261020-1127](service-authentication.md#security-service-authentication20261020-1127) | [security/service-authentication@20261020-1127](service-authentication.md#security-service-authentication20261020-1127) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Diagnosis Record → Security | [diagnosis-record/query-contract@20260823-0234](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20260823-0234) | 按逻辑记录去重并统一列表与详情职责 |
| Diagnosis Record → Security | [diagnosis-record/query-contract@20260823-1933](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20260823-1933) | 以派生列表属性和最小 Metric body 查询记录 |
| Diagnosis Record → Security | [diagnosis-record/query-contract@20260823-2329](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20260823-2329) | 临时兼容旧 body 的最小 Metric 查询合同 |
| Diagnosis Record → Security | [diagnosis-record/query-contract@20261012-2310](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20261012-2310) | Task 保留期后读取 Record 自身冻结定义 |
| Diagnosis Record → Security | [diagnosis-record/query-contract@20261028-1518](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20261028-1518) | Read an exact Task batch without an implicit source-time window |
| Metric Result → Security | [metric-result/evidence@20260823-0234](../metric-result/evidence.md#metric-result-evidence20260823-0234) | 以结构化 evidence 保存可追溯诊断依据 |
| Metric Result → Security | [metric-result/evidence@20260823-1933](../metric-result/evidence.md#metric-result-evidence20260823-1933) | 分离 Metric 判定证据与 Execution 执行观测 |
| Model Connection → Security | [model-connection/credential-handling@20260823-0234](../model-connection/credential-handling.md#model-connection-credential-handling20260823-0234) | 模型凭证作为只写 API 字段并限制传播 |
| Model Connection → Security | [model-connection/credential-handling@20261012-2310](../model-connection/credential-handling.md#model-connection-credential-handling20261012-2310) | 冻结模型身份限定运行时凭证绑定 |
| Model Connection → Security | [model-connection/credential-handling@20261021-0005](../model-connection/credential-handling.md#model-connection-credential-handling20261021-0005) | Bind saved credentials to their connection endpoint during setup |
| Model Connection → Security | [model-connection/diagnostic-validation@20261021-0500](../model-connection/diagnostic-validation.md#model-connection-diagnostic-validation20261021-0500) | Verify two bounded canonical text cases before choosing a diagnostic model |
| Model Connection → Security | [model-connection/model-discovery@20261021-0005](../model-connection/model-discovery.md#model-connection-model-discovery20261021-0005) | Discover models from verified provider catalogs before saving a connection |
| Projection → Security | [projection/image-attachments@20261010-0350](../projection/image-attachments.md#projection-image-attachments20261010-0350) | 图片材料必须有明确受支持的来源映射 |
| Sandbox Connection → Security | [sandbox-connection/resource-contract@20261102-1700](../sandbox-connection/resource-contract.md#sandbox-connection-resource-contract20261102-1700) | Sandbox Connection contract |
| Sandbox Connection → Security | [sandbox-connection/resource-contract@20261103-1242](../sandbox-connection/resource-contract.md#sandbox-connection-resource-contract20261103-1242) | Automate deployment-host enrollment while retaining explicit verified activation |
| Sandbox Execution → Security | [sandbox-execution/lifecycle@20261102-1700](../sandbox-execution/lifecycle.md#sandbox-execution-lifecycle20261102-1700) | Sandbox Execution contract |
| Sandbox Execution → Security | [sandbox-execution/lifecycle@20261102-1833](../sandbox-execution/lifecycle.md#sandbox-execution-lifecycle20261102-1833) | Clarify OpenShell workspace ownership and delete preconditions |
| Task Stats → Security | [task-stats/query-contract@20260823-0234](../task-stats/query-contract.md#task-stats-query-contract20260823-0234) | 统一查询成功终态快照并稳定去重 |
| Task Stats → Security | [task-stats/query-contract@20261009-2321](../task-stats/query-contract.md#task-stats-query-contract20261009-2321) | 保留趋势查询并提供精确持久 Task 统计点 |
| Trace Retrieval → Security | [trace-retrieval/detail-replay@20260823-0234](../trace-retrieval/detail-replay.md#trace-retrieval-detail-replay20260823-0234) | 通过注入的查询源回放托管数仓轨迹 |
| Dataset ↔ Security | [dataset/diagnosis-input-purpose@20261010-0350](../dataset/diagnosis-input-purpose.md#dataset-diagnosis-input-purpose20261010-0350) | 中性 AUTH scope exact Trace readonly lift；保留 whole-output 边界 |
| Diagnoser ↔ Security | [diagnoser/specialist-material-policy@20261010-0350](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261010-0350) | 五项可选专项的有限政策、真实材料与裁决范围 |
| Diagnoser ↔ Security | [diagnoser/specialist-material-policy@20261117-1152](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261117-1152) | Specialist configuration, evidence, and seven executable checks |
| Job ↔ Security | [job/quick-request-receipt@20261009-2200](../job/quick-request-receipt.md#job-quick-request-receipt20261009-2200) | 可信 quick Job 永久保留原 Task 身份 |
| Job ↔ Security | [job/retirement-migration@20261010-0350](../job/retirement-migration.md#job-retirement-migration20261010-0350) | 退役资源迁移遵守授权、排空与回读边界 |
| Metric Result ↔ Security | [metric-result/evidence@20260823-1933](../metric-result/evidence.md#metric-result-evidence20260823-1933) | 分离 Metric 判定证据与 Execution 执行观测 |
| Resource Sharing ↔ Security | [resource-sharing/discovery@20260823-0234](../resource-sharing/discovery.md#resource-sharing-discovery20260823-0234) | 共享 Space 只扩展显式列表发现范围 |
| Resource Sharing ↔ Security | [resource-sharing/usage-policy@20260823-0234](../resource-sharing/usage-policy.md#resource-sharing-usage-policy20260823-0234) | 共享资源必须在同 Scope 合同下使用 |
| Scope ↔ Security | [scope/reference-consistency@20260823-0234](../scope/reference-consistency.md#scope-reference-consistency20260823-0234) | Job 只绑定完全相同 Scope 的资源 |
| Task ↔ Security | [task/execution-snapshot@20261010-0350](../task/execution-snapshot.md#task-execution-snapshot20261010-0350) | ABI1.4 专项冻结；安全历史读取与全 pool 物理发布门禁 |
| Task ↔ Security | [task/execution-snapshot@20261028-1611](../task/execution-snapshot.md#task-execution-snapshot20261028-1611) | Freeze one saved check for an authorized Trace selection |
<!-- adr-related:end -->
