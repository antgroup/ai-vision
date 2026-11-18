---
object_key: resource-sharing
title: Resource Sharing 决策档案
scope: Discovery and use of shared resources
---

# Resource Sharing 决策档案

共享资源发现和使用策略。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [discovery](discovery.md) | [resource-sharing/discovery@20260823-0234](discovery.md#resource-sharing-discovery20260823-0234) | [resource-sharing/discovery@20260823-0234](discovery.md#resource-sharing-discovery20260823-0234) |
| [usage-policy](usage-policy.md) | [resource-sharing/usage-policy@20260823-0234](usage-policy.md#resource-sharing-usage-policy20260823-0234) | [resource-sharing/usage-policy@20260823-0234](usage-policy.md#resource-sharing-usage-policy20260823-0234) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Security → Resource Sharing | [security/authorization-boundary@20260823-0234](../security/authorization-boundary.md#security-authorization-boundary20260823-0234) | AgentLens 不承担最终用户与 owner 授权 |
| Security ↔ Resource Sharing | [security/authorization-boundary@20261010-0350](../security/authorization-boundary.md#security-authorization-boundary20261010-0350) | 保持 Django 最终用户授权；专项与 owned CLI 显式授权 |
<!-- adr-related:end -->
