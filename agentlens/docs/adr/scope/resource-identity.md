---
object_key: scope
topic: resource-identity
title: Scope 资源身份
---

# Scope 资源身份

## scope-resource-identity@20260823-0234

**资源以精确 Scope 和业务 UUID 标识**

```yaml
record_id: scope/resource-identity@20260823-0234
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - diagnoser
  - dataset
  - job
  - model-connection
canonical_doc: docs/architecture.md
```

### 背景

Diagnoser、Dataset、Job 和模型连接既需要全局可寻址，又需要稳定表达所属空间和 Agent。

### 决策

- 资源使用业务 UUID 跨模块引用，自增数据库 ID 只用于持久化内部。
- Diagnoser、Dataset 和 Job 绑定明确的 `space_uuid + agent_uuid`，名称唯一性在该 Scope 内判定。
- 单资源读取按全局业务 UUID 定位；列表接口显式携带所属命名空间。

### 保持不变

Scope 表达资源归属，不替代最终用户身份、空间成员关系或 owner 授权。

### 影响与风险

业务 UUID 与 Scope 分离可稳定引用和迁移；列表调用方必须提供正确命名空间。

### 验证

由 ORM 唯一约束、CRUD 路由和跨 Scope 列表测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/architecture.md` 核对为已实施行为。
