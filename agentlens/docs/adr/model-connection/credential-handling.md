---
object_key: model-connection
topic: credential-handling
title: Model Connection 凭证处理
---

# Model Connection 凭证处理

## model-connection-credential-handling@20260823-0234

**模型凭证作为只写 API 字段并限制传播**

```yaml
record_id: model-connection/credential-handling@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - security
  - diagnoser
  - llm-judge
canonical_doc: docs/api.md
```

### 背景

模型 API key 属于敏感配置，不能随普通资源读取、日志或诊断证据扩散。

### 决策

- API Key 当前直接写入 OceanBase 的 ModelConnection 文本字段，应用层没有加密或解密机制。
- 读取 API 只返回 `api_key_configured`，不回显完整凭证；更新省略 API Key 或传 null 时保留原值。
- 日志、错误响应、DiagnosisRecord 和 evidence 不写完整 API key 或认证头。
- 连接测试、Trial 和 Task 执行从持久化连接读取 API Key，并只在构造 LLMJudge 请求时使用。

### 保持不变

模型返回内容和 Prompt 按诊断证据策略处理，不因为使用模型连接而自动视为凭证。

### 影响与风险

API 脱敏降低读取面泄漏风险，但数据库访问者仍可读取明文凭证；存储保护依赖数据库和部署环境的
访问控制。排障必须依赖非敏感连接状态和错误分类。

### 验证

由 ModelConnection CRUD 响应、持久化模型和 LLM Client 请求测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/api.md` 核对为已实施行为。


## model-connection-credential-handling@20261012-2310

**冻结模型身份限定运行时凭证绑定**

```yaml
record_id: model-connection/credential-handling@20261012-2310
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-12T23:10+08:00
decided_at: 2026-10-12T23:10+08:00
implemented_at: 2026-10-12T23:10+08:00
supersedes:
  - model-connection/credential-handling@20260823-0234
affected_objects:
  - security
  - diagnoser
  - llm-judge
canonical_doc: docs/api.md
```

### 背景

模型 API key 属于敏感配置，不能随普通资源读取、日志或诊断证据扩散。

### 决策

- 对有快照 Task，当前同 Space 连接 UUID、endpoint、model_name 必须与冻结身份一致才绑定当前 key。endpoint 只按 LLMJudge 去掉尾部斜杠，不放宽 origin、端口、路径或 model 身份。缺失冻结身份或身份改变不绑定模型，沿既有模型不可用路径安全失败；材料无需模型时的 SKIP 保留。
- 同身份 key 轮换继续支持；trusted Core advisory probe 与 advanced AVAILABLE 门禁不变。legacy 无快照仍显式使用当前连接，不伪造冻结语义。

- API Key 当前直接写入 OceanBase 的 ModelConnection 文本字段，应用层没有加密或解密机制。
- 读取 API 只返回 `api_key_configured`，不回显完整凭证；更新省略 API Key 或传 null 时保留原值。
- 日志、错误响应、DiagnosisRecord 和 evidence 不写完整 API key 或认证头。
- 连接测试、Trial 和 Task 执行从持久化连接读取 API Key，并只在构造 LLMJudge 请求时使用。

### 保持不变

模型返回内容和 Prompt 按诊断证据策略处理，不因为使用模型连接而自动视为凭证。

### 影响与风险

API 脱敏降低读取面泄漏风险，但数据库访问者仍可读取明文凭证；存储保护依赖数据库和部署环境的
访问控制。排障必须依赖非敏感连接状态和错误分类。

### 验证

由 ModelConnection CRUD 响应、持久化模型和 LLM Client 请求测试验证。

### 实施说明

已按授权修复并增加离线传输、历史投影与作用域回归；不宣称真实模型或生产验证。

## model-connection-credential-handling@20261021-0005

**Bind saved credentials to their connection endpoint during setup**

```yaml
record_id: model-connection/credential-handling@20261021-0005
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-21T00:05+08:00
decided_at: 2026-10-21T00:08+08:00
implemented_at: 2026-10-21T00:08+08:00
supersedes:
  - model-connection/credential-handling@20261012-2310
related_records:
  - model-connection/model-discovery@20261021-0005
affected_objects:
  - security
  - diagnoser
  - llm-judge
canonical_doc: docs/api.md
```

### Background

Provider and region selection make changing the endpoint easier. Reusing the existing write-only
credential for a different endpoint could send it to an unintended recipient. Discovery also
needs to reuse a saved key without sending that key back to the browser.

### Decision

- API keys remain stored in the ModelConnection text field without application-level encryption.
  Read APIs return only `api_key_configured`; logs, errors and diagnostic evidence must not include
  complete keys or authentication headers.
- PATCH normally preserves the key when omitted or null. If the endpoint changes after trimming
  surrounding whitespace and trailing slashes, a nonempty replacement `api_key` is required.
  This restriction applies to custom endpoints as well as presets; it does not restrict which
  custom endpoint can be saved. Equivalent trailing-slash edits preserve the existing key.
- Discovery can use the saved key only after a same-Space lookup and an equivalent endpoint check.
  A different endpoint requires a supplied key. Discovery does not save keys or connection state.
- Connection tests, Trial and Task execution continue to read the key internally to construct
  LLM Judge requests.
- For snapshot-backed Tasks, the current same-Space connection UUID, endpoint and `model_name`
  must match the frozen identity before binding the current key. Runtime endpoint comparison
  retains the existing LLM Judge trailing-slash semantics; setup normalization does not broaden
  snapshot matching. Missing or changed identity follows the existing unavailable-model path.
  No-model-required SKIP and same-identity key rotation remain supported.
- Trusted Core advisory probes, advanced AVAILABLE gates and legacy Tasks without snapshots
  retain their existing behavior. Legacy execution uses the current connection and does not
  claim frozen semantics.

### Unchanged

Read-side credential redaction, database storage format, diagnostic runtime calls and frozen
Task identity comparisons remain unchanged.

### Impact and risks

Clients that change an endpoint while omitting the key now receive HTTP 422 with
`discovery_error: missing_api_key`. They must explicitly supply a key for the destination.
Model-name-only edits and same-endpoint key rotation remain compatible. No migration is needed.
Rolling back removes the setup destination guard and discovery, while leaving stored data intact.

### Validation

Route tests cover missing keys, equivalent endpoints, new keys, cross-Space discovery and no state
changes. Existing snapshot and credential-redaction tests remain applicable. These are offline
checks and do not establish live model-provider or production acceptance.

### Implementation notes

The PATCH destination guard and discovery credential binding are implemented in the current
worktree. Design and code review accepted this scope under the requested setup improvement.
Offline endpoint-change, discovery, frozen-identity and credential-redaction checks passed.
Live database and authenticated provider acceptance remain unverified.
