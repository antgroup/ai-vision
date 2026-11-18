---
object_key: resource-sharing
topic: usage-policy
title: 共享资源使用策略
---

# 共享资源使用策略

## resource-sharing-usage-policy@20260823-0234

**共享资源必须在同 Scope 合同下使用**

```yaml
record_id: resource-sharing/usage-policy@20260823-0234
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - scope
  - job
  - diagnoser
  - dataset
canonical_doc: docs/api.md
```

### 背景

资源可被发现后仍需明确如何进入任务，避免由前端展示或 UUID 可达性代替服务端约束。

### 决策

- 当前 Job 只绑定同 Scope 的 Dataset 与 Diagnoser。
- 共享 Space 可直接创建其自身可运行的 Dataset、Diagnoser 和 Job，但不自动成为其他 Scope 的资源。
- 当前版本不提供共享源挂载 API；未来挂载必须显式建立目标 Scope 身份和引用关系。

### 保持不变

不把共享可见性解释为用户授权，也不通过客户端隐藏按钮实施安全边界。

### 影响与风险

跨 Scope 复用需要显式产品和服务端合同；直接引用共享 UUID 会被一致性校验拒绝。

### 验证

由 Job 绑定、builtin 同步到共享 Scope 和跨 Scope 拒绝测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/api.md` 核对为已实施行为。
