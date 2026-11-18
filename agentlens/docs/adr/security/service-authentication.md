---
object_key: security
topic: service-authentication
title: Security 服务认证
---

# Security 服务认证

## security-service-authentication@20260823-0234

**HTTP 服务使用预共享 token 和执行 fence**

```yaml
record_id: security/service-authentication@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - http-api
  - scheduler
  - task-runner
canonical_doc: docs/api.md
```

### 背景

AgentLens 主要由 AI Vision 和集群内部实例调用，需要轻量且一致的服务间认证。

### 决策

- 除 `/internal/healthz` 外，HTTP 请求统一校验 `x-antmonitor-token` 预共享 token；业务 API 和其他
  internal 路由都不匿名开放。
- 远端内部执行请求除预共享 token 外，还将 Master fenced token 与 Worker 最近观察到的租约 token
  比较并拒绝更小值；Master 本地执行不经过 HTTP。
- 认证失败使用统一错误响应，不在日志或响应中暴露配置 token。
- 健康检查按文档定义的边界提供，不隐式扩大业务 API 匿名访问。

### 保持不变

预共享 token 只认证调用服务，不提供最终用户、Space 成员或 owner 级授权。

### 影响与风险

token 泄漏会扩大服务访问面，必须由部署 secret 和网络边界共同保护并支持轮换。

### 验证

由认证中间件、internal execute fence、错误响应和日志测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/api.md` 核对为已实施行为。

## security-service-authentication@20261020-1127

**匿名存活与数据库就绪探针，业务接口保留服务认证**

```yaml
record_id: security/service-authentication@20261020-1127
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-20T11:27+08:00
decided_at: 2026-10-20T11:27+08:00
implemented_at: 2026-10-20T11:27+08:00
supersedes:
  - security/service-authentication@20260823-0234
affected_objects:
  - http-api
  - scheduler
  - task-runner
canonical_doc: docs/api.md
```

### 背景

容器编排需要区分 HTTP 进程存活与应用数据库就绪。现有存活探针不访问数据库，不能作为数据库故障时
停止路由流量的依据。

### 决策

- 仅 `/internal/healthz` 与 `/internal/readyz` 精确路径匿名开放。前者只验证 HTTP 存活；
  后者沿用应用数据库配置、使用独立短超时连接执行 `SELECT 1`，返回 200 或 503 及固定状态，
  不包含异常、连接地址或凭据。MySQL 连接、读取、写入 socket 超时分别为 1 秒。
- 其他既有业务 API 和 internal 路由校验 `x-antmonitor-token` 预共享 token。
  既有 `/v1/chat/completions`、`/v1/completions`、`/v1/responses` 继续使用
  `Authorization: Bearer` 校验同一服务 token，并保留 OpenAI 错误格式。
- 远端内部执行请求同时将 Master fenced token 与 Worker 最近观察到的租约 token 比较并拒绝更小值；
  Master 本地执行不经过 HTTP。
- 认证失败不在日志或响应中暴露配置 token。健康探针不查询业务数据，也不验证完整遥测链路。

### 保持不变

预共享 token 只认证调用服务，不提供最终用户、Space 成员或 owner 级授权。
Task 状态、执行所有权和异常恢复合同保持既有行为。

### 影响与风险

匿名就绪查询会建立短连接；探针频率由部署配置限制。token 仍由部署 secret 和网络边界保护。
数据库可用不等于依赖的遥测读取、上报及任务执行可用。

### 验证

认证与路由测试覆盖匿名精确路径、业务路径继续拒绝匿名、真实 SQL 查询成功和数据库异常返回固定 503；
存活探针在数据库不可用时继续返回 200。

### 实施说明

`main.py`、认证中间件、API 与部署文档同步实现两个探针，保留现有业务认证与执行 fence。
