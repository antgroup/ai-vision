---
object_key: scope
title: Scope 决策档案
scope: Resource identity and consistency of cross-resource references
---

# Scope 决策档案

资源身份和跨资源引用一致性。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [reference-consistency](reference-consistency.md) | [scope/reference-consistency@20260823-0234](reference-consistency.md#scope-reference-consistency20260823-0234) | [scope/reference-consistency@20260823-0234](reference-consistency.md#scope-reference-consistency20260823-0234) |
| [resource-identity](resource-identity.md) | [scope/resource-identity@20260823-0234](resource-identity.md#scope-resource-identity20260823-0234) | [scope/resource-identity@20260823-0234](resource-identity.md#scope-resource-identity20260823-0234) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Model Connection → Scope | [model-connection/resource-contract@20260823-0234](../model-connection/resource-contract.md#model-connection-resource-contract20260823-0234) | 模型连接是独立的 Scope 资源 |
| Resource Sharing → Scope | [resource-sharing/discovery@20260823-0234](../resource-sharing/discovery.md#resource-sharing-discovery20260823-0234) | 共享 Space 只扩展显式列表发现范围 |
| Resource Sharing → Scope | [resource-sharing/usage-policy@20260823-0234](../resource-sharing/usage-policy.md#resource-sharing-usage-policy20260823-0234) | 共享资源必须在同 Scope 合同下使用 |
| Diagnoser ↔ Scope | [diagnoser/builtin-definition-source@20261010-0350](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261010-0350) | 保留通用定义源并建立独立专项定义 |
| Diagnoser ↔ Scope | [diagnoser/builtin-definition-source@20261117-1340](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261117-1340) | Provision four Core presets on demand in personal Spaces |
| Job ↔ Scope | [job/retirement-migration@20261010-0350](../job/retirement-migration.md#job-retirement-migration20261010-0350) | 退役资源迁移遵守授权、排空与回读边界 |
| Security ↔ Scope | [security/authorization-boundary@20261010-0350](../security/authorization-boundary.md#security-authorization-boundary20261010-0350) | 保持 Django 最终用户授权；专项与 owned CLI 显式授权 |
<!-- adr-related:end -->
