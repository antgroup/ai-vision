---
object_key: resource-sharing
topic: discovery
title: 共享资源发现
---

# 共享资源发现

## resource-sharing-discovery@20260823-0234

**共享 Space 只扩展显式列表发现范围**

```yaml
record_id: resource-sharing/discovery@20260823-0234
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - scope
  - diagnoser
  - dataset
  - job
canonical_doc: docs/api.md
```

### 背景

平台需要发现公共资源，但发现范围不应隐式改变本地资源身份或可执行引用。

### 决策

- 资源列表以请求 Scope 为主，并按 API 明确规则追加对应共享 Space 的可发现来源。
- Diagnoser 和 Dataset 列表可分别显式加入用户共享、业务共享和通用共享 Space；Job 列表只可显式加入
  用户共享 Space。
- 共享源保留自身 Scope 和业务 UUID，响应可区分本地与共享来源。
- 未请求或不适用的共享范围不自动加入列表。

### 保持不变

发现共享资源不创建本地副本、不挂载到本地 Scope，也不授权 Job 跨 Scope 绑定。

### 影响与风险

调用方可以展示共享来源，但必须遵守后续使用策略，不能把可见等同于可运行。

### 验证

由 Diagnoser、Dataset 和 Job 列表的 Scope、共享来源及各自参数边界测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/api.md` 核对为已实施行为。
