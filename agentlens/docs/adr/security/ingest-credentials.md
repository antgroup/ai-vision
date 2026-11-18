---
object_key: security
topic: ingest-credentials
title: Security 诊断回写凭证
---

# Security 诊断回写凭证

## security-ingest-credentials@20260915-1750

**由 AI Vision 签发按任务 Scope 隔离的短期回写凭证**

```yaml
record_id: security/ingest-credentials@20260915-1750
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-09-15T17:50+08:00
decided_at: 2026-09-15T17:50+08:00
implemented_at: 2026-09-15T18:02+08:00
supersedes: []
related_records:
  - security/authorization-boundary@20260823-0234
  - security/service-authentication@20260823-0234
affected_objects: []
canonical_doc: docs/configuration.md
```

### 背景

现有 OTLP 回写的 X-Access 认证不能通过启用了 aivisionauth 的网关。用户已授权打通自动回写，
沿用平台签发空间凭证的方式，不要求用户手工管理 Key。

### 决策

- 配置凭证接口后，AgentLens 从自己保存的 Job 解析 Space、Agent，将 OTLP 批次按此 Scope 拆分。
- AgentLens 使用现有 `AGENTLENS_TOKEN` 调用 AI Vision 的内部签发接口。AI Vision 验证服务身份、
  Agent 归属及有效采集身份，签发短期 v2 ingest token；签名密钥只保留在控制面与网关。
- 凭证在内存中按 Scope 有界缓存，到期前重新获取；回写使用 Bearer 认证，失败不退回匿名请求。
- 记录与 TaskStats 使用相同回写通道。服务凭证、上报 Token 和诊断正文不进入回写错误日志。
- 未配置凭证接口的旧网关部署保留 X-Access 兼容路径。

### 保持不变

AI Vision 不访问诊断数据库或代转诊断正文；AgentLens 继续直接经 Antcollector 写入遥测存储。
Job、DiagnosisRecord、TaskStats 的字段、保存策略、执行 fence 和业务时间不变。

### 影响与风险

新凭证的获取依赖 AI Vision；已缓存凭证在有效期内可继续使用。Token 有效期限制身份撤销的生效窗口。
预共享服务密钥继续由部署配置保护。结果回写失败独立于已完成的 Task 计算状态，不伪造成功结果。

### 验证

覆盖未认证与越界签发、混合 Scope 批次拆分、凭证缓存和续期、回写失败；通过真实网关验证
诊断记录、统计及质量看板查询。

### 实施说明

已完成服务端签发、按 Scope 回写、缓存与续期，以及拒绝越界/失效凭证的测试。
本地真实整点任务已通过网关回写，SigNoz 中的两个空间归属正确，浏览器质量看板和 BadCase 列表均可读。
