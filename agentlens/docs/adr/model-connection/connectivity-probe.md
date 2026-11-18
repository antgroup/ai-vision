---
object_key: model-connection
topic: connectivity-probe
title: Model Connection 连通性检测
---

# Model Connection 连通性检测

## model-connection-connectivity-probe@20261008-2225

**显式连接检测使用单次 180 秒 HTTP 等待**

```yaml
record_id: model-connection/connectivity-probe@20261008-2225
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-08T22:25+08:00
decided_at: 2026-10-08T22:25+08:00
implemented_at: 2026-10-08T22:29+08:00
supersedes: []
related_records:
  - model-connection/resource-contract@20260823-0234
  - diagnoser/llm-request-limits@20261008-2225
affected_objects:
  - llm-judge
  - http-api
canonical_doc: docs/api.md
```

### 背景

显式检测的实际推理请求可能在默认 60 秒内没有返回。三次自动重试会重复发起推理，且让外层
HTTP 请求先超时；不能用模型目录 GET 或虚构成功替代一次真实请求。

### 决策

- `POST /model-connection/{uuid}/test` 构造 Judge 时显式使用 `timeout=180`、`max_retries=0`。
  每次检测最多发送一次真实 Chat Completions `ping` POST。
- 保留 probe 的请求内容、凭证、模型、thinking 参数及 `max_tokens=1`；不读取 Diagnoser Spec。
- 成功按既有 probe 结果标记 `available`，HTTP 错误或超时标记 `unavailable`，并关闭 Judge。
  连接参数不完整时仍直接标记不可用，不发请求。
- timeout 是 HTTP 各阶段的等待上限，不是端到端墙钟时限；代理须留出处理余量，正式诊断的
  重试策略和缺省 60 秒保持原状。

### 保持不变

ModelConnection 资源、凭证脱敏、修改后回到 untested、引用及删除合同不变；probe 仍发送真实
推理请求并按原有 HTTP 成功条件判定，不改为目录查询或缓存结果。

### 影响与风险

单次失败不再自动重试，瞬时网络错误需要用户再次显式检测；较长等待仍可能超过外层代理时限。
回滚会恢复原先三次 60 秒策略，不更改已保存状态或连接参数。

### 验证

通过真实 API 路由、Judge 和 HTTP transport 检查成功、超时、429、5xx 的实际请求数量、
180 秒配置、原始 probe payload、状态与关闭行为；不请求外部模型。

### 实施说明

路由已显式配置 180 秒、零重试，probe 内容与成功条件保持原状。修正夹具后，对 HEAD 的旧路由
单独重放四个回归场景，均在 60 秒配置或重复请求数量上失败；当前真实路由、Judge 和 HTTP
transport 的成功、超时、429、5xx 场景均通过。仅替换测试 HTTP transport，不请求外部模型。

## model-connection-connectivity-probe@20261008-2255

**连通性检测省略 provider 特有的 Thinking 控制**

```yaml
record_id: model-connection/connectivity-probe@20261008-2255
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-08T22:55+08:00
decided_at: 2026-10-08T22:55+08:00
implemented_at: 2026-10-08T23:01+08:00
supersedes:
  - model-connection/connectivity-probe@20261008-2225
related_records:
  - model-connection/resource-contract@20260823-0234
  - llm-judge/thinking-mode@20261008-2255
  - diagnoser/llm-request-limits@20261008-2225
affected_objects:
  - llm-judge
  - http-api
canonical_doc: docs/api.md
```

### 背景

真实 probe 的 HTTP 400 响应明确说明不支持 `enable_thinking`，不是凭证错误或超时。
连接检测应检验实际模型请求，且不应强制所有 OpenAI 兼容服务接受 vendor 特有字段。

### 决策

- `POST /model-connection/{uuid}/test` 显式构造 Judge 使用 `enable_thinking=None`、`timeout=180`
  和 `max_retries=0`，每次检测最多发送一次真实 Chat Completions `ping` POST。
- 请求保留连接的 model、messages、`temperature=0` 和 `max_tokens=1`，不发送 `enable_thinking`
  或 `chat_template_kwargs`，不读取 Diagnoser Spec，也不新增 probe 配置或模型专属映射。
- 按既有 probe 的 HTTP 成功条件标记 available；HTTP 错误或超时标记 unavailable，并关闭 Judge。
  参数不完整时直接标记不可用，不发请求；不通过删除字段重试、目录查询或缓存虚构成功。
- timeout 是各网络阶段的等待上限，不是端到端墙钟时限；代理须留出余量，正式诊断的默认
  Thinking 参数、重试次数与缺省 60 秒不变。

### 保持不变

ModelConnection 资源、凭证脱敏、修改后回到 untested、引用与删除合同不变。
仍需真实模型服务接受标准请求；省略 vendor 字段不保证所有模型接受其它参数或一定返回成功。

### 影响与风险

模型使用自己的推理默认模式，等待可能变长。单次失败仍需要用户再次显式检测；回滚会恢复
双 Thinking 字段，可能重新触发不支持参数的 HTTP 400，连接与历史状态本身不变。

### 备选方案

- 猜测 token 限制并增大输出预算：不采用，当前确定错误指向 vendor 字段，保留 max_tokens=1。
- 改为 GET 模型目录：不采用，不能证明模型实际接受推理请求。

### 验证

用真实 API 路由、Judge 和 HTTP transport 回放拒绝 vendor 参数的 400，验证最终标准 payload
与单次请求；继续覆盖成功、400、429、5xx、超时、凭证脱敏和关闭行为。不调用外部模型。

### 实施说明

路由构造配置与当前文档已落地。真实路由、Judge 和 HTTP transport 的五个回归场景在修复前
失败，修复后全部通过：成功夹具会拒绝含 vendor 字段的请求，另覆盖 400、429、5xx 与超时。
最终 payload 保留 temperature=0 和 max_tokens=1，只发一次请求，HTTP 各阶段等待上限为
180 秒；上游错误仍标记 unavailable。仅替换测试 HTTP transport，未请求外部模型。

## model-connection-connectivity-probe@20261021-0023

**Use provider-default sampling for the official Anthropic connectivity probe**

```yaml
record_id: model-connection/connectivity-probe@20261021-0023
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-21T00:23+08:00
decided_at: 2026-10-21T00:23+08:00
implemented_at: 2026-10-21T00:23+08:00
supersedes:
  - model-connection/connectivity-probe@20261008-2255
related_records:
  - diagnoser/llm-temperature@20261021-0023
  - llm-judge/thinking-mode@20261021-0023
affected_objects:
  - llm-judge
  - http-api
canonical_doc: docs/api.md
```

### Background

Current Claude models reject the connectivity probe's fixed temperature 0. The official OpenAI
compatibility endpoint supports the existing messages and token budget, so a separate Messages
client is unnecessary for this setup improvement.

### Decision

- `POST /model-connection/{uuid}/test` constructs Judge with `enable_thinking=None`, `timeout=180`
  and `max_retries=0`, making at most one real Chat Completions `ping` request.
- Keep connection model, user message and `max_tokens=1`. Omit vendor thinking fields and do not
  read a Diagnoser Spec. The exact normalized official Anthropic endpoint omits temperature;
  every other endpoint retains `temperature=0`.
- HTTP success marks the connection available; HTTP or transport failure marks it unavailable.
  Close Judge on completion. Incomplete connection parameters fail without making a request.
- Do not use model-directory success, cached results or a parameter-removal retry as a probe.
  Network timeouts remain phase limits, not absolute deadlines; the outer proxy retains margin.

### Unchanged

Resource identity, credential redaction, reset-to-untested on edits, references, deletion,
formal-runtime retry policy, max token settings and all non-Anthropic payloads are unchanged.

### Impact and risks

The Anthropic probe uses its default sampling and thinking behavior. Passing the probe still does
not guarantee a model accepts a full diagnostic prompt or returns the required metric JSON.
Rolling back restores temperature 0 and may cause current Claude models to reject the request.

### Validation

Offline real-route/Judge/HTTP-transport tests cover the existing success/failure behavior and the
Anthropic payload exception. No authenticated model-provider execution is claimed.

### Implementation notes

Implemented and reviewed within the user's provider-addition request. The transport remains
OpenAI-compatible Chat Completions, without a database migration or native Messages implementation.


## model-connection-connectivity-probe@20261021-0051

**Return bounded, credential-safe reasons for explicit connection test failures**

```yaml
record_id: model-connection/connectivity-probe@20261021-0051
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-21T00:51+08:00
decided_at: 2026-10-21T00:51+08:00
implemented_at: 2026-10-21T00:54+08:00
supersedes:
  - model-connection/connectivity-probe@20261021-0023
related_records:
  - model-connection/credential-handling@20261021-0005
  - diagnoser/llm-temperature@20261021-0023
  - llm-judge/thinking-mode@20261021-0023
affected_objects:
  - llm-judge
  - http-api
canonical_doc: docs/api.md
```

### Background

Real provider tests returned unavailable connections without an actionable explanation. Model
catalog membership did not guarantee account access: an NVIDIA model returned HTTP 404 with
`function_not_found_for_account`. Treating every failure as an invalid credential would be
misleading. The user requested failure explanations with three-language presentation and real
frontend/backend validation.

### Decision

- The explicit test still sends at most one real Chat Completions `ping` POST with a 180-second
  per-network-phase timeout, `max_retries=0` and `max_tokens=1`. It omits vendor thinking fields
  and does not read a Diagnoser Spec. Only the exact normalized official Anthropic endpoint
  omits temperature; other endpoints retain `temperature=0`.
- Incomplete parameters fail before a provider request. A successful HTTP response without an
  explicit error envelope marks `available`. HTTP/transport failures and explicit error envelopes
  mark `unavailable`; a probe does not require diagnostic metric JSON or nonempty generated text.
  Do not replace real inference with model discovery, cached success or parameter-removal retries.
- The test response preserves the connection fields and adds `test_error`, null on success or one
  of `authentication_failed`, `permission_denied`, `model_not_found`, `rate_limited`,
  `quota_exceeded`, `timeout`, `connection_failed`, `invalid_request`, and `unavailable`.
  The API response remains HTTP 200 when the connection exists and the probe finishes.
- Classify from HTTP status and a finite set of exact machine codes. A 404 means the model or
  endpoint is unavailable for this account, never proof of an invalid key. Distinguish a 429
  carrying an explicit insufficient-quota code from ordinary throttling. Provider-specific
  numeric codes are interpreted only for their known official endpoints. Unknown responses
  retain a generic reason; never infer causes from natural-language messages.
- Neither exception text nor the API includes raw upstream bodies, messages, authentication
  headers, keys, or arbitrary code labels. Parse at most 64 KiB for machine-code classification.
  The reason belongs only to this test response; persist only the existing connection status.
  GET/list responses and database schema remain unchanged.
- Close Judge after the request. Outer proxies must retain timeout margin and must not expose
  provider authentication failures as platform authentication failures.

### Unchanged

Resource identity, credential redaction, reset-to-untested on edits, references, deletion,
formal diagnostic requests/retries, model discovery, token budget and non-Anthropic probe payloads
are unchanged. Passing a lightweight probe does not validate a full diagnostic prompt or its JSON.

### Impact and risks

Clients may safely localize the reason and old clients can ignore the additive field. The reason
is not retained after a fresh list request; a new explicit test refreshes it. Providers differ in
error semantics, so status-based reasons can be broad and unknown vendor codes remain generic.
HTTP 2xx error envelopes are now failed tests instead of false success. Rolling back drops the
optional reason and restores the previous success rule; no migration or data rewrite is required.

### Validation

Offline real-route/Judge/HTTP-transport cases cover all nine reasons, one-request bounds, timeout
configuration, success with truncated content, explicit error envelopes, incomplete parameters,
404 credential ambiguity, scoped numeric codes, and omission of secrets in bodies/exceptions.
The parent task separately owns authenticated provider and browser validation; these tests do
not claim live provider success.

### Implementation notes

Implemented within the user's explicit failure-reason and localization request, with the frontend
and Django proxy sharing the nine-code contract. No raw response or test reason is stored.


## model-connection-connectivity-probe@20261023-1225

**Persist probe status only for the configuration actually tested**

```yaml
record_id: model-connection/connectivity-probe@20261023-1225
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-23T12:25+08:00
decided_at: 2026-10-23T12:25+08:00
implemented_at: 2026-10-23T12:25+08:00
supersedes:
  - model-connection/connectivity-probe@20261021-0051
related_records:
  - model-connection/credential-handling@20261021-0005
  - model-connection/diagnostic-validation@20261021-0500
  - diagnoser/llm-temperature@20261021-0023
  - llm-judge/thinking-mode@20261021-0023
affected_objects:
  - llm-judge
  - http-api
canonical_doc: docs/api.md
```

### Background

Model connections are Space resources being exposed as a first-level integration. A user may edit
an endpoint, model or key while a connection-only probe is in flight. The old unconditional status
write could mark the replacement configuration available without testing it. The user approved
shared connections with independent feature bindings and explicitly approved guarding stale probe
writes in the reviewed design.

### Decision

- `POST /model-connection/{uuid}/test` copies the saved connection and ends its database transaction
  before provider I/O. Completion atomically compares UUID, Space, configuration revision, endpoint,
  model and key. Edits, including edit-and-restore (ABA), and deletion reject the write with HTTP 409;
  do not replace the new configuration's state. Django maps this to safe `configuration_changed`.
- A valid unchanged configuration still returns HTTP 200 with the existing connection and transient
  `test_error`: null on success, or `authentication_failed`, `permission_denied`, `model_not_found`,
  `rate_limited`, `quota_exceeded`, `timeout`, `connection_failed`, `invalid_request`, `unavailable`.
- Send at most one real Chat Completions `ping`, with a 180-second per-phase timeout, no retries,
  and max_tokens=1. Do not read a Diagnoser Spec or send vendor thinking controls. Only the exact
  normalized official Anthropic endpoint omits temperature; other endpoints use temperature=0.
- Missing fields or a key outside 1–8192 visible ASCII characters return safe invalid_request without
  constructing a provider client. HTTP success without an explicit error envelope marks available;
  transport/HTTP errors and explicit error envelopes mark unavailable. A diagnostic JSON response
  or nonempty generated text is not required. Close Judge on completion.
- Classify by HTTP status and finite exact machine codes, never natural-language messages. Parse at
  most 64 KiB for classification; distinguish explicit quota codes from ordinary throttling and do
  not infer invalid credentials from a 404. Interpret vendor numeric codes only at trusted endpoints.
- Persist only the existing status and modified time, preserving any current diagnostic report.
  No checked-at or error-history field is added. Clients show missing check time as unknown and
  never use modified time as proof of when a probe ran. Keys, exception messages and raw upstream
  data remain absent from responses and logs. A stale-conflict response contains no credentials.

### Unchanged

The endpoint/body, successful response, request budget, diagnostic-validation contract, resource
identity and database schema are unchanged.

### Impact and risks

The additive HTTP 409 prevents false current-config
success; clients should refresh and explicitly retry after editing. Holding a row lock across
provider I/O was rejected because it would block configuration changes for the network timeout.
A timestamp-only comparison would miss ABA; the existing revision and identity provide the guard.
Reverting restores the unsafe late-write race; no schema rollback is needed. Existing concurrent
same-configuration probes still follow their completion order rather than a new attempt ledger.

### Validation

Ten new SQL-backed cases failed on the old route, covering transaction release, model/endpoint/key
edits, ABA, deletion and four malformed keys; all passed after the change. The existing bounded
Judge/HTTP-transport probes retain their payload and one-request assertions. Django regression
rejects an unsafe 502 downgrade of conflict and verifies a credential-free 409 reason. Parent
acceptance owns the real authenticated frontend/backend/model evidence separately.

### Implementation notes

The table owns the detached snapshot and atomic status write; the HTTP route owns the bounded
probe and the Django proxy maps stale writes to the finite public error reason. This uses the
existing configuration revision and requires no database migration.
