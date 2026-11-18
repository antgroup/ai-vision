---
object_key: model-connection
topic: resource-contract
title: Model Connection 资源合同
---

# Model Connection 资源合同

## model-connection-resource-contract@20260823-0234

**模型连接是独立的 Scope 资源**

```yaml
record_id: model-connection/resource-contract@20260823-0234
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - diagnoser
  - llm-judge
  - scope
canonical_doc: docs/api.md
```

### 背景

多个 LLM Diagnoser 需要复用服务地址、模型名和访问配置，并在删除前知道引用关系。

### 决策

- ModelConnection 是只绑定 `space_uuid`、不绑定 Agent 的资源，具有业务 UUID、名称、`endpoint`、
  `model_name`、`api_key`、可用状态和审计字段。
- 提供创建、列表、详情、更新、连通性测试、引用查询和删除接口。
- Diagnoser 通过 UUID 引用；被引用连接的删除受服务端引用检查约束。
- 连接更新供后续 Executor 构造使用，不重写历史 DiagnosisRecord。

### 保持不变

Prompt、metrics 和诊断策略不属于模型连接；连接也不管理最终用户授权。

### 影响与风险

独立资源减少重复配置，但更新共享连接会影响引用它的后续诊断执行。

### 验证

由 ModelConnection CRUD、test、references 和 Diagnoser 引用测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/api.md` 核对为已实施行为。
