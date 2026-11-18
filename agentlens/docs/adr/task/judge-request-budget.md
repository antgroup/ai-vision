---
object_key: task
topic: judge-request-budget
title: Task Judge 请求预算
---

# Task Judge 请求预算


## task-judge-request-budget@20261009-1945

**Task Judge 请求预算：通用 Core 与可信有界执行**

```yaml
record_id: task/judge-request-budget@20261009-1945
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-09T19:45+08:00
decided_at: 2026-10-09T19:45+08:00
implemented_at: 2026-10-09T19:45+08:00
supersedes: []
affected_objects:
  - task-runner
  - llm-judge
  - executor
  - task-stats
canonical_doc: docs/scheduling.md
```

### 背景

重试、rescue 和崩溃都会丢失进程内计数；首次运行必须可靠限制可能发送的请求，不把未知 usage 计为零。

### 决策

- 仅 bounded_core_v1 启用 nullable judge_request_ledger JSON schema_version=1，与 snapshot/Stats 分离。additive 010/ORM/DDL 同步；旧 window Task 不建立无界 JSON。
- 总 cap=30；logical case=hash(Task UUID, Trace ID, Diagnoser UUID, frozen definition digest)，每 case 跨 rescue 最多 3，case key 不含 attempt。
- Infra 每操作独立 session，SELECT FOR UPDATE + populate_existing 读取 Task；claim 验证当前 RUNNING/worker/attempt/frozen policy/ledger 后追加有序 request_uuid 元数据并 commit。无 durable ticket 就无 HTTP；锁不跨网络。
- reserved/dispatch_unknown 保守占额度，不表示 Provider receipt，不退还崩溃窗口 reservation。dispatch intent 只证明本地准备调用 transport。
- finish 按 ticket 幂等保存真实 response/request_error、HTTP status、安全 failure kind、每字段 token 与 known_fields。旧 owner 可补真实费用，不能修改状态/Stats/snapshot；completion 冲突拒绝。DB 写失败 fail closed，不重发 HTTP 修账。
- 首次 cap refusal 仅当本 case 零历史 reservation 时 budget_exhausted SKIP；已有 reservation/已发送/retry refusal 为 FAILED。stale/canceled/非法 policy/DB 错误不伪装 SKIP。
- start/rescue/terminal 清 Stats 不清 ledger。公开 reserved_slots、response_confirmed_attempts、unknown_attempts、request_error_attempts 及 token coverage；unknown/null 与已知零分开。aggregate 只有对应 token 字段覆盖全部 reservations 时才完整，否则 null；部分 sum 必须带 known coverage。
- 不存 Prompt/Trace/body/headers/key；不改 TaskStats 计分或 Metric verdict。Provider 全局 20 requests/60 seconds 的验证预算由主控另管，Task cap 不冒充 RPM。

### 保持不变

T1 质量/信号分离、TaskStats total/good/bad/failed/skipped、不可变历史来源、CUSTOM/未涉及 legacy 合同，以及原始 Trace/runtime 身份保持。

### 影响与风险

ABI 1.3.0 发布前需排空旧可执行 Task 或保留兼容 Worker。reservation 是保守上界，不是账单或 provider receipt；材料和模型判断覆盖有明确限制。

### 验证

使用独立反例的 genuine RED→GREEN、mock transport 与事务边界离线测试。双引擎锁/并发、平台/API/GUI 与真实模型人工校准由主控另验收，本记录不宣称这些已通过。

### 实施说明

源实现和离线合同已实现；Task 4 报告记录精确检查、风险及待补真实 gate。

## task-judge-request-budget@20261028-1611

**Reuse the durable request budget for one saved diagnoser**

```yaml
record_id: task/judge-request-budget@20261028-1611
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-28T16:11+08:00
decided_at: 2026-10-28T16:11+08:00
implemented_at: 2026-10-28T16:11+08:00
supersedes:
  - task/judge-request-budget@20261009-1945
related_records:
  - task/execution-snapshot@20261028-1611
affected_objects:
  - task-runner
  - llm-judge
  - executor
  - task-stats
canonical_doc: docs/scheduling.md
```

### Background

Selected-Trace execution must reuse saved checks, including Specialists and CUSTOM checks, while
preserving durable accounting across retries and rescue. A single selected diagnoser keeps the
existing budget sufficient for ten Traces without introducing another budget size.

### Decision

- Both trusted `bounded_core_v1` and `bounded_diagnosis_v1` use the nullable schema-1
  `judge_request_ledger`, independent of immutable snapshots and resettable TaskStats. Window Tasks
  without a policy do not acquire a fictitious budget or unbounded ledger.
- The total cap remains 30 and the per-case cap remains 3. A logical case hashes Task UUID, Trace ID,
  Diagnoser UUID and frozen definition digest; it does not include the attempt. The new policy permits
  exactly one saved diagnoser and 1–10 fixed Trace IDs. The old policy retains its Core restrictions.
- Each ledger operation uses an independent session and row lock with fresh state. Claim validates
  RUNNING/worker/attempt, snapshot revision and policy before committing a durable ticket. HTTP cannot
  start without a ticket; no database lock is held across network calls.
- Reservations and unknown dispatch outcomes consume budget without claiming a provider receipt.
  Finish records actual safe outcomes, HTTP status, failure kind and per-field token coverage by ticket.
  Old owners may complete accounting for an in-flight request but cannot change Task state or snapshot.
  Conflicting completion fails; accounting failure never triggers another provider call to repair it.
- A first refusal with no previous reservation for that case is a budget-exhausted skip. Subsequent
  refusal is failed execution. Stale ownership, cancellation, invalid policy and database errors fail
  closed rather than masquerading as applicability skips.
- Start/rescue/terminal statistics resets preserve the ledger. Public summaries expose reservations,
  confirmed responses, unknown/error attempts and token coverage; unknown is not zero. Aggregates are
  complete only when every reservation supplies that field, with partial sums labelled by coverage.
- Ledger data contains no Prompt, Trace, request body, headers or credential. Provider RPM limits
  remain separate from this per-Task cap. Saved-check model availability is not weakened by enabling
  accounting; saved LLM runs require an AVAILABLE same-Space connection.

### Unchanged

Metric verdicts, quality/signal separation, TaskStats counters, raw Trace identity and historical facts
remain unchanged. Existing Core and unbounded window Tasks keep their prior execution contracts.

### Impact and risks

Older workers reject the new policy kind. Enable the new endpoint only with a worker pool that
supports it; source tests do not prove a mixed-version rollout. Reservations remain conservative
limits rather than billing receipts.

### Validation

Cover new policy shape, one-diagnoser enforcement, saved model availability, existing Core behavior,
runtime request hooks and existing ledger fence/accounting tests. Real rule runs do not constitute
real-provider LLM validation; database-fixture skips must remain explicit in the validation report.

### Implementation notes

Implemented under the user's approved single-saved-diagnoser scope. The existing schema and cap are
reused without a migration; no Task ledger or historical snapshot is rewritten.
