---
object_key: task
topic: execution-snapshot
title: 创建 Task 时冻结有效执行定义与语义版本
---

# 创建 Task 时冻结有效执行定义与语义版本

## task-execution-snapshot@20261009-1800

**创建 Task 时冻结有效执行定义与语义版本**

```yaml
record_id: task/execution-snapshot@20261009-1800
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-09T18:00+08:00
decided_at: 2026-10-09T18:00+08:00
implemented_at: 2026-10-09T18:00+08:00
supersedes: []
related_records:
  - task-stats/aggregation-model@20261009-1800
affected_objects:
  - scheduler
  - task-runner
  - task-stats
  - model-connection
canonical_doc: docs/scheduling.md
```

### 背景

等待派发及 rescue 期间可修改资源配置；执行时读取当前配置会改变同一 Task 的算法、样本与历史解释。

### 决策

- 手动触发与调度创建时保存 `execution_snapshot`，固定有效 Dataset 查询/Scope/Selector、Job 保存策略、
  Diagnoser UUID/key/name/version/spec、生成参数、Prompt 摘要和模型连接 UUID/endpoint/model_name。
- 快照字段按允许列表复制；不复制内联 model、API key、认证 headers、datasource DSN 或未知配置。
  模型 endpoint 拒绝 userinfo、query 和 fragment。凭证只在运行时按原连接 UUID 和 Space 解析。
- 快照 `executor_revision=1.0.0` 是执行语义版本，涵盖 Executor/Projector/Selector；与 Git HEAD 独立。
  行为变化须升版本并保留兼容实现，或先排空旧 Task；不支持的版本明确 FAILED，不执行当前算法。
- 同一 Task 的所有 attempt/rescue 保留原快照；新触发 Task 使用新定义。additive migration 009 只加
  nullable JSON 列，不改写 legacy Task。旧无快照 Task 明确使用 `legacy_current_definition` 合同，
  不伪造创建时冻结定义；它仍按原执行入口读取当前资源，日志与读取 API 都可识别。
- Task GET/List 只返回脱敏的 `diagnoser_definitions` 与 `definition_source`，不回显执行 Spec/Prompt。
- schema、执行 revision 与 score policy 未支持时不解析其定义、不套用当前政策，Runner 用安全空计数
  标 FAILED；Task 元数据入口返回 `unsupported_snapshot` 与空定义，不宣称已还原。
- Job、Dataset、Diagnoser 与同 Space 的模型连接在创建事务内锁读，并刷新已有 ORM identity；
  手动锁后重检 Job 状态，调度锁后重检 enabled/schedule 与扫描时 cron/修改版本，失效候选不创建。
- OceanBase RR 可因配置并发改变拒绝锁读（6235），此时不创建混合快照；手动触发 rollback 并返回
  409 `configuration changed while creating task; retry`，不暴露SQL/连接详情。其它DB错误不伪装冲突。
  调度交给下一正常tick重新读取，不在当前候选中隐式重试。

### 保持不变

worker/attempt/status fence、取消、稀疏 Record、调度逻辑时间、手动触发活动 Task 幂等保持原合同。

### 影响与风险

冻结公开模型身份而运行时解析 key 允许密钥轮换；原连接删除或不可用仍会失败。旧配置无法从现有资源
可靠还原，因此 legacy 不承诺可重现。回滚前应排空快照 Task，避免旧 Worker 忽略新列而改用当前定义。

### 验证

覆盖创建后修改绑定/名称/权重/查询、重试、凭证排除、模型身份冻结、版本拒绝及两引擎 JSON 读回。

### 实施说明

统一快照构造与恢复、两类创建入口、Runner、ORM/DDL/migration 和公开定义 metadata 已实现。


## task-execution-snapshot@20261009-1945

**创建 Task 时冻结有效执行定义与语义版本：通用 Core 与可信有界执行**

```yaml
record_id: task/execution-snapshot@20261009-1945
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-09T19:45+08:00
decided_at: 2026-10-09T19:45+08:00
implemented_at: 2026-10-09T19:45+08:00
supersedes:
  - task/execution-snapshot@20261009-1800
affected_objects:
  - scheduler
  - task-runner
  - task-stats
  - model-connection
  - diagnoser
canonical_doc: docs/scheduling.md
```

### 背景

新 Core material/variant、可信执行政策和新 Trace 复测需要冻结；持久请求计数不能进入 immutable snapshot。

### 决策

- 创建手动/调度 Task 时锁读并刷新 Job/Dataset/Diagnoser/同 Space 模型连接，冻结有效 query/scope/selector、保存策略、定义 UUID/key/name/version/spec/role/生成参数/Prompt digest、模型 UUID/endpoint/model_name；不混合配置版本。
- 扩展 whitelist 只包含 canonical builtin_key/canonical_variant/required_evidence/result_boundary 和有限 trace_selection；不复制内联 model、API key、headers、datasource DSN 或未知字段。endpoint 拒绝 userinfo/query/fragment。
- executor_revision=1.3.0、schema_version=1、score_policy=lightweight-v1。旧 ABI 不可执行，必须排空或保留兼容 Worker；旧 schema/policy 不套用当前解释。safe read-only metadata 只按受支持 schema/policy/安全形状读取原版本，独立于可执行 ABI。
- trusted builder 的 execution_policy 是服务端 callable 参数，不从 Job/spec/browser 复制。bounded_core_v1 冻结最多一个 canonical LLM、1–10 fixed_trace_ids、total=30/per_case=3 和 scoped model availability。legacy None 保持 window 合同。
- 同一 Task 的 rescue/retry 保持 snapshot，ledger 独立可变且不 reset。新 Task 可重判原 Trace 或选择新的授权 Trace；rebind helper 深拷贝，只替换本次 fixed IDs 和 before_task_uuid/before_trace_ids，原检查/Prompt/模型/score/预算保持。合法资源绑定与查询时间窗变化是采样条件，不改变检查条件；本人授权归创建服务。
- 原 Task/记录/stats 不变。同 IDs 不是独立修复证明；改变检查或模型条件必须明确不可比，不能静默升级。
- additive 009 原快照列和 additive 010 ledger 均不改写 legacy 行。legacy 无快照仍显式 legacy_current_definition，不伪造创建时可重现定义。
- RR 配置变化/锁读冲突时 rollback、手动返回安全 409，调度下一正常 tick 重新读；其它 DB 错误不伪装冲突。Task API 不回显 Spec/Prompt/ledger，只给安全定义与独立预算摘要。

### 保持不变

T1 质量/信号分离、TaskStats total/good/bad/failed/skipped、不可变历史来源、CUSTOM/未涉及 legacy 合同，以及原始 Trace/runtime 身份保持。

### 影响与风险

ABI 1.3.0 发布前需排空旧可执行 Task 或保留兼容 Worker。reservation 是保守上界，不是账单或 provider receipt；材料和模型判断覆盖有明确限制。

### 验证

使用独立反例的 genuine RED→GREEN、mock transport 与事务边界离线测试。双引擎锁/并发、平台/API/GUI 与真实模型人工校准由主控另验收，本记录不宣称这些已通过。

### 实施说明

源实现和离线合同已实现；Task 4 报告记录精确检查、风险及待补真实 gate。

## task-execution-snapshot@20261010-0350

**ABI1.4 专项冻结；安全历史读取与全 pool 物理发布门禁**

```yaml
record_id: task/execution-snapshot@20261010-0350
decision_status: superseded
implementation_status: not_started
proposed_at: 2026-10-10T03:50+08:00
decided_at: 2026-10-10T09:06+08:00
implemented_at: null
supersedes:
  - task/execution-snapshot@20261009-1945
related_records:
  - diagnoser/builtin-definition-source@20261010-0350
  - diagnoser/specialist-material-policy@20261010-0350
  - security/private-check-policy@20261010-0350
  - job/retirement-migration@20261010-0350
  - task/attempt-ownership@20261009-1945
  - task/judge-request-budget@20261009-1945
affected_objects:
  - scheduler
  - task-runner
  - job
  - diagnosis-record
  - task-stats
canonical_doc: docs/scheduling.md
```

### 背景

当前1.3冻结Core有界政策，physicalretirement与privatepolicy需新execute ABI；history不能依赖退役executor。

### 决策

- 手动/schedule同Job锁/freshread，锁Job/Dataset/Diagnoser/同Space model，冻结queryscope/selector/save、UUIDkeyname/version/role/options/Promptdigest/非secret modelUUIDendpointname。api_key/headers/DSN/unknownspec不入，endpoint无userinfo/queryfragment；运行只取凭证，身份参数冻结。RR变更整txnrollback，manual409/schedule下tickretry；DBfault不conflict。
- schema1/scorelightweight-v1保留；execute revision目标1.4.0，strict拒其它revision/schema/score/retiredidentity，TraceJudge前FAILED不按catalog重写。legacyNone诚实legacy_current_definition并核current eligibility无fallback；所有旧legacyNone活动先drain。
- whitelist冻结finite specialist_policy、definition/configurationdigest；privatevalue仅内部snapshot供执行。publicTask/Recorddefinition不Spec/Prompt/marker。Core required_evidence/result_boundary/trace_selection保持，禁browser inline model/secretDSN未知字段。
- snapshot_definitions safehistory只校已知schema/policy/safeshape，不要求executeABI当前/Executor/退役Projector/旧worker；支持旧revision读原定义role版本。unknownshape unsupported_snapshot安全空metadata，不二次解失败body。oldRecord/Stats保存事实不重评。
- trusted bounded_core_v1仍servercallable，max1canonicalCore/1..10fixedID/total30/percase3/原模型policy，不Job/browser开关；专项不扩whitelist。advancedSpecialist execution_policy=None/AVAILABLE-only/judge_summary not_enabled；009/010/Django0028 additive不回写。
- retry/rescue同Task保immutable snapshot/独立ledger不reset；newTask同/新Trace授权/原条件rebind按T5，旧Quickbefore不支持复测时原history仍可读。相同ID不独立证明，改definition/model/score不可比。
- rootphysicalpublish先全受控workerpool排空：包括全部内置/CUSTOM、pending/dispatched/running/可rescue、legacyNone。记source1.3/target1.4 source+catalog/schema/builddigest、poolworker列表、exact零activitycensus、pauseJob、rollback旧image/依赖恢复材料。无已证明revision确定性dispatch，不能混worker赌旧任务命中，仅部分ownedJobdrain不足。nonowned只报告阻塞，另授权操作。
- 全pooldrain+sourcebuild回执与root显式publish授权才激活1.4physicalbuild；恢复oldenabled前实际恢复验证source1.3worker/build/旧依赖，否则disabled。safehistory不旧build，1.4不能fallback冒rollback。

### 保持不变

Task身份/时间/CAS/attempt/ledger/取消/非抢占/原stats/schema/query不改，GitHEAD不替并行tree sourcebuild摘要。

### 影响与风险

pool会被nonowned/legacy阻塞不能造终态；离线review不授权替换旧Taskworker，privatefreeze需public全链不echo。

### 验证

后续1.4freeze/旧execute拒而safeGET成功/legacyNone/privateDTO/fullcensusrescue/CoreQuick不变/无旧buildrollbackdisabled，真实pool另root。

### 实施说明

未实施。Ssnapshot/eligibility，pool/baseline/buildactivation归root。

## task-execution-snapshot@20261028-1611

**Freeze one saved check for an authorized Trace selection**

```yaml
record_id: task/execution-snapshot@20261028-1611
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-28T16:11+08:00
decided_at: 2026-10-28T16:11+08:00
implemented_at: 2026-10-28T16:11+08:00
supersedes:
  - task/execution-snapshot@20261010-0350
related_records:
  - diagnoser/builtin-definition-source@20261010-0350
  - diagnoser/specialist-material-policy@20261010-0350
  - security/private-check-policy@20261010-0350
  - job/retirement-migration@20261010-0350
  - task/attempt-ownership@20261009-1945
  - task/judge-request-budget@20261028-1611
affected_objects:
  - scheduler
  - task-runner
  - job
  - diagnosis-record
  - task-stats
  - http-api
canonical_doc: docs/scheduling.md
```

### Background

Trace list/detail users should run an existing diagnoser, including a configured Specialist, using
its saved configuration. A separate template selection would duplicate existing configuration and
cannot freeze private Specialist policy through a public or platform receipt.

### Decision

- Manual and scheduled Tasks continue to lock and fresh-read their Job, Dataset, Diagnosers and
  same-Space model connections. Snapshots freeze authorized source/query/selector, save policy,
  definition identity/name/version/role/options, Prompt digest and public model UUID/endpoint/name.
  Credentials, headers, datasource DSNs and unknown fields are excluded. Endpoints reject userinfo,
  query and fragment; runtime keys are resolved only for the original frozen provider identity.
- Schema 1, score policy `lightweight-v1` and execution revision `1.4.0` remain. Execution rejects
  unsupported revisions, schemas, scores and retired identities before querying/evaluating source;
  it never substitutes current catalog definitions. Legacy snapshots remain explicitly legacy,
  validate current eligibility, and do not claim historical reproducibility.
- Finite Specialist policies and configuration/definition digests are frozen internally. Public
  Task and Record definitions exclude spec, Prompt and private markers. Safe historical metadata
  validates known schema/shape independently of the current execution ABI or retired executors;
  unsupported shapes return empty safe metadata. Historical records and scores are not recomputed.
- The new `/runs` contract accepts one saved Diagnoser UUID and 1–10 authorized Trace IDs. Django
  enforces current execute permission and user/Space/Agent binding, deriving source scope itself.
  AgentLens enforces exact same-Space/Agent definition and model bindings. The full retained spans
  of every selected ID are read, with no relative time cutoff; missing or internal-output-only inputs
  fail the whole selection before resource writes.
- Schema-2 provisioning plans contain only safe definition references/digests and public model/source
  identities. Full private configuration remains in AgentLens. Before first acceptance, locked
  definition digest drift produces a conflict. Dataset, Job, Task and durable owned receipt commit
  atomically. Accepted replay validates the stored receipt identity/scope and returns the original
  Task even after a live definition edit/deletion or model availability change. Expired Tasks are not
  recreated. The platform receipt remains caller-owned and current authorization is always rechecked.
- `bounded_diagnosis_v1` freezes exactly one saved builtin or CUSTOM rule/LLM, including configured
  Specialists, with the existing 30/3 request budget. Saved LLM connections must be AVAILABLE in the
  same Space at freeze and runtime binding. No model/spec/Prompt/policy overrides or implicit probes
  are accepted. Options contain only safe identity/display fields and configuration availability.
- `bounded_core_v1` and old Quick endpoints retain their original canonical Core, advisory model
  status and frozen-condition retest behavior. Generic Specialist window Tasks remain unbounded by
  these policies and AVAILABLE-only. Saved runs expose no frozen-condition retest; another run creates
  a new request and freezes the current saved definition. Legacy retest cannot export a saved private
  snapshot into a platform plan.
- Retry/rescue preserves the same immutable snapshot and independent ledger. Source selection must
  equal the policy's fixed IDs. Record creation, protected snapshot masking and applicability skips
  use the existing executor pipeline. No migration or historical snapshot rewrite is introduced.
- Physical activation still requires explicit release authorization and a compatible complete worker
  pool. Prior ABI retirement requires a full controlled-pool activity/rescue census, drained Tasks,
  paused Jobs, exact source/catalog/schema/build evidence and verified rollback artifacts. Partial
  owned-Job drain is insufficient; nonowned blockers require separate authorization. Do not re-enable
  old workloads without a verified old worker/build/dependency restoration. Safe history is not an
  execution fallback. The new policy must not be enabled against workers that do not understand it.

### Unchanged

Task identity, logical time, cancellation, attempt/CAS fences, independent record retention, statistics
and feedback remain unchanged. Shared/readable definitions do not bypass exact execution scope.
Existing fixed-Task record queries and bounded projection-failure snapshots remain in effect.

### Impact and risks

Private policies require end-to-end safe DTO and receipt checks. Changed definitions before acceptance
must be retried with a new explicit request rather than silently re-frozen. Saved history depends on
the retained Task receipt under the existing authorization contract. Old workers fail closed on the
new policy, so source validation is not proof of production rollout or rollback safety.

### Validation

Exercise saved builtin/CUSTOM/Specialist freezing, private-field exclusion, exact scope, model policy,
definition drift, accepted replay, expiry, strict HTTP grammar and old Core compatibility. Verify real
collector and scheduler runs, records and source immutability separately from fixture tests. Preserve
database and external-model validation gaps in the report; physical release gates remain independent.

### Implementation notes

Source implementation follows the explicitly approved single-saved-diagnoser design. New failures were
observed before implementation, then targeted tests passed. This record describes implemented source
semantics; it does not certify an unperformed production deployment, full-pool drain or rollback.

## task-execution-snapshot@20261102-1700

**Freeze optional sandbox routing and retain the 1.4 local execution path**

```yaml
record_id: task/execution-snapshot@20261102-1700
decision_status: superseded
implementation_status: retired
proposed_at: 2026-11-02T17:00+08:00
decided_at: 2026-11-02T17:00+08:00
implemented_at: 2026-11-02T17:50+08:00
supersedes:
  - task/execution-snapshot@20261028-1611
related_records: []
affected_objects:
  - executor
  - task-runner
  - sandbox-connection
  - sandbox-execution
canonical_doc: docs/scheduling.md
```

### Background

Optional sandbox routing must not change after Task creation or silently change old frozen Tasks. The user approved the sandbox design and source implementation.

### Decision

- Manual and scheduled Tasks continue to lock and fresh-read their Job, Dataset, Diagnosers and
  same-Space model connections. Snapshots freeze authorized source/query/selector, save policy,
  definition identity/name/version/role/options, Prompt digest and public model UUID/endpoint/name.
  Credentials, headers, datasource DSNs and unknown fields are excluded. Endpoints reject userinfo,
  query and fragment; runtime keys are resolved only for the original frozen provider identity.
- Schema 1, score policy `lightweight-v1` and execution revision `1.5.0` apply. Execution rejects
  unsupported revisions, schemas, scores and retired identities before querying/evaluating source;
  it never substitutes current catalog definitions. Legacy snapshots remain explicitly legacy,
  validate current eligibility, and do not claim historical reproducibility.
- Finite Specialist policies and configuration/definition digests are frozen internally. Public
  Task and Record definitions exclude spec, Prompt and private markers. Safe historical metadata
  validates known schema/shape independently of the current execution ABI or retired executors;
  unsupported shapes return empty safe metadata. Historical records and scores are not recomputed.
- The new `/runs` contract accepts one saved Diagnoser UUID and 1–10 authorized Trace IDs. Django
  enforces current execute permission and user/Space/Agent binding, deriving source scope itself.
  AgentLens enforces exact same-Space/Agent definition and model bindings. The full retained spans
  of every selected ID are read, with no relative time cutoff; missing or internal-output-only inputs
  fail the whole selection before resource writes.
- Schema-2 provisioning plans contain only safe definition references/digests and public model/source
  identities. Full private configuration remains in AgentLens. Before first acceptance, locked
  definition digest drift produces a conflict. Dataset, Job, Task and durable owned receipt commit
  atomically. Accepted replay validates the stored receipt identity/scope and returns the original
  Task even after a live definition edit/deletion or model availability change. Expired Tasks are not
  recreated. The platform receipt remains caller-owned and current authorization is always rechecked.
- `bounded_diagnosis_v1` freezes exactly one saved builtin or CUSTOM rule/LLM, including configured
  Specialists, with the existing 30/3 request budget. Saved LLM connections must be AVAILABLE in the
  same Space at freeze and runtime binding. No model/spec/Prompt/policy overrides or implicit probes
  are accepted. Options contain only safe identity/display fields and configuration availability.
- `bounded_core_v1` and old Quick endpoints retain their original canonical Core, advisory model
  status and frozen-condition retest behavior. Generic Specialist window Tasks remain unbounded by
  these policies and AVAILABLE-only. Saved runs expose no frozen-condition retest; another run creates
  a new request and freezes the current saved definition. Legacy retest cannot export a saved private
  snapshot into a platform plan.
- Retry/rescue preserves the same immutable snapshot and independent ledger. Source selection must
  equal the policy's fixed IDs. Record creation, protected snapshot masking and applicability skips
  use the existing executor pipeline. No migration or historical snapshot rewrite is introduced.
- Physical activation still requires explicit release authorization and a compatible complete worker
  pool. Prior ABI retirement requires a full controlled-pool activity/rescue census, drained Tasks,
  paused Jobs, exact source/catalog/schema/build evidence and verified rollback artifacts. Partial
  owned-Job drain is insufficient; nonowned blockers require separate authorization. Do not re-enable
  old workloads without a verified old worker/build/dependency restoration. Safe history is not an
  execution fallback. The new policy must not be enabled against workers that do not understand it.

- Custom Rule snapshots additionally freeze resolved local/sandbox routing. Sandbox profiles contain protocol version, Space/connection UUID, provider, endpoint, image, CPU, memory, timeout and configuration revision; secrets are excluded. New Task builders resolve the Space binding under the same transaction as definitions. Explicit sandbox selections remain selected even when unavailable.
- The 1.5 worker retains the unchanged 1.4 execution path only for old snapshots without sandbox routing. It never resolves a new Space default for such frozen Tasks. Unsupported revisions still fail closed, and a 1.4 worker must reject 1.5 Tasks. No in-place historical rewrite occurs.
- Current runtime credentials are loaded only for the frozen Space/connection/endpoint/profile identity, permitting secret rotation but not identity drift. Every remote execution is leased to the concrete Task and attempt; retries cannot reuse a closed logical execution or affect another attempt's sandbox.

### Unchanged

Task identity, logical time, cancellation, attempt fences, quality/statistical algorithms, source authorization and historical read boundaries remain unchanged.

### Impact and risks

The new revision requires compatible workers. Source implementation is not production rollout authorization; existing full-pool qualification and rollback obligations remain.

### Validation

Test binding changes, profile identity drift and secret rotation; local legacy 1.4 replay; unknown-revision refusal; all creation paths and remote cleanup failures. Preserve actual-service versus fixture boundaries.

### Implementation notes

Source wiring and local verification are complete: persisted Task/Runner and authenticated Trial use the real ARM64 OpenShell 0.1.6 provider with synthetic Trace data; protocol, locking and compatibility tests pass. Telemetry adapters in that smoke are fixtures. This does not qualify production native images or authorize physical rollout.


## task-execution-snapshot@20261103-1505

**Freeze enhanced runtime and materials under ABI 1.6**

```yaml
record_id: task/execution-snapshot@20261103-1505
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-11-03T15:05+08:00
decided_at: 2026-11-03T15:05+08:00
implemented_at: 2026-11-03T17:10+08:00
supersedes:
  - task/execution-snapshot@20261102-1700
related_records:
  - executor/sandbox-backend@20261103-1505
  - sandbox-execution/lifecycle@20261103-1505
affected_objects:
  - executor
  - sandbox-execution
canonical_doc: docs/diagnoser/sandbox.md
```

### Background

Optional sandbox routing must not change after Task creation or silently change old frozen Tasks. The user approved the sandbox design and source implementation.

### Decision

- Execution revision `1.6.0` adds `rule_runtime`, `rule_runtime_manifest` and normalized materials, including content hashes, to the snapshot allowlist. New Task creation rejects an enhanced rule resolving to local execution. Restore rejects injected enhanced fields in 1.4/1.5 snapshots and rejects malformed or non-Custom-Rule enhanced specs.
- ABI 1.6 explicitly retains the original 1.4 local path and 1.5 frozen local/remote path. Historical snapshots are not rewritten and never silently gain imports or new Space defaults. Older workers reject the new revision.
- Runner and Trial propagate the same runtime identity and immutable material representation. Lease admission compares the runtime/material fields to the owning Task snapshot under the attempt fence, and fingerprints the complete request. Changes to code, runtime, material content or selected image create different execution identities.
- Saved manifest SHA256 and frozen image remain authoritative even if a plugin subsequently advertises a new environment. Credentials may rotate only for the existing frozen profile; runtime/image mismatch fails before lease reservation.
- Full compatible worker-pool qualification and explicit release authorization remain prerequisites for physical activation. Rollback disables new enhanced authoring/upgrades; enhanced Tasks cannot be redirected to legacy local execution.


- Manual and scheduled Tasks continue to lock and fresh-read their Job, Dataset, Diagnosers and
  same-Space model connections. Snapshots freeze authorized source/query/selector, save policy,
  definition identity/name/version/role/options, Prompt digest and public model UUID/endpoint/name.
  Credentials, headers, datasource DSNs and unknown fields are excluded. Endpoints reject userinfo,
  query and fragment; runtime keys are resolved only for the original frozen provider identity.
- Schema 1, score policy `lightweight-v1` and execution revision `1.6.0` apply. Execution rejects
  unsupported revisions, schemas, scores and retired identities before querying/evaluating source;
  it never substitutes current catalog definitions. Legacy snapshots remain explicitly legacy,
  validate current eligibility, and do not claim historical reproducibility.
- Finite Specialist policies and configuration/definition digests are frozen internally. Public
  Task and Record definitions exclude spec, Prompt and private markers. Safe historical metadata
  validates known schema/shape independently of the current execution ABI or retired executors;
  unsupported shapes return empty safe metadata. Historical records and scores are not recomputed.
- The new `/runs` contract accepts one saved Diagnoser UUID and 1–10 authorized Trace IDs. Django
  enforces current execute permission and user/Space/Agent binding, deriving source scope itself.
  AgentLens enforces exact same-Space/Agent definition and model bindings. The full retained spans
  of every selected ID are read, with no relative time cutoff; missing or internal-output-only inputs
  fail the whole selection before resource writes.
- Schema-2 provisioning plans contain only safe definition references/digests and public model/source
  identities. Full private configuration remains in AgentLens. Before first acceptance, locked
  definition digest drift produces a conflict. Dataset, Job, Task and durable owned receipt commit
  atomically. Accepted replay validates the stored receipt identity/scope and returns the original
  Task even after a live definition edit/deletion or model availability change. Expired Tasks are not
  recreated. The platform receipt remains caller-owned and current authorization is always rechecked.
- `bounded_diagnosis_v1` freezes exactly one saved builtin or CUSTOM rule/LLM, including configured
  Specialists, with the existing 30/3 request budget. Saved LLM connections must be AVAILABLE in the
  same Space at freeze and runtime binding. No model/spec/Prompt/policy overrides or implicit probes
  are accepted. Options contain only safe identity/display fields and configuration availability.
- `bounded_core_v1` and old Quick endpoints retain their original canonical Core, advisory model
  status and frozen-condition retest behavior. Generic Specialist window Tasks remain unbounded by
  these policies and AVAILABLE-only. Saved runs expose no frozen-condition retest; another run creates
  a new request and freezes the current saved definition. Legacy retest cannot export a saved private
  snapshot into a platform plan.
- Retry/rescue preserves the same immutable snapshot and independent ledger. Source selection must
  equal the policy's fixed IDs. Record creation, protected snapshot masking and applicability skips
  use the existing executor pipeline. No migration or historical snapshot rewrite is introduced.
- Physical activation still requires explicit release authorization and a compatible complete worker
  pool. Prior ABI retirement requires a full controlled-pool activity/rescue census, drained Tasks,
  paused Jobs, exact source/catalog/schema/build evidence and verified rollback artifacts. Partial
  owned-Job drain is insufficient; nonowned blockers require separate authorization. Do not re-enable
  old workloads without a verified old worker/build/dependency restoration. Safe history is not an
  execution fallback. The new policy must not be enabled against workers that do not understand it.

- Custom Rule snapshots additionally freeze resolved local/sandbox routing. Sandbox profiles contain protocol version, Space/connection UUID, provider, endpoint, image, CPU, memory, timeout and configuration revision; secrets are excluded. New Task builders resolve the Space binding under the same transaction as definitions. Explicit sandbox selections remain selected even when unavailable.
- The 1.6 worker retains the unchanged 1.4 execution path only for old snapshots without sandbox routing. It never resolves a new Space default for such frozen Tasks. Unsupported revisions still fail closed, and older workers must reject 1.6 Tasks. No in-place historical rewrite occurs.
- Current runtime credentials are loaded only for the frozen Space/connection/endpoint/profile identity, permitting secret rotation but not identity drift. Every remote execution is leased to the concrete Task and attempt; retries cannot reuse a closed logical execution or affect another attempt's sandbox.

### Unchanged

Task identity, logical time, cancellation, attempt fences, quality/statistical algorithms, source authorization and historical read boundaries remain unchanged.

### Impact and risks

The new revision requires compatible workers. Source implementation is not production rollout authorization; existing full-pool qualification and rollback obligations remain.

### Validation

Test binding changes, profile identity drift and secret rotation; local legacy 1.4 replay; unknown-revision refusal; all creation paths and remote cleanup failures. Preserve actual-service versus fixture boundaries.

### Implementation notes

ABI 1.6 source implementation and prepared-environment qualification are complete. Source checks
cover retained 1.4 local and 1.5 frozen-routing behavior, rejection of injected enhanced fields,
normalized material/runtime freezing and attempt-owned lease admission. The existing historical
snapshot bodies were not rewritten.

Real acceptance on a local macOS ARM64 host with prepared/cached OrbStack Linux ARM64 worker images
passed 17 HTTP Trials and four persisted full-Runner Tasks. Each Task used real isolated seekdb
rows, snapshot construction, attempt CAS and statistics. After its snapshot was persisted, the live
QA rule gained different material and BAD code; the Task retained its original materials, runtime
and manifest and produced the original GOOD result through the actual provider.

Trace reads were an explicit per-instance synthetic raw-span adapter and produced Record DTOs were
privately captured. The fixtures intentionally retain unavailable semantic projection and
incomplete-source statistics; no source SQL, telemetry ingestion, Record transport or complete
semantic projection is claimed. SQL readback confirms closed, cleanup-verified Task leases;
independent post-browser readback also confirmed all 28 final-connection leases closed in SQL and
the journal, zero matching workload containers/volumes on the same Docker daemon, zero
sandboxes/templates through the mTLS gateway, and a ready plugin with zero active leases. One earlier private
fixture Task failed before provider admission because it used an unsupported duration unit; it was
retained separately, the private fixture was corrected, and all four final Tasks passed.

This is prepared source QA, not cold-start, physical release, private native delivery or Linux/amd64
qualification. Complete compatible worker-pool activation, release authorization and rollback
obligations remain separate. No publication, merge, commit or push is authorized by this status.
