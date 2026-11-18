---
object_key: security
topic: authorization-boundary
title: Security 授权边界
---

# Security 授权边界

## security-authorization-boundary@20260823-0234

**AgentLens 不承担最终用户与 owner 授权**

```yaml
record_id: security/authorization-boundary@20260823-0234
decision_status: superseded
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - http-api
  - diagnosis-record
  - task-stats
  - resource-sharing
canonical_doc: docs/architecture.md
```

### 背景

AgentLens 没有 AI Vision 用户身份和空间成员关系，无法独立判断调用者可访问的资源或 owner。

### 决策

- 面向最终用户的身份、空间成员和 owner 授权由 AI Vision 代理层承担。
- AgentLens 按请求中的资源 UUID、Scope 和可选 `owner_id` 执行精确查询，不推断调用者身份。
- 未传 owner 时返回该资源合同下的全局结果；owner 无匹配时返回空，不回退全局。
- 服务端 Scope 引用一致性和凭证保护仍由 AgentLens 强制执行。

### 保持不变

客户端隐藏入口、展示文案和可选查询参数都不能替代 AI Vision 的服务端授权。

### 影响与风险

绕过 AI Vision 直接调用 AgentLens 可能获得调用 token 可访问的全局数据，部署必须限制该入口。

### 验证

由 owner 查询、Scope 绑定、AI Vision 代理和安全边界集成测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/architecture.md` 核对为已实施行为。

## security-authorization-boundary@20261010-0350

**保持 Django 最终用户授权；专项与 owned CLI 显式授权**

```yaml
record_id: security/authorization-boundary@20261010-0350
decision_status: accepted
implementation_status: not_started
proposed_at: 2026-10-10T03:50+08:00
decided_at: 2026-10-10T09:06+08:00
implemented_at: null
supersedes:
  - security/authorization-boundary@20260823-0234
related_records:
  - scope/reference-consistency@20260823-0234
  - resource-sharing/discovery@20260823-0234
  - resource-sharing/usage-policy@20260823-0234
  - dataset/diagnosis-input-purpose@20261010-0350
  - security/private-check-policy@20261010-0350
  - job/retirement-migration@20261010-0350
affected_objects:
  - diagnoser
  - dataset
  - job
  - task
  - trace-retrieval
  - http-api
canonical_doc: docs/architecture.md
```

### 背景

专项/CLI写入口需要权限，visibility和scope不能代所有权。

### 决策

Django负责user/membership/owner与本人ObservationSubject；AL无humanidentity，仅可信businessUUID/exactspaceagentowner/引用/credential边界。既有每资源missingowner/global语义保持，缺本人binding不得fallback扩大。专项catalog/TrialEXECUTE+currentenabledAgent/type本人Subject，sync/config MANAGE且现有team/concrete本人Agent高级限制，draftTrial另MANAGE；remote资源exactspaceagent/keyvariant，授权错先安全拒。实际datasetserver注入、browserlabels/scope/test_input非AUTH。仅四routeallowlist无wildcard；ALdirect仍token/network边界，sameSpace非最终userAUTH。CLIwrites只root显式owned Job/Dataset/Diagnoser allowlist+scope/envfingerprint/ownershipmanifest安全refs，name/runtime/BUILTIN/creatorNone非owner，共享/defaultscope默认拒，可见不授权sync/remap。全poolreadonlycensus可report阻塞不pause别人/改Task，需另授权；所有write锁复核同Scope引用/model同Space无migration例外。私密传播独立security/private-check-policy。

### 保持不变

业务UUID/Scope非AUTH/共享发现使用/原userowner/模型凭证/TaskCAS保持。

### 影响与风险

计划正确不授权；pooldrain可能需超owned的独立授权；安全code/ref不私密body。

### 验证

后续permission/本人/disabledAgent/team/crossscope/model/shared/forbiddenpayload/manifestmissingdrift/censuszero写。

### 实施说明

未实施。C-BE用户AUTH，AL/B可信scopegrant，不UI隐藏代server。
