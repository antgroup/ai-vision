---
object_key: scope
topic: reference-consistency
title: Scope 引用一致性
---

# Scope 引用一致性

## scope-reference-consistency@20260823-0234

**Job 只绑定完全相同 Scope 的资源**

```yaml
record_id: scope/reference-consistency@20260823-0234
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - job
  - dataset
  - diagnoser
  - model-connection
canonical_doc: docs/architecture.md
```

### 背景

跨 Scope 直接引用会绕过资源归属并使任务运行权限和配置来源不可判断。

### 决策

- Job 对 Dataset 和 Diagnoser 的直接引用必须具有完全相同的 `space_uuid + agent_uuid`。
- LLM Diagnoser 引用 ModelConnection 时只校验 `space_uuid` 一致，因为 ModelConnection 不绑定
  `agent_uuid`。
- 创建、更新和手动触发入口保证绑定合法；Runner 加载已绑定配置时不重复改变语义。
- builtin 或共享来源身份不能绕过各自适用的引用一致性检查。

### 保持不变

共享源的发现不等于可直接跨 Scope 运行；当前没有共享源挂载 API。

### 影响与风险

严格一致性可能要求先在目标 Scope 创建或挂载资源，避免运行期隐式借用。

### 验证

由 Job、Diagnoser、ModelConnection CRUD 和手动触发测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/architecture.md` 核对为已实施行为。
